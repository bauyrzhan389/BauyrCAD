import math
import numpy as np
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QFileDialog
from bauyrcad import solidworks
from bauyrcad.api import Part, Assembly
from bauyrcad.gui import solidworks_export
from test_gui_revolve_workflow import win


def plate():
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 5)
    return p


def test_part_action_opens_path_dialog_and_requires_native(win, monkeypatch, tmp_path):
    p = plate(); win._set_document(p.doc)
    calls = []
    monkeypatch.setattr(solidworks, 'available', lambda: True)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'part.SLDPRT'), ''))
    monkeypatch.setattr(solidworks_export, 'run', lambda parent, title, fn: fn())
    monkeypatch.setattr(solidworks, 'export_part_file', lambda doc, path, **kw: calls.append((path, kw)))
    win.a_export_sw.trigger()
    assert calls == [(str(tmp_path / 'part.SLDPRT'), {'require_native': True})]
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: ('', ''))
    win.a_export_sw.trigger()
    assert len(calls) == 1


def test_assembly_action_chooses_folder_and_requires_native(win, monkeypatch, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    p = plate(); p.save(tmp_path / 'part.bcad')
    a = Assembly(); a.add(tmp_path / 'part.bcad'); a.save(tmp_path / 'assy.bcasm')
    w = AssemblyWindow(assembly=a.asm); w.show(); w.dirty = False
    calls = []
    monkeypatch.setattr(solidworks, 'available', lambda: True)
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(solidworks_export, 'run', lambda parent, title, fn: fn())
    monkeypatch.setattr(solidworks, 'export_assembly_files', lambda asm, folder, **kw: calls.append((folder, kw)))
    try:
        w.a_export_sw.trigger()
        assert calls == [(str(tmp_path / 'assy_SolidWorks'), {'require_native': True})]
    finally:
        w.dirty = False; w.close()


def test_strict_export_rejects_known_gaps_before_creating_file(tmp_path):
    p = plate(); p.doc.features[0].suppressed = True
    with pytest.raises(solidworks.SolidWorksError, match='suppressed'):
        solidworks.export_part(None, p.doc, tmp_path / 'part.SLDPRT', require_native=True)
    assert not (tmp_path / 'part.SLDPRT').exists()
    p.doc.features[0].suppressed = False
    p.plane('front', offset=5)
    p.axis(point=(0, 0, 0), direction=(0, 1, 0))
    p.point((1, 2, 3))
    p.curve([(0, 0, 0), (10, 5, 0), (20, 0, 5)])
    assert solidworks.native_export_issues(p.doc) == []        # reference geometry exports (WP05)


def test_precheck_refuses_reference_geometry_solidworks_cannot_define():
    from bauyrcad import solidworks_features as swf
    p = plate()
    # BauyrCAD turns the base normal about any line; SolidWorks' 'At angle' plane turns
    # about a line lying in (or parallel to) the base plane
    p.plane('front', angle=30, axis=(1, 0, 1))
    tilted = p.doc.features[-1]
    issues = solidworks.native_export_issues(p.doc)
    assert any(tilted.name in i and 'not parallel to the base plane' in i for i in issues), issues
    with pytest.raises(solidworks._NotNative, match='not parallel'):
        swf.require(tilted, p.doc.context_before(tilted))
    # a planar face's normal can't be the line an angled plane turns about
    q = plate()
    q.plane('front', angle=30, axis=q.face('+z'))
    issues = solidworks.native_export_issues(q.doc)
    assert any("planar face's normal" in i for i in issues), issues
    # ... but it can be what a plane is normal to, and an angle about an axis in the plane is fine
    r = plate()
    r.plane(normal_to=r.face('+z'), at=(5, 5, 5))
    r.plane('front', angle=30, axis='y')
    r.plane('top', angle=-45, axis=r.edges_where(kind='line', direction='x', near=(10, 20, 5))[0])
    assert solidworks.native_export_issues(r.doc) == []


def test_strict_runtime_failure_never_uses_body_fallback(monkeypatch, tmp_path):
    p = plate()
    def fail(*args):
        raise solidworks._NotNative('test feature mismatch')
    monkeypatch.setattr(solidworks, '_native_part', fail)
    monkeypatch.setattr(solidworks, '_solid_part', lambda *a: pytest.fail('flattened export'))
    with pytest.raises(solidworks.SolidWorksError, match='test feature mismatch'):
        solidworks.export_part(None, p.doc, tmp_path / 'part.SLDPRT', require_native=True)
    assert not (tmp_path / 'part.SLDPRT').exists()


def test_later_import_is_rejected_without_flattening(tmp_path):
    base = plate(); source = tmp_path / "base.step"; base.export(source)
    p = Part(); p.import_file(source)
    assert solidworks.native_export_issues(p.doc) == []
    p.import_file(source)
    issues = solidworks.native_export_issues(p.doc)
    assert len(issues) == 1 and p.doc.features[1].name in issues[0]
    with pytest.raises(solidworks.SolidWorksError, match="start of history"):
        solidworks.export_part(None, p.doc, tmp_path / "result", require_native=True)
    assert not (tmp_path / "result.SLDPRT").exists()


# ---- strict gate and pre-checks (no SolidWorks needed) --------------------------------------
def documented_unsupported_types():
    """The feature TYPEs docs/SOLIDWORKS_EXPORT_COVERAGE.md lists as having no translator."""
    import re
    text = (Path(__file__).parents[1] / "docs" / "SOLIDWORKS_EXPORT_COVERAGE.md").read_text("utf-8")
    line = next(l for l in text.splitlines()
                if l.startswith("**Feature types without a native translator"))
    return sorted(re.findall(r"`([a-z_]+)`", line))


def test_gate_is_derived_from_handlers_and_matches_the_coverage_matrix():
    from bauyrcad import solidworks_features as swf
    from bauyrcad.core.features import FEATURE_TYPES
    unsupported = solidworks.unsupported_types()
    assert unsupported == sorted(set(FEATURE_TYPES) - {"sketch", "import_step", *swf.HANDLERS})
    assert set(swf.HANDLERS) <= set(FEATURE_TYPES)
    assert set(swf.PRECHECKS) <= set(FEATURE_TYPES)
    assert documented_unsupported_types() == unsupported


def test_precheck_finds_runtime_only_failures_before_solidworks(tmp_path):
    from bauyrcad import solidworks_features as swf
    from bauyrcad.core.features import ExtrudeFeature, SketchFeature
    from bauyrcad.core.plane import SketchPlane
    # sketch text exports (D7), except what SolidWorks would read as format codes
    p = plate()
    s = p.sketch(on=p.face("+z")); s.text("AB", at=(2, 2), height=4)
    assert solidworks.native_export_issues(p.doc) == []
    s.text("<r30>A", at=(2, 10), height=4)
    issues = solidworks.native_export_issues(p.doc)
    assert any("Sketch2" in i and "format code" in i for i in issues), issues
    # the pre-check is the predicate the handler runs, so the build refuses the same way
    with pytest.raises(solidworks._NotNative, match="format code"):
        swf.require(p.doc.features[2])
    # XC-04: a sketch whose face is gone (BauyrCAD kept it at its last place) is refused
    b = plate()
    on_side = b.sketch(on=b.face("+x")); on_side.circle((-2.5, 10), d=2)
    base = b.doc.features[0].sketch
    base.entities.clear(); base.constraints.clear()
    base.add_circle((10, 10), 8)                       # the plate becomes a disc: no +X face
    b.doc.regenerate()
    assert b.doc.features[2].warning and not b.doc.features[2].error
    issues = solidworks.native_export_issues(b.doc)
    assert any(i.startswith("Sketch2: Sketch plane") and "fix the reference" in i
               for i in issues), issues
    # revolve about the global Y axis (an axis_spec)
    q = Part(); s = q.sketch("front"); s.rect((10, 0), (20, 30)); q.revolve(s, axis="y")
    issues = solidworks.native_export_issues(q.doc)
    assert any("Revolve1" in i and "axis" in i for i in issues), issues
    # revolve about a line in another sketch / an ordinary line outside the profile: SolidWorks
    # makes no feature (seen in SolidWorks 2024), so they are refused before it starts; a
    # profile edge used as the axis exports
    for external in (True, False):
        r = Part()
        axis_sketch = r.sketch("front"); line = axis_sketch.line((0, -10), (0, 40))
        profile = r.sketch("front") if external else axis_sketch
        profile.rect((10, 0), (20, 30)); r.revolve(profile, axis=line)
        assert r.doc.features[-1].error is None
        issues = solidworks.native_export_issues(r.doc)
        want = "another sketch" if external else "ordinary sketch line"
        assert len(issues) == 1 and "Revolve1" in issues[0] and want in issues[0], issues
        with pytest.raises(solidworks._NotNative, match=want):
            swf.require(r.doc.features[-1], r.doc.context_before(r.doc.features[-1]))
    e = Part(); s = e.sketch("front")
    lines = [s.line(a, b) for a, b in (((0, 0), (10, 0)), ((10, 0), (10, 30)),
                                       ((10, 30), (0, 30)), ((0, 30), (0, 0)))]
    e.revolve(s, axis=lines[-1])
    assert solidworks.native_export_issues(e.doc) == []
    # an import holding two solids
    two = Part(); a = two.sketch("front"); a.rect((0, 0), (5, 5)); a.rect((10, 0), (15, 5))
    two.extrude(a, 5); two.export(tmp_path / "two.step")
    r = Part(); r.import_file(tmp_path / "two.step")
    issues = solidworks.native_export_issues(r.doc)
    assert any("2 solid bodies" in i for i in issues), issues
    # a feature in an error state (a cut that misses the body)
    e = plate()
    sk = e.doc.add_feature(SketchFeature(SketchPlane((0, 0, 50), (0, 0, 1), (1, 0, 0))))
    sk.sketch.add_rectangle((100, 100), (110, 110))
    e.doc.add_feature(ExtrudeFeature(sk.id, 3, "cut"))
    e.doc.regenerate()
    assert e.doc.features[-1].error
    issues = solidworks.native_export_issues(e.doc)
    assert any("Cut-Extrude1" in i and "error in BauyrCAD" in i for i in issues), issues


def test_parameters_and_equations_block_strict_export(tmp_path):
    p = plate()
    p.parameter("t", "5 mm")
    assert any("Parameters: 1 parameter(s) and 0 equation link(s)" in i
               for i in solidworks.native_export_issues(p.doc))
    p.bind_parameter(p.doc.features[1], "depth", "t + 1 mm")
    issues = solidworks.native_export_issues(p.doc)
    assert any("1 parameter(s) and 1 equation link(s)" in i for i in issues), issues
    with pytest.raises(solidworks.SolidWorksError, match="equation link"):
        solidworks.export_part(None, p.doc, tmp_path / "p.SLDPRT", require_native=True)
    assert not (tmp_path / "p.SLDPRT").exists()


def test_strict_one_call_exports_check_before_starting_solidworks(monkeypatch, tmp_path):
    class NoSession:
        def __init__(self, *a, **k):
            pytest.fail("SolidWorks was started although the pre-check knows the export fails")
    monkeypatch.setattr(solidworks, "Session", NoSession)
    p = plate(); s = p.sketch(on=p.face("+z")); s.circle((10, 10), d=5); p.cut(s, 2)
    p.doc.features[-1].suppressed = True; p.doc.regenerate()
    with pytest.raises(solidworks.SolidWorksError, match="suppressed"):
        solidworks.export_part_file(p.doc, tmp_path / "p.SLDPRT", require_native=True)
    p.save(tmp_path / "p.bcad")
    a = Assembly(); a.add(tmp_path / "p.bcad"); a.save(tmp_path / "a.bcasm")
    with pytest.raises(solidworks.SolidWorksError, match="suppressed"):
        solidworks.export_assembly_files(a.asm, tmp_path / "out", require_native=True)
    assert not (tmp_path / "out").exists()


def test_gui_part_export_shows_known_gaps_before_starting_solidworks(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    p = plate(); p.doc.features[1].suppressed = True; p.doc.regenerate(); win._set_document(p.doc)
    shown = []
    monkeypatch.setattr(solidworks, "available", lambda: True)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: pytest.fail("asked for a path"))
    monkeypatch.setattr(solidworks_export, "run", lambda *a, **k: pytest.fail("export started"))
    assert win.export_solidworks() is None
    assert shown and "Boss-Extrude1: suppressed" in shown[0] and "Nothing was saved" in shown[0]


def test_gui_assembly_export_shows_known_gaps_before_starting_solidworks(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from bauyrcad.gui.assembly_window import AssemblyWindow
    p = plate(); p.parameter("t", "5 mm"); p.save(tmp_path / "part.bcad")
    a = Assembly(); a.add(tmp_path / "part.bcad"); a.save(tmp_path / "assy.bcasm")
    w = AssemblyWindow(assembly=a.asm); w.show(); w.dirty = False
    shown = []
    monkeypatch.setattr(solidworks, "available", lambda: True)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: pytest.fail("asked for a folder"))
    monkeypatch.setattr(solidworks_export, "run", lambda *a, **k: pytest.fail("export started"))
    try:
        assert w.export_solidworks() is None
        assert shown and "Parameters: 1 parameter(s)" in shown[0]
    finally:
        w.dirty = False; w.close()


def test_gui_exports_a_bodyless_part_on_a_private_copy(win, monkeypatch, tmp_path):
    p = Part(); s = p.sketch("front"); s.rect((0, 0), (20, 10)); p.plane("top", offset=5)
    assert p.doc.body is None
    win._set_document(p.doc)
    calls = []
    monkeypatch.setattr(solidworks, "available", lambda: True)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "sk.SLDPRT"), ""))
    monkeypatch.setattr(solidworks_export, "run", lambda parent, title, fn: fn())
    monkeypatch.setattr(solidworks, "export_part_file", lambda doc, path, **kw: calls.append(doc))
    win.a_export_sw.trigger()
    assert len(calls) == 1 and calls[0] is not win.doc
    assert calls[0].to_dict() == win.doc.to_dict()


