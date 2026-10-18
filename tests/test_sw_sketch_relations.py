"""Sketch relations and dimensions in real SolidWorks (package WP04).

Every BauyrCAD relation becomes the SolidWorks relation of the same kind, checked call by call
(SketchAddConstraints returns nothing: the sketch's relation kinds must gain the expected
swConstraintType_e), and a relation SolidWorks refuses is a blocking warning. Every dimension
becomes the SolidWorks dimension of the same kind and is read back (IDisplayDimension.Type2,
value, IDimension.DrivenState, text position); driven (reference) dimensions stay driven; the
fully-defined state is kept.

Every export is strict (require_native=True), reopened in SolidWorks and decoded offline
(sw_support.check_saved_part); each sketch's relations are compared as a multiset of
swConstraintType_e with what BauyrCAD holds."""
import math

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.api import Part
from bauyrcad.core.sketch import Constraint, constraints, model, solver
from sw_support import (assert_sketch_points, check_saved_part, feature, needs_solidworks,
                        relation_kinds, sketch_dims, sketch_summary)

pytestmark = needs_solidworks

KIND = {"sketch": "ProfileFeature"}
# swConstraintType_e of BauyrCAD relations / dimensions (horizontal / vertical: on a line, or
# between two points)
SW_KIND = {"coincident": 9, "midpoint": 12, "parallel": 7, "perpendicular": 8, "collinear": 27,
           "equal": 14, "concentric": 10, "tangent": 6, "fix": 17, "symmetric": 11,
           "distance": 1, "hdistance": 1, "vdistance": 1, "point_line_distance": 1,
           "diameter": 15, "radius": 3, "angle": 2}
DIM_TYPE = {"distance": 2, "point_line_distance": 2, "hdistance": 11, "vdistance": 12,
            "diameter": 6, "radius": 5, "angle": 3}


def steps(part):
    return [(f.name, KIND.get(f.TYPE)) for f in part.doc.features]


def proven(sw, part, tmp_path, notes=()):
    rep = solidworks.export_part(sw, part.doc, tmp_path / part.name, require_native=True)
    assert rep.native and not rep.warnings, rep.warnings
    assert rep.features == [f.name for f in part.doc.features]
    for n in rep.notes:
        assert any(k in n for k in notes), rep.notes
    doc = check_saved_part(sw, rep, part.doc.body, steps(part))
    return rep, doc


def ln(sketch, a, b, construction=False):
    """A line without the automatic Horizontal / Vertical (relations are added explicitly)."""
    return sketch.sk.add_line(a, b, construction)


def poly(sketch, pts):
    """A closed chain of lines sharing their end points, without automatic relations."""
    ids = [sketch.sk.pid(p) for p in pts]
    return [sketch.sk.add_line(a, b) for a, b in zip(ids, ids[1:] + ids[:1])]


def rel(sketch, kind, *items):
    """Add a relation as the GUI's Add Relations does (make_relation + try_add)."""
    refs = [i if isinstance(i, tuple) else ("e", i.id) for i in items]
    for c in constraints.make_relation(sketch.sk, kind, refs):
        ok, msg = solver.try_add(sketch.sk, c)
        assert ok, (kind, msg)


def add(sketch, c):
    ok, msg = solver.try_add(sketch.sk, c)
    assert ok, (c.type, msg)
    return c


def dim(sketch, kind, *items, label=None, driving=True):
    """A dimension as the GUI's Smart Dimension makes it: positive value (sign flags in
    data), text at label (None: as the API, no position)."""
    refs = [i if isinstance(i, tuple) else ("e", i.id) for i in items]
    c = Constraint(0, kind, refs)
    if kind in ("hdistance", "vdistance", "point_line_distance"):
        c.data["s"] = 1.0
        c.data["s"] = 1.0 if constraints.measure(sketch.sk, c) >= 0 else -1.0
    c.label = None if label is None else list(label)
    c.value = constraints.measure(sketch.sk, c)
    c.driving = driving
    if not driving:                          # as the GUI's over-define prompt does
        c.id = sketch.sk._next_cid
        sketch.sk._next_cid += 1
        sketch.sk.constraints.append(c)
        return c
    return add(sketch, c)


