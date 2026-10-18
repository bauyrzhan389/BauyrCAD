"""Exporter infrastructure shared by every feature family, checked in real SolidWorks: derived
and validated extrude / revolve directions, hidden named helper geometry, keep_selection,
multi-feature steps, strict notes vs losses, body-less parts, rebuild health, MMGS units and
SolidWorks preferences. Each export is reopened in SolidWorks AND decoded offline
(tests/sw_support.py: check_saved_part)."""
import math

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad import solidworks_features as swf
from bauyrcad.api import Part
from bauyrcad.core import geometry
from bauyrcad.core.plane import SketchPlane
from sw_support import (check_saved_part, decoded, extrude_def, feature, hidden, needs_solidworks,
                        revolve_def, type_of)

pytestmark = needs_solidworks


def export(sw, part, tmp_path, name=None):
    rep = solidworks.export_part(sw, part.doc, tmp_path / (name or part.name), require_native=True)
    assert rep.native and not rep.warnings, rep.warnings
    assert rep.features == [f.name for f in part.doc.features]
    return rep


# ---- VAL-01: extrude / revolve go through the volume + first-moment validation, with the
# direction derived (attempt 0), so a mirrored result can never be kept ----------------------
def direction_parts():
    out = {}
    p = Part("boss-reverse")                       # base boss to -Z: same volume either way
    s = p.sketch("front"); s.rect((0, 0), (30, 20)); p.extrude(s, 5, reverse=True)
    out[p.name] = (p, {"Boss-Extrude1": dict(end=0, depth=5, reverse=True, boss=True)}, {})
    for rev in (False, True):                      # base revolve 90 deg, both senses
        q = Part(f"revolve90-{rev}")
        s = q.sketch("front"); s.centerline((0, 0), (0, 10)); s.rect((10, 0), (20, 30))
        q.revolve(s, 90, reverse=rev)
        out[q.name] = (q, {}, {"Revolve1": dict(type=0, angle=90, reverse=rev, boss=True)})
    r = Part("shaft-cut90")                        # cut-revolve on an axisymmetric body
    s = r.sketch("front"); s.centerline((0, 0), (0, 10))
    s.polyline([(0, 0), (12, 0), (12, 50), (0, 50)], close=True); r.revolve(s)
    s2 = r.sketch("front"); s2.centerline((0, 0), (0, 10)); s2.rect((8, 20), (13, 30))
    r.revolve_cut(s2, 90, reverse=True)
    out[r.name] = (r, {}, {"Revolve1": dict(type=0, angle=360, boss=True),
                           "Cut-Revolve1": dict(type=0, angle=90, reverse=True, boss=False)})
    # a sketch plane whose normal is opposite SolidWorks' sketch normal (-Z on the Front
    # Plane), with an auto-flipped cut. SolidWorks' Reverse Direction = BauyrCAD's reverse
    # XOR the auto-flip XOR (the sketch normals are opposite)
    m = Part("flipped-normal")
    pl = SketchPlane((0, 0, 0), (0, 0, -1), (1, 0, 0))
    s = m.sketch(pl); s.rect((0, 0), (30, 20)); m.extrude(s, 6)
    s2 = m.sketch(pl); s2.circle((8, 8), d=5); m.cut(s2, 3)
    s3 = m.sketch(pl); s3.circle((20, 8), d=5); m.cut(s3, 2, reverse=True)
    assert [f.flipped for f in m.doc.features if f.TYPE == "extrude"] == [False, True, False]
    out[m.name] = (m, {"Boss-Extrude1": dict(end=0, depth=6, reverse=True, boss=True),
                       "Cut-Extrude1": dict(end=0, depth=3, reverse=False, boss=False),
                       "Cut-Extrude2": dict(end=0, depth=2, reverse=False, boss=False)}, {})
    return out


def test_extrude_and_revolve_directions_are_derived_and_validated(sw, tmp_path):
    for name, (part, extrudes, revolves) in direction_parts().items():
        rep = export(sw, part, tmp_path)
        ops = [f.name for f in part.doc.features if f.TYPE in ("extrude", "revolve")]
        # the derived definition was right first time for every operation
        assert {k: rep.attempts[k] for k in ops} == {k: 0 for k in ops}, (name, rep.attempts)
        steps = [(f.name, {"sketch": "ProfileFeature"}.get(f.TYPE)) for f in part.doc.features]
        doc = check_saved_part(sw, rep, part.doc.body, steps)
        for op, want in extrudes.items():
            got = extrude_def(sw, feature(sw, doc, op))
            assert {k: got[k] for k in want} == pytest.approx(want), (name, op, got)
        for op, want in revolves.items():
            got = revolve_def(sw, feature(sw, doc, op))
            assert {k: got[k] for k in want} == pytest.approx(want), (name, op, got)
        sw.close(doc)


