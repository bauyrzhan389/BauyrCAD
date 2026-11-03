"""Measure (SolidWorks-style): sizes of selected faces, edges and vertices, and the distance,
centre distance and angle between two of them. Pure data in, dict + text out, so the GUI,
the scripting API and tests share it."""

from __future__ import annotations

import math

import numpy as np

from . import geometry


def entity(shape, kind: str, index: int, faces=None) -> dict:
    """Description of one selected entity: kind, sizes, and its 'centre' / axis if any."""
    if kind == 'point':
        geometry.point_shape(index)  # validates coordinates using the shared geometry boundary
        p = np.asarray(index, float)
        return dict(ref=('point', tuple(p)), kind='point', point=p, center=p)
    if kind not in ('face', 'edge', 'vertex'):
        raise ValueError(f'Unknown measurement entity: {kind}')
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
        out["center"] = f.center
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


def measure(shape, refs, *, mode='minimum') -> dict:
    """refs: [(kind, index)] with kind "face" | "edge" | "vertex". Returns
    {"items": [...], and for two items "distance", "point_a", "point_b", "delta" (dx dy dz),
    "center_distance", "axis_distance", "normal_distance", "angle" where they apply; for
    several edges / faces "total_length" / "total_area"}."""
    if mode not in ('minimum', 'center', 'maximum'):
        raise ValueError('Distance mode must be minimum, center or maximum')
    refs = list(refs)
    faces = geometry.face_infos(shape) if any(k == "face" for k, _ in refs) else None
    items = [entity(shape, k, i, faces) for k, i in refs]
    out = {"items": items}
    edges = [e for e in items if "length" in e]
    areas = [e for e in items if "area" in e]
    if len(edges) > 1:
        out["total_length"] = sum(e["length"] for e in edges)
    if len(areas) > 1:
        out["total_area"] = sum(e["area"] for e in areas)
    if len(items) == 3 and all(e['kind'] in ('point', 'vertex') for e in items):
        a, b, c = [e['point'] for e in items]
        u, v = a-b, c-b
        scale = np.linalg.norm(u) * np.linalg.norm(v)
        if scale > 1e-15:
            out['three_point_angle'] = math.degrees(math.acos(float(np.clip(u @ v / scale, -1, 1))))
    if len(items) != 2:
        return out
    a, b = items
    def sub(item):
        return geometry.point_shape(item['point']) if item['kind'] == 'point' else geometry.subshape(shape, *item['ref'])
    sa, sb = sub(a), sub(b)
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
    qa, qb = pa, pb
    if mode == 'center':
        if ca is not None and cb is not None:
            qa, qb = ca, cb
        elif a.get('axis_point') is not None and b.get('axis_point') is not None:
            # Closest points on the two infinite axes, also valid for skew cylinders.
            p, q = a['axis_point'], b['axis_point']
            u, v = a['axis'], b['axis']
            t = np.linalg.lstsq(np.column_stack((u, -v)), q-p, rcond=None)[0]
            qa, qb = p+t[0]*u, q+t[1]*v
        else:
            raise ValueError('Center distance needs two vertices, circular edges, spheres or cylindrical axes. Use Minimum distance for these selections.')
    elif mode == 'maximum':
        qa, qb = _maximum_points(a, b)
    out.update(mode=mode, measurement=float(np.linalg.norm(qb-qa)),
               measurement_point_a=np.asarray(qa), measurement_point_b=np.asarray(qb),
               measurement_delta=np.asarray(qb)-qa)
    return out


