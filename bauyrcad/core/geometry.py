"""Thin wrappers around the OpenCASCADE (OCP) geometry kernel.

Everything that touches OCC types lives here (and in step_io.py) so the rest
of the application works with plain Python/numpy data. Swapping or upgrading
the kernel should only require changes in these modules.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.GeomAbs import (GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_Cone,
                         GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus)
from OCP.GProp import GProp_GProps
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Shape
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

from .plane import SketchPlane


def _quiet_occ():
    """OCC's translators print statistics to stdout; keep stdout clean for the CLI/JSON."""
    try:
        from OCP.Message import Message
        Message.DefaultMessenger_s().ChangePrinters().Clear()
    except Exception:  # pragma: no cover
        pass


_quiet_occ()
from .sketch import CircleSeg, LineSeg, Loop, Region


class GeometryError(Exception):
    pass


# --------------------------------------------------------------------------
# Small conversions
# --------------------------------------------------------------------------


def _pnt(p) -> gp_Pnt:
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def _dir(v) -> gp_Dir:
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _vec(v) -> gp_Vec:
    return gp_Vec(float(v[0]), float(v[1]), float(v[2]))


def _xyz(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()])


# --------------------------------------------------------------------------
# Sketch profiles -> faces
# --------------------------------------------------------------------------


def spline_edge(pts2d, plane: SketchPlane):
    """Cubic interpolating B-spline through points, same parametrisation/end tangents as
    the 2D sketch display (chord length, not-a-knot)."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.TColgp import TColgp_HArray1OfPnt
    from OCP.TColStd import TColStd_HArray1OfReal
    from .sketch.model import spline_eval, spline_params

    pts2d = np.asarray(pts2d, float)
    n = len(pts2d)
    arr = TColgp_HArray1OfPnt(1, n)
    for i, p in enumerate(pts2d):
        arr.SetValue(i + 1, _pnt(plane.to_world(*p)))
    params = TColStd_HArray1OfReal(1, n)
    for i, t in enumerate(spline_params(pts2d)):
        params.SetValue(i + 1, float(t))
    interp = GeomAPI_Interpolate(arr, params, False, 1e-9)
    _, (t0, t1) = spline_eval(pts2d, 3)
    w0 = plane.xdir * t0[0] + plane.ydir * t0[1]
    w1 = plane.xdir * t1[0] + plane.ydir * t1[1]
    interp.Load(_vec(w0), _vec(w1), False)
    interp.Perform()
    if not interp.IsDone():
        raise GeometryError("Spline interpolation failed")
    return BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()


def loop_to_wire(loop: Loop, plane: SketchPlane):
    from OCP.GC import GC_MakeArcOfCircle
    from .sketch.profiles import ArcSeg, SplineSeg

    mk = BRepBuilderAPI_MakeWire()
    for seg in loop.segments:
        if isinstance(seg, LineSeg):
            a, b = plane.to_world(*seg.a), plane.to_world(*seg.b)
            edge = BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge()
        elif isinstance(seg, ArcSeg):
            p = [_pnt(plane.to_world(*q)) for q in (seg.a, seg.mid(), seg.b)]
            arc = GC_MakeArcOfCircle(p[0], p[1], p[2])
            if not arc.IsDone():
                raise GeometryError("Could not build an arc of the sketch profile")
            edge = BRepBuilderAPI_MakeEdge(arc.Value()).Edge()
        elif isinstance(seg, SplineSeg):
            edge = spline_edge(seg.pts, plane)
        elif isinstance(seg, CircleSeg):
            n = plane.normal if seg.ccw else -plane.normal
            ax = gp_Ax2(_pnt(plane.to_world(*seg.center)), _dir(n), _dir(plane.xdir))
            edge = BRepBuilderAPI_MakeEdge(gp_Circ(ax, seg.radius)).Edge()
        else:
            raise GeometryError(f"Unsupported segment {seg!r}")
        mk.Add(edge)
    if not mk.IsDone():
        raise GeometryError("Could not build a closed wire from the sketch profile")
    return mk.Wire()


def region_to_face(region: Region, plane: SketchPlane):
    pln = gp_Pln(gp_Ax3(_pnt(plane.origin), _dir(plane.normal), _dir(plane.xdir)))
    mk = BRepBuilderAPI_MakeFace(pln, loop_to_wire(region.outer, plane), True)
    for hole in region.holes:
        mk.Add(loop_to_wire(hole, plane))
    if not mk.IsDone():
        raise GeometryError("Could not build a face from the sketch profile")
    fix = ShapeFix_Face(mk.Face())
    fix.FixOrientation()
    fix.Perform()
    return fix.Face()


def sketch_edges(sketch, plane: SketchPlane) -> list:
    """3D edges of all non-construction curves of a sketch."""
    from OCP.GC import GC_MakeArcOfCircle
    from .sketch import Arc, Circle, Line, Spline
    from .sketch.profiles import ArcSeg

    edges = []
    for e in sketch.curves(include_construction=False):
        if isinstance(e, Line):
            a, b = plane.to_world(*sketch.pt(e.p1)), plane.to_world(*sketch.pt(e.p2))
            edges.append(BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge())
        elif isinstance(e, Circle):
            ax = gp_Ax2(_pnt(plane.to_world(*sketch.pt(e.center))), _dir(plane.normal), _dir(plane.xdir))
            edges.append(BRepBuilderAPI_MakeEdge(gp_Circ(ax, e.r)).Edge())
        elif isinstance(e, Arc):
            seg = ArcSeg(tuple(sketch.pt(e.center)), tuple(sketch.pt(e.start)), tuple(sketch.pt(e.end)))
            p = [_pnt(plane.to_world(*q)) for q in (seg.a, seg.mid(), seg.b)]
            edges.append(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p[0], p[1], p[2]).Value()).Edge())
        elif isinstance(e, Spline):
            edges.append(spline_edge([sketch.pt(p) for p in e.pts], plane))
    return edges


@dataclass
class SketchRegion:
    """One bounded area of a sketch's planar arrangement (SolidWorks 'contour')."""
    face: object                 # planar TopoDS_Face in 3D
    area: float
    point: np.ndarray            # an interior point in sketch (u, v) coordinates
    triangles: np.ndarray        # (m, 3, 2) triangulation in sketch coordinates

    def contains(self, uv) -> bool:
        p = np.asarray(uv, float)
        a, b, c = self.triangles[:, 0], self.triangles[:, 1], self.triangles[:, 2]

        def side(p1, p2):
            return (p2[:, 0] - p1[:, 0]) * (p[1] - p1[:, 1]) - (p2[:, 1] - p1[:, 1]) * (p[0] - p1[:, 0])
        s1, s2, s3 = side(a, b), side(b, c), side(c, a)
        eps = -1e-12
        inside = ((s1 >= eps) & (s2 >= eps) & (s3 >= eps)) | ((s1 <= -eps) & (s2 <= -eps) & (s3 <= -eps))
        return bool(inside.any())


