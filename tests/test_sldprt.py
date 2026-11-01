"""SolidWorks file reader (bauyrcad.core.sldprt): containers, Parasolid streams, display mesh,
feature tree, KeyWords, the 3DExperience store and mates.

Synthetic streams test each decoding layer; tests/data/sw holds small files saved by real
SolidWorks 2024 from BauyrCAD exports (made by the real-SolidWorks tests' scripts, with the
SolidWorks API's own answers written into the assertions below)."""

import json
import math
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from bauyrcad.core import sldprt

SW_SAMPLE = Path(r"C:\Users\Public\Documents\SOLIDWORKS\SOLIDWORKS 2024\CAM Examples"
                 r"\tutorial_parts\Assemblies\bolt.sldprt")
DATA = Path(__file__).parent / "data" / "sw"


def _section(name: str, body: bytes, sig=b"\x6a\xac\xe4\x5d") -> bytes:
    """One section of the 2015+ container, as SolidWorks writes it."""
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = comp.compress(body) + comp.flush()
    swapped = bytes(((x >> 4) | (x << 4)) & 0xFF for x in name.encode())
    return (sig + b"\x14\x00\x06\x00\x08\x00" + struct.pack("<I", 0x16A1B931)
            + struct.pack("<IIII", zlib.crc32(body), len(raw), len(body), len(swapped))
            + swapped + raw)


def _array(es, ty, payload, n):
    return struct.pack("<IIII", es, ty, 2, n) + payload


def _cube_display_list(a=10.0) -> bytes:
    """Six quad faces of an a-mm cube as triangle strips, like uoTempFaceTessData_c."""
    c = np.array([[x, y, z] for x in (0, a) for y in (0, a) for z in (0, a)]) / 1000.0
    quads = {  # strip order v0 v1 v2 v3, outward normal
        (-1, 0, 0): [0, 1, 2, 3], (1, 0, 0): [4, 6, 5, 7], (0, -1, 0): [0, 4, 1, 5],
        (0, 1, 0): [2, 3, 6, 7], (0, 0, -1): [0, 2, 4, 6], (0, 0, 1): [1, 5, 3, 7]}
    out = b"uoTempFaceTessData_c" + b"\0" * 8
    for n, q in quads.items():
        v = c[q].astype("<f4").tobytes()
        nn = np.tile(np.array(n, "<f4"), (4, 1)).tobytes()
        out += _array(4, 8, struct.pack("<i", 4), 1) + _array(12, 0x64, v, 4)
        out += _array(12, 0x64, nn, 4) + _array(4, 8, struct.pack("<6i", 1, 5, 9, 0, 7, 3), 6)
        out += b"\0" * 40                    # bounding box / edge data the reader skips
    return out


def _partition() -> bytes:
    def ps(kind):
        head = f": TRANSMIT FILE ({kind}) created by modeller version 3501256".encode()
        sch = b"SCH_3501256_35102_13006"
        return (b"PS" + struct.pack(">I", len(head)) + head
                + struct.pack(">I", len(sch)) + sch + b"\0" * 32)
    out = b""
    for body in (ps("partition"), ps("deltas")):
        z = zlib.compress(body)
        chunk = sldprt.ZLB_TAG + struct.pack("<II", len(body), len(z)) + z + b"\0" * 8
        out += struct.pack("<I", len(chunk)) + chunk
    return out


def synthetic_sldprt() -> bytes:
    return (b"\xec\x29\xc1\x41\x00\x00\x00\x04" + b"\x11" * 8
            + _section("Contents/Config-0-Partition", _partition())
            + _section("Contents/DisplayLists", _cube_display_list())
            + _section("PreviewPNG", b"\x89PNG fake"))


def test_sw2015_container_sections_and_names():
    data = synthetic_sldprt()
    assert sldprt.container(data) == "sw2015"
    s = sldprt.read_sections(data)
    assert set(s) == {"Contents/Config-0-Partition", "Contents/DisplayLists", "PreviewPNG"}
    assert s["PreviewPNG"] == b"\x89PNG fake"
    assert sldprt.configurations(s) == [0]


def test_parasolid_streams_are_found_and_labelled():
    t = sldprt.parasolid(sldprt.read_sections(synthetic_sldprt()))
    assert [x.kind for x in t] == ["partition", "deltas"]
    assert t[0].modeller == 3501256 and t[0].schema == "SCH_3501256_35102_13006"
    assert t[0].data.startswith(b"PS")