def test_a_mirrored_result_is_rejected_by_the_first_moment(sw, tmp_path, monkeypatch):
    """Force the handler to try the WRONG direction first: the validator must reject it
    (same volume, material on the other side) and keep the right one."""
    real = swf.HANDLERS["extrude"]

    def wrong_first(s, doc, f, ctx, st, attempt):
        if attempt > 1:
            return swf.NO_MORE
        swapped = 1 - attempt
        return real(s, doc, f, ctx, st, swapped)
    monkeypatch.setitem(swf.HANDLERS, "extrude", wrong_first)
    p = Part("mirror-probe")
    s = p.sketch("front"); s.rect((0, 0), (30, 20)); p.extrude(s, 5, reverse=True)
    rep = export(sw, p, tmp_path)
    assert rep.attempts["Boss-Extrude1"] == 1
    doc = check_saved_part(sw, rep, p.doc.body, ["Sketch1", "Boss-Extrude1"])
    assert extrude_def(sw, feature(sw, doc, "Boss-Extrude1"))["reverse"] is True
    sw.close(doc)


# ---- D1 / CON-13: helper reference geometry is named after its feature, hidden and listed;
# BauyrCAD's own Plane1 keeps its name ---------------------------------------------------------
def helper_part():
    p = Part("helpers")
    s = p.sketch(SketchPlane((0, 0, 12), (0, 0, 1), (1, 0, 0)))     # no BauyrCAD plane for it
    s.rect((0, 0), (40, 30)); p.extrude(s, 5)                       # body z = 12..17
    n = np.array([1.0, 0.0, 1.0]) / math.sqrt(2)                     # an inclined explicit plane
    s2 = p.sketch(SketchPlane((36, 15, 17), n, (0, 1, 0))); s2.circle((0, 0), d=4); p.cut(s2, 3)
    pl = p.plane("front", offset=17)                                 # BauyrCAD's Plane1
    s3 = p.sketch(pl); s3.circle((10, 10), d=6); boss = p.extrude(s3, 4)
    p.pattern_linear(boss, direction=(1, 1, 0), spacing=8, count=2)  # an arbitrary direction
    s4 = p.sketch(pl); s4.circle((30, 6), d=3); boss2 = p.extrude(s4, 2)
    p.pattern_linear(boss2, direction="-x", spacing=6, count=2, direction2="y", spacing2=5,
                     count2=2)                                       # two std axes
    return p


def test_helper_geometry_is_named_hidden_and_reported(sw, tmp_path):
    p = helper_part()
    rep = export(sw, p, tmp_path)
    assert rep.helpers == ["Sketch1 Plane", "Sketch2 Plane Sketch", "Sketch2 Plane",
                           "LPattern1 Axis Sketch", "LPattern1 Axis", "LPattern2 Axis",
                           "LPattern2 Axis-2"], rep.helpers
    steps = [(f.name, {"sketch": "ProfileFeature", "ref_plane": "RefPlane",
                       "linear_pattern": "LPattern"}.get(f.TYPE)) for f in p.doc.features]
    doc = check_saved_part(sw, rep, p.doc.body, steps)
    kinds = {"Sketch1 Plane": "RefPlane", "Sketch2 Plane Sketch": "3DProfileFeature",
             "Sketch2 Plane": "RefPlane", "LPattern1 Axis Sketch": "3DProfileFeature",
             "LPattern1 Axis": "RefAxis", "LPattern2 Axis": "RefAxis", "LPattern2 Axis-2": "RefAxis"}
    for name, kind in kinds.items():
        f = feature(sw, doc, name)
        assert (type_of(f), hidden(f)) == (kind, True), name
    assert type_of(feature(sw, doc, "Plane1")) == "RefPlane"
    # the decoded file agrees: helpers are hidden reference geometry where it says so
    t = decoded(rep.path)
    assert all(t[n].hidden in (True, None) for n in kinds)
    # the helper plane really is the explicit plane (sketch on it, extruded exactly)
    assert rep.attempts["Cut-Extrude1"] == 0 and rep.attempts["Boss-Extrude1"] == 0
    sw.close(doc)