def sketch_regions(sketch, plane: SketchPlane) -> list[SketchRegion]:
    """Every bounded area enclosed by the sketch curves, even where curves cross.

    The sketch plane is split by all curves (OCC General Fuse splitter); the piece that
    touches the outside of the working rectangle is the unbounded exterior and is dropped.
    Dangling curves bound nothing and are ignored.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.TopTools import TopTools_ListOfShape

    edges = sketch_edges(sketch, plane)
    b = sketch.bounds()
    if not edges or b is None:
        return text_regions(sketch, plane) if b is not None else []
    lo, hi = np.asarray(b[0]), np.asarray(b[1])
    pad = 0.1 * max(float(np.linalg.norm(hi - lo)), 1.0)
    u0, v0 = lo - pad
    u1, v1 = hi + pad
    pln = gp_Pln(gp_Ax3(_pnt(plane.origin), _dir(plane.normal), _dir(plane.xdir)))
    base = BRepBuilderAPI_MakeFace(pln, float(u0), float(u1), float(v0), float(v1)).Face()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(base)
    for e in edges:
        tools.Append(e)
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(args)
    sp.SetTools(tools)
    sp.Build()
    if not sp.IsDone():
        raise GeometryError("Could not compute the sketch contours")
    out = []
    eps = pad * 1e-6
    for f in indexed_map(sp.Shape(), TopAbs_FACE):
        face = TopoDS.Face_s(f)
        flo, fhi = bounding_box(face)
        # exterior piece: reaches the working rectangle's border
        cu = np.array([plane.to_local(p) for p in _bbox_corners(flo, fhi)])
        if cu[:, 0].min() <= u0 + eps or cu[:, 0].max() >= u1 - eps or \
                cu[:, 1].min() <= v0 + eps or cu[:, 1].max() >= v1 - eps:
            continue
        t = tessellate(face, lin_deflection=pad * 2e-3)
        if not len(t.triangles):
            continue
        uv = np.array([plane.to_local(p) for p in t.points])
        tri = uv[t.triangles]
        areas = 0.5 * np.abs((tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) -
                             (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1]))
        k = int(np.argmax(areas))
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        out.append(SketchRegion(face, props.Mass(), tri[k].mean(axis=0), tri))
    out.sort(key=lambda r: (-r.area, round(float(r.point[0]), 6), round(float(r.point[1]), 6)))
    return out + text_regions(sketch, plane)


# --------------------------------------------------------------------------
# Sketch text
# --------------------------------------------------------------------------

_TEXT_CACHE: dict = {}
_CAP_CACHE: dict = {}


def _font(font, bold, italic, size):
    from OCP.Font import (Font_FontAspect_Bold, Font_FontAspect_BoldItalic,
                          Font_FontAspect_Italic, Font_FontAspect_Regular)
    from OCP.NCollection import NCollection_Utf8String
    from OCP.StdPrs import StdPrs_BRepFont
    aspect = {(False, False): Font_FontAspect_Regular, (True, False): Font_FontAspect_Bold,
              (False, True): Font_FontAspect_Italic, (True, True): Font_FontAspect_BoldItalic}[
        (bool(bold), bool(italic))]
    f = StdPrs_BRepFont()
    if not f.Init(NCollection_Utf8String(font), aspect, float(size)):
        if not f.Init(NCollection_Utf8String("Arial"), aspect, float(size)):
            raise GeometryError(f"Font {font!r} is not installed")
    return f


def _text_local(text, font, bold, italic, size):
    """Letter faces in the XY plane, baseline start at the origin (cached)."""
    key = (text, font, bool(bold), bool(italic), round(float(size), 9))
    if key not in _TEXT_CACHE:
        from OCP.NCollection import NCollection_Utf8String
        from OCP.StdPrs import StdPrs_BRepTextBuilder
        shape = StdPrs_BRepTextBuilder().Perform(_font(font, bold, italic, size),
                                                  NCollection_Utf8String(text), gp_Ax3())
        _TEXT_CACHE[key] = shape
    return _TEXT_CACHE[key]


def _cap_ratio(font, bold, italic) -> float:
    """Capital letter height per font size (so `height` is the height of an "H")."""
    key = (font, bool(bold), bool(italic))
    if key not in _CAP_CACHE:
        lo, hi = bounding_box(_text_local("H", font, bold, italic, 100.0))
        _CAP_CACHE[key] = max((hi[1] - lo[1]) / 100.0, 0.3)
    return _CAP_CACHE[key]


def text_faces(t: dict, plane: SketchPlane) -> list:
    """Planar faces (with holes for letter counters) of one sketch text item, in 3D."""
    size = t["height"] / _cap_ratio(t["font"], t["bold"], t["italic"])
    local = _text_local(t["text"], t["font"], t["bold"], t["italic"], size)
    a = np.radians(t.get("angle", 0.0))
    x = np.cos(a) * plane.xdir + np.sin(a) * plane.ydir
    y = np.cross(plane.normal, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = x, y, plane.normal
    m[:3, 3] = plane.to_world(*t["at"])
    placed = transformed(local, m)
    return [TopoDS.Face_s(f) for f in indexed_map(placed, TopAbs_FACE)]


def faces_with_holes(faces, holes) -> list:
    """Planar faces minus the hole faces lying on them; holes outside every face are kept as
    faces of their own (even-odd rule of sketch profiles)."""
    used = set()
    out = []
    for f in faces:
        inner = []
        for k, h in enumerate(holes):
            c = common(f, h)
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(c, props)
            if props.Mass() > 1e-12:
                inner.append(h)
                used.add(k)
        if not inner:
            out.append(f)
            continue
        out += [TopoDS.Face_s(x) for x in indexed_map(cut(f, compound(inner)), TopAbs_FACE)]
    return out + [h for k, h in enumerate(holes) if k not in used]


def text_outlines(t: dict, plane: SketchPlane) -> list:
    """Outline polylines (u, v) of a text item, for drawing it in the sketch."""
    out = []
    for f in text_faces(t, plane):
        for e in indexed_map(f, TopAbs_EDGE):
            c = BRepAdaptor_Curve(TopoDS.Edge_s(e))
            us = np.linspace(c.FirstParameter(), c.LastParameter(), 12)
            out.append(np.array([plane.to_local(_xyz(c.Value(u))) for u in us]))
    return out


def text_regions(sketch, plane: SketchPlane) -> list:
    """SketchRegions of the letters of all text items (selectable contours)."""
    out = []
    for t in getattr(sketch, "texts", []):
        for face in text_faces(t, plane):
            tess = tessellate(face, lin_deflection=t["height"] * 2e-3)
            if not len(tess.triangles):
                continue
            uv = np.array([plane.to_local(p) for p in tess.points])
            tri = uv[tess.triangles]
            areas = 0.5 * np.abs((tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) -
                                 (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1]))
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            out.append(SketchRegion(face, props.Mass(), tri[int(np.argmax(areas))].mean(axis=0), tri))
    return out


def _bbox_corners(lo, hi):
    return [np.array([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def fuse_all(shapes):
    shapes = list(shapes)
    if not shapes:
        raise GeometryError("Nothing to fuse")
    result = shapes[0]
    for s in shapes[1:]:
        result = fuse(result, s)
    return result


def translate(shape, vec):
    t = gp_Trsf()
    t.SetTranslation(_vec(vec))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def rotate(shape, origin, direction, angle):
    """Rigid rotation of a shape about an axis (angle in radians)."""
    from OCP.gp import gp_Ax1
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(_pnt(origin), _dir(direction)), float(angle))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def revolve(face, origin, direction, angle):
    """Solid of revolution of a planar face about an axis (angle in radians)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1
    mk = BRepPrimAPI_MakeRevol(face, gp_Ax1(_pnt(origin), _dir(direction)), float(angle), True)
    mk.Build()
    if not mk.IsDone():
        raise GeometryError("Revolve failed")
    return mk.Shape()


