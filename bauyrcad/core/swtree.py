"""Editing SolidWorks 2024 feature trees on the exact archive model (bauyrcad.core.mfc).

:func:`transplant` copies features that a donor part (saved by SolidWorks) has on top of a
base part into that base: objects the new features share with the donor's older features
(planes, the part's external object, the body they merge into...) become the base's
counterparts, everything else is copied, and :func:`renumber` sets the counters SolidWorks
keeps in step with the tree. :mod:`mfc` numbers the archive afresh when it is written, so
every tag and plain reference follows the inserted objects.

Only ``Contents/Config-0`` and ``Contents/Config-0-ResolvedFeatures`` need to be exact: the
model header, ``Header2``, ``CMgr`` and ``CMgrHdr2`` of the base may stay as they are
(SolidWorks 2024 opens, rebuilds and edits such a file; docs/SLDPRT_FORMAT.md section 17).

No OCC, no Qt.
"""

from __future__ import annotations

import struct
import math
from pathlib import Path

from . import mfc, sldprt, swfile

FIXED_SEQ = 101          # the sequence word of folders and planes SolidWorks does not renumber
ATOM_BASE = 100          # atom k (oldest first) is numbered 100 + k
ATOM_BYTES = 88          # an atom's size in Config-0 (the model data counts it)
LAST_BODY = 58           # a body feature's countdown: 58 + body features from it to the end
PMARK_FIRST = 4          # a body feature's PMark sequence: 4 + 3 (atoms + k - 1)


# sections SolidWorks rebuilds or does without (docs/SLDPRT_FORMAT.md sections 12 and 17): the
# geometry caches must go (the stored solid is not compared with the features), the indexes
# and add-in data would be stale
REGENERATED = swfile.CACHES + (
    "Contents/3DExperienceExchange2", "Contents/3DExperienceExchange_ConfigHeader",
    "Contents/PMISemanticDataDB", "SwDocContentMgr/SwDocContentMgrInfo",
    "swXmlContents/KeyWords", "swXmlContents/Features", "ThirdPty/Animator",
    "ThirdPty/CMMotionLoadMapU", "ThirdPty/CM_MOTION_LOAD_1", "ThirdPty/SWA_Schedules",
    "ThirdPtyStore/VisualStates", "ModelStamps")


class TreeError(ValueError):
    pass


def load(src) -> tuple[swfile.Container, mfc.Archive]:
    """A part file (path or bytes) as its container and its configuration-0 archive."""
    data = src if isinstance(src, (bytes, bytearray)) else open(src, "rb").read()
    return swfile.Container(bytes(data)), mfc.load_archive(sldprt.read_sections(bytes(data)))


def save(container: swfile.Container, arc: mfc.Archive, path) -> None:
    """Write the part with the edited archive; SolidWorks rebuilds the solid from the
    features on opening (the model header and configuration manager stay as they were)."""
    names = container.names()
    with open(path, "wb") as fh:
        fh.write(container.build(arc.dumps(), [n for n in REGENERATED if n in names]))


def name(f: mfc.Obj) -> str:
    return f.values["name"].values["name"]


def _feature_id(f: mfc.Obj) -> int:
    return f.values["name"].values["id"]


# list fields whose items are paired by a key rather than by position
_KEYED = {
    ("uoModelData_c", "atoms"): lambda d: d["_0"].values["feature_id"],
    ("moHistoryFolder_c", "items"): lambda d: d["_1"].values["id"],
}


def _pair(a, b, owner, key, out: dict, seen: set):
    """Pair the donor's value `a` with the base's `b` (same layout): objects of the same class
    met at the same place are the same object."""
    if isinstance(a, mfc.Obj) and isinstance(b, mfc.Obj):
        if a.name != b.name or id(a) in seen:
            return
        seen.add(id(a))
        out.setdefault(id(a), b)
        for k, v in a.values.items():
            _pair(v, b.values.get(k), a.name, k, out, seen)
    elif isinstance(a, dict) and isinstance(b, dict):
        for k, v in a.items():
            _pair(v, b.get(k), owner, k, out, seen)
    elif isinstance(a, list) and isinstance(b, list):
        keyf = _KEYED.get((owner, key))
        if keyf is None:
            for x, y in zip(a, b):
                _pair(x, y, owner, key, out, seen)
            return
        by = {keyf(y): y for y in b}
        for x in a:
            y = by.get(keyf(x))
            if y is not None:
                _pair(x, y, owner, key, out, seen)
    elif isinstance(a, mfc.Ref) and isinstance(b, mfc.Ref) and a.target is not None and \
            b.target is not None:
        _pair(a.target, b.target, owner, key, out, seen)


