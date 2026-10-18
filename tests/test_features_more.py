"""Applied / advanced features: fillet, chamfer, shell, draft, hole wizard, patterns, mirror,
sweep, loft, boundary, rib, reference geometry, helix - checked against hand volumes, save /
reload, and following edits of earlier features."""

import math

import numpy as np
import pytest

from bauyrcad.api import ApiError, Part
from bauyrcad.core import geometry
from bauyrcad.core.document import Document


def plate(w=100, h=50, t=20):
    p = Part("plate")
    sk = p.sketch("front")
    sk.rect((0, 0), (w, h))
    p.extrude(sk, t)
    return p


V0 = 100 * 50 * 20


def close(a, b, rel=1e-6):
    return abs(a - b) <= rel * max(abs(b), 1.0)


def test_fillet_and_chamfer_volumes():
    p = plate()
    p.fillet([(50, 50, 20)], r=5)
    assert close(p.volume(), V0 - 100 * (25 - math.pi * 25 / 4))
    p = plate()
    p.chamfer((50, 50, 20), d=3)
    assert close(p.volume(), V0 - 100 * 4.5)
    p = plate()
    p.chamfer((50, 50, 20), d=3, d2=6)
    assert close(p.volume(), V0 - 100 * 9)
    p = plate()
    p.chamfer((50, 50, 20), d=4, angle=45)
    assert close(p.volume(), V0 - 100 * 8)


def test_fillet_all_edges_of_a_face_and_by_selector():
    p = plate()
    p.fillet(faces=p.face("+z"), r=2)
    n_edges_top = len(p.edges_where(kind="arc")) + len(p.edges_where(kind="circle"))
    assert p.volume() < V0 and n_edges_top >= 0
    p = plate()
    verticals = p.edges_where(direction="z")
    assert len(verticals) == 4
    p.fillet(verticals, r=10)
    assert close(p.volume(), V0 - 4 * 20 * (100 - math.pi * 100 / 4))


def test_fillet_too_large_is_a_clear_error():
    p = plate()
    with pytest.raises(ApiError, match="radius may be too large"):
        p.fillet((50, 50, 20), r=40)


def test_shell_open_closed_outward():
    p = plate()
    p.shell(2, remove=p.face("+z"))
    assert close(p.volume(), V0 - 96 * 46 * 18)
    p = plate()
    p.shell(2)
    assert close(p.volume(), V0 - 96 * 46 * 16)
    p = plate()
    p.shell(2, remove=p.face("+z"), outward=True)
    lo, hi = p.bbox()
    assert np.allclose(lo, [-2, -2, -2]) and np.allclose(hi, [102, 52, 20])


def test_draft_tapers_walls_towards_the_pull_direction():
    p = plate()
    sides = [p.face(n) for n in ("+x", "-x", "+y", "-y")]
    p.draft(sides, 5, neutral="front")
    # the base (z = 0) keeps its size, the top shrinks by 20 * tan(5 deg) per side
    top = p.face("+z")
    s = 20 * math.tan(math.radians(5))
    assert close(top.area, (100 - 2 * s) * (50 - 2 * s), 1e-6)
    lo, hi = p.bbox()
    assert np.allclose(hi, [100, 50, 20])


def test_hole_wizard_counterbore_tapped_countersink():
    p = plate()
    top = p.face("+z")
    h = p.hole(top, [(20, 25), (80, 25)], size="M6", kind="counterbore")
    cb = math.pi / 4 * (6.6 ** 2 * (20 - 6.4) + 11 ** 2 * 6.4) * 2
    assert close(p.volume(), V0 - cb, 1e-6)
    assert h.name == "CBORE for M6 Screw1"
    assert "Ø6.6 THRU" in h.callout() and "Ø11" in h.callout()
    t = p.hole(top, (50, 25), size="M5", kind="tapped", depth=10)
    assert t.dims()["d"] == 4.2 and t.callout().startswith("M5x0.8")
    before = p.volume()
    p.hole(top, (50, 10), size="M4", kind="countersink")
    assert p.volume() < before
    # a plain drilled hole: Ø8, 12 deep with a 118 deg point
    p = plate()
    p.hole(p.face("+z"), (50, 25), d=8, depth=12)
    tip = 4 / math.tan(math.radians(59))
    assert close(p.volume(), V0 - math.pi * 16 * 12 - math.pi * 16 * tip / 3, 1e-5)


