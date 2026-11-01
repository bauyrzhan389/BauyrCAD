"""OpenCASCADE operations behind the applied and advanced features (fillet, chamfer, shell,
draft, sweep, loft, holes, helix, patterns, mirror).

Like geometry.py this module owns OCC calls, so features.py stays kernel-free. Every function
takes and returns plain shapes / numpy data and raises GeometryError on failure.
"""

from __future__ import annotations

import math

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform)
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_ListOfShape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

from . import geometry
from .geometry import GeometryError, _dir, _pnt, _vec, _xyz, indexed_map


# --------------------------------------------------------------------------
# Edge / face look-up
# --------------------------------------------------------------------------


def edge_infos(shape) -> list[dict]:
    """Signature of every edge (same numbering as geometry.subshape / tessellate):
    kind (line | circle | arc | curve), length, mid, p0, p1, and centre / radius / axis
    (circles and arcs) or direction (lines)."""
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    out = []
    anc = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, anc)
    for i, e in enumerate(indexed_map(shape, TopAbs_EDGE)):
        edge = TopoDS.Edge_s(e)
        info = {"index": i, "kind": "curve", "length": 0.0, "degenerate": False, "seam": False}
        if anc.Contains(e):
            faces = list(anc.FindFromKey(e))
            # a seam (the closing line of a cylinder, a sphere's meridian) has one face
            info["seam"] = len(faces) >= 2 and all(f.IsSame(faces[0]) for f in faces[1:])
        if BRep_Tool.Degenerated_s(edge):
            info["degenerate"] = True
            out.append(info)
            continue
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(edge, props)
        c = BRepAdaptor_Curve(edge)
        u0, u1 = c.FirstParameter(), c.LastParameter()
        info.update(length=props.Mass(), p0=_xyz(c.Value(u0)), p1=_xyz(c.Value(u1)),
                    mid=_xyz(c.Value((u0 + u1) / 2)))
        t = c.GetType()
        if t == GeomAbs_Line:
            d = info["p1"] - info["p0"]
            info.update(kind="line", direction=d / max(np.linalg.norm(d), 1e-300))
        elif t == GeomAbs_Circle:
            circ = c.Circle()
            full = abs(u1 - u0) >= 2 * np.pi - 1e-9
            info.update(kind="circle" if full else "arc", center=_xyz(circ.Location()),
                        radius=circ.Radius(), axis=_xyz(circ.Axis().Direction()))
        out.append(info)
    return out


def edge_samples(shape, indices, n=33) -> dict:
    """{edge index: (n, 3) points along the edge} (curve parameter spacing)."""
    items = indexed_map(shape, TopAbs_EDGE)
    out = {}
    for i in indices:
        c = BRepAdaptor_Curve(TopoDS.Edge_s(items[i]))
        us = np.linspace(c.FirstParameter(), c.LastParameter(), n)
        out[i] = np.array([_xyz(c.Value(u)) for u in us])
    return out


def convertible_edges(shape, indices=None) -> list[dict]:
    """edge_infos (+ "pts" samples) of the given edges, for sketch Convert Entities."""
    infos = edge_infos(shape)
    idx = [e["index"] for e in infos if not e["degenerate"] and not e.get("seam")] \
        if indices is None else list(indices)
    smp = edge_samples(shape, idx)
    return [dict(infos[i], pts=smp[i]) for i in idx]


def _edges(shape, indices):
    items = indexed_map(shape, TopAbs_EDGE)
    return [TopoDS.Edge_s(items[i]) for i in indices]


def _faces(shape, indices):
    items = indexed_map(shape, TopAbs_FACE)
    return [TopoDS.Face_s(items[i]) for i in indices]


def face_edge_indices(shape, face_indices) -> list[int]:
    """Edge indices bounding the given faces (no duplicates, in edge order)."""
    edges = indexed_map(shape, TopAbs_EDGE)
    wanted = set()
    for f in _faces(shape, face_indices):
        exp = TopExp_Explorer(f, TopAbs_EDGE)
        while exp.More():
            cur = exp.Current()
            for k, e in enumerate(edges):
                if e.IsSame(cur):
                    wanted.add(k)
                    break
            exp.Next()
    return sorted(wanted)


def edge_faces(shape, edge_index) -> list[int]:
    """Indices of the faces that share an edge."""
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    faces = indexed_map(shape, TopAbs_FACE)
    e = indexed_map(shape, TopAbs_EDGE)[edge_index]
    out = []
    lst = m.FindFromKey(e)
    for f in lst:
        for k, g in enumerate(faces):
            if g.IsSame(f) and k not in out:
                out.append(k)
    return out


