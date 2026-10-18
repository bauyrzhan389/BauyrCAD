"""Parametric features.

A part is an ordered list of features. Regeneration replays them from the
start; each feature reads and updates a RegenContext (the current body plus
look-up of earlier features). To add a new feature type (revolve, fillet,
...) subclass Feature, implement execute/params and decorate with @register.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np

from . import geometry
from .plane import SketchPlane
from .sketch import Sketch, SketchError

FEATURE_TYPES: dict[str, type["Feature"]] = {}


def register(cls):
    FEATURE_TYPES[cls.TYPE] = cls
    return cls


class FeatureError(Exception):
    pass


class CutMissesError(FeatureError):
    """A cut tool that removes no material (SolidWorks: 'does not intersect the model')."""

    def __init__(self, msg: str = "The cut does not intersect the model"):
        super().__init__(msg)


class RegenContext:
    def __init__(self):
        self.body = None                    # current TopoDS_Shape or None
        self.features: dict[str, Feature] = {}


class Feature:
    TYPE = "feature"
    NAME_PREFIX = "Feature"

    def __init__(self, id: str = "", name: str = ""):
        self.id = id
        self.name = name
        self.suppressed = False
        self.error: str | None = None       # set by regeneration
        self.warning: str | None = None     # built, but something needs attention
        # Set by regeneration for features that add / remove a tool body: [(tool, "boss" |
        # "cut")]. Patterns and mirrors replay these (not saved).
        self.tools: list = []

    def depends_on(self) -> list[str]:
        return []

    def execute(self, ctx: RegenContext) -> None:
        raise NotImplementedError

    def params(self) -> dict:
        return {}

    def to_dict(self) -> dict:
        return {"type": self.TYPE, "id": self.id, "name": self.name,
                "suppressed": self.suppressed, **self.params()}

    @classmethod
    def from_dict(cls, d: dict) -> "Feature":
        f = FEATURE_TYPES[d["type"]]._from_params(d)
        f.id, f.name, f.suppressed = d["id"], d["name"], d.get("suppressed", False)
        return f

    @classmethod
    def _from_params(cls, d: dict) -> "Feature":
        raise NotImplementedError


@register
class SketchFeature(Feature):
    TYPE = "sketch"
    NAME_PREFIX = "Sketch"

    def __init__(self, plane: SketchPlane, sketch: Sketch | None = None,
                 plane_spec: dict | None = None, **kw):
        super().__init__(**kw)
        self.plane = plane
        self.sketch = sketch or Sketch()
        # Where the plane comes from (refs.py plane spec): a model face or a reference plane
        # moves the sketch with it when earlier features change. None = fixed plane.
        self.plane_spec = plane_spec
        self.faces: list = []
        self.profile_error: str | None = None
        self._regions_key = None
        self._regions = None

    def regions(self) -> list:
        """All selectable closed areas (contours) of the sketch, cached."""
        import json
        key = json.dumps([self.plane.to_dict(), self.sketch.to_dict()], sort_keys=True)
        if key != self._regions_key:
            self._regions = geometry.sketch_regions(self.sketch, self.plane)
            self._regions_key = key
        return self._regions

    def select_regions(self, points) -> list:
        """Regions containing the given (u, v) points (the stored contour picks)."""
        regions = self.regions()
        out = []
        for p in points:
            hit = next((r for r in regions if r.contains(p)), None)
            if hit is None:
                raise FeatureError("A selected contour no longer exists in the sketch - "
                                   "edit the feature and re-select the contours")
            if hit not in out:
                out.append(hit)
        return out

    def build_faces(self) -> list:
        faces = []
        if self.sketch.curves(include_construction=False) or not self.sketch.texts:
            faces = [geometry.region_to_face(r, self.plane) for r in self.sketch.regions()]
        letters = [f for t in self.sketch.texts for f in geometry.text_faces(t, self.plane)]
        # like SolidWorks, letters inside a closed profile are holes in it (even-odd)
        return geometry.faces_with_holes(faces, letters) if faces and letters else faces + letters

    def execute(self, ctx: RegenContext) -> None:
        # An open sketch is not an error by itself - only when it is extruded.
        self.faces, self.profile_error = [], None
        if self.plane_spec is not None and self.plane_spec.get("type") != "std":
            from .refs import RefError, resolve_plane
            try:
                pl = resolve_plane(self.plane_spec, ctx)
                self.plane = SketchPlane(pl.origin, pl.normal, pl.xdir, self.plane.name or pl.name)
            except RefError as e:
                self.warning = f"Sketch plane: {e} (kept its last position)"
        if self.sketch.is_empty():
            self.profile_error = "Sketch is empty"
            return
        try:
            self.faces = self.build_faces()
        except (SketchError, geometry.GeometryError) as e:
            self.profile_error = str(e)

    def path_wire(self):
        """The sketch's curves as one connected wire (a sweep path)."""
        from . import modeling
        edges = geometry.sketch_edges(self.sketch, self.plane)
        if not edges:
            raise FeatureError(f"{self.name} has no curves for a path")
        return modeling.wire_from_edges(edges)

    def params(self) -> dict:
        d = {"plane": self.plane.to_dict(), "sketch": self.sketch.to_dict()}
        if self.plane_spec is not None:
            d["plane_spec"] = self.plane_spec
        return d

    @classmethod
    def _from_params(cls, d):
        return cls(SketchPlane.from_dict(d["plane"]), Sketch.from_dict(d["sketch"]),
                   d.get("plane_spec"))


