"""Sketch mode: SolidWorks-style sketch tools, relations, Smart Dimension and dragging.

Colours follow SolidWorks: blue = under defined, black = fully defined, dashed = construction,
orange = hover / preview, light blue = selected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..core.features import SketchFeature
from ..core.plane import REFERENCE_PLANES, SketchPlane
from ..core.sketch import (Arc, Circle, Constraint, Line, PointEntity, Sketch, SketchError,
                           Spline, builders, ops, solver)
from ..core.sketch.constraints import (RELATION_NAMES, TYPES, applicable_relations,
                                       format_value, is_dimension, make_relation, measure)
from . import sketch_draw as sd
from .command_line import evaluate
from .commands import Command, hint, spin
from .icons import icon
from .command_help import tooltip

SNAP_PX = 10
SNAP_LABELS = {"point": "Endpoint", "center": "Center", "mid": "Midpoint",
               "quadrant": "Quadrant", "intersection": "Intersection", "curve": "Nearest",
               "tangent": "Tangent", "perpendicular": "Perpendicular", "parallel": "Parallel",
               "angle": "Angle",
               "model_point": "Model vertex", "model_center": "Model center",
               "model_mid": "Model midpoint", "model_quad": "Model quadrant",
               "model_edge": "On model edge"}
# SolidWorks Quick Snaps: each can be switched off, or forced alone for the next click
SNAP_TYPES = [("point", "Point (endpoints)"), ("center", "Center Point"), ("mid", "Midpoint"),
              ("quadrant", "Quadrant"), ("intersection", "Intersection"),
              ("curve", "Nearest (on a curve)"), ("tangent", "Tangent"),
              ("perpendicular", "Perpendicular"), ("parallel", "Parallel"),
              ("hv", "Horizontal / Vertical"), ("align", "Horizontal / Vertical to Points"),
              ("angle", "Angle"), ("model", "Model edges and points"), ("grid", "Grid")]
SNAP_DEFAULT_OFF = {"grid"}           # the grid snap has its own checkbox (Snap to grid)
# priority when two snap targets are equally near (lower wins)
SNAP_RANK = {"intersection": 0, "model_point": 1, "model_center": 1, "mid": 2, "model_mid": 2,
             "quadrant": 3, "model_quad": 3}
PICK_PX = 8
C_UNDER, C_DEF, C_SEL, C_HOVER = "#1f5fd6", "#141414", "#00a2ff", "#ff8a00"
C_PREV, C_DIM, C_DRIVEN, C_REL = "#ff8a00", "#1d1d1d", "#808080", "#2d8a47"
C_ERR, C_GUIDE, C_BOX = "#e0162b", "#5c8fd6", "#2f7de1"
C_REGION, REGION_OPACITY = "#6fa8ff", 0.30     # shading of closed (extrudable) areas


@dataclass
class Snap:
    uv: np.ndarray
    kind: str = "free"          # point | center | mid | quadrant | intersection | curve |
    #                             model_point | model_center | model_mid | model_quad |
    #                             model_edge | h | v | align | grid | free | typed
    ref: tuple | None = None
    guides: list = field(default_factory=list)


# ============================================================================
# Tools
# ============================================================================

class Tool:
    key = "tool"
    label = "Tool"
    icon = ""
    n = 0                       # clicks needed (0 = tool handles clicks itself)
    commit_mask = ()            # which clicks become sketch points (vs. location only)
    prompts = ("Click",)
    numeric = True              # accepts typed coordinates / values
    drag_click = False          # release after a drag counts as a click (click-drag drawing)
    snapping = True
    dyn_fields = ()             # AutoCAD dynamic input: values typed after the first point

    def __init__(self, cmd):
        self.cmd = cmd
        self.pts: list[Snap] = []
        self.cursor: Snap | None = None
        self.locked: dict[int, float] = {}      # dynamic-input values (field index -> value)
        self._typed: dict[int, float] = {}      # the values the last shape was built with

    @property
    def sk(self) -> Sketch:
        return self.cmd.sketch

    # ---- hooks -------------------------------------------------------------------------
    def activate(self):
        pass

    def deactivate(self):
        pass

    def options(self):
        return None

    def anchor(self):
        return self.pts[-1].uv if self.pts else None

    def prompt(self) -> str:
        return self.prompts[min(len(self.pts), len(self.prompts) - 1)]

    def press(self, s: Snap, ev):
        self.click(s)

    def release(self, s: Snap, ev):
        if ev.dragging and self.drag_click and self.pts:
            self.click(s)

    def move(self, s: Snap, ev):
        self.cursor = self.constrain(s)

    def constrain(self, s: Snap) -> Snap:
        """The cursor with the typed dynamic-input values applied (see dyn_fields)."""
        return s

    def click(self, s: Snap):
        self._typed = {}
        if self.pts and self.locked:
            s = self.constrain(s)
            self._typed = dict(self.locked)
        self.pts.append(s)
        if self.n and len(self.pts) >= self.n:
            snaps, self.pts = self.pts, []
            self.cmd.apply(lambda sk: self.create(sk, snaps), self.label)
            self.locked = {}
            self.cmd.dynamic_input_done()

    def finish(self) -> bool:
        had = bool(self.pts)
        self.pts = []
        self.locked = {}
        self.cmd.dynamic_input_done()
        return had

    def typed_dims(self) -> bool:
        return bool(self._typed) and self.cmd.dim_typed

    def escape(self) -> bool:
        return self.finish()

    def enter(self) -> bool:
        return self.finish()

    def typed(self, text: str):
        base = self.anchor()
        cur = self.cursor.uv if self.cursor else None
        p = self.cmd.parse_point(text, base, cur)
        self.click(Snap(p, "typed"))

    # ---- creation --------------------------------------------------------------------------
    def args(self, sk, snaps):
        return [self.cmd.commit(s, sk) if (i < len(self.commit_mask) and self.commit_mask[i])
                else tuple(s.uv) for i, s in enumerate(snaps)]

    def build(self, sk, args):
        raise NotImplementedError

    def create(self, sk, snaps):
        return self.build(sk, self.args(sk, snaps))

    def preview(self):
        """Polylines to draw for the operation in progress."""
        if not self.pts or self.cursor is None or not self.n:
            return []
        snaps = self.pts + [self.cursor]
        if len(snaps) < self.n:
            return [np.array([s.uv for s in snaps])]
        return self.cmd.preview_of(lambda sk: self.create(sk, snaps))


def _nearest_on_polyline(P, q):
    """Closest point to q on a polyline P (n, 2)."""
    A, B = P[:-1], P[1:]
    AB = B - A
    L2 = np.maximum(np.sum(AB * AB, axis=1), 1e-300)
    t = np.clip(np.sum((q - A) * AB, axis=1) / L2, 0.0, 1.0)
    X = A + t[:, None] * AB
    return X[int(np.argmin(np.linalg.norm(X - q, axis=1)))]


def _sign(v):
    return 1.0 if v >= 0 else -1.0


def _typed_side(value, cursor_delta):
    """A typed length follows the cursor's side; a negative one goes the other way."""
    return value if value < 0 else _sign(cursor_delta) * value


def _dim_edge(sk, line, kind, value, label):
    """Driving horizontal / vertical dimension on a line (as Smart Dimension makes it)."""
    p, q = sk.pt(line.p1), sk.pt(line.p2)
    ax = 0 if kind == "hdistance" else 1
    sk.add_constraint(kind, [("e", line.id)], value=abs(value), label=label, s=_sign(q[ax] - p[ax]))


class LineTool(Tool):
    key, label, icon = "line", "Line", "line"
    construction = False
    drag_click = True
    prompts = ("Line: first point",
               "Next point  [x,y | @dx,dy | @length<angle | type a length: Tab to the angle]")
    dyn_fields = ("Length", "Angle")

    def activate(self):
        self.last = None
        self.first = None
        self._start_drag = False

    def press(self, s, ev):
        # A first-point drag draws a line. In an existing chain the press already
        # commits an endpoint; releasing after mouse jitter must not add another.
        self._start_drag = not self.pts
        self.click(s)

    def release(self, s, ev):
        started = self._start_drag
        self._start_drag = False
        if started and ev.dragging and self.pts:
            self.click(s)

    def constrain(self, s):
        if not self.pts or not self.locked:
            return s
        a = self.pts[-1].uv
        d = s.uv - a
        ang = math.radians(self.locked[1]) if 1 in self.locked else math.atan2(d[1], d[0])
        L = self.locked.get(0, float(np.linalg.norm(d)))
        return Snap(a + L * np.array([math.cos(ang), math.sin(ang)]), "typed")

    def click(self, s):
        if not self.pts:
            self.pts = [s]
            self.last = None
            return
        typed = {}
        if self.locked:
            s = self.constrain(s)
            typed = dict(self.locked)
        a_snap = self.pts[0] if self.last is None else None

        def make(sk):
            if a_snap is not None:
                self.first = self.last = self.cmd.commit(a_snap, sk)
            pid = self.cmd.commit(s, sk)
            (ln,) = builders.line(sk, self.last, pid, self.construction)
            self.cmd.snap_relation(sk, ln, s)
            if 0 in typed and self.cmd.dim_typed and not self.construction:
                m = (sk.pt(ln.p1) + sk.pt(ln.p2)) / 2
                sk.add_constraint("distance", [("e", ln.id)], value=abs(typed[0]),
                                  label=tuple(m + np.array([0.0, 0.08 * abs(typed[0])])))
            return pid

        pid = self.cmd.apply(make, self.label)
        self.locked = {}
        self.cmd.dynamic_input_done()
        if pid is None:
            if a_snap is not None:
                self.last = None
            return
        closed = pid == self.first
        self.last = pid
        self.pts.append(s)
        if closed:
            self.pts, self.last = [], None

    def finish(self):
        had = bool(self.pts)
        self.pts, self.last = [], None
        self._start_drag = False
        return had

    def preview(self):
        if self.pts and self.cursor is not None:
            return [np.array([self.pts[-1].uv, self.cursor.uv])]
        return []


class CenterlineTool(LineTool):
    key, label, icon = "centerline", "Centerline", "centerline"
    construction = True


class MidpointLineTool(Tool):
    key, label, icon = "midpoint_line", "Midpoint Line", "midpoint_line"
    n, commit_mask = 2, (True, True)
    prompts = ("Midpoint Line: midpoint", "End point")

    def build(self, sk, a):
        return builders.midpoint_line(sk, a[0], a[1])


class CornerRectTool(Tool):
    key, label, icon = "rect_corner", "Corner Rectangle", "rectangle"
    n, commit_mask = 2, (True, True)
    drag_click = True
    prompts = ("Corner Rectangle: first corner",
               "Opposite corner  [x,y | @width,height | type the width, Tab, the height, Enter]")
    dyn_fields = ("Width", "Height")

    def constrain(self, s):
        if not self.pts or not self.locked:
            return s
        a = self.pts[0].uv
        c = np.array(s.uv, float)
        d = c - a
        if 0 in self.locked:
            c[0] = a[0] + _typed_side(self.locked[0], d[0])
        if 1 in self.locked:
            c[1] = a[1] + _typed_side(self.locked[1], d[1])
        return Snap(c, "typed")

    def build(self, sk, a):
        L = builders.corner_rectangle(sk, a[0], a[1])
        if self.typed_dims():
            p1, p3 = sk.pt(L[0].p1), sk.pt(L[1].p2)
            w, h = p3 - p1
            off = 0.12 * max(abs(w), abs(h))
            if 0 in self._typed:
                m = (sk.pt(L[0].p1) + sk.pt(L[0].p2)) / 2
                _dim_edge(sk, L[0], "hdistance", w, tuple(m - [0, off * _sign(h)]))
            if 1 in self._typed:
                m = (sk.pt(L[1].p1) + sk.pt(L[1].p2)) / 2
                _dim_edge(sk, L[1], "vdistance", h, tuple(m + [off * _sign(w), 0]))
        return L


class CenterRectTool(Tool):
    key, label, icon = "rect_center", "Center Rectangle", "rect_center"
    n, commit_mask = 2, (True, False)
    drag_click = True
    prompts = ("Center Rectangle: centre",
               "Corner  [x,y | @half-width,half-height | type the width, Tab, the height]")
    dyn_fields = ("Width", "Height")

    def constrain(self, s):
        if not self.pts or not self.locked:
            return s
        c0 = self.pts[0].uv
        c = np.array(s.uv, float)
        d = c - c0
        if 0 in self.locked:
            c[0] = c0[0] + _sign(d[0]) * abs(self.locked[0]) / 2
        if 1 in self.locked:
            c[1] = c0[1] + _sign(d[1]) * abs(self.locked[1]) / 2
        return Snap(c, "typed")

    def build(self, sk, a):
        out = builders.center_rectangle(sk, a[0], a[1])
        if self.typed_dims():
            L = out[:4]
            w = sk.pt(L[0].p2)[0] - sk.pt(L[0].p1)[0]
            h = sk.pt(L[1].p2)[1] - sk.pt(L[1].p1)[1]
            off = 0.12 * max(abs(w), abs(h))
            if 0 in self._typed:
                m = (sk.pt(L[0].p1) + sk.pt(L[0].p2)) / 2
                _dim_edge(sk, L[0], "hdistance", w, tuple(m - [0, off]))
            if 1 in self._typed:
                m = (sk.pt(L[1].p1) + sk.pt(L[1].p2)) / 2
                _dim_edge(sk, L[1], "vdistance", h, tuple(m + [off, 0]))
        return out


class ThreePointRectTool(Tool):
    key, label, icon = "rect_3pt", "3 Point Corner Rectangle", "rect_3pt"
    n, commit_mask = 3, (True, True, False)
    prompts = ("3 Point Rectangle: first corner", "Second corner (edge)", "Width")

    def build(self, sk, a):
        return builders.three_point_rectangle(sk, *a)


