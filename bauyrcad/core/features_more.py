"""Applied and advanced features (SolidWorks Features tab): Fillet, Chamfer, Shell, Draft,
Hole Wizard, Linear / Circular Pattern, Mirror, Swept / Lofted / Boundary Boss and Cut, Rib,
and the reference geometry and curves (Plane, Axis, Point, Helix, Curve Through Points).

Edges and faces are stored as refs.EdgeRef / FaceRef and re-found on every rebuild, so the
features follow the model when earlier dimensions change. OCC work is in modeling.py.
"""

from __future__ import annotations

import math

import numpy as np

from . import geometry, modeling
from .features import (CutMissesError, Feature, FeatureError, RegenContext, SketchFeature,
                       apply_tool, profile_faces, register, removes_material)
from .geometry import GeometryError
from .plane import SketchPlane
from .refs import (EdgeRef, RefError, edge_ref, face_ref, resolve_axis, resolve_plane,
                   resolver)


def _run(fn, *a, **kw):
    """OCC / reference errors become feature errors."""
    try:
        return fn(*a, **kw)
    except (GeometryError, RefError) as e:
        raise FeatureError(str(e)) from None


def _need_body(ctx, what):
    if ctx.body is None:
        raise FeatureError(f"{what} needs a solid body - create a boss first")


def _refs_out(refs):
    return [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in refs]


def _through_length(ctx, *pts) -> float:
    ps = [np.asarray(p, float) for p in pts]
    if ctx.body is not None:
        ps += list(geometry.bounding_box(ctx.body))
    ps = np.array(ps) if ps else np.zeros((1, 3))
    return 2.0 * float(np.linalg.norm(ps.max(0) - ps.min(0))) + 10.0


# ============================================================================================
# Fillet / Chamfer
# ============================================================================================


class _EdgeFeature(Feature):
    """Common edge / face selection: edges, plus every edge of the selected faces."""

    def __init__(self, edges=None, faces=None, **kw):
        super().__init__(**kw)
        self.edges = [edge_ref(e) for e in (edges or [])]
        self.faces = [face_ref(f) for f in (faces or [])]

    def edge_indices(self, ctx) -> list[int]:
        _need_body(ctx, self.NAME_PREFIX)
        r = resolver(ctx)
        idx = [_run(r.edge, e) for e in self.edges]
        if self.faces:
            fidx = [_run(r.face, f) for f in self.faces]
            idx += modeling.face_edge_indices(ctx.body, fidx)
        idx = sorted(set(idx))
        if not idx:
            raise FeatureError("Select the edges (or faces) to use")
        return idx

    def _sel_params(self):
        return {"edges": _refs_out(self.edges), "faces": _refs_out(self.faces)}


@register
class FilletFeature(_EdgeFeature):
    """Constant radius fillet (radius2: linear variation along the edge chain)."""

    TYPE = "fillet"
    NAME_PREFIX = "Fillet"

    def __init__(self, radius=2.0, edges=None, faces=None, radius2=None, **kw):
        super().__init__(edges, faces, **kw)
        self.radius = float(radius)
        self.radius2 = None if radius2 is None else float(radius2)

    def execute(self, ctx):
        idx = self.edge_indices(ctx)
        ctx.body = _run(modeling.fillet, ctx.body, idx, self.radius, self.radius2)

    def params(self):
        return {"radius": self.radius, "radius2": self.radius2, **self._sel_params()}

    @classmethod
    def _from_params(cls, d):
        return cls(d["radius"], d.get("edges"), d.get("faces"), d.get("radius2"))


CHAMFER_TYPES = {"distance": "Equal distance", "distance_distance": "Distance - distance",
                 "angle_distance": "Angle - distance"}


@register
class ChamferFeature(_EdgeFeature):
    TYPE = "chamfer"
    NAME_PREFIX = "Chamfer"

    def __init__(self, distance=1.0, edges=None, faces=None, kind="distance", distance2=None,
                 angle=45.0, flip=False, **kw):
        super().__init__(edges, faces, **kw)
        self.distance = float(distance)
        self.kind = kind
        self.distance2 = float(distance2) if distance2 is not None else float(distance)
        self.angle = float(angle)
        self.flip = bool(flip)

    def execute(self, ctx):
        idx = self.edge_indices(ctx)
        if self.kind == "distance_distance":
            args = dict(d2=self.distance2)
        elif self.kind == "angle_distance":
            if not 0 < self.angle < 90:
                raise FeatureError("The chamfer angle must be between 0 and 90 degrees")
            args = dict(angle=self.angle)
        else:
            args = {}
        ctx.body = _run(modeling.chamfer, ctx.body, idx, self.distance, flip=self.flip, **args)

    def params(self):
        return {"distance": self.distance, "kind": self.kind, "distance2": self.distance2,
                "angle": self.angle, "flip": self.flip, **self._sel_params()}

    @classmethod
    def _from_params(cls, d):
        return cls(d["distance"], d.get("edges"), d.get("faces"), d.get("kind", "distance"),
                   d.get("distance2"), d.get("angle", 45.0), d.get("flip", False))


# ============================================================================================
# Shell / Draft
# ============================================================================================


@register
class ShellFeature(Feature):
    TYPE = "shell"
    NAME_PREFIX = "Shell"

    def __init__(self, thickness=2.0, faces=None, outward=False, **kw):
        super().__init__(**kw)
        self.thickness = float(thickness)
        self.faces = [face_ref(f) for f in (faces or [])]
        self.outward = bool(outward)

    def execute(self, ctx):
        _need_body(ctx, "Shell")
        r = resolver(ctx)
        idx = [_run(r.face, f) for f in self.faces]
        ctx.body = _run(modeling.shell, ctx.body, idx, self.thickness, self.outward)

    def params(self):
        return {"thickness": self.thickness, "faces": _refs_out(self.faces), "outward": self.outward}

    @classmethod
    def _from_params(cls, d):
        return cls(d["thickness"], d.get("faces"), d.get("outward", False))


