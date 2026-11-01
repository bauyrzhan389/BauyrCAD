"""Read SolidWorks part / assembly files (.sldprt / .sldasm) without SolidWorks.

Decoded from the files alone (2026-10-11/15, notes in docs/SLDPRT_FORMAT.md). Pure Python +
NumPy, no OCC, no Qt, no SolidWorks. It gives:

* every section ("stream") of the file, from both containers SolidWorks has used:
  an OLE2 compound file (up to about SolidWorks 2014) and, since 2015, a ZIP-like
  container whose local headers carry a per-file signature instead of ``PK\\3\\4`` and
  nibble-swapped section names;
* the Parasolid transmit streams of each configuration (``Contents/Config-N-Partition``):
  the exact B-rep, as a binary Parasolid partition (``.xmp_bin``) plus its rollback deltas;
* the display-list tessellation SolidWorks stores for quick opening
  (``Contents/DisplayLists``), as a triangle mesh in mm, which is enough for STL;
* the FeatureManager tree (:func:`feature_tree`): names, feature ids, MFC classes and the
  matching ``IFeature.GetTypeName2`` names, suppression, absorbed sketches and the
  dimensions SolidWorks shows (:func:`keywords`);
* the mates of an assembly (:func:`mates`): name, ``swMateType_e``, components, and where
  SolidWorks stores them, alignment, distance, angle and suppression.

The feature tree and mates are what the SolidWorks export tests use to prove, without
trusting the SolidWorks API alone, that an exported file holds native features.

The B-rep still has to be translated from Parasolid XT to OCC before STEP export; see the
roadmap in docs/SLDPRT_FORMAT.md.
"""

from __future__ import annotations

import re
import struct
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# 16-byte tag in front of every zlib chunk inside a section (both containers)
ZLB_TAG = bytes.fromhex("231dd571da8148a2a85898b21b89ef99")
_CFB_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
# ZIP local header after the signature: version 20, flags 6, method 8 (deflate)
_SW_MARK = b"\x14\x00\x06\x00\x08\x00"


class SldprtError(Exception):
    pass


# ----------------------------------------------------------------------------------------
# Containers
# ----------------------------------------------------------------------------------------

def container(data: bytes) -> str:
    """'cfb' (OLE2 compound file, SolidWorks <= 2014) or 'sw2015' (ZIP-like, 2015+)."""
    if data[:8] == _CFB_MAGIC:
        return "cfb"
    if data[4:8] == b"\x00\x00\x00\x04" and _SW_MARK in data[:4096]:
        return "sw2015"
    raise SldprtError("Not a SolidWorks file (unknown container)")


def _nibble_swap(b: bytes) -> bytes:
    return bytes(((x >> 4) | (x << 4)) & 0xFF for x in b)


def _sw2015_sections(data: bytes):
    """Sections of the 2015+ container: [sig 4][14 00 06 00 08 00][dos time 4][crc32 4]
    [compressed 4][uncompressed 4][name length 4][name, nibble-swapped][raw deflate].
    The 4-byte signature differs per file, so a section is recognised by its CRC-32. The
    remaining marker hits (directory-like records) do not validate and are skipped."""
    i = data.find(_SW_MARK)
    while i >= 4:
        crc, csz, usz, nl = struct.unpack_from("<IIII", data, i + 10)
        p0 = i + 26 + nl
        if nl < 1024 and p0 + csz <= len(data):
            try:
                out = zlib.decompress(data[p0:p0 + csz], -15)
            except zlib.error:
                out = None
            if out is not None and len(out) == usz and zlib.crc32(out) == crc:
                yield _nibble_swap(data[i + 26:p0]).decode("latin-1"), out
                i = data.find(_SW_MARK, p0 + csz)
                continue
        i = data.find(_SW_MARK, i + 1)


def _cfb_sections(data: bytes):
    """Streams of an OLE2 / Compound File Binary (MS-CFB), stdlib only."""
    end, free = 0xFFFFFFFE, 0xFFFFFFFF
    ss = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mss = 1 << struct.unpack_from("<H", data, 0x20)[0]
    nfat, dir0, _, cutoff, mfat0, _, difat0, ndifat = struct.unpack_from("<8I", data, 0x2C)
    per = ss // 4

    def sector(n):
        return data[(n + 1) * ss:(n + 2) * ss]

    difat = list(struct.unpack_from("<109I", data, 0x4C))
    n = difat0
    while n not in (end, free) and ndifat:
        v = struct.unpack(f"<{per}I", sector(n))
        difat += v[:-1]
        n, ndifat = v[-1], ndifat - 1
    fat = []
    for s in difat[:nfat]:
        fat += struct.unpack(f"<{per}I", sector(s))

    def chain(n):
        seen = 0
        while n not in (end, free) and n < len(fat) and seen <= len(fat):
            yield n
            n, seen = fat[n], seen + 1

    def read(n):
        return b"".join(sector(s) for s in chain(n))

    raw = read(dir0)
    ents = []
    for k in range(len(raw) // 128):
        e = raw[k * 128:(k + 1) * 128]
        nl = struct.unpack_from("<H", e, 64)[0]
        left, right, child = struct.unpack_from("<iii", e, 68)
        start, size = struct.unpack_from("<IQ", e, 116)
        ents.append((e[:max(nl - 2, 0)].decode("utf-16-le", "replace"), e[66],
                     left, right, child, start, size))
    mini = read(ents[0][5])
    minifat = []
    for s in chain(mfat0):
        minifat += struct.unpack(f"<{per}I", sector(s))

    def read_mini(n, size):
        out = []
        while n not in (end, free) and n < len(minifat) and len(out) * mss < size:
            out.append(mini[n * mss:(n + 1) * mss])
            n = minifat[n]
        return b"".join(out)[:size]

    found = []

    def walk(i, path, depth=0):
        if i < 0 or i >= len(ents) or depth > 64:
            return
        name, typ, left, right, child, start, size = ents[i]
        walk(left, path, depth + 1)
        if typ == 2:      # stream
            body = read_mini(start, size) if size < cutoff else read(start)[:size]
            found.append(("/".join(path + [name]), body))
        elif typ == 1:    # storage
            walk(child, path + [name], depth + 1)
        walk(right, path, depth + 1)

    walk(ents[0][4], [])
    return found


def inflate_chunks(blob: bytes) -> list[bytes]:
    """Every ZLB chunk of a section: [tag 16][uncompressed 4][compressed 4][zlib ...]."""
    out, i = [], blob.find(ZLB_TAG)
    while i >= 0:
        usz, csz = struct.unpack_from("<II", blob, i + 16)
        try:
            body = zlib.decompress(blob[i + 24:i + 24 + csz])
        except zlib.error:
            body = None
        if body is not None and len(body) == usz:
            out.append(body)
            i = blob.find(ZLB_TAG, i + 24 + csz)
        else:
            i = blob.find(ZLB_TAG, i + 1)
    return out


def read_sections(src) -> dict[str, bytes]:
    """{section name: bytes} for a .sldprt / .sldasm path or its bytes. CFB streams named
    ``*__ZLB`` are unwrapped and listed without the suffix, so names match across
    containers. A name that occurs twice keeps its last copy."""
    data = Path(src).read_bytes() if isinstance(src, (str, Path)) else bytes(src)
    pairs = _cfb_sections(data) if container(data) == "cfb" else _sw2015_sections(data)
    out = {}
    for name, body in pairs:
        if name.endswith("__ZLB"):
            name, body = name[:-5], b"".join(inflate_chunks(body))
        out[name] = body
    return out


# ----------------------------------------------------------------------------------------
# Parasolid B-rep
# ----------------------------------------------------------------------------------------

@dataclass
class Transmit:
    """One Parasolid transmit stream ('partition' or 'deltas'), neutral binary."""
    kind: str
    modeller: int      # Parasolid version, e.g. 3501256 = 35.1.256
    schema: str        # e.g. SCH_3501256_35102_13006 (base schema 13006 + embedded delta)
    data: bytes


_PS_HEADER = re.compile(rb"^PS\x00\x00\x00.: TRANSMIT FILE \((\w+)\) created by modeller "
                        rb"version (\d+)\x00\x00\x00.(SCH_[0-9_]+)", re.S)


def configurations(sections: dict) -> list[int]:
    return sorted(int(m.group(1)) for k in sections
                  if (m := re.fullmatch(r"Contents/Config-(\d+)-Partition", k)))


def parasolid(sections: dict, config: int = 0) -> list[Transmit]:
    """Parasolid transmit streams of a configuration: the partition first, then deltas."""
    blob = sections.get(f"Contents/Config-{config}-Partition")
    if blob is None:
        raise SldprtError(f"No Parasolid partition for configuration {config} "
                          f"(available: {configurations(sections)})")
    out = []
    for body in inflate_chunks(blob):
        m = _PS_HEADER.match(body)
        if m:
            out.append(Transmit(m.group(1).decode(), int(m.group(2)), m.group(3).decode(), body))
    if not out:
        raise SldprtError("Partition section holds no Parasolid transmit stream")
    return out


def write_parasolid(src, out_path, config: int = 0) -> list[str]:
    """Write the partition as out_path (.xmp_bin, Parasolid's partition extension) and the
    deltas next to it (.xmd_bin). Returns the written paths."""
    out_path, written = Path(out_path), []
    for t in parasolid(read_sections(src), config):
        p = out_path if t.kind == "partition" else out_path.with_suffix(".xmd_bin")
        p.write_bytes(t.data)
        written.append(str(p))
    return written


# ----------------------------------------------------------------------------------------
# Display-list tessellation -> triangle mesh
# ----------------------------------------------------------------------------------------

@dataclass
class Mesh:
    points: np.ndarray      # (N, 3) mm
    triangles: np.ndarray   # (M, 3) indices into points, outward by the stored normals
    face: np.ndarray        # (M,) face record index of each triangle

    @property
    def n_faces(self) -> int:
        return int(self.face.max()) + 1 if len(self.face) else 0

    def volume(self) -> float:
        a, b, c = (self.points[self.triangles[:, k]] for k in range(3))
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)

    def area(self) -> float:
        a, b, c = (self.points[self.triangles[:, k]] for k in range(3))
        return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2.0)

    def bounds(self):
        return self.points.min(axis=0), self.points.max(axis=0)


