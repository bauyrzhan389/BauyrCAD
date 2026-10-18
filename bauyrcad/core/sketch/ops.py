"""Sketch editing operations: pick, intersect, trim, extend, fillet, chamfer, offset,
mirror, move/copy/rotate/scale and patterns."""

from __future__ import annotations

import math

import numpy as np

from . import builders
from .model import TOL, cross2, Arc, Circle, Line, PointEntity, SketchError, Spline

TWO_PI = 2 * math.pi


# ============================================================================
# Picking
# ============================================================================

def dist_to_entity(sk, e, p) -> float:
    p = np.asarray(p, float)
    if isinstance(e, Line):
        a, b = sk.pt(e.p1), sk.pt(e.p2)
        d = b - a
        t = np.clip(np.dot(p - a, d) / max(np.dot(d, d), 1e-300), 0, 1)
        return float(np.linalg.norm(a + t * d - p))
    if isinstance(e, Circle):
        return abs(float(np.linalg.norm(p - sk.pt(e.center))) - e.r)
    if isinstance(e, Arc):
        c = sk.pt(e.center)
        ang = math.atan2(*(p - c)[::-1])
        if _in_arc(sk, e, ang):
            return abs(float(np.linalg.norm(p - c)) - sk.radius(e))
        return min(np.linalg.norm(p - sk.pt(e.start)), np.linalg.norm(p - sk.pt(e.end)))
    if isinstance(e, PointEntity):
        return float(np.linalg.norm(p - sk.pt(e.p)))
    pts = sk.sample(e, 64)
    a, b = pts[:-1], pts[1:]
    d = b - a
    t = np.clip(np.einsum("ij,ij->i", p - a, d) / np.maximum(np.einsum("ij,ij->i", d, d), 1e-300), 0, 1)
    return float(np.min(np.linalg.norm(a + t[:, None] * d - p, axis=1)))


def pick(sk, p, tol, points=True, entities=True, exclude=()):
    """Nearest ("p", id) or ("e", id) within tol; points win over curves."""
    p = np.asarray(p, float)
    if points:
        best, bd = None, tol
        used = {q for e in sk.entities.values() for q in e.point_ids()} | {0}
        for pid in used:
            if ("p", pid) in exclude:
                continue
            d = float(np.linalg.norm(sk.pt(pid) - p))
            if d < bd:
                best, bd = pid, d
        if best is not None:
            return ("p", best)
    if entities:
        best, bd = None, tol
        for e in sk.entities.values():
            if ("e", e.id) in exclude or isinstance(e, PointEntity):
                continue
            d = dist_to_entity(sk, e, p)
            if d < bd:
                best, bd = e.id, d
        if best is not None:
            return ("e", best)
    return None


def entities_in_box(sk, lo, hi):
    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
    out = []
    for e in sk.entities.values():
        s = sk.sample(e, 32)
        if (s >= lo).all() and (s <= hi).all():
            out.append(e.id)
    return out


def nearest_on(sk, e, p):
    """Closest point on entity e to p (used for 'point on curve' snapping)."""
    p = np.asarray(p, float)
    if isinstance(e, Line):
        a, b = sk.pt(e.p1), sk.pt(e.p2)
        d = b - a
        t = np.clip(np.dot(p - a, d) / max(np.dot(d, d), 1e-300), 0, 1)
        return a + t * d
    if isinstance(e, (Circle, Arc)):
        c = sk.pt(e.center)
        v = p - c
        return c + v / max(np.linalg.norm(v), 1e-12) * sk.radius(e)
    return None


# ============================================================================
# Intersections (analytic for lines / circles / arcs)
# ============================================================================

def _in_arc(sk, e, ang, eps=1e-9):
    a0, sw = sk.arc_angles(e)
    return ((ang - a0) % TWO_PI) <= sw + eps


def _line_line(a, b, c, d):
    r, s = b - a, d - c
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-14:
        return []
    qp = c - a
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    u = (qp[0] * r[1] - qp[1] * r[0]) / den
    return [(t, u)]


def _line_circle(a, b, c, r):
    d = b - a
    f = a - c
    A, B, C = d @ d, 2 * f @ d, f @ f - r * r
    disc = B * B - 4 * A * C
    if disc < 0 or A < 1e-300:
        return []
    sq = math.sqrt(disc)
    return sorted({(-B - sq) / (2 * A), (-B + sq) / (2 * A)})