def gui_angle(sketch, l1, l2, label):
    """The GUI's angle between two lines: the rays (s1, s2) towards the text position."""
    sk = sketch.sk
    d1 = sk.pt(l1.p2) - sk.pt(l1.p1)
    d2 = sk.pt(l2.p2) - sk.pt(l2.p1)
    cr = d1[0] * d2[1] - d1[1] * d2[0]
    A1, A2 = sk.pt(l1.p1), sk.pt(l2.p1)
    t = ((A2 - A1)[0] * d2[1] - (A2 - A1)[1] * d2[0]) / cr
    V = A1 + t * d1
    L = np.asarray(label, float)
    s1 = 1.0 if np.dot(L - V, d1) >= 0 else -1.0
    s2 = 1.0 if np.dot(L - V, d2) >= 0 else -1.0
    r1, r2 = d1 * s1, d2 * s2
    ang = math.atan2(r1[0] * r2[1] - r1[1] * r2[0], np.dot(r1, r2))
    c = Constraint(0, "angle", [("e", l1.id), ("e", l2.id)],
                   data={"s1": s1, "s2": s2, "sgn": 1.0 if ang >= 0 else -1.0})
    c.label = list(L)
    c.value = constraints.measure(sk, c)
    return add(sketch, c)


def expected_kinds(sk, extra=()):
    """swConstraintType_e multiset SolidWorks must hold for a BauyrCAD sketch (+ extra: ties
    the export adds, e.g. a curve point on the sketch origin)."""
    out = {}
    for c in sk.constraints:
        if c.type == "arc_internal":
            continue
        k = SW_KIND.get(c.type)
        if c.type in ("horizontal", "vertical"):
            pts = all(t == "p" for t, _ in c.refs)
            k = {("horizontal", False): 4, ("vertical", False): 5,
                 ("horizontal", True): 25, ("vertical", True): 26}[(c.type, pts)]
        out[k] = out.get(k, 0) + 1
    for k in extra:
        out[k] = out.get(k, 0) + 1
    return out


def fully_defined(sketch):
    return solver.analyze(sketch.sk).fully_defined


