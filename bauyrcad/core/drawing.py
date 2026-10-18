"""2D drawings: SolidWorks-style drawing sheets made from a part or an assembly.

A Drawing is a sheet (A4..A0, landscape/portrait) with a border and an ISO-style title block,
holding orthographic and isometric views of one model (.bcad part, .bcasm assembly or a
STEP/IGES file). Views are exact hidden-line projections (OCC HLR). Views can be projected
from other views (first- or third-angle placement) and stay aligned with their parent.
Dimensions (linear, aligned, diameter, radius) are driven by the model: they measure the
projected geometry and follow it when the model changes.

Everything is plain data plus a list of 2D primitives (lines, arcs, text, filled arrows) in
sheet millimetres, which the GUI paints and the exporters write:
  PDF / SVG / PNG (vector, via matplotlib), DXF (ezdxf; real DIMENSION entities, layers and
  linetypes), DWG (DXF converted by the free ODA File Converter when it is installed).
No Qt in here: the API, CLI and GUI share it.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import geometry
from .bom import BOMTable, Balloon

FILE_EXTENSION = ".bcdrw"
FORMAT_VERSION = 1

SHEET_SIZES = {                      # landscape width x height, mm
    "A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0),
    "A1": (841.0, 594.0), "A0": (1189.0, 841.0),
    "Letter": (279.4, 215.9), "Tabloid": (431.8, 279.4),
}
STANDARD_SCALES = [50, 20, 10, 5, 2, 1, 1 / 2, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100, 1 / 200,
                   1 / 500, 1 / 1000]

# view direction (towards the viewer) and sheet +x, Y-up model (Front = XY plane)
ORIENTATIONS = {
    "front": ((0, 0, 1), (1, 0, 0)),
    "back": ((0, 0, -1), (-1, 0, 0)),
    "top": ((0, 1, 0), (1, 0, 0)),
    "bottom": ((0, -1, 0), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 0, -1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "isometric": ((1, 1, 1), (1, 0, -1)),
}

# name: (line weight mm, dash pattern mm or None, grey level 0..1 for the screen/PDF)
LAYERS = {
    "VISIBLE": (0.50, None, 0.0),
    "HIDDEN": (0.25, (3.0, 1.5), 0.0),
    "TANGENT": (0.18, None, 0.45),
    "CENTER": (0.18, (8.0, 1.5, 1.0, 1.5), 0.0),
    "DIM": (0.25, None, 0.0),
    "TEXT": (0.25, None, 0.0),
    "BORDER": (0.70, None, 0.0),
    "FRAME": (0.35, None, 0.0),
    "THIN": (0.18, None, 0.0),
}

ARROW_LEN, ARROW_HALF = 3.0, 0.55      # mm on the sheet (ISO 129: closed filled, ~20 deg)
DIM_TEXT, NOTE_TEXT = 3.5, 3.5
EXT_GAP, EXT_OVER = 1.0, 2.0           # extension line: gap from the object, overshoot


class DrawingError(Exception):
    pass


# =====================================================================================
# primitives (sheet mm, y up)
# =====================================================================================
@dataclass
class Line:
    a: np.ndarray
    b: np.ndarray
    layer: str = "VISIBLE"
    owner: str = ""


@dataclass
class Arc:
    c: np.ndarray
    r: float
    a0: float                           # degrees, counter-clockwise from a0 to a1
    a1: float
    layer: str = "VISIBLE"
    owner: str = ""

    @property
    def full(self) -> bool:
        return self.a1 - self.a0 >= 360.0 - 1e-9

    def points(self, seg_deg=3.0) -> np.ndarray:
        n = max(4, int(abs(self.a1 - self.a0) / seg_deg) + 1)
        a = np.radians(np.linspace(self.a0, self.a1, n))
        return self.c + self.r * np.column_stack([np.cos(a), np.sin(a)])


@dataclass
class Poly:
    pts: np.ndarray
    layer: str = "VISIBLE"
    owner: str = ""
    closed: bool = False


@dataclass
class Text:
    pos: np.ndarray
    text: str
    height: float = DIM_TEXT
    angle: float = 0.0                  # degrees
    ha: str = "left"                    # left | center | right
    va: str = "bottom"                  # bottom | center | top
    layer: str = "TEXT"
    owner: str = ""

    LEADING = 1.6                      # line pitch / cap height

    def width(self) -> float:
        lines = self.text.split("\n")
        return max(len(s) for s in lines) * self.height * 0.62

    def _frame(self):
        t = math.radians(self.angle)
        return np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])

    def _top(self):
        n = self.text.count("\n") + 1
        total = self.height * (1 + self.LEADING * (n - 1))
        return {"top": 0.0, "center": total / 2, "bottom": total}[self.va], total

    def layout(self) -> list:
        """[(baseline point, line)]. `height` is the cap height and the alignment is on the
        cap box (not the font's ascent/descent), so every backend places text identically."""
        top, _ = self._top()
        R = self._frame()
        out = []
        for i, line in enumerate(self.text.split("\n")):
            y = top - self.height - i * self.LEADING * self.height
            out.append((self.pos + R @ np.array([0.0, y]), line))
        return out

    def box(self) -> np.ndarray:
        """Corners of the text's box on the sheet (for picking)."""
        w = self.width()
        top, total = self._top()
        x0 = {"left": 0.0, "center": -w / 2, "right": -w}[self.ha]
        c = np.array([[x0, top - total], [x0 + w, top - total], [x0 + w, top], [x0, top]])
        return self.pos + c @ self._frame().T


@dataclass
class Solid:
    pts: np.ndarray                     # filled polygon (arrowheads)
    layer: str = "DIM"
    owner: str = ""


def _arrow(tip, direction, owner, layer="DIM"):
    """Filled arrowhead with its tip at `tip`, pointing along `direction`."""
    d = np.asarray(direction, float)
    d = d / (np.linalg.norm(d) or 1.0)
    n = np.array([-d[1], d[0]])
    base = tip - d * ARROW_LEN
    return Solid(np.array([tip, base + n * ARROW_HALF, base - n * ARROW_HALF]), layer, owner)


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _perp(v):
    return np.array([-v[1], v[0]])


def fmt_number(v: float, decimals: int = 2) -> str:
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def scale_text(s: float) -> str:
    if s >= 1:
        return f"{fmt_number(s, 3)}:1"
    return f"1:{fmt_number(1 / s, 3)}"


# =====================================================================================
# model
# =====================================================================================
@dataclass
class View:
    id: str
    name: str
    direction: tuple
    xdir: tuple
    x: float = 0.0                     # sheet position of the view's centre
    y: float = 0.0
    scale: float | None = None         # None = sheet scale
    parent: str | None = None          # projected from this view (stays aligned with it)
    align: str | None = None           # "x" (above/below parent) or "y" (left/right)
    hidden: bool = True                # hidden lines visible (dashed) / removed
    tangent: bool = True               # tangent edges visible
    center_marks: bool = True
    label: bool | None = None          # None: label only when the view scale differs

    def to_dict(self):
        d = dict(self.__dict__)
        d["direction"], d["xdir"] = list(self.direction), list(self.xdir)
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["direction"], d["xdir"] = tuple(d["direction"]), tuple(d["xdir"])
        return cls(**d)


@dataclass
class Dimension:
    """Linear dims measure p2 - p1 along a direction ("h", "v" or "a" = aligned with p1->p2);
    diameter/radius dims measure a circle. Points are in view coordinates (model mm)."""
    id: str
    view: str
    kind: str                          # linear | diameter | radius
    p1: tuple | None = None
    p2: tuple | None = None
    orient: str = "a"
    center: tuple | None = None
    r: float = 0.0
    pos: tuple = (0.0, 0.0)            # placement (dimension line / text), view coordinates
    text: str | None = None            # override; "<>" in it is replaced by the value
    decimals: int | None = None

    def to_dict(self):
        return {k: (list(v) if isinstance(v, (tuple, np.ndarray)) else v)
                for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        for k in ("p1", "p2", "center", "pos"):
            if d.get(k) is not None:
                d[k] = tuple(d[k])
        return cls(**d)

    def direction(self) -> np.ndarray:
        if self.orient == "h":
            return np.array([1.0, 0.0])
        if self.orient == "v":
            return np.array([0.0, 1.0])
        return _unit(np.subtract(self.p2, self.p1))

    @property
    def value(self) -> float:
        if self.kind == "linear":
            return abs(float(np.subtract(self.p2, self.p1) @ self.direction()))
        return 2 * self.r if self.kind == "diameter" else self.r

    def label(self, decimals=2) -> str:
        v = fmt_number(self.value, self.decimals if self.decimals is not None else decimals)
        v = {"diameter": "⌀", "radius": "R"}.get(self.kind, "") + v
        if self.text:
            return self.text.replace("<>", v)
        return v


@dataclass
class Note:
    id: str
    x: float
    y: float
    text: str
    height: float = NOTE_TEXT
    angle: float = 0.0

    def to_dict(self):
        return dict(self.__dict__)


TITLE_FIELDS = [("title", "Title"), ("drawing_no", "Drawing No."), ("material", "Material"),
                ("company", "Company"), ("drawn_by", "Drawn by"), ("checked_by", "Checked by"),
                ("date", "Date"), ("revision", "Rev."), ("finish", "Finish")]


@dataclass
class ViewGeometry:
    edges: list                        # ProjectedEdge, cleaned
    lo: np.ndarray
    hi: np.ndarray
    circles: list                      # (center, r) of full circles, for centre marks / dims

    @property
    def center(self):
        return (self.lo + self.hi) / 2


def load_model_shape(path):
    """Solid of a part (.bcad), an assembly (.bcasm, as placed) or a STEP/IGES/BREP file."""
    path = Path(path)
    if not path.exists():
        raise DrawingError(f"Model not found: {path}")
    ext = path.suffix.lower()
    if ext == ".bcad":
        from .document import Document
        doc = Document.load(path)
        shape = doc.body
    elif ext == ".bcasm":
        from .assembly import Assembly
        shape = Assembly.load(path).world_shape()
    else:
        from .exchange import read
        shape = read(path)
    if shape is None or geometry.is_null(shape):
        raise DrawingError(f"{path.name} has no solid body")
    return shape


class Drawing:
    def __init__(self, model: str | None = None, sheet: str = "A3", portrait: bool = False,
                 scale: float = 1.0, projection: str = "first"):
        self.model = str(model) if model else None
        self.sheet = sheet
        self.portrait = portrait
        self.scale = float(scale)
        self.projection = projection          # "first" (ISO) | "third" (ANSI)
        self.decimals = 2
        self.views: list[View] = []
        self.dims: list[Dimension] = []
        self.notes: list[Note] = []
        self.bom: BOMTable | None = None
        self.balloons: list[Balloon] = []
        self.title = {k: "" for k, _ in TITLE_FIELDS}
        self.title["date"] = _dt.date.today().isoformat()
        if model:
            self.title["title"] = Path(model).stem
        self.path: Path | None = None
        self.shape = None                      # model solid (world coordinates)
        self._empty_assembly = False
        self.shape_override = None             # an in-memory shape instead of the file
        self.model_version = 0
        self._geo: dict = {}
        self._counter = 0

    # ---- sheet ------------------------------------------------------------------------
    @property
    def size(self) -> tuple[float, float]:
        w, h = SHEET_SIZES[self.sheet]
        return (h, w) if self.portrait else (w, h)

    @property
    def frame(self):
        """Drawing frame inside the border (ISO 5457: 20 mm filing margin on the left)."""
        W, H = self.size
        return 20.0, 10.0, W - 10.0, H - 10.0

    TB_W, TB_H = 180.0, 32.0

    def usable_area(self):
        """Region for views: inside the frame and above the title block."""
        x0, y0, x1, y1 = self.frame
        return x0 + 5, y0 + self.TB_H + 5, x1 - 5, y1 - 5

    def _new_id(self, prefix):
        existing = {v.id for v in self.views} | {d.id for d in self.dims} | {n.id for n in self.notes}
        existing |= {b.id for b in self.balloons} | ({self.bom.id} if self.bom else set())
        existing |= {b.view for b in self.balloons}  # deleted views must not capture old balloons
        while True:
            self._counter += 1
            i = f"{prefix}{self._counter}"
            if i not in existing:
                return i

    # ---- model ------------------------------------------------------------------------
    def model_path(self) -> Path | None:
        if not self.model:
            return None
        p = Path(self.model)
        if not p.is_absolute() and self.path is not None:
            p = self.path.parent / p
        return p

    def load_model(self):
        self._empty_assembly = False
        if self.bom:
            assembly = self._assembly()
            self.bom.refresh(assembly)
            self._empty_assembly = not any(c.shape is not None for c in assembly.components)
            self.shape = None if self._empty_assembly else assembly.world_shape()
        elif self.shape_override is not None:
            self.shape = self.shape_override
        else:
            p = self.model_path()
            if p is None:
                raise DrawingError("The drawing has no model")
            self.shape = load_model_shape(p)
        self.model_version += 1
        self._geo.clear()
        return self.shape

    def reload(self):
        """Re-read the model and re-attach dimensions to the new geometry."""
        old = {v.id: self.geometry(v) for v in self.views} if self.shape is not None else {}
        self.load_model()
        for d in self.dims:
            v = self.view(d.view)
            if v is not None:
                self._reattach(d, old.get(v.id), self.geometry(v))

    def _ensure_model(self):
        if self.shape is None and not self._empty_assembly:
            self.load_model()

    def model_bounds(self):
        self._ensure_model()
        if self._empty_assembly:
            return np.zeros(3), np.zeros(3)
        return geometry.bounding_box(self.shape)

    def model_size(self) -> float:
        lo, hi = self.model_bounds()
        return max(float(np.linalg.norm(hi - lo)), 1e-6)

    # ---- views ------------------------------------------------------------------------
    def view(self, vid) -> View | None:
        if isinstance(vid, View):
            return vid
        return next((v for v in self.views if v.id == vid), None)

    def view_scale(self, v: View) -> float:
        return v.scale if v.scale else self.scale

    def geometry(self, v: View) -> ViewGeometry:
        self._ensure_model()
        if self._empty_assembly:
            return ViewGeometry([], np.zeros(2), np.zeros(2), [])
        key = (v.id, self.model_version, tuple(np.round(v.direction, 9)),
               tuple(np.round(v.xdir, 9)), v.hidden, v.tangent)
        g = self._geo.get(key)
        if g is None:
            tol = max(self.model_size() * 2e-5, 1e-5)
            edges = geometry.project_hlr(self.shape, v.direction, v.xdir, hidden=v.hidden,
                                         smooth=v.tangent, tol=max(self.model_size() * 2e-4, 0.005))
            edges = clean_edges(edges, tol)
            pts = np.vstack([e.extent_points() for e in edges]) if edges else np.zeros((1, 2))
            circles = []
            for e in edges:
                if e.kind == "circle" and e.a1 - e.a0 >= 360 - 1e-6:
                    if not any(np.linalg.norm(c - e.center) < tol and abs(r - e.r) < tol
                               for c, r in circles):
                        circles.append((e.center.copy(), e.r))
            g = ViewGeometry(edges, pts.min(0), pts.max(0), circles)
            self._geo = {k: val for k, val in self._geo.items() if k[0] != v.id}
            self._geo[key] = g
        return g

    def to_sheet(self, v: View, p) -> np.ndarray:
        g = self.geometry(v)
        return np.array([v.x, v.y]) + self.view_scale(v) * (np.asarray(p, float) - g.center)

    def to_view(self, v: View, s) -> np.ndarray:
        g = self.geometry(v)
        return g.center + (np.asarray(s, float) - [v.x, v.y]) / self.view_scale(v)

    def view_box(self, v: View):
        """Sheet rectangle (x0, y0, x1, y1) of a view's geometry."""
        g = self.geometry(v)
        a, b = self.to_sheet(v, g.lo), self.to_sheet(v, g.hi)
        return a[0], a[1], b[0], b[1]

    def add_view(self, orientation="front", at=None, scale=None, hidden=None, name=None,
                 direction=None, xdir=None) -> View:
        """A model view. orientation: front/back/top/bottom/left/right/isometric, or pass
        direction (towards the viewer) and xdir."""
        if direction is None:
            key = orientation.lower()
            key = {"iso": "isometric"}.get(key, key)
            if key not in ORIENTATIONS:
                raise DrawingError(f"Unknown view orientation {orientation!r}")
            direction, xdir = ORIENTATIONS[key]
            name = name or key.capitalize()
        n, x, _ = geometry.view_frame(direction, xdir)
        iso = abs(n @ [1, 0, 0]) < 0.99 and abs(n @ [0, 1, 0]) < 0.99 and abs(n @ [0, 0, 1]) < 0.99
        v = View(self._new_id("V"), name or "View", tuple(n), tuple(x), scale=scale,
                 hidden=(not iso) if hidden is None else hidden)
        self.views.append(v)
        if at is None:
            x0, y0, x1, y1 = self.usable_area()
            at = ((x0 + x1) / 2, (y0 + y1) / 2)
        v.x, v.y = float(at[0]), float(at[1])
        return v

    def projected_frame(self, parent: View, side: str):
        """(direction, xdir, align) of the view placed on `side` (left/right/up/down) of
        parent, using this drawing's projection method."""
        n, x, y = geometry.view_frame(parent.direction, parent.xdir)
        third = self.projection == "third"
        if side in ("right", "left"):
            looks_from_right = (side == "right") == third
            return (tuple(x), tuple(-n), "y") if looks_from_right else (tuple(-x), tuple(n), "y")
        if side in ("up", "down"):
            looks_from_top = (side == "up") == third
            return (tuple(y), tuple(x), "x") if looks_from_top else (tuple(-y), tuple(x), "x")
        raise DrawingError(f"side must be left/right/up/down, not {side!r}")

    def projected_view(self, parent, side="right", at=None) -> View:
        parent = self.view(parent)
        direction, xdir, align = self.projected_frame(parent, side)
        v = View(self._new_id("V"), orientation_name(direction, xdir), direction, xdir,
                 scale=parent.scale, parent=parent.id, align=align, hidden=parent.hidden,
                 tangent=parent.tangent)
        self.views.append(v)
        if at is None:
            px0, py0, px1, py1 = self.view_box(parent)
            g = self.geometry(v)
            half = (g.hi - g.lo) * self.view_scale(v) / 2
            gap = 20.0
            at = {"right": (px1 + gap + half[0], parent.y), "left": (px0 - gap - half[0], parent.y),
                  "up": (parent.x, py1 + gap + half[1]), "down": (parent.x, py0 - gap - half[1])}[side]
        self.move_view(v, *at)
        return v

    def move_view(self, v, x, y):
        """Move a view; projected views keep their alignment, children follow."""
        v = self.view(v)
        par = self.view(v.parent) if v.parent else None
        if par is not None:
            if v.align == "x":
                x = par.x
            elif v.align == "y":
                y = par.y
        v.x, v.y = float(x), float(y)
        for c in self.views:
            if c.parent == v.id:
                self.move_view(c, c.x if c.align != "x" else v.x, c.y if c.align != "y" else v.y)

    def remove_view(self, v):
        v = self.view(v)
        self.views.remove(v)
        self.dims = [d for d in self.dims if d.view != v.id]
        for c in self.views:
            if c.parent == v.id:
                c.parent, c.align = None, None

    def fit_scale(self, iso=True) -> float:
        """Largest standard scale at which a standard 3-view layout (+ iso) fits the sheet."""
        lo, hi = self.model_bounds()
        w, h, d = hi - lo
        x0, y0, x1, y1 = self.usable_area()
        aw, ah = x1 - x0, y1 - y0
        gap = 25.0
        for s in STANDARD_SCALES:
            if s * (w + d) + gap + 30 <= aw and s * (h + d) + gap + 30 <= ah:
                return s
        return STANDARD_SCALES[-1]

    def standard_views(self, iso=True, scale=None) -> list[View]:
        """Front, Top and Right (Left in first angle) aligned, plus an isometric view."""
        self._ensure_model()
        if scale is None:
            scale = self.fit_scale(iso)
        self.scale = scale
        lo, hi = self.model_bounds()
        w, h, d = (hi - lo) * scale
        x0, y0, x1, y1 = self.usable_area()
        gap = 25.0
        W, H = w + gap + d, h + gap + d
        ox, oy = x0 + (x1 - x0 - W) / 2, y0 + (y1 - y0 - H) / 2
        third = self.projection == "third"
        front = self.add_view("front", at=(ox + w / 2, oy + (h / 2 if third else d + gap + h / 2)))
        top = self.projected_view(front, "up" if third else "down")
        side = self.projected_view(front, "right")
        out = [front, top, side]
        if iso:
            cx = ox + w + gap + d / 2
            cy = oy + (h + gap + d / 2 if third else d / 2)
            v = self.add_view("isometric", at=(cx, cy))
            g = self.geometry(v)
            ext = (g.hi - g.lo)
            room = np.array([max(d, 40.0) + gap, max(d, 40.0) + gap])
            s = self.scale
            for cand in STANDARD_SCALES:
                if cand <= self.scale and np.all(ext * cand <= room * 1.6):
                    s = cand
                    break
            v.scale = None if abs(s - self.scale) < 1e-12 else s
            out.append(v)
        return out

    # ---- dimensions ---------------------------------------------------------------------
    def dim(self, did) -> Dimension | None:
        return next((d for d in self.dims if d.id == did), None)

    def add_linear(self, view, p1, p2, orient=None, pos=None, text=None) -> Dimension:
        """Linear dimension between two points of a view (view coordinates, model mm).
        orient: "h", "v", "a" (aligned) or None = horizontal/vertical when the points line
        up, else aligned."""
        v = self.view(view)
        p1, p2 = np.asarray(p1, float), np.asarray(p2, float)
        if orient is None:
            d = p2 - p1
            orient = "h" if abs(d[1]) < 1e-6 * (abs(d[0]) + 1) else \
                "v" if abs(d[0]) < 1e-6 * (abs(d[1]) + 1) else "a"
        if pos is None:
            g = self.geometry(v)
            off = 10.0 / self.view_scale(v)
            mid = (p1 + p2) / 2
            if orient == "h":
                pos = (mid[0], g.hi[1] + off if mid[1] > g.center[1] else g.lo[1] - off)
            elif orient == "v":
                pos = (g.hi[0] + off if mid[0] > g.center[0] else g.lo[0] - off, mid[1])
            else:
                n = _perp(_unit(p2 - p1))
                if n @ (mid - g.center) < 0:
                    n = -n
                pos = tuple(mid + n * off)
        d = Dimension(self._new_id("D"), v.id, "linear", tuple(p1), tuple(p2), orient,
                      pos=tuple(pos), text=text)
        self.dims.append(d)
        return d

    def add_diameter(self, view, center, r, pos=None, text=None, radius=False) -> Dimension:
        v = self.view(view)
        c = np.asarray(center, float)
        if pos is None:
            ang = math.radians(45)
            pos = tuple(c + (r + 8.0 / self.view_scale(v)) * np.array([math.cos(ang), math.sin(ang)]))
        d = Dimension(self._new_id("D"), v.id, "radius" if radius else "diameter",
                      center=tuple(c), r=float(r), pos=tuple(pos), text=text)
        self.dims.append(d)
        return d

    def add_radius(self, view, center, r, pos=None, text=None) -> Dimension:
        return self.add_diameter(view, center, r, pos, text, radius=True)

    def auto_dimension(self, view) -> list[Dimension]:
        """Overall width and height, and each distinct hole diameter (SolidWorks' Model Items
        in spirit): a quick first set of dimensions to tidy up by hand."""
        v = self.view(view)
        g = self.geometry(v)
        lo, hi = g.lo, g.hi
        out = [self.add_linear(v, (lo[0], lo[1]), (hi[0], lo[1]), "h",
                               pos=((lo[0] + hi[0]) / 2, lo[1] - 12.0 / self.view_scale(v))),
               self.add_linear(v, (lo[0], lo[1]), (lo[0], hi[1]), "v",
                               pos=(lo[0] - 12.0 / self.view_scale(v), (lo[1] + hi[1]) / 2))]
        seen = []
        for c, r in sorted(g.circles, key=lambda cr: -cr[1]):
            if any(abs(r - r2) < 1e-6 for r2 in seen):
                continue
            seen.append(r)
            out.append(self.add_diameter(v, c, r))
        return out

    def _reattach(self, d: Dimension, old: ViewGeometry | None, new: ViewGeometry):
        """After a model change, move each dimension reference to the matching new point."""
        exact = max(self.model_size() * 1e-6, 1e-6)
        o_lo, o_span = (old.lo, np.maximum(old.hi - old.lo, 1e-9)) if old is not None else (new.lo, None)
        n_lo, n_span = new.lo, np.maximum(new.hi - new.lo, 1e-9)

        def match(p, cands):
            """Same point if it still exists, else the one in the same relative place in the
            view (a corner stays that corner, a centred hole stays centred)."""
            if not len(cands):
                return None
            dist = np.linalg.norm(cands - p, axis=1)
            k = int(np.argmin(dist))
            if dist[k] < exact or o_span is None:
                return cands[k] if dist[k] < max(self.model_size() * 0.02, 0.5) else None
            rel = (p - o_lo) / o_span
            rd = np.linalg.norm((cands - n_lo) / n_span - rel, axis=1)
            k = int(np.argmin(rd))
            return cands[k] if rd[k] < 0.15 else None

        if d.kind == "linear":
            pts = view_points(new)
            moved = []
            for attr in ("p1", "p2"):
                p = np.asarray(getattr(d, attr))
                q = match(p, pts)
                if q is not None:
                    setattr(d, attr, tuple(q))
                    moved.append(q - p)
            if moved:                                   # keep the dimension line's offset
                d.pos = tuple(np.asarray(d.pos) + np.mean(moved, axis=0))
        elif new.circles:
            c0 = np.asarray(d.center)
            centres = np.array([c for c, _ in new.circles])
            q = match(c0, centres)
            if q is not None:
                k = int(np.argmin(np.linalg.norm(centres - q, axis=1)))
                d.center, d.r = tuple(q), float(new.circles[k][1])
                d.pos = tuple(np.asarray(d.pos) + (q - c0))

    # ---- notes ----------------------------------------------------------------------------
    def add_note(self, text, at, height=NOTE_TEXT) -> Note:
        n = Note(self._new_id("N"), float(at[0]), float(at[1]), text, height)
        self.notes.append(n)
        return n

    # ---- rendering to primitives ----------------------------------------------------------
    def _assembly(self):
        from .assembly import Assembly
        path = self.model_path()
        if path is None or path.suffix.lower() != ".bcasm":
            raise DrawingError("A BOM needs a saved .bcasm assembly linked to this drawing")
        return Assembly.load(path)

    def add_bom(self, at=None):
        assembly = self._assembly()
        if self.bom is None:
            self.bom = BOMTable(self._new_id("B"))
            self.bom.x, self.bom.y = at or (self.size[0] - 190, self.size[1] - 22)
        elif at is not None:
            self.bom.x, self.bom.y = map(float, at)
        self.bom.refresh(assembly)
        return self.bom

    def refresh_bom(self):
        if self.bom is None:
            raise DrawingError("Insert a Bill of Materials first")
        return self.bom.refresh(self._assembly())

    def add_balloon(self, view, component, anchor, at):
        v = self.view(view)
        if v is None:
            raise DrawingError("Select a drawing view for the balloon")
        inst = self.bom.instances.get(component) if self.bom else None
        if inst is None:
            raise DrawingError("Insert a BOM and select an assembly component")
        anchor = np.asarray(anchor, float)
        if anchor.shape != (3,) or not np.isfinite(anchor).all():
            raise DrawingError("Balloon anchor must be a finite component-local 3D point")
        b = Balloon(self._new_id("L"), v.id, component, inst["instance_id"], inst["key"],
                    tuple(anchor), float(at[0]), float(at[1]))
        self.balloons.append(b)
        return b

    def balloon_target(self, b):
        inst = self.bom.instances.get(b.component) if self.bom else None
        v = self.view(b.view)
        if (not inst or v is None or inst["key"] != b.key or
                inst["instance_id"] != b.instance_id or inst["status"]):
            return "?", None
        from .assembly import Pose
        p = Pose.from_dict(inst["pose"]).point(b.anchor)
        _, x, y = geometry.view_frame(v.direction, v.xdir)
        return str(self.bom.numbers[b.key]), self.to_sheet(v, [p @ x, p @ y])

    def balloon_primitives(self, b):
        label, tip = self.balloon_target(b)
        c = np.array([b.x, b.y])
        out = [Arc(c, 4.5, 0, 360, "DIM", b.id),
               Text(c, label, 3.0, ha="center", va="center", owner=b.id)]
        if tip is not None:
            start = c + _unit(tip - c) * 4.5
            out += [Line(start, tip, "DIM", b.id), _arrow(tip, tip - start, b.id)]
        else:
            out.append(Text(c + [6, 0], "UNRESOLVED", 2.5, va="center", owner=b.id))
        return out

    def bom_primitives(self):
        import textwrap
        if self.bom is None:
            return []
        b = self.bom
        widths = [14, 48, 96, 16]
        cells = [["ITEM", "PART NUMBER", "DESCRIPTION", "QTY"]]
        for row in b.rows:
            r = b.displayed(row)
            cells.append([str(r["item"]) + ("!" if r["status"] else ""), r["part_number"],
                          r["description"] + (" [UNRESOLVED]" if r["status"] else ""),
                          str(r["quantity"])])
        out, y = [], b.y
        for values in cells:
            lines = [textwrap.wrap(str(s), max(1, int((w - 4) / (2.8 * 0.62)))) or [""]
                     for s, w in zip(values, widths)]
            h = max(len(s) for s in lines) * 4.5 + 4
            x = b.x
            out.append(Line(np.array([x, y]), np.array([x + sum(widths), y]), "THIN", b.id))
            for ls, w in zip(lines, widths):
                out.append(Line(np.array([x, y]), np.array([x, y - h]), "THIN", b.id))
                out.append(Text(np.array([x + 2, y - 2]), "\n".join(ls), 2.8,
                                va="top", owner=b.id))
                x += w
            out.append(Line(np.array([x, y]), np.array([x, y - h]), "THIN", b.id))
            y -= h
        out.append(Line(np.array([b.x, y]), np.array([b.x + sum(widths), y]), "THIN", b.id))
        return out

    def primitives(self, include_dims=True) -> list:
        out = self.sheet_primitives()
        for v in self.views:
            out += self.view_primitives(v)
        if include_dims:
            for d in self.dims:
                out += self.dim_primitives(d)
        for n in self.notes:
            out.append(Text(np.array([n.x, n.y]), n.text, n.height, n.angle, "left", "bottom",
                            "TEXT", n.id))
        out += self.bom_primitives()
        for b in self.balloons:
            out += self.balloon_primitives(b)
        return out

    def view_primitives(self, v: View, sheet=True) -> list:
        g = self.geometry(v)
        s = self.view_scale(v) if sheet else 1.0
        off = (np.array([v.x, v.y]) - s * g.center) if sheet else np.zeros(2)
        out = []
        for e in g.edges:
            layer = "HIDDEN" if not e.visible else ("TANGENT" if e.style == "smooth" else "VISIBLE")
            if e.kind == "line":
                out.append(Line(off + s * e.p0, off + s * e.p1, layer, v.id))
            elif e.kind == "circle":
                out.append(Arc(off + s * e.center, s * e.r, e.a0, e.a1, layer, v.id))
            else:
                out.append(Poly(off + s * e.pts, layer, v.id))
        if v.center_marks:
            n = np.asarray(v.direction)
            ortho = np.max(np.abs(n)) > 0.999
            for c, r in (g.circles if ortho else []):
                if r * s < 1.0:
                    continue
                cs, ext = off + s * c, r * s + 2.0
                out.append(Line(cs - [ext, 0], cs + [ext, 0], "CENTER", v.id))
                out.append(Line(cs - [0, ext], cs + [0, ext], "CENTER", v.id))
        show = v.label if v.label is not None else (v.scale is not None and
                                                    abs(v.scale - self.scale) > 1e-12)
        if sheet and show:
            x0, y0, x1, y1 = self.view_box(v)
            out.append(Text(np.array([(x0 + x1) / 2, y0 - 6.0]),
                            f"{v.name.upper()}\nSCALE {scale_text(self.view_scale(v))}",
                            3.5, 0, "center", "top", "TEXT", v.id))
        return out

    def dim_primitives(self, d: Dimension, sheet=True) -> list:
        v = self.view(d.view)
        if v is None:
            return []
        T = (lambda p: self.to_sheet(v, p)) if sheet else (lambda p: np.asarray(p, float))
        k = self.view_scale(v) if sheet else 1.0
        label = d.label(self.decimals)
        o = d.id
        out = []
        if d.kind == "linear":
            p1, p2, pos = T(d.p1), T(d.p2), T(d.pos)
            u = d.direction()
            n = _perp(u)
            off = float((pos - p1) @ n)
            a = p1 + n * off
            b = p1 + u * float((p2 - p1) @ u) + n * off
            for p, q in ((p1, a), (p2, b)):          # extension lines (q - p is along n)
                L = float((q - p) @ n)
                if abs(L) > EXT_GAP:
                    sgn = math.copysign(1.0, L)
                    out.append(Line(p + n * sgn * EXT_GAP, q + n * sgn * EXT_OVER, "DIM", o))
            L = np.linalg.norm(b - a)
            inside = L >= 2 * ARROW_LEN + 1.0
            dab = _unit(b - a) if L > 1e-9 else u
            out.append(Line(a, b, "DIM", o))
            if inside:
                out += [_arrow(a, -dab, o), _arrow(b, dab, o)]
            else:
                out += [_arrow(a, dab, o), _arrow(b, -dab, o),
                        Line(a - dab * (ARROW_LEN + 3), a, "DIM", o),
                        Line(b, b + dab * (ARROW_LEN + 3), "DIM", o)]
            ang = math.degrees(math.atan2(dab[1], dab[0]))
            if ang > 90.0 + 1e-6 or ang <= -90.0 + 1e-6:
                ang -= 180.0 if ang > 0 else -180.0
            t = math.radians(ang)
            up = np.array([-math.sin(t), math.cos(t)])
            mid = (a + b) / 2
            out.append(Text(mid + up * 1.0, label, DIM_TEXT, ang, "center", "bottom", "DIM", o))
        else:
            c, pos = T(d.center), T(d.pos)
            r = d.r * k
            dv = _unit(pos - c) if np.linalg.norm(pos - c) > 1e-9 else np.array([1.0, 0.0])
            dist = float(np.linalg.norm(pos - c))
            rim = c + dv * r
            if d.kind == "diameter" and dist < r:          # inside: full diameter line
                out += [Line(c - dv * r, c + dv * r, "DIM", o),
                        _arrow(c + dv * r, dv, o), _arrow(c - dv * r, -dv, o)]
                out.append(Text(pos + [0, 1.0], label, DIM_TEXT, 0, "center", "bottom", "DIM", o))
            else:
                if d.kind == "radius":
                    out.append(Line(c, pos, "DIM", o))
                    out.append(_arrow(rim, dv if dist > r else -dv, o) if dist > r
                               else _arrow(rim, dv, o))
                else:
                    far = c - dv * r                        # leader through the centre
                    out += [Line(far, pos, "DIM", o), _arrow(rim, -dv, o), _arrow(far, dv, o)]
                side = 1.0 if dv[0] >= 0 else -1.0
                tw = len(label) * DIM_TEXT * 0.62 + 1.0
                out.append(Line(pos, pos + [side * tw, 0], "DIM", o))
                out.append(Text(pos + [side * tw / 2, 0.8], label, DIM_TEXT, 0, "center",
                                "bottom", "DIM", o))
        return out

    def sheet_primitives(self) -> list:
        W, H = self.size
        x0, y0, x1, y1 = self.frame
        out = [Poly(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), "BORDER", "sheet", True)]
        # centring marks (ISO 5457)
        for p, q in (((W / 2, y0), (W / 2, y0 - 5)), ((W / 2, y1), (W / 2, y1 + 5)),
                     ((x0, H / 2), (x0 - 5, H / 2)), ((x1, H / 2), (x1 + 5, H / 2))):
            out.append(Line(np.array(p), np.array(q), "BORDER", "sheet"))
        out += self.title_block_primitives()
        return out

    def title_cells(self):
        """(x, y, w, h, caption, text, text height) of the title block cells."""
        x1, y0 = self.frame[2], self.frame[1]
        bx = x1 - self.TB_W
        t = self.title
        W, H = SHEET_SIZES[self.sheet]
        cells = [
            (0, 0, 40, 8, "Drawn by", t.get("drawn_by", ""), 2.5),
            (40, 0, 40, 8, "Checked by", t.get("checked_by", ""), 2.5),
            (80, 0, 25, 8, "Date", t.get("date", ""), 2.5),
            (105, 0, 20, 8, "Scale", scale_text(self.scale), 2.5),
            (125, 0, 25, 8, "Size / Sheet", f"{self.sheet}   1 / 1", 2.5),
            (150, 0, 30, 8, "Projection", "", 2.5),
            (0, 8, 65, 8, "Material", t.get("material", ""), 2.5),
            (65, 8, 40, 8, "Finish", t.get("finish", ""), 2.5),
            (105, 8, 20, 8, "Units", "mm", 2.5),
            (125, 8, 40, 8, "Drawing No.", t.get("drawing_no", ""), 2.5),
            (165, 8, 15, 8, "Rev.", t.get("revision", ""), 2.5),
            (0, 16, 60, 16, "Company", t.get("company", ""), 3.5),
            (60, 16, 120, 16, "Title", t.get("title", ""), 5.0),
        ]
        return [(bx + cx, y0 + cy, cw, ch, cap, txt, th) for cx, cy, cw, ch, cap, txt, th in cells]

    def title_block_primitives(self) -> list:
        out = []
        for x, y, w, h, cap, txt, th in self.title_cells():
            out.append(Poly(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]), "FRAME",
                            "title", True))
            out.append(Text(np.array([x + 1.0, y + h - 0.8]), cap, 1.8, 0, "left", "top", "THIN",
                            "title"))
            if txt:
                size = th
                while len(txt) * size * 0.62 > w - 2 and size > 1.8:
                    size -= 0.25
                base = y + (1.3 if h < 12 else (h - 2.6 - size) / 2)
                out.append(Text(np.array([x + 1.5, base]), txt, size, 0, "left", "bottom",
                                "TEXT", "title"))
            if cap == "Projection":
                out += self._projection_symbol(x + w / 2, y + h / 2 - 0.8)
        return out

    def _projection_symbol(self, cx, cy):
        """ISO 128 projection symbol: a truncated cone and its end view. First angle: the
        circles to the right of the cone; third angle: to the left."""
        o = "title"
        d1, d2, L = 5.0, 2.6, 6.0         # big/small diameters, cone length
        first = self.projection != "third"
        cone_cx, circ_cx = (cx - 4.5, cx + 4.5) if first else (cx + 4.5, cx - 4.5)
        xa, xb = cone_cx - L / 2, cone_cx + L / 2          # small end left, big end right
        pts = np.array([[xa, cy - d2 / 2], [xb, cy - d1 / 2], [xb, cy + d1 / 2], [xa, cy + d2 / 2]])
        out = [Poly(pts, "THIN", o, True),
               Arc(np.array([circ_cx, cy]), d1 / 2, 0, 360, "THIN", o),
               Arc(np.array([circ_cx, cy]), d2 / 2, 0, 360, "THIN", o),
               Line(np.array([xa - 1.2, cy]), np.array([xb + 1.2, cy]), "CENTER", o),
               Line(np.array([circ_cx - d1 / 2 - 1, cy]), np.array([circ_cx + d1 / 2 + 1, cy]), "CENTER", o),
               Line(np.array([circ_cx, cy - d1 / 2 - 1]), np.array([circ_cx, cy + d1 / 2 + 1]), "CENTER", o)]
        return out

    # ---- picking helpers (for the GUI) ------------------------------------------------------
    def pick_entity(self, sheet_pt, tol, quadrants=False) -> tuple | None:
        """Nearest model point or edge under a sheet point, for dimensioning:
        ("point", view, p) | ("circle", view, (c, r)) | ("line", view, (p0, p1)) | None.
        Coordinates are view coordinates. Points (vertices, centres) win over edges; arc
        quadrant points only with quadrants=True (Shift-click, SolidWorks' max/min arc
        condition), so a plain click on a circle's rim picks the circle."""
        best = None
        sp = np.asarray(sheet_pt, float)
        for v in self.views:
            x0, y0, x1, y1 = self.view_box(v)
            if not (x0 - tol <= sp[0] <= x1 + tol and y0 - tol <= sp[1] <= y1 + tol):
                continue
            g = self.geometry(v)
            s = self.view_scale(v)
            q = self.to_view(v, sp)
            t = tol / s
            pts = view_points(g, quadrants=quadrants)
            if len(pts):
                dd = np.linalg.norm(pts - q, axis=1)
                k = int(np.argmin(dd))
                if dd[k] < t and (best is None or dd[k] * 0.5 < best[0]):
                    best = (dd[k] * 0.5, ("point", v, pts[k]))      # points win over edges
            for e in g.edges:
                dist = _dist_to_poly(q, e.points())
                if dist < t and (best is None or dist < best[0]):
                    if e.kind == "circle":
                        ent = ("circle", v, (e.center, e.r, e.a1 - e.a0 >= 360 - 1e-6))
                    elif e.kind == "line":
                        ent = ("line", v, (e.p0, e.p1))
                    else:
                        continue
                    best = (dist, ent)
        return best[1] if best else None

    def hit(self, sheet_pt, tol):
        """Topmost drawing object at a sheet point: ("dim", Dimension) | ("note", Note) |
        ("view", View) | None."""
        sp = np.asarray(sheet_pt, float)
        for b in reversed(self.balloons):
            if any(_prim_distance(p, sp) < tol for p in self.balloon_primitives(b)):
                return "balloon", b
        if self.bom and any(_prim_distance(p, sp) < tol for p in self.bom_primitives()):
            return "bom", self.bom
        for d in reversed(self.dims):
            for p in self.dim_primitives(d):
                if _prim_distance(p, sp) < tol:
                    return ("dim", d)
        for n in reversed(self.notes):
            box = Text(np.array([n.x, n.y]), n.text, n.height, n.angle).box()
            if _in_poly(sp, box, tol):
                return ("note", n)
        for v in reversed(self.views):
            x0, y0, x1, y1 = self.view_box(v)
            if x0 - tol <= sp[0] <= x1 + tol and y0 - tol <= sp[1] <= y1 + tol:
                return ("view", v)
        return None

    # ---- persistence ---------------------------------------------------------------------
    def to_dict(self):
        model = self.model
        if model and self.path is not None:
            try:
                model = os.path.relpath(Path(model).resolve() if Path(model).is_absolute()
                                        else self.path.parent / model, self.path.parent)
            except ValueError:            # another drive
                pass
        return {"format": "bauyrcad-drawing", "version": FORMAT_VERSION, "model": model,
                "sheet": self.sheet, "portrait": self.portrait, "scale": self.scale,
                "projection": self.projection, "decimals": self.decimals,
                "title": self.title, "views": [v.to_dict() for v in self.views],
                "dims": [d.to_dict() for d in self.dims],
                "notes": [n.to_dict() for n in self.notes],
                "bom": self.bom.to_dict() if self.bom else None,
                "balloons": [b.to_dict() for b in self.balloons],
                # a model that only exists in memory (scripting) travels inside the file
                "embedded_brep": (geometry.shape_to_brep_text(self.shape_override)
                                  if self.shape_override is not None and not self.model else None)}

    @classmethod
    def from_dict(cls, d, path=None) -> "Drawing":
        dr = cls(None, d.get("sheet", "A3"), d.get("portrait", False), d.get("scale", 1.0),
                 d.get("projection", "first"))
        dr.model = d.get("model")
        dr.path = Path(path) if path else None
        dr.decimals = d.get("decimals", 2)
        dr.title.update(d.get("title", {}))
        dr.views = [View.from_dict(v) for v in d.get("views", [])]
        dr.dims = [Dimension.from_dict(x) for x in d.get("dims", [])]
        dr.notes = [Note(**n) for n in d.get("notes", [])]
        dr.bom = BOMTable(**d["bom"]) if d.get("bom") else None
        dr.balloons = [Balloon(**b) for b in d.get("balloons", [])]
        if d.get("embedded_brep"):
            dr.shape_override = geometry.shape_from_brep_text(d["embedded_brep"])
        dr._counter = len(dr.views) + len(dr.dims) + len(dr.notes)
        return dr

    def save(self, path):
        path = Path(path)
        if path.suffix.lower() != FILE_EXTENSION:
            path = path.with_suffix(FILE_EXTENSION)
        if self.model and self.path is None and not Path(self.model).is_absolute():
            self.model = str(Path(self.model).resolve())
        elif self.model and self.path is not None and not Path(self.model).is_absolute():
            self.model = str((self.path.parent / self.model).resolve())
        self.path = path
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path) -> "Drawing":
        path = Path(path)
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("format") != "bauyrcad-drawing":
            raise DrawingError(f"{path.name} is not a BauyrCAD drawing")
        return cls.from_dict(d, path)

    # ---- export ------------------------------------------------------------------------------
    def export(self, path, view=None):
        """Write the drawing: .pdf .svg .png (vector sheet), .dxf, .dwg. view= a view id to
        write only that view at 1:1 in model millimetres (DXF/DWG, e.g. for laser cutting)."""
        path = Path(path)
        ext = path.suffix.lower()
        if ext in (".pdf", ".svg", ".png"):
            from .drawing_export import write_matplotlib
            write_matplotlib(self, path)
        elif ext == ".dxf":
            from .drawing_export import write_dxf
            write_dxf(self, path, view)
        elif ext == ".dwg":
            from .drawing_export import write_dwg
            write_dwg(self, path, view)
        else:
            raise DrawingError(f"Unsupported drawing format {ext!r} (use .pdf .dxf .dwg .svg .png)")
        return path


