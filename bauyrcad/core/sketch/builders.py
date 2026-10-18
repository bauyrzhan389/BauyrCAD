"""Drawing tools: create sketch geometry together with SolidWorks-style automatic relations.

Point arguments may be existing point ids (int) or (x, y) coordinates.
Every builder returns the list of created entities.
"""

from __future__ import annotations

import math

import numpy as np

from .model import TOL, cross2, SketchError


def _xy(sk, p):
    return sk.pt(p) if isinstance(p, (int, np.integer)) else np.asarray(p, float)


def _rel(sk, t, *refs, **data):
    return sk.add_constraint(t, refs, **data)


def _hv_relation(sk, line, tol_deg=1e-6):
    a, b = sk.pt(line.p1), sk.pt(line.p2)
    d = b - a
    if abs(d[1]) <= abs(d[0]) * tol_deg:
        _rel(sk, "horizontal", ("e", line.id))
    elif abs(d[0]) <= abs(d[1]) * tol_deg:
        _rel(sk, "vertical", ("e", line.id))


# ---- lines ---------------------------------------------------------------------------------

def line(sk, a, b, construction=False, auto_hv=True):
    ln = sk.add_line(a, b, construction)
    if auto_hv:
        _hv_relation(sk, ln)
    return [ln]


def centerline(sk, a, b):
    return line(sk, a, b, construction=True)


def midpoint_line(sk, mid, end):
    """Line symmetric about its midpoint (SolidWorks Midpoint Line)."""
    m, e = _xy(sk, mid), _xy(sk, end)
    ln = sk.add_line(tuple(2 * m - e), end)
    mp = sk.pid(mid)
    sk.add_point_entity(mp)
    _rel(sk, "midpoint", ("p", mp), ("e", ln.id))
    _hv_relation(sk, ln)
    return [ln]


# ---- rectangles -------------------------------------------------------------------------------

def _closed_polyline(sk, pids, construction=False):
    n = len(pids)
    return [sk.add_line(pids[i], pids[(i + 1) % n], construction) for i in range(n)]


def corner_rectangle(sk, p1, p3):
    a, c = _xy(sk, p1), _xy(sk, p3)
    if abs(c[0] - a[0]) < TOL or abs(c[1] - a[1]) < TOL:
        raise SketchError("Rectangle has zero width or height")
    ids = [sk.pid(p1), sk.add_point(c[0], a[1]), sk.pid(p3), sk.add_point(a[0], c[1])]
    L = _closed_polyline(sk, ids)
    for i in (0, 2):
        _rel(sk, "horizontal", ("e", L[i].id))
    for i in (1, 3):
        _rel(sk, "vertical", ("e", L[i].id))
    return L


def _center_diagonals(sk, L, center):
    """Construction diagonals whose midpoint is the centre point (SolidWorks centre rectangle)."""
    d1 = sk.add_line(L[0].p1, L[2].p1, construction=True)
    d2 = sk.add_line(L[1].p1, L[3].p1, construction=True)
    cp = sk.pid(center)
    sk.add_point_entity(cp)
    _rel(sk, "midpoint", ("p", cp), ("e", d1.id))
    _rel(sk, "midpoint", ("p", cp), ("e", d2.id))
    return [d1, d2]


def center_rectangle(sk, center, corner):
    c, k = _xy(sk, center), _xy(sk, corner)
    h = np.abs(k - c)
    if h.min() < TOL:
        raise SketchError("Rectangle has zero width or height")
    corners = [c + [-h[0], -h[1]], c + [h[0], -h[1]], c + [h[0], h[1]], c + [-h[0], h[1]]]
    ids = [sk.add_point(*p) for p in corners]
    L = _closed_polyline(sk, ids)
    for i in (0, 2):
        _rel(sk, "horizontal", ("e", L[i].id))
    for i in (1, 3):
        _rel(sk, "vertical", ("e", L[i].id))
    return L + _center_diagonals(sk, L, center)