class ThreePointCenterRectTool(Tool):
    key, label, icon = "rect_3pt_center", "3 Point Center Rectangle", "rect_3pt_center"
    n, commit_mask = 3, (True, False, False)
    prompts = ("3 Point Center Rectangle: centre", "Edge midpoint", "Corner")

    def build(self, sk, a):
        return builders.three_point_center_rectangle(sk, *a)


class ParallelogramTool(Tool):
    key, label, icon = "parallelogram", "Parallelogram", "parallelogram"
    n, commit_mask = 3, (True, True, True)
    prompts = ("Parallelogram: first corner", "Second corner", "Third corner")

    def build(self, sk, a):
        return builders.parallelogram(sk, *a)


class SlotTool(Tool):
    key, label, icon = "slot", "Straight Slot", "slot"
    n, commit_mask = 3, (True, True, False)
    prompts = ("Straight Slot: first arc centre", "Second arc centre", "Slot width (radius)")

    def build(self, sk, a):
        return builders.straight_slot(sk, *a)


class CenterSlotTool(Tool):
    key, label, icon = "slot_center", "Centerpoint Straight Slot", "slot_center"
    n, commit_mask = 3, (True, False, False)
    prompts = ("Centerpoint Slot: slot centre", "Arc centre (half length)", "Slot width (radius)")

    def build(self, sk, a):
        return builders.center_slot(sk, *a)


class CircleTool(Tool):
    key, label, icon = "circle", "Circle", "circle"
    n, commit_mask = 2, (True, False)
    drag_click = True
    prompts = ("Circle: centre", "Radius  [radius | point | type the diameter, Enter]")
    dyn_fields = ("Diameter",)

    def constrain(self, s):
        if not self.pts or 0 not in self.locked:
            return s
        c0 = self.pts[0].uv
        d = s.uv - c0
        n = float(np.linalg.norm(d))
        u = d / n if n > 1e-12 else np.array([1.0, 0.0])
        return Snap(c0 + u * abs(self.locked[0]) / 2, "typed")

    def create(self, sk, snaps):
        (c,) = builders.circle(sk, self.cmd.commit(snaps[0], sk), tuple(snaps[1].uv))
        if snaps[1].kind == "point":
            sk.add_constraint("coincident", [snaps[1].ref, ("e", c.id)])
        if self.typed_dims() and 0 in self._typed:
            cen = sk.pt(c.center)
            sk.add_constraint("diameter", [("e", c.id)], value=abs(self._typed[0]),
                              label=tuple(cen + np.array([0.7, 0.7]) * abs(self._typed[0])))
        return [c]


class PerimeterCircleTool(Tool):
    key, label, icon = "circle_perimeter", "Perimeter Circle", "circle_perimeter"
    n = 3
    prompts = ("Perimeter Circle: first point", "Second point", "Third point")

    def create(self, sk, snaps):
        (c,) = builders.perimeter_circle(sk, *(tuple(s.uv) for s in snaps))
        for s in snaps:
            if s.kind == "point":
                sk.add_constraint("coincident", [s.ref, ("e", c.id)])
        return [c]


class CenterArcTool(Tool):
    key, label, icon = "arc_center", "Centerpoint Arc", "arc_center"
    n, commit_mask = 3, (True, True, False)
    prompts = ("Centerpoint Arc: centre", "Start point (sets radius)", "End point (move to set direction)")

    def activate(self):
        self.sweep = 0.0
        self.prev = None

    def move(self, s, ev):
        super().move(s, ev)
        if len(self.pts) == 2:
            c = self.pts[0].uv
            ang = math.atan2(*(s.uv - c)[::-1])
            if self.prev is not None:
                d = (ang - self.prev + math.pi) % (2 * math.pi) - math.pi
                self.sweep += d
            self.prev = ang
        else:
            self.sweep, self.prev = 0.0, None

    def create(self, sk, snaps):
        c = self.cmd.commit(snaps[0], sk)
        st = self.cmd.commit(snaps[1], sk)
        end = snaps[2].ref[1] if snaps[2].kind == "point" else tuple(snaps[2].uv)
        return builders.center_arc(sk, c, st, end, ccw=self.sweep >= 0)


class TangentArcTool(Tool):
    key, label, icon = "arc_tangent", "Tangent Arc", "arc_tangent"
    prompts = ("Tangent Arc: click the end of a line or arc", "End point")

    def click(self, s):
        if not self.pts:
            if s.kind != "point":
                self.cmd.message("Start a tangent arc on the endpoint of a line or arc")
                return
            pid = s.ref[1]
            owner = [e for e in self.sk.entities_using_point(pid)
                     if isinstance(e, (Line, Arc)) and pid in e.end_ids()]
            if not owner:
                self.cmd.message("Start a tangent arc on the endpoint of a line or arc")
                return
            self.owner = owner[0]
            self.tangent = self._tangent(self.owner, pid)
            self.pts = [s]
            return
        start = self.pts[0].ref[1]
        owner, tangent = self.owner.id, self.tangent
        self.pts = []

        def make(sk):
            end = self.cmd.commit(s, sk)
            (arc,) = builders.tangent_arc(sk, start, tangent, end)
            rel = make_relation(sk, "tangent", [("e", owner), ("e", arc.id)])[0]
            sk.add_constraint(rel.type, rel.refs, **rel.data)      # a fresh, unique id
            return [arc]

        self.cmd.apply(make, self.label)

    def _tangent(self, e, pid):
        sk = self.sk
        if isinstance(e, Line):
            other = e.p1 if e.p2 == pid else e.p2
            return sk.pt(pid) - sk.pt(other)
        c = sk.pt(e.center)
        r = sk.pt(pid) - c
        ccw_dir = np.array([-r[1], r[0]])
        return ccw_dir if pid == e.end else -ccw_dir

    def preview(self):
        if not self.pts or self.cursor is None:
            return []
        start, tangent = self.pts[0].ref[1], self.tangent
        end = self.cursor.uv
        return self.cmd.preview_of(lambda sk: builders.tangent_arc(sk, start, tangent, tuple(end)))


class ThreePointArcTool(Tool):
    key, label, icon = "arc_3pt", "3 Point Arc", "arc_3pt"
    n, commit_mask = 3, (True, True, False)
    prompts = ("3 Point Arc: start point", "End point", "Point on the arc")

    def build(self, sk, a):
        return builders.three_point_arc(sk, *a)


class PolygonTool(Tool):
    key, label, icon = "polygon", "Polygon", "polygon"
    n, commit_mask = 2, (True, False)
    drag_click = True
    prompts = ("Polygon: centre", "Vertex  [radius]")

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.sides = QSpinBox()
        self.sides.setRange(3, 64)
        self.sides.setValue(6)
        f.addRow("Sides", self.sides)
        self.mode = QComboBox()
        self.mode.addItems(["Inscribed circle (vertex)", "Circumscribed circle (edge)"])
        f.addRow(self.mode)
        return w

    def build(self, sk, a):
        return builders.polygon(sk, a[0], a[1], self.sides.value(), self.mode.currentIndex() == 0)


class SplineTool(Tool):
    key, label, icon = "spline", "Spline", "spline"
    prompts = ("Spline: first point", "Next point (right-click / Enter / double-click to finish)")

    def click(self, s):
        self.pts.append(s)

    def finish(self):
        snaps, self.pts = self.pts, []
        if len(snaps) >= 2:
            self.cmd.apply(lambda sk: builders.spline(sk, [self.cmd.commit(x, sk) for x in snaps]),
                           self.label)
            return True
        return bool(snaps)

    def escape(self):
        had = bool(self.pts)
        self.pts = []
        return had

    def preview(self):
        pts = [s.uv for s in self.pts] + ([self.cursor.uv] if self.cursor is not None else [])
        if len(pts) < 2:
            return []
        from ..core.sketch.model import spline_eval
        return [spline_eval(np.array(pts), 96)[0]]


class PointTool(Tool):
    key, label, icon = "point", "Point", "point"
    n, commit_mask = 1, (True,)
    prompts = ("Point: location",)

    def build(self, sk, a):
        return builders.point(sk, a[0])


# ---- Smart Dimension ------------------------------------------------------------------------

class SmartDimensionTool(Tool):
    key, label, icon = "smart_dim", "Smart Dimension", "smart_dim"
    numeric = False
    snapping = False
    prompts = ("Smart Dimension: select an entity or point",)

    def activate(self):
        self.first = self.second = None
        self.placing = False

    def prompt(self):
        if self.placing:
            return "Click to place the dimension"
        if self.first:
            return "Select a second entity, or click to place"
        return "Smart Dimension: select a line, circle, arc or point"

    def press(self, s, ev):
        cmd = self.cmd
        if self.placing:
            hit = cmd.hit_test(s.uv, ev.display, dims=False, glyphs=False)
            if self.second is None and hit is not None and hit != self.first and self._pair_ok(self.first, hit):
                self.second = hit
                return
            self._place(s.uv)
            return
        hit = cmd.hit_test(s.uv, ev.display, dims=False, glyphs=False)
        if hit is None or hit[0] == "c":
            return
        if self.first is None:
            self.first = hit
            e = self.sk.entities.get(hit[1]) if hit[0] == "e" else None
            self.placing = isinstance(e, (Line, Circle, Arc))
            cmd.selection = [hit]
            cmd.redraw()
        elif hit != self.first and self._pair_ok(self.first, hit):
            self.second = hit
            self.placing = True

    def _pair_ok(self, a, b):
        def kind(r):
            if r[0] == "p":
                return "p"
            e = self.sk.entities[r[1]]
            return "l" if isinstance(e, Line) else ("c" if isinstance(e, (Circle, Arc)) else "x")
        return "x" not in (kind(a), kind(b))

    def make(self, L):
        sk = self.sk
        refs = [r for r in (self.first, self.second) if r is not None]
        L = np.asarray(L, float)

        def ent(r):
            return sk.entities[r[1]] if r[0] == "e" else None

        c = None
        if len(refs) == 1:
            e = ent(refs[0])
            if isinstance(e, Circle):
                c = Constraint(0, "diameter", refs)
            elif isinstance(e, Arc):
                c = Constraint(0, "radius", refs)
            elif isinstance(e, Line):
                p, q = sk.pt(e.p1), sk.pt(e.p2)
                t = sd.choose_linear_type(p, q, L)
                c = Constraint(0, t, refs)
                if t != "distance":
                    ax = 0 if t == "hdistance" else 1
                    c.data["s"] = 1.0 if q[ax] - p[ax] >= 0 else -1.0
        else:
            a, b = refs
            ea, eb = ent(a), ent(b)
            if ea is None and eb is None:
                p, q = sk.pt(a[1]), sk.pt(b[1])
                t = sd.choose_linear_type(p, q, L)
                c = Constraint(0, t, refs)
                if t != "distance":
                    ax = 0 if t == "hdistance" else 1
                    c.data["s"] = 1.0 if q[ax] - p[ax] >= 0 else -1.0
            elif isinstance(ea, Line) and isinstance(eb, Line):
                d1 = sk.pt(ea.p2) - sk.pt(ea.p1)
                d2 = sk.pt(eb.p2) - sk.pt(eb.p1)
                cr = d1[0] * d2[1] - d1[1] * d2[0]
                if abs(cr) < 1e-9 * np.linalg.norm(d1) * np.linalg.norm(d2):
                    c = Constraint(0, "point_line_distance", [b, a])
                    self._side(c, sk.pt(eb.p1), ea)
                else:
                    c = Constraint(0, "angle", [a, b])
                    A1, A2 = sk.pt(ea.p1), sk.pt(eb.p1)
                    s = ((A2 - A1)[0] * d2[1] - (A2 - A1)[1] * d2[0]) / cr
                    V = A1 + s * d1
                    s1 = 1.0 if np.dot(L - V, d1) >= 0 else -1.0
                    s2 = 1.0 if np.dot(L - V, d2) >= 0 else -1.0
                    r1, r2 = d1 * s1, d2 * s2
                    ang = math.atan2(r1[0] * r2[1] - r1[1] * r2[0], np.dot(r1, r2))
                    c.data.update(s1=s1, s2=s2, sgn=1.0 if ang >= 0 else -1.0)
            elif isinstance(ea, Line) or isinstance(eb, Line):
                line_ref, other = (a, b) if isinstance(ea, Line) else (b, a)
                c = Constraint(0, "point_line_distance", [other, line_ref])
                eo = ent(other)
                p = sk.pt(other[1]) if eo is None else sk.pt(eo.center)
                self._side(c, p, sk.entities[line_ref[1]])
            else:
                c = Constraint(0, "distance", refs)
        if c is None:
            return None
        c.label = list(L)
        c.value = measure(sk, c)
        return c

    def _side(self, c, p, line):
        A, B = self.sk.pt(line.p1), self.sk.pt(line.p2)
        d = B - A
        c.data["s"] = 1.0 if d[0] * (p - A)[1] - d[1] * (p - A)[0] >= 0 else -1.0

    def _place(self, L):
        c = self.make(L)
        self.activate()
        self.cmd.selection = []
        if c is None:
            return
        self.cmd.add_dimension(c)

    def finish(self):
        """Right-click / Enter: drop the pending pick but stay in Smart Dimension (SolidWorks:
        the tool stays active for the next dimension until Esc)."""
        self.activate()
        self.cmd.selection = []
        return True

    def enter(self):
        return self.finish()

    def escape(self):
        """Esc leaves Smart Dimension (the window then switches to Select)."""
        self.activate()
        self.cmd.selection = []
        return False

    def preview_dim(self):
        if not self.placing or self.cursor is None:
            return None
        try:
            return self.make(self.cursor.uv)
        except Exception:
            return None

    def preview(self):
        return []


# ---- modify tools -------------------------------------------------------------------------------