def _require_solid(shape, what):
    if shape is None or shape.IsNull() or geometry.count_solids(shape) == 0:
        raise GeometryError(f"{what} produced no solid")
    if not geometry.is_valid(shape):
        fixed = geometry.fix_shape(shape)
        if geometry.count_solids(fixed) == 0:
            raise GeometryError(f"{what} produced an invalid solid")
        shape = fixed
    return shape


# --------------------------------------------------------------------------
# Fillet / chamfer
# --------------------------------------------------------------------------


def fillet(shape, edge_indices, radius, radius2=None):
    """Constant-radius fillet (or a linear radius -> radius2 variation) on edges; tangent
    edge chains are followed automatically, as in SolidWorks."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    if radius <= 0:
        raise GeometryError("The fillet radius must be positive")
    mk = BRepFilletAPI_MakeFillet(shape)
    for e in _edges(shape, edge_indices):
        if radius2 is None:
            mk.Add(float(radius), e)
        else:
            mk.Add(float(radius), float(radius2), e)
    try:
        mk.Build()
        ok = mk.IsDone()
    except Exception:  # noqa: BLE001 - OCC raises Standard_Failure
        ok = False
    if not ok:
        raise GeometryError("The fillet could not be built - the radius may be too large "
                            "for the selected edges")
    return _require_solid(mk.Shape(), "The fillet")


def chamfer(shape, edge_indices, d1, d2=None, angle=None, flip=False):
    """Chamfer edges: equal distance d1, two distances (d1, d2) or distance-angle (d1, angle
    in degrees). The first distance is measured on the edge's first face (flip=True: the
    other face)."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer
    if d1 <= 0 or (d2 is not None and d2 <= 0):
        raise GeometryError("Chamfer distances must be positive")
    mk = BRepFilletAPI_MakeChamfer(shape)
    faces = indexed_map(shape, TopAbs_FACE)
    for i, e in zip(edge_indices, _edges(shape, edge_indices)):
        if d2 is None and angle is None:
            mk.Add(float(d1), e)
            continue
        adj = edge_faces(shape, i)
        if not adj:
            raise GeometryError("A chamfer edge has no faces")
        f = TopoDS.Face_s(faces[adj[-1] if flip and len(adj) > 1 else adj[0]])
        if angle is not None:
            mk.AddDA(float(d1), math.radians(float(angle)), e, f)
        else:
            mk.Add(float(d1), float(d2), e, f)
    try:
        mk.Build()
        ok = mk.IsDone()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise GeometryError("The chamfer could not be built - the distance may be too large "
                            "for the selected edges")
    return _require_solid(mk.Shape(), "The chamfer")


# --------------------------------------------------------------------------
# Shell / draft
# --------------------------------------------------------------------------


def shell(shape, face_indices, thickness, outward=False):
    """Hollow the solid with walls of `thickness`, removing the given faces (open shell).
    With no faces the result is a closed hollow body. Inner corners of plane walls stay
    sharp, like SolidWorks."""
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape, BRepOffsetAPI_MakeThickSolid
    from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection
    if thickness <= 0:
        raise GeometryError("The shell thickness must be positive")
    off = float(thickness) if outward else -float(thickness)
    # Arc joins only fill gaps (outward at convex edges); inward walls still meet sharp. It
    # also copes with fillets, where the intersection join fails - so try it first.
    for join in (GeomAbs_Arc, GeomAbs_Intersection):
        try:
            if face_indices:
                lst = TopTools_ListOfShape()
                for f in _faces(shape, face_indices):
                    lst.Append(f)
                mk = BRepOffsetAPI_MakeThickSolid()
                mk.MakeThickSolidByJoin(shape, lst, off, 1e-4, BRepOffset_Skin, False, False, join)
                mk.Build()
                if not mk.IsDone():
                    continue
                return _require_solid(mk.Shape(), "The shell")
            mk = BRepOffsetAPI_MakeOffsetShape()
            mk.PerformByJoin(shape, off, 1e-4, BRepOffset_Skin, False, False, join)
            if not mk.IsDone():
                continue
            other = solid_from_shell(mk.Shape())
            return geometry.cut(other, shape) if outward else geometry.cut(shape, other)
        except Exception:  # noqa: BLE001 - try the other join, then report
            continue
    raise GeometryError("The shell could not be built - the thickness may be too large for the "
                        "smallest radii or walls. Tip: shell earlier in the tree (before holes "
                        "and small fillets), or use a smaller thickness")


