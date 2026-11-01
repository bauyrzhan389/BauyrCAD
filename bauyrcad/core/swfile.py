"""Write SolidWorks 2024 part (.SLDPRT) and assembly (.SLDASM) files without SolidWorks.

A part is written as SolidWorks itself writes an imported solid: a seed file SolidWorks
saved once (``bauyrcad/data/sw_seeds/part.SLDPRT``, its tree one Imported feature) with
the body replaced. An assembly is a seed assembly of the same number of components
(``bauyrcad/data/sw_seeds/asm/seedNN.SLDASM``) with each component's part file, name,
placement and fixed state patched in (:func:`write_assembly`). Found by black-box
comparison of files, SolidWorks as the judge (notes: docs/SLDPRT_FORMAT.md, "Writing .SLDPRT"
and "Writing .SLDASM"):

* the 2015+ container is a ZIP behind an 8-byte prefix, its record signatures disguised and
  its names nibble-swapped; SolidWorks checks the structure, not CRCs or compression;
* an Imported body is a Parasolid neutral binary stream in the section
  ``Config-0-FeatureBodies/LocalBodies``, under SolidWorks' schema key
  (:func:`bauyrcad.core.parasolid.to_binary` with ``solidworks=True``), framed by a small
  header of SolidWorks' that carries the chunk's length;
* the geometry caches (the Parasolid partition, display lists, previews, lightweight data)
  are dropped: SolidWorks rebuilds them from the feature when it opens the file.

Pure Python + NumPy; the OCC side is :mod:`bauyrcad.core.xt_brep`.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .sldprt import ZLB_TAG

SEEDS = Path(__file__).resolve().parent.parent / "data" / "sw_seeds"
ASM_SEEDS = SEEDS / "asm"
LOCAL_BODIES = "Config-0-FeatureBodies/LocalBodies"
CACHES = ("Contents/Config-0-Partition", "Contents/DisplayLists", "Preview", "PreviewPNG",
          "Contents/Config-0-LWDATA")
# an assembly's caches and indexes, rebuilt by SolidWorks on opening (FaceTessellations/*
# too); the seeds are stored without them
ASM_CACHES = ("Contents/DisplayLists", "Preview", "PreviewPNG", "Contents/Config-0-LWDATA",
              "swXmlContents/COMPINSTANCETREE", "SwDocContentMgr/SwDocContentMgrInfo",
              "Contents/3DExperienceExchange2", "Contents/3DExperienceExchange_ConfigHeader",
              "ThirdPtyStore/VisualStates", "swXmlContents/KeyWords")
CONFIG0, MODEL_HEADER, HEADER2, CMGR = ("Contents/Config-0", "Contents/Config-0-ModelHeader",
                                        "Header2", "Contents/CMgr")
_BASE = 8                    # ZIP offsets count from here (after an 8-byte prefix)


class SwFileError(ValueError):
    pass


def _swap(b: bytes) -> bytes:
    return bytes(((x >> 4) | (x << 4)) & 0xFF for x in b)


@dataclass
class _Entry:
    name: str
    lh: int                  # local header position
    cd: int                  # central directory entry position
    nl: int                  # name length
    csz: int
    usz: int
    crc: int

    @property
    def data_pos(self) -> int:
        return self.lh + 30 + self.nl


class Container:
    """A SolidWorks 2015+ file read through its central directory and re-packed with
    sections replaced or dropped; everything else (filler, decoy records, headers) is
    copied as it is."""

    def __init__(self, data: bytes):
        self.data = data
        k = len(data) - 22
        while k >= 0:                   # the end record: the one whose CD offset adds up
            n1, n2, cdsz, cdoff = struct.unpack_from("<HHII", data, k + 8)
            if (n1 == n2 and 0 < n1 < 10000 and cdoff + _BASE + cdsz == k
                    and struct.unpack_from("<HH", data, k + 4) == (0, 0)):
                break
            k -= 1
        else:
            raise SwFileError("not a SolidWorks 2015+ file (no end record)")
        self.eocd = k
        self.entries, p = [], cdoff + _BASE
        for _ in range(n2):
            (_sig, _made, _need, _fl, _meth, _tm, _dt, crc, csz, usz, nl, xl, cml, _dk, _ia,
             _ea, off) = struct.unpack_from("<IHHHHHHIIIHHHHHII", data, p)
            if xl or cml:
                raise SwFileError("unexpected ZIP extra or comment field")
            name = _swap(data[p + 46:p + 46 + nl]).decode("latin-1")
            self.entries.append(_Entry(name, off + _BASE, p, nl, csz, usz, crc))
            p += 46 + nl
        self.cd_end = p
        self.by_name = {e.name: e for e in self.entries}

    def names(self) -> list:
        return [e.name for e in self.entries]

    def get(self, name) -> bytes:
        e = self.by_name[name]
        raw = self.data[e.data_pos:e.data_pos + e.csz]
        return zlib.decompress(raw, -15) if e.csz else b""

    def build(self, replace: dict | None = None, drop=()) -> bytes:
        """The file with ``replace = {name: new bytes}`` and the sections in ``drop``
        removed; unchanged sections keep their compressed bytes."""
        replace, d = replace or {}, self.data
        order = sorted(self.entries, key=lambda e: e.lh)
        out, newpos, prev_end = bytearray(d[:order[0].lh]), {}, order[0].lh
        for e in order:
            out += d[prev_end:e.lh]                                  # filler between records
            prev_end = e.data_pos + e.csz
            if e.name in drop:
                continue
            if e.name in replace:
                body = replace[e.name]
                c = zlib.compressobj(6, zlib.DEFLATED, -15)
                comp, crc, usz = c.compress(body) + c.flush(), zlib.crc32(body), len(body)
            else:
                comp, crc, usz = d[e.data_pos:e.data_pos + e.csz], e.crc, e.usz
            hdr = bytearray(d[e.lh:e.lh + 30 + e.nl])
            struct.pack_into("<III", hdr, 14, crc, len(comp), usz)
            newpos[e.name] = (len(out), crc, len(comp), usz)
            out += hdr + comp
        out += d[prev_end:self.entries[0].cd]                         # filler before the CD
        cd_start, keep = len(out), [e for e in self.entries if e.name not in drop]
        for e in keep:
            rec = bytearray(d[e.cd:e.cd + 46 + e.nl])
            pos, crc, csz, usz = newpos[e.name]
            struct.pack_into("<III", rec, 16, crc, csz, usz)
            struct.pack_into("<I", rec, 42, pos - _BASE)
            out += rec
        out += d[self.cd_end:self.eocd]                               # filler before the end
        eo = bytearray(d[self.eocd:self.eocd + 22])
        struct.pack_into("<HHII", eo, 8, len(keep), len(keep), len(out) - cd_start,
                         cd_start - _BASE)                            # CD size counts filler
        return bytes(out + eo + d[self.eocd + 22:])


def local_bodies(template: bytes, body: bytes) -> bytes:
    """The seed's LocalBodies section holding ``body`` (a neutral binary transmit stream):
    SolidWorks' header kept, its two lengths (chunk + 5, chunk) updated, then the ZLB chunk
    [tag][uncompressed][compressed][zlib][8 zero bytes]."""
    i = template.find(ZLB_TAG)
    if i < 9:
        raise SwFileError("the seed's LocalBodies section has no body chunk")
    z = zlib.compress(body, 6)
    chunk = 16 + 8 + len(z) + 8
    head = bytearray(template[:i])
    head[i - 9:i - 5] = struct.pack("<I", chunk + 5)
    head[i - 4:i] = struct.pack("<I", chunk)
    return bytes(head) + ZLB_TAG + struct.pack("<II", len(body), len(z)) + z + b"\0" * 8


def write_part(shape, path, name: str = "Part1", *, features=False, document=None,
               require_native=False):
    """Write SolidWorks 2024 without COM and return path/native/features/warnings.

    By default, write the shape as one Imported solid (several solids as lumps). With
    ``features=True``, supply a Document as ``shape`` or through ``document``: supported
    sketch/extrude/revolve history is native, unsupported history falls back as a whole. With
    ``require_native=True`` unsupported input raises before opening the destination.
    """
    warnings = []
    if features:
        from . import swfeatures
        if document is None and hasattr(shape, 'features') and hasattr(shape, 'body'):
            document, shape = shape, shape.body
        try:
            if document is None:
                raise swfeatures.UnsupportedFeature('No parametric document supplied')
            return swfeatures.write(document, path, name)
        except swfeatures.UnsupportedFeature as e:
            if require_native:
                raise SwFileError(str(e)) from e
            warnings.append(str(e))
    elif require_native:
        raise SwFileError('require_native needs features=True')
    from . import parasolid, xt_brep
    bodies = xt_brep.bodies_from_shape(shape, name)
    body = bodies[0] if len(bodies) == 1 else xt_brep.merge_lumps(bodies, name)
    body.attributes, body.name = [], ""            # names come from the file, as in SolidWorks
    for f in body.faces:
        f.color = None
    seed = Container((SEEDS / "part.SLDPRT").read_bytes())
    data = seed.build(replace={LOCAL_BODIES: local_bodies(seed.get(LOCAL_BODIES),
                                                          parasolid.to_binary(body, True))},
                      drop=CACHES)
    Path(path).write_bytes(data)
    return {'path': str(path), 'native': False, 'features': ['Imported1'], 'warnings': warnings}


# ---- assemblies -----------------------------------------------------------------------------
# The seed family (made once with SolidWorks 2024, projects/sldasm_diff/make_asm_seeds.py):
# seedNN.SLDASM (N = 1..46) holds N floating components; component i (1-based) is the dummy part
# dII.SLDPRT, named dII-1, at t = (100 i + 12.3, 45.6, 78.9) mm with no rotation. Records
# can't be cloned to add components, so there is one seed per component count.
_PLACEMENT_AFTER = 144       # component name (its first string in Config-0) end -> placement
_FIXED_AFTER = 86            # placement block end -> the component's fixed flag (u8)


def asm_caches(names) -> tuple:
    """The sections of an assembly that write_assembly drops (SolidWorks rebuilds them)."""
    return tuple(n for n in names if n in ASM_CACHES or n.startswith("FaceTessellations/"))


def max_components() -> int:
    """The largest component count a seed exists for."""
    return max((int(p.stem[4:]) for p in ASM_SEEDS.glob("seed*.SLDASM")), default=0)


def cstring(text: str) -> bytes:
    """SolidWorks' (MFC) string: ff fe ff, the length in UTF-16 units (u8, or ff + u16),
    then UTF-16LE."""
    u = text.encode("utf-16-le")
    n = len(u) // 2
    head = bytes([n]) if n < 255 else b"\xff" + struct.pack("<H", n)
    return b"\xff\xfe\xff" + head + u


def cstrings(data: bytes):
    """(position, byte length, text) of every string in a section that decodes as one."""
    for m in re.finditer(b"\xff\xfe\xff", data):
        p = m.start() + 3
        if p >= len(data):
            break
        n, q = data[p], p + 1
        if n == 255:
            if q + 2 > len(data):
                continue
            n, q = struct.unpack_from("<H", data, q)[0], q + 2
        if q + 2 * n > len(data):
            continue
        try:
            text = data[q:q + 2 * n].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        yield m.start(), q + 2 * n - m.start(), text


def transform_block(R, t_m) -> bytes:
    """A component placement in Config-0: u8 rotation flag, the rotation (9 f64, R
    transposed row-major = IMathTransform.ArrayData[:9]) only when there is one, the
    translation (3 f64, m) and the scale (f64 1)."""
    tail = struct.pack("<4d", *map(float, t_m), 1.0)
    if np.allclose(R, np.eye(3), rtol=0, atol=1e-15):
        return b"\x00" + tail
    return b"\x01" + struct.pack("<9d", *np.asarray(R, float).T.ravel()) + tail


def read_transform(data: bytes, pos: int):
    """(R, t in m, scale, byte length) of the transform block at ``pos``."""
    if data[pos] not in (0, 1):
        raise SwFileError(f"no transform block at {pos}")
    if data[pos]:
        v = struct.unpack_from("<13d", data, pos + 1)
        return np.array(v[:9]).reshape(3, 3).T, np.array(v[9:12]), v[12], 105
    v = struct.unpack_from("<4d", data, pos + 1)
    return np.eye(3), np.array(v[:3]), v[3], 33


def _patch(data: bytes, edits) -> bytes:
    """``edits`` = [(position, old length, new bytes)] applied in one pass."""
    out, prev = bytearray(), 0
    for p, n, new in sorted(edits, key=lambda e: e[0]):
        if p < prev:
            raise SwFileError("overlapping edits in a seed assembly section")
        out += data[prev:p] + new
        prev = p + n
    return bytes(out + data[prev:])


def _only(found: list, what: str, count: int = 1) -> list:
    if len(found) != count:
        raise SwFileError(f"seed assembly: {what} found {len(found)} times, expected {count}")
    return found


def _string_index(data: bytes) -> dict:
    """{text: [(position, byte length)]} of a section's strings."""
    index = {}
    for p, n, text in cstrings(data):
        index.setdefault(text, []).append((p, n))
    return index


