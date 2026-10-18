"""Tetrahedral meshing of B-rep solids with Gmsh.

The solid is handed to Gmsh as a BREP file. Gmsh re-numbers faces, so model
faces are matched to Gmsh surfaces by centroid and area; this gives, for
each CAD face, the set of mesh nodes lying on it (used for fixtures).
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field

import numpy as np

from ..core import geometry

_GMSH_LOCK = threading.Lock()  # gmsh is a global singleton - one mesh at a time


class MeshError(Exception):
    pass


@dataclass
class Mesh:
    nodes: np.ndarray                  # (N, 3)
    elements: np.ndarray               # (M, 10) or (M, 4) node indices, gmsh ordering
    order: int
    face_nodes: dict = field(default_factory=dict)   # CAD face index -> node index array
    face_tris: dict = field(default_factory=dict)    # CAD face index -> (k, 6|3) surface elements

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_elements(self) -> int:
        return len(self.elements)


def auto_mesh_size(shape) -> float:
    lo, hi = geometry.bounding_box(shape)
    ext = np.sort(hi - lo)
    # Driven by the two smaller dimensions so thin parts still get a few layers.
    return float(max(min(ext[1] / 4.0, np.linalg.norm(hi - lo) / 25.0), 1e-3))


def mesh_shape(shape, size: float | None = None, order: int = 2, progress=None,
               local_sizes=()) -> Mesh:
    """Tetrahedral mesh of a solid.

    local_sizes: [(cad_face_indices, element_size), ...] - finer (or coarser) elements on
    those faces, blending smoothly into the global size (Gmsh distance/threshold fields).
    """
    import gmsh

    if geometry.count_solids(shape) == 0:
        raise MeshError("The model has no solid body to mesh")
    size = size or auto_mesh_size(shape)
    faces = geometry.face_infos(shape)

    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    with _GMSH_LOCK:
        try:
            geometry.write_brep(shape, path)
            gmsh.initialize(interruptible=False)
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("part")
            gmsh.model.occ.importShapes(path)
            gmsh.model.occ.synchronize()

            # Match CAD faces to gmsh surfaces (gmsh renumbers them on import).
            surfs = [(tag, np.array(gmsh.model.occ.getCenterOfMass(dim, tag)),
                      gmsh.model.occ.getMass(dim, tag)) for dim, tag in gmsh.model.getEntities(2)]
            face_tag = {}
            for f in faces:
                best, best_err = None, np.inf
                for tag, c, a in surfs:
                    err = np.linalg.norm(c - f.centroid) + abs(a - f.area) / max(f.area, 1e-12)
                    if err < best_err:
                        best, best_err = tag, err
                if best is not None:
                    face_tag[f.index] = best

            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size / 20.0)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 16)
            gmsh.option.setNumber("Mesh.ElementOrder", order)
            gmsh.option.setNumber("Mesh.HighOrderOptimize", 1 if order == 2 else 0)
            gmsh.option.setNumber("Mesh.Optimize", 1)
            fields = []
            for face_ids, local in local_sizes:
                tags = [face_tag[i] for i in face_ids if i in face_tag]
                if not tags or local <= 0:
                    continue
                dist = gmsh.model.mesh.field.add("Distance")
                gmsh.model.mesh.field.setNumbers(dist, "SurfacesList", tags)
                gmsh.model.mesh.field.setNumber(dist, "Sampling", 60)
                thr = gmsh.model.mesh.field.add("Threshold")
                gmsh.model.mesh.field.setNumber(thr, "InField", dist)
                gmsh.model.mesh.field.setNumber(thr, "SizeMin", float(local))
                gmsh.model.mesh.field.setNumber(thr, "SizeMax", float(size))
                gmsh.model.mesh.field.setNumber(thr, "DistMin", float(local))
                gmsh.model.mesh.field.setNumber(thr, "DistMax", float(local) + 4.0 * float(size))
                fields.append(thr)
                gmsh.option.setNumber("Mesh.MeshSizeMin", min(size, local) / 20.0)
            if fields:
                mn = gmsh.model.mesh.field.add("Min")
                gmsh.model.mesh.field.setNumbers(mn, "FieldsList", fields)
                gmsh.model.mesh.field.setAsBackgroundMesh(mn)
            if progress:
                progress(f"Meshing (element size {size:.3g} mm"
                         + (f", {len(fields)} local size control(s)" if fields else "") + ")...")
            gmsh.model.mesh.generate(3)

            tags, coords, _ = gmsh.model.mesh.getNodes()
            tags = tags.astype(np.int64)
            index = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
            index[tags] = np.arange(len(tags))
            nodes = coords.reshape(-1, 3)

            etype = 11 if order == 2 else 4
            npe = 10 if order == 2 else 4
            elems = []
            for dim, tag in gmsh.model.getEntities(3):
                types, _, enodes = gmsh.model.mesh.getElements(dim, tag)
                for t, en in zip(types, enodes):
                    if t == etype:
                        elems.append(index[en.astype(np.int64)].reshape(-1, npe))
            if not elems:
                raise MeshError("Gmsh produced no volume elements")
            elements = np.vstack(elems)

            ttype, tpe = (9, 6) if order == 2 else (2, 3)       # tri6 / tri3 surface elements
            face_nodes, face_tris = {}, {}
            for fi, tag in face_tag.items():
                ntags, _, _ = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True)
                face_nodes[fi] = np.unique(index[ntags.astype(np.int64)])
                types, _, enodes = gmsh.model.mesh.getElements(2, tag)
                for t, en in zip(types, enodes):
                    if t == ttype:
                        face_tris[fi] = index[en.astype(np.int64)].reshape(-1, tpe)
        except MeshError:
            raise
        except Exception as e:
            raise MeshError(f"Meshing failed: {e}") from e
        finally:
            if gmsh.isInitialized():
                gmsh.finalize()
            os.remove(path)

    # Drop nodes not used by volume elements (e.g. from lower-dim entities).
    used = np.unique(elements)
    if len(used) != len(nodes):
        remap = np.full(len(nodes), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        nodes = nodes[used]
        elements = remap[elements]
        face_nodes = {k: remap[v][remap[v] >= 0] for k, v in face_nodes.items()}
        face_tris = {k: remap[v] for k, v in face_tris.items()}
    return Mesh(nodes, elements, order, face_nodes, face_tris)