def solid_from_shell(shape):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.TopAbs import TopAbs_SHELL
    if geometry.count_solids(shape):
        return shape
    mk = BRepBuilderAPI_MakeSolid()
    for s in indexed_map(shape, TopAbs_SHELL):
        mk.Add(TopoDS.Shell_s(s))
    solid = mk.Solid()
    from OCP.BRepLib import BRepLib
    BRepLib.OrientClosedSolid_s(solid)
    return solid


def draft(shape, face_indices, angle_deg, neutral_origin, neutral_normal):
    """Taper faces by an angle about their intersection with the neutral plane. The pull
    direction is the neutral plane normal: the faces lean inwards (material is removed)
    going along it for a positive angle, like SolidWorks' Draft."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
    n = np.asarray(neutral_normal, float)
    n = n / np.linalg.norm(n)
    pln = gp_Pln(_pnt(neutral_origin), _dir(n))
    mk = BRepOffsetAPI_DraftAngle(shape)
    for f in _faces(shape, face_indices):
        mk.Add(f, _dir(n), math.radians(float(angle_deg)), pln, True)
        if not mk.AddDone():
            raise GeometryError("A face could not be drafted - only planar, cylindrical and "
                                "conical faces that cross the neutral plane can be")
    try:
        mk.Build()
        ok = mk.IsDone()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise GeometryError("The draft could not be built")
    return _require_solid(mk.Shape(), "The draft")


# --------------------------------------------------------------------------
# Wires for sweeps and lofts
# --------------------------------------------------------------------------


def wire_from_edges(edges):
    """One connected wire from edges given in any order."""
    lst = TopTools_ListOfShape()
    for e in edges:
        lst.Append(e)
    mk = BRepBuilderAPI_MakeWire()
    mk.Add(lst)
    if not mk.IsDone():
        raise GeometryError("The path curves are not connected end to end")
    w = mk.Wire()
    n = 0
    exp = TopExp_Explorer(w, TopAbs_EDGE)
    while exp.More():
        n += 1
        exp.Next()
    if n != len(edges):
        raise GeometryError("The path must be one connected chain of curves (no branches or gaps)")
    return w


def outer_wire(face):
    from OCP.BRepTools import BRepTools
    return BRepTools.OuterWire_s(TopoDS.Face_s(face))


def wire_start(wire):
    """(start point, unit tangent) at the start of a wire (its first edge in order)."""
    from OCP.BRepTools import BRepTools_WireExplorer
    ex = BRepTools_WireExplorer(TopoDS.Wire_s(wire))
    e = ex.Current()
    c = BRepAdaptor_Curve(e)
    reversed_ = e.Orientation() == 1  # TopAbs_REVERSED
    u = c.LastParameter() if reversed_ else c.FirstParameter()
    p, v = gp_Pnt(), gp_Vec()
    c.D1(u, p, v)
    t = _xyz(v) * (-1 if reversed_ else 1)
    return _xyz(p), t / max(np.linalg.norm(t), 1e-300)


def wire_points(wire, n_per_edge=24) -> np.ndarray:
    """A polyline through the wire (for display)."""
    pts = []
    exp = TopExp_Explorer(wire, TopAbs_EDGE)
    while exp.More():
        c = BRepAdaptor_Curve(TopoDS.Edge_s(exp.Current()))
        for u in np.linspace(c.FirstParameter(), c.LastParameter(), n_per_edge):
            pts.append(_xyz(c.Value(u)))
        exp.Next()
    return np.array(pts)


def is_closed_wire(wire) -> bool:
    a, b = wire_ends(wire)
    return bool(np.linalg.norm(a - b) < 1e-7 * max(1.0, float(np.linalg.norm(a))))


def wire_ends(wire):
    """(start, end) points of a wire, following its edges in order."""
    from OCP.BRepTools import BRepTools_WireExplorer
    ex = BRepTools_WireExplorer(TopoDS.Wire_s(wire))
    first, last_edge = None, None
    while ex.More():
        if first is None:
            first = _xyz(BRep_Tool.Pnt_s(ex.CurrentVertex()))
        last_edge = ex.Current()
        ex.Next()
    c = BRepAdaptor_Curve(last_edge)
    reversed_ = last_edge.Orientation() == 1
    last = _xyz(c.Value(c.FirstParameter() if reversed_ else c.LastParameter()))
    return first, last


def circle_face(center, normal, radius, inner_radius=None):
    """Planar disc (or ring) normal to `normal` - the profile of a circular sweep."""
    ax = gp_Ax2(_pnt(center), _dir(normal))
    outer = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(ax, float(radius))).Edge()).Wire()
    mk = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3(_pnt(center), _dir(normal))), outer, True)
    if inner_radius:
        inner = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(ax, float(inner_radius))).Edge()).Wire()
        inner.Reverse()
        mk.Add(inner)
    from OCP.ShapeFix import ShapeFix_Face
    fix = ShapeFix_Face(mk.Face())
    fix.FixOrientation()
    fix.Perform()
    return fix.Face()


def _pipe_shell(profile_wire, path_wire, frenet):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_RightCorner
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    ps = BRepOffsetAPI_MakePipeShell(TopoDS.Wire_s(path_wire))
    ps.SetMode(bool(frenet))
    ps.SetTransitionMode(BRepBuilderAPI_RightCorner)      # sharp path corners: mitred
    ps.Add(profile_wire, False, False)
    ps.Build()
    if not ps.IsDone():
        raise GeometryError("sweep failed")
    ps.MakeSolid()
    sol = ps.Shape()
    if geometry.volume(sol) < 0:
        sol = sol.Reversed()
    return sol


def sweep(face, path_wire, frenet=False):
    """Solid swept by a planar profile face (holes included) along a path wire. Sharp corners
    of the path are mitred (like SolidWorks)."""
    try:
        f = TopoDS.Face_s(face)
        outer = outer_wire(f)
        solid = _pipe_shell(outer, path_wire, frenet)
        holes = [_pipe_shell(w, path_wire, frenet) for w in wires_of(f) if not w.IsSame(outer)]
        if holes:
            solid = geometry.cut_many(solid, holes)
        return _require_solid(solid, "The sweep")
    except Exception:  # noqa: BLE001 - fall back to the simple pipe (smooth paths)
        pass
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from OCP.GeomFill import GeomFill_IsCorrectedFrenet, GeomFill_IsFrenet
    try:
        mk = BRepOffsetAPI_MakePipe(TopoDS.Wire_s(path_wire), face,
                                    GeomFill_IsFrenet if frenet else GeomFill_IsCorrectedFrenet)
        mk.Build()
        ok = mk.IsDone()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise GeometryError("The sweep could not be built - check that the profile is not "
                            "larger than the path's smallest bend radius")
    return _require_solid(mk.Shape(), "The sweep")


def loft(sections, ruled=False, closed=False):
    """Solid through closed section wires (or vertices at the ends) in order."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.TopoDS import TopoDS_Vertex
    if len(sections) < 2:
        raise GeometryError("A loft needs at least two profiles")
    mk = BRepOffsetAPI_ThruSections(True, bool(ruled), 1e-6)
    mk.CheckCompatibility(True)
    items = list(sections) + ([sections[0]] if closed else [])
    for s in items:
        if isinstance(s, TopoDS_Vertex):
            mk.AddVertex(s)
        else:
            mk.AddWire(TopoDS.Wire_s(s))
    try:
        mk.Build()
        ok = mk.IsDone()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise GeometryError("The loft could not be built - check the profiles and their order")
    return _require_solid(mk.Shape(), "The loft")


