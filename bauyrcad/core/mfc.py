"""Exact model of the MFC ``CArchive`` streams in SolidWorks 2024 part files.

``Contents/Config-N`` and ``Contents/Config-N-ResolvedFeatures`` are one MFC archive split in
two sections: the second starts with the load-map count the first ended at. Every object is
written as a tag (a new-class header ``FF FF <schema> <length> <name>``, a class tag
``0x8000 | k`` or, for a back reference, the object's index; ``7F FF`` + u32 for big
indices) followed by what its class's ``Serialize`` writes. Nothing in the bytes says where an
object ends, so the walker needs the field layout of every class (:data:`LAYOUTS`, decoded
from files SolidWorks saved, docs/SLDPRT_FORMAT.md section 16). A wrong layout desynchronises
the walk, which is caught: the next tag is not a valid one, the stream does not end where the
walk does, or the load-map count differs from the one the next section starts with.

Objects keep their identity: a nested-object field holds the :class:`Obj` itself, whether it
was written inline or as a back reference, and a plain (untagged) u16/u32 object reference is
resolved to its :class:`Obj` too. :func:`dumps` numbers the map again as it writes, so tags
and plain references follow inserted or removed objects (the basis of renumbering, M2).

No OCC, no Qt.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

NEW_CLASS = 0xFFFF
NEW_CLASS_BYTES = struct.pack("<H", NEW_CLASS)
NULL_TAG = struct.pack("<H", 0)
BIG = 0x7FFF
CLASS_BIT = 0x8000
BIG_CLASS_BIT = 0x80000000


class MfcError(ValueError):
    pass


@dataclass(eq=False)
class MfcClass:
    name: str
    schema: int


@dataclass(eq=False)
class Obj:
    cls: MfcClass
    values: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.cls.name

    def __repr__(self):
        return f"<{self.cls.name} {len(self.values)} fields>"


@dataclass(eq=False)
class Opaque:
    """Bytes kept as they are, up to the header of the class that follows (an object whose
    fields are not decoded yet). ``nested`` are placeholders for the objects of the listed
    classes found inside, which take map slots; ``classes`` the class indices they were
    tagged with (they must stay the same when the archive is written again)."""
    data: bytes
    nested: list
    classes: dict


@dataclass(eq=False)
class Ref:
    """A plain (untagged) object reference: ``target`` is the referenced Obj, or None while
    the index is outside the archive (``index`` keeps the number read)."""
    index: int
    target: Obj | None = None


# ----------------------------------------------------------------------------------------
# Layout language
# ----------------------------------------------------------------------------------------
# A layout is a list of fields. A field is a type, or (name, type). Types:
#   "u8" "u16" "u32" "i32" "f32" "f64"  scalars (little-endian)
#   "str"                               CString: FF FE FF, u8 length (FF + u16, FFFF + u32), UTF-16LE
#   "obj"                               a nested object (tag + its class layout), or a back reference
#   "ref16" "ref32"                     a plain object index, no tag bit
#   "raw:N"                             N bytes kept as they are (only where the extent is proven)
#   ("arr", "u16"|"u32", [fields])      a count, then that many items
#   ("arr", "@name", [fields])          as many items as the field `name` read earlier in this
#                                       object says
#   ("if", name, (values...), [fields]) fields present when the field `name` read earlier in
#                                       this object has one of the values ("ifnot": has none)
#   ("ifall", ((name, values), ...), [a], [b])
#                                       fields a when every condition matches, else b
#   ("rep", n, [fields])                n items, n fixed
#   ("objs0",)                          objects up to a NULL tag (the NULL is consumed)
#   ("ifback", name, [a], [b])          fields a when the object field `name` read earlier in
#                                       this object was a back reference (or null), else b
#   ("ifobj", name, sub, (values...), [a], [b])
#                                       fields a when the field `sub` of the object `name` read
#                                       earlier in this object has one of the values, else b
#   ("opaque", next, (classes...))      bytes kept as they are up to the new-class header of
#                                       class `next`; objects of the listed (earlier) classes
#                                       inside are counted by their class tags (they take map
#                                       slots). Only for document settings whose extent is so
#                                       proven, and the count is checked against the next
#                                       stream's start

_SCALAR = {"u8": "<B", "u16": "<H", "u32": "<I", "i32": "<i", "f32": "<f", "f64": "<d"}


def _sub(o, name):
    """The field `name` of a nested object; `$class` selects its runtime class."""
    return None if o is None else o.name if name == "$class" else o.values.get(name)


def _split(f):
    if isinstance(f, tuple) and len(f) == 2 and isinstance(f[1], (str, tuple)) and \
            f[0] not in ("arr", "if", "ifnot", "ifall", "rep", "ifback", "ifobj", "opaque", "objs0"):
        return f[0], f[1]
    return None, f


# ----------------------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------------------

class Reader:
    """Walks one stream. ``table`` is the load map shared by the streams of one archive
    (index -> MfcClass or Obj); index 0 is NULL."""

    def __init__(self, data: bytes, layouts: dict, table: list | None = None, start: int = 1):
        self.b = data
        self.i = 0
        self.layouts = layouts
        self.table = table if table is not None else []
        while len(self.table) < start:
            self.table.append(None)
        self.refs: list[Ref] = []
        self.stack: list[str] = []
        self.trace = None                      # list to collect (offset, path, kind, value)

    # -- primitives
    def need(self, n):
        if self.i + n > len(self.b):
            self.fail(f"needs {n} bytes, stream ends")

    def fail(self, msg):
        path = " > ".join(self.stack)
        raise MfcError(f"offset {self.i}: {msg} [{path}]")

    def scalar(self, t):
        fmt = _SCALAR[t]
        n = struct.calcsize(fmt)
        self.need(n)
        v = struct.unpack_from(fmt, self.b, self.i)[0]
        self.i += n
        return v

    def cstring(self):
        b, i = self.b, self.i
        if b[i:i + 3] != b"\xff\xfe\xff":
            self.fail(f"CString expected, found {b[i:i + 4].hex(' ')}")
        self.need(4)
        n, j = b[i + 3], i + 4
        if n == 0xFF:
            n, j = struct.unpack_from("<H", b, j)[0], j + 2
            if n == 0xFFFF:
                n, j = struct.unpack_from("<I", b, j)[0], j + 4
        if j + 2 * n > len(b):
            self.fail("CString runs past the stream")
        try:
            s = b[j:j + 2 * n].decode("utf-16-le")
        except UnicodeDecodeError:
            s = None
        if s is None or len(_cstring(s)) != j + 2 * n - i:
            self.fail("CString not in canonical form")
        self.i = j + 2 * n
        return s

    # -- objects
    def obj(self):
        at = self.i
        w = self.scalar("u16")
        if w == NEW_CLASS:
            schema = self.scalar("u16")
            n = self.scalar("u16")
            self.need(n)
            try:
                name = self.b[self.i:self.i + n].decode("ascii")
            except UnicodeDecodeError:
                self.i = at
                self.fail("bad class name")
            self.i += n
            if not name.isidentifier():
                self.i = at
                self.fail(f"bad class name {name!r}")
            cls = MfcClass(name, schema)
            self.table.append(cls)
            return self.body(cls, at)
        if w == BIG:
            v = self.scalar("u32")
            is_class, k = bool(v & BIG_CLASS_BIT), v & ~BIG_CLASS_BIT
            if not is_class and k < BIG:
                self.i = at
                self.fail(f"big tag for small index {k}")
        else:
            is_class, k = bool(w & CLASS_BIT), w & ~CLASS_BIT
            if is_class and k == BIG:
                self.i = at
                self.fail("bad class tag")
        if is_class:
            cls = self.table[k] if k < len(self.table) else None
            if not isinstance(cls, MfcClass):
                self.i = at
                self.fail(f"class tag {k} names no class ({type(cls).__name__})")
            return self.body(cls, at)
        if k == 0:
            return None
        o = self.table[k] if k < len(self.table) else None
        if not isinstance(o, Obj):
            self.i = at
            self.fail(f"back reference {k} names no object ({type(o).__name__})")
        return o

    def body(self, cls: MfcClass, at: int) -> Obj:
        o = Obj(cls)
        self.table.append(o)
        lay = self.layouts.get(cls.name)
        if lay is None:
            self.i = at
            self.fail(f"no layout for {cls.name} (schema {cls.schema})")
        self.stack.append(cls.name)
        self.fields(lay, o.values)
        self.stack.pop()
        return o

    def fields(self, lay, out: dict):
        back = {}
        for k, f in enumerate(lay):
            name, t = _split(f)
            key = name if name is not None else f"_{k}"
            at = self.i
            if t == "obj":
                n0 = len(self.table)
                out[key] = self.obj()
                back[key] = len(self.table) == n0
            elif isinstance(t, tuple) and t[0] == "ifback":
                d = {}
                self.fields(t[2] if back[t[1]] else t[3], d)
                out[key] = d
            elif isinstance(t, tuple) and t[0] == "ifobj":
                d = {}
                self.fields(t[4] if _sub(out[t[1]], t[2]) in t[3] else t[5], d)
                out[key] = d
            else:
                out[key] = self.field(t, out)
            if self.trace is not None:
                self.trace.append((at, "/".join(self.stack), key, out[key]))

    def field(self, t, cur: dict):
        if isinstance(t, tuple):
            kind = t[0]
            if kind in ("arr", "arr_mask"):
                n = (cur[t[1]] & t[2]) if kind == "arr_mask" else (cur[t[1][1:]] if t[1].startswith("@") else self.scalar(t[1]))
                if n > len(self.b) - self.i:
                    self.fail(f"array count {n} too large")
                items = []
                for _ in range(n):
                    d = {}
                    self.fields(t[3] if kind == "arr_mask" else t[2], d)
                    items.append(d)
                return items
            if kind == "rep":
                items = []
                for _ in range(t[1]):
                    d = {}
                    self.fields(t[2], d)
                    items.append(d)
                return items
            if kind in ("if", "ifnot"):
                if (cur.get(t[1]) in t[2]) == (kind == "if"):
                    d = {}
                    self.fields(t[3], d)
                    return d
                return None
            if kind == "ifall":
                d = {}
                self.fields(t[2] if all(cur.get(k) in vs for k, vs in t[1]) else t[3], d)
                return d
            if kind == "opaque":
                return self.opaque(t[1], t[2])
            if kind == "objs0":
                items = []
                while True:
                    o = self.obj()
                    if o is None:
                        return items
                    items.append(o)
            raise MfcError(f"bad layout field {t!r}")
        if t in _SCALAR:
            return self.scalar(t)
        if t == "str":
            return self.cstring()
        if t == "obj":
            return self.obj()
        if t in ("ref16", "ref32"):
            r = Ref(self.scalar("u16" if t == "ref16" else "u32"))
            self.refs.append(r)
            return r
        if t.startswith("raw:"):
            n = int(t[4:])
            self.need(n)
            v = bytes(self.b[self.i:self.i + n])
            self.i += n
            return v
        raise MfcError(f"bad layout field {t!r}")

    def opaque(self, nxt: str, nested) -> Opaque:
        nm = nxt.encode("ascii")
        j = self.i
        while True:
            j = self.b.find(nm, j)
            if j < 0:
                self.fail(f"no {nxt} header after the opaque bytes")
            h = j - 6
            if h >= self.i and self.b[h:h + 2] == NEW_CLASS_BYTES and \
                    struct.unpack_from("<H", self.b, h + 4)[0] == len(nm):
                break
            j += 1
        data = bytes(self.b[self.i:h])
        idx = {}
        for name in nested:
            ks = [k for k, e in enumerate(self.table) if isinstance(e, MfcClass) and e.name == name]
            if len(ks) != 1:
                self.fail(f"opaque: class {name} is not in the map exactly once")
            idx[name] = ks[0]
        tags = {struct.pack("<H", CLASS_BIT | k): name for name, k in idx.items()}
        placeholders = []
        for i in range(len(data) - 1):
            name = tags.get(data[i:i + 2])
            if name is not None:
                o = Obj(MfcClass(name, -1))
                self.table.append(o)
                placeholders.append(o)
        self.i = h
        return Opaque(data, placeholders, idx)

    def resolve(self):
        for r in self.refs:
            o = self.table[r.index] if 0 < r.index < len(self.table) else None
            r.target = o if isinstance(o, Obj) else None


# ----------------------------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------------------------

def _cstring(s: str) -> bytes:
    raw = s.encode("utf-16-le")
    n = len(raw) // 2
    if n < 0xFF:
        head = bytes([n])
    elif n < 0xFFFF:
        head = b"\xff" + struct.pack("<H", n)
    else:
        head = b"\xff\xff\xff" + struct.pack("<I", n)
    return b"\xff\xfe\xff" + head + raw


class Writer:
    def __init__(self, layouts: dict, index: dict | None = None, count: int = 1,
                 plan: dict | None = None):
        self.out = bytearray()
        self.layouts = layouts
        self.index = index if index is not None else {}   # id(MfcClass|Obj) -> map index
        self.count = count
        self.plan = plan                                  # id(Obj) -> index (forward refs)
        self.names = {}                                   # id(MfcClass) -> name
        self.refs = []                                    # offsets of written references

    def tag_index(self, k: int, cls: bool):
        if not cls:
            self.refs.append(len(self.out))
        if k < BIG:
            self.out += struct.pack("<H", (CLASS_BIT | k) if cls else k)
        else:
            self.out += struct.pack("<HI", BIG, (BIG_CLASS_BIT | k) if cls else k)

    def obj(self, o):
        if o is None:
            self.out += b"\0\0"
            return
        if id(o) in self.index:
            self.tag_index(self.index[id(o)], False)
            return
        c = o.cls
        if id(c) in self.index:
            self.tag_index(self.index[id(c)], True)
        else:
            nm = c.name.encode("ascii")
            self.out += struct.pack("<HHH", NEW_CLASS, c.schema, len(nm)) + nm
            self.index[id(c)] = self.count
            self.names[id(c)] = c.name
            self.count += 1
        self.index[id(o)] = self.count
        self.count += 1
        self.fields(self.layouts[c.name], o.values)

    def fields(self, lay, vals: dict):
        back = {}
        for k, f in enumerate(lay):
            name, t = _split(f)
            key = name if name is not None else f"_{k}"
            if t == "obj":
                n0 = self.count
                self.obj(vals[key])
                back[key] = self.count == n0
            elif isinstance(t, tuple) and t[0] == "ifback":
                self.fields(t[2] if back[t[1]] else t[3], vals[key])
            elif isinstance(t, tuple) and t[0] == "ifobj":
                self.fields(t[4] if _sub(vals[t[1]], t[2]) in t[3] else t[5], vals[key])
            elif isinstance(t, tuple) and t[0] == "ifall":
                self.fields(t[2] if all(vals.get(n) in vs for n, vs in t[1]) else t[3], vals[key])
            else:
                self.field(t, vals[key])

    def field(self, t, v):
        if isinstance(t, tuple):
            kind = t[0]
            if kind in ("arr", "arr_mask"):
                if kind == "arr" and not t[1].startswith("@"):
                    self.out += struct.pack(_SCALAR[t[1]], len(v))
                for d in v:
                    self.fields(t[3] if kind == "arr_mask" else t[2], d)
                return
            if kind == "rep":
                for d in v:
                    self.fields(t[2], d)
                return
            if kind in ("if", "ifnot"):
                if v is not None:
                    self.fields(t[3], v)
                return
            if kind == "objs0":
                for o in v:
                    self.obj(o)
                self.out += NULL_TAG
                return
            if kind == "opaque":
                for name, k in v.classes.items():
                    now = [i for key, i in self.index.items() if self.names.get(key) == name]
                    if now and now != [k]:
                        raise MfcError(f"opaque bytes tag class {name} as {k}, it is now {now}")
                self.out += v.data
                for o in v.nested:
                    self.index[id(o)] = self.count
                    self.count += 1
                return
        if t in _SCALAR:
            self.out += struct.pack(_SCALAR[t], v)
        elif t == "str":
            self.out += _cstring(v)
        elif t == "obj":
            self.obj(v)
        elif t in ("ref16", "ref32"):
            k = v.index
            if v.target is not None:
                k = self.index.get(id(v.target))
                if k is None and self.plan is not None:
                    k = self.plan.get(id(v.target))
                if k is None:
                    raise MfcError(f"plain reference to an object not in the archive ({v.target})")
            self.refs.append(len(self.out))
            self.out += struct.pack("<H" if t == "ref16" else "<I", k)
        elif t.startswith("raw:"):
            self.out += v
        else:
            raise MfcError(f"bad layout field {t!r}")


# ----------------------------------------------------------------------------------------
# Streams
# ----------------------------------------------------------------------------------------

@dataclass
class Stream:
    """One section of the archive: the root fields (read with the ROOT layout of the
    section kind) and the load-map count at its start and end."""
    kind: str
    root: dict
    start: int
    end: int


def loads(data: bytes, kind: str, layouts: dict | None = None, table: list | None = None,
          start: int | None = None) -> tuple[Stream, list]:
    """Parse a stream. ``kind`` picks the root layout (``"config"``, ``"resolved"``).
    Returns the stream and the load map (pass it on to the next stream of the archive)."""
    lay = LAYOUTS if layouts is None else layouts
    if kind == "resolved":
        first = struct.unpack_from("<I", data, 0)[0]
        if table is not None and len(table) != first:
            raise MfcError(f"stream starts at map count {first}, the previous one ended at "
                           f"{len(table)}")
        start = first
    r = Reader(data, lay, table, CONFIG_START if start is None else start)
    s0 = len(r.table)
    root = {}
    r.stack.append(f"<{kind}>")
    r.fields(lay[f"<{kind}>"], root)
    if r.i != len(data):
        r.fail(f"walk ends at {r.i} of {len(data)} bytes")
    r.resolve()
    return Stream(kind, root, s0, len(r.table)), r.table


def dumps(stream: Stream, layouts: dict | None = None, index: dict | None = None,
          count: int | None = None) -> tuple[bytes, dict, int]:
    """Serialise a stream. Returns (bytes, index map, count at the end); pass the index map
    and count on to the next stream of the archive."""
    lay = LAYOUTS if layouts is None else layouts
    c0 = stream.start if count is None else count
    idx = dict(index or {})
    root = dict(stream.root)
    if stream.kind == "resolved":
        root["start"] = c0                 # the map count this stream continues from
    # dry run to number every object, so plain references may point forward
    dry = Writer(lay, dict(idx), c0)
    dry.fields(lay[f"<{stream.kind}>"], root)
    w = Writer(lay, idx, c0, plan=dry.index)
    w.fields(lay[f"<{stream.kind}>"], root)
    return bytes(w.out), w.index, w.count


@dataclass
class Archive:
    """The two sections of one configuration's archive: ``Contents/Config-N`` (the part,
    units, settings, views) and ``Contents/Config-N-ResolvedFeatures`` (the feature tree)."""
    config: Stream
    resolved: Stream
    table: list
    n: int = 0

    @property
    def features(self) -> list:
        """The FeatureManager's top-level objects, in tree order."""
        return [d["_0"] for d in self.resolved.root["features"]]

    def dumps(self) -> dict:
        """{section name: bytes}, numbered afresh."""
        c, idx, count = dumps(self.config)
        r, _, _ = dumps(self.resolved, index=idx, count=count)
        return {f"Contents/Config-{self.n}": c, f"Contents/Config-{self.n}-ResolvedFeatures": r}


