"""An OpenCASCADE solid as Parasolid's neutral model (:mod:`bauyrcad.core.parasolid`).

The OCC side of the .x_t export: it reads the B-rep and turns it into Parasolid's
conventions (metres, outward face senses, fins in loop order with the face on their left)
and removes what Parasolid does not have:

* seam edges (a full cylinder, cone, sphere or torus face in OCC is cut along one
  parameter line; Parasolid's analytic surfaces are periodic, so the face simply has no
  edge there and its ends become separate loops);
* degenerate edges (OCC's zero-length edges at the poles of a sphere or a cone's apex);
* vertices left with a single closed edge, which becomes a vertex-less ring edge (how
  Parasolid itself bounds a full cylinder face, checked against SolidWorks' partitions).

Curves and surfaces that are not analytic are written as B-splines: closed directions made
periodic (so their seams go too) and stored in Parasolid's unwrapped periodic form, knots
where parameter lines turn made G1 the way SolidWorks does on import. Edges and vertices
further than half Parasolid's 1e-5 mm from their faces (measured, not OCC's tolerance) are
written tolerant: SP-curves on the fins, tolerant vertices. Each solid is a Body (its outer
shell and cavities); several are written as an assembly of bodies, as SolidWorks does.
"""

from __future__ import annotations

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GeomAbs import (GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Ellipse,
                         GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus)
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopAbs import (TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED,
                        TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE)
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedMapOfShape

from . import parasolid as px

M = 1e-3                     # mm -> m
ACCURATE = px.LINEAR_RES / M  # mm: edges and vertices written as accurate must be this close
MAX_TOLERANCE = 1e-2         # mm: an edge further than this from its face is not exported


def _xyz(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], float)


def _pos(p) -> np.ndarray:
    return _xyz(p) * M


def _list(shape, kind, cast):
    out, ex = [], TopExp_Explorer(shape, kind)
    while ex.More():
        out.append(cast(ex.Current()))
        ex.Next()
    return out


# ---- NURBS --------------------------------------------------------------------------------
def _arr1(vals, kind):
    from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
    a = (TColStd_Array1OfInteger if kind is int else TColStd_Array1OfReal)(1, len(vals))
    for i, x in enumerate(vals, start=1):
        a.SetValue(i, kind(x))
    return a


def periodic_form(knots, mults, degree):
    """Parasolid's form of a periodic knot vector given OCC's (one period, the last knot =
    the first + period, equal end multiplicities): the distinct knots and multiplicities of
    an expanded vector whose valid range [t_degree, t_n] is that period, extended by the
    neighbouring periods' knots (Parasolid's 'imaginary' knots), and the number of
    vertices n. SolidWorks writes a closed sweep this way: OCC [0, 2pi] x [6, 6], degree 6,
    6 poles -> [-2pi, 0, 2pi, 4pi] x [1, 6, 6, 1], 7 vertices."""
    period = knots[-1] - knots[0]
    flat = [k for k, m in zip(knots[:-1], mults[:-1]) for _ in range(m)]   # one period
    m0, p = mults[0], degree
    n = len(flat) - m0 + 1 + p

    def at(i):
        q, r = divmod(i, len(flat))
        return flat[r] + q * period
    expanded = [at(m0 - 1 - p + i) for i in range(n + p + 1)]
    kn, mu = [], []
    for x in expanded:
        if kn and abs(x - kn[-1]) <= 1e-12 * max(1.0, abs(period)):
            mu[-1] += 1
        else:
            kn.append(x)
            mu.append(1)
    return kn, mu, n


def _same_curve(a, b, t0, t1, tol, n=23) -> bool:
    return all(a.Value(t).Distance(b.Value(t)) <= tol for t in np.linspace(t0, t1, n))


def _curve_from(poles, weights, knots, mults, degree):
    from OCP.Geom import Geom_BSplineCurve
    from OCP.TColgp import TColgp_Array1OfPnt
    pa = TColgp_Array1OfPnt(1, len(poles))
    for i, q in enumerate(poles, start=1):
        pa.SetValue(i, gp_Pnt(*map(float, q)))
    if weights is None:
        return Geom_BSplineCurve(pa, _arr1(knots, float), _arr1(mults, int), degree, False)
    return Geom_BSplineCurve(pa, _arr1(weights, float), _arr1(knots, float), _arr1(mults, int),
                             degree, False)


def _bcurve(bc, size) -> px.Geom:
    """A Geom_BSplineCurve (mm) as an XT B-curve (m); a periodic one in Parasolid's form."""
    n = bc.NbPoles()
    poles = [_xyz(bc.Pole(i)) for i in range(1, n + 1)]
    weights = [bc.Weight(i) for i in range(1, n + 1)] if bc.IsRational() else None
    knots = [bc.Knot(i) for i in range(1, bc.NbKnots() + 1)]
    mults = [bc.Multiplicity(i) for i in range(1, bc.NbKnots() + 1)]
    deg, periodic = bc.Degree(), bc.IsPeriodic()
    if periodic:
        kn, mu, nx = periodic_form(knots, mults, deg)
        for s in range(n):                  # which OCC pole comes first: checked, not assumed
            q = [poles[(i + s) % n] for i in range(nx)]
            w = None if weights is None else [weights[(i + s) % n] for i in range(nx)]
            c = _curve_from(q, w, kn, mu, deg)
            if _same_curve(c, bc, knots[0], knots[-1], 1e-9 * size):
                poles, weights, knots, mults = q, w, kn, mu
                break
        else:
            raise px.XtError("a periodic B-spline curve could not be put in Parasolid's form")
    return px.Geom("bcurve", {"degree": deg, "knots": knots, "mults": mults,
                              "poles": np.array(poles) * M, "weights": weights,
                              "periodic": periodic, "closed": periodic or bc.IsClosed()})


