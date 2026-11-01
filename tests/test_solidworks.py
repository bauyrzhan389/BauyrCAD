"""Export to SolidWorks (.SLDPRT feature trees, .SLDASM with mates), checked by reopening the
files in SolidWorks AND by decoding the saved files offline (bauyrcad.core.sldprt), so the
native tree is proven from the file itself and not only by the SolidWorks API. Runs only where
SolidWorks is installed and NOT already open (the tests never touch a user's session); set
BAUYRCAD_SW_TESTS=1 to force. Shared helpers: tests/sw_support.py."""
import math

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.api import Assembly, Part
from bauyrcad.core import geometry, sldprt
from bauyrcad.core.sketch import Constraint, constraints, solver
from sw_support import (api_tree, assert_children_decoded, assert_decoded_matches_api,
                        assert_decoded_mates, assert_decoded_tree, assert_same_mass, decoded,
                        doc_steps, needs_solidworks, rebuild_errors, reopen)

pytestmark = needs_solidworks

# GetTypeName2 of each BauyrCAD step kind (by its default name). A later boss or cut on an
# existing body may be saved as an Instant3D feature ('ICE').
KINDS = {"Sketch": "ProfileFeature", "Boss-Extrude": {"Extrusion", "Boss", "ICE"},
         "Cut-Extrude": {"Cut", "ICE"}, "Revolve": "Revolution", "Cut-Revolve": "RevCut"}


def kind(name):
    return KINDS[name.rstrip("0123456789")]


