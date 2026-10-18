"""Assemblies: mates, DOF analysis, dragging, persistence and export."""
import math

import numpy as np
import pytest

from bauyrcad.api import ApiError, Assembly, Part
from bauyrcad.core import geometry
from bauyrcad.core.document import Document


@pytest.fixture
def parts(tmp_path):
    base = Part("base")
    s = base.sketch("front")
    s.rect((0, 0), (60, 40))
    s.circle((30, 20), d=10)
    base.extrude(s, 10)                                   # plate z 0..10 with a bore
    base.save(tmp_path / "base.bcad")
    pin = Part("pin")
    s = pin.sketch("front")
    s.circle((0, 0), d=10)
    pin.extrude(s, 40)                                    # pin along +z, z 0..40
    pin.save(tmp_path / "pin.bcad")
    link = Part("link")
    s = link.sketch("front")
    s.rect((-8, -8), (50, 8))
    s.circle((0, 0), d=10)
    link.extrude(s, 6)                                    # a lever with a bore at its origin
    link.save(tmp_path / "link.bcad")
    return tmp_path


def pin_axis(asm, pin):
    return pin.world((0, 0, 0)), pin.pose.R @ np.array([0, 0, 1.0])


def test_concentric_and_coincident_pin(parts):
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    pin = asm.add(parts / "pin.bcad", at=(100, 30, 5), rotate=("x", 35))
    assert base.comp.fixed and not pin.comp.fixed
    assert asm.dof() == {"base<1>": 0, "pin<1>": 6}

    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    p, a = pin_axis(asm, pin)
    assert abs(abs(a[2]) - 1) < 1e-9                      # axis along Z
    assert np.allclose(p[:2], [30, 20], atol=1e-7)        # on the bore axis
    assert asm.dof()["pin<1>"] == 2                       # slide + spin

    asm.mate("coincident", pin.face("-z"), base.face("+z"))
    p, a = pin_axis(asm, pin)
    assert np.allclose(p, [30, 20, 10], atol=1e-7) and a[2] > 0.999
    assert asm.dof()["pin<1>"] == 1                       # only spin about its axis

    # dragging can only spin it: the axis point stays put
    edge_local = np.array([5.0, 0, 20])
    before = pin.world(edge_local)
    target = before + np.array([30.0, 40.0, 25.0])
    assert asm.asm.drag_to(pin.comp.id, edge_local, target).ok
    p2, a2 = pin_axis(asm, pin)
    assert np.allclose(p2, [30, 20, 10], atol=1e-6) and a2[2] > 0.999999
    after = pin.world(edge_local)
    assert after[2] == pytest.approx(before[2], abs=1e-6)                 # no axial motion
    assert np.linalg.norm(after[:2] - [30, 20]) == pytest.approx(5, abs=1e-6)  # still on the pin
    assert not np.allclose(after, before, atol=1e-3)                      # but it did spin