def profile_faces(ctx: RegenContext, sketch_id: str, regions):
    """(sketch feature, planar faces) to extrude/revolve: selected contours or all profiles."""
    sk = ctx.features.get(sketch_id)
    if not isinstance(sk, SketchFeature):
        raise FeatureError("Referenced sketch is missing")
    if sk.error:
        raise FeatureError(f"{sk.name}: {sk.error}")
    if regions:
        return sk, [r.face for r in sk.select_regions(regions)]
    if sk.profile_error:
        hint = (" - or select the contours to use" if "cross" in sk.profile_error
                or "branch" in sk.profile_error else "")
        raise FeatureError(f"{sk.name}: {sk.profile_error}{hint}")
    if not sk.faces:
        raise FeatureError(f"{sk.name} has no closed profiles")
    return sk, sk.faces


def _volume_tol(body) -> float:
    return 1e-9 * max(geometry.volume(body), 1.0)


def removes_material(body, tool) -> bool:
    """True when subtracting `tool` from `body` would remove a non-zero volume."""
    return geometry.volume(geometry.common(body, tool)) > _volume_tol(body)


def apply_tool(ctx: RegenContext, operation: str, tool) -> None:
    """Boss: fuse the tool body into the part. Cut: subtract it.

    A cut that removes nothing raises CutMissesError (the body is left unchanged).
    """
    if operation == "cut":
        if ctx.body is None:
            raise FeatureError("Nothing to cut - create a boss first")
        result = geometry.cut(ctx.body, tool)
        if geometry.count_solids(result) == 0:
            raise FeatureError("Cut removes the whole body")
        if geometry.volume(ctx.body) - geometry.volume(result) <= _volume_tol(ctx.body):
            raise CutMissesError()
    else:
        result = tool if ctx.body is None else geometry.fuse(ctx.body, tool)
    ctx.body = result


END_CONDITIONS = {
    "blind": "Blind",
    "midplane": "Mid Plane",
    "through_all": "Through All",
    "through_all_both": "Through All - Both",
}


