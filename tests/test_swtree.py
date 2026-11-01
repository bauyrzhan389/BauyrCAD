"""Adding features to a SolidWorks 2024 part's tree on the exact archive model
(bauyrcad/core/swtree.py): Sketch2 + Boss-Extrude2 of the donor P2 (made by SolidWorks)
transplanted onto P1 must give SolidWorks' own P2, and onto the triangle T3 SolidWorks' own
T3B, in everything but stamps, persistent ids, plane display sizes and the face-naming
records SolidWorks recomputes. SolidWorks opening the result: tests/test_sw_swtree.py."""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from bauyrcad.core import mfc, sldprt, swtree

DATA = Path(__file__).parent / "data" / "sw" / "mfc"
CFG, RF = "Contents/Config-0", "Contents/Config-0-ResolvedFeatures"
NEW = ["Sketch2", "Boss-Extrude2"]


def _arc(name):
    return mfc.load_archive(sldprt.read_sections(DATA / f"{name}.SLDPRT"))


def _counters(arc):
    """Every number SolidWorks keeps in step with the tree."""
    part = arc.config.root["part"].values
    md = part["model_data"].values
    u32 = lambda b, at: struct.unpack_from("<I", b, at)[0]  # noqa: E731
    return {
        "seq": [f["seq"] for f in swtree._features_with_seq(arc)],
        "ids": [swtree.name(f) + f":{swtree._feature_id(f)}" for f in arc.features],
        "atoms": [(d["_0"].values["feature_id"], d["_0"].values["_3"], d["_0"].values["head"][8])
                  for d in md["atoms"]],
        "model": (u32(md["head"], 34), u32(md["head"], 44)),
        "env": u32(part["environment"].values["data"], 12),
        "blocks": u32(part["blocks"].values["tail"], 84),
        "transref": u32(part["trans_ref_plane"].values["tail"], 140),
        "bodies": [(f.values["counters"][16:20], f.values["counters"][28:36],
                    f.values["pmark"].values["seq"]) for f in arc.features
                   if "counters" in f.values],
        "history": [d["_1"].values["id"] for d in
                    next(f for f in arc.features if f.name == "moHistoryFolder_c")
                    .values["items"]],
    }


@pytest.mark.parametrize("base, want", [("P1", "P2"), ("T3", "T3B")])
def test_transplant_gives_what_solidworks_writes(base, want):
    arc = _arc(base)
    new = swtree.transplant(arc, _arc("P2"), NEW)
    assert [swtree.name(f) for f in new] == NEW
    ref = _arc(want)
    assert _counters(arc) == _counters(ref)
    out, theirs = arc.dumps(), sldprt.read_sections(DATA / f"{want}.SLDPRT")
    assert len(out[CFG]) == len(theirs[CFG]) and len(out[RF]) == len(theirs[RF])
    # the written archive reads back with the same tree
    again = mfc.load_archive({CFG: out[CFG], RF: out[RF]})
    assert [swtree.name(f) for f in again.features][-4:] == ["Sketch1", "Boss-Extrude1"] + NEW
    # Boss-Extrude2 absorbs the transplanted Sketch2 and merges into the base's Boss-Extrude1
    ext = again.features[-1]
    assert ext.values["children"][0]["_0"] is again.features[-2]
    assert ext.values["merge"].values["id"] == swtree._feature_id(again.features[-3])


def test_transplant_refuses_what_it_cannot_place():
    with pytest.raises(swtree.TreeError):
        swtree.transplant(_arc("P2"), _arc("P2"), NEW)          # the base already has them
    with pytest.raises(swtree.TreeError):
        swtree.transplant(_arc("P1"), _arc("P2"), ["Boss-Extrude2"])   # not the donor's tail


def test_save_drops_the_regenerated_sections(tmp_path):
    container, arc = swtree.load(DATA / "T3.SLDPRT")
    swtree.transplant(arc, _arc("P2"), NEW)
    path = tmp_path / "g2.SLDPRT"
    swtree.save(container, arc, path)
    secs = sldprt.read_sections(path)
    assert not set(swtree.REGENERATED) & set(secs)
    assert "_MO_VERSION_17000/Biography" in secs and "Contents/CMgr" in secs
    assert [f.name for f in sldprt.feature_tree(path)][-2:] == NEW


