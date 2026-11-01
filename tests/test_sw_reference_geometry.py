"""Reference geometry in real SolidWorks (package WP05): every BauyrCAD reference plane, axis,
point and curve becomes the native SolidWorks reference feature of the same kind and name
(Reference Plane, Reference Axis, Reference Point, Curve Through XYZ Points / 3D sketch),
with BauyrCAD's references and constraints, and later features select it by its BauyrCAD
feature id (a sketch on a plane, a pattern about an axis, a plane through reference points,
a sweep along a curve).

A reference feature has no volume, so each one is read back from the saved file: the
definition (swRefPlaneType_e and constraints, distance / angle, references; swRefAxisType_e;
swRefPointType_e; the curve's points) and the geometry (plane origin and normal, axis line,
point position) against BauyrCAD's. Each plane also carries a sketch + cut / boss, so the
volume and centre of mass prove its place too. Every export is strict (require_native=True),
reopened, rebuilt without What's Wrong items and decoded offline
(sw_support.check_saved_part)."""
import math

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.api import Part
from bauyrcad.core.plane import SketchPlane
from sw_support import (assert_sketch_points, axis_def, check_saved_part, curve_points, feature, needs_solidworks,
                        plane_def, point_def, same_line, sketch_def)

pytestmark = needs_solidworks

KIND = {"sketch": "ProfileFeature", "ref_plane": "RefPlane", "ref_axis": "RefAxis",
        "ref_point": "RefPoint"}
FLIP_NOTE = "normal the other way"


def steps(part):
    out = []
    for f in part.doc.features:
        if f.TYPE == "curve":
            out.append((f.name, "3DProfileFeature" if f.polyline else "CurveInFile"))
        else:
            out.append((f.name, KIND.get(f.TYPE)))
    return out


def proven(sw, part, tmp_path, helpers=(), notes=(), rel=1e-6):
    """Strict export + the standard proof; returns (report, reopened document)."""
    rep = solidworks.export_part(sw, part.doc, tmp_path / part.name, require_native=True)
    assert rep.native and not rep.warnings, rep.warnings
    assert rep.features == [f.name for f in part.doc.features]
    assert sorted(rep.helpers) == sorted(helpers), rep.helpers
    for n in rep.notes:
        assert any(k in n for k in notes), rep.notes
    doc = check_saved_part(sw, rep, part.doc.body, steps(part), rel=rel)
    return rep, doc


def block(name, boss=False):
    """60 x 40 x 20 block (x 0..60, y 0..40, z 0..20); boss=True adds a d16 x 10 boss on top
    at (30, 20)."""
    p = Part(name)
    s = p.sketch("front"); s.rect((0, 0), (60, 40)); p.extrude(s, 20)
    if boss:
        s = p.sketch(on=p.face("+z")); s.circle((30, 20), d=16); p.extrude(s, 10)
    return p


def hole_through(part, plane, world, d=4.0):
    """A circle on the plane around the projection of `world`, cut blind 150 mm towards it:
    a hole along the plane's normal through `world` (proves the plane's place). Blind, not
    Through All: SolidWorks' Through All from a sketch plane outside the body was seen to stop
    short of an oblique exit (~0.25 % of such a hole). The fixtures keep the holes apart
    (crossing holes make the two kernels' volumes differ by ~1e-4)."""
    pl = plane.result_plane
    w = np.asarray(world, float)
    s = part.sketch(plane)
    s.circle(pl.to_local(w), d=d)
    part.cut(s, 150.0, reverse=float((w - pl.origin) @ pl.normal) > 0)   # a cut goes along -normal
    return s


def sketch_on(part, plane, world, d=4.0):
    """A sketch on an inclined plane: a circle around the projection of `world`, used by no
    feature. (A hole along an inclined plane's normal is no proof: SolidWorks measures such a
    hole up to ~1 % off where it leaves the block through two perpendicular faces, although
    its geometry is exact; BauyrCAD's equals the analytic value.) Its points are checked
    where BauyrCAD's are, so the sketch proves the plane's place and frame."""
    s = part.sketch(plane)
    s.circle(plane.result_plane.to_local(np.asarray(world, float)), d=d)
    return s


