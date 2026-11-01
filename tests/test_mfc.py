"""The exact MFC archive model of SolidWorks 2024 parts (bauyrcad/core/mfc.py).

tests/data/sw/mfc holds donors SolidWorks 2024 saved from BauyrCAD's native export
(projects/sw_features/make_family.py): P1 = rectangle 40 x 20 on Front + Boss-Extrude1 10,
P1s = P1 + Sketch2 (a circle, not extruded), T3 / Q4 = triangle / square of lines + extrude,
P2 = P1 + Sketch2 + Boss-Extrude2 (an Instant3D boss), P2t = the same on the Top plane,
P3 = P2 + a cut, T3B = T3 + Sketch2 + Boss-Extrude2, H1 = a rectangle with a hole + extrude
(a sketch of two loops), S0 = a sketch only (no solid), I1 = H1 as STEP opened in SolidWorks
and saved (an Imported body; projects/sw_features/make_m1b.py)."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from bauyrcad.core import mfc, sldprt

DATA = Path(__file__).parent / "data" / "sw" / "mfc"
DONORS = ("P1", "P1s", "T3", "Q4", "P2", "P2t", "P3", "T3B", "H1", "S0", "I1",
          "R1", "R2", "C0", "C1")
SEED = Path(__file__).parents[1] / "bauyrcad" / "data" / "sw_seeds" / "part.SLDPRT"
CFG, RF = "Contents/Config-0", "Contents/Config-0-ResolvedFeatures"


def _sections(name):
    path = DATA / f"{name}.SLDPRT"
    if not path.is_file():
        pytest.skip(f"requires private SolidWorks comparison fixture: {path.name}")
    return sldprt.read_sections(path)


@pytest.mark.parametrize("name", DONORS)
def test_donor_archive_round_trips_byte_for_byte(name):
    secs = _sections(name)
    arc = mfc.load_archive(secs)
    out = arc.dumps()
    assert out[CFG] == secs[CFG]
    assert out[RF] == secs[RF]


@pytest.mark.parametrize("name", DONORS)
def test_config_map_ends_where_the_feature_stream_starts(name):
    secs = _sections(name)
    arc = mfc.load_archive(secs)
    assert arc.config.start == mfc.CONFIG_START
    assert arc.config.end == struct.unpack_from("<I", secs[RF], 0)[0] == arc.resolved.start
    assert [c.name for c in arc.table[2:7:2]] == ["moPart_c", "moNodeName_c",
                                                  "moVisualProperties_c"]


def test_feature_tree_objects_and_names():
    arc = mfc.load_archive(_sections("P1s"))
    names = [f.values["name"].values["name"] for f in arc.features]
    assert names[-4:] == ["Origin", "Sketch1", "Boss-Extrude1", "Sketch2"]
    assert [f.name for f in arc.features[-4:]] == ["moOriginProfileFeature_c",
                                                   "moProfileFeature_c", "moExtrusion_c",
                                                   "moProfileFeature_c"]
    # the extrude absorbs Sketch1: a back reference to that very object
    ext = arc.features[-2]
    assert ext.values["children"][0]["_0"] is arc.features[-3]
    # D1 of the extrude: the depth double
    d1 = ext.values["end_spec"].values["dim"].values["handle"].values["dim"].values["param"]
    assert d1.values["name"].values["name"] == "D1"
    assert d1.values["value"] == pytest.approx(0.010)


def test_sketch_geometry_and_relations():
    arc = mfc.load_archive(_sections("P1"))
    sk = arc.features[-2].values["sketch"]
    pts = [(p["x"], p["y"]) for p in sk.values["points"]]
    assert pts == pytest.approx([(0, 0), (0.04, 0), (0.04, 0.02), (0, 0.02)])
    lines = sk.values["seglists"][0]["segs"]
    assert [(s["p0"], s["p1"]) for s in lines] == [(0, 1), (1, 2), (2, 3), (3, 0)]
    # two horizontal (4) and two vertical (5) relations, one line each
    rels = sk.values["rel1"]
    assert [r["type"] for r in rels] == [4, 4, 5, 5]
    assert [r["entities"][0]["_0"].values["index"] for r in rels] == [0, 2, 1, 3]
    # the circle of P1s: one arc, start = end point, centre a separate point
    circ = mfc.load_archive(_sections("P1s")).features[-1].values["sketch"]
    assert [len(s["segs"]) for s in circ.values["seglists"]] == [0, 0, 1]   # lines, ?, arcs
    (arc_seg,) = circ.values["seglists"][2]["segs"]
    assert arc_seg["arc"] == 1 and arc_seg["p0"] == arc_seg["p1"]
    c = circ.values["points"][arc_seg["arc_data"]["centre"]]
    assert (c["x"], c["y"]) == pytest.approx((0.04, 0.01))


def test_face_ids_follow_the_sketch_segments():
    """The extrude's face-id records name the sketch segments (4 lines, 3 for the triangle)
    and point at the part's external object through plain back references."""
    for name, n in (("P1", 4), ("T3", 3)):
        ext = mfc.load_archive(_sections(name)).features[-1]
        face = ext.values["bodies"].values["faces"][0]["_0"]
        ids = [d["_0"] for d in face.values["ids"] if d["_0"] is not None]
        assert ids[0].name == "moEndFaceSurfIdRep_c"
        assert sorted(o.values["segment"] for o in ids[1:]) == list(range(1, n + 1))
        owner = ids[0].values["face"].values["owner"]
        assert all(o.values["face"].values["owner"] is owner for o in ids[1:])


