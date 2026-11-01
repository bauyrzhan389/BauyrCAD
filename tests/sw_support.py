"""Shared helpers for the real-SolidWorks export tests (tests/test_solidworks.py and the
per-family tests/test_sw_<family>.py files).

* ``needs_solidworks``: module mark - skip unless SolidWorks is installed and NOT already
  running (a running SolidWorks belongs to the user; set BAUYRCAD_SW_TESTS=1 to force).
* ``sw`` fixture (tests/conftest.py, from :func:`sw_session`): ONE private SolidWorks
  instance for the whole test run, started by the first test that asks for it; the
  documents a test opens are closed when it ends, pass or fail (close_new_documents).
* doc_steps: the BauyrCAD steps a saved file must hold - build expected trees from the
  Document, never from the exporter's own report.features.
* reopen / api_tree / user_api_tree: read the saved file back through the SolidWorks API.
* assert_decoded_tree / assert_decoded_matches_api / assert_decoded_mates: decode the saved
  file offline with bauyrcad.core.sldprt, so a native tree is proven from the file itself,
  not only from what the SolidWorks API reports.
* definition / rebuild_errors / assert_same_mass: definition read-back, rebuild health, and
  volume + centre of mass against BauyrCAD.
* feature / type_of / hidden / extrude_def / revolve_def / doc_units: one feature by name,
  its type (Instant3D 'ICE' resolved), visibility, Extrude / Revolve definitions read back
  (end conditions, depth, angle, reverse...), and the document units.
* check_saved_part: the standard proof of an exported part in one call (decoded tree exact
  with the reported helpers, API match, rebuild health, MMGS units, volume + centroid).
* sketch_def / sketch_summary / parents / sketch_axes: a SolidWorks sketch read back
  (segments by kind and construction state, sketch / Point entity counts, text, relation
  kinds by swConstraintType_e, display dimension values, constrained status), what a
  feature sits on, and BauyrCAD's sketch axes in the saved sketch's frame.
* relation_kinds / sketch_dims / assert_sketch_points: every relation of a saved sketch by
  swConstraintType_e, its display dimensions (kind, value, driven state, text position), and
  its points where BauyrCAD's are (relations and dimensions moved nothing).
* plane_def / axis_def / point_def / curve_points: reference geometry read back (definition
  type, constraints, distance / angle, what it references, and its geometry: plane origin and
  normal, axis line, point position, Curve Through XYZ Points' points).
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.core import geometry, sldprt


def solidworks_usable() -> bool:
    if not solidworks.available():
        return False
    return not solidworks._running() or bool(os.environ.get("BAUYRCAD_SW_TESTS"))


needs_solidworks = pytest.mark.skipif(not solidworks_usable(),
                                      reason="needs SolidWorks installed and not running")


def sw_session():
    """Generator behind the session-scoped ``sw`` fixture of tests/conftest.py."""
    import faulthandler
    was = faulthandler.is_enabled()
    faulthandler.disable()       # COM's handled RPC exceptions look 'fatal' to faulthandler
    try:
        with solidworks.Session() as s:
            yield s
    finally:
        if was:
            faulthandler.enable()


# ---- reading the saved file back through SolidWorks ------------------------------------
def reopen(sw, path):
    """Open a saved .SLDPRT / .SLDASM in the test session (closed when the test ends)."""
    kind = 1 if str(path).upper().endswith(".SLDPRT") else 2
    r = sw.sw.OpenDoc6(str(path), kind, 1, "", 0, 0)
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None, f"SolidWorks could not open {path} ({r})"
    sw._docs.append(doc)
    return doc


def close_new_documents(sw, before):
    """Close the session's documents that are not in ``before`` (the per-test cleanup of
    tests/conftest.py, so one failed test can't leave a same-titled file open for the next)."""
    for doc in [d for d in sw._docs if not any(d is b for b in before)]:
        sw.close(doc)


