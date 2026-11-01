"""Assemblies: parts placed as rigid components and positioned by mates (SolidWorks style).

* A **component** references a part file (.bcad, or STEP/IGES/BREP) and has a rigid pose
  (rotation R, translation t): world = R @ local + t. A component is either fixed (grounded)
  or floating (6 degrees of freedom).
* A **mate** relates one face of each of two components. Planar faces give a plane
  (point + normal), cylindrical/conical faces an axis (point + direction):

      coincident    plane-plane: normals opposed (or aligned) and planes touch
                    axis-axis:   same axis;  plane-axis: axis lies in the plane
      parallel      plane-plane / axis-axis / plane-axis (axis parallel to the plane)
      perpendicular plane-plane / axis-axis / plane-axis (axis along the normal)
      distance      plane-plane: parallel with an offset d
      angle         plane-plane: angle between the normals
      concentric    axis-axis:   collinear axes
      lock          whole components rigidly together

* The mate solver moves floating components as little as possible (minimum-norm Newton
  steps on small rigid motions) so that all mates hold, exactly like the sketch solver does
  for sketch geometry. Dragging adds the mouse target as extra equations.

Faces are referenced in the part's own coordinates by FaceRef (index + centroid/area), so
mates survive edits of the part that don't move the mated face.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import geometry

FILE_FORMAT = "bauyrcad-assembly"
FILE_VERSION = 1
FILE_EXTENSION = ".bcasm"

MATE_TYPES = ("coincident", "parallel", "perpendicular", "distance", "angle", "concentric", "lock")
MATE_LABELS = {t: t.capitalize() for t in MATE_TYPES}

PALETTE = [(176, 186, 200), (214, 168, 110), (140, 180, 150), (170, 150, 200), (200, 140, 140),
           (120, 170, 200), (200, 190, 120), (150, 150, 150)]


class AssemblyError(Exception):
    pass


# ============================================================================
# Poses
# ============================================================================

def rodrigues(w) -> np.ndarray:
    """Rotation matrix of the rotation vector w (axis * angle)."""
    w = np.asarray(w, float)
    th = float(np.linalg.norm(w))
    if th < 1e-14:
        K = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])
        return np.eye(3) + K
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def rotation_vector(R) -> np.ndarray:
    """Inverse of rodrigues: rotation vector (axis * angle) of a rotation matrix."""
    R = np.asarray(R, float)
    c = np.clip((np.trace(R) - 1) / 2, -1.0, 1.0)
    th = math.acos(c)
    if th < 1e-9:
        return np.zeros(3)
    if math.pi - th < 1e-6:                          # 180 degrees: axis from R + I
        M = (R + np.eye(3)) / 2
        k = np.sqrt(np.maximum(np.diag(M), 0))
        i = int(np.argmax(k))
        k[i] = math.sqrt(max(M[i, i], 1e-300))
        for j in range(3):
            if j != i:
                k[j] = M[i, j] / k[i]
        return k / np.linalg.norm(k) * th
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(th))
    return w * th


def orthonormalize(R) -> np.ndarray:
    u, _, vt = np.linalg.svd(R)
    Q = u @ vt
    if np.linalg.det(Q) < 0:
        u[:, -1] *= -1
        Q = u @ vt
    return Q


@dataclass
class Pose:
    R: np.ndarray = field(default_factory=lambda: np.eye(3))
    t: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def __post_init__(self):
        self.R = np.asarray(self.R, float).reshape(3, 3)
        self.t = np.asarray(self.t, float).reshape(3)

    def point(self, p):
        return self.R @ np.asarray(p, float) + self.t

    def vector(self, v):
        return self.R @ np.asarray(v, float)

    def inverse_point(self, p):
        return self.R.T @ (np.asarray(p, float) - self.t)

    def moved(self, dt, w) -> "Pose":
        return Pose(rodrigues(w) @ self.R, self.t + np.asarray(dt, float))

    @property
    def matrix(self) -> np.ndarray:
        m = np.eye(4)
        m[:3, :3], m[:3, 3] = self.R, self.t
        return m

    def copy(self) -> "Pose":
        return Pose(self.R.copy(), self.t.copy())

    def to_dict(self):
        return {"R": np.round(self.R, 15).tolist(), "t": np.round(self.t, 12).tolist()}

    @classmethod
    def from_dict(cls, d):
        return cls(orthonormalize(np.asarray(d["R"], float)), d["t"])


# ============================================================================
# Components and mates
# ============================================================================

class Component:
    def __init__(self, id: str, name: str, source: str, pose: Pose | None = None,
                 fixed: bool = False, color=None):
        self.id, self.name, self.source = id, name, source
        self.instance_id = uuid.uuid4().hex
        self.pose = pose or Pose()
        self.fixed = fixed
        self.color = tuple(color) if color else None
        self.shape = None
        self.faces: list = []
        self.doc = None
        self._tess = None
        self.load_error: str | None = None
        self.load_warning: str | None = None     # loaded, but from a stand-in file

    # ---- loading ----------------------------------------------------------------------
    def resolve_path(self, base_dir: Path | None) -> Path:
        p = Path(self.source)
        if not p.is_absolute() and base_dir is not None:
            p = (base_dir / p)
        return p

    def load(self, base_dir: Path | None = None):
        path = self.resolve_path(base_dir)
        self.load_error = self.load_warning = None
        self._tess = None
        if not path.exists():
            # a part file lost in a copy: its STEP export next to it still shows the part
            stand_in = next((path.with_suffix(e) for e in (".step", ".stp", ".STEP")
                             if path.with_suffix(e).exists()), None)
            if stand_in is None:
                self.shape, self.faces, self.doc = None, [], None
                self.load_error = f"{path.name}: file not found"
                return self
            self.load_warning = (f"{path.name} is missing - showing {stand_in.name} instead "
                                 "(geometry only, not editable as a part)")
            path = stand_in
        try:
            if path.suffix.lower() == ".bcad":
                from .document import Document
                self.doc = Document.load(path)
                shape = self.doc.body
            else:
                from .exchange import read
                self.doc = None
                shape = read(path)
            if shape is None or geometry.is_null(shape):
                raise AssemblyError("the part has no body")
            self.shape = shape
            self.faces = geometry.face_infos(shape)
        except Exception as e:  # noqa: BLE001 - keep the assembly usable
            self.shape, self.faces = None, []
            self.load_error = f"{path.name}: {e}"
        return self

    @property
    def tessellation(self):
        if self._tess is None and self.shape is not None:
            self._tess = geometry.tessellate(self.shape)
        return self._tess

    def world_shape(self):
        return geometry.transformed(self.shape, self.pose.matrix)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "source": self.source, "fixed": self.fixed,
                "instance_id": self.instance_id,
                "pose": self.pose.to_dict(), "color": list(self.color) if self.color else None}

    @classmethod
    def from_dict(cls, d):
        comp = cls(d["id"], d["name"], d["source"], Pose.from_dict(d["pose"]), d.get("fixed", False),
                   d.get("color"))
        comp.instance_id = d.get("instance_id", "legacy:" + d["id"])
        return comp


@dataclass
class MateRef:
    component: str
    face: object                 # FaceRef (part coordinates); None for component-level (lock)

    def to_dict(self):
        return {"component": self.component, "face": self.face.to_dict() if self.face else None}

    @classmethod
    def from_dict(cls, d):
        from ..fea.study import FaceRef
        return cls(d["component"], FaceRef.from_dict(d["face"]) if d.get("face") else None)


@dataclass
class Mate:
    id: str
    name: str
    type: str
    refs: list                   # [MateRef, MateRef]
    value: float = 0.0           # distance [mm] or angle [rad]
    align: float = -1.0          # -1 anti-aligned (opposed), +1 aligned
    sign: float = 1.0            # side of the distance offset
    suppressed: bool = False
    data: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self):
        return {"id": self.id, "name": self.name, "type": self.type,
                "refs": [r.to_dict() for r in self.refs], "value": self.value,
                "align": self.align, "sign": self.sign, "suppressed": self.suppressed,
                "data": self.data}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d["name"], d["type"], [MateRef.from_dict(r) for r in d["refs"]],
                   d.get("value", 0.0), d.get("align", -1.0), d.get("sign", 1.0),
                   d.get("suppressed", False), d.get("data", {}))


def face_geometry(face):
    """('plane', point, normal) or ('axis', point, direction) in part coordinates."""
    if face.is_planar:
        return "plane", np.asarray(face.centroid, float), np.asarray(face.normal, float)
    if face.kind in ("cylinder", "cone") and face.axis is not None:
        return "axis", np.asarray(face.axis_point, float), np.asarray(face.axis, float)
    return None, None, None


def mate_options(kind1, kind2) -> list[str]:
    """Mate types that make sense for two picked face kinds ('plane'/'axis')."""
    k = {kind1, kind2}
    if k == {"plane"}:
        return ["coincident", "parallel", "perpendicular", "distance", "angle", "lock"]
    if k == {"axis"}:
        return ["concentric", "parallel", "perpendicular", "lock"]
    if k == {"plane", "axis"}:
        return ["coincident", "parallel", "perpendicular", "lock"]
    return ["lock"]


# ============================================================================
# Mate equations
# ============================================================================

def _geom_local(asm, ref):
    comp = asm.component(ref.component)
    idx = ref.face.resolve(comp.faces)
    if idx is None:
        raise AssemblyError(f"A mated face of {comp.name} no longer exists")
    kind, p, v = face_geometry(comp.faces[idx])
    if kind is None:
        raise AssemblyError(f"Unsupported face type for mating on {comp.name}")
    return kind, p, v


def _geom_world(asm, ref, poses, cache=None):
    """(kind, point, direction) of a mated face in world coordinates. `cache` keeps the
    resolved part-space geometry for the duration of a solve."""
    if cache is not None:
        key = id(ref)
        if key not in cache:
            cache[key] = _geom_local(asm, ref)
        kind, p, v = cache[key]
    else:
        kind, p, v = _geom_local(asm, ref)
    pose = poses[ref.component]
    return kind, pose.point(p), pose.vector(v)


def _axis_align(m, v1, v2):
    """a1 - s a2 with s = +-1 held fixed during a Newton iteration (see MateSolver).
    Axes have no direction; unlike a1 x a2, this has a usable slope at 90 degrees."""
    s = m.__dict__.get("_axis_sign")
    if s is None:
        s = 1.0 if float(np.dot(v1, v2)) >= 0 else -1.0
    return list(v1 - s * v2)


AXIS_MATES = {("concentric", "axis"), ("coincident", "axis"), ("parallel", "axis"),
              ("perpendicular", "mixed")}


def mate_residual(asm, m: Mate, poses, cache=None) -> list[float]:
    if m.type == "lock":
        a, b = poses[m.refs[0].component], poses[m.refs[1].component]
        R12 = np.asarray(m.data["R12"])
        t12 = np.asarray(m.data["t12"])
        return list((b.R - a.R @ R12).ravel()) + list(b.t - (a.R @ t12 + a.t))
    k1, p1, v1 = _geom_world(asm, m.refs[0], poses, cache)
    k2, p2, v2 = _geom_world(asm, m.refs[1], poses, cache)
    if k1 == "axis" and k2 == "plane":          # normalise: plane first
        k1, p1, v1, k2, p2, v2 = k2, p2, v2, k1, p1, v1
    t = m.type
    if t in ("coincident", "concentric") and k1 == k2 == "axis":
        # an axis has no direction: collinear either way round (a1 x a2 = 0)
        w = p2 - p1
        return _axis_align(m, v1, v2) + list(w - np.dot(w, v1) * v1)
    if t == "coincident" and k1 == k2 == "plane":
        return list(v1 - m.align * v2) + [float(np.dot(p2 - p1, v1))]
    if t == "coincident":                                     # plane-axis
        return [float(np.dot(v2, v1)), float(np.dot(p2 - p1, v1))]
    if t == "distance":
        return list(v1 - m.align * v2) + [float(np.dot(p2 - p1, v1)) - m.sign * m.value]
    if t == "parallel":
        if k1 != k2:                                          # axis parallel to plane
            return [float(np.dot(v1, v2))]
        if k1 == "axis":
            return _axis_align(m, v1, v2)
        return list(v1 - m.align * v2)                        # planes keep their side
    if t == "perpendicular":
        if k1 != k2:                                          # axis along the normal
            return _axis_align(m, v1, v2)
        return [float(np.dot(v1, v2))]
    if t == "angle":
        # signed angle about a reference axis carried by component 1 (smooth at 0 and 180)
        u = poses[m.refs[0].component].vector(m.data["axis1"])
        u = u / np.linalg.norm(u)
        phi = math.atan2(float(np.dot(u, np.cross(v1, v2))), float(np.dot(v1, v2)))
        return [math.sin(phi - m.sign * m.value)]
    raise AssemblyError(f"Mate {m.type} is not defined for {k1}/{k2}")


# ============================================================================
# Solver
# ============================================================================

@dataclass
class SolveResult:
    ok: bool
    residual: float
    iterations: int


class MateSolver:
    def __init__(self, asm, mates=None, lock_rotation=()):
        self.asm = asm
        self.lock_rotation = set(lock_rotation)       # component ids that may only translate
        self.free = [c.id for c in asm.components if not c.fixed and c.shape is not None]
        self.index = {cid: 6 * i for i, cid in enumerate(self.free)}
        self.n = 6 * len(self.free)
        ms = asm.mates if mates is None else mates
        self.mates = [m for m in ms if not m.suppressed and self._loaded(m)]
        self._cache: dict = {}

    def _loaded(self, m):
        return all(self.asm.component(r.component) is not None and
                   self.asm.component(r.component).shape is not None for r in m.refs)

    def base_poses(self):
        return {c.id: c.pose.copy() for c in self.asm.components}

    def poses_at(self, base, x):
        out = dict(base)
        for cid, i in self.index.items():
            w = np.zeros(3) if cid in self.lock_rotation else x[i + 3:i + 6]
            out[cid] = base[cid].moved(x[i:i + 3], w)
        return out

    def residuals(self, poses, mates=None, extra=None):
        r = []
        for m in (self.mates if mates is None else mates):
            r += mate_residual(self.asm, m, poses, self._cache)
        if extra is not None:
            r += list(extra(poses))
        return np.asarray(r, float)

    def jacobian(self, base, mates=None, extra=None, h=1e-7):
        x0 = np.zeros(self.n)
        r0 = self.residuals(self.poses_at(base, x0), mates, extra)
        J = np.zeros((len(r0), self.n))
        locked = {self.index[c] + k for c in self.lock_rotation if c in self.index for k in (3, 4, 5)}
        for j in range(self.n):
            if j in locked:
                continue                     # this rotation is not an unknown
            xp, xm = x0.copy(), x0.copy()
            xp[j], xm[j] = h, -h
            J[:, j] = (self.residuals(self.poses_at(base, xp), mates, extra) -
                       self.residuals(self.poses_at(base, xm), mates, extra)) / (2 * h)
        return r0, J

    def _newton(self, base, mates=None, extra=None, max_iter=50, tol=1e-9):
        scale = max(self.asm.size(), 1.0)
        err = float(np.linalg.norm(self.residuals(base, mates, extra)))
        it = 0
        for it in range(max_iter):
            if err < tol * scale or self.n == 0:
                break
            self._freeze_axis_signs(base, mates)
            err = float(np.linalg.norm(self.residuals(base, mates, extra)))
            r, J = self.jacobian(base, mates, extra)
            A = J @ J.T
            lam = 1e-12 * (np.trace(A) / max(len(A), 1) + 1.0)
            try:
                dx = -J.T @ np.linalg.solve(A + lam * np.eye(len(A)), r)
            except np.linalg.LinAlgError:
                dx = -np.linalg.lstsq(J, r, rcond=None)[0]
            step, improved = 1.0, False
            while step > 1e-4:
                cand = self.poses_at(base, step * dx)
                e = float(np.linalg.norm(self.residuals(cand, mates, extra)))
                if e < err:
                    improved = True
                    break
                step *= 0.5
            if not improved:
                break
            base, err = cand, e
        return base, err, it

    def _freeze_axis_signs(self, poses, mates=None):
        for m in (self.mates if mates is None else mates):
            m.__dict__.pop("_axis_sign", None)
            if m.type in ("concentric", "coincident", "parallel", "perpendicular"):
                try:
                    k1, _, v1 = _geom_world(self.asm, m.refs[0], poses, self._cache)
                    k2, _, v2 = _geom_world(self.asm, m.refs[1], poses, self._cache)
                except AssemblyError:
                    continue
                if k1 == "axis" or k2 == "axis":
                    m._axis_sign = 1.0 if float(np.dot(v1, v2)) >= 0 else -1.0

    def solve(self, drag=None) -> SolveResult:
        """Satisfy all mates. drag: callable(poses) -> residuals pulling a component along."""
        base = self.base_poses()
        if drag is not None:
            # weighted least squares: mates dominate, so the component follows the mouse only
            # along the motion its mates allow (the drag target itself is not reachable)
            def weak(poses, f=drag):
                return 1e-3 * np.asarray(f(poses), float)
            base, _, _ = self._newton(base, extra=weak, max_iter=10)
        base, err, it = self._newton(base)
        scale = max(self.asm.size(), 1.0)
        ok = err < 1e-6 * scale
        if ok:
            for c in self.asm.components:
                c.pose = Pose(orthonormalize(base[c.id].R), base[c.id].t)
        return SolveResult(ok, err, it)

    def rank(self, mates=None) -> int:
        if self.n == 0:
            return 0
        r, J = self.jacobian(self.base_poses(), mates)
        if J.size == 0:
            return 0
        s = np.linalg.svd(J, compute_uv=False)
        return int(np.sum(s > 1e-6 * max(s[0], 1.0))) if len(s) else 0

    def analyze(self) -> dict:
        """{component id: remaining DOF} (fixed components: 0)."""
        out = {c.id: 0 for c in self.asm.components}
        if self.n == 0:
            return out
        _, J = self.jacobian(self.base_poses())
        if J.size == 0:
            null = np.eye(self.n)
        else:
            _, s, vt = np.linalg.svd(J, full_matrices=True)
            rank = int(np.sum(s > 1e-6 * max(s[0] if len(s) else 1.0, 1.0)))
            null = vt[rank:].T
        for cid, i in self.index.items():
            block = null[i:i + 6] if null.size else np.zeros((6, 0))
            out[cid] = int(np.linalg.matrix_rank(block, tol=1e-6)) if block.size else 0
        return out


# ============================================================================
# Assembly document
# ============================================================================

class Assembly:
    def __init__(self):
        self.components: list[Component] = []
        self.mates: list[Mate] = []
        self.path: Path | None = None

    # ---- lookup / naming ----------------------------------------------------------------
    def component(self, cid) -> Component | None:
        return next((c for c in self.components if c.id == cid), None)

    def mate(self, mid) -> Mate | None:
        return next((m for m in self.mates if m.id == mid), None)

    def _new_id(self, prefix, existing):
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    @property
    def base_dir(self) -> Path | None:
        return self.path.parent if self.path else None

    def size(self) -> float:
        lo, hi = self.bounds()
        return float(np.linalg.norm(hi - lo)) if lo is not None else 1.0

    def bounds(self):
        pts = []
        for c in self.components:
            if c.shape is None:
                continue
            lo, hi = geometry.bounding_box(c.shape)
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                for z in (lo[2], hi[2])])
            pts.append(corners @ c.pose.R.T + c.pose.t)
        if not pts:
            return None, None
        allp = np.vstack(pts)
        return allp.min(axis=0), allp.max(axis=0)

    # ---- editing ---------------------------------------------------------------------------
    def add_component(self, source, pose: Pose | None = None, fixed: bool | None = None,
                      name: str | None = None) -> Component:
        """Insert a part file. The first component is fixed (grounded) by default.
        Without a pose the part is placed beside what is already there."""
        source = str(source)
        stem = Path(source).stem
        count = sum(1 for c in self.components if Path(c.source).stem == stem) + 1
        cid = self._new_id("c", {c.id for c in self.components})
        comp = Component(cid, name or f"{stem}<{count}>", self._store_path(source))
        comp.color = PALETTE[len(self.components) % len(PALETTE)]
        comp.load(self.base_dir if not Path(comp.source).is_absolute() else None)
        if comp.load_error and Path(source).exists():
            comp.source = str(Path(source).resolve())
            comp.load()
        if comp.load_error:
            raise AssemblyError(comp.load_error)
        comp.fixed = (not self.components) if fixed is None else fixed
        if pose is not None:
            comp.pose = pose
        elif self.components:
            _, hi = self.bounds()
            lo_c, _ = geometry.bounding_box(comp.shape)
            gap = 0.15 * max(self.size(), 10.0)
            comp.pose = Pose(np.eye(3), [hi[0] + gap - lo_c[0], 0.0, 0.0])
        self.components.append(comp)
        return comp

    def _store_path(self, source) -> str:
        """Paths are stored relative to the assembly file when possible (portable folders)."""
        p = Path(source)
        if self.path is not None and p.is_absolute():
            try:
                return str(p.resolve().relative_to(self.path.parent.resolve()))
            except ValueError:
                return str(p)
        return str(p.resolve()) if p.exists() else str(p)

    def remove_component(self, cid):
        self.components = [c for c in self.components if c.id != cid]
        self.mates = [m for m in self.mates if all(r.component != cid for r in m.refs)]

    def remove_mate(self, mid):
        self.mates = [m for m in self.mates if m.id != mid]

    def new_mate(self, type_, ref1: MateRef, ref2: MateRef, value: float = 0.0) -> Mate:
        """Build a mate with orientation flags chosen to need the least motion (not added)."""
        if type_ not in MATE_TYPES:
            raise AssemblyError(f"Unknown mate type {type_!r}")
        if ref1.component == ref2.component:
            raise AssemblyError("Pick faces on two different components")
        mid = self._new_id("m", {m.id for m in self.mates})
        n = sum(1 for m in self.mates if m.type == type_) + 1
        m = Mate(mid, f"{MATE_LABELS[type_]}{n}", type_, [ref1, ref2], float(value))
        poses = {c.id: c.pose for c in self.components}
        if type_ == "lock":
            a, b = poses[ref1.component], poses[ref2.component]
            m.data["R12"] = (a.R.T @ b.R).tolist()
            m.data["t12"] = (a.R.T @ (b.t - a.t)).tolist()
            return m
        k1, p1, v1 = _geom_world(self, ref1, poses)
        k2, p2, v2 = _geom_world(self, ref2, poses)
        if k1 == "axis" and k2 == "plane":
            k1, p1, v1, k2, p2, v2 = k2, p2, v2, k1, p1, v1
        if type_ not in mate_options(k1, k2):
            raise AssemblyError(f"A {type_} mate can't join a {k1} and a {k2}")
        d = float(np.dot(v1, v2))
        if type_ in ("coincident", "distance") and k1 == k2 == "plane":
            # SolidWorks convention for face mates: the faces oppose each other (contact /
            # facing across the gap). "Flip alignment" gives the aligned variant.
            m.align = -1.0
        else:
            m.align = 1.0 if d >= 0 else -1.0             # axes, parallel: least rotation
        if type_ == "distance":
            m.sign = 1.0                                   # gap in front of face 1
        if type_ == "angle":
            m.value = float(value)
            u = self._angle_axis(ref1, ref2, v1, v2)
            m.data["axis1"] = (poses[ref1.component].R.T @ u).tolist()
            phi = math.atan2(float(np.dot(u, np.cross(v1, v2))), float(np.dot(v1, v2)))
            m.sign = 1.0 if phi >= 0 else -1.0
        return m

    def _angle_axis(self, ref1, ref2, v1, v2):
        """Rotation axis of an angle mate: the normals' cross product, or - when they are
        parallel - the rotation the existing mates still allow."""
        c = np.cross(v1, v2)
        if np.linalg.norm(c) > 1e-6:
            return c / np.linalg.norm(c)
        solver = MateSolver(self)
        if solver.n:
            _, J = solver.jacobian(solver.base_poses())
            if J.size:
                _, s, vt = np.linalg.svd(J, full_matrices=True)
                rank = int(np.sum(s > 1e-6 * max(s[0] if len(s) else 1.0, 1.0)))
                null = vt[rank:]
            else:
                null = np.eye(solver.n)
            for cid in (ref2.component, ref1.component):
                i = solver.index.get(cid)
                if i is None:
                    continue
                for vec in null:
                    w = vec[i + 3:i + 6]
                    w = w - np.dot(w, v1) * v1
                    if np.linalg.norm(w) > 1e-6:
                        return w / np.linalg.norm(w)
        a = np.array([1.0, 0, 0]) if abs(v1[0]) < 0.9 else np.array([0, 1.0, 0])
        w = np.cross(v1, a)
        return w / np.linalg.norm(w)

    def check_mate(self, m: Mate) -> str:
        """'ok', 'redundant' or 'conflict'."""
        base = MateSolver(self)
        r0 = base.rank()
        s1 = MateSolver(self, [*self.mates, m])
        r1 = s1.rank()
        if r1 > r0:
            return "ok"
        res = np.abs(s1.residuals(s1.base_poses(), [m])).max(initial=0.0)
        return "redundant" if res < 1e-6 * max(self.size(), 1) else "conflict"

    def add_mate(self, m: Mate, check=True) -> tuple[bool, str]:
        """Add mate m and solve; roll back (and explain) if it can't be satisfied."""
        if check:
            status = self.check_mate(m)
            if status != "ok":
                return False, ("This mate is already implied by the others (over-defining)"
                               if status == "redundant" else
                               "This mate conflicts with the existing mates")
        saved = {c.id: c.pose.copy() for c in self.components}
        self._pre_orient(m)
        self.mates.append(m)
        res = self.solve()
        if not res.ok:
            self.mates.remove(m)
            for c in self.components:
                c.pose = saved[c.id]
            return False, "The mates could not be satisfied with this mate (try flipping the alignment)"
        return True, ""

    def _pre_orient(self, m: Mate):
        """If a free component faces exactly the wrong way for an alignment mate, turn it
        180 degrees first: at 180 degrees the linearised equations give no direction."""
        if m.type not in ("coincident", "distance", "parallel"):
            return
        poses = {c.id: c.pose for c in self.components}
        try:
            k1, p1, v1 = _geom_world(self, m.refs[0], poses)
            k2, p2, v2 = _geom_world(self, m.refs[1], poses)
        except AssemblyError:
            return
        if not (k1 == k2 == "plane"):
            return                                          # axis relations are sign-free
        if float(np.dot(v1, m.align * v2)) > -0.5:
            return                                          # Newton can handle it
        for ref, pt, v in ((m.refs[1], p2, v2), (m.refs[0], p1, v1)):
            comp = self.component(ref.component)
            if comp is None or comp.fixed:
                continue
            a = np.cross(v, [1.0, 0, 0])
            if np.linalg.norm(a) < 1e-6:
                a = np.cross(v, [0, 1.0, 0])
            Rr = rodrigues(a / np.linalg.norm(a) * math.pi)
            comp.pose = Pose(Rr @ comp.pose.R, Rr @ (comp.pose.t - pt) + pt)
            return

    def solve(self, drag=None) -> SolveResult:
        return MateSolver(self).solve(drag)

    def dof(self) -> dict:
        return MateSolver(self).analyze()

    def mobility(self) -> int:
        """Degrees of freedom of the whole assembly (a mechanism's mobility). Per-component
        dof() counts every motion a part can make, including its parents' in a chain."""
        solver = MateSolver(self)
        return solver.n - solver.rank()

    # ---- interference ------------------------------------------------------------------------
    def interference(self, min_volume: float = 1e-3, ignore=()) -> list[dict]:
        """Pairs of components whose solids overlap by more than min_volume [mm3], largest
        first: [{"a": id, "b": id, "volume": mm3}]. Touching faces (a shaft in a bore of the
        same size) are not interference. `ignore`: pairs of component ids to skip (e.g. a
        press fit that is modelled line-on-line)."""
        skip = {frozenset(p) for p in ignore}
        comps = [c for c in self.components if c.shape is not None]
        shapes = {c.id: c.world_shape() for c in comps}
        boxes = {cid: geometry.bounding_box(sh) for cid, sh in shapes.items()}
        out = []
        # Sweep the widest-spread axis: separated components never enter the
        # pair loop. Keep original indices so pair orientation and equal-volume
        # ordering remain compatible with the component-tree order.
        if len(comps) < 2:
            return out
        centers = np.array([(boxes[c.id][0] + boxes[c.id][1]) / 2 for c in comps])
        axis = int(np.argmax(np.ptp(centers, axis=0)))
        ordered = sorted(enumerate(comps), key=lambda item: boxes[item[1].id][0][axis])
        active = []
        hits = []
        for index, current in ordered:
            low, high = boxes[current.id]
            active = [(i, c) for i, c in active if boxes[c.id][1][axis] >= low[axis]]
            for other_index, other in active:
                i, j = sorted((index, other_index))
                a, b = comps[i], comps[j]
                if frozenset((a.id, b.id)) in skip:
                    continue
                other_low, other_high = boxes[other.id]
                if np.any(high < other_low) or np.any(other_high < low):
                    continue
                v = geometry.common_volume(shapes[a.id], shapes[b.id])
                if v > min_volume:
                    hits.append((i, j, {"a": a.id, "b": b.id, "volume": v}))
            active.append((index, current))
        out = [hit for _, _, hit in sorted(hits, key=lambda hit: hit[:2])]
        return sorted(out, key=lambda d: -d["volume"])

    def clearance(self, a, b=None) -> dict:
        """Minimum distance between component a and component b, or (b=None) the nearest other
        component: {"a", "b", "distance" mm, "point_a", "point_b" (world), "overlap" mm3}.
        Distance 0 means touching or overlapping; overlap > 0 means they interfere."""
        ca = self.component(a)
        if ca is None or ca.shape is None:
            raise AssemblyError(f"No solid component {a!r}")
        others = [self.component(b)] if b is not None else \
            [c for c in self.components if c.id != ca.id and c.shape is not None]
        if not others or others[0] is None or others[0].shape is None:
            raise AssemblyError(f"No solid component {b!r}")
        sa = ca.world_shape()
        la, ha = geometry.bounding_box(sa)
        cands = []
        for c in others:
            sc = c.world_shape()
            lc, hc = geometry.bounding_box(sc)
            cands.append((geometry.box_distance(la, ha, lc, hc), c, sc))
        best = None
        for lower, c, sc in sorted(cands, key=lambda t: t[0]):
            if best is not None and lower > best["distance"]:
                break                                  # boxes are lower bounds: done
            d, pa, pb = geometry.min_distance(sa, sc)
            if best is None or d < best["distance"]:
                best = {"a": ca.id, "b": c.id, "distance": d, "point_a": pa, "point_b": pb,
                        "overlap": 0.0, "_shape": sc}
        if best["distance"] < 1e-9:
            best["overlap"] = geometry.common_volume(sa, best["_shape"])
        best.pop("_shape")
        return best

    def clearance_sweep(self, a, b, poses: list[dict]) -> dict:
        """Clearance between a and b (b=None: any other component) over a list of
        {component id: Pose} frames (e.g. from motion()). Returns the closest frame:
        {"distance", "frame", "b", "point_a", "point_b", "overlap", "distances": per frame}.
        The components are put back where they were."""
        saved = {c.id: c.pose.copy() for c in self.components}
        per_frame, best = [], None
        try:
            for i, fr in enumerate(poses):
                for c in self.components:
                    if c.id in fr:
                        c.pose = fr[c.id].copy()
                r = self.clearance(a, b)
                per_frame.append(r["distance"])
                if best is None or r["distance"] < best["distance"]:
                    best = {**r, "frame": i}
        finally:
            for c in self.components:
                c.pose = saved[c.id]
        if best is None:
            raise AssemblyError("No frames to check")
        best["distances"] = per_frame
        return best

    # ---- driving mates (motion) ------------------------------------------------------------
    def drive(self, values: dict, max_step_deg: float = 4.0, max_step_mm: float = 2.0) -> SolveResult:
        """Set distance [mm] / angle [rad] mate values {mate id: value} and re-solve, walking
        there in small steps so the mechanism stays on its branch (like dragging a driving
        dimension). On failure every pose and value is restored."""
        mates = {mid: self.mate(mid) for mid in values}
        for mid, m in mates.items():
            if m is None:
                raise AssemblyError(f"No mate {mid!r}")
            if m.type not in ("distance", "angle"):
                raise AssemblyError(f"{m.name} is a {m.type} mate - only distance and angle mates can be driven")
        saved_poses = {c.id: c.pose.copy() for c in self.components}
        start = {mid: m.value for mid, m in mates.items()}
        n = 1
        for mid, m in mates.items():
            step = math.radians(max_step_deg) if m.type == "angle" else max_step_mm
            n = max(n, int(math.ceil(abs(float(values[mid]) - start[mid]) / max(step, 1e-9))))
        res = SolveResult(True, 0.0, 0)
        for k in range(1, n + 1):
            for mid, m in mates.items():
                m.value = start[mid] + (float(values[mid]) - start[mid]) * k / n
            res = self.solve()
            if not res.ok:
                for mid, m in mates.items():
                    m.value = start[mid]
                for c in self.components:
                    c.pose = saved_poses[c.id]
                return res
        return res

    def motion(self, keyframes: list[dict], frames: int = 12) -> list[dict]:
        """Poses along a motion: keyframes are {mate id: value} dicts, visited in order with
        `frames` interpolated steps between two keyframes (a SolidWorks-style motion study
        driven by mates). Returns one {component id: Pose} per frame (including the first
        keyframe). The assembly is left at the last keyframe."""
        if not keyframes:
            return []
        res = self.drive(keyframes[0])
        if not res.ok:
            raise AssemblyError("The first keyframe can't be reached")
        out = [{c.id: c.pose.copy() for c in self.components}]
        for i, (a, b) in enumerate(zip(keyframes, keyframes[1:]), start=2):
            a = {**{mid: self.mate(mid).value for mid in b}, **a}
            for f in range(1, frames + 1):
                t = f / frames
                t = 0.5 - 0.5 * math.cos(math.pi * t)          # ease in/out
                target = {mid: a[mid] + (b[mid] - a[mid]) * t for mid in b}
                res = self.drive(target)
                if not res.ok:
                    raise AssemblyError(f"Keyframe {i} can't be reached from keyframe {i - 1} "
                                        "(the mechanism locks or hits a singular position)")
                out.append({c.id: c.pose.copy() for c in self.components})
        return out

    # ---- dragging -------------------------------------------------------------------------
    def drag_to(self, cid, local_point, target, allow_rotation=True) -> SolveResult:
        """Move component cid so its local point follows target, respecting the mates.
        allow_rotation=False keeps the dragged component's orientation (pure translation,
        like a left-drag in the GUI); other components still move as their mates need."""
        comp = self.component(cid)
        if comp is None or comp.fixed:
            return SolveResult(False, 0.0, 0)
        lp, tg = np.asarray(local_point, float), np.asarray(target, float)
        solver = MateSolver(self, lock_rotation=() if allow_rotation else (cid,))
        return solver.solve(drag=lambda poses: poses[cid].point(lp) - tg)

    def rotate_drag(self, cid, pivot_local, R_target) -> SolveResult:
        comp = self.component(cid)
        if comp is None or comp.fixed:
            return SolveResult(False, 0.0, 0)
        pl = np.asarray(pivot_local, float)
        pivot_world = comp.pose.point(pl)
        Rt = np.asarray(R_target, float)
        scale = max(self.size(), 1.0)

        def pull(poses):
            p = poses[cid]
            return list((p.R - Rt).ravel() * scale * 0.2) + list(p.point(pl) - pivot_world)
        return MateSolver(self).solve(drag=pull)

    # ---- files ------------------------------------------------------------------------------
    def reload(self):
        for c in self.components:
            c.load(self.base_dir)
        return self.solve()

    def to_dict(self):
        return {"format": FILE_FORMAT, "version": FILE_VERSION, "units": "mm",
                "components": [c.to_dict() for c in self.components],
                "mates": [m.to_dict() for m in self.mates]}

    @classmethod
    def from_dict(cls, d, base_dir=None) -> "Assembly":
        if d.get("format") != FILE_FORMAT:
            raise ValueError("Not a BauyrCAD assembly file")
        asm = cls()
        asm.components = [Component.from_dict(c) for c in d.get("components", [])]
        asm.mates = [Mate.from_dict(m) for m in d.get("mates", [])]
        for c in asm.components:
            c.load(Path(base_dir) if base_dir else None)
        return asm

    def save(self, path):
        path = Path(path)
        old_dir = self.base_dir
        for c in self.components:          # keep sources pointing at the same files
            src = c.resolve_path(old_dir)
            try:
                c.source = str(src.resolve().relative_to(path.parent.resolve()))
            except ValueError:
                c.source = str(src.resolve())
        self.path = path
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "Assembly":
        path = Path(path)
        asm = cls.from_dict(json.loads(path.read_text(encoding="utf-8")), path.parent)
        asm.path = path
        return asm

    def world_shape(self):
        shapes = [c.world_shape() for c in self.components if c.shape is not None]
        if not shapes:
            raise AssemblyError("The assembly is empty")
        return geometry.compound(shapes)

    def export(self, path, fmt=None):
        """STEP and Parasolid (.x_t) keep the assembly structure (one part per part file,
        placed instances, names, colours), and so does a SolidWorks .SLDASM (written with
        its .SLDPRT parts next to it, components fixed / floating as here); the other
        formats get the whole assembly as one compound."""
        from .exchange import FORMATS, SLDASM, export, export_format_for, write_step_assembly
        name = self.path.stem if self.path else "Assembly"
        if fmt == SLDASM.key or (fmt is None and Path(path).suffix.lower() in SLDASM.extensions):
            f = SLDASM
        else:
            f = FORMATS[fmt] if fmt else export_format_for(path)
        if not f.key.startswith("step") and f.key not in ("xt", "sldasm"):
            export(self.world_shape(), path, f.key, name=name)
            return
        parts, index, instances = [], {}, []
        for c in self.components:
            if c.shape is None:
                continue
            key = str(c.resolve_path(self.base_dir)).lower()
            if key not in index:
                index[key] = len(parts)
                parts.append((Path(c.source).stem, c.shape, c.color))
            instances.append((index[key], c.name, c.pose.matrix, c.fixed))
        if f.key == "sldasm":             # SolidWorks: the parts once, next to the assembly
            from . import parasolid, swfile
            from .exchange import ExchangeError
            try:
                swfile.write_assembly(parts, instances, path, name=name)
            except (swfile.SwFileError, parasolid.XtError) as e:
                raise ExchangeError(f"SolidWorks assembly (.SLDASM) export: {e}") from e
            return
        instances = [(k, n, m) for k, n, m, _fixed in instances]
        if f.key == "xt":                 # Parasolid: parts once, placed, named, coloured
            from .exchange import write_xt_assembly
            write_xt_assembly(path, parts, [(k, n, m) for k, n, m in instances], name=name)
            return
        write_step_assembly(path, parts, instances, name=name, fmt=f.key)