def _circle_circle(c1, r1, c2, r2):
    d = np.linalg.norm(c2 - c1)
    if d < 1e-12 or d > r1 + r2 + 1e-12 or d < abs(r1 - r2) - 1e-12:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    m = c1 + a * (c2 - c1) / d
    perp = np.array([-(c2 - c1)[1], (c2 - c1)[0]]) / d
    return [m + h * perp, m - h * perp] if h > 1e-12 else [m]


def _on_entity(sk, e, p, eps):
    if isinstance(e, Line):
        return dist_to_entity(sk, e, p) < eps
    if isinstance(e, Circle):
        return True
    if isinstance(e, Arc):
        c = sk.pt(e.center)
        return _in_arc(sk, e, math.atan2(*(p - c)[::-1]), 1e-7)
    return False


def intersections(sk, e, others, extend_line=False):
    """Intersection points of e with the other entities (curves, splines approximated)."""
    eps = 1e-7 * sk.scale()
    pts = []
    for o in others:
        if o.id == e.id or isinstance(o, PointEntity):
            continue
        cand = []
        if isinstance(e, Line) and isinstance(o, Line):
            a, b, c, d = sk.pt(e.p1), sk.pt(e.p2), sk.pt(o.p1), sk.pt(o.p2)
            for t, u in _line_line(a, b, c, d):
                if -1e-9 <= u <= 1 + 1e-9 and (extend_line or -1e-9 <= t <= 1 + 1e-9):
                    cand.append(a + t * (b - a))
        elif isinstance(e, Line) and isinstance(o, (Circle, Arc)):
            a, b = sk.pt(e.p1), sk.pt(e.p2)
            for t in _line_circle(a, b, sk.pt(o.center), sk.radius(o)):
                if extend_line or -1e-9 <= t <= 1 + 1e-9:
                    q = a + t * (b - a)
                    if _on_entity(sk, o, q, eps):
                        cand.append(q)
        elif isinstance(e, (Circle, Arc)) and isinstance(o, Line):
            a, b = sk.pt(o.p1), sk.pt(o.p2)
            for t in _line_circle(a, b, sk.pt(e.center), sk.radius(e)):
                if -1e-9 <= t <= 1 + 1e-9:
                    cand.append(a + t * (b - a))
        elif isinstance(e, (Circle, Arc)) and isinstance(o, (Circle, Arc)):
            for q in _circle_circle(sk.pt(e.center), sk.radius(e), sk.pt(o.center), sk.radius(o)):
                if _on_entity(sk, o, q, eps):
                    cand.append(q)
        elif isinstance(o, Spline) or isinstance(e, Spline):
            from .profiles import polyline_hits
            cand = list(polyline_hits(sk.sample(e, 96), sk.sample(o, 96), eps))
        pts += [np.asarray(q) for q in cand]
    return pts


def _line_param(sk, e, p):
    a, b = sk.pt(e.p1), sk.pt(e.p2)
    d = b - a
    return float(np.dot(np.asarray(p) - a, d) / max(np.dot(d, d), 1e-300))


def _angle_on(sk, e, p):
    c = sk.pt(e.center)
    return math.atan2(*(np.asarray(p) - c)[::-1])


# ============================================================================
# Trim / Extend
# ============================================================================

KEEP_ON_TRIM = {"horizontal", "vertical", "parallel", "perpendicular", "collinear",
                "concentric", "tangent", "coincident", "midpoint"}


def _drop_size_constraints(sk, eid):
    """After trimming, dimensions/equalities on the trimmed curve no longer make sense."""
    sk.constraints = [c for c in sk.constraints
                      if ("e", eid) not in c.refs or c.type in KEEP_ON_TRIM - {"midpoint"}]


def _point_on_constraints(sk, pid, pos, others):
    for o in others:
        if isinstance(o, (Line, Circle, Arc)) and dist_to_entity(sk, o, pos) < 1e-6 * sk.scale():
            if pid in o.point_ids():
                continue
            ends = [q for q in o.end_ids() if np.linalg.norm(sk.pt(q) - pos) < 1e-6 * sk.scale()]
            if ends:
                sk.add_constraint("coincident", [("p", pid), ("p", ends[0])])
            else:
                sk.add_constraint("coincident", [("p", pid), ("e", o.id)])
            return