def three_point_rectangle(sk, p1, p2, p3):
    """First edge p1-p2, third point sets the width (any angle)."""
    a, b, c = _xy(sk, p1), _xy(sk, p2), _xy(sk, p3)
    d = b - a
    n = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-12)
    w = float(np.dot(c - b, n))
    if np.linalg.norm(d) < TOL or abs(w) < TOL:
        raise SketchError("Rectangle has zero width or height")
    ids = [sk.pid(p1), sk.pid(p2), sk.add_point(*(b + n * w)), sk.add_point(*(a + n * w))]
    L = _closed_polyline(sk, ids)
    _rel(sk, "perpendicular", ("e", L[0].id), ("e", L[1].id))
    _rel(sk, "parallel", ("e", L[0].id), ("e", L[2].id))
    _rel(sk, "parallel", ("e", L[1].id), ("e", L[3].id))
    _hv_relation(sk, L[0])
    return L


def three_point_center_rectangle(sk, center, mid_edge, corner):
    c, m, k = _xy(sk, center), _xy(sk, mid_edge), _xy(sk, corner)
    u = m - c
    lu = np.linalg.norm(u)
    if lu < TOL:
        raise SketchError("Rectangle has zero size")
    u = u / lu
    v = np.array([-u[1], u[0]])
    a, b = lu, float(np.dot(k - c, v))
    if abs(b) < TOL:
        raise SketchError("Rectangle has zero width")
    corners = [c - u * a - v * b, c + u * a - v * b, c + u * a + v * b, c - u * a + v * b]
    ids = [sk.add_point(*p) for p in corners]
    L = _closed_polyline(sk, ids)
    _rel(sk, "perpendicular", ("e", L[0].id), ("e", L[1].id))
    _rel(sk, "parallel", ("e", L[0].id), ("e", L[2].id))
    _rel(sk, "parallel", ("e", L[1].id), ("e", L[3].id))
    return L + _center_diagonals(sk, L, center)


def parallelogram(sk, p1, p2, p3):
    a, b, c = _xy(sk, p1), _xy(sk, p2), _xy(sk, p3)
    if abs(cross2(b - a, c - b)) < TOL:
        raise SketchError("Parallelogram points are collinear")
    ids = [sk.pid(p1), sk.pid(p2), sk.pid(p3), sk.add_point(*(a + c - b))]
    L = _closed_polyline(sk, ids)
    _rel(sk, "parallel", ("e", L[0].id), ("e", L[2].id))
    _rel(sk, "parallel", ("e", L[1].id), ("e", L[3].id))
    return L


def polygon(sk, center, vertex, sides: int, inscribed: bool = True):
    """Regular polygon; vertex is a corner (inscribed) or an edge midpoint (circumscribed)."""
    if sides < 3:
        raise SketchError("A polygon needs at least 3 sides")
    c, v = _xy(sk, center), _xy(sk, vertex)
    r = np.linalg.norm(v - c)
    if r < TOL:
        raise SketchError("Polygon has zero size")
    a0 = math.atan2(*(v - c)[::-1])
    if not inscribed:
        r = r / math.cos(math.pi / sides)
        a0 += math.pi / sides
    pts = [c + r * np.array([math.cos(a0 + 2 * math.pi * i / sides),
                             math.sin(a0 + 2 * math.pi * i / sides)]) for i in range(sides)]
    ids = [sk.add_point(*p) for p in pts]
    L = _closed_polyline(sk, ids)
    cid = sk.pid(center)
    circ = sk.add_circle(cid, r, construction=True)
    for pid in ids:
        _rel(sk, "coincident", ("p", pid), ("e", circ.id))
    for ln in L[1:]:
        _rel(sk, "equal", ("e", L[0].id), ("e", ln.id))
    return L + [circ]


# ---- circles and arcs ---------------------------------------------------------------------------

def circle(sk, center, rim):
    c, p = _xy(sk, center), _xy(sk, rim)
    return [sk.add_circle(center, float(np.linalg.norm(p - c)))]


