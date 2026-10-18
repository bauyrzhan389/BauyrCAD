"""Run a Study on a Document: resolve BCs -> mesh -> solve -> result fields."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..core import geometry
from .mesher import Mesh, mesh_shape
from .solver import StaticResult, SolverError, solve_static
from .study import FaceForce, Material, PointForce, Pressure, Study

# Result field name -> (unit, description)
FIELDS = {
    "von Mises Stress": "MPa",
    "Displacement Magnitude": "mm",
    "Displacement X": "mm",
    "Displacement Y": "mm",
    "Displacement Z": "mm",
    "Max Principal Stress (P1)": "MPa",
    "Min Principal Stress (P3)": "MPa",
    "Normal Stress X": "MPa",
    "Normal Stress Y": "MPa",
    "Normal Stress Z": "MPa",
    "Shear Stress XY": "MPa",
    "Shear Stress YZ": "MPa",
    "Shear Stress ZX": "MPa",
    "Factor of Safety": "",
}


class AnalysisError(Exception):
    pass


@dataclass
class StudyResults:
    mesh: Mesh
    result: StaticResult
    material: Material
    applied_force: np.ndarray
    load_nodes: list = field(default_factory=list)
    solve_time: float = 0.0
    warnings: list = field(default_factory=list)
    _fields: dict = field(default_factory=dict)

    def field(self, name: str) -> np.ndarray:
        if name not in self._fields:
            r = self.result
            u, s = r.displacement, r.stress
            if name == "von Mises Stress":
                v = r.von_mises
            elif name == "Displacement Magnitude":
                v = np.linalg.norm(u, axis=1)
            elif name.startswith("Displacement "):
                v = u[:, "XYZ".index(name[-1])]
            elif name.startswith("Normal Stress "):
                v = s[:, "XYZ".index(name[-1])]
            elif name.startswith("Shear Stress "):
                v = s[:, 3 + ["XY", "YZ", "ZX"].index(name[-2:])]
            elif "Principal" in name:
                t = np.zeros((len(s), 3, 3))
                t[:, 0, 0], t[:, 1, 1], t[:, 2, 2] = s[:, 0], s[:, 1], s[:, 2]
                t[:, 0, 1] = t[:, 1, 0] = s[:, 3]
                t[:, 1, 2] = t[:, 2, 1] = s[:, 4]
                t[:, 0, 2] = t[:, 2, 0] = s[:, 5]
                ev = np.linalg.eigvalsh(t)
                v = ev[:, 2] if "P1" in name else ev[:, 0]
            elif name == "Factor of Safety":
                v = self.material.yield_strength / np.maximum(r.von_mises, 1e-12)
            else:
                raise KeyError(name)
            self._fields[name] = v
        return self._fields[name]

    # ---- post-processing helpers ------------------------------------------------------
    @property
    def element_size(self) -> float:
        """Mean corner-to-corner edge length of the tetrahedra."""
        c = self.mesh.nodes[self.mesh.elements[:, :4]]
        return float(np.mean(np.linalg.norm(c[:, 1] - c[:, 0], axis=1)))

    def exclusion_mask(self, radius=None) -> np.ndarray:
        """True for nodes within `radius` of a point-load node (singular zone)."""
        mask = np.zeros(self.mesh.n_nodes, dtype=bool)
        if not self.load_nodes:
            return mask
        r = 3.0 * self.element_size if radius is None else radius
        for n in self.load_nodes:
            mask |= np.linalg.norm(self.mesh.nodes - self.mesh.nodes[n], axis=1) < r
        return mask

    def probe(self, point, name="von Mises Stress"):
        """Value at the mesh node nearest to point -> (value, node_xyz, distance)."""
        d = np.linalg.norm(self.mesh.nodes - np.asarray(point, float), axis=1)
        i = int(np.argmin(d))
        return float(self.field(name)[i]), self.mesh.nodes[i].tolist(), float(d[i])

    def max_in(self, name="von Mises Stress", box=None, center=None, radius=None,
               nodes=None, exclude_point_loads=True):
        """Maximum of a field over a region -> (value, xyz).
        box=((x0,y0,z0),(x1,y1,z1)), or center+radius sphere, or explicit node indices.
        exclude_point_loads: True (3 element sizes), a radius, or False."""
        v = self.field(name)
        X = self.mesh.nodes
        m = np.ones(self.mesh.n_nodes, dtype=bool)
        if box is not None:
            lo, hi = np.asarray(box[0], float), np.asarray(box[1], float)
            m &= np.all((X >= lo - 1e-9) & (X <= hi + 1e-9), axis=1)
        if center is not None:
            m &= np.linalg.norm(X - np.asarray(center, float), axis=1) <= radius
        if nodes is not None:
            sel = np.zeros_like(m)
            sel[np.asarray(nodes, int)] = True
            m &= sel
        if exclude_point_loads is not False:
            m &= ~self.exclusion_mask(None if exclude_point_loads is True else exclude_point_loads)
        if not m.any():
            raise ValueError("The region contains no mesh nodes")
        i = int(np.argmax(np.where(m, v, -np.inf)))
        return float(v[i]), X[i].tolist()

    def mean_on_face(self, face_index, name="Displacement Magnitude") -> float:
        idx = self.mesh.face_nodes.get(face_index)
        if idx is None or not len(idx):
            raise ValueError(f"Face {face_index} is not in the mesh")
        return float(np.mean(self.field(name)[idx]))

    def summary(self) -> dict:
        vm = self.result.von_mises
        disp = self.field("Displacement Magnitude")
        out = {
            "nodes": self.mesh.n_nodes,
            "elements": self.mesh.n_elements,
            "dof": self.result.n_dof,
            "element_size": round(self.element_size, 4),
            "max_von_mises": float(vm.max()),
            "max_displacement": float(disp.max()),
            "min_fos": float(self.material.yield_strength / max(vm.max(), 1e-12)),
            "reaction": self.result.reaction_total.tolist(),
            "applied": self.applied_force.tolist(),
            "solve_time": self.solve_time,
            "method": self.result.method,
        }
        if self.load_nodes:
            # point loads are singular: also report the peak outside their neighbourhood
            r = 3.0 * self.element_size
            m = ~self.exclusion_mask(r)
            if m.any():
                vmax = float(vm[m].max())
                out["max_von_mises_away_from_point_loads"] = vmax
                out["min_fos_away_from_point_loads"] = float(
                    self.material.yield_strength / max(vmax, 1e-12))
                out["point_load_exclusion_radius"] = round(r, 4)
        return out

    def vtk_grid(self):
        """pyvista UnstructuredGrid of the mesh with all result fields."""
        import pyvista as pv
        el = self.mesh.elements
        if el.shape[1] == 10:
            cells_el = el[:, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]]   # gmsh -> VTK edge order
            ctype = pv.CellType.QUADRATIC_TETRA
        else:
            cells_el = el
            ctype = pv.CellType.TETRA
        n = cells_el.shape[1]
        cells = np.hstack([np.full((len(cells_el), 1), n), cells_el]).ravel()
        grid = pv.UnstructuredGrid(cells, np.full(len(cells_el), ctype, dtype=np.uint8),
                                   self.mesh.nodes)
        for name in FIELDS:
            grid.point_data[name] = self.field(name)
        grid.point_data["Displacement"] = self.result.displacement
        return grid


def _tet_face_lookup(elements):
    """sorted corner triple of every tet face -> the tet's opposite corner node."""
    c = elements[:, :4]
    table = {}
    for opp, (i, j, k) in ((3, (0, 1, 2)), (2, (0, 1, 3)), (1, (0, 2, 3)), (0, (1, 2, 3))):
        tri = np.sort(c[:, [i, j, k]], axis=1)
        for t, o in zip(map(tuple, tri.tolist()), c[:, opp].tolist()):
            table[t] = o
    return table


