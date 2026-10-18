"""Drawing exporters: PDF / SVG / PNG (matplotlib, vector), DXF (ezdxf), DWG (ODA File Converter).

DWG is a closed format with no free writer library; the free ODA File Converter
(https://www.opendesign.com/guestfiles/oda_file_converter) converts our DXF to DWG when it is
installed. Every AutoCAD version (and most CAD/CAM software) opens DXF directly.
"""

from __future__ import annotations

import glob
import math
import os
from pathlib import Path

import numpy as np

from .drawing import LAYERS, Arc, Drawing, DrawingError, Line, Poly, Solid, Text

PT = 72.0 / 25.4                       # points per mm
CAP = 0.73                             # cap height / em of DejaVu Sans
ODA_URL = "https://www.opendesign.com/guestfiles/oda_file_converter"


def _arc_step(r):
    """Angular step (deg) keeping the chord error below 0.01 mm."""
    if r <= 0.01:
        return 30.0
    return float(np.clip(math.degrees(2 * math.acos(max(1 - 0.01 / r, -1))), 0.5, 5.0))


def _polyline(p) -> np.ndarray | None:
    if isinstance(p, Line):
        return np.array([p.a, p.b])
    if isinstance(p, Arc):
        return p.points(_arc_step(p.r))
    if isinstance(p, Poly):
        return np.vstack([p.pts, p.pts[:1]]) if p.closed else p.pts
    return None


# =====================================================================================
# PDF / SVG / PNG
# =====================================================================================
def write_matplotlib(dr: Drawing, path, dpi=200):
    import matplotlib
    from matplotlib.collections import LineCollection
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon

    path = Path(path)
    W, H = dr.size
    with matplotlib.rc_context({"lines.scale_dashes": False, "pdf.fonttype": 42,
                                "svg.fonttype": "none", "font.family": "DejaVu Sans"}):
        fig = Figure(figsize=(W / 25.4, H / 25.4))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.set_aspect("equal")
        ax.axis("off")
        by_layer: dict = {}
        for p in dr.primitives():
            if isinstance(p, Text):
                lw, _, grey = LAYERS.get(p.layer, LAYERS["TEXT"])
                for pt, line in p.layout():
                    ax.text(pt[0], pt[1], line, fontsize=p.height / CAP * PT, rotation=p.angle,
                            rotation_mode="anchor", color=str(grey), ha=p.ha, va="baseline")
            elif isinstance(p, Solid):
                grey = LAYERS.get(p.layer, LAYERS["DIM"])[2]
                ax.add_patch(Polygon(p.pts, closed=True, facecolor=str(grey), edgecolor="none"))
            else:
                pl = _polyline(p)
                if pl is not None:
                    by_layer.setdefault(p.layer, []).append(pl)
        for layer, segs in by_layer.items():
            lw, dashes, grey = LAYERS.get(layer, LAYERS["THIN"])
            style = "solid" if dashes is None else (0, tuple(d * PT for d in dashes))
            ax.add_collection(LineCollection(segs, linewidths=lw * PT, colors=str(grey),
                                             linestyles=[style], capstyle="round",
                                             joinstyle="round"))
        fig.savefig(str(path), format=path.suffix[1:].lower(), dpi=dpi, facecolor="white")


# =====================================================================================
# DXF
# =====================================================================================
_ACI = {"TANGENT": 8, "CENTER": 1, "DIM": 7, "HIDDEN": 7}
_LTYPE = {"HIDDEN": "BC_HIDDEN", "CENTER": "BC_CENTER"}


def _dxf_text(s: str) -> str:
    return s.replace("⌀", "%%c").replace("°", "%%d").replace("±", "%%p")


def _setup(doc):
    doc.header["$MEASUREMENT"] = 1
    doc.header["$INSUNITS"] = 4                     # millimetres
    doc.header["$LUNITS"] = 2
    doc.header["$LTSCALE"] = 1.0
    doc.header["$LWDISPLAY"] = 1
    doc.linetypes.add("BC_HIDDEN", pattern=[4.5, 3.0, -1.5], description="Hidden __ __ __")
    doc.linetypes.add("BC_CENTER", pattern=[12.0, 8.0, -1.5, 1.0, -1.5],
                      description="Center ____ _ ____")
    valid = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100]
    for name, (lw, dashes, _) in LAYERS.items():
        w = min(valid, key=lambda v: abs(v - lw * 100))
        doc.layers.add(name, color=_ACI.get(name, 7), linetype=_LTYPE.get(name, "Continuous"),
                       lineweight=w)
    doc.styles.get("Standard").dxf.font = "arial.ttf"
    ds = doc.dimstyles.new("BC_ISO")
    for k, v in dict(dimtxt=3.5, dimasz=3.0, dimexe=2.0, dimexo=1.0, dimgap=1.0, dimdec=2,
                     dimtad=1, dimtih=0, dimtoh=0, dimzin=8, dimazin=0, dimdsep=ord("."),
                     dimclrd=0, dimclre=0, dimclrt=0, dimlunit=2, dimtofl=1,
                     dimcen=0).items():
        ds.dxf.set(k, v)


