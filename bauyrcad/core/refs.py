"""References from features to model geometry that survive regeneration.

B-rep indices change whenever an earlier feature is edited, so features remember edges and
faces by a geometric signature and re-find them in the current body (the same approach as
FaceRef for simulation fixtures). Planes and axes are small dicts ("specs") that name a
standard plane / axis, a model face or edge, a reference feature, or explicit numbers.

    plane spec:  {"type": "std", "name": "Top Plane"}      axis spec: {"type": "std", "name": "y"}
                 {"type": "face", "ref": FaceRef dict}                {"type": "edge", "ref": EdgeRef dict}
                 {"type": "feature", "id": "f7"}                      {"type": "face", "ref": FaceRef dict}
                 {"type": "explicit", "plane": SketchPlane dict}      {"type": "feature", "id": "f8"}
                                                                      {"type": "sketch_line", "sketch": id, "entity": n}
                                                                      {"type": "explicit", "point": xyz, "dir": xyz}
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..fea.study import FaceRef
from .plane import REFERENCE_PLANES, SketchPlane

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


class RefError(Exception):
    pass


def _l(v):
    return None if v is None else [float(x) for x in np.asarray(v, float).ravel()]


@dataclass
class EdgeRef:
    """An edge remembered by kind, midpoint, length and ends (+ circle / line data)."""

    kind: str
    mid: list
    length: float
    p0: list | None = None
    p1: list | None = None
    center: list | None = None
    radius: float | None = None
    direction: list | None = None

    @classmethod
    def from_info(cls, e: dict) -> "EdgeRef":
        return cls(e["kind"], _l(e["mid"]), float(e["length"]), _l(e.get("p0")), _l(e.get("p1")),
                   _l(e.get("center")), None if e.get("radius") is None else float(e["radius"]),
                   _l(e.get("direction")))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    def match(self, edges):
        """Diagnose a reference without changing it; candidates use normalized scores."""
        from .reference_match import choose
        size = max(self.length, 1e-9)
        candidates = []
        for e in edges:
            if e.get("degenerate") or e["kind"] != self.kind:
                continue
            if self.direction is not None and (e.get("direction") is None or
                    abs(float(np.dot(e["direction"], self.direction))) < 1 - 1e-6):
                continue
            if self.radius is not None and (e.get("radius") is None or
                    abs(e["radius"] - self.radius) > 0.5 * self.radius):
                continue
            error = float(np.linalg.norm(np.asarray(e["mid"]) - self.mid)) + abs(e["length"] - self.length)
            exact = error <= 1e-6 * max(size, 1.0) + 1e-9
            if self.radius is not None:
                exact = exact and abs(e["radius"] - self.radius) <= 1e-6 * max(size, 1.0)
            candidates.append({"index": e["index"], "score": error / size, "exact": bool(exact)})
        return choose(candidates)

    def resolve(self, edges: list[dict]) -> int | None:
        return self.match(edges)["index"]


def edge_ref(d) -> EdgeRef:
    return d if isinstance(d, EdgeRef) else EdgeRef.from_dict(d)


def face_ref(d) -> FaceRef:
    return d if isinstance(d, FaceRef) else FaceRef.from_dict(d)


def plane_on_face(face) -> SketchPlane:
    """Sketch plane on a planar face (FaceInfo), origin = projection of the global origin.
    For faces along an axis the local (u, v) are the global coordinates (as in the API):
        +Z: u=x, v=y   -Z: u=x, v=-y   +Y: u=x, v=-z   -Y: u=x, v=z   +X: u=-z, v=y   -X: u=z, v=y
    """
    if not face.is_planar:
        raise RefError("A plane needs a planar face")
    n = np.asarray(face.normal, float)
    origin = np.dot(face.centroid, n) * n
    if abs(n[2]) > 1 - 1e-9 or abs(n[1]) > 1 - 1e-9:
        return SketchPlane(origin, n, [1.0, 0, 0], "Face")
    if abs(n[0]) > 1 - 1e-9:
        return SketchPlane(origin, n, np.cross([0, 1.0, 0], n), "Face")
    return SketchPlane.from_face(origin, n)


# --------------------------------------------------------------------------
# resolution against a regeneration context
# --------------------------------------------------------------------------


class Resolver:
    """Face / edge look-up on the current body of a regeneration, computed once."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._faces = None
        self._edges = None
        self._key = None

    def _check(self):
        if self._key is not self.ctx.body:
            self._faces = self._edges = None
            self._key = self.ctx.body

    @property
    def faces(self):
        from . import geometry
        self._check()
        if self._faces is None:
            self._faces = [] if self.ctx.body is None else geometry.face_infos(self.ctx.body)
        return self._faces

    @property
    def edges(self):
        from . import modeling
        self._check()
        if self._edges is None:
            self._edges = [] if self.ctx.body is None else modeling.edge_infos(self.ctx.body)
        return self._edges

    def face(self, ref) -> int:
        match = face_ref(ref).match(self.faces)
        i = match["index"]
        if i is None:
            raise RefError(f"Selected face reference is {match['status']} - edit the feature and reselect it")
        return i

    def edge(self, ref) -> int:
        match = edge_ref(ref).match(self.edges)
        i = match["index"]
        if i is None:
            raise RefError(f"Selected edge reference is {match['status']} - edit the feature and reselect it")
        return i


