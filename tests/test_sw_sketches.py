"""Sketches in real SolidWorks (package WP03): the plane a sketch goes on is what it references
in BauyrCAD (standard plane, reference plane, model face - never a coplanar stand-in or an
extra plane), SolidWorks' sketch coordinate system is moved / turned onto BauyrCAD's where the
origin, Horizontal / Vertical or text depend on it, and every entity and sketch-tool result
arrives as the same SolidWorks entities (arcs on any plane, splines through BauyrCAD's own
points, Point entities, construction curves, shared centres, sketch text, empty and unused
sketches).

Every export is strict (require_native=True), reopened in SolidWorks and decoded offline
(tests/sw_support.py: check_saved_part), and the SolidWorks sketch is read back: segments by
kind, points, relation kinds, dimensions, constrained status, what it sits on."""
import math

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.api import Part
from bauyrcad.core.plane import SketchPlane
from bauyrcad.core.sketch import Constraint, builders, constraints, ops, solver
from sw_support import (check_saved_part, feature, needs_solidworks, parents, sketch_axes,
                        sketch_def, sketch_summary, type_of)

pytestmark = needs_solidworks

KIND = {"sketch": "ProfileFeature", "ref_plane": "RefPlane"}


def steps(part):
    return [(f.name, KIND.get(f.TYPE)) for f in part.doc.features]


def proven(sw, part, tmp_path, helpers=(), notes=(), rel=1e-6):
    """Strict export + the standard proof (decoded tree exact, API match, rebuild health,
    MMGS, volume and centre of mass); returns (report, reopened document)."""
    rep = solidworks.export_part(sw, part.doc, tmp_path / part.name, require_native=True)
    assert rep.native and not rep.warnings, rep.warnings
    assert rep.features == [f.name for f in part.doc.features]
    assert list(rep.helpers) == list(helpers), rep.helpers
    for n in rep.notes:
        assert any(k in n for k in notes), rep.notes
    doc = check_saved_part(sw, rep, part.doc.body, steps(part), rel=rel)
    return rep, doc


def fully_defined(sketch):
    return solver.analyze(sketch.sk).fully_defined


def relate(sketch, kind, *refs):
    """Add a relation the way the GUI's Add Relations does (constraints.make_relation)."""
    for c in constraints.make_relation(sketch.sk, kind, list(refs)):
        ok, msg = solver.try_add(sketch.sk, c)
        assert ok, msg


def hv_dims(sketch, pid):
    """Driving horizontal + vertical distances from the sketch origin to point pid."""
    for kind in ("hdistance", "vdistance"):
        k = Constraint(0, kind, [("p", 0), ("p", pid)])
        v = constraints.measure(sketch.sk, k)
        k.value, k.data["s"] = abs(v), 1.0 if v >= 0 else -1.0
        ok, msg = solver.try_add(sketch.sk, k)
        assert ok, msg


def summary(sw, doc, name):
    return sketch_summary(sw, feature(sw, doc, name))


