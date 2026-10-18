"""Motion studies (driving mates), interference detection, cut direction, and drawing
fixes found while building the mini robot arm (projects/mini_roboarm)."""
import math

import numpy as np
import pytest

from bauyrcad.api import ApiError, Assembly, Drawing, Part
from bauyrcad.core import geometry


@pytest.fixture
def hinge(tmp_path):
    base = Part("base")
    s = base.sketch("front")
    s.rect((-20, -20), (20, 20))
    s.circle((0, 0), d=10)
    base.extrude(s, 10)                                   # block z 0..10 with a bore
    base.save(tmp_path / "base.bcad")
    link = Part("link")
    s = link.sketch("front")
    s.slot((0, 0), (60, 0), 16)
    s.circle((0, 0), d=10)
    link.extrude(s, 5)                                    # lever, bore at its origin
    link.save(tmp_path / "link.bcad")
    asm = Assembly("hinge")
    b = asm.add(tmp_path / "base.bcad")
    lk = asm.add(tmp_path / "link.bcad", at=(0, 0, 10))
    asm.mate("concentric", lk.face(kind="cylinder", d=10), b.face(kind="cylinder", d=10))
    asm.mate("coincident", lk.face("-z"), b.face("+z"))
    ang = asm.mate("angle", b.face("+y"), lk.face("+y"), 0)
    ang.name = "Swing"
    return asm, b, lk, ang


def link_angle(lk):
    d = lk.pose.R @ np.array([1.0, 0, 0])
    return math.degrees(math.atan2(d[1], d[0]))


def test_drive_angle_mate_moves_the_mechanism(hinge):
    asm, b, lk, ang = hinge
    assert asm.dof()["link<1>"] == 0
    asm.set("Swing", 90)                                  # big step: walked in small steps
    a90 = link_angle(lk)
    assert abs(abs(a90) - 90) < 1e-5
    asm.set(ang, 135)
    assert abs(abs(link_angle(lk)) - 135) < 1e-5
    assert np.sign(link_angle(lk)) == np.sign(a90)       # kept turning the same way
    assert np.allclose(lk.world((0, 0, 0)), [0, 0, 10], atol=1e-6)   # still on the pin


def test_drive_rejects_non_driving_mates(hinge):
    asm, *_ = hinge
    with pytest.raises(ApiError):
        asm.set(asm.asm.mates[0].name, 5)                 # concentric


def test_motion_keyframes_and_gif(hinge, tmp_path):
    asm, b, lk, ang = hinge
    poses = asm.motion([{"Swing": 0}, {"Swing": 60}, {"Swing": 30}], frames=5)
    assert len(poses) == 1 + 2 * 5
    tip = [p[lk.comp.id].point((60, 0, 0)) for p in poses]
    assert np.allclose(np.linalg.norm(np.array(tip)[:, :2], axis=1), 60, atol=1e-6)
    assert abs(abs(link_angle(lk)) - 30) < 1e-5         # left at the last keyframe
    out = tmp_path / "hinge.gif"
    asm.animate(out, [{"Swing": 0}, {"Swing": 45}], frames=3, size=(200, 150),
                trace=(lk, (60, 0, 0)))
    from PIL import Image
    im = Image.open(out)
    assert im.n_frames == 4 and im.size == (200, 150)


def test_suppressed_driver_frees_the_joint(hinge):
    asm, b, lk, ang = hinge
    assert asm.mobility() == 0
    asm.suppress(ang)
    assert asm.dof()["link<1>"] == 1 and asm.mobility() == 1
    asm.suppress(ang, False)
    assert asm.dof()["link<1>"] == 0


def test_interference_overlap_and_touching(tmp_path):
    cube = Part("cube")
    s = cube.sketch("front")
    s.rect((0, 0), (10, 10))
    cube.extrude(s, 10)
    cube.save(tmp_path / "cube.bcad")
    asm = Assembly()
    asm.add(tmp_path / "cube.bcad")
    c2 = asm.add(tmp_path / "cube.bcad", at=(10, 0, 0))   # face to face: touching
    assert asm.interference() == []
    c2.move(-4, 0, 0)                                     # 4 mm overlap
    hits = asm.interference()
    assert len(hits) == 1 and hits[0]["volume"] == pytest.approx(400, rel=1e-6)
    assert asm.interference(ignore=[("cube<1>", "cube<2>")]) == []


def test_shaft_in_same_size_bore_is_not_interference(hinge):
    asm, *_ = hinge
    assert asm.interference(min_volume=1e-3) == []


# ---- cut direction (IMPROVEMENTS 2026-09-27) ------------------------------------------------

def _block():
    p = Part("b")
    s = p.sketch("front")
    s.rect((0, 0), (40, 40))
    p.extrude(s, 10)                                      # z 0..10: the Front plane is its back
    return p


