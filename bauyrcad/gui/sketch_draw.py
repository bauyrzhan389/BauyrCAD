"""Pure-geometry helpers for drawing sketches: dashes, dimension graphics, relation glyphs.

Everything works in sketch plane (u, v) coordinates; `px` is the size of one screen pixel
in sketch units so arrows and gaps keep a constant on-screen size.
"""

from __future__ import annotations

import math

import numpy as np

from ..core.sketch import Arc, Circle, Line
from ..core.sketch.constraints import format_value, measure


def point_in_polygon(p, poly) -> bool:
    """Even-odd test of a point against a closed polygon (n, 2)."""
    x, y = float(p[0]), float(p[1])
    inside = False
    P = np.asarray(poly, float)
    for (x0, y0), (x1, y1) in zip(P, np.roll(P, -1, axis=0)):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def dashed(poly, px, dash=9, gap=5):
    """Split a polyline into dash segments of constant screen length."""
    poly = np.asarray(poly, float)
    if len(poly) < 2:
        return []
    seg = np.linalg.norm(np.diff(poly, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    total = s[-1]
    if total <= 0:
        return []
    period = (dash + gap) * px
    out = []
    start = 0.0
    while start < total:
        end = min(start + dash * px, total)
        ts = np.concatenate([[start], s[(s > start) & (s < end)], [end]])
        out.append(np.column_stack([np.interp(ts, s, poly[:, k]) for k in range(poly.shape[1])]))
        start += period
    return out


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0])


def _perp(v):
    return np.array([-v[1], v[0]])


def arrow(tip, direction, px, size=11):
    """Two short strokes forming an arrow head at tip, pointing along direction."""
    d = _unit(np.asarray(direction, float))
    back = -d * size * px
    c, s = math.cos(0.38), math.sin(0.38)
    r1 = np.array([c * back[0] - s * back[1], s * back[0] + c * back[1]])
    r2 = np.array([c * back[0] + s * back[1], -s * back[0] + c * back[1]])
    tip = np.asarray(tip, float)
    return [np.array([tip, tip + r1]), np.array([tip, tip + r2])]


def _ref_points(sk, c):
    pts = []
    for k, i in c.refs:
        if k == "p":
            pts.append(sk.pt(i))
        else:
            e = sk.entities[i]
            if isinstance(e, (Circle, Arc)):
                pts.append(sk.pt(e.center))
            elif isinstance(e, Line):
                pts += [sk.pt(e.p1), sk.pt(e.p2)]
    return pts


def default_label(sk, c, px):
    """A sensible default text position for a dimension."""
    pts = _ref_points(sk, c)
    if c.type in ("diameter", "radius"):
        e = sk.entities[c.refs[0][1]]
        return sk.pt(e.center) + np.array([1, 1]) / math.sqrt(2) * (sk.radius(e) + 25 * px)
    if len(pts) >= 2:
        p, q = pts[0], pts[1]
        return (p + q) / 2 + _perp(_unit(q - p)) * 30 * px
    return pts[0] + 30 * px if pts else np.zeros(2)


def _linear(p, q, L, px, axis=None):
    """Graphics for a linear dimension between p and q.
    axis None -> aligned, 'h' -> horizontal, 'v' -> vertical."""
    if axis == "h":
        P, Q = np.array([p[0], L[1]]), np.array([q[0], L[1]])
    elif axis == "v":
        P, Q = np.array([L[0], p[1]]), np.array([L[0], q[1]])
    else:
        d = _unit(q - p)
        n = _perp(d)
        off = float(np.dot(L - p, n))
        P, Q = p + n * off, q + n * off
    lines = []
    gap, over = 4 * px, 8 * px
    for a, A in ((p, P), (q, Q)):
        v = A - a
        if np.linalg.norm(v) > gap:
            u = _unit(v)
            lines.append(np.array([a + u * gap, A + u * over]))
    d = Q - P
    if np.linalg.norm(d) < 1e-12:
        return lines, L
    u = _unit(d)
    t = float(np.dot(L - P, u))
    span = np.linalg.norm(d)
    lo, hi = min(0.0, t), max(span, t)
    lines.append(np.array([P + u * lo, P + u * hi]))
    if span > 30 * px:
        lines += arrow(P, -u, px) + arrow(Q, u, px)
    else:  # small dimension: arrows outside
        lines += arrow(P, u, px) + arrow(Q, -u, px)
        lines.append(np.array([P - u * 18 * px, P]))
        lines.append(np.array([Q, Q + u * 18 * px]))
    text = P + u * t
    return lines, text