@register
class DraftFeature(Feature):
    """Neutral-plane draft: faces tilt by `angle` about their line on the neutral plane; the
    pull direction is the neutral plane's normal (reverse flips it)."""

    TYPE = "draft"
    NAME_PREFIX = "Draft"

    def __init__(self, angle=1.0, faces=None, neutral=None, reverse=False, **kw):
        super().__init__(**kw)
        self.angle = float(angle)
        self.faces = [face_ref(f) for f in (faces or [])]
        self.neutral = neutral or {"type": "std", "name": "Top Plane"}
        self.reverse = bool(reverse)

    def execute(self, ctx):
        _need_body(ctx, "Draft")
        if not self.faces:
            raise FeatureError("Select the faces to draft")
        pl = _run(resolve_plane, self.neutral, ctx)
        r = resolver(ctx)
        idx = [_run(r.face, f) for f in self.faces]
        n = -pl.normal if self.reverse else pl.normal
        ctx.body = _run(modeling.draft, ctx.body, idx, self.angle, pl.origin, n)

    def params(self):
        return {"angle": self.angle, "faces": _refs_out(self.faces), "neutral": self.neutral,
                "reverse": self.reverse}

    @classmethod
    def _from_params(cls, d):
        return cls(d["angle"], d.get("faces"), d.get("neutral"), d.get("reverse", False))


# ============================================================================================
# Hole Wizard
# ============================================================================================

# ISO metric coarse: pitch, tap drill, clearance (ISO 273 close / normal / loose),
# counterbore for ISO 4762 socket head cap screws (DIN 974-1: diameter, depth) and
# countersink for ISO 10642 (90 deg, head diameter). Every value can be overridden.
METRIC = {
    "M2":   dict(pitch=0.4,  tap=1.6,  clear=(2.2, 2.4, 2.6),    cb=(4.3, 2.3),   csk=4.4),
    "M2.5": dict(pitch=0.45, tap=2.05, clear=(2.7, 2.9, 3.1),    cb=(5.0, 2.8),   csk=5.5),
    "M3":   dict(pitch=0.5,  tap=2.5,  clear=(3.2, 3.4, 3.6),    cb=(6.5, 3.4),   csk=6.7),
    "M4":   dict(pitch=0.7,  tap=3.3,  clear=(4.3, 4.5, 4.8),    cb=(8.0, 4.4),   csk=9.0),
    "M5":   dict(pitch=0.8,  tap=4.2,  clear=(5.3, 5.5, 5.8),    cb=(10.0, 5.4),  csk=11.2),
    "M6":   dict(pitch=1.0,  tap=5.0,  clear=(6.4, 6.6, 7.0),    cb=(11.0, 6.4),  csk=13.4),
    "M8":   dict(pitch=1.25, tap=6.8,  clear=(8.4, 9.0, 10.0),   cb=(15.0, 8.6),  csk=17.9),
    "M10":  dict(pitch=1.5,  tap=8.5,  clear=(10.5, 11.0, 12.0), cb=(18.0, 10.6), csk=22.4),
    "M12":  dict(pitch=1.75, tap=10.2, clear=(13.0, 13.5, 14.5), cb=(20.0, 12.6), csk=26.9),
    "M16":  dict(pitch=2.0,  tap=14.0, clear=(17.0, 17.5, 18.5), cb=(26.0, 16.6), csk=33.6),
    "M20":  dict(pitch=2.5,  tap=17.5, clear=(21.0, 22.0, 24.0), cb=(33.0, 20.6), csk=40.3),
    "M24":  dict(pitch=3.0,  tap=21.0, clear=(25.0, 26.0, 28.0), cb=(40.0, 24.6), csk=48.0),
}
FITS = {"close": 0, "normal": 1, "loose": 2}
HOLE_KINDS = {"simple": "Hole", "tapped": "Tapped hole", "counterbore": "Counterbore",
              "countersink": "Countersink"}


def hole_sizes(kind: str, size: str, fit: str = "normal") -> dict:
    """Standard dimensions of a Hole Wizard hole: d, and cbore_d / cbore_depth / csk_d."""
    s = METRIC.get(size)
    if s is None:
        raise FeatureError(f"Unknown size {size!r}; use one of {', '.join(METRIC)}")
    if kind == "tapped":
        return {"d": s["tap"], "pitch": s["pitch"], "major": float(size[1:])}
    d = s["clear"][FITS.get(fit, 1)]
    out = {"d": d}
    if kind == "counterbore":
        out.update(cbore_d=s["cb"][0], cbore_depth=s["cb"][1])
    elif kind == "countersink":
        out.update(csk_d=s["csk"], csk_angle=90.0)
    return out