class TrimTool(Tool):
    key, label, icon = "trim", "Trim Entities", "trim"
    numeric = False
    snapping = False
    prompts = ("Trim: click (or drag across) the pieces to remove",)

    def press(self, s, ev):
        self._trim_at(s.uv, ev)

    def move(self, s, ev):
        super().move(s, ev)
        if ev.dragging:
            self._trim_at(s.uv, ev)

    def _trim_at(self, uv, ev):
        hit = self.cmd.hit_test(uv, ev.display, points=False, dims=False, glyphs=False)
        if hit is None or hit[0] != "e":
            return
        self.cmd.apply(lambda sk: ops.trim(sk, hit[1], uv), self.label)


class ExtendTool(Tool):
    key, label, icon = "extend", "Extend Entities", "extend"
    numeric = False
    snapping = False
    prompts = ("Extend: click a line or arc near the end to extend",)

    def press(self, s, ev):
        hit = self.cmd.hit_test(s.uv, ev.display, points=False, dims=False, glyphs=False)
        if hit is None or hit[0] != "e":
            return

        def run(sk):
            if not ops.extend(sk, hit[1], s.uv):
                raise SketchError("Nothing to extend to")
        self.cmd.apply(run, self.label)


class _CornerTool(Tool):
    numeric = False
    snapping = False

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.size = spin(self.default, 0.001, 1e6)
        f.addRow(self.size_label, self.size)
        return w

    def press(self, s, ev):
        hit = self.cmd.hit_test(s.uv, ev.display, dims=False, glyphs=False)
        if hit is None:
            return
        pid = None
        if hit[0] == "p":
            pid = hit[1]
        elif hit[0] == "e" and isinstance(self.sk.entities[hit[1]], Line):
            e = self.sk.entities[hit[1]]
            pid = min((e.p1, e.p2), key=lambda p: np.linalg.norm(self.sk.pt(p) - s.uv))
        if pid is not None:
            self.cmd.apply(lambda sk: self.corner(sk, pid), self.label)


class FilletTool(_CornerTool):
    key, label, icon = "fillet", "Sketch Fillet", "fillet_sk"
    prompts = ("Sketch Fillet: click a corner (or a line near the corner)",)
    default, size_label = 5.0, "Radius"

    def corner(self, sk, pid):
        return ops.fillet(sk, pid, self.size.value())


class ChamferTool(_CornerTool):
    key, label, icon = "chamfer", "Sketch Chamfer", "chamfer_sk"
    prompts = ("Sketch Chamfer: click a corner (or a line near the corner)",)
    default, size_label = 5.0, "Distance"

    def corner(self, sk, pid):
        return ops.chamfer(sk, pid, self.size.value())


def connected_chain(sk, eid):
    """All curves connected end-to-end with entity eid (SolidWorks 'select chain')."""
    start = sk.entities[eid]
    if isinstance(start, Circle):
        return [eid]
    chain, frontier = {eid}, [start]
    while frontier:
        e = frontier.pop()
        for pid in e.end_ids():
            for o in sk.entities_using_point(pid):
                if (o.id not in chain and isinstance(o, (Line, Arc)) and pid in o.end_ids()
                        and o.construction == start.construction):
                    chain.add(o.id)
                    frontier.append(o)
    return sorted(chain)


class OffsetTool(Tool):
    key, label, icon = "offset", "Offset Entities", "offset"
    numeric = False
    snapping = False
    prompts = ("Offset: hover a curve (the side of the cursor sets the direction) and click",)

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.dist = spin(5.0, 0.001, 1e6)
        f.addRow("Distance", self.dist)
        self.chain = QCheckBox("Select chain")
        self.chain.setChecked(True)
        f.addRow(self.chain)
        self.reverse = QCheckBox("Reverse")
        f.addRow(self.reverse)
        return w

    def _candidates(self, uv, ev):
        hit = self.cmd.hit_test(uv, ev.display, points=False, dims=False, glyphs=False)
        if hit is None or hit[0] != "e" or isinstance(self.sk.entities[hit[1]], (Spline, PointEntity)):
            return None
        ids = connected_chain(self.sk, hit[1]) if self.chain.isChecked() else [hit[1]]
        best = None
        for sign in (1.0, -1.0):
            copy = self.sk.copy()
            try:
                new = ops.offset(copy, ids, sign * self.dist.value())
            except SketchError:
                continue
            d = min(ops.dist_to_entity(copy, e, uv) for e in new)
            if best is None or d < best[0]:
                best = (d, sign, [copy.sample(e) for e in new])
        if best is None:
            return None
        sign = -best[1] if self.reverse.isChecked() else best[1]
        return ids, sign, best[2]

    def move(self, s, ev):
        super().move(s, ev)
        self._cand = self._candidates(s.uv, ev)

    def press(self, s, ev):
        cand = self._candidates(s.uv, ev)
        if cand is None:
            return
        ids, sign, _ = cand
        self.cmd.apply(lambda sk: ops.offset(sk, ids, sign * self.dist.value()), self.label)

    def preview(self):
        cand = getattr(self, "_cand", None)
        if not cand:
            return []
        if self.reverse.isChecked():
            ids, sign, _ = cand
            return self.cmd.preview_of(lambda sk: ops.offset(sk, ids, sign * self.dist.value()))
        return cand[2]


class TextTool(Tool):
    """Sketch Text: click to place (left end of the baseline); click a text to edit it."""

    key, label, icon = "text", "Text", "text"
    numeric = False
    prompts = ("Text: click where the text starts - or click a text to edit it",)

    def activate(self):
        self.sel = None

    def options(self):
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QFontComboBox, QLineEdit
        w = QWidget()
        f = QFormLayout(w)
        self.edit = QLineEdit("TEXT")
        f.addRow("Text", self.edit)
        self.height = spin(5.0, 0.01, 1e5)
        f.addRow("Height", self.height)
        self.font = QFontComboBox()
        self.font.setCurrentFont(QFont("Arial"))
        f.addRow("Font", self.font)
        row = QHBoxLayout()
        self.bold, self.italic = QCheckBox("Bold"), QCheckBox("Italic")
        row.addWidget(self.bold)
        row.addWidget(self.italic)
        f.addRow(row)
        self.angle = spin(0.0, -360.0, 360.0, 2, "°", 5.0)
        f.addRow("Angle", self.angle)
        self.del_btn = QPushButton(icon("delete"), "Delete this text")
        self.del_btn.setEnabled(False)
        self.del_btn.clicked.connect(self._delete)
        f.addRow(self.del_btn)
        f.addRow(hint("Height = capital letter height. Extrude the sketch to emboss the text, "
                      "Extruded Cut to engrave it."))
        self.edit.editingFinished.connect(self._changed)
        for sig in (self.height.valueChanged, self.font.currentFontChanged, self.bold.toggled,
                    self.italic.toggled, self.angle.valueChanged):
            sig.connect(self._changed)
        return w

    def values(self) -> dict:
        return {"text": self.edit.text() or "TEXT", "height": self.height.value(),
                "font": self.font.currentFont().family(), "bold": self.bold.isChecked(),
                "italic": self.italic.isChecked(), "angle": self.angle.value()}

    def _text_at(self, uv):
        from .sketch_draw import point_in_polygon
        for t in reversed(self.sk.texts):
            if point_in_polygon(uv, self.sk.text_box(t)):
                return t["id"]
        return None

    def press(self, s, ev):
        tid = self._text_at(s.uv)
        if tid is not None:
            self._select(tid)
            return
        vals = self.values()
        box = {}

        def add(sk):
            t = sk.add_text(vals["text"], tuple(s.uv), vals["height"], vals["font"], vals["bold"],
                            vals["italic"], vals["angle"])
            box["id"] = t["id"]
        self.cmd.apply(add, self.label)
        self._select(box.get("id"))

    def _select(self, tid):
        self.sel = tid
        t = next((x for x in self.sk.texts if x["id"] == tid), None)
        self.cmd.text_selection = tid
        if hasattr(self, "del_btn"):
            self.del_btn.setEnabled(t is not None)
        if t is not None and hasattr(self, "edit"):
            from PySide6.QtGui import QFont
            ws = (self.edit, self.height, self.font, self.bold, self.italic, self.angle)
            for w_ in ws:
                w_.blockSignals(True)
            self.edit.setText(t["text"])
            self.height.setValue(t["height"])
            self.font.setCurrentFont(QFont(t["font"]))
            self.bold.setChecked(t["bold"])
            self.italic.setChecked(t["italic"])
            self.angle.setValue(t.get("angle", 0.0))
            for w_ in ws:
                w_.blockSignals(False)
        self.cmd.redraw()

    def _changed(self, *_):
        if self.sel is None:
            return
        vals, tid = self.values(), self.sel

        def upd(sk):
            for t in sk.texts:
                if t["id"] == tid:
                    t.update(vals)
        self.cmd.apply(upd, "Edit text")

    def _delete(self):
        if self.sel is None:
            return
        tid = self.sel
        self.cmd.apply(lambda sk: sk.remove_text(tid), "Delete text")
        self._select(None)

    def deactivate(self):
        self.cmd.text_selection = None

    def preview(self):
        if self.cursor is None or self.sel is not None or not hasattr(self, "edit"):
            return []
        if self._text_at(self.cursor.uv) is not None:
            return []
        t = dict(self.values(), at=[0.0, 0.0], id=0)
        polys = self.cmd.text_polylines(dict(t, angle=0.0))
        a = np.radians(t["angle"])
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        return [np.asarray(p) @ R.T + self.cursor.uv for p in polys]