def transformed(shape, matrix4):
    """Shape moved by a 4x4 rigid transform (rotation + translation)."""
    from OCP.gp import gp_Trsf
    m = np.asarray(matrix4, float)
    t = gp_Trsf()
    t.SetValues(*m[0, :4], *m[1, :4], *m[2, :4])
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def prism(face, vec):
    mk = BRepPrimAPI_MakePrism(face, _vec(vec))
    mk.Build()
    if not mk.IsDone():
        raise GeometryError("Extrusion failed")
    return mk.Shape()


def compound(shapes) -> TopoDS_Compound:
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


# --------------------------------------------------------------------------
# Booleans and clean-up
# --------------------------------------------------------------------------


def _clean(shape):
    try:
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        u.Build()
        return u.Shape()
    except Exception:  # pragma: no cover - clean-up is best-effort
        return shape


def fuse(a, b):
    op = BRepAlgoAPI_Fuse(a, b)
    op.Build()
    if not op.IsDone():
        raise GeometryError("Boolean union failed")
    return _clean(op.Shape())


def cut(a, b):
    op = BRepAlgoAPI_Cut(a, b)
    op.Build()
    if not op.IsDone():
        raise GeometryError("Boolean cut failed")
    return _clean(op.Shape())


def _boolean_many(op, body, tools, what):
    from OCP.TopTools import TopTools_ListOfShape
    args, tl = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(body)
    for t in tools:
        tl.Append(t)
    op.SetArguments(args)
    op.SetTools(tl)
    op.Build()
    if not op.IsDone():
        raise GeometryError(f"Boolean {what} failed")
    return _clean(op.Shape())