EXPORT_FILTERS = [("pdf", "PDF (*.pdf)"), ("dxf", "AutoCAD DXF (*.dxf)"),
                  ("dwg", "AutoCAD DWG (*.dwg)"), ("svg", "SVG (*.svg)"), ("png", "PNG image (*.png)")]


def orientation_name(direction, xdir=None) -> str:
    n = _unit(np.asarray(direction, float))
    for name, (d, _) in ORIENTATIONS.items():
        if np.allclose(n, _unit(np.asarray(d, float)), atol=1e-6):
            return name.capitalize()
    return "Projected"


def view_points(g: ViewGeometry, quadrants=True) -> np.ndarray:
    """Snap points of a view: vertices (line/arc ends), circle centres and, with quadrants,
    the quadrant points arcs pass (for exact sizes over rounded ends)."""
    pts = []
    for e in g.edges:
        if e.kind == "line":
            pts += [e.p0, e.p1]
        elif e.kind == "circle":
            pts.append(e.center)
            if quadrants:
                pts += list(e.extent_points())      # arc ends + quadrant points (max sizes)
            elif e.a1 - e.a0 < 360 - 1e-6:
                P = e.points()
                pts += [P[0], P[-1]]                # arc ends only
        else:
            pts += [e.pts[0], e.pts[-1]]
    if not pts:
        return np.zeros((0, 2))
    pts = np.array(pts)
    # merge duplicates
    keep = []
    for p in pts:
        if not any(np.linalg.norm(p - q) < 1e-6 for q in keep):
            keep.append(p)
    return np.array(keep)