@register
class ExtrudeFeature(Feature):
    """Extruded boss (operation='boss') or extruded cut (operation='cut')."""

    TYPE = "extrude"

    def __init__(self, sketch_id: str, depth: float = 10.0, operation: str = "boss",
                 end: str = "blind", reverse: bool = False, regions=None, **kw):
        super().__init__(**kw)
        self.sketch_id = sketch_id
        self.depth = float(depth)
        self.operation = operation
        self.end = end
        self.reverse = bool(reverse)
        # Selected contours as interior points (u, v) of the sketch; empty = all profiles.
        self.regions = [list(map(float, p)) for p in (regions or [])]
        # Set by regeneration/preview: the default cut direction missed the body and the
        # opposite one was used (not saved - it is recomputed every time).
        self.flipped = False

    @property
    def NAME_PREFIX(self):  # noqa: N802 - mirrors the class attribute on other features
        return "Cut-Extrude" if self.operation == "cut" else "Boss-Extrude"

    def depends_on(self) -> list[str]:
        return [self.sketch_id]

    def direction(self, sk: SketchFeature) -> np.ndarray:
        sign = -1.0 if self.operation == "cut" else 1.0
        if self.reverse:
            sign = -sign
        return sign * sk.plane.normal

    def _through_length(self, ctx: RegenContext, sk: SketchFeature) -> float:
        pts = [sk.plane.origin]
        if ctx.body is not None:
            pts += list(geometry.bounding_box(ctx.body))
        b = sk.sketch.bounds()
        if b is not None:
            pts += [sk.plane.to_world(*b[0]), sk.plane.to_world(*b[1])]
        pts = np.array(pts)
        return 2.0 * float(np.linalg.norm(pts.max(0) - pts.min(0))) + 10.0

    def auto_direction(self, ctx: RegenContext) -> bool:
        """A one-sided cut in the default direction may flip into the material.

        Like SolidWorks, a cut sketched on a boundary of the body (e.g. on the plane the body
        was extruded from) goes into the material whichever way the sketch normal points.
        An explicit reverse=True is always honoured.
        """
        return (self.operation == "cut" and not self.reverse and ctx.body is not None
                and self.end in ("blind", "through_all"))

    def tool_shape(self, ctx: RegenContext):
        """The tool body in the direction regeneration will use (for previews)."""
        sk, faces = profile_faces(ctx, self.sketch_id, self.regions)
        d = self.direction(sk)
        tool = self._tool(ctx, sk, faces, d)
        self.flipped = False
        if self.auto_direction(ctx) and not removes_material(ctx.body, tool):
            other = self._tool(ctx, sk, faces, -d)
            if removes_material(ctx.body, other):
                self.flipped = True
                return other
        return tool

    def _tool(self, ctx: RegenContext, sk: SketchFeature, faces, d):
        if self.end == "blind":
            if self.depth <= 0:
                raise FeatureError("Depth must be positive")
            solids = [geometry.prism(f, d * self.depth) for f in faces]
        elif self.end == "midplane":
            if self.depth <= 0:
                raise FeatureError("Depth must be positive")
            solids = [geometry.prism(geometry.translate(f, -d * self.depth / 2), d * self.depth)
                      for f in faces]
        elif self.end == "through_all":
            L = self._through_length(ctx, sk)
            solids = [geometry.prism(f, d * L) for f in faces]
        elif self.end == "through_all_both":
            L = self._through_length(ctx, sk)
            solids = [geometry.prism(geometry.translate(f, -d * L), d * 2 * L) for f in faces]
        else:
            raise FeatureError(f"Unknown end condition {self.end!r}")
        if len(solids) == 1:
            return solids[0]
        if self.regions:          # adjacent picked contours merge into one body
            return geometry.fuse_all(solids)
        return geometry.compound(solids)

    def execute(self, ctx: RegenContext) -> None:
        sk, faces = profile_faces(ctx, self.sketch_id, self.regions)
        d = self.direction(sk)
        self.flipped = False
        tool = self._tool(ctx, sk, faces, d)
        try:
            apply_tool(ctx, self.operation, tool)
        except CutMissesError:
            if not self.auto_direction(ctx):
                raise
            tool = self._tool(ctx, sk, faces, -d)
            apply_tool(ctx, self.operation, tool)
            self.flipped = True
        self.tools = [(tool, self.operation)]

    def params(self) -> dict:
        return {"sketch_id": self.sketch_id, "depth": self.depth, "operation": self.operation,
                "end": self.end, "reverse": self.reverse, "regions": self.regions}

    @classmethod
    def _from_params(cls, d):
        return cls(d["sketch_id"], d["depth"], d["operation"], d["end"], d["reverse"],
                   d.get("regions", []))


REVOLVE_ENDS = {"blind": "Blind", "midplane": "Mid Plane"}