def _add_prim(msp, p, T=lambda q: q):
    from ezdxf.enums import TextEntityAlignment
    attrs = {"layer": p.layer}
    if isinstance(p, Line):
        msp.add_line(tuple(T(p.a)), tuple(T(p.b)), dxfattribs=attrs)
    elif isinstance(p, Arc):
        c = tuple(T(p.c))
        if p.full:
            msp.add_circle(c, p.r, dxfattribs=attrs)
        else:
            msp.add_arc(c, p.r, p.a0, p.a1, dxfattribs=attrs)
    elif isinstance(p, Poly):
        msp.add_lwpolyline([tuple(T(q)) for q in p.pts], close=p.closed, dxfattribs=attrs)
    elif isinstance(p, Solid):
        pts = [tuple(T(q)) for q in p.pts]
        msp.add_solid(pts + [pts[-1]] if len(pts) == 3 else pts, dxfattribs=attrs)
    elif isinstance(p, Text):
        align = {"left": TextEntityAlignment.LEFT, "center": TextEntityAlignment.CENTER,
                 "right": TextEntityAlignment.RIGHT}[p.ha]
        for pt, line in p.layout():                 # one TEXT per line, on its baseline
            if line:
                t = msp.add_text(_dxf_text(line), height=p.height, rotation=p.angle,
                                 dxfattribs=attrs)
                t.set_placement(tuple(T(pt)), align=align)


def _add_dim(msp, dr, d, sheet=True):
    """A real DIMENSION entity (AutoCAD shows and edits it as a dimension)."""
    v = dr.view(d.view)
    T = (lambda p: dr.to_sheet(v, p)) if sheet else (lambda p: np.asarray(p, float))
    k = dr.view_scale(v) if sheet else 1.0
    dec = d.decimals if d.decimals is not None else dr.decimals
    override = {"dimlfac": 1.0 / k, "dimdec": dec}
    attrs = {"layer": "DIM"}
    if d.kind == "linear":
        u = d.direction()
        text = _dxf_text(d.text) if d.text else "<>"
        dim = msp.add_linear_dim(base=tuple(T(d.pos)), p1=tuple(T(d.p1)), p2=tuple(T(d.p2)),
                                 angle=math.degrees(math.atan2(u[1], u[0])), text=text,
                                 dimstyle="BC_ISO", override=override, dxfattribs=attrs)
    else:
        c, pos = T(d.center), T(d.pos)
        r = d.r * k
        dv = pos - c
        ang = math.degrees(math.atan2(dv[1], dv[0])) if np.linalg.norm(dv) > 1e-9 else 45.0
        # AutoCAD prefixes diameter dims with its own Ø; radius dims get "R"
        text = _dxf_text(d.text) if d.text else ("<>" if d.kind == "diameter" else "R<>")
        fn = msp.add_diameter_dim if d.kind == "diameter" else msp.add_radius_dim
        dim = fn(center=tuple(c), radius=r, location=tuple(pos), text=text,
                 dimstyle="BC_ISO", override=override, dxfattribs=attrs)
    dim.render()


def build_dxf(dr: Drawing, view=None):
    import ezdxf
    doc = ezdxf.new("R2010", setup=True, units=4)
    _setup(doc)
    msp = doc.modelspace()
    if view is not None:
        v = dr.view(view)
        if v is None:
            raise DrawingError(f"No view {view!r}")
        prims, dims = dr.view_primitives(v, sheet=False), [d for d in dr.dims if d.view == v.id]
        sheet = False
    else:
        prims = dr.primitives(include_dims=False)
        dims, sheet = dr.dims, True
    for p in prims:
        _add_prim(msp, p)
    for d in dims:
        try:
            _add_dim(msp, dr, d, sheet)
        except Exception:  # noqa: BLE001 - fall back to plain lines and text
            for p in dr.dim_primitives(d, sheet):
                _add_prim(msp, p)
    return doc


def write_dxf(dr: Drawing, path, view=None):
    build_dxf(dr, view).saveas(str(path))


# =====================================================================================
# DWG
# =====================================================================================
def find_oda_converter() -> str | None:
    """Path of ODAFileConverter.exe (also configures ezdxf to use it), or None."""
    from ezdxf.addons import odafc
    import ezdxf
    if odafc.is_installed():
        return odafc.get_win_exec_path() if os.name == "nt" else "ODAFileConverter"
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for root in roots:
        hits = sorted(glob.glob(os.path.join(root, "ODA", "*", "ODAFileConverter.exe")))
        if hits:
            ezdxf.options.set("odafc-addon", "win_exec_path", hits[-1])
            return hits[-1]
    return None


def write_dwg(dr: Drawing, path, view=None, version="R2018"):
    if find_oda_converter() is None:
        raise DrawingError(
            "Saving DWG needs the free ODA File Converter (DWG is a closed format).\n"
            f"Install it from {ODA_URL} and try again,\n"
            "or save as DXF, which AutoCAD and other CAD programs open directly.")
    from ezdxf.addons import odafc
    odafc.export_dwg(build_dxf(dr, view), str(path), version=version, replace=True)