def load_archive(sections: dict, n: int = 0) -> Archive:
    """Parse configuration n's archive from a file's sections (``sldprt.read_sections``).
    Raises MfcError where a layout does not fit (a class not decoded yet, or a desync)."""
    cfg, table = loads(sections[f"Contents/Config-{n}"], "config")
    res, table = loads(sections[f"Contents/Config-{n}-ResolvedFeatures"], "resolved",
                       table=table)
    return Archive(cfg, res, table, n)


# ----------------------------------------------------------------------------------------
# Layouts (docs/SLDPRT_FORMAT.md section 16)
# ----------------------------------------------------------------------------------------

CONFIG_START = 2          # the load map of Contents/Config-N starts at 2 (moNodeName_c is class 4)

LAYOUTS: dict = {}

# The name object of every feature (and of the part): name, flags, feature id.
NODE_NAME = [("name", "str"), "raw:7", ("flag", "u8"), ("id", "u32"), "u32", "str",
             # Parameter names have no feature id and a short suffix. Imported atom names
             # also use -1, but their prefix is nonzero (I1).
             ("suffix", ("ifall", (("id", (0xFFFFFFFF,)), ("_1", (bytes(7),))),
                         ["u16"], ["u32"]))]

# What every feature writes after its name object (folders, planes, sketches, extrudes...).
BLOCK = [
    ("ref", "ref32"), "u32", "u32", "u16",
    ("seq", "u32"),
    "u8", "u32",
    ("created_version", "u32"), ("created_date", "u32"), ("version", "u32"), "u32", "u32",
    ("date", "u32"), "u16",
    "str",
    "u8", "u16", "u16", "u8", "i32", "i32", "f32",
    "raw:14",
    "u32", "raw:6", "i32",
    ("stamp", "raw:8"),
    "u16", "u32", "u32",
]
# absorbed children (an extrude's profile sketch): back references, or whole objects inline
FEATURE = [("name", "obj"), "u16", ("children", ("arr", "u16", ["obj"])), "u16"] + BLOCK[1:]