def test_export_works_on_a_private_copy_regenerated_once(monkeypatch, tmp_path):
    from bauyrcad.core import geometry
    from bauyrcad.core.document import Document
    p = plate()
    s2 = p.sketch(on=p.face("+z")); s2.circle((10, 10), d=6); p.cut(s2, 2)
    p.fillet(p.edges_where(direction="z"), r=2)
    live = p.doc
    def vol(shape):
        return 0.0 if shape is None else geometry.volume(shape)
    want = [vol(live.regenerate(upto=i + 1).body) for i in range(len(live.features))]
    live.regenerate()
    before = live.to_dict()
    calls, seen = [], {}
    real = Document.regenerate

    def counting(self, *a, **k):
        calls.append(self)
        return real(self, *a, **k)
    monkeypatch.setattr(Document, "regenerate", counting)

    def fake_native_part(s, hist, report):
        assert hist.doc is not live
        for i, f in enumerate(hist.doc.features):       # what every handler gets: cached
            assert hist.ctx(i).body is hist.before(i)
            assert vol(hist.after[i]) == pytest.approx(want[i])
            f.error = "touched by the export"            # a worker may change ITS copy only
        seen["hist"] = hist
        raise solidworks._NotNative("stop here")
    monkeypatch.setattr(solidworks, "_native_part", fake_native_part)
    with pytest.raises(solidworks.SolidWorksError, match="stop here"):
        solidworks.export_part(None, live, tmp_path / "p.SLDPRT", require_native=True)
    assert calls == [seen["hist"].doc]                  # ONE regeneration, of the copy
    assert live.to_dict() == before and all(f.error is None for f in live.features)