def fuse_many(body, tools):
    """Body united with several tool bodies in one boolean (patterns)."""
    return _boolean_many(BRepAlgoAPI_Fuse(), body, tools, "union")


def cut_many(body, tools):
    """Body minus several (possibly overlapping) tool bodies in one boolean."""
    return _boolean_many(BRepAlgoAPI_Cut(), body, tools, "cut")


def common(a, b):
    op = BRepAlgoAPI_Common(a, b)
    op.Build()
    if not op.IsDone():
        raise GeometryError("Boolean intersection failed")
    return op.Shape()


def min_distance(a, b) -> tuple[float, np.ndarray, np.ndarray]:
    """Exact minimum distance between two shapes and the closest points (on a, on b).
    0 when they touch or their boundaries cross; a solid entirely inside another reports the
    gap between the surfaces, so check common_volume() for containment."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    d = BRepExtrema_DistShapeShape(a, b)
    d.Perform()
    if not d.IsDone() or d.NbSolution() == 0:
        raise GeometryError("Distance computation failed")
    return float(d.Value()), _xyz(d.PointOnShape1(1)), _xyz(d.PointOnShape2(1))


_SUB_KINDS = {"face": TopAbs_FACE, "edge": TopAbs_EDGE, "vertex": TopAbs_VERTEX}


def subshape(shape, kind: str, index: int):
    """The index-th face / edge / vertex of a shape (the numbering face_infos, tessellate
    and vertices use)."""
    items = indexed_map(shape, _SUB_KINDS[kind])
    if not 0 <= index < len(items):
        raise GeometryError(f"No {kind} {index}")
    return items[index]


def edge_info(shape, index: int) -> dict:
    """Length and shape of an edge: line (ends, direction), circle / arc (centre, radius,
    axis) or curve."""
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    e = TopoDS.Edge_s(subshape(shape, "edge", index))
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(e, props)
    c = BRepAdaptor_Curve(e)
    u0, u1 = c.FirstParameter(), c.LastParameter()
    out = {"kind": "curve", "length": props.Mass(), "p0": _xyz(c.Value(u0)),
           "p1": _xyz(c.Value(u1)), "mid": _xyz(c.Value((u0 + u1) / 2))}
    t = c.GetType()
    if t == GeomAbs_Line:
        d = out["p1"] - out["p0"]
        out.update(kind="line", direction=d / max(np.linalg.norm(d), 1e-300))
    elif t == GeomAbs_Circle:
        circ = c.Circle()
        full = abs(u1 - u0) >= 2 * np.pi - 1e-9
        out.update(kind="circle" if full else "arc", center=_xyz(circ.Location()),
                   radius=circ.Radius(), axis=_xyz(circ.Axis().Direction()))
    return out


def point_on_face(shape, index: int) -> np.ndarray:
    """A point strictly inside a face (away from its edges): the inside sample of a UV grid
    nearest the middle of the face's parameter range. Used to find the same face elsewhere
    (e.g. in SolidWorks)."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN
    face = TopoDS.Face_s(subshape(shape, "face", index))
    surf = BRepAdaptor_Surface(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
    best, bd = None, np.inf
    n = 11
    for i in range(1, n):
        for j in range(1, n):
            u, v = u0 + (u1 - u0) * i / n, v0 + (v1 - v0) * j / n
            cls = BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-7)
            if cls.State() == TopAbs_IN:
                d = ((u - uc) / max(u1 - u0, 1e-12)) ** 2 + ((v - vc) / max(v1 - v0, 1e-12)) ** 2
                if d < bd:
                    best, bd = (u, v), d
    if best is None:
        raise GeometryError(f"No interior point found on face {index}")
    return _xyz(surf.Value(*best))


