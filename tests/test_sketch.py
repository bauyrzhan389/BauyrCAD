import math

import numpy as np
import pytest

from bauyrcad.core.sketch import (Arc, Circle, Constraint, Line, Sketch, SketchError, builders,
                                  constraints, ops, solver)


def rect(sk, w=40, h=20, x=0, y=0):
    return builders.corner_rectangle(sk, (x, y), (x + w, y + h))


def dim(sk, type_, refs, value=None, **data):
    c = Constraint(0, type_, refs, data=data)
    c.value = constraints.measure(sk, c) if value is None else value
    ok, msg = solver.try_add(sk, c)
    assert ok, msg
    return c


# ---- profiles -----------------------------------------------------------------------------

def test_rectangle_profile_and_relations():
    sk = Sketch()
    rect(sk)
    (r,) = sk.regions()
    assert r.area() == pytest.approx(800)
    assert sum(c.type in ("horizontal", "vertical") for c in sk.constraints) == 4


def test_nested_profiles_become_holes():
    sk = Sketch()
    rect(sk, 100, 50)
    sk.add_circle((20, 25), 5)
    builders.corner_rectangle(sk, (50, 10), (80, 40))
    sk.add_circle((65, 25), 3)
    regions = sorted(sk.regions(), key=lambda r: -r.area())
    assert len(regions) == 2
    assert len(regions[0].holes) == 2
    assert regions[0].area() == pytest.approx(5000 - math.pi * 25 - 900)


def test_slot_profile_area_is_exact():
    sk = Sketch()
    builders.straight_slot(sk, (0, 0), (40, 0), (10, 5))   # length 40, radius 5
    (r,) = sk.regions()
    assert r.area() == pytest.approx(40 * 10 + math.pi * 25, rel=1e-3)
    assert r.outer.signed_area() > 0


def test_construction_geometry_is_ignored_by_profiles():
    sk = Sketch()
    builders.center_rectangle(sk, (0, 0), (10, 5))      # has construction diagonals
    builders.centerline(sk, (-20, 0), (20, 0))
    (r,) = sk.regions()
    assert r.area() == pytest.approx(200)


def test_open_and_crossing_contours_are_reported():
    sk = Sketch()
    sk.add_line((0, 0), (10, 0))
    with pytest.raises(SketchError, match="open"):
        sk.regions()
    sk = Sketch()
    rect(sk, 10, 10)
    rect(sk, 10, 10, 5, 5)
    with pytest.raises(SketchError, match="cross"):
        sk.regions()


# ---- solver ------------------------------------------------------------------------------------

def test_fully_defined_rectangle():
    sk = Sketch()
    L = rect(sk)
    assert solver.analyze(sk).dof == 4
    dim(sk, "distance", [("e", L[0].id)], 50)          # width 40 -> 50
    dim(sk, "distance", [("e", L[1].id)], 30)          # height 20 -> 30
    dim(sk, "coincident", [("p", L[0].p1), ("p", 0)])  # corner on origin
    a = solver.analyze(sk)
    assert a.dof == 0 and a.fully_defined
    assert np.allclose(sk.pt(L[1].p2), [50, 30], atol=1e-7)


def test_redundant_and_conflicting_constraints_are_rejected():
    sk = Sketch()
    L = rect(sk)
    c = Constraint(0, "parallel", [("e", L[0].id), ("e", L[2].id)])
    ok, msg = solver.try_add(sk, c)          # both already horizontal
    assert not ok and "over-define" in msg
    dim(sk, "distance", [("e", L[0].id)], 40)
    c = Constraint(0, "distance", [("e", L[2].id)], 55.0)   # opposite side: conflicts
    ok, _ = solver.try_add(sk, c)
    assert not ok
    assert len(sk.constraints) == 5


def test_drag_keeps_relations():
    sk = Sketch()
    L = rect(sk)
    corner = L[1].p2
    res = solver.solve(sk, drag={corner: (60, 35)})
    assert res.ok
    assert np.allclose(sk.pt(corner), [60, 35], atol=1e-6)
    a, b = sk.pt(L[0].p1), sk.pt(L[0].p2)
    assert a[1] == pytest.approx(b[1])                     # still horizontal
    assert np.allclose(sk.pt(L[0].p1), [0, 0], atol=1e-6)  # far corner didn't move


