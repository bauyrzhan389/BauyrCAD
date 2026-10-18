"""Constraint equations.

Each constraint type is a residual function r(G, c) -> list of floats that is zero when the
relation holds. ``G`` is a geometry accessor (points and radii read from the solver's
unknown vector), so the same functions serve the solver, measurement and checks.

Dimensions store their target in ``c.value``; with value=None a residual function returns
the *measured* quantity instead (used to create/display dimensions).
"""

from __future__ import annotations

import math

import numpy as np

from .model import Arc, Circle, Line, Spline, spline_params


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _norm(v):
    return math.hypot(v[0], v[1])


class Geo:
    """Reads point coordinates / radii either from the sketch or from a parameter vector."""

    def __init__(self, sketch, X=None, pidx=None, ridx=None):
        self.sk, self.X, self.pidx, self.ridx = sketch, X, pidx or {}, ridx or {}

    def P(self, pid):
        i = self.pidx.get(pid)
        if i is None or self.X is None:
            return self.sk.points[pid].xy
        return self.X[i:i + 2]

    def E(self, eid):
        return self.sk.entities[eid]

    def R(self, eid):
        e = self.sk.entities[eid]
        if isinstance(e, Circle):
            i = self.ridx.get(eid)
            return e.r if (i is None or self.X is None) else self.X[i]
        return _norm(self.P(e.start) - self.P(e.center))

    def center(self, eid):
        return self.P(self.E(eid).center)

    def line(self, eid):
        e = self.E(eid)
        return self.P(e.p1), self.P(e.p2)


def _val(c, measured):
    return measured if c.value is None else measured - c.value


def _refs(c):
    return [i for _, i in c.refs]


def _is(G, eid, cls):
    return isinstance(G.E(eid), cls)


def _roundish(G, eid):
    return isinstance(G.E(eid), (Circle, Arc))


# ---- relations -----------------------------------------------------------------

def r_coincident(G, c):
    (k0, a), (k1, b) = c.refs
    if k0 == "p" and k1 == "p":
        return list(G.P(a) - G.P(b))
    p, e = (a, b) if k0 == "p" else (b, a)
    return r_point_on(G, p, e)


def r_point_on(G, pid, eid):
    p = G.P(pid)
    if _is(G, eid, Line):
        a, b = G.line(eid)
        d = b - a
        return [_cross(d, p - a) / max(_norm(d), 1e-12)]
    if _roundish(G, eid):
        return [_norm(p - G.center(eid)) - G.R(eid)]
    if _is(G, eid, Spline):
        return [_spline_offset(np.array([G.P(q) for q in G.E(eid).pts]), p)]
    return []


def _spline_offset(pts, p) -> float:
    """Signed distance of p from the spline through pts, normal to the curve at its closest
    point (beyond an end: from the end tangent's extension, so the residual stays smooth).
    The same chord-length cubic as the sketch draws (model.spline_eval)."""
    if len(pts) == 2:
        a, b = pts
        d = b - a
        return float(_cross(d, p - a) / max(_norm(d), 1e-12))
    from scipy.interpolate import CubicSpline
    t = spline_params(pts)
    cs = CubicSpline(t, pts, axis=0, bc_type="not-a-knot" if len(pts) > 3 else "natural")
    ts = np.linspace(0.0, 1.0, 32 * len(pts) + 1)
    u = float(ts[int(np.argmin(np.linalg.norm(cs(ts) - p, axis=1)))])
    for _ in range(30):                      # Newton on (c(u) - p) . c'(u) = 0
        c, d1, d2 = cs(u), cs(u, 1), cs(u, 2)
        g = float(np.dot(c - p, d1))
        h = float(np.dot(d1, d1) + np.dot(c - p, d2))
        if abs(h) < 1e-300:
            break
        u_new = min(max(u - g / h, 0.0), 1.0)
        if abs(u_new - u) < 1e-15:
            u = u_new
            break
        u = u_new
    c, d = cs(u), cs(u, 1)
    return float(_cross(d, p - c) / max(_norm(d), 1e-12))


def r_midpoint(G, c):
    p, l = c.refs[0][1], c.refs[1][1]
    a, b = G.line(l)
    return list(G.P(p) - (a + b) / 2)