def trim(sk, eid, click) -> bool:
    """Power trim: remove the piece of entity eid between the nearest intersections
    around the clicked position (the whole entity if nothing intersects it)."""
    e = sk.entities[eid]
    others = [o for o in sk.entities.values() if o.id != eid]
    hits = intersections(sk, e, others)
    click = np.asarray(click, float)
    if isinstance(e, Line):
        ts = sorted({round(_line_param(sk, e, h), 12) for h in hits if 1e-9 < _line_param(sk, e, h) < 1 - 1e-9})
        tc = _line_param(sk, e, click)
        lo = max([t for t in ts if t < tc], default=None)
        hi = min([t for t in ts if t > tc], default=None)
        if lo is None and hi is None:
            sk.remove_entity(eid)
            return True
        a, b = sk.pt(e.p1), sk.pt(e.p2)
        d = b - a
        _drop_size_constraints(sk, eid)
        if lo is not None and hi is not None:
            p_lo = sk.add_point(*(a + lo * d))
            p_hi = sk.add_point(*(a + hi * d))
            old_p2 = e.p2
            e.p2 = p_lo
            new = sk.add_line(p_hi, old_p2, e.construction)
            for c in list(sk.constraints_of("e", eid)):
                if c.type in ("horizontal", "vertical"):
                    sk.add_constraint(c.type, [("e", new.id)])
            sk.add_constraint("collinear", [("e", eid), ("e", new.id)])
            _point_on_constraints(sk, p_lo, sk.pt(p_lo), others)
            _point_on_constraints(sk, p_hi, sk.pt(p_hi), others)
        elif lo is not None:            # keep [0, lo]
            old = e.p2
            e.p2 = sk.add_point(*(a + lo * d))
            _point_on_constraints(sk, e.p2, sk.pt(e.p2), others)
            sk.cleanup_points([old])
        else:                           # keep [hi, 1]
            old = e.p1
            e.p1 = sk.add_point(*(a + hi * d))
            _point_on_constraints(sk, e.p1, sk.pt(e.p1), others)
            sk.cleanup_points([old])
        return True

    if isinstance(e, (Circle, Arc)):
        c = sk.pt(e.center)
        r = sk.radius(e)
        ac = _angle_on(sk, e, click)
        if isinstance(e, Circle):
            angs = sorted({round(_angle_on(sk, e, h) % TWO_PI, 12) for h in hits})
            if len(angs) < 2:
                sk.remove_entity(eid)
                return True
            rel = sorted(((a - ac) % TWO_PI, a) for a in angs)
            a_hi = rel[0][1]                        # first intersection CCW from click
            a_lo = rel[-1][1]                       # first intersection CW from click
            # keep the arc from a_hi CCW to a_lo
            s = sk.add_point(*(c + r * np.array([math.cos(a_hi), math.sin(a_hi)])))
            t = sk.add_point(*(c + r * np.array([math.cos(a_lo), math.sin(a_lo)])))
            arc = Arc(e.center, s, t, id=eid, construction=e.construction)
            sk.entities[eid] = arc
            sk.constraints = [k for k in sk.constraints if ("e", eid) not in k.refs
                              or k.type in KEEP_ON_TRIM | {"diameter", "radius", "equal"}]
            _point_on_constraints(sk, s, sk.pt(s), others)
            _point_on_constraints(sk, t, sk.pt(t), others)
            return True
        # arc
        a0, sw = sk.arc_angles(e)
        uc = (ac - a0) % TWO_PI
        us = sorted({round((_angle_on(sk, e, h) - a0) % TWO_PI, 12) for h in hits})
        us = [u for u in us if 1e-9 < u < sw - 1e-9]
        lo = max([u for u in us if u < uc], default=None)
        hi = min([u for u in us if u > uc], default=None)
        if lo is None and hi is None:
            sk.remove_entity(eid)
            return True

        def pt_at(u):
            return c + r * np.array([math.cos(a0 + u), math.sin(a0 + u)])

        keep = KEEP_ON_TRIM | {"radius", "diameter", "equal"}
        sk.constraints = [k for k in sk.constraints if ("e", eid) not in k.refs or k.type in keep]
        if lo is not None and hi is not None:
            p_lo, p_hi = sk.add_point(*pt_at(lo)), sk.add_point(*pt_at(hi))
            old_end = e.end
            e.end = p_lo
            new = sk.add_arc(e.center, p_hi, old_end, e.construction)
            sk.add_constraint("equal", [("e", eid), ("e", new.id)])
            _point_on_constraints(sk, p_lo, sk.pt(p_lo), others)
            _point_on_constraints(sk, p_hi, sk.pt(p_hi), others)
        elif lo is not None:
            old = e.end
            e.end = sk.add_point(*pt_at(lo))
            _point_on_constraints(sk, e.end, sk.pt(e.end), others)
            sk.cleanup_points([old])
        else:
            old = e.start
            e.start = sk.add_point(*pt_at(hi))
            _point_on_constraints(sk, e.start, sk.pt(e.start), others)
            sk.cleanup_points([old])
        return True

    sk.remove_entity(eid)   # splines / points: delete
    return True