def boss_on(part, plane, world, depth, d=8.0, reverse=False):
    s = part.sketch(plane)
    s.circle(plane.result_plane.to_local(np.asarray(world, float)), d=d)
    part.extrude(s, depth, reverse=reverse)
    return s


def assert_plane(sw, doc, f, typ, constraints, signed=True, **kw):
    """The saved plane is BauyrCAD's (place; normal, unless signed=False) and of this
    definition; kw: distance / angle / refs read back."""
    got = plane_def(sw, doc, feature(sw, doc, f.name))
    want = f.result_plane
    assert got["type"] == typ, (f.name, got)
    assert got["constraints"] == constraints, (f.name, got)
    dot = float(got["normal"] @ want.normal)
    assert abs(abs(dot) - 1) < 1e-9, (f.name, got["normal"], want.normal)
    if signed:
        assert dot > 0, (f.name, got["normal"], want.normal)
    assert abs(float((got["origin"] - want.origin) @ want.normal)) < 1e-6, (f.name, got)
    for k, v in kw.items():
        assert got[k] == (pytest.approx(v, abs=1e-9) if isinstance(v, float) else v), (f.name, k, got)
    return got


# ---- RP-02/03/04/06/10/11/12: offset, coincident and mid planes --------------------------------
def test_offset_coincident_and_mid_planes(sw, tmp_path):
    p = block("ref-planes-offset")
    p1 = p.plane("front", offset=-15)                     # RP-02: the other side (Flip offset)
    boss_on(p, p1, (30, 20, -15), 15, d=16)
    p2 = p.plane(p.face("+z"), offset=12)                 # RP-03: offset from a model face
    boss_on(p, p2, (45, 30, 32), 12, reverse=True)
    p3 = p.plane("top", offset=20)
    p4 = p.plane(p3, offset=10)                           # RP-04: offset from an earlier plane
    hole_through(p, p4, (10, 30, 5))
    p5 = p.plane("right", offset=0)                       # RP-06: coincident copy
    boss_on(p, p5, (0, 20, 10), 5, reverse=True)
    p6 = p.plane("top", offset=50, flip=True)             # RP-12: Flip (normal -y)
    boss_on(p, p6, (50, 50, 10), 10)
    p7 = p.plane(mid=(p.face("-x"), p.face("+x")))        # RP-10: mid between faces
    hole_through(p, p7, (p7.result_plane.origin[0], 10, 15))
    p8 = p.plane(mid=("front", p1))                       # RP-10: mid between planes
    s8 = p.sketch(p8); s8.circle(p8.result_plane.to_local((30, 20, -7.5)), d=6); p.cut(s8, 3)
    far = SketchPlane((0, 0, -40), (0, 0, 1), (1, 0, 0))
    p9 = p.plane(mid=("front", far))                      # RP-11: an explicit second plane
    assert p.doc.body is not None and all(not f.error for f in p.doc.features)
    assert np.allclose(p6.result_plane.normal, (0, -1, 0))

    rep, doc = proven(sw, p, tmp_path, helpers=["Plane9 Plane"], notes=[FLIP_NOTE])
    assert any(n.startswith("Plane6:") for n in rep.notes), rep.notes      # only the offset flip
    assert all(not n.startswith(("Plane1:", "Plane2:", "Plane7:", "Plane8:")) for n in rep.notes)
    assert_plane(sw, doc, p1, 5, [8 | 256], distance=15.0, refs=["Front Plane"])
    assert_plane(sw, doc, p2, 5, [8], distance=12.0, refs=["entity"])
    assert_plane(sw, doc, p3, 5, [8], distance=20.0, refs=["Top Plane"])
    assert_plane(sw, doc, p4, 5, [8], distance=10.0, refs=["Plane3"])
    assert_plane(sw, doc, p5, 1, [4], refs=["Right Plane"])
    got6 = assert_plane(sw, doc, p6, 5, [8], signed=False, distance=50.0, refs=["Top Plane"])
    assert got6["normal"] @ np.array([0, 1.0, 0]) > 0       # SolidWorks keeps Top's direction
    assert_plane(sw, doc, p7, 1, [128, 128], refs=["entity", "entity"])
    assert_plane(sw, doc, p8, 1, [128, 128])
    assert sorted(plane_def(sw, doc, feature(sw, doc, "Plane8"))["refs"]) == ["Front Plane", "Plane1"]
    assert_plane(sw, doc, p9, 1, [128, 128])
    assert sorted(plane_def(sw, doc, feature(sw, doc, "Plane9"))["refs"]) == ["Front Plane", "Plane9 Plane"]
    # the sketches sit on the planes themselves
    for sk, pl in (("Sketch2", "Plane1"), ("Sketch3", "Plane2"), ("Sketch4", "Plane4"),
                   ("Sketch5", "Plane5"), ("Sketch6", "Plane6"), ("Sketch7", "Plane7"),
                   ("Sketch8", "Plane8")):
        assert pl in [x.Name for x in (sw.T(q, "IFeature") for q in feature(sw, doc, sk).GetParents())], sk