def test_display_mesh_is_a_closed_outward_cube(tmp_path):
    m = sldprt.display_mesh(sldprt.read_sections(synthetic_sldprt()))
    assert (m.n_faces, len(m.triangles), len(m.points)) == (6, 12, 8)
    assert m.volume() == pytest.approx(1000.0, rel=1e-6)      # outward -> positive volume
    assert m.area() == pytest.approx(600.0, rel=1e-6)
    edges = np.sort(np.vstack([m.triangles[:, [0, 1]], m.triangles[:, [1, 2]],
                               m.triangles[:, [2, 0]]]), axis=1)
    assert (np.unique(edges, axis=0, return_counts=True)[1] == 2).all()   # watertight
    sldprt.write_stl(m, tmp_path / "cube.stl")
    assert (tmp_path / "cube.stl").stat().st_size == 84 + 50 * 12


def test_not_a_solidworks_file():
    with pytest.raises(sldprt.SldprtError):
        sldprt.container(b"ISO-10303-21;" + b"\0" * 64)


@pytest.mark.skipif(not SW_SAMPLE.exists(), reason="SolidWorks 2024 sample parts not installed")
def test_real_solidworks_2024_sample():
    s = sldprt.read_sections(SW_SAMPLE)
    t = sldprt.parasolid(s)
    assert t[0].kind == "partition" and t[0].modeller >= 2900000
    m = sldprt.display_mesh(s)
    lo, hi = m.bounds()
    assert hi[2] - lo[2] == pytest.approx(120.65, abs=1e-3)       # 4.75 in bolt
    assert m.volume() > 0


# ========================================================================================
# Feature tree, KeyWords, 3DExperience store, mates - synthetic streams
# ========================================================================================

class Pairs(list):
    """A MessagePack map written from (key, value) pairs, so a key may repeat."""


def mp(v) -> bytes:
    """Minimal MessagePack writer for the synthetic 3DExperience store."""
    if v is None:
        return b"\xc0"
    if isinstance(v, bool):
        return b"\xc3" if v else b"\xc2"
    if isinstance(v, int):
        return bytes([v]) if 0 <= v < 128 else b"\xd2" + struct.pack(">i", v)
    if isinstance(v, float):
        return b"\xcb" + struct.pack(">d", v)
    if isinstance(v, str):
        e = v.encode()
        return (bytes([0xA0 | len(e)]) if len(e) < 32 else b"\xd9" + bytes([len(e)])) + e
    if isinstance(v, (Pairs, dict)):
        items = list(v.items()) if isinstance(v, dict) else list(v)
        head = bytes([0x80 | len(items)]) if len(items) < 16 else b"\xde" + struct.pack(">H", len(items))
        return head + b"".join(mp(k) + mp(x) for k, x in items)
    if isinstance(v, list):
        return bytes([0x90 | len(v)]) + b"".join(mp(x) for x in v)
    raise TypeError(v)


def unqlite(docs: dict, overflow_key=None, page=4096) -> bytes:
    """An UnQLite file: header page, one bucket page of chained cells; the cell of
    overflow_key keeps its key and data on an overflow page."""
    head = (b"unqlite" + struct.pack(">IIII", 0xDB7C2712, 0, 512, page)
            + struct.pack(">H", 4) + b"hash").ljust(page, b"\0")
    bucket = bytearray(page)
    over = bytearray(page)
    off = 16
    struct.pack_into(">H", bucket, 0, off)
    items = list(docs.items())
    for n, (key, value) in enumerate(items):
        kb, data = key.encode(), mp(value)
        spill = key == overflow_key
        size = 26 + (0 if spill else len(kb) + len(data))
        nxt = off + size if n + 1 < len(items) else 0
        struct.pack_into(">IIQHQ", bucket, off, 0, len(kb), len(data), nxt, 2 if spill else 0)
        if spill:
            over[18:18 + len(kb) + len(data)] = kb + data
        else:
            bucket[off + 26:off + size] = kb + data
        off += size
    return head + bytes(bucket) + bytes(over)


def cstr(text: str) -> bytes:
    return b"\xff\xfe\xff" + bytes([len(text)]) + text.encode("utf-16-le")


def new_class(name: str) -> bytes:
    return b"\xff\xff" + struct.pack("<HH", 1, len(name)) + name.encode()