def test_drag_respects_dimension():
    sk = Sketch()
    L = rect(sk)
    dim(sk, "distance", [("e", L[0].id)], 40)
    solver.solve(sk, drag={L[1].p2: (80, 35)})
    assert np.linalg.norm(sk.pt(L[0].p2) - sk.pt(L[0].p1)) == pytest.approx(40)
    assert sk.pt(L[1].p2)[1] == pytest.approx(35, abs=1e-6)  # height followed the mouse


def test_change_dimension_values():
    sk = Sketch()
    c = sk.add_circle((0, 0), 5)
    d = dim(sk, "diameter", [("e", c.id)])
    assert d.value == pytest.approx(10)
    ok, _ = solver.set_dimension(sk, d, 24)
    assert ok and c.r == pytest.approx(12)
    l1 = sk.add_line((0, 0), (10, 0))
    l2 = sk.add_line((0, 0), (10, 10))
    ang = Constraint(0, "angle", [("e", l1.id), ("e", l2.id)])
    ang.value = constraints.measure(sk, ang)
    assert math.degrees(ang.value) == pytest.approx(45)
    solver.try_add(sk, ang)
    solver.set_dimension(sk, ang, math.radians(30))
    d1 = sk.pt(l2.p2) - sk.pt(l2.p1)
    d0 = sk.pt(l1.p2) - sk.pt(l1.p1)
    assert math.degrees(math.acos(d0 @ d1 / np.linalg.norm(d0) / np.linalg.norm(d1))) == pytest.approx(30)


def test_tangent_and_concentric_relations():
    sk = Sketch()
    ln = sk.add_line((-20, 7), (20, 7))
    c = sk.add_circle((0, 0), 5)
    for rel in constraints.make_relation(sk, "tangent", [("e", ln.id), ("e", c.id)]):
        assert solver.try_add(sk, rel)[0]
    assert abs(sk.pt(ln.p1)[1] - sk.pt(c.center)[1]) == pytest.approx(c.r)
    c2 = sk.add_circle((3, 1), 2)
    for rel in constraints.make_relation(sk, "concentric", [("e", c.id), ("e", c2.id)]):
        assert solver.try_add(sk, rel)[0]
    assert np.allclose(sk.pt(c.center), sk.pt(c2.center))


def test_point_on_spline_holds_and_follows_the_spline():
    """Coincident point-on-spline is a real relation (as in SolidWorks), not a no-op."""
    from scipy.optimize import minimize_scalar
    from bauyrcad.core.sketch import model

    def gap(sk, sp, p):
        P = np.array([sk.pt(i) for i in sp.pts])
        samples = model.spline_eval(P, 2001)[0]
        k = int(np.argmin(np.linalg.norm(samples - p, axis=1)))
        lo, hi = max(k - 1, 0) / 2000, min(k + 1, 2000) / 2000
        from scipy.interpolate import CubicSpline
        cs = CubicSpline(model.spline_params(P), P, axis=0)
        return minimize_scalar(lambda u: np.linalg.norm(cs(u) - p), bounds=(lo, hi),
                               method="bounded", options={"xatol": 1e-12}).fun
    sk = Sketch()
    sp = sk.add_spline([(0, 0), (10, 8), (20, 2), (30, 9)])
    pe = sk.add_point_entity((12, 3))                        # off the curve
    for c in constraints.make_relation(sk, "coincident", [("p", pe.p), ("e", sp.id)]):
        assert solver.try_add(sk, c)[0]
    assert gap(sk, sp, sk.pt(pe.p)) < 1e-7
    solver.solve(sk, drag={sp.pts[1]: (10, 12)})             # the point follows the spline
    assert np.allclose(sk.pt(sp.pts[1]), (10, 12))
    assert gap(sk, sp, sk.pt(pe.p)) < 1e-7


def test_equal_between_a_line_and_a_circle_is_refused_as_in_solidworks():
    sk = Sketch()
    ln, c = sk.add_line((0, 0), (10, 0)), sk.add_circle((20, 0), 3)
    with pytest.raises(ValueError, match="Equal relates lines"):
        constraints.make_relation(sk, "equal", [("e", ln.id), ("e", c.id)])
    assert "equal" not in constraints.applicable_relations(sk, [("e", ln.id), ("e", c.id)])