def doc_steps(doc) -> list:
    """Every BauyrCAD step of a Document, in order: what a saved file must hold. Expected
    trees are built from this, never from the exporter's own report.features (a step the
    exporter left out would then pass unnoticed)."""
    return [f.name for f in doc.features]


def api_tree(sw, doc) -> list:
    """[(name, GetTypeName2, IFeature)] of the top-level FeatureManager items."""
    out = []
    feat = sw.T(doc.FirstFeature(), "IFeature")
    while feat is not None:
        out.append((feat.Name, feat.GetTypeName2(), feat))
        feat = sw.T(feat.GetNextFeature(), "IFeature")
    return out


def user_api_tree(sw, doc) -> list:
    """api_tree without the default folders, planes and origin (everything up to Origin)."""
    rows = api_tree(sw, doc)
    k = next((i for i, (_, t, _) in enumerate(rows) if t == "OriginProfileFeature"), -1)
    return rows[k + 1:]


def suppressed(feat) -> bool:
    """IFeature.IsSuppressed2 in the active configuration."""
    r = feat.IsSuppressed2(1, None)                       # swThisConfiguration
    return bool(r[0] if isinstance(r, (tuple, list)) else r)


def definition(sw, feat, iface: str):
    """The feature's definition object cast to its *FeatureData interface."""
    d = sw.T(feat.GetDefinition(), iface)
    assert d is not None, f"{feat.Name}: no {iface}"
    return d


def rebuild_errors(sw, doc) -> list:
    """Force a full rebuild and return What's Wrong as [(feature name, error code, warning)]."""
    ok, items = solidworks.rebuild_health(sw, doc)
    return items if ok or items else [("<rebuild>", -1, False)]


def feature(sw, doc, name):
    """The top-level IFeature called name (fails the test if there is none)."""
    for n, _, f in api_tree(sw, doc):
        if n == name:
            return f
    raise AssertionError(f"no feature {name!r} in {[n for n, _, _ in api_tree(sw, doc)]}")


def type_of(feat) -> str:
    """GetTypeName2, or GetTypeName for an Instant3D ('ICE') boss / cut."""
    t = feat.GetTypeName2()
    return feat.GetTypeName() if t == "ICE" else t


def hidden(feat) -> bool:
    """A sketch / reference feature hidden in the graphics area (swVisibilityStateHide)."""
    return feat.Visible == 1


def extrude_def(sw, feat) -> dict:
    """IExtrudeFeatureData2 read back: end conditions, depths (mm), flags."""
    d = definition(sw, feat, "IExtrudeFeatureData2")
    return {"end": d.GetEndCondition(True), "end2": d.GetEndCondition(False),
            "depth": d.GetDepth(True) * 1e3, "both": bool(d.BothDirections),
            "reverse": bool(d.ReverseDirection),
            "boss": type_of(feat) in ("Extrusion", "Boss"),     # IsBossFeature is False for a base
            "contours": d.GetContoursCount()}


def revolve_def(sw, feat) -> dict:
    """IRevolveFeatureData2 read back: swRevolveType_e, angle (deg), flags."""
    d = definition(sw, feat, "IRevolveFeatureData2")
    return {"type": d.Type, "angle": math.degrees(d.GetRevolutionAngle(True)),
            "reverse": bool(d.ReverseDirection), "boss": type_of(feat) == "Revolution"}


SEGMENT_KINDS = {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}
# swConstraintType_e of the relations BauyrCAD makes
RELATIONS = {1: "distance", 4: "horizontal", 5: "vertical", 6: "tangent", 7: "parallel",
             8: "perpendicular", 9: "coincident", 10: "concentric", 11: "symmetric",
             12: "midpoint", 14: "equal", 17: "fix", 25: "horizontal points",
             26: "vertical points", 27: "collinear", 28: "coradial", 42: "merged"}