# Sketch entities (in sgSketch): an id, a common block (layer, colour, style...), then the
# kind's own fields.
SK_ENTITY = [("id", "u32"), ("base", "raw:64")]
# a point: x, y (m, sketch plane), the two segments that meet there (handles or NULL)
SK_POINT = SK_ENTITY + [("kind", "u16"), ("x", "f64"), ("y", "f64"), ("a", "u16"), ("b", "u16"),
                        ("seg0", "obj"), ("seg1", "obj"), ("c", "u16"), "i32", "raw:36"]
# a segment: its end points (indices into the point list); arcs (flag 1) add the centre
# point, four -2 words and a u16
SK_SEG = SK_ENTITY + [("p0", "u16"), ("p1", "u16"), "u32", "f64", ("arc", "u32"),
                      ("arc_data", ("if", "arc", (1, 0xffffffff), [("centre", "u16"), "i32", "i32", "i32",
                                                       "i32", "u16"]))]
# a handle to a sketch entity: its index in its list
SK_HANDLE = [("index", "u16"), "i32", "u32"]


def _relation(arity: int, trailer: list) -> list:
    """A sketch relation of a group: a 28-byte prefix, the type and flags, `arity` entity
    handles, then the group's trailer."""
    return ["raw:8", "i32", "raw:16", ("type", "u32"), "u16", "u16", "u16", "u16", "u16",
            ("entities", ("rep", arity, ["obj"]))] + trailer