def test_renumbering_follows_object_identity():
    """One more object in Config-0 (as a new feature's atom adds) moves every later tag and
    plain reference of the feature stream by one; parsing the result gives the same tree."""
    secs = _sections("P1")
    arc = mfc.load_archive(secs)
    c, idx, count = mfc.dumps(arc.config)
    shifted, _, _ = mfc.dumps(arc.resolved, index=idx, count=count + 1)
    assert len(shifted) == len(secs[RF]) and shifted != secs[RF]
    assert struct.unpack_from("<I", shifted, 0)[0] == count + 1
    table = list(arc.table[:count]) + [mfc.Obj(mfc.MfcClass("placeholder", 0))]
    res, _ = mfc.loads(shifted, "resolved", table=table)
    assert [f.name for f in res.root["features"] and [d["_0"] for d in res.root["features"]]]         == [f.name for f in arc.features]
    again, _, _ = mfc.dumps(res, index=idx, count=count + 1)
    assert again == shifted


def test_a_wrong_layout_desynchronises_loudly():
    secs = _sections("P1")
    bad = dict(mfc.LAYOUTS)
    bad["moSketchChain_c"] = bad["moSketchChain_c"][:-2]       # truncate beyond the optional cache prefix
    with pytest.raises(mfc.MfcError):
        cfg, table = mfc.loads(secs[CFG], "config", bad)
        mfc.loads(secs[RF], "resolved", bad, table=table)
    with pytest.raises(mfc.MfcError, match="no layout"):
        lay = dict(mfc.LAYOUTS)
        del lay["moExtrusion_c"]
        cfg, table = mfc.loads(secs[CFG], "config", lay)
        mfc.loads(secs[RF], "resolved", lay, table=table)


def test_cstring_forms():
    for s in ("", "D1", "x" * 300, "Ä€"):
        b = mfc._cstring(s)
        r = mfc.Reader(b, {})
        assert r.cstring() == s and r.i == len(b)


def _per_feature(arc, count):
    """The feature stream of `arc` numbered as if it continued a map of `count` entries:
    (bytes, offsets of references, [(start, end) of each top-level feature])."""
    _, idx, _ = mfc.dumps(arc.config)
    root = dict(arc.resolved.root, start=count)
    dry = mfc.Writer(mfc.LAYOUTS, dict(idx), count)
    dry.fields(mfc.LAYOUTS["<resolved>"], root)
    w = mfc.Writer(mfc.LAYOUTS, dict(idx), count, plan=dry.index)
    w.out += struct.pack("<IH", count, len(arc.features))
    spans = []
    for f in arc.features:
        a = len(w.out)
        w.obj(f)
        spans.append((a, len(w.out)))
    return bytes(w.out), set(w.refs), spans