def sketch_def(sw, feat):
    """The ISketch of a sketch feature."""
    sk = sw.T(feat.GetSpecificFeature2(), "ISketch")
    assert sk is not None, f"{feat.Name} is not a sketch"
    return sk


def sketch_summary(sw, feat) -> dict:
    """What a SolidWorks sketch holds: segments by kind ('construction line'...), sketch
    points, Point entities, texts, relation kinds (RELATIONS), display dimensions and
    swConstrainedStatus_e (3 = fully defined)."""
    sk = sketch_def(sw, feat)
    segs = {}
    for x in sk.GetSketchSegments() or ():
        seg = sw.T(x, "ISketchSegment")
        k = ("construction " if seg.ConstructionGeometry else "") + SEGMENT_KINDS.get(seg.GetType(), "?")
        segs[k] = segs.get(k, 0) + 1
    rels = {}
    rm = sw.T(sk.RelationManager, "ISketchRelationManager")
    for r in rm.GetRelations(0) or ():
        k = RELATIONS.get(sw.T(r, "ISketchRelation").GetRelationType(), "other")
        rels[k] = rels.get(k, 0) + 1
    dims, dd = [], feat.GetFirstDisplayDimension()
    while dd is not None:
        d = sw.T(sw.T(dd, "IDisplayDimension").GetDimension2(0), "IDimension")
        # the sketch's own dimensions ('D1@Sketch1'); SolidWorks also lists the dimension of
        # the reference plane the sketch sits on ('D1@Sketch1 Plane')
        if d.FullName.split("@")[1] == feat.Name:
            dims.append(d.SystemValue)
        dd = feat.GetNextDisplayDimension(dd)
    return {"segments": segs, "points": len(sk.GetSketchPoints2() or ()),
            "user_points": sk.GetUserPointsCount(), "texts": len(sk.GetSketchTextSegments() or ()),
            "relations": rels, "dims": dims, "status": sk.GetConstrainedStatus()}


def relation_kinds(sw, feat) -> dict:
    """{swConstraintType_e: count} of every relation of a saved sketch (dimensions included:
    DISTANCE 1, ANGLE 2, RADIUS 3, DIAMETER 15)."""
    rm = sw.T(sketch_def(sw, feat).RelationManager, "ISketchRelationManager")
    out = {}
    for r in rm.GetRelations(0) or ():
        k = int(sw.T(r, "ISketchRelation").GetRelationType())
        out[k] = out.get(k, 0) + 1
    return out


def sketch_dims(sw, feat) -> list:
    """The sketch's own display dimensions read back: [{name, type (IDisplayDimension.Type2),
    value (mm, or radians for angles), driven (IDimension.DrivenState: 1 driven, 2 driving),
    pos (text position, model mm)}]."""
    out, dd = [], feat.GetFirstDisplayDimension()
    while dd is not None:
        ddi = sw.T(dd, "IDisplayDimension")
        d = sw.T(ddi.GetDimension2(0), "IDimension")
        if d.FullName.split("@")[1] == feat.Name:
            t = ddi.Type2
            pos = np.array(sw.T(ddi.GetAnnotation(), "IAnnotation").GetPosition()[:3]) * 1e3
            out.append({"name": d.FullName.split("@")[0], "type": t,
                        "value": d.SystemValue if t == 3 else d.SystemValue * 1e3,
                        "driven": d.DrivenState, "pos": pos})
        dd = feat.GetNextDisplayDimension(dd)
    return out