class FakeSession:
    """Stands in for a SolidWorks session where the exporter only saves and closes."""
    def __init__(self):
        self.saved, self.closed = [], []

    def save(self, doc, path):
        Path(path).write_text("fake")
        self.saved.append(path)

    def close(self, doc):
        self.closed.append(doc)


def test_notes_do_not_block_strict_export_but_losses_do(monkeypatch, tmp_path):
    p = plate()
    monkeypatch.setattr(solidworks, "_check_health", lambda s, doc, report: None)
    monkeypatch.setattr(solidworks, "_check_final", lambda s, doc, hist, report: None)

    def native(note=None, loss=None):
        def fake(s, hist, report):
            report.features += [f.name for f in hist.doc.features]
            report.notes += [note] if note else []
            report.warnings += [loss] if loss else []
            return "doc"
        return fake
    s = FakeSession()
    monkeypatch.setattr(solidworks, "_native_part", native(note="Loft1: surface differs slightly"))
    rep = solidworks.export_part(s, p.doc, tmp_path / "a.SLDPRT", require_native=True)
    assert rep.native and rep.notes == ["Loft1: surface differs slightly"] and not rep.warnings
    assert s.saved and s.closed == ["doc"]
    s = FakeSession()
    monkeypatch.setattr(solidworks, "_native_part",
                        native(loss="Sketch1: 1 relation(s) not transferred"))
    with pytest.raises(solidworks.SolidWorksError, match="relation"):
        solidworks.export_part(s, p.doc, tmp_path / "b.SLDPRT", require_native=True)
    assert not s.saved and s.closed == ["doc"]          # the SolidWorks document is not left open


