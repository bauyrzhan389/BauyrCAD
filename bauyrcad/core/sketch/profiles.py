"""Closed profile detection: chain curves into loops, nest loops into regions.

Construction geometry and points are ignored. Curves connect through shared points,
coincident relations, or endpoints that lie on top of each other. Loops that cross each
other are reported (use Trim to clean them up), as in SolidWorks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .model import TOL, Arc, Circle, Line, SketchError, Spline, spline_eval


# ---- oriented segments (plain coordinates) ------------------------------------------------

@dataclass
class LineSeg:
    a: tuple
    b: tuple

    def sample(self):
        return np.array([self.a, self.b], float)

    def reversed(self):
        return LineSeg(self.b, self.a)


@dataclass
class ArcSeg:
    center: tuple
    a: tuple
    b: tuple
    ccw: bool = True

    @property
    def radius(self):
        return math.dist(self.center, self.a)

    def sweep(self):
        c = np.asarray(self.center)
        a0 = math.atan2(*(np.asarray(self.a) - c)[::-1])
        a1 = math.atan2(*(np.asarray(self.b) - c)[::-1])
        sw = (a1 - a0) % (2 * math.pi) if self.ccw else -((a0 - a1) % (2 * math.pi))
        return a0, sw

    def mid(self):
        a0, sw = self.sweep()
        c = np.asarray(self.center)
        return tuple(c + self.radius * np.array([math.cos(a0 + sw / 2), math.sin(a0 + sw / 2)]))

    def sample(self, n=None):
        a0, sw = self.sweep()
        k = n or max(16, int(256 * abs(sw) / (2 * math.pi)) + 1)
        t = a0 + np.linspace(0, sw, k + 1)
        pts = np.asarray(self.center) + self.radius * np.column_stack([np.cos(t), np.sin(t)])
        pts[0], pts[-1] = self.a, self.b
        return pts

    def reversed(self):
        return ArcSeg(self.center, self.b, self.a, not self.ccw)


@dataclass
class CircleSeg:
    center: tuple
    radius: float
    ccw: bool = True

    def sample(self, n=256):
        t = np.linspace(0, 2 * math.pi, n, endpoint=False)
        if not self.ccw:
            t = -t
        return np.asarray(self.center) + self.radius * np.column_stack([np.cos(t), np.sin(t)])

    def reversed(self):
        return CircleSeg(self.center, self.radius, not self.ccw)


@dataclass
class SplineSeg:
    pts: np.ndarray

    def sample(self, n=48):
        return spline_eval(self.pts, n)[0]

    def reversed(self):
        return SplineSeg(self.pts[::-1].copy())


@dataclass
class Loop:
    segments: list

    def polygon(self) -> np.ndarray:
        parts = []
        for s in self.segments:
            p = s.sample()
            parts.append(p if isinstance(s, CircleSeg) else p[:-1])
        return np.vstack(parts)

    def signed_area(self) -> float:
        if len(self.segments) == 1 and isinstance(self.segments[0], CircleSeg):
            c = self.segments[0]
            return (1 if c.ccw else -1) * math.pi * c.radius ** 2
        p = self.polygon()
        x, y = p[:, 0], p[:, 1]
        return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))

    def reversed(self) -> "Loop":
        return Loop([s.reversed() for s in reversed(self.segments)])

    def oriented(self, ccw: bool) -> "Loop":
        return self if (self.signed_area() > 0) == ccw else self.reversed()

    def sample_point(self):
        return tuple(self.polygon()[0])


@dataclass
class Region:
    outer: Loop
    holes: list = field(default_factory=list)

    def area(self) -> float:
        return abs(self.outer.signed_area()) - sum(abs(h.signed_area()) for h in self.holes)


# ---- helpers ----------------------------------------------------------------------------------

def point_in_polygon(pt, poly: np.ndarray) -> bool:
    x, y = pt
    xs, ys = poly[:, 0], poly[:, 1]
    xj, yj = np.roll(xs, 1), np.roll(ys, 1)
    cond = (ys > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = (xj - xs) * (y - ys) / (yj - ys) + xs
    return bool(np.count_nonzero(cond & (x < xint)) % 2)


def polyline_hits(P: np.ndarray, Q: np.ndarray, eps: float) -> np.ndarray:
    """Intersection points between two polylines (vectorised segment tests)."""
    a, b = P[:-1, None, :], P[1:, None, :]
    c, d = Q[None, :-1, :], Q[None, 1:, :]
    r, s = b - a, d - c
    den = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
    qp = c - a
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[..., 1] - qp[..., 1] * s[..., 0]) / den
        u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / den
    ok = (np.abs(den) > 1e-14) & (t >= -1e-9) & (t <= 1 + 1e-9) & (u >= -1e-9) & (u <= 1 + 1e-9)
    hits = (a + t[..., None] * r)[ok]
    # overlapping collinear pieces
    par = np.abs(den) <= 1e-14
    if par.any():
        cr = np.abs(qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0])
        rr = np.maximum(np.einsum("...k,...k", r, r), 1e-300)
        t0 = np.einsum("...k,...k", qp, r) / rr
        t1 = np.einsum("...k,...k", d - a, r) / rr
        lo, hi = np.minimum(t0, t1), np.maximum(t0, t1)
        ov = par & (cr < eps * np.sqrt(rr)) & (hi > 1e-9) & (lo < 1 - 1e-9) & (hi - lo > 1e-9)
        if ov.any():
            mid = np.clip((np.maximum(lo, 0) + np.minimum(hi, 1)) / 2, 0, 1)
            hits = np.vstack([hits, (a + mid[..., None] * r)[ov]])
    return hits


# ---- main ------------------------------------------------------------------------------------------

def _node_map(sketch, curves):
    """Union-find merge of points connected by coincident relations or position."""
    parent = {}

    def find(p):
        parent.setdefault(p, p)
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        parent[find(a)] = find(b)

    ends = sorted({p for e in curves for p in e.end_ids()})
    for p in ends:
        find(p)
    for c in sketch.constraints:
        if c.type == "coincident" and all(k == "p" for k, _ in c.refs):
            a, b = c.refs[0][1], c.refs[1][1]
            if a in parent and b in parent:
                union(a, b)
    tol = TOL * max(10.0, sketch.scale() * 1e-3)
    for i, p in enumerate(ends):
        for q in ends[i + 1:]:
            if np.linalg.norm(sketch.pt(p) - sketch.pt(q)) < tol:
                union(p, q)
    return find


def _segment(sketch, e, reverse=False):
    if isinstance(e, Line):
        s = LineSeg(tuple(sketch.pt(e.p1)), tuple(sketch.pt(e.p2)))
    elif isinstance(e, Arc):
        s = ArcSeg(tuple(sketch.pt(e.center)), tuple(sketch.pt(e.start)), tuple(sketch.pt(e.end)), True)
    elif isinstance(e, Spline):
        s = SplineSeg(np.array([sketch.pt(p) for p in e.pts]))
    else:
        raise TypeError(e)
    return s.reversed() if reverse else s


def find_loops(sketch) -> list[Loop]:
    curves = sketch.curves(include_construction=False)
    circles = [e for e in curves if isinstance(e, Circle)]
    opens = [e for e in curves if not isinstance(e, Circle)]
    find = _node_map(sketch, opens)
    ends = {e.id: (find(e.end_ids()[0]), find(e.end_ids()[1])) for e in opens}
    closed_self = [e for e in opens if ends[e.id][0] == ends[e.id][1]]  # closed spline
    adj: dict[int, list[int]] = {}
    for e in opens:
        i, j = ends[e.id]
        if i == j:
            continue
        adj.setdefault(i, []).append(e.id)
        adj.setdefault(j, []).append(e.id)

    open_pts = [tuple(sketch.pt(n)) for n, ls in adj.items() if len(ls) == 1]
    if open_pts:
        raise SketchError("Sketch has open contours (unconnected ends are marked in red)", open_pts)
    branch = [tuple(sketch.pt(n)) for n, ls in adj.items() if len(ls) > 2]
    if branch:
        raise SketchError("Sketch has branching contours - more than two curves meet at the "
                          "red points (use Trim or construction geometry)", branch)

    _check_crossings(sketch, curves, ends, find)

    loops, used = [], set()
    for e in opens:
        if e.id in used or ends[e.id][0] == ends[e.id][1]:
            continue
        start = e.id
        cur_node = ends[start][0]
        eid = start
        segs = []
        while True:
            used.add(eid)
            i, j = ends[eid]
            ent = sketch.entities[eid]
            forward = (i == cur_node)
            segs.append(_segment(sketch, ent, reverse=not forward))
            cur_node = j if forward else i
            nxt = [k for k in adj[cur_node] if k != eid]
            eid = nxt[0]
            if eid == start:
                break
        loops.append(Loop(segs))
    for e in closed_self:
        loops.append(Loop([_segment(sketch, e)]))
    for c in circles:
        loops.append(Loop([CircleSeg(tuple(sketch.pt(c.center)), c.r)]))
    return loops


def _check_crossings(sketch, curves, ends, find):
    eps = TOL * max(10.0, sketch.scale() * 1e-3)
    samples = {e.id: sketch.sample(e, 48 if not isinstance(e, Line) else None) for e in curves}
    nodes = {e.id: ({find(p) for p in e.end_ids()} if e.id in ends else set()) for e in curves}
    for i, e1 in enumerate(curves):
        for e2 in curves[i + 1:]:
            b1 = samples[e1.id]
            b2 = samples[e2.id]
            if (b1.max(0) < b2.min(0) - eps).any() or (b2.max(0) < b1.min(0) - eps).any():
                continue
            hits = polyline_hits(b1, b2, eps)
            if not len(hits):
                continue
            shared = [sketch.pt(n) for n in nodes[e1.id] & nodes[e2.id]]
            tol = max(eps * 10, 1e-3 * sketch.scale())
            bad = [h for h in hits if all(np.linalg.norm(h - s) > tol for s in shared)]
            if bad:
                raise SketchError("Sketch curves cross or overlap each other (use Trim)",
                                  [tuple(bad[0])])


def find_regions(sketch) -> list[Region]:
    loops = find_loops(sketch)
    if not loops:
        raise SketchError("Sketch contains no closed profiles")
    polys = [lp.polygon() for lp in loops]
    areas = [abs(lp.signed_area()) for lp in loops]
    parents = []
    for i, lp in enumerate(loops):
        p = lp.sample_point()
        parents.append([j for j in range(len(loops))
                        if j != i and areas[j] > areas[i] and point_in_polygon(p, polys[j])])
    depth = [len(p) for p in parents]
    regions = {}
    for i, lp in enumerate(loops):
        if depth[i] % 2 == 0:
            regions[i] = Region(lp.oriented(ccw=True))
    for i, lp in enumerate(loops):
        if depth[i] % 2 == 1:
            parent = min(parents[i], key=lambda j: areas[j])
            regions[parent].holes.append(lp.oriented(ccw=False))
    return list(regions.values())