def record(tag: bytes, name: str, fid: int, flag=0x40, children=b"", n=0, suppressed=0,
           inline=b"") -> bytes:
    """[class tag][0x8004][name][7 bytes][flag][id][0][''][6 x 0][n][children][0][suppressed]"""
    body = tag + b"\x04\x80" + cstr(name) + b"\0" * 7 + bytes([flag]) + struct.pack("<I", fid)
    body += struct.pack("<I", 0) + b"\xff\xfe\xff\x00" + b"\0" * 6 + struct.pack("<H", n)
    if inline:
        return body + inline + b"\0" * 24           # owner's own fields after inline children
    return body + children + struct.pack("<II", 0, suppressed) + b"\0" * 24


K_SKETCH = 0x120          # class index of moProfileFeature_c (the stream starts at 0x70)


def resolved_features() -> bytes:
    ref = struct.pack("<H", 0x8000 | K_SKETCH)
    out = struct.pack("<I", 0x70) + b"\x00\x00"
    out += record(new_class("moCommentsFolder_c"), "Comments", 8)
    out += record(new_class("moOriginProfileFeature_c"), "Origin", 5, flag=0xC0)
    out += record(new_class("moProfileFeature_c"), "Sketch1", 26)
    # the extrude absorbed Sketch1 (stored before it: back reference to its object index)
    out += record(new_class("moExtrusion_c"), "Boss-Extrude1", 32, n=1,
                  children=struct.pack("<H", K_SKETCH + 1), suppressed=1)
    out += record(new_class("Fillet_c"), "Fillet1", 34, suppressed=1)
    # Hole Wizard stores its two sketches inline, right after its own header
    out += record(new_class("moHoleWzd_c"), "CBORE for M6 Screw1", 37, n=2,
                  inline=record(ref, "Sketch3", 36) + record(ref, "Sketch2", 35))
    out += record(new_class("moRefPlane_c"), "Plane1", 40, flag=0xC0)
    out += record(ref, "Sketch4", 41, suppressed=1)
    out += record(new_class("moICE_c"), "Cut-Extrude1", 47, n=1,
                  children=struct.pack("<H", 0x0555))
    return out


KEYWORDS = """<?xml version="1.0" encoding="UTF-8"?>
<Keywords id="1" Name="Part1"><Configuration id="0" Name="Default" Type="ConfigurationManager"/>
<Feature id="8" Name="Comments" Type="Comments"/><Sketch id="5" Name="Origin" Type="Origin"/>
<Sketch id="26" Name="Sketch1" Dissectable="true"><Dimension Name="D1">80</Dimension></Sketch>
<Extrusion id="32" Name="Boss-Extrude1" Type="Boss-Extrude"><Dimension Name="D1">20</Dimension></Extrusion>
<Feature id="34" Name="Fillet1" Type="Fillet"><Dimension Name="D1">R6</Dimension></Feature>
<HoleWizard id="37" Name="CBORE for M6 Screw1" DissectableChildren="35"/>
<Sketch id="36" Name="Sketch3" Type="Sketch"/>
<Sketch id="35" Name="Sketch2"><Dimension Name="Thru Hole Dia.">&lt;MOD-DIAM&gt;6.6</Dimension></Sketch>
<Feature id="40" Name="Plane1" Type="Plane"><Dimension Name="D1">30</Dimension></Feature>
<Sketch id="41" Name="Sketch4"/>
<Extrusion id="47" Name="Cut-Extrude1" DissectableChildren="41"/></Keywords>"""


def synthetic_part() -> dict:
    hole = Pairs([("class", "moHoleWzd_c"), ("class", "moHoleWzd_c"),
                  ("name", "CBORE for M6 Screw1"), ("id", 37), ("suppressed", 0),
                  ("suppressed", True), ("hidden", 0), ("hidden", False)])
    toc = {"class": "object-map", "model": {
        "class": "moPart_c", "configid": 0, "configname": "Default",
        "object": [{"class": "moHoleWzd_c", "object": {"class": "object", "guid": "g-hole"}}]}}
    return {"Contents/Config-0-ResolvedFeatures": resolved_features(),
            "swXmlContents/KeyWords": b"\x86" + KEYWORDS.encode(),
            "Contents/3DExperienceExchange2": unqlite({"TOC": toc, "g-hole": hole},
                                                      overflow_key="g-hole")}


def test_msgpack_keeps_repeated_keys_and_decodes_every_type():
    blob = mp(Pairs([("a", 1), ("a", -5), ("f", 0.5), ("s", "x" * 40), ("n", None),
                     ("l", [True, False, {"k": 2}])]))
    v, end = sldprt.msgpack_decode(blob)
    assert end == len(blob)
    assert isinstance(v["a"], sldprt.Repeated) and v["a"] == [1, -5]
    assert (v["f"], v["s"], v["n"], v["l"]) == (0.5, "x" * 40, None, [True, False, {"k": 2}])
    assert sldprt.msgpack_decode(b"\xcd\x01\x00")[0] == 256           # uint16
    assert sldprt.msgpack_decode(b"\xc4\x02ab")[0] == b"ab"            # bin8
    with pytest.raises(sldprt.SldprtError):
        sldprt.msgpack_decode(b"\xc1")                                  # never used