@register
class RevolveFeature(Feature):
    """Revolved boss (operation='boss') or revolved cut (operation='cut').

    The axis is a line in the profile sketch or another earlier sketch. With axis_id=None
    the profile sketch's only centerline is used.
    """

    TYPE = "revolve"

    def __init__(self, sketch_id: str, angle: float = 360.0, operation: str = "boss",
                 axis_id: int | None = None, end: str = "blind", reverse: bool = False,
                 regions=None, axis_sketch_id: str | None = None, axis_spec=None, **kw):
        super().__init__(**kw)
        self.sketch_id = sketch_id
        self.angle = float(angle)             # degrees
        self.operation = operation
        self.axis_id = axis_id
        self.axis_sketch_id = axis_sketch_id
        self.axis_spec = axis_spec
        self.end = end
        self.reverse = bool(reverse)
        self.regions = [list(map(float, p)) for p in (regions or [])]

    @property
    def NAME_PREFIX(self):  # noqa: N802
        return "Cut-Revolve" if self.operation == "cut" else "Revolve"

    def axis_line(self, sk: SketchFeature):
        """The sketch Line used as the axis."""
        from .sketch import Line
        s = sk.sketch
        if self.axis_id is not None:
            e = s.entities.get(self.axis_id)
            if not isinstance(e, Line):
                raise FeatureError("The revolve axis line no longer exists in the sketch")
            return e
        centerlines = [e for e in s.entities.values() if isinstance(e, Line) and e.construction]
        if len(centerlines) == 1:
            return centerlines[0]
        if not centerlines:
            raise FeatureError("Add a centerline to the sketch (or pick a line) as the revolve axis")
        raise FeatureError("The sketch has several centerlines - pick the revolve axis")

    def depends_on(self) -> list[str]:
        ids = [self.sketch_id] + ([self.axis_sketch_id] if self.axis_sketch_id else [])
        if self.axis_spec and self.axis_spec.get("type") == "feature":
            ids.append(self.axis_spec["id"])
        return list(dict.fromkeys(ids))

    def profile_context(self, ctx):
        """An isolated ordinary axis line is not part of the closed profile.

        Retry an invalid profile with just the chosen axis treated as construction;
        preserve ordinary lines that already form valid closed profile boundaries.
        This is a temporary copy, never a mutation of the user's sketch.
        """
        sk = ctx.features.get(self.sketch_id)
        if (not self.axis_spec and isinstance(sk, SketchFeature) and sk.profile_error and self.axis_id is not None
                and self.axis_sketch_id in (None, sk.id)):
            from .sketch import Line
            axis = sk.sketch.entities.get(self.axis_id)
            if isinstance(axis, Line) and not axis.construction:
                clone = SketchFeature.from_dict(sk.to_dict())
                clone.sketch.entities[self.axis_id].construction = True
                clone.execute(ctx)
                copied = RegenContext()
                copied.body, copied.features = ctx.body, dict(ctx.features)
                copied.features[sk.id] = clone
                return copied
        return ctx

    def axis_world(self, sk: SketchFeature, ctx=None):
        if self.axis_spec is not None:
            from .refs import resolve_axis, resolver, RefError
            if ctx is None:
                raise FeatureError("The revolve axis needs a model context")
            spec = self.axis_spec
            if spec.get("type") == "feature":
                f = ctx.features.get(spec["id"])
                if f is None or f.suppressed or f.error or f.TYPE != "ref_axis":
                    raise FeatureError("The revolve reference axis is missing, suppressed or invalid")
            try:
                if spec.get("type") == "edge":
                    r = resolver(ctx)
                    if r.edges[r.edge(spec["ref"])]["kind"] != "line":
                        raise FeatureError("Select a straight model edge as the revolve axis")
                pa, d = resolve_axis(spec, ctx)
            except RefError as e:
                raise FeatureError(str(e)) from e
            pa, d = np.asarray(pa, float), np.asarray(d, float)
            if not np.all(np.isfinite(pa)) or not np.all(np.isfinite(d)) or np.linalg.norm(d) < 1e-9:
                raise FeatureError("The revolve axis must have a finite, nonzero direction")
            d = d / np.linalg.norm(d)
            if abs((pa - sk.plane.origin) @ sk.plane.normal) > 1e-6 or abs(d @ sk.plane.normal) > 1e-6:
                raise FeatureError("The revolve axis line must lie in the profile sketch plane")
            return pa, d, (np.array(sk.plane.to_local(pa)), np.array(sk.plane.to_local(pa + d)))
        source = sk
        if self.axis_sketch_id and self.axis_sketch_id != sk.id:
            source = ctx.features.get(self.axis_sketch_id) if ctx else None
            if not isinstance(source, SketchFeature) or source.suppressed:
                raise FeatureError("The revolve axis sketch is missing, suppressed or invalid")
        ln = self.axis_line(source)
        a, b = source.sketch.pt(ln.p1), source.sketch.pt(ln.p2)
        pa, pb = source.plane.to_world(*a), source.plane.to_world(*b)
        d = pb - pa
        if np.linalg.norm(d) < 1e-9:
            raise FeatureError("The revolve axis line has zero length")
        if source.error:
            raise FeatureError(f"The revolve axis sketch is invalid: {source.error}")
        if max(abs((p - sk.plane.origin) @ sk.plane.normal) for p in (pa, pb)) > 1e-6:
            raise FeatureError("The revolve axis line must lie in the profile sketch plane")
        return pa, d / np.linalg.norm(d), (np.array(sk.plane.to_local(pa)), np.array(sk.plane.to_local(pb)))

    def _check_side(self, sk, faces, a2, b2):
        """A profile may touch the axis but must not cross it."""
        d = (b2 - a2) / np.linalg.norm(b2 - a2)
        tol = 1e-6 * max(sk.sketch.scale(), 1.0)
        for r in (sk.select_regions(self.regions) if self.regions else sk.regions()):
            pts = r.triangles.reshape(-1, 2)
            side = d[0] * (pts[:, 1] - a2[1]) - d[1] * (pts[:, 0] - a2[0])
            if side.max() > tol and side.min() < -tol:
                raise FeatureError("A profile crosses the revolve axis - it must lie on one side")

    def tool_shape(self, ctx: RegenContext):
        ctx = self.profile_context(ctx)
        sk, faces = profile_faces(ctx, self.sketch_id, self.regions)
        origin, direction, (a2, b2) = self.axis_world(sk, ctx)
        self._check_side(sk, faces, np.asarray(a2), np.asarray(b2))
        if not 0 < self.angle <= 360:
            raise FeatureError("Angle must be between 0 and 360 degrees")
        ang = math.radians(self.angle) * (-1 if self.reverse else 1)
        if self.end == "midplane":
            faces = [geometry.rotate(f, origin, direction, -ang / 2) for f in faces]
        solids = [geometry.revolve(f, origin, direction, ang) for f in faces]
        if len(solids) == 1:
            return solids[0]
        return geometry.fuse_all(solids) if self.regions else geometry.compound(solids)

    def execute(self, ctx: RegenContext) -> None:
        tool = self.tool_shape(ctx)
        apply_tool(ctx, self.operation, tool)
        self.tools = [(tool, self.operation)]

    def params(self) -> dict:
        return {"sketch_id": self.sketch_id, "angle": self.angle, "operation": self.operation,
                "axis_id": self.axis_id, "end": self.end, "reverse": self.reverse,
                "axis_sketch_id": self.axis_sketch_id,
                "axis_spec": self.axis_spec,
                "regions": self.regions}

    @classmethod
    def _from_params(cls, d):
        return cls(d["sketch_id"], d["angle"], d["operation"], d.get("axis_id"), d.get("end", "blind"),
                   d.get("reverse", False), d.get("regions", []), axis_sketch_id=d.get("axis_sketch_id"),
                   axis_spec=d.get("axis_spec"))


_STEP_CACHE: dict[str, object] = {}


@register
class ImportFeature(Feature):
    """A body imported from STEP / IGES / BREP. The file content is embedded in the project."""

    TYPE = "import_step"      # historical name, also covers IGES and BREP
    NAME_PREFIX = "Imported"

    def __init__(self, filename: str, step_text: str, format: str = "step", **kw):
        super().__init__(**kw)
        self.filename = filename
        self.step_text = step_text
        self.format = format

    def shape(self):
        from .exchange import read_text
        key = self.format + hashlib.sha1(self.step_text.encode("utf-8", "replace")).hexdigest()
        if key not in _STEP_CACHE:
            _STEP_CACHE[key] = read_text(self.format, self.step_text)
        return _STEP_CACHE[key]

    def execute(self, ctx: RegenContext) -> None:
        shape = self.shape()
        ctx.body = shape if ctx.body is None else geometry.fuse(ctx.body, shape)

    def params(self) -> dict:
        return {"filename": self.filename, "step_text": self.step_text, "format": self.format}

    @classmethod
    def _from_params(cls, d):
        return cls(d["filename"], d["step_text"], d.get("format", "step"))


# applied / advanced features register themselves on import
from . import features_more  # noqa: E402,F401