# =====================================================================================
# hidden line clean-up
# =====================================================================================
def clean_edges(edges, tol):
    """Remove hidden lines that lie on visible (or already kept hidden) lines, as a
    drawing shows them: HLR reports e.g. the back rim of a through hole under the front rim."""
    out_vis, hidden = [], []
    for e in edges:
        e = _straighten(e, tol)
        (out_vis if e.visible else hidden).append(e)
    cover_lines = [(e.p0, e.p1) for e in out_vis if e.kind == "line"]
    cover_arcs = [(e.center, e.r, e.a0, e.a1) for e in out_vis if e.kind == "circle"]
    cover_polys = [e.points() for e in out_vis]
    out_hid = []
    # longest first, so short duplicates fold into long ones
    hidden.sort(key=lambda e: -_length(e))
    for e in hidden:
        pieces = []
        if e.kind == "line":
            for a, b in _subtract_line(e.p0, e.p1, cover_lines, tol):
                pieces.append(geometry.ProjectedEdge("line", False, e.style, p0=a, p1=b))
                cover_lines.append((a, b))
        elif e.kind == "circle":
            for a0, a1 in _subtract_arc(e.center, e.r, e.a0, e.a1, cover_arcs, tol):
                pieces.append(geometry.ProjectedEdge("circle", False, e.style, center=e.center,
                                                     r=e.r, a0=a0, a1=a1))
                cover_arcs.append((e.center, e.r, a0, a1))
        else:
            for run in _subtract_poly(e.pts, cover_polys + [np.array(l) for l in cover_lines], tol):
                pieces.append(geometry.ProjectedEdge("poly", False, e.style, pts=run))
        for p in pieces:
            cover_polys.append(p.points())
        out_hid += pieces
    return out_vis + out_hid