# ---- SK-RL-02/03/05/06/07/08/09/10/11/12/14: every relation type ------------------------------
def relations_part():
    p = Part("relations")
    # Sketch1: a plate defined by relations only (+ 5 dimensions), fully defined
    s = p.sketch("front")
    L = poly(s, [(0, 0), (40, 0), (40, 25), (0, 25)])
    rel(s, "coincident", ("p", L[0].p1), ("p", 0))              # point-point (on the origin)
    rel(s, "horizontal", L[0])
    rel(s, "perpendicular", L[0], L[1])
    rel(s, "parallel", L[2], L[0])
    rel(s, "perpendicular", L[3], L[2])
    cl = ln(s, (20, 0), (20, 25), construction=True)
    rel(s, "midpoint", ("p", cl.p1), L[0])
    rel(s, "midpoint", ("p", cl.p2), L[2])
    c1, c2 = s.circle((12, 12.5), r=4), s.circle((28, 12.5), r=4)
    rel(s, "symmetric", ("p", c1.center), ("p", c2.center), cl)  # about a centerline
    rel(s, "equal", c1, c2)
    s.dim(L[0]); s.dim(L[1]); s.dim(c1)
    dim(s, "distance", c1, c2, label=(20, 18))                    # centre to centre
    dim(s, "vdistance", ("p", 0), ("p", c1.center), label=(6, 6))
    assert fully_defined(s)
    p.extrude(s, 8)

    # Sketch2: tangents (line side, line end on the circle, external / internal arcs),
    # concentric, Fix on a circle
    s2 = p.sketch("front")
    C = s2.circle((0, 60), r=10)
    rel(s2, "fix", C)
    t = math.radians(60)
    touch, d = np.array([10 * math.cos(t), 60 + 10 * math.sin(t)]), np.array([-math.sin(t), math.cos(t)])
    Lt = s2.line(tuple(touch - 12 * d), tuple(touch + 12 * d))
    rel(s2, "tangent", Lt, C)
    A = s2.arc((0, 60), (15, 60), (0, 75))                        # its own centre, on C's
    rel(s2, "concentric", A, C)
    B = s2.arc((25, 60), (25 + 15 * math.cos(math.radians(150)), 60 + 15 * math.sin(math.radians(150))),
               (25 + 15 * math.cos(math.radians(210)), 60 + 15 * math.sin(math.radians(210))))
    rel(s2, "tangent", B, C)                                      # external
    D = s2.arc((0, 63), (7 * math.cos(math.radians(-120)), 63 + 7 * math.sin(math.radians(-120))),
               (7 * math.cos(math.radians(-60)), 63 + 7 * math.sin(math.radians(-60))))
    rel(s2, "tangent", D, C)                                      # internal
    q = np.array([10 * math.cos(math.radians(135)), 60 + 10 * math.sin(math.radians(135))])
    e = np.array([-math.sin(math.radians(135)), math.cos(math.radians(135))])
    Lq = s2.line(tuple(q), tuple(q + 10 * e))
    rel(s2, "coincident", ("p", Lq.p1), C)                        # the GUI's tangent snap:
    add(s2, Constraint(0, "tangent", [("e", Lq.id), ("e", C.id)], data={"at": Lq.p1}))
    assert {c.data.get("mode") for c in s2.sk.constraints if c.type == "tangent"} == {None, "internal"}

    # Sketch3: collinear, H/V points (3 points, the origin), Fix on a point / line / arc /
    # spline / Point entity, point on line / arc / circle / spline, equal and parallel on 3
    # lines, equal arc-circle, midpoint of a Point entity, symmetric about an ordinary line
    s3 = p.sketch("front")
    La, Lb = ln(s3, (0, 100), (20, 100)), ln(s3, (30, 100), (50, 100))
    Lx = ln(s3, (85, 100), (95, 100))
    rel(s3, "collinear", La, Lb, Lx)                             # 3 lines: 2 pairs
    Pa, Pb, Pc = s3.point((60, 100)), s3.point((70, 100)), s3.point((80, 100))
    rel(s3, "horizontal", ("p", Pa.p), ("p", Pb.p), ("p", Pc.p))
    Pv = s3.point((-5, 120))
    rel(s3, "vertical", ("p", Pv.p), ("p", 0))
    rel(s3, "fix", ("p", La.p1))
    Lc = s3.line((0, 130), (20, 140)); rel(s3, "fix", Lc)
    Ac = s3.arc((40, 130), (45, 130), (40, 135)); rel(s3, "fix", Ac)
    Sp = s3.spline([(60, 130), (70, 138), (80, 132), (90, 140)]); rel(s3, "fix", Sp)
    Pf = s3.point((100, 130)); rel(s3, "fix", Pf)
    P1 = s3.point((5, 132.5)); rel(s3, "coincident", ("p", P1.p), Lc)
    P2 = s3.point((40 + 5 * math.cos(math.radians(45)), 130 + 5 * math.sin(math.radians(45))))
    rel(s3, "coincident", ("p", P2.p), Ac)
    Cc = s3.circle((120, 130), r=6)
    P3 = s3.point((126, 130)); rel(s3, "coincident", ("p", P3.p), Cc)
    on_spline = model.spline_eval(np.array([s3.sk.pt(i) for i in Sp.pts]), 101)[0][37]
    P4 = s3.point(tuple(on_spline)); rel(s3, "coincident", ("p", P4.p), Sp)
    Q = [ln(s3, (0, 160 + 5 * k), (10, 160 + 5 * k)) for k in range(3)]
    rel(s3, "parallel", *Q)
    rel(s3, "equal", *Q)
    Ae = s3.arc((140, 130), (146, 130), (140, 136))
    rel(s3, "equal", Ae, Cc)
    Pm = s3.point((10, 100)); rel(s3, "midpoint", ("p", Pm.p), La)
    Ax = ln(s3, (30, 150), (30, 180))                            # an ordinary line as the axis
    S1, S2 = s3.point((25, 165)), s3.point((35, 165))
    rel(s3, "symmetric", ("p", S1.p), ("p", S2.p), Ax)
    return p


def test_every_relation_type_is_native(sw, tmp_path):
    p = relations_part()
    types = {c.type for f in p.doc.features if f.TYPE == "sketch" for c in f.sketch.constraints}
    assert set(constraints.RELATION_NAMES) <= types           # all 12 relation types
    rep, doc = proven(sw, p, tmp_path)
    sketches = {f.name: f.sketch for f in p.doc.features if f.TYPE == "sketch"}
    for name, sk in sketches.items():
        f = feature(sw, doc, name)
        got = relation_kinds(sw, f)
        # Point entities on a curve's point get a coincident tie (none here); the origin is
        # tied only when a curve uses BauyrCAD's point 0 (none here)
        assert got == expected_kinds(sk), (name, got, expected_kinds(sk))
        assert_sketch_points(sw, f, next(g for g in p.doc.features if g.name == name))
        status = sketch_summary(sw, f)["status"]
        assert status == (3 if solver.analyze(sk).fully_defined else 2), (name, status)
    assert sketch_summary(sw, feature(sw, doc, "Sketch1"))["status"] == 3
    sw.close(doc)