def _on_circle(k, n=6, r=15):
    return r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)


HEXAGON = [("line", _on_circle(k), _on_circle(k + 1)) for k in range(6)]
RECT = [("line", (0, 0), (40, 0)), ("line", (40, 0), (40, 20)), ("line", (40, 20), (0, 20)),
        ("line", (0, 20), (0, 0))]


def _sketch1(arc):
    return next(f for f in arc.features if swtree.name(f) == "Sketch1")


def test_a_profile_written_from_geometry_is_what_solidworks_writes():
    """P1's rectangle written again from its four lines gives SolidWorks' own feature archive
    but for the order of the profile chain (SolidWorks finds its own)."""
    container, arc = swtree.load(DATA / "P1.SLDPRT")
    swtree.set_profile(arc, _sketch1(arc), [RECT])
    ours = arc.dumps()[RF]
    theirs = sldprt.read_sections(DATA / "P1.SLDPRT")[RF]
    assert len(ours) == len(theirs)
    assert sum(a != b for a, b in zip(ours, theirs)) <= 8


@pytest.mark.parametrize("donor, loops", [
    ("N6", [HEXAGON]),
    ("DS", [[("line", (0, -10), (0, 10)), ("arc", (0, 0), (0, 10), (0, -10))]]),
])
def test_profiles_match_solidworks_sketches(donor, loops):
    """A hexagon and a D shape (line + arc) written into P1 hold the entities, ids, counters
    and chains of SolidWorks' own sketches of the same shapes."""
    _, arc = swtree.load(DATA / "P1.SLDPRT")
    swtree.set_profile(arc, _sketch1(arc), loops)
    again = mfc.load_archive(dict(zip((CFG, RF), (arc.dumps()[CFG], arc.dumps()[RF]))))
    ours, theirs = (_sketch1(a).values["sketch"].values for a in (again, _arc(donor)))
    pick = lambda sk: ([(p["id"], p["kind"], p["a"], p["b"], p["c"], round(p["x"], 9),  # noqa: E731
                         round(p["y"], 9)) for p in sorted(sk["points"], key=lambda p: (p["x"], p["y"]))],
                       [[(s["id"], s["arc"], s["base"][17]) for s in sl["segs"]]
                        for sl in sk["seglists"]],
                       sorted(r["type"] for r in sk["rel1"]), sk["_29"][:4],
                       [d["_0"] for d in sk["table"]][1:4])
    a, b = pick(ours), pick(theirs)
    assert [x[1:] for x in a[0]] == [x[1:] for x in b[0]]      # points (ids may differ)
    assert a[1:] == b[1:]
    assert sorted(len(c["_0"].values["segments"]) for c in _sketch1(again).values["chains"]) == \
        sorted(len(c["_0"].values["segments"]) for c in _sketch1(_arc(donor)).values["chains"])


def test_profile_loops_must_close():
    _, arc = swtree.load(DATA / "P1.SLDPRT")
    with pytest.raises(swtree.TreeError, match="does not end"):
        swtree.set_profile(arc, _sketch1(arc), [[("line", (0, 0), (40, 0)),
                                                 ("line", (40, 0), (40, 20))]])


@pytest.mark.parametrize("donor,loops,dims", [
    ("R1", [RECT], [("length", 0)]),
    ("R2", [RECT], [("length", 0), ("length", 1)]),
    ("C1", [[("circle", (0, 0), 6)]], [("diameter", 0)]),
])
def test_written_dimensions_match_solidworks(donor, loops, dims):
    arc = _arc("P1")
    swtree.set_profile(arc, _sketch1(arc), loops, dims=dims)
    again = mfc.load_archive(arc.dumps())
    def pick(a):
        obj = _sketch1(a).values["sketch"]
        sk = obj.values
        rows = []
        for group in ("linear_dims", "radial_dims"):
            for item in sk[group]:
                d = item["_0"].values
                p = d["param"].values
                rows.append((p["name"].values["name"], p["value"], d["type"],
                             tuple(d[k].values["index"] for k in ("p0", "p1", "arc") if k in d)))
        handles = [d["_0"].values["handle"].values for d in sk["list1"].values["items"]]
        assert all(h["sketch"].target is obj for h in handles)
        return (rows, [(h["index"], h["value"]) for h in handles],
                [p["base"][17] for p in sk["points"]],
                [[s["base"][17] for s in sl["segs"]] for sl in sk["seglists"]],
                sk["_29"], sk["table"])
    assert pick(again) == pick(_arc(donor))