# ---- RP-05/07/08/09: at angle, three points, normal to a curve, explicit planes -----------------
def test_angle_three_point_normal_and_explicit_planes(sw, tmp_path):
    p = block("ref-planes-angle")
    top_edge = p.edges_where(kind="line", direction="x", near=(30, 40, 20))[0]
    z_edge = p.edges_where(kind="line", direction="z", near=(60, 40, 10))[0]
    a1 = p.plane("front", angle=30, axis="y")             # RP-07 about a global axis
    sketch_on(p, a1, (6, 5, 4))
    a2 = p.plane("top", angle=-45, axis=top_edge)         # RP-07 about an edge, negative angle
    sketch_on(p, a2, (50, 33, 4))
    ax = p.axis(planes=("front", "right"))                # the Y axis as a reference axis
    a3 = p.plane("front", angle=240, axis=ax)             # RP-07 about a RefAxis, > 180 deg
    sketch_on(p, a3, (50, 21, 4))
    t1 = p.plane(points=[(0, 0, 0), (60, 0, 0), (0, 40, 20)])         # RP-08 on vertices
    sketch_on(p, t1, (18, 29, 16))
    t2 = p.plane(points=[(0, 0, 40), (10, 0, 45), (0, 10, 50)], flip=True)   # RP-08 mid-air
    sketch_on(p, t2, (22, 5, 4))
    n1 = p.plane(normal_to="x", at=(10, 0, 0))            # RP-09 global axis + point
    hole_through(p, n1, (6, 13, 4))
    n2 = p.plane(normal_to=z_edge)                        # RP-09 edge, no point (its start)
    hole_through(p, n2, (30, 25, 4))
    n3 = p.plane(normal_to=ax, at=(60, 40, 20))           # RP-09 RefAxis + a vertex
    hole_through(p, n3, (42, 5, 16))
    from bauyrcad.core.features_more import RefPlaneFeature
    sl = p.sketch("front"); ln = sl.sk.add_line((60, 0), (60, 10), True)     # a centerline
    a4 = p._add(RefPlaneFeature("angle", {"type": "std", "name": "Front Plane"}, angle=45,
                                axis={"type": "sketch_line", "sketch": sl.feature.id,
                                      "entity": ln.id}))  # RP-07 about a sketch line (GUI)
    sketch_on(p, a4, (55, 20, 4))
    front_sketch = p.doc.features[0]
    from bauyrcad.api import Sketch
    e1 = p.plane(Sketch(p, front_sketch), offset=5)       # RP-05: a sketch's own plane
    assert e1.base == {"type": "std", "name": "Front Plane"}
    hole_through(p, e1, (30, 37, 4))
    tilted = SketchPlane((0, 0, 0), (1, 1, 0), (0, 0, 1))
    e2 = p.plane(tilted, offset=3)                        # RP-05: an explicit plane (helper)
    sketch_on(p, e2, (6, 37, 4))
    assert p.doc.body is not None and all(not f.error for f in p.doc.features), \
        [(f.name, f.error) for f in p.doc.features if f.error]

    rep, doc = proven(sw, p, tmp_path, notes=[FLIP_NOTE],
                      helpers=["Plane1 Axis", "Plane5 Points", "Plane6 Axis", "Plane6 Points",
                               "Plane11 Plane", "Plane11 Plane Sketch"])
    flipped = {n.split(":")[0] for n in rep.notes}
    assert flipped <= {"Plane6", "Plane7", "Plane8"}, rep.notes        # normal-to planes only
    for f, ang in ((a1, 30.0), (a2, 315.0), (a3, 240.0), (a4, 45.0)):
        flip = plane_def(sw, doc, feature(sw, doc, f.name))["constraints"][0] & 256   # either way
        got = assert_plane(sw, doc, f, 7, [16 | flip, 4])
        assert got["angle"] in (pytest.approx(ang), pytest.approx(360 - ang)), (f.name, got)
    assert plane_def(sw, doc, feature(sw, doc, "Plane2"))["refs"] == ["Top Plane", "entity"]
    assert plane_def(sw, doc, feature(sw, doc, "Plane3"))["refs"] == ["Front Plane", "Axis1"]
    assert_plane(sw, doc, t1, 3, [4, 4, 4], refs=["entity"] * 3)
    assert_plane(sw, doc, t2, 3, [4, 4, 4])
    for f in (n1, n2, n3):
        assert_plane(sw, doc, f, 8, [2, 4], signed=f.name not in flipped)
    assert plane_def(sw, doc, feature(sw, doc, "Plane8"))["refs"][0] == "Axis1"
    assert_plane(sw, doc, e1, 5, [8], distance=5.0, refs=["Front Plane"])
    assert_plane(sw, doc, e2, 5, [8], distance=3.0, refs=["Plane11 Plane"])
    # every sketch sits on its plane, with its points where BauyrCAD's are
    for f in p.doc.features:
        if f.TYPE == "sketch" and f.plane_spec and f.plane_spec.get("type") == "feature":
            sk = feature(sw, doc, f.name)
            plane = next(g.name for g in p.doc.features if g.id == f.plane_spec["id"])
            assert plane in [sw.T(q, "IFeature").Name for q in sk.GetParents()], f.name
            assert_sketch_points(sw, sk, f)