class RelationsTool(Tool):
    """Display/Delete Relations: every relation and dimension of the sketch in one list."""

    key, label, icon = "relations", "Display/Delete Relations", "relation"
    numeric = False
    snapping = False
    prompts = ("Display/Delete Relations: select items in the list to see them, Delete removes them",)

    def options(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.filter = QComboBox()
        for k, v in (("all", "All"), ("relations", "Relations"), ("dimensions", "Dimensions"),
                     ("driven", "Driven dimensions")):
            self.filter.addItem(v, k)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(180)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        del_btn = QPushButton(icon("delete"), "Delete")
        del_btn.clicked.connect(lambda: self.delete_items())
        del_all = QPushButton("Delete All")
        del_all.clicked.connect(lambda: self.delete_items(all_shown=True))
        row.addWidget(del_btn)
        row.addWidget(del_all)
        lay.addLayout(row)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.list.itemSelectionChanged.connect(self._selected)
        self.refresh()
        return w

    def shown(self):
        k = self.filter.currentData() if hasattr(self, "filter") else "all"
        out = []
        for c in self.sk.constraints:
            if c.type == "arc_internal":
                continue
            dim = is_dimension(c)
            if (k == "relations" and dim) or (k == "dimensions" and not dim) or \
                    (k == "driven" and (not dim or c.driving)):
                continue
            out.append(c)
        return out

    def refresh(self, *_):
        if not hasattr(self, "list"):
            return
        self.list.blockSignals(True)
        self.list.clear()
        for c in self.shown():
            it = QListWidgetItem(icon("smart_dim" if is_dimension(c) else "relation"),
                                 self.cmd._describe_constraint(c))
            it.setData(Qt.UserRole, c.id)
            self.list.addItem(it)
        self.list.blockSignals(False)

    def _selected(self):
        sel = []
        for it in self.list.selectedItems():
            sel.append(("c", it.data(Qt.UserRole)))
            c = self.sk.constraint(it.data(Qt.UserRole))
            if c is not None:
                sel += [tuple(r) for r in c.refs]
        self.cmd.selection = sel
        self.cmd.redraw()

    def delete_items(self, all_shown=False):
        ids = ([c.id for c in self.shown()] if all_shown
               else [it.data(Qt.UserRole) for it in self.list.selectedItems()])
        if not ids:
            return

        def run(sk):
            for cid in ids:
                sk.remove_constraint(cid)
        self.cmd.selection = []
        self.cmd.apply(run, "Delete relations")
        self.refresh()


class ConvertTool(Tool):
    """Convert Entities: model edges projected into the sketch (SolidWorks)."""

    key, label, icon = "convert", "Convert Entities", "convert"
    numeric = False
    snapping = False
    prompts = ("Convert Entities: click model edges to project them into the sketch",)

    def activate(self):
        self._cands = []
        self._hot = None
        body = getattr(self.cmd.doc, "body", None)
        pl = self.cmd.plane
        if body is None or pl is None:
            return
        from ..core import modeling
        try:
            infos = modeling.convertible_edges(body)
        except Exception:  # noqa: BLE001
            infos = []
        for it in infos:
            uv = np.array([pl.to_local(q) for q in it["pts"]], float)
            if len(uv) >= 2 and np.ptp(uv, axis=0).max() > 1e-9:
                on_plane = float(np.max(np.abs((np.asarray(it["pts"]) - pl.origin) @ pl.normal)))
                self._cands.append((it, uv, on_plane))

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.fix = QCheckBox("Fix converted entities (fully defined)")
        self.fix.setChecked(True)
        f.addRow(self.fix)
        b = QPushButton(icon("convert"), "Convert all edges in the sketch plane")
        b.setToolTip("Sketching on a face: its outline, holes and every other model edge lying "
                     "in the plane")
        b.clicked.connect(self._convert_plane)
        f.addRow(b)
        return w

    def _nearest(self, uv, ev):
        if not self._cands:
            return None
        tol = 8 * self.cmd.viewer.world_per_pixel()
        best = None
        for c in self._cands:
            P = c[1]
            q = _nearest_on_polyline(P, np.asarray(uv, float))
            d = float(np.linalg.norm(q - uv))
            # edges in the plane win over edges behind it that project to the same place
            key = (round(d / max(tol, 1e-12), 1), c[2])
            if d < tol and (best is None or key < best[0]):
                best = (key, c)
        return None if best is None else best[1]

    def move(self, s, ev):
        super().move(s, ev)
        self._hot = self._nearest(s.uv, ev)

    def _run(self, items):
        pl = self.cmd.plane
        fix = self.fix.isChecked() if hasattr(self, "fix") else True
        self.cmd.apply(lambda sk: ops.convert(sk, items, pl.to_local, pl.normal, fix), self.label)

    def press(self, s, ev):
        c = self._nearest(s.uv, ev)
        if c is not None:
            self._run([c[0]])

    def _convert_plane(self):
        scale = max((float(np.ptp(c[1], axis=0).max()) for c in self._cands), default=1.0)
        items = [c[0] for c in self._cands if c[2] < 1e-6 * max(scale, 1.0)]
        if not items:
            self.cmd.win.status("No model edges lie in the sketch plane")
            return
        self._run(items)

    def preview(self):
        return [] if self._hot is None else [self._hot[1]]


class _SelectionTool(Tool):
    """Tools that act on selected entities: select first (click / box), then Enter."""

    snapping = True

    def activate(self):
        self.stage = 1 if self.cmd.selected_entities() else 0

    def prompt(self):
        if self.stage == 0:
            return f"{self.label}: select entities, then press Enter (or right-click)"
        return self.stage_prompt()

    def stage_prompt(self):
        return self.prompts[min(len(self.pts), len(self.prompts) - 1)]

    def press(self, s, ev):
        if self.stage == 0:
            self.cmd.select_at(s.uv, ev)
            return
        self.click(s)

    def finish(self):
        if self.stage == 0:
            if self.cmd.selected_entities():
                self.stage = 1
                self.cmd.refresh_prompt()
                return True
            return False
        return super().finish()

    def enter(self):
        return self.finish()

    def typed(self, text):
        if self.stage == 0:
            return
        super().typed(text)

    def moved_preview(self, fn):
        ids = self.cmd.selected_entities()

        def run(sk):
            return fn(sk, ids)
        return self.cmd.preview_of(run, copy_all=True, only=ids)


class MirrorTool(_SelectionTool):
    key, label, icon = "mirror", "Mirror Entities", "mirror"
    numeric = False
    prompts = ("Mirror: click the mirror line (a line or centerline)",)

    def click(self, s):
        hit = self.cmd.hit_test(s.uv, self.cmd.last_display, points=False, dims=False, glyphs=False)
        if hit is None or hit[0] != "e" or not isinstance(self.sk.entities[hit[1]], Line):
            self.cmd.message("Click a line or centerline to mirror about")
            return
        ids = [i for i in self.cmd.selected_entities() if i != hit[1]]
        self.cmd.apply(lambda sk: ops.mirror(sk, ids, hit[1]), self.label)
        self.cmd.selection = []
        self.stage = 0


class MoveTool(_SelectionTool):
    key, label, icon = "move", "Move Entities", "move"
    copy = False
    prompts = ("Base point", "Destination point  [x,y | @dx,dy]")

    def click(self, s):
        self.pts.append(s)
        if len(self.pts) == 2:
            d = self.pts[1].uv - self.pts[0].uv
            ids = self.cmd.selected_entities()
            self.pts = []
            self.cmd.apply(lambda sk: ops.move(sk, ids, *d, copy=self.copy), self.label)
            if not self.copy:
                self.stage = 1

    def preview(self):
        if self.stage == 0 or not self.pts or self.cursor is None:
            return []
        d = self.cursor.uv - self.pts[0].uv
        ids = self.cmd.selected_entities()
        return self.cmd.preview_of(lambda sk: ops.move(sk, ids, *d, copy=True))


class CopyTool(MoveTool):
    key, label, icon = "copy", "Copy Entities", "copy"
    copy = True


class RotateTool(_SelectionTool):
    key, label, icon = "rotate", "Rotate Entities", "rotate"
    prompts = ("Rotation centre", "Angle  [degrees, or click a direction]")

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.copy_cb = QCheckBox("Keep original (copy)")
        f.addRow(self.copy_cb)
        return w

    def _angle(self):
        return math.atan2(*(self.cursor.uv - self.pts[0].uv)[::-1])

    def click(self, s):
        if not self.pts:
            self.pts = [s]
            return
        self.cursor = s
        self._apply(self._angle())

    def _apply(self, ang):
        c = self.pts[0].uv
        ids = self.cmd.selected_entities()
        self.pts = []
        self.cmd.apply(lambda sk: ops.rotate(sk, ids, c, ang, copy=self.copy_cb.isChecked()), self.label)

    def typed(self, text):
        if self.stage == 1 and self.pts:
            self._apply(math.radians(evaluate(text)))
        else:
            super().typed(text)

    def preview(self):
        if self.stage == 0 or not self.pts or self.cursor is None:
            return []
        c, ang = self.pts[0].uv, self._angle()
        ids = self.cmd.selected_entities()
        return self.cmd.preview_of(lambda sk: ops.rotate(sk, ids, c, ang, copy=True))


class ScaleTool(_SelectionTool):
    key, label, icon = "scale", "Scale Entities", "scale"
    prompts = ("Scale base point", "Type the scale factor and press Enter")

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.factor = spin(2.0, 1e-6, 1e6, 4, "")
        f.addRow("Factor", self.factor)
        self.copy_cb = QCheckBox("Keep original (copy)")
        f.addRow(self.copy_cb)
        b = QPushButton("Apply")
        b.clicked.connect(lambda: self._apply(self.factor.value()))
        f.addRow(b)
        return w

    def click(self, s):
        self.pts = [s]

    def _apply(self, k):
        base = self.pts[0].uv if self.pts else np.zeros(2)
        ids = self.cmd.selected_entities()
        if not ids:
            return
        self.pts = []
        self.cmd.apply(lambda sk: ops.scale(sk, ids, base, k, copy=self.copy_cb.isChecked()), self.label)

    def typed(self, text):
        if self.stage == 1 and self.pts:
            self._apply(evaluate(text))


class LinearPatternTool(_SelectionTool):
    key, label, icon = "lin_pattern", "Linear Sketch Pattern", "lin_pattern"
    numeric = False
    prompts = ("Set the pattern and press Apply",)

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.n1 = QSpinBox()
        self.n1.setRange(1, 500)
        self.n1.setValue(3)
        self.s1, self.a1 = spin(20.0, -1e6, 1e6), spin(0.0, -360, 360, 2, "°")
        self.n2 = QSpinBox()
        self.n2.setRange(1, 500)
        self.s2, self.a2 = spin(20.0, -1e6, 1e6), spin(90.0, -360, 360, 2, "°")
        for lbl, wd in (("Direction 1 count", self.n1), ("Spacing", self.s1), ("Angle", self.a1),
                        ("Direction 2 count", self.n2), ("Spacing", self.s2), ("Angle", self.a2)):
            f.addRow(lbl, wd)
            (wd.valueChanged).connect(lambda *_: self.cmd.redraw(preview_only=True))
        b = QPushButton("Apply")
        b.clicked.connect(self._apply)
        f.addRow(b)
        return w

    def _run(self, sk, ids):
        return ops.linear_pattern(sk, ids, self.a1.value(), self.s1.value(), self.n1.value(),
                                  self.a2.value(), self.s2.value(), self.n2.value())

    def _apply(self):
        ids = self.cmd.selected_entities()
        if ids:
            self.cmd.apply(lambda sk: self._run(sk, ids), self.label)

    def click(self, s):
        pass

    def preview(self):
        ids = self.cmd.selected_entities()
        if not ids:
            return []
        return self.cmd.preview_of(lambda sk: self._run(sk, ids))


class CircularPatternTool(_SelectionTool):
    key, label, icon = "circ_pattern", "Circular Sketch Pattern", "circ_pattern"
    numeric = False
    prompts = ("Click to pick the pattern centre (default: origin), then Apply",)

    def options(self):
        w = QWidget()
        f = QFormLayout(w)
        self.count = QSpinBox()
        self.count.setRange(2, 500)
        self.count.setValue(6)
        self.total = spin(360.0, -360, 360, 2, "°")
        self.cx, self.cy = spin(0.0), spin(0.0)
        for lbl, wd in (("Instances", self.count), ("Total angle", self.total),
                        ("Centre X", self.cx), ("Centre Y", self.cy)):
            f.addRow(lbl, wd)
            wd.valueChanged.connect(lambda *_: self.cmd.redraw(preview_only=True))
        b = QPushButton("Apply")
        b.clicked.connect(self._apply)
        f.addRow(b)
        return w

    def _run(self, sk, ids):
        return ops.circular_pattern(sk, ids, (self.cx.value(), self.cy.value()),
                                    self.count.value(), self.total.value())

    def click(self, s):
        self.cx.setValue(float(s.uv[0]))
        self.cy.setValue(float(s.uv[1]))

    def _apply(self):
        ids = self.cmd.selected_entities()
        if ids:
            self.cmd.apply(lambda sk: self._run(sk, ids), self.label)

    def preview(self):
        ids = self.cmd.selected_entities()
        return self.cmd.preview_of(lambda sk: self._run(sk, ids)) if ids else []


# ---- select / drag ------------------------------------------------------------------------------

class SelectTool(Tool):
    key, label, icon = "select", "Select", "select"
    numeric = False
    snapping = False
    prompts = ("Select entities; drag to move them; box-select on empty space; double-click a "
               "dimension to edit it",)

    def activate(self):
        self.mode = None
        self.box = None

    def press(self, s, ev):
        cmd = self.cmd
        self.hit = cmd.hit_test(s.uv, ev.display)
        self.start = s.uv.copy()
        self.mode = None
        self.drag_solver = None
        if self.hit is None:
            self.mode = "box"
        elif self.hit[0] == "c":
            c = cmd.sketch.constraint(self.hit[1])
            self.mode = "label" if c is not None and is_dimension(c) else None
        else:
            self.mode = "geom"

    def move(self, s, ev):
        super().move(s, ev)
        cmd = self.cmd
        if not ev.dragging:
            h = cmd.hit_test(s.uv, ev.display)
            if h != cmd.hover:
                cmd.hover = h
                cmd.redraw()
            return
        if self.mode == "box":
            self.box = (self.start, s.uv.copy())
            cmd.redraw(preview_only=True)
        elif self.mode == "label":
            c = cmd.sketch.constraint(self.hit[1])
            if c.label is None:
                c.label = list(s.uv)
            c.label = list(s.uv)
            cmd.redraw()
        elif self.mode == "geom":
            if self.drag_solver is None:
                cmd.checkpoint("Drag")
                self.drag_solver = solver.Solver(cmd.sketch)
                self.targets0 = self._targets0()
            target = s.uv
            self.drop = None
            if getattr(self, "absolute", False) and not ev.shift:
                # dragging one point: it snaps like a drawing tool (Shift = no snapping)
                snap = cmd.snap(*s.uv, None, exclude_points=frozenset([self.hit[1]]))
                if snap.kind in SNAP_LABELS or snap.kind == "grid":
                    target, self.drop = snap.uv, snap
            self.cursor = self.drop
            delta = target - self.start
            drag = {}
            for key, val in self.targets0.items():
                if isinstance(key, tuple):
                    drag[key] = np.linalg.norm(target - val)
                else:
                    drag[key] = val + delta if not getattr(self, "absolute", False) else target
            self.drag_solver.solve(drag)
            cmd.after_drag()

    def _targets0(self):
        sk = self.cmd.sketch
        self.absolute = False
        k, i = self.hit
        if k == "p":
            self.absolute = True
            return {i: sk.pt(i)}
        e = sk.entities[i]
        if isinstance(e, Circle) and np.linalg.norm(self.start - sk.pt(e.center)) > 0.5 * e.r:
            return {("r", e.id): sk.pt(e.center)}
        pids = e.point_ids()
        # dragging a selected group moves the whole selection
        sel = self.cmd.selected_entities()
        if i in sel and len(sel) > 1:
            pids = sorted({p for j in sel for p in sk.entities[j].point_ids()})
        return {p: sk.pt(p) for p in pids}

    def release(self, s, ev):
        cmd = self.cmd
        if self.mode == "box" and self.box is not None:
            lo, hi = self.box
            crossing = hi[0] < lo[0]      # right-to-left = crossing selection
            ids = self._box_select(lo, hi, crossing)
            if not ev.ctrl:
                cmd.selection = []
            for i in ids:
                if ("e", i) not in cmd.selection:
                    cmd.selection.append(("e", i))
            self.box = None
            cmd.selection_changed()
            return
        if self.mode == "geom" and self.drag_solver is not None:
            self.drag_solver = None
            drop, self.drop, self.cursor = getattr(self, "drop", None), None, None
            if drop is not None and getattr(self, "absolute", False):
                cmd.drop_point(self.hit[1], drop)
            cmd.changed(checkpoint=False)
            return
        if not ev.dragging:
            cmd.select_at(s.uv, ev, hit=self.hit)
        elif self.mode == "label":
            cmd.changed(checkpoint=False)

    def _box_select(self, a, b, crossing):
        sk = self.cmd.sketch
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        if not crossing:
            return ops.entities_in_box(sk, lo, hi)
        out = []
        for e in sk.entities.values():
            pts = sk.sample(e, 32)
            if ((pts >= lo) & (pts <= hi)).all(axis=1).any():
                out.append(e.id)
        return out

    def preview(self):
        if self.box is None:
            return []
        (x0, y0), (x1, y1) = self.box
        return [np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])]


class AddRelationsTool(Tool):
    key, label, icon = "add_relations", "Add Relations", "relation"
    numeric = False
    snapping = False
    additive_selection = True
    prompts = ("Click points or lines to add/remove them from the selection, then choose a "
               "relation below. Click empty space to clear. No Ctrl needed.",)

    def press(self, s, ev):
        hit = self.cmd.hit_test(s.uv, ev.display, dims=False, glyphs=False)
        self.cmd.select_at(s.uv, ev, hit=hit)

    def move(self, s, ev):
        hit = self.cmd.hit_test(s.uv, ev.display, dims=False, glyphs=False)
        if hit != self.cmd.hover:
            self.cmd.hover = hit
            self.cmd.redraw()


