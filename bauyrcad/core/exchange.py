"""Neutral CAD / mesh file formats: export and import.

Exact B-rep formats (keep the solid): STEP AP203/AP214/AP242, IGES, BREP.
Mesh formats (triangles only, for 3D printing / rendering): STL, OBJ, glTF (.glb), VRML.

SolidWorks 2024 parts can be written offline as exact Imported solids, or experimentally
as native sketch/extrude trees when a parametric document is supplied.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import geometry


class ExchangeError(Exception):
    pass


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extensions: tuple
    exact: bool          # B-rep (True) or triangle mesh (False)

    @property
    def filter(self) -> str:
        return f"{self.label} ({' '.join('*' + e for e in self.extensions)})"


EXPORT_FORMATS = [
    Format("step214", "STEP AP214", (".step", ".stp"), True),
    Format("step242", "STEP AP242", (".step", ".stp"), True),
    Format("step203", "STEP AP203", (".step", ".stp"), True),
    Format("iges", "IGES", (".igs", ".iges"), True),
    Format("xt", "Parasolid (SolidWorks, NX, Solid Edge)", (".x_t",), True),
    Format("sldprt", "SolidWorks 2024 part, exact solid (no SolidWorks needed)", (".sldprt",), True),
    Format("sldprt_features", "SolidWorks 2024 editable feature tree (no SolidWorks needed)", (".sldprt",), True),
    Format("brep", "OpenCASCADE BREP", (".brep",), True),
    Format("stl", "STL (binary)", (".stl",), False),
    Format("stl_ascii", "STL (ASCII)", (".stl",), False),
    Format("obj", "Wavefront OBJ", (".obj",), False),
    Format("glb", "glTF binary", (".glb",), False),
    Format("wrl", "VRML", (".wrl",), False),
]
# formats only an assembly writes (Assembly.export; a part's export has no .sldasm)
SLDASM = Format("sldasm", "SolidWorks 2024 assembly + its parts, exact solids (no SolidWorks "
                "needed)", (".sldasm",), True)
ASSEMBLY_FORMATS = [SLDASM]
ASSEMBLY_EXPORT_FORMATS = EXPORT_FORMATS[:5] + ASSEMBLY_FORMATS + EXPORT_FORMATS[5:]   # after .x_t
ASSEMBLY_EXPORT_FORMATS = [f for f in ASSEMBLY_EXPORT_FORMATS if f.key != 'sldprt_features']
IMPORT_FORMATS = [
    Format("step", "STEP", (".step", ".stp"), True),
    Format("iges", "IGES", (".igs", ".iges"), True),
    Format("brep", "OpenCASCADE BREP", (".brep", ".brp"), True),
]
FORMATS = {f.key: f for f in EXPORT_FORMATS}

_STEP_SCHEMA = {"step214": "AP214IS", "step242": "AP242DIS", "step203": "AP203"}


def export_format_for(path) -> Format:
    ext = Path(path).suffix.lower()
    for f in EXPORT_FORMATS:
        if ext in f.extensions:
            return f
    raise ExchangeError(f"Unsupported export format {ext!r}")


def import_format_for(path) -> Format:
    ext = Path(path).suffix.lower()
    for f in IMPORT_FORMATS:
        if ext in f.extensions:
            return f
    raise ExchangeError(f"Unsupported import format {ext!r}")


def import_filter() -> str:
    exts = " ".join("*" + e for f in IMPORT_FORMATS for e in f.extensions)
    return ";;".join([f"CAD files ({exts})"] + [f.filter for f in IMPORT_FORMATS])


# ----------------------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------------------

def _mesh(shape, quality: float):
    lo, hi = geometry.bounding_box(shape)
    defl = max(float(np.linalg.norm(hi - lo)), 1.0) * quality
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, defl, False, 0.25, True)
    return defl


def export(shape, path, fmt: str | None = None, quality: float = 2e-4, name: str = "Part", *,
           document=None, features=False, require_native=False):
    """Write shape to path. fmt is a key of FORMATS (default: from the extension)."""
    if geometry.is_null(shape):
        raise ExchangeError("Nothing to export - the model is empty")
    f = FORMATS[fmt] if fmt else export_format_for(path)
    features = features or f.key == 'sldprt_features'
    if (features or require_native) and f.key not in ('sldprt', 'sldprt_features'):
        raise ExchangeError('Feature-tree export requires .SLDPRT')
    path = str(path)
    if f.key in _STEP_SCHEMA:
        _write_step(shape, path, _STEP_SCHEMA[f.key], name)
    elif f.key == "iges":
        from OCP.IGESControl import IGESControl_Controller, IGESControl_Writer
        IGESControl_Controller.Init_s()
        w = IGESControl_Writer("MM", 1)          # 1 = B-rep solids (type 186), not surfaces
        w.AddShape(shape)
        w.ComputeModel()
        if not w.Write(path):
            raise ExchangeError(f"Could not write {path}")
    elif f.key == "xt":                       # Parasolid text, written by BauyrCAD itself
        from . import parasolid, xt_brep
        try:
            parasolid.write(xt_brep.bodies_from_shape(shape, name), path, name)
        except parasolid.XtError as e:
            raise ExchangeError(f"Parasolid (.x_t) export: {e}. STEP works for this part.") from e
    elif f.key in ("sldprt", "sldprt_features"):  # written by BauyrCAD itself
        from . import parasolid, swfile
        try:
            return swfile.write_part(shape, path, name, features=features, document=document,
                                     require_native=require_native)
        except parasolid.XtError as e:
            raise ExchangeError(f"SolidWorks part (.SLDPRT) export: {e}. STEP works for this "
                                "part.") from e
    elif f.key == "brep":
        geometry.write_brep(shape, path)
    elif f.key in ("stl", "stl_ascii"):
        from OCP.StlAPI import StlAPI_Writer
        _mesh(shape, quality)
        w = StlAPI_Writer()
        w.ASCIIMode = f.key == "stl_ascii"
        if not w.Write(shape, path):
            raise ExchangeError(f"Could not write {path}")
    elif f.key == "obj":
        _write_obj(shape, path, quality, name)
    elif f.key == "glb":
        _write_glb(shape, path, quality, name)
    elif f.key == "wrl":
        from OCP.VrmlAPI import VrmlAPI_Writer
        _mesh(shape, quality)
        if not VrmlAPI_Writer().Write(shape, path):
            raise ExchangeError(f"Could not write {path}")
    else:  # pragma: no cover
        raise ExchangeError(f"Unsupported format {f.key}")


def _write_step(shape, path, schema, name):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller, STEPControl_Writer
    STEPControl_Controller.Init_s()
    Interface_Static.SetCVal_s("write.step.schema", schema)
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetCVal_s("write.step.product.name", name)
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    if w.Write(path) != IFSelect_RetDone:
        raise ExchangeError(f"Could not write {path}")


def _write_obj(shape, path, quality, name):
    lo, hi = geometry.bounding_box(shape)
    t = geometry.tessellate(shape, max(float(np.linalg.norm(hi - lo)), 1.0) * quality)
    with open(path, "w", encoding="ascii") as fh:
        fh.write(f"# BauyrCAD export, units: mm\no {name}\n")
        np.savetxt(fh, t.points, fmt="v %.6f %.6f %.6f")
        np.savetxt(fh, t.normals, fmt="vn %.6f %.6f %.6f")
        f = t.triangles + 1
        np.savetxt(fh, np.column_stack([f[:, 0], f[:, 0], f[:, 1], f[:, 1], f[:, 2], f[:, 2]]),
                   fmt="f %d//%d %d//%d %d//%d")


def _write_glb(shape, path, quality, name):
    from OCP.Message import Message_ProgressRange
    from OCP.RWGltf import RWGltf_CafWriter
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TColStd import TColStd_IndexedDataMapOfStringString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    _mesh(shape, quality)
    doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    label = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()).AddShape(shape)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    w = RWGltf_CafWriter(TCollection_AsciiString(path), True)
    if not w.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange()):
        raise ExchangeError(f"Could not write {path}")


# ----------------------------------------------------------------------------------------
# Import
# ----------------------------------------------------------------------------------------

def read(path):
    f = import_format_for(path)
    path = str(path)
    if f.key == "step":
        from .step_io import read_step
        return read_step(path)
    if f.key == "iges":
        return _read_iges(path)
    return geometry.fix_shape(geometry.read_brep(path))


def _read_iges(path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader
    from .step_io import _try_make_solid
    r = IGESControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        raise ExchangeError(f"Could not read IGES file: {path}")
    r.TransferRoots()
    shape = geometry.fix_shape(r.OneShape())
    if geometry.is_null(shape):
        raise ExchangeError("IGES file contains no geometry")
    if geometry.count_solids(shape) == 0:
        shape = _try_make_solid(shape)
    return shape


def read_text(key: str, text: str):
    """Read embedded file content (projects store imported files as text)."""
    suffix = {"step": ".step", "iges": ".igs", "brep": ".brep"}[key]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return read(tmp)
    finally:
        os.remove(tmp)


# ---- STEP assemblies (product structure) ---------------------------------------------------------
def write_xt_assembly(path, parts, instances, name="Assembly") -> None:
    """A Parasolid .x_t of an assembly, written by BauyrCAD itself: parts = (name, shape,
    rgb 0-255 | None), each written once (a part with several solids as one body of several
    lumps), named, its faces coloured; instances = (part index, name, 4x4 pose in mm)."""
    from . import parasolid, xt_brep
    try:
        bodies = []
        for pname, shape, rgb in parts:
            pieces = xt_brep.bodies_from_shape(shape, pname)
            body = pieces[0] if len(pieces) == 1 else xt_brep.merge_lumps(pieces, pname)
            body.name = pname
            if rgb is not None:
                for f in body.faces:
                    f.color = tuple(float(c) / 255.0 for c in rgb[:3])
            bodies.append(body)
        insts = []
        for k, iname, m in instances:
            m = np.asarray(m, float)
            insts.append(parasolid.Instance(k, rotation=m[:3, :3].tolist(),
                                            translation=tuple(m[:3, 3] * 1e-3), name=iname))
        parasolid.write(parasolid.Assembly(name, bodies, insts), path, name)
    except parasolid.XtError as e:
        raise ExchangeError(f"Parasolid (.x_t) export: {e}. STEP works for this assembly.") from e


def write_step_assembly(path, parts, instances, name="Assembly", fmt="step214") -> None:
    """Write a STEP file with a real assembly structure, so SolidWorks & co. open it as an
    assembly of separate parts instead of one multi-body part.

    parts:     list of (part_name, shape, rgb or None) - each written once as a product
    instances: list of (part_index, instance_name, 4x4 matrix) - placements of those parts
    """
    from OCP.gp import gp_Trsf
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

    if not instances:
        raise ExchangeError("Nothing to export - the assembly is empty")
    schema = _STEP_SCHEMA.get(fmt, "AP214IS")
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def set_name(label, text):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(str(text)))

    root = tool.NewShape()                           # the top assembly
    set_name(root, name)
    part_labels = []
    for pname, shape, rgb in parts:
        lab = tool.AddShape(shape, False)            # a simple shape = one part (product)
        set_name(lab, pname)
        if rgb:
            colors.SetColor(lab, Quantity_Color(*(c / 255.0 for c in rgb[:3]), Quantity_TOC_RGB),
                            XCAFDoc_ColorSurf)
        part_labels.append(lab)
    for idx, iname, M in instances:
        M = np.asarray(M, float)
        tr = gp_Trsf()
        tr.SetValues(*M[0, :4], *M[1, :4], *M[2, :4])
        comp = tool.AddComponent(root, part_labels[idx], TopLoc_Location(tr))
        set_name(comp, iname)
    tool.UpdateAssemblies()

    STEPControl_Controller.Init_s()
    Interface_Static.SetCVal_s("write.step.schema", schema)
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    w = STEPCAFControl_Writer()
    w.SetColorMode(True)
    w.SetNameMode(True)
    if not w.Transfer(doc, STEPControl_AsIs):
        raise ExchangeError("Could not translate the assembly to STEP")
    if w.Write(str(path)) != IFSelect_RetDone:
        raise ExchangeError(f"Could not write {path}")


def read_step_structure(path) -> dict:
    """Product structure of a STEP file: {'name', 'parts': [names], 'instances': [(part, name,
    4x4)]}. Used by the tests to check what other CAD systems will see."""
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    r = STEPCAFControl_Reader()
    r.SetNameMode(True)
    if not r.Perform(str(path), doc):
        raise ExchangeError(f"Could not read {path}")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def name(label):
        attr = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
            return attr.Get().ToExtString()
        return ""

    tops = TDF_LabelSequence()
    tool.GetFreeShapes(tops)
    out = {"name": "", "parts": [], "instances": []}
    for i in range(1, tops.Length() + 1):
        top = tops.Value(i)
        out["name"] = name(top)
        if not XCAFDoc_ShapeTool.IsAssembly_s(top):
            out["parts"].append(name(top))
            continue
        comps = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetComponents_s(top, comps, False)
        for j in range(1, comps.Length() + 1):
            c = comps.Value(j)
            from OCP.TDF import TDF_Label
            ref = TDF_Label()
            XCAFDoc_ShapeTool.GetReferredShape_s(c, ref)
            pname = name(ref)
            if pname not in out["parts"]:
                out["parts"].append(pname)
            tr = XCAFDoc_ShapeTool.GetLocation_s(c).Transformation()
            M = np.eye(4)
            for a in range(3):
                for b in range(4):
                    M[a, b] = tr.Value(a + 1, b + 1)
            out["instances"].append((pname, name(c), M))
    return out