def vertex(p):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    return BRepBuilderAPI_MakeVertex(_pnt(p)).Vertex()


# --------------------------------------------------------------------------
# Curves
# --------------------------------------------------------------------------


def helix_wire(center, axis, xdir, radius, pitch, turns, left_hand=False, taper_deg=0.0,
               start_deg=0.0):
    """Helix (constant pitch; optional taper) as a B-spline wire, starting at start_deg from
    xdir about the axis. Right-handed (counter-clockwise seen from the axis tip) by default."""
    if radius <= 0 or pitch <= 0 or turns <= 0:
        raise GeometryError("Helix radius, pitch and revolutions must be positive")
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    from OCP.TColgp import TColgp_Array1OfPnt
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    x = np.asarray(xdir, float)
    x = x - a * (x @ a)
    x = x / np.linalg.norm(x)
    y = np.cross(a, x)
    c = np.asarray(center, float)
    n = max(int(turns * 48), 16) + 1
    t = np.linspace(0.0, turns, n)
    s = -1.0 if left_hand else 1.0
    ang = math.radians(start_deg) + s * 2 * np.pi * t
    h = t * pitch
    r = radius + h * math.tan(math.radians(taper_deg))
    pts = c + np.outer(r * np.cos(ang), x) + np.outer(r * np.sin(ang), y) + np.outer(h, a)
    arr = TColgp_Array1OfPnt(1, n)
    for i, p in enumerate(pts):
        arr.SetValue(i + 1, _pnt(p))
    from OCP.GeomAbs import GeomAbs_C2
    bs = GeomAPI_PointsToBSpline(arr, 3, 8, GeomAbs_C2, 1e-6 * max(radius, 1.0))
    if not bs.IsDone():
        raise GeometryError("Helix approximation failed")
    edge = BRepBuilderAPI_MakeEdge(bs.Curve()).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()


