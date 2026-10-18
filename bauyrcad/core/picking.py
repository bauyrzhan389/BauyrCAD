"""Display-mesh ray queries, independent of Qt, VTK and the geometry kernel."""
import numpy as np


def ray_faces(points, triangles, face_ids, start, end):
    """All intersected faces on a segment, nearest first, one hit per face.

    Both sides of triangles participate, so occluded/back faces can be selected.
    Returns (face id, segment fraction, world point). No persistent references are
    constructed from the display mesh; callers map the face id back to the model.
    """
    tri = np.asarray(points, float)[np.asarray(triangles, int)]
    if not len(tri):
        return []
    start, end = np.asarray(start, float), np.asarray(end, float)
    direction = end - start
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    h = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, h)
    scale = np.linalg.norm(e1, axis=1) * np.linalg.norm(h, axis=1)
    valid = np.abs(det) > 1e-12 * np.maximum(scale, 1e-30)
    inv = np.zeros_like(det)
    np.divide(1.0, det, out=inv, where=valid)
    s = start - tri[:, 0]
    u = inv * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    v = inv * (q @ direction)
    t = inv * np.einsum("ij,ij->i", e2, q)
    valid &= (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t >= 0) & (t <= 1)
    seen, hits = set(), []
    for i in np.flatnonzero(valid)[np.argsort(t[valid], kind="stable")]:
        face = int(face_ids[i])
        if face not in seen:
            seen.add(face)
            hits.append((face, float(t[i]), start + t[i] * direction))
    return hits