def _edge_bspline(edge, size):
    """The B-spline of an edge's curve (mm), running the edge's way. A closed curve the edge
    covers is made periodic; of a closed curve it only partly covers, the edge's piece."""
    from OCP.GeomAbs import GeomAbs_BezierCurve, GeomAbs_BSplineCurve
    from OCP.GeomConvert import GeomConvert
    from OCP.Geom import Geom_TrimmedCurve
    c = BRepAdaptor_Curve(edge)
    t = c.GetType()
    if t == GeomAbs_BSplineCurve:
        bc = c.BSpline()
    elif t == GeomAbs_BezierCurve:
        bc = GeomConvert.CurveToBSplineCurve_s(c.Bezier())
    else:
        geom = BRep_Tool.Curve_s(edge, 0.0, 0.0)          # located; OCP returns the curve only
        bc = GeomConvert.CurveToBSplineCurve_s(
            Geom_TrimmedCurve(geom, c.FirstParameter(), c.LastParameter()))
    f, l = c.FirstParameter(), c.LastParameter()
    if bc.IsClosed() and not bc.IsPeriodic():
        whole = abs(f - bc.FirstParameter()) < 1e-9 and abs(l - bc.LastParameter()) < 1e-9
        if whole:
            per = bc.Copy()
            per.SetPeriodic()
            if _same_curve(per, bc, f, l, 1e-9 * size):
                return per
            raise px.XtError("a closed B-spline edge is not smooth where it closes")
        bc = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(bc, f, l))
    return _g1_curve(bc)


def _surface_from(poles, weights, uk, um, vk, vm, ud, vd, uper, vper):
    from OCP.Geom import Geom_BSplineSurface
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array2OfReal
    nu, nv = len(poles), len(poles[0])
    pa = TColgp_Array2OfPnt(1, nu, 1, nv)
    wa = TColStd_Array2OfReal(1, nu, 1, nv) if weights is not None else None
    for i in range(nu):
        for j in range(nv):
            pa.SetValue(i + 1, j + 1, gp_Pnt(*map(float, poles[i][j])))
            if wa is not None:
                wa.SetValue(i + 1, j + 1, float(weights[i][j]))
    args = (_arr1(uk, float), _arr1(vk, float), _arr1(um, int), _arr1(vm, int), ud, vd, uper, vper)
    return Geom_BSplineSurface(pa, *args) if wa is None else Geom_BSplineSurface(pa, wa, *args)


def _same_surface(a, b, box, tol, n=7) -> bool:
    u0, u1, v0, v1 = box
    return all(a.Value(u, v).Distance(b.Value(u, v)) <= tol
               for u in np.linspace(u0, u1, n) for v in np.linspace(v0, v1, n))


def _bsurface(bs, size) -> px.Geom:
    """A Geom_BSplineSurface (mm) as an XT B-surface (m); periodic directions in
    Parasolid's form (periodic_form), one direction at a time, each checked."""
    nu, nv = bs.NbUPoles(), bs.NbVPoles()
    poles = [[_xyz(bs.Pole(i, j)) for j in range(1, nv + 1)] for i in range(1, nu + 1)]
    weights = ([[bs.Weight(i, j) for j in range(1, nv + 1)] for i in range(1, nu + 1)]
               if bs.IsURational() or bs.IsVRational() else None)
    uk = [bs.UKnot(i) for i in range(1, bs.NbUKnots() + 1)]
    um = [bs.UMultiplicity(i) for i in range(1, bs.NbUKnots() + 1)]
    vk = [bs.VKnot(i) for i in range(1, bs.NbVKnots() + 1)]
    vm = [bs.VMultiplicity(i) for i in range(1, bs.NbVKnots() + 1)]
    ud, vd = bs.UDegree(), bs.VDegree()
    uper, vper = bs.IsUPeriodic(), bs.IsVPeriodic()
    box = (uk[0], uk[-1], vk[0], vk[-1])
    if uper:
        kn, mu, n = periodic_form(uk, um, ud)
        for s in range(nu):
            q = [poles[(i + s) % nu] for i in range(n)]
            w = None if weights is None else [weights[(i + s) % nu] for i in range(n)]
            if _same_surface(_surface_from(q, w, kn, mu, vk, vm, ud, vd, False, vper), bs, box,
                             1e-9 * size):
                poles, weights, uk, um = q, w, kn, mu
                break
        else:
            raise px.XtError("a periodic B-spline surface could not be put in Parasolid's form")
    if vper:
        kn, mu, n = periodic_form(vk, vm, vd)
        for s in range(nv):
            q = [[row[(j + s) % nv] for j in range(n)] for row in poles]
            w = None if weights is None else [[row[(j + s) % nv] for j in range(n)] for row in weights]
            if _same_surface(_surface_from(q, w, uk, um, kn, mu, ud, vd, False, False), bs, box,
                             1e-9 * size):
                poles, weights, vk, vm = q, w, kn, mu
                break
        else:
            raise px.XtError("a periodic B-spline surface could not be put in Parasolid's form")
    return px.Geom("bsurface", {
        "u_degree": ud, "v_degree": vd, "u_knots": uk, "u_mults": um, "v_knots": vk,
        "v_mults": vm, "poles": np.array(poles) * M, "weights": weights,
        "u_periodic": uper, "v_periodic": vper,
        "u_closed": uper or bs.IsUClosed(), "v_closed": vper or bs.IsVClosed()})


