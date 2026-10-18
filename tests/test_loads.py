"""Distributed loads, local mesh control and robust post-processing (validated vs theory)."""
import numpy as np
import pytest

from bauyrcad.api import ApiError, Part
from bauyrcad.core.document import Document
from bauyrcad.fea.study import MATERIAL_LIBRARY, Material

E = 210000.0


def bar(length=100.0):
    part = Part()
    s = part.sketch("front")
    s.rect((0, 0), (10, 10))
    part.extrude(s, length)
    return part


def test_face_force_uniform_tension():
    part = bar()
    st = part.study(material="S235JR (EN 10025)")
    st.fix(part.face("-z"))
    st.face_force(part.face("+z"), (0, 0, 1000))
    s = st.run()
    assert s["applied"] == pytest.approx([0, 0, 1000])
    assert s["reaction"] == pytest.approx([0, 0, -1000], abs=1e-6)
    sz, _, _ = st.probe((5, 5, 50), "Normal Stress Z")
    assert sz == pytest.approx(10.0, rel=0.01)                       # F / A
    dz = st.mean_on(part.face("+z"), "Displacement Z")
    assert dz == pytest.approx(1000 * 100 / (E * 100), rel=0.03)     # F L / (E A), clamped root
    assert "max_von_mises_away_from_point_loads" not in s            # no point loads


def test_pressure_on_end_face_compresses():
    part = bar()
    st = part.study()
    st.fix(part.face("-z"))
    st.pressure(part.face("+z"), 10.0)                                # pushes into the material
    s = st.run()
    assert s["applied"] == pytest.approx([0, 0, -1000], rel=1e-9)
    assert st.probe((5, 5, 50), "Normal Stress Z")[0] == pytest.approx(-10.0, rel=0.01)


def test_internal_pressure_matches_lame():
    a, b, p = 10.0, 20.0, 10.0
    part = Part()
    s = part.sketch("front")
    s.circle((0, 0), r=b)
    s.circle((0, 0), r=a)
    part.extrude(s, 40)
    bore = part.face(kind="cylinder", d=2 * a)
    st = part.study(material="Plain Carbon Steel")
    st.fix(part.face("-z"))
    st.pressure(bore, p)
    st.mesh(size=2.5)
    res = st.run()
    assert np.linalg.norm(res["applied"]) < 1e-6 * p * 2 * np.pi * a * 40   # closed ring: ~0 net
    hoop, xyz, _ = st.probe((0, a, 30), "Normal Stress X")          # at +y the hoop stress is sigma_x
    lame = p * (b ** 2 + a ** 2) / (b ** 2 - a ** 2)
    assert hoop == pytest.approx(lame, rel=0.04)
    radial = st.probe((0, a, 30), "Normal Stress Y")[0]
    assert radial == pytest.approx(-p, rel=0.08)


def test_cantilever_tip_face_force_matches_beam_theory():
    part = bar()
    st = part.study()
    st.fix(part.face("-z"))
    st.face_force(part.face("+z"), (0, -100, 0))
    st.run()
    uy = -st.mean_on(part.face("+z"), "Displacement Y")
    m = MATERIAL_LIBRARY["Plain Carbon Steel"]
    I, L = 10 ** 4 / 12, 100
    timo = 100 * L ** 3 / (3 * m.E * I) + 100 * L / (5 / 6 * m.E / (2 * (1 + m.nu)) * 100)
    assert uy == pytest.approx(timo, rel=0.02)


def test_local_mesh_control_refines_near_the_face():
    def run(control):
        part = Part()
        s = part.sketch("front")
        s.rect((0, 0), (60, 20))
        s.circle((30, 10), d=8)
        part.extrude(s, 5)
        st = part.study()
        st.fix(part.face("-x"))
        st.face_force(part.face("+x"), (0, -100, 0))
        st.mesh(size=4.0)
        if control:
            st.mesh_control(part.holes(d=8), 0.6)
        st.run()
        return st
    coarse, fine = run(False), run(True)
    near = lambda st: np.abs(np.linalg.norm(st.nodes[:, :2] - [30, 10], axis=1) - 4) < 0.05  # noqa: E731
    assert near(fine).sum() > 4 * near(coarse).sum()
    assert fine.results.mesh.n_nodes < 6 * coarse.results.mesh.n_nodes   # refinement stays local


def test_point_load_summary_excludes_singularity():
    part = bar()
    st = part.study()
    st.fix(part.face("-z"))
    st.force((5, 10, 100), (0, -100, 0))
    s = st.run()
    assert s["max_von_mises_away_from_point_loads"] < s["max_von_mises"]
    assert s["min_fos_away_from_point_loads"] > s["min_fos"]
    assert any("singular" in w for w in s["warnings"])
    v, xyz = st.max_in(box=((0, 0, 40), (10, 10, 60)))                # mid-span only
    assert xyz[2] == pytest.approx(40, abs=2)                          # peak at the root side
    assert v == pytest.approx(100 * 60 * 5 / (10 ** 4 / 12), rel=0.05)  # M c / I at z = 40


def test_face_filters():
    part = Part()
    s = part.sketch()
    s.rect((0, 0), (40, 20))
    s.circle((10, 10), d=9)
    s.circle((30, 10), d=6)
    part.extrude(s, 5)
    assert len(part.holes()) == 2 and len(part.holes(d=9)) == 1
    assert part.face(kind="cylinder", d=6).diameter == pytest.approx(6)
    with pytest.raises(ApiError, match="add at="):
        part.face(kind="cylinder")
    assert part.face(kind="cylinder", at=(10, 14.5, 2)).diameter == pytest.approx(9)
    assert {f.kind for f in part.faces_where(normal="+z")} == {"plane"}


def test_new_loads_materials_and_controls_roundtrip(tmp_path):
    part = bar()
    st = part.study(material="09G2S / 09Г2С (GOST 19281)")
    st.fix(part.face("-z"))
    st.face_force(part.face("+z"), (0, -50, 0))
    st.pressure(part.face("+y"), 0.5)
    st.mesh_control(part.face("-z"), 1.5)
    path = part.save(tmp_path / "loads.bcad")
    doc = Document.load(path)
    s2 = doc.studies[0]
    assert [type(x).__name__ for x in s2.loads] == ["FaceForce", "Pressure"]
    assert s2.loads[1].pressure == 0.5 and s2.mesh_controls[0].size == 1.5
    assert s2.material.ultimate_strength == 490.0
    # files from before 'ultimate_strength' existed still load
    assert Material.from_dict({"name": "x", "E": 1.0, "nu": 0.3, "yield_strength": 1.0,
                               "density": 1.0}).ultimate_strength == 0.0