def vertex_point(shape, index: int) -> np.ndarray:
    return _xyz(BRep_Tool.Pnt_s(TopoDS.Vertex_s(subshape(shape, "vertex", index))))


def box_distance(lo_a, hi_a, lo_b, hi_b) -> float:
    """Distance between two axis-aligned boxes (0 if they overlap): a lower bound of the
    distance between what they contain."""
    gap = np.maximum(0.0, np.maximum(np.asarray(lo_a) - hi_b, np.asarray(lo_b) - hi_a))
    return float(np.linalg.norm(gap))


def common_volume(a, b) -> float:
    """Volume shared by two solids (0 when they only touch or are apart)."""
    lo_a, hi_a = bounding_box(a)
    lo_b, hi_b = bounding_box(b)
    if np.any(hi_a < lo_b) or np.any(hi_b < lo_a):
        return 0.0
    return max(volume(common(a, b)), 0.0)


def fix_shape(shape):
    f = ShapeFix_Shape(shape)
    f.Perform()
    return f.Shape()


def is_valid(shape) -> bool:
    return BRepCheck_Analyzer(shape).IsValid()


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


def volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def center_of_mass(shape) -> np.ndarray:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return _xyz(props.CentreOfMass())


def _split_at_knots(shape):
    """The shape with every B-spline face and edge split at its interior knots (C3 breaks):
    each piece is then one polynomial span, which the fixed Gauss integration measures
    exactly. Whole multi-span B-spline faces (a sketch spline through 6 points, font
    outlines...) are measured ~1e-4 off by it, and the adaptive integration can be several %
    off on them. The original shape when the split fails, gives an invalid shape (a sweep
    along a closed periodic spline does) or changes the volume by more than 1e-3."""
    from OCP.GeomAbs import GeomAbs_C3
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideContinuity
    try:
        d = ShapeUpgrade_ShapeDivideContinuity(shape)
        d.SetBoundaryCriterion(GeomAbs_C3)
        d.SetPCurveCriterion(GeomAbs_C3)
        d.SetSurfaceCriterion(GeomAbs_C3)
        d.Perform()
        out = d.Result()
        if out is None or out.IsNull() or out.IsSame(shape):
            return shape
        if not BRepCheck_Analyzer(out).IsValid():
            return shape
        v0, v1 = volume(shape), volume(out)
        return out if abs(v1 - v0) <= 1e-3 * abs(v0) + 1e-9 else shape
    except Exception:  # noqa: BLE001 - measuring must never fail on this
        return shape


def accurate_mass_properties(shape) -> tuple[tuple[float, np.ndarray], tuple[float, np.ndarray]]:
    """((volume, centre of mass) by Gauss integration, (volume, centre of mass) by adaptive
    integration), both on the shape split at its B-spline knots (_split_at_knots): the first
    is the accurate measure, and the difference between the two is how uncertain it still is
    (0 on analytic faces). For checks against another kernel (the SolidWorks export)."""
    sh = _split_at_knots(shape)
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh, g)
    a = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh, a, 1e-9, True)
    return (g.Mass(), _xyz(g.CentreOfMass())), (a.Mass(), _xyz(a.CentreOfMass()))