def test_through_cut_on_back_plane_flips_into_the_material():
    p = _block()
    v0 = p.volume()
    s = p.sketch("front")
    s.circle((20, 20), d=10)
    p.cut(s, through=True)
    assert v0 - p.volume() == pytest.approx(math.pi * 25 * 10, rel=1e-6)


def test_blind_cut_flips_too_and_a_miss_is_an_error():
    p = _block()
    v0 = p.volume()
    s = p.sketch("front")
    s.circle((20, 20), d=10)
    p.cut(s, 4)
    assert v0 - p.volume() == pytest.approx(math.pi * 25 * 4, rel=1e-6)
    miss = p.sketch("front")
    miss.circle((200, 200), d=5)
    with pytest.raises(ApiError, match="does not intersect"):
        p.cut(miss, through=True)


# ---- drawings -------------------------------------------------------------------------------

@pytest.fixture
def slot_file(tmp_path):
    p = Part("link")
    s = p.sketch("front")
    s.slot((0, 0), (100, 0), 20)
    s.circle((100, 0), d=5)
    p.extrude(s, 8)
    p.save(tmp_path / "link.bcad")
    return tmp_path / "link.bcad"


def test_overall_size_over_rounded_ends_is_exact(slot_file):
    dr = Drawing(slot_file, sheet="A4")
    dims = dr.auto_dimension("Front")
    texts = [d["text"] for d in dr.info()["dims"]]
    assert "120" in texts and "20" in texts


def test_radius_of_an_arc_and_dim_at(slot_file):
    dr = Drawing(slot_file, sheet="A4")
    f = dr.view("Front")
    r = dr.radius(f, near=(110, 0, 0))                    # the rounded end, not the 5 mm hole
    assert r.r == pytest.approx(10)
    d = dr.dim(f, (0, 0, 0), (100, 0, 0), at=(50, 25, 0))
    assert d.pos[1] == pytest.approx(25)
    info = {x["id"]: x for x in dr.info()["dims"]}
    assert info[d.id]["value"] == pytest.approx(100)


def test_clearance_and_sweep(hinge, tmp_path):
    """A link swinging towards a post: exact clearance, touching vs interfering, and the
    closest frame of a motion."""
    asm, b, lk, ang = hinge
    post = Part("post")
    s = post.sketch("front")
    s.circle((0, 0), d=10)
    post.extrude(s, 30)
    post.save(tmp_path / "post.bcad")
    p = asm.add(tmp_path / "post.bcad", at=(45, 40, 0), fixed=True)
    # link at 0 deg spans y -8..8; the post surface is at y = 35 above x = 45: 27 mm
    c = asm.clearance(lk, p)
    assert c["distance"] == pytest.approx(27.0, abs=1e-6) and c["overlap"] == 0
    assert c["point_b"][1] == pytest.approx(35.0, abs=1e-6)
    # nearest anything: the base it sits on (touching faces are not interference)
    near = asm.clearance(lk)
    assert near["b"] == "base<1>" and near["distance"] < 1e-9 and near["overlap"] < 1e-6
    assert asm.clearance(p)["b"] in ("base<1>", "link<1>")
    # swing 0 -> 90 deg: it hits the post at ~41.6 deg (atan(40/45))
    sweep = asm.clearance_sweep(lk, p, keyframes=[{"Swing": 0}, {"Swing": 90}], frames=18)
    assert sweep["distance"] < 1e-9 and sweep["overlap"] > 1.0
    assert len(sweep["distances"]) == 19 and sweep["distances"][0] == pytest.approx(27.0, abs=1e-6)
    # the pose is restored after the sweep (to where motion() left it: the last keyframe)
    assert abs(abs(link_angle(lk)) - 90) < 1e-5
    with pytest.raises(ApiError):
        asm.clearance_sweep(lk, p)


def test_assembly_render_uses_one_edge_actor(hinge, tmp_path, monkeypatch):
    """Vendor STEPs have thousands of edges; one VTK actor per edge made a 67-part render take
    over 10 minutes. All edges must go into a single line mesh."""
    import pyvista as pv
    asm, *_ = hinge
    calls = []
    real = pv.Plotter.add_mesh

    def counting(self, mesh, *a, **k):
        calls.append(type(mesh).__name__)
        return real(self, mesh, *a, **k)
    monkeypatch.setattr(pv.Plotter, "add_mesh", counting)
    n_edges = sum(len(c.tessellation.edge_polylines) for c in asm.asm.components)
    assert n_edges > 10
    out = asm.render(tmp_path / "hinge.png")
    assert (tmp_path / "hinge.png").stat().st_size > 10000
    assert len(calls) == len(asm.asm.components) + 1        # one surface per part + all edges