def _config0(data: bytes, edits) -> bytes:
    """Config-0 = 'moAssembly_c' header (18 bytes), u32 L1, block 1, u32 L2, block 2: the
    edits are applied and both lengths kept right."""
    L1 = struct.unpack_from("<I", data, 18)[0]
    L2 = struct.unpack_from("<I", data, 22 + L1)[0]
    d1 = sum(len(new) - n for p, n, new in edits if p < 22 + L1)
    d2 = sum(len(new) - n for p, n, new in edits if p >= 22 + L1)
    if any(p < 22 + L1 < p + n for p, n, _ in edits) or any(p < 22 for p, _, _ in edits):
        raise SwFileError("seed assembly: an edit crosses a Config-0 block boundary")
    return _patch(data, list(edits) + [(18, 4, struct.pack("<I", L1 + d1)),
                                       (22 + L1, 4, struct.pack("<I", L2 + d2))])


def _file_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    return name or "Part"


DOC_PART, DOC_ASSEMBLY = 2, 3   # document type after a referenced file's stem in the model header


def is_subassembly(shape) -> bool:
    """A write_assembly part entry that references an existing .SLDASM (a sub-assembly)."""
    return isinstance(shape, (str, Path)) and str(shape).lower().endswith(".sldasm")


def is_reference(shape) -> bool:
    """A write_assembly part entry that references an existing SolidWorks file next to the assembly
    (.SLDASM sub-assembly, or a .SLDPRT, e.g. one saved by SolidWorks): referenced, not written."""
    return isinstance(shape, (str, Path)) and str(shape).lower().endswith((".sldasm", ".sldprt"))