def assert_sketch_points(sw, feat, sketch_feature, tol=1e-6):
    """Every point of a saved SolidWorks sketch is where BauyrCAD's is, and the other way round
    (mm, compared as sets: points SolidWorks joined count once) - its relations and dimensions
    moved nothing when SolidWorks solved it."""
    sk_sw, sk = sketch_def(sw, feat), sketch_feature.sketch
    xf = sw.T(sk_sw.ModelToSketchTransform, "IMathTransform")

    def to_sketch(w_mm):
        mp = sw.T(sw.math.CreatePoint(solidworks._doubles(np.asarray(w_mm, float) * 1e-3)), "IMathPoint")
        return np.array(sw.T(mp.MultiplyTransform(xf), "IMathPoint").ArrayData)[:2] * 1e3
    ours = [to_sketch(sketch_feature.plane.to_world(*sk.pt(i)))
            for i in {i for e in sk.entities.values() for i in e.point_ids()}]
    theirs = [np.array([q.X, q.Y]) * 1e3
              for q in (sw.T(x, "ISketchPoint") for x in (sk_sw.GetSketchPoints2() or ()))]
    for a, b, what in ((ours, theirs, "BauyrCAD"), (theirs, ours, "SolidWorks")):
        for p in a:
            gap = min((float(np.linalg.norm(p - q)) for q in b), default=math.inf)
            assert gap < tol, f"{feat.Name}: a {what} point at {p} is {gap:.3g} mm from the other's"


def parents(sw, feat) -> list:
    """Names of a feature's parents (IFeature.GetParents): what a sketch sits on."""
    return [sw.T(p, "IFeature").Name for p in (feat.GetParents() or ())]


def sketch_axes(sw, feat, plane) -> tuple:
    """BauyrCAD's u and v axes in the saved SolidWorks sketch's frame (2D, unit)."""
    sk = sketch_def(sw, feat)
    xf = sw.T(sk.ModelToSketchTransform, "IMathTransform")

    def apply(p):
        mp = sw.T(sw.math.CreatePoint(solidworks._doubles(p)), "IMathPoint")
        return np.array(sw.T(mp.MultiplyTransform(xf), "IMathPoint").ArrayData)
    o = apply(np.zeros(3))
    return tuple((apply(np.asarray(d, float)) - o)[:2] for d in (plane.xdir, plane.ydir))


def doc_units(sw, doc) -> dict:
    """Document units: swUnitSystem (5 = MMGS), linear (0 = mm), angular (0 = degrees)."""
    ext = sw.T(doc.Extension, "IModelDocExtension")
    return {k: ext.GetUserPreferenceInteger(v, 0)
            for k, v in (("system", 263), ("linear", 47), ("angular", 51))}


def check_saved_part(sw, rep, shape, steps, helpers=(), rel=1e-6):
    """The standard proof for an exported part: the decoded file holds exactly these steps
    (+ the reported helpers / extra features) natively and in order, equal to what the
    SolidWorks API reports; it rebuilds without What's Wrong items; MMGS units; volume and
    centre of mass match BauyrCAD (shape None: no solid). Returns the reopened document."""
    extra = list(rep.helpers) + [n for feats in rep.steps.values() for n in feats[1:]]
    assert_decoded_tree(rep.path, steps, helpers=list(helpers) + extra, exact=True)
    assert_children_decoded(rep.path)
    doc = reopen(sw, rep.path)
    assert_decoded_matches_api(sw, doc, rep.path)
    assert rebuild_errors(sw, doc) == []
    assert doc_units(sw, doc) == {"system": 5, "linear": 0, "angular": 0}
    if shape is None:
        assert not (sw.T(doc, "IPartDoc").GetBodies2(0, False) or ())
    else:
        assert_same_mass(sw, doc, shape, rel=rel)
    return doc


def assert_same_mass(sw, doc, shape, rel=1e-6):
    """Volume and centre of mass of the SolidWorks document match a BauyrCAD shape (measured
    accurately: B-spline faces split at their knots, geometry.accurate_mass_properties)."""
    (v, c), _ = geometry.accurate_mass_properties(shape)
    assert solidworks._volume(sw, doc) == pytest.approx(v, rel=rel)
    size = max(float(np.linalg.norm(np.subtract(*geometry.bounding_box(shape)))), 1.0)
    assert np.allclose(solidworks._center(sw, doc), c, atol=max(rel, 1e-6) * size * 10)


