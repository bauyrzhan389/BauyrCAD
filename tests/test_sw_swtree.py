"""SolidWorks 2024 opening a part whose tree BauyrCAD extended without SolidWorks (only the
judge here): Sketch2 + Boss-Extrude2 of the donor P2 transplanted onto the triangle part T3
(a combination SolidWorks never saved) opens with the four features and the volume of
SolidWorks' own T3B, rebuilds cleanly, the new depth D1 edits, and SolidWorks saves it."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from bauyrcad import solidworks
from bauyrcad.core import mfc, sldprt, swtree
from sw_support import needs_solidworks

pytestmark = needs_solidworks
DATA = Path(__file__).parent / "data" / "sw" / "mfc"
T3B_VOLUME = 5120.575          # mm³, SolidWorks' own T3B


def _tree(sw, doc):
    out, user = [], False
    f = sw.T(doc.FirstFeature(), "IFeature")
    while f is not None:
        if user:
            out.append((f.Name, f.GetTypeName2()))
        user = user or f.GetTypeName2() == "OriginProfileFeature"
        f = sw.T(f.GetNextFeature(), "IFeature")
    return out


def test_solidworks_opens_a_grafted_tree(sw, tmp_path):
    container, arc = swtree.load(DATA / "T3.SLDPRT")
    new = swtree.transplant(arc, mfc.load_archive(sldprt.read_sections(DATA / "P2.SLDPRT")),
                            ["Sketch2", "Boss-Extrude2"])
    for p in new[0].values["sketch"].values["points"]:     # the circle onto the corner (30, 0)
        if p["kind"] != 0x1F:
            p["x"] -= 0.010
            p["y"] -= 0.010
    path = tmp_path / "g2.SLDPRT"
    swtree.save(container, arc, path)
    r = sw.sw.OpenDoc6(str(path.resolve()), 1, 1, "", 0, 0)
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None, r
    sw._docs.append(doc)
    assert _tree(sw, doc) == [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion"),
                              ("Sketch2", "ProfileFeature"), ("Boss-Extrude2", "ICE")]
    assert solidworks._volume(sw, doc) == pytest.approx(T3B_VOLUME, rel=1e-6)
    ok, issues = solidworks.rebuild_health(sw, doc)
    assert ok and not issues
    d = doc.Parameter("D1@Boss-Extrude2")
    sw.T(d, "IDimension").SystemValue = 0.030
    doc.EditRebuild3()
    # 10 mm more of the d12 boss, all of it beyond the 10 mm triangle
    assert solidworks._volume(sw, doc) == pytest.approx(T3B_VOLUME + math.pi * 6 ** 2 * 10,
                                                        rel=1e-6)
    again = tmp_path / "again.SLDPRT"
    sw.save(doc, again)
    sw.close(doc)
    assert [f.name for f in sldprt.feature_tree(again)][-2:] == ["Sketch2", "Boss-Extrude2"]


SLOT = [("line", (0, -5), (30, -5)), ("arc", (30, 0), (30, -5), (30, 5)),
        ("line", (30, 5), (0, 5)), ("arc", (0, 0), (0, 5), (0, -5))]
HOLED = [[("line", (0, 0), (40, 0)), ("line", (40, 0), (40, 20)), ("line", (40, 20), (0, 20)),
          ("line", (0, 20), (0, 0))], [("circle", (20, 10), 4)]]


@pytest.mark.parametrize("loops,dims,edits,volume", [
    ([HOLED[0]], [("length", 0), ("length", 1)], [("D1", 50), ("D2", 30)], 15000),
    ([[('circle', (3, 4), 9)]], [("diameter", 0)], [("D1", 20)], math.pi * 100 * 10),
    (HOLED, [("length", 0), ("diameter", 0)], [("D1", 50), ("D2", 10)],
     (1000 - math.pi * 25) * 10),
])
def test_solidworks_edits_written_sketch_dimensions(sw, tmp_path, loops, dims, edits, volume):
    container, arc = swtree.load(DATA / "P1.SLDPRT")
    sketch = next(f for f in arc.features if swtree.name(f) == "Sketch1")
    swtree.set_profile(arc, sketch, loops, dims=dims)
    path = tmp_path / "dimensions.SLDPRT"
    swtree.save(container, arc, path)
    r = sw.sw.OpenDoc6(str(path.resolve()), 1, 1, "", 0, 0)
    assert not isinstance(r, tuple) or r[1] == 0, r
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None
    sw._docs.append(doc)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    for dim, size in edits:
        d = sw.T(doc.Parameter(f"{dim}@Sketch1"), "IDimension")
        assert d is not None
        d.SystemValue = size / 1000
        doc.EditRebuild3()
        assert d.SystemValue == pytest.approx(size / 1000)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(volume, rel=1e-6)
    saved = tmp_path / "dimensions_saved.SLDPRT"
    sw.save(doc, saved)
    sw.close(doc)
    r = sw.sw.OpenDoc6(str(saved.resolve()), 1, 1, "", 0, 0)
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None
    sw._docs.append(doc)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(volume, rel=1e-6)


@pytest.mark.parametrize("loops, area", [([SLOT], 300 + math.pi * 25),
                                         (HOLED, 800 - math.pi * 16)])
def test_solidworks_rebuilds_profiles_written_from_geometry(sw, tmp_path, loops, area):
    """P1's rectangle replaced by a slot (lines + arcs) or by a rectangle with a round hole
    (two loops): SolidWorks rebuilds Boss-Extrude1 (10 mm) from them, D1 edits."""
    container, arc = swtree.load(DATA / "P1.SLDPRT")
    swtree.set_profile(arc, next(f for f in arc.features if swtree.name(f) == "Sketch1"), loops)
    path = tmp_path / "profile.SLDPRT"
    swtree.save(container, arc, path)
    r = sw.sw.OpenDoc6(str(path.resolve()), 1, 1, "", 0, 0)
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None, r
    sw._docs.append(doc)
    assert _tree(sw, doc) == [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion")]
    assert solidworks._volume(sw, doc) == pytest.approx(area * 10, rel=1e-6)
    ok, issues = solidworks.rebuild_health(sw, doc)
    assert ok and not issues
    sw.T(doc.Parameter("D1@Boss-Extrude1"), "IDimension").SystemValue = 0.025
    doc.EditRebuild3()
    assert solidworks._volume(sw, doc) == pytest.approx(area * 25, rel=1e-6)
    sw.close(doc)