# ---- RA-01..07: reference axes, used by patterns -------------------------------------------------
def test_reference_axes_of_every_kind(sw, tmp_path):
    p = block("ref-axes", boss=True)
    cyl = p.faces_where(kind="cylinder", d=16)[0]
    rim = p.edges_where(kind="circle", r=8, near=(38, 20, 30))[0]
    x1 = p.axis(face=cyl)                                 # RA-01
    x2 = p.axis(face=p.face("+z"))                        # RA-02: the boss top's normal
    x3 = p.axis(edge=p.edges_where(kind="line", direction="x", near=(30, 40, 20))[0])  # RA-03
    x4 = p.axis(edge=rim)                                 # RA-04: a circular edge
    x5 = p.axis(planes=("front", "top"))                  # RA-05: the X axis
    x6 = p.axis(through=[(0, 0, 0), (60, 40, 20)])        # RA-06 on vertices
    x7 = p.axis(through=[(10, 50, 0), (20, 60, 5)])       # RA-06 mid-air (helper points)
    x8 = p.axis(point=(5, 5, 5), direction=(1, 1, 0))     # RA-07 (helper points)
    x9 = p.axis(point=(0, 0, 0), direction=(0, 1, 0))     # RA-07 along Y: two standard planes
    # consumers select the axes by their BauyrCAD feature id
    s = p.sketch(on=p.face("+z", at=(50, 20, 20))); s.circle(s.plane.to_local((45, 20, 20)), d=4)
    c1 = p.cut(s, 5)
    p.pattern_circular([c1], axis=x1, count=4)
    s = p.sketch(on=p.face("+z", at=(50, 20, 20))); s.circle(s.plane.to_local((55, 5, 20)), d=3)
    c2 = p.cut(s, 4)
    p.pattern_linear([c2], direction=x5, spacing=10, count=3)      # Axis5 runs along -X
    s = p.sketch(on=p.face("+z", at=(50, 20, 20))); s.circle(s.plane.to_local((38.5, 28.5, 20)), d=2)
    c3 = p.cut(s, 3)
    p.pattern_circular([c3], axis=rim, count=4)           # a circular edge: its coaxial face
    assert all(not f.error for f in p.doc.features), [(f.name, f.error) for f in p.doc.features if f.error]

    rep, doc = proven(sw, p, tmp_path, helpers=["Axis2 Points", "Axis7 Points", "Axis8 Points",
                                                "CirPattern2 Axis"])
    for f, typ in ((x1, 3), (x2, 4), (x3, 0), (x4, 3), (x5, 1), (x6, 2), (x7, 2), (x8, 2), (x9, 1)):
        got = axis_def(sw, doc, feature(sw, doc, f.name))
        assert got["type"] == typ, (f.name, got)
        assert same_line(got["point"], got["direction"], *f.result_axis), (f.name, got, f.result_axis)
    assert axis_def(sw, doc, feature(sw, doc, "Axis5"))["refs"] == 2