def extend(sk, eid, click) -> bool:
    """Extend the end of a line or arc nearest the click up to the next curve."""
    e = sk.entities[eid]
    others = [o for o in sk.entities.values() if o.id != eid]
    click = np.asarray(click, float)
    if isinstance(e, Line):
        a, b = sk.pt(e.p1), sk.pt(e.p2)
        at_end = np.linalg.norm(click - b) < np.linalg.norm(click - a)
        hits = intersections(sk, e, others, extend_line=True)
        ts = [_line_param(sk, e, h) for h in hits]
        cand = [t for t in ts if t > 1 + 1e-9] if at_end else [t for t in ts if t < -1e-9]
        if not cand:
            return False
        t = min(cand) if at_end else max(cand)
        pid = e.p2 if at_end else e.p1
        if len(sk.entities_using_point(pid)) > 1:
            raise SketchError("That end is connected to another curve")
        sk.set_pt(pid, a + t * (b - a))
        _point_on_constraints(sk, pid, sk.pt(pid), others)
        return True
    if isinstance(e, Arc):
        c, r = sk.pt(e.center), sk.radius(e)
        a0, sw = sk.arc_angles(e)
        at_end = np.linalg.norm(click - sk.pt(e.end)) < np.linalg.norm(click - sk.pt(e.start))
        full = Circle(e.center, r, id=-1)
        sk.entities[-1] = full
        try:
            hits = intersections(sk, full, others)
        finally:
            del sk.entities[-1]
        us = [(_angle_on(sk, e, h) - a0) % TWO_PI for h in hits]
        us = [u for u in us if sw + 1e-9 < u < TWO_PI - 1e-9]
        if not us:
            return False
        u = min(us) if at_end else max(us)
        pid = e.end if at_end else e.start
        if len(sk.entities_using_point(pid)) > 1:
            raise SketchError("That end is connected to another curve")
        sk.set_pt(pid, c + r * np.array([math.cos(a0 + u), math.sin(a0 + u)]))
        _point_on_constraints(sk, pid, sk.pt(pid), others)
        return True
    return False


# ============================================================================
# Fillet / Chamfer
# ============================================================================

def _corner_lines(sk, pid):
    lines = [e for e in sk.entities_using_point(pid) if isinstance(e, Line) and not e.construction]
    if len(lines) != 2:
        raise SketchError("Select a corner where exactly two lines meet")
    dirs = []
    for ln in lines:
        other = ln.p2 if ln.p1 == pid else ln.p1
        v = sk.pt(other) - sk.pt(pid)
        dirs.append((ln, v / np.linalg.norm(v), np.linalg.norm(v)))
    return dirs


def _move_line_end(sk, ln, pid, new_pid):
    if ln.p1 == pid:
        ln.p1 = new_pid
    else:
        ln.p2 = new_pid


def _drop_corner_constraints(sk, pid):
    sk.constraints = [c for c in sk.constraints if ("p", pid) not in c.refs]


def fillet(sk, pid: int, radius: float, add_dimension=True):
    """Round the corner between two lines at point pid with a tangent arc."""
    (l1, u1, len1), (l2, u2, len2) = _corner_lines(sk, pid)
    cosang = float(np.clip(np.dot(u1, u2), -1, 1))
    ang = math.acos(cosang)
    if ang < 1e-6 or ang > math.pi - 1e-6:
        raise SketchError("Lines are parallel at this corner")
    d = radius / math.tan(ang / 2)
    if d >= min(len1, len2) - 1e-9:
        raise SketchError("Fillet radius is too large for these lines")
    P = sk.pt(pid)
    bis = (u1 + u2) / np.linalg.norm(u1 + u2)
    C = P + bis * radius / math.sin(ang / 2)
    t1, t2 = sk.add_point(*(P + u1 * d)), sk.add_point(*(P + u2 * d))
    _drop_corner_constraints(sk, pid)
    _move_line_end(sk, l1, pid, t1)
    _move_line_end(sk, l2, pid, t2)
    # CCW from t1 to t2 if the corner turns that way
    if cross2(sk.pt(t1) - C, sk.pt(t2) - C) > 0:
        arc = sk.add_arc(tuple(C), t1, t2)
    else:
        arc = sk.add_arc(tuple(C), t2, t1)
    for ln in (l1, l2):
        a, b = sk.pt(ln.p1), sk.pt(ln.p2)
        sk.add_constraint("tangent", [("e", ln.id), ("e", arc.id)],
                          s=1.0 if cross2(b - a, C - a) >= 0 else -1.0)
    if add_dimension:
        sk.add_constraint("radius", [("e", arc.id)], radius, label=list(C + (P - C) * 1.6))
    sk.cleanup_points([pid])
    return arc