# ---- decoding the saved file offline ------------------------------------------------------
def assert_decoded_tree(path, expected, helpers=(), exact=False, config=0):
    """The saved file itself holds these steps, in order, as native features (see
    sldprt.tree_mismatches for the forms of ``expected``)."""
    problems = sldprt.tree_mismatches(path, expected, config=config, helpers=helpers, exact=exact)
    assert not problems, "\n".join([f"decoded tree of {path}:"] + problems)


def assert_children_decoded(path, config=0):
    """Every absorbed sketch the saved file declares was identified (SwFeature.unlinked),
    so parents / children and an exact tree check can be trusted."""
    missed = [(f.name, f.unlinked) for f in sldprt.feature_tree(path, config) if f.unlinked]
    assert not missed, f"absorbed children not identified in {path}: {missed}"


def decoded(path, config=0) -> dict:
    """{name: SwFeature} of the decoded user tree."""
    return {f.name: f for f in sldprt.feature_tree(path, config)}


def assert_decoded_matches_api(sw, doc, path, config=0):
    """Every top-level user feature SolidWorks reports is decoded from the file with the same
    name, id, GetTypeName2 and suppression state, in the same order, and the file holds no
    other top-level feature. Returns the decoded tree."""
    tree = [f for f in sldprt.feature_tree(path, config) if not f.internal]
    api = user_api_tree(sw, doc)
    got = [(f.name, f.id, f.type_name) for f in tree]
    want = [(n, feat.GetID(), t) for n, t, feat in api]
    assert got == want, f"decoded {got}\n    API {want}"
    for f, (n, _, feat) in zip(tree, api):
        if f.suppressed is not None:
            assert f.suppressed == suppressed(feat), (n, f.suppressed)
    return tree


def api_mates(sw, doc) -> list:
    """[{name, id, type (GetTypeName2), mate_type, alignment, suppressed}] of the mate folder."""
    out = []
    for _, t, f in api_tree(sw, doc):
        if t != "MateGroup":
            continue
        sub = sw.T(f.GetFirstSubFeature(), "IFeature")
        while sub is not None:
            m = sw.T(sub.GetSpecificFeature2(), "IMate2")
            out.append({"name": sub.Name, "id": sub.GetID(), "type": sub.GetTypeName2(),
                        "mate_type": m.Type, "alignment": m.Alignment,
                        "suppressed": suppressed(sub)})
            sub = sw.T(sub.GetNextSubFeature(), "IFeature")
    return out


def assert_decoded_mates(path, expected=None, sw=None, doc=None):
    """Decoded mates match the SolidWorks API (when sw/doc are given: name, id, type,
    swMateType_e, alignment, suppression) and ``expected`` [{name, type, components,
    distance (mm), angle (deg), suppressed}] where given. Returns the decoded mates."""
    ms = sldprt.mates(path)
    if sw is not None:
        api = api_mates(sw, doc)
        assert [m.name for m in ms] == [a["name"] for a in api]
        for m, a in zip(ms, api):
            assert (m.id, m.type_name, m.type) == (a["id"], a["type"], a["mate_type"]), (m, a)
            if m.suppressed is not None:
                assert m.suppressed == a["suppressed"], (m.name, m.suppressed)
            if m.align is not None and m.align > 0:
                assert m.alignment == a["alignment"], (m.name, m.align, a["alignment"])
    for want in expected or ():
        m = next((x for x in ms if x.name == want["name"]), None)
        assert m is not None, (want["name"], [x.name for x in ms])
        if "type" in want:
            assert sldprt.MATE_TYPES[m.type] == want["type"], (m.name, m.type)
        if "components" in want:                 # SolidWorks may list them in either order
            assert sorted(m.components) == sorted(want["components"]), (m.name, m.components)
        if "distance" in want:
            assert m.distance == pytest.approx(want["distance"] * 1e-3, abs=1e-12), m.name
        if "angle" in want:
            assert m.angle == pytest.approx(math.radians(want["angle"]), abs=1e-12), m.name
        if "suppressed" in want:
            assert m.suppressed is bool(want["suppressed"]), m.name
    return ms