def test_helpers_keep_an_in_progress_selection(sw):
    """keep_selection: a helper made while a handler is building a multi-selection (with
    marks) leaves that selection as it was."""
    doc = sw.new("part")
    try:
        rep = solidworks.PartReport("x")
        st = swf.State(rep)
        ext = sw.T(doc.Extension, "IModelDocExtension")
        doc.ClearSelection2(True)
        assert ext.SelectByID2("Front Plane", "PLANE", 0, 0, 0, False, 1, None, 0)
        assert ext.SelectByID2("Right Plane", "PLANE", 0, 0, 0, True, 2, None, 0)
        sm = sw.T(doc.SelectionManager, "ISelectionMgr")

        def selection():
            return [(sw.T(sm.GetSelectedObject6(i, -1), "IFeature").Name, sm.GetSelectedObjectMark(i))
                    for i in range(1, sm.GetSelectedObjectCount2(-1) + 1)]
        want = [("Front Plane", 1), ("Right Plane", 2)]
        assert selection() == want
        ax = swf.line_axis(sw, doc, st, (5, 0, 0), (1, 2, 3), "Probe")
        assert selection() == want
        pts_sketch, pts = swf.helper_points(sw, doc, st, [(1, 2, 3), (4, 5, 6)], "Probe")
        assert selection() == want
        pl = swf.helper_plane(sw, doc, st, SketchPlane((1, 2, 3), (1, 1, 1), (1, -1, 0)), "Probe")
        assert selection() == want
        ax2 = swf.std_axis(sw, doc, st, "z", "Probe")
        assert selection() == want
        assert rep.helpers == ["Probe Axis Sketch", "Probe Axis", "Probe Points",
                               "Probe Plane Sketch", "Probe Plane", "Probe Axis-2"]
        assert not rep.warnings
        # each helper is hidden and where it should be
        for f in (ax, pts_sketch, pl, ax2):
            assert hidden(f)
        prm = np.array(sw.T(ax.GetSpecificFeature2(), "IRefAxis").GetRefAxisParams()) * 1e3
        d = np.array([1, 2, 3]) / math.sqrt(14)
        for q in (prm[:3], prm[3:6]):
            assert np.linalg.norm(np.cross(q - [5, 0, 0], d)) < 1e-6
        assert [(round(x.X * 1e3, 6), round(x.Y * 1e3, 6), round(x.Z * 1e3, 6)) for x in pts] == \
            [(1, 2, 3), (4, 5, 6)]
        # made once: the same requests reuse them
        assert swf.line_axis(sw, doc, st, (5, 0, 0), (-1, -2, -3), "Other") is ax
        assert swf.std_axis(sw, doc, st, "z", "Other") is ax2
    finally:
        sw.close(doc)


# ---- D6 / DOC-2 and CON-13: a step SolidWorks needs several features for, and features
# SolidWorks names itself, never take a later BauyrCAD step's name ----------------------------
def test_multi_feature_step_and_solidworks_own_names(sw, tmp_path):
    p = Part("holes")
    s = p.sketch("front"); s.rect((0, 0), (60, 40)); p.extrude(s, 10)
    p.hole(p.face("+z"), [(15, 20), (45, 20)], d=6, depth=5)       # 2 Hole Wizard features
    s2 = p.sketch(on=p.face("+z")); s2.rect((25, 5), (35, 12)); p.cut(s2, 3)
    rep = export(sw, p, tmp_path)
    assert rep.steps == {"Hole1": ["Hole1", "Hole1-2"]}
    # Hole Wizard's own sketches were named after the hole, so 'Sketch2' is BauyrCAD's
    doc = check_saved_part(sw, rep, p.doc.body,
                           [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion"),
                            ("Hole1", "HoleWzd"), ("Sketch2", "ProfileFeature"), "Cut-Extrude1"])
    assert type_of(feature(sw, doc, "Hole1-2")) == "HoleWzd"
    t = decoded(rep.path)
    inner = sorted(f.name for f in t.values() if f.parent in (t["Hole1"].id, t["Hole1-2"].id))
    assert inner and all(n.startswith("Hole1 ") for n in inner), inner
    sw.close(doc)


