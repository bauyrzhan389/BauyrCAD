"""Sketch planes: a local 2D coordinate system (u, v) embedded in 3D space."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError("Zero-length vector")
    return v / n


@dataclass
class SketchPlane:
    """Plane with origin, unit normal and in-plane x direction.

    The local y direction is ``normal x xdir`` so that (xdir, ydir, normal)
    is a right-handed frame.
    """

    origin: np.ndarray
    normal: np.ndarray
    xdir: np.ndarray
    name: str = ""

    def __post_init__(self):
        self.origin = np.asarray(self.origin, dtype=float)
        self.normal = _unit(self.normal)
        x = np.asarray(self.xdir, dtype=float)
        # Make xdir exactly orthogonal to the normal.
        x = x - np.dot(x, self.normal) * self.normal
        self.xdir = _unit(x)

    @property
    def ydir(self) -> np.ndarray:
        return np.cross(self.normal, self.xdir)

    def to_world(self, u: float, v: float) -> np.ndarray:
        return self.origin + u * self.xdir + v * self.ydir

    def to_world_many(self, uv) -> np.ndarray:
        uv = np.asarray(uv, dtype=float).reshape(-1, 2)
        return self.origin + uv[:, :1] * self.xdir + uv[:, 1:2] * self.ydir

    def to_local(self, p) -> tuple[float, float]:
        d = np.asarray(p, dtype=float) - self.origin
        return float(np.dot(d, self.xdir)), float(np.dot(d, self.ydir))

    def intersect_ray(self, p0, p1) -> np.ndarray | None:
        """Intersection of the line through p0, p1 with this plane."""
        p0 = np.asarray(p0, dtype=float)
        d = np.asarray(p1, dtype=float) - p0
        denom = np.dot(d, self.normal)
        if abs(denom) < 1e-12:
            return None
        t = np.dot(self.origin - p0, self.normal) / denom
        return p0 + t * d

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "origin": self.origin.tolist(),
            "normal": self.normal.tolist(),
            "xdir": self.xdir.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SketchPlane":
        return cls(d["origin"], d["normal"], d["xdir"], d.get("name", ""))

    @classmethod
    def from_face(cls, origin, normal) -> "SketchPlane":
        """Plane on a model face; picks a stable in-plane x direction."""
        n = _unit(normal)
        # Prefer global X, then global Z, projected into the plane.
        for cand in (np.array([1.0, 0, 0]), np.array([0, 0, -1.0])):
            if abs(np.dot(cand, n)) < 0.9:
                return cls(origin, n, cand, "Face")
        return cls(origin, n, np.array([0, 1.0, 0]), "Face")


# Standard reference planes (SolidWorks convention, Y is up).
FRONT = SketchPlane([0, 0, 0], [0, 0, 1], [1, 0, 0], "Front Plane")
TOP = SketchPlane([0, 0, 0], [0, 1, 0], [1, 0, 0], "Top Plane")
RIGHT = SketchPlane([0, 0, 0], [1, 0, 0], [0, 0, -1], "Right Plane")
REFERENCE_PLANES = {p.name: p for p in (FRONT, TOP, RIGHT)}