def test_polygon_is_regular_and_stays_regular_when_dragged():
    sk = Sketch()
    ents = builders.polygon(sk, (0, 0), (10, 0), 6)
    lines = [e for e in ents if isinstance(e, Line)]
    solver.solve(sk, drag={lines[0].p1: (15, 3)})
    lens = [np.linalg.norm(sk.pt(l.p2) - sk.pt(l.p1)) for l in lines]
    assert np.ptp(lens) < 1e-6
    (r,) = sk.regions()
    assert r.area() == pytest.approx(3 * math.sqrt(3) / 2 * lens[0] ** 2, rel=1e-6)


# ---- builders ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("make,area", [
    (lambda sk: builders.center_rectangle(sk, (0, 0), (10, 5)), 200),
    (lambda sk: builders.three_point_rectangle(sk, (0, 0), (10, 10), (0, 20)), math.hypot(10, 10) * 10 * math.sqrt(2)),
    (lambda sk: builders.parallelogram(sk, (0, 0), (10, 0), (15, 5)), 50),
    (lambda sk: builders.perimeter_circle(sk, (1, 0), (0, 1), (-1, 0)), math.pi),
    (lambda sk: builders.center_slot(sk, (0, 0), (20, 0), (0, 4)), 40 * 8 + math.pi * 16),
])
def test_builder_areas(make, area):
    sk = Sketch()
    make(sk)
    assert sum(r.area() for r in sk.regions()) == pytest.approx(area, rel=1e-3)


def test_arcs_close_a_profile():
    sk = Sketch()
    a = sk.add_point(0, 0)
    b = sk.add_point(20, 0)
    sk.add_line(a, b)
    builders.three_point_arc(sk, b, a, (10, 10))          # semicircle on top
    (r,) = sk.regions()
    assert r.area() == pytest.approx(math.pi * 100 / 2, rel=1e-3)


def test_tangent_arc_is_tangent():
    sk = Sketch()
    ln = sk.add_line((0, 0), (10, 0))
    (arc,) = builders.tangent_arc(sk, ln.p2, (1, 0), (20, 10))
    c = sk.pt(arc.center)
    assert c[0] == pytest.approx(10) and c[1] == pytest.approx(10)


# ---- modify tools -----------------------------------------------------------------------------------

def test_trim_line_between_intersections():
    sk = Sketch()
    h = sk.add_line((0, 0), (30, 0))
    sk.add_line((10, -5), (10, 5))
    sk.add_line((20, -5), (20, 5))
    ops.trim(sk, h.id, (15, 0))
    lines = [e for e in sk.entities.values() if isinstance(e, Line)]
    horizontal = [l for l in lines if abs(sk.pt(l.p1)[1]) < 1e-9 and abs(sk.pt(l.p2)[1]) < 1e-9]
    spans = sorted(tuple(sorted((sk.pt(l.p1)[0], sk.pt(l.p2)[0]))) for l in horizontal)
    assert spans == [pytest.approx((0, 10)), pytest.approx((20, 30))]


def test_trim_two_overlapping_rectangles_to_one_profile():
    sk = Sketch()
    A = rect(sk, 20, 20)
    B = rect(sk, 20, 20, 10, 10)
    ops.trim(sk, A[1].id, (20, 15))      # right edge of A inside B
    ops.trim(sk, A[2].id, (15, 20))      # top edge of A inside B
    ops.trim(sk, B[0].id, (15, 10))      # bottom edge of B inside A
    ops.trim(sk, B[3].id, (10, 15))      # left edge of B inside A
    (r,) = sk.regions()
    assert r.area() == pytest.approx(700)


def test_trim_circle_to_arc():
    sk = Sketch()
    c = sk.add_circle((0, 0), 10)
    sk.add_line((0, -20), (0, 20))
    ops.trim(sk, c.id, (10, 0))                  # remove the right half
    arc = sk.entities[c.id]
    assert isinstance(arc, Arc)
    a0, sw = sk.arc_angles(arc)
    assert sw == pytest.approx(math.pi)
    assert sk.pt(arc.start)[0] == pytest.approx(0, abs=1e-9)


