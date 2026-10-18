"""Drawing window: SolidWorks-style drawing sheets (views, dimensions, title block) of a part
or an assembly, saved as .bcdrw and exported to PDF, DXF, DWG, SVG or PNG."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QFileSystemWatcher, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QFont, QFontMetricsF, QKeySequence,
                           QPainter, QPainterPath, QPen, QPolygonF, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QSplitter, QToolBar, QToolButton, QVBoxLayout, QWidget,
                               QTableWidget, QTableWidgetItem, QAbstractItemView)

from .. import APP_NAME
from ..core.drawing import (EXPORT_FILTERS, FILE_EXTENSION, LAYERS, ORIENTATIONS, SHEET_SIZES,
                            STANDARD_SCALES, TITLE_FIELDS, Arc, Dimension, Drawing, DrawingError,
                            Line, Poly, Solid, Text, _perp, _unit, scale_text)
from .command_line import CommandLine, CommandRegistry, TypeToCommandFilter
from .icons import icon
from .command_help import tooltip, description

DRW_FILTER = f"BauyrCAD drawing (*{FILE_EXTENSION})"
MODEL_FILTER = ("Models (*.bcad *.bcasm *.step *.stp *.igs *.iges *.brep);;BauyrCAD part (*.bcad);;"
                "BauyrCAD assembly (*.bcasm);;STEP (*.step *.stp)")
SEL = QColor(30, 110, 230)
HOVER = QColor(255, 120, 40)
PREVIEW = QColor(30, 110, 230)
CAP = 0.716                                  # cap height / em (Arial)
MOUSE_HINT = ("Left-drag: move views / dimensions  ·  Double-click a dimension: edit text  ·  "
              "Wheel: zoom  ·  Middle- or right-drag: pan  ·  Right-click: menu")


# =====================================================================================
# sheet canvas
# =====================================================================================
class SheetCanvas(QWidget):
    """Paints the drawing's primitives (sheet mm, y up) and forwards mouse events in sheet
    coordinates to the window."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.zoom = 2.0                       # px per mm
        self.origin = QPointF(40, 40)         # px position of sheet (0, H)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(400, 300)
        self._pan = None
        self._press_px = None
        self._fitted = False

    # ---- mapping ------------------------------------------------------------------------
    def to_px(self, p) -> QPointF:
        H = self.win.dr.size[1]
        return QPointF(self.origin.x() + p[0] * self.zoom, self.origin.y() + (H - p[1]) * self.zoom)

    def to_sheet(self, q) -> np.ndarray:
        H = self.win.dr.size[1]
        return np.array([(q.x() - self.origin.x()) / self.zoom,
                         H - (q.y() - self.origin.y()) / self.zoom])

    def fit(self):
        W, H = self.win.dr.size
        m = 24
        self.zoom = max(min((self.width() - 2 * m) / W, (self.height() - 2 * m) / H), 0.05)
        self.origin = QPointF((self.width() - W * self.zoom) / 2, (self.height() - H * self.zoom) / 2)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._fitted:
            self._fitted = True
            self.fit()

    # ---- painting -----------------------------------------------------------------------
    def _pen(self, layer, color=None, extra=0.0):
        lw, dashes, grey = LAYERS.get(layer, LAYERS["THIN"])
        w = max(lw * self.zoom, 1.0) + extra
        c = color or QColor.fromRgbF(grey, grey, grey)
        pen = QPen(c, w)
        pen.setCapStyle(Qt.RoundCap if dashes is None else Qt.FlatCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if dashes:
            pen.setDashPattern([max(d * self.zoom / w, 0.5) for d in dashes])
        return pen

    def _paint_prim(self, qp: QPainter, p, color=None, extra=0.0):
        if isinstance(p, Text):
            qp.save()
            f = QFont("Arial")
            f.setPixelSize(max(int(round(p.height / CAP * self.zoom)), 1))
            qp.setFont(f)
            qp.setPen(color or QColor(0, 0, 0))
            fm = QFontMetricsF(f)
            for pt, line in p.layout():
                w = fm.horizontalAdvance(line)
                dx = {"left": 0.0, "center": -w / 2, "right": -w}[p.ha]
                qp.save()
                qp.translate(self.to_px(pt))
                qp.rotate(-p.angle)
                qp.drawText(QPointF(dx, 0), line)
                qp.restore()
            qp.restore()
            return
        if isinstance(p, Solid):
            qp.setPen(Qt.NoPen)
            qp.setBrush(QBrush(color or QColor(0, 0, 0)))
            qp.drawPolygon(QPolygonF([self.to_px(q) for q in p.pts]))
            qp.setBrush(Qt.NoBrush)
            return
        qp.setPen(self._pen(p.layer, color, extra))
        if isinstance(p, Line):
            qp.drawLine(self.to_px(p.a), self.to_px(p.b))
        elif isinstance(p, Arc):
            c = self.to_px(p.c)
            r = p.r * self.zoom
            rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            if p.full:
                qp.drawEllipse(rect)
            else:
                qp.drawArc(rect, int(round(p.a0 * 16)), int(round((p.a1 - p.a0) * 16)))
        elif isinstance(p, Poly):
            path = QPainterPath(self.to_px(p.pts[0]))
            for q in p.pts[1:]:
                path.lineTo(self.to_px(q))
            if p.closed:
                path.closeSubpath()
            qp.drawPath(path)

    def paintEvent(self, _e):
        win = self.win
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(128, 136, 148))
        W, H = win.dr.size
        a, b = self.to_px((0, H)), self.to_px((W, 0))
        sheet = QRectF(a, b)
        qp.fillRect(sheet.translated(4, 4), QColor(90, 96, 106))
        qp.fillRect(sheet, QColor(255, 255, 255))
        sel = win.selected_owners()
        hov = win.hover_owner
        try:
            prims = win.prims()
        except Exception as e:  # noqa: BLE001 - e.g. missing model: still show the sheet
            qp.setPen(QColor(180, 30, 30))
            qp.drawText(sheet.adjusted(20, 20, -20, -20), Qt.AlignCenter, f"Cannot show the model:\n{e}")
            prims = win.dr.sheet_primitives()
        for p in prims:
            color = SEL if p.owner in sel else (HOVER if p.owner == hov and hov else None)
            self._paint_prim(qp, p, color)
        # views: selection frame
        for v in win.dr.views:
            if v.id in sel or v.id == hov:
                try:
                    x0, y0, x1, y1 = win.dr.view_box(v)
                except Exception:  # noqa: BLE001
                    continue
                pen = QPen(SEL if v.id in sel else HOVER, 1, Qt.DashLine)
                qp.setPen(pen)
                qp.drawRect(QRectF(self.to_px((x0 - 3, y1 + 3)), self.to_px((x1 + 3, y0 - 3))))
        # smart dimension feedback
        for kind, v, data in win.dim_highlights():
            self._paint_entity(qp, kind, v, data, HOVER)
        for p in win.preview_prims():
            self._paint_prim(qp, p, PREVIEW)
        qp.end()

    def _paint_entity(self, qp, kind, v, data, color):
        dr = self.win.dr
        if kind == "point":
            c = self.to_px(dr.to_sheet(v, data))
            qp.setPen(QPen(color, 2))
            qp.setBrush(QBrush(color))
            qp.drawEllipse(c, 4, 4)
            qp.setBrush(Qt.NoBrush)
        elif kind == "line":
            qp.setPen(QPen(color, 3))
            qp.drawLine(self.to_px(dr.to_sheet(v, data[0])), self.to_px(dr.to_sheet(v, data[1])))
        elif kind == "circle":
            c = self.to_px(dr.to_sheet(v, data[0]))
            r = data[1] * dr.view_scale(v) * self.zoom
            qp.setPen(QPen(color, 3))
            qp.drawEllipse(c, r, r)

    # ---- mouse ----------------------------------------------------------------------------
    def wheelEvent(self, e):
        f = 1.15 ** (e.angleDelta().y() / 120)
        pos = e.position()
        s = self.to_sheet(pos)
        self.zoom = float(np.clip(self.zoom * f, 0.05, 80))
        H = self.win.dr.size[1]
        self.origin = QPointF(pos.x() - s[0] * self.zoom, pos.y() - (H - s[1]) * self.zoom)
        self.update()

    def mousePressEvent(self, e):
        self.setFocus()
        self._press_px = e.position()
        if e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan = (e.position(), QPointF(self.origin), e.button())
            return
        if e.button() == Qt.LeftButton:
            self.win.canvas_press(self.to_sheet(e.position()), e.modifiers())

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            start, orig, _ = self._pan
            self.origin = orig + (e.position() - start)
            self.update()
            return
        self.win.canvas_move(self.to_sheet(e.position()), bool(e.buttons() & Qt.LeftButton))

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            start, _, button = self._pan
            self._pan = None
            moved = (e.position() - start).manhattanLength() > 4
            if button == Qt.RightButton and not moved:
                self.win.canvas_menu(self.to_sheet(e.position()), e.globalPosition().toPoint())
            return
        if e.button() == Qt.LeftButton:
            self.win.canvas_release(self.to_sheet(e.position()))

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.win.canvas_double_click(self.to_sheet(e.position()))

    def tol(self, px=6.0) -> float:
        return px / self.zoom