@register
class HoleFeature(Feature):
    """Hole Wizard: simple / tapped / counterbore / countersink holes at points of a face or
    plane. Points are (u, v) in the plane's sketch coordinates (for faces along an axis these
    are the global coordinates, see refs.plane_on_face)."""

    TYPE = "hole"
    NAME_PREFIX = "Hole"

    def __init__(self, plane=None, points=None, kind="simple", size=None, d=None,
                 depth=10.0, end="blind", fit="normal", cbore_d=None, cbore_depth=None,
                 csk_d=None, csk_angle=90.0, thread_depth=None, drill_point=True,
                 reverse=False, **kw):
        super().__init__(**kw)
        self.plane = plane or {"type": "std", "name": "Front Plane"}
        self.points = [[float(p[0]), float(p[1])] for p in (points or [])]
        self.kind = kind
        self.size = size
        self.d = None if d is None else float(d)
        self.depth = float(depth)
        self.end = end
        self.fit = fit
        self.cbore_d = cbore_d
        self.cbore_depth = cbore_depth
        self.csk_d = csk_d
        self.csk_angle = float(csk_angle)
        self.thread_depth = thread_depth
        self.drill_point = bool(drill_point)
        self.reverse = bool(reverse)

    @property
    def NAME_PREFIX(self):  # noqa: N802
        if self.size:
            return {"tapped": f"{self.size} Tapped Hole", "counterbore": f"CBORE for {self.size} Screw",
                    "countersink": f"CSK for {self.size} Screw"}.get(self.kind, f"{self.size} Clearance Hole")
        return {"counterbore": "CBORE Hole", "countersink": "CSK Hole"}.get(self.kind, "Hole")

    def dims(self) -> dict:
        """Effective dimensions (standard values for a size, overridden by explicit ones)."""
        out = hole_sizes(self.kind, self.size, self.fit) if self.size else {}
        if self.d is not None:
            out["d"] = self.d
        for k in ("cbore_d", "cbore_depth", "csk_d"):
            if getattr(self, k) is not None:
                out[k] = float(getattr(self, k))
        out["csk_angle"] = self.csk_angle
        if "d" not in out:
            raise FeatureError("Give the hole a diameter or a standard size")
        if self.kind == "counterbore" and ("cbore_d" not in out or "cbore_depth" not in out):
            raise FeatureError("Give the counterbore diameter and depth (or a standard size)")
        if self.kind == "countersink" and "csk_d" not in out:
            raise FeatureError("Give the countersink diameter (or a standard size)")
        return out

    def callout(self) -> str:
        """Drawing callout text, e.g. 'M6x1.0 - 12 deep' or 'Ø6.6 thru, CBORE Ø11 x 6.4'."""
        dm = self.dims()
        depth = "THRU" if self.end == "through_all" else f"▽ {self.depth:g}"
        if self.kind == "tapped" and self.size:
            td = self.thread_depth or (None if self.end == "through_all" else 0.8 * self.depth)
            return (f"{self.size}x{dm['pitch']:g} - "
                    + ("THRU" if td is None else f"▽ {td:g}") + f"  (tap drill Ø{dm['d']:g})")
        s = f"Ø{dm['d']:g} {depth}"
        if self.kind == "counterbore":
            s += f", ⌴ Ø{dm['cbore_d']:g} ▽ {dm['cbore_depth']:g}"
        elif self.kind == "countersink":
            s += f", ⌵ Ø{dm['csk_d']:g} x {dm['csk_angle']:g}°"
        return s

    def world_points(self, ctx):
        pl = _run(resolve_plane, self.plane, ctx)
        return pl, [pl.to_world(*p) for p in self.points]

    def tool_shape(self, ctx, direction_sign=1.0):
        if not self.points:
            raise FeatureError("Place at least one hole point")
        pl, pts = self.world_points(ctx)
        dm = self.dims()
        u = -pl.normal * (-1 if self.reverse else 1) * direction_sign
        depth = self.depth if self.end == "blind" else _through_length(ctx, *pts)
        if depth <= 0:
            raise FeatureError("The hole depth must be positive")
        tools = [_run(modeling.hole_tool, p, u, dm["d"], depth, self.kind,
                      dm.get("cbore_d", 0.0), dm.get("cbore_depth", 0.0), dm.get("csk_d", 0.0),
                      dm.get("csk_angle", 90.0), self.drill_point and self.end == "blind")
                 for p in pts]
        return tools[0] if len(tools) == 1 else geometry.compound(tools)

    def execute(self, ctx):
        _need_body(ctx, "Hole")
        tool = self.tool_shape(ctx)
        if self.plane.get("type") != "face" and not self.reverse:
            # a plane, not a face: drill into whichever side has the material
            other = self.tool_shape(ctx, -1.0)
            if (geometry.volume(geometry.common(ctx.body, other))
                    > geometry.volume(geometry.common(ctx.body, tool))):
                tool = other
        if not removes_material(ctx.body, tool):
            raise CutMissesError("The hole does not intersect the model")
        ctx.body = _run(geometry.cut, ctx.body, tool)
        if geometry.count_solids(ctx.body) == 0:
            raise FeatureError("The hole removes the whole body")
        self.tools = [(tool, "cut")]

    def params(self):
        return {k: getattr(self, k) for k in (
            "plane", "points", "kind", "size", "d", "depth", "end", "fit", "cbore_d",
            "cbore_depth", "csk_d", "csk_angle", "thread_depth", "drill_point", "reverse")}

    @classmethod
    def _from_params(cls, d):
        d = {k: v for k, v in d.items() if k not in ("type", "id", "name", "suppressed")}
        return cls(**d)


# ============================================================================================
# Patterns and mirror
# ============================================================================================


class _InstanceFeature(Feature):
    """Copies of seed features' tool bodies (or of the whole body) under rigid transforms."""

    def __init__(self, seeds=None, bodies=False, **kw):
        super().__init__(**kw)
        self.seeds = list(seeds or [])
        self.bodies = bool(bodies)

    def depends_on(self):
        return list(self.seeds)

    def transforms(self, ctx) -> list[np.ndarray]:
        raise NotImplementedError

    def execute(self, ctx):
        _need_body(ctx, self.NAME_PREFIX)
        mats = self.transforms(ctx)
        if not mats:
            raise FeatureError("The pattern has no instances")
        if self.bodies or not self.seeds:
            base = ctx.body
            copies = [self.place(base, m) for m in mats]
            ctx.body = _run(geometry.fuse_many, ctx.body, copies)
            self.tools = [(c, "boss") for c in copies]
            return
        seed_tools = []
        for sid in self.seeds:
            f = ctx.features.get(sid)
            if f is None:
                raise FeatureError("A seed feature is missing")
            if f.suppressed:
                continue
            if f.error:
                raise FeatureError(f"The seed feature {f.name} failed")
            if not f.tools:
                raise FeatureError(f"{f.name} can't be patterned (only features that add or "
                                   "remove a body: extrudes, revolves, holes, sweeps, lofts, "
                                   "ribs, patterns) - pattern the body instead")
            seed_tools += f.tools
        out = []
        for op in ("boss", "cut"):
            tools = [self.place(t, m) for t, o in seed_tools if o == op for m in mats]
            if not tools:
                continue
            if op == "cut":
                body = _run(geometry.cut_many, ctx.body, tools)
                if geometry.count_solids(body) == 0:
                    raise FeatureError("The pattern removes the whole body")
            else:
                body = _run(geometry.fuse_many, ctx.body, tools)
            ctx.body = body
            out += [(t, op) for t in tools]
        self.tools = out

    def place(self, shape, m):
        return modeling.transform_shape(shape, m)

    def _base_params(self):
        return {"seeds": self.seeds, "bodies": self.bodies}