TOOLS = [SelectTool, AddRelationsTool, LineTool, CenterlineTool, MidpointLineTool, CornerRectTool, CenterRectTool,
         ThreePointRectTool, ThreePointCenterRectTool, ParallelogramTool, SlotTool,
         CenterSlotTool, CircleTool, PerimeterCircleTool, CenterArcTool, TangentArcTool,
         ThreePointArcTool, PolygonTool, SplineTool, PointTool, SmartDimensionTool, TrimTool,
         ExtendTool, OffsetTool, ConvertTool, TextTool, RelationsTool, MirrorTool, FilletTool, ChamferTool, MoveTool, CopyTool,
         RotateTool, ScaleTool, LinearPatternTool, CircularPatternTool]
TOOL_BY_KEY = {t.key: t for t in TOOLS}

PANEL_GROUPS = [
    ("Draw", ["select", "line", "centerline", "midpoint_line", "rect_corner", "rect_center",
              "rect_3pt", "rect_3pt_center", "parallelogram", "slot", "slot_center", "circle",
              "circle_perimeter", "arc_center", "arc_tangent", "arc_3pt", "polygon", "spline",
              "point", "text"]),
    ("Dimension / Modify", ["smart_dim", "add_relations", "trim", "extend", "offset", "convert", "mirror", "fillet",
                            "chamfer", "move", "copy", "rotate", "scale", "lin_pattern",
                            "circ_pattern", "relations"]),
]


# ============================================================================
# Value editor popup (SolidWorks "Modify" box)
# ============================================================================