# ---- SK-PL-03/04/05, XC-05, SK-PL-07: the sketch sits on what it references -------------------
def plane_parts():
    out = []
    a = Part("offset-plate")                 # the face's projected origin lies outside the face
    s = a.sketch("front"); s.rect((20, 20), (60, 50)); a.extrude(s, 10)
    s2 = a.sketch(on=a.face("+z")); s2.circle((40, 35), d=10); a.cut(s2, 4)
    out.append((a, {"Sketch2": ["Boss-Extrude1"]}))
    w = Part("offset-wedge")                 # an inclined face away from the origin
    s = w.sketch("front"); s.polyline([(20, 0), (80, 0), (80, 10), (20, 30)], close=True)
    w.extrude(s, 40)
    face = w.face(normal=(1, 3, 0))
    s2 = w.sketch(on=face); s2.circle(s2.local(face.centroid), d=8); w.cut(s2, 5)
    out.append((w, {"Sketch2": ["Boss-Extrude1"]}))
    c = Part("face-on-front")                # a face lying on the Front Plane
    s = c.sketch("front"); s.rect((0, 0), (40, 30)); c.extrude(s, 10, reverse=True)
    s2 = c.sketch(on=c.face("+z")); s2.circle((20, 15), d=8); c.cut(s2, 3)
    out.append((c, {"Sketch1": ["Front Plane"], "Sketch2": ["Boss-Extrude1"]}))
    d = Part("face-on-plane1")               # a face lying on an earlier reference plane
    s = d.sketch("front"); s.rect((0, 0), (40, 30)); d.extrude(s, 20)
    d.plane("front", offset=20)
    s2 = d.sketch(on=d.face("+z")); s2.circle((20, 15), d=8); d.cut(s2, 3)
    out.append((d, {"Sketch2": ["Boss-Extrude1"]}))
    e = Part("second-plane")                 # two coincident reference planes
    s = e.sketch("front"); s.rect((0, 0), (40, 30)); e.extrude(s, 10)
    e.plane("front", offset=25)
    p2 = e.plane("front", offset=25)
    s2 = e.sketch(p2); s2.circle((20, 15), d=8); e.extrude(s2, 15, reverse=True)
    out.append((e, {"Sketch2": ["Plane2"]}))
    f = Part("mid-plane")                    # a mid plane between two faces
    s = f.sketch("front"); s.rect((0, 0), (40, 30)); f.extrude(s, 20)
    mid = f.plane(mid=(f.face("+z"), f.face("-z")))
    s2 = f.sketch(mid); s2.rect((40, 10), (60, 20)); f.extrude(s2, 6, midplane=True)
    out.append((f, {"Sketch2": ["Plane1"]}))
    return out


def test_sketches_sit_on_what_they_reference(sw, tmp_path):
    for part, want in plane_parts():
        rep, doc = proven(sw, part, tmp_path)        # and no helper plane anywhere
        for sketch, owners in want.items():
            assert parents(sw, feature(sw, doc, sketch)) == owners, (part.name, sketch)
        sw.close(doc)


# ---- XC-04: a face reference that no longer resolves ----------------------------------------
def stale_face_part():
    b = Part("stale-face")
    s = b.sketch("front"); s.rect((0, 0), (20, 20)); b.extrude(s, 5)
    side = b.sketch(on=b.face("+x")); side.circle((-2.5, 10), d=2)
    base = b.doc.features[0].sketch
    base.entities.clear(); base.constraints.clear()
    base.add_circle((10, 10), 8)                  # the plate becomes a disc: its +X face is gone
    b.doc.regenerate()
    assert b.doc.features[2].warning
    return b


def test_a_lost_face_reference_is_refused_strictly_and_reported(sw, tmp_path):
    b = stale_face_part()
    with pytest.raises(solidworks.SolidWorksError, match="Sketch2: Sketch plane"):
        solidworks.export_part(sw, b.doc, tmp_path / "strict", require_native=True)
    rep = solidworks.export_part(sw, b.doc, tmp_path / "loose")
    assert rep.native and rep.helpers == ["Sketch2 Plane"]
    assert any(w.startswith("Sketch2: Sketch plane") and "helper plane" in w
               for w in rep.warnings), rep.warnings
    doc = check_saved_part(sw, rep, b.doc.body, steps(b))
    assert parents(sw, feature(sw, doc, "Sketch2")) == ["Sketch2 Plane"]
    sw.close(doc)