def spline_wire(points, closed=False):
    """Interpolating 3D curve through points (Curve Through XYZ Points), the curve SolidWorks
    makes from the same points: a cubic spline in chord-length parameters with natural ends
    (zero curvature) when open, periodic when closed."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.TColgp import TColgp_HArray1OfPnt
    from OCP.TColStd import TColStd_HArray1OfReal
    from scipy.interpolate import CubicSpline
    pts = np.asarray(points, float)
    if len(pts) < 2:
        raise GeometryError("A curve needs at least two points")
    if len(pts) == 2:
        return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(_pnt(pts[0]), _pnt(pts[1])).Edge()).Wire()
    loop = np.vstack([pts, pts[:1]]) if closed else pts
    d = np.maximum(np.linalg.norm(np.diff(loop, axis=0), axis=1), 1e-12)
    t = np.concatenate([[0.0], np.cumsum(d)]) / float(np.sum(d))
    # the one cubic spline through the points with these parameters and end tangents: the
    # natural / periodic spline when the tangents are its own (a closed curve repeats its
    # first point, and starts and ends with the same tangent)
    cs = CubicSpline(t, loop, axis=0, bc_type="periodic" if closed else "natural")
    arr = TColgp_HArray1OfPnt(1, len(loop))
    params = TColStd_HArray1OfReal(1, len(loop))
    for i, (p, x) in enumerate(zip(loop, t)):
        arr.SetValue(i + 1, _pnt(p))
        params.SetValue(i + 1, float(x))
    it = GeomAPI_Interpolate(arr, params, False, 1e-9)
    it.Load(_vec(cs(0.0, 1)), _vec(cs(1.0, 1)), False)
    it.Perform()
    if not it.IsDone():
        raise GeometryError("Curve interpolation failed")
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(it.Curve()).Edge()).Wire()


def polyline_wire(points):
    pts = np.asarray(points, float)
    mk = BRepBuilderAPI_MakeWire()
    for a, b in zip(pts[:-1], pts[1:]):
        mk.Add(BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge())
    return mk.Wire()


# --------------------------------------------------------------------------
# Holes
# --------------------------------------------------------------------------


def _cyl(p, d, radius, length):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    return BRepPrimAPI_MakeCylinder(gp_Ax2(_pnt(p), _dir(d)), float(radius), float(length)).Shape()


def _cone(p, d, r1, r2, length):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    return BRepPrimAPI_MakeCone(gp_Ax2(_pnt(p), _dir(d)), float(r1), float(r2), float(length)).Shape()


def hole_tool(point, direction, d, depth, kind="simple", cbore_d=0.0, cbore_depth=0.0,
              csk_d=0.0, csk_angle=90.0, drill_point=True, drill_angle=118.0):
    """One hole's cutting tool, starting at `point` on the face and going along `direction`
    (into the material): simple / tapped (drill d), counterbore or countersink."""
    p = np.asarray(point, float)
    u = np.asarray(direction, float)
    u = u / np.linalg.norm(u)
    back = 1e-3 * max(d, 1.0)                 # start slightly outside the face
    p0 = p - u * back
    parts = [_cyl(p0, u, d / 2, depth + back)]
    if drill_point:
        tip = (d / 2) / math.tan(math.radians(drill_angle / 2))
        parts.append(_cone(p + u * depth, u, d / 2, 0.0, tip))
    if kind == "counterbore":
        if cbore_d <= d or cbore_depth <= 0:
            raise GeometryError("The counterbore must be wider than the hole and have a depth")
        parts.append(_cyl(p0, u, cbore_d / 2, cbore_depth + back))
    elif kind == "countersink":
        if csk_d <= d:
            raise GeometryError("The countersink diameter must be larger than the hole")
        h = (csk_d - d) / 2 / math.tan(math.radians(csk_angle / 2))
        parts.append(_cone(p0, u, csk_d / 2 + back * math.tan(math.radians(csk_angle / 2)), d / 2, h + back))
    return geometry.fuse_all(parts)


# --------------------------------------------------------------------------
# Transforms for patterns and mirror
# --------------------------------------------------------------------------


def translation_matrix(v) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = v
    return m


def rotation_matrix(point, axis, angle_deg) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    th = math.radians(angle_deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K
    p = np.asarray(point, float)
    m = np.eye(4)
    m[:3, :3] = R
    m[:3, 3] = p - R @ p
    return m


def mirror_shape(shape, origin, normal):
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(_pnt(origin), _dir(normal)))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def transform_shape(shape, m):
    return geometry.transformed(shape, m)


# --------------------------------------------------------------------------
# Sections (rib)
# --------------------------------------------------------------------------


def plane_rect_face(origin, normal, xdir, u0, u1, v0, v1):
    pln = gp_Pln(gp_Ax3(_pnt(origin), _dir(normal), _dir(xdir)))
    return BRepBuilderAPI_MakeFace(pln, float(u0), float(u1), float(v0), float(v1)).Face()


def split_face(face, edges):
    """Faces of `face` split by edges (BRepAlgoAPI_Splitter)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(face)
    for e in edges:
        tools.Append(e)
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(args)
    sp.SetTools(tools)
    sp.Build()
    if not sp.IsDone():
        raise GeometryError("Could not split the plane")
    return [TopoDS.Face_s(f) for f in indexed_map(sp.Shape(), TopAbs_FACE)]