SK_REL_GROUPS = [
    ("rel1", _relation(1, [])),
    ("rel2", _relation(2, ["raw:6", "u16", "i32", "i32", "raw:24"])),
    ("rel3", _relation(3, ["raw:6"])),
] + [(f"rel{k}", None) for k in range(4, 9)]


def _rel_fields() -> list:
    out = []
    for name, lay in SK_REL_GROUPS:
        out.append((f"n_{name}", "u16"))
        out.append((name, ("arr", f"@n_{name}", lay if lay is not None else [])))
    return out


SG_SKETCH = [
    ("npts", "u32"), ("points", ("arr", "@npts", ["u16", "u32"] + SK_POINT)),
    ("seglists", ("rep", 3, [("n", "u32"), "u16", ("segs", ("arr", "@n", ["u32"] + SK_SEG))])),
    "raw:30", ("radial_dims", ("arr", "u16", ["obj"])),
    ("linear_dims", ("arr", "u16", ["obj"])), ("dim_tail", "raw:4"),
] + _rel_fields() + [
    ("list1", "obj"), "raw:12", "u16", "u16", ("table", ("rep", 18, ["u32"])), "str", "raw:18",
    "raw:20", "u32", "raw:36", ("list2", "obj"),
]
# Keep the existing public field keys after splitting the old 38-byte dimension block.
SG_SKETCH = [(f"_{i - 3}", f) if i >= 7 and isinstance(f, str) else f
             for i, f in enumerate(SG_SKETCH)]
# A collinear relation may store an extension-line display indicator in this block.
_indicator_index = next(i for i, item in enumerate(SG_SKETCH) if isinstance(item, tuple) and item[0] == '_29')
SG_SKETCH[_indicator_index:_indicator_index + 1] = [
    ('_29', 'raw:12'), ('extension_indicator', 'obj'), ('indicator_tail', 'raw:22')]
# what a sketch feature writes after its sgSketch; the plane it lies on comes last
SKETCH_TAIL = [("chains_count", "u32"), "u32", ("chains", ("arr", "@chains_count", ["obj"]))]
SKETCH_PLANE = ["u32", "u32", "raw:7", "u32", ("axis_prefix", "u16"),
                ("axis", "obj"), ("plane", "obj")]

# Units (moUnitsTable_c): what every unit class writes first
UNITS = ["u32", "u32", "f64", "u32", "u32", "f64", "u32", "u32", "u32", "u32"]

# A component of another feature (the history list's entries, a sketch's plane): a unit
# component object, then the referenced feature's id
COMP = [("component", "obj"), "raw:67", ("version", "u32"), ("id", "u32"), ("stamp", "u32")]