# ---- SK-PL-08 / SK-PL-09 / SK-PL-10: SolidWorks' sketch frame moved / turned onto BauyrCAD's ---
def test_sketch_frame_is_turned_and_moved_where_it_matters(sw, tmp_path):
    p = Part("turned")
    s = p.sketch("front"); s.rect((0, 0), (60, 40)); p.extrude(s, 10)
    # an explicit inclined plane whose u axis is 30 deg off SolidWorks' own sketch X there
    n = np.array([1.0, 0.0, 1.0]) / math.sqrt(2)
    sw_x = np.array([1.0, 0.0, -1.0]) / math.sqrt(2)
    xdir = math.cos(math.radians(30)) * sw_x + math.sin(math.radians(30)) * np.array([0, 1.0, 0])
    pl = SketchPlane((52, 20, 8), n, xdir)
    s2 = p.sketch(pl)
    L = s2.rect((-4, -3), (4, 3))
    s2.dim(L[0]); s2.dim(L[1]); hv_dims(s2, L[0].p1)
    assert fully_defined(s2)
    p.cut(s2, 3)
    # an explicit plane parallel to Front whose origin is NOT the projection of the model
    # origin, with geometry on the sketch origin: SolidWorks' sketch origin is moved there
    o = Part("moved-origin")
    pl2 = SketchPlane((5, 5, 12), (0, 0, 1), (1, 0, 0))
    s3 = o.sketch(pl2)
    R = builders.corner_rectangle(s3.sk, 0, (10, 6))
    s3.dim(R[0]); s3.dim(R[1])
    assert fully_defined(s3)
    o.extrude(s3, 4)
    # a face nearly along X (BauyrCAD's u is -Z on it)
    q = Part("near-x-face")
    s = q.sketch("front"); s.polyline([(0, 0), (40, 0), (45, 30), (0, 30)], close=True)
    q.extrude(s, 20)
    face = q.face(normal=(30, -5, 0))
    s4 = q.sketch(on=face)
    c = np.array(s4.local(face.centroid))
    Q = s4.rect(tuple(c - (3, 2)), tuple(c + (3, 2)))
    s4.dim(Q[0]); s4.dim(Q[1])
    q.cut(s4, 2)

    # BauyrCAD's sketch origin is not the model origin's projection on these explicit planes:
    # dimensions / relations to it go to a hidden helper point there (D1)
    rep, doc = proven(sw, p, tmp_path, helpers=["Sketch2 Points", "Sketch2 Plane Sketch",
                                                "Sketch2 Plane"])
    got = summary(sw, doc, "Sketch2")
    assert (got["relations"].get("horizontal"), got["relations"].get("vertical")) == (2, 2), got
    assert got["status"] == 3, got                       # fully defined, as in BauyrCAD
    assert sorted(round(v * 1e3, 6) for v in got["dims"]) == [3, 4, 6, 8], got
    u, v = sketch_axes(sw, feature(sw, doc, "Sketch2"), pl)
    assert np.allclose(u, [1, 0], atol=1e-9) and np.isclose(abs(v[1]), 1, atol=1e-9), (u, v)
    sw.close(doc)

    rep, doc = proven(sw, o, tmp_path, helpers=["Sketch1 Points", "Sketch1 Plane"])
    got = summary(sw, doc, "Sketch1")
    assert got["status"] == 3 and sorted(round(v * 1e3, 6) for v in got["dims"]) == [6, 10], got
    sk = sketch_def(sw, feature(sw, doc, "Sketch1"))
    xf = sw.T(sk.ModelToSketchTransform, "IMathTransform")
    mp = sw.T(sw.math.CreatePoint(solidworks._doubles(np.array([5, 5, 12]) * 1e-3)), "IMathPoint")
    at = np.array(sw.T(mp.MultiplyTransform(xf), "IMathPoint").ArrayData)
    assert np.allclose(at, 0, atol=1e-12), at              # SolidWorks' sketch origin = ours
    sw.close(doc)

    rep, doc = proven(sw, q, tmp_path)
    got = summary(sw, doc, "Sketch2")
    assert (got["relations"].get("horizontal"), got["relations"].get("vertical")) == (2, 2), got
    assert parents(sw, feature(sw, doc, "Sketch2")) == ["Boss-Extrude1"]
    sw.close(doc)


# ---- SK-PL-11 / SK-EN-04 / SK-BL-07: arcs of every kind, on back faces and mirrored frames -----
def pacman(sketch, c, r, ccw=True):
    """A 270-degree arc closed by two lines through its centre."""
    cx, cy = c
    if ccw:
        arc = sketch.arc((cx, cy), (cx + r, cy), (cx, cy - r))
    else:
        arc = sketch.arc((cx, cy), (cx + r, cy), (cx, cy + r), ccw=False)
    builders.line(sketch.sk, arc.end, arc.center)
    builders.line(sketch.sk, arc.center, arc.start)
    return arc