def _face_bspline(face, s, size):
    """The B-spline of a face's surface (mm): B-spline and Bezier surfaces as they are;
    extrusion, revolution, offset and other surfaces over the face's span, exactly where
    GeomConvert can and else approximated to 1e-7 mm (checked). A closed direction is made
    periodic when that keeps the surface (Parasolid needs it to drop the seam)."""
    from OCP.Geom import Geom_RectangularTrimmedSurface
    from OCP.GeomAbs import (GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_C2,
                             GeomAbs_SurfaceOfExtrusion, GeomAbs_SurfaceOfRevolution)
    from OCP.GeomConvert import GeomConvert, GeomConvert_ApproxSurface
    t = s.GetType()
    if t == GeomAbs_BSplineSurface:
        bs = s.BSpline()
    elif t == GeomAbs_BezierSurface:
        bs = GeomConvert.SurfaceToBSplineSurface_s(s.Bezier())
    else:
        geom = BRep_Tool.Surface_s(face)
        f0, f1, e0, e1 = BRepTools.UVBounds_s(face)
        g0, g1, h0, h1 = geom.Bounds()
        exact = t in (GeomAbs_SurfaceOfExtrusion, GeomAbs_SurfaceOfRevolution)
        # a margin around the face's span keeps its edges inside; an approximation of an
        # offset surface can go wrong inside the face when the margin reaches a region it
        # cannot follow (0.14 mm seen with 2 % on a shell of a spline part), so smaller
        # margins are tried until the result checks out on a dense grid
        for margin in ((0.02,) if exact else (0.02, 0.005, 0.001, 0.0)):
            du, dv = (f1 - f0) * margin, (e1 - e0) * margin
            if geom.IsUPeriodic() and f1 - f0 > geom.UPeriod() - 1e-9:
                u0, u1 = g0, g0 + geom.UPeriod()
            else:
                u0, u1 = max(f0 - du, g0), min(f1 + du, g1)
            if geom.IsVPeriodic() and e1 - e0 > geom.VPeriod() - 1e-9:
                v0, v1 = h0, h0 + geom.VPeriod()
            else:
                v0, v1 = max(e0 - dv, h0), min(e1 + dv, h1)
            trimmed = Geom_RectangularTrimmedSurface(geom, u0, u1, v0, v1)
            if exact:
                bs = GeomConvert.SurfaceToBSplineSurface_s(trimmed)
            else:
                # GeomConvert approximates offset surfaces itself, loosely (0.03 mm seen):
                # approximate on purpose, to 1e-7 mm
                ap = GeomConvert_ApproxSurface(trimmed, 1e-7, GeomAbs_C2, GeomAbs_C2, 9, 9, 500, 1)
                if not ap.HasResult():
                    continue
                bs = ap.Surface()
            if _near_surface(bs, trimmed, (u0, u1, v0, v1), 1e-6, n=11 if exact else 25):
                break
        else:
            raise px.XtError("a surface could not be turned into a B-spline within 1e-6 mm")
    for closed, periodic, make in ((bs.IsUClosed(), bs.IsUPeriodic(), "SetUPeriodic"),
                                   (bs.IsVClosed(), bs.IsVPeriodic(), "SetVPeriodic")):
        if closed and not periodic:
            per = bs.Copy()
            try:
                getattr(per, make)()
            except Exception:  # noqa: BLE001 - stays closed, its seam is kept
                continue
            if _same_surface(per, bs, bs.Bounds(), 1e-9 * size):
                bs = per
    return _g1_surface(bs, size)


_SMOOTH_TOL = 1e-6           # mm: a C0 knot is removed when the shape stays within this
_SPLIT_MOVE = 1e-7           # mm: how far splitting a C0 knot may move the shape


def _c0_knots(mults, degree):
    return [i for i in range(1, len(mults) - 1) if mults[i] >= degree]


def _split_knot(knots, mults, i, delta):
    """Knot i (multiplicity = degree) as degree - 1 at k plus 1 at k + delta: formally C1,
    the same number of poles (how SolidWorks imports such surfaces)."""
    return (knots[:i + 1] + [knots[i] + delta] + knots[i + 1:],
            mults[:i] + [mults[i] - 1, 1] + mults[i + 1:])


def _turns(bs, d, k) -> bool:
    """Whether the parameter lines in direction d ('u' / 'v') change direction at knot k
    (a sweep's surface at its path's knots), not only speed (a rational circle's knots)."""
    u0, u1, v0, v1 = bs.Bounds()
    eps = 1e-9 * ((u1 - u0) if d == "u" else (v1 - v0))
    for t in np.linspace(0.05, 0.95, 7):
        tan = []
        for x in (k - eps, k + eps):
            u, v = (x, v0 + t * (v1 - v0)) if d == "u" else (u0 + t * (u1 - u0), x)
            p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
            bs.D1(u, v, p, du, dv)
            w = du if d == "u" else dv
            if w.Magnitude() < 1e-12:
                return True
            tan.append(_xyz(w) / w.Magnitude())
        if float(tan[0] @ tan[1]) < 1 - 1e-10:
            return True
    return False


def _g1_surface(bs, size):
    """Parasolid needs a B-surface on a face to be G1 and judges it by its parameter lines:
    an interior knot of multiplicity = degree where they turn (a sweep's surface at its
    path's knots, though its normal is continuous) is removed where the surface is C1
    there, else split (_split_knot) with a gap that moves the surface less than
    _SPLIT_MOVE. Knots where only the speed changes (a rational circle's) stay: SolidWorks
    reads them as they are."""
    for d in ("U", "V"):
        deg = getattr(bs, d + "Degree")()
        nk = getattr(bs, f"Nb{d}Knots")
        mult = getattr(bs, d + "Multiplicity")
        for i in range(nk() - 1, 1, -1):
            if mult(i) >= deg and _turns(bs, d.lower(), getattr(bs, d + "Knot")(i)):
                c = bs.Copy()
                if getattr(c, f"Remove{d}Knot")(i, deg - 1, _SMOOTH_TOL):
                    bs = c
    g = _bsurface_data(bs)
    turning = {d: [i for i in _c0_knots(g[d + "_mults"], g[d + "_degree"])
                   if _turns(bs, d, g[d + "_knots"][i])] for d in ("u", "v")}
    if not turning["u"] and not turning["v"]:
        return bs
    for d in ("u", "v"):
        knots, mults = g[d + "_knots"], g[d + "_mults"]
        for i in reversed(turning[d]):
            # the parameter speed at the knot sets the gap for a _SPLIT_MOVE shift
            speed = max(_speed(bs, d, knots[i], t) for t in np.linspace(0, 1, 7))
            gap = min(_SPLIT_MOVE / max(speed, 1e-12), (knots[i + 1] - knots[i]) / 10)
            knots, mults = _split_knot(knots, mults, i, gap)
        g[d + "_knots"], g[d + "_mults"] = knots, mults
    out = _surface_from(g["poles"], g["weights"], g["u_knots"], g["u_mults"], g["v_knots"],
                        g["v_mults"], g["u_degree"], g["v_degree"], bs.IsUPeriodic(),
                        bs.IsVPeriodic())
    if not _same_surface(out, bs, bs.Bounds(), 10 * _SPLIT_MOVE, n=41):
        raise px.XtError("a B-spline surface is only C0 at some knots and could not be smoothed")
    return out