def parts():
    p = Part("plate")
    s = p.sketch("front"); s.rect((0, 0), (80, 50)); s.circle((40, 25), d=20)
    p.extrude(s, 10)
    s2 = p.sketch(on=p.face("+z")); s2.rect((5, 5), (20, 15))
    p.cut(s2, 4)
    q = Part("shaft")
    s = q.sketch("front"); s.centerline((0, 0), (0, 10))
    s.polyline([(0, 0), (12, 0), (12, 50), (0, 50)], close=True)
    q.revolve(s)
    s2 = q.sketch("front"); s2.centerline((0, 0), (0, 10)); s2.rect((10, 20), (13, 26))
    q.revolve_cut(s2, 270)
    r = Part("block")
    s = r.sketch("top"); s.rect((-20, -15), (20, 15))
    r.extrude(s, 30, midplane=True)
    s2 = r.sketch("right"); s2.circle((0, 0), d=8)
    r.cut(s2, through=True, both=True)
    c = Part("contours")
    s = c.sketch("front"); s.rect((0, 0), (60, 20)); s.rect((20, -20), (40, 40))
    c.extrude(s, 5, contours=[(30, 10), (50, 10)])
    w = Part("wedge")
    s = w.sketch("front"); s.polyline([(0, 0), (60, 0), (60, 10), (0, 30)], close=True)
    w.extrude(s, 40)
    s2 = w.sketch(on=w.face(normal=(1, 3, 0))); s2.circle((0, 0), d=8)
    w.cut(s2, 5)
    sp = Part("curvy")
    s = sp.sketch("front")
    s.spline([(-10, 20), (0, 30), (15, 25), (30, 35)])
    s.line((30, 35), (30, 45)); s.line((30, 45), (-10, 45)); s.line((-10, 45), (-10, 20))
    sp.extrude(s, 4)
    return {"plate": (p, ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1"]),
            "shaft": (q, ["Sketch1", "Revolve1", "Sketch2", "Cut-Revolve1"]),
            "block": (r, ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1"]),
            "contours": (c, ["Sketch1", "Boss-Extrude1"]),
            "wedge": (w, ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1"]),
            "curvy": (sp, ["Sketch1", "Boss-Extrude1"])}


# the D1 dimension SolidWorks shows on each operation (mm, degrees)
DEPTHS = {"plate": {"Boss-Extrude1": 10, "Cut-Extrude1": 4},
          "shaft": {"Revolve1": 360, "Cut-Revolve1": 270},
          "block": {"Boss-Extrude1": 30}, "contours": {"Boss-Extrude1": 5},
          "wedge": {"Boss-Extrude1": 40, "Cut-Extrude1": 5}, "curvy": {"Boss-Extrude1": 4}}


def test_parts_rebuild_as_native_feature_trees(sw, tmp_path):
    for name, (part, feats) in parts().items():
        rep = solidworks.export_part(sw, part.doc, tmp_path / name)
        assert rep.native and not rep.warnings, (name, rep.warnings)
        assert doc_steps(part.doc) == rep.features == feats, name
        tol = 1e-4 if name == "curvy" else 1e-6
        assert rep.volume == pytest.approx(geometry.volume(part.doc.body), rel=tol), name
        # the saved file itself: exactly these steps, natively, each sketch absorbed by the
        # operation after it, and the operation's depth / angle
        assert_decoded_tree(rep.path, [(f, kind(f)) for f in feats], exact=True)
        t = decoded(rep.path)
        for sk, op in zip(feats[::2], feats[1::2]):
            assert t[sk].parent == t[op].id, (name, sk, op)
        assert_children_decoded(rep.path)
        for op, value in DEPTHS[name].items():
            assert sldprt.dim_value(t[op].dims["D1"]) == pytest.approx(value), (name, op)
        doc = reopen(sw, rep.path)
        assert_decoded_matches_api(sw, doc, rep.path)
        assert rebuild_errors(sw, doc) == [], name
        assert_same_mass(sw, doc, part.doc.body, rel=tol)
        sw.close(doc)


def test_revolve_with_external_and_ordinary_axis_lines(sw, tmp_path):
    for external in (True, False):
        p = Part("external-axis" if external else "ordinary-axis")
        axis_sketch = p.sketch("front")
        line = axis_sketch.line((0, -10), (0, 40))
        profile = p.sketch("front") if external else axis_sketch
        profile.rect((10, 0), (20, 30))
        p.revolve(profile, axis=line)
        rep = solidworks.export_part(sw, p.doc, tmp_path / p.name)
        assert rep.volume == pytest.approx(p.volume(), rel=1e-6)
        reopened = reopen(sw, rep.path)
        assert solidworks._volume(sw, reopened) == pytest.approx(p.volume(), rel=1e-6)
        if rep.native:
            assert not rep.warnings and rep.features == doc_steps(p.doc), rep.warnings
            assert "Revolve1" in [n for n, _, _ in api_tree(sw, reopened)]
            assert_decoded_tree(rep.path, [(f, kind(f)) for f in doc_steps(p.doc)], exact=True)
            assert_decoded_matches_api(sw, reopened, rep.path)
        else:
            assert any("imported as a solid body" in w for w in rep.warnings)
            # the file shows the fallback for what it is: one body without history
            assert [f.type_name for f in sldprt.feature_tree(rep.path)] == ["BaseBody"]
        sw.close(reopened)


def applied_parts():
    def plate(x0=0.0):
        p = Part("plate")
        sk = p.sketch("front")
        sk.rect((x0, 0), (x0 + 100, 50))
        p.extrude(sk, 20)
        return p

    out = {}
    ch = plate()
    ch.chamfer((50, 50, 20), d=2)
    ch.chamfer((50, 0, 20), d=2, d2=4)
    ch.chamfer((0, 25, 20), d=3, angle=30)
    out["chamfered"] = (ch, {"Chamfer1": "Chamfer", "Chamfer2": "Chamfer", "Chamfer3": "Chamfer"})
    p = plate()
    p.fillet(p.edges_where(direction="z"), r=6)
    h = p.hole(p.face("+z"), [(20, 25)], size="M6", kind="counterbore")
    p.pattern_linear(h, "x", 30, 3)
    p.hole(p.face("+z"), (50, 10), size="M5", kind="countersink")
    p.hole(p.face("+z"), (50, 40), d=8, depth=12)
    out["plate"] = (p, {"Fillet1": "Fillet", "LPattern1": "LPattern",
                        "CBORE for M6 Screw1": "HoleWzd", "Hole1": "HoleWzd"})
    q = plate()
    q.shell(2, remove=q.face("+z"))
    q.draft([q.face(n) for n in ("+x", "-x")], 3, neutral="front")
    out["shelled"] = (q, {"Shell1": "Shell", "Draft1": "Draft"})
    f = Part("flange")
    sk = f.sketch("front")
    sk.circle((0, 0), d=100)
    sk.circle((0, 0), d=30)
    f.extrude(sk, 10)
    s2 = f.sketch(on=f.face("+z"))
    s2.circle((35, 0), d=8)
    c = f.cut(s2, through=True)
    f.pattern_circular(c, axis=f.face(kind="cylinder", d=30), count=8)
    out["flange"] = (f, {"CirPattern1": "CirPattern"})
    m = plate(-50)
    s3 = m.sketch("front")
    s3.rect((10, 0), (20, 10))
    m.mirror(m.extrude(s3, 30), "right")
    m.mirror(plane="top")
    out["mirrored"] = (m, {"Mirror1": "MirrorPattern", "Mirror2": "MirrorSolid"})
    lo = Part("loft")
    a = lo.sketch("top")
    a.rect((-20, -20), (20, 20))
    b = lo.sketch(lo.plane("top", offset=30))
    b.circle((0, 0), d=20)
    lo.loft([a, b])
    path = lo.sketch("front")
    path.polyline([(-15, 30), (-15, 45), (15, 45)])
    lo.sweep(path, d=6)
    out["loft"] = (lo, {"Plane1": "RefPlane", "Loft1": "Blend", "Sweep1": "Sweep"})
    sp = Part("spring")
    sp.sweep(sp.helix(center=(0, 0, 0), axis="y", d=30, pitch=8, turns=4), d=3)
    out["spring"] = (sp, {"Helix/Spiral1": "Helix", "Sweep1": "Sweep"})
    return out


def test_applied_features_rebuild_natively(sw, tmp_path):
    for name, (part, want) in applied_parts().items():
        rep = solidworks.export_part(sw, part.doc, tmp_path / name, require_native=True)
        steps = doc_steps(part.doc)
        assert rep.native and not rep.warnings and rep.features == steps, (name, rep.warnings)
        tol = {"loft": 1e-2, "spring": 1e-3}.get(name, 1e-6)  # blends / helices fit a bit differently
        assert rep.volume == pytest.approx(geometry.volume(part.doc.body), rel=tol), name
        # every BauyrCAD step (from the document, not the exporter's report) is in the saved
        # file, in order, natively, with the right type, and its absorbed sketches are found
        assert_decoded_tree(rep.path, [(n, want.get(n)) for n in steps])
        assert_children_decoded(rep.path)
        if name == "loft":                  # both loft profiles and the sweep path absorbed
            t = decoded(rep.path)
            assert t["Loft1"].children == [t["Sketch1"].id, t["Sketch2"].id]
            assert t["Sweep1"].children == [t["Sketch3"].id]
        doc = reopen(sw, rep.path)
        assert_decoded_matches_api(sw, doc, rep.path)
        types = {n: t for n, t, _ in api_tree(sw, doc)}
        for feat, kind_ in want.items():
            assert feat in types, (name, feat, sorted(types))
            assert types[feat] == kind_, (name, feat, types[feat])
        assert rebuild_errors(sw, doc) == [], name
        assert_same_mass(sw, doc, part.doc.body, rel=tol)
        sw.close(doc)


@pytest.mark.parametrize("fmt", ["step", "iges", "brep"])
def test_imported_base_preserves_later_features(sw, tmp_path, fmt):
    blk = Part("blk"); s = blk.sketch("front"); s.rect((0, 0), (30, 30)); blk.extrude(s, 10)
    blk.export(tmp_path / ("blk." + fmt))
    e = Part("imported")
    e.import_file(tmp_path / ("blk." + fmt))
    s = e.sketch(on=e.face("+z")); s.circle((0, 0), d=10)
    e.extrude(s, 5)
    cut = e.sketch("front"); cut.circle((20, 20), d=6); e.cut(cut, through=True)
    rep = solidworks.export_part(sw, e.doc, tmp_path / "imported", require_native=True)
    assert rep.native and not rep.warnings
    names = [f.name for f in e.doc.features]
    assert rep.features == names
    assert rep.volume == pytest.approx(geometry.volume(e.doc.body), rel=1e-6)
    # the imported base is the only body without history; everything after it is native
    assert_decoded_tree(rep.path, [(names[0], "BaseBody"), (names[1], "ProfileFeature"),
                                   (names[2], "ICE"), (names[3], "ProfileFeature"),
                                   (names[4], "ICE")], exact=True)
    doc = reopen(sw, rep.path)
    assert_decoded_matches_api(sw, doc, rep.path)
    assert rebuild_errors(sw, doc) == []
    assert_same_mass(sw, doc, e.doc.body)
    types = {name: (feat.GetTypeName() if kind_ == "ICE" else kind_)
             for name, kind_, feat in api_tree(sw, doc)}
    assert types[names[0]] == "BaseBody"
    assert types[names[2]] == "Boss"
    assert types[names[4]] == "Cut"
    # Suppressing the later cut must recover the preceding body, proving it is editable.
    feat = next(feat for name, _, feat in api_tree(sw, doc) if name == names[4])
    assert feat.SetSuppression2(0, 1, None)
    doc.EditRebuild3()
    before = e.doc.regenerate(upto=4).body
    assert solidworks._volume(sw, doc) == pytest.approx(geometry.volume(before), rel=1e-6)
    # ... and the saved state says so: decode a copy saved in that state
    copy = tmp_path / "imported_suppressed.SLDPRT"
    r = sw.T(doc.Extension, "IModelDocExtension").SaveAs(str(copy), 0, 1 | 2, None, 0, 0)
    assert (r[0] if isinstance(r, tuple) else r) and copy.exists()
    assert {f.name: f.suppressed for f in sldprt.feature_tree(copy)} == {
        n: n == names[4] for n in names}
    t = decoded(copy)                       # KeyWords drops a suppressed feature's children
    assert t[names[4]].children == [t[names[3]].id] and t[names[2]].children == [t[names[1]].id]
    assert_decoded_matches_api(sw, doc, copy)
    e.doc.regenerate()
    sw.close(doc)


def test_fully_defined_sketch_stays_fully_defined(sw, tmp_path):
    d = Part("dims")
    s = d.sketch("front")
    L = s.rect((0, 0), (80, 50))
    c = s.circle((30, 20), d=20)
    sk = s.sk
    for e in L[:2]:
        s.dim(e)
    s.dim(c)
    for kind_ in ("hdistance", "vdistance"):
        k = Constraint(0, kind_, [("p", 0), ("p", c.center)])
        k.value = constraints.measure(sk, k)
        k.data["s"] = 1.0
        assert solver.try_add(sk, k)[0]
    corner = min((pid for pid in sk.points if pid), key=lambda pid: np.linalg.norm(sk.pt(pid)))
    assert solver.try_add(sk, Constraint(0, "coincident", [("p", corner), ("p", 0)]))[0]
    assert solver.analyze(sk).dof == 0
    d.extrude(s, 12)
    rep = solidworks.export_part(sw, d.doc, tmp_path / "dims")
    assert rep.native and not rep.warnings
    # the five driving dimensions and the depth are in the saved file
    t = decoded(rep.path)
    assert sorted(sldprt.dim_value(v) for v in t["Sketch1"].dims.values()) == [20, 20, 30, 50, 80]
    assert sldprt.dim_value(t["Boss-Extrude1"].dims["D1"]) == 12
    doc = reopen(sw, rep.path)
    assert_decoded_matches_api(sw, doc, rep.path)
    assert rebuild_errors(sw, doc) == []
    assert_same_mass(sw, doc, d.doc.body)
    sketch = next(f for n, t_, f in api_tree(sw, doc) if t_ == "ProfileFeature")
    swsk = sw.T(sketch.GetSpecificFeature2(), "ISketch")
    assert swsk.GetConstrainedStatus() == 3                # swFullyConstrained
    n = 0
    dd = sketch.GetFirstDisplayDimension()
    while dd is not None:
        n += 1
        dd = sketch.GetNextDisplayDimension(dd)
    assert n == 5
    sw.close(doc)


def test_assembly_moves_by_its_mates_in_solidworks(sw, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    base = Part("base"); s = base.sketch("front"); s.rect((0, 0), (60, 40)); s.circle((30, 20), d=10)
    base.extrude(s, 10); base.save(src / "base.bcad")
    pin = Part("pin"); s = pin.sketch("front"); s.circle((0, 0), d=10)
    pin.extrude(s, 40); pin.save(src / "pin.bcad")
    link = Part("link"); s = link.sketch("front"); s.rect((-8, -8), (50, 8)); s.circle((0, 0), d=10)
    link.extrude(s, 6); link.save(src / "link.bcad")
    asm = Assembly("hinge")
    b = asm.add(src / "base.bcad")
    p = asm.add(src / "pin.bcad", at=(90, 0, 0))
    lk = asm.add(src / "link.bcad", at=(0, 90, 30))
    asm.mate("concentric", p.face(kind="cylinder"), b.face(kind="cylinder"))
    asm.mate("coincident", p.face("-z"), b.face("+z"))
    asm.mate("concentric", lk.face(kind="cylinder"), p.face(kind="cylinder"))
    asm.mate("distance", lk.face("-z"), b.face("+z"), 5.0)
    asm.mate("angle", lk.face("+y"), b.face("+y"), 30)
    asm.save(src / "hinge.bcasm")
    rep = solidworks.export_assembly(sw, asm.asm, tmp_path / "out", require_native=True)
    assert all(ok for _, ok, _ in rep.mates), rep.mates
    assert not rep.warnings and all(pr.native for pr in rep.parts.values())
    for pr in rep.parts.values():                  # each part file holds its native history
        assert_decoded_tree(pr.path, [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion")],
                            exact=True)
    doc = reopen(sw, rep.path)
    adoc = sw.T(doc, "IAssemblyDoc")
    comps = {sw.T(c, "IComponent2").Name2: sw.T(c, "IComponent2") for c in adoc.GetComponents(True)}
    assert {n: c.IsFixed() for n, c in comps.items()} == {"base-1": True, "pin-1": False, "link-1": False}
    groups = [f for n, t, f in api_tree(sw, doc) if t == "MateGroup"]
    n = 0
    sub = sw.T(groups[0].GetFirstSubFeature(), "IFeature")
    while sub is not None:
        n += 1
        sub = sw.T(sub.GetNextSubFeature(), "IFeature")
    assert n == 5
    # the saved file holds the five mates: same names, ids, types, alignment and suppression
    # as the SolidWorks API reports, between the right components, with the right values
    assert_decoded_mates(rep.path, [
        {"name": "Concentric1", "type": "concentric", "components": ["pin<1>", "base<1>"],
         "suppressed": False},
        {"name": "Coincident1", "type": "coincident", "components": ["pin<1>", "base<1>"],
         "suppressed": False},
        {"name": "Concentric2", "type": "concentric", "components": ["link<1>", "pin<1>"],
         "suppressed": False},
        {"name": "Distance1", "type": "distance", "components": ["link<1>", "base<1>"],
         "distance": 5.0, "suppressed": False},
        {"name": "Angle1", "type": "angle", "components": ["link<1>", "base<1>"], "angle": 30.0,
         "suppressed": False}], sw=sw, doc=doc)
    # shove the pin sideways: its mates pull it back onto the bore
    pin_c = comps["pin-1"]
    R0, t0 = solidworks._pose_of(sw, pin_c)
    assert np.allclose(t0, p.pose.t, atol=1e-6)
    pin_c.Transform2 = sw.transform(R0, (t0 + [5, 0, 0]) * 1e-3)
    doc.EditRebuild3()
    assert np.allclose(solidworks._pose_of(sw, pin_c)[1], t0, atol=1e-4)
    assert np.allclose(solidworks._pose_of(sw, comps["link-1"])[0], lk.pose.R, atol=1e-6)
    # Concentric + coincident mates leave the pin free to spin about its axis.
    # Check actual motion after reopening, not just the presence of mate features.
    from bauyrcad.core.assembly import rodrigues
    target = R0 @ rodrigues(np.array([0.0, 0.0, math.pi / 4]))
    pin_c.Transform2 = sw.transform(target, t0 * 1e-3)
    doc.EditRebuild3()
    R1, t1 = solidworks._pose_of(sw, pin_c)
    assert np.allclose(R1, target, atol=1e-6)
    assert np.allclose(t1, t0, atol=1e-4)
    sw.close(doc)


def test_gui_export_button(tmp_path, monkeypatch):
    """File > Export to SolidWorks runs in the background and reports."""
    import time
    from PySide6.QtWidgets import QApplication, QMessageBox
    from bauyrcad.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    shown = []
    def capture_report(box):
        from pathlib import Path
        from PySide6.QtTest import QTest
        shown.append(box.text())
        box.show(); QTest.qWait(80)
        folder = Path('projects/solidworks-export-verification'); folder.mkdir(parents=True, exist_ok=True)
        box.grab().save(str(folder / 'native-export-report.png'))
        box.close()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", capture_report)
    p = Part("plate"); s = p.sketch("front"); s.rect((0, 0), (40, 30)); p.extrude(s, 5)
    w = MainWindow()
    w.show()
    w._set_document(p.doc)
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "plate.SLDPRT"), ""))
    w.a_export_sw.trigger()  # exercise QAction's checked=False argument
    worker = w._sw_worker
    end = time.monotonic() + 240
    while worker.isRunning() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()
    assert (tmp_path / "plate.SLDPRT").exists()
    assert shown and "feature tree" in shown[-1]
    assert_decoded_tree(tmp_path / "plate.SLDPRT", [("Sketch1", "ProfileFeature"),
                                                    ("Boss-Extrude1", "Extrusion")], exact=True)
    w.dirty = False
    w.close()