class ValueEditor(QFrame):
    def __init__(self, parent, title, value_text, on_ok):
        super().__init__(parent, Qt.Popup)
        self.on_ok = on_ok
        self.setStyleSheet("QFrame{background:#fff;border:1px solid #7aa6e0;border-radius:4px;}"
                           "QLineEdit{border:1px solid #c9d3df;padding:3px;font:11pt 'Consolas';}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        t = QLabel(title)
        t.setStyleSheet("border:0;color:#4a5a70;")
        lay.addWidget(t)
        row = QHBoxLayout()
        self.edit = QLineEdit(value_text)
        self.edit.selectAll()
        self.edit.returnPressed.connect(self._ok)
        row.addWidget(self.edit)
        ok = QToolButton()
        ok.setIcon(icon("exit_sketch"))
        ok.clicked.connect(self._ok)
        row.addWidget(ok)
        lay.addLayout(row)
        self.error = QLabel()
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("border:0;color:#b03030;")
        lay.addWidget(self.error)

    def _ok(self):
        if self.on_ok(self.edit.text()) is not False:
            self.close()

    def popup(self):
        self.move(QCursor.pos() + QPoint(12, 12))
        self.show()
        self.edit.setFocus()


# ============================================================================
# The sketch command
# ============================================================================

class SketchCommand(Command):
    title = "Sketch"
    icon_name = "sketch"
    ok_text = "Exit Sketch"

    def __init__(self, win, feature: SketchFeature | None = None, plane: SketchPlane | None = None,
                 tool: str = "line", plane_spec: dict | None = None):
        super().__init__(win)
        self.feature = feature
        self.plane = feature.plane if feature else plane
        self.plane_spec = feature.plane_spec if feature else plane_spec
        self.sketch = feature.sketch.copy() if feature else Sketch()
        self.dimension_equations = {
            int(b["field"].split(":")[1]): b["expression"]
            for b in self.doc.parameter_bindings
            if feature is not None and b["feature"] == feature.id and b["field"].startswith("dimension:")
        }
        self.initial_tool = tool
        self.tool: Tool | None = None
        self.selection: list = []
        self.hover = None
        self.drawing = False
        self.history: list = []
        self.future: list = []
        self.analysis = None
        self.solve_ok = True
        self.error_points = []
        self.last_display = (0, 0)
        self._dim_hits = []
        self._glyph_hits = []
        self.snap_grid = False
        self.inference = True
        self.dim_typed = True             # typed dynamic-input values become dimensions
        self.shade_regions = True         # shade closed areas while sketching
        self.region_count = 0
        self._dyn = None                  # DynamicInput (AutoCAD-style value fields)
        self._raw_cursor = None           # last snapped cursor, before typed values
        self.show_relations = True
        self.text_selection = None        # id of the text the Text tool is editing
        self.quick_snap = None            # a Quick Snap forced for the next click
        self.snap_enabled = win.snap_settings() if hasattr(win, "snap_settings") else {}
        self.angle_step = float(self.snap_enabled.pop("_angle", 15.0))
        self._text_cache: dict = {}
        if feature:
            self.title = f"Edit {feature.name}"

    # ---- panel ---------------------------------------------------------------------------
    def build_panel(self):
        self.stack = QStackedWidget()
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        l0.addWidget(hint("Select a plane or a planar face of the model to sketch on."))
        for name in REFERENCE_PLANES:
            b = QPushButton(icon("plane"), name)
            b.clicked.connect(lambda _=False, n=name: self._set_plane(
                REFERENCE_PLANES[n], {"type": "std", "name": n}))
            l0.addWidget(b)
        for name, pl in self.win.ref_planes().items():
            b = QPushButton(icon("ref_plane"), name)
            b.clicked.connect(lambda _=False, n=name, p_=pl: self._set_plane(
                p_, self.win.plane_spec_by_name(n)))
            l0.addWidget(b)
        l0.addStretch(1)
        self.stack.addWidget(p0)

        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        self.tool_buttons = {}
        self.geometry_tool_groups = []
        for gname, keys in PANEL_GROUPS:
            box = QGroupBox(gname)
            self.geometry_tool_groups.append(box)
            grid = QGridLayout(box)
            grid.setSpacing(2)
            for i, key in enumerate(keys):
                t = TOOL_BY_KEY[key]
                b = QToolButton()
                b.setIcon(icon(t.icon))
                b.setIconSize(b.iconSize() * 1.25)
                b.setToolTip(tooltip(t.label, icon_name=t.icon))
                b.setCheckable(True)
                b.setAutoRaise(True)
                b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
                grid.addWidget(b, i // 7, i % 7)
                self.tool_buttons[key] = b
            l1.addWidget(box)

        self.tool_box = QGroupBox("Tool")
        tb = QVBoxLayout(self.tool_box)
        self.prompt_label = QLabel()
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setStyleSheet("color:#1f3b5c;font-weight:600;")
        tb.addWidget(self.prompt_label)
        self.tool_opts = QVBoxLayout()
        tb.addLayout(self.tool_opts)
        self.numeric_hint = hint("Type to enter values: x,y  ·  @dx,dy  ·  @length<angle  ·  length")
        tb.addWidget(self.numeric_hint)
        l1.addWidget(self.tool_box)

        self.sel_box = QGroupBox("Selection")
        sb = QVBoxLayout(self.sel_box)
        sb.addWidget(hint("Ctrl-click to select several points or lines, or use Add Relations "
                          "to select them one at a time without Ctrl."))
        add_rel = QPushButton(icon("relation"), "Add Relations")
        add_rel.clicked.connect(lambda: self.set_tool("add_relations"))
        sb.addWidget(add_rel)
        self.sel_list = QListWidget()
        self.sel_list.setMaximumHeight(90)
        sb.addWidget(self.sel_list)
        self.rel_label = QLabel("Add Relations")
        self.rel_label.setStyleSheet("font-weight:600;")
        sb.addWidget(self.rel_label)
        self.rel_buttons = QGridLayout()
        sb.addLayout(self.rel_buttons)
        sb.addWidget(QLabel("Existing relations / dimensions:"))
        self.exist_list = QListWidget()
        self.exist_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.exist_list.setMaximumHeight(110)
        sb.addWidget(self.exist_list)
        row = QHBoxLayout()
        rm = QPushButton(icon("delete"), "Delete")
        rm.setToolTip("Delete the selected relations / dimensions (or the selected geometry)")
        rm.clicked.connect(self._delete_from_panel)
        row.addWidget(rm)
        cons = QPushButton(icon("construction"), "Construction")
        cons.setToolTip("Toggle construction geometry for the selected entities")
        cons.clicked.connect(self.toggle_construction)
        row.addWidget(cons)
        self.driven_btn = QPushButton("Driven")
        self.driven_btn.setToolTip("Toggle a selected dimension between driving and driven (reference)")
        self.driven_btn.clicked.connect(self.toggle_driven)
        row.addWidget(self.driven_btn)
        sb.addLayout(row)
        l1.addWidget(self.sel_box)

        st = QGroupBox("Status")
        sl = QVBoxLayout(st)
        self.dof_label = QLabel()
        self.profile_label = QLabel()
        self.profile_label.setWordWrap(True)
        sl.addWidget(self.dof_label)
        sl.addWidget(self.profile_label)
        opts = QHBoxLayout()
        self.grid_cb = QCheckBox("Snap to grid")
        self.grid_cb.toggled.connect(lambda on: setattr(self, "snap_grid", on))
        self.inf_cb = QCheckBox("Inference")
        self.inf_cb.setChecked(True)
        self.inf_cb.toggled.connect(lambda on: setattr(self, "inference", on))
        self.rel_cb = QCheckBox("Show relations")
        self.rel_cb.setChecked(True)
        self.rel_cb.toggled.connect(self._toggle_relations)
        for w in (self.grid_cb, self.inf_cb, self.rel_cb):
            opts.addWidget(w)
        sl.addLayout(opts)
        self.dimtyped_cb = QCheckBox("Dimension typed values (dynamic input)")
        self.dimtyped_cb.setChecked(True)
        self.dimtyped_cb.setToolTip("Width / height / length / diameter typed while drawing "
                                    "become driving dimensions")
        self.dimtyped_cb.toggled.connect(lambda on: setattr(self, "dim_typed", on))
        sl.addWidget(self.dimtyped_cb)
        self.fill_cb = QCheckBox("Shade closed regions (what Extrude will use)")
        self.fill_cb.setChecked(True)
        self.fill_cb.toggled.connect(lambda on: (setattr(self, "shade_regions", on),
                                                 self.update_region_fill()))
        sl.addWidget(self.fill_cb)
        l1.addWidget(st)
        l1.addStretch(1)
        self.stack.addWidget(p1)
        return self.stack

    def repair_sketch(self):
        """Repair Sketch: close small gaps, delete zero-length and duplicate curves."""
        box = {}

        def run(sk):
            box.update(ops.repair(sk))
        self.apply(run, "Repair Sketch")
        n = sum(box.values())
        self.win.status("Repair Sketch: " + (", ".join(f"{v} {k.replace('_', ' ')}"
                                                       for k, v in box.items() if v)
                                             if n else "no problems found"))
        return box

    def _toggle_relations(self, on):
        self.show_relations = on
        self.redraw()

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self):
        if self.plane is None:
            self.stack.setCurrentIndex(0)
            self.viewer.show_reference_planes(True, self.win.model_size() * 1.2)
            self.viewer.pick_faces = True
            self.win.status("Select a sketch plane (Front, Top, Right) or a planar face")
        else:
            self._begin_drawing()

    def _set_plane(self, plane, spec=None):
        self.plane = plane
        self.plane_spec = spec
        self._begin_drawing()

    def on_click(self, info):
        if self.drawing:
            return
        if info.kind == "plane":
            pl = self.win.plane_by_name(info.plane)
            if pl is not None:
                self._set_plane(pl, self.win.plane_spec_by_name(info.plane))
        elif info.kind == "face":
            f = self.doc.faces[info.face]
            if not f.is_planar:
                self.win.status("That face is not planar - select a flat face or a plane")
                return
            from ..core.refs import plane_on_face
            from ..fea.study import FaceRef
            self._set_plane(plane_on_face(f), {"type": "face", "ref": FaceRef.from_face(f).to_dict()})

    def _begin_drawing(self):
        self.drawing = True
        self.viewer.show_reference_planes(False)
        self.stack.setCurrentIndex(1)
        self.viewer.enter_sketch_mode(self.plane)
        if self.sketch.entities:
            self.viewer.fit()
        self.win.set_sketch_tools_enabled(True)
        self.set_tool(self.initial_tool)
        self.changed(checkpoint=False, solve=True)

    def _bindings_for(self, fid):
        remaining = {c.id for c in self.sketch.constraints if c.driving}
        bindings = [b for b in self.doc.parameter_bindings if b["feature"] != fid]
        bindings.extend({"feature": fid, "field": f"dimension:{cid}", "expression": expression}
                        for cid, expression in self.dimension_equations.items() if cid in remaining)
        return bindings

    def ok(self):
        if self.plane is None:
            return True
        self.set_tool("select")
        if self.feature is None and self.sketch.is_empty():
            return True
        had_bindings = self.feature is not None and any(
            b["feature"] == self.feature.id for b in self.doc.parameter_bindings)
        if self.dimension_equations or had_bindings:
            from ..core.document import Document
            trial = Document.from_dict(self.doc.to_dict())
            if self.feature is None:
                candidate = trial.add_feature(SketchFeature(self.plane, self.sketch.copy(), self.plane_spec))
            else:
                candidate = trial.feature(self.feature.id)
                candidate.sketch = self.sketch.copy()
            try:
                trial.configure_parameters(trial.parameters, self._bindings_for(candidate.id))
            except ValueError as exc:
                self.message(str(exc))
                return False
        self.win.checkpoint()
        if self.feature is None:
            feat = self.doc.add_feature(SketchFeature(self.plane, self.sketch, self.plane_spec))
            self.win.select_feature_later = feat.id
        else:
            feat = self.feature
            feat.sketch = self.sketch
        self.doc.parameter_bindings = self._bindings_for(feat.id)
        self.win.rebuild()
        return True

    # ---- dynamic input -----------------------------------------------------------------------
    def start_dynamic_input(self, text: str) -> bool:
        """Typing a number after the first point of a shape opens its value fields."""
        t = self.tool
        if not (self.drawing and t is not None and t.dyn_fields and t.pts):
            return False
        if not text or text[0] not in "0123456789.-":
            return False
        from .dynamic_input import DynamicInput
        if self._dyn is None:
            self._dyn = DynamicInput(self.win)
        self._dyn.open(t.dyn_fields, text, self._dyn_change, self._dyn_commit, self._dyn_cancel)
        return True

    def _cursor_or_default(self):
        if self._raw_cursor is not None:
            return self._raw_cursor
        a = self.tool.anchor()
        return Snap(np.asarray(a, float) + [1.0, 1.0] if a is not None else np.zeros(2), "free")

    def _dyn_change(self, values):
        if self.tool is None:
            return
        self.tool.locked = dict(values)
        self.tool.move(self._cursor_or_default(), None)
        self.redraw(preview_only=True)

    def _dyn_commit(self, values):
        if self.tool is None:
            return
        self.tool.locked = dict(values)
        self.tool.click(self._cursor_or_default())
        self.refresh_prompt()
        self.redraw()

    def _dyn_cancel(self):
        if self.tool is not None:
            self.tool.locked = {}
        self.redraw()

    def dynamic_input_done(self):
        if self._dyn is not None and self._dyn.isVisible():
            self._dyn.close_input()

    def cleanup(self):
        if getattr(self, "_editor", None) is not None:
            self._editor.close()
        self.dynamic_input_done()
        self.viewer.set_fill("sk_regions", [], C_REGION)
        if self.tool:
            self.tool.deactivate()
        self.viewer.exit_sketch_mode()
        self.viewer.show_reference_planes(False)
        self.win.set_sketch_tools_enabled(False)

    # ---- tools -----------------------------------------------------------------------------------
    def set_tool(self, key: str):
        if key not in TOOL_BY_KEY:
            key = "select"
        if self.tool:
            self.tool.deactivate()
        self.tool = TOOL_BY_KEY[key](self)
        self.tool.activate()
        if not self.drawing:
            return
        for k, b in self.tool_buttons.items():
            b.setChecked(k == key)
        while self.tool_opts.count():
            w = self.tool_opts.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        opts = self.tool.options()
        if opts is not None:
            self.tool_opts.addWidget(opts)
        self.tool_box.setTitle(self.tool.label)
        for group in self.geometry_tool_groups:
            group.setVisible(key != "add_relations")
        self.numeric_hint.setVisible(self.tool.numeric)
        self.refresh_prompt()
        self.win.sync_sketch_tool(key)
        self.redraw()

    def refresh_prompt(self):
        if self.tool:
            self.prompt_label.setText(self.tool.prompt())
            self.win.status(self.tool.prompt())

    def message(self, text):
        self.win.status(f"⚠ {text}")

    # ---- history ---------------------------------------------------------------------------------
    def checkpoint(self, label=""):
        self.history.append(self._snapshot())
        del self.history[:-100]
        self.future.clear()

    def _snapshot(self):
        return {"sketch": self.sketch.to_dict(), "equations": dict(self.dimension_equations)}

    def _restore(self, d):
        self.sketch = Sketch.from_dict(d["sketch"])
        self.dimension_equations = dict(d["equations"])
        self.selection = [r for r in self.selection if self._ref_alive(r)]
        self.hover = None

    def rollback(self):
        if self.history:
            self._restore(self.history.pop())

    def undo(self):
        if self.tool and self.tool.escape():
            self.redraw()
            return
        if self.history:
            self.future.append(self._snapshot())
            self._restore(self.history.pop())
            self.changed(checkpoint=False)

    def redo(self):
        if self.future:
            self.history.append(self._snapshot())
            self._restore(self.future.pop())
            self.changed(checkpoint=False)

    def _ref_alive(self, r):
        k, i = r
        if k == "p":
            return i in self.sketch.points
        if k == "e":
            return i in self.sketch.entities
        return self.sketch.constraint(i) is not None

    def apply(self, fn, label=""):
        """Run a modifying operation with undo and error handling. fn(sketch) -> result."""
        self.checkpoint(label)
        try:
            result = fn(self.sketch)
            res = solver.solve(self.sketch)
            if not res.ok:
                raise SketchError("The sketch could not be solved after this operation")
        except SketchError as e:
            self.rollback()
            self.message(str(e))
            self.redraw()
            return None
        self.changed(checkpoint=False, solve=False)
        return result if result is not None else True

    def preview_of(self, fn, copy_all=False, only=None):
        """Run fn on a copy of the sketch and return the new geometry as polylines."""
        copy = self.sketch.copy()
        before = set(copy.entities)
        try:
            out = fn(copy)
        except (SketchError, ValueError, ZeroDivisionError, np.linalg.LinAlgError):
            return []
        ents = [copy.entities[i] for i in copy.entities if i not in before]
        if not ents and isinstance(out, list):
            ents = [e for e in out if hasattr(e, "id") and e.id in copy.entities]
        polys = []
        px = self.viewer.world_per_pixel()
        for e in ents:
            s = copy.sample(e)
            if isinstance(e, PointEntity):
                continue
            polys += sd.dashed(s, px) if e.construction else [s]
        return polys

    # ---- snapping ----------------------------------------------------------------------------------
    def model_snaps(self):
        """Snap targets from the solid: its edges lying in the sketch plane (sketching on a
        face: the face outline, hole centres, ...). Cached per plane."""
        key = (id(self.plane), id(getattr(self.doc, "body", None)))
        if getattr(self, "_model_snap_key", None) == key:
            return self._model_snap_data
        pts, segs = [], []
        body = getattr(self.doc, "body", None) if hasattr(self, "doc") else None
        if body is not None and self.plane is not None:
            from ..core import geometry
            try:
                edges = geometry.edges_on_plane(body, self.plane.origin, self.plane.normal)
            except Exception:  # noqa: BLE001 - snapping is a convenience
                edges = []
            for e in edges:
                uv = np.array([self.plane.to_local(q) for q in e["pts"]], float)
                segs.append(uv)
                if e["kind"] == "circle":
                    c = np.array(self.plane.to_local(e["center"]), float)
                    pts.append((c, "model_center"))
                    r = e["r"]
                    for d in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                        q = c + r * np.array(d, float)
                        if np.min(np.linalg.norm(uv - q, axis=1)) < r * 0.02 + 1e-9:
                            pts.append((q, "model_quad"))
                if not e["closed"]:
                    pts += [(uv[0], "model_point"), (uv[-1], "model_point")]
                if e["kind"] == "line":
                    pts.append(((uv[0] + uv[-1]) / 2, "model_mid"))
        self._model_snap_key, self._model_snap_data = key, (pts, segs)
        return pts, segs

    def _is_center(self, pid) -> bool:
        return any(isinstance(e, (Circle, Arc)) and e.center == pid
                   for e in self.sketch.entities.values())

    def snap_on(self, kind) -> bool:
        """Is this snap type active for the next pick? A Quick Snap forces one type alone."""
        if self.quick_snap is not None:
            return kind == self.quick_snap
        return self.inference and self.snap_enabled.get(kind, True)

    def _special_snaps(self, p, tol, exclude=frozenset()):
        """Nearest of: model points, midpoints, quadrants and intersections within tol."""
        sk = self.sketch
        on = self.snap_on
        cands = [(q, k, None) for q, k in self.model_snaps()[0]] if on("model") else []
        near = []
        for e in sk.entities.values():
            if isinstance(e, PointEntity) or e.id in exclude:
                continue
            if isinstance(e, Line) and on("mid"):
                cands.append(((sk.pt(e.p1) + sk.pt(e.p2)) / 2, "mid", ("e", e.id)))
            elif isinstance(e, (Circle, Arc)) and on("quadrant"):
                c, r = sk.pt(e.center), sk.radius(e)
                for d in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                    q = c + r * np.array(d, float)
                    if isinstance(e, Circle) or ops.dist_to_entity(sk, e, q) < 1e-7 * max(r, 1):
                        cands.append((q, "quadrant", ("e", e.id)))
            if on("intersection") and not isinstance(e, Spline) and ops.dist_to_entity(sk, e, p) < 2 * tol:
                near.append(e)
        for i, a in enumerate(near):
            for b in near[i + 1:]:
                for q in ops.intersections(sk, a, [b]):
                    cands.append((q, "intersection", (("e", a.id), ("e", b.id))))
        best, bd = None, tol
        for q, k, ref in cands:
            d = float(np.linalg.norm(np.asarray(q) - p))
            if d < bd - 1e-9 or (best is not None and abs(d - bd) <= 1e-9
                                 and SNAP_RANK.get(k, 9) < SNAP_RANK.get(best.kind, 9)):
                best, bd = Snap(np.asarray(q, float), k, ref), d
        return best

    def _relative_snaps(self, p, anchor, tol, exclude=frozenset()):
        """Snaps that depend on the previous point: tangent to a circle / arc, perpendicular
        to a line (the foot of the perpendicular), parallel to a line, angle steps."""
        sk = self.sketch
        on = self.snap_on
        a = np.asarray(anchor, float)
        best, bd = None, tol
        for e in sk.entities.values():
            if e.id in exclude or e.construction and isinstance(e, PointEntity):
                continue
            if on("tangent") and isinstance(e, (Circle, Arc)):
                c, r = sk.pt(e.center), sk.radius(e)
                dv = a - c
                dist = float(np.linalg.norm(dv))
                if dist > r * (1 + 1e-9):
                    phi = math.acos(r / dist)
                    base = math.atan2(dv[1], dv[0])
                    for sgn in (1, -1):
                        q = c + r * np.array([math.cos(base + sgn * phi), math.sin(base + sgn * phi)])
                        if isinstance(e, Arc) and ops.dist_to_entity(sk, e, q) > 1e-7 * max(r, 1):
                            continue
                        d = float(np.linalg.norm(q - p))
                        if d < bd:
                            best, bd = Snap(q, "tangent", ("e", e.id)), d
            if on("perpendicular") and isinstance(e, Line):
                p1, p2 = sk.pt(e.p1), sk.pt(e.p2)
                u = p2 - p1
                L2 = float(u @ u)
                if L2 > 1e-24:
                    t = float((a - p1) @ u) / L2
                    q = p1 + t * u
                    if -1e-9 <= t <= 1 + 1e-9 and np.linalg.norm(q - a) > tol:
                        d = float(np.linalg.norm(q - p))
                        if d < bd:
                            best, bd = Snap(q, "perpendicular", ("e", e.id)), d
        if best is not None:
            return best
        r = p - a
        dist = float(np.linalg.norm(r))
        if dist < 2 * tol:
            return None
        if on("parallel"):
            for e in sk.entities.values():
                if e.id in exclude or not isinstance(e, Line):
                    continue
                u = sk.pt(e.p2) - sk.pt(e.p1)
                n = float(np.linalg.norm(u))
                if n < 1e-12:
                    continue
                u = u / n
                if abs(u[0]) > 1 - 1e-9 or abs(u[1]) > 1 - 1e-9:
                    continue                          # horizontal / vertical: the H/V snap
                if a.tolist() in (sk.pt(e.p1).tolist(), sk.pt(e.p2).tolist()):
                    continue                          # the line we are continuing from
                q = a + u * float(r @ u)
                d = float(np.linalg.norm(q - p))
                if d < bd:
                    guide = np.array([sk.pt(e.p1), sk.pt(e.p2)])
                    best, bd = Snap(q, "parallel", ("e", e.id), [guide, np.array([a, q])]), d
            if best is not None:
                return best
        if on("angle"):
            step = math.radians(max(self.angle_step, 1e-3))
            ang = math.atan2(r[1], r[0])
            k = round(ang / step) * step
            if abs(math.degrees(k)) % 90 > 1e-6:      # 0 / 90 / 180: the H/V snap does those
                q = a + dist * np.array([math.cos(k), math.sin(k)])
                if np.linalg.norm(q - p) < tol * 0.8:
                    return Snap(q, "angle", None, [np.array([a, q])])
        return None

    def snap(self, u, v, anchor=None, exclude_points=frozenset()) -> Snap:
        """The cursor position after snapping, SolidWorks Quick Snaps style.
        exclude_points: points being dragged (not snapped to themselves)."""
        sk = self.sketch
        p = np.array([u, v], float)
        px = self.viewer.world_per_pixel()
        tol = SNAP_PX * px * (1.6 if self.quick_snap else 1.0)
        on = self.snap_on
        ex_ents = frozenset(e.id for e in sk.entities.values()
                            if set(e.point_ids()) & set(exclude_points))
        if on("point") or on("center"):
            used = {q for e in sk.entities.values() for q in e.point_ids()} | {0}
            best, bd = None, tol
            for pid in used - set(exclude_points):
                if pid not in sk.points:
                    continue
                is_c = self._is_center(pid)
                if not on("center" if is_c else "point"):
                    continue
                d = float(np.linalg.norm(sk.pt(pid) - p))
                if d < bd:
                    best, bd = pid, d
            if best is not None:
                return Snap(sk.pt(best), "center" if self._is_center(best) else "point", ("p", best))
        special = self._special_snaps(p, tol, ex_ents)
        if special is not None:
            return special
        if anchor is not None:
            rel = self._relative_snaps(p, anchor, tol, ex_ents)
            if rel is not None and rel.kind in ("tangent", "perpendicular"):
                return rel
        else:
            rel = None
        q = p.copy()
        kind, guides = "free", []
        if on("hv") and anchor is not None:
            if abs(p[1] - anchor[1]) < tol:
                q[1] = anchor[1]
                kind = "h"
                guides.append(np.array([anchor, q]))
            elif abs(p[0] - anchor[0]) < tol:
                q[0] = anchor[0]
                kind = "v"
                guides.append(np.array([anchor, q]))
        if kind == "free" and rel is not None:
            return rel                                 # parallel / angle
        if on("align") and kind == "free":
            used = ({pid for e in sk.entities.values() for pid in e.point_ids()} | {0}) - set(exclude_points)
            for pid in used:
                pt = sk.pt(pid)
                if abs(p[0] - pt[0]) < tol * 0.6:
                    q[0] = pt[0]
                    guides.append(np.array([pt, q]))
                    kind = "align"
                    break
            for pid in used:
                pt = sk.pt(pid)
                if abs(p[1] - pt[1]) < tol * 0.6:
                    q[1] = pt[1]
                    guides.append(np.array([pt, q]))
                    kind = "align"
                    break
        if kind == "free" and on("curve"):
            hit = ops.pick(sk, q, tol, points=False, entities=True)
            if hit and hit[1] not in ex_ents and not isinstance(sk.entities[hit[1]], (Spline, PointEntity)):
                on_c = ops.nearest_on(sk, sk.entities[hit[1]], q)
                if on_c is not None:
                    return Snap(on_c, "curve", hit)
        if kind == "free" and on("model"):
            best, bd = None, tol                          # nearest point on a model edge
            for seg in self.model_snaps()[1]:
                on_m = _nearest_on_polyline(seg, q)
                d = float(np.linalg.norm(on_m - q))
                if d < bd:
                    best, bd = on_m, d
            if best is not None:
                return Snap(best, "model_edge")
        if (self.snap_grid or self.quick_snap == "grid") and kind in ("free",):
            s = self.viewer.grid_step()
            q = np.round(q / s) * s
            kind = "grid"
        return Snap(q, kind, None, guides)

    def commit(self, s: Snap, sk=None) -> int:
        """Turn a snap into a sketch point (with the implied relation)."""
        sk = sk or self.sketch
        if s.kind in ("point", "center"):
            return s.ref[1]
        # a point placed exactly on an existing point (typed coordinates, grid) *is* that
        # point - otherwise loops would close through two separate coincident points
        eps = 1e-9 * max(sk.scale(), 1.0)
        used = {p for e in sk.entities.values() for p in e.point_ids()} | {0}
        for pid in used:
            if pid in sk.points and np.linalg.norm(sk.pt(pid) - s.uv) < eps:
                return pid
        pid = sk.add_point(*s.uv)
        if s.kind == "mid":
            sk.add_constraint("midpoint", [("p", pid), s.ref])
        elif s.kind in ("curve", "quadrant", "tangent", "perpendicular"):
            sk.add_constraint("coincident", [("p", pid), s.ref])
        elif s.kind == "intersection":
            for ref in s.ref:
                sk.add_constraint("coincident", [("p", pid), ref])
        return pid

    def drop_point(self, pid, s: Snap):
        """A dragged point released on a snap target joins it (SolidWorks): onto a point it
        becomes that point (coincident), onto a midpoint / curve it gets that relation."""
        sk = self.sketch
        if pid not in sk.points:
            return
        if s.kind in ("point", "center") and s.ref is not None:
            other = s.ref[1]
            if other == pid or other not in sk.points:
                return
            for e in sk.entities_using_point(pid):
                if other in e.point_ids():
                    return                       # both ends of one curve: that would collapse it
            keep, drop = (other, pid) if other == 0 or sk.points[other].fixed else (other, pid)
            sk.replace_point(drop, keep)
            return
        rel = {"mid": "midpoint", "curve": "coincident", "quadrant": "coincident",
               "tangent": "coincident", "perpendicular": "coincident"}.get(s.kind)
        if rel is not None and s.ref is not None and s.ref[1] in sk.entities:
            from ..core.sketch import Constraint
            c = Constraint(0, rel, [("p", pid), tuple(s.ref)])
            solver.try_add(sk, c)

    def snap_relation(self, sk, line, s: Snap):
        """The relation a snap implies for the line just drawn to it (SolidWorks)."""
        rel = {"tangent": "tangent", "perpendicular": "perpendicular", "parallel": "parallel"}.get(s.kind)
        if rel is None or s.ref is None or s.ref[1] not in sk.entities:
            return
        if rel == "tangent":
            from ..core.sketch import Constraint
            # the end on the circle is the one placed at the snap
            end = min((line.p1, line.p2), key=lambda q: float(np.linalg.norm(sk.pt(q) - s.uv)))
            solver.try_add(sk, Constraint(0, "tangent", [("e", line.id), tuple(s.ref)],
                                          data={"at": end}))
            return
        for c in make_relation(sk, rel, [("e", line.id), tuple(s.ref)]):
            solver.try_add(sk, c)

    def parse_point(self, text, base, cursor):
        t = text.strip().replace(" ", "")
        rel = t.startswith("@")
        if rel:
            t = t[1:]
        origin = base if (rel and base is not None) else np.zeros(2)
        if "<" in t:
            L, a = t.split("<", 1)
            ang = math.radians(evaluate(a))
            return origin + evaluate(L) * np.array([math.cos(ang), math.sin(ang)])
        if "," in t or ";" in t:
            x, y = t.replace(";", ",").split(",", 1)
            return origin + np.array([evaluate(x), evaluate(y)])
        L = evaluate(t)
        if base is None:
            raise ValueError("Type x,y for the first point")
        d = (cursor - base) if cursor is not None else np.array([1.0, 0.0])
        n = np.linalg.norm(d)
        d = d / n if n > 1e-12 else np.array([1.0, 0.0])
        return base + d * L

    # ---- hit testing / selection -------------------------------------------------------------------
    def hit_test(self, uv, display, points=True, dims=True, glyphs=True, entities=True):
        uv = np.asarray(uv, float)
        disp = np.asarray(display, float)
        if dims:
            for dxy, cid in self._dim_hits:
                if np.linalg.norm(dxy - disp) < 18:
                    return ("c", cid)
        if glyphs and self.show_relations:
            for dxy, cid in self._glyph_hits:
                if np.linalg.norm(dxy - disp) < 10:
                    return ("c", cid)
        tol = PICK_PX * self.viewer.world_per_pixel()
        return ops.pick(self.sketch, uv, tol, points=points, entities=entities)

    def select_at(self, uv, ev, hit="__auto__"):
        if hit == "__auto__":
            hit = self.hit_test(uv, ev.display)
        if hit is None:
            if not ev.ctrl:
                self.selection = []
        elif ev.ctrl or isinstance(self.tool, _SelectionTool) or getattr(self.tool, "additive_selection", False):
            if hit in self.selection:
                self.selection.remove(hit)
            else:
                self.selection.append(hit)
        else:
            self.selection = [hit]
        self.selection_changed()

    def selected_entities(self) -> list[int]:
        return [i for k, i in self.selection if k == "e" and i in self.sketch.entities]

    def selection_changed(self):
        self._refresh_selection_panel()
        self.redraw()

    # ---- viewer events ------------------------------------------------------------------------------------
    def _snap_for_tool(self, u, v):
        if self.tool.snapping:
            return self.snap(u, v, self.tool.anchor())
        return Snap(np.array([u, v], float), "free")

    def on_sketch_press(self, u, v, ev):
        self.last_display = ev.display
        s = self._snap_for_tool(u, v)
        self.tool.press(s, ev)
        if self.quick_snap is not None and self.tool.snapping:
            self.quick_snap = None
            self.win.sync_quick_snap(None)
        self.refresh_prompt()
        self.redraw()

    def on_sketch_move(self, u, v, ev):
        self.last_display = ev.display
        s = self._snap_for_tool(u, v)
        self._raw_cursor = s
        self.tool.move(s, ev)
        if self._dyn is not None and self._dyn.isVisible():
            self._dyn.follow()
        txt = f"X {s.uv[0]:.3f}   Y {s.uv[1]:.3f} mm"
        a = self.tool.anchor()
        if a is not None:
            d = s.uv - a
            txt += f"    Δ {np.linalg.norm(d):.3f}  ∠ {math.degrees(math.atan2(d[1], d[0])):.1f}°"
        self.win.status(f"{self.tool.prompt()}      {txt}")
        self.redraw(preview_only=True)

    def on_sketch_release(self, u, v, ev):
        self.last_display = ev.display
        self.tool.release(self._snap_for_tool(u, v), ev)
        self.refresh_prompt()
        self.redraw()

    def on_sketch_double(self, u, v, ev):
        hit = self.hit_test((u, v), ev.display)
        if hit and hit[0] == "c":
            c = self.sketch.constraint(hit[1])
            if c is not None and is_dimension(c):
                self.edit_dimension(c)
                return
        if isinstance(self.tool, SplineTool):
            self.tool.click(self._snap_for_tool(u, v))
            self.tool.finish()
            self.redraw()
            return
        self.on_sketch_press(u, v, ev)

    def on_sketch_finish(self):
        if not self.tool.finish() and self.tool.key != "select":
            self.set_tool("select")
        self.refresh_prompt()
        self.redraw()

    def on_escape(self):
        if not self.drawing:
            return False
        if self.tool.escape():
            self.redraw()
            return True
        if self.tool.key != "select":
            self.set_tool("select")
            return True
        if self.selection:
            self.selection = []
            self.selection_changed()
            return True
        return True    # Esc never discards the sketch; use Cancel for that

    def on_delete(self):
        if not self.drawing or not self.selection:
            return False
        refs = list(self.selection)
        self.selection = []
        self.apply(lambda sk: ops.delete(sk, refs), "Delete")
        self._refresh_selection_panel()
        return True

    def command_enter(self) -> bool:
        """Space / Enter, AutoCAD style: finish the current step; with nothing pending, end
        the tool (back to Select); with Select idle, let the window repeat the last command."""
        if not self.drawing or self.tool is None:
            return False
        if self.tool.enter():
            self.refresh_prompt()
            self.redraw()
            return True
        if self.tool.key != "select":
            self.set_tool("select")
            return True
        return False

    def numeric_prompt(self):
        if not self.drawing or self.tool is None or not self.tool.numeric:
            return None

        def handler(text):
            try:
                self.tool.typed(text)
            except (ValueError, SyntaxError, ZeroDivisionError) as e:
                self.message(str(e) or "Invalid input")
            self.refresh_prompt()
            self.redraw()
        return self.tool.prompt(), handler

    # ---- dimensions and relations ----------------------------------------------------------------------
    def add_dimension(self, c: Constraint):
        status = solver.check_constraint(self.sketch, c)
        if status != "ok":
            r = QMessageBox.question(
                self.win, "Smart Dimension",
                "Adding this dimension will over-define the sketch.\n\n"
                "Make it a driven (reference) dimension instead?",
                QMessageBox.Yes | QMessageBox.Cancel)
            if r != QMessageBox.Yes:
                return
            c.driving = False
            self.checkpoint("Dimension")
            c.id = self.sketch._next_cid
            self.sketch._next_cid += 1
            self.sketch.constraints.append(c)
            self.changed(checkpoint=False)
            return
        self.checkpoint("Dimension")
        ok, msg = solver.try_add(self.sketch, c)
        if not ok:
            self.rollback()
            self.message(msg)
            return
        self.changed(checkpoint=False)
        self.edit_dimension(c)

    def edit_dimension(self, c: Constraint):
        c = self.sketch.constraint(c.id) or c
        is_angle = c.type == "angle"
        cur = math.degrees(c.value) if is_angle else c.value
        if not c.driving:
            self.message("Driven dimensions show a measurement; make it driving to edit")
            return

        def done(text):
            expression = text.strip()[1:].strip() if text.strip().startswith("=") else None
            try:
                if expression is not None:
                    from ..core.parameters import parameter_value
                    value = parameter_value(self.doc.parameters, expression, "angle" if is_angle else "length")
                else:
                    v = evaluate(text)
                    value = math.radians(v) if is_angle else v
                if not math.isfinite(value):
                    raise ValueError("The value must be finite")
                if c.type in ("diameter", "radius", "distance") and value <= 0:
                    raise ValueError("The value must be positive")
                target = self.sketch.constraint(c.id)
                if target is None:
                    raise ValueError("This dimension no longer exists")
                trial = self.sketch.copy()
                ok, msg = solver.set_dimension(trial, trial.constraint(c.id), value)
                if not ok:
                    raise ValueError(msg)
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
                self._editor.error.setText(str(exc))
                self.message(str(exc))
                return False
            self.checkpoint("Modify dimension")
            self.sketch = trial
            if expression is None:
                self.dimension_equations.pop(c.id, None)
            else:
                self.dimension_equations[c.id] = expression
            self.changed(checkpoint=False)
            return True

        name = {"diameter": "Diameter", "radius": "Radius", "angle": "Angle (degrees)"}.get(c.type, "Distance")
        expression = self.dimension_equations.get(c.id)
        initial = "=" + expression if expression is not None else f"{cur:.6g}"
        self._editor = ValueEditor(self.win, f"Modify {name}: =name links a parameter; a number unlinks", initial, done)
        self._editor.setMinimumWidth(440)
        self._editor.popup()

    def add_relation(self, rtype):
        refs = [r for r in self.selection if r[0] in ("p", "e") and self._ref_alive(r)]
        if rtype not in applicable_relations(self.sketch, refs):
            name = RELATION_NAMES.get(rtype, rtype)
            self.message(f"{name}: select suitable entities first (e.g. "
                         f"{'two lines' if rtype in ('parallel', 'perpendicular', 'equal', 'collinear') else 'two points or one or more lines' if rtype in ('horizontal', 'vertical') else 'the entities to relate'})")
            return
        cons = make_relation(self.sketch, rtype, refs)
        self.checkpoint("Relation")
        for c in cons:
            ok, msg = solver.try_add(self.sketch, c)
            if not ok:
                self.rollback()
                self.message(f"{RELATION_NAMES.get(rtype, rtype)}: {msg}")
                self.redraw()
                return
        self.changed(checkpoint=False)

    def toggle_construction(self):
        ids = self.selected_entities()
        if ids:
            self.apply(lambda sk: ops.toggle_construction(sk, ids), "Construction")

    def toggle_driven(self):
        cs = [self.sketch.constraint(i) for k, i in self.selection if k == "c"]
        cs = [c for c in cs if c is not None and is_dimension(c)]
        if not cs:
            return
        self.checkpoint("Driven")
        for c in cs:
            if c.driving:
                c.driving = False
            else:
                c.value = measure(self.sketch, c)
                if solver.check_constraint(self.sketch.copy(), Constraint(0, c.type, c.refs, c.value, True, None, c.data)) != "ok":
                    self.message("Making this dimension driving would over-define the sketch")
                    continue
                c.driving = True
        self.changed(checkpoint=False)

    def _delete_from_panel(self):
        ids = [it.data(Qt.UserRole) for it in self.exist_list.selectedItems()]
        if ids:
            self.apply(lambda sk: [sk.remove_constraint(i) for i in ids], "Delete relation")
        else:
            self.on_delete()
        self._refresh_selection_panel()

    # ---- state refresh ------------------------------------------------------------------------------------
    def changed(self, checkpoint=True, solve=True):
        if solve:
            res = solver.solve(self.sketch)
            self.solve_ok = res.ok
        else:
            self.solve_ok = True
        try:
            self.analysis = solver.analyze(self.sketch)
        except Exception:
            self.analysis = None
        self.selection = [r for r in self.selection if self._ref_alive(r)]
        self._refresh_status()
        self._refresh_selection_panel()
        self.redraw()

    def after_drag(self):
        if self.region_count:
            self.viewer.set_fill("sk_regions", [], C_REGION)
            self.region_count = 0
        self.redraw()

    def _refresh_status(self):
        a = self.analysis
        if not self.solve_ok:
            self.dof_label.setText("<span style='color:#c0392b'>&#9888; Sketch could not be solved</span>")
        elif a is None or self.sketch.is_empty():
            self.dof_label.setText("")
        elif a.fully_defined:
            self.dof_label.setText("<b>Fully Defined</b>")
        else:
            self.dof_label.setText(f"<span style='color:{C_UNDER}'><b>Under Defined</b> "
                                   f"({a.dof} degrees of freedom)</span>")
        self.error_points = []
        self.update_region_fill()
        if not self.sketch.curves(include_construction=False):
            self.profile_label.setText("")
            return
        try:
            regions = self.sketch.regions()
            n_holes = sum(len(r.holes) for r in regions)
            txt = f"{len(regions)} closed profile(s)" + (f", {n_holes} hole(s)" if n_holes else "")
            self.profile_label.setText(f"<span style='color:#2f9e57'>&#10004; {txt}</span>")
        except SketchError as e:
            self.error_points = e.points
            self.profile_label.setText(f"<span style='color:#c77700'>&#9888; {e}</span>")

    def update_region_fill(self):
        """Shade every enclosed area of the sketch (also areas formed by crossing curves):
        these are the contours Extrude can use, so an open outline is easy to spot."""
        v = self.viewer
        self.region_count = 0
        if not (self.shade_regions and self.drawing and self.plane is not None
                and self.sketch.curves(include_construction=False)):
            v.set_fill("sk_regions", [], C_REGION)
            v.render()
            return
        from ..core import geometry
        try:
            regions = geometry.sketch_regions(self.sketch, self.plane)
        except Exception:  # noqa: BLE001 - shading is only a visual aid
            regions = []
        self.region_count = len(regions)
        tris = [self.plane.to_world_many(r.triangles.reshape(-1, 2)).reshape(-1, 3, 3)
                for r in regions if len(r.triangles)]
        v.set_fill("sk_regions", np.vstack(tris) if tris else [], C_REGION, REGION_OPACITY)
        v.render()

    def _describe_ref(self, r):
        k, i = r
        if k == "p":
            p = self.sketch.pt(i)
            return icon("point"), ("Origin" if i == 0 else f"Point{i}") + f" ({p[0]:.4g}, {p[1]:.4g})"
        if k == "e":
            e = self.sketch.entities[i]
            ic = {"line": "centerline" if e.construction else "line", "circle": "circle",
                  "arc": "arc_3pt", "spline": "spline", "point": "point"}[e.kind]
            return icon(ic), self.sketch.describe(e)
        c = self.sketch.constraint(i)
        return icon("smart_dim" if is_dimension(c) else "relation"), self._describe_constraint(c)

    def _describe_constraint(self, c):
        if is_dimension(c):
            v = c.value if c.driving else measure(self.sketch, c)
            kind = {"distance": "Distance", "hdistance": "Horizontal distance",
                    "vdistance": "Vertical distance", "point_line_distance": "Distance",
                    "diameter": "Diameter", "radius": "Radius", "angle": "Angle"}[c.type]
            return f"{kind} {format_value(c, v)}" + ("" if c.driving else " (driven)")
        names = []
        for k, i in c.refs:
            names.append(("Origin" if i == 0 else f"Point{i}") if k == "p"
                         else f"{self.sketch.entities[i].label}{i}")
        return f"{RELATION_NAMES.get(c.type, c.type)}: {', '.join(names)}"

    def _refresh_selection_panel(self):
        self.sel_list.clear()
        for r in self.selection:
            if not self._ref_alive(r):
                continue
            ic, text = self._describe_ref(r)
            self.sel_list.addItem(QListWidgetItem(ic, text))
        while self.rel_buttons.count():
            w = self.rel_buttons.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        geo = [r for r in self.selection if r[0] in ("p", "e") and self._ref_alive(r)]
        rels = applicable_relations(self.sketch, geo) if geo else []
        for i, rt in enumerate(rels):
            b = QPushButton(f"{TYPES[rt].glyph}  {RELATION_NAMES[rt]}")
            b.clicked.connect(lambda _=False, t=rt: self.add_relation(t))
            self.rel_buttons.addWidget(b, i // 2, i % 2)
        self.rel_label.setVisible(bool(rels))
        self.exist_list.clear()
        shown = set()
        for r in self.selection:
            if r[0] == "c":
                cands = [self.sketch.constraint(r[1])]
            elif self._ref_alive(r):
                cands = self.sketch.constraints_of(*r)
            else:
                cands = []
            for c in cands:
                if c is None or c.id in shown:
                    continue
                shown.add(c.id)
                it = QListWidgetItem(icon("smart_dim" if is_dimension(c) else "relation"),
                                     self._describe_constraint(c))
                it.setData(Qt.UserRole, c.id)
                self.exist_list.addItem(it)
                if r[0] == "c":
                    it.setSelected(True)
        self.driven_btn.setEnabled(any(r[0] == "c" and self.sketch.constraint(r[1]) is not None
                                       and is_dimension(self.sketch.constraint(r[1]))
                                       for r in self.selection))

    # ---- drawing ---------------------------------------------------------------------------------------------
    def redraw(self, preview_only=False):
        if not self.drawing:
            return
        v = self.viewer
        px = v.world_per_pixel()
        sk = self.sketch
        if not preview_only:
            a = self.analysis
            groups: dict = {}
            points: dict = {}
            sel = set(self.selection)
            for e in sk.entities.values():
                ref = ("e", e.id)
                defined = a is not None and a.entity_defined(sk, e) and self.solve_ok
                if ref in sel:
                    color = C_SEL
                elif ref == self.hover:
                    color = C_HOVER
                else:
                    color = C_DEF if defined else C_UNDER
                if isinstance(e, PointEntity):
                    points.setdefault(color, []).append(sk.pt(e.p))
                    continue
                s = sk.sample(e)
                width = 3.2 if color in (C_SEL, C_HOVER) else 2.2
                polys = sd.dashed(s, px) if e.construction else [s]
                groups.setdefault((color, width), []).extend(polys)
            used = {p for e in sk.entities.values() if not isinstance(e, PointEntity)
                    for p in e.point_ids()}
            for pid in used:
                ref = ("p", pid)
                if ref in sel:
                    color = C_SEL
                elif ref == self.hover:
                    color = C_HOVER
                elif pid == 0 or (a is not None and pid not in a.free_points and self.solve_ok):
                    color = C_DEF
                else:
                    color = C_UNDER
                points.setdefault(color, []).append(sk.pt(pid))
            for name in list(getattr(self, "_layers", [])):
                v.sketch_layer(name, [], "#000")
            self._layers = []
            for i, ((color, width), polys) in enumerate(groups.items()):
                name = f"sk_curves_{i}"
                v.sketch_layer(name, polys, color, width, lift=1.5)
                self._layers.append(name)
            for name in list(getattr(self, "_pt_layers", [])):
                v.sketch_points(name, [], "#000")
            self._pt_layers = []
            for i, (color, pts) in enumerate(points.items()):
                name = f"sk_pts_{i}"
                v.sketch_points(name, pts, color, 9 if color in (C_SEL, C_HOVER) else 6.5, lift=2.2)
                self._pt_layers.append(name)
            v.sketch_points("sk_origin", [(0, 0)], "#dd2222", 8, lift=2.4)
            txt, txt_sel = [], []
            for t in sk.texts:
                (txt_sel if t["id"] == self.text_selection else txt).extend(self.text_polylines(t))
            v.sketch_layer("sk_texts", txt, C_DEF, 1.6, lift=1.5)
            v.sketch_layer("sk_texts_sel", txt_sel, C_SEL, 2.4, lift=1.6)
            self._draw_dimensions(px)
            self._draw_relations(px)
            v.sketch_points("sk_errors", self.error_points, C_ERR, 14, lift=2.6)
        self._draw_preview(px)
        v.render()

    def text_polylines(self, t) -> list:
        """(u, v) outlines of a text item (cached)."""
        import json
        from ..core import geometry
        from ..core.plane import SketchPlane
        key = json.dumps({k: t[k] for k in ("text", "at", "height", "font", "bold", "italic", "angle")
                          if k in t}, sort_keys=True)
        if key not in self._text_cache:
            try:
                pl = SketchPlane([0, 0, 0], [0, 0, 1], [1, 0, 0])
                self._text_cache[key] = geometry.text_outlines(
                    dict({"angle": 0.0, "bold": False, "italic": False}, **t), pl)
            except Exception:  # noqa: BLE001 - e.g. a missing font
                self._text_cache[key] = []
        return self._text_cache[key]

    def _draw_dimensions(self, px):
        v = self.viewer
        lines_drv, lines_dvn, texts, hits = [], [], [], []
        for c in self.sketch.constraints:
            if not is_dimension(c):
                continue
            try:
                polys, tp, text = sd.dimension_graphics(self.sketch, c, px)
            except Exception:
                continue
            ref = ("c", c.id)
            color = C_SEL if ref in self.selection else (C_HOVER if ref == self.hover else
                                                         (C_DIM if c.driving else C_DRIVEN))
            (lines_drv if c.driving else lines_dvn).extend(polys)
            texts.append((tp, text, color, 15, "#ffffff"))
            hits.append((v.sketch_to_display(tp), c.id))
        v.sketch_layer("sk_dim_lines", lines_drv, C_DIM, 1.3, lift=1.8)
        v.sketch_layer("sk_dim_lines_driven", lines_dvn, C_DRIVEN, 1.3, lift=1.8)
        v.sketch_texts("sk_dim_text", texts)
        self._dim_hits = hits

    def _draw_relations(self, px):
        v = self.viewer
        items, hits = [], []
        if self.show_relations:
            count: dict = {}
            for c in self.sketch.constraints:
                t = TYPES.get(c.type)
                if t is None or t.dimension or c.type == "arc_internal":
                    continue
                ents = c.entity_ids()
                if ents and ents[0] in self.sketch.entities:
                    anchor, n = sd.entity_anchor(self.sketch, self.sketch.entities[ents[0]])
                    key = ("e", ents[0])
                elif c.point_refs():
                    anchor, n = self.sketch.pt(c.point_refs()[0]), np.array([0.7, 0.7])
                    key = ("p", c.point_refs()[0])
                else:
                    continue
                k = count.get(key, 0)
                count[key] = k + 1
                pos = anchor + n * (16 + 20 * k) * px
                ref = ("c", c.id)
                col = C_SEL if ref in self.selection else (C_HOVER if ref == self.hover else C_REL)
                items.append((pos, t.glyph, col, 12, "#e7f6ea"))
                hits.append((v.sketch_to_display(pos), c.id))
        v.sketch_texts("sk_rel_text", items)
        self._glyph_hits = hits

    def _draw_preview(self, px):
        v = self.viewer
        polys, guides, snap_pts = [], [], []
        dim_texts = []
        t = self.tool
        if t is not None:
            try:
                polys = t.preview()
            except (SketchError, ValueError, ZeroDivisionError):
                polys = []
            s = t.cursor
            if s is not None and (t.snapping or isinstance(t, SelectTool)):
                for g in s.guides:
                    guides += sd.dashed(g, px, 3, 3)
                if s.kind in SNAP_LABELS:
                    snap_pts.append((s.uv, s.kind))
            if isinstance(t, SmartDimensionTool):
                c = t.preview_dim()
                if c is not None:
                    try:
                        p2, tp, text = sd.dimension_graphics(self.sketch, c, px)
                        polys = list(polys) + p2
                        dim_texts.append((tp, text, C_PREV, 15, "#ffffff"))
                    except Exception:
                        pass
        box = isinstance(t, SelectTool) and t.box is not None
        v.sketch_layer("sk_preview", polys, C_BOX if box else C_PREV, 1.6 if box else 2.0, lift=2.0)
        v.sketch_layer("sk_guides", guides, C_GUIDE, 1.2, lift=1.9)
        marks, mmarks, labels = [], [], []
        for uv, kind in snap_pts:                 # AutoCAD-style snap markers + a label
            (mmarks if kind.startswith("model") else marks).extend(sd.snap_marker(kind, uv, px))
            labels.append((np.asarray(uv) + np.array([14, -16]) * px, SNAP_LABELS[kind],
                           "#8a4b00" if not kind.startswith("model") else "#00706f", 10, "#fff6e0"))
        v.sketch_layer("sk_snap", marks, "#ff8a00", 2.2, lift=3.0)
        v.sketch_layer("sk_snap_model", mmarks, "#00a0a0", 2.2, lift=3.0)
        v.sketch_texts("sk_snap_label", labels)
        v.sketch_texts("sk_dim_preview", dim_texts)
