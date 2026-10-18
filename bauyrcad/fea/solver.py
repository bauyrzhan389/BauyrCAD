"""Linear elastic static solver for 4- and 10-node tetrahedra.

Displacement-based FEM, small strain, isotropic material:
    K u = F,   K = sum_e  int_Ve  B^T D B dV
Assembly is vectorised with numpy over element chunks; the sparse system is
solved with PARDISO (pypardiso, Intel MKL), falling back to AMG-CG or SciPy's SuperLU.
Stresses are recovered at element nodes and averaged to global nodes.

Strain / stress component order: xx, yy, zz, xy, yz, zx (engineering shear).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


class SolverError(Exception):
    pass


# Node natural coordinates, gmsh ordering (edges 01,12,20,03,23,13 for tet10).
TET10_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]
TET10_NODES = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                        [.5, 0, 0], [.5, .5, 0], [0, .5, 0], [0, 0, .5], [0, .5, .5], [.5, 0, .5]])
TET4_NODES = TET10_NODES[:4]

_a, _b = 0.5854101966249685, 0.1381966011250105
GAUSS_TET4PT = (np.array([[_b, _b, _b], [_a, _b, _b], [_b, _a, _b], [_b, _b, _a]]),
                np.full(4, 1.0 / 24.0))
GAUSS_TET1PT = (np.array([[0.25, 0.25, 0.25]]), np.array([1.0 / 6.0]))

_DL = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])  # dL_i / dxi


def shape_derivatives(xi, npe: int) -> np.ndarray:
    """dN/dxi at natural point xi -> (npe, 3)."""
    L = np.array([1 - xi[0] - xi[1] - xi[2], xi[0], xi[1], xi[2]])
    if npe == 4:
        return _DL.copy()
    dN = np.zeros((10, 3))
    for i in range(4):
        dN[i] = (4 * L[i] - 1) * _DL[i]
    for k, (i, j) in enumerate(TET10_EDGES):
        dN[4 + k] = 4 * (L[j] * _DL[i] + L[i] * _DL[j])
    return dN


def elasticity_matrix(E: float, nu: float) -> np.ndarray:
    lam = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[np.arange(3), np.arange(3)] = lam + 2 * mu
    D[np.arange(3, 6), np.arange(3, 6)] = mu
    return D


def _b_matrices(X: np.ndarray, dNxi: np.ndarray):
    """B matrices (m, 6, 3*npe) and det(J) (m,) for element coordinates X (m, npe, 3)."""
    J = np.einsum("ai,maj->mij", dNxi, X)
    det = np.linalg.det(J)
    if np.any(det <= 0):
        raise SolverError(f"{int(np.sum(det <= 0))} inverted or degenerate elements in mesh")
    dNx = np.einsum("mij,aj->mia", np.linalg.inv(J), dNxi)   # (m, 3, npe)
    m, npe = X.shape[0], X.shape[1]
    B = np.zeros((m, 6, 3 * npe))
    B[:, 0, 0::3] = dNx[:, 0]
    B[:, 1, 1::3] = dNx[:, 1]
    B[:, 2, 2::3] = dNx[:, 2]
    B[:, 3, 0::3] = dNx[:, 1]
    B[:, 3, 1::3] = dNx[:, 0]
    B[:, 4, 1::3] = dNx[:, 2]
    B[:, 4, 2::3] = dNx[:, 1]
    B[:, 5, 0::3] = dNx[:, 2]
    B[:, 5, 2::3] = dNx[:, 0]
    return B, det


def assemble_stiffness(nodes, elements, D, chunk: int = 4000) -> sp.csr_matrix:
    npe = elements.shape[1]
    ndof = 3 * len(nodes)
    gauss = GAUSS_TET4PT if npe == 10 else GAUSS_TET1PT
    dNs = [shape_derivatives(p, npe) for p in gauss[0]]
    K = sp.csr_matrix((ndof, ndof))
    for s in range(0, len(elements), chunk):
        el = elements[s:s + chunk]
        X = nodes[el]
        Ke = np.zeros((len(el), 3 * npe, 3 * npe))
        for dN, w in zip(dNs, gauss[1]):
            B, det = _b_matrices(X, dN)
            Ke += np.einsum("mki,kl,mlj->mij", B, D, B, optimize=True) * (w * det)[:, None, None]
        dofs = (3 * el[:, :, None] + np.arange(3)).reshape(len(el), -1)
        rows = np.repeat(dofs, 3 * npe, axis=1).ravel()
        cols = np.tile(dofs, (1, 3 * npe)).ravel()
        K = K + sp.csr_matrix((Ke.ravel(), (rows, cols)), shape=(ndof, ndof))
    return K


DIRECT_DOF_LIMIT = 30000   # above this SuperLU fill-in explodes; use AMG-CG instead


def rigid_body_modes(nodes: np.ndarray) -> np.ndarray:
    """The 6 rigid body modes (3 translations, 3 rotations) as (3N, 6) array."""
    X = nodes - nodes.mean(axis=0)
    B = np.zeros((3 * len(nodes), 6))
    for c in range(3):
        B[c::3, c] = 1.0
    B[0::3, 3], B[1::3, 3] = -X[:, 1], X[:, 0]
    B[1::3, 4], B[2::3, 4] = -X[:, 2], X[:, 1]
    B[0::3, 5], B[2::3, 5] = X[:, 2], -X[:, 0]
    return B


def _solve_direct(K, f):
    lu = spla.splu(K.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                   options={"SymmetricMode": True})
    return lu.solve(f)


SUPERLU_MAX_EQUATIONS = 150_000   # beyond this SuperLU takes many minutes and GBs of RAM
INSTALL_PARDISO = ("Install the PARDISO solver (it solves 500k equations in ~20 s): "
                   r".\.venv\Scripts\python.exe -m pip install pypardiso")


def memory_gb() -> tuple[float | None, float | None]:
    """(available, total) physical memory in GB, or None where unknown."""
    try:
        import ctypes
        if hasattr(ctypes, "windll"):
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return ms.ullAvailPhys / 2**30, ms.ullTotalPhys / 2**30
        with open("/proc/meminfo") as fh:
            info = {ln.split(":")[0]: float(ln.split()[1]) / 2**20 for ln in fh if ":" in ln}
        return info.get("MemAvailable"), info.get("MemTotal")
    except Exception:  # noqa: BLE001 - unknown platform: no check
        return None, None


def direct_memory_gb(n_eq: int, nnz: int) -> float:
    """PARDISO factorisation memory, measured on tet10 plates (145-165 bytes per nonzero of
    K at 270k-500k equations, growing slowly with size)."""
    return nnz * 145.0 * max(n_eq / 2.7e5, 1.0) ** 0.2 / 2**30


def _memory_message(n_eq, need, avail, total):
    have = f"{avail:.1f} GB free of {total:.1f} GB" if total else "not enough"
    return (f"Not enough memory for the solver: {n_eq:,} equations need about {need:.1f} GB, "
            f"this computer has {have}. Use a coarser global mesh and refine locally where "
            "stresses matter (st.mesh_control(faces, size)); close other programs; or use "
            "linear elements (order=1) for a first look.")


def _check_memory(n_eq, nnz, progress):
    need = direct_memory_gb(n_eq, nnz)
    avail, total = memory_gb()
    if total is not None and need > 0.9 * total:
        raise SolverError(_memory_message(n_eq, need, avail, total))
    if avail is not None and need > 0.9 * avail and progress:
        progress(f"Warning: the solver needs about {need:.1f} GB and {avail:.1f} GB is free - "
                 "close other programs, or it will be slow (swapping)")
    return need


def _solve(K, f, nodes=None, free=None, progress=None):
    """Solve the SPD system K u = f, choosing the fastest available method."""
    n = K.shape[0]
    try:
        import pypardiso  # Intel MKL PARDISO - by far the fastest here
    except ImportError:
        pypardiso = None
    if pypardiso is not None:
        need = _check_memory(n, K.nnz, progress)
        try:
            return pypardiso.spsolve(K.tocsr(), f), "PARDISO"
        except pypardiso.pardiso_wrapper.PyPardisoError as e:
            if "-2" in str(e):                          # PARDISO: not enough memory
                avail, total = memory_gb()
                raise SolverError(_memory_message(n, need, avail, total)) from None
            if progress:
                progress(f"PARDISO failed ({e}); trying the other solvers...")
    if n <= DIRECT_DOF_LIMIT or nodes is None:
        return _superlu(K, f), "SuperLU (direct)"
    try:
        import pyamg
        B = rigid_body_modes(nodes)[free]
        ml = pyamg.smoothed_aggregation_solver(
            K.tocsr(), B=B, symmetry="symmetric", strength=("symmetric", {"theta": 0.0}),
            max_coarse=500)
        residuals: list = []
        u = ml.solve(f, tol=1e-10, accel="cg", maxiter=1000, residuals=residuals)
        rel = np.linalg.norm(K @ u - f) / max(np.linalg.norm(f), 1e-300)
        if rel < 1e-7:
            return u, f"AMG-CG ({len(residuals)} iterations)"
        if n > SUPERLU_MAX_EQUATIONS:           # thin plates: AMG stalls, SuperLU would crawl
            raise SolverError(f"The iterative solver did not converge on {n:,} equations "
                              f"(thin walls do this). {INSTALL_PARDISO}")
        if progress:
            progress("Iterative solver did not converge, falling back to direct solver...")
        return _superlu(K, f), "SuperLU (direct; AMG-CG did not converge)"
    except ImportError:
        pass
    return _superlu(K, f), "SuperLU (direct)"


def _superlu(K, f):
    """SciPy's SuperLU, refused for systems it would take many minutes (and GBs) on."""
    n = K.shape[0]
    if n > SUPERLU_MAX_EQUATIONS:
        raise SolverError(f"{n:,} equations are too many for the fallback solver. {INSTALL_PARDISO}")
    try:
        return _solve_direct(K, f)
    except MemoryError:
        avail, total = memory_gb()
        raise SolverError(_memory_message(n, direct_memory_gb(n, K.nnz), avail, total)) from None