@register
class LinearPatternFeature(_InstanceFeature):
    TYPE = "linear_pattern"
    NAME_PREFIX = "LPattern"

    def __init__(self, seeds=None, direction=None, spacing=10.0, count=2, direction2=None,
                 spacing2=10.0, count2=1, reverse=False, reverse2=False, skip=None,
                 bodies=False, **kw):
        super().__init__(seeds, bodies, **kw)
        self.direction = direction or {"type": "std", "name": "x"}
        self.spacing = float(spacing)
        self.count = int(count)
        self.direction2 = direction2
        self.spacing2 = float(spacing2)
        self.count2 = int(count2)
        self.reverse = bool(reverse)
        self.reverse2 = bool(reverse2)
        self.skip = [list(map(int, s)) for s in (skip or [])]

    def transforms(self, ctx):
        if self.count < 1 or self.count2 < 1:
            raise FeatureError("Instance counts must be at least 1")
        d1 = _run(resolve_axis, self.direction, ctx)[1] * (-1 if self.reverse else 1)
        d2 = np.zeros(3)
        if self.direction2 is not None and self.count2 > 1:
            d2 = _run(resolve_axis, self.direction2, ctx)[1] * (-1 if self.reverse2 else 1)
        skip = {tuple(s) for s in self.skip}
        return [modeling.translation_matrix(i * self.spacing * d1 + j * self.spacing2 * d2)
                for i in range(self.count) for j in range(self.count2 if self.direction2 else 1)
                if (i, j) != (0, 0) and (i, j) not in skip]

    def params(self):
        return {**self._base_params(), "direction": self.direction, "spacing": self.spacing,
                "count": self.count, "direction2": self.direction2, "spacing2": self.spacing2,
                "count2": self.count2, "reverse": self.reverse, "reverse2": self.reverse2,
                "skip": self.skip}

    @classmethod
    def _from_params(cls, d):
        return cls(d.get("seeds"), d.get("direction"), d["spacing"], d["count"], d.get("direction2"),
                   d.get("spacing2", 10.0), d.get("count2", 1), d.get("reverse", False),
                   d.get("reverse2", False), d.get("skip"), d.get("bodies", False))


@register
class CircularPatternFeature(_InstanceFeature):
    TYPE = "circular_pattern"
    NAME_PREFIX = "CirPattern"

    def __init__(self, seeds=None, axis=None, count=4, angle=360.0, equal_spacing=True,
                 reverse=False, skip=None, bodies=False, **kw):
        super().__init__(seeds, bodies, **kw)
        self.axis = axis or {"type": "std", "name": "z"}
        self.count = int(count)
        self.angle = float(angle)
        self.equal_spacing = bool(equal_spacing)
        self.reverse = bool(reverse)
        self.skip = [int(s) for s in (skip or [])]

    def step(self) -> float:
        if not self.equal_spacing:
            return self.angle
        if abs(self.angle - 360.0) < 1e-9:
            return 360.0 / self.count
        return self.angle / max(self.count - 1, 1)

    def transforms(self, ctx):
        if self.count < 1:
            raise FeatureError("The instance count must be at least 1")
        p, a = _run(resolve_axis, self.axis, ctx)
        s = self.step() * (-1 if self.reverse else 1)
        return [modeling.rotation_matrix(p, a, i * s) for i in range(1, self.count)
                if i not in set(self.skip)]

    def params(self):
        return {**self._base_params(), "axis": self.axis, "count": self.count, "angle": self.angle,
                "equal_spacing": self.equal_spacing, "reverse": self.reverse, "skip": self.skip}

    @classmethod
    def _from_params(cls, d):
        return cls(d.get("seeds"), d.get("axis"), d["count"], d.get("angle", 360.0),
                   d.get("equal_spacing", True), d.get("reverse", False), d.get("skip"),
                   d.get("bodies", False))


@register
class MirrorFeature(_InstanceFeature):
    """Mirror seed features (or the whole body) about a plane or planar face."""

    TYPE = "mirror"
    NAME_PREFIX = "Mirror"

    def __init__(self, seeds=None, plane=None, bodies=False, **kw):
        super().__init__(seeds, bodies, **kw)
        self.plane = plane or {"type": "std", "name": "Right Plane"}

    def transforms(self, ctx):
        self._pl = _run(resolve_plane, self.plane, ctx)
        return [None]

    def place(self, shape, _m):
        return modeling.mirror_shape(shape, self._pl.origin, self._pl.normal)

    def params(self):
        return {**self._base_params(), "plane": self.plane}

    @classmethod
    def _from_params(cls, d):
        return cls(d.get("seeds"), d.get("plane"), d.get("bodies", False))


# ============================================================================================
# Sweep / Loft / Boundary
# ============================================================================================


def _path_wire(ctx, path: dict):
    """Wire of a sweep path spec: {"sketch": id} or {"feature": id} (helix / curve)."""
    fid = path.get("sketch") or path.get("feature")
    f = ctx.features.get(fid)
    if f is None:
        raise FeatureError("The sweep path is missing")
    if isinstance(f, SketchFeature):
        return _run(f.path_wire)
    w = getattr(f, "result_wire", None)
    if w is None:
        raise FeatureError(f"{f.name} is not a curve")
    return w


@register
class SweepFeature(Feature):
    """Swept boss / cut: a sketch profile, or a circle of `diameter` (SolidWorks' circular
    profile, optionally a tube with inner_diameter) along a sketch or 3D curve path."""

    TYPE = "sweep"

    def __init__(self, path=None, profile=None, operation="boss", diameter=None,
                 inner_diameter=None, regions=None, frenet=None, **kw):
        super().__init__(**kw)
        self.path = path or {}
        self.profile = profile                    # sketch id or None (circular profile)
        self.operation = operation
        self.diameter = None if diameter is None else float(diameter)
        self.inner_diameter = None if inner_diameter is None else float(inner_diameter)
        self.regions = [list(map(float, p)) for p in (regions or [])]
        self.frenet = frenet                      # None = auto (on for helices)

    @property
    def NAME_PREFIX(self):  # noqa: N802
        return "Cut-Sweep" if self.operation == "cut" else "Sweep"

    def depends_on(self):
        return [x for x in (self.profile, self.path.get("sketch"), self.path.get("feature")) if x]

    def tool_shape(self, ctx):
        wire = _path_wire(ctx, self.path)
        f = ctx.features.get(self.path.get("feature") or "")
        frenet = self.frenet if self.frenet is not None else isinstance(f, HelixFeature)
        if self.profile is None:
            if not self.diameter or self.diameter <= 0:
                raise FeatureError("Give the circular profile a diameter (or pick a profile sketch)")
            if self.inner_diameter and not 0 < self.inner_diameter < self.diameter:
                raise FeatureError("The inner diameter must be smaller than the outer one")
            p, t = modeling.wire_start(wire)
            faces = [modeling.circle_face(p, t, self.diameter / 2,
                                          self.inner_diameter / 2 if self.inner_diameter else None)]
        else:
            _, faces = profile_faces(ctx, self.profile, self.regions)
        solids = [_run(modeling.sweep, face, wire, frenet) for face in faces]
        return solids[0] if len(solids) == 1 else geometry.fuse_all(solids)

    def execute(self, ctx):
        tool = self.tool_shape(ctx)
        apply_tool(ctx, self.operation, tool)
        self.tools = [(tool, self.operation)]

    def params(self):
        return {"path": self.path, "profile": self.profile, "operation": self.operation,
                "diameter": self.diameter, "inner_diameter": self.inner_diameter,
                "regions": self.regions, "frenet": self.frenet}

    @classmethod
    def _from_params(cls, d):
        return cls(d.get("path"), d.get("profile"), d.get("operation", "boss"), d.get("diameter"),
                   d.get("inner_diameter"), d.get("regions"), d.get("frenet"))


