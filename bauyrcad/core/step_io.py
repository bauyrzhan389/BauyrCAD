"""STEP (ISO 10303) import and export."""

from __future__ import annotations

import os
import tempfile

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.TopAbs import TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from . import geometry


class StepError(Exception):
    pass


def read_step(path: str):
    """Read a STEP file and return a single (compound) shape in millimetres."""
    reader = STEPControl_Reader()
    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise StepError(f"Could not read STEP file: {path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if geometry.is_null(shape):
        raise StepError("STEP file contains no geometry")
    shape = geometry.fix_shape(shape)
    if geometry.count_solids(shape) == 0:
        shape = _try_make_solid(shape)
    return shape


def read_step_text(text: str):
    fd, path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return read_step(path)
    finally:
        os.remove(path)


def _try_make_solid(shape):
    """Surface-only STEP files: sew the faces and close them into a solid."""
    sew = BRepBuilderAPI_Sewing(1e-3)
    sew.Add(shape)
    sew.Perform()
    sewn = sew.SewedShape()
    exp = TopExp_Explorer(sewn, TopAbs_SHELL)
    solids = []
    while exp.More():
        mk = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(exp.Current()))
        if mk.IsDone():
            solids.append(mk.Solid())
        exp.Next()
    if not solids:
        return shape
    return solids[0] if len(solids) == 1 else geometry.compound(solids)


def write_step(shape, path: str, schema: str = "AP214") -> None:
    if geometry.is_null(shape):
        raise StepError("Nothing to export - the model is empty")
    Interface_Static.SetCVal_s("write.step.schema", schema)
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise StepError(f"Could not write STEP file: {path}")