def chamfer(sk, pid: int, d1: float, d2: float | None = None):
    d2 = d1 if d2 is None else d2
    (l1, u1, len1), (l2, u2, len2) = _corner_lines(sk, pid)
    if d1 >= len1 - 1e-9 or d2 >= len2 - 1e-9:
        raise SketchError("Chamfer distance is too large for these lines")
    P = sk.pt(pid)
    t1, t2 = sk.add_point(*(P + u1 * d1)), sk.add_point(*(P + u2 * d2))
    _drop_corner_constraints(sk, pid)
    _move_line_end(sk, l1, pid, t1)
    _move_line_end(sk, l2, pid, t2)
    ln = sk.add_line(t1, t2)
    sk.cleanup_points([pid])
    return ln


# ============================================================================
# Offset
# ============================================================================

def _chain_order(sk, eids):
    """Order connected curves head-to-tail; returns [(entity, reversed)] and closed flag."""
    ents = [sk.entities[i] for i in eids]
    if len(ents) == 1:
        return [(ents[0], False)], isinstance(ents[0], Circle)
    remaining = list(ents)
    chain = [(remaining.pop(0), False)]

    def ends(e, rev):
        a, b = e.end_ids()
        return (b, a) if rev else (a, b)

    grown = True
    while remaining and grown:
        grown = False
        tail = ends(*chain[-1])[1]
        head = ends(*chain[0])[0]
        for e in list(remaining):
            a, b = e.end_ids()
            if a == tail or b == tail:
                chain.append((e, b == tail))
                remaining.remove(e)
                grown = True
                break
            if a == head or b == head:
                chain.insert(0, (e, a == head))
                remaining.remove(e)
                grown = True
                break
    if remaining:
        raise SketchError("Offset works on one connected chain of curves at a time")
    closed = ends(*chain[0])[0] == ends(*chain[-1])[1]
    return chain, closed


def offset(sk, eids, distance: float):
    """Offset a chain of lines/arcs (or one circle). Positive = to the left of the chain
    direction (outside for a counter-clockwise loop)."""
    if not eids:
        raise SketchError("Select the curves to offset")
    chain, closed = _chain_order(sk, eids)
    if len(chain) == 1 and isinstance(chain[0][0], Circle):
        e = chain[0][0]
        r = e.r + distance
        if r <= TOL:
            raise SketchError("Offset distance is larger than the radius")
        c = sk.add_circle(e.center, r)
        return [c]
    # orientation of a closed chain: make positive distance go outward
    if closed:
        poly = np.vstack([_oriented_sample(sk, e, rev) for e, rev in chain])
        area = 0.5 * np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - poly[:, 1] * np.roll(poly[:, 0], -1))
        if area > 0:
            distance = -distance  # right of a CCW loop is outside
    pieces = []
    for e, rev in chain:
        if isinstance(e, Line):
            a, b = sk.pt(e.p1), sk.pt(e.p2)
            if rev:
                a, b = b, a
            u = (b - a) / np.linalg.norm(b - a)
            n = np.array([-u[1], u[0]])
            pieces.append(("line", a + n * distance, b + n * distance))
        elif isinstance(e, Arc):
            c, r = sk.pt(e.center), sk.radius(e)
            s, t = sk.pt(e.start), sk.pt(e.end)
            # walking CCW (not reversed) the centre is on the left
            rr = r - distance if not rev else r + distance
            if rr <= TOL:
                raise SketchError("Offset distance is larger than an arc radius")
            ps, pt_ = c + (s - c) * rr / r, c + (t - c) * rr / r
            pieces.append(("arc", e.center, (pt_, ps) if rev else (ps, pt_), rev))
        else:
            raise SketchError("Offset supports lines, arcs and circles")
    # join consecutive pieces at the intersection of offset lines
    n = len(pieces)
    starts = [np.array(p[1] if p[0] == "line" else p[2][0], float) for p in pieces]
    ends_ = [np.array(p[2] if p[0] == "line" else p[2][1], float) for p in pieces]
    joins = range(n if closed else n - 1)
    for i in joins:
        j = (i + 1) % n
        if pieces[i][0] == "line" and pieces[j][0] == "line":
            hit = _line_line(starts[i], ends_[i], starts[j], ends_[j])
            if hit:
                t, _ = hit[0]
                q = starts[i] + t * (ends_[i] - starts[i])
                ends_[i], starts[j] = q, q.copy()
        if np.linalg.norm(ends_[i] - starts[j]) > 1e-9 * sk.scale():
            mid = (ends_[i] + starts[j]) / 2        # arcs stay tangent: ends already meet
            ends_[i], starts[j] = mid, mid.copy()
    pids_s = [sk.add_point(*p) for p in starts]
    pids_e = [None] * n
    for i in range(n):
        j = (i + 1) % n
        if closed or i < n - 1:
            pids_e[i] = pids_s[j]
        else:
            pids_e[i] = sk.add_point(*ends_[i])
    out = []
    for i, p in enumerate(pieces):
        if p[0] == "line":
            out.append(sk.add_line(pids_s[i], pids_e[i]))
        else:
            c, rev = p[1], p[3]           # arcs share the source centre (concentric)
            out.append(sk.add_arc(c, pids_e[i], pids_s[i]) if rev
                       else sk.add_arc(c, pids_s[i], pids_e[i]))
    # SolidWorks-style offset relations: keep offset lines parallel to their sources and
    # drive the offset with one distance dimension.
    dim_done = False
    for (src, _), new in zip(chain, out):
        if isinstance(src, Line) and isinstance(new, Line):
            sk.add_constraint("parallel", [("e", src.id), ("e", new.id)])
            if not dim_done:
                a, b = sk.pt(src.p1), sk.pt(src.p2)
                q = sk.pt(new.p1)
                sgn = 1.0 if cross2(b - a, q - a) >= 0 else -1.0
                mid = (sk.pt(new.p1) + sk.pt(new.p2)) / 2
                sk.add_constraint("point_line_distance", [("e", new.id), ("e", src.id)],
                                  abs(distance), label=list(mid + (mid - (a + b) / 2) * 0.8), s=sgn)
                dim_done = True
    return out