def _hv(G, c, axis):
    if len(c.refs) == 1:
        a, b = G.line(c.refs[0][1])
    else:
        a, b = G.P(c.refs[0][1]), G.P(c.refs[1][1])
    return [a[axis] - b[axis]]


def r_horizontal(G, c):
    return _hv(G, c, 1)


def r_vertical(G, c):
    return _hv(G, c, 0)


def _dirs(G, c):
    a1, b1 = G.line(c.refs[0][1])
    a2, b2 = G.line(c.refs[1][1])
    return b1 - a1, b2 - a2


def r_parallel(G, c):
    d1, d2 = _dirs(G, c)
    return [_cross(d1, d2) / max(_norm(d1) * _norm(d2), 1e-12)]


def r_perpendicular(G, c):
    d1, d2 = _dirs(G, c)
    return [np.dot(d1, d2) / max(_norm(d1) * _norm(d2), 1e-12)]


def r_collinear(G, c):
    l1, l2 = c.refs[0][1], c.refs[1][1]
    e2 = G.E(l2)
    return r_point_on(G, e2.p1, l1) + r_point_on(G, e2.p2, l1)


def _size(G, eid):
    if _is(G, eid, Line):
        a, b = G.line(eid)
        return _norm(b - a)
    return G.R(eid)


def r_equal(G, c):
    return [_size(G, c.refs[0][1]) - _size(G, c.refs[1][1])]


def r_concentric(G, c):
    return list(G.center(c.refs[0][1]) - G.center(c.refs[1][1]))


def r_tangent(G, c):
    e1, e2 = c.refs[0][1], c.refs[1][1]
    s = c.data.get("s", 1.0)
    if _is(G, e2, Line):
        e1, e2 = e2, e1
    if _is(G, e1, Line):
        a, b = G.line(e1)
        d = b - a
        at = c.data.get("at")
        if at is not None:
            # tangent where the line's end point lies on the circle (it has a coincident
            # relation): the line is perpendicular to the radius there. The distance form
            # below is singular together with that coincident relation.
            return [float(np.dot(G.P(at) - G.center(e2), d)) / max(_norm(d), 1e-12)]
        return [s * _cross(d, G.center(e2) - a) / max(_norm(d), 1e-12) - G.R(e2)]
    dist = _norm(G.center(e1) - G.center(e2))
    if c.data.get("mode") == "internal":
        return [dist - s * (G.R(e1) - G.R(e2))]
    return [dist - (G.R(e1) + G.R(e2))]


def r_fix(G, c):
    out = []
    xy = c.data["xy"]
    k, i = c.refs[0]
    pids = [i] if k == "p" else G.E(i).point_ids()
    for pid, p0 in zip(pids, xy):
        out += list(G.P(pid) - np.asarray(p0))
    if k == "e" and _is(G, i, Circle):
        out.append(G.R(i) - c.data["r"])
    return out


def r_symmetric(G, c):
    p, q, l = c.refs[0][1], c.refs[1][1], c.refs[2][1]
    a, b = G.line(l)
    d = b - a
    n = max(_norm(d), 1e-12)
    P, Q = G.P(p), G.P(q)
    mid = (P + Q) / 2
    return [_cross(d, mid - a) / n, np.dot(Q - P, d) / n]


# ---- dimensions ------------------------------------------------------------------

def _two_points(G, c):
    if len(c.refs) == 1:            # a line: its endpoints
        return G.line(c.refs[0][1])
    return G.P(c.refs[0][1]), G.P(c.refs[1][1])


def r_distance(G, c):
    """Aligned distance: point-point, line length, or centre distances."""
    pts = []
    for k, i in c.refs:
        if k == "p":
            pts.append(G.P(i))
        elif _roundish(G, i):
            pts.append(G.center(i))
        else:
            pts.extend(G.line(i))
    return [_val(c, _norm(pts[1] - pts[0]))]


def r_hdistance(G, c):
    a, b = _two_points(G, c)
    return [_val(c, c.data.get("s", 1.0) * (b[0] - a[0]))]