def _near_surface(bs, ref, box, tol, n=25) -> bool:
    """Whether a converted surface lies within tol (mm) of the reference over its span,
    both ways, by projection (a converted revolution has another parameterization, so the
    surfaces are not compared at equal parameters)."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    u0, u1, v0, v1 = box
    b0, b1, c0, c1 = bs.Bounds()
    for a, b, (s0, s1, t0, t1) in ((ref, bs, (u0, u1, v0, v1)), (bs, ref, (b0, b1, c0, c1))):
        for u in np.linspace(s0, s1, n):
            for v in np.linspace(t0, t1, n):
                pr = GeomAPI_ProjectPointOnSurf(a.Value(u, v), b)
                if not pr.NbPoints() or pr.LowerDistance() > tol:
                    return False
    return True


def _speed(bs, d, k, t):
    """|dS/du| (d = 'u') or |dS/dv| at parameter k in that direction, t along the other."""
    u0, u1, v0, v1 = bs.Bounds()
    if d == "u":
        u, v = k, v0 + t * (v1 - v0)
    else:
        u, v = u0 + t * (u1 - u0), k
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    bs.D1(u, v, p, du, dv)
    return (du if d == "u" else dv).Magnitude()


def _bsurface_data(bs):
    nu, nv = bs.NbUPoles(), bs.NbVPoles()
    return {
        "poles": [[_xyz(bs.Pole(i, j)) for j in range(1, nv + 1)] for i in range(1, nu + 1)],
        "weights": ([[bs.Weight(i, j) for j in range(1, nv + 1)] for i in range(1, nu + 1)]
                    if bs.IsURational() or bs.IsVRational() else None),
        "u_knots": [bs.UKnot(i) for i in range(1, bs.NbUKnots() + 1)],
        "u_mults": [bs.UMultiplicity(i) for i in range(1, bs.NbUKnots() + 1)],
        "v_knots": [bs.VKnot(i) for i in range(1, bs.NbVKnots() + 1)],
        "v_mults": [bs.VMultiplicity(i) for i in range(1, bs.NbVKnots() + 1)],
        "u_degree": bs.UDegree(), "v_degree": bs.VDegree()}


def _curve_turns(bc, k) -> bool:
    eps = 1e-9 * (bc.LastParameter() - bc.FirstParameter())
    a, b = bc.DN(k - eps, 1), bc.DN(k + eps, 1)
    if a.Magnitude() < 1e-12 or b.Magnitude() < 1e-12:
        return True
    return float(_xyz(a) @ _xyz(b)) / (a.Magnitude() * b.Magnitude()) < 1 - 1e-10


def _g1_curve(bc):
    """The curve version of _g1_surface."""
    deg = bc.Degree()
    for i in range(bc.NbKnots() - 1, 1, -1):
        if bc.Multiplicity(i) >= deg and _curve_turns(bc, bc.Knot(i)):
            c = bc.Copy()
            if c.RemoveKnot(i, deg - 1, _SMOOTH_TOL):
                bc = c
    knots = [bc.Knot(i) for i in range(1, bc.NbKnots() + 1)]
    mults = [bc.Multiplicity(i) for i in range(1, bc.NbKnots() + 1)]
    c0 = [i for i in _c0_knots(mults, deg) if _curve_turns(bc, knots[i])]
    if not c0 or bc.IsPeriodic():
        return bc
    for i in reversed(c0):
        speed = bc.DN(knots[i], 1).Magnitude()
        knots, mults = _split_knot(knots, mults, i, min(_SPLIT_MOVE / max(speed, 1e-12),
                                                        (knots[i + 1] - knots[i]) / 10))
    n = bc.NbPoles()
    out = _curve_from([_xyz(bc.Pole(i)) for i in range(1, n + 1)],
                      [bc.Weight(i) for i in range(1, n + 1)] if bc.IsRational() else None,
                      knots, mults, deg)
    if not _same_curve(out, bc, knots[0], knots[-1], 10 * _SPLIT_MOVE, n=201):
        raise px.XtError("a B-spline curve is only C0 at some knots and could not be smoothed")
    return out


def _surface(face, size=1.0):
    """(px.Geom, adaptor, B-spline or None) for a face's surface, in Parasolid's terms."""
    s = BRepAdaptor_Surface(face, True)
    t = s.GetType()
    if t == GeomAbs_Plane:
        a = s.Plane().Position()
        g = px.Geom("plane", {"pvec": _pos(a.Location()), "normal": _xyz(a.Direction()),
                              "x_axis": _xyz(a.XDirection())})
    elif t == GeomAbs_Cylinder:
        c = s.Cylinder()
        a = c.Position()
        g = px.Geom("cylinder", {"pvec": _pos(a.Location()), "axis": _xyz(a.Direction()),
                                 "radius": c.Radius() * M, "x_axis": _xyz(a.XDirection())})
    elif t == GeomAbs_Cone:
        c = s.Cone()
        a = c.Position()
        ang = c.SemiAngle()
        # Parasolid uses half a cone, its axis pointing the way the radius grows (away from
        # the apex, as SolidWorks writes it); OCC's radius grows along +Z when the
        # semi-angle is positive
        z = _xyz(a.Direction())
        g = px.Geom("cone", {"pvec": _pos(a.Location()), "axis": z if ang > 0 else -z,
                             "radius": c.RefRadius() * M, "sin_half_angle": abs(np.sin(ang)),
                             "cos_half_angle": np.cos(ang), "x_axis": _xyz(a.XDirection())})
    elif t == GeomAbs_Sphere:
        c = s.Sphere()
        a = c.Position()
        g = px.Geom("sphere", {"centre": _pos(a.Location()), "radius": c.Radius() * M,
                               "axis": _xyz(a.Direction()), "x_axis": _xyz(a.XDirection())})
    elif t == GeomAbs_Torus:
        c = s.Torus()
        a = c.Position()
        g = px.Geom("torus", {"centre": _pos(a.Location()), "axis": _xyz(a.Direction()),
                              "major_radius": c.MajorRadius() * M,
                              "minor_radius": c.MinorRadius() * M, "x_axis": _xyz(a.XDirection())})
    else:
        bs = _face_bspline(face, s, size)
        return _bsurface(bs, size), s, bs
    return g, s, None


def _outward(face, s):
    """A point on the face's surface (m) and the face's outward normal there (OCC)."""
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    s.D1(u, v, p, du, dv)
    n = _xyz(du.Crossed(dv))
    if np.linalg.norm(n) < 1e-12:
        raise px.XtError("a face's normal is undefined at its middle")
    n /= np.linalg.norm(n)
    return _pos(p), (-n if face.Orientation() == TopAbs_REVERSED else n)


def _bs_normal(bs, p_m) -> np.ndarray:
    """The natural normal of a B-spline surface (mm) at the point nearest to p (m)."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*(np.asarray(p_m) / M)), bs)
    u, v = pr.LowerDistanceParameters()
    q, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    bs.D1(u, v, q, du, dv)
    n = _xyz(du.Crossed(dv))
    if np.linalg.norm(n) < 1e-12:
        raise px.XtError("a B-spline face's normal is undefined at its middle")
    return n / np.linalg.norm(n)