def solids_of(shape) -> list:
    from OCP.TopAbs import TopAbs_SOLID
    return [TopoDS.Solid_s(s) for s in indexed_map(shape, TopAbs_SOLID)]


def distance(a, b) -> float:
    return geometry.min_distance(a, b)[0]


def line_edge(a, b):
    return BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge()


def point_in_solid(shape, p) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    c = BRepClass3d_SolidClassifier(shape, _pnt(p), 1e-7)
    return c.State() == TopAbs_IN


def face_sample_point(face) -> np.ndarray:
    """A point strictly inside a (planar) face."""
    t = geometry.tessellate(face)
    tri = t.points[t.triangles]
    areas = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    return tri[int(np.argmax(areas))].mean(axis=0)


def face_area(face) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def rotate_shape(shape, point, axis, angle_deg):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(_pnt(point), _dir(axis)), math.radians(angle_deg))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def translate_shape(shape, v):
    t = gp_Trsf()
    t.SetTranslation(_vec(v))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def wires_of(shape) -> list:
    return [TopoDS.Wire_s(w) for w in indexed_map(shape, TopAbs_WIRE)]


# --------------------------------------------------------------------------
# Intersect (regions of a body cut by planes)
# --------------------------------------------------------------------------


def interior_point(solid) -> np.ndarray:
    """A point strictly inside a solid: its centroid when that is inside, else the inside
    grid point nearest to it."""
    c = geometry.center_of_mass(solid)
    if point_in_solid(solid, c):
        return c
    lo, hi = geometry.bounding_box(solid)
    best, bd = None, np.inf
    for n in (6, 12, 24):
        ax = [np.linspace(lo[k], hi[k], n + 2)[1:-1] for k in range(3)]
        for x in ax[0]:
            for y in ax[1]:
                for z in ax[2]:
                    q = np.array([x, y, z])
                    d = float(np.linalg.norm(q - c))
                    if d < bd and point_in_solid(solid, q):
                        best, bd = q, d
        if best is not None:
            return best
    return c