# =====================================================================================
# dialogs
# =====================================================================================
class TitleBlockDialog(QDialog):
    def __init__(self, parent, title: dict):
        super().__init__(parent)
        self.setWindowTitle("Title Block")
        form = QFormLayout(self)
        self.edits = {}
        for key, label in TITLE_FIELDS:
            ed = QLineEdit(title.get(key, ""))
            form.addRow(label, ed)
            self.edits[key] = ed
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self):
        return {k: e.text() for k, e in self.edits.items()}


class NoteDialog(QDialog):
    def __init__(self, parent, text=""):
        super().__init__(parent)
        self.setWindowTitle("Note")
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        self.edit.setMinimumSize(380, 140)
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def text(self):
        return self.edit.toPlainText().rstrip()


# =====================================================================================
# window
# =====================================================================================
class DrawingWindow(QMainWindow):
    def __init__(self, main_window=None, drawing: Drawing | None = None):
        super().__init__()
        self.main = main_window
        self.dr = drawing or Drawing()
        self.dirty = False
        self.mode = None                     # None | "dim" | "place" | "projected" | "note"
        self.selected: list = []             # [("view"|"dim"|"note", object)]
        self.hover_owner = ""
        self.hover_entity = None
        self.picks: list = []                # smart dimension picks
        self._mouse = np.zeros(2)
        self._drag = None
        self._placing = None
        self._proj = None
        self._prims = None
        self._undo: list = []
        self._redo: list = []
        self._own_save = 0.0
        self.setWindowIcon(icon("drawing"))
        self.resize(1500, 950)
        self._build_actions()
        self._build_ui()
        for menu in self.findChildren(QMenu):
            menu.setToolTipsVisible(True)
        self._build_command_line()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _p: QTimer.singleShot(500, self._model_changed))
        self._watch()
        self._title()
        self.sync_panel()

    # ---- construction -------------------------------------------------------------------
    def _act(self, text, icon_name, slot, shortcut=None, tip=None, checkable=False):
        a = QAction(icon(icon_name), text, self) if icon_name else QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setToolTip(tooltip(text, tip, icon_name))
        a.setStatusTip(description(text, tip, icon_name))
        a.setCheckable(checkable)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("New\nDrawing", "new", self.file_new, None, "New drawing of a part or assembly")
        self.a_open = A("Open...", "open", self.file_open, QKeySequence.Open)
        self.a_save = A("Save", "save", self.file_save, QKeySequence.Save)
        self.a_save_as = A("Save As...", "", self.file_save_as, QKeySequence.SaveAs)
        self.a_pdf = A("Save\nPDF", "pdf", lambda: self.file_export("pdf"), "Ctrl+P",
                       "Save the sheet as a vector PDF")
        self.a_dxf = A("Save\nDXF", "dxf", lambda: self.file_export("dxf"), None,
                       "Save as AutoCAD DXF (whole sheet, or the selected view at 1:1)")
        self.a_dwg = A("Save\nDWG", "dwg", lambda: self.file_export("dwg"), None,
                       "Save as AutoCAD DWG (needs the free ODA File Converter)")
        self.a_export = A("Export...", "export", lambda: self.file_export(None), "Ctrl+E",
                          "Export the drawing (PDF, DXF, DWG, SVG, PNG)")
        self.a_std = A("Standard\n3 View", "std3", self.standard_views, None,
                       "Front, Top and Right/Left views (and an isometric), aligned")
        self.a_model_view = A("Model\nView", "iso", self.model_view_menu, None,
                              "Insert a view in any standard orientation")
        self.a_projected = A("Projected\nView", "projected", self.start_projected, None,
                             "Project a view from the selected view (move the mouse to a side)")
        self.a_dim = A("Smart\nDimension", "smart_dim", self.start_dim, None,
                       "Dimension edges, points and circles of the views")
        self.a_auto = A("Auto\nDimension", "auto_dim", self.auto_dimension, None,
                        "Overall size and hole diameters of the selected view")
        self.a_note = A("Note", "note", self.start_note, None, "Place a text note")
        self.a_bom = A("Bill of Materials", "title", self.insert_bom, None,
                       "Insert or refresh an assembly BOM; drag to move, double-click to edit")
        self.a_balloon = A("Balloon", "note", self.start_balloon, None,
                           "Select a view and component, then click leader tip and bubble position")
        self.a_refresh_bom = A("Refresh BOM", "undo", self.refresh_bom, None,
                               "Reload saved assembly quantities, metadata and balloon targets")
        self.a_hidden = A("Hidden\nLines", "hidden", self.toggle_hidden, None,
                          "Hidden lines visible / removed (selected views, or all)")
        self.a_tangent = A("Tangent\nEdges", "tangent", self.toggle_tangent, None,
                           "Tangent edges visible / removed (selected views, or all)")
        self.a_title = A("Title\nBlock", "title", self.edit_title_block, None, "Edit the title block")
        self.a_delete = A("Delete", "delete", self.delete_selected, QKeySequence.Delete)
        self.a_reload = A("Reload\nModel", "undo", self.reload_model, "Ctrl+R",
                          "Re-read the model; views and dimensions update")
        self.a_undo = A("Undo", "undo", self.undo, QKeySequence.Undo)
        self.a_redo = A("Redo", "redo", self.redo, "Ctrl+Y")
        self.a_fit = A("Zoom to\nSheet", "fit", lambda: self.canvas.fit(), "F")

    def _build_ui(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as, None, self.a_pdf,
                  self.a_dxf, self.a_dwg, self.a_export, None, self.a_reload):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_delete):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Insert")
        for a in (self.a_std, self.a_model_view, self.a_projected, None, self.a_dim, self.a_auto,
                  self.a_note, self.a_bom, self.a_balloon, self.a_refresh_bom):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&View")
        for a in (self.a_hidden, self.a_tangent, None, self.a_fit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Sheet")
        m.addAction(self.a_title)

        tb = QToolBar()
        tb.setIconSize(QSize(28, 28))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        for a in (self.a_new, self.a_open, self.a_save, None, self.a_std, self.a_model_view,
                  self.a_projected, None, self.a_dim, self.a_auto, self.a_note, None,
                  self.a_hidden, self.a_tangent, self.a_title, self.a_delete, None, self.a_pdf,
                  self.a_dxf, self.a_dwg, None, self.a_reload, self.a_undo, self.a_redo, self.a_fit):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.addToolBar(tb)
        annotations = QToolBar("Assembly annotations")
        annotations.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for action in (self.a_bom, self.a_balloon, self.a_refresh_bom):
            annotations.addAction(action)
        self.addToolBarBreak()
        self.addToolBar(annotations)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_panel())
        self.canvas = SheetCanvas(self)
        split.addWidget(self.canvas)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 1210])
        self.setCentralWidget(split)
        self.status_label = QLabel(MOUSE_HINT)
        self.statusBar().addWidget(self.status_label, 1)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.escape)

    def _build_panel(self):
        w = QWidget()
        w.setMinimumWidth(260)
        lay = QVBoxLayout(w)
        g = QGroupBox("Sheet")
        f = QFormLayout(g)
        self.cb_size = QComboBox()
        self.cb_size.addItems(list(SHEET_SIZES))
        self.cb_orient = QComboBox()
        self.cb_orient.addItems(["Landscape", "Portrait"])
        self.cb_scale = QComboBox()
        self.cb_scale.addItems([scale_text(s) for s in STANDARD_SCALES])
        self.cb_proj = QComboBox()
        self.cb_proj.addItems(["First angle (ISO)", "Third angle (ANSI)"])
        f.addRow("Size", self.cb_size)
        f.addRow("Orientation", self.cb_orient)
        f.addRow("Scale", self.cb_scale)
        f.addRow("Projection", self.cb_proj)
        self.model_label = QLabel()
        self.model_label.setWordWrap(True)
        f.addRow("Model", self.model_label)
        lay.addWidget(g)
        for cb in (self.cb_size, self.cb_orient, self.cb_scale, self.cb_proj):
            cb.currentIndexChanged.connect(self._sheet_changed)

        g = QGroupBox("Selected view")
        f = QFormLayout(g)
        self.view_label = QLabel("-")
        self.cb_vscale = QComboBox()
        self.cb_vscale.addItems(["Use sheet scale"] + [scale_text(s) for s in STANDARD_SCALES])
        self.ck_hidden = QCheckBox("Hidden lines visible")
        self.ck_tangent = QCheckBox("Tangent edges visible")
        self.ck_center = QCheckBox("Centre marks")
        f.addRow("View", self.view_label)
        f.addRow("Scale", self.cb_vscale)
        f.addRow(self.ck_hidden)
        f.addRow(self.ck_tangent)
        f.addRow(self.ck_center)
        self.view_box = g
        lay.addWidget(g)
        self.cb_vscale.currentIndexChanged.connect(self._view_props_changed)
        for ck in (self.ck_hidden, self.ck_tangent, self.ck_center):
            ck.toggled.connect(self._view_props_changed)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color:#34495e;")
        lay.addWidget(self.help)
        lay.addStretch(1)
        return w

    def _build_command_line(self):
        self.commands = CommandRegistry()
        R = self.commands.add
        R("SMARTDIM", "Smart Dimension", self.start_dim, ("D", "DIM", "DI"), "smart_dim")
        R("STANDARD3", "Standard 3 View", self.standard_views, ("3V", "S3", "STD"), "std3")
        R("MODELVIEW", "Model View", self.model_view_menu, ("MV",), "iso")
        R("PROJECTED", "Projected View", self.start_projected, ("PV", "PROJ"), "projected")
        R("AUTODIM", "Auto Dimension", self.auto_dimension, ("AD",), "auto_dim")
        R("NOTE", "Note", self.start_note, ("T", "TEXT", "N"), "note")
        R("BOM", "Bill of Materials", self.insert_bom, (), "title")
        R("BALLOON", "Balloon", self.start_balloon, ("BAL",), "note")
        R("REFRESHBOM", "Refresh BOM", self.refresh_bom, (), "undo")
        R("ERASE", "Delete selection", self.delete_selected, ("E", "DEL"), "delete")
        R("HIDDEN", "Hidden lines visible / removed", self.toggle_hidden, ("HL",), "hidden")
        R("TITLEBLOCK", "Title block", self.edit_title_block, ("TB",), "title")
        R("PDF", "Save PDF", lambda: self.file_export("pdf"), ("EXPORTPDF",), "pdf")
        R("DXF", "Save DXF", lambda: self.file_export("dxf"), ("EXPORTDXF",), "dxf")
        R("DWG", "Save DWG", lambda: self.file_export("dwg"), ("EXPORTDWG",), "dwg")
        R("SAVE", "Save", self.file_save, ("QSAVE",), "save")
        R("RELOAD", "Reload model", self.reload_model, ("RE",), "undo")
        R("UNDO", "Undo", self.undo, ("U",), "undo")
        R("ZOOMFIT", "Zoom to sheet", lambda: self.canvas.fit(), ("Z", "ZE"), "fit")
        self.cmdline = CommandLine(self.commands, self)
        self._type_filter = TypeToCommandFilter(self, self.cmdline)
        QApplication.instance().installEventFilter(self._type_filter)
        for a, name in ((self.a_dim, "SMARTDIM"), (self.a_projected, "PROJECTED"),
                        (self.a_note, "NOTE"), (self.a_std, "STANDARD3"),
                        (self.a_bom, "BOM"), (self.a_balloon, "BALLOON")):
            cmd = next(c for c in self.commands.commands if c.name == name)
            a.triggered.connect(lambda _=False, c=cmd: self.commands.remember(c))

    def command_enter_pressed(self) -> bool:
        if self.mode is not None:
            self.escape()
            return True
        return False

    # ---- state ----------------------------------------------------------------------------
    def status(self, text):
        self.status_label.setText(text)

    def _title(self):
        name = self.dr.path.name if self.dr.path else "Drawing1"
        self.setWindowTitle(f"{name}{' *' if self.dirty else ''} - {APP_NAME} Drawing")

    def prims(self):
        if self._prims is None:
            self._prims = self.dr.primitives()
        return self._prims

    def changed(self, dirty=True):
        self._prims = None
        if dirty:
            self.dirty = True
        self._title()
        self.sync_panel()
        self.canvas.update()

    def snapshot(self):
        self._undo.append(json.dumps(self.dr.to_dict()))
        del self._undo[:-50]
        self._redo.clear()

    def _restore(self, text):
        old = self.dr
        d = Drawing.from_dict(json.loads(text), old.path)
        d.model = json.loads(text).get("model")
        d.shape, d.shape_override = old.shape, old.shape_override
        d._empty_assembly = old._empty_assembly
        d.model_version, d._geo, d._counter = old.model_version, old._geo, old._counter
        if old.path is not None and d.model and not Path(d.model).is_absolute():
            d.model = str((old.path.parent / d.model).resolve())
        self.dr = d
        self.selected = []
        self.changed()

    def undo(self):
        if self._undo:
            self._redo.append(json.dumps(self.dr.to_dict()))
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(json.dumps(self.dr.to_dict()))
            self._restore(self._redo.pop())

    def selected_owners(self) -> set:
        return {getattr(o, "id", "") for _, o in self.selected}

    def selected_views(self):
        return [o for k, o in self.selected if k == "view"]

    def sync_panel(self):
        dr = self.dr
        for cb, val in ((self.cb_size, list(SHEET_SIZES).index(dr.sheet)),
                        (self.cb_orient, 1 if dr.portrait else 0),
                        (self.cb_proj, 1 if dr.projection == "third" else 0),
                        (self.cb_scale, _scale_index(dr.scale))):
            cb.blockSignals(True)
            if val is not None:
                cb.setCurrentIndex(val)
            elif cb is self.cb_scale:
                cb.setEditText(scale_text(dr.scale))
            cb.blockSignals(False)
        self.model_label.setText(Path(dr.model).name if dr.model else "(none)")
        views = self.selected_views()
        self.view_box.setEnabled(len(views) == 1)
        widgets = (self.cb_vscale, self.ck_hidden, self.ck_tangent, self.ck_center)
        for wdg in widgets:
            wdg.blockSignals(True)
        if len(views) == 1:
            v = views[0]
            self.view_label.setText(v.name)
            k = _scale_index(v.scale) if v.scale else None
            self.cb_vscale.setCurrentIndex(0 if v.scale is None or k is None else k + 1)
            self.ck_hidden.setChecked(v.hidden)
            self.ck_tangent.setChecked(v.tangent)
            self.ck_center.setChecked(v.center_marks)
        else:
            self.view_label.setText("-")
        for wdg in widgets:
            wdg.blockSignals(False)
        self.help.setText(self._help_text())

    def _help_text(self):
        if self.mode == "balloon":
            return ("<b>Balloon</b><br>Click the leader tip on the chosen component, then "
                    "the bubble centre. Esc cancels. Tips follow component-local coordinates.")
        if any(k == "bom" for k, _ in self.selected):
            return ("<b>Bill of Materials</b><br>Drag to move. Double-click to edit drawing "
                    "text. Refresh BOM reads saved part and assembly changes.")
        if any(k == "balloon" for k, _ in self.selected):
            return ("<b>Balloon</b><br>Drag to move the bubble. A ? means an unresolved "
                    "component or view; delete and place a new balloon to retarget it.")
        if self.mode == "dim":
            return ("<b>Smart Dimension</b><br>Click an edge (length), a circle (diameter), an "
                    "arc (radius), or two points / edges (distance), then click where the "
                    "dimension goes. Move the mouse to choose horizontal, vertical or aligned. "
                    "Shift-click an arc to measure to its max/min point. "
                    "Esc to finish.")
        if self.mode == "projected":
            return ("<b>Projected View</b><br>Move the mouse to a side of the view and click to "
                    "place the projection.")
        if self.mode == "place":
            return "<b>Model View</b><br>Click on the sheet to place the view."
        if self.mode == "note":
            return "<b>Note</b><br>Click where the note goes."
        if not self.dr.views:
            return ("Start with <b>Standard 3 View</b> or <b>Model View</b>. Type a command "
                    "(e.g. D for Smart Dimension) at any time.")
        return ""

    # ---- sheet / view properties --------------------------------------------------------
    def _sheet_changed(self):
        self.snapshot()
        dr = self.dr
        old_size = dr.size
        dr.sheet = self.cb_size.currentText()
        dr.portrait = self.cb_orient.currentIndex() == 1
        new_scale = STANDARD_SCALES[self.cb_scale.currentIndex()]
        dr.projection = "third" if self.cb_proj.currentIndex() == 1 else "first"
        if abs(new_scale - dr.scale) > 1e-12:
            self._rescale(new_scale)
        if dr.size != old_size:                   # keep views in the same relative places
            fx, fy = dr.size[0] / old_size[0], dr.size[1] / old_size[1]
            for v in dr.views:
                if v.parent is None:
                    dr.move_view(v, v.x * fx, v.y * fy)
            self.canvas.fit()
        self.changed()

    def _rescale(self, new_scale):
        """Change the sheet scale; views spread out about the sheet centre accordingly."""
        dr = self.dr
        ratio = new_scale / dr.scale
        x0, y0, x1, y1 = dr.usable_area()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        dr.scale = new_scale
        for v in dr.views:
            if v.parent is None and v.scale is None:
                dr.move_view(v, cx + (v.x - cx) * ratio, cy + (v.y - cy) * ratio)
        for v in dr.views:
            if v.parent is not None:
                par = dr.view(v.parent)
                dr.move_view(v, par.x + (v.x - par.x) * ratio, par.y + (v.y - par.y) * ratio)

    def _view_props_changed(self):
        views = self.selected_views()
        if len(views) != 1:
            return
        self.snapshot()
        v = views[0]
        i = self.cb_vscale.currentIndex()
        v.scale = None if i == 0 else STANDARD_SCALES[i - 1]
        v.hidden = self.ck_hidden.isChecked()
        v.tangent = self.ck_tangent.isChecked()
        v.center_marks = self.ck_center.isChecked()
        for c in self.dr.views:                    # projected children share the scale
            if c.parent == v.id:
                c.scale = v.scale
        self.changed()

    def toggle_hidden(self):
        views = self.selected_views() or self.dr.views
        if not views:
            return
        self.snapshot()
        on = not all(v.hidden for v in views)
        for v in views:
            v.hidden = on
        self.status("Hidden lines " + ("visible" if on else "removed"))
        self.changed()

    def toggle_tangent(self):
        views = self.selected_views() or self.dr.views
        if not views:
            return
        self.snapshot()
        on = not all(v.tangent for v in views)
        for v in views:
            v.tangent = on
        self.changed()

    def edit_title_block(self):
        dlg = TitleBlockDialog(self, self.dr.title)
        if dlg.exec():
            self.snapshot()
            self.dr.title.update(dlg.values())
            self.changed()

    # ---- model --------------------------------------------------------------------------
    def set_model(self, path, standard=True):
        """Use a part / assembly / STEP file as the drawing's model."""
        self.dr.model = str(Path(path).resolve())
        if not self.dr.title.get("title"):
            self.dr.title["title"] = Path(path).stem
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.dr.load_model()
            if standard and not self.dr.views:
                self.dr.standard_views()
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Drawing", str(e))
            return False
        QApplication.restoreOverrideCursor()
        self._watch()
        self.changed()
        self.canvas.fit()
        return True

    def _ensure_model(self) -> bool:
        if self.dr.model or self.dr.shape_override is not None:
            return True
        path, _ = QFileDialog.getOpenFileName(self, "Model for the Drawing", self._folder(), MODEL_FILTER)
        return bool(path) and self.set_model(path, standard=False)

    def _folder(self):
        if self.dr.path:
            return str(self.dr.path.parent)
        if self.dr.model:
            return str(Path(self.dr.model).parent)
        return str(Path.home())

    def _watch(self):
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)
        p = self.dr.model_path()
        if p is not None and p.exists():
            self._watcher.addPath(str(p))

    def _model_changed(self):
        self._watch()                      # editors often replace the file
        self.reload_model(quiet=True)

    def reload_model(self, quiet=False):
        if not self.dr.model:
            return
        before = json.dumps(self.dr.to_dict())
        try:
            self.dr.reload()
        except Exception as e:  # noqa: BLE001
            if not quiet:
                QMessageBox.warning(self, "Reload", str(e))
            self.status(f"Could not reload the model: {e}")
            return
        changed = before != json.dumps(self.dr.to_dict())
        if changed:
            self._undo.append(before)
            del self._undo[:-50]
            self._redo.clear()
        unresolved = sum(self.dr.balloon_target(b)[0] == "?" for b in self.dr.balloons)
        self.status(f"Reloaded {Path(self.dr.model).name}: views, dimensions and BOM updated. "
                    f"{unresolved} unresolved balloons.")
        self.changed(dirty=changed)

    # ---- views ----------------------------------------------------------------------------
    def standard_views(self):
        if not self._ensure_model():
            return
        self.snapshot()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self.dr.views:
                self.dr.views, self.dr.dims = [], []
            views = self.dr.standard_views()
        finally:
            QApplication.restoreOverrideCursor()
        self.status(f"Standard 3 View at {scale_text(self.dr.scale)} "
                    f"({'first' if self.dr.projection == 'first' else 'third'} angle projection)")
        self.selected = []
        self.changed()
        return views

    def model_view_menu(self):
        if not self._ensure_model():
            return
        menu = QMenu(self)
        for key in ORIENTATIONS:
            menu.addAction(icon({"isometric": "iso", "front": "front", "top": "top",
                                 "right": "right"}.get(key, "part")), key.capitalize(),
                           lambda k=key: self.start_place_view(k))
        self._show_menu(menu, QCursor.pos())

    def _show_menu(self, menu, pos):
        menu.exec(pos)

    def start_place_view(self, orientation):
        self.escape()
        self.snapshot()
        self._placing = self.dr.add_view(orientation, at=tuple(self._mouse))
        self.mode = "place"
        self.status(f"Click to place the {orientation} view")
        self.changed()

    def start_projected(self):
        views = self.selected_views()
        if len(views) != 1:
            self.status("Select one view to project from, then Projected View")
            if not self.dr.views:
                return
            views = [self.dr.views[0]]
        self.escape()
        self.snapshot()
        self._proj = {"parent": views[0], "side": None, "view": None}
        self.mode = "projected"
        self.status("Move the mouse to a side of the view, click to place")
        self.changed(dirty=False)

    def _update_projected(self, pt):
        pj = self._proj
        par = pj["parent"]
        dx, dy = pt[0] - par.x, pt[1] - par.y
        side = ("right" if dx > 0 else "left") if abs(dx) > abs(dy) else ("up" if dy > 0 else "down")
        if side != pj["side"]:
            if pj["view"] is not None:
                self.dr.remove_view(pj["view"])
            pj["view"] = self.dr.projected_view(par, side)
            pj["side"] = side
        v = pj["view"]
        self.dr.move_view(v, pt[0], pt[1])
        self._prims = None
        self.canvas.update()

    def auto_dimension(self):
        views = self.selected_views() or [v for v in self.dr.views
                                           if np.max(np.abs(v.direction)) > 0.999][:1]
        if not views:
            self.status("Select a view to dimension")
            return
        self.snapshot()
        n = 0
        for v in views:
            n += len(self.dr.auto_dimension(v))
        self.status(f"Added {n} dimensions")
        self.changed()

    # ---- notes ----------------------------------------------------------------------------
    def start_note(self):
        self.escape()
        self.mode = "note"
        self.status("Click where the note goes")
        self.changed(dirty=False)

    # ---- smart dimension ------------------------------------------------------------------
    def start_dim(self):
        self.escape()
        if not self.dr.views:
            self.status("Insert a view first")
            return
        self.mode = "dim"
        self.picks = []
        self.status("Smart Dimension: click an edge, circle or point")
        self.changed(dirty=False)

    def dim_highlights(self):
        out = [(k, v, d) for k, v, d in self.picks]
        if self.mode == "dim" and self.hover_entity is not None:
            out.append(self.hover_entity)
        return out

    def preview_prims(self):
        if self.mode != "dim" or not self.picks:
            return []
        d = self._candidate(self._mouse)
        return self.dr.dim_primitives(d) if d is not None else []

    def _candidate(self, pt) -> Dimension | None:
        """The dimension the current picks + mouse position would create."""
        if not self.picks:
            return None
        v = self.picks[0][1]
        m = self.dr.to_view(v, pt)
        P = [(k, d) for k, _, d in self.picks]
        if len(P) == 1:
            k, d = P[0]
            if k == "circle":
                c, r, full = d
                kind = "diameter" if full else "radius"
                return Dimension("preview", v.id, kind, center=tuple(c), r=float(r), pos=tuple(m))
            if k == "line":
                a, b = d
                u = _unit(np.subtract(b, a))
                orient = "h" if abs(u[1]) < 1e-6 else "v" if abs(u[0]) < 1e-6 else _orient(a, b, m)
                return Dimension("preview", v.id, "linear", tuple(a), tuple(b), orient, pos=tuple(m))
            return None
        (k1, d1), (k2, d2) = P[:2]
        if k1 == "line" and k2 == "line":
            u1, u2 = _unit(np.subtract(*d1[::-1])), _unit(np.subtract(*d2[::-1]))
            if abs(u1[0] * u2[1] - u1[1] * u2[0]) > 1e-6:
                return None                               # angle: not supported yet
            k1, d1 = "point", (np.asarray(d1[0]) + d1[1]) / 2
        if k2 == "line" and k1 != "line":
            k1, d1, k2, d2 = k2, d2, k1, d1
        if k1 == "line":                                   # point (or centre) to line
            p = _as_point(k2, d2)
            a, b = np.asarray(d1[0], float), np.asarray(d1[1], float)
            u = _unit(b - a)
            foot = a + u * float((p - a) @ u)
            if abs(u[0]) < 1e-6:
                orient = "h"
            elif abs(u[1]) < 1e-6:
                orient = "v"
            else:
                orient = "a"
            return Dimension("preview", v.id, "linear", tuple(p), tuple(foot), orient, pos=tuple(m))
        a, b = _as_point(k1, d1), _as_point(k2, d2)
        return Dimension("preview", v.id, "linear", tuple(a), tuple(b), _orient(a, b, m), pos=tuple(m))

    def _dim_click(self, pt, shift=False):
        """First click picks an entity; a second click on another entity picks it too
        (distance); a click on empty space (or with two picks, anywhere) places the dim."""
        ent = self.dr.pick_entity(pt, self.canvas.tol(), quadrants=shift)
        if ent is not None and self.picks and ent[1] is not self.picks[0][1]:
            self.picks = []                                # a different view: start again
        if not self.picks:
            if ent is not None:
                self.picks = [ent]
                self.status("Click to place the dimension, or pick a second entity")
                self.canvas.update()
            return
        if len(self.picks) == 1 and ent is not None and not _same(ent, self.picks[0]):
            self.picks.append(ent)
            if self._candidate(pt) is None:
                self.picks.pop()
                self.status("Angular dimensions are not supported yet")
                return
            self.status("Click to place the dimension")
            self.canvas.update()
            return
        d = self._candidate(pt)
        if d is None:
            self.status("Pick a second point or edge")
            return
        self.snapshot()
        d.id = self.dr._new_id("D")
        self.dr.dims.append(d)
        self.picks = []
        self.status(f"Added {d.label(self.dr.decimals)} - pick the next entity, Esc to finish")
        self.changed()

    # ---- mouse --------------------------------------------------------------------------------
    def insert_bom(self):
        self.escape()
        try:
            before = json.dumps(self.dr.to_dict())
            b = self.dr.add_bom()
            self._undo.append(before)
            del self._undo[:-50]
            self._redo.clear()
            self.selected = [("bom", b)]
            self.changed()
            self.status("BOM inserted/refreshed. Drag to move; double-click to edit displayed text.")
        except Exception as e:
            QMessageBox.warning(self, "Bill of Materials", str(e))

    def refresh_bom(self):
        self.escape()
        if not self.dr.bom:
            self.insert_bom()
            return
        self.reload_model()

    def edit_bom(self):
        b = self.dr.bom
        dlg = QDialog(self)
        dlg.setWindowTitle("Bill of Materials — drawing text overrides")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Item and quantity come from the assembly. Edits affect this drawing only."))
        table = QTableWidget(len(b.rows), 4)
        table.setHorizontalHeaderLabels(["Item", "Part number", "Description", "Qty"])
        for i, row in enumerate(b.rows):
            r = b.displayed(row)
            for j, value in enumerate((r["item"], r["part_number"], r["description"], r["quantity"])):
                cell = QTableWidgetItem(str(value))
                if j in (0, 3):
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                cell.setToolTip(row["status"] or row["key"])
                table.setItem(i, j, cell)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setColumnWidth(2, 280)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        def reset_cells():
            for i, row in enumerate(b.rows):
                table.item(i, 1).setText(row["part_number"])
                table.item(i, 2).setText(row["description"])
        buttons.button(QDialogButtonBox.Reset).clicked.connect(reset_cells)
        layout.addWidget(buttons)
        dlg.resize(640, 320)
        if dlg.exec():
            self.snapshot()
            for i, row in enumerate(b.rows):
                edits = {}
                for col, field in ((1, "part_number"), (2, "description")):
                    value = table.item(i, col).text()
                    if value != row[field]:
                        edits[field] = value
                b.overrides[row["key"]] = edits
            self.changed()

    def start_balloon(self):
        views = self.selected_views()
        self.escape()
        if not self.dr.bom or len(views) != 1:
            self.status("Insert a BOM, select one drawing view, then choose Balloon.")
            return
        items = list(self.dr.bom.instances.items())
        if not items:
            self.status("The assembly has no components.")
            return
        labels = [f'{cid}: {inst["name"]} — item {self.dr.bom.numbers[inst["key"]]}'
                  for cid, inst in items]
        label, ok = QInputDialog.getItem(self, "Balloon component", "Component in selected view:", labels, 0, False)
        if not ok:
            return
        self._balloon_component = items[labels.index(label)][0]
        self._balloon_view = views[0]
        self._balloon_anchor = None
        self.mode = "balloon"
        self.sync_panel()
        self.status("Click the leader tip on the selected component, then the balloon centre. Esc cancels.")

    def canvas_press(self, pt, modifiers):
        self._mouse = pt
        if self.mode == "balloon":
            if (not self.dr.bom or self._balloon_component not in self.dr.bom.instances
                    or self.dr.view(self._balloon_view.id) is None):
                self.escape()
                self.status("The balloon target changed. Select a view and place the balloon again.")
                return
            if self._balloon_anchor is None:
                from ..core.assembly import Pose
                from ..core.geometry import view_frame
                v = self._balloon_view
                uv = self.dr.to_view(v, pt)
                n, x, y = view_frame(v.direction, v.xdir)
                inst = self.dr.bom.instances[self._balloon_component]
                pose = Pose.from_dict(inst["pose"])
                # Projected pick on the plane through this component's origin.
                world = x * uv[0] + y * uv[1] + n * (pose.t @ n)
                self._balloon_anchor = pose.inverse_point(world)
                self.status("Now click the balloon centre.")
            else:
                self.snapshot()
                self.dr.add_balloon(self._balloon_view, self._balloon_component,
                                    self._balloon_anchor, pt)
                self.escape()
                self.changed()
            return
        if self.mode == "dim":
            self._dim_click(pt, bool(modifiers & Qt.ShiftModifier))
            return
        if self.mode == "place":
            self._placing = None
            self.mode = None
            self.status("View placed")
            self.changed()
            return
        if self.mode == "projected":
            self._update_projected(pt)
            v = self._proj["view"]
            self._proj = None
            self.mode = None
            self.selected = [("view", v)] if v else []
            self.status(f"{v.name} view placed" if v else "")
            self.changed()
            return
        if self.mode == "note":
            self.mode = None
            dlg = NoteDialog(self)
            if dlg.exec() and dlg.text():
                self.snapshot()
                self.dr.add_note(dlg.text(), pt)
            self.changed()
            return
        hit = self.dr.hit(pt, self.canvas.tol())
        if hit is None:
            self.selected = []
            self.changed(dirty=False)
            return
        if modifiers & Qt.ControlModifier:
            if any(o is hit[1] for _, o in self.selected):
                self.selected = [s for s in self.selected if s[1] is not hit[1]]
            else:
                self.selected.append(hit)
        else:
            self.selected = [hit]
        kind, obj = hit
        if kind == "view":
            self._drag = {"kind": kind, "obj": obj, "start": pt, "orig": (obj.x, obj.y), "moved": False}
        elif kind == "dim":
            self._drag = {"kind": kind, "obj": obj, "start": pt, "orig": obj.pos, "moved": False}
        else:
            self._drag = {"kind": kind, "obj": obj, "start": pt, "orig": (obj.x, obj.y), "moved": False}
        self.changed(dirty=False)

    def canvas_move(self, pt, left_down):
        self._mouse = pt
        if self.mode == "place" and self._placing is not None:
            self.dr.move_view(self._placing, *pt)
            self._prims = None
            self.canvas.update()
            return
        if self.mode == "projected":
            self._update_projected(pt)
            return
        if self.mode == "dim":
            shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
            ent = self.dr.pick_entity(pt, self.canvas.tol(), quadrants=shift)
            self.hover_entity = ent
            self.canvas.update()
            return
        if self._drag is not None and left_down:
            d = self._drag
            delta = pt - d["start"]
            if not d["moved"] and np.linalg.norm(delta) * self.canvas.zoom < 3:
                return
            if not d["moved"]:
                self.snapshot()
                d["moved"] = True
            obj = d["obj"]
            if d["kind"] == "view":
                self.dr.move_view(obj, d["orig"][0] + delta[0], d["orig"][1] + delta[1])
            elif d["kind"] == "dim":
                v = self.dr.view(obj.view)
                obj.pos = tuple(np.asarray(d["orig"]) + delta / self.dr.view_scale(v))
            else:
                obj.x, obj.y = d["orig"][0] + delta[0], d["orig"][1] + delta[1]
            self._prims = None
            self.dirty = True
            self.canvas.update()
            return
        hit = self.dr.hit(pt, self.canvas.tol()) if self.dr.views or self.dr.notes else None
        owner = getattr(hit[1], "id", "") if hit else ""
        if owner != self.hover_owner:
            self.hover_owner = owner
            self.canvas.update()

    def canvas_release(self, pt):
        if self._drag is not None and self._drag["moved"]:
            self._title()
        self._drag = None

    def canvas_double_click(self, pt):
        if self.mode is not None:
            return
        hit = self.dr.hit(pt, self.canvas.tol())
        if hit is None:
            return
        kind, obj = hit
        if kind == "bom":
            self.edit_bom()
            return
        if kind == "dim":
            text, ok = QInputDialog.getText(
                self, "Dimension Text",
                f"Measured: {obj.label(self.dr.decimals)}\nText (<> = the value, empty = default):",
                text=obj.text or "")
            if ok:
                self.snapshot()
                obj.text = text or None
                self.changed()
        elif kind == "note":
            dlg = NoteDialog(self, obj.text)
            if dlg.exec() and dlg.text():
                self.snapshot()
                obj.text = dlg.text()
                self.changed()
        elif kind == "view":
            self.selected = [hit]
            self.changed(dirty=False)

    def canvas_menu(self, pt, gpos):
        if self.mode is not None:
            self.escape()
            return
        hit = self.dr.hit(pt, self.canvas.tol())
        menu = QMenu(self)
        if hit is not None:
            if not any(o is hit[1] for _, o in self.selected):
                self.selected = [hit]
                self.changed(dirty=False)
            kind, obj = hit
            if kind == "view":
                menu.addAction(icon("projected"), "Projected View", self.start_projected)
                menu.addAction(icon("auto_dim"), "Auto Dimension", self.auto_dimension)
                menu.addAction(icon("hidden"), "Hidden Lines Visible / Removed", self.toggle_hidden)
                menu.addAction(icon("dxf"), "Save this view as DXF (1:1)...",
                               lambda: self.file_export("dxf", view=obj))
            if kind == "dim":
                menu.addAction("Edit Text...", lambda: self.canvas_double_click(pt))
                for label, o in (("Horizontal", "h"), ("Vertical", "v"), ("Aligned", "a")):
                    if obj.kind == "linear":
                        menu.addAction(label, lambda o=o, d=obj: self._set_orient(d, o))
            menu.addAction(icon("delete"), "Delete", self.delete_selected)
        else:
            menu.addAction(icon("std3"), "Standard 3 View", self.standard_views)
            menu.addAction(icon("iso"), "Model View...", self.model_view_menu)
            menu.addAction(icon("smart_dim"), "Smart Dimension", self.start_dim)
            menu.addAction(icon("note"), "Note", self.start_note)
            menu.addAction(icon("title"), "Title Block...", self.edit_title_block)
        self._show_menu(menu, gpos)

    def _set_orient(self, d, o):
        self.snapshot()
        d.orient = o
        self.changed()

    def delete_selected(self):
        if not self.selected:
            return
        self.snapshot()
        for kind, obj in self.selected:
            if kind == "view" and obj in self.dr.views:
                self.dr.remove_view(obj)
            elif kind == "dim" and obj in self.dr.dims:
                self.dr.dims.remove(obj)
            elif kind == "note" and obj in self.dr.notes:
                self.dr.notes.remove(obj)
            elif kind == "balloon" and obj in self.dr.balloons:
                self.dr.balloons.remove(obj)
            elif kind == "bom":
                self.dr.bom = None
        self.selected = []
        self.changed()

    def escape(self):
        if self.mode == "place" and self._placing is not None:
            self.dr.remove_view(self._placing)
            if self._undo:
                self._undo.pop()
        if self.mode == "projected" and self._proj:
            if self._proj["view"] is not None:
                self.dr.remove_view(self._proj["view"])
            if self._undo:
                self._undo.pop()
        self._placing = self._proj = None
        self.mode = None
        self.picks = []
        self.hover_entity = None
        self.status(MOUSE_HINT)
        self.changed(dirty=False)

    # ---- files --------------------------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Drawing", "Save changes to the drawing?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.file_save()
        return r == QMessageBox.Discard

    def file_new(self):
        if self.main is not None:
            self.main.open_drawing(None)
        else:
            DrawingWindow().show()

    def file_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Drawing", self._folder(), DRW_FILTER)
        if not path:
            return
        if self.dr.views or self.dirty:
            if self.main is not None:
                self.main.open_drawing(path)
                return
        self.load(path)

    def load(self, path):
        try:
            self.dr = Drawing.load(path)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.dr.load_model()
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Open Drawing", str(e))
        self.dirty = False
        self._undo.clear()
        self.selected = []
        self._watch()
        self.changed(dirty=False)
        self.canvas.fit()

    def file_save(self) -> bool:
        if self.dr.path is None:
            return self.file_save_as()
        self.dr.save(self.dr.path)
        self.dirty = False
        self._title()
        self.status(f"Saved {self.dr.path}")
        return True

    def file_save_as(self) -> bool:
        stem = Path(self.dr.model).stem if self.dr.model else "Drawing1"
        path, _ = QFileDialog.getSaveFileName(self, "Save Drawing",
                                              str(Path(self._folder()) / f"{stem}{FILE_EXTENSION}"),
                                              DRW_FILTER)
        if not path:
            return False
        self.dr.save(path)
        self.dirty = False
        self._title()
        self.status(f"Saved {self.dr.path}")
        return True

    def file_export(self, fmt=None, view=None, path=None):
        """Export the sheet (or one view at 1:1 for DXF/DWG)."""
        if not self.dr.views:
            QMessageBox.information(self, "Export", "The drawing has no views yet.")
            return None
        if fmt in ("dxf", "dwg") and view is None and len(self.selected_views()) == 1 and path is None:
            v = self.selected_views()[0]
            box = QMessageBox(self)
            box.setWindowTitle(f"Save {fmt.upper()}")
            box.setText(f"Save the whole sheet, or only the selected view ({v.name}) at 1:1 in "
                        "model millimetres (for laser / CNC cutting)?")
            sheet_btn = box.addButton("Whole sheet", QMessageBox.AcceptRole)
            view_btn = box.addButton(f"{v.name} view at 1:1", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is view_btn:
                view = v
            elif box.clickedButton() is not sheet_btn:
                return None
        if path is None:
            stem = self.dr.path.stem if self.dr.path else (Path(self.dr.model).stem if self.dr.model else "Drawing1")
            if view is not None:
                stem += f"_{view.name.lower()}"
            filters = [f for k, f in EXPORT_FILTERS if fmt is None or k == fmt]
            path, chosen = QFileDialog.getSaveFileName(
                self, "Export Drawing", str(Path(self._folder()) / f"{stem}.{fmt or 'pdf'}"),
                ";;".join(filters))
            if not path:
                return None
            key = next((k for k, f in EXPORT_FILTERS if f == chosen), fmt or "pdf")
            if Path(path).suffix.lower() != "." + key:
                path += "." + key
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.dr.export(path, view=view.id if view is not None else None)
        except DrawingError as e:
            QApplication.restoreOverrideCursor()
            if Path(path).suffix.lower() == ".dwg":
                r = QMessageBox.question(self, "Save DWG", f"{e}\n\nSave a DXF instead?",
                                         QMessageBox.Yes | QMessageBox.No)
                if r == QMessageBox.Yes:
                    return self.file_export("dxf", view, str(Path(path).with_suffix(".dxf")))
            else:
                QMessageBox.critical(self, "Export", str(e))
            return None
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export", f"Could not export {path}:\n\n{e}")
            return None
        QApplication.restoreOverrideCursor()
        self.status(f"Saved {path}" + (f" ({view.name} view, 1:1)" if view is not None else ""))
        return path

    def closeEvent(self, event):
        if not self._confirm_discard():
            event.ignore()
            return
        QApplication.instance().removeEventFilter(self._type_filter)
        if self.main is not None and self in getattr(self.main, "_drawing_windows", []):
            self.main._drawing_windows.remove(self)
        event.accept()


# =====================================================================================
# helpers
# =====================================================================================
def _scale_index(s):
    for i, c in enumerate(STANDARD_SCALES):
        if s and abs(c - s) < 1e-9:
            return i
    return None


def _as_point(kind, data):
    return np.asarray(data[0] if kind == "circle" else data, float)


def _same(a, b) -> bool:
    if a[0] != b[0] or a[1] is not b[1]:
        return False
    da, db = a[2], b[2]
    if a[0] == "point":
        return bool(np.allclose(da, db))
    return all(np.allclose(np.asarray(x, float), np.asarray(y, float)) for x, y in zip(da[:2], db[:2]))


def _orient(a, b, m) -> str:
    """SolidWorks-style: dragging above/below the two points gives a horizontal dimension,
    left/right gives a vertical one, elsewhere aligned."""
    a, b, m = (np.asarray(x, float) for x in (a, b, m))
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    in_x = lo[0] <= m[0] <= hi[0]
    in_y = lo[1] <= m[1] <= hi[1]
    if in_x and not in_y:
        return "h"
    if in_y and not in_x:
        return "v"
    if abs(a[0] - b[0]) < 1e-9:
        return "v"
    if abs(a[1] - b[1]) < 1e-9:
        return "h"
    return "a"