def _oriented_sample(sk, e, rev):
    s = sk.sample(e, 24)
    return s[::-1] if rev else s


# ============================================================================
# Copy / transform / mirror / patterns
# ============================================================================

def _copy_entities(sk, eids, fn, reuse_fixed_points=None):
    """Copy entities mapping every point through fn(xy). Points shared between the
    copied entities stay shared. reuse_fixed_points(pid) -> True keeps the original point."""
    pmap = {}

    def P(pid):
        if pid not in pmap:
            if reuse_fixed_points and reuse_fixed_points(pid):
                pmap[pid] = pid
            else:
                pmap[pid] = sk.add_point(*fn(sk.pt(pid)))
        return pmap[pid]

    out = []
    for eid in eids:
        e = sk.entities[eid]
        if isinstance(e, Line):
            out.append(sk.add_line(P(e.p1), P(e.p2), e.construction))
        elif isinstance(e, Circle):
            out.append(sk.add_circle(P(e.center), e.r, e.construction))
        elif isinstance(e, Arc):
            s, t = P(e.start), P(e.end)
            c = P(e.center)
            if getattr(fn, "reflects", False):
                s, t = t, s          # a mirror reverses orientation
            out.append(sk.add_arc(c, s, t, e.construction))
        elif isinstance(e, Spline):
            out.append(sk.add_spline([P(p) for p in e.pts], e.construction))
        elif isinstance(e, PointEntity):
            out.append(sk.add_point_entity(P(e.p)))
    return out, pmap


def transform(sk, eids, M, t, copy=False, scale_radius=1.0):
    """Apply x -> M x + t to the entities (moving them, or creating a transformed copy)."""
    def fn(p):
        return M @ p + t
    fn.reflects = np.linalg.det(M) < 0
    if copy:
        out, _ = _copy_entities(sk, eids, fn)
        for e in out:
            if isinstance(e, Circle):
                e.r *= scale_radius
        return out
    pids = {p for i in eids for p in sk.entities[i].point_ids()}
    for pid in pids:
        sk.set_pt(pid, fn(sk.pt(pid)))
    for i in eids:
        e = sk.entities[i]
        if isinstance(e, Circle):
            e.r *= scale_radius
    # relations that pinned the moved geometry would pull it back: drop fixes on them
    sk.constraints = [c for c in sk.constraints if not (
        c.type == "fix" and any((k == "p" and i in pids) or (k == "e" and i in eids) for k, i in c.refs))]
    return [sk.entities[i] for i in eids]


def rotation(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s], [s, c]])


def move(sk, eids, dx, dy, copy=False):
    return transform(sk, eids, np.eye(2), np.array([dx, dy]), copy)