def test_extend_line_to_next_curve():
    sk = Sketch()
    ln = sk.add_line((0, 0), (5, 0))
    sk.add_line((12, -5), (12, 5))
    assert ops.extend(sk, ln.id, (5, 0))
    assert sk.pt(ln.p2)[0] == pytest.approx(12)


def test_fillet_corner():
    sk = Sketch()
    L = rect(sk, 40, 20)
    corner = L[0].p2                 # (40, 0)
    arc = ops.fillet(sk, corner, 5)
    assert solver.solve(sk).ok
    assert sk.radius(arc) == pytest.approx(5)
    (r,) = sk.regions()
    assert r.area() == pytest.approx(800 - (25 - math.pi * 25 / 4), rel=1e-4)


def test_chamfer_corner():
    sk = Sketch()
    L = rect(sk, 40, 20)
    ops.chamfer(sk, L[0].p2, 4)
    (r,) = sk.regions()
    assert r.area() == pytest.approx(800 - 8)


def test_offset_closed_rectangle_outwards():
    sk = Sketch()
    L = rect(sk, 40, 20)
    ops.offset(sk, [l.id for l in L], 5)
    regions = sorted(sk.regions(), key=lambda r: -r.area())
    assert regions[0].area() == pytest.approx(50 * 30 - 800)


def test_offset_circle_and_open_chain():
    sk = Sketch()
    c = sk.add_circle((0, 0), 10)
    (c2,) = ops.offset(sk, [c.id], -3)
    assert c2.r == pytest.approx(7)
    a, b, d = sk.add_point(0, 20), sk.add_point(10, 20), sk.add_point(10, 30)
    l1, l2 = sk.add_line(a, b), sk.add_line(b, d)
    out = ops.offset(sk, [l1.id, l2.id], 2)
    assert len(out) == 2
    assert np.allclose(sk.pt(out[0].p2), sk.pt(out[1].p1))


def test_mirror_connects_on_axis_and_adds_symmetry():
    sk = Sketch()
    axis = builders.centerline(sk, (0, -50), (0, 50))[0]
    p0, p1, p2 = sk.add_point(0, 0), sk.add_point(10, 0), sk.add_point(0, 10)
    ents = [sk.add_line(p0, p1), sk.add_line(p1, p2)]
    ops.mirror(sk, [e.id for e in ents], axis.id)
    (r,) = sk.regions()                  # mirrored halves close the triangle
    assert r.area() == pytest.approx(100)
    assert any(c.type == "symmetric" for c in sk.constraints)
    assert solver.solve(sk, drag={p1: (15, 0)}).ok
    ax = sk.pt(axis.p1)[0]              # the (unfixed) axis may move a little too
    assert sk.pt(axis.p2)[0] == pytest.approx(ax)
    mirrored = [p for p in sk.points if p not in (0, p0, p1, p2, axis.p1, axis.p2)]
    assert len(mirrored) == 1
    assert sk.pt(mirrored[0])[0] - ax == pytest.approx(-(sk.pt(p1)[0] - ax))


def test_patterns_and_transforms():
    sk = Sketch()
    c = sk.add_circle((10, 0), 2)
    ops.circular_pattern(sk, [c.id], (0, 0), 6)
    assert len(sk.regions()) == 6
    sk2 = Sketch()
    c = sk2.add_circle((0, 0), 1)
    ops.linear_pattern(sk2, [c.id], 0, 5, 4, 90, 5, 3)
    assert len(sk2.regions()) == 12
    ops.move(sk2, [c.id], 0, -10)
    assert np.allclose(sk2.pt(c.center), [0, -10])
    ops.rotate(sk2, [c.id], (0, 0), math.pi / 2)
    assert np.allclose(sk2.pt(c.center), [10, 0])


# ---- serialization -----------------------------------------------------------------------------------

def test_roundtrip_and_v1_migration():
    sk = Sketch()
    rect(sk)
    sk.add_circle((10, 10), 3)
    s2 = Sketch.from_dict(sk.to_dict())
    assert s2.to_dict() == sk.to_dict()
    v1 = {"next_id": 3, "entities": [{"type": "line", "id": 1, "a": [0, 0], "b": [5, 0]},
                                     {"type": "circle", "id": 2, "center": [9, 9], "radius": 2}]}
    m = Sketch.from_dict(v1)
    assert len(m.entities) == 2 and isinstance(list(m.entities.values())[1], Circle)