def test_redundant_and_conflicting_mates_are_rejected(parts):
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    pin = asm.add(parts / "pin.bcad", at=(90, 0, 0))
    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    with pytest.raises(ApiError, match="implied|over"):
        asm.mate("parallel", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    assert len(asm.asm.mates) == 1


def test_distance_angle_and_lock(parts):
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    pin = asm.add(parts / "pin.bcad", at=(90, 0, 0))
    link = asm.add(parts / "link.bcad", at=(0, 90, 30))
    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    asm.mate("coincident", pin.face("-z"), base.face("+z"))
    asm.mate("concentric", link.face(kind="cylinder"), pin.face(kind="cylinder"))
    asm.mate("distance", link.face("-z"), base.face("+z"), 5.0)          # 5 mm gap
    assert link.world((0, 0, 0))[2] == pytest.approx(15.0, abs=1e-6)
    assert asm.dof()["link<1>"] == 1                                      # swings about the pin
    asm.mate("angle", link.face("+y"), base.face("+y"), 30)
    ny = link.pose.R @ np.array([0, 1.0, 0])
    assert math.degrees(math.acos(np.clip(ny @ [0, 1, 0], -1, 1))) == pytest.approx(30, abs=1e-5)
    assert asm.dof()["link<1>"] == 0
    asm.mate("lock", pin, base)
    assert all(v == 0 for v in asm.dof().values())


def test_save_load_export(parts):
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    pin = asm.add(parts / "pin.bcad", at=(90, 0, 0))
    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    asm.mate("coincident", pin.face("-z"), base.face("+z"))
    path = asm.save(parts / "sub" / "hinge.bcasm") if False else asm.save(parts / "hinge.bcasm")
    text = path.read_text()
    assert '"source": "base.bcad"' in text                                 # relative, portable
    again = Assembly.open(path)
    assert [c.name for c in again.components] == ["base<1>", "pin<1>"]
    assert np.allclose(again.component("pin<1>").pose.t, pin.pose.t)
    assert again.dof()["pin<1>"] == 1
    out = asm.export(parts / "hinge.step")
    d = Document()
    d.import_file(out)
    d.regenerate()
    v_base = 60 * 40 * 10 - math.pi * 25 * 10
    v_pin = math.pi * 25 * 40
    assert geometry.volume(d.body) == pytest.approx(v_base + v_pin - math.pi * 25 * 0, rel=1e-5)
    info = asm.info()
    assert info["components"][1]["dof"] == 1 and info["mates"][0]["type"] == "concentric"


def test_part_edits_propagate_and_render(parts, tmp_path):
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    pin = asm.add(parts / "pin.bcad", at=(90, 0, 0))
    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    asm.mate("coincident", pin.face("-z"), base.face("+z"))
    asm.save(parts / "a.bcasm")
    # make the base thicker: the pin must follow the moved top face after reload
    doc = Document.load(parts / "base.bcad")
    doc.features[1].depth = 16
    doc.regenerate()
    doc.save(parts / "base.bcad")
    assert asm.asm.reload().ok
    assert pin.world((0, 0, 0))[2] == pytest.approx(16.0, abs=1e-6)
    png = asm.render(tmp_path / "asm.png")
    assert png.stat().st_size > 20000


def test_translation_only_drag(parts):
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    link = asm.add(parts / "link.bcad", at=(100, 0, 20))
    R0 = link.pose.R.copy()
    assert asm.asm.drag_to(link.comp.id, (0, 0, 0), (120, 30, 20), allow_rotation=False).ok
    assert np.allclose(link.pose.R, R0)
    assert np.allclose(link.world((0, 0, 0)), [120, 30, 20], atol=1e-6)
    # on a pin, a translation-only drag sideways can't move it; along the pin it can
    pin = asm.add(parts / "pin.bcad", at=(-60, 0, 0))
    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    asm.mate("coincident", pin.face("-z"), base.face("+z"))
    asm.mate("concentric", link.face(kind="cylinder"), pin.face(kind="cylinder"))
    before = link.world((0, 0, 0))
    asm.asm.drag_to(link.comp.id, (0, 0, 0), before + [15, 15, 0], allow_rotation=False)
    assert np.allclose(link.world((0, 0, 0))[:2], [30, 20], atol=1e-6)   # stays on the pin
    asm.asm.drag_to(link.comp.id, (0, 0, 0), link.world((0, 0, 0)) + [0, 0, 7], allow_rotation=False)
    assert link.world((0, 0, 0))[2] == pytest.approx(before[2] + 7, abs=1e-3)  # slides up


def test_step_export_keeps_assembly_structure(parts):
    """SolidWorks opens the STEP as an assembly: one product per part, placed instances."""
    from bauyrcad.core import exchange
    asm = Assembly()
    base = asm.add(parts / "base.bcad")
    pin = asm.add(parts / "pin.bcad", at=(90, 0, 0))
    asm.add(parts / "pin.bcad", at=(90, 40, 0), rotate=("x", 30))
    asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder"))
    asm.save(parts / "hinge.bcasm")
    for fmt in ("step214", "step242"):
        out = parts / f"hinge_{fmt}.step"
        asm.asm.export(out, fmt)
        st = exchange.read_step_structure(out)
        assert st["name"] == "hinge" and st["parts"] == ["base", "pin"]
        assert [(p, n) for p, n, _ in st["instances"]] == [
            ("base", "base<1>"), ("pin", "pin<1>"), ("pin", "pin<2>")]
        for (_, _, M), c in zip(st["instances"], asm.asm.components):
            assert np.allclose(M, c.pose.matrix, atol=1e-9)
        shape = exchange.read(out)
        assert geometry.count_solids(shape) == 3
        v = 60 * 40 * 10 - math.pi * 25 * 10 + 2 * math.pi * 25 * 40
        assert geometry.volume(shape) == pytest.approx(v, rel=1e-6)


def test_missing_part_file_falls_back_to_its_step(tmp_path):
    from bauyrcad.api import Assembly, Part
    from bauyrcad.core.assembly import Assembly as CoreAssembly
    a = Part("a")
    sk = a.sketch("front")
    sk.rect((0, 0), (20, 10))
    a.extrude(sk, 5)
    a.save(tmp_path / "a.bcad")
    a.export(tmp_path / "a.step")
    b = Part("b")
    sk = b.sketch("front")
    sk.circle((0, 0), d=8)
    b.extrude(sk, 10)
    b.save(tmp_path / "b.bcad")
    asm = Assembly()
    asm.add(tmp_path / "a.bcad")
    asm.add(tmp_path / "b.bcad")
    asm.save(tmp_path / "x.bcasm")
    (tmp_path / "a.bcad").unlink()                 # lost in a copy; its STEP is still there
    (tmp_path / "b.bcad").unlink()                 # lost with no STEP
    core = CoreAssembly.load(tmp_path / "x.bcasm")
    ca, cb = core.components
    assert ca.shape is not None and ca.load_error is None and "a.step" in ca.load_warning
    assert cb.shape is None and cb.load_error == "b.bcad: file not found"