def surface_loads(mesh, face_ids, force=None, pressure=None, lookup=None):
    """Consistent nodal forces for a uniform load on faces -> ([(node, F)], total force).

    force: total force vector [N] spread over the faces by area.
    pressure: [MPa], positive pushing into the material (outward normal found per facet
    from the tetrahedron it belongs to, so curved faces work).
    Straight-sided tri6 facets: corners 0, mid-edge nodes 1/3 each; tri3: 1/3 per corner.
    """
    tris = [mesh.face_tris[i] for i in face_ids if i in mesh.face_tris]
    if not tris:
        raise AnalysisError("Loaded faces could not be matched to the mesh")
    T = np.vstack(tris)
    X = mesh.nodes
    a, b, c = X[T[:, 0]], X[T[:, 1]], X[T[:, 2]]
    nrm = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(nrm, axis=1)
    if force is not None:
        per = np.outer(area / area.sum(), np.asarray(force, float))
    else:
        lookup = lookup if lookup is not None else _tet_face_lookup(mesh.elements)
        unit = nrm / np.maximum(2 * area, 1e-300)[:, None]
        opp = np.array([lookup.get(tuple(sorted(t)), -1) for t in T[:, :3].tolist()])
        if (opp < 0).any():
            raise AnalysisError("Pressure face is not on the boundary of the mesh")
        points_inside = np.einsum("ij,ij->i", X[opp] - a, unit) > 0
        unit[points_inside] *= -1                        # now outward normals
        per = -float(pressure) * unit * area[:, None]    # positive pressure pushes inward
    cols = (3, 4, 5) if T.shape[1] == 6 else (0, 1, 2)
    idx = np.concatenate([T[:, k] for k in cols])
    val = np.vstack([per / 3.0] * 3)
    F = np.zeros((mesh.n_nodes, 3))
    np.add.at(F, idx, val)
    nodes = np.unique(idx)
    return [(int(n), F[n]) for n in nodes], per.sum(axis=0)