# ---- SK-RL-14: a relation SolidWorks refuses is never dropped silently -----------------------
def refused_part():
    p = Part("refused")
    s = p.sketch("front")
    L = s.rect((0, 0), (30, 20))
    # a second Horizontal on the same line (BauyrCAD keeps redundant relations it is given;
    # SolidWorks refuses the duplicate)
    s.sk.add_constraint("horizontal", [("e", L[0].id)])
    p.extrude(s, 5)
    return p


def test_refused_relations_are_blocking(sw, tmp_path):
    p = refused_part()
    with pytest.raises(solidworks.SolidWorksError, match=r"Sketch1: 1 relation\(s\) not transferred: "
                                                         r"Horizontal \(line \d+\): refused"):
        solidworks.export_part(sw, p.doc, tmp_path / "strict", require_native=True)
    rep = solidworks.export_part(sw, p.doc, tmp_path / "loose")
    assert rep.native and len(rep.warnings) == 1 and "Horizontal" in rep.warnings[0], rep.warnings


# ---- SK-RL-08 (D2): an Equal between a line and an arc has no SolidWorks equivalent -------------
def test_mixed_equal_is_refused_in_bauyrcad_and_before_solidworks_starts(sw):
    from bauyrcad.api import ApiError
    p = Part("mixed-equal")
    s = p.sketch("front")
    ln, c = s.line((0, 0), (10, 0)), s.circle((20, 0), r=10)
    with pytest.raises(ApiError, match="Equal relates lines"):
        s.relate("equal", ln, c)
    s.sk.add_constraint("equal", [("e", ln.id), ("e", c.id)])   # e.g. an older file
    issues = solidworks.native_export_issues(p.doc)
    assert any("Equal between a line and an arc" in i for i in issues), issues


# ---- SK-DM-02/03/05/07/08/09/11: every dimension type, read back -------------------------------
def dims_part():
    p = Part("dims")
    # Sketch1: an inclined quadrilateral, fully defined by relations and every linear / angle
    # dimension (API and GUI text positions, acute / obtuse, a supplementary-quadrant angle)
    s = p.sketch("front")
    L = poly(s, [(0, 0), (40, 0), (50, 30), (5, 25)])
    rel(s, "coincident", ("p", L[0].p1), ("p", 0))
    rel(s, "horizontal", L[0])
    s.dim(L[0])                                                   # line length, no text position
    gui_angle(s, L[0], L[1], (37, 3))                             # interior 108.4 deg
    dim(s, "distance", ("p", L[1].p2), ("p", L[0].p1), label=(22, 19))   # aligned point-point
    dim(s, "point_line_distance", ("p", L[2].p2), L[0], label=(9, 12))   # point to line
    gui_angle(s, L[0], L[3], (-3, 3))                             # supplementary quadrant 101.3
    assert fully_defined(s)
    p.extrude(s, 8)

    # Sketch2 on the top face: radius of a circle, diameter, centre distances, point to
    # centre, centre to line, radius / diameter of arcs
    s2 = p.sketch(on=p.face("+z"))
    C1, C2 = s2.circle((15, 10), r=3), s2.circle((30, 12), r=2.5)
    s2.dim(C1, type="radius"); s2.dim(C2)
    dim(s2, "distance", C1, C2)                                   # centre to centre, API
    dim(s2, "distance", ("p", 0), C1, label=(5, 9))               # origin to a centre
    base = ln(s2, (5, 2), (35, 2), construction=True)
    rel(s2, "horizontal", base)
    dim(s2, "point_line_distance", C2, base, label=(33, 7))       # centre to a line
    A1 = s2.arc((20, 20), (23, 20), (20, 23)); A1.construction = True
    s2.dim(A1)                                                    # arc radius
    A2 = s2.arc((35, 20), (37, 20), (35, 22)); A2.construction = True
    s2.dim(A2, type="diameter")                                   # arc diameter
    p.cut(s2, 2)

    # Sketch3: a fully defined rectangle with a redundant diagonal as a driven dimension
    s3 = p.sketch("front")
    R = s3.rect((30, 5), (60, 25))
    s3.dim(R[0]); s3.dim(R[1])
    dim(s3, "hdistance", ("p", 0), ("p", R[0].p1), label=(15, -4))
    dim(s3, "vdistance", ("p", 0), ("p", R[0].p1), label=(26, 2))
    assert fully_defined(s3)
    diag = dim(s3, "distance", ("p", R[0].p1), ("p", R[1].p2), label=(42, 18), driving=False)
    assert solver.check_constraint(s3.sk, Constraint(0, "distance", diag.refs, 1.0)) != "ok"
    assert fully_defined(s3)
    p.extrude(s3, 5)

    # Sketch4 (unused): API angles (default text: acute, and obtuse with a turned ray), the
    # offset tool's line-line distance, and a driven diameter that is not redundant
    s4 = p.sketch("front")
    a = s4.line((0, 60), (30, 60))
    b = s4.line(a.p1, (30 * math.cos(math.radians(35)), 60 + 30 * math.sin(math.radians(35))))
    add(s4, Constraint(0, "angle", [("e", a.id), ("e", b.id)], math.radians(35)))
    c = s4.line((40, 60), (70, 60))
    e = s4.line(c.p1, (40 + 30 * math.cos(math.radians(70)), 60 + 30 * math.sin(math.radians(70))))
    k = Constraint(0, "angle", [("e", c.id), ("e", e.id)], data={"s1": -1.0, "sgn": -1.0})
    k.value = constraints.measure(s4.sk, k)
    add(s4, k)
    chain = s4.polyline([(0, 90), (20, 90), (30, 100)])
    s4.offset(chain, 3)
    circ = s4.circle((60, 95), r=4)
    dim(s4, "diameter", circ, driving=False)
    return p