@dataclass
class StaticResult:
    displacement: np.ndarray    # (N, 3) mm
    stress: np.ndarray          # (N, 6) MPa, nodal averaged
    von_mises: np.ndarray       # (N,) MPa
    reaction_total: np.ndarray  # (3,) N, sum of support reactions
    n_dof: int
    method: str = ""


def von_mises(s: np.ndarray) -> np.ndarray:
    sx, sy, sz, txy, tyz, tzx = s.T
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                   + 3.0 * (txy ** 2 + tyz ** 2 + tzx ** 2))


def recover_stress(nodes, elements, u, D) -> np.ndarray:
    npe = elements.shape[1]
    natural = TET10_NODES if npe == 10 else TET4_NODES
    X = nodes[elements]
    ue = u.reshape(-1, 3)[elements].reshape(len(elements), -1)
    acc = np.zeros((len(nodes), 6))
    cnt = np.zeros(len(nodes))
    for a in range(npe):
        B, _ = _b_matrices(X, shape_derivatives(natural[a], npe))
        sig = np.einsum("mij,mj->mi", B, ue) @ D
        np.add.at(acc, elements[:, a], sig)
        np.add.at(cnt, elements[:, a], 1)
    return acc / np.maximum(cnt, 1)[:, None]


def solve_static(nodes: np.ndarray, elements: np.ndarray, E: float, nu: float,
                 fixed_nodes: np.ndarray, point_loads: list[tuple[int, np.ndarray]],
                 progress=None) -> StaticResult:
    n = len(nodes)
    ndof = 3 * n
    D = elasticity_matrix(E, nu)

    if progress:
        progress(f"Assembling stiffness ({len(elements)} elements, {ndof} DOF)...")
    K = assemble_stiffness(nodes, elements, D)

    F = np.zeros(ndof)
    for node, vec in point_loads:
        F[3 * node:3 * node + 3] += vec

    fixed = np.zeros(ndof, dtype=bool)
    fixed_nodes = np.asarray(fixed_nodes, dtype=np.int64)
    if fixed_nodes.size == 0:
        raise SolverError("The model is not restrained - add at least one fixture")
    for c in range(3):
        fixed[3 * fixed_nodes + c] = True
    free = ~fixed

    if progress:
        progress(f"Solving {int(free.sum())} equations...")
    Kff = K[free][:, free]
    u = np.zeros(ndof)
    u_free, method = _solve(Kff, F[free], nodes, free, progress)
    if not np.all(np.isfinite(u_free)):
        raise SolverError("Solver failed - the model may be insufficiently restrained")
    u[free] = u_free

    R = K @ u - F
    reaction_total = R.reshape(-1, 3)[fixed_nodes].sum(axis=0)

    if progress:
        progress("Recovering stresses...")
    stress = recover_stress(nodes, elements, u, D)
    return StaticResult(u.reshape(-1, 3), stress, von_mises(stress), reaction_total, ndof, method)