def arc_parts():
    b = Part("back-face-arc")                 # a sketch on the -Z face (SolidWorks' X is -X)
    s = b.sketch("front"); s.rect((0, 0), (40, 40)); b.extrude(s, 10)
    s2 = b.sketch(on=b.face("-z")); pacman(s2, (15, -20), 8); b.extrude(s2, 3)
    m = Part("mirrored-arc")                  # our normal opposite SolidWorks' (-Z on Front)
    pl = SketchPlane((0, 0, 0), (0, 0, -1), (1, 0, 0))
    s = m.sketch(pl); pacman(s, (10, 10), 8); m.extrude(s, 4)
    s2 = m.sketch(pl); pacman(s2, (10, 10), 4, ccw=False); m.cut(s2, 2)
    t = Part("arc-tools")                     # tangent arc, 3-point arc, slot, sketch fillet
    s = t.sketch("front")
    sk = s.sk
    l1 = s.line((0, 0), (20, 0))
    (ta,) = builders.tangent_arc(sk, l1.p2, (1, 0), (20, 10))
    rel = constraints.make_relation(sk, "tangent", [("e", l1.id), ("e", ta.id)])[0]
    sk.add_constraint(rel.type, rel.refs, **rel.data)        # as the GUI's Tangent Arc tool
    end = ta.end if np.linalg.norm(sk.pt(ta.end) - (20, 10)) < 1e-9 else ta.start
    l2 = builders.line(sk, end, (0, 10))[0]
    builders.three_point_arc(sk, l2.p2, l1.p1, (-3, 5))
    t.extrude(s, 4)
    s2 = t.sketch("front"); s2.slot((15, 5), (35, 5), 8); t.extrude(s2, 4)
    s3 = t.sketch("front"); s3.rect((0, 8), (30, 20)); s3.fillet((30, 20), 4); t.extrude(s3, 4)
    return [b, m, t]


def test_arcs_of_every_kind_on_any_plane(sw, tmp_path):
    for part in arc_parts():
        rep, doc = proven(sw, part, tmp_path)
        for f in part.doc.features:
            if f.TYPE != "sketch":
                continue
            ours = [e for e in f.sketch.entities.values() if not e.construction]
            got = summary(sw, doc, f.name)
            assert got["segments"].get("arc", 0) == sum(e.kind in ("arc", "circle") for e in ours), \
                (part.name, f.name, got)
            assert got["segments"].get("line", 0) == sum(e.kind == "line" for e in ours), \
                (part.name, f.name, got)
        if part.name == "arc-tools":
            s1, s2, s3 = (summary(sw, doc, n) for n in ("Sketch1", "Sketch2", "Sketch3"))
            assert s1["relations"].get("tangent") == 1, s1
            assert s2["relations"].get("tangent") == 4 and s2["relations"].get("equal") == 1, s2
            assert s2["segments"] == {"line": 2, "arc": 2, "construction line": 1}, s2
            assert s3["relations"].get("tangent") == 2, s3
            assert [round(v * 1e3, 9) for v in s3["dims"]] == [4.0], s3      # the fillet radius
        sw.close(doc)