def test_a_com_error_closes_the_document_and_leaves_the_part_alone(monkeypatch, tmp_path):
    p = plate()
    before = p.doc.to_dict()
    monkeypatch.setattr(solidworks, "_native_part", lambda s, hist, report: "doc")

    def com_error(s, doc, report):
        raise RuntimeError("(-2147417848, 'The object invoked has disconnected from its clients')")
    monkeypatch.setattr(solidworks, "_check_health", com_error)
    s = FakeSession()
    with pytest.raises(RuntimeError, match="disconnected"):
        solidworks.export_part(s, p.doc, tmp_path / "c.SLDPRT")
    assert s.closed == ["doc"] and not s.saved
    assert p.doc.to_dict() == before and p.doc.body is not None


def test_rebuild_errors_and_warnings_are_blocking_losses(monkeypatch):
    rep = solidworks.PartReport("x")
    monkeypatch.setattr(solidworks, "rebuild_health",
                        lambda s, doc: (True, [("Fillet1", 18, False), ("Sketch3", 51, True)]))
    solidworks._check_health(None, None, rep)
    assert rep.warnings == ["Fillet1: SolidWorks rebuild error (the fillet radius is too big)",
                            "Sketch3: SolidWorks rebuild warning (sketch error)"]
    assert not rep.notes