def _curve(edge, size=1.0):
    c = BRepAdaptor_Curve(edge)
    t = c.GetType()
    if t == GeomAbs_Line:
        ln = c.Line()
        return px.Geom("line", {"pvec": _pos(ln.Location()), "direction": _xyz(ln.Direction())})
    if t == GeomAbs_Circle:
        ci = c.Circle()
        a = ci.Position()
        return px.Geom("circle", {"centre": _pos(a.Location()), "normal": _xyz(a.Direction()),
                                  "x_axis": _xyz(a.XDirection()), "radius": ci.Radius() * M})
    if t == GeomAbs_Ellipse:
        el = c.Ellipse()
        a = el.Position()
        return px.Geom("ellipse", {"centre": _pos(a.Location()), "normal": _xyz(a.Direction()),
                                   "x_axis": _xyz(a.XDirection()),
                                   "major_radius": el.MajorRadius() * M,
                                   "minor_radius": el.MinorRadius() * M})
    return _bcurve(_edge_bspline(edge, size), size)


def _rechain(fins, ends):
    """Loops from the fins left in an OCC wire once seams and degenerate edges are taken
    out: each loop follows the wire order, joining a fin to the next unused fin that starts
    where it ends."""
    loops, used = [], [False] * len(fins)
    for k0 in range(len(fins)):
        if used[k0]:
            continue
        loop, k = [], k0
        while True:
            used[k] = True
            loop.append(fins[k])
            end = ends[k][1]
            if end == ends[k0][0]:
                break
            nxt = next((j % len(fins) for j in range(k + 1, k + 1 + len(fins))
                        if not used[j % len(fins)] and ends[j % len(fins)][0] == end), None)
            if nxt is None:
                raise px.XtError("a face boundary does not close once its seam is removed")
            k = nxt
        loops.append(loop)
    return loops


def bodies_from_shape(shape, name="") -> list:
    """Parasolid's model of every solid in the shape, one Body each (written as a
    multi-body part: an assembly of the bodies, as SolidWorks does)."""
    if shape is None or shape.IsNull():
        raise px.XtError("there is no solid to export")
    solids = _list(shape, TopAbs_SOLID, TopoDS.Solid_s)
    if not solids:
        raise px.XtError("there is no solid to export")
    return [_solid_body(s, name if len(solids) == 1 else f"{name}-{i + 1}")
            for i, s in enumerate(solids)]


def merge_lumps(bodies, name="") -> px.Body:
    """Several solids as one body of several lumps (a part with separate solids placed in
    an assembly: SolidWorks opens such a body as one part)."""
    verts, edges, faces, shells, vtol = [], [], [], [], []
    for b in bodies:
        vo, eo, fo = len(verts), len(edges), len(faces)
        verts += b.vertices
        vtol += b.vertex_tol or [None] * len(b.vertices)
        for e in b.edges:
            edges.append(px.Edge(e.curve, None if e.start is None else e.start + vo,
                                 None if e.end is None else e.end + vo, e.tolerance, e.trim))
        for f in b.faces:
            loops = [[px.Isolated(x.vertex + vo) if isinstance(x, px.Isolated)
                      else px.Fin(x.edge + eo, x.sense, x.sp) for x in loop] for loop in f.loops]
            faces.append(px.Face(f.surface, f.sense, loops, f.color))
        shells += [[[i + fo for i in sh] for sh in lump] for lump in b.lumps()]
    out = px.Body(verts, edges, faces, name, shells=shells,
                  vertex_tol=vtol if any(vtol) else None)
    out.max_tolerance = max(getattr(b, "max_tolerance", 0.0) for b in bodies)
    return out


def body_from_shape(shape, name="") -> px.Body:
    """Parasolid's model of a shape holding exactly one solid."""
    bodies = bodies_from_shape(shape, name)
    if len(bodies) != 1:
        raise px.XtError(f"{len(bodies)} solid bodies (bodies_from_shape writes them all)")
    return bodies[0]