def resolver(ctx) -> Resolver:
    r = getattr(ctx, "_resolver", None)
    if r is None:
        r = Resolver(ctx)
        ctx._resolver = r
    return r


def resolve_plane(spec: dict, ctx) -> SketchPlane:
    t = spec.get("type")
    if t == "std":
        name = spec["name"]
        pl = REFERENCE_PLANES.get(name) or {"front": "Front Plane", "top": "Top Plane",
                                            "right": "Right Plane"}.get(name.lower())
        if isinstance(pl, str):
            pl = REFERENCE_PLANES[pl]
        if pl is None:
            raise RefError(f"Unknown plane {name!r}")
        return pl
    if t == "explicit":
        return SketchPlane.from_dict(spec["plane"])
    if t == "face":
        r = resolver(ctx)
        return plane_on_face(r.faces[r.face(spec["ref"])])
    if t == "feature":
        f = ctx.features.get(spec["id"])
        pl = getattr(f, "result_plane", None)
        if pl is None:
            raise RefError(f"Reference plane {getattr(f, 'name', spec['id'])} is missing or failed")
        return pl
    raise RefError(f"Unknown plane reference {spec!r}")


def resolve_axis(spec: dict, ctx) -> tuple[np.ndarray, np.ndarray]:
    """(point, unit direction) of an axis spec."""
    t = spec.get("type")
    if t == "std":
        return np.zeros(3), np.array(AXES[spec["name"].lower().lstrip("+-")])
    if t == "explicit":
        d = np.asarray(spec["dir"], float)
        return np.asarray(spec.get("point", (0, 0, 0)), float), d / np.linalg.norm(d)
    if t == "edge":
        r = resolver(ctx)
        e = r.edges[r.edge(spec["ref"])]
        if e["kind"] == "line":
            return np.asarray(e["p0"]), np.asarray(e["direction"])
        if e["kind"] in ("circle", "arc"):
            return np.asarray(e["center"]), np.asarray(e["axis"])
        raise RefError("An axis needs a straight or circular edge")
    if t == "face":
        r = resolver(ctx)
        f = r.faces[r.face(spec["ref"])]
        if f.axis is not None:
            return np.asarray(f.axis_point), np.asarray(f.axis) / np.linalg.norm(f.axis)
        if f.is_planar:
            return np.asarray(f.centroid), np.asarray(f.normal)
        raise RefError("An axis needs a cylindrical / conical face, or a planar face (normal)")
    if t == "feature":
        f = ctx.features.get(spec["id"])
        ax = getattr(f, "result_axis", None)
        if ax is None:
            raise RefError(f"Reference axis {getattr(f, 'name', spec['id'])} is missing or failed")
        return ax
    if t == "sketch_line":
        sk = ctx.features.get(spec["sketch"])
        if sk is None or not hasattr(sk, "sketch"):
            raise RefError("The sketch of the axis line is missing")
        e = sk.sketch.entities.get(spec["entity"])
        if e is None or e.kind != "line":
            raise RefError("The axis line no longer exists in its sketch")
        a = sk.plane.to_world(*sk.sketch.pt(e.p1))
        b = sk.plane.to_world(*sk.sketch.pt(e.p2))
        return a, (b - a) / np.linalg.norm(b - a)
    raise RefError(f"Unknown axis reference {spec!r}")


def describe_plane(spec: dict, features=None) -> str:
    t = spec.get("type")
    if t == "std":
        return spec["name"]
    if t == "face":
        return "Face"
    if t == "feature" and features is not None:
        f = next((x for x in features if x.id == spec["id"]), None)
        return f.name if f else "?"
    return "Plane"


def describe_axis(spec: dict, features=None) -> str:
    t = spec.get("type")
    if t == "std":
        return f"{spec['name'].upper()} axis"
    if t == "edge":
        return "Edge"
    if t == "face":
        return "Face axis"
    if t == "sketch_line":
        return "Sketch line"
    if t == "feature" and features is not None:
        f = next((x for x in features if x.id == spec["id"]), None)
        return f.name if f else "?"
    return "Axis"