class _Copier:
    def __init__(self, pairs: dict, classes: dict):
        self.pairs = pairs
        self.classes = classes          # name -> the base's MfcClass (new ones added)
        self.copies: dict = {}

    def obj(self, o):
        if o is None:
            return None
        if id(o) in self.pairs:
            return self.pairs[id(o)]
        if id(o) in self.copies:
            return self.copies[id(o)]
        cls = self.classes.get(o.cls.name)
        if cls is None:
            cls = self.classes[o.cls.name] = mfc.MfcClass(o.cls.name, o.cls.schema)
        n = mfc.Obj(cls)
        self.copies[id(o)] = n
        n.values = {k: self.val(v) for k, v in o.values.items()}
        return n

    def val(self, v):
        if isinstance(v, mfc.Obj):
            return self.obj(v)
        if isinstance(v, dict):
            return {k: self.val(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.val(x) for x in v]
        if isinstance(v, mfc.Ref):
            return mfc.Ref(v.index, self.obj(v.target) if v.target is not None else None)
        if isinstance(v, mfc.Opaque):
            raise TreeError("a new feature holds bytes that are not decoded (opaque)")
        return v


def transplant(base: mfc.Archive, donor: mfc.Archive, names) -> list:
    """Copy the donor's features `names` (the last ones of its tree, absent from the base)
    into the base, after its last feature, with their atoms and history entries. The donor
    must hold the base's other features under the same names. Returns the new features."""
    names = list(names)
    bf = {name(f): f for f in base.features}
    dfeat = [f for f in donor.features if name(f) in names]
    if [name(f) for f in dfeat] != names or any(n in bf for n in names):
        raise TreeError(f"the donor must end with {names}, which the base must not have")
    ids = {_feature_id(f) for f in base.features}
    if ids & {_feature_id(f) for f in dfeat}:
        raise TreeError("a new feature's id is already used in the base")
    pairs: dict = {}
    seen: set = set()
    for f in donor.features:
        if name(f) not in names and name(f) in bf:
            _pair(f, bf[name(f)], None, None, pairs, seen)
    _pair(donor.config.root, base.config.root, None, None, pairs, seen)
    classes = {c.name: c for c in base.table if isinstance(c, mfc.MfcClass)}
    cp = _Copier(pairs, classes)
    new = [cp.obj(f) for f in dfeat]
    missing = [name(f) for f in donor.features if id(f) in cp.copies and name(f) not in names]
    if missing:
        raise TreeError(f"the new features need {missing} too (not in the base)")
    base.resolved.root["features"].extend({"_0": f} for f in new)
    # the atoms of the new body features (newest first) and their history entries
    bmd = base.config.root["part"].values["model_data"].values
    dmd = donor.config.root["part"].values["model_data"].values
    new_ids = {_feature_id(f) for f in dfeat}
    atoms = [cp.val(d) for d in dmd["atoms"] if d["_0"].values["feature_id"] in new_ids]
    bmd["atoms"][:0] = atoms
    bh, dh = (next(f for f in a.features if f.name == "moHistoryFolder_c") for a in (base, donor))
    have = {d["_1"].values["id"] for d in bh.values["items"]}
    bh.values["items"].extend(cp.val(d) for d in dh.values["items"]
                              if d["_1"].values["id"] not in have and
                              d["_1"].values["id"] in new_ids)
    renumber(base, len(new), len(atoms), next_id=max(
        d["_0"].values["next_id"] for d in dmd["atoms"]) if dmd["atoms"] else None)
    return new


def _set_u32(b: bytes, at: int, v: int) -> bytes:
    b = bytearray(b)
    struct.pack_into("<I", b, at, v)
    return bytes(b)


def _get_u32(b: bytes, at: int) -> int:
    return struct.unpack_from("<I", b, at)[0]


def _features_with_seq(arc: mfc.Archive) -> list:
    """The value dicts of every feature block (top-level features, inline children, the
    notes folder's end tag written inline), once each, in tree order."""
    out, seen = [], set()

    def visit(o):
        if not isinstance(o, mfc.Obj) or id(o) in seen:
            return
        seen.add(id(o))
        if "created_version" in o.values:
            out.append(o.values)
        for v in o.values.values():
            walk(v)

    def walk(v):
        if isinstance(v, mfc.Obj):
            if "created_version" in v.values:
                visit(v)
        elif isinstance(v, dict):
            if "created_version" in v and id(v) not in seen:
                seen.add(id(v))
                out.append(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for f in arc.features:
        visit(f)
    return out


def renumber(arc: mfc.Archive, n_new: int, atoms_new: int, next_id: int | None = None):
    """Set the counters for `n_new` features (of which `atoms_new` bodies, each with an atom)
    just appended to the tree:

    * every renumbered sequence word (not 101) moves up by n_new, the new features continue
      the sequence; the environment's word too;
    * atoms are numbered 100 + k (oldest first), all but the newest flagged; their next-id
      words take `next_id`;
    * the model data, the sketch-block manager and the transferred plane data count the
      atoms (+1 each, the model data +88 bytes each);
    * body features count down to the end (58 + n) and name their atom (100 + k); their
      PMark records are 4 + 3 (atoms + k - 1).
    """
    if any(f.name in ('Fillet_c', 'Chamfer_c') for f in arc.features):
        raise TreeError('Finishing-feature counter renumbering is not supported; use the gated offline writer')
    feats = _features_with_seq(arc)
    old = feats[:len(feats) - n_new] if n_new else feats
    for f in old:
        if f["seq"] != FIXED_SEQ:
            f["seq"] += n_new
    top = max(f["seq"] for f in old if f["seq"] != FIXED_SEQ)
    for k, f in enumerate(feats[len(old):], 1):
        f["seq"] = top + k
    part = arc.config.root["part"].values
    env = part["environment"].values
    env["data"] = _set_u32(env["data"], 12, _get_u32(env["data"], 12) + n_new)
    md = part["model_data"].values
    atoms = md["atoms"]
    na = len(atoms)
    for k, d in enumerate(reversed(atoms), 1):
        a = d["_0"].values
        a["head"] = _set_u32(a["head"], 8, ATOM_BASE + k)
        a["_3"] = 0 if k == na else 1                # 1 on every atom but the newest
        if next_id is not None:
            a["next_id"] = a["next_id2"] = max(next_id, a["next_id"])
    md["head"] = _set_u32(md["head"], 44, ATOM_BASE + na)
    md["head"] = _set_u32(md["head"], 34, _get_u32(md["head"], 34) + ATOM_BYTES * atoms_new)
    bl = part["blocks"].values
    bl["tail"] = _set_u32(bl["tail"], 84, _get_u32(bl["tail"], 84) + atoms_new)
    tr = part["trans_ref_plane"].values
    tr["tail"] = _set_u32(tr["tail"], 140, _get_u32(tr["tail"], 140) + atoms_new)
    bodies = [f for f in arc.features if "counters" in f.values]
    for k, f in enumerate(bodies, 1):
        c = f.values["counters"]
        revolve = "has_merge" in f.values
        if revolve:
            c += struct.pack("<I", f.values["has_merge"]) + f.values["body_data"]["tail"]
        c = c[:16] + struct.pack("<H", LAST_BODY + len(bodies) - k + 1) + c[18:]
        c = _set_u32(c, 28 if len(c) == 48 else 32, ATOM_BASE + k)
        if revolve:
            f.values["counters"] = c[:18]
            f.values["body_data"]["tail"] = c[22:]
        else:
            f.values["counters"] = c
        pm = f.values.get("pmark")
        if pm is not None:
            pm.values["seq"] = PMARK_FIRST + 3 * (na + k - 1)
        if revolve:
            axis = f.values["axis"].values
            sketch_id = axis["entity"].values["id"]
            axis["sketch_seq"] = next(sk.values["seq"] for sk in arc.features
                                      if _feature_id(sk) == sketch_id)


# ----------------------------------------------------------------------------------------
# Sketch profiles (docs/SLDPRT_FORMAT.md section 18)
# ----------------------------------------------------------------------------------------

POINT_KIND = 0x1E
HOLE = 0x10001            # a chain's flag word for an inner loop (1 for the outer one)
HORIZONTAL, VERTICAL = 4, 5   # sketch relation types (one line each, relation group 1)


def _class(arc: mfc.Archive, cname: str) -> mfc.MfcClass:
    for c in arc.table:
        if isinstance(c, mfc.MfcClass) and c.name == cname:
            return c
    c = mfc.MfcClass(cname, 1)
    arc.table.append(c)
    return c


def _handle(arc, kind: str, index: int) -> mfc.Obj:
    o = mfc.Obj(_class(arc, "sgLineHandle" if kind == "line" else "sgArcHandle"))
    o.values = {"index": index, "_1": -1, "_2": 0}
    return o


def _close(a, b, tol=1e-9) -> bool:
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _relation(arc, rtype: int, line: int) -> dict:
    return {"_0": bytes(8), "_1": -1, "_2": bytes(16), "type": rtype, "_4": 2, "_5": 0, "_6": 0,
            "_7": 0xFFFE, "_8": 0, "entities": [{"_0": _handle(arc, "line", line)}]}


def set_profile(arc: mfc.Archive, feature: mfc.Obj, loops, hv: bool = True, dims=()) -> None:
    """Replace the entities of a sketch feature by closed loops, the outer loop first, then
    holes. A loop is a list of segments in sketch coordinates (mm), each ``("line", p, q)``,
    ``("arc", centre, p, q)`` (counter-clockwise from p to q),
    ``("arc", centre, p, q, ccw)`` or one ``("circle", centre, r)``;
    consecutive segments must share their end points. With `hv`, lines along the sketch axes
    get horizontal / vertical relations (as SolidWorks infers them). ``dims`` contains
    ``("length", line_index)`` / ``("diameter", arc_index)`` pairs (zero-based within each
    entity list), dimensioning their current sizes. Existing dimensions are removed."""
    sk = feature.values["sketch"].values
    tp = dict(sk["points"][0])
    ts = next(s for sl in sk["seglists"] for s in sl["segs"])
    points, lines, arcs, chains = [], [], [], []
    chain_id = 0

    def point(xy, a=0, b=2, c=0):
        p = dict(tp)
        base = bytearray(p["base"])
        base[17] = 0
        p["base"] = bytes(base)
        p.update(id=len(points) + 1, kind=POINT_KIND, x=xy[0] / 1e3, y=xy[1] / 1e3, a=a, b=b, c=c,
                 seg0=None, seg1=None)
        points.append(p)
        return len(points) - 1

    def segment(kind, p0, p1, centre=None, ccw=True):
        nonlocal chain_id
        chain_id += 1
        s = dict(ts)
        base = bytearray(s["base"])
        base[17] = 0                               # relations on this entity
        lst = lines if kind == "line" else arcs
        s.update({"_0": chain_id, "id": len(lst) + 1, "base": bytes(base), "p0": p0, "p1": p1,
                  "_5": 1, "_6": -1.0, "arc": (1 if ccw else 0xffffffff) if kind != "line" else 0,
                  "arc_data": None if kind == "line" else
                  {"centre": centre, "_1": -2, "_2": -2, "_3": -2, "_4": -2, "_5": 0}})
        lst.append(s)
        return kind, len(lst) - 1, chain_id

    for k, loop in enumerate(loops):
        ids = []
        if len(loop) == 1 and loop[0][0] == "circle":
            _, c, r = loop[0]
            ci = point(c, b=1, c=1)
            pi = point((c[0] + r, c[1]), a=2)
            kind, i, cid = segment("arc", pi, pi, ci)
            points[pi]["seg0"] = _handle(arc, kind, i)
            ids.append(cid)
        else:
            starts = [point(seg[1] if seg[0] == "line" else seg[2]) for seg in loop]
            made = []
            for j, seg in enumerate(loop):
                end = seg[2] if seg[0] == "line" else seg[3]
                nxt = loop[(j + 1) % len(loop)]
                if not _close(end, nxt[1] if nxt[0] == "line" else nxt[2]):
                    raise TreeError(f"loop {k}: segment {j} does not end where the next starts")
                p0, p1 = starts[j], starts[(j + 1) % len(loop)]
                if seg[0] == "line":
                    made.append(segment("line", p0, p1))
                else:
                    made.append(segment("arc", p0, p1, point(seg[1], b=1, c=1), len(seg) < 5 or seg[4]))
            for j, (kind, i, cid) in enumerate(made):
                ids.append(cid)
            for j in range(len(loop)):              # the two segments meeting at each start
                meet = sorted([made[j - 1], made[j]], key=lambda m: (m[0] != "line", m[1]))
                p = points[starts[j]]
                p["seg0"], p["seg1"] = (_handle(arc, m[0], m[1]) for m in meet)
        ch = mfc.Obj(_class(arc, "moSketchChain_c"))
        chains.append((k == 0, ch, ids))
    tc = next(c["_0"] for f in arc.features for c in f.values.get("chains", []))
    out = []
    for outer, ch, ids in sorted(chains, key=lambda c: c[0]):      # holes first
        ch.values = dict(tc.values, segments=[{"_0": i} for i in ids], _1=1 if outer else HOLE)
        out.append({"_0": ch})
    sk["npts"] = len(points)
    sk["points"] = points
    sk["seglists"] = [{"n": len(lines), "_1": 0, "segs": lines},
                      {"n": 0, "_1": 0, "segs": []},
                      {"n": len(arcs), "_1": 0, "segs": arcs}]
    for g in range(1, 9):
        sk[f"n_rel{g}"], sk[f"rel{g}"] = 0, []
    rels = []
    if hv:
        for i, s in enumerate(lines):
            (x0, y0), (x1, y1) = ((points[s[k]]["x"], points[s[k]]["y"]) for k in ("p0", "p1"))
            rtype = HORIZONTAL if abs(y1 - y0) < 1e-12 else VERTICAL if abs(x1 - x0) < 1e-12                 else None
            if rtype is not None:
                rels.append(_relation(arc, rtype, i))
                base = bytearray(s["base"])
                base[17] += 1
                s["base"] = bytes(base)
    sk["n_rel1"], sk["rel1"] = len(rels), rels
    sk["_29"] = struct.pack("<I", chain_id + len(rels) + 1) + sk["_29"][4:]  # segs + rels + 1
    table = [dict(d) for d in sk["table"]]
    for i, v in enumerate((chain_id + 1, len(points) + 1, len(lines) + 1, len(arcs) + 1)):
        table[i]["_0"] = v
    sk["table"] = table
    feature.values["chains_count"] = feature.values["_37"] = len(out)
    feature.values["chains"] = out
    sk["linear_dims"], sk["radial_dims"] = [], []
    sk["list1"].values["items"] = []
    feature.values["axis"] = None
    for kind, index in dims:
        add_dimension(arc, feature, kind, index)


def add_centerline(arc: mfc.Archive, feature: mfc.Obj, start, end) -> int:
    """Append a construction axis (mm) without adding it to a closed profile chain."""
    if _close(start, end) or not all(math.isfinite(x) for p in (start, end) for x in p):
        raise TreeError("centerline needs two finite distinct points")
    sk = feature.values["sketch"].values
    points, lines = sk["points"], sk["seglists"][0]["segs"]
    ids = []
    for xy in (start, end):
        p = dict(points[0])
        base = bytearray(p["base"]); base[17] = 0
        p.update(id=len(points)+1, base=bytes(base), x=xy[0]/1000, y=xy[1]/1000,
                 a=0, b=1, c=0, seg0=None, seg1=None)
        ids.append(len(points)); points.append(p)
    # Construction-line style from the SW-saved M5 donors. Its endpoints are free points.
    base = bytearray.fromhex("ffff1f0003ffffffff0400ffff000080bf0000000000000500010002000000000080bf"
                             "00000c00000000000000000000000000000000f03f0000000000000000")
    index = len(lines)
    # The construction marker is carried by the preceding serialized record, not
    # just by this line's style: the high word of the prior line's final word, or
    # the line-list prefix when this is its first segment. Omitting it makes SW
    # interpret the axis as profile geometry (revolve rebuild error 51).
    if lines:
        lines[-1]["arc"] |= 2 << 16
    else:
        sk["seglists"][0]["_1"] = 2
    total = sum(len(sl["segs"]) for sl in sk["seglists"])
    s = dict(next(s for sl in sk["seglists"] for s in sl["segs"]))
    s.update(_0=total+1, id=index+1, base=bytes(base), p0=ids[0], p1=ids[1],
             _5=0, _6=-1., arc=0, arc_data=None)
    rtype = HORIZONTAL if abs(start[1]-end[1])<1e-9 else VERTICAL if abs(start[0]-end[0])<1e-9 else None
    if rtype is not None:
        sk["rel1"].append(_relation(arc, rtype, index)); sk["n_rel1"] += 1
        base[17] = 1; s["base"] = bytes(base)
    lines.append(s); sk["seglists"][0]["n"] += 1; sk["npts"] += 2
    for i, extra in ((0,1),(1,2),(2,1)):
        sk["table"][i]["_0"] += extra
    sk["_29"] = _set_u32(sk["_29"], 0, _get_u32(sk["_29"], 0)+2+int(rtype is not None))
    feature.values["axis"] = _handle(arc, "line", index)
    return index


def add_dimension(arc: mfc.Archive, feature: mfc.Obj, kind: str, index: int) -> str:
    """Add a driving line length, diameter or radius; return D1, D2, ...

    ``index`` is zero-based in the sketch's line list (length) or arc list (radial).
    The bundled SolidWorks-made donors supply only annotation/style templates; all entity
    references, parameter ids, names and values are written here, without SolidWorks.
    Diameter accepts full circles; radius accepts circles and arcs. Duplicates are refused.
    """
    if feature not in arc.features or feature.name != "moProfileFeature_c":
        raise TreeError("dimension target must be a sketch feature in this archive")
    if kind not in ("length", "diameter", "radius"):
        raise TreeError("dimension kind must be 'length', 'diameter' or 'radius'")
    sk_obj = feature.values["sketch"]
    sk = sk_obj.values
    radial = kind in ("diameter", "radius")
    segs = sk["seglists"][2 if radial else 0]["segs"]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(segs):
        raise TreeError(f"{kind}: entity index out of range")
    seg = segs[index]
    group = "radial_dims" if radial else "linear_dims"
    for item in sk[group]:
        d = item["_0"].values
        duplicate = d["arc"].values["index"] == index if radial else {
            d["p0"].values["index"], d["p1"].values["index"]} == {seg["p0"], seg["p1"]}
        if duplicate:
            raise TreeError("this entity already has a dimension")
    points = sk["points"]
    if kind == "diameter" and seg["p0"] != seg["p1"]:
        raise TreeError("diameter dimensions require a full circle")
    p, q = (points[seg["arc_data"]["centre"]], points[seg["p0"]]) if radial else (
        points[seg["p0"]], points[seg["p1"]])
    value = math.hypot(q["x"] - p["x"], q["y"] - p["y"]) * (2 if kind == "diameter" else 1)
    if not math.isfinite(value) or value <= 0:
        raise TreeError("dimension size must be finite and positive")
    seed = "sketch_radial.SLDPRT" if radial else "sketch_linear.SLDPRT"
    if kind == "radius":
        seed = "native/SI_radius_arc.SLDPRT"
    _, donor = load(Path(__file__).parents[1] / "data" / "sw_seeds" / seed)
    source = next(f.values["sketch"] for f in donor.features if name(f) == "Sketch1")
    template = source.values[group][0]["_0"]
    display = source.values["list1"].values["items"][0]["_0"]
    classes = {c.name: c for c in arc.table if isinstance(c, mfc.MfcClass)}
    cp = _Copier({id(source): sk_obj}, classes)
    dim, ann = cp.obj(template), cp.obj(display)
    all_dims = sk["linear_dims"] + sk["radial_dims"]
    existing = {x["_0"].values["param"].values["name"].values["name"] for x in all_dims}
    n = 1
    while f"D{n}" in existing:
        n += 1
    dim_name = f"D{n}"
    # Walk the live graph: the original load map does not include transplanted features or
    # dimensions added since loading. Never reuse one of their ids.
    ids, seen = [], set()
    def collect(v):
        if isinstance(v, mfc.Obj):
            if id(v) in seen:
                return
            seen.add(id(v))
            if v.name in ("moNodeName_c", "moLengthParameter_c", "moAngleParameter_c") and v.values["id"] != 0xFFFFFFFF:
                ids.append(v.values["id"])
            collect(v.values)
        elif isinstance(v, dict):
            for x in v.values():
                collect(x)
        elif isinstance(v, list):
            for x in v:
                collect(x)
    collect(arc.config.root)
    collect(arc.resolved.root)
    param = dim.values["param"].values
    param["id"], param["value"] = max(ids, default=0) + 1, value
    param["name"].values["name"] = dim_name
    if radial:
        dim.values["arc"].values["index"] = index
        affected = [seg]
    else:
        for key in ("p0", "p1"):
            dim.values[key].values["index"] = seg[key]
        affected = [p, q]
    for ent in affected:
        base = bytearray(ent["base"])
        base[17] += 1
        ent["base"] = bytes(base)
    h = ann.values["handle"].values
    h["sketch"], h["index"] = mfc.Ref(0, sk_obj), len(sk[group])
    h["value"] = h["_16"] = value
    part = arc.config.root["part"].values
    if all_dims:
        annotation_id = struct.unpack_from("<I", sk["list1"].values["items"][0]["_0"].values["_0"], 155)[0]
    else:
        part["_22"] += 1
        annotation_id = part["_22"]
    head = bytearray(ann.values["_0"])
    struct.pack_into("<I", head, 155, annotation_id)
    ann.values["_0"] = bytes(head)
    sk[group].append({"_0": dim})
    sk["list1"].values["items"].append({"_0": ann})
    place_dimensions(feature)
    for c in classes.values():
        if c not in arc.table:
            arc.table.append(c)
    return dim_name


def place_dimensions(feature: mfc.Obj) -> None:
    """Place sketch annotations outside the profile, in metres in sketch coordinates.

    Positions and handle offsets were identified by moving annotations in saved SW2024
    donors. Recompute the entire layout so incremental additions stay deterministic.
    """
    sk = feature.values["sketch"].values
    points = sk["points"]
    xy = [(p["x"], p["y"]) for p in points]
    if not xy:
        return
    xmin, xmax = min(p[0] for p in xy), max(p[0] for p in xy)
    ymin, ymax = min(p[1] for p in xy), max(p[1] for p in xy)
    # Include whole circles, whose stored circumference point alone is not a bound.
    for seg in sk["seglists"][2]["segs"]:
        c, p = points[seg["arc_data"]["centre"]], points[seg["p0"]]
        r = math.hypot(p["x"]-c["x"], p["y"]-c["y"])
        xmin, xmax = min(xmin, c["x"]-r), max(xmax, c["x"]+r)
        ymin, ymax = min(ymin, c["y"]-r), max(ymax, c["y"]+r)
    cx, cy = (xmin+xmax)/2, (ymin+ymax)/2
    gx, gy = sum(p[0] for p in xy)/len(xy), sum(p[1] for p in xy)/len(xy)
    gap = max(.003, .12 * max(xmax-xmin, ymax-ymin))
    placed = []
    for item in sk["list1"].values["items"]:
        ann = item["_0"]
        h = ann.values["handle"].values
        radial = ann.name == "moDisplayRadialDim_c"
        d = sk["radial_dims" if radial else "linear_dims"][h["index"]]["_0"].values
        if radial:
            seg = sk["seglists"][2]["segs"][d["arc"].values["index"]]
            p = points[seg["arc_data"]["centre"]]
            mx, my = p["x"], p["y"]
            radius = d["param"].values["value"] / (2 if d["type"] == 15 else 1)
            nx, ny = (1 if mx >= cx else -1)/math.sqrt(2), (1 if my >= cy else -1)/math.sqrt(2)
            offset = max(radius, (xmax-mx if nx>0 else mx-xmin)/abs(nx),
                         (ymax-my if ny>0 else my-ymin)/abs(ny)) + gap
        else:
            p, q = (points[d[k].values["index"]] for k in ("p0", "p1"))
            mx, my = (p["x"]+q["x"])/2, (p["y"]+q["y"])/2
            length = math.hypot(q["x"]-p["x"], q["y"]-p["y"])
            nx, ny = -(q["y"]-p["y"])/length, (q["x"]-p["x"])/length
            if nx*(mx-gx) + ny*(my-gy) < 0:
                nx, ny = -nx, -ny
            # Project the complete profile box onto the outward normal.
            offset = max(nx*(x-mx)+ny*(y-my) for x in (xmin,xmax) for y in (ymin,ymax)) + gap
        x, y = mx+nx*offset, my+ny*offset
        while any(math.hypot(x-a,y-b) < gap*1.5 for a,b in placed):
            offset += gap
            x, y = mx+nx*offset, my+ny*offset
        placed.append((x,y))
        head = bytearray(ann.values["_2"])
        struct.pack_into("<3d", head, 0, x, y, 0.)
        ann.values["_2"] = bytes(head)
        data = bytearray(h["_7"])
        struct.pack_into("<5d", data, 0, nx, ny, 0., offset/radius if radial else .5,
                         offset-radius if radial else offset)
        if radial:
            struct.pack_into("<d", data, 40, 2*radius)
        h["_7"] = bytes(data)