def test_a_bodyless_part_never_falls_back_to_a_missing_solid(monkeypatch, tmp_path):
    p = Part(); s = p.sketch("front"); s.rect((0, 0), (20, 10))

    def fail(*a):
        raise solidworks._NotNative("no luck")
    monkeypatch.setattr(solidworks, "_native_part", fail)
    monkeypatch.setattr(solidworks, "_solid_part", lambda *a: pytest.fail("solid fallback of nothing"))
    with pytest.raises(solidworks.SolidWorksError, match="no solid body"):
        solidworks.export_part(FakeSession(), p.doc, tmp_path / "sk.SLDPRT")


def test_feature_tolerances_follow_the_feature_and_its_sketches():
    q = Part(); s = q.sketch("front"); s.spline([(0, 0), (10, 5), (20, 0)]); s.line((20, 0), (0, 0))
    q.extrude(s, 3)
    q.fillet(q.edges_where(direction="z")[:1], r=0.5)
    by_id = {f.id: f for f in q.doc.features}
    assert solidworks._feature_tol(q.doc.features[1], by_id) == solidworks._SPLINE
    assert solidworks._feature_tol(q.doc.features[2], by_id) == solidworks._EXACT
    p = plate()
    by_id = {f.id: f for f in p.doc.features}
    assert solidworks._feature_tol(p.doc.features[1], by_id) == solidworks._EXACT
    t = plate()
    s = t.sketch(on=t.face("+z")); s.text("AB", at=(2, 2), height=4); t.cut(s, 0.5)
    by_id = {f.id: f for f in t.doc.features}
    assert solidworks._feature_tol(t.doc.features[3], by_id) == solidworks._TEXT