def dimension_graphics(sk, c, px):
    """(polylines, text position, text) for a dimension constraint."""
    L = np.asarray(c.label, float) if c.label is not None else default_label(sk, c, px)
    val = c.value if c.driving else measure(sk, c)
    text = format_value(c, val)
    if not c.driving:
        text = f"({text})"
    t = c.type
    if t in ("distance", "hdistance", "vdistance"):
        pts = _ref_points(sk, c)
        if len(pts) < 2:
            return [], L, text
        axis = {"hdistance": "h", "vdistance": "v"}.get(t)
        lines, tp = _linear(pts[0], pts[1], L, px, axis)
        return lines, tp, text
    if t == "point_line_distance":
        (k0, a), (_, l) = c.refs
        if k0 == "p":
            p = sk.pt(a)
        else:
            e = sk.entities[a]
            p = sk.pt(e.center) if isinstance(e, (Circle, Arc)) else sk.pt(e.p1)
        ln = sk.entities[l]
        A, B = sk.pt(ln.p1), sk.pt(ln.p2)
        d = _unit(B - A)
        foot = A + d * np.dot(p - A, d)
        lines, tp = _linear(p, foot, L, px) if np.linalg.norm(foot - p) > 1e-12 else ([], L)
        tt = np.dot(foot - A, d)
        span = np.linalg.norm(B - A)
        if tt < 0 or tt > span:          # dashed extension of the line to the foot point
            end = A if tt < 0 else B
            lines += dashed(np.array([end, foot]), px, 4, 3)
        return lines, tp, text
    if t in ("diameter", "radius"):
        e = sk.entities[c.refs[0][1]]
        C, r = sk.pt(e.center), sk.radius(e)
        u = _unit(L - C)
        rim = C + u * r
        lines = []
        if t == "diameter":
            lines.append(np.array([C - u * r, rim]))
            lines += arrow(rim, u, px) + arrow(C - u * r, -u, px)
        else:
            lines.append(np.array([C, rim]))
            lines += arrow(rim, u, px)
        if np.linalg.norm(L - C) > r:
            lines.append(np.array([rim, L - u * 10 * px]))
        return lines, L, text
    if t == "angle":
        l1, l2 = (sk.entities[i] for _, i in c.refs)
        a1, b1, a2, b2 = sk.pt(l1.p1), sk.pt(l1.p2), sk.pt(l2.p1), sk.pt(l2.p2)
        d1 = (b1 - a1) * c.data.get("s1", 1.0)
        d2 = (b2 - a2) * c.data.get("s2", 1.0)
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-12:
            return [], L, text
        s = ((a2 - a1)[0] * (b2 - a2)[1] - (a2 - a1)[1] * (b2 - a2)[0]) / (
            (b1 - a1)[0] * (b2 - a2)[1] - (b1 - a1)[1] * (b2 - a2)[0])
        V = a1 + s * (b1 - a1)
        R = max(np.linalg.norm(L - V), 12 * px)
        t1 = math.atan2(d1[1], d1[0])
        ang = math.atan2(d1[0] * d2[1] - d1[1] * d2[0], np.dot(d1, d2))
        ts = t1 + np.linspace(0, ang, 48)
        arc = V + R * np.column_stack([np.cos(ts), np.sin(ts)])
        lines = [arc]
        tang0 = _perp(_unit(d1)) * (1 if ang > 0 else -1)
        tang1 = -_perp(_unit(d2)) * (1 if ang > 0 else -1)
        lines += arrow(arc[0], -tang0, px) + arrow(arc[-1], -tang1, px)
        for base, dvec in ((a1, d1), (a2, d2)):   # extension from the segment to the arc
            e_pt = V + _unit(dvec) * R
            lines += dashed(np.array([V, e_pt]), px, 4, 3) if np.linalg.norm(e_pt - V) > 0 else []
        mid = V + R * np.array([math.cos(t1 + ang / 2), math.sin(t1 + ang / 2)])
        return lines, (L if c.label is not None else mid), text
    return [], L, text