@register
class LoftFeature(Feature):
    """Lofted boss / cut through profiles in order: sketches (their closed profile; a sketch
    with only a point is a loft to a point) or explicit 3D points."""

    TYPE = "loft"
    SMOOTH = False

    def __init__(self, sections=None, operation="boss", ruled=False, closed=False, **kw):
        super().__init__(**kw)
        self.sections = list(sections or [])      # sketch ids or [x, y, z] points
        self.operation = operation
        self.ruled = bool(ruled)
        self.closed = bool(closed)

    @property
    def NAME_PREFIX(self):  # noqa: N802
        return "Cut-Loft" if self.operation == "cut" else "Loft"

    def depends_on(self):
        return [s for s in self.sections if isinstance(s, str)]

    def _section(self, ctx, s):
        if not isinstance(s, str):
            return modeling.vertex(s)
        f = ctx.features.get(s)
        if not isinstance(f, SketchFeature):
            raise FeatureError("A loft profile sketch is missing")
        if f.error:
            raise FeatureError(f"{f.name}: {f.error}")
        if not f.faces:
            pts = [e for e in f.sketch.entities.values() if e.kind == "point"]
            if len(pts) == 1 and len(f.sketch.entities) == 1:
                return modeling.vertex(f.plane.to_world(*f.sketch.pt(pts[0].p)))
            raise FeatureError(f"{f.name}: {f.profile_error or 'no closed profile'}")
        if len(f.faces) > 1:
            raise FeatureError(f"{f.name} has {len(f.faces)} profiles - a loft profile must be one "
                               "closed contour")
        return modeling.outer_wire(f.faces[0])

    def tool_shape(self, ctx):
        secs = [self._section(ctx, s) for s in self.sections]
        return _run(self._build, secs)

    def _build(self, secs):
        return modeling.loft(secs, self.ruled, self.closed)

    def execute(self, ctx):
        tool = self.tool_shape(ctx)
        apply_tool(ctx, self.operation, tool)
        self.tools = [(tool, self.operation)]

    def params(self):
        return {"sections": self.sections, "operation": self.operation, "ruled": self.ruled,
                "closed": self.closed}

    @classmethod
    def _from_params(cls, d):
        return cls(d.get("sections"), d.get("operation", "boss"), d.get("ruled", False),
                   d.get("closed", False))


@register
class BoundaryFeature(LoftFeature):
    """Boundary boss / cut through profiles (direction 1 curves). Built as a smoothed
    (curvature-continuous) loft; direction-2 curves are not supported yet."""

    TYPE = "boundary"

    @property
    def NAME_PREFIX(self):  # noqa: N802
        return "Boundary-Cut" if self.operation == "cut" else "Boundary"

    def _build(self, secs):
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCP.GeomAbs import GeomAbs_C2
        from OCP.TopoDS import TopoDS, TopoDS_Vertex
        if len(secs) < 2:
            raise GeometryError("A boundary needs at least two profiles")
        mk = BRepOffsetAPI_ThruSections(True, False, 1e-6)
        mk.CheckCompatibility(True)
        mk.SetSmoothing(True)
        mk.SetContinuity(GeomAbs_C2)
        for s in secs + ([secs[0]] if self.closed else []):
            mk.AddVertex(s) if isinstance(s, TopoDS_Vertex) else mk.AddWire(TopoDS.Wire_s(s))
        mk.Build()
        if not mk.IsDone():
            raise GeometryError("The boundary could not be built - check the profiles")
        return modeling._require_solid(mk.Shape(), "The boundary")


# ============================================================================================
# Rib
# ============================================================================================


