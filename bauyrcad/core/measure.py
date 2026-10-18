"""Measure (SolidWorks-style): sizes of selected faces, edges and vertices, and the distance,
centre distance and angle between two of them. Pure data in, dict + text out, so the GUI,
the scripting API and tests share it."""

from __future__ import annotations

import math

import numpy as np

from . import geometry


def entity(shape, kind: str, index: int, faces=None) -> dict:
    """Description of one selected entity: kind, sizes, and its 'centre' / axis if any."""
    if kind == "vertex":
        p = geometry.vertex_point(shape, index)
        return {"ref": ("vertex", index), "kind": "vertex", "point": p, "center": p}
    if kind == "edge":
        e = geometry.edge_info(shape, index)
        e["ref"] = ("edge", index)
        return e
    faces = faces if faces is not None else geometry.face_infos(shape)
    f = faces[index]
    out = {"ref": ("face", index), "kind": f.kind, "area": f.area, "centroid": f.centroid}
    if f.kind == "plane":
        out.update(normal=f.normal)
    if f.radius is not None:
        out["radius"] = f.radius
    if f.axis is not None:
        out.update(axis=f.axis, axis_point=f.axis_point)
    if f.kind == "sphere":
        out["center"] = f.centroid
    return out


def _direction(e):
    """Line direction (edges), plane normal (faces), axis (cylinders): (vector, is_normal)."""
    if e["kind"] == "line":
        return e["direction"], False
    if e["kind"] == "plane":
        return e["normal"], True
    if e.get("axis") is not None:
        return e["axis"], False
    return None, False


def _center(e):
    """Point used for the centre distance: a vertex, a circle / sphere centre."""
    return e.get("center")


def measure(shape, refs) -> dict:
    """refs: [(kind, index)] with kind "face" | "edge" | "vertex". Returns
    {"items": [...], and for two items "distance", "point_a", "point_b", "delta" (dx dy dz),
    "center_distance", "axis_distance", "normal_distance", "angle" where they apply; for
    several edges / faces "total_length" / "total_area"}."""
    faces = geometry.face_infos(shape) if any(k == "face" for k, _ in refs) else None
    items = [entity(shape, k, i, faces) for k, i in refs]
    out = {"items": items}
    edges = [e for e in items if "length" in e]
    areas = [e for e in items if "area" in e]
    if len(edges) > 1:
        out["total_length"] = sum(e["length"] for e in edges)
    if len(areas) > 1:
        out["total_area"] = sum(e["area"] for e in areas)
    if len(items) != 2:
        return out
    a, b = items
    sa = geometry.subshape(shape, *a["ref"])
    sb = geometry.subshape(shape, *b["ref"])
    d, pa, pb = geometry.min_distance(sa, sb)
    out.update(distance=d, point_a=pa, point_b=pb, delta=pb - pa)
    ca, cb = _center(a), _center(b)
    if ca is not None and cb is not None and not (a["kind"] == b["kind"] == "vertex"):
        out["center_distance"] = float(np.linalg.norm(cb - ca))
        out["center_delta"] = cb - ca
    va, na = _direction(a)
    vb, nb = _direction(b)
    if va is not None and vb is not None:
        c = abs(float(np.clip(va @ vb, -1.0, 1.0)))
        ang = math.degrees(math.acos(c))
        if na != nb:                              # a plane and a line / axis
            ang = 90.0 - ang
        out["angle"] = ang
        if ang < 1e-6:                            # parallel
            if na and nb:                          # two parallel planes
                pa_ = a["centroid"]
                out["normal_distance"] = abs(float((b["centroid"] - pa_) @ va))
            elif not na and not nb and (a.get("axis_point") is not None or a.get("center") is not None):
                p = a.get("axis_point") if a.get("axis_point") is not None else a.get("center")
                q = b.get("axis_point") if b.get("axis_point") is not None else b.get("center")
                if p is not None and q is not None:
                    w = q - p
                    out["axis_distance"] = float(np.linalg.norm(w - va * (w @ va)))
    return out


def _fmt(v, unit="mm"):
    return f"{v:.4g} {unit}" if abs(v) >= 1e-3 or v == 0 else f"{v:.3e} {unit}"


def _xyz(p):
    return "(" + ", ".join(f"{c:.4g}" for c in p) + ")"


def describe(item) -> str:
    k = item["kind"]
    if k == "vertex":
        return f"Vertex {_xyz(item['point'])} mm"
    if k in ("line", "curve"):
        return f"Edge ({k}): length {_fmt(item['length'])}"
    if k in ("circle", "arc"):
        r = item["radius"]
        return (f"Edge ({k}): ⌀{2 * r:.4g} mm (R{r:.4g}), length {_fmt(item['length'])}, "
                f"centre {_xyz(item['center'])}")
    s = f"Face ({k}): area {_fmt(item['area'], 'mm²')}"
    if item.get("radius") is not None and k in ("cylinder", "sphere", "torus"):
        s += f", ⌀{2 * item['radius']:.4g} mm"
    if k == "plane":
        s += f", normal {_xyz(np.round(item['normal'], 4))}"
    return s


def report(result) -> list[str]:
    """Human-readable lines (what the Measure window shows)."""
    lines = [describe(e) for e in result["items"]]
    if "distance" in result:
        dx, dy, dz = result["delta"]
        lines.append(f"Distance {_fmt(result['distance'])}   dX {dx:.4g}  dY {dy:.4g}  dZ {dz:.4g}")
    if "center_distance" in result:
        dx, dy, dz = result["center_delta"]
        lines.append(f"Centre distance {_fmt(result['center_distance'])}   dX {dx:.4g}  dY {dy:.4g}  "
                     f"dZ {dz:.4g}")
    if "normal_distance" in result:
        lines.append(f"Normal distance {_fmt(result['normal_distance'])} (parallel planes)")
    if "axis_distance" in result:
        lines.append(f"Axis distance {_fmt(result['axis_distance'])} (parallel axes)")
    if "angle" in result:
        lines.append(f"Angle {result['angle']:.4g}°")
    if "total_length" in result:
        lines.append(f"Total length {_fmt(result['total_length'])}")
    if "total_area" in result:
        lines.append(f"Total area {_fmt(result['total_area'], 'mm²')}")
    return lines