# ---- XC-02 / D3: blends and helices export strictly; their small shape differences are notes
def test_blends_export_strictly_with_notes(sw, tmp_path):
    lo = Part("loft")
    a = lo.sketch("top"); a.rect((-20, -20), (20, 20))
    b = lo.sketch(lo.plane("top", offset=30)); b.circle((0, 0), d=20)
    lo.loft([a, b])
    path = lo.sketch("front"); path.polyline([(-15, 30), (-15, 45), (15, 45)])
    lo.sweep(path, d=6)
    sp = Part("spring")
    sp.sweep(sp.helix(center=(0, 0, 0), axis="y", d=30, pitch=8, turns=4), d=3)
    for part, kinds, rel in ((lo, {"Plane1": "RefPlane", "Loft1": "Blend", "Sweep1": "Sweep"}, 1e-2),
                             (sp, {"Helix/Spiral1": "Helix", "Sweep1": "Sweep"}, 1e-3)):
        rep = export(sw, part, tmp_path)
        assert all("differs slightly" in n for n in rep.notes), rep.notes
        steps = [(f.name, kinds.get(f.name, {"sketch": "ProfileFeature"}.get(f.TYPE)))
                 for f in part.doc.features]
        doc = check_saved_part(sw, rep, part.doc.body, steps, rel=rel)
        if part is sp:
            assert rep.helpers == ["Helix/Spiral1 Sketch"]
            h = sw.T(feature(sw, doc, "Helix/Spiral1").GetDefinition(), "IHelixFeatureData")
            assert (h.Pitch * 1e3, h.Revolution) == pytest.approx((8, 4))
        sw.close(doc)
    assert any("Loft1" in n for n in solidworks.export_part(
        sw, lo.doc, tmp_path / "loft2", require_native=True).notes)


# ---- XC-03: a part without a solid (a layout / skeleton) exports natively -------------------
def test_bodyless_part_exports_natively(sw, tmp_path):
    p = Part("layout")
    s = p.sketch("front"); s.rect((0, 0), (40, 20))
    p.plane("top", offset=5)
    p.helix(center=(0, 0, 0), axis="y", d=20, pitch=5, turns=3)
    assert p.doc.body is None
    rep = export(sw, p, tmp_path)
    assert rep.volume == 0
    doc = check_saved_part(sw, rep, None, [("Sketch1", "ProfileFeature"), ("Plane1", "RefPlane"),
                                           ("Helix/Spiral1", "Helix")])
    sw.close(doc)


# ---- XC-06: rebuild health is read from SolidWorks ------------------------------------------
def test_rebuild_health_finds_feature_errors(sw, tmp_path):
    p = Part("filleted")
    s = p.sketch("front"); s.rect((0, 0), (30, 20)); p.extrude(s, 10)
    p.fillet(p.edges_where(direction="z")[:1], r=2)
    rep = export(sw, p, tmp_path)
    doc = check_saved_part(sw, rep, p.doc.body, ["Sketch1", "Boss-Extrude1", ("Fillet1", "Fillet")])
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    # over-define the profile in SolidWorks: a horizontal line made vertical as well
    sketch = feature(sw, doc, "Sketch1")
    doc.ClearSelection2(True); sketch.Select2(False, 0); doc.EditSketch()
    for seg in sw.T(doc.GetActiveSketch2(), "ISketch").GetSketchSegments():
        line = sw.T(seg, "ISketchLine")
        a, b = (sw.T(x, "ISketchPoint") for x in (line.GetStartPoint2(), line.GetEndPoint2()))
        if abs(a.Y - b.Y) < 1e-9:
            doc.ClearSelection2(True); sw.T(seg, "ISketchSegment").Select4(False, None)
            doc.SketchAddConstraints("sgVERTICAL2D")
            break
    sw.T(doc.SketchManager, "ISketchManager").InsertSketch(True)
    ok, items = solidworks.rebuild_health(sw, doc)
    assert ("Sketch1", 51, True) in items, items                  # What's Wrong: sketch error
    r = solidworks.PartReport("x")
    solidworks._check_health(sw, doc, r)
    assert "Sketch1: SolidWorks rebuild warning (sketch error)" in r.warnings, r.warnings
    sw.close(doc)


# ---- DOC-7 / Session: MMGS documents and preferences put back -------------------------------
def test_new_documents_are_mmgs_and_preferences_are_restored(sw):
    from sw_support import doc_units
    for kind in ("part", "assembly"):
        doc = sw.new(kind)
        try:
            assert doc_units(sw, doc) == {"system": 5, "linear": 0, "angular": 0}, kind
            ext = sw.T(doc.Extension, "IModelDocExtension")
            assert (ext.GetUserPreferenceInteger(49, 0), ext.GetUserPreferenceInteger(52, 0)) == (2, 2)
        finally:
            sw.close(doc)
    app = sw.sw
    for k, v in solidworks._PREF_TOGGLES.items():                # set while the export runs
        assert bool(app.GetUserPreferenceToggle(k)) is v, k
    app.SetUserPreferenceToggle(18, True)                         # as if the user had it on
    try:
        with solidworks.Session() as inner:                       # attaches to this SolidWorks
            assert not inner._own
            assert bool(inner.sw.GetUserPreferenceToggle(18)) is False
            assert inner._prefs == {18: True}
        assert bool(app.GetUserPreferenceToggle(18)) is True       # put back
    finally:
        app.SetUserPreferenceToggle(18, False)