def _resolve(refs, faces, owner):
    out = []
    for ref in refs:
        idx = ref.resolve(faces)
        if idx is None:
            raise AnalysisError(f"{owner}: a referenced face no longer exists in the model")
        out.append(idx)
    return out


LARGE_MODEL_DOF = 300_000


@dataclass
class PreparedStudy:
    """Common mesh and boundary conditions for internal and external solvers."""
    mesh: Mesh
    fixed_nodes: np.ndarray
    loads: list
    applied_force: np.ndarray
    load_nodes: list
    warnings: list


def prepare_study(study: Study, doc, progress=None) -> PreparedStudy:
    if getattr(study, "interactions", None):
        raise AnalysisError("Contact interactions require a contact-capable solver; this linear static path cannot export or solve them")
    if study.element_order not in (1, 2):
        raise AnalysisError("Element order must be 1 (tet4) or 2 (tet10)")
    m = study.material
    if not np.isfinite([m.E, m.nu, m.density]).all() or m.E <= 0 or not -1 < m.nu < 0.5 or m.density < 0:
        raise AnalysisError("Material requires finite E > 0, -1 < nu < 0.5 and density >= 0")
    if not doc.has_body():
        raise AnalysisError("There is no model to analyse")
    warnings = []
    n_solids = geometry.count_solids(doc.body)
    if n_solids == 0:
        raise AnalysisError("The model has no solid body")
    if n_solids > 1:
        warnings.append(f"Model has {n_solids} separate solids; each must be restrained")
    if not study.fixtures:
        raise AnalysisError("Add at least one Fixed Face fixture")
    if not study.loads:
        raise AnalysisError("Add at least one load")

    faces = doc.faces
    fixed_faces = [i for fx in study.fixtures for i in _resolve(fx.faces, faces, fx.name)]
    local = [(_resolve(mc.faces, faces, "Mesh control"), mc.size) for mc in study.mesh_controls]

    order = study.element_order
    mesh = mesh_shape(doc.body, study.mesh_size or None, order, progress, local_sizes=local)
    ndof = 3 * mesh.n_nodes
    if ndof > LARGE_MODEL_DOF:
        msg = (f"Large model: {ndof:,} DOF - expect several GB of memory and minutes of solve "
               f"time; prefer local mesh controls over a finer global mesh")
        warnings.append(msg)
        if progress:
            progress(msg)

    fixed_nodes = np.unique(np.concatenate([np.zeros(0, int)] + [mesh.face_nodes.get(i, np.zeros(0, int))
                                            for i in fixed_faces]))
    if fixed_nodes.size == 0:
        raise AnalysisError("Fixed faces could not be matched to the mesh")

    surface_nodes = np.unique(np.concatenate(list(mesh.face_nodes.values())))
    # Point loads go to corner nodes only: a point force on a quadratic
    # element's mid-edge node is equivalent but less intuitive to inspect.
    candidates = surface_nodes
    if order == 2:
        corner = np.zeros(mesh.n_nodes, dtype=bool)
        corner[mesh.elements[:, :4].ravel()] = True
        candidates = surface_nodes[corner[surface_nodes]]
    loads, load_nodes = [], []
    applied = np.zeros(3)
    lo, hi = geometry.bounding_box(doc.body)
    diag = np.linalg.norm(hi - lo)
    lookup = None
    for ld in study.loads:
        if isinstance(ld, PointForce):
            d = np.linalg.norm(mesh.nodes[candidates] - np.asarray(ld.point), axis=1)
            k = int(np.argmin(d))
            if d[k] > 0.05 * diag:
                warnings.append(f"{ld.name}: nearest mesh node is {d[k]:.3g} mm from the load point")
            node = int(candidates[k])
            if node in load_nodes:
                warnings.append(f"{ld.name}: lands on the same mesh node as another point load")
            loads.append((node, np.asarray(ld.force, dtype=float)))
            load_nodes.append(node)
            applied += np.asarray(ld.force, dtype=float)
        elif isinstance(ld, FaceForce):
            nl, total = surface_loads(mesh, _resolve(ld.faces, faces, ld.name), force=ld.force)
            loads += nl
            applied += total
        elif isinstance(ld, Pressure):
            if lookup is None:
                lookup = _tet_face_lookup(mesh.elements)
            nl, total = surface_loads(mesh, _resolve(ld.faces, faces, ld.name),
                                      pressure=ld.pressure, lookup=lookup)
            loads += nl
            applied += total
        else:
            raise AnalysisError(f"Unsupported load type: {type(ld).__name__}")
    if load_nodes:
        warnings.append("Point loads give a singular stress peak under the load: judge stresses "
                        "with max_von_mises_away_from_point_loads, or use a face load")

    if any(not np.isfinite(f).all() for _, f in loads):
        raise AnalysisError("Loads must contain finite values")
    return PreparedStudy(mesh, fixed_nodes, loads, applied, load_nodes, warnings)


def run_study(study: Study, doc, progress=None) -> StudyResults:
    t0 = time.perf_counter()
    prepared = prepare_study(study, doc, progress)
    mesh, warnings = prepared.mesh, prepared.warnings
    try:
        res = solve_static(mesh.nodes, mesh.elements, study.material.E, study.material.nu,
                           prepared.fixed_nodes, prepared.loads, progress)
    except SolverError as e:
        raise AnalysisError(str(e)) from e
    if "did not converge" in res.method:
        warnings.append(f"Iterative solver did not converge; used {res.method}")
    return StudyResults(mesh, res, study.material, prepared.applied_force, prepared.load_nodes,
                        time.perf_counter() - t0, warnings)