def freeform_area(shape) -> float:
    """Total area (mm²) of the faces that are not plane / cylinder / cone / sphere / torus
    (B-spline, Bezier, extruded or revolved spline...): where two kernels' surfaces and
    mass integrations may differ slightly."""
    total = 0.0
    if shape is None:
        return total
    analytic = (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus)
    for f in indexed_map(shape, TopAbs_FACE):
        face = TopoDS.Face_s(f)
        if BRepAdaptor_Surface(face).GetType() not in analytic:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            total += abs(props.Mass())
    return total


def adaptive_mass_properties(shape, eps: float = 1e-9) -> tuple[float, np.ndarray]:
    """(volume, centre of mass) by OpenCASCADE's adaptive integration to the relative error
    ``eps``. The default (``volume``, ``center_of_mass``) uses a fixed Gauss order: exact on
    analytic faces, a little off on B-spline faces. The two disagree only where the measuring
    itself is uncertain, which is what the SolidWorks export's noise floor needs to know."""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, eps, True)
    return props.Mass(), _xyz(props.CentreOfMass())


def bounding_box(shape) -> tuple[np.ndarray, np.ndarray]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        return np.zeros(3), np.zeros(3)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return np.array([x0, y0, z0]), np.array([x1, y1, z1])


def count_solids(shape) -> int:
    n = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        n += 1
        exp.Next()
    return n


def is_null(shape) -> bool:
    return shape is None or shape.IsNull()


def indexed_map(shape, kind) -> list:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return [m.FindKey(i) for i in range(1, m.Extent() + 1)]


@dataclass
class FaceInfo:
    index: int
    area: float
    centroid: np.ndarray
    is_planar: bool
    normal: np.ndarray | None = None  # outward normal for planar faces
    plane_origin: np.ndarray | None = None
    kind: str = "other"               # plane | cylinder | cone | sphere | torus | bspline | other
    radius: float | None = None       # cylinders / spheres / tori (major)
    axis: np.ndarray | None = None    # cylinder / cone axis direction
    axis_point: np.ndarray | None = None

    @property
    def diameter(self) -> float | None:
        return None if self.radius is None else 2.0 * self.radius


@dataclass
class Tessellation:
    points: np.ndarray             # (n, 3)
    triangles: np.ndarray          # (m, 3) point indices
    face_ids: np.ndarray           # (m,) face index per triangle
    normals: np.ndarray            # (n, 3) per-point normals
    edge_polylines: list = field(default_factory=list)  # list of (k, 3) arrays
    edge_ids: list = field(default_factory=list)        # B-rep edge index of each polyline


def face_infos(shape) -> list[FaceInfo]:
    infos = []
    for i, f in enumerate(indexed_map(shape, TopAbs_FACE)):
        face = TopoDS.Face_s(f)
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        info = FaceInfo(i, props.Mass(), _xyz(props.CentreOfMass()), False)
        surf = BRepAdaptor_Surface(face)
        st = surf.GetType()
        if st == GeomAbs_Plane:
            pln = surf.Plane()
            # the surface normal is X x Y of the plane's axes - equal to Axis().Direction()
            # only for right-handed (direct) axes; revolved faces often have indirect ones
            pos = pln.Position()
            n = np.cross(_xyz(pos.XDirection()), _xyz(pos.YDirection()))
            if face.Orientation() == TopAbs_REVERSED:
                n = -n
            info.is_planar = True
            info.normal = n
            info.plane_origin = info.centroid
            info.kind = "plane"
        elif st == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            info.kind, info.radius = "cylinder", cyl.Radius()
            info.axis = _xyz(cyl.Axis().Direction())
            info.axis_point = _xyz(cyl.Axis().Location())
        elif st == GeomAbs_Cone:
            cone = surf.Cone()
            info.kind = "cone"
            info.axis = _xyz(cone.Axis().Direction())
            info.axis_point = _xyz(cone.Axis().Location())
        elif st == GeomAbs_Sphere:
            info.kind, info.radius = "sphere", surf.Sphere().Radius()
        elif st == GeomAbs_Torus:
            info.kind, info.radius = "torus", surf.Torus().MajorRadius()
        elif st in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
            info.kind = "bspline"
        infos.append(info)
    return infos


def vertices(shape) -> np.ndarray:
    vs = [_xyz(BRep_Tool.Pnt_s(TopoDS.Vertex_s(v))) for v in indexed_map(shape, TopAbs_VERTEX)]
    return np.array(vs) if vs else np.zeros((0, 3))


