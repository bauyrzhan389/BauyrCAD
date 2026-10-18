"""Display symbols for loads (arrows, points, faces) shared by the GUI and the renderer."""

from __future__ import annotations

import numpy as np

from .study import FaceForce, PointForce, Pressure


def _outward(face):
    """Outward direction at a face's centroid (planar faces, or partial cylinders)."""
    if face.normal is not None:
        return np.asarray(face.normal, float)
    if face.kind == "cylinder" and face.axis is not None:
        a = np.asarray(face.axis, float)
        v = face.centroid - face.axis_point
        v = v - np.dot(v, a) * a
        if np.linalg.norm(v) > 1e-6 * max(face.radius or 1, 1):
            return v / np.linalg.norm(v)
    return None


def load_symbols(load, faces, length):
    """-> (arrows [(tip, direction, length)], points [xyz], loaded face indices)."""
    arrows, points, face_ids = [], [], []
    if isinstance(load, PointForce):
        arrows.append((np.asarray(load.point, float), np.asarray(load.force, float), length))
        points.append(np.asarray(load.point, float))
        return arrows, points, face_ids
    for ref in load.faces:
        i = ref.resolve(faces)
        if i is None:
            continue
        f = faces[i]
        face_ids.append(i)
        if isinstance(load, FaceForce):
            arrows.append((f.centroid, np.asarray(load.force, float), length))
        elif isinstance(load, Pressure):
            n = _outward(f)
            if n is not None:
                d = -n if load.pressure >= 0 else n
                # arrows touch the face from outside, pointing in (for positive pressure)
                tip = f.centroid if load.pressure >= 0 else f.centroid + n * length
                arrows.append((tip, d, length))
    return arrows, points, face_ids


def describe(load) -> str:
    if isinstance(load, Pressure):
        return f"{load.name}  ({load.pressure:.4g} MPa, {len(load.faces)} face{'s' if len(load.faces) != 1 else ''})"
    if isinstance(load, FaceForce):
        return f"{load.name}  ({load.magnitude:.4g} N on {len(load.faces)} face{'s' if len(load.faces) != 1 else ''})"
    return f"{load.name}  ({load.magnitude:.4g} N)"