def test_unqlite_cells_follow_chains_and_overflow_pages():
    db = unqlite({"TOC": {"x": 1}, "big": {"name": "y" * 50}, "z": [1, 2]}, overflow_key="big")
    assert sldprt.unqlite_cells(db) == [("TOC", {"x": 1}), ("big", {"name": "y" * 50}),
                                        ("z", [1, 2])]
    with pytest.raises(sldprt.SldprtError):
        sldprt.unqlite_cells(b"not a database")


def test_keywords_items_dimensions_and_children():
    kw = sldprt.keywords(synthetic_part())
    assert 0 not in kw                                   # <Configuration id="0"> is skipped
    assert (kw[32].tag, kw[32].type, kw[32].dims) == ("Extrusion", "Boss-Extrude", {"D1": "20"})
    assert kw[37].children == [35] and kw[35].dims == {"Thru Hole Dia.": "<MOD-DIAM>6.6"}
    assert [sldprt.dim_value(t) for t in ("R6", "<MOD-DIAM>6.6", "270°", "-1.5e2", "x")] == \
        [6.0, 6.6, 270.0, -150.0, None]


def test_feature_tree_classes_parents_and_suppression():
    tree = sldprt.feature_tree(synthetic_part())
    assert [f.name for f in tree] == ["Sketch1", "Boss-Extrude1", "Fillet1", "CBORE for M6 Screw1",
                                      "Sketch3", "Sketch2", "Plane1", "Sketch4", "Cut-Extrude1"]
    t = {f.name: f for f in tree}
    assert [t[n].type_name for n in ("Sketch1", "Boss-Extrude1", "Fillet1", "CBORE for M6 Screw1",
                                     "Plane1", "Sketch4", "Cut-Extrude1")] == \
        ["ProfileFeature", "Extrusion", "Fillet", "HoleWzd", "RefPlane", "ProfileFeature", "ICE"]
    assert t["Sketch4"].resolved_by == "keywords"          # class reference voted by KeyWords
    # absorbed sketches: back reference (exact class index), inline Hole Wizard, KeyWords
    assert (t["Sketch1"].parent, t["Sketch3"].parent, t["Sketch2"].parent, t["Sketch4"].parent) \
        == (32, 37, 37, 47)
    assert t["CBORE for M6 Screw1"].children == [36, 35] and t["Sketch3"].internal
    assert not t["Sketch1"].internal and not t["Sketch4"].internal
    # suppression: plain records, after a back reference, and (inline children) from 3DX
    assert {n: f.suppressed for n, f in t.items()} == {
        "Sketch1": False, "Boss-Extrude1": True, "Fillet1": True, "CBORE for M6 Screw1": True,
        "Sketch3": False, "Sketch2": False, "Plane1": False, "Sketch4": True,
        "Cut-Extrude1": False}
    assert t["CBORE for M6 Screw1"].hidden is False
    assert t["Plane1"].dims == {"D1": "30"}
    everything = sldprt.feature_tree(synthetic_part(), user_only=False)
    assert [f.name for f in everything if f.system] == ["Comments", "Origin"]
    with pytest.raises(sldprt.SldprtError, match="configuration 3"):
        sldprt.feature_tree(synthetic_part(), config=3)


def test_tree_mismatches_names_order_types_and_internal_sketches():
    part = synthetic_part()
    ok = [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion", True), "Fillet1",
          ("CBORE for M6 Screw1", "HoleWzd"), ("Cut-Extrude1", {"ICE", "Cut"})]
    assert sldprt.tree_mismatches(part, ok) == []
    # Sketch4 is absorbed by Cut-Extrude1, so only the plane is an extra top-level feature
    assert sldprt.tree_mismatches(part, ok, exact=True) == [
        "Plane1: unexpected feature in the saved tree"]
    assert sldprt.tree_mismatches(part, ok, exact=True, helpers=["Plane1"]) == []
    bad = sldprt.tree_mismatches(part, ["Fillet1", "Sketch1", ("Boss-Extrude1", "Boss", False),
                                        "Sketch2", "Hole2"])
    assert bad == ["Sketch1: out of order in the saved feature tree",
                   "Boss-Extrude1: out of order in the saved feature tree",
                   "Boss-Extrude1: saved as Extrusion, expected Boss",
                   "Boss-Extrude1: suppressed=True, expected False",
                   "Sketch2: that name belongs to a sketch inside CBORE for M6 Screw1, "
                   "not to the exported step",
                   "Hole2: not in the saved feature tree"]