def test_sketch_frame_needs_are_predicted_from_the_sketch():
    """Which sketches need SolidWorks' sketch coordinate system moved / turned (WP03)."""
    from types import SimpleNamespace as NS
    from bauyrcad.core.sketch import Sketch
    sk = Sketch(); sk.add_circle((5, 5), 2)
    aligned, off = NS(hv={"h": "h", "v": "v"}, phi=0.0), NS(hv=None, phi=0.3)
    assert not solidworks._needs_turning(sk, off)          # nothing depends on the axes
    sk.add_text("A", (0, 0))
    assert solidworks._needs_turning(sk, off) and not solidworks._needs_turning(sk, aligned)
    assert solidworks._needs_turning(sk, NS(hv={"h": "v", "v": "h"}, phi=math.pi / 2))
    sk2 = Sketch(); sk2.add_rectangle((1, 1), (4, 3))     # horizontal / vertical relations
    assert solidworks._needs_turning(sk2, off) and not solidworks._needs_turning(sk2, aligned)
    assert not solidworks._needs_turning(sk2, NS(hv={"h": "v", "v": "h"}, phi=math.pi / 2))
    assert not solidworks._uses_origin(sk2)
    sk2.add_line(0, (3, 0))                                # a line from the origin point
    assert solidworks._uses_origin(sk2)


def test_sketch_text_precheck():
    p = plate()
    s = p.sketch(on=p.face("+z")); s.text("BAUYR 42", at=(2, 2), height=4, bold=True, angle=30)
    assert solidworks.native_export_issues(p.doc) == []
    s.text("A" + chr(10) + "B", at=(2, 10), height=4)
    issues = solidworks.native_export_issues(p.doc)
    assert any("multi-line" in i for i in issues), issues
    q = plate()
    s = q.sketch(on=q.face("+z")); s.rect((1, 1), (19, 19)); s.text("A", at=(5, 5), height=4)
    issues = solidworks.native_export_issues(q.doc)
    assert any("a sketch of its own" in i for i in issues), issues


def test_sketch_relation_and_dimension_precheck():
    """Relations / dimensions SolidWorks can't hold as BauyrCAD does are refused before
    SolidWorks starts (WP04)."""
    from bauyrcad.core.sketch import Constraint
    p = plate()
    s = p.sketch(on=p.face("+z"))
    ln, c = s.line((1, 1), (9, 1)), s.circle((14, 10), 3)
    s.relate("parallel", ln, s.line((1, 5), (9, 7)))
    assert solidworks.native_export_issues(p.doc) == []
    sk = s.sk
    sk.add_constraint("equal", [("e", ln.id), ("e", c.id)])          # an older file
    lone = sk.add_point(30, 30)                                       # used by no entity
    sk.add_constraint("horizontal", [("p", lone), ("p", ln.p1)])
    par = s.line((1, 12), (9, 12))
    sk.constraints.append(Constraint(sk._next_cid, "angle", [("e", ln.id), ("e", par.id)], 0.0))
    sk._next_cid += 1
    sk.add_constraint("vertical", [("e", par.id)])                   # does not hold
    issues = solidworks.native_export_issues(p.doc)
    for text in ("Equal between a line and an arc", "a point no sketch entity uses",
                 "an angle between parallel lines", "Vertical does not hold"):
        assert any(text in i for i in issues), (text, issues)


def test_explicit_plane_origins_and_text_heights_are_known_without_solidworks():
    """Which sketches need a helper point for their origin (D1), and the text height the
    letters' placement check scales with (WP03)."""
    from bauyrcad.core.plane import SketchPlane
    assert not solidworks._origin_elsewhere(SketchPlane((0, 0, 12), (0, 0, 1), (1, 0, 0)))
    assert not solidworks._origin_elsewhere(SketchPlane((3, 3, 3), (1, 1, 1), (1, -1, 0)))
    assert solidworks._origin_elsewhere(SketchPlane((5, 5, 12), (0, 0, 1), (1, 0, 0)))
    t = plate()
    s = t.sketch(on=t.face("+z")); s.text("AB", at=(2, 2), height=4); s.text("C", at=(2, 9), height=6)
    t.cut(s, 0.5)
    by_id = {f.id: f for f in t.doc.features}
    assert solidworks._text_height(t.doc.features[3], by_id) == 6
    assert solidworks._text_height(t.doc.features[1], by_id) == 0