# ---- SK-EN-05 / SK-EN-06 / XC-01 / SK-EN-08 (D8): splines keep BauyrCAD's points --------------
def test_splines_keep_their_points(sw, tmp_path):
    p = Part("splines")
    s = p.sketch("front")
    sp = s.spline([(-10, 20), (0, 30), (15, 25), (30, 35)])
    s.line((30, 35), (30, 45)); s.line((30, 45), (-10, 45)); s.line((-10, 45), (-10, 20))
    guide = s.line((-15, 30), (35, 30), construction=True)
    relate(s, "coincident", ("p", sp.pts[1]), ("e", guide.id))   # an interior spline point
    s.sk.add_spline([(40, 30), (50, 40), (60, 30)], construction=True)
    p.extrude(s, 4)
    s2 = p.sketch("front")                                    # 3 points: natural ends
    s2.spline([(-10, 0), (10, 8), (30, 5)]); s2.line((30, 5), (30, 22)); s2.line((30, 22), (-10, 22))
    s2.line((-10, 22), (-10, 0))
    p.extrude(s2, 3)
    s3 = p.sketch("front")                                    # 6 uneven points
    s3.spline([(20, 20), (25, 23), (30, 25), (36, 22), (44, 18), (51, 18)])  # stays in x 20..51
    s3.line((51, 18), (51, 40)); s3.line((51, 40), (20, 40)); s3.line((20, 40), (20, 20))
    p.extrude(s3, 2)
    # the profile lines only touch the spline ends (separate BauyrCAD points): SolidWorks
    # joins them, which is a note
    rep, doc = proven(sw, p, tmp_path, notes=("joined", "spline through the same points"),
                      rel=solidworks._SPLINE)
    # SolidWorks' chord-length not-a-knot spline through the 6 uneven points is not bit-exact
    # (D8: a note); the 3- and 4-point ones are BauyrCAD's curve exactly
    spline_notes = [n for n in rep.notes if "spline" in n]
    assert all(n.startswith("Boss-Extrude3") for n in spline_notes), rep.notes
    for name, n in (("Sketch1", 4), ("Sketch2", 3), ("Sketch3", 6)):
        f = feature(sw, doc, name)
        assert summary(sw, doc, name)["segments"].get("spline") == 1, name
        splines = [sw.T(x, "ISketchSpline") for x in sketch_def(sw, f).GetSketchSegments()
                   if sw.T(x, "ISketchSegment").GetType() == 3
                   and not sw.T(x, "ISketchSegment").ConstructionGeometry]
        assert [x.GetPointCount() for x in splines] == [n], name
    s1 = summary(sw, doc, "Sketch1")
    assert s1["segments"].get("construction spline") == 1, s1
    assert s1["relations"].get("coincident", 0) >= 1, s1
    sw.close(doc)


# ---- SK-EN-07 / SK-EN-10 / SK-BL-02/04/05/06 / SK-OP-03: points, construction, shared centres ---
def builder_part():
    p = Part("builders")
    s = p.sketch("front")
    L = s.center_rect((0, 0), 60, 40)                      # + construction diagonals + centre point
    centre = next(e for e in s.sk.entities.values() if e.kind == "point")
    relate(s, "coincident", ("p", centre.p), ("p", 0))
    s.dim(L[0]); s.dim(L[1])
    assert fully_defined(s)
    p.extrude(s, 10)
    s2 = p.sketch(on=p.face("+z")); s2.polygon((-18, 0), 6, sides=6)     # construction circle
    p.cut(s2, 2)
    s3 = p.sketch(on=p.face("+z"))
    builders.center_slot(s3.sk, (10, 8), (20, 8), (10, 11))               # centre point, midpoint
    p.cut(s3, 1)
    s4 = p.sketch(on=p.face("+z"))                         # a straight slot and its offset: the
    ring = s4.slot((5, -10), (20, -10), 4)                 # offset arcs share the slot's centres
    s4.offset([e for e in ring if not e.construction], 2)
    p.cut(s4, 1.5, contours=[(12.5, -7)])
    s5 = p.sketch(on=p.face("+z"))                         # Point entities, a fixed one, a
    pt = s5.point((-25, 15))                               # midpoint line (a reference sketch)
    relate(s5, "fix", ("e", pt.id))
    builders.midpoint_line(s5.sk, (-15, 15), (-10, 15))
    s5.sk.add_arc((-20, -15), (-17, -15), (-20, -12), construction=True)
    return p