def write_assembly(parts, instances, path, name: str = "Assembly") -> list:
    """A SolidWorks 2024 .SLDASM and its parts, written without SolidWorks.

    ``parts`` = [(part name, shape, rgb or None)], each written once as
    ``<folder>/<part name>.SLDPRT`` (:func:`write_part`; colours are not written yet).
    A sub-assembly is a part entry whose shape is the path of a ``.SLDASM`` already written
    next to this one; a part entry whose shape is the path of an existing ``.SLDPRT`` next to
    this one (e.g. saved by SolidWorks, so its feature tree is kept) is referenced the same way (:func:`write_assembly` on it first): it is referenced, not written, and
    its records are marked as an assembly (document type 3 after the stem in the model header,
    as SolidWorks writes a sub-assembly component; 2 = part), so an assembly tree of any size
    is built from assemblies of up to :func:`max_components` components each;
    ``instances`` = [(part index, instance name, 4x4 pose in mm, fixed)]. SolidWorks names
    components after their part files (``peg-1``, ``peg-2``), so instance names are not
    stored. Returns the part files written.

    The seed with N = len(instances) components is patched: in the model header (and its
    copy Header2) each referenced file's absolute path and stem and each component's name;
    in Config-0 each name, placement (rotation, translation, keeping the block length right)
    and fixed flag; in CMgr each name. SolidWorks finds a part next to the assembly first,
    so the folder can be moved as a whole."""
    n = len(instances)
    if n == 0:
        raise SwFileError("the assembly has no components to export")
    if n > max_components():
        raise SwFileError(f"a .SLDASM can be written with at most {max_components()} components "
                          f"without SolidWorks (this assembly has {n}); STEP or Parasolid (.x_t) "
                          "export has no limit")
    path = Path(path)
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    stems, seen, files = [], set(), []
    for pname, shape, _rgb in parts:                       # unique file names per folder
        if is_reference(shape):
            sub = Path(shape)
            kind = "sub-assembly" if is_subassembly(shape) else "part"
            if not sub.exists():
                raise SwFileError(f"{kind} {sub} not found: write it first")
            if sub.resolve().parent != folder.resolve():
                raise SwFileError(f"{kind} {sub.name} must be in the same folder as {path.name}")
            if sub.stem.lower() in seen:
                raise SwFileError(f"two components reference different files named {sub.stem}")
            seen.add(sub.stem.lower())
            stems.append(sub.stem)
            files.append(sub)
            continue
        stem, k = _file_name(pname), 1
        while stem.lower() in seen:
            k += 1
            stem = f"{_file_name(pname)}_{k}"
        seen.add(stem.lower())
        stems.append(stem)
        files.append(folder / f"{stem}.SLDPRT")
    written = []
    for f, (_pname, shape, _rgb) in zip(files, parts):
        if is_reference(shape):
            continue
        write_part(shape, f, f.stem)
        written.append(f)

    seed = Container((ASM_SEEDS / f"seed{n:02d}.SLDASM").read_bytes())
    sec = {s: seed.get(s) for s in (CONFIG0, MODEL_HEADER, HEADER2, CMGR)}
    strings = {s: _string_index(d) for s, d in sec.items()}
    edits = {s: [] for s in sec}
    count = {}
    for i, (k, _iname, pose, fixed) in enumerate(instances, 1):
        stem = stems[k]
        count[stem] = count.get(stem, 0) + 1
        comp = f"{stem}-{count[stem]}"
        dummy = f"d{i:02d}"
        M = np.asarray(pose, float)
        R, t = M[:3, :3], M[:3, 3] * 1e-3
        if not (np.allclose(R @ R.T, np.eye(3), atol=1e-9) and np.linalg.det(R) > 0):
            raise SwFileError(f"component {comp}: the pose is not a rotation + translation")
        part_path = str(files[k].resolve())
        is_asm = files[k].suffix.lower() == ".sldasm"
        for s in (MODEL_HEADER, HEADER2):                 # referenced file, stem, shown name
            (hit,) = _only([h for txt, hits in strings[s].items() for h in hits
                            if txt.lower().endswith(f"\\{dummy}.sldprt")], f"{dummy}.SLDPRT in {s}")
            edits[s].append((*hit, cstring(part_path)))
            (hit,) = _only(strings[s].get(dummy, []), f"{dummy} in {s}")
            p_end = hit[0] + hit[1]                       # the document type follows the stem
            if sec[s][p_end:p_end + 2] != bytes((DOC_PART, 0)):
                raise SwFileError(f"seed assembly: no part document type after {dummy} in {s}")
            edits[s].append((hit[0], hit[1] + 1,
                             cstring(stem) + bytes((DOC_ASSEMBLY if is_asm else DOC_PART,))))
            (hit,) = _only(strings[s].get(f"{dummy}-1", []), f"{dummy}-1 in {s}")
            edits[s].append((*hit, cstring(comp)))
        (hit,) = _only(strings[CMGR].get(f"{dummy}-1", []), f"{dummy}-1 in CMgr")
        edits[CMGR].append((*hit, cstring(comp)))
        d = sec[CONFIG0]
        names = _only(strings[CONFIG0].get(f"{dummy}-1", []), f"{dummy}-1 in Config-0", 2)
        edits[CONFIG0] += [(p, ln, cstring(comp)) for p, ln in names]
        p = sum(names[0]) + _PLACEMENT_AFTER
        if d[p:p + 33] != transform_block(np.eye(3), np.array((100.0 * i + 12.3, 45.6, 78.9)) * 1e-3):
            raise SwFileError(f"seed assembly: component {i}'s placement is not where expected")
        edits[CONFIG0].append((p, 33, transform_block(R, t)))
        q = p + 33 + _FIXED_AFTER
        if d[q] != 0 or any(a <= q < a + ln for a, ln in names):
            raise SwFileError(f"seed assembly: no fixed flag after component {i}'s placement")
        edits[CONFIG0].append((q, 1, b"\x01" if fixed else b"\x00"))
    new = {s: (_config0(sec[s], edits[s]) if s == CONFIG0 else _patch(sec[s], edits[s]))
           for s in sec}
    path.write_bytes(seed.build(replace=new, drop=asm_caches(seed.names())))
    return written


def assembly_components(path) -> list:
    """(component name, R, t in mm, fixed) per component of a .SLDASM this module wrote,
    read back from Config-0 (for tests and checks)."""
    from .sldprt import read_sections
    d = read_sections(path)[CONFIG0]
    first, seen = {}, {}
    for p, ln, text in cstrings(d):                # a component's name is there twice
        if re.fullmatch(r".+-\d+", text):
            first.setdefault(text, p + ln)
            seen[text] = seen.get(text, 0) + 1
    out = []
    for text, end in first.items():
        if seen[text] == 2:
            q = end + _PLACEMENT_AFTER
            R, t, _scale, size = read_transform(d, q)
            out.append((text, R, t * 1e3, bool(d[q + size + _FIXED_AFTER])))
    return out