def dm_mate(config, name, kind, cls, comps, fid) -> bytes:
    return (cstr(config) + struct.pack("<I", 0) + cstr(name) + struct.pack("<ii", 2, kind)
            + cstr(cls) + cstr(f"{name} ({comps})") + struct.pack("<II", 0, fid) + b"\x09\x80")


def synthetic_assembly() -> dict:
    dm = (new_class("moBCModeInfo_c") + b"\0" * 8 + new_class("moBCMate_c")
          + dm_mate("Default", "Coincident1", 0, "moMateCoincident", "base<1>,pin<1>", 28)
          + dm_mate("Default", "Distance1", 5, "moMateDistanceDim_c", "link<1>,part(2)<1>", 31))
    ref = {"class": "moPlaneRef_w", "dummy": {"class": "moCompFace_c", "target": "pin-1@hinge"}}
    group = Pairs([
        ("class", "moMateGroup_c"), ("class", "moNodeList_c"), ("name", "Mates"),
        ("NumberMates", 2),
        ("Mate1", {"class": "moMateDistanceDim_c", "description": "Distance1", "id": 31,
                   "suppressed": True, "hidden": False, "Aligment": 2, "Distance": 0.005,
                   "FlipDim": 0, "NumberEntities": 2, "Ref0": ref, "Ref1": ref}),
        ("Mate0", {"class": "moMateCoincident", "description": "Coincident1", "id": 28,
                   "suppressed": False, "Aligment": 1, "NumberEntities": 1, "Ref0": ref})])
    toc = {"model": {"configid": 0, "configname": "Default",
                     "object": [{"class": "moMateGroup_c", "object": {"guid": "g-mates"}}]}}
    return {"SwDocContentMgr/SwDocContentMgrInfo": dm,
            "Contents/3DExperienceExchange2": unqlite({"TOC": toc, "g-mates": group})}


def test_mates_merge_document_manager_list_and_3dexperience_details():
    ms = sldprt.mates(synthetic_assembly())
    assert [(m.name, m.type, m.type_name, m.id, m.config) for m in ms] == [
        ("Coincident1", 0, "MateCoincident", 28, "Default"),
        ("Distance1", 5, "MateDistanceDim", 31, "Default")]
    c, d = ms
    assert c.components == ["base<1>", "pin<1>"] and d.components == ["link<1>", "part(2)<1>"]
    assert (c.suppressed, c.align, c.alignment, c.distance) == (False, 1, 0, None)
    assert (d.suppressed, d.alignment, d.distance, d.flip) == (True, 1, 0.005, 0)
    assert d.entities == [("moPlaneRef_w", "pin-1@hinge")] * 2
    # without the 3DExperience store: names, types and components only
    dm_only = {"SwDocContentMgr/SwDocContentMgrInfo":
               synthetic_assembly()["SwDocContentMgr/SwDocContentMgrInfo"]}
    assert [(m.name, m.suppressed, m.align) for m in sldprt.mates(dm_only)] == [
        ("Coincident1", None, None), ("Distance1", None, None)]


def test_type_names_follow_the_solidworks_api():
    assert [sldprt.type_name(c) for c in (
        "moExtrusion_c", "Fillet_c", "mo3DProfileFeature_c", "moMateCoincident", "moSketchHole",
        "moOffsetRefSurface_c", None)] == ["Extrusion", "Fillet", "3DProfileFeature",
                                           "MateCoincident", "SketchHole", "OffsetRefSuface", None]


# ========================================================================================
# Real files saved by SolidWorks 2024 (BauyrCAD exports; the expected values were read back
# through the SolidWorks API when the files were made)
# ========================================================================================

needs_data = pytest.mark.skipif(not DATA.is_dir(), reason="tests/data/sw fixtures missing")