def test_points_construction_and_shared_centres(sw, tmp_path):
    p = builder_part()
    rep, doc = proven(sw, p, tmp_path)
    s1 = summary(sw, doc, "Sketch1")
    assert s1["segments"] == {"line": 4, "construction line": 2}, s1
    assert s1["user_points"] == 1 and s1["status"] == 3, s1          # fully defined like ours
    assert s1["relations"].get("midpoint") == 2, s1
    s2 = summary(sw, doc, "Sketch2")
    assert s2["segments"] == {"line": 6, "construction arc": 1}, s2
    assert s2["relations"].get("equal") == 5 and s2["relations"].get("coincident", 0) >= 6, s2
    s3 = summary(sw, doc, "Sketch3")
    assert s3["segments"] == {"line": 2, "arc": 2, "construction line": 1}, s3
    assert s3["user_points"] == 1 and s3["relations"].get("midpoint") == 1, s3
    assert s3["points"] == 7, s3                       # arc centres ARE the axis ends (merged)
    s4 = summary(sw, doc, "Sketch4")
    assert s4["segments"] == {"line": 4, "arc": 4, "construction line": 1}, s4
    assert s4["points"] == 10, s4                      # 2 shared centres + 4 + 4 ends
    assert s4["relations"].get("parallel") == 2, s4
    s5 = summary(sw, doc, "Sketch5")
    assert s5["segments"] == {"line": 1, "construction arc": 1}, s5
    assert s5["user_points"] == 2, s5
    assert s5["relations"].get("fix") == 1 and s5["relations"].get("midpoint") == 1, s5
    assert type_of(feature(sw, doc, "Sketch5")) == "ProfileFeature"
    sw.close(doc)


# ---- SK-BL-03 / SK-OP-01/02/04/05/06: sketch tool results ----------------------------------------
def tool_part():
    p = Part("tools")                    # the profiles overlap, so the part stays one body
    s = p.sketch("front")
    builders.three_point_rectangle(s.sk, (0, 0), (20, 10), (15, 25))
    p.extrude(s, 5)
    s2 = p.sketch("front"); builders.parallelogram(s2.sk, (10, 0), (30, 0), (35, 15))
    p.extrude(s2, 5)
    s3 = p.sketch("front"); s3.rect((0, 20), (30, 40)); s3.fillet((30, 40), 4); s3.chamfer((0, 20), 3)
    p.extrude(s3, 5)
    s4 = p.sketch("front")                                    # mirror about a centreline
    axis = s4.centerline((15, 35), (15, 70))
    half = s4.polyline([(15, 38), (30, 38), (30, 48), (22, 58), (15, 58)])
    s4.mirror(half, axis)
    p.extrude(s4, 5)
    s5 = p.sketch("front")                                    # trim and extend
    R = s5.rect((25, 0), (65, 20))
    v1 = s5.line((40, -10), (40, 15))
    v2 = s5.line((50, -10), (50, 20))
    s5.trim(R[0], (45, 0))                                    # bottom edge between the lines
    s5.trim(v1, (40, -5))
    s5.trim(v2, (50, -5))
    ops.extend(s5.sk, v1.id, (40, 15))                        # up to the top edge
    p.extrude(s5, 5, contours=[(30, 10)])
    return p


def test_sketch_tool_results(sw, tmp_path):
    p = tool_part()
    rep, doc = proven(sw, p, tmp_path, notes=("joined",))
    s1 = summary(sw, doc, "Sketch1")
    assert s1["relations"].get("perpendicular") == 1 and s1["relations"].get("parallel") == 2, s1
    assert summary(sw, doc, "Sketch2")["relations"].get("parallel") == 2
    s3 = summary(sw, doc, "Sketch3")
    assert s3["segments"] == {"line": 5, "arc": 1}, s3
    assert s3["relations"].get("tangent") == 2 and [round(v * 1e3, 9) for v in s3["dims"]] == [4.0], s3
    assert summary(sw, doc, "Sketch4")["relations"].get("symmetric") == 3
    assert summary(sw, doc, "Sketch5")["relations"].get("collinear") == 1
    sw.close(doc)
    # Convert Entities: BauyrCAD stores fixed curves (no edge link), so SolidWorks gets them fixed
    c = Part("converted")
    s = c.sketch("front"); s.rect((0, 0), (40, 30)); s.circle((20, 15), d=10); c.extrude(s, 10)
    s2 = c.sketch(on=c.face("+z")); s2.convert(face=c.face("+z")); s2.circle((20, 15), d=20)
    c.cut(s2, 2, contours=[(27.5, 15)])
    rep, doc = proven(sw, c, tmp_path, notes=("joined",))
    got = summary(sw, doc, "Sketch2")
    assert got["segments"] == {"line": 4, "arc": 2}, got
    fixes = sum(k.type == "fix" for k in s2.sk.constraints)    # (a redundant fix is left out)
    assert got["relations"] == {"fix": fixes, "coincident": 1}, got    # + the corner on the origin
    sw.close(doc)