def _length(e):
    P = e.points()
    return float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1)))


def _straighten(e, tol):
    if e.kind != "poly" or len(e.pts) < 2:
        return e
    a, b = e.pts[0], e.pts[-1]
    L = np.linalg.norm(b - a)
    if L < tol:
        return e
    u = (b - a) / L
    rel = e.pts - a
    if np.max(np.abs(rel[:, 0] * u[1] - rel[:, 1] * u[0])) < tol:
        t = rel @ u
        return geometry.ProjectedEdge("line", e.visible, e.style, p0=a + u * t.min(), p1=a + u * t.max())
    return e


def _merge(iv):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _subtract_line(p0, p1, covers, tol):
    d = p1 - p0
    L = float(np.linalg.norm(d))
    if L < tol:
        return []
    u = d / L
    iv = []
    for a, b in covers:
        ab = b - a
        lab = np.linalg.norm(ab)
        if lab < tol:
            continue
        if abs(u[0] * ab[1] - u[1] * ab[0]) / lab > 1e-4:
            continue
        ra = a - p0
        if abs(ra[0] * u[1] - ra[1] * u[0]) > tol:
            continue
        ta, tb = float(ra @ u), float((b - p0) @ u)
        lo, hi = max(min(ta, tb), 0.0), min(max(ta, tb), L)
        if hi > lo:
            iv.append((lo - tol, hi + tol))
    pieces, t = [], 0.0
    for lo, hi in _merge(iv):
        if lo > t + 2 * tol:
            pieces.append((p0 + u * t, p0 + u * min(lo, L)))
        t = max(t, hi)
    if L > t + 2 * tol:
        pieces.append((p0 + u * t, p1))
    return pieces