# ---- reference geometry read back ---------------------------------------------------------
def _ref_name(sw, obj):
    """The feature name of a selected reference ('entity' for a face / edge / vertex / point:
    SolidWorks hands some of those back as plain values)."""
    import pythoncom
    if obj is None or isinstance(obj, (tuple, list, float, int)):
        return "entity"
    try:
        raw = obj._oleobj_ if hasattr(obj, "_oleobj_") else obj
        raw.QueryInterface(sw._mod.IFeature.CLSID, pythoncom.IID_IDispatch)
    except Exception:  # noqa: BLE001 - not a feature
        return "entity"
    return sw.T(obj, "IFeature").Name


def plane_def(sw, doc, feat) -> dict:
    """IRefPlaneFeatureData + IRefPlane.Transform: {type (swRefPlaneType_e), constraints
    [swRefPlaneReferenceConstraints_e of each reference], distance (mm), angle (deg), refs
    (names of referenced features, 'entity' otherwise), origin (mm), normal}."""
    d = definition(sw, feat, "IRefPlaneFeatureData")
    xf = sw.T(sw.T(feat.GetSpecificFeature2(), "IRefPlane").Transform, "IMathTransform")
    a = np.array(xf.ArrayData)
    cons = [d.Constraint(i) for i in range(3)]
    d.AccessSelections(doc, None)
    try:
        refs = [_ref_name(sw, d.Reference(i)) for i in range(3) if cons[i]]
    finally:
        d.ReleaseSelectionAccess()
    return {"type": d.Type, "constraints": [c for c in cons if c], "distance": d.Distance * 1e3,
            "angle": math.degrees(d.Angle), "refs": refs, "origin": a[9:12] * 1e3,
            "normal": a[6:9]}


def axis_def(sw, doc, feat) -> dict:
    """IRefAxisFeatureData + IRefAxis.GetRefAxisParams: {type (swRefAxisType_e), refs
    (number of references), point (mm), direction}."""
    d = definition(sw, feat, "IRefAxisFeatureData")
    prm = np.array(sw.T(feat.GetSpecificFeature2(), "IRefAxis").GetRefAxisParams()) * 1e3
    typ = d.Type
    d.AccessSelections(doc, None)
    try:
        r = d.GetSelections()
    finally:
        d.ReleaseSelectionAccess()
    ents = r[0] if isinstance(r, tuple) and len(r) == 2 and isinstance(r[0], (tuple, list)) else r
    return {"type": typ, "refs": len(ents or ()), "point": prm[:3],
            "direction": (prm[3:6] - prm[:3]) / np.linalg.norm(prm[3:6] - prm[:3])}


def point_def(sw, doc, feat) -> dict:
    """IRefPointFeatureData + IRefPoint.GetRefPoint: {type (swRefPointType_e), xyz (mm)}."""
    d = definition(sw, feat, "IRefPointFeatureData")
    rp = sw.T(feat.GetSpecificFeature2(), "IRefPoint")
    return {"type": d.Type, "xyz": np.array(sw.T(rp.GetRefPoint(), "IMathPoint").ArrayData[:3]) * 1e3}


def curve_points(sw, feat):
    """The points (mm, n x 3) of a Curve Through XYZ Points (IFreePointCurveFeatureData)."""
    d = definition(sw, feat, "IFreePointCurveFeatureData")
    return np.array(d.PointArray).reshape(-1, 3) * 1e3


def same_line(p, d, q, e, tol=1e-6) -> bool:
    d, e = np.asarray(d, float) / np.linalg.norm(d), np.asarray(e, float) / np.linalg.norm(e)
    return (np.linalg.norm(np.cross(d, e)) < 1e-9
            and np.linalg.norm(np.cross(np.asarray(q, float) - np.asarray(p, float), d)) < tol)