def volume_regions(body, tool_faces) -> list[dict]:
    """All closed regions of a body and cutting faces (SolidWorks Intersect): [{"solid",
    "volume", "point" (inside it), "material" (True = part of the body, False = a closed
    void, e.g. inside a shelled part under a capping plane)}]."""
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    lst = TopTools_ListOfShape()
    lst.Append(body)
    for f in tool_faces:
        lst.Append(f)
    mv = BOPAlgo_MakerVolume()
    mv.SetArguments(lst)
    mv.SetIntersect(True)
    mv.Perform()
    if mv.HasErrors():
        raise GeometryError("The body and the planes could not be intersected")
    out = []
    for s in solids_of(mv.Shape()):
        v = geometry.volume(s)
        if v <= 1e-9 * max(geometry.volume(body), 1.0):
            continue
        shared = geometry.volume(geometry.common(s, body))
        out.append({"solid": s, "volume": v, "point": interior_point(s),
                    "material": shared > 0.5 * v})
    out.sort(key=lambda r: (not r["material"], -r["volume"]))
    return out


# --------------------------------------------------------------------------
# Wrap (sketch profiles wrapped onto a cylinder)
# --------------------------------------------------------------------------


def _ordered_edges(wire):
    from OCP.BRepTools import BRepTools_WireExplorer
    ex = BRepTools_WireExplorer(TopoDS.Wire_s(wire))
    out = []
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def wrap_solids(profile_faces, plane, axis_point, axis_dir, radius, depth, emboss=True):
    """Solids of planar sketch profiles wrapped onto a cylinder (SolidWorks Wrap): the sketch
    plane must be parallel to the axis; a sketch length along the circumference becomes the
    same arc length on the cylinder. emboss: `depth` outwards, else inwards (deboss)."""
    from OCP.BRepLib import BRepLib
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.TColgp import TColgp_HArray1OfPnt2d
    from OCP.gp import gp_Pnt2d
    d = np.asarray(axis_dir, float)
    d = d / np.linalg.norm(d)
    A = np.asarray(axis_point, float)
    n = np.asarray(plane.normal, float)
    if abs(float(n @ d)) > 1e-6:
        raise GeometryError("The wrap sketch must be on a plane parallel to the cylinder axis "
                            "(e.g. a plane tangent to it)")
    if depth <= 0:
        raise GeometryError("The wrap depth must be positive")
    e1 = n if float((plane.origin - A) @ n) >= 0 else -n
    e2 = np.cross(d, e1)
    R = float(radius)
    surf = Geom_CylindricalSurface(gp_Ax3(_pnt(A), _dir(d), _dir(e1)), R)

    def uv(p):
        q = np.asarray(p, float) - A
        return float(q @ e2) / R, float(q @ d)

    def edge2d(edge):
        c = BRepAdaptor_Curve(edge)
        u0, u1 = c.FirstParameter(), c.LastParameter()
        k = 2 if c.GetType() == 0 else 33               # GeomAbs_Line: 2 points are enough
        us = np.linspace(u0, u1, k)
        if edge.Orientation() == 1:                     # reversed in the wire
            us = us[::-1]
        pts = [uv(_xyz(c.Value(u))) for u in us]
        if k == 2:
            pts = [pts[0], ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2), pts[1]]
        arr = TColgp_HArray1OfPnt2d(1, len(pts))
        for i, (a, b) in enumerate(pts):
            arr.SetValue(i + 1, gp_Pnt2d(a, b))
        it = Geom2dAPI_Interpolate(arr, False, 1e-9)
        it.Perform()
        if not it.IsDone():
            raise GeometryError("A wrap profile curve could not be mapped")
        return BRepBuilderAPI_MakeEdge(it.Curve(), surf).Edge()

    def wire2d(w):
        mk = BRepBuilderAPI_MakeWire()
        for e in _ordered_edges(w):
            mk.Add(edge2d(e))
        if not mk.IsDone():
            raise GeometryError("A wrap profile is not closed")
        wr = mk.Wire()
        BRepLib.BuildCurves3d_s(wr)
        return wr

    solids = []
    for face in profile_faces:
        f = TopoDS.Face_s(face)
        outer = outer_wire(f)
        mk = BRepBuilderAPI_MakeFace(surf, wire2d(outer), True)
        for w in wires_of(f):
            if not w.IsSame(outer):
                mk.Add(wire2d(w))
        fx = ShapeFix_Face(mk.Face())
        fx.FixOrientation()
        fx.Perform()
        wf = fx.Face()
        th = BRepOffsetAPI_MakeThickSolid()
        th.MakeThickSolidBySimple(wf, float(depth) if emboss else -float(depth))
        th.Build()
        if not th.IsDone():
            raise GeometryError("The wrapped profile could not be thickened")
        sol = th.Shape()
        if geometry.volume(sol) < 0:
            sol = sol.Reversed()
        solids.append(sol)
    return solids
