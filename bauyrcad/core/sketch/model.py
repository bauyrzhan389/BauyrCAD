"""Sketch data model: points, curves and constraints.

Geometry is stored parametrically, the way a CAD sketcher does it:

* **Points** hold the (x, y) unknowns. Curves reference points by id, so two lines that
  share an endpoint share the *same* point and stay connected while dragging.
* **Entities**: Line(p1, p2), Circle(center, r), Arc(center, start, end; CCW from start to
  end), Spline(through-points), Point(p). Any entity can be construction geometry.
* **Constraints** (relations and dimensions) reference points ("p", id) and entities
  ("e", id). The solver in solver.py moves the points so that all driving constraints hold.

Point id 0 is the sketch origin (fixed at 0, 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

TOL = 1e-6
ORIGIN = 0
FORMAT_VERSION = 2


def cross2(a, b) -> float:
    """z-component of the 2D cross product."""
    return float(a[0] * b[1] - a[1] * b[0])


class SketchError(Exception):
    """Raised when a sketch cannot be turned into closed profiles or an edit is invalid."""

    def __init__(self, message: str, points=None):
        super().__init__(message)
        self.points = list(points or [])


# ----------------------------------------------------------------------------
# Points and entities
# ----------------------------------------------------------------------------


@dataclass
class SPoint:
    id: int
    x: float
    y: float
    fixed: bool = False

    @property
    def xy(self) -> np.ndarray:
        return np.array([self.x, self.y])


class Entity:
    kind = "entity"
    label = "Entity"

    def __init__(self, id: int = 0, construction: bool = False):
        self.id = id
        self.construction = construction

    def point_ids(self) -> list[int]:
        return []

    def end_ids(self) -> list[int]:
        """Endpoints that can connect to other curves (empty for closed curves)."""
        return []

    def _base(self) -> dict:
        return {"type": self.kind, "id": self.id, "construction": self.construction}

    def to_dict(self) -> dict:
        return self._base()


class Line(Entity):
    kind, label = "line", "Line"

    def __init__(self, p1: int, p2: int, **kw):
        super().__init__(**kw)
        self.p1, self.p2 = p1, p2

    def point_ids(self):
        return [self.p1, self.p2]

    def end_ids(self):
        return [self.p1, self.p2]

    def to_dict(self):
        return {**self._base(), "p1": self.p1, "p2": self.p2}


class Circle(Entity):
    kind, label = "circle", "Circle"

    def __init__(self, center: int, r: float, **kw):
        super().__init__(**kw)
        self.center, self.r = center, float(r)

    def point_ids(self):
        return [self.center]

    def to_dict(self):
        return {**self._base(), "center": self.center, "r": self.r}


class Arc(Entity):
    """Circular arc, counter-clockwise from start to end around center."""

    kind, label = "arc", "Arc"

    def __init__(self, center: int, start: int, end: int, **kw):
        super().__init__(**kw)
        self.center, self.start, self.end = center, start, end

    def point_ids(self):
        return [self.center, self.start, self.end]

    def end_ids(self):
        return [self.start, self.end]

    def to_dict(self):
        return {**self._base(), "center": self.center, "start": self.start, "end": self.end}


class Spline(Entity):
    """Cubic interpolating spline through its points."""

    kind, label = "spline", "Spline"

    def __init__(self, pts: list[int], **kw):
        super().__init__(**kw)
        self.pts = list(pts)

    def point_ids(self):
        return list(self.pts)

    def end_ids(self):
        return [self.pts[0], self.pts[-1]]

    def to_dict(self):
        return {**self._base(), "pts": list(self.pts)}


class PointEntity(Entity):
    kind, label = "point", "Point"

    def __init__(self, p: int, **kw):
        super().__init__(**kw)
        self.p = p

    def point_ids(self):
        return [self.p]

    def to_dict(self):
        return {**self._base(), "p": self.p}


ENTITY_TYPES = {c.kind: c for c in (Line, Circle, Arc, Spline, PointEntity)}
CURVES = (Line, Circle, Arc, Spline)


@dataclass
class Constraint:
    """A geometric relation or a dimension.

    refs: list of ("p", point_id) / ("e", entity_id)
    value: dimension value (mm, or radians for angles); None for relations
    driving: False for reference (driven) dimensions, which only display a measurement
    label: dimension text position in sketch coordinates
    data: type-specific flags fixed at creation (orientation signs etc.)
    """

    id: int
    type: str
    refs: list
    value: float | None = None
    driving: bool = True
    label: list | None = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "refs": [list(r) for r in self.refs],
                "value": self.value, "driving": self.driving, "label": self.label,
                "data": self.data}

    @classmethod
    def from_dict(cls, d) -> "Constraint":
        return cls(d["id"], d["type"], [tuple(r) for r in d["refs"]], d.get("value"),
                   d.get("driving", True), d.get("label"), d.get("data", {}))

    def entity_ids(self) -> list[int]:
        return [i for k, i in self.refs if k == "e"]

    def point_refs(self) -> list[int]:
        return [i for k, i in self.refs if k == "p"]


# ----------------------------------------------------------------------------
# Spline evaluation (chord-length cubic spline, shared with the 3D edge builder)
# ----------------------------------------------------------------------------


def spline_params(pts: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    d = np.maximum(d, 1e-12)
    t = np.concatenate([[0.0], np.cumsum(d)])
    return t / t[-1]


def spline_eval(pts: np.ndarray, n: int = 64):
    """Sample the interpolating spline; returns (samples, end tangents)."""
    pts = np.asarray(pts, float)
    if len(pts) == 2:
        s = np.linspace(0, 1, n)[:, None]
        return pts[0] + s * (pts[1] - pts[0]), (pts[1] - pts[0], pts[1] - pts[0])
    from scipy.interpolate import CubicSpline
    t = spline_params(pts)
    cs = CubicSpline(t, pts, axis=0, bc_type="not-a-knot" if len(pts) > 3 else "natural")
    tt = np.linspace(0, 1, n)
    d = cs(tt, 1)
    return cs(tt), (d[0], d[-1])


# ----------------------------------------------------------------------------
# Sketch container
# ----------------------------------------------------------------------------


class Sketch:
    def __init__(self):
        self.points: dict[int, SPoint] = {ORIGIN: SPoint(ORIGIN, 0.0, 0.0, fixed=True)}
        self.entities: dict[int, Entity] = {}
        self.constraints: list[Constraint] = []
        # Sketch text (SolidWorks Sketch Text): {"id", "text", "at": [u, v] (left end of the
        # baseline), "height" (capital letter height), "font", "bold", "italic", "angle"}.
        # The letter outlines are profiles (extrude / cut them) but not solver geometry.
        self.texts: list[dict] = []
        self._next_pid = 1
        self._next_eid = 1
        self._next_cid = 1

    # ---- points ---------------------------------------------------------
    def add_point(self, x: float, y: float) -> int:
        pid = self._next_pid
        self._next_pid += 1
        self.points[pid] = SPoint(pid, float(x), float(y))
        return pid

    def pid(self, ref) -> int:
        """A point id: existing id (int) or new point at (x, y)."""
        if isinstance(ref, (int, np.integer)):
            return int(ref)
        return self.add_point(ref[0], ref[1])

    def pt(self, pid: int) -> np.ndarray:
        return self.points[pid].xy

    def set_pt(self, pid: int, xy) -> None:
        p = self.points[pid]
        if not p.fixed:
            p.x, p.y = float(xy[0]), float(xy[1])

    # ---- entities -------------------------------------------------------
    def _add(self, e: Entity) -> Entity:
        e.id = self._next_eid
        self._next_eid += 1
        self.entities[e.id] = e
        return e

    def add_line(self, a, b, construction=False) -> Line:
        pa, pb = self.pid(a), self.pid(b)
        if pa == pb or np.linalg.norm(self.pt(pa) - self.pt(pb)) < TOL:
            raise SketchError("Line has zero length")
        return self._add(Line(pa, pb, construction=construction))

    def add_circle(self, center, r: float, construction=False) -> Circle:
        if r < TOL:
            raise SketchError("Circle radius must be positive")
        return self._add(Circle(self.pid(center), r, construction=construction))

    def add_arc(self, center, start, end, construction=False) -> Arc:
        c, s, e = self.pid(center), self.pid(start), self.pid(end)
        if np.linalg.norm(self.pt(s) - self.pt(c)) < TOL:
            raise SketchError("Arc radius must be positive")
        if s == e or np.linalg.norm(self.pt(s) - self.pt(e)) < TOL:
            raise SketchError("Arc start and end coincide - use a circle")
        return self._add(Arc(c, s, e, construction=construction))

    def add_spline(self, pts, construction=False) -> Spline:
        ids = [self.pid(p) for p in pts]
        if len(ids) < 2:
            raise SketchError("A spline needs at least two points")
        return self._add(Spline(ids, construction=construction))

    def add_rectangle(self, p1, p2) -> list:
        """Corner rectangle with horizontal/vertical relations (scripting convenience)."""
        from . import builders
        return builders.corner_rectangle(self, p1, p2)

    def add_point_entity(self, p) -> PointEntity:
        return self._add(PointEntity(self.pid(p)))

    def curves(self, include_construction=True):
        return [e for e in self.entities.values() if isinstance(e, CURVES)
                and (include_construction or not e.construction)]

    def entity(self, eid: int) -> Entity | None:
        return self.entities.get(eid)

    def entities_using_point(self, pid: int) -> list[Entity]:
        return [e for e in self.entities.values() if pid in e.point_ids()]

    def is_empty(self) -> bool:
        return not self.entities and not self.texts

    # ---- text ----------------------------------------------------------------------------
    def add_text(self, text, at, height=5.0, font="Arial", bold=False, italic=False,
                 angle=0.0) -> dict:
        if not str(text).strip():
            raise SketchError("The text is empty")
        if height <= 0:
            raise SketchError("The text height must be positive")
        t = {"id": max([x["id"] for x in self.texts], default=0) + 1, "text": str(text),
             "at": [float(at[0]), float(at[1])], "height": float(height), "font": str(font),
             "bold": bool(bold), "italic": bool(italic), "angle": float(angle)}
        self.texts.append(t)
        return t

    def remove_text(self, tid: int) -> None:
        self.texts = [t for t in self.texts if t["id"] != tid]

    def text_box(self, t) -> np.ndarray:
        """Approximate corners (4, 2) of a text item (for bounds and picking)."""
        h = t["height"]
        w = 0.72 * h * max(len(t["text"]), 1)
        a = math.radians(t.get("angle", 0.0))
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        box = np.array([[0, -0.3 * h], [w, -0.3 * h], [w, 1.1 * h], [0, 1.1 * h]])
        return np.asarray(t["at"], float) + box @ R.T

    # ---- constraints ------------------------------------------------------
    def add_constraint(self, type: str, refs, value=None, driving=True, label=None, **data) -> Constraint:
        c = Constraint(self._next_cid, type, [tuple(r) for r in refs], value, driving,
                       list(label) if label is not None else None, data)
        self._next_cid += 1
        self.constraints.append(c)
        return c

    def constraint(self, cid: int) -> Constraint | None:
        return next((c for c in self.constraints if c.id == cid), None)

    def remove_constraint(self, cid: int) -> None:
        self.constraints = [c for c in self.constraints if c.id != cid]

    def constraints_of(self, kind: str, id: int) -> list[Constraint]:
        return [c for c in self.constraints if (kind, id) in c.refs]

    def _refs_alive(self, c: Constraint) -> bool:
        return all((k == "p" and i in self.points) or (k == "e" and i in self.entities)
                   for k, i in c.refs)

    # ---- removal ----------------------------------------------------------
    def remove_entity(self, eid: int) -> None:
        e = self.entities.pop(eid, None)
        if e is None:
            return
        self.constraints = [c for c in self.constraints if ("e", eid) not in c.refs]
        self.cleanup_points(e.point_ids())

    def cleanup_points(self, pids) -> None:
        """Delete points no entity uses any more (and constraints on them)."""
        used = {p for e in self.entities.values() for p in e.point_ids()}
        for p in pids:
            if p != ORIGIN and p not in used and p in self.points:
                del self.points[p]
        self.constraints = [c for c in self.constraints if self._refs_alive(c)]

    def replace_point(self, old: int, new: int) -> None:
        """Merge point `old` into `new` (used for coincidence while drawing)."""
        if old == new:
            return
        for e in self.entities.values():
            for attr in ("p1", "p2", "center", "start", "end", "p"):
                if getattr(e, attr, None) == old:
                    setattr(e, attr, new)
            if isinstance(e, Spline):
                e.pts = [new if p == old else p for p in e.pts]
        for c in self.constraints:
            c.refs = [("p", new) if r == ("p", old) else r for r in c.refs]
        # constraints that became trivial (e.g. coincident(p, p))
        self.constraints = [c for c in self.constraints
                            if not (c.type == "coincident" and c.refs[0] == c.refs[1])]
        if old != ORIGIN:
            self.points.pop(old, None)

    # ---- geometry queries ----------------------------------------------------
    def radius(self, e) -> float:
        if isinstance(e, Circle):
            return e.r
        return float(np.linalg.norm(self.pt(e.start) - self.pt(e.center)))

    def arc_angles(self, e: Arc) -> tuple[float, float]:
        """(start angle, positive CCW sweep) in radians."""
        c = self.pt(e.center)
        a0 = math.atan2(*(self.pt(e.start) - c)[::-1])
        a1 = math.atan2(*(self.pt(e.end) - c)[::-1])
        sweep = (a1 - a0) % (2 * math.pi)
        return a0, sweep if sweep > 1e-12 else 2 * math.pi

    def sample(self, e: Entity, n: int | None = None) -> np.ndarray:
        """Polyline through the entity in sketch coordinates."""
        if isinstance(e, Line):
            return np.array([self.pt(e.p1), self.pt(e.p2)])
        if isinstance(e, Circle):
            t = np.linspace(0, 2 * math.pi, (n or 96) + 1)
            return self.pt(e.center) + e.r * np.column_stack([np.cos(t), np.sin(t)])
        if isinstance(e, Arc):
            a0, sw = self.arc_angles(e)
            k = n or max(8, int(96 * sw / (2 * math.pi)) + 1)
            t = a0 + np.linspace(0, sw, k + 1)
            return self.pt(e.center) + self.radius(e) * np.column_stack([np.cos(t), np.sin(t)])
        if isinstance(e, Spline):
            return spline_eval(np.array([self.pt(p) for p in e.pts]), n or 96)[0]
        if isinstance(e, PointEntity):
            return np.array([self.pt(e.p)])
        raise TypeError(e)

    def bounds(self):
        pts = [self.sample(e) for e in self.entities.values()] + [self.text_box(t) for t in self.texts]
        if not pts:
            return None
        allp = np.vstack(pts)
        return allp.min(axis=0), allp.max(axis=0)

    def scale(self) -> float:
        b = self.bounds()
        return max(float(np.linalg.norm(b[1] - b[0])), 1.0) if b else 1.0

    def describe(self, e: Entity) -> str:
        cons = " (construction)" if e.construction else ""
        if isinstance(e, Line):
            a, b = self.pt(e.p1), self.pt(e.p2)
            return f"Line{e.id}: L={np.linalg.norm(b - a):.4g}{cons}"
        if isinstance(e, Circle):
            return f"Circle{e.id}: Ø{2 * e.r:.4g}{cons}"
        if isinstance(e, Arc):
            return f"Arc{e.id}: R{self.radius(e):.4g}{cons}"
        if isinstance(e, Spline):
            return f"Spline{e.id}: {len(e.pts)} points{cons}"
        return f"Point{e.id}"

    # ---- profiles ----------------------------------------------------------------
    def regions(self):
        from .profiles import find_regions
        return find_regions(self)

    # ---- serialization --------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": FORMAT_VERSION,
                "points": [[p.id, p.x, p.y, p.fixed] for p in self.points.values()],
                "entities": [e.to_dict() for e in self.entities.values()],
                "constraints": [c.to_dict() for c in self.constraints],
                "texts": [dict(t) for t in self.texts],
                "next": [self._next_pid, self._next_eid, self._next_cid]}

    @classmethod
    def from_dict(cls, d: dict) -> "Sketch":
        if d.get("version", 1) < 2:
            return _migrate_v1(d)
        s = cls()
        s.points = {int(i): SPoint(int(i), x, y, bool(f)) for i, x, y, f in d["points"]}
        s.points.setdefault(ORIGIN, SPoint(ORIGIN, 0.0, 0.0, True))
        for ed in d["entities"]:
            kind = ed["type"]
            kw = {"id": ed["id"], "construction": ed.get("construction", False)}
            if kind == "line":
                e = Line(ed["p1"], ed["p2"], **kw)
            elif kind == "circle":
                e = Circle(ed["center"], ed["r"], **kw)
            elif kind == "arc":
                e = Arc(ed["center"], ed["start"], ed["end"], **kw)
            elif kind == "spline":
                e = Spline(ed["pts"], **kw)
            elif kind == "point":
                e = PointEntity(ed["p"], **kw)
            else:
                raise ValueError(f"Unknown sketch entity {kind!r}")
            s.entities[e.id] = e
        s.constraints = [Constraint.from_dict(c) for c in d.get("constraints", [])]
        s.texts = [dict(t) for t in d.get("texts", [])]
        s._next_pid, s._next_eid, s._next_cid = d["next"]
        return s

    def copy(self) -> "Sketch":
        return Sketch.from_dict(self.to_dict())


def _migrate_v1(d: dict) -> Sketch:
    """v0.1 sketches stored raw coordinates per entity; rebuild shared points."""
    s = Sketch()

    def point(xy):
        for p in s.points.values():
            if math.dist((p.x, p.y), xy) < TOL * 10:
                return p.id
        return s.add_point(*xy)

    for ed in d.get("entities", []):
        if ed["type"] == "line":
            s.add_line(point(tuple(ed["a"])), point(tuple(ed["b"])))
        elif ed["type"] == "circle":
            s.add_circle(point(tuple(ed["center"])), ed["radius"])
    return s