def _solid_body(solid, name="") -> px.Body:
    """One solid: its shells (the outer one first, then each cavity), seams and degenerate
    edges removed, and edges or vertices further than Parasolid's resolution from their
    faces written tolerant (SP-curves on the fins, trimmed curves where accurate edges
    meet a tolerant vertex)."""
    from OCP.BRepClass3d import BRepClass3d
    if not BRepCheck_Analyzer(solid).IsValid():
        raise px.XtError("the solid is not valid (BRepCheck)")
    vmap, emap = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(solid, TopAbs_VERTEX, vmap)
    TopExp.MapShapes_s(solid, TopAbs_EDGE, emap)
    vid = lambda v: vmap.FindIndex(v) - 1                                     # noqa: E731
    from . import geometry
    lo, hi = geometry.bounding_box(solid)
    size = max(float(np.linalg.norm(np.subtract(hi, lo))), 1.0)          # mm

    edges, edge_of, apex, occ_edge, tol = [], {}, {}, [], 0.0
    for i in range(1, emap.Extent() + 1):
        e = TopoDS.Edge_s(emap.FindKey(i).Oriented(TopAbs_FORWARD))
        tol = max(tol, BRep_Tool.Tolerance_s(e))
        if BRep_Tool.Degenerated_s(e):
            apex[i - 1] = vid(TopExp.FirstVertex_s(e))
            continue
        a, b = TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)
        edge_of[i - 1] = len(edges)
        edges.append(px.Edge(_curve(e, size), vid(a), vid(b)))   # along increasing parameter
        occ_edge.append(e)

    outer = BRepClass3d.OuterShell_s(solid)
    shells = _list(solid, TopAbs_SHELL, TopoDS.Shell_s)            # orientation as in the solid
    shells.sort(key=lambda s: not s.IsSame(outer))
    faces, face_info, shell_faces = [], [], []
    for shell in shells:
        shell_faces.append([])
        for face in _list(shell, TopAbs_FACE, TopoDS.Face_s):
            shell_faces[-1].append(len(faces))
            geom, s, bs = _surface(face, size)
            p, n_out = _outward(face, s)
            natural = px.natural_normal(geom, p) if bs is None else _bs_normal(bs, p)
            sense = float(n_out @ natural) > 0
            # Parasolid has no seam on a periodic surface (every analytic one); a closed
            # B-surface that could not be made periodic keeps its seam edges
            seamless = bs is None or ((bs.IsUPeriodic() or not bs.IsUClosed())
                                      and (bs.IsVPeriodic() or not bs.IsVClosed()))
            wires = []
            for wire in _list(face, TopAbs_WIRE, TopoDS.Wire_s):
                wx, ws = BRepTools_WireExplorer(wire, face), []
                while wx.More():
                    e = wx.Current()
                    # the edge's own (composed) orientation: the explorer's Orientation()
                    # does not follow a reversed wire
                    ws.append((emap.FindIndex(e) - 1, e.Orientation() == TopAbs_FORWARD))
                    wx.Next()
                wires.append(ws)
            # a seam is used twice by the face (once each way); an edge that only lies along
            # the seam line (a cut's side ending there) is used once and stays
            count = {}
            for ws in wires:
                for k, _ in ws:
                    count[k] = count.get(k, 0) + 1
            loops = []
            if geom.kind == "cone":   # Parasolid keeps a cone's apex as an isolated vertex
                loops += [[px.Isolated(apex[k])] for ws in wires for k, _ in ws if k in apex]
            for ws in wires:
                fins = [px.Fin(edge_of[k], fwd) for k, fwd in ws
                        if k in edge_of and (count[k] == 1 or not seamless)]
                if fins:
                    ends = [px.fin_vertices(px.Body([], edges, []), f) for f in fins]
                    loops.extend(_rechain(fins, ends))
            faces.append(px.Face(geom, sense, loops))
            face_info.append((face, geom, bs))

    # edges: drop the seams (no fins left)
    used = sorted({fin.edge for f in faces for loop in f.loops for fin in loop
                   if isinstance(fin, px.Fin)})
    renum = {k: i for i, k in enumerate(used)}
    edges, occ_edge = [edges[k] for k in used], [occ_edge[k] for k in used]
    for f in faces:
        for loop in f.loops:
            for fin in loop:
                if isinstance(fin, px.Fin):
                    fin.edge = renum[fin.edge]
    # vertices: drop the unused ones (seam ends, poles, apexes) and make a closed edge whose
    # vertex nothing else uses a ring edge
    uses = {}
    for k, e in enumerate(edges):
        for v in {e.start, e.end}:
            uses.setdefault(v, set()).add(k)
    for v, ks in uses.items():
        if len(ks) == 1:
            e = edges[next(iter(ks))]
            if e.start == e.end == v:
                e.start = e.end = None
    isolated = [loop[0] for f in faces for loop in f.loops if isinstance(loop[0], px.Isolated)]
    keep = sorted({v for e in edges for v in (e.start, e.end) if v is not None}
                  | {i.vertex for i in isolated})
    new = {v: i for i, v in enumerate(keep)}
    for e in edges:
        if e.start is not None:
            e.start, e.end = new[e.start], new[e.end]
    for i in isolated:
        i.vertex = new[i.vertex]
    occ_vertex = [TopoDS.Vertex_s(vmap.FindKey(v + 1)) for v in keep]
    verts = [_pos(BRep_Tool.Pnt_s(v)) for v in occ_vertex]

    body = px.Body(verts, edges, faces, name, shells=[shell_faces])
    if tol > ACCURATE / 10 or _loose_vertices(vmap):
        egap, vgap = _gaps(solid, occ_edge, occ_vertex)
        _tolerant(body, occ_edge, face_info, egap, vgap, size)
        body.max_tolerance = max(egap + vgap, default=0.0)        # mm, measured
    else:
        body.max_tolerance = tol
    return body


def _loose_vertices(vmap) -> bool:
    return any(BRep_Tool.Tolerance_s(TopoDS.Vertex_s(vmap.FindKey(i))) > ACCURATE / 10
               for i in range(1, vmap.Extent() + 1))