def choose_linear_type(p, q, L):
    """SolidWorks-like choice between aligned / horizontal / vertical by cursor position."""
    d = q - p
    n = np.linalg.norm(d)
    if n < 1e-12:
        return "distance"
    t = float(np.dot(L - p, d)) / (n * n)
    if abs(d[0]) < 1e-9 * n or abs(d[1]) < 1e-9 * n or 0.0 <= t <= 1.0:
        return "distance"
    xs, ys = sorted([p[0], q[0]]), sorted([p[1], q[1]])
    if xs[0] <= L[0] <= xs[1]:
        return "hdistance"
    if ys[0] <= L[1] <= ys[1]:
        return "vdistance"
    return "distance"


def entity_anchor(sk, e):
    """Where relation glyphs attach to an entity."""
    if isinstance(e, Line):
        return (sk.pt(e.p1) + sk.pt(e.p2)) / 2, _perp(_unit(sk.pt(e.p2) - sk.pt(e.p1)))
    if isinstance(e, Circle):
        return sk.pt(e.center) + np.array([0, e.r]), np.array([0, 1.0])
    if isinstance(e, Arc):
        a0, sw = sk.arc_angles(e)
        u = np.array([math.cos(a0 + sw / 2), math.sin(a0 + sw / 2)])
        return sk.pt(e.center) + u * sk.radius(e), u
    pts = sk.sample(e, 16)
    return pts[len(pts) // 2], np.array([0, 1.0])


def snap_marker(kind: str, uv, px: float) -> list:
    """AutoCAD-style snap marker polylines around uv, ~12 px: square = endpoint / vertex,
    triangle = midpoint, circle = centre, diamond = quadrant, X = intersection, hourglass =
    nearest / on edge."""
    c = np.asarray(uv, float)
    h = 6.0 * px
    base = kind.replace("model_", "")
    if base in ("point",):
        pts = [(-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)]
    elif base == "mid":
        pts = [(-h, -h * 0.8), (h, -h * 0.8), (0, h), (-h, -h * 0.8)]
    elif base == "center":
        a = np.linspace(0, 2 * np.pi, 25)
        return [c + h * np.column_stack([np.cos(a), np.sin(a)])]
    elif base == "quad":
        pts = [(0, -h), (h, 0), (0, h), (-h, 0), (0, -h)]
    elif base == "quadrant":
        pts = [(0, -h), (h, 0), (0, h), (-h, 0), (0, -h)]
    elif base == "intersection":
        return [c + np.array([(-h, -h), (h, h)]), c + np.array([(-h, h), (h, -h)])]
    elif base == "tangent":                            # circle with a tangent line
        a = np.linspace(0, 2 * np.pi, 21)
        return [c + 0.7 * h * np.column_stack([np.cos(a), np.sin(a)]),
                c + np.array([(-h, 0.7 * h), (h, 0.7 * h)])]
    elif base == "perpendicular":                      # the perpendicular symbol
        return [c + np.array([(-h, -h), (h, -h)]), c + np.array([(0, -h), (0, h)])]
    elif base == "parallel":
        return [c + np.array([(-h, -0.6 * h), (0, h)]), c + np.array([(0, -h), (h, 0.6 * h)])]
    elif base == "angle":
        a = np.linspace(0, np.pi / 4, 9)
        return [c + np.array([(h, -h), (-h, -h), (h, h)]),
                c + np.array([-h, -h]) + 1.2 * h * np.column_stack([np.cos(a), np.sin(a)])]
    else:                                              # nearest / on edge: hourglass
        pts = [(-h, h), (h, h), (-h, -h), (h, -h), (-h, h)]
    return [c + np.array(pts, float)]