# An extrude's face choosers: the faces per body and their box; a second chooser and box follow
# when the first box says so, else 12 bytes (seen with sketches of several loops)
DIRECTIONS = [("bodies", "obj"), ("bbox", "obj"),
              ("second", ("ifobj", "bbox", "more", (1,), [("bodies2", "obj"), ("bbox2", "obj")],
                          ["u32", "u32", "u32"]))]

# The data of the part's PMark record (Config-0)
PMARK_DATA = [("n_seqs", "u32"), ("seqs", ("arr", "@n_seqs", ["u32"])), "raw:36", ("list", "obj"),
              "raw:4", "str", "str"]

# Folders of the FeatureManager tree
FOLDER = FEATURE + ["u32", "u32"]

LAYOUTS.update({
    "<resolved>": [("start", "u32"), ("features", ("arr", "u16", ["obj"])), "u32"],
    "<config>": [("part", "obj")],
    "moPart_c": ["u32", "u32", ("version", "u32"), ("name", "obj"), ("visual", "obj"),
                 ("units", "obj"), ("unit_component", "obj"), ("curvature", "obj"),
                 ("trans_ref_plane", "obj"), ("xhatch", "obj"), ("material", "obj"),
                 ("model_data", "obj"), ("relations", "obj"), ("environment", "obj"),
                 ("list", "obj"), ("views", ("rep", 10, ["obj"])), "u8", "u32", "u32",
                 ("colours", "obj"),
                 ("blocks", "obj"), ("annotation_views", "obj"), "u32", "u32", ("n_ids", "u32"),
                 ("ids", ("arr", "@n_ids", ["u32"])), "raw:30", ("pmarks", "obj"),
                 # without a PMark record (parts saved with an Imported body) the part writes
                 # the record's data itself
                 ("pmark_data", ("ifback", "pmarks", ["u32"] + PMARK_DATA, [])),
                 ("threads", "obj"), ("exploded_views", "obj")],
    "moVisualProperties_c": ["raw:12", "str", "str", "raw:72", "str", "raw:16", "str", "raw:81",
                             "str", "raw:12", "str", "raw:76", "str", "raw:4", "str", "str",
                             "str", "raw:92", "str", "raw:8", "str", "raw:8", "str", "raw:24"],
    "moUnitsTable_c": [("units", ("arr", "u16", ["obj"]))],
    "moLengthUserUnits_c": UNITS + ["raw:14", "u16", ("dual", "obj")],
    "moAngleUserUnits_c": UNITS + ["raw:16"],
    "moNumberUserUnits_c": UNITS + ["raw:14"],
    "moDensityUnits_c": UNITS + ["raw:14", "u16", "u16", ("has_length", "u8"),
                                 ("length", ("if", "has_length", (1,), ["obj"])), "u16", "u32"],
    "moFloatNumberUserUnits_c": UNITS + ["raw:14"],
    "moSpringConstantUnits_c": UNITS + ["raw:14"],
    "moUnitSysUnits_c": UNITS + ["raw:16"],
    "moForceUnits_c": UNITS + ["raw:16"],
    "moStressUnits_c": UNITS + ["raw:14"],
    "moGravityUnits_c": UNITS + ["raw:14"],
    "moLinearMotorUnits_c": UNITS + ["raw:14"],
    "moRotaryMotorUnits_c": UNITS + ["raw:14"],
    "moPowerUnits_c": UNITS + ["raw:16"],
    "moEnergyUnits_c": UNITS + ["raw:16"],
    "moTimeUnits_c": UNITS + ["raw:16"],
    "moFrequencyUserUnits_c": UNITS + ["raw:14"],
    "moUnitComponent_c": ["raw:12"],
    "gcCurvatureObject_c": ["raw:48"],
    # a flag, then 10 doubles (the model's box) when the part has a solid
    "moTransRefPlaneData_c": ["raw:24", ("has_box", "u8"),
                              ("box", ("if", "has_box", (1,), [("rep", 10, ["f64"])])), ("tail", "raw:146")],
    "gcXhatch_c": ["str", "raw:40"],
    "moMaterial_c": [("density", "obj")],
    "moDensityParameter_c": ["raw:18", "str", "raw:11"],
    "uoModelData_c": [("head", "raw:48"), ("atoms", ("arr", "u32", ["obj"])), ("version", "u32"), "u32",
                      "u32", ("point_records", ("arr_mask", "_4", 0x0fffffff, ["raw:32"]))],
    # document settings (drafting standard, dimension and note styles, fonts...): not decoded
    # yet; they end where the environment folder starts and hold length-units objects
    "moRelMgr_c": [("settings", ("opaque", "moEnvFolder_c", ("moLengthUserUnits_c",)))],
    "moEnvFolder_c": [("name", "obj"), "u16", ("lights", ("arr", "u16", ["obj"])), ("data", "raw:47"),
                      "str", "raw:74"],
    "moAmbientLight_c": [("name", "obj"), "raw:51", "str", "raw:112", "str", "raw:36"],
    "moDirectionLight_c": [("name", "obj"), "raw:51", "str", "raw:112", "str", "raw:68"],
    "suObList": [("items", ("arr", "u16", ["obj"]))],
    # a named view (*Normal To, *Front, ... *Dimetric): optional rotation
    "moView_c": [("name", "str"), ("rot", "u8"), ("matrix", ("if", "rot", (1,), [("rep", 9, ["f64"])])),
                 "raw:121"],
    "moFeatColorTab_c": ["raw:18"],
    "moSketchBlockMgr_c": ["raw:18", "str", "raw:55", "str", ("tail", "raw:94")],
    "moAnnotationView_c": [("name", "str"), ("rot", "u8"),
                           ("matrix", ("if", "rot", (1,), [("rep", 9, ["f64"])])), "raw:169"],
    # a record whose second word selects the form: 1 = a list and two strings (Config-0),
    # 0 = 58 bytes (in features)
    "moPMarkRecord_c": [("seq", "u32"), ("full", "u32"),
                        ("data", ("if", "full", (1,), PMARK_DATA)),
                        ("short", ("ifnot", "full", (1,), ["raw:58"]))],
    # cosmetic-thread manager: not decoded yet (field order varies with the model)
    "moCThreadRefMgr_c": [("data", ("opaque", "moPrtExplViewManager_c", ()))],
    "moPrtExplViewManager_c": ["raw:18", "str", "raw:55", "str", "raw:122"],
    # a name object (imported parts: the translator's name), or NULL and the same fields inline
    "moAtom_c": [("name", "obj"),
                 ("inline", ("ifback", "name", ["raw:7", ("flag", "u8"), ("id", "i32"), "u32", "str",
                                                "u32"], [])),
                 ("head", "raw:12"), "u32", ("feature_id", "u32"), ("feature_id2", "u32"), "u32",
                 "raw:12", "u32", ("next_id", "i32"), "u32", ("next_id2", "u32"), "u32"],
    "moNodeName_c": NODE_NAME,
    "moCommentsFolder_c": FOLDER,
    "moFavoriteFolder_c": FOLDER + [("items", ("arr", "u32", ["obj"])), "u32"],
    "moHistoryFolder_c": FOLDER + [("items", ("arr", "u16", ["obj", "obj"]))],
    "moHistoryFeatItemData_c": ["u16", "u8", "raw:6", ("flag", "u8"), ("id", "i32"), "u32", "str"],
    "moCompFeature_c": COMP,
    # the plane a sketch lies on: a component of the plane feature (its id), then display data
    "moCompRefPlane_c": COMP + ["u16", "u32", ("rot", "u8"),
                                ("matrix", ("if", "rot", (1,), [("rep", 9, ["f64"])])), "raw:24",
                                "f64", "raw:3", "u32", "i32", "u32", "u32", "u32", "raw:8"],
    # a closed chain of sketch segments (their ids), as a profile uses it
    "moSketchChain_c": [("segments", ("arr", "u16", ["u32"])), "u32", "u16", "u32", "i32",
                        "raw:8", "u32", "u32", "str", "u16", "i32", "raw:20"],
    "moSelectionSetFolder_c": FOLDER + [("sets", ("arr", "u16", ["obj"])), "u32"],
    "moSensorFolder_c": FOLDER,
    "moDocsFolder_c": FOLDER,
    "moDetailCabinet_c": [("name", "obj"), "u16", "u16"],
    # the Notes folder holds an end-tag folder of its own class, which points back at it
    "moNotesAreaFtrFolder_c": FOLDER + [("child", "obj"), ("ifback", "child",
                                        ["u32", "u32", "u32", "u32", "u32", "u32", "u32", "u32"],
                                        BLOCK + ["u32", "u16", "f64", "f64", "f64", "u16"])],
    "moSurfaceBodyFolder_c": FOLDER + ["u32", "u32", "u32"],
    "moSolidBodyFolder_c": FOLDER + ["u32", "u32", "u32", "ref16"],
    "moInkMarkupFolder_c": FOLDER,
    "moEqnFolder_c": FOLDER,
    "moMaterialFolder_c": FOLDER + ["str", "u32"],
    "moRefPlane_c": FEATURE + ["raw:38", "u32", "raw:16", "u32", "raw:54", "f64", "f64", "f64",
                               "u32", "raw:10", ("data", "obj")],
    # a default plane: origin, normal, optional rotation (flag 1: 9 doubles), display box
    "moDefaultRefPlnData_c": [("origin", ("rep", 3, ["f64"])), ("normal", ("rep", 3, ["f64"])),
                              ("rot", "u8"), ("matrix", ("if", "rot", (1,), [("rep", 9, ["f64"])])),
                              ("rep", 4, ["f64"]), "u8", ("box", ("rep", 4, ["f64"])),
                              "u16", "u32", "i32", "i32", "u8", "f64", "f64", "f64", "u32",
                              "raw:12"],
    "moOriginProfileFeature_c": FEATURE + ["raw:14", ("sketch", "obj"), "raw:30"] + SKETCH_TAIL
                                + SKETCH_PLANE,
    "moProfileFeature_c": FEATURE + ["raw:14", ("sketch", "obj"), "raw:30"] + SKETCH_TAIL
                          + SKETCH_PLANE,
    "sgSketch": SG_SKETCH,
    "moExtrusion_c": FEATURE + [("counters", "raw:48")] + DIRECTIONS + [("pmark", "obj"), ("end_spec", "obj")],
    # an Instant3D boss / cut: what it merges into (moCompSolidBody_c), then as an extrude
    "moICE_c": FEATURE + [("counters", "raw:46"), ("merge", "obj")] + DIRECTIONS + [("pmark", "obj"),
                                                                     ("end_spec", "obj")],
    # an Imported body (the B-rep is in Config-0-FeatureBodies): the extrude's 48 bytes, 22
    # more, its PMark, then the body's name
    "moBaseBody_c": FEATURE + [("counters", "raw:48"), "raw:22", ("pmark", "obj"), "str", "u32",
                               ("body_name", "str"), "u32"],
    "moCompSolidBody_c": [("component", "obj"), "raw:67", ("version", "u32"), ("face", "obj"),
                          ("n", "u32"), ("seqs", ("arr", "@n", ["u32"])), ("id", "u32"), "raw:8",
                          "u32", "u16", "u16"],
    "moEndFace3IntSurfIdRep_c": ["u16", ("face", "obj"), "u32", "u32", "u32"],
    "moFromSktEnt3IntSurfIdRep_c": ["u16", ("face", "obj"), ("segment", "u32"), "i32", "u32"],
    # end condition (blind...), then its dimension chain: display dimension -> handle ->
    # ParallelPlaneDistanceDim_c -> moLengthParameter_c (D1, the depth in m)
    "moEndSpec_c": ["raw:24", ("dim", "obj"), "raw:76", ("from_spec", "obj")],
    "moDisplayDistanceDim_c": ["raw:420", "str", "raw:132", ("handle", "obj")],
    "moFeatureDimHandle_c": ["raw:107", ("dim", "obj"), "raw:124", ("rot", "u8"),
                            ("matrix", ("if", "rot", (1,), [("rep", 9, ["f64"])])), "raw:41",
                            "str", "str", "str", "str", "raw:262", "str", "u32", "i32",
                            ("value", "f64"), "u32", "u32", "u32", "u32", "f64", "str",
                            ("favourite", "obj")],
    "ParallelPlaneDistanceDim_c": ["u32", ("param", "obj")],
    "moLengthParameter_c": [("name", "obj"), ("value", "f64"), "raw:3", ("id", "u32"), "raw:16"],
    "moFavoriteHandle_c": ["raw:206", "str", "str", "str", "str", "raw:92", "str", "str", "i32",
                           "raw:8", "str", "raw:40", "str", "raw:54"],
    "sgPntPntDist": ["u32", ("param", "obj"), ("type", "u32"), "u32", "u32",
                     ("p0", "obj"), ("p1", "obj"), "raw:34"],
    "sgCircleDim": ["u32", ("param", "obj"), ("type", "u32"), "u32", "u32",
                    ("arc", "obj"), ("entity", "obj"), "raw:36"],
    "sgEntHandle": ["raw:10"],
    "moDisplayRadialDim_c": ["raw:420", "str", "raw:132", ("handle", "obj")],
    "moSkDimHandleValG2_c": ["raw:107", ("sketch", "ref16"), ("index", "u16"),
                            ("_2", "raw:8"), ("_3", "str"), ("_4", "str"), ("_5", "str"), ("_6", "str"),
                            ("_7", "raw:262"), ("_8", "str"), ("_9", "u32"), ("_10", "i32"),
                            ("value", "f64"), ("_12", "u32"), ("_13", "u32"), ("_14", "u32"),
                            ("_15", "u32"), ("_16", "f64"), ("_17", "str"), ("favourite", "obj")],
    "moFromEndSpec_c": ["raw:36"],
    "moPerBodyChooserData_c": [("faces", ("arr", "u16", ["obj"])), "u16", "u16"],
    # a face of the body: n id records (the end face, one per sketch segment...; NULLs included)
    "moFaceRef_c": ["u32", "u32", ("n", "u32"), "raw:24", "u16", ("ids", ("arr", "@n", ["obj"])),
                    "raw:8", ("version", "u32"), "raw:8"],
    # face ids: the face record of the feature that made the face (+ the sketch segment)
    "moEndFaceSurfIdRep_c": ["u16", ("face", "obj"), "u32", "u32"],
    "moFromSktEntSurfIdRep_c": ["u16", ("face", "obj"), ("segment", "u32")],
    "moFR_c": [("owner", "obj"), ("id", "u32"), ("stamp", "u32")],
    "moExtObject_c": [("path", "obj"), ("name", "obj"), "u16", "u8", "u32", "str", "str", "str",
                      "raw:18", "str", "raw:16"],
    "moCStringHandle_c": ["str"],
    # `more` = 1 when the feature's second face chooser and box follow (DIRECTIONS)
    "moBBoxCenterData_c": ["u32", ("box", ("rep", 4, ["f64"])), "raw:8", ("n", "u32"),
                           ("arr", "@n", ["u32"]), "u16",
                           ("owner", "obj"), "u16", ("b", "u16"), "u16", ("owner2", "obj"),
                           ("more", "u16"), "u16", "u16", ("if", "b", (1,), ["u16"])],
    "sgLineHandle": SK_HANDLE,
    "sgArcHandle": SK_HANDLE,
    "sgPointHandle": SK_HANDLE,
    "moExtensionLineIndicator_c": [("entity", "obj"), "raw:4"],
})
LAYOUTS["moSkDimHandleRadial_c"] = LAYOUTS["moSkDimHandleValG2_c"] + ["raw:52", "str", "raw:18"]

