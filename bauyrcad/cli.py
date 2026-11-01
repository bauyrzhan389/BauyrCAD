"""Command line interface (for scripting, automation and Claude).

    python -m bauyrcad info   part.bcad                 # JSON: features, volume, faces, studies
    python -m bauyrcad render part.bcad -o part.png [--view iso] [--study "Static 1"]
    python -m bauyrcad run    part.bcad [--study NAME] [--png vm.png] [--field ...] [--vtu out.vtu]
    python -m bauyrcad export part.bcad part.step [--format step242]
    python -m bauyrcad formats                          # list export/import formats
    python -m bauyrcad script model.py [args...]       # run a modelling script
    python -m bauyrcad [part.bcad]                      # open the GUI
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys

import numpy as np

COMMANDS = ("info", "render", "run", "export", "formats", "script", "drawing", "solidworks")


def document_info(doc) -> dict:
    from .core import geometry
    out = {"features": [], "studies": []}
    for f in doc.features:
        d = {"id": f.id, "name": f.name, "type": f.TYPE, "error": f.error, "suppressed": f.suppressed}
        if getattr(f, "warning", None):
            d["warning"] = f.warning
        if f.TYPE == "sketch":
            d["plane"] = {k: (np.round(v, 6).tolist() if hasattr(v, "tolist") else v)
                          for k, v in f.plane.to_dict().items()}
            d["entities"] = len(f.sketch.entities)
            d["profile_error"] = f.profile_error
        elif f.TYPE == "extrude":
            d.update(sketch=f.sketch_id, operation=f.operation, end=f.end, depth=f.depth,
                     reverse=f.reverse, contours=f.regions)
        out["features"].append(d)
    if doc.has_body():
        lo, hi = geometry.bounding_box(doc.body)
        out["volume_mm3"] = round(geometry.volume(doc.body), 6)
        out["bbox"] = [np.round(lo, 6).tolist(), np.round(hi, 6).tolist()]
        out["solids"] = geometry.count_solids(doc.body)
        out["faces"] = [{"index": f.index, "kind": f.kind, "planar": f.is_planar,
                         "diameter": None if f.radius is None else round(2 * f.radius, 6),
                         "area": round(f.area, 4),
                         "centroid": np.round(f.centroid, 4).tolist(),
                         "normal": None if f.normal is None else np.round(f.normal, 6).tolist()}
                        for f in doc.faces]
    for st in doc.studies:
        m = st.material
        out["studies"].append({
            "name": st.name, "material": m.name, "E": m.E, "nu": m.nu,
            "yield": m.yield_strength, "density": m.density,
            "mass_g": round(out.get("volume_mm3", 0) * 1e-9 * m.density * 1000, 4),
            "fixtures": [{"name": fx.name, "faces": [r.resolve(doc.faces) for r in fx.faces]}
                         for fx in st.fixtures],
            "loads": [ld.to_dict() for ld in st.loads],
            "mesh_size": st.mesh_size, "order": st.element_order,
            "mesh_controls": [{"faces": [r.resolve(doc.faces) for r in mc.faces], "size": mc.size}
                              for mc in st.mesh_controls]})
    return out


def _study(doc, name):
    if not doc.studies:
        raise SystemExit("The part has no study")
    if name is None:
        return doc.studies[0]
    for s in doc.studies:
        if s.name == name:
            return s
    raise SystemExit(f"No study named {name!r}; have {[s.name for s in doc.studies]}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="bauyrcad")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("file")
    p = sub.add_parser("render")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--view", default="iso")
    p.add_argument("--study")
    p.add_argument("--size", default="1400x1000")
    p = sub.add_parser("run")
    p.add_argument("file")
    p.add_argument("--study")
    p.add_argument("--png")
    p.add_argument("--field", default="von Mises Stress")
    p.add_argument("--view", default="iso")
    p.add_argument("--vtu")
    p = sub.add_parser("export")
    p.add_argument("file")
    p.add_argument("out")
    p.add_argument("--format")
    p.add_argument("--features", action="store_true", help="offline SolidWorks feature tree, with reported solid fallback")
    p.add_argument("--require-native", action="store_true", help="fail instead of falling back to an Imported solid")
    p = sub.add_parser("drawing", help="drawing sheet of a part/assembly -> .pdf .dxf .dwg .svg .png .bcdrw")
    p.add_argument("file", help=".bcad / .bcasm / STEP model, or a .bcdrw drawing")
    p.add_argument("out", nargs="+", help="output file(s)")
    p.add_argument("--sheet", default="A3")
    p.add_argument("--portrait", action="store_true")
    p.add_argument("--third-angle", action="store_true", help="ANSI third-angle projection")
    p.add_argument("--scale", help="e.g. 1:2 (default: largest standard scale that fits)")
    p.add_argument("--no-iso", action="store_true")
    p.add_argument("--auto-dims", action="store_true", help="overall sizes and hole diameters")
    p.add_argument("--title", help="title block title")
    p.add_argument("--view", help="DXF/DWG of this view only, at 1:1 (e.g. Front)")
    p = sub.add_parser("solidworks", help="native .SLDPRT / .SLDASM via the SolidWorks on this PC")
    p.add_argument("file", help=".bcad part or .bcasm assembly")
    p.add_argument("out", help=".SLDPRT path (part) or output folder (assembly)")
    p.add_argument("--solid", action="store_true", help="solid bodies instead of feature trees")
    sub.add_parser("formats")
    p = sub.add_parser("script")
    p.add_argument("script")
    p.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)

    if a.cmd == "formats":
        from .core import exchange
        print(json.dumps({"export": [(f.key, f.label, f.extensions) for f in exchange.EXPORT_FORMATS],
                          "assembly_export": [(f.key, f.label, f.extensions)
                                              for f in exchange.ASSEMBLY_FORMATS],
                          "import": [(f.key, f.label, f.extensions) for f in exchange.IMPORT_FORMATS]},
                         indent=1))
        return 0
    if a.cmd == "script":
        sys.argv = [a.script, *a.args]
        runpy.run_path(a.script, run_name="__main__")
        return 0
    if a.cmd == "solidworks":
        from . import solidworks
        if a.file.lower().endswith(".bcasm"):
            from .core.assembly import Assembly as _Asm
            rep = solidworks.export_assembly_files(_Asm.load(a.file), a.out, not a.solid)
        else:
            from .core.document import Document
            rep = solidworks.export_part_file(Document.load(a.file), a.out, not a.solid)
        print(json.dumps(rep, indent=1, default=str))
        return 0
    if a.cmd == "drawing":
        from .api import Drawing
        if a.file.lower().endswith(".bcdrw"):
            dr = Drawing.open(a.file)
        else:
            dr = Drawing(a.file, sheet=a.sheet, portrait=a.portrait,
                         projection="third" if a.third_angle else "first", scale=a.scale,
                         iso=not a.no_iso)
            if a.auto_dims:
                dr.auto_dimension(dr.views[0])
        if a.title:
            dr.title(title=a.title)
        for out in a.out:
            if out.lower().endswith(".bcdrw"):
                dr.save(out)
            else:
                dr.export(out, view=a.view if out.lower().endswith((".dxf", ".dwg")) else None)
        print(json.dumps({**dr.info(), "written": a.out}, indent=1))
        return 0

    from .core.document import Document
    doc = Document.load(a.file)
    if a.cmd == "info":
        print(json.dumps(document_info(doc), indent=1))
    elif a.cmd == "render":
        from .render import render_model
        w, h = (int(x) for x in a.size.lower().split("x"))
        st = _study(doc, a.study) if a.study else None
        render_model(doc, a.out, view=a.view, size=(w, h), study=st)
        print(a.out)
    elif a.cmd == "run":
        from .fea.analysis import run_study
        st = _study(doc, a.study)
        res = run_study(st, doc)
        s = res.summary()
        s["warnings"] = res.warnings
        s["material"] = st.material.name
        if a.png:
            from .render import render_results
            clip = 99.0 if ("Stress" in a.field and res.load_nodes) else None
            render_results(doc, res, a.png, field=a.field, view=a.view, clip_percentile=clip)
            s["png"] = a.png
        if a.vtu:
            res.vtk_grid().save(a.vtu)
            s["vtu"] = a.vtu
        print(json.dumps(s, indent=1))
    elif a.cmd == "export":
        from .core.swfile import SwFileError
        try:
            rep = doc.export(a.out, a.format, features=a.features, require_native=a.require_native)
        except SwFileError as exc:
            print(f"Could not export: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(rep, indent=1) if a.features else a.out)
    return 0