def test_hole_on_the_far_side_flips_into_the_material():
    p = plate()
    p.hole("front", (50, 25), d=6)        # Front plane is the bottom face; the hole goes in
    assert close(p.volume(), V0 - math.pi * 9 * 20, 1e-6)


def test_linear_and_circular_patterns():
    p = plate()
    hole = p.sketch("front")
    hole.circle((10, 10), r=3)
    cut = p.cut(hole, through=True)
    p.pattern_linear(cut, "x", 20, 4, direction2="y", spacing2=30, count2=2)
    assert close(p.volume(), V0 - 8 * math.pi * 9 * 20)
    p = plate()
    h = p.hole(p.face("+z"), (70, 25), d=4)
    p.pattern_circular(h, axis=((50, 25, 0), (0, 0, 1)), count=6)
    assert close(p.volume(), V0 - 6 * math.pi * 4 * 20)
    # skipped instances
    p = plate()
    h = p.hole(p.face("+z"), (10, 10), d=4)
    p.pattern_linear(h, "x", 20, 4, skip=[(2, 0)])
    assert close(p.volume(), V0 - 3 * math.pi * 4 * 20)


def test_circular_pattern_about_a_cylinder_face():
    p = Part("flange")
    sk = p.sketch("front")
    sk.circle((0, 0), d=100)
    sk.circle((0, 0), d=30)
    p.extrude(sk, 10)
    h = p.hole(p.face("+z"), (35, 0), d=8)
    p.pattern_circular(h, axis=p.face(kind="cylinder", d=30), count=8)
    assert close(p.volume(), math.pi / 4 * (100 ** 2 - 30 ** 2) * 10 - 8 * math.pi * 16 * 10)


def test_mirror_features_and_body():
    p = plate()
    boss = p.sketch("front")
    boss.rect((10, 0), (20, 10))
    b = p.extrude(boss, 30)
    p.mirror(b, "right")
    assert close(p.volume(), V0 + 10 * 10 * 10 + 10 * 10 * 30)
    p = plate()
    p.mirror(plane="right")
    assert close(p.volume(), 2 * V0)
    lo, hi = p.bbox()
    assert np.allclose(lo[0], -100)


def test_sweeps_circular_profile_sketch_profile_and_spring():
    p = Part("pipe")
    path = p.sketch("top")
    path.polyline([(0, 0), (50, 0), (50, 40)])
    p.sweep(path, d=6, inner_d=4)
    assert close(p.volume(), math.pi / 4 * (36 - 16) * 90, 1e-6)   # mitred corner: exact
    s = Part("spring")
    hx = s.helix(center=(0, 0, 0), axis="y", d=30, pitch=8, turns=4)
    s.sweep(hx, d=3)
    L = 4 * math.hypot(math.pi * 30, 8)
    assert close(s.volume(), math.pi / 4 * 9 * L, 5e-3)
    q = Part("bar")
    path = q.sketch("top")
    path.line((0, 0), (0, 60))
    prof = q.sketch("front")
    prof.rect((-5, -3), (5, 3))
    q.sweep(path, profile=prof)
    assert close(q.volume(), 60 * 60)