def r_vdistance(G, c):
    a, b = _two_points(G, c)
    return [_val(c, c.data.get("s", 1.0) * (b[1] - a[1]))]


def r_point_line_distance(G, c):
    """Perpendicular distance from a point (or circle centre / line start) to a line."""
    (k0, a), (k1, l) = c.refs
    if k0 == "p":
        p = G.P(a)
    elif _roundish(G, a):
        p = G.center(a)
    else:
        p = G.line(a)[0]
    la, lb = G.line(l)
    d = lb - la
    return [_val(c, c.data.get("s", 1.0) * _cross(d, p - la) / max(_norm(d), 1e-12))]


def r_diameter(G, c):
    return [_val(c, 2.0 * G.R(c.refs[0][1]))]


def r_radius(G, c):
    return [_val(c, G.R(c.refs[0][1]))]


def r_angle(G, c):
    d1, d2 = _dirs(G, c)
    d1 = d1 * c.data.get("s1", 1.0)
    d2 = d2 * c.data.get("s2", 1.0)
    ang = math.atan2(_cross(d1, d2), np.dot(d1, d2)) * c.data.get("sgn", 1.0)
    return [_val(c, ang)]


# ---- internal (implicit) ----------------------------------------------------------

def r_arc_internal(G, c):
    e = G.E(c.refs[0][1])
    cen = G.P(e.center)
    return [_norm(G.P(e.end) - cen) - _norm(G.P(e.start) - cen)]


class CType:
    def __init__(self, name, fn, glyph="", dimension=False):
        self.name, self.fn, self.glyph, self.dimension = name, fn, glyph, dimension


TYPES = {t.name: t for t in [
    CType("coincident", r_coincident, "◉"),
    CType("midpoint", r_midpoint, "M"),
    CType("horizontal", r_horizontal, "—"),
    CType("vertical", r_vertical, "|"),
    CType("parallel", r_parallel, "∥"),
    CType("perpendicular", r_perpendicular, "⊥"),
    CType("collinear", r_collinear, "⋯"),
    CType("equal", r_equal, "="),
    CType("concentric", r_concentric, "◎"),
    CType("tangent", r_tangent, "T"),
    CType("fix", r_fix, "⚓"),
    CType("symmetric", r_symmetric, "⇆"),
    CType("distance", r_distance, dimension=True),
    CType("hdistance", r_hdistance, dimension=True),
    CType("vdistance", r_vdistance, dimension=True),
    CType("point_line_distance", r_point_line_distance, dimension=True),
    CType("diameter", r_diameter, dimension=True),
    CType("radius", r_radius, dimension=True),
    CType("angle", r_angle, dimension=True),
    CType("arc_internal", r_arc_internal),
]}

RELATION_NAMES = {
    "coincident": "Coincident", "midpoint": "Midpoint", "horizontal": "Horizontal",
    "vertical": "Vertical", "parallel": "Parallel", "perpendicular": "Perpendicular",
    "collinear": "Collinear", "equal": "Equal", "concentric": "Concentric",
    "tangent": "Tangent", "fix": "Fix", "symmetric": "Symmetric",
}


def residual(sketch, c, G=None) -> list[float]:
    return TYPES[c.type].fn(G or Geo(sketch), c)


def measure(sketch, c) -> float:
    """Current value of a dimension's measured quantity."""
    v, c.value = c.value, None
    try:
        return float(TYPES[c.type].fn(Geo(sketch), c)[0])
    finally:
        c.value = v


def is_dimension(c) -> bool:
    return TYPES[c.type].dimension


def format_value(c, value=None) -> str:
    v = c.value if value is None else value
    if v is None:
        v = 0.0
    if c.type == "angle":
        return f"{math.degrees(v):.4g}°"
    if c.type == "diameter":
        return f"Ø{v:.4g}"
    if c.type == "radius":
        return f"R{v:.4g}"
    return f"{v:.4g}"


# ---- which relations make sense for a selection (SolidWorks "Add Relations") -----------