def test_differential_donors_move_only_reference_fields():
    """Reference fields are exactly what the object numbering moves: P1 vs Q4 / P1w-like
    donors (same objects, other values) differ in no reference; P1 vs P2 and T3 vs T3B (two
    more features: the history list gains 6 objects, Config-0 one atom) differ, feature by
    feature, only by uniform object-count shifts in references."""
    a, q = (mfc.load_archive(_sections(n)) for n in ("P1", "Q4"))
    xa, refs, sa = _per_feature(a, q.resolved.start)
    xq, _, sq = _per_feature(q, q.resolved.start)
    assert len(xa) == len(xq)
    assert not [i for i in range(len(xa)) if xa[i] != xq[i] and {i, i - 1} & refs]
    for base, more in (("P1", "P2"), ("T3", "T3B")):
        a, b = (mfc.load_archive(_sections(n)) for n in (base, more))
        xa, refs, sa = _per_feature(a, b.resolved.start)
        xb, _, sb = _per_feature(b, b.resolved.start)
        shifts = set()
        for (p0, p1), (q0, q1) in zip(sa, sb):
            if p1 - p0 != q1 - q0:          # the history folder: two more entries
                continue
            for i in range(p0, p1):
                start = i if i in refs else i - 1 if i - 1 in refs else None
                if xa[i] != xb[i - p0 + q0] and start is not None:
                    shifts.add(struct.unpack_from("<H", xb, start - p0 + q0)[0]
                               - struct.unpack_from("<H", xa, start)[0])
        assert shifts and shifts <= {1, 6}


def test_part_variants():
    """Config-0 of a part without a solid (no curvature object, no model box), of an
    Imported body (the translator's name in the model data's atom) and of the seed BauyrCAD
    writes .SLDPRT files from (no PMark record: the part writes its data itself)."""
    part = mfc.load_archive(_sections("S0")).config.root["part"].values
    assert part["curvature"] is None
    assert part["trans_ref_plane"].values["has_box"] == 0
    assert part["trans_ref_plane"].values["box"] is None
    arc = mfc.load_archive(_sections("I1"))
    (atom,) = arc.config.root["part"].values["model_data"].values["atoms"]
    assert atom["_0"].values["name"].values["name"] == "Part1 1[1]"
    assert atom["_0"].values["inline"] == {}
    body = arc.features[-1]
    assert body.name == "moBaseBody_c" and body.values["body_name"] == "Imported1"
    secs = sldprt.read_sections(SEED)
    arc = mfc.load_archive(secs)
    part = arc.config.root["part"].values
    assert part["pmarks"] is None and part["pmark_data"]["list"].name == "suObList"
    assert arc.features[-1].values["body_name"] == "Imported1"
    out = arc.dumps()
    assert out[CFG] == secs[CFG] and out[RF] == secs[RF]


def test_branch_on_a_field_of_a_nested_object():
    """("ifobj", ...) reads the fields that a nested object announces: an extrude's second
    face chooser follows only when its first box says so (else 12 bytes; parts saved by
    other tools)."""
    lay = {"<config>": [("o", "obj"), ("x", ("ifobj", "o", "more", (1,), [("a", "u32")],
                                               [("b", "u16"), ("c", "u16")]))],
           "K": [("more", "u16")]}
    head = struct.pack("<HHH", 0xFFFF, 1, 1) + b"K"
    for more, tail in ((1, struct.pack("<I", 7)), (0, struct.pack("<HH", 8, 9))):
        data = head + struct.pack("<H", more) + tail
        st, _ = mfc.loads(data, "config", lay)
        assert list(st.root["x"]) == (["a"] if more else ["b", "c"])
        assert mfc.dumps(st, lay)[0] == data


def test_sketch_display_reference_is_u16_followed_by_dimension_index():
    arc = mfc.load_archive(_sections("R2"))
    sketch = arc.features[-2].values["sketch"]
    handles = [d["_0"].values["handle"].values for d in sketch.values["list1"].values["items"]]
    assert [h["index"] for h in handles] == [0, 1]
    assert all(h["sketch"].target is sketch for h in handles)
    assert [d["_0"].values["param"].values["value"]
            for d in sketch.values["linear_dims"]] == [0.04, 0.02]
    # Inserting another object before the sketch must move both references, without
    # changing the second dimension's index (mistaking these for ref32 misses that case).
    arc.config.root["part"].values["list"].values["items"].append({
        "_0": mfc.Obj(next(o.cls for o in arc.table if isinstance(o, mfc.Obj)
                          and o.name == "suObList"), {"items": []})})
    again = mfc.load_archive(arc.dumps())
    sketch = again.features[-2].values["sketch"]
    handles = [d["_0"].values["handle"].values for d in sketch.values["list1"].values["items"]]
    assert [h["index"] for h in handles] == [0, 1]
    assert all(h["sketch"].target is sketch for h in handles)