@register
class RibFeature(Feature):
    """Rib from an open sketch line: material fills from the line to the body.

    direction="parallel": in the sketch plane (a gusset), thickness across the plane.
    direction="normal":   the line thickened in the plane, extruded normal to the sketch
                          until it meets the body (flip = the other side of the plane).
    side: "both" / "first" / "second" side of the sketch for the thickness.
    """

    TYPE = "rib"
    NAME_PREFIX = "Rib"

    def __init__(self, sketch_id="", thickness=2.0, direction="parallel", side="both",
                 flip=False, **kw):
        super().__init__(**kw)
        self.sketch_id = sketch_id
        self.thickness = float(thickness)
        self.direction = direction
        self.side = side
        self.flip = bool(flip)

    def depends_on(self):
        return [self.sketch_id]

    def _span(self, side):
        t = self.thickness
        return {"both": (-t / 2, t / 2), "first": (0.0, t), "second": (-t, 0.0)}[side]

    def execute(self, ctx):
        _need_body(ctx, "Rib")
        if self.thickness <= 0:
            raise FeatureError("The rib thickness must be positive")
        sk = ctx.features.get(self.sketch_id)
        if not isinstance(sk, SketchFeature):
            raise FeatureError("The rib sketch is missing")
        tool = _run(self._tool, ctx, sk)
        ctx.body = _run(geometry.fuse, ctx.body, tool)
        self.tools = [(tool, "boss")]

    def _tool(self, ctx, sk):
        pl = sk.plane
        n = pl.normal
        L = _through_length(ctx, pl.origin)
        edges = geometry.sketch_edges(sk.sketch, pl)
        if not edges:
            raise GeometryError("The rib sketch has no curves")
        ext = []
        for e in sk.sketch.curves(include_construction=False):
            smp = sk.sketch.sample(e)
            for a, b in ((smp[1], smp[0]), (smp[-2], smp[-1])):
                d = np.asarray(b) - np.asarray(a)
                d = d / max(np.linalg.norm(d), 1e-12)
                ext.append(modeling.line_edge(pl.to_world(*b), pl.to_world(*(np.asarray(b) + d * L))))
        cutters = edges + ext
        if self.direction == "parallel":
            big = modeling.plane_rect_face(pl.origin, n, pl.xdir, -L, L, -L, L)
            pieces = modeling.split_face(big, cutters)
            lo, hi = self._span(self.side)
            slabs = []
            for f in pieces:
                base = modeling.translate_shape(f, n * lo)
                slabs.append(geometry.prism(base, n * (hi - lo)))
        else:
            if len(edges) != 1 or sk.sketch.curves(include_construction=False)[0].kind != "line":
                raise GeometryError("A rib normal to the sketch needs one straight line")
            e = sk.sketch.curves(include_construction=False)[0]
            a, b = np.asarray(sk.sketch.pt(e.p1)), np.asarray(sk.sketch.pt(e.p2))
            d = (b - a) / np.linalg.norm(b - a)
            nrm = np.array([-d[1], d[0]])
            lo, hi = self._span(self.side)
            A, B = a - d * L, b + d * L
            quad = [A + nrm * lo, B + nrm * lo, B + nrm * hi, A + nrm * hi]
            wire = modeling.polyline_wire([pl.to_world(*q) for q in quad + [quad[0]]])
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
            face = BRepBuilderAPI_MakeFace(wire, True).Face()
            sgn = -1.0 if self.flip else 1.0
            slabs = [geometry.prism(face, n * sgn * L)]
        # keep the pieces outside the body that touch the original rib curve and are closed
        # in by the body (not reaching the far ends of the construction)
        rib_curve = geometry.compound(edges)
        far = L * 0.9
        keep = []
        for slab in slabs:
            rest = geometry.cut(slab, ctx.body)
            for s in modeling.solids_of(rest):
                if modeling.distance(s, rib_curve) > 1e-6 * L + 1e-7:
                    continue
                lo_, hi_ = geometry.bounding_box(s)
                if np.max(np.abs(lo_ - pl.origin)) > far or np.max(np.abs(hi_ - pl.origin)) > far:
                    continue
                keep.append(s)
        if not keep:
            raise GeometryError("The rib does not meet the body - extend the sketch line towards "
                                "the model or flip the side")
        if self.direction == "parallel" and len(keep) > 1:
            # closed in on both sides of the line: the smaller side (flip: the larger one)
            keep.sort(key=geometry.volume)
            keep = [keep[-1] if self.flip else keep[0]]
        return keep[0] if len(keep) == 1 else geometry.fuse_all(keep)

    def params(self):
        return {"sketch_id": self.sketch_id, "thickness": self.thickness,
                "direction": self.direction, "side": self.side, "flip": self.flip}

    @classmethod
    def _from_params(cls, d):
        return cls(d["sketch_id"], d["thickness"], d.get("direction", "parallel"),
                   d.get("side", "both"), d.get("flip", False))


# ============================================================================================
# Wrap
# ============================================================================================


@register
class WrapFeature(Feature):
    """Wrap a sketch's closed profiles onto a cylindrical face: emboss (raised) or deboss
    (recessed) by `depth`. The sketch plane must be parallel to the cylinder axis (tangent
    to it, typically); circumferential lengths are kept as arc lengths."""

    TYPE = "wrap"
    NAME_PREFIX = "Wrap"

    def __init__(self, sketch_id="", face=None, depth=1.0, kind="emboss", regions=None, **kw):
        super().__init__(**kw)
        self.sketch_id = sketch_id
        self.face = None if face is None else face_ref(face)
        self.depth = float(depth)
        self.kind = kind
        self.regions = [list(map(float, p)) for p in (regions or [])]

    def depends_on(self):
        return [self.sketch_id]

    def execute(self, ctx):
        _need_body(ctx, "Wrap")
        if self.face is None:
            raise FeatureError("Pick the cylindrical face to wrap onto")
        sk, faces = profile_faces(ctx, self.sketch_id, self.regions)
        r = resolver(ctx)
        f = r.faces[_run(r.face, self.face)]
        if f.kind != "cylinder":
            raise FeatureError("Wrap needs a cylindrical face")
        solids = _run(modeling.wrap_solids, faces, sk.plane, f.axis_point, f.axis, f.radius,
                      self.depth, self.kind != "deboss")
        tool = solids[0] if len(solids) == 1 else geometry.compound(solids)
        op = "cut" if self.kind == "deboss" else "boss"
        apply_tool(ctx, op, tool)
        self.tools = [(tool, op)]

    def params(self):
        return {"sketch_id": self.sketch_id, "face": self.face.to_dict() if self.face else None,
                "depth": self.depth, "kind": self.kind, "regions": self.regions}

    @classmethod
    def _from_params(cls, d):
        return cls(d["sketch_id"], d.get("face"), d.get("depth", 1.0), d.get("kind", "emboss"),
                   d.get("regions"))


# ============================================================================================
# Intersect
# ============================================================================================


@register
class IntersectFeature(Feature):
    """SolidWorks Intersect with planes: the body is split into regions by planes (or planar
    faces, extended); `exclude` = points inside material regions to remove, `fill` = points
    inside closed voids to add as material (e.g. the inside of a shelled part below a capping
    plane). Everything else is merged back into one body."""

    TYPE = "intersect"
    NAME_PREFIX = "Intersect"

    def __init__(self, planes=None, exclude=None, fill=None, **kw):
        super().__init__(**kw)
        self.planes = list(planes or [])    # plane specs
        self.exclude = [list(map(float, p)) for p in (exclude or [])]
        self.fill = [list(map(float, p)) for p in (fill or [])]

    def regions(self, ctx) -> list[dict]:
        _need_body(ctx, "Intersect")
        if not self.planes:
            raise FeatureError("Pick the planes (or planar faces) to intersect with")
        L = _through_length(ctx)
        lo, hi = geometry.bounding_box(ctx.body)
        mid = (lo + hi) / 2
        faces = []
        for spec in self.planes:
            pl = _run(resolve_plane, spec, ctx)
            c = pl.to_local(mid)
            faces.append(modeling.plane_rect_face(pl.origin, pl.normal, pl.xdir, c[0] - L,
                                                  c[0] + L, c[1] - L, c[1] + L))
        return _run(modeling.volume_regions, ctx.body, faces)

    @staticmethod
    def _match(regions, p, material):
        q = np.asarray(p, float)
        cands = [r for r in regions if r["material"] == material]
        inside = [r for r in cands if modeling.point_in_solid(r["solid"], q)]
        if inside:
            return inside[0]
        if not cands:
            return None
        return min(cands, key=lambda r: float(np.linalg.norm(r["point"] - q)))

    def execute(self, ctx):
        regs = self.regions(ctx)
        drop = {id(self._match(regs, p, True)) for p in self.exclude}
        add = {id(self._match(regs, p, False)) for p in self.fill}
        keep = [r["solid"] for r in regs if (r["material"] and id(r) not in drop)
                or (not r["material"] and id(r) in add)]
        if not keep:
            raise FeatureError("Every region is excluded - nothing would be left")
        ctx.body = _run(geometry.fuse_all, keep) if len(keep) > 1 else keep[0]
        if len(keep) > 1:
            ctx.body = geometry._clean(ctx.body)

    def params(self):
        return {"planes": self.planes, "exclude": self.exclude, "fill": self.fill}

    @classmethod
    def _from_params(cls, d):
        return cls(d.get("planes"), d.get("exclude"), d.get("fill"))