def rotate(sk, eids, center, angle, copy=False):
    R = rotation(angle)
    c = np.asarray(center, float)
    return transform(sk, eids, R, c - R @ c, copy)


def scale(sk, eids, center, factor, copy=False):
    c = np.asarray(center, float)
    return transform(sk, eids, np.eye(2) * factor, c - factor * c, copy, scale_radius=factor)


def mirror(sk, eids, axis_eid, symmetric_relations=True):
    """Mirror entities about a line (typically a centreline). Points on the axis are reused
    so mirrored halves stay connected."""
    ax = sk.entities[axis_eid]
    if not isinstance(ax, Line):
        raise SketchError("Mirror about a line or centreline")
    a, b = sk.pt(ax.p1), sk.pt(ax.p2)
    u = (b - a) / np.linalg.norm(b - a)
    M = 2 * np.outer(u, u) - np.eye(2)

    def fn(p):
        return a + M @ (p - a)
    fn.reflects = True
    eps = 1e-7 * sk.scale()

    def on_axis(pid):
        p = sk.pt(pid)
        return abs(cross2(u, p - a)) < eps

    ids = [i for i in eids if i != axis_eid]
    out, pmap = _copy_entities(sk, ids, fn, reuse_fixed_points=on_axis)
    if symmetric_relations:
        for src, dst in pmap.items():
            if src != dst and src != 0:
                sk.add_constraint("symmetric", [("p", src), ("p", dst), ("e", axis_eid)])
    return out


def linear_pattern(sk, eids, direction_deg, spacing, count, direction2_deg=None, spacing2=0.0, count2=1):
    out = []
    d1 = np.array([math.cos(math.radians(direction_deg)), math.sin(math.radians(direction_deg))])
    d2 = (np.array([math.cos(math.radians(direction2_deg)), math.sin(math.radians(direction2_deg))])
          if direction2_deg is not None else np.zeros(2))
    for i in range(count):
        for j in range(max(count2, 1)):
            if i == 0 and j == 0:
                continue
            out += move(sk, eids, *(d1 * spacing * i + d2 * spacing2 * j), copy=True)
    return out


def circular_pattern(sk, eids, center, count, total_deg=360.0):
    if count < 2:
        return []
    full = abs(abs(total_deg) - 360.0) < 1e-9
    step = math.radians(total_deg) / (count if full else count - 1)
    out = []
    for i in range(1, count):
        out += rotate(sk, eids, center, step * i, copy=True)
    return out


def toggle_construction(sk, eids):
    for i in eids:
        e = sk.entities.get(i)
        if e is not None and not isinstance(e, PointEntity):
            e.construction = not e.construction


def delete(sk, refs):
    for k, i in refs:
        if k == "e":
            sk.remove_entity(i)
        elif k == "p" and i != 0:
            for e in sk.entities_using_point(i):
                sk.remove_entity(e.id)
            sk.cleanup_points([i])
        elif k == "c":
            sk.remove_constraint(i)


# ============================================================================
# Convert Entities (model edges -> sketch curves)
# ============================================================================