def _subtract_arc(c, r, a0, a1, covers, tol):
    span = a1 - a0
    iv = []
    for cc, rr, b0, b1 in covers:
        if np.linalg.norm(cc - c) > tol or abs(rr - r) > tol:
            continue
        s = (b0 - a0) % 360.0
        e = s + (b1 - b0)
        for k in (-360.0, 0.0, 360.0):
            lo, hi = max(s + k, 0.0), min(e + k, span)
            if hi > lo:
                iv.append((lo, hi))
    eps = math.degrees(tol / max(r, 1e-9)) + 1e-6
    pieces, t = [], 0.0
    for lo, hi in _merge([(lo - eps, hi + eps) for lo, hi in iv]):
        if lo > t + 2 * eps:
            pieces.append((a0 + t, a0 + lo))
        t = max(t, hi)
    if span > t + 2 * eps:
        pieces.append((a0 + t, a1))
    return pieces


def _seg_dist(P, A, B):
    """Distances of points P (n,2) to segments A->B (m,2): (n, m)."""
    AB = B - A
    L2 = np.maximum(np.sum(AB * AB, axis=1), 1e-30)
    AP = P[:, None, :] - A[None, :, :]
    t = np.clip(np.sum(AP * AB[None], axis=2) / L2[None], 0, 1)
    proj = A[None] + t[..., None] * AB[None]
    return np.linalg.norm(P[:, None, :] - proj, axis=2)