def _maximum_points(a, b):
    """Exact farthest pair for points and full circles in parallel planes.

    Trimmed arcs and arbitrary surfaces are deliberately not approximated by vertices.
    """
    allowed = ('point', 'vertex', 'circle')
    if a['kind'] not in allowed or b['kind'] not in allowed:
        raise ValueError('Maximum distance currently supports points and full circular edges; use Minimum for arcs and faces.')
    ca, cb = np.asarray(a['center']), np.asarray(b['center'])
    circles = [e for e in (a, b) if e['kind'] == 'circle']
    if not circles:
        return ca, cb
    n = np.asarray(circles[0]['axis'])
    if len(circles) == 2 and abs(float(n @ circles[1]['axis'])) < 1-1e-9:
        raise ValueError('Maximum distance requires circular edges in parallel planes.')
    u = cb-ca; u = u-n*(u@n)
    if np.linalg.norm(u) < 1e-12:
        u = np.cross(n, np.eye(3)[int(np.argmin(np.abs(n)))])
    u /= np.linalg.norm(u)
    return ca-u*a.get('radius', 0), cb+u*b.get('radius', 0)


def model_dimensions(shape):
    lo, hi = geometry.measurement_bounds(shape)
    return dict(minimum=lo, maximum=hi, size=hi-lo, diagonal=float(np.linalg.norm(hi-lo)))


def _fmt(v, unit="mm"):
    return f"{v:.4g} {unit}" if abs(v) >= 1e-3 or v == 0 else f"{v:.3e} {unit}"


def _xyz(p):
    return "(" + ", ".join(f"{c:.4g}" for c in p) + ")"


def describe(item) -> str:
    k = item["kind"]
    if k in ('vertex', 'point'):
        return f"{k.title()} {_xyz(item['point'])} mm"
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


def report(result, *, unit='mm', decimals=4, show_xyz=True) -> list[str]:
    """Human-readable lines (what the Measure window shows)."""
    if unit not in UNIT_FACTORS or not 0 <= decimals <= 8:
        raise ValueError('Choose mm, cm, m or in and 0–8 decimal places')
    scale = UNIT_FACTORS[unit]
    number = lambda v: f'{float(v):.{decimals}f}'
    length = lambda v: number(v*scale) + ' ' + unit
    xyz = lambda p: '(' + ', '.join(number(v*scale) for v in p) + ') ' + unit
    delta = lambda p: ('   ' + '  '.join(f'd{axis} {length(v)}' for axis, v in zip('XYZ', p))) if show_xyz else ''
    lines = []
    for item in result['items']:
        kind = item['kind']
        if kind in ('point', 'vertex'):
            line = f'{kind.title()} {xyz(item["point"])}'
        elif 'length' in item:
            line = f'Edge ({kind}): length {length(item["length"])}'
        else:
            line = f'Face ({kind}): area {number(item["area"]*scale**2)} {unit}²'
        if 'radius' in item:
            line += f', ⌀{length(2*item["radius"])} (R{length(item["radius"])})'
        if 'center' in item and kind not in ('point', 'vertex'):
            line += f', centre {xyz(item["center"])}'
        lines.append(line)
    if result.get('mode') in ('center', 'maximum'):
        label = 'Centre' if result['mode'] == 'center' else 'Maximum'
        lines.append(f'{label} distance {length(result["measurement"])}' + delta(result['measurement_delta']))
    if "distance" in result:
        lines.append(f"Minimum distance {length(result['distance'])}" + delta(result['delta']))
    if "center_distance" in result and result.get('mode') != 'center':
        lines.append(f"Centre distance {length(result['center_distance'])}" + delta(result['center_delta']))
    if "normal_distance" in result:
        lines.append(f"Normal distance {length(result['normal_distance'])} (parallel planes)")
    if "axis_distance" in result:
        lines.append(f"Axis distance {length(result['axis_distance'])} (parallel axes)")
    if "angle" in result:
        lines.append(f"Angle {number(result['angle'])}°")
    if 'three_point_angle' in result:
        lines.append(f"Angle at second point {number(result['three_point_angle'])}°")
    if "total_length" in result:
        lines.append(f"Total length {length(result['total_length'])}")
    if "total_area" in result:
        lines.append(f"Total area {number(result['total_area']*scale**2)} {unit}²")
    return lines


UNIT_FACTORS = {'mm': 1.0, 'cm': 0.1, 'm': 0.001, 'in': 1/25.4}