def test_loft_boundary_and_loft_to_point():
    p = Part("loft")
    a = p.sketch("top")
    a.rect((-20, -20), (20, 20))
    b = p.sketch(p.plane("top", offset=30))
    b.rect((-10, -10), (10, 10))
    p.loft([a, b])
    frustum = 30 / 3 * (40 ** 2 + 20 ** 2 + 40 * 20)
    assert close(p.volume(), frustum, 1e-6)
    q = Part("pyramid")
    a = q.sketch("top")
    a.rect((-20, -20), (20, 20))
    q.loft([a, (0, 30, 0)])
    assert close(q.volume(), 40 * 40 * 30 / 3)
    r = Part("boundary")
    a = r.sketch("top")
    a.circle((0, 0), d=40)
    b = r.sketch("top", offset=50)
    b.circle((0, 0), d=20)
    r.boundary([a, b])
    assert r.volume() > math.pi * 100 * 50


def test_rib_gusset_in_an_l_bracket():
    p = Part("bracket")
    sk = p.sketch("front")
    sk.polyline([(0, 0), (60, 0), (60, 8), (8, 8), (8, 60), (0, 60)], close=True)
    p.extrude(sk, 40)
    rib = p.sketch("front", offset=20)
    rib.line((8, 40), (40, 8))
    p.rib(rib, 6)
    base = (60 * 8 + 52 * 8) * 40
    assert close(p.volume(), base + 0.5 * 32 * 32 * 6)


def test_reference_plane_moves_its_sketch_and_axis_from_planes():
    p = Part("ref")
    pl = p.plane("top", offset=25)
    sk = p.sketch(pl)
    sk.rect((0, 0), (10, 10))
    p.extrude(sk, 5)
    assert np.allclose(p.bbox()[0], [0, 25, -10])
    pl.distance = 40
    p._dirty = True
    assert np.allclose(p.bbox()[0], [0, 40, -10])
    ax = p.axis(planes=("front", "top"))
    assert np.allclose(np.abs(ax.result_axis[1]), [1, 0, 0])
    q = Part("angled")
    q.plane("front", angle=30, axis="y")
    assert q.doc.features[-1].result_plane is not None


def test_sketch_on_a_face_follows_the_face():
    p = plate(t=20)
    pocket = p.sketch(on=p.face("+z"))
    pocket.rect((20, 10), (40, 30))
    p.cut(pocket, 5)
    base = p.doc.features[1]
    base.depth = 30
    p._dirty = True
    lo, hi = p.bbox()
    assert close(hi[2], 30)
    # the pocket moved up with the top face: volume = 30-thick plate minus the pocket
    assert close(p.volume(), 100 * 50 * 30 - 20 * 20 * 5)


def test_features_follow_an_edit_and_survive_save_load(tmp_path):
    p = plate()
    p.fillet((50, 50, 20), r=5)
    p.shell(2, remove=p.face("-z"))
    p.hole(p.face("+z"), (50, 25), size="M6", kind="tapped", depth=8)
    v = p.volume()
    path = tmp_path / "f.bcad"
    p.save(path)
    d = Document.load(path)
    assert not [f.error for f in d.features if f.error]
    assert close(geometry.volume(d.body), v)
    d.features[1].depth = 30
    d.regenerate()
    assert not [f.error for f in d.features if f.error]
    assert geometry.volume(d.body) > v


def test_pattern_of_a_fillet_explains_what_to_do():
    p = plate()
    f = p.fillet((50, 50, 20), r=3)
    with pytest.raises(ApiError, match="pattern the body instead"):
        p.pattern_linear(f, "x", 10, 2)


def test_convert_entities_face_outline_and_projection():
    p = Part("c")
    sk = p.sketch("front")
    sk.rect((0, 0), (100, 50))
    sk.circle((70, 25), d=20)
    p.extrude(sk, 10)
    p.fillet(p.edges_where(direction="z"), r=8)
    top = p.face("+z")
    area = top.area
    s2 = p.sketch(on=top)
    made = s2.convert(face=top)
    assert sorted(e.kind for e in made) == ["arc"] * 4 + ["circle"] + ["line"] * 4
    st = s2.status()
    assert st["fully_defined"] and st["profiles"] == "1 profile(s), 1 hole(s)"
    p.extrude(s2, 5)
    assert close(p.volume(), area * 15)
    # edges above one another project once
    q = Part("d")
    sk = q.sketch("top")
    sk.rect((0, 0), (60, 40))
    q.extrude(sk, 20)
    s3 = q.sketch("front", offset=50)
    s3.convert(edges=q.edges_where(direction="x") + q.edges_where(direction="y"))
    assert s3.status()["contours"][0]["area"] == pytest.approx(1200)