# Revolve records, derived from SW2024 full/partial, reverse and midplane donors.
LAYOUTS.update({
    "moRevolution_c": FEATURE + [("counters", "raw:18"), ("has_merge", "u32"),
        ("body_data", ("ifall", (("has_merge", (1,)),),
                       [("tail", "raw:24"), ("merge", "obj")], [("tail", "raw:26")]))] + DIRECTIONS + [
        ("pmark", "obj"), "raw:9", ("annotations", "obj"), "u32", "u32",
        ("axis", "obj"), ("end_spec", "obj")],
    "moLineRef_w": [("entity", "obj"), "raw:12", ("sketch_seq", "u32"), "raw:113"],
    "moCompSketchEntHandle_c": COMP + [("segment", "obj")],
    "moRevEndSpec_c": ["raw:52", ("dim", "obj"), "raw:42"],
    "moDisplayAngularDim_c": LAYOUTS["moDisplayDistanceDim_c"],
    "AngleDim_c": ["u32", ("param", "obj")],
    "moAngleParameter_c": LAYOUTS["moLengthParameter_c"],
})
LAYOUTS["moRevCut_c"] = LAYOUTS["moRevolution_c"]
LAYOUTS["moFeatureDimHandle_c"][2] = ("geometry", ("ifobj", "dim", "$class", ("AngleDim_c",),
                                                ["raw:76"], ["raw:124"]))