# ---- SK-EN-09 (D7): sketch text ------------------------------------------------------------------
def text_part():
    t = Part("engraved")
    s = t.sketch("front"); s.rect((0, 0), (90, 40)); t.extrude(s, 5)
    s2 = t.sketch(on=t.face("+z")); s2.text("BAUYR 42", at=(5, 25), height=10); t.cut(s2, 0.5)
    s3 = t.sketch(on=t.face("+z")); s3.text("CAD", at=(55, 5), height=6, bold=True, angle=30)
    t.cut(s3, 0.3)
    s4 = t.sketch(on=t.face("-z"))                            # SolidWorks' X is -X there
    s4.text("Ag", at=(10, -30), height=8, font="Times New Roman", italic=True)
    t.extrude(s4, 0.4)
    # our normal opposite SolidWorks' (-Z on the Front Plane): the text frame is mirrored
    s5 = t.sketch(SketchPlane((0, 0, 0), (0, 0, -1), (1, 0, 0)))
    s5.text("MIR", at=(40, -20), height=6)
    t.extrude(s5, 0.3)
    return t


def test_sketch_text_is_editable_text(sw, tmp_path):
    t = text_part()
    rep, doc = proven(sw, t, tmp_path, notes=("font outlines", "construction line"), rel=1e-3)
    assert any("CAD" in n and "construction line" in n for n in rep.notes), rep.notes
    assert any("MIR" in n and "construction line" in n for n in rep.notes), rep.notes
    for name, want in (("Sketch2", ("BAUYR 42", "Arial", False, False, 10)),
                       ("Sketch3", ("CAD", "Arial", True, False, 6)),
                       ("Sketch4", ("Ag", "Times New Roman", False, True, 8)),
                       ("Sketch5", ("MIR", "Arial", False, False, 6))):
        f = feature(sw, doc, name)
        texts = [sw.T(x, "ISketchText") for x in sketch_def(sw, f).GetSketchTextSegments()]
        assert len(texts) == 1, name
        fmt = sw.T(texts[0].GetTextFormat(), "ITextFormat")
        got = (texts[0].Text, fmt.TypeFaceName, bool(fmt.Bold), bool(fmt.Italic),
               round(fmt.CharHeight * 1e3, 9))
        assert got == want, (name, got)
    assert summary(sw, doc, "Sketch3")["segments"] == {"construction line": 1, "text": 1}
    sw.close(doc)


# ---- SK-ST-03: empty and unused sketches ---------------------------------------------------------
def test_empty_and_unused_sketches_keep_their_names(sw, tmp_path):
    p = Part("idle-sketches")
    s = p.sketch("front"); s.rect((0, 0), (30, 20)); p.extrude(s, 5)
    p.sketch("top")                                           # empty
    s3 = p.sketch(on=p.face("+z")); s3.polyline([(2, 2), (10, 8), (20, 4)])   # open, unused
    p.sketch("right")                                         # empty, last
    rep, doc = proven(sw, p, tmp_path)
    for name, segs in (("Sketch2", {}), ("Sketch3", {"line": 2}), ("Sketch4", {})):
        f = feature(sw, doc, name)
        assert type_of(f) == "ProfileFeature", name
        assert summary(sw, doc, name)["segments"] == segs, name
    assert parents(sw, feature(sw, doc, "Sketch2")) == ["Top Plane"]
    assert parents(sw, feature(sw, doc, "Sketch3")) == ["Boss-Extrude1"]
    sw.close(doc)
