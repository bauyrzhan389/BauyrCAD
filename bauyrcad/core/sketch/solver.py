"""Geometric constraint solver.

Unknowns: x, y of every non-fixed point and the radius of every circle.
Equations: residuals of all driving constraints (+ implicit "arc endpoints equidistant").

Newton iterations with the damped minimum-norm step
    dx = -J^T (J J^T + λI)^-1 r
move the geometry as little as possible to satisfy the constraints, which is what makes
dragging feel natural: unconstrained geometry follows the mouse, constrained geometry
stays put. The Jacobian is assembled from small per-constraint finite differences.

Degrees of freedom: the null space of J tells which unknowns can still move, so each
point/entity can be coloured fully defined (black) or under defined (blue).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .constraints import TYPES, Geo
from .model import Arc, Circle, Constraint

MAX_ITER = 60


@dataclass
class SolveResult:
    ok: bool
    residual: float
    iterations: int


@dataclass
class Analysis:
    dof: int
    free_points: set = field(default_factory=set)
    free_radii: set = field(default_factory=set)

    @property
    def fully_defined(self) -> bool:
        return self.dof == 0

    def entity_defined(self, sketch, e) -> bool:
        if any(p in self.free_points for p in e.point_ids()):
            return False
        return not (isinstance(e, Circle) and e.id in self.free_radii)


class Solver:
    def __init__(self, sketch, constraints=None):
        self.sk = sketch
        self.pidx, self.ridx = {}, {}
        n = 0
        for pid, p in sketch.points.items():
            if not p.fixed:
                self.pidx[pid] = n
                n += 2
        for eid, e in sketch.entities.items():
            if isinstance(e, Circle):
                self.ridx[eid] = n
                n += 1
        self.n = n
        cons = [c for c in (sketch.constraints if constraints is None else constraints)
                if c.driving]
        cons += [Constraint(-e.id, "arc_internal", [("e", e.id)])
                 for e in sketch.entities.values() if isinstance(e, Arc)]
        self.cons = cons
        self._deps = {}
        self.scale = sketch.scale()

    # ---- vector <-> sketch ----------------------------------------------------------
    def vector(self) -> np.ndarray:
        X = np.zeros(self.n)
        for pid, i in self.pidx.items():
            X[i:i + 2] = self.sk.points[pid].xy
        for eid, i in self.ridx.items():
            X[i] = self.sk.entities[eid].r
        return X

    def write(self, X) -> None:
        for pid, i in self.pidx.items():
            p = self.sk.points[pid]
            p.x, p.y = float(X[i]), float(X[i + 1])
        for eid, i in self.ridx.items():
            self.sk.entities[eid].r = float(X[i])

    # ---- residuals and Jacobian ----------------------------------------------------------
    def deps(self, c) -> list[int]:
        key = id(c)
        if key not in self._deps:
            idx = []
            for k, i in c.refs:
                pids = [i] if k == "p" else self.sk.entities[i].point_ids()
                for p in pids:
                    if p in self.pidx:
                        idx += [self.pidx[p], self.pidx[p] + 1]
                if k == "e" and i in self.ridx:
                    idx.append(self.ridx[i])
            self._deps[key] = sorted(set(idx))
        return self._deps[key]

    def residuals(self, X, cons=None) -> np.ndarray:
        G = Geo(self.sk, X, self.pidx, self.ridx)
        out = []
        for c in (self.cons if cons is None else cons):
            out += TYPES[c.type].fn(G, c)
        return np.asarray(out, dtype=float)

    def jacobian(self, X, cons=None):
        cons = self.cons if cons is None else cons
        G = Geo(self.sk, X, self.pidx, self.ridx)
        rows = []
        h = 1e-7 * self.scale
        Xw = X.copy()
        G.X = Xw
        for c in cons:
            fn = TYPES[c.type].fn
            r0 = fn(G, c)
            block = np.zeros((len(r0), self.n))
            for j in self.deps(c):
                x0 = Xw[j]
                Xw[j] = x0 + h
                rp = fn(G, c)
                Xw[j] = x0 - h
                rm = fn(G, c)
                Xw[j] = x0
                block[:, j] = (np.asarray(rp) - np.asarray(rm)) / (2 * h)
            rows.append(block)
        return np.vstack(rows) if rows else np.zeros((0, self.n))

    # ---- solving ---------------------------------------------------------------------------
    def _newton(self, X, cons, extra=None, max_iter=MAX_ITER, tol=None):
        tol = tol or 1e-9 * self.scale

        def full_r(X):
            r = self.residuals(X, cons)
            if extra:
                r = np.concatenate([r, extra[2] * (X[extra[0]] - extra[1])])
            return r

        r = full_r(X)
        err = float(np.linalg.norm(r)) if r.size else 0.0
        it = 0
        for it in range(max_iter):
            if err < tol:
                break
            J = self.jacobian(X, cons)
            if extra:
                E = np.zeros((len(extra[0]), self.n))
                E[np.arange(len(extra[0])), extra[0]] = extra[2]
                J = np.vstack([J, E])
            A = J @ J.T
            lam = 1e-12 * (np.trace(A) / max(len(A), 1) + 1.0)
            try:
                dx = -J.T @ np.linalg.solve(A + lam * np.eye(len(A)), r)
            except np.linalg.LinAlgError:
                dx = -np.linalg.lstsq(J, r, rcond=None)[0]
            step, improved = 1.0, False
            while step > 1e-4:
                Xn = X + step * dx
                rn = full_r(Xn)
                en = float(np.linalg.norm(rn))
                if en < err:
                    improved = True
                    break
                step *= 0.5
            if not improved:
                break  # stalled: inconsistent constraints or singular configuration
            X, r, err = Xn, rn, en
        return X, err, it

    def solve(self, drag: dict | None = None) -> SolveResult:
        """Satisfy all constraints. drag = {pid: (x, y)} or {("r", eid): r} targets."""
        X0 = self.vector()
        X = X0
        tol = 1e-9 * self.scale
        if drag:
            idx, tgt = [], []
            for key, val in drag.items():
                if isinstance(key, tuple) and key[0] == "r":
                    if key[1] in self.ridx:
                        idx.append(self.ridx[key[1]])
                        tgt.append(float(val))
                elif key in self.pidx:
                    i = self.pidx[key]
                    idx += [i, i + 1]
                    tgt += [float(val[0]), float(val[1])]
            if idx:
                X, _, _ = self._newton(X, self.cons, (np.array(idx), np.array(tgt), 1.0), max_iter=25)
        X, err, it = self._newton(X, self.cons)
        ok = err < 1e3 * tol
        if ok:
            self.write(X)
        return SolveResult(ok, err, it)

    # ---- analysis ------------------------------------------------------------------------------
    def rank(self, cons=None) -> int:
        J = self.jacobian(self.vector(), cons)
        if J.size == 0:
            return 0
        s = np.linalg.svd(J, compute_uv=False)
        return int(np.sum(s > 1e-7 * max(s[0], 1.0)))

    def analyze(self) -> Analysis:
        X = self.vector()
        J = self.jacobian(X)
        if J.size == 0:
            null = np.eye(self.n)
        else:
            _, s, Vt = np.linalg.svd(J, full_matrices=True)
            rank = int(np.sum(s > 1e-7 * max(s[0] if len(s) else 1.0, 1.0)))
            null = Vt[rank:].T
        free = np.linalg.norm(null, axis=1) > 1e-6 if null.size else np.zeros(self.n, bool)
        fp = {pid for pid, i in self.pidx.items() if free[i] or free[i + 1]}
        fr = {eid for eid, i in self.ridx.items() if free[i]}
        return Analysis(null.shape[1] if null.size else 0, fp, fr)


# ---- convenience ------------------------------------------------------------------------

def solve(sketch, drag=None) -> SolveResult:
    return Solver(sketch).solve(drag)


def analyze(sketch) -> Analysis:
    return Solver(sketch).analyze()


def check_constraint(sketch, c) -> str:
    """'ok', 'redundant' (adds nothing) or 'conflict' (cannot be satisfied) for new constraint c."""
    base = Solver(sketch)
    r0 = base.rank()
    s1 = Solver(sketch, [*sketch.constraints, c])
    r1 = s1.rank()
    if r1 > r0:
        return "ok"
    res = np.abs(s1.residuals(s1.vector(), [c])).max(initial=0.0)
    return "redundant" if res < 1e-7 * sketch.scale() else "conflict"


def try_add(sketch, c) -> tuple[bool, str]:
    """Add constraint c and solve; roll back and explain if it over-defines the sketch."""
    status = check_constraint(sketch, c)
    if status != "ok":
        return False, ("This relation/dimension is already implied by existing ones "
                       "(it would over-define the sketch)" if status == "redundant" else
                       "This conflicts with existing relations/dimensions")
    snapshot = sketch.to_dict()
    c.id = sketch._next_cid
    sketch._next_cid += 1
    sketch.constraints.append(c)
    res = Solver(sketch).solve()
    if not res.ok:
        _restore(sketch, snapshot)
        return False, "The sketch could not be solved with this constraint"
    return True, ""


def _restore(sketch, snapshot):
    from .model import Sketch
    s = Sketch.from_dict(snapshot)
    sketch.points, sketch.entities, sketch.constraints = s.points, s.entities, s.constraints
    sketch._next_pid, sketch._next_eid, sketch._next_cid = s._next_pid, s._next_eid, s._next_cid


def set_dimension(sketch, c, value) -> tuple[bool, str]:
    """Change a driving dimension and re-solve; restores on failure."""
    snapshot = sketch.to_dict()
    old = c.value
    c.value = float(value)
    if not c.driving:
        return True, ""
    res = Solver(sketch).solve()
    if not res.ok:
        _restore(sketch, snapshot)
        for cc in sketch.constraints:
            if cc.id == c.id:
                cc.value = old
        return False, "The sketch cannot be solved with this value"
    return True, ""