# ---- RPT-01..03: reference points, used by a plane and an axis -----------------------------------
def test_reference_points_of_every_kind(sw, tmp_path):
    p = block("ref-points", boss=True)
    rim = p.edges_where(kind="circle", r=8, near=(38, 20, 30))[0]
    q1 = p.point((1, 2, 3))                               # RPT-01 (helper sketch point)
    q2 = p.point(arc_center=rim)                          # RPT-02
    q3 = p.point(face_center=p.face("+x"))                # RPT-03 planar
    q4 = p.point(face_center=p.faces_where(kind="cylinder", d=16)[0])   # RPT-03 cylinder
    pl = p.plane(points=[q2.result_point, q3.result_point, q4.result_point])   # selects them
    hole_through(p, pl, (45, 20, 10), d=3)
    ax = p.axis(through=[q1.result_point, q2.result_point])
    assert np.allclose(q2.result_point, (30, 20, 30)) and np.allclose(q4.result_point, (30, 20, 25))

    rep, doc = proven(sw, p, tmp_path, helpers=["Point1 Points"])
    for f, typ in ((q1, 7), (q2, 3), (q3, 4), (q4, 4)):
        got = point_def(sw, doc, feature(sw, doc, f.name))
        assert got["type"] == typ, (f.name, got)
        assert np.allclose(got["xyz"], f.result_point, atol=1e-6), (f.name, got)
    assert plane_def(sw, doc, feature(sw, doc, pl.name))["refs"] == ["Point2", "Point3", "Point4"]
    got = axis_def(sw, doc, feature(sw, doc, ax.name))
    assert got["type"] == 2 and same_line(got["point"], got["direction"], *ax.result_axis)


# ---- CV-01..05: curves through XYZ points ---------------------------------------------------------
@pytest.mark.parametrize("closed", [False, True])
def test_spline_curve_keeps_its_points_and_shape(sw, tmp_path, closed):
    pts = ([(20, 0, 0), (0, 20, 3), (-20, 0, 0), (0, -20, -3)] if closed else
           [(0, 0, 0), (20, 10, 5), (40, 0, 10), (60, -10, 0)])
    p = Part(f"ref-curve-{'closed' if closed else 'open'}")
    c = p.curve(pts, closed=closed)                       # CV-01 / CV-02
    p.sweep(c, d=2 if closed else 3)
    assert p.doc.body is not None
    rep, doc = proven(sw, p, tmp_path, notes=["differs slightly"], rel=1e-3)
    assert not any(n.startswith("Curve1") for n in rep.notes), rep.notes   # the same spline
    got = curve_points(sw, feature(sw, doc, "Curve1"))
    want = np.array(pts + ([pts[0]] if closed else []), float)
    assert got.shape == want.shape and np.abs(got - want).max() < 1e-9


def test_polylines_and_two_point_curves(sw, tmp_path):
    p = Part("ref-curve-lines")
    l1 = p.curve([(0, 0, 0), (30, 0, 0), (30, 20, 10), (0, 20, 20)], polyline=True)  # CV-03
    l2 = p.curve([(0, 0, 40), (20, 0, 40), (20, 20, 40), (0, 20, 40)], polyline=True,
                 closed=True)                                                        # CV-04
    c2 = p.curve([(0, 0, 60), (30, 0, 60)])                                          # CV-05
    p.sweep(c2, d=4)
    rep, doc = proven(sw, p, tmp_path, notes=["differs slightly"], rel=1e-3)
    for f, n_seg, n_pts in ((l1, 3, 4), (l2, 4, 4)):
        sk = sketch_def(sw, feature(sw, doc, f.name))
        assert len(sk.GetSketchSegments()) == n_seg and len(sk.GetSketchPoints2()) == n_pts
    assert np.abs(curve_points(sw, feature(sw, doc, c2.name)) - np.array(c2.points)).max() < 1e-9