def tessellate(shape, lin_deflection: float | None = None, ang_deflection: float = 0.35) -> Tessellation:
    lo, hi = bounding_box(shape)
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    if lin_deflection is None:
        lin_deflection = diag * 1e-3
    BRepMesh_IncrementalMesh(shape, lin_deflection, False, ang_deflection, True)

    pts, tris, fids, nrms = [], [], [], []
    offset = 0
    for fi, f in enumerate(indexed_map(shape, TopAbs_FACE)):
        face = TopoDS.Face_s(f)
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        n_nodes = tri.NbNodes()
        p = np.array([_xyz(tri.Node(k).Transformed(trsf)) for k in range(1, n_nodes + 1)])
        t = np.array([tri.Triangle(k).Get() for k in range(1, tri.NbTriangles() + 1)], dtype=np.int64) - 1
        reversed_ = face.Orientation() == TopAbs_REVERSED
        if reversed_:
            t = t[:, ::-1]
        # Per-point normals from the triangle fan (area weighted).
        fn = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        pn = np.zeros_like(p)
        for c in range(3):
            np.add.at(pn, t[:, c], fn)
        pn /= np.maximum(np.linalg.norm(pn, axis=1, keepdims=True), 1e-300)
        pts.append(p)
        tris.append(t + offset)
        fids.append(np.full(len(t), fi))
        nrms.append(pn)
        offset += n_nodes

    edges, edge_ids = [], []
    for ei, e in enumerate(indexed_map(shape, TopAbs_EDGE)):
        edge = TopoDS.Edge_s(e)
        if BRep_Tool.Degenerated_s(edge):
            continue
        try:
            curve = BRepAdaptor_Curve(edge)
            disc = GCPnts_TangentialDeflection(curve, ang_deflection * 0.5, lin_deflection)
            poly = np.array([_xyz(disc.Value(k)) for k in range(1, disc.NbPoints() + 1)])
            if len(poly) >= 2:
                edges.append(poly)
                edge_ids.append(ei)
        except Exception:
            continue

    if not pts:
        return Tessellation(np.zeros((0, 3)), np.zeros((0, 3), int), np.zeros(0, int),
                            np.zeros((0, 3)), edges, edge_ids)
    return Tessellation(np.vstack(pts), np.vstack(tris), np.concatenate(fids),
                        np.vstack(nrms), edges, edge_ids)


# --------------------------------------------------------------------------
# Hidden-line projection (drawing views)
# --------------------------------------------------------------------------


@dataclass
class ProjectedEdge:
    """One edge of a drawing view, in the view plane (model mm, x right, y up).

    kind: "line" (p0, p1) | "circle" (center, r, a0, a1 in degrees, CCW; a full circle has
    a1 - a0 == 360) | "poly" (pts, a discretised ellipse or spline).
    visible: False for hidden lines.  style: "sharp" | "smooth" (tangent) | "outline"
    (silhouette of a curved face)."""
    kind: str
    visible: bool
    style: str
    p0: np.ndarray | None = None
    p1: np.ndarray | None = None
    center: np.ndarray | None = None
    r: float = 0.0
    a0: float = 0.0
    a1: float = 0.0
    pts: np.ndarray | None = None

    def points(self, tol=0.05) -> np.ndarray:
        """The edge as a polyline (for drawing and picking)."""
        if self.kind == "line":
            return np.array([self.p0, self.p1])
        if self.kind == "circle":
            span = self.a1 - self.a0
            n = max(8, int(abs(np.radians(span)) / max(np.arccos(max(1 - tol / max(self.r, 1e-9), -1)) * 2,
                                                          0.02)) + 1)
            n = min(n, 720)
            a = np.radians(np.linspace(self.a0, self.a1, n))
            return self.center + self.r * np.column_stack([np.cos(a), np.sin(a)])
        return self.pts

    def extent_points(self) -> np.ndarray:
        """Points that bound the edge exactly: ends, plus the quadrant points an arc passes
        (so overall sizes over rounded ends are exact, not a chord of the polyline)."""
        if self.kind != "circle":
            return self.points()
        a = [self.a0, self.a1]
        k = np.ceil(self.a0 / 90.0)
        while k * 90.0 <= self.a1 + 1e-9:
            a.append(k * 90.0)
            k += 1
        a = np.radians(np.array(a))
        return self.center + self.r * np.column_stack([np.cos(a), np.sin(a)])


def edges_on_plane(shape, origin, normal, tol=None) -> list[dict]:
    """Model edges lying in a plane (for sketch snapping when sketching on a face):
    [{"kind": "line" | "circle" | "curve", "pts": (n, 3) polyline, "center": xyz | None,
    "r": radius, "closed": bool}]."""
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    o = np.asarray(origin, float)
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    lo, hi = bounding_box(shape)
    size = max(float(np.linalg.norm(hi - lo)), 1.0)
    tol = tol or size * 1e-6
    out = []
    for e in indexed_map(shape, TopAbs_EDGE):
        edge = TopoDS.Edge_s(e)
        if BRep_Tool.Degenerated_s(edge):
            continue
        try:
            c = BRepAdaptor_Curve(edge)
            d = GCPnts_TangentialDeflection(c, 0.2, size * 1e-3)
            pts = np.array([_xyz(d.Value(k)) for k in range(1, d.NbPoints() + 1)])
        except Exception:  # pragma: no cover - odd edge
            continue
        if len(pts) < 2 or np.max(np.abs((pts - o) @ n)) > tol:
            continue
        t = c.GetType()
        item = {"kind": "curve", "pts": pts, "center": None, "r": 0.0,
                "closed": bool(np.linalg.norm(pts[0] - pts[-1]) < tol * 10)}
        if t == GeomAbs_Line:
            item["kind"] = "line"
        elif t == GeomAbs_Circle:
            circ = c.Circle()
            item.update(kind="circle", center=_xyz(circ.Location()), r=circ.Radius())
        out.append(item)
    return out


