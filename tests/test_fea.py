import numpy as np
import pytest

from bauyrcad.fea.analysis import AnalysisError, run_study
from bauyrcad.fea.solver import (TET10_NODES, assemble_stiffness, elasticity_matrix,
                                 rigid_body_modes, shape_derivatives)
from bauyrcad.fea.study import FaceRef, FixedFace, PointForce


def test_tet10_shape_function_derivatives_sum_to_zero():
    for xi in TET10_NODES:
        assert np.allclose(shape_derivatives(xi, 10).sum(axis=0), 0)


def test_element_stiffness_is_symmetric_and_has_rigid_body_null_space():
    nodes = TET10_NODES.astype(float) * 2.0
    K = assemble_stiffness(nodes, np.arange(10)[None, :], elasticity_matrix(1000.0, 0.25)).toarray()
    assert np.allclose(K, K.T)
    assert np.abs(K @ rigid_body_modes(nodes)).max() < 1e-9 * np.abs(K).max()


def _cantilever(doc, order=2, size=0.0):
    st = doc.add_study()
    root = next(f for f in doc.faces if f.is_planar and f.normal[2] < -0.9)
    st.fixtures.append(FixedFace(doc.new_bc_id(), "Fixed-1", [FaceRef.from_face(root)]))
    st.loads.append(PointForce(doc.new_bc_id(), "Force-1", [5, 5, 100], [0, -100, 0]))
    st.element_order, st.mesh_size = order, size
    return st


def test_cantilever_matches_beam_theory(beam_doc):
    st = _cantilever(beam_doc)
    res = run_study(st, beam_doc)
    tip = np.abs(res.mesh.nodes[:, 2] - 100) < 1e-6
    uy = -res.result.displacement[tip, 1].mean()
    E, nu, I, L, F, A = st.material.E, st.material.nu, 10 ** 4 / 12, 100, 100, 100
    timoshenko = F * L ** 3 / (3 * E * I) + F * L / (5 / 6 * E / (2 * (1 + nu)) * A)
    assert uy == pytest.approx(timoshenko, rel=0.02)
    # bending stress on the top fibre at mid span: sigma = M c / I
    mid = (np.abs(res.mesh.nodes[:, 2] - 50) < 3) & (np.abs(res.mesh.nodes[:, 1] - 10) < 1e-6)
    assert res.result.stress[mid, 2].mean() == pytest.approx(F * 50 * 5 / I, rel=0.03)
    # equilibrium: reactions balance the applied load
    assert np.allclose(res.result.reaction_total, [0, 100, 0], atol=1e-4)


@pytest.fixture
def no_pardiso(monkeypatch):
    """Pretend pypardiso is not installed (to test the fallback solvers)."""
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pypardiso":
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)


def test_iterative_solver_path_agrees(beam_doc, monkeypatch, no_pardiso):
    import bauyrcad.fea.solver as solver
    st = _cantilever(beam_doc)
    direct = run_study(st, beam_doc)
    monkeypatch.setattr(solver, "DIRECT_DOF_LIMIT", 0)
    iterative = run_study(st, beam_doc)
    assert "AMG" in iterative.result.method
    scale = np.abs(direct.result.displacement).max()
    assert np.allclose(iterative.result.displacement, direct.result.displacement, atol=1e-6 * scale)


def test_study_requires_fixture_and_load(beam_doc):
    st = beam_doc.add_study()
    with pytest.raises(AnalysisError, match="Fixed"):
        run_study(st, beam_doc)


def test_fixture_face_survives_regeneration(beam_doc):
    st = _cantilever(beam_doc)
    ref = st.fixtures[0].faces[0]
    beam_doc.features[1].depth = 50   # root face unchanged, still found after rebuild
    beam_doc.regenerate()
    assert ref.resolve(beam_doc.faces) is not None


def test_results_grid_contains_fields(beam_doc):
    res = run_study(_cantilever(beam_doc, size=4.0), beam_doc)
    grid = res.vtk_grid()
    assert grid.n_cells == res.mesh.n_elements
    for name in ("von Mises Stress", "Displacement Magnitude", "Factor of Safety"):
        assert name in grid.point_data
    assert res.summary()["min_fos"] > 0


# ---- solver choice and memory guard ---------------------------------------------------------
def _spd(n=300):
    import scipy.sparse as sp
    main = np.full(n, 4.0)
    off = np.full(n - 1, -1.0)
    return sp.diags([off, main, off], [-1, 0, 1]).tocsr(), np.ones(n)


def test_pardiso_is_used_and_matches_superlu():
    pytest.importorskip("pypardiso")
    from bauyrcad.fea import solver
    K, f = _spd()
    u, method = solver._solve(K, f)
    assert method == "PARDISO"
    assert np.allclose(u, solver._solve_direct(K, f))


def test_memory_guard_refuses_models_bigger_than_the_computer(monkeypatch):
    from bauyrcad.fea import solver
    K, f = _spd()
    monkeypatch.setattr(solver, "memory_gb", lambda: (0.5, 1.0))
    monkeypatch.setattr(solver, "direct_memory_gb", lambda n, nnz: 20.0)
    if pytest.importorskip("pypardiso"):
        with pytest.raises(solver.SolverError, match="Not enough memory.*20.0 GB.*mesh_control"):
            solver._solve(K, f)
    # plenty of memory: it solves
    monkeypatch.setattr(solver, "direct_memory_gb", lambda n, nnz: 0.01)
    assert solver._solve(K, f)[1] == "PARDISO"


def test_pardiso_out_of_memory_becomes_a_clear_error(monkeypatch):
    pypardiso = pytest.importorskip("pypardiso")
    from bauyrcad.fea import solver
    K, f = _spd()

    def oom(A, b):
        raise pypardiso.pardiso_wrapper.PyPardisoError(-2)
    monkeypatch.setattr(pypardiso, "spsolve", oom)
    with pytest.raises(solver.SolverError, match="Not enough memory"):
        solver._solve(K, f)


def test_no_endless_superlu_fallback_without_pardiso(monkeypatch, no_pardiso):
    from bauyrcad.fea import solver
    K, f = _spd()
    assert solver._solve(K, f)[1] == "SuperLU (direct)"          # small: still fine
    monkeypatch.setattr(solver, "SUPERLU_MAX_EQUATIONS", 100)
    monkeypatch.setattr(solver, "DIRECT_DOF_LIMIT", 10)
    with pytest.raises(solver.SolverError, match="pip install pypardiso"):
        solver._solve(K, f)                                      # no nodes -> no AMG either


def test_direct_memory_estimate_matches_measurements():
    from bauyrcad.fea import solver
    # measured PARDISO extra memory on tet10 plates (2026-09-28): 2.58 / 4.17 / 5.38 GB
    for n, nnz, gb in ((273654, 19232801, 2.58), (403476, 28616471, 4.17), (501879, 35749982, 5.38)):
        assert solver.direct_memory_gb(n, nnz) == pytest.approx(gb, rel=0.12)