@needs_data
def test_real_applied_features_tree():
    tree = sldprt.feature_tree(DATA / "features.SLDPRT")
    rows = [(f.name, f.id, f.type_name, f.parent) for f in tree if not f.internal]
    assert rows == [("Sketch1", 26, "ProfileFeature", 32), ("Boss-Extrude1", 32, "Extrusion", None),
                    ("Fillet1", 34, "Fillet", None), ("CBORE for M6 Screw1", 37, "HoleWzd", None),
                    ("X Axis", 39, "RefAxis", None), ("LPattern1", 46, "LPattern", None),
                    ("CSK for M5 Screw1", 49, "HoleWzd", None), ("Hole1", 52, "HoleWzd", None)]
    t = {f.name: f for f in tree}
    assert [f.name for f in tree if f.internal] == ["Sketch3", "Sketch2", "Sketch5", "Sketch4",
                                                    "Sketch7", "Sketch6"]
    assert {k: sldprt.dim_value(v) for k, v in t["LPattern1"].dims.items()} == {"D1": 3, "D3": 30}
    assert sldprt.dim_value(t["Fillet1"].dims["D1"]) == 6
    assert sldprt.dim_value(t["Sketch2"].dims["Thru Hole Dia."]) == 6.6
    assert not any(f.suppressed for f in tree) and all(f.resolved_by != "unresolved" for f in tree)
    assert sldprt.tree_mismatches(DATA / "features.SLDPRT", [
        ("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion"), ("Fillet1", "Fillet"),
        ("CBORE for M6 Screw1", "HoleWzd"), ("LPattern1", "LPattern"),
        ("CSK for M5 Screw1", "HoleWzd"), ("Hole1", "HoleWzd")], exact=True, helpers=["X Axis"]) == []


@needs_data
def test_real_2d_sketch_after_3d_sketches():
    """Two 3D sketches (helper points and a helper line) between two 2D sketches: both
    sketch classes are referenced by index with the same KeyWords tag 'Sketch', and the
    indices follow the order of the classes' headers (an explicit-plane sketch, WP03)."""
    tree = sldprt.feature_tree(DATA / "sketch_helpers.SLDPRT")
    rows = [(f.name, f.type_name, f.parent) for f in tree if not f.internal]
    assert rows == [("Sketch1", "ProfileFeature", 32), ("Boss-Extrude1", "Extrusion", None),
                    ("Sketch2 Points", "3DProfileFeature", None),
                    ("Sketch2 Plane Sketch", "3DProfileFeature", None),
                    ("Sketch2 Plane", "RefPlane", None), ("Sketch2", "ProfileFeature", 47),
                    ("Cut-Extrude1", "ICE", None)]
    t = {f.name: f for f in tree}
    assert sorted(sldprt.dim_value(v) for v in t["Sketch2"].dims.values()) == [3, 4, 6, 8]


@needs_data
def test_real_suppression_per_configuration():
    path = DATA / "suppressed.SLDPRT"
    assert sldprt.feature_configurations(path) == [0, 1]
    states = {cfg: {f.name: f.suppressed for f in sldprt.feature_tree(path, cfg) if not f.internal}
              for cfg in (0, 1)}
    # SolidWorks' own IsSuppressed2 per configuration (Default, Alt) when the file was saved;
    # Boss-Extrude2 and Cut-Extrude1 own a back-referenced sketch (flag after the reference)
    assert states[0] == {"Sketch1": False, "Boss-Extrude1": False, "Fillet1": True,
                         "CBORE for M6 Screw1": True, "Plane1": True, "Sketch4": True,
                         "Boss-Extrude2": True, "Sketch5": False, "Cut-Extrude1": False,
                         "Plane2": False, "Sketch6": False}
    assert states[1] == {**{n: False for n in states[0]}, "Cut-Extrude1": True}


@needs_data
def test_real_tree_mismatches_catch_renamed_steps_and_dumb_solids():
    # BauyrCAD's steps of the suppression fixture. SolidWorks gave the Hole Wizard's sketches
    # the names Sketch2/Sketch3 and an unnamed helper plane the name Plane1, so the export
    # kept SolidWorks' own names for the later sketches and the plane.
    steps = ["Sketch1", "Boss-Extrude1", "Fillet1", "CBORE for M6 Screw1", "Sketch2",
             "Boss-Extrude2", "Sketch3", "Cut-Extrude1", "Plane1", "Sketch4"]
    bad = sldprt.tree_mismatches(DATA / "suppressed.SLDPRT", steps)
    assert bad[:2] == ["Sketch2: that name belongs to a sketch inside CBORE for M6 Screw1, "
                       "not to the exported step",
                       "Sketch3: that name belongs to a sketch inside CBORE for M6 Screw1, "
                       "not to the exported step"]
    assert "Plane1: out of order in the saved feature tree" in bad
    imported = DATA / "imported.SLDPRT"
    later = [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "ICE"),
             ("Sketch2", "ProfileFeature"), ("Cut-Extrude1", "ICE")]
    assert sldprt.tree_mismatches(imported, later) == [
        "Imported1: BaseBody body without feature history"]
    assert sldprt.tree_mismatches(imported, [("Imported1", "BaseBody")] + later, exact=True) == []
    t = {f.name: f for f in sldprt.feature_tree(imported)}
    assert (t["Sketch1"].parent, t["Sketch2"].parent, t["Imported1"].dumb_solid) == (33, 47, True)