def test_every_dimension_type_is_read_back(sw, tmp_path):
    p = dims_part()
    s4 = p.doc.features[-1].sketch
    angles = sorted(round(math.degrees(c.value), 6) for c in s4.constraints if c.type == "angle")
    assert angles == [35.0, 110.0]
    rep, doc = proven(sw, p, tmp_path)
    for f in p.doc.features:
        if f.TYPE != "sketch":
            continue
        got = sketch_dims(sw, feature(sw, doc, f.name))
        assert_sketch_points(sw, feature(sw, doc, f.name), f)
        ours = [c for c in f.sketch.constraints if constraints.is_dimension(c)]
        assert len(got) == len(ours), (f.name, got)
        left = list(got)
        for c in ours:
            want = abs(c.value if c.driving else constraints.measure(f.sketch, c))
            match = [g for g in left if g["type"] == DIM_TYPE[c.type]
                     and abs(abs(g["value"]) - want) < 1e-6 and g["driven"] == (2 if c.driving else 1)]
            assert match, (f.name, c.type, want, c.driving, got)
            g = match[0]
            left.remove(g)
            if c.label is not None:                           # SK-DM-11: text where BauyrCAD has it
                w = f.plane.to_world(*c.label)
                off = g["pos"] - w
                off -= float(off @ f.plane.normal) * f.plane.normal     # (SolidWorks lifts it)
                assert np.linalg.norm(off) < 1e-6, (f.name, c.type, g["pos"], w)
        assert sketch_summary(sw, feature(sw, doc, f.name))["status"] == (
            3 if solver.analyze(f.sketch).fully_defined else 2), f.name
    sw.close(doc)


# ---- SK-DM-09: a dimension SolidWorks finds over-defining is never kept silently ------------------
def test_over_defining_dimension_is_blocking(sw, tmp_path):
    p = Part("joined")
    s = p.sketch("front")
    l1 = ln(s, (0, 0), (10, 0))
    l2 = ln(s, (10, 0), (10, 10))       # its own start point on l1's end: SolidWorks joins them
    rel(s, "coincident", ("p", l1.p1), ("p", 0))
    rel(s, "horizontal", l1)
    s.dim(l1)
    dim(s, "hdistance", ("p", 0), ("p", l2.p1), label=(5, -3))   # = l1's length there
    with pytest.raises(solidworks.SolidWorksError, match="would over-define the SolidWorks sketch"):
        solidworks.export_part(sw, p.doc, tmp_path / "strict", require_native=True)
    rep = solidworks.export_part(sw, p.doc, tmp_path / "loose")
    assert rep.native and any("would over-define" in w for w in rep.warnings), rep.warnings
    assert any("joined" in n for n in rep.notes), rep.notes