def circumcenter(a, b, c):
    a, b, c = map(lambda p: np.asarray(p, float), (a, b, c))
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        raise SketchError("The three points are collinear")
    sa, sb, sc = a @ a, b @ b, c @ c
    return np.array([(sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d,
                     (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d])


def perimeter_circle(sk, p1, p2, p3):
    a, b, c = _xy(sk, p1), _xy(sk, p2), _xy(sk, p3)
    cen = circumcenter(a, b, c)
    return [sk.add_circle(tuple(cen), float(np.linalg.norm(a - cen)))]


def center_arc(sk, center, start, end, ccw=True):
    """Centrepoint arc; `end` only sets the angle (radius comes from start)."""
    c, s, e = _xy(sk, center), _xy(sk, start), _xy(sk, end)
    r = np.linalg.norm(s - c)
    d = e - c
    e_on = c + d / max(np.linalg.norm(d), 1e-12) * r
    end_ref = end if isinstance(end, (int, np.integer)) and abs(np.linalg.norm(d) - r) < 1e-9 else tuple(e_on)
    if ccw:
        return [sk.add_arc(center, start, end_ref)]
    return [sk.add_arc(center, end_ref, start)]


def three_point_arc(sk, start, end, through):
    s, e, m = _xy(sk, start), _xy(sk, end), _xy(sk, through)
    cen = circumcenter(s, e, m)
    # CCW from start to end must pass through m
    ccw = cross2(e - s, m - s) < 0
    cp = tuple(cen)
    return [sk.add_arc(cp, start, end) if ccw else sk.add_arc(cp, end, start)]


def tangent_arc(sk, start_pid: int, tangent, end):
    """Arc starting at an existing endpoint, tangent to `tangent` (direction) there."""
    s, e = sk.pt(start_pid), _xy(sk, end)
    t = np.asarray(tangent, float)
    t = t / max(np.linalg.norm(t), 1e-12)
    n = np.array([-t[1], t[0]])
    ch = e - s
    denom = 2 * np.dot(ch, n)
    if abs(denom) < 1e-9 * max(np.linalg.norm(ch), 1):
        raise SketchError("End point is in line with the tangent - use a line")
    r = np.dot(ch, ch) / denom          # signed: > 0 turns left (CCW)
    cen = s + n * r
    if r > 0:
        arc = sk.add_arc(tuple(cen), start_pid, end)
    else:
        arc = sk.add_arc(tuple(cen), end, start_pid)
    return [arc]


# ---- slots --------------------------------------------------------------------------------------

def straight_slot(sk, a, b, rim):
    """Slot around centreline a-b; `rim` sets the half-width."""
    A, B, P = _xy(sk, a), _xy(sk, b), _xy(sk, rim)
    d = B - A
    L = np.linalg.norm(d)
    if L < TOL:
        raise SketchError("Slot length is zero")
    u = d / L
    n = np.array([-u[1], u[0]])
    r = abs(float(np.dot(P - A, n)))
    if r < TOL:
        raise SketchError("Slot width is zero")
    ca, cb = sk.pid(a), sk.pid(b)
    p1, p2 = sk.add_point(*(A - n * r)), sk.add_point(*(B - n * r))
    p3, p4 = sk.add_point(*(B + n * r)), sk.add_point(*(A + n * r))
    l1 = sk.add_line(p1, p2)
    arc_b = sk.add_arc(cb, p2, p3)
    l2 = sk.add_line(p3, p4)
    arc_a = sk.add_arc(ca, p4, p1)
    axis = sk.add_line(ca, cb, construction=True)
    _hv_relation(sk, axis)
    for ln in (l1, l2):
        for arc in (arc_a, arc_b):
            _rel(sk, "tangent", ("e", ln.id), ("e", arc.id),
                 s=1.0 if cross2(sk.pt(ln.p2) - sk.pt(ln.p1), sk.pt(arc.center) - sk.pt(ln.p1)) >= 0 else -1.0)
    _rel(sk, "equal", ("e", arc_a.id), ("e", arc_b.id))
    return [l1, arc_b, l2, arc_a, axis]


def center_slot(sk, center, end, rim):
    C, E = _xy(sk, center), _xy(sk, end)
    A = 2 * C - E
    ents = straight_slot(sk, tuple(A), end, rim)
    axis = ents[-1]
    cp = sk.pid(center)
    sk.add_point_entity(cp)
    _rel(sk, "midpoint", ("p", cp), ("e", axis.id))
    return ents


# ---- other ------------------------------------------------------------------------------------------

def point(sk, p):
    return [sk.add_point_entity(p)]


def spline(sk, pts):
    return [sk.add_spline(pts)]