def _subtract_poly(pts, covers, tol):
    if not covers:
        return [pts]
    A = np.vstack([c[:-1] for c in covers if len(c) >= 2])
    B = np.vstack([c[1:] for c in covers if len(c) >= 2])
    covered = _seg_dist(pts, A, B).min(axis=1) < tol * 3
    runs, cur = [], []
    for p, cv in zip(pts, covered):
        if cv:
            if len(cur) >= 2:
                runs.append(np.array(cur))
            cur = []
        else:
            cur.append(p)
    if len(cur) >= 2:
        runs.append(np.array(cur))
    return runs


def _dist_to_poly(q, P) -> float:
    if len(P) < 2:
        return float(np.linalg.norm(P[0] - q)) if len(P) else 1e30
    return float(_seg_dist(np.asarray(q, float)[None], P[:-1], P[1:]).min())


def _in_poly(p, box, tol) -> bool:
    lo, hi = box.min(0) - tol, box.max(0) + tol
    return bool(np.all(p >= lo) and np.all(p <= hi))


def _prim_distance(p, q) -> float:
    if isinstance(p, Line):
        return _dist_to_poly(q, np.array([p.a, p.b]))
    if isinstance(p, Arc):
        return _dist_to_poly(q, p.points())
    if isinstance(p, Poly):
        P = np.vstack([p.pts, p.pts[:1]]) if p.closed else p.pts
        return _dist_to_poly(q, P)
    if isinstance(p, Text):
        return 0.0 if _in_poly(q, p.box(), 0.0) else 1e30
    if isinstance(p, Solid):
        return 0.0 if _in_poly(q, p.pts, 0.3) else 1e30
    return 1e30