def applicable_relations(sketch, refs) -> list[str]:
    pts = [i for k, i in refs if k == "p"]
    ents = [sketch.entities[i] for k, i in refs if k == "e"]
    lines = [e for e in ents if isinstance(e, Line)]
    rounds = [e for e in ents if isinstance(e, (Circle, Arc))]
    n = len(refs)
    out = []
    if n == 1 and lines:
        out += ["horizontal", "vertical", "fix"]
    elif n == 1 and (pts or rounds or ents):
        out += ["fix"]
    if n == 2 and len(pts) == 2:
        out += ["coincident", "horizontal", "vertical"]
    if n == 2 and len(pts) == 1 and len(ents) == 1:
        out += ["coincident"] + (["midpoint"] if lines else [])
    if n == 2 and len(lines) == 2:
        out += ["parallel", "perpendicular", "collinear", "equal"]
    if n == 2 and len(lines) == 1 and len(rounds) == 1:
        out += ["tangent"]
    if n == 2 and len(rounds) == 2:
        out += ["concentric", "equal", "tangent"]
    if n == 3 and len(pts) == 2 and len(lines) == 1:
        out += ["symmetric"]
    if n >= 2 and len(lines) == n:
        out = list(dict.fromkeys(out + ["horizontal", "vertical", "parallel", "collinear", "equal"]))
    if n > 2 and len(pts) == n:
        out += ["horizontal", "vertical"]
    return out


def mixed_equal(sketch, c) -> bool:
    """An Equal between a line and an arc / circle (length = radius): SolidWorks' Equal
    relates lines OR arcs and circles only, so BauyrCAD no longer makes it either."""
    ents = [sketch.entities.get(i) for k, i in c.refs if k == "e"]
    return (c.type == "equal" and any(isinstance(e, Line) for e in ents)
            and any(isinstance(e, (Circle, Arc)) for e in ents))


def make_relation(sketch, rtype, refs) -> list:
    """Build the constraint(s) for relation `rtype` on the selection. Not yet added.
    Raises ValueError for an Equal between a line and an arc / circle (as SolidWorks)."""
    from .model import Constraint
    pts = [r for r in refs if r[0] == "p"]
    ents = [r for r in refs if r[0] == "e"]
    out = []
    if rtype == "equal" and mixed_equal(sketch, Constraint(0, "equal", ents)):
        raise ValueError("Equal relates lines (length) or arcs and circles (radius), not a line "
                         "with an arc or circle (as in SolidWorks)")

    def C(t, rr, **data):
        return Constraint(0, t, list(rr), data=data)

    if rtype in ("horizontal", "vertical"):
        if len(pts) >= 2 and not ents:
            out += [C(rtype, [pts[0], p]) for p in pts[1:]]
        else:
            out += [C(rtype, [e]) for e in ents]
    elif rtype in ("parallel", "collinear", "equal") and len(ents) > 2:
        out += [C(rtype, [ents[0], e]) for e in ents[1:]]
    elif rtype == "fix":
        k, i = refs[0]
        if k == "p":
            out.append(C("fix", [refs[0]], xy=[list(sketch.pt(i))]))
        else:
            e = sketch.entities[i]
            data = {"xy": [list(sketch.pt(p)) for p in e.point_ids()]}
            if isinstance(e, Circle):
                data["r"] = e.r
            out.append(C("fix", [refs[0]], **data))
    elif rtype == "tangent":
        c = C("tangent", ents)
        e1, e2 = (sketch.entities[i] for _, i in ents)
        if isinstance(e2, Line):
            e1, e2 = e2, e1
        if isinstance(e1, Line):
            a, b = sketch.pt(e1.p1), sketch.pt(e1.p2)
            c.data["s"] = 1.0 if _cross(b - a, sketch.pt(e2.center) - a) >= 0 else -1.0
        else:
            d = _norm(sketch.pt(e1.center) - sketch.pt(e2.center))
            r1, r2 = sketch.radius(e1), sketch.radius(e2)
            if abs(d - abs(r1 - r2)) < abs(d - (r1 + r2)):
                c.data["mode"] = "internal"
                c.data["s"] = 1.0 if r1 >= r2 else -1.0
        out.append(c)
    elif rtype == "symmetric":
        out.append(C("symmetric", pts + ents))
    elif rtype == "midpoint":
        out.append(C("midpoint", pts + ents))
    else:
        out.append(C(rtype, pts + ents))
    return out
