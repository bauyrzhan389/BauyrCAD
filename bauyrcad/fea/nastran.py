"""MSC Nastran SOL 101 export. No solver installation is needed to write a deck.

Pressure and face forces use the same equivalent nodal loads as BauyrCAD's
linear solver. Coordinates: mm; force: N; stress: MPa; mass: tonne; time: s.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

import numpy as np

from .analysis import AnalysisError, prepare_study


def _field(value):
    if value is None:
        return " " * 16
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            raise AnalysisError("Nastran fields must be finite")
        for precision in range(10, 6, -1):
            text = f"{value:.{precision}E}"
            if len(text) <= 16:
                return text.rjust(16)
        raise AnalysisError("Value cannot be represented in a Nastran field")
    text = str(value)
    if len(text) > 16:
        raise AnalysisError("Nastran field exceeds 16 characters")
    return text.rjust(16)


def _card(name, *values):
    """Large fixed-field records, padded to pairs of physical lines."""
    values = list(values)
    values += [None] * ((-len(values)) % 8)
    lines = []
    for start in range(0, len(values), 4):
        prefix = name + "*" if start == 0 else "*"
        lines.append(prefix.ljust(8) + "".join(_field(v) for v in values[start:start + 4]))
    return "\n".join(lines) + "\n"


def write_bdf(prepared, material, path, title="BauyrCAD static study"):
    """Atomically write one prepared linear-static load case; return a summary."""
    mesh = prepared.mesh
    nodes, elements = mesh.nodes, mesh.elements
    if nodes.ndim != 2 or nodes.shape[1] != 3 or not np.isfinite(nodes).all():
        raise AnalysisError("Invalid mesh coordinates")
    if elements.ndim != 2 or elements.shape[1] not in (4, 10) or not len(elements):
        raise AnalysisError("Nastran export supports nonempty tet4/tet10 meshes")
    if elements.min() < 0 or elements.max() >= len(nodes):
        raise AnalysisError("Mesh contains invalid node indices")
    forces = np.zeros_like(nodes, dtype=float)
    for node, force in prepared.loads:
        forces[node] += force
    if not np.isfinite(forces).all():
        raise AnalysisError("Invalid nodal forces")
    # Gmsh edges: 12,23,31,14,34,24; Nastran: 12,23,31,14,24,34.
    if elements.shape[1] == 10:
        elements = elements[:, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]]
    title = " ".join(str(title).split()).encode("ascii", "replace").decode()[:60]
    path = Path(path)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", newline="\n",
                                         dir=path.parent, prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temp = stream.name
            stream.write("$ BauyrCAD - MSC Nastran linear static export\n"
                         "$ Units: mm, N, MPa, tonne, s. MAT1 density converted from kg/m^3.\n"
                         "$ Face forces/pressure mapped to consistent equivalent nodal FORCEs.\n"
                         "$ No contact, friction, large deformation or nonlinear materials.\n"
                         "SOL 101\nCEND\n")
            stream.write(f"TITLE = {title}\nECHO = NONE\nSUBCASE 1\n  SPC = 1\n  LOAD = 2\n"
                         "  DISPLACEMENT(PRINT) = ALL\n  SPCFORCES(PRINT) = ALL\n"
                         "  STRESS(PRINT) = ALL\nBEGIN BULK\n")
            stream.write(_card("PARAM", "AUTOSPC", "NO"))
            stream.write(_card("MAT1", 1, float(material.E), None, float(material.nu),
                               float(material.density) * 1e-12))
            stream.write(_card("PSOLID", 1, 1))
            for nid, xyz in enumerate(nodes, 1):
                stream.write(_card("GRID", nid, None, *map(float, xyz)))
            for eid, conn in enumerate(elements, 1):
                stream.write(_card("CTETRA", eid, 1, *(int(n) + 1 for n in conn)))
            # Solid elements have translation DOFs only. Explicitly suppress unused
            # rotations so AUTOSPC cannot silently stabilize a free translation.
            stream.write(_card("SPC1", 1, 456, 1, "THRU", len(nodes)))
            fixed = np.unique(prepared.fixed_nodes)
            for start in range(0, len(fixed), 6):
                stream.write(_card("SPC1", 1, 123, *(int(n) + 1 for n in fixed[start:start + 6])))
            nonzero = np.flatnonzero(np.any(forces != 0, axis=1))
            for n in nonzero:
                magnitude = float(np.linalg.norm(forces[n]))
                stream.write(_card("FORCE", 2, int(n) + 1, 0, magnitude,
                                   *map(float, forces[n] / magnitude)))
            if not len(nonzero):
                raise AnalysisError("The study has no nonzero nodal loads")
            stream.write("ENDDATA\n")
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            os.unlink(temp)
    return {"path": str(path), "solution": 101, "nodes": len(nodes), "elements": len(elements),
            "fixed_nodes": len(fixed), "loaded_nodes": len(nonzero),
            "applied_force": forces.sum(axis=0).tolist(),
            "applied_moment": np.cross(nodes, forces).sum(axis=0).tolist(),
            "warnings": list(prepared.warnings)}


def export_bdf(study, doc, path, progress=None):
    """Mesh the current setup and export without running either solver."""
    prepared = prepare_study(study, doc, progress)
    if progress:
        progress("Writing Nastran BDF...")
    return write_bdf(prepared, study.material, path, study.name)