def _keywords_with(monkeypatch, change):
    """Make feature_tree see KeyWords items changed by ``change(item) -> item``."""
    import dataclasses
    real = sldprt.keywords
    monkeypatch.setattr(sldprt, "keywords", lambda src: {
        i: change(dataclasses.replace(v, dims=dict(v.dims), children=list(v.children)))
        for i, v in real(src).items()})


@needs_data
def test_real_class_reference_follows_header_order_not_corpus_frequency(monkeypatch):
    """A class index names ONE class, numbered in the order of the class headers. Suppressed
    Instant3D features carry a KeyWords Type ('Boss-Extrude'), which in other files goes with
    moExtrusion_c far more often than with moICE_c; the reference still names the ICE header
    that comes next in the stream (review of WP01, 2026-10-18)."""
    path = DATA / "suppressed.SLDPRT"

    def boss(item):
        if item.id == 60:                               # Cut-Extrude1, a class reference
            item.type = "Boss-Extrude"
        return item
    _keywords_with(monkeypatch, boss)
    for cfg in (0, 1):
        t = {f.name: f for f in sldprt.feature_tree(path, cfg)}
        assert (t["Cut-Extrude1"].mfc_class, t["Cut-Extrude1"].resolved_by) == ("moICE_c", "keywords")
    assert sldprt.tree_mismatches(path, [("Boss-Extrude2", "ICE"), ("Cut-Extrude1", "ICE")]) == []


@needs_data
def test_real_absorbed_sketches_of_lofts_sweeps_and_suppressed_owners():
    """Absorbed sketches stored before their owner are back references to MFC object
    indices: every profile of a loft, a sweep's path, and the sketches of features suppressed
    in the active configuration (KeyWords lists no children for those) are linked, and
    nothing is left unidentified."""
    def tree(name, cfg=0):
        return {f.name: f for f in sldprt.feature_tree(DATA / name, cfg)}
    loft = tree("loft.SLDPRT")              # Sketch1, Plane1, Sketch2, Loft1, Sketch3, Sweep1
    assert loft["Loft1"].children == [loft["Sketch1"].id, loft["Sketch2"].id]
    assert loft["Sketch1"].parent == loft["Sketch2"].parent == loft["Loft1"].id
    assert loft["Sweep1"].children == [loft["Sketch3"].id]
    for cfg in (0, 1):                      # Boss-Extrude2 suppressed in Default, Cut-Extrude1 in Alt
        t = tree("suppressed.SLDPRT", cfg)
        assert t["Sketch4"].parent == t["Boss-Extrude2"].id, cfg
        assert t["Sketch5"].parent == t["Cut-Extrude1"].id, cfg
    t = tree("imported_suppressed.SLDPRT")  # Cut-Extrude1 suppressed
    assert t["Cut-Extrude1"].suppressed and t["Cut-Extrude1"].children == [t["Sketch2"].id]
    for name in ("loft.SLDPRT", "suppressed.SLDPRT", "imported_suppressed.SLDPRT",
                 "features.SLDPRT", "imported.SLDPRT", "shaft.SLDPRT", "sketch_helpers.SLDPRT"):
        assert all(f.unlinked == 0 for f in tree(name).values()), name


@needs_data
def test_real_back_references_alone_find_the_keywords_children(monkeypatch):
    """With KeyWords' DissectableChildren hidden, the back-reference links alone give the
    same absorbed sketches."""
    names = ("features.SLDPRT", "imported.SLDPRT", "shaft.SLDPRT", "sketch_helpers.SLDPRT",
             "suppressed.SLDPRT", "loft.SLDPRT", "imported_suppressed.SLDPRT")
    want = {n: [(f.name, f.parent, f.unlinked) for f in sldprt.feature_tree(DATA / n)] for n in names}

    def hide(item):
        item.children = []
        return item
    _keywords_with(monkeypatch, hide)
    for n in names:
        assert [(f.name, f.parent, f.unlinked) for f in sldprt.feature_tree(DATA / n)] == want[n], n