def view_frame(direction, xdir):
    """Orthonormal (n, x, y) of a view looking along -direction (direction points at the
    viewer), with x to the right on the sheet."""
    n = np.asarray(direction, float)
    n = n / np.linalg.norm(n)
    x = np.asarray(xdir, float)
    x = x - n * (x @ n)
    x = x / np.linalg.norm(x)
    return n, x, np.cross(n, x)


def project_hlr(shape, direction, xdir, hidden=True, smooth=True, tol=0.02) -> list[ProjectedEdge]:
    """Orthographic hidden-line-removed projection of shape (exact: OCC HLRBRep_Algo).

    The view looks from +direction towards the model; xdir is the sheet's +x. Coordinates are
    in model units in that view frame (origin = model origin)."""
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

    n, x, _ = view_frame(direction, xdir)
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), _dir(n), _dir(x))))
    algo.Update()
    algo.Hide()
    out = HLRBRep_HLRToShape(algo)
    groups = [(out.VCompound(), True, "sharp"), (out.OutLineVCompound(), True, "outline")]
    if smooth:
        groups.append((out.Rg1LineVCompound(), True, "smooth"))
    if hidden:
        groups += [(out.HCompound(), False, "sharp"), (out.OutLineHCompound(), False, "outline")]
    edges = []
    for comp, visible, style in groups:
        if comp is None or comp.IsNull():
            continue
        exp = TopExp_Explorer(comp, TopAbs_EDGE)
        while exp.More():
            e = TopoDS.Edge_s(exp.Current())
            exp.Next()
            try:
                c = BRepAdaptor_Curve(e)
            except Exception:  # pragma: no cover - degenerate edge
                continue
            u0, u1 = c.FirstParameter(), c.LastParameter()
            q0, q1 = _xyz(c.Value(u0))[:2], _xyz(c.Value(u1))[:2]
            t = c.GetType()
            if t == GeomAbs_Line:
                if np.linalg.norm(q1 - q0) > 1e-9:
                    edges.append(ProjectedEdge("line", visible, style, p0=q0, p1=q1))
                continue
            if t == GeomAbs_Circle:
                circ = c.Circle()
                cen = _xyz(circ.Location())[:2]
                r = circ.Radius()
                if abs(u1 - u0) >= 2 * np.pi - 1e-9:
                    edges.append(ProjectedEdge("circle", visible, style, center=cen, r=r,
                                               a0=0.0, a1=360.0))
                    continue
                qm = _xyz(c.Value((u0 + u1) / 2))[:2]
                ang = [np.degrees(np.arctan2(*(q - cen)[::-1])) for q in (q0, qm, q1)]
                a0, am, a1 = ang
                if circ.Axis().Direction().Z() < 0:          # parameter runs clockwise
                    a0, a1 = a1, a0
                a1 = a0 + (a1 - a0) % 360.0
                if a1 - a0 < 1e-9:
                    a1 = a0 + 360.0
                edges.append(ProjectedEdge("circle", visible, style, center=cen, r=r, a0=a0, a1=a1))
                continue
            d = GCPnts_TangentialDeflection(c, 0.1, tol)
            pts = np.array([_xyz(d.Value(i))[:2] for i in range(1, d.NbPoints() + 1)])
            if len(pts) >= 2:
                edges.append(ProjectedEdge("poly", visible, style, pts=pts))
    return edges


# --------------------------------------------------------------------------
# BREP (native OCC) text serialization
# --------------------------------------------------------------------------


def write_brep(shape, path: str) -> None:
    if not BRepTools.Write_s(shape, path):
        raise GeometryError(f"Could not write BREP file {path}")


def read_brep(path: str):
    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, path, BRep_Builder()):
        raise GeometryError(f"Could not read BREP file {path}")
    return shape


def shape_to_brep_text(shape) -> str:
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        write_brep(shape, path)
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        os.remove(path)


def shape_from_brep_text(text: str):
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return read_brep(path)
    finally:
        os.remove(path)
