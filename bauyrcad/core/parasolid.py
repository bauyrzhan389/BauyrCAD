"""Write Parasolid transmit files (.x_t, text) without Parasolid.

Parasolid is the kernel SolidWorks, NX, Solid Edge and Onshape run on, so an XT file opens
in them as an exact solid. Pure Python + NumPy, no OCC: :mod:`bauyrcad.core.xt_brep` turns
an OpenCASCADE shape into the neutral model below (metres), this module checks it and
writes the nodes. Format: the public *Parasolid XT Format Reference* (schema 12006, which
"applications writing XT files must use") and the field layouts of the schema files
Parasolid ships (``sch_12006``); conventions checked against what Parasolid itself wrote
for BauyrCAD models (the partitions inside ``tests/data/sw/*.SLDPRT``). Notes:
docs/SLDPRT_FORMAT.md, section "Writing Parasolid XT".

The model is Parasolid's, not OpenCASCADE's: no seam or degenerate edges (a full cylinder
face is bounded by vertex-less ring edges), every edge of a solid has exactly two fins of
opposite sense, a fin's vertex is the one at its forward end, an edge runs along its curve
(curve sense '+'), and each face's sense says whether its outward normal follows the
surface's natural normal.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

import numpy as np

SCHEMA_KEY = "SCH_1200000_12006"
MODELLER = ": TRANSMIT FILE created by modeller version 1200000"
SIZE_BOX = 1000.0            # m; the model must lie inside +-500 m
LINEAR_RES = 1e-8            # m; edges and vertices within it are accurate

# Transmitted fields of schema 12006, in file order (name:kind). Kinds: d int, u/n small
# int, f double (null '?'), v vector (null '?'), c char, l logical, p node index (0 = null);
# a last field "x:f[]" / "x:n[]" makes a variable-length node (its length is written first);
# "x:f9" / "x:u8" / "x:l14" are fixed arrays; "x:c[]" a string.
_GEOM = "node_id:d attributes_features:p owner:p next:p previous:p geometric_owner:p sense:c"
NODE_FIELDS = {
    12: ("BODY", "highest_node_id:d attributes_features:p attribute_chains:p surface:p curve:p "
                 "point:p key:p res_size:f res_linear:f ref_instance:p next:p previous:p "
                 "state:u owner:p body_type:u nom_geom_state:u shell:p boundary_surface:p "
                 "boundary_curve:p boundary_point:p region:p edge:p vertex:p"),
    13: ("SHELL", "node_id:d attributes_features:p body:p next:p face:p edge:p vertex:p "
                  "region:p front_face:p"),
    14: ("FACE", "node_id:d attributes_features:p tolerance:f next:p previous:p loop:p "
                 "shell:p surface:p sense:c next_on_surface:p previous_on_surface:p "
                 "next_front:p previous_front:p front_shell:p"),
    15: ("LOOP", "node_id:d attributes_features:p halfedge:p face:p next:p"),
    16: ("EDGE", "node_id:d attributes_features:p tolerance:f halfedge:p previous:p next:p "
                 "curve:p next_on_curve:p previous_on_curve:p owner:p"),
    17: ("HALFEDGE", "attributes_features:p loop:p forward:p backward:p vertex:p other:p "
                     "edge:p curve:p next_at_vx:p sense:c"),
    18: ("VERTEX", "node_id:d attributes_features:p halfedge:p previous:p next:p point:p "
                   "tolerance:f owner:p"),
    19: ("REGION", "node_id:d attributes_features:p body:p next:p previous:p shell:p type:c"),
    29: ("POINT", "node_id:d attributes_features:p owner:p next:p previous:p pvec:v"),
    30: ("LINE", _GEOM + " pvec:v direction:v"),
    31: ("CIRCLE", _GEOM + " centre:v normal:v x_axis:v radius:f"),
    32: ("ELLIPSE", _GEOM + " centre:v normal:v x_axis:v major_radius:f minor_radius:f"),
    50: ("PLANE", _GEOM + " pvec:v normal:v x_axis:v"),
    51: ("CYLINDER", _GEOM + " pvec:v axis:v radius:f x_axis:v"),
    52: ("CONE", _GEOM + " pvec:v axis:v radius:f sin_half_angle:f cos_half_angle:f x_axis:v"),
    53: ("SPHERE", _GEOM + " centre:v radius:f axis:v x_axis:v"),
    54: ("TORUS", _GEOM + " centre:v axis:v major_radius:f minor_radius:f x_axis:v"),
    45: ("BSPLINE_VERTICES", "vertices:f[]"),
    124: ("B_SURFACE", _GEOM + " nurbs:p data:p"),
    126: ("NURBS_SURF", "u_periodic:l v_periodic:l u_degree:n v_degree:n n_u_vertices:d "
                        "n_v_vertices:d u_knot_type:u v_knot_type:u n_u_knots:d n_v_knots:d "
                        "rational:l u_closed:l v_closed:l surface_form:u vertex_dim:n "
                        "bspline_vertices:p u_knot_mult:p v_knot_mult:p u_knots:p v_knots:p"),
    127: ("KNOT_MULT", "mult:n[]"),
    128: ("KNOT_SET", "knots:f[]"),
    134: ("B_CURVE", _GEOM + " nurbs:p data:p"),
    136: ("NURBS_CURVE", "degree:n n_vertices:d vertex_dim:n n_knots:d knot_type:u periodic:l "
                         "closed:l rational:l curve_form:u bspline_vertices:p knot_mult:p knots:p"),
    133: ("TRIMMED_CURVE", _GEOM + " basis_curve:p point_1:v point_2:v parm_1:f parm_2:f"),
    137: ("SP_CURVE", _GEOM + " surface:p b_curve:p original:p tolerance_to_original:f"),
    141: ("GEOMETRIC_OWNER", "owner:p next:p previous:p shared_geometry:p"),
    10: ("ASSEMBLY", "highest_node_id:d attributes_features:p attribute_chains:p list:p surface:p "
                     "curve:p point:p key:p res_size:f res_linear:f ref_instance:p next:p "
                     "previous:p state:u owner:p type:u sub_instance:p"),
    11: ("INSTANCE", "node_id:d attributes_features:p type:u part:p transform:p assembly:p "
                     "next_in_part:p prev_in_part:p next_of_part:p prev_of_part:p"),
    100: ("TRANSFORM", "node_id:d owner:p next:p previous:p rotation_matrix:f9 "
                       "translation_vector:v scale:f flag:d perspective_vector:v"),
    70: ("LIST", "node_id:d owner:p next:p previous:p list_type:d list_length:d block_length:d "
                 "size_of_entry:d list_block:p finger_block:p finger_index:d notransmit:l"),
    74: ("POINTER_LIS_BLOCK", "n_entries:d next_block:p entries:p[]"),
    79: ("ATT_DEF_ID", "string:c[]"),
    80: ("ATTRIB_DEF", "next:p identifier:p type_id:d actions:u8 legal_owners:l14 fields:u[]"),
    81: ("ATTRIBUTE", "node_id:d definition:p owner:p next:p previous:p next_of_type:p "
                      "previous_of_type:p fields:p[]"),
    82: ("INT_VALUES", "values:d[]"),
    83: ("REAL_VALUES", "values:f[]"),
    84: ("CHAR_VALUES", "values:c[]"),
}
# system attribute definitions, as SolidWorks writes them: (type_id, identifier, actions on
# rotate, scale, translate, reflect, split, merge, transfer, change, legal owners in the
# order assembly, instance, body, shell, face, loop, edge, vertex, feature, surface, curve,
# point, region, fin, field types: 2 real, 3 char)
ATTRIBUTES = {
    "name": (8017, "SDL/TYSA_NAME", [0] * 8, [True] * 14, [3]),
    "colour": (8001, "SDL/TYSA_COLOUR", [0, 0, 0, 0, 3, 5, 0, 0],
               [False] * 4 + [True, False, True] + [False] * 7, [2]),
}
FIELDS = {t: (name, [tuple(f.split(":")) for f in spec.split()])
          for t, (name, spec) in NODE_FIELDS.items()}
TYPE_OF = {name: t for t, (name, _) in FIELDS.items()}

CURVE_KINDS = {"line": 30, "circle": 31, "ellipse": 32, "bcurve": 134}
SURFACE_KINDS = {"plane": 50, "cylinder": 51, "cone": 52, "sphere": 53, "torus": 54,
                 "bsurface": 124}
_UNSET = 1          # knot_type / curve_form / surface_form: not known (Parasolid works it out)


class XtError(ValueError):
    """The model cannot be written as a valid XT file (the message says why)."""


# ---- neutral model (metres) --------------------------------------------------------------
@dataclass
class Geom:
    """A curve or surface: ``kind`` (line, circle, ellipse / plane, cylinder, cone, sphere,
    torus) and its XT fields (``pvec``, ``direction``, ``centre``, ``axis``, ...); or a NURBS:
    ``bcurve`` {degree, knots, mults (distinct knots + multiplicities), poles (n x 3), weights
    (n, or None), periodic, closed} / ``bsurface`` {u_degree, v_degree, u_knots, u_mults,
    v_knots, v_mults, poles (nu x nv x 3, v fastest in the file), weights (nu x nv or None),
    u_periodic, v_periodic, u_closed, v_closed}. A periodic NURBS is in Parasolid's form: the
    knots of its valid range plus one period's worth of imaginary knots, the first poles
    repeated at the end (xt_brep.periodic_form)."""
    kind: str
    data: dict


@dataclass
class Fin:
    edge: int
    sense: bool              # True: the loop runs along the edge
    # on a tolerant edge: its curve on the face, an SP-curve {b: 2D B-curve data (poles n x 2
    # in the surface's parameters), t0, t1: its parameters at the fin's start and end,
    # p0, p1: the 3D points there (m)}, running the fin's way
    sp: dict | None = None


@dataclass
class Isolated:
    """The only member of an isolated loop: a vertex alone in a face (a cone's apex)."""
    vertex: int


@dataclass
class Face:
    surface: Geom
    sense: bool              # True: the outward normal is the surface's natural normal
    loops: list              # [[Fin, ...], ...] each with the face on its left, or [Isolated]
    color: tuple | None = None   # (r, g, b) 0..1: an SDL/TYSA_COLOUR attribute


@dataclass
class Edge:
    curve: Geom | None       # the edge runs along increasing curve parameter (None if tolerant)
    start: int | None        # vertex indices, both None for a ring edge
    end: int | None
    tolerance: float | None = None   # m: a tolerant edge (its fins carry SP-curves)
    # an accurate edge at a tolerant vertex: the piece of its curve {t0, t1, p0, p1} that
    # the edge uses, written as a trimmed curve
    trim: dict | None = None


@dataclass
class Body:
    """One manifold solid body: one or more lumps, each an outer shell and any cavities."""
    vertices: list           # [xyz (m), ...]
    edges: list
    faces: list
    name: str = ""
    # [[outer shell's face indices, cavity shell's face indices, ...], ...] one list per
    # lump (a connected solid); None: one lump bounded by all faces
    shells: list | None = None
    vertex_tol: list | None = None   # m per vertex (None = accurate)
    # more attributes: [(owner, key, fields)], owner "body" or ("face", index); key in
    # ATTRIBUTES or a definition tuple; fields: one entry per field, None (a null pointer)
    # or ("int" | "real", [values]) or ("char", text)
    attributes: list = field(default_factory=list)

    def lumps(self):
        return self.shells or [[list(range(len(self.faces)))]]


@dataclass
class Instance:
    """A placed use of a part: x' = rotation . x + translation (rotation row-major, as
    Parasolid stores it; translation in m)."""
    part: int                         # index into Assembly.parts
    rotation: list = field(default_factory=lambda: [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    translation: tuple = (0.0, 0.0, 0.0)
    name: str = ""


@dataclass
class Assembly:
    """Parts written once, placed by instances (several instances may share a part)."""
    name: str
    parts: list                       # [Body]
    instances: list                   # [Instance]


@dataclass
class _Node:
    type: int
    f: dict = field(default_factory=dict)
    index: int = 0


# ---- checks -------------------------------------------------------------------------------
def fin_vertices(body: Body, fin: Fin):
    """(backward vertex, forward vertex) of a fin: where it starts and ends."""
    e = body.edges[fin.edge]
    return (e.start, e.end) if fin.sense else (e.end, e.start)


def validate(body: Body) -> None:
    """The rules of a Parasolid manifold solid (XT Format Reference, 'Body types'), raised
    as XtError: every edge has exactly two fins of opposite sense, loops close nose to
    tail, ring edges are alone in their loop, vectors are unit, the model lies inside the
    size box, and every vertex is used."""
    uses = [[] for _ in body.edges]
    used_vx = set()
    for fi, face in enumerate(body.faces):
        if face.surface.kind not in SURFACE_KINDS:
            raise XtError(f"face {fi}: surface type {face.surface.kind!r} is not supported")
        for li, loop in enumerate(face.loops):
            if not loop:
                raise XtError(f"face {fi} loop {li} is empty")
            if isinstance(loop[0], Isolated):
                if len(loop) != 1 or not 0 <= loop[0].vertex < len(body.vertices):
                    raise XtError(f"face {fi} loop {li}: a bad isolated vertex")
                used_vx.add(loop[0].vertex)
                continue
            for k, fin in enumerate(loop):
                if not isinstance(fin, Fin) or not 0 <= fin.edge < len(body.edges):
                    raise XtError(f"face {fi}: fin on unknown edge {fin.edge}")
                uses[fin.edge].append(fin.sense)
                a, b = fin_vertices(body, fin)
                if a is None or b is None:
                    if len(loop) != 1 or a is not None or b is not None:
                        raise XtError(f"face {fi} loop {li}: a ring edge must be alone")
                    continue
                used_vx.update((a, b))
                nxt = loop[(k + 1) % len(loop)]
                if fin_vertices(body, nxt)[0] != b:
                    raise XtError(f"face {fi} loop {li}: fins {k} and {k + 1} do not meet")
    for ei, (e, s) in enumerate(zip(body.edges, uses)):
        if sorted(s) != [False, True]:
            raise XtError(f"edge {ei}: {len(s)} fins {s} (a solid needs one of each sense)")
        if (e.start is None) != (e.end is None):
            raise XtError(f"edge {ei}: one vertex only")
        if e.tolerance is None and (e.curve is None or e.curve.kind not in CURVE_KINDS):
            raise XtError(f"edge {ei}: an accurate edge needs a supported curve")
        if e.tolerance is not None and not 0 < e.tolerance < SIZE_BOX:
            raise XtError(f"edge {ei}: bad tolerance {e.tolerance}")
    sp = [(fi, fin) for fi, f in enumerate(body.faces) for loop in f.loops for fin in loop
          if isinstance(fin, Fin)]
    for fi, fin in sp:
        if (body.edges[fin.edge].tolerance is not None) != (fin.sp is not None):
            raise XtError(f"face {fi}: a tolerant edge's fins (and only theirs) need SP-curves")
        if fin.sp is not None:
            _check_nurbs(Geom("bcurve", fin.sp["b"]), SIZE_BOX / 2, dim=2)
    seen = sorted(i for lump in body.lumps() for shell in lump for i in shell)
    if seen != list(range(len(body.faces))):
        raise XtError("the shells must hold every face exactly once")
    if body.vertex_tol is not None and len(body.vertex_tol) != len(body.vertices):
        raise XtError("one vertex tolerance per vertex")
    if used_vx != set(range(len(body.vertices))):
        raise XtError(f"vertices {sorted(set(range(len(body.vertices))) - used_vx)} are unused")
    half = SIZE_BOX / 2
    for g in [f.surface for f in body.faces] + [e.curve for e in body.edges if e.curve]:
        if g.kind in ("bcurve", "bsurface"):
            _check_nurbs(g, half)
            continue
        for k, v in g.data.items():
            if k in ("direction", "normal", "axis", "x_axis") and abs(np.linalg.norm(v) - 1) > 1e-9:
                raise XtError(f"{g.kind}.{k} is not a unit vector: {v}")
            if k in ("pvec", "centre") and np.max(np.abs(v)) > half:
                raise XtError(f"the model is outside the Parasolid size box (+-{half:g} m)")
    if body.vertices and np.max(np.abs(np.asarray(body.vertices, float))) > half:
        raise XtError(f"the model is outside the Parasolid size box (+-{half:g} m)")


def _check_nurbs(g: Geom, half: float, dim: int = 3) -> None:
    d = g.data
    if np.asarray(d["poles"]).shape[-1] != dim:
        raise XtError(f"{g.kind}: vertices must have {dim} coordinates")
    dirs = ("",) if g.kind == "bcurve" else ("u_", "v_")
    poles = np.asarray(d["poles"], float)
    for i, pre in enumerate(dirs):
        deg, knots, mults = d[pre + "degree"], np.asarray(d[pre + "knots"], float), d[pre + "mults"]
        n = poles.shape[i]
        if len(knots) != len(mults) or np.any(np.diff(knots) <= 0) or min(mults) < 1:
            raise XtError(f"{g.kind}: bad knot vector")
        if sum(mults) != n + deg + 1 or max(mults[1:-1], default=1) > deg or deg < 1:
            raise XtError(f"{g.kind}: {n} vertices do not fit degree {deg} and knots {list(mults)}")
    w = d.get("weights")
    if w is not None and (np.asarray(w).shape != poles.shape[:-1] or np.min(w) <= 0):
        raise XtError(f"{g.kind}: weights must be positive, one per vertex")
    if np.max(np.abs(poles)) > half:
        raise XtError(f"the model is outside the Parasolid size box (+-{half:g} m)")


def _nurbs(node, g: Geom):
    """The NURBS_CURVE / NURBS_SURF node of a B-curve / B-surface and its arrays."""
    d = g.data
    poles = np.asarray(d["poles"], float)
    w = d.get("weights")
    if w is not None:                       # rational: x w, y w, z w, w
        w = np.asarray(w, float)[..., None]
        poles = np.concatenate([poles * w, w], axis=-1)
    verts = node("BSPLINE_VERTICES", vertices=poles.ravel().tolist())
    common = dict(rational=w is not None, vertex_dim=poles.shape[-1], bspline_vertices=verts)
    if g.kind == "bcurve":
        return node("NURBS_CURVE", degree=d["degree"], n_vertices=poles.shape[0],
                    n_knots=len(d["knots"]), knot_type=_UNSET, periodic=d["periodic"],
                    closed=d["closed"], curve_form=_UNSET,
                    knot_mult=node("KNOT_MULT", mult=list(d["mults"])),
                    knots=node("KNOT_SET", knots=list(d["knots"])), **common)
    return node("NURBS_SURF", u_periodic=d["u_periodic"], v_periodic=d["v_periodic"],
                u_degree=d["u_degree"], v_degree=d["v_degree"], n_u_vertices=poles.shape[0],
                n_v_vertices=poles.shape[1], u_knot_type=_UNSET, v_knot_type=_UNSET,
                n_u_knots=len(d["u_knots"]), n_v_knots=len(d["v_knots"]),
                u_closed=d["u_closed"], v_closed=d["v_closed"], surface_form=_UNSET,
                u_knot_mult=node("KNOT_MULT", mult=list(d["u_mults"])),
                v_knot_mult=node("KNOT_MULT", mult=list(d["v_mults"])),
                u_knots=node("KNOT_SET", knots=list(d["u_knots"])),
                v_knots=node("KNOT_SET", knots=list(d["v_knots"])), **common)


def _geometry(node, kind, g: Geom, owner):
    """The curve / surface node of an edge / face (B-curves and B-surfaces with their
    NURBS nodes; their auxiliary data node is left null, which the format allows)."""
    if g.kind in ("bcurve", "bsurface"):
        return node(kind[g.kind], owner=owner, sense="+", nurbs=_nurbs(node, g))
    return node(kind[g.kind], owner=owner, sense="+", **g.data)


def natural_normal(g: Geom, p) -> np.ndarray:
    """Unit natural normal (dR/du x dR/dv) of an XT surface at a point on it."""
    p = np.asarray(p, float)
    d = g.data
    if g.kind == "plane":
        n = np.asarray(d["normal"], float)
    elif g.kind in ("cylinder", "cone", "torus"):
        a = np.asarray(d["axis"], float)
        base = np.asarray(d["pvec" if g.kind != "torus" else "centre"], float)
        r = (p - base) - ((p - base) @ a) * a
        er = r / np.linalg.norm(r)
        if g.kind == "cylinder":
            n = er
        elif g.kind == "cone":              # the axis points where the radius grows; the
            n = er - d["sin_half_angle"] / d["cos_half_angle"] * a    # normal is outward
        else:                               # torus: away from the tube's centre circle
            n = p - (base + d["major_radius"] * er)
    elif g.kind == "sphere":
        n = p - np.asarray(d["centre"], float)
    else:
        raise XtError(f"no natural normal for {g.kind}")
    return n / np.linalg.norm(n)


# ---- nodes --------------------------------------------------------------------------------
def _chain(nodes, nxt="next", prev="previous"):
    for a, b in zip(nodes, nodes[1:]):
        a.f[nxt] = b
        if prev:
            b.f[prev] = a


def _text_value(s) -> str:
    """A name as Parasolid characters: printable ASCII, single spaces (two spaces together
    are not allowed in a text file), no backslash (escaped only from version 12.1)."""
    s = "".join(c if 32 <= ord(c) < 127 and c != "\\" else "_" for c in str(s))
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def _as_assembly(model):
    """One Body stays a single part; a list of bodies is a multi-body part, written as
    SolidWorks writes one: an assembly placing each body once, unmoved."""
    if isinstance(model, Body) or isinstance(model, Assembly):
        return model
    bodies = list(model)
    if len(bodies) == 1:
        return bodies[0]
    return Assembly(bodies[0].name.rsplit("-", 1)[0] if bodies[0].name else "",
                    bodies, [Instance(i, name=b.name) for i, b in enumerate(bodies)])


def _nodes(model) -> list:
    """All nodes of a part: one body as the root (node 1); an Assembly as an ASSEMBLY root
    with one INSTANCE (TRANSFORM) per placement and each part's BODY written once."""
    model = _as_assembly(model)
    bodies = [model] if isinstance(model, Body) else model.parts
    for b in bodies:
        validate(b)
    out, rings, defs, owned_attrs, chains = [], {}, {}, {}, {}

    def node(t, **f):
        n = _Node(TYPE_OF[t] if isinstance(t, str) else t, dict(f))
        out.append(n)
        return n

    def owned(dependent, geometry):
        """Register a dependent on the geometry it uses (its ring of GEOMETRIC_OWNERs)."""
        rings.setdefault(id(geometry), (geometry, []))[1].append(
            node("GEOMETRIC_OWNER", owner=dependent, shared_geometry=geometry))

    def attribute(part, owner, key, value):
        """An SDL/TYSA attribute on owner (in part's attribute chains); one ATTRIB_DEF per
        kind in the file."""
        spec = ATTRIBUTES[key] if isinstance(key, str) else key
        if spec[1] not in defs:
            type_id, ident, actions, legal, fields = spec
            defs[spec[1]] = node("ATTRIB_DEF", identifier=node("ATT_DEF_ID", string=ident),
                                 type_id=type_id, actions=actions, legal_owners=legal[:14],
                                 fields=fields)
        if key == "name":
            vals = [node("CHAR_VALUES", values=_text_value(value))]
        elif key == "colour":
            vals = [node("REAL_VALUES", values=[float(x) for x in value])]
        else:                                     # explicit fields
            kinds = {"int": "INT_VALUES", "real": "REAL_VALUES", "char": "CHAR_VALUES"}
            vals = [None if v is None else node(kinds[v[0]], values=v[1]) for v in value]
        a = node("ATTRIBUTE", definition=defs[spec[1]], owner=owner, fields=vals)
        owned_attrs.setdefault(id(owner), (owner, []))[1].append(a)
        chains.setdefault(id(part), (part, {}))[1].setdefault(spec[1], []).append(a)

    if isinstance(model, Body):
        parts = [_body_nodes(node, owned, attribute, model)]
    else:
        asm = node("ASSEMBLY", res_size=SIZE_BOX, res_linear=LINEAR_RES, state=1, type=1)
        if _text_value(model.name):
            attribute(asm, asm, "name", model.name)
        parts = [_body_nodes(node, owned, attribute, b) for b in model.parts]
        insts, of_part = [], {}
        for ins in model.instances:
            inst = node("INSTANCE", type=1, assembly=asm, part=parts[ins.part])
            r = np.asarray(ins.rotation, float)
            tr = np.asarray(ins.translation, float)
            flag = ((1 if np.any(np.abs(tr) > 0) else 0) | (2 if not np.allclose(r, np.eye(3)) else 0)
                    | (8 if np.linalg.det(r) < 0 else 0))
            inst.f["transform"] = node("TRANSFORM", owner=inst, scale=1.0, flag=flag,
                                       rotation_matrix=r.ravel().tolist(), translation_vector=tr)
            if _text_value(ins.name):
                attribute(asm, inst, "name", ins.name)
            insts.append(inst)
            of_part.setdefault(ins.part, []).append(inst)
        _chain(insts, nxt="next_in_part", prev="prev_in_part")
        for k, ii in of_part.items():                     # the instances of each part
            _chain(ii, nxt="next_of_part", prev="prev_of_part")
            parts[k].f["ref_instance"] = ii[0]
        _chain(parts)
        asm.f["sub_instance"] = insts[0]
    # attributes: each owner's chain, each part's chain per kind and its list of them
    for owner, attrs in owned_attrs.values():
        _chain(attrs)
        owner.f["attributes_features"] = attrs[0]
    for part, kinds in chains.values():
        heads = []
        for attrs in kinds.values():
            _chain(attrs, nxt="next_of_type", prev="previous_of_type")
            heads.append(attrs[0])
        block = node("POINTER_LIS_BLOCK", n_entries=len(heads),
                     entries=heads + [None] * (20 - len(heads)))
        part.f["attribute_chains"] = node("LIST", owner=part, list_type=4,
                                          list_length=len(heads), block_length=20,
                                          size_of_entry=8, list_block=block, finger_block=block,
                                          finger_index=1, notransmit=True, _no_id=True)
    for geometry, ring in rings.values():
        for k, g in enumerate(ring):
            g.f["next"], g.f["previous"] = ring[(k + 1) % len(ring)], ring[k - 1]
        geometry.f["geometric_owner"] = ring[0]
    nid = 0
    for i, n in enumerate(out, start=1):
        n.index = i                                       # the root is node 1
        if n.f.pop("_no_id", False):                      # a 2D B-curve of an SP-curve
            n.f["node_id"] = 0
        elif any(f == "node_id" for f, _ in FIELDS[n.type][1]):   # not fins, bodies, assemblies
            nid += 1
            n.f["node_id"] = nid
    for n in out:
        if n.type in (10, 12):
            n.f["highest_node_id"] = nid
    return out


def _body_nodes(node, owned, attribute, body: Body):
    b = node("BODY", res_size=SIZE_BOX, res_linear=LINEAR_RES, state=1, body_type=1,
             nom_geom_state=1)
    if _text_value(body.name):
        attribute(b, b, "name", body.name)
    # regions and shells: the infinite void first; per lump a solid region with its outer
    # shell and one shell per cavity (faces' back side), the void shells on their front side
    # (the infinite region's for outer shells, a finite void region per cavity)
    void = node("REGION", body=b, type="V")
    regions, outer_voids, face_shells, first_solid = [void], [], {}, None
    for lump in body.lumps():
        solid = node("REGION", body=b, type="S")
        regions.append(solid)
        solid_shells = []
        for k, shell in enumerate(lump):
            ss = node("SHELL", body=b, region=solid)
            if k == 0:
                vs = node("SHELL", region=void)
                outer_voids.append(vs)
            else:
                cavity = node("REGION", body=b, type="V")
                regions.append(cavity)
                vs = node("SHELL", region=cavity)
                cavity.f["shell"] = vs
            solid_shells.append(ss)
            for i in shell:
                face_shells[i] = (ss, vs)
        _chain(solid_shells, prev=None)
        solid.f["shell"] = solid_shells[0]
        first_solid = first_solid or solid_shells[0]
    _chain(outer_voids, prev=None)
    void.f["shell"] = outer_voids[0]
    _chain(regions)
    b.f.update(region=void, shell=first_solid)

    tol = body.vertex_tol or [None] * len(body.vertices)
    vx = [node("VERTEX", owner=b, tolerance=t) for t in tol]
    pts = [node("POINT", owner=v, pvec=np.asarray(p, float)) for v, p in zip(vx, body.vertices)]
    for v, p in zip(vx, pts):
        v.f["point"] = p
    edges = [node("EDGE", owner=b, tolerance=e.tolerance) for e in body.edges]
    curves = []
    for en, e in zip(edges, body.edges):
        if e.curve is None:                               # tolerant: curves on the fins
            continue
        if e.trim is None:
            c = _geometry(node, CURVE_KINDS, e.curve, en)
            curves.append(c)
        else:                                             # the used piece of the curve
            basis = _geometry(node, CURVE_KINDS, e.curve, b)
            c = node("TRIMMED_CURVE", owner=en, sense="+", basis_curve=basis,
                     point_1=e.trim["p0"], point_2=e.trim["p1"], parm_1=e.trim["t0"],
                     parm_2=e.trim["t1"])
            owned(c, basis)
            curves += [c, basis]
        en.f["curve"] = c
    faces, surfaces, fins_at = [], [], {}
    edge_fins = [[] for _ in body.edges]
    for fi, face in enumerate(body.faces):
        ss, vs = face_shells[fi]
        fn = node("FACE", shell=ss, front_shell=vs, sense="+" if face.sense else "-")
        if face.color is not None:
            attribute(b, fn, "colour", face.color)
        sn = _geometry(node, SURFACE_KINDS, face.surface, fn)
        fn.f["surface"] = sn
        faces.append(fn)
        surfaces.append(sn)
        loops = []
        for loop in face.loops:
            ln = node("LOOP", face=fn)
            fins = []
            for fin in loop:
                if isinstance(fin, Isolated):     # an edge-less fin on the vertex, sense unset
                    hn = node("HALFEDGE", loop=ln, vertex=vx[fin.vertex])
                    fins_at.setdefault(fin.vertex, []).append(hn)
                    fins.append(hn)
                    continue
                hn = node("HALFEDGE", loop=ln, edge=edges[fin.edge], sense="+" if fin.sense else "-")
                fwd = fin_vertices(body, fin)[1]
                if fwd is not None:
                    hn.f["vertex"] = vx[fwd]
                    fins_at.setdefault(fwd, []).append(hn)
                if fin.sp is not None:            # tolerant edge: the fin's own curve
                    s = fin.sp
                    b2 = node("B_CURVE", owner=None, sense="+", _no_id=True,
                              nurbs=_nurbs(node, Geom("bcurve", s["b"])))
                    spn = node("SP_CURVE", owner=b, sense="+", surface=sn, b_curve=b2)
                    tc = node("TRIMMED_CURVE", owner=hn, sense="+", basis_curve=spn,
                              point_1=s["p0"], point_2=s["p1"], parm_1=s["t0"], parm_2=s["t1"])
                    owned(tc, spn)
                    owned(spn, sn)
                    hn.f["curve"] = tc
                    curves += [tc, spn]
                edge_fins[fin.edge].append((fin.sense, hn))
                fins.append(hn)
            for k, hn in enumerate(fins):
                hn.f["forward"] = fins[(k + 1) % len(fins)]
                hn.f["backward"] = fins[k - 1]
            ln.f["halfedge"] = fins[0]
            loops.append(ln)
        _chain(loops, prev=None)
        if loops:
            fn.f["loop"] = loops[0]
    for en, fins in zip(edges, edge_fins):
        (_, neg), (_, pos) = sorted(fins, key=lambda t: t[0])
        pos.f["other"], neg.f["other"] = neg, pos
        en.f["halfedge"] = pos                            # edge->fin is the positive fin
    for i, fins in fins_at.items():
        vx[i].f["halfedge"] = fins[0]
        _chain(fins, nxt="next_at_vx", prev=None)
    for owner, key, value in body.attributes:
        attribute(b, b if owner == "body" else faces[owner[1]], key, value)
    for ss in {id(s): s for s, _ in face_shells.values()}.values():   # back faces per shell
        chain = [fn for fi, fn in enumerate(faces) if face_shells[fi][0] is ss]
        _chain(chain)
        ss.f["face"] = chain[0]
    for vs in {id(v): v for _, v in face_shells.values()}.values():   # front faces per shell
        chain = [fn for fi, fn in enumerate(faces) if face_shells[fi][1] is vs]
        _chain(chain, nxt="next_front", prev="previous_front")
        vs.f["front_face"] = chain[0]
    for chain in (vx, pts, edges, curves, surfaces):
        _chain(chain)
    b.f.update(boundary_surface=surfaces[0] if surfaces else None,
               boundary_curve=curves[0] if curves else None,
               boundary_point=pts[0] if pts else None,
               edge=edges[0] if edges else None, vertex=vx[0] if vx else None)
    return b


# ---- text ---------------------------------------------------------------------------------
def _num(x) -> str:
    x = float(x)
    if x == 0:
        return "0"
    s = repr(x)
    return s[:-2] if s.endswith(".0") else s


def _field(kind, v) -> str:
    if kind in ("c", "l"):
        if kind == "l":
            return "T" if v else "F"
        return v if v else "?"
    if kind == "p":
        return f"{v.index if v is not None else 0} "
    if kind in ("d", "u", "n"):
        return f"{int(v or 0)} "
    if kind == "f":
        return "?" if v is None else _num(v) + " "
    if kind == "v":
        return "?" if v is None else "".join(_num(c) + " " for c in np.asarray(v, float))
    if kind in ("f[]", "f9"):
        return "".join(_num(c) + " " for c in v)
    if kind in ("u8", "u[]", "d[]"):
        return "".join(f"{int(c)} " for c in v)
    if kind == "l14":
        return "".join("T" if c else "F" for c in v)
    if kind == "p[]":
        return "".join(f"{c.index if c is not None else 0} " for c in v)
    if kind == "c[]":
        return v
    if kind == "n[]":
        return "".join(f"{int(c)} " for c in v)
    raise XtError(f"field kind {kind!r}")


def _record(n: _Node) -> str:
    name, fields = FIELDS[n.type]
    unknown = set(n.f) - {f for f, _ in fields}
    if unknown:
        raise XtError(f"{name}: unknown fields {sorted(unknown)}")
    body = "".join(_field(k, n.f.get(f)) for f, k in fields)
    if fields[-1][1].endswith("[]"):                  # variable length: type, length, index
        return f"{n.type} {len(n.f[fields[-1][0]])} {n.index} " + body
    return f"{n.type} {n.index} " + body


def _wrap(stream: str, width: int = 80) -> list:
    """Records of at most ``width`` characters. Trailing spaces are dropped by readers and
    leading ones are significant, so a record never ends with a space."""
    lines, i = [], 0
    while i < len(stream):
        j = min(i + width, len(stream))
        while j < len(stream) and stream[j - 1] == " " and j > i + 1:
            j -= 1
        lines.append(stream[i:j])
        i = j
    return lines


def _escape(v: str) -> str:
    return (str(v).replace("^", "^^").replace(";", "^;").replace(" ", "^_")
            .replace("\n", "^n"))


def header(name="part", file="part.x_t", application="BauyrCAD") -> list:
    """The common header: banner lines, part 1 (keyword data), part 2 (schema), part 3."""
    star = lambda s: (s + "*" * 80)[:80]                                    # noqa: E731
    part1 = {"MC": "unknown", "MC_MODEL": "unknown", "MC_ID": "unknown", "OS": "unknown",
             "OS_RELEASE": "unknown", "FRU": "BauyrCAD", "APPL": application, "SITE": "unknown",
             "USER": "unknown", "FORMAT": "text", "GUISE": "transmit", "KEY": name,
             "FILE": file, "DATE": _dt.date.today().strftime("%d-%b-%Y").lower().lstrip("0")}
    text = "**PART1;" + "".join(f"{k}={_escape(v)};" for k, v in part1.items())
    return ([star("**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"),
             star("**PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789")]
            + [text[i:i + 80] for i in range(0, len(text), 80)]
            + [f"**PART2;SCH={SCHEMA_KEY};USFLD_SIZE=0;", "**PART3;", star("**END_OF_HEADER")])


def to_text(model, name="part", file="part.x_t") -> str:
    """The whole .x_t file: one Body, a list of them (a multi-body part) or an Assembly."""
    nodes = _nodes(model)
    stream = (f"T{len(MODELLER)} {MODELLER}{len(SCHEMA_KEY)} {SCHEMA_KEY}0 "
              + "".join(_record(n) for n in nodes) + "1 0 ")
    return "\n".join(header(name, file) + _wrap(stream.rstrip(" "))) + "\n"


def write(model, path, name=None) -> None:
    """Write a .x_t (LF line ends, ASCII): one Body, a list of them or an Assembly."""
    from pathlib import Path
    path = Path(path)
    first = (model if isinstance(model, (Body, Assembly)) else model[0])
    text = to_text(model, name or first.name or path.stem, path.name)
    path.write_bytes(text.encode("ascii"))


# ---- neutral binary (.x_b; the bodies inside SolidWorks files) -----------------------------
_NULL_INT, _NULL_REAL = -32764, -3.14158e13
# SolidWorks 2024 reads an Imported body only under its own schema key; the nodes keep the
# base schema 13006's layouts (each type flagged 255, 'same as base', at its first node)
SW_MODELLER = ": TRANSMIT FILE created by modeller version 3501256"
SW_SCHEMA_KEY, SW_NODE_TYPES = "SCH_3501256_35102_13006", 231


def _ptr(i: int) -> bytes:
    """A node index: index + 1 in 2 bytes, larger ones as (-(i % 32767 + 1), i // 32767)."""
    import struct
    if i < 32767:
        return struct.pack(">h", i + 1)
    return struct.pack(">hh", -(i % 32767 + 1), i // 32767)


def _bin_field(kind, v) -> bytes:
    import struct
    if kind == "p":
        return _ptr(v.index if v is not None else 0)
    if kind == "p[]":
        return b"".join(_ptr(c.index if c is not None else 0) for c in v)
    if kind == "d":
        return struct.pack(">i", _NULL_INT if v is None else int(v))
    if kind == "u":
        return struct.pack(">B", int(v or 0))
    if kind == "n":
        return struct.pack(">h", int(v or 0))
    if kind == "f":
        return struct.pack(">d", _NULL_REAL if v is None else float(v))
    if kind == "v":
        vals = [_NULL_REAL] * 3 if v is None else [float(c) for c in np.asarray(v, float)]
        return struct.pack(">3d", *vals)
    if kind == "c":
        return (v or "?").encode("ascii")
    if kind == "l":
        return b"\x01" if v else b"\x00"
    if kind in ("f[]", "f9"):
        return b"".join(struct.pack(">d", float(c)) for c in v)
    if kind in ("u8", "u[]"):
        return bytes(int(c) for c in v)
    if kind == "d[]":
        return b"".join(struct.pack(">i", int(c)) for c in v)
    if kind == "n[]":
        return b"".join(struct.pack(">h", int(c)) for c in v)
    if kind == "l14":
        return bytes(1 if c else 0 for c in v)
    if kind == "c[]":
        return v.encode("ascii")
    raise XtError(f"field kind {kind!r}")


def _bin_record(n: _Node, base13006: bool) -> bytes:
    import struct
    name, fields = FIELDS[n.type]
    if base13006 and n.type == 80:            # schema 13006: field_names after actions
        fields = fields[:4] + [("field_names", "p")] + fields[4:]
    body = b"".join(_bin_field(k, n.f.get(f)) for f, k in fields)
    head = struct.pack(">h", n.type)
    if fields[-1][1].endswith("[]"):
        head += struct.pack(">i", len(n.f[fields[-1][0]]))
    return head, _ptr(n.index) + body


def to_binary(model, solidworks: bool = False) -> bytes:
    """Neutral binary Parasolid (.x_b) of one Body, a list of them or an Assembly. With
    solidworks=True: SolidWorks 2024's schema key (base 13006, every node type 'same as
    base'), the only form its Imported bodies accept."""
    import struct
    nodes = _nodes(model)
    modeller, key = (SW_MODELLER, SW_SCHEMA_KEY) if solidworks else (MODELLER, SCHEMA_KEY)
    out = [b"PS\x00\x00", struct.pack(">h", len(modeller)), modeller.encode(),
           struct.pack(">i", len(key)), key.encode()]
    if solidworks:
        out.append(struct.pack(">h", SW_NODE_TYPES))
    out.append(struct.pack(">i", 0))                        # user field size
    seen = set()
    for n in nodes:
        head, rest = _bin_record(n, solidworks)
        if solidworks and n.type not in seen:
            seen.add(n.type)
            head = head[:2] + b"\xff" + head[2:]
        out += [head, rest]
    out.append(b"\x00\x01\x00\x01")                         # terminator: type 1, index 0
    return b"".join(out)