def test_sketch_text_engraves_and_round_trips(tmp_path):
    p = Part("tag")
    sk = p.sketch("front")
    sk.rect((0, 0), (80, 30))
    p.extrude(sk, 5)
    tx = p.sketch(on=p.face("+z"))
    tx.text("M6 A", at=(8, 10), height=8)
    assert len(tx.status()["contours"]) == 3       # M, 6, A (with its counter as a hole)
    p.cut(tx, 0.6)
    v = p.volume()
    assert 80 * 30 * 5 - 200 < v < 80 * 30 * 5 - 20
    lo, hi = geometry.bounding_box(p.doc.body)
    p.save(tmp_path / "t.bcad")
    d = Document.load(tmp_path / "t.bcad")
    assert close(geometry.volume(d.body), v)
    assert d.features[-2].sketch.texts[0]["text"] == "M6 A"


def cup():
    p = Part("cup")
    sk = p.sketch("front")
    sk.rect((0, 0), (40, 40))
    p.extrude(sk, 30)
    p.shell(3, remove=p.face("+z"))
    return p


def test_intersect_removes_regions_and_fills_voids():
    p = cup()
    regs = p.regions(p.plane("front", offset=25))
    assert [r["material"] for r in regs] == [True, True, False]
    assert [round(r["volume"]) for r in regs] == [14568, 2220, 25432]
    p = cup()
    p.intersect(p.plane("front", offset=25), exclude=[(20, 1.5, 27.5)])
    assert close(p.volume(), 14568)
    p = cup()
    p.intersect(p.plane("front", offset=25), fill=[(20, 20, 10)])
    assert close(p.volume(), 14568 + 2220 + 25432)


def test_wrap_emboss_deboss_on_a_cylinder():
    R = 20
    p = Part("cyl")
    sk = p.sketch("top")
    sk.circle((0, 0), r=R)
    p.extrude(sk, 40)
    s2 = p.sketch("front", offset=R)
    s2.rect((-10, 10), (10, 20))
    v0 = p.volume()
    p.wrap(s2, p.face(kind="cylinder"), depth=2)
    assert close(p.volume() - v0, 10 * 20 / R * ((R + 2) ** 2 - R ** 2) / 2, 1e-6)
    q = Part("cyl2")
    sk = q.sketch("top")
    sk.circle((0, 0), r=R)
    q.extrude(sk, 40)
    s3 = q.sketch("front", offset=R)
    s3.rect((-10, 10), (10, 20))
    v0 = q.volume()
    q.wrap(s3, q.face(kind="cylinder"), depth=2, deboss=True)
    assert close(v0 - q.volume(), 10 * 20 / R * (R ** 2 - (R - 2) ** 2) / 2, 1e-6)
    with pytest.raises(ApiError, match="parallel to the cylinder axis"):
        s4 = q.sketch("top", offset=45)
        s4.rect((0, 0), (5, 5))
        q.wrap(s4, q.face(kind="cylinder", at=(20, 5, 0)), depth=1)


def test_repair_sketch_closes_gaps_and_removes_duplicates():
    from bauyrcad.core.sketch import Sketch, ops
    sk = Sketch()
    a, b, c, d = (0, 0), (50, 0), (50, 30), (0, 30.02)        # 0.02 gap at the last corner
    sk.add_line(a, b)
    sk.add_line(b, c)
    sk.add_line(c, d)
    sk.add_line((0, 30), (0, 0))
    sk.add_line((0, 0), (50, 0))                               # a duplicate on top of the first
    out = ops.repair(sk)
    assert out["gaps_closed"] >= 1 and out["duplicates"] == 1
    regions = sk.regions()
    assert len(regions) == 1 and len(sk.entities) == 4