LAYOUTS["moFeatureDimHandle_c"][5] = ("placement", ("ifobj", "dim", "$class", ("AngleDim_c",),
                                                 ["raw:43"], ["raw:41"]))

# Standalone model headers of the bundled single-configuration body donors.
LAYOUTS.update({
    "<model_header>": [("header", "obj")],
    "moHeader_c": ["u32", ("users", "obj"), ("notes", "obj"), ("logs", "obj"),
                   ("counters", "raw:14"), ("external", "obj"), "raw:42",
                   ("extra", "obj"), "raw:172"],
    "su_CStringArray": [("items", ("arr", "u16", ["str"]))],
    "moLogs_c": [("items", ("arr", "u16", ["obj"])), ("id", "u32"), ("name", "str")],
    "moStamp_c": ["raw:6", "u32", "str"],
})

# Constant edge fillet/chamfer records from SW2024 saved-file donors.
# The finishing-feature prefix differs from the extrusion/revolve body counters.
# Edge refs have a four-byte tail; face refs have eight. Multi-edge donors expose
# this distinction, which is hidden by zero padding in a one-edge donor.
EDGE_FINISH = FEATURE + [("counters", "raw:62"), ("pmark", "obj"),
    ("surfaces", ("arr", "u32", ["obj"])), ("edges", ("arr", "u16", ["obj"]))]