# ============================================================================================
# Reference geometry and curves
# ============================================================================================


class _RefFeature(Feature):
    """Reference features change no body; they publish a plane / axis / point / wire."""

    def params(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    @classmethod
    def _from_params(cls, d):
        return cls(**{k: d[k] for k in cls.FIELDS if k in d})


@register
class RefPlaneFeature(_RefFeature):
    """Reference plane.

    mode "offset":  base plane / face + distance (negative = other side)
         "angle":   base plane + axis (a line in it) + angle
         "points":  three points
         "normal":  normal to an axis, through a point (default: the axis point)
         "mid":     midway between two parallel planes / faces
    """

    TYPE = "ref_plane"
    NAME_PREFIX = "Plane"
    FIELDS = ("mode", "base", "distance", "axis", "angle", "points", "base2", "point", "flip")

    def __init__(self, mode="offset", base=None, distance=10.0, axis=None, angle=0.0,
                 points=None, base2=None, point=None, flip=False, **kw):
        super().__init__(**kw)
        self.mode = mode
        self.base = base or {"type": "std", "name": "Front Plane"}
        self.distance = float(distance)
        self.axis = axis
        self.angle = float(angle)
        self.points = [list(map(float, p)) for p in (points or [])]
        self.base2 = base2
        self.point = None if point is None else list(map(float, point))
        self.flip = bool(flip)
        self.result_plane: SketchPlane | None = None

    def execute(self, ctx):
        self.result_plane = None
        pl = _run(self._compute, ctx)
        if self.flip:
            pl = SketchPlane(pl.origin, -pl.normal, pl.xdir, pl.name)
        self.result_plane = SketchPlane(pl.origin, pl.normal, pl.xdir, self.name)

    def _compute(self, ctx) -> SketchPlane:
        if self.mode == "offset":
            b = resolve_plane(self.base, ctx)
            return SketchPlane(b.origin + b.normal * self.distance, b.normal, b.xdir)
        if self.mode == "angle":
            b = resolve_plane(self.base, ctx)
            if self.axis is None:
                raise FeatureError("Pick the axis (a line or edge in the base plane)")
            p, a = resolve_axis(self.axis, ctx)
            m = modeling.rotation_matrix(p, a, self.angle)[:3, :3]
            x = a - b.normal * (a @ b.normal)
            return SketchPlane(p, m @ b.normal, x if np.linalg.norm(x) > 1e-9 else m @ b.xdir)
        if self.mode == "points":
            if len(self.points) != 3:
                raise FeatureError("A plane through points needs three points")
            a, b, c = (np.asarray(p) for p in self.points)
            nrm = np.cross(b - a, c - a)
            if np.linalg.norm(nrm) < 1e-12:
                raise FeatureError("The three points are on one line")
            return SketchPlane(a, nrm, b - a)
        if self.mode == "normal":
            if self.axis is None:
                raise FeatureError("Pick the axis / edge the plane is normal to")
            p, a = resolve_axis(self.axis, ctx)
            origin = np.asarray(self.point) if self.point is not None else p
            x = np.cross(a, [0, 0, 1.0]) if abs(a[2]) < 0.9 else np.cross(a, [1.0, 0, 0])
            return SketchPlane(origin, a, x)
        if self.mode == "mid":
            if self.base2 is None:
                raise FeatureError("Pick the second plane / face")
            b1, b2 = resolve_plane(self.base, ctx), resolve_plane(self.base2, ctx)
            if abs(float(b1.normal @ b2.normal)) < 1 - 1e-6:
                raise FeatureError("A mid plane needs two parallel planes / faces")
            dist = float((b2.origin - b1.origin) @ b1.normal)
            return SketchPlane(b1.origin + b1.normal * dist / 2, b1.normal, b1.xdir)
        raise FeatureError(f"Unknown plane mode {self.mode!r}")


@register
class RefAxisFeature(_RefFeature):
    """Reference axis: "points" (two points), "face" (cylinder / cone axis), "edge"
    (straight edge), "planes" (two planes intersect), "point_dir" (point + direction)."""

    TYPE = "ref_axis"
    NAME_PREFIX = "Axis"
    FIELDS = ("mode", "points", "ref", "base", "base2", "point", "dir")

    def __init__(self, mode="points", points=None, ref=None, base=None, base2=None,
                 point=None, dir=None, **kw):
        super().__init__(**kw)
        self.mode = mode
        self.points = [list(map(float, p)) for p in (points or [])]
        self.ref = ref
        self.base = base
        self.base2 = base2
        self.point = None if point is None else list(map(float, point))
        self.dir = None if dir is None else list(map(float, dir))
        self.result_axis = None

    def execute(self, ctx):
        self.result_axis = None
        self.result_axis = _run(self._compute, ctx)

    def _compute(self, ctx):
        if self.mode == "points":
            if len(self.points) != 2:
                raise FeatureError("An axis through points needs two points")
            a, b = (np.asarray(p) for p in self.points)
            if np.linalg.norm(b - a) < 1e-12:
                raise FeatureError("The two points coincide")
            return a, (b - a) / np.linalg.norm(b - a)
        if self.mode in ("face", "edge"):
            if self.ref is None:
                raise FeatureError("Pick the face or edge")
            return resolve_axis({"type": self.mode, "ref": self.ref}, ctx)
        if self.mode == "planes":
            p1, p2 = resolve_plane(self.base, ctx), resolve_plane(self.base2, ctx)
            d = np.cross(p1.normal, p2.normal)
            if np.linalg.norm(d) < 1e-9:
                raise FeatureError("The planes are parallel")
            d = d / np.linalg.norm(d)
            A = np.array([p1.normal, p2.normal, d])
            rhs = np.array([p1.normal @ p1.origin, p2.normal @ p2.origin, 0.0])
            return np.linalg.solve(A, rhs), d
        if self.mode == "point_dir":
            d = np.asarray(self.dir, float)
            return np.asarray(self.point, float), d / np.linalg.norm(d)
        raise FeatureError(f"Unknown axis mode {self.mode!r}")


@register
class RefPointFeature(_RefFeature):
    """Reference point: "coords", "arc_center" (circular edge), "face_center" (centroid)."""

    TYPE = "ref_point"
    NAME_PREFIX = "Point"
    FIELDS = ("mode", "coords", "ref")

    def __init__(self, mode="coords", coords=None, ref=None, **kw):
        super().__init__(**kw)
        self.mode = mode
        self.coords = list(map(float, coords or (0, 0, 0)))
        self.ref = ref
        self.result_point = None

    def execute(self, ctx):
        self.result_point = None
        if self.mode == "coords":
            self.result_point = np.asarray(self.coords, float)
        elif self.mode == "arc_center":
            r = resolver(ctx)
            e = r.edges[_run(r.edge, self.ref)]
            if "center" not in e:
                raise FeatureError("Pick a circular edge")
            self.result_point = np.asarray(e["center"])
        elif self.mode == "face_center":
            r = resolver(ctx)
            self.result_point = np.asarray(r.faces[_run(r.face, self.ref)].centroid)
        else:
            raise FeatureError(f"Unknown point mode {self.mode!r}")


@register
class HelixFeature(_RefFeature):
    """Helix / spiral curve (a sweep path for springs and threads).

    Placement: `sketch` = a sketch with one circle (centre, diameter; axis = the sketch
    normal), or explicit center / axis / diameter. defined_by: "pitch_revolutions",
    "height_revolutions" or "height_pitch"."""

    TYPE = "helix"
    NAME_PREFIX = "Helix/Spiral"
    FIELDS = ("sketch", "center", "axis", "diameter", "defined_by", "pitch", "revolutions",
              "height", "left_hand", "taper", "start_angle", "reverse")

    def __init__(self, sketch=None, center=None, axis=None, diameter=None,
                 defined_by="pitch_revolutions", pitch=5.0, revolutions=5.0, height=None,
                 left_hand=False, taper=0.0, start_angle=0.0, reverse=False, **kw):
        super().__init__(**kw)
        self.sketch = sketch
        self.center = None if center is None else list(map(float, center))
        self.axis = None if axis is None else list(map(float, axis))
        self.diameter = None if diameter is None else float(diameter)
        self.defined_by = defined_by
        self.pitch = float(pitch)
        self.revolutions = float(revolutions)
        self.height = None if height is None else float(height)
        self.left_hand = bool(left_hand)
        self.taper = float(taper)
        self.start_angle = float(start_angle)
        self.reverse = bool(reverse)
        self.result_wire = None

    def depends_on(self):
        return [self.sketch] if self.sketch else []

    def pitch_turns(self):
        if self.defined_by == "height_revolutions":
            if not self.height or self.revolutions <= 0:
                raise FeatureError("Give the height and the revolutions")
            return self.height / self.revolutions, self.revolutions
        if self.defined_by == "height_pitch":
            if not self.height or self.pitch <= 0:
                raise FeatureError("Give the height and the pitch")
            return self.pitch, self.height / self.pitch
        return self.pitch, self.revolutions

    def execute(self, ctx):
        self.result_wire = None
        if self.sketch:
            sk = ctx.features.get(self.sketch)
            if not isinstance(sk, SketchFeature):
                raise FeatureError("The helix sketch is missing")
            circles = [e for e in sk.sketch.curves(include_construction=True) if e.kind == "circle"]
            if len(circles) != 1:
                raise FeatureError("The helix sketch needs exactly one circle")
            c = circles[0]
            center = sk.plane.to_world(*sk.sketch.pt(c.center))
            axis, xdir, d = sk.plane.normal, sk.plane.xdir, 2 * c.r
        else:
            if self.center is None or self.axis is None or not self.diameter:
                raise FeatureError("Give the helix a sketch circle, or center, axis and diameter")
            center, axis, d = np.asarray(self.center), np.asarray(self.axis, float), self.diameter
            axis = axis / np.linalg.norm(axis)
            xdir = np.cross(axis, [0, 0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0, 0])
        if self.reverse:
            axis = -axis
        pitch, turns = self.pitch_turns()
        self.result_wire = _run(modeling.helix_wire, center, axis, xdir, d / 2, pitch, turns,
                                self.left_hand, self.taper, self.start_angle)


@register
class CurveFeature(_RefFeature):
    """Curve Through XYZ Points (interpolating spline; closed=True for a loop)."""

    TYPE = "curve"
    NAME_PREFIX = "Curve"
    FIELDS = ("points", "closed", "polyline")

    def __init__(self, points=None, closed=False, polyline=False, **kw):
        super().__init__(**kw)
        self.points = [list(map(float, p)) for p in (points or [])]
        self.closed = bool(closed)
        self.polyline = bool(polyline)
        self.result_wire = None

    def execute(self, ctx):
        self.result_wire = None
        pts = self.points + ([self.points[0]] if self.closed and self.polyline else [])
        fn = modeling.polyline_wire if self.polyline else (
            lambda p: modeling.spline_wire(p, self.closed))
        self.result_wire = _run(fn, pts)


# feature types that publish something for later features to reference
REFERENCE_TYPES = (RefPlaneFeature, RefAxisFeature, RefPointFeature, HelixFeature, CurveFeature)
TOOL_TYPES = ("extrude", "revolve", "hole", "sweep", "loft", "boundary", "rib", "wrap",
              "linear_pattern", "circular_pattern", "mirror")

__all__ = [n for n in dir() if n.endswith("Feature")] + ["METRIC", "hole_sizes", "EdgeRef",
                                                          "REFERENCE_TYPES", "TOOL_TYPES"]