def convert(sk, items, to_local, normal, fix=True):
    """Project model edges into the sketch (SolidWorks Convert Entities).

    items: edge dicts as modeling.edge_infos gives them plus "pts" (a 3D polyline): lines
    become lines, circles / arcs whose axis is normal to the sketch become circles / arcs,
    anything else (inclined circles, B-splines) a spline through the projected polyline.
    to_local(xyz) -> (u, v); normal: the sketch plane normal. Ends are shared with existing
    sketch points at the same place, so converted outlines are closed profiles. fix=True adds
    a Fix relation to each new curve (the converted geometry is fully defined).
    """
    from . import constraints, solver
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    scale = max(sk.scale(), 1.0)
    for it in items:
        if "pts" in it and len(it["pts"]):
            scale = max(scale, float(np.ptp(np.asarray(it["pts"]), axis=0).max()))
    tol = 1e-7 * scale
    index = {}

    def key(uv):
        return (round(float(uv[0]) / tol), round(float(uv[1]) / tol))

    for pid in sk.points:
        index.setdefault(key(sk.pt(pid)), pid)

    def P(uv):
        k = key(uv)
        if k not in index:
            index[k] = sk.add_point(float(uv[0]), float(uv[1]))
        return index[k]

    def L(p):
        return np.asarray(to_local(p), float)

    made = []
    have = {frozenset((e.p1, e.p2)) for e in sk.entities.values() if isinstance(e, Line)}
    have |= {("c", e.center, round(e.r / tol)) for e in sk.entities.values() if isinstance(e, Circle)}
    have |= {("a", e.center, e.start, e.end) for e in sk.entities.values() if isinstance(e, Arc)}
    for it in items:
        kind = it.get("kind")
        exact_circle = kind in ("circle", "arc") and abs(float(np.dot(it["axis"], n))) > 1 - 1e-9
        if kind == "line":
            a, b = L(it["p0"]), L(it["p1"])
            if np.linalg.norm(b - a) < tol * 10:
                continue                          # an edge along the normal projects to a point
            k = frozenset((P(a), P(b)))
            if k not in have:                      # edges above one another project once
                have.add(k)
                made.append(sk.add_line(P(a), P(b)))
        elif exact_circle and kind == "circle":
            k = ("c", P(L(it["center"])), round(float(it["radius"]) / tol))
            if k not in have:
                have.add(k)
                made.append(sk.add_circle(k[1], float(it["radius"])))
        elif exact_circle:
            c, a, b, m = L(it["center"]), L(it["p0"]), L(it["p1"]), L(it["mid"])
            ccw = cross2(b - a, m - a) < 0          # CCW from a to b passes through m
            s_, e_ = (P(a), P(b)) if ccw else (P(b), P(a))
            k = ("a", P(c), s_, e_)
            if k not in have:
                have.add(k)
                made.append(sk.add_arc(P(c), s_, e_))
        else:
            pts = np.array([L(q) for q in it["pts"]])
            keep = [pts[0]]
            for q in pts[1:]:
                if np.linalg.norm(q - keep[-1]) > tol * 100:
                    keep.append(q)
            if len(keep) < 2:
                continue
            closed = np.linalg.norm(keep[-1] - keep[0]) < tol * 1000
            if closed:                             # a closed curve as two splines
                keep[-1] = keep[0]
                h = len(keep) // 2
                parts = [keep[:h + 1], keep[h:]]
            else:
                parts = [keep]
            for part in parts:
                ids = [P(part[0])] + [sk.add_point(*q) for q in part[1:-1]] + [P(part[-1])]
                made.append(sk.add_spline(ids))
    if fix:
        for e in made:
            for c in constraints.make_relation(sk, "fix", [("e", e.id)]):
                solver.try_add(sk, c)
    return made


# ============================================================================
# Repair Sketch
# ============================================================================

def repair(sk, gap=None) -> dict:
    """SolidWorks Repair Sketch: close small gaps between curve ends (merge ends closer than
    `gap`, default 0.1 % of the sketch size), delete zero-length lines and duplicate curves
    lying on top of each other. Returns counts of what was fixed."""
    from .model import ORIGIN
    gap = gap if gap is not None else 1e-3 * sk.scale()
    out = {"gaps_closed": 0, "zero_length": 0, "duplicates": 0}
    for e in list(sk.entities.values()):
        if isinstance(e, Line) and np.linalg.norm(sk.pt(e.p1) - sk.pt(e.p2)) < TOL * 10:
            keep, drop = (e.p2, e.p1) if e.p1 != ORIGIN else (e.p1, e.p2)
            sk.remove_entity(e.id)
            if drop in sk.points and keep in sk.points and drop != keep:
                sk.replace_point(drop, keep)
            out["zero_length"] += 1
    while True:
        ends = sorted({p for e in sk.entities.values() for p in e.end_ids()})
        best = None
        for i, a in enumerate(ends):
            for b in ends[i + 1:]:
                d = float(np.linalg.norm(sk.pt(a) - sk.pt(b)))
                if d < gap and (best is None or d < best[0]):
                    best = (d, a, b)
        if best is None:
            break
        _, a, b = best
        keep, drop = (a, b) if (a == ORIGIN or sk.points[a].fixed) else (b, a)
        if sk.points[drop].fixed and drop != ORIGIN:
            break
        if not (keep == ORIGIN or sk.points[keep].fixed):
            sk.set_pt(keep, (sk.pt(a) + sk.pt(b)) / 2)
        sk.replace_point(drop, keep)
        out["gaps_closed"] += 1
    seen = set()
    for e in list(sk.entities.values()):
        if isinstance(e, Line):
            k = ("l", frozenset((e.p1, e.p2)), e.construction)
        elif isinstance(e, Circle):
            k = ("c", e.center, round(e.r / TOL), e.construction)
        elif isinstance(e, Arc):
            k = ("a", e.center, e.start, e.end, e.construction)
        else:
            continue
        if k in seen:
            sk.remove_entity(e.id)
            out["duplicates"] += 1
        else:
            seen.add(k)
    return out