def _gaps(solid, occ_edge, occ_vertex):
    """The real gaps (mm): of each edge's curve from its faces' surfaces (9 points), and of
    each vertex from the ends of its edges' curves. OCC's tolerances only bound these,
    often loosely (a chamfer's vertices carry 1e-4 mm while they lie on their edges to
    1e-12)."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    e2f = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(solid, TopAbs_EDGE, TopAbs_FACE, e2f)
    egap, vgap = [], [0.0] * len(occ_vertex)
    vindex = {i: v for i, v in enumerate(occ_vertex)}
    for e in occ_edge:
        c = BRepAdaptor_Curve(e)
        pts = [c.Value(t) for t in np.linspace(c.FirstParameter(), c.LastParameter(), 9)]
        g = 0.0
        for f in e2f.FindFromKey(e):
            surf = BRep_Tool.Surface_s(TopoDS.Face_s(f))
            for p in pts:
                pr = GeomAPI_ProjectPointOnSurf(p, surf)
                if pr.NbPoints():
                    g = max(g, pr.LowerDistance())
        egap.append(g)
        for v in (TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)):
            for i, ov in vindex.items():
                if ov.IsSame(v):
                    d = c.Value(BRep_Tool.Parameter_s(v, e)).Distance(BRep_Tool.Pnt_s(v))
                    vgap[i] = max(vgap[i], d)
    return egap, vgap


def _tolerant(body, occ_edge, face_info, egap, vgap, size):
    """Edges and vertices further than half Parasolid's resolution from their faces
    become tolerant, as do the vertices of tolerant edges. A tolerant edge has no curve:
    each of its fins gets an SP-curve on its face (_sp_curve), and its tolerance covers
    them (2.5x the furthest one strays from the edge, so the two fins' curves also agree
    within it; at least 3x the measured gap and twice the resolution). An accurate edge
    at a tolerant vertex keeps its curve, trimmed to its ends (_trim): the vertex need
    not lie on it."""
    half = ACCURATE / 2
    etol = [max(3 * g, 2 * ACCURATE) if g > half else None for g in egap]
    vtol = [max(3 * g, 2 * ACCURATE) if g > half else None for g in vgap]
    if not any(etol) and not any(vtol):
        return
    for fi, f in enumerate(body.faces):
        face, geom, bs = face_info[fi]
        for loop in f.loops:
            for fin in loop:
                if isinstance(fin, px.Fin) and etol[fin.edge] is not None:
                    fin.sp, dev = _sp_curve(occ_edge[fin.edge], geom, bs, fin.sense)
                    etol[fin.edge] = max(etol[fin.edge], 2.5 * dev)
    for k, e in enumerate(body.edges):
        if etol[k] is not None and e.start is not None:
            for v in (e.start, e.end):
                vtol[v] = max(vtol[v] or 0.0, etol[k])
    body.vertex_tol = [None if t is None else t * M for t in vtol]
    for k, e in enumerate(body.edges):
        if etol[k] is not None:
            e.curve, e.tolerance = None, etol[k] * M
        elif e.start is not None and (vtol[e.start] or vtol[e.end]):
            e.trim = _trim(e.curve, occ_edge[k])


def _twin(geom, bs):
    """An OCC surface (mm) with the XT surface's parameterization, and the maps from its
    parameters to XT's (lengths in metres; a cone's v runs along the axis) and back."""
    from OCP.Geom import (Geom_ConicalSurface, Geom_CylindricalSurface, Geom_Plane,
                          Geom_SphericalSurface, Geom_ToroidalSurface)
    from OCP.gp import gp_Ax3, gp_Dir
    if bs is not None:
        return bs, (lambda u, v: (u, v)), (lambda u, v: (u, v))
    d = geom.data
    ax = lambda c, z: gp_Ax3(gp_Pnt(*(np.asarray(d[c]) / M)), gp_Dir(*d[z]),     # noqa: E731
                             gp_Dir(*d["x_axis"]))
    k = geom.kind
    if k == "plane":
        return (Geom_Plane(ax("pvec", "normal")), lambda u, v: (u * M, v * M),
                lambda u, v: (u / M, v / M))
    if k == "cylinder":
        return (Geom_CylindricalSurface(ax("pvec", "axis"), d["radius"] / M),
                lambda u, v: (u, v * M), lambda u, v: (u, v / M))
    if k == "cone":
        a = np.arctan2(d["sin_half_angle"], d["cos_half_angle"])
        c = np.cos(a)
        return (Geom_ConicalSurface(ax("pvec", "axis"), a, d["radius"] / M),
                lambda u, v: (u, v * c * M), lambda u, v: (u, v / (c * M)))
    if k == "sphere":
        return (Geom_SphericalSurface(ax("centre", "axis"), d["radius"] / M),
                lambda u, v: (u, v), lambda u, v: (u, v))
    if k == "torus":
        return (Geom_ToroidalSurface(ax("centre", "axis"), d["major_radius"] / M,
                                     d["minor_radius"] / M), lambda u, v: (u, v), lambda u, v: (u, v))
    raise px.XtError(f"no parameterization for {k}")


def _sp_curve(edge, geom, bs, sense):
    """A tolerant edge's curve on one face, in the face's XT surface parameters, running
    the fin's way: points along the edge projected onto the surface (unwrapped across
    periodic seams) and interpolated by a 2D B-spline (periodic when the loop closes in
    the parameter plane). Returns the data and how far (mm) the curve on the surface
    strays from the edge's curve, which sets the edge's tolerance."""
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve, GeomAPI_ProjectPointOnSurf
    from OCP.gp import gp_Pnt2d
    from OCP.TColgp import TColgp_HArray1OfPnt2d
    twin, to_xt, back = _twin(geom, bs)
    c = BRepAdaptor_Curve(edge)
    ts = np.linspace(c.FirstParameter(), c.LastParameter(), 41)
    pts = [c.Value(t) for t in (ts if sense else ts[::-1])]
    uv = []
    periods = (twin.UPeriod() if twin.IsUPeriodic() else None,
               twin.VPeriod() if twin.IsVPeriodic() else None)
    for p in pts:
        pr = GeomAPI_ProjectPointOnSurf(p, twin)
        if not pr.NbPoints():
            raise px.XtError("a tolerant edge could not be put on its face")
        q = list(pr.LowerDistanceParameters())
        if uv:                                    # keep the curve continuous across seams
            for i, per in enumerate(periods):
                if per:
                    q[i] += per * round((uv[-1][i] - q[i]) / per)
        uv.append(q)
    uv = [to_xt(*q) for q in uv]
    closed = np.allclose(uv[0], uv[-1], atol=1e-9)
    pts2 = uv[:-1] if closed else uv
    arr = TColgp_HArray1OfPnt2d(1, len(pts2))
    for i, q in enumerate(pts2, start=1):
        arr.SetValue(i, gp_Pnt2d(*map(float, q)))
    ip = Geom2dAPI_Interpolate(arr, closed, 1e-12)
    ip.Perform()
    if not ip.IsDone():
        raise px.XtError("a tolerant edge's curve on its face could not be fitted")
    c2 = ip.Curve()
    t0, t1 = c2.FirstParameter(), c2.LastParameter()
    curve3d = BRep_Tool.Curve_s(edge, 0.0, 0.0)      # located; OCP returns the curve only
    on = lambda t: twin.Value(*back(*_xy(c2.Value(t))))                       # noqa: E731
    dev = 0.0
    for t in np.linspace(t0, t1, 81):
        pc = GeomAPI_ProjectPointOnCurve(on(t), curve3d)
        if pc.NbPoints():
            dev = max(dev, pc.LowerDistance())
    if dev > MAX_TOLERANCE:
        raise px.XtError(f"an edge lies {dev:.1e} mm off its face (more than "
                         f"{MAX_TOLERANCE:g} mm: the model needs repair)")
    b = _bcurve2d(c2)
    return {"b": b, "t0": t0, "t1": t1, "p0": _pos(on(t0)), "p1": _pos(on(t1))}, dev


def _xy(p) -> tuple:
    return p.X(), p.Y()


def _bcurve2d(c2) -> dict:
    """A Geom2d_BSplineCurve (XT parameters) as 2D B-curve data; periodic in Parasolid's
    form (periodic_form), the first pole found by evaluating."""
    from OCP.Geom2d import Geom2d_BSplineCurve
    from OCP.TColgp import TColgp_Array1OfPnt2d
    from OCP.gp import gp_Pnt2d
    n = c2.NbPoles()
    poles = [_xy(c2.Pole(i)) for i in range(1, n + 1)]
    weights = [c2.Weight(i) for i in range(1, n + 1)] if c2.IsRational() else None
    knots = [c2.Knot(i) for i in range(1, c2.NbKnots() + 1)]
    mults = [c2.Multiplicity(i) for i in range(1, c2.NbKnots() + 1)]
    deg, periodic = c2.Degree(), c2.IsPeriodic()
    if periodic:
        kn, mu, nx = periodic_form(knots, mults, deg)
        for s in range(n):
            q = [poles[(i + s) % n] for i in range(nx)]
            arr = TColgp_Array1OfPnt2d(1, nx)
            for i, xy in enumerate(q, start=1):
                arr.SetValue(i, gp_Pnt2d(*xy))
            if weights is None:
                cand = Geom2d_BSplineCurve(arr, _arr1(kn, float), _arr1(mu, int), deg, False)
            else:
                w = [weights[(i + s) % n] for i in range(nx)]
                cand = Geom2d_BSplineCurve(arr, _arr1(w, float), _arr1(kn, float),
                                           _arr1(mu, int), deg, False)
            if all(cand.Value(t).Distance(c2.Value(t)) <= 1e-10
                   for t in np.linspace(knots[0], knots[-1], 23)):
                poles, knots, mults = q, kn, mu
                weights = None if weights is None else [weights[(i + s) % n] for i in range(nx)]
                break
        else:
            raise px.XtError("a periodic curve on a face could not be put in Parasolid's form")
    if weights is not None:
        raise px.XtError("rational curves on faces are not written")      # not made by fits
    return {"degree": deg, "knots": knots, "mults": mults, "poles": np.array(poles),
            "weights": None, "periodic": periodic, "closed": periodic}


def _trim(curve: px.Geom, edge) -> dict:
    """The piece of an accurate edge's XT curve between its ends: parameters on the XT
    curve (projected) and the points there, which lie exactly on it."""
    from OCP.Geom import Geom_Circle, Geom_Ellipse, Geom_Line
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    from OCP.gp import gp_Ax2, gp_Dir, gp_Lin
    d, k = curve.data, curve.kind
    if k == "line":
        twin, scale = Geom_Line(gp_Lin(gp_Pnt(*(np.asarray(d["pvec"]) / M)),
                                       gp_Dir(*d["direction"]))), M
    elif k in ("circle", "ellipse"):
        a2 = gp_Ax2(gp_Pnt(*(np.asarray(d["centre"]) / M)), gp_Dir(*d["normal"]),
                    gp_Dir(*d["x_axis"]))
        twin = (Geom_Circle(a2, d["radius"] / M) if k == "circle"
                else Geom_Ellipse(a2, d["major_radius"] / M, d["minor_radius"] / M))
        scale = 1.0
    else:
        twin, scale = _curve_from(np.asarray(d["poles"]) / M, d["weights"], d["knots"],
                                  d["mults"], d["degree"]), 1.0
    c = BRepAdaptor_Curve(edge)
    ts = []
    for t in (c.FirstParameter(), c.LastParameter()):
        pc = GeomAPI_ProjectPointOnCurve(c.Value(t), twin)
        ts.append(pc.LowerDistanceParameter())
    if twin.IsPeriodic() and ts[1] <= ts[0]:
        ts[1] += twin.Period()
    return {"t0": ts[0] * scale, "t1": ts[1] * scale,
            "p0": _pos(twin.Value(ts[0])), "p1": _pos(twin.Value(ts[1]))}