LAYOUTS.update({
    "Chamfer_c": EDGE_FINISH + [("edge_components", "obj"), "obj", "obj",
        ("dim1", "obj"), ("dim2", "obj"),
        ("dim3", ("ifobj", "dim2", "$class", (None,), ["obj"], [])),
        ("placement", ("ifobj", "dim2", "$class", (None,), ["raw:3"], ["raw:40"])),
        ("edge", "obj"), "raw:54"],
    "Fillet_c": EDGE_FINISH + ["raw:6", ("edge_components", "obj"),
        ("list1", "obj"), ("list2", "obj"), ("list3", "obj"), "raw:12", ("radii", "obj"),
        "raw:58", ("faces", ("arr", "u32", ["obj", "obj"])), "raw:26",
        ("values", ("arr", "u32", ["u32", "u32", "f64", "obj"])), "raw:42"],
    "moSurfaceIdRep_c": ["ref16", ("face", "obj")],
    "moFilletSurfIdRep_c": ["ref16", ("face", "obj"), "u32"],
    "moCompFace_c": [("component", "obj"), "raw:67", ("version", "u32"),
                     "u8", ("face", "obj"), "u16"],
    "moCompEdge_c": [("component", "obj"), "raw:67", ("version", "u32"),
                     "u8", ("edge", "obj"), "raw:62"],
    "ThreeDRadiusDim_c": ["u32", ("param", "obj")],
    "edgeRadiusObject_c": [("dim", "obj"), "raw:52", "str", "raw:18", ("edge", "obj"), "raw:5", "obj"],
    "moEdgeRef_c": LAYOUTS["moFaceRef_c"][:-1] + ["raw:4"],
})
LAYOUTS["moFeatureDimHandle_c"][2] = ("geometry", ("ifobj", "dim", "$class",
    ("AngleDim_c", "ThreeDRadiusDim_c"), ["raw:76"], ["raw:124"]))
# Saved face-attached sketch planes (SW2024). Preserve existing positional keys.
def _named_layout(layout):
    return [(name if name is not None else f'_{i}', typ)
            for i, field in enumerate(layout) for name, typ in [_split(field)]]

# The common point block contains a nullable external-entity reference. Preserve the
# original positional keys while exposing it (a NULL reference occupies two bytes).
_point = _named_layout(['u16', 'u32'] + SK_POINT)
_base_index = next(i for i, field in enumerate(_point) if field[0] == 'base')
_point[_base_index:_base_index + 1] = [('base', 'raw:35'), ('external', 'obj'), ('base_tail', 'raw:27')]
LAYOUTS['sgSketch'][1] = ('points', ('arr', '@npts', _point))
LAYOUTS.update({
    'sgExtEnt_c': [('reference', 'obj')],
    'moSketchExtRef_w': [('entity', 'obj'), 'raw:66', ('back', 'obj')],
    'moPointBackedUpData_c': ['raw:30', 'str'],
    'moSketchRegion_c': [('segments', 'obj')],
})

_profile = LAYOUTS['moProfileFeature_c'][:-len(SKETCH_PLANE)]
LAYOUTS['moProfileFeature_c'] = _profile + [
    'u32', ('cache_flag', 'u8'), ('cache', ('arr', 'u32', ['u8'])), 'raw:4',
    ('edges', ('arr', 'u16', ['obj'])),
    ('cache_tail', ('if', 'cache_flag', (0,),
                    ['u32', 'u32', 'u32', ('arr', 'u32', ['u32']), 'u32'])),
    'u32', ('axis_prefix', 'u16'), ('axis', 'obj'), ('plane', 'obj')]
_plane = _named_layout(LAYOUTS['moRefPlane_c'])
LAYOUTS['moRefPlane_c'] = [_plane[0],
    ('anonymous', ('ifobj', 'name', '$class', (None,), NODE_NAME[1:-1] + ['u32'], []))] + _plane[1:]
_standard = LAYOUTS['moCompRefPlane_c'][len(COMP):]
LAYOUTS['moCompRefPlane_c'] = COMP + [
    ('inline', ('if', 'id', (0xffffffff,), ['obj'])),
    ('tail', ('ifall', (('id', (0xffffffff,)),),
        ['u16', ('rot', 'u8'), ('matrix', ('if', 'rot', (1,), [('rep', 9, ['f64'])])),
         'raw:24', 'f64', 'u8', 'u32', 'i32', 'u32', 'u32', 'u32', 'raw:8'], _standard))]
LAYOUTS['moFaceRefPlnData_c'] = LAYOUTS['moDefaultRefPlnData_c'][:12] + [
    'raw:16', ('face', 'obj'), 'raw:220', ('rot2', 'u8'),
    ('matrix2', ('if', 'rot2', (1,), [('rep', 9, ['f64'])])),
    ('translation2', ('rep', 3, ['f64'])), 'f64', 'u8']

# A plane component ends at its reference (or inline anonymous plane). The following
# transform/display tail belongs to the sketch. Plane-to-plane references have no such
# sketch tail; keeping it on the component consumed the offset-plane definition.
_component_plane = LAYOUTS['moCompRefPlane_c']
_short_plane_tail = _component_plane[-1][1][2]
_standard_plane_tail = _component_plane[-1][1][3]
LAYOUTS['moCompRefPlane_c'] = _component_plane[:-1]
for _profile_class in ('moProfileFeature_c', 'moOriginProfileFeature_c'):
    LAYOUTS[_profile_class] += [('plane_tail', ('ifobj', 'plane', 'id', (0xffffffff,),
                                              _short_plane_tail, _standard_plane_tail))]
_face_plane = _named_layout(LAYOUTS['moFaceRefPlnData_c'])
LAYOUTS['moFaceRefPlnData_c'] = _face_plane[:14] + [
    ('attachment', ('ifobj', 'face', '$class', ('moCompRefPlane_c',),
                    ['raw:98', ('origin2', ('rep', 3, ['f64'])),
                     ('normal2', ('rep', 3, ['f64'])), ('rot2', 'u8'),
                     ('matrix2', ('if', 'rot2', (1,), [('rep', 9, ['f64'])])),
                     ('translation2', ('rep', 3, ['f64'])), 'f64', 'raw:25',
                     ('dimension', 'obj'), 'raw:16'], _face_plane[14:]))]
_reference_plane_frame = LAYOUTS['moFaceRefPlnData_c'][-1][1][4][:-2]
LAYOUTS.update({
    'moConstraintMidPlaneRefplaneData_c': LAYOUTS['moDefaultRefPlnData_c'][:12] +
        ['raw:16', ('interface', 'obj')],
    'moConstraintRefplaneInterfaceData_c': [('first', 'obj'), ('second', 'obj'), ('third', 'obj')],
    'moRefPlaneMidPlaneGeom_c': [('reference', 'obj'), 'raw:41'],
    'moRefPlaneCoincidentGeom_c': [('reference', 'obj'), 'raw:40'],
    'moPlaneRef_w': [('entity', 'obj'), ('frame', ('rep', 1, _reference_plane_frame))],
})
LAYOUTS['moConstraintCoincPlaneRefplaneData_c'] = LAYOUTS['moConstraintMidPlaneRefplaneData_c']