def test_a_failed_process_probe_never_claims_the_users_solidworks(monkeypatch):
    """Session ownership fails closed: when tasklist fails, the instance DispatchEx returned
    may be the user's, so it is never hidden, closed (CloseAllDocuments) or exited."""
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("tasklist", 20)))
    assert solidworks._sw_pids() is None
    assert solidworks._running()                      # unknown counts as running
    own = solidworks._owns_instance
    assert own(None, {7}) == (False, set())           # probe failed before DispatchEx
    assert own({7}, None) == (False, set())           # ... or after it
    assert own({7}, {7}) == (False, set())            # attached to the user's instance
    assert own({7}, {7, 9}) == (True, {9})            # a new one we started
    assert own(set(), {9}) == (True, {9})
    assert own(set(), set()) == (True, set())         # nothing ran before: it can only be ours
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=""))
    assert solidworks._sw_pids() is None


def test_api_sketch_on_a_standard_plane_references_it():
    """Part.sketch('front') stores the Front Plane as its reference (like the GUI), so the
    SolidWorks export puts it on the Front Plane, never on a reference plane lying there."""
    p = Part()
    for name, want in (("front", "Front Plane"), ("xz", "Top Plane"), ("right", "Right Plane")):
        assert p.sketch(name).feature.plane_spec == {"type": "std", "name": want}
    from bauyrcad import solidworks_features as swf
    from bauyrcad.core.plane import FRONT, SketchPlane
    st = swf.State()
    st.planes.append((SketchPlane(FRONT.origin, FRONT.normal, FRONT.xdir), "Plane1"))
    got = swf.helper_plane(None, None, st, SketchPlane((0, 0, 0), (0, 0, -1), (1, 0, 0)), "S")
    assert got == ("std", "Front Plane")              # the standard plane before any other


def test_spline_bounded_volumes_are_measured_exactly():
    """OpenCASCADE's whole-face Gauss integration is ~1e-4 off on a multi-span spline face
    (and the adaptive one several %); split at the knots both are exact."""
    from scipy.interpolate import CubicSpline
    from bauyrcad.core import geometry
    from bauyrcad.core.sketch.model import spline_eval, spline_params
    pts = np.array([(20, 20), (25, 23), (30, 25), (36, 22), (44, 18), (51, 18)], float)
    p = Part()
    s = p.sketch("front"); sp = s.spline([tuple(q) for q in pts])
    low = [tuple(q - (0, 0.3)) for q in np.asarray(spline_eval(pts, 40)[0])[::-1]]
    s.polyline([sp.pts[-1]] + low + [sp.pts[0]]); p.extrude(s, 2)
    cs = CubicSpline(spline_params(pts), pts, axis=0, bc_type="not-a-knot")
    tt = np.linspace(0, 1, 400001)
    xy, d = cs(tt), cs(tt, 1)
    area = np.trapezoid(xy[:, 0] * d[:, 1], tt)                      # Green: x dy
    loop = np.vstack([xy[-1:], low, xy[:1]])
    area += sum(0.5 * (loop[k, 0] + loop[k + 1, 0]) * (loop[k + 1, 1] - loop[k, 1])
                for k in range(len(loop) - 1))
    exact = 2 * abs(area)
    (v, c), (va, ca) = geometry.accurate_mass_properties(p.doc.body)
    assert v == pytest.approx(exact, rel=1e-8) and va == pytest.approx(exact, rel=1e-8)
    assert abs(geometry.volume(p.doc.body) - exact) > 1e-3        # what the plain measure gives
    assert geometry.freeform_area(p.doc.body) == pytest.approx(70, rel=0.1)


def test_accurate_measure_keeps_a_sweep_along_a_closed_spline():
    """Splitting a sweep along a closed periodic spline at its knots gives an invalid shape
    (volume -272056 mm³ for a 392 mm³ ring): the accurate measure then keeps the plain one."""
    from bauyrcad.core import geometry
    p = Part("closed-sweep")
    c = p.curve([(20, 0, 0), (0, 20, 3), (-20, 0, 0), (0, -20, -3)], closed=True)
    p.sweep(c, d=2)
    (v, _), (va, _) = geometry.accurate_mass_properties(p.doc.body)
    assert v == pytest.approx(geometry.volume(p.doc.body), rel=1e-3)
    assert va == pytest.approx(v, rel=1e-3)