# Typed array as serialised in the display list: [element size][type][2][count][data]
_VEC3_HDR = struct.pack("<III", 12, 0x64, 2)
_INT_HDR = struct.pack("<III", 4, 0x08, 2)


def _face_records(d: bytes):
    """(strip lengths, vertices, normals) of every face tessellation record. A record is an
    int32 array of triangle-strip lengths followed by two float32 xyz arrays (positions in
    metres, unit normals) whose length is the sum of the strip lengths."""
    i = d.find(_VEC3_HDR)
    while i >= 0:
        n = struct.unpack_from("<I", d, i + 12)[0]
        j = i + 16 + 12 * n                       # expected normals header
        if 3 <= n and d[j:j + 12] == _VEC3_HDR and struct.unpack_from("<I", d, j + 12)[0] == n:
            for k in range(1, n // 3 + 1):          # strip-count array ends right before i
                h = i - 16 - 4 * k
                if h < 0:
                    break
                if d[h:h + 12] == _INT_HDR and struct.unpack_from("<I", d, h + 12)[0] == k:
                    strips = np.frombuffer(d, "<i4", k, h + 16)
                    if strips.min() >= 3 and int(strips.sum()) == n:
                        pts = np.frombuffer(d, "<f4", 3 * n, i + 16).reshape(n, 3)
                        nrm = np.frombuffer(d, "<f4", 3 * n, j + 16).reshape(n, 3)
                        yield strips, pts, nrm
                        break
            i = d.find(_VEC3_HDR, j + 16 + 12 * n)
            continue
        i = d.find(_VEC3_HDR, i + 1)


def display_mesh(sections: dict, weld: float = 1e-4) -> Mesh:
    """Triangle mesh (mm) of the stored display lists. Vertices closer than weld mm are
    merged so faces share their boundary vertices."""
    d = sections.get("Contents/DisplayLists")
    if not d:
        raise SldprtError("File has no display lists (saved without tessellation?)")
    pts_all, tris_all, face_all, base = [], [], [], 0
    for fi, (strips, pts, nrm) in enumerate(_face_records(d)):
        tri = []
        s = 0
        for L in strips:
            for t in range(int(L) - 2):
                a, b, c = s + t, s + t + 1, s + t + 2
                tri.append((a, b, c) if t % 2 == 0 else (b, a, c))
            s += int(L)
        tri = np.asarray(tri, dtype=np.int64)
        p = pts.astype(np.float64) * 1000.0
        geo = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
        ref = nrm[tri].sum(axis=1)
        flip = np.einsum("ij,ij->i", geo, ref) < 0
        tri[flip] = tri[flip][:, [1, 0, 2]]
        pts_all.append(p)
        tris_all.append(tri + base)
        face_all.append(np.full(len(tri), fi))
        base += len(p)
    if not pts_all:
        raise SldprtError("No face tessellation found in the display lists")
    pts = np.vstack(pts_all)
    tris = np.vstack(tris_all)
    if weld > 0:
        key = np.round(pts / weld).astype(np.int64)
        _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        pts, tris = pts[first], inv.reshape(-1)[tris]
    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    return Mesh(pts, tris[keep], np.concatenate(face_all)[keep])


def write_stl(mesh: Mesh, path, name: str = "sldprt") -> None:
    """Binary STL in mm."""
    p = mesh.points[mesh.triangles].astype(np.float32)              # (M, 3, 3)
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    rec = np.zeros(len(p), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, p
    with open(path, "wb") as f:
        f.write(name.encode()[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(p)))
        f.write(rec.tobytes())


# ----------------------------------------------------------------------------------------
# MFC CArchive primitives (Contents/Config-N*, SwDocContentMgr/SwDocContentMgrInfo)
# ----------------------------------------------------------------------------------------

_CSTR = b"\xff\xfe\xff"
_CSTR_AT = re.compile(b"(?=\xff\xfe\xff)")
_NEW_CLASS = re.compile(rb"\xff\xff(..)(..)([A-Za-z_][A-Za-z0-9_]+)", re.S)
_NO_ID = 0xFFFFFFFF


def _u16(b, i):
    return struct.unpack_from("<H", b, i)[0]


def _u32(b, i):
    return struct.unpack_from("<I", b, i)[0]


def _sections(src) -> dict:
    return src if isinstance(src, dict) else read_sections(src)


def mfc_string(b: bytes, i: int):
    """The MFC ``CString`` at offset i as (text, end offset), or None. Layout: ``FF FE FF``,
    a u8 length (``FF`` + u16, then ``FFFF`` + u32, for long ones), UTF-16LE text."""
    if b[i:i + 3] != _CSTR or i + 4 > len(b):
        return None
    j = i + 4
    n = b[i + 3]
    if n == 0xFF:
        if j + 2 > len(b):
            return None
        n, j = _u16(b, j), j + 2
        if n == 0xFFFF:
            if j + 4 > len(b):
                return None
            n, j = _u32(b, j), j + 4
    raw = b[j:j + 2 * n]
    if len(raw) != 2 * n:
        return None
    try:
        return raw.decode("utf-16-le"), j + 2 * n
    except UnicodeDecodeError:
        return None


def _new_classes(b: bytes) -> dict:
    """{offset: (class name, end offset)} of every MFC new-class header
    ``FF FF <schema u16> <length u16> <ASCII class name>``."""
    out = {}
    for m in _NEW_CLASS.finditer(b):
        n = int.from_bytes(m.group(2), "little")
        if 2 < n <= len(m.group(3)):
            out[m.start()] = (m.group(3)[:n].decode("ascii"), m.start() + 6 + n)
    return out


def _mfc_tokens(b: bytes) -> list:
    """Class headers ('C', offset, name, end) and strings ('S', offset, text, end), in
    stream order."""
    classes = _new_classes(b)
    out, i, n = [], 0, len(b)
    while i < n - 3:
        hit = classes.get(i)
        if hit is not None:
            out.append(("C", i, hit[0], hit[1]))
            i = hit[1]
            continue
        if b[i] == 0xFF and b[i + 1] == 0xFE and b[i + 2] == 0xFF:
            s = mfc_string(b, i)
            if s is not None:
                out.append(("S", i, s[0], s[1]))
                i = s[1]
                continue
        i += 1
    return out


def _mfc_indices(b: bytes, classes: dict):
    """Simulate CArchive's load map over a ResolvedFeatures stream. Returns
    ({class index: class name}, {offset of an object's tag: object index}). The stream
    continues the archive of Contents/Config-N, so the count starts at the stream's first
    u32. A new-class header takes two indices (class, object), a class tag ``0x80xx`` one.
    Data bytes that look like tags make the count drift by a few in some files, so callers
    anchor or validate what they take from it."""
    start = _u32(b, 0) if len(b) >= 4 else 0
    count, cls, obj = start, {}, {}
    i, n = 4, len(b)
    while i < n - 1:
        hit = classes.get(i)
        if hit is not None:
            cls[count] = hit[0]
            obj[i] = count + 1
            count += 2
            i = hit[1]
            continue
        if b[i] == 0xFF and b[i + 1] == 0xFE and i + 2 < n and b[i + 2] == 0xFF:
            s = mfc_string(b, i)
            if s is not None:
                i = s[1]
                continue
        hi = b[i + 1]
        if hi & 0x80 and hi != 0xFF:
            v = (b[i] | (hi << 8)) & 0x7FFF
            if v in cls or 0 < v < start:
                obj[i] = count
                count += 1
                i += 2
                continue
        i += 1
    return cls, obj


# ----------------------------------------------------------------------------------------
# Feature records of Contents/Config-N-ResolvedFeatures
# ----------------------------------------------------------------------------------------

@dataclass
class _Record:
    start: int              # offset of the object's class tag / new-class header
    pos: int                # offset of the name CString
    name: str
    new_class: str | None   # class introduced by this record's own header
    class_ref: int | None   # or the index of an earlier class (tag 0x8000 | k)
    flag: int               # 0x40; 0xC0 on default planes, origin and, once a part has
                            # several configurations, on many user features too
    id: int                 # = IFeature.GetID, = KeyWords id
    end: int                # offset just after the id


def _records(b: bytes, classes: dict) -> list:
    """Every feature-like object: [class tag][0x80xx][CString name][7 bytes][u8 flag][u32 id].
    The class tag is a new-class header ending right before the 0x80xx word, or a class
    reference 0x8000|k (big form 7FFF + u32 0x80000000|k)."""
    ends = {end: (start, name) for start, (name, end) in classes.items()}
    out = []
    for m in _CSTR_AT.finditer(b):
        i = m.start()
        if i < 4:
            continue
        s = mfc_string(b, i)
        if s is None:
            continue
        name, name_end = s
        if name_end + 12 > len(b) or _u16(b, i - 2) & 0xFF00 != 0x8000:
            continue
        if i - 2 in ends:
            start, new = ends[i - 2]
            ref = None
        else:
            t = _u16(b, i - 4)
            if t == 0x7FFF:
                continue
            if t & 0x8000:
                start, new, ref = i - 4, None, t & 0x7FFF
            elif i >= 8 and _u16(b, i - 8) == 0x7FFF and _u32(b, i - 6) & 0x80000000:
                start, new, ref = i - 8, None, _u32(b, i - 6) & 0x7FFFFFFF
            else:
                continue
        out.append(_Record(start, i, name, new, ref, b[name_end + 7], _u32(b, name_end + 8),
                           name_end + 12))
    return out


@dataclass
class _Tail:
    n_children: int = 0
    refs: list = field(default_factory=list)   # object indices of children stored earlier
    inline_at: int | None = None               # offset of the first child stored inline
    suppressed: bool | None = None


def _tail(b: bytes, r: _Record) -> _Tail:
    """The fields after the id: u32 0, an empty CString, 6 zero bytes, u16 number of absorbed
    children, one object reference per child, u32, u32 suppressed (0/1). A child stored
    earlier (the profile sketch of an extrude) is a 2-byte back reference; a child stored
    inline (Hole Wizard sketches) is a whole object of unknown length, after which the
    owner's own fields cannot be found at a fixed offset."""
    e, t = r.end, _Tail()
    if e + 24 > len(b) or b[e + 4:e + 8] != b"\xff\xfe\xff\x00":
        return t
    t.n_children = _u16(b, e + 14)
    j = e + 16
    for _ in range(t.n_children):
        if j + 2 > len(b):
            return t
        w = _u16(b, j)
        if w == 0x7FFF:                              # big tag: u32 object or class index
            if j + 6 > len(b):
                return t
            v = _u32(b, j + 2)
            if v & 0x80000000:
                t.inline_at = j
                return t
            t.refs.append(v)
            j += 6
        elif w & 0x8000:                             # class tag or new class: inline object
            t.inline_at = j
            return t
        else:
            if w:
                t.refs.append(w)
            j += 2
    if j + 8 <= len(b):
        v = _u32(b, j + 4)
        t.suppressed = bool(v) if v in (0, 1) else None
    return t


# KeyWords (element tag, display Type) -> MFC classes seen with it, with how often (from the
# exact class headers of 1,781 files on this PC). Resolves class references k: every record
# that uses k votes with its KeyWords type. Format: tag|type|class[:count] class[:count]...
_KW_TABLE = """
Cut|Cut-Extrude|moCut_c:29
Cut||moCut_c:139 moSketchHole:4
Extrusion|Boss-Extrude|moExtrusion_c:435 moBoss_c:25 moICE_c:11
Extrusion|Cut-Extrude|moICE_c:22
Extrusion|Cut-Extrude-Thin|moCutThin_c
Extrusion|Extrude-Thin|moExtruThin_c:6 moBossThin_c:3
Extrusion||moICE_c:139 moBoss_c:120 moBossThin_c:6 moGusset_c:2 moCutThin_c:2
Feature|3DPoint|moRefPoint_c:36
Feature|ACPoint|moAssemblyConnectionPoint_c
Feature|ARPoint|moAssemblyRoutePoint_c
Feature|AlignGroup|moAlignGroup_c
Feature|Annotations|moDetailCabinet_c:1777
Feature|Attribute|moAttribute_c:142
Feature|Axes|moRefAxisFtrFolder_c:19
Feature|Axis|moRefAxis_c:120
Feature|Base-Flange|moSMBaseFlange_c:24
Feature|BaseBend|moSketchBend_c:5
Feature|Body-Delete/Keep |moDeleteBody_c:16
Feature|Body-Move/Copy|moMoveCopyBody_c:16
Feature|Boundary|moNetBlend_c:4
Feature|Boundary-Surface|moNetBlend_c:7
Feature|Break-Corner|moBreakCornerFeat_c
Feature|CPoint|moConnectionPoint_c:3
Feature|Cavity|moCavity_c:2
Feature|Chamfer|Chamfer_c:66
Feature|CirPattern|moCirPattern_c:59
Feature|Closed Corner|moCornerFeat_c
Feature|Coincident|moMateCoincident:4
Feature|Combine|moCombineBodies_c:11
Feature|Comments|moCommentsFolder_c:1778
Feature|CompCurve|moCompositeCurve_c:15
Feature|Concentric|moMateConcentric
Feature|Configuration Comments|moConfigCommentsFolder_c:22
Feature|Configuration Table|moNativeConfigurationTableFeature_c:813
Feature|Convert-Solid|moSolidToSheetMetal_c:3
Feature|Coordinate System|moCoordSys_c:14
Feature|Cosmetic Thread|moCosmeticThread_c:33
Feature|Curve|moRefCurve_c:5 moCurveInFile_c:2
Feature|Curves|moCurveFtrFolder_c:7
Feature|Cut list|moSolidBodyFolder_c:48 moFeatSolidBodyFolder_c:2
Feature|Cut-List-Item|moCutListFolder_c:12
Feature|Cut-Revolve|moRevCut_c:39
Feature|Cut-Sweep|moSweepCut_c:11
Feature|Deform|moDeform_c
Feature|DeleteFace|moDelFace_c:10
Feature|DerivedCirPattern|moDerivedCirPattern_c
Feature|DerivedLPattern|moDerivedLPattern_c
Feature|Design Binder|moDocsFolder_c:1777
Feature|Detail Folder|moDetailFolder_c:67 moInkMarkupView_c:4
Feature|Distance|moMateDistanceDim_c:4
Feature|Dome|moDome_c:4
Feature|Draft|moDraft_c:16
Feature|Edge-Flange|moEdgeFlange_c:7
Feature|EdgeBend|moOneBend_c:6
Feature|End cap|moEndCap_c:2
Feature|Equations|moEqnFolder_c:1778
Feature|Excel Design Table|moDesignTableFeature_c:5
Feature|Favorites|moFavoriteFolder_c:1778
Feature|Features|moSmartComponentFeatFolder_c
Feature|Fillet|Fillet_c:174
Feature|Fillet Bead|moWeldBeadFeat_c:2
Feature|Flat-Pattern|moFlatPattern_c:29 moTemplateFlatPattern_c:5
Feature|Flatten-Bends|moFlattenBends_c:3
Feature|Flex|moBending_c
Feature|Fold|moFold_c
Feature|Folder|moFtrFolder_c:801
Feature|Force|moAEMLinearForce:2
Feature|Freeze***|moLockFeature_c
Feature|Gauge Table|moSMEnvTableFeat_c
Feature|Graphics Bodies|moFeatGraphicsBodyFolder_c:7
Feature|Gravity|moAEMGravity
Feature|Helix/Spiral|moHelix_c:6
Feature|Hem|moHem_c:5
Feature|HemBend|moOneBend_c:3
Feature|History|moHistoryFolder_c:1777
Feature|Hole Series|moHoleSeries_c:2
Feature|Hole Thread|moCosmeticThread_c:9
Feature|Imported|moBaseBody_c:785
Feature|InContextFeature|moInContextFeatHolder_c:15
Feature|Indent|moPunch_c:4
Feature|Intersect|moSculpt_c:2
Feature|JP|moBranchPoint_c:2
Feature|Jog|moJog_c
Feature|LPattern|moLPattern_c:36
Feature|LibFeat|moLibraryFeature_c
Feature|LinearMotor|moAEMLinearMotor:3
Feature|LinearSpring|moAEMLinearSpring
Feature|Live Section Planes|moLiveSectionFolder_c:240
Feature|LocalCirPattern|moLocalCirPattern_c:5
Feature|LocalLPattern|moLocalLPattern_c:6
Feature|Loft|moBlend_c:18
Feature|Markups|moInkMarkupFolder_c:1765
Feature|Mates|moMateGroup_c:260
Feature|Measure|moMeasurement_c:2
Feature|Measurement|moMeasurementFolder_c:2
Feature|MidSurface|moMidSurface_c:2
Feature|Mirror|moMirrorPattern_c:49 moMirrorSolid_c:21
Feature|MirrorBend|moOneBend_c
Feature|MirrorComponent|moMirrorCompFeat_c:3
Feature|Miter Flange|moSMMiteredFlange_c:2
Feature|Move Face|moMoveFace_c
Feature|Multi_CAD_Imported|moMBimport_c:7
Feature|Notes|moNotesAreaFtrFolder_c:1765
Feature|PLANE|moRefPlane_c:258
Feature|Planar Section|moPlaneSection_c:2
Feature|Planar Sections|moPlaneSectionFolder_c:2
Feature|Plane|moRefPlane_c:1505
Feature|Planes|moRefPlaneFtrFolder_c:19
Feature|Plot|moSimPlotFeature_c:3
Feature|Point|moRefPointFtrFolder_c:2
Feature|Process-Bends|moProcessBends_c:3
Feature|RPoint|moRoutePoint_c
Feature|References|moSmartComponentRefFolder_c
Feature|Results|moSimResultFolder_c:5
Feature|Revolve|moRevolution_c:111
Feature|Revolve-Thin|moRevolutionThin_c:3
Feature|Rib|moRib_c:8
Feature|RotaryMotor|moAEMRotationalMotor:2
Feature|RoundBend|moOneBend_c
Feature|Route|moFabricatedRoute_c:6
Feature|Ruled Surface|moRuledSrfFromEdge_c:3
Feature|SOLIDWORKS Materials|moMaterialFolder_c:1508
Feature|Scale|moScale_c:55
Feature|Selection Sets|moSelectionSetFolder_c:1765
Feature|Sensor|moSensor_c
Feature|Sensors|moSensorFolder_c:1778 moSensor_c:2
Feature|SharpBend|moOneBend_c:4
Feature|Sheet-Metal|moSheetMetal_c:30 moTemplateSheetMetal_c:5
Feature|Shell|moShell_c:55
Feature|Simulation|moAEMSimFeature_c:82
Feature|Sketch Picture|moSketchBitmap_c:4
Feature|Sketch Transformation|moFoldUpDownSketchFolder_c:28
Feature|Sketch-Pattern|moSketchPattern_c
Feature|SketchBend|moOneBend_c
Feature|Sketched Bend|moSM3dBend_c:3
Feature|Sketches|moProfileFtrFolder_c:19
Feature|Smart Feature|moSmartComponentFeature_c
Feature|Solid Bodies|moSolidBodyFolder_c:1470 moFeatSolidBodyFolder_c:17
Feature|Solid Body Contact|moAEM3DContact:3
Feature|SolidWorks Materials|moMaterialFolder_c:10
Feature|Split|moSplit_c:14
Feature|Split Line|moPLine_c:79
Feature|Stock-Base-|moSavedExtBody_c:3
Feature|Structural Member|moWeldMemberFeat_c:17
Feature|Sub-weldment|moSubWeldFolder_c:2
Feature|Surface Bodies|moSurfaceBodyFolder_c:1518 moFeatSurfaceBodyFolder_c:19
Feature|Surface-Extend|moExtendRefSurface_c:3
Feature|Surface-Extrude|moExtruRefSurface_c:37
Feature|Surface-Fill|moFillRefSurface_c:12
Feature|Surface-Imported|moRefSurface_c:4
Feature|Surface-Knit|moSewRefSurface_c:11
Feature|Surface-Loft|moBlendRefSurface_c:4
Feature|Surface-MidSurface|moMidRefSurface_c:2
Feature|Surface-Offset|moOffsetRefSurface_c:10
Feature|Surface-Plane|moPlanarSurface_c:70
Feature|Surface-Revolve|moRevolvRefSurf_c:8
Feature|Surface-Sweep|moSweepRefSurface_c:6
Feature|Surface-Trim|moTrimRefSurface_c:15
Feature|SurfaceCut|moSurfCut_c:5
Feature|Sweep|moSweep_c:30
Feature|TPattern|moTablePattern_c
Feature|Tables|moConfigTableFolder_c:813
Feature|Thicken|moThicken_c:7
Feature|Time|moSimPlotXAxisFeature_c:3
Feature|Trim/Extend|moWeldCornerFeat_c:5
Feature|Unfold|moUnFold_c
Feature|VarFillet|VarFillet_c:2
Feature|VarPattern|moDimPattern_c
Feature|Weld Bead|moCosmeticWeldBead_c
Feature|Weld Folder|moCosmeticWeldCutList_c
Feature|Weld Sub Folder|moCosmeticWeldSubFolder_c
Feature|Weldment|moWeldmentFeature_c:18
Feature|Workflow Sensitive|moSensor_c:52
HoleWizard||moHoleWzd_c:58
Sketch|3DSketch|mo3DProfileFeature_c:59
Sketch||moProfileFeature_c:720 mo3DProfileFeature_c:31
Sketch|Origin|moOriginProfileFeature_c:1778
Sketch|Sketch|moProfileFeature_c:39
"""


def _parse_kw_table(text: str) -> dict:
    out = {}
    for line in text.strip("\n").splitlines():
        tag, kind, classes = line.split("|")
        votes = {}
        for c in classes.split():
            name, _, n = c.partition(":")
            votes[name] = int(n or 1)
        out[(tag, kind or None)] = votes
    return out


_KW_CLASSES = _parse_kw_table(_KW_TABLE)


def _kw_classes(tag, kind) -> dict:
    hit = _KW_CLASSES.get((tag, kind))
    if hit:
        return hit
    if tag == "HoleWizard":                     # Type is the hole's own description
        return {"moHoleWzd_c": 1}
    if kind and kind.startswith("Flatten-<"):
        return {"moUiBend_c": 1}
    if kind and kind.endswith("_Mirrored"):
        return {"moMirrorStock_c": 1}
    return {}


# GetTypeName2 is the class name without 'mo' and '_c' (checked against the SolidWorks API
# list), except for these
_TYPE_NAME_EXCEPTIONS = {"moOffsetRefSurface_c": "OffsetRefSuface"}
# a body without feature history ('Imported', 3D Interconnect)
DUMB_SOLID_CLASSES = frozenset({"moBaseBody_c", "moMBimport_c"})
_SKETCH_CLASSES = frozenset({"moProfileFeature_c", "mo3DProfileFeature_c"})


def type_name(mfc_class: str | None) -> str | None:
    """IFeature.GetTypeName2 of a feature saved with this MFC class (moExtrusion_c ->
    'Extrusion', Fillet_c -> 'Fillet', moMateCoincident -> 'MateCoincident')."""
    if not mfc_class:
        return None
    if mfc_class in _TYPE_NAME_EXCEPTIONS:
        return _TYPE_NAME_EXCEPTIONS[mfc_class]
    n = mfc_class
    if n.startswith("mo") and len(n) > 2 and (n[2].isupper() or n[2].isdigit()):
        n = n[2:]
    return n[:-2] if n.endswith("_c") else n


# ----------------------------------------------------------------------------------------
# swXmlContents/KeyWords (SolidWorks' search index of the model)
# ----------------------------------------------------------------------------------------

@dataclass
class KwItem:
    """One item of swXmlContents/KeyWords."""
    id: int                     # feature id (IFeature.GetID)
    tag: str                    # Feature, Sketch, Extrusion, Cut, HoleWizard, Reference...
    name: str
    type: str | None            # display type: 'Boss-Extrude', 'Fillet', 'LPattern'...
    dims: dict = field(default_factory=dict)       # dimension name -> shown value
    children: list = field(default_factory=list)   # DissectableChildren feature ids


def keywords(src) -> dict:
    """{feature id: KwItem} from swXmlContents/KeyWords: plain XML after a 1-byte prefix.
    Dimension values are the displayed text in document units ('20', 'R6', '270°',
    '<MOD-DIAM>6.6'); :func:`dim_value` turns them into numbers."""
    b = _sections(src).get("swXmlContents/KeyWords")
    i = b.find(b"<?xml") if b else -1
    if i < 0:
        return {}
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(b[i:])
    except ET.ParseError:
        return {}
    out = {}
    for el in root:
        if el.tag == "Configuration" or not (el.get("id") or "").isdigit():
            continue
        fid = int(el.get("id"))
        out[fid] = KwItem(fid, el.tag, el.get("Name", ""), el.get("Type"),
                          {d.get("Name", ""): (d.text or "") for d in el.findall("Dimension")},
                          [int(x) for x in re.findall(r"\d+", el.get("DissectableChildren", ""))])
    return out


_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def dim_value(text: str) -> float | None:
    """The number in a displayed dimension value: 'R6' -> 6, '<MOD-DIAM>6.6' -> 6.6,
    '270°' -> 270 (document units: mm, degrees), or None."""
    m = _NUMBER.search(re.sub(r"<[^>]*>", "", text or ""))
    return float(m.group()) if m else None


# ----------------------------------------------------------------------------------------
# Contents/3DExperienceExchange2: an UnQLite key/value file of MessagePack documents
# ----------------------------------------------------------------------------------------

class Repeated(list):
    """All values of a key that occurs more than once in one MessagePack map (SolidWorks
    writes 'class', 'suppressed', 'hidden' twice in feature documents, and one 'model' per
    configuration in the TOC). The last value is the current one."""


def _last(v):
    return v[-1] if isinstance(v, Repeated) and v else v


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def msgpack_decode(b: bytes, i: int = 0):
    """(value, end offset) of the MessagePack item at i. Maps keep repeated keys as
    :class:`Repeated`; ext types become (type, bytes)."""
    t = b[i]
    if t <= 0x7F:
        return t, i + 1
    if t >= 0xE0:
        return t - 256, i + 1
    if t <= 0x8F:
        return _mp_map(b, i + 1, t & 0x0F)
    if t <= 0x9F:
        return _mp_array(b, i + 1, t & 0x0F)
    if t <= 0xBF:
        n = t & 0x1F
        return _mp_str(b, i + 1, n)
    if t == 0xC0:
        return None, i + 1
    if t in (0xC2, 0xC3):
        return t == 0xC3, i + 1
    if t in (0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xD9, 0xDA, 0xDB):
        w = {0xC4: 1, 0xC5: 2, 0xC6: 4, 0xC7: 1, 0xC8: 2, 0xC9: 4, 0xD9: 1, 0xDA: 2, 0xDB: 4}[t]
        n, j = int.from_bytes(_take(b, i + 1, w), "big"), i + 1 + w
        if t >= 0xD9:
            return _mp_str(b, j, n)
        if t >= 0xC7:                                    # ext 8/16/32: type byte, data
            return (_take(b, j, 1)[0], _take(b, j + 1, n)), j + 1 + n
        return _take(b, j, n), j + n
    if t == 0xCA:
        return struct.unpack(">f", _take(b, i + 1, 4))[0], i + 5
    if t == 0xCB:
        return struct.unpack(">d", _take(b, i + 1, 8))[0], i + 9
    if 0xCC <= t <= 0xD3:
        w = 1 << ((t - 0xCC) & 3)
        return int.from_bytes(_take(b, i + 1, w), "big", signed=t >= 0xD0), i + 1 + w
    if 0xD4 <= t <= 0xD8:                                # fixext 1, 2, 4, 8, 16
        n = 1 << (t - 0xD4)
        return (_take(b, i + 1, 1)[0], _take(b, i + 2, n)), i + 2 + n
    if t in (0xDC, 0xDD, 0xDE, 0xDF):
        w = 2 if t in (0xDC, 0xDE) else 4
        n = int.from_bytes(_take(b, i + 1, w), "big")
        return (_mp_array if t <= 0xDD else _mp_map)(b, i + 1 + w, n)
    raise SldprtError(f"MessagePack type {t:#x} at {i}")


def _take(b, i, n):
    out = b[i:i + n]
    if len(out) != n:
        raise SldprtError("truncated MessagePack data")
    return out


def _mp_str(b, i, n):
    return _take(b, i, n).decode("utf-8", "replace"), i + n


def _mp_map(b, i, n):
    d = {}
    for _ in range(n):
        k, i = msgpack_decode(b, i)
        v, i = msgpack_decode(b, i)
        if isinstance(k, (dict, list, bytes, tuple)):
            k = str(k)
        if k in d:
            if not isinstance(d[k], Repeated):
                d[k] = Repeated([d[k]])
            d[k].append(v)
        else:
            d[k] = v
    return d, i


def _mp_array(b, i, n):
    a = []
    for _ in range(n):
        v, i = msgpack_decode(b, i)
        a.append(v)
    return a, i


def unqlite_cells(db: bytes) -> list:
    """[(key, value)] of every live record of an UnQLite database ('lhash' engine): values
    MessagePack-decoded, None where that fails. Page 0 is the header ('unqlite', magic,
    time, sector size, page size, engine name). Each bucket page starts with the u16 offset
    of its first cell; a cell is u32 hash, u32 key length, u64 data length, u16 next cell
    offset, u64 first overflow page, then key and data (big-endian). Overflow pages hold
    the key and data with an 18-byte header on the first page and 8 bytes on the next."""
    if not db.startswith(b"unqlite"):
        raise SldprtError("Not an UnQLite database")
    page = struct.unpack_from(">I", db, 19)[0] if len(db) >= 23 else 0
    if page < 512 or page > 65536 or page & (page - 1):
        page = 4096
    npages = len(db) // page
    out = []
    for pg in range(1, npages):
        base = pg * page
        off = struct.unpack_from(">H", db, base)[0]
        guard = 0
        while off and 12 <= off < page - 26 and guard < page:
            guard += 1
            a = base + off
            nkey, ndata, inext, iovfl = struct.unpack_from(">IQHQ", db, a + 4)
            if not (1 <= nkey <= 256 and ndata < 50_000_000):
                break
            if iovfl == 0:
                kb = db[a + 26:a + 26 + nkey]
                data = db[a + 26 + nkey:a + 26 + nkey + ndata]
            else:
                payload, p, k = bytearray(), iovfl, 0
                while p and k < npages and len(payload) < nkey + ndata:
                    pb = db[p * page:(p + 1) * page]
                    if len(pb) < 18:
                        break
                    payload += pb[18:] if k == 0 else pb[8:]
                    p, k = struct.unpack_from(">Q", pb, 0)[0], k + 1
                kb, data = bytes(payload[:nkey]), bytes(payload[nkey:nkey + ndata])
            if len(data) != ndata:
                break
            try:
                v, end = msgpack_decode(data)
                v = v if end == ndata else None
            except (SldprtError, IndexError, struct.error, RecursionError):
                v = None
            out.append((kb.decode("latin-1"), v))
            off = inext
    return out


def exchange_docs(src) -> dict:
    """{key: document} of Contents/3DExperienceExchange2: a 'TOC' (per configuration, the
    GUIDs of its documents) and one document per reference plane, axis, point, coordinate
    system, Hole Wizard feature and mate group, with 'class', 'name', 'id' (feature id),
    'suppressed' and 'hidden'. Empty when the file has no such section."""
    db = _sections(src).get("Contents/3DExperienceExchange2")
    return dict(unqlite_cells(db)) if db and db.startswith(b"unqlite") else {}


def _toc(docs: dict) -> dict:
    """{configuration id: (configuration name, [document GUIDs])} from the TOC document."""
    toc = docs.get("TOC")
    out = {}
    if not isinstance(toc, dict):
        return out
    for m in _as_list(toc.get("model")):
        if not isinstance(m, dict):
            continue
        guids, stack = [], [m]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if isinstance(o.get("guid"), str):
                    guids.append(o["guid"])
                stack.extend(_as_list(o.get("object")))
            elif isinstance(o, list):
                stack.extend(o)
        out[_last(m.get("configid"))] = (_last(m.get("configname")), guids)
    return out


def _config_docs(docs: dict, config: int) -> list:
    """The documents of one configuration (all documents when there is no TOC)."""
    toc = _toc(docs)
    if config in toc:
        return [docs[g] for g in toc[config][1] if isinstance(docs.get(g), dict)]
    return [v for k, v in docs.items() if k != "TOC" and isinstance(v, dict)]


# ----------------------------------------------------------------------------------------
# Feature tree
# ----------------------------------------------------------------------------------------

@dataclass
class SwFeature:
    """One item of a SolidWorks FeatureManager tree, decoded from the saved file."""
    name: str
    id: int                          # = IFeature.GetID
    mfc_class: str | None            # e.g. moExtrusion_c, Fillet_c; None if unresolved
    type_name: str | None            # = IFeature.GetTypeName2 ('Extrusion', 'Fillet'...)
    suppressed: bool | None          # None where the file does not tell
    system: bool = False             # default folders, Front/Top/Right Plane, Origin
    parent: int | None = None        # id of the feature that absorbed this one
    children: list = field(default_factory=list)   # ids of absorbed features (sketches)
    hidden: bool | None = None       # reference geometry / Hole Wizard (3DExperience store)
    internal: bool = False           # stored inside its parent, made by it (Hole Wizard sketches)
    kw_tag: str | None = None        # KeyWords element (Feature, Sketch, Extrusion...)
    kw_type: str | None = None       # KeyWords display type ('Boss-Extrude', 'Fillet'...)
    dims: dict = field(default_factory=dict)       # dimension name -> shown value
    resolved_by: str = "unresolved"  # mfc-new | keywords | mfc-count | unresolved
    unlinked: int = 0                # absorbed children the file declares but the decoder
                                     # could not identify (so `children` is incomplete)

    @property
    def dumb_solid(self) -> bool:
        """An imported body without history (Imported / 3D Interconnect)."""
        return self.mfc_class in DUMB_SOLID_CLASSES


def feature_configurations(src) -> list:
    """Configuration numbers N that have a Contents/Config-N-ResolvedFeatures section."""
    return sorted(int(m.group(1)) for k in _sections(src)
                  if (m := re.fullmatch(r"Contents/Config-(\d+)-ResolvedFeatures", k)))


def _class_headers(classes) -> dict:
    """{class name: offset of its header in this stream} (the first, if a name repeats)."""
    out = {}
    for pos in sorted(classes):
        out.setdefault(classes[pos][0], pos)
    return out


def _resolve_classes(recs, kw, b, classes, sim=None) -> dict:
    """{class index k: (class name, how)} for the class references of the records.

    An archive gives each class ONE index, numbered in the order the class headers come.
    So a reference k at or above the stream's start count names a class whose header is in
    this stream before k is first used, and a smaller k a class whose header is not here
    (it is in Contents/Config-N). The candidates for k are the classes its records' KeyWords
    types allow (a set: how often a type goes with a class in other files only breaks
    ties). This stream's indices are then matched one-to-one to the headers in stream
    order (monotone), by how close each header's simulated count, corrected at the indices
    that have a single candidate, comes to k; a header whose count is exactly k but whose
    class the KeyWords type does not allow is taken only when nothing else keeps the order
    (KeyWords ids of a part with an inserted part can name another part's feature). A
    reference without a usable KeyWords type takes the header whose simulated count is
    exactly k ('mfc-count'), else stays unresolved."""
    headers = _class_headers(classes)
    groups = defaultdict(list)
    for r in recs:
        if r.class_ref is not None:
            groups[r.class_ref].append(r)
    start = _u32(b, 0) if len(b) >= 4 else 0
    _, sim_obj = sim or _mfc_indices(b, classes)
    sim_k = {c: sim_obj[p] - 1 for c, p in headers.items() if p in sim_obj}

    out, cands, votes_of, unvoted = {}, {}, {}, []
    for k, rs in groups.items():
        first = min(x.pos for x in rs)
        votes = Counter()
        for x in rs:
            item = kw.get(x.id)
            if item is not None:
                for c, n in _kw_classes(item.tag, item.type).items():
                    votes[c] += n
        if k >= start:
            here = [c for c in votes if c in sim_k and headers[c] < first]
        else:
            here = [c for c in votes if c not in headers] or list(votes)
        if not here:
            unvoted.append((k, first))
            continue
        votes_of[k], cands[k] = votes, here

    # the simulated count drifts (data bytes that look like tags, objects it misses):
    # correct it at the indices whose class is certain
    anchors = sorted((headers[c[0]], k - sim_k[c[0]]) for k, c in cands.items()
                     if k >= start and len(c) == 1)

    def est(c):
        p = headers[c]
        shift = next((d for q, d in reversed(anchors) if q <= p), anchors[0][1] if anchors else 0)
        return sim_k[c] + shift

    for k, first in unvoted:
        c = next((c for c in sim_k if headers[c] < first and sim_k[c] == k), None)
        out[k] = (c, "mfc-count") if c is not None else (None, "unresolved")
    taken = {c for c, _ in out.values() if c is not None}

    ks = sorted(k for k in cands if k >= start)
    first_use = {k: min(x.pos for x in groups[k]) for k in ks}
    opts = []
    for k in ks:
        voted = [c for c in cands[k] if c not in taken] or cands[k]
        counted = [c for c in sim_k if c not in voted and headers[c] < first_use[k]
                   and k in (sim_k[c], est(c))]
        opts.append(sorted(voted + counted, key=headers.get))
    # states: the class of the last matched header -> (total cost, matches so far); an index
    # may stay unmatched (at a large cost) when no header keeps the order, e.g. a stream that
    # holds a second archive (an inserted part)
    states = {None: (0.0, ())}
    for i, k in enumerate(ks):
        nxt = {}
        for last, (total, path) in states.items():
            for c in opts[i]:
                if last is not None and headers[c] <= headers[last]:
                    continue
                cost = total + abs(est(c) - k) - 1e-6 * votes_of[k][c] + (0 if c in votes_of[k] else 1e9)
                if c not in nxt or cost < nxt[c][0]:
                    nxt[c] = (cost, path + ((k, c),))
            if last not in nxt or total + 1e12 < nxt[last][0]:
                nxt[last] = (total + 1e12, path)
        states = nxt
    matched = dict(min(states.values(), key=lambda v: v[0])[1]) if states else {}
    for k, c in matched.items():
        out[k] = (c, "keywords" if c in votes_of[k] else "mfc-count")
    taken |= set(matched.values())
    for k in sorted((k for k in ks if k not in matched), key=lambda k: -votes_of[k].most_common(1)[0][1]):
        order = sorted(cands[k], key=lambda c: (c in taken, -votes_of[k][c]))
        taken.add(order[0])
        out[k] = (order[0], "keywords")
    taken |= {c for c, _ in out.values() if c is not None}
    # indices of Contents/Config-N: strongest vote first, one class per index
    for k in sorted((k for k in cands if k < start), key=lambda k: -votes_of[k].most_common(1)[0][1]):
        order = sorted(cands[k], key=lambda c: -votes_of[k][c])
        c = next((c for c in order if c not in taken), order[0])
        taken.add(c)
        out[k] = (c, "keywords")
    return out


def _object_anchors(kmap, b, classes) -> list:
    """[(offset, object index)] where the MFC object count is known exactly: the header of
    a class whose index k is resolved (the object it introduces is k + 1), increasing in
    both offset and index."""
    headers = _class_headers(classes)
    by_class = defaultdict(set)
    for k, (c, _how) in kmap.items():
        if c is not None:
            by_class[c].add(k)
    start = _u32(b, 0) if len(b) >= 4 else 0
    pts = sorted((headers[c], next(iter(ks)) + 1) for c, ks in by_class.items()
                 if len(ks) == 1 and c in headers and next(iter(ks)) >= start)
    out = []
    for p, o in pts:                       # drop anchors that break the order
        if not out or o > out[-1][1]:
            out.append((p, o))
    return out


def _link_back_refs(recs, tails, kmap, b, classes, cls_of, parent, kids, link, sim=None):
    """Link absorbed children stored earlier (an extrude's profile sketch, a loft's
    profiles, a sweep's path) to their owner. Each is a back reference to an MFC object
    index. The exact counts at :func:`_object_anchors` bound where that object can be in
    the stream; a reference is linked when one free sketch lies within its bounds (in the
    order of the owner's other references), or when the drift-corrected simulated count of
    one of several is less than half as far from it as the next one's. Checked against
    KeyWords' DissectableChildren on 924 SolidWorks 2024 sample parts: 1436 links agree,
    none differs, 189 are left to KeyWords (counted in SwFeature.unlinked when it lacks
    them too)."""
    anchors = _object_anchors(kmap, b, classes)
    _, sim = sim or _mfc_indices(b, classes)

    def bounds(o, owner):
        lo = max((p for p, a in anchors if a < o), default=-1)
        hi = min((p for p, a in anchors if a > o), default=len(b))
        at = next((p for p, a in anchors if a == o), None)
        return lo, min(hi, recs[owner].start), at

    def estimate(j):
        s = recs[j].start
        if s not in sim:
            return None
        shift = next((a - sim[p] for p, a in reversed(anchors) if p <= s and p in sim), 0)
        return sim[s] + shift

    for n, t in enumerate(tails):
        if not t.refs or len(kids[n]) >= t.n_children:
            continue
        pool = [j for j in range(n) if cls_of[j] in _SKETCH_CLASSES
                and (j not in parent or parent[j] == n)]
        refs = sorted(set(t.refs))
        options = {}
        for o in refs:
            lo, hi, at = bounds(o, n)
            options[o] = [j for j in pool if (recs[j].start == at if at is not None
                                              else lo < recs[j].start < hi)]
        done = {}
        progress = True
        while progress and len(kids[n]) < t.n_children:
            progress = False
            for o in refs:
                if o in done:
                    continue
                below = max((done[x] for x in done if x < o), default=-1)
                above = min((done[x] for x in done if x > o), default=n)
                opts = [j for j in options[o] if below < j < above and j not in done.values()]
                if len(opts) > 1:
                    gap = sorted((abs(e - o), j) for j in opts if (e := estimate(j)) is not None)
                    if len(gap) > 1 and 2 * gap[0][0] < gap[1][0]:
                        opts = [gap[0][1]]
                if len(opts) == 1:
                    done[o] = opts[0]
                    if parent.get(opts[0]) != n:
                        link(opts[0], n)
                    progress = True


def feature_tree(src, config: int = 0, user_only: bool = True) -> list:
    """The FeatureManager tree of configuration ``config`` in stream order, as SwFeature.

    ``src`` is a path, the file's bytes or :func:`read_sections` output. With
    ``user_only`` the default folders, planes and origin are left out. Stream order is tree
    order, with absorbed sketches just before their owner; Hole Wizard writes its two
    sketches after the hole. The classes come from the records' own class headers, else
    from the vote of their KeyWords types, else from counting the MFC map."""
    sections = _sections(src)
    b = sections.get(f"Contents/Config-{config}-ResolvedFeatures")
    if b is None:
        raise SldprtError(f"No feature tree for configuration {config} "
                          f"(available: {feature_configurations(sections)})")
    kw = keywords(sections)
    classes = _new_classes(b)
    recs = [r for r in _records(b, classes) if r.id != _NO_ID]
    sim = _mfc_indices(b, classes)
    kmap = _resolve_classes(recs, kw, b, classes, sim)
    tails = [_tail(b, r) for r in recs]
    docs = {_last(d.get("id")): d for d in _config_docs(exchange_docs(sections), config)
            if isinstance(_last(d.get("id")), int)}
    parent, kids, inner = {}, defaultdict(list), set()
    by_id = {r.id: n for n, r in enumerate(recs)}

    def link(child, owner):
        if child != owner and child not in parent and recs[child].id < recs[owner].id:
            parent[child] = owner
            kids[owner].append(child)

    def walk(n):                     # children serialised inside their owner follow it
        j = n + 1
        t = tails[n]
        if t.inline_at is not None and j < len(recs) and recs[j].start == t.inline_at:
            for _ in range(t.n_children - len(t.refs)):
                if j >= len(recs) or recs[j].id >= recs[n].id:
                    break
                link(j, n)
                if parent.get(j) == n:
                    inner.add(j)
                j = walk(j)
        return j

    n = 0
    while n < len(recs):
        n = walk(n)
    for item in kw.values():
        for cid in item.children:
            if item.id in by_id and cid in by_id:
                link(by_id[cid], by_id[item.id])
    cls_of = [r.new_class or kmap.get(r.class_ref, (None,))[0] for r in recs]
    # Supported 2024 archives have an exact object map. Counting tags heuristically can
    # misidentify repeated extrusion classes after newly written sketches/dimensions.
    from . import mfc
    try:
        if f'Contents/Config-{config}' not in sections:
            raise mfc.MfcError('Configuration stream absent')
        arc = mfc.load_archive(sections, config)
        exact = {f.values['name'].values['id']: f for f in arc.features}
    except mfc.MfcError:
        exact = {}
    for n, r in enumerate(recs):
        if r.id in exact:
            cls_of[n] = exact[r.id].name
    if any(t.refs and len(kids[n]) < t.n_children for n, t in enumerate(tails)):
        _link_back_refs(recs, tails, kmap, b, classes, cls_of, parent, kids, link, sim)
    origin = next((n for n, c in enumerate(cls_of) if c == "moOriginProfileFeature_c"), None)
    out = []
    for n, r in enumerate(recs):
        cls, how = (r.new_class, "mfc-new") if r.new_class else kmap.get(r.class_ref, (None, "unresolved"))
        if r.id in exact:
            cls, how = exact[r.id].name, 'mfc-exact'
        t, item, doc = tails[n], kw.get(r.id), docs.get(r.id)
        suppressed = t.suppressed if t.inline_at is None else None
        hidden = None
        if doc is not None:
            if suppressed is None and "suppressed" in doc:
                suppressed = bool(_last(doc["suppressed"]))
            if "hidden" in doc:
                hidden = bool(_last(doc["hidden"]))
        system = origin is not None and n <= origin     # folders, default planes, origin
        out.append(SwFeature(
            r.name, r.id, cls, type_name(cls), suppressed, system,
            recs[parent[n]].id if n in parent else None, [recs[c].id for c in kids[n]], hidden,
            n in inner, item.tag if item else None,
            item.type if item else None,
            dict(item.dims) if item else {}, how, max(0, t.n_children - len(kids[n]))))
    return [f for f in out if not f.system] if user_only else out


def tree_mismatches(src, expected, config: int = 0, helpers=(), exact: bool = False) -> list:
    """Why a saved file does not hold the expected native feature tree ([] = it does).

    ``expected`` lists the steps in order: a name, or (name, type) or
    (name, type, suppressed), where type is a GetTypeName2 string or a set of them. Every
    step must be in the tree, in that order, as a feature of its own (not a sketch made
    inside another feature, such as Hole Wizard's), with a decoded class, the right type
    and, when given, the right suppression state. An imported body without history
    (DUMB_SOLID_CLASSES) is a mismatch unless it is an expected step of that type. With
    ``exact`` every other top-level feature must be named in ``helpers``."""
    tree = feature_tree(src, config)
    names = [f.name for f in tree]
    found = {f.name: f for f in tree}
    by_id = {f.id: f for f in tree}
    problems, last, want = [], -1, {}
    for item in expected:
        item = (item,) if isinstance(item, str) else tuple(item)
        name, kinds = item[0], item[1] if len(item) > 1 else None
        kinds = {kinds} if isinstance(kinds, str) else set(kinds or ())
        want[name] = kinds
        f = found.get(name)
        if f is None:
            problems.append(f"{name}: not in the saved feature tree")
            continue
        if f.internal:
            owner = by_id.get(f.parent)
            problems.append(f"{name}: that name belongs to a sketch inside "
                            f"{owner.name if owner else f.parent}, not to the exported step")
            continue
        k = names.index(name)
        if k < last:
            problems.append(f"{name}: out of order in the saved feature tree")
        last = max(last, k)
        if f.mfc_class is None:
            problems.append(f"{name}: feature class not decoded")
        elif kinds and f.type_name not in kinds:
            problems.append(f"{name}: saved as {f.type_name}, expected {'/'.join(sorted(kinds))}")
        if len(item) > 2 and item[2] is not None:
            if f.suppressed is None:
                problems.append(f"{name}: suppression state not decodable")
            elif f.suppressed != bool(item[2]):
                problems.append(f"{name}: suppressed={f.suppressed}, expected {bool(item[2])}")
    for f in tree:
        if f.dumb_solid and f.type_name not in want.get(f.name, ()):
            problems.append(f"{f.name}: {f.type_name} body without feature history")
        elif exact and f.name not in want and f.name not in helpers and f.parent is None:
            problems.append(f"{f.name}: unexpected feature in the saved tree")
    return problems


# ----------------------------------------------------------------------------------------
# Assembly mates
# ----------------------------------------------------------------------------------------

MATE_TYPES = {0: "coincident", 1: "concentric", 2: "perpendicular", 3: "parallel",
              4: "tangent", 5: "distance", 6: "angle", 7: "unknown", 8: "symmetric",
              9: "cam", 10: "gear", 11: "width", 12: "lock to sketch", 13: "rack pinion",
              14: "max mates", 15: "path", 16: "lock", 17: "screw", 18: "linear coupler",
              19: "universal joint", 20: "coordinate", 21: "slot", 22: "hinge", 23: "slider",
              24: "profile center", 25: "magnetic"}            # swMateType_e
# mate class -> swMateType_e, for files whose mate list holds only the 3DExperience details
_MATE_CLASS_TYPE = {
    "moMateCoincident": 0, "moMateConcentric": 1, "moMatePerpendicular": 2, "moMateParallel": 3,
    "moMateTangent": 4, "moMateDistanceDim_c": 5, "moMateLimitDistanceDim_c": 5,
    "moMatePlanarAngleDim_c": 6, "moMateLimitPlanarAngleDim_c": 6, "moMateSymmetric": 8,
    "moMateCamCoincident": 9, "moMateCamTangent": 9, "moMateGearDim_c": 10, "moMateWidth": 11,
    "moMateRackPinionDim_c": 13, "moMatePath_c": 15, "moMateLock_c": 16, "moMateScrew_c": 17,
    "moMateUniversalJoint_c": 19, "moMateCoordinate": 20, "moMateHinge": 22}


@dataclass
class SwMate:
    """One mate of a SolidWorks assembly, decoded from the saved file."""
    name: str
    type: int | None                 # swMateType_e (0 coincident, 1 concentric, ... 5 distance)
    mfc_class: str | None            # moMateCoincident, moMateDistanceDim_c...
    id: int | None                   # mate feature id
    components: list = field(default_factory=list)   # ['pin<1>', 'base<1>']
    config: str | None = None        # configuration the mate list belongs to
    suppressed: bool | None = None
    hidden: bool | None = None
    align: int | None = None         # stored 'Aligment': 0 n/a, 1 aligned, 2 anti-aligned
    distance: float | None = None    # metres
    angle: float | None = None       # radians
    flip: int | None = None          # FlipDim
    lock_rotate: int | None = None   # concentric 'Lock rotation'
    entities: list = field(default_factory=list)     # [(reference class, 'pin-1@Assem1')]

    @property
    def type_name(self) -> str | None:
        """IFeature.GetTypeName2 of the mate ('MateCoincident', 'MateDistanceDim'...)."""
        return type_name(self.mfc_class)

    @property
    def alignment(self) -> int | None:
        """swMateAlign_e (0 aligned, 1 anti-aligned) when the file stores one."""
        return self.align - 1 if self.align in (1, 2) else None


def _split_components(text: str) -> list:
    """'(link<1>,base<1>)' -> ['link<1>', 'base<1>'] (commas inside <> or () kept)."""
    text = text.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return []
    parts, cur, depth = [], "", 0
    for ch in text[1:-1]:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _dm_mates(b: bytes) -> list:
    """Mate records (moBCMate_c) of SwDocContentMgr/SwDocContentMgrInfo, the list the
    SolidWorks Document Manager reads: CString configuration, u32 0, CString name, i32 2,
    i32 swMateType_e, CString class, CString 'name (compA<1>,compB<1>)', u32 0, u32 id."""
    toks = _mfc_tokens(b)
    out = []
    for k, t in enumerate(toks):
        if t[0] != "S" or k == 0 or not t[2].startswith("mo") or "Mate" not in t[2]:
            continue
        nm = toks[k - 1]
        if nm[0] != "S" or nm[3] + 8 != t[1]:
            continue
        kind = struct.unpack_from("<i", b, nm[3] + 4)[0]
        d = mfc_string(b, t[3])
        if d is None or d[1] + 8 > len(b):
            continue
        cfg = toks[k - 2][2] if k >= 2 and toks[k - 2][0] == "S" else None
        desc = d[0]
        rest = desc[len(nm[2]):] if desc.startswith(nm[2]) else desc[desc.find("("):]
        out.append({"config": cfg, "name": nm[2], "type": kind, "class": t[2],
                    "components": _split_components(rest), "id": _u32(b, d[1] + 4)})
    return out


def _mate_docs(docs: dict, config: int) -> list:
    """Mate documents of the configuration's mate group(s), in mate order."""
    out = []

    def walk(v):
        if isinstance(v, dict):
            cls = _last(v.get("class"))
            if isinstance(cls, str) and cls.startswith("moMate") and cls != "moMateGroup_c" \
                    and ("description" in v or "name" in v):
                out.append(v)
                return
            for k in sorted(v, key=lambda x: (not str(x).startswith("Mate"),
                                              int(str(x)[4:]) if str(x)[4:].isdigit() else 0)):
                walk(v[k])
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for d in _config_docs(docs, config):
        if "NumberMates" in d or "moMateGroup_c" in _as_list(d.get("class")):
            walk(d)
    return out


def mates(src, config: int = 0) -> list:
    """The mates of an assembly as SwMate, in mate-folder order.

    Name, type, class, components and id come from SwDocContentMgr/SwDocContentMgrInfo
    (every assembly); suppression, alignment, distance, angle, flip and the mated
    entities from the configuration's mate group in Contents/3DExperienceExchange2, when
    SolidWorks wrote one (not in every file)."""
    sections = _sections(src)
    dm = _dm_mates(sections["SwDocContentMgr/SwDocContentMgrInfo"]) \
        if "SwDocContentMgr/SwDocContentMgrInfo" in sections else []
    docs = exchange_docs(sections)
    toc = _toc(docs)
    details = {}
    for d in _mate_docs(docs, config):
        details.setdefault(_last(d.get("description", d.get("name"))), d)
    cfg_names = list(dict.fromkeys(m["config"] for m in dm))
    want = toc.get(config, (None,))[0]
    if want not in cfg_names:
        want = cfg_names[config] if config < len(cfg_names) else (cfg_names[0] if cfg_names else None)
    rows = [m for m in dm if m["config"] == want] if dm else [
        {"config": None, "name": _last(d.get("description", d.get("name"))), "class": _last(d.get("class")),
         "type": _MATE_CLASS_TYPE.get(_last(d.get("class"))), "components": [], "id": _last(d.get("id"))}
        for d in details.values()]
    out = []
    for m in rows:
        d = details.get(m["name"], {})

        def num(key):
            v = _last(d.get(key))
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        ents = []
        for k in range(int(num("NumberEntities") or 0) or 4):
            ref = d.get(f"Ref{k}")
            if isinstance(ref, dict):
                dummy = ref.get("dummy") if isinstance(ref.get("dummy"), dict) else {}
                ents.append((_last(ref.get("class")), _last(dummy.get("target"))))
        sup, hid = _last(d.get("suppressed")), _last(d.get("hidden"))
        out.append(SwMate(
            m["name"], m["type"], m["class"], m["id"], list(m["components"]), m["config"],
            None if sup is None else bool(sup), None if hid is None else bool(hid),
            num("Aligment"), num("Distance"), num("Angle"), num("FlipDim"), num("LockRotate"),
            ents))
    return out


# ----------------------------------------------------------------------------------------
# Summary / command line
# ----------------------------------------------------------------------------------------

def info(src) -> dict:
    data = Path(src).read_bytes()
    sections = read_sections(data)
    out = {"container": container(data), "sections": len(sections),
           "configurations": configurations(sections), "parasolid": None, "mesh": None}
    try:
        t = parasolid(sections, out["configurations"][0] if out["configurations"] else 0)
        out["parasolid"] = {"modeller": t[0].modeller, "schema": t[0].schema,
                            "streams": [(x.kind, len(x.data)) for x in t]}
    except SldprtError as e:
        out["parasolid"] = str(e)
    try:
        m = display_mesh(sections)
        lo, hi = m.bounds()
        out["mesh"] = {"faces": m.n_faces, "triangles": len(m.triangles),
                       "bbox_mm": [lo.round(3).tolist(), hi.round(3).tolist()],
                       "area_mm2": round(m.area(), 3), "volume_mm3": round(m.volume(), 3)}
    except SldprtError as e:
        out["mesh"] = str(e)
    return out


def _table(rows) -> str:
    widths = [max(len(str(r[k])) for r in rows) for k in range(len(rows[0]))]
    return "\n".join("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def _yes_no(v) -> str:
    return "?" if v is None else ("yes" if v else "no")


def main(argv=None) -> int:
    import argparse
    import json
    import sys
    from dataclasses import asdict
    ap = argparse.ArgumentParser(prog="python -m bauyrcad.core.sldprt",
                                 description="Inspect / convert a SolidWorks file without SolidWorks")
    ap.add_argument("file")
    ap.add_argument("--sections", action="store_true", help="list every section")
    ap.add_argument("--dump", metavar="DIR", help="write every decoded section into DIR")
    ap.add_argument("--stl", metavar="OUT", help="write the display-list mesh as STL")
    ap.add_argument("--parasolid", metavar="OUT", help="write the Parasolid partition (.xmp_bin)")
    ap.add_argument("--tree", action="store_true", help="feature tree (FeatureManager order)")
    ap.add_argument("--all", action="store_true",
                    help="with --tree: also the default folders, planes and origin")
    ap.add_argument("--mates", action="store_true", help="mates of an assembly")
    ap.add_argument("--keywords", action="store_true", help="KeyWords items and dimensions")
    ap.add_argument("--json", action="store_true", help="--tree / --mates / --keywords as JSON")
    ap.add_argument("--config", type=int, default=0)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sections = read_sections(a.file)
    if a.sections:
        for k, v in sections.items():
            print(f"{len(v):10d}  {k}")
    if a.dump:
        d = Path(a.dump)
        d.mkdir(parents=True, exist_ok=True)
        for k, v in sections.items():
            (d / re.sub(r"[^\w.-]", "_", k)).write_bytes(v)
    if a.stl:
        write_stl(display_mesh(sections), a.stl, Path(a.file).stem)
    if a.parasolid:
        print("\n".join(write_parasolid(a.file, a.parasolid, a.config)))
    if a.tree:
        tree = feature_tree(sections, a.config, user_only=not a.all)
        if a.json:
            print(json.dumps([asdict(f) for f in tree], indent=1, ensure_ascii=False))
        else:
            print(_table([("id", "name", "type", "suppressed", "parent", "dimensions")] + [
                (f.id, f.name, f.type_name or "?", _yes_no(f.suppressed), f.parent or "",
                 "  ".join(f"{k}={v}" for k, v in f.dims.items())) for f in tree]))
    if a.mates:
        ms = mates(sections, a.config)
        if a.json:
            print(json.dumps([asdict(m) for m in ms], indent=1, ensure_ascii=False))
        elif ms:
            print(_table([("id", "name", "type", "components", "suppressed", "align", "value")] + [
                (m.id, m.name, MATE_TYPES.get(m.type, m.type), ", ".join(m.components),
                 _yes_no(m.suppressed), {1: "aligned", 2: "anti"}.get(m.align, ""),
                 f"{m.distance * 1000:g} mm" if m.distance is not None else
                 f"{m.angle * 180 / 3.141592653589793:g} deg" if m.angle is not None else "")
                for m in ms]))
        else:
            print("no mates")
    if a.keywords:
        kw = keywords(sections)
        if a.json:
            print(json.dumps([asdict(k) for k in kw.values()], indent=1, ensure_ascii=False))
        else:
            print(_table([("id", "tag", "name", "type", "dimensions")] + [
                (k.id, k.tag, k.name, k.type or "", "  ".join(f"{n}={v}" for n, v in k.dims.items()))
                for k in sorted(kw.values(), key=lambda k: k.id)]))
    if not (a.sections or a.dump or a.stl or a.parasolid or a.tree or a.mates or a.keywords):
        print(json.dumps(info(a.file), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