SAMPLES = Path(r"C:\Users\Public\Documents\SOLIDWORKS\SOLIDWORKS 2024\samples")


@pytest.mark.skipif(not SAMPLES.is_dir(), reason="SolidWorks 2024 sample files not installed")
def test_sample_class_references_match_the_3dexperience_store():
    """Boss extrudes after the base feature are moBoss_c, as the 3DExperience store of the
    same file says, not the far more frequent moExtrusion_c; and no two class indices of a
    stream resolve to the same class."""
    files = {SAMPLES / "tutorial" / "advdrawings" / "switch top plate.sldprt":
             {"tall screw bosses": "Boss", "lone boss": "Boss"},
             SAMPLES / "tutorial" / "advdrawings" / "gear- caddy.sldprt": {"short screw boss": "Boss"},
             SAMPLES / "learn" / "Mouse" / "Bottom Housing.SLDPRT": {}}
    for path, want in files.items():
        if not path.is_file():
            continue
        sec = sldprt.read_sections(path)
        tree = sldprt.feature_tree(sec)
        t = {f.name: f for f in tree}
        assert {n: t[n].type_name for n in want} == want, path.name
        docs = {sldprt._last(d.get("id")): sldprt._last(d.get("class"))
                for d in sldprt._config_docs(sldprt.exchange_docs(sec), 0) if isinstance(d, dict)}
        for f in tree:
            if docs.get(f.id) and f.resolved_by != "mfc-new":
                assert f.mfc_class == docs[f.id], (path.name, f.name)
        b = sec["Contents/Config-0-ResolvedFeatures"]
        classes = sldprt._new_classes(b)
        recs = [r for r in sldprt._records(b, classes) if r.id != sldprt._NO_ID]
        kmap = sldprt._resolve_classes(recs, sldprt.keywords(sec), b, classes)
        named = [c for c, _ in kmap.values() if c]
        assert len(named) == len(set(named)), path.name


@needs_data
def test_real_revolve_dimensions():
    t = {f.name: f for f in sldprt.feature_tree(DATA / "shaft.SLDPRT")}
    assert [(n, f.type_name) for n, f in t.items()] == [
        ("Sketch1", "ProfileFeature"), ("Revolve1", "Revolution"),
        ("Sketch2", "ProfileFeature"), ("Cut-Revolve1", "RevCut")]
    assert (sldprt.dim_value(t["Revolve1"].dims["D1"]),
            sldprt.dim_value(t["Cut-Revolve1"].dims["D1"])) == (360, 270)
    assert (t["Sketch1"].parent, t["Sketch2"].parent) == (31, 37)


@needs_data
def test_real_assembly_mates():
    ms = sldprt.mates(DATA / "hinge.SLDASM")
    # IMate2.Type / Alignment / IsSuppressed2, read in SolidWorks when the file was saved
    assert [(m.name, m.type, m.alignment, m.suppressed) for m in ms] == [
        ("Concentric1", 1, 0, False), ("Coincident1", 0, 1, False), ("Concentric2", 1, 0, False),
        ("Distance1", 5, 1, False), ("Angle1", 6, 0, True)]
    m = {x.name: x for x in ms}
    assert m["Distance1"].distance == pytest.approx(0.005)
    assert m["Distance1"].components == ["link<1>", "base<1>"]
    assert m["Angle1"].angle == pytest.approx(math.radians(30))
    assert m["Concentric1"].entities == [("moCylinderRef_w", "base-1@hinge"),
                                         ("moCylinderRef_w", "pin-1@hinge")]
    assert [f.type_name for f in sldprt.feature_tree(DATA / "hinge.SLDASM")] == ["MateGroup"]


@needs_data
def test_command_line_tree_and_mates(capsys):
    assert sldprt.main([str(DATA / "imported.SLDPRT"), "--tree", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["name"], r["type_name"]) for r in rows] == [
        ("Imported1", "BaseBody"), ("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "ICE"),
        ("Sketch2", "ProfileFeature"), ("Cut-Extrude1", "ICE")]
    sldprt.main([str(DATA / "hinge.SLDASM"), "--mates"])
    out = capsys.readouterr().out
    assert "Distance1" in out and "5 mm" in out and "30 deg" in out
    sldprt.main([str(DATA / "suppressed.SLDPRT"), "--tree", "--config", "1"])
    line = next(x for x in capsys.readouterr().out.splitlines() if "Cut-Extrude1" in x)
    assert line.split()[3] == "yes"