def test_dimension_ids_and_values_follow_new_geometry_and_multiple_sketches():
    arc = _arc("P2")
    first = _sketch1(arc)
    second = next(f for f in arc.features if swtree.name(f) == "Sketch2")
    swtree.set_profile(arc, first, [RECT], dims=[("length", 0), ("length", 1)])
    swtree.set_profile(arc, second, [[("circle", (30, 15), 9)]], dims=[("diameter", 0)])
    again = mfc.load_archive(arc.dumps())
    params = [o.values for o in again.table if isinstance(o, mfc.Obj) and o.name == "moLengthParameter_c"]
    assert len({p["id"] for p in params}) == len(params)
    assert sorted(p["value"] for p in params) == pytest.approx([.01, .018, .02, .02, .04])


def test_replacing_a_dimensioned_profile_removes_stale_dimensions():
    arc = _arc("R2")
    swtree.set_profile(arc, _sketch1(arc), [HEXAGON])
    sk = _sketch1(mfc.load_archive(arc.dumps())).values["sketch"].values
    assert not sk["linear_dims"] and not sk["radial_dims"] and not sk["list1"].values["items"]
    assert all(p["base"][17] == 0 for p in sk["points"])


@pytest.mark.parametrize("kind,index", [("angle", 0), ("length", -1), ("length", 4),
                                       ("length", True), ("diameter", 0)])
def test_bad_dimensions_do_not_change_the_archive(kind, index):
    arc = _arc("P1")
    before = arc.dumps()
    with pytest.raises(swtree.TreeError):
        swtree.add_dimension(arc, _sketch1(arc), kind, index)
    assert arc.dumps() == before


def test_duplicate_dimension_is_refused():
    arc = _arc("R1")
    with pytest.raises(swtree.TreeError, match="already"):
        swtree.add_dimension(arc, _sketch1(arc), "length", 0)

@pytest.mark.parametrize('offset,scale', [((0,0),1), ((125,-80),3), ((-30,40),.1)])
def test_dimension_layout_outside_profile_and_roundtrips(offset, scale):
    arc = _arc('P1'); f = _sketch1(arc)
    def xy(x,y): return (offset[0]+scale*x,offset[1]+scale*y)
    corners=[xy(0,0),xy(40,0),xy(40,20),xy(0,20)]
    loops=[[('line',corners[i],corners[(i+1)%4]) for i in range(4)],
           [('circle',xy(20,10),3*scale)]]
    swtree.set_profile(arc,f,loops,dims=[('length',i) for i in range(4)]+[('diameter',0)])
    positions=[struct.unpack_from('<3d',i['_0'].values['_2']) for i in f.values['sketch'].values['list1'].values['items']]
    xmin,ymin=xy(0,0);xmax,ymax=xy(40,20)
    for x,y,z in positions:
        assert x*1000<xmin or x*1000>xmax or y*1000<ymin or y*1000>ymax
        assert z==0
    assert len(set(positions))==5
    before=arc.dumps(); swtree.place_dimensions(f)
    assert arc.dumps()==before
    assert mfc.load_archive(before).dumps()==before


def test_diagonal_dimension_text_is_perpendicular_to_line():
    arc=_arc('P1');f=_sketch1(arc)
    loop=[('line',(0,0),(30,40)),('line',(30,40),(0,40)),('line',(0,40),(0,0))]
    swtree.set_profile(arc,f,[loop],dims=[('length',0)])
    ann=f.values['sketch'].values['list1'].values['items'][0]['_0']
    x,y,_=struct.unpack_from('<3d',ann.values['_2'])
    assert (x-.015)*.03+(y-.02)*.04==pytest.approx(0,abs=1e-12)
    assert x>.015 and y<.02
