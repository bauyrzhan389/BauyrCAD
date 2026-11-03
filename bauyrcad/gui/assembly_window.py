"""Assembly window: insert parts, mate them, drag them around (SolidWorks-style)."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QFileSystemWatcher, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QRadioButton,
                               QSplitter, QStackedWidget, QToolBar, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import APP_NAME
from ..core import exchange
from ..core.assembly import (FILE_EXTENSION, MATE_LABELS, Assembly, AssemblyError, Component,
                             MateRef, Pose, face_geometry, mate_options, rodrigues,
                             rotation_vector as _rotation_vector)
from ..fea.study import FaceRef
from .command_line import CommandLine, CommandRegistry, TypeToCommandFilter
from .commands import spin
from .icons import icon
from .command_help import tooltip, description
from .viewer import SELECT_RGB, Viewer

ASM_FILTER = f"BauyrCAD assembly (*{FILE_EXTENSION})"
PART_FILTER = "Parts (*.bcad *.step *.stp *.igs *.iges *.brep);;BauyrCAD part (*.bcad);;STEP (*.step *.stp)"
PICK_RGB = np.array([255, 120, 40], np.uint8)
COMP_SEL_RGB = np.array([120, 185, 250], np.uint8)


class AssemblyWindow(QMainWindow):
    def __init__(self, main_window=None, assembly: Assembly | None = None):
        super().__init__()
        self.main = main_window
        self.asm = assembly or Assembly()
        self.dirty = False
        self.anim_duration = 0.45               # s; mate animations (0 = off)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(15)
        self._anim_timer.timeout.connect(self._anim_step)
        self._anim = None
        self._shown: dict = {}                  # component id -> Pose currently displayed
        self.selected: str | None = None        # selected component id
        self.mate_panel = False
        self.picks: list = []                   # [(component id, face index)]
        self.preview_mate = None
        self._saved_poses = None
        self._drag = None
        self._undo: list = []
        self._redo: list = []
        self._own_save = 0.0
        self.setWindowIcon(icon("assembly"))
        self.resize(1500, 920)
        self._build_actions()
        self._build_ui()
        for menu in self.findChildren(QMenu):
            menu.setToolTipsVisible(True)
        self._build_command_line()
        self.viewer.drag_handler = self
        self.viewer.clicked.connect(self._clicked)
        self.viewer.rightClicked.connect(self._right_clicked)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _p: QTimer.singleShot(500, self._parts_changed))
        self.refresh(fit=True)

    # =====================================================================================
    # construction
    # =====================================================================================
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
        self.a_new = A("New", "new", self.file_new)
        self.a_open = A("Open...", "open", self.file_open, QKeySequence.Open)
        self.a_save = A("Save", "save", self.file_save, QKeySequence.Save)
        self.a_save_as = A("Save As...", "", self.file_save_as, QKeySequence.SaveAs)
        self.a_export = A("Export\nSTEP...", "export", self.file_export, "Ctrl+E",
                          "Export the assembly: STEP keeps the assembly structure (SolidWorks "
                          "opens it as an assembly of parts); also IGES, STL, ...")
        self.a_export_sw = A("Export to\nSolidWorks", "solidworks", self.export_solidworks, None,
                             "Native SolidWorks assembly (.SLDASM + .SLDPRT parts): floating "
                             "components and real mates, so it moves in SolidWorks")
        self.a_drawing = A("Make\nDrawing", "drawing", self.make_drawing, "Ctrl+Shift+D",
                           "Drawing sheet of the assembly (save as PDF, DXF or DWG)")
        self.a_insert = A("Insert\nComponent", "import", self.insert_component, "Ctrl+I",
                          "Insert a part (.bcad / STEP / IGES) into the assembly")
        self.a_mate = A("Mate", "mate", self.start_mate, None,
                        "Position components relative to each other with mates")
        self.a_animate = A("Animate\nMates", "run", self._toggle_animation, None,
                           "Animate components into place when mates are applied", True)
        self.a_animate.setChecked(True)
        self.a_fix = A("Fix /\nFloat", "fixed", self.toggle_fixed, None,
                       "Fix (ground) or float the selected component")
        self.a_delete = A("Delete", "delete", self.delete_selected, QKeySequence.Delete)
        self.a_reload = A("Reload\nParts", "undo", self.reload_parts, "Ctrl+R",
                          "Reload all part files and re-solve the mates")
        self.a_open_part = A("Open\nPart", "part", self.open_part, None,
                             "Open the selected component's part for editing")
        self.a_undo = A("Undo", "undo", self.undo, QKeySequence.Undo)
        self.a_redo = A("Redo", "redo", self.redo, "Ctrl+Y")
        v = self.viewer_action = lambda text, ic, fn, key=None: A(text, ic, fn, key)
        self.a_iso = v("Isometric", "iso", lambda: self.viewer.view_isometric(), "Ctrl+4")
        self.a_front = v("Front", "front", lambda: self.viewer.view_front(), "Ctrl+1")
        self.a_top = v("Top", "top", lambda: self.viewer.view_top(), "Ctrl+2")
        self.a_right = v("Right", "right", lambda: self.viewer.view_right(), "Ctrl+3")
        self.a_fit = v("Zoom to Fit", "fit", lambda: self.viewer.fit())
        self.a_hide = A("Hide / Show Selected", "hide_component", lambda: self.visibility.toggle(self.selected))
        self.a_isolate = A("Isolate Selected", "isolate_component",
            lambda checked: self.visibility.isolate(self.selected) if checked else self.visibility.exit_isolate(), checkable=True)
        self.a_show_all = A("Show All Components", "show_components", lambda: self.visibility.show_all())
        self.a_zoom_selected = A("Zoom to Selected", "fit", lambda: self.visibility.zoom(self.selected))
        self.a_measure = A("Measure", "measure", self.start_measure)
        self.a_bounds = A("Bounding Box", "bounds", self.show_dimensions)
        self.a_section = A("Section View", "section", self.show_section)
        self.a_section.setCheckable(True)
        self.a_check_geometry = A("Check Geometry", "check_geometry", self.show_geometry_check)
        from .view_image import export_view, copy_view
        self.a_view_image = A("Save View Image...", "view_image", lambda: export_view(self))
        self.a_copy_view = A("Copy View Image", "copy_view_image", lambda: copy_view(self))
        self.a_mass = A("Mass Properties", "mass_properties", self.show_mass_properties)
        self.a_clearance = A("Minimum Clearance", "clearance", self.show_clearance)
        self.a_interference = A("Interference", "interference", self.show_interference)

    def _build_ui(self):
        self.a_pack = self._act("Pack and Go", "export", self.pack_and_go, None,
                               "Bundle the assembly, mates and saved part files into a portable ZIP")
        self.setStyleSheet("""
            QToolBar { background: #f5f5f5; spacing: 2px; }
            QToolButton { padding: 3px; }
            QToolButton:checked { background: #dddddd; border: 1px solid #888888; border-radius: 3px; }
            QTreeWidget { background: #ffffff; }
        """)
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as, None, self.a_insert,
                  self.a_export, self.a_export_sw, self.a_pack, self.a_drawing, self.a_reload, None,
                  self.a_view_image, self.a_copy_view):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_delete):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Assembly")
        for a in (self.a_insert, self.a_mate, self.a_fix, self.a_open_part, self.a_animate):
            m.addAction(a)
        m = mb.addMenu("&View")
        from .appearance import add_menu
        self.background_actions = add_menu(self, m)
        from .appearance import add_display_menu
        self.display_actions = add_display_menu(self, m)
        for a in (self.a_front, self.a_top, self.a_right, self.a_iso, self.a_fit, self.a_section,
                  self.a_view_image, self.a_copy_view):
            m.addAction(a)

        m.addSeparator()
        for action in (self.a_hide, self.a_isolate, self.a_show_all, self.a_zoom_selected):
            m.addAction(action)
        tb = QToolBar()
        tb.setIconSize(QSize(28, 28))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        for a in (self.a_insert, self.a_mate, self.a_fix, self.a_delete, self.a_animate,
                  None, self.a_open_part, self.a_reload, None, self.a_export,
                  self.a_export_sw, self.a_pack, self.a_drawing):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.assembly_toolbar=tb
        self.view_toolbar = QToolBar('View', self)
        self.view_toolbar.setIconSize(QSize(28, 28))
        self.view_toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        for action in (self.a_front, self.a_top, self.a_right, self.a_iso, self.a_fit, self.a_section):
            self.view_toolbar.addAction(action)

        evaluate = mb.addMenu("&Evaluate")
        evaluate.addAction(self.a_measure)
        evaluate.addAction(self.a_bounds)
        evaluate.addAction(self.a_mass)
        evaluate.addAction(self.a_check_geometry)
        evaluate.addAction(self.a_interference)
        evaluate.addAction(self.a_clearance)
        evaluate_bar = self.evaluate_toolbar = QToolBar('Evaluate', self)
        evaluate_bar.setIconSize(QSize(28, 28))
        evaluate_bar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        evaluate_bar.addAction(self.a_measure)
        evaluate_bar.addAction(self.a_bounds)
        evaluate_bar.addAction(self.a_mass)
        evaluate_bar.addAction(self.a_interference)
        evaluate_bar.addAction(self.a_clearance)
        evaluate_bar.addAction(self.a_check_geometry)

        split = QSplitter(Qt.Horizontal)
        self.left = QStackedWidget()
        self.left.setMinimumWidth(320)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        self.tree.itemDoubleClicked.connect(self._tree_double_click)
        self.left.addWidget(self.tree)
        self.left.addWidget(self._build_mate_panel())
        from .workspace_layout import manager, viewport_tools
        split.addWidget(manager(self))
        self.viewer = Viewer()
        from .component_visibility import ComponentVisibility
        self.visibility = ComponentVisibility(self)
        viewport_tools(self)
        split.addWidget(self.viewer)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 1160])
        from PySide6.QtWidgets import QTabWidget
        central=QWidget(); layout=QVBoxLayout(central); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        quick=QToolBar(); quick.setIconSize(QSize(20,20))
        for action in (self.a_new,self.a_open,self.a_save,self.a_undo,self.a_redo): quick.addAction(action)
        layout.addWidget(quick)
        self.tabs=QTabWidget(); self.tabs.setDocumentMode(True); self.tabs.setTabPosition(QTabWidget.South)
        for toolbar,label in ((self.assembly_toolbar,'Assembly'),(self.evaluate_toolbar,'Evaluate'),(self.view_toolbar,'View')):
            self.tabs.addTab(toolbar,label)
        self.tabs.setMaximumHeight(104)
        layout.addWidget(self.tabs); layout.addWidget(split,1)
        self.setCentralWidget(central)
        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.status_label.setText(self.MOUSE_HINT)
        self.status_label.setToolTip(self.MOUSE_HINT)
        self.dof_label = QLabel()
        self.statusBar().addPermanentWidget(self.dof_label)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._escape)

    def _build_mate_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        head = QLabel("Mate")
        head.setStyleSheet("font-size:15px;font-weight:600;color:#1f3b5c;")
        lay.addWidget(head)
        row = QHBoxLayout()
        self.mate_add_btn = QPushButton(icon("exit_sketch"), "Add Mate")
        self.mate_add_btn.clicked.connect(self.add_preview_mate)
        close = QPushButton("Close")
        close.clicked.connect(self.close_mate_panel)
        row.addWidget(self.mate_add_btn)
        row.addWidget(close)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Entities to mate (click two faces):"))
        self.pick_list = QListWidget()
        self.pick_list.setMaximumHeight(70)
        lay.addWidget(self.pick_list)
        clr = QPushButton("Clear selections")
        clr.clicked.connect(self._clear_picks)
        lay.addWidget(clr)
        lay.addWidget(QLabel("Standard mates:"))
        self.type_group = QButtonGroup(w)
        self.type_buttons = {}
        grid = QVBoxLayout()
        for t, label in MATE_LABELS.items():
            b = QRadioButton(label)
            b.toggled.connect(lambda on, t=t: on and self._preview())
            self.type_group.addButton(b)
            self.type_buttons[t] = b
            grid.addWidget(b)
        lay.addLayout(grid)
        form = QFormLayout()
        self.mate_dist = spin(10.0, -1e6, 1e6)
        self.mate_angle = spin(90.0, 0.0, 180.0, 3, "°", 5)
        form.addRow("Distance", self.mate_dist)
        form.addRow("Angle", self.mate_angle)
        self.flip = QCheckBox("Flip alignment (aligned / anti-aligned)")
        form.addRow(self.flip)
        lay.addLayout(form)
        for sig in (self.mate_dist.valueChanged, self.mate_angle.valueChanged, self.flip.toggled):
            sig.connect(lambda *_: self._preview())
        self.mate_msg = QLabel()
        self.mate_msg.setWordWrap(True)
        lay.addWidget(self.mate_msg)
        lay.addStretch(1)
        return w

    def _build_command_line(self):
        self.commands = CommandRegistry()
        R = self.commands.add
        R("INSERT", "Insert component", self.insert_component, ("I", "INS"), "import")
        R("MATE", "Mate", self.start_mate, ("MA", "MT"), "mate")
        R("FIX", "Fix / float component", self.toggle_fixed, ("FL", "FLOAT"), "fixed")
        R("ERASE", "Delete selection", self.delete_selected, ("E", "DEL"), "delete")
        R("OPENPART", "Open part", self.open_part, ("OP",), "part")
        R("RELOAD", "Reload parts", self.reload_parts, ("RE",), "undo")
        R("SAVE", "Save", self.file_save, ("QSAVE",), "save")
        R("EXPORT", "Export", self.file_export, ("EXP",), "export")
        R("PACKANDGO", "Pack and Go", self.pack_and_go, ("PACK",), "export")
        R("SECTION", "Section View", self.show_section, ("SEC",), "section")
        R("CHECKGEOMETRY", "Check Geometry", self.show_geometry_check, ("CHECK",), "check_geometry")
        R("HIDE", "Hide / Show Selected", lambda: self.visibility.toggle(self.selected), (), "hide_component")
        R("ISOLATE", "Isolate Selected", lambda: self.visibility.isolate(self.selected), (), "isolate_component")
        R("SHOWALL", "Show All Components", self.visibility.show_all, (), "show_components")
        R("ZOOMSELECTED", "Zoom to Selected", lambda: self.visibility.zoom(self.selected), ("ZS",), "fit")
        from .view_image import export_view, copy_view
        R("VIEWIMAGE", "Save View Image", lambda: export_view(self), (), "view_image")
        R("COPYVIEW", "Copy View Image", lambda: copy_view(self), (), "copy_view_image")
        R("MEASURE", "Measure", self.start_measure, ("MEA", "DIST", "EVALUATE"), "measure")
        R("BOUNDINGBOX", "Bounding Box", self.show_dimensions, ("BBOX",), "bounds")
        R("MASSPROP", "Mass Properties", self.show_mass_properties, ("MASS",), "mass_properties")
        R("CLEARANCE", "Minimum Clearance", self.show_clearance, ("GAP",), "clearance")
        R("INTERFERENCE", "Interference Detection", self.show_interference, ("INTERFERE",), "interference")
        R("DRAWING", "Make drawing", self.make_drawing, ("DRW", "MAKEDRAWING"), "drawing")
        R("SOLIDWORKS", "Export to SolidWorks", self.export_solidworks, ("SW", "SLDASM"), "solidworks")
        R("UNDO", "Undo", self.undo, ("U",), "undo")
        R("ZOOMFIT", "Zoom to fit", lambda: self.viewer.fit(), ("Z", "ZE"), "fit")
        R("ISOMETRIC", "Isometric view", self.viewer.view_isometric, ("ISO",), "iso")
        self.cmdline = CommandLine(self.commands, self)
        self._type_filter = TypeToCommandFilter(self, self.cmdline)
        QApplication.instance().installEventFilter(self._type_filter)
        for a, name in ((self.a_insert, "INSERT"), (self.a_mate, "MATE"), (self.a_fix, "FIX")):
            cmd = next(c for c in self.commands.commands if c.name == name)
            a.triggered.connect(lambda _=False, c=cmd: self.commands.remember(c))

    def numpad_view(self, key: str):
        self.viewer.numpad(key)

    def command_enter_pressed(self) -> bool:
        if self.mate_panel:
            if self.preview_mate is not None:
                self.add_preview_mate()
            else:
                self.close_mate_panel()
            return True
        return False

    # =====================================================================================
    # state / refresh
    # =====================================================================================
    def status(self, text):
        self.status_label.setText(text)

    def _title(self):
        name = self.asm.path.name if self.asm.path else "Assembly1"
        self.setWindowTitle(f"{name}{' *' if self.dirty else ''} - {APP_NAME} Assembly")

    def checkpoint(self):
        self._undo.append(json.dumps(self.asm.to_dict()))
        del self._undo[:-60]
        self._redo.clear()
        self.dirty = True
        self._title()

    def _restore(self, snapshot):
        d = json.loads(snapshot)
        loaded = {(c.id, c.source): c for c in self.asm.components}
        comps = []
        for cd in d["components"]:
            old = loaded.get((cd["id"], cd["source"]))
            comp = Component.from_dict(cd)
            if old is not None and old.shape is not None:
                comp.shape, comp.faces, comp.doc, comp._tess = old.shape, old.faces, old.doc, old._tess
            else:
                comp.load(self.asm.base_dir)
            comps.append(comp)
        from ..core.assembly import Mate
        self.asm.components = comps
        self.asm.mates = [Mate.from_dict(m) for m in d["mates"]]
        self.dirty = True
        self.refresh(animate=True)

    def undo(self):
        if self.mate_panel or not self._undo:
            return
        self._redo.append(json.dumps(self.asm.to_dict()))
        self._restore(self._undo.pop())

    def redo(self):
        if self.mate_panel or not self._redo:
            return
        self._undo.append(json.dumps(self.asm.to_dict()))
        self._restore(self._redo.pop())

    def refresh(self, fit=False, animate=False):
        """Rebuild the view. animate=True glides components from where they were shown."""
        if getattr(self, 'measure_win', None) is not None:
            self.measure_win.model_changed()
        if getattr(self, 'mass_win', None) is not None:
            self.mass_win.model_changed()
        if getattr(self, 'geometry_check_win', None) is not None:
            self.geometry_check_win.model_changed()
        if getattr(self, 'interference_win', None) is not None:
            self.interference_win.model_changed()
        if getattr(self, 'clearance_win', None) is not None:
            self.clearance_win.model_changed()
        items = [(c.id, c.tessellation, c.pose.matrix, c.color or (176, 186, 200))
                 for c in self.asm.components if c.shape is not None]
        if self._anim is not None:
            self._finish_animation(apply=False)
        before = dict(self._shown)
        self.viewer.set_assembly(items)
        self.visibility.apply()
        self._shown = {c.id: c.pose.copy() for c in self.asm.components}
        if animate:
            for cid, pose in before.items():
                if cid in self._shown:
                    self._shown[cid] = pose
                    self.viewer.set_assembly_pose(cid, pose.matrix)
            self.animate_poses()
        self._update_colors()
        self._refresh_tree()
        self._update_dof()
        self._watch()
        self._title()
        if fit:
            self.viewer.view_isometric()
            QTimer.singleShot(60, self.viewer.fit)

    def update_poses(self):
        """Show the current poses immediately (dragging)."""
        if getattr(self, 'measure_win', None) is not None:
            self.measure_win.model_changed()
        if getattr(self, 'mass_win', None) is not None:
            self.mass_win.model_changed()
        if getattr(self, 'geometry_check_win', None) is not None:
            self.geometry_check_win.model_changed()
        if getattr(self, 'interference_win', None) is not None:
            self.interference_win.model_changed()
        if getattr(self, 'clearance_win', None) is not None:
            self.clearance_win.model_changed()
        self._anim_timer.stop()
        self._anim = None
        for c in self.asm.components:
            self.viewer.set_assembly_pose(c.id, c.pose.matrix)
            self._shown[c.id] = c.pose.copy()
        self.viewer.render()

    def animate_poses(self):
        """Glide the components from what is on screen to their current (solved) poses.
        Each moving component turns about its centre of mass while that centre travels in a
        straight line, so parts visibly slide and swing into their mates."""
        from ..core import geometry
        if self.anim_duration <= 0:
            self.update_poses()
            return
        self._finish_animation(apply=False)
        tracks = {}
        for c in self.asm.components:
            start = self._shown.get(c.id)
            if start is None or c.shape is None:
                continue
            end = c.pose.copy()
            dR = end.R @ start.R.T
            w = _rotation_vector(dR)
            cl = geometry.center_of_mass(c.shape)
            c0, c1 = start.point(cl), end.point(cl)
            if np.linalg.norm(c1 - c0) < 1e-9 and np.linalg.norm(w) < 1e-9:
                continue
            tracks[c.id] = (start.R.copy(), w, cl, c0, c1, end)
        if not tracks:
            self.update_poses()
            return
        self._anim = {"t0": time.monotonic(), "tracks": tracks}
        self._anim_timer.start()

    def _anim_step(self):
        a = self._anim
        if a is None:
            self._anim_timer.stop()
            return
        u = min((time.monotonic() - a["t0"]) / self.anim_duration, 1.0)
        e = u * u * (3 - 2 * u)                        # ease in / out
        for cid, (R0, w, cl, c0, c1, end) in a["tracks"].items():
            if u >= 1.0:
                pose = end
            else:
                R = rodrigues(w * e) @ R0
                cpos = c0 + (c1 - c0) * e
                pose = Pose(R, cpos - R @ cl)
            self.viewer.set_assembly_pose(cid, pose.matrix)
            self._shown[cid] = pose
        self.viewer.render()
        if u >= 1.0:
            self._anim_timer.stop()
            self._anim = None

    def _finish_animation(self, apply=True):
        """Jump a running animation to its end (e.g. when the user grabs a part)."""
        if self._anim is None:
            return
        self._anim_timer.stop()
        if apply:
            for cid, track in self._anim["tracks"].items():
                self.viewer.set_assembly_pose(cid, track[-1].matrix)
                self._shown[cid] = track[-1].copy()
            self.viewer.render()
        self._anim = None

    def _update_colors(self):
        ov = {}
        if self.selected is not None:
            ov[(self.selected, None)] = COMP_SEL_RGB
        for cid, f in self.picks:
            ov[(cid, f)] = PICK_RGB
        self.viewer.set_assembly_overrides(ov)

    def _update_dof(self):
        try:
            d = self.asm.dof()
        except AssemblyError as e:
            self.dof_label.setText(f"⚠ {e}")
            return {}
        total = sum(d.values())
        self.dof_label.setText("Fully defined" if total == 0 else
                               f"Under defined: {total} DOF")
        self._dof = d
        return d

    def _refresh_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        name = self.asm.path.stem if self.asm.path else "Assembly1"
        root = QTreeWidgetItem([name])
        root.setIcon(0, icon("assembly"))
        root.setData(0, Qt.UserRole, ("root", None))
        self.tree.addTopLevelItem(root)
        dof = getattr(self, "_dof", {})
        for c in self.asm.components:
            prefix = "(f) " if c.fixed else ("(-) " if dof.get(c.id, 0) > 0 else "")
            it = QTreeWidgetItem([f"{prefix}{c.name}"])
            it.setIcon(0, icon("warning") if c.load_error else icon("part"))
            it.setToolTip(0, c.load_error or c.load_warning or f"{c.source}\n{dof.get(c.id, 0)} DOF")
            if c.load_error:
                it.setForeground(0, QColor("#c0392b"))
            it.setData(0, Qt.UserRole, ("comp", c.id))
            root.addChild(it)
        mates = QTreeWidgetItem(["Mates"])
        mates.setIcon(0, icon("folder"))
        mates.setData(0, Qt.UserRole, ("mates", None))
        root.addChild(mates)
        for m in self.asm.mates:
            names = ", ".join(self.asm.component(r.component).name if self.asm.component(r.component)
                              else "?" for r in m.refs)
            extra = (f" {m.value:g} mm" if m.type == "distance" else
                     f" {math.degrees(m.value):g}°" if m.type == "angle" else "")
            it = QTreeWidgetItem([f"{m.name}{extra} ({names})"])
            it.setIcon(0, icon("mate"))
            it.setData(0, Qt.UserRole, ("mate", m.id))
            if m.suppressed:
                it.setForeground(0, QColor("#9aa5b1"))
            mates.addChild(it)
        root.setExpanded(True)
        mates.setExpanded(True)
        self.tree.blockSignals(False)
        self.visibility.decorate_tree()

    # =====================================================================================
    # selection and tree
    # =====================================================================================
    def _tree_data(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _tree_selection(self):
        d = self._tree_data()
        self.selected = d[1] if d and d[0] == "comp" else None
        self._update_colors()

    def _select_component(self, cid):
        self.selected = cid
        root = self.tree.topLevelItem(0)
        for i in range(root.childCount() if root else 0):
            it = root.child(i)
            if it.data(0, Qt.UserRole) == ("comp", cid):
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(it)
                self.tree.blockSignals(False)
        self._update_colors()

    def _clicked(self, info):
        section = getattr(self, 'section_win', None)
        if section is not None and section.consume_pick(info):
            return
        mw = getattr(self, 'measure_win', None)
        if mw is not None and mw.isVisible():
            hit = None
            if info.topology is not None and info.kind == 'asm_face':
                _ = mw.body
                index = mw.face_lookup.get((info.component, info.face))
                if index is not None:
                    hit = ('face', index, info.point)
            mw.pick(*info.display, hit=hit)
            return
        if info.kind != "asm_face":
            if not self.mate_panel:
                self.selected = None
                self.tree.clearSelection()
                self._update_colors()
            return
        if self.mate_panel:
            self._add_pick(info.component, info.face)
            return
        self._select_component(info.component)
        comp = self.asm.component(info.component)
        f = comp.faces[info.face]
        self.status(f"{comp.name}: face {info.face + 1} ({f.kind}, {f.area:.4g} mm²)"
                    + ("  - fixed" if comp.fixed else ""))

    def _right_clicked(self, info):
        names = {c.id: c.name for c in self.asm.components}
        mw = getattr(self, 'measure_win', None)
        if mw is not None and mw.isVisible():
            self.viewer.select_other(info.display, names=names)
            return
        if self.mate_panel:
            self.viewer.select_other(info.display, names=names)
            return
        if info.kind == "asm_face" and not self.mate_panel:
            self._select_component(info.component)
            menu = self._component_menu(info.component)
            menu.addSeparator()
            menu.addAction("Select Other...", lambda: self.viewer.select_other(info.display, names=names))
            self._show_menu(menu, QCursor.pos())

    def _show_menu(self, menu, global_pos):
        """Pop up a context menu (one place, so tests can intercept it)."""
        menu.exec(global_pos)

    def _component_menu(self, cid):
        comp = self.asm.component(cid)
        menu = QMenu(self)
        menu.addAction(icon("fixed"), "Float" if comp.fixed else "Fix", self.toggle_fixed)
        menu.addAction(icon("part"), "Open Part", self.open_part)
        menu.addAction(icon("mate"), "Mate...", self.start_mate)
        menu.addSeparator()
        menu.addAction(icon("hide_component"), "Hide" if self.visibility.visible(cid) else "Show",
                       lambda: self.visibility.toggle(cid))
        menu.addAction(icon("isolate_component"), "Exit Isolate" if self.visibility.isolated == cid else "Isolate",
                       lambda: self.visibility.isolate(cid))
        menu.addAction(icon("fit"), "Zoom to Selected", lambda: self.visibility.zoom(cid))
        menu.addAction(self.a_show_all)
        menu.addSeparator()
        menu.addAction(icon("delete"), "Delete", self.delete_selected)
        return menu

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or self.mate_panel:
            return
        self.tree.setCurrentItem(item)
        kind, oid = item.data(0, Qt.UserRole)
        if kind == "comp":
            self._show_menu(self._component_menu(oid), self.tree.viewport().mapToGlobal(pos))
        elif kind == "mate":
            m = self.asm.mate(oid)
            menu = QMenu(self)
            menu.addAction("Edit...", lambda: self.edit_mate(oid))
            menu.addAction("Flip alignment", lambda: self._flip_mate(oid))
            menu.addAction("Unsuppress" if m.suppressed else "Suppress", lambda: self._suppress_mate(oid))
            menu.addSeparator()
            menu.addAction(icon("delete"), "Delete", lambda: self._delete_mate(oid))
            self._show_menu(menu, self.tree.viewport().mapToGlobal(pos))

    def _tree_double_click(self, item, _col):
        kind, oid = item.data(0, Qt.UserRole)
        if kind == "mate":
            self.edit_mate(oid)
        elif kind == "comp":
            self._select_component(oid)
            self.open_part()

    # =====================================================================================
    # component commands
    # =====================================================================================
    MOUSE_HINT = ("Left-drag a component: move (no rotation)  ·  Right-drag a component: rotate  ·  "
                  "Middle-drag: rotate view  ·  Right-drag empty space: pan")

    def _toggle_animation(self):
        self.anim_duration = 0.45 if self.a_animate.isChecked() else 0.0

    def insert_component(self, path=None):
        if not path:
            start = str(self.asm.base_dir or Path.home())
            path, _ = QFileDialog.getOpenFileName(self, "Insert Component", start, PART_FILTER)
        if not path:
            return None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.checkpoint()
            comp = self.asm.add_component(path)
        except Exception as e:  # noqa: BLE001 - shown to the user
            QApplication.restoreOverrideCursor()
            self._undo.pop()
            QMessageBox.critical(self, "Insert Component", str(e))
            return None
        QApplication.restoreOverrideCursor()
        self.refresh(fit=len(self.asm.components) <= 2)
        self._select_component(comp.id)
        self.status(f"Inserted {comp.name}" + (" (fixed)" if comp.fixed else
                                              " - left-drag to move, right-drag to rotate, or add mates"))
        return comp

    def toggle_fixed(self):
        comp = self.asm.component(self.selected) if self.selected else None
        if comp is None:
            self.status("Select a component first")
            return
        self.checkpoint()
        comp.fixed = not comp.fixed
        self.asm.solve()
        self.refresh(animate=True)
        self._select_component(comp.id)

    def delete_selected(self):
        d = self._tree_data()
        if d and d[0] == "mate":
            self._delete_mate(d[1])
            return
        if self.selected is None:
            return
        comp = self.asm.component(self.selected)
        n = sum(1 for m in self.asm.mates if any(r.component == comp.id for r in m.refs))
        msg = f"Delete {comp.name}" + (f" and its {n} mate(s)?" if n else "?")
        if QMessageBox.question(self, "Delete", msg) != QMessageBox.Yes:
            return
        self.checkpoint()
        self.asm.remove_component(comp.id)
        self.selected = None
        self.refresh()

    def open_part(self):
        comp = self.asm.component(self.selected) if self.selected else None
        if comp is None or self.main is None:
            self.status("Select a component first")
            return
        path = comp.resolve_path(self.asm.base_dir)
        self.main.file_open(str(path))
        self.main.raise_()
        self.main.activateWindow()

    def reload_parts(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = self.asm.reload()
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh(animate=True)
        errs = [c.load_error for c in self.asm.components if c.load_error]
        self.status("⚠ " + "; ".join(errs) if errs else
                    ("Parts reloaded" + ("" if res.ok else " - the mates could not all be satisfied")))

    def _watch(self):
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)
        for c in self.asm.components:
            p = c.resolve_path(self.asm.base_dir)
            if p.exists():
                self._watcher.addPath(str(p))

    def _parts_changed(self):
        if self._drag is None and not self.mate_panel:
            self.reload_parts()

    # =====================================================================================
    # dragging (called by the viewer)
    # =====================================================================================
    def press(self, info, x, y, button="left") -> bool:
        """Left-drag moves a component (orientation kept), right-drag rotates it."""
        mw = getattr(self, 'measure_win', None)
        if mw is not None and mw.isVisible():
            return False
        if self.mate_panel or info.kind != "asm_face":
            return False
        self._finish_animation()
        comp = self.asm.component(info.component)
        self._select_component(comp.id)
        if comp.fixed:
            self.status(f"{comp.name} is fixed - Float it to move it")
            return False
        self._drag = {"cid": comp.id, "local": comp.pose.inverse_point(info.point),
                      "point": np.asarray(info.point, float), "start": (x, y),
                      "R0": comp.pose.R.copy(), "snapshot": json.dumps(self.asm.to_dict()),
                      "pivot": self._component_center_local(comp), "t": time.monotonic(),
                      "button": button}
        return True

    def _component_center_local(self, comp):
        from ..core import geometry
        return geometry.center_of_mass(comp.shape)

    def move(self, x, y):
        d = self._drag
        if d is None:
            return
        if d["button"] == "left":
            target = self.viewer.ray_plane((x, y), d["point"])
            if target is None:
                return
            self.asm.drag_to(d["cid"], d["local"], target, allow_rotation=False)
        else:
            right, up, _ = self.viewer.camera_axes()
            dx, dy = x - d["start"][0], y - d["start"][1]
            axis = up * dx - right * dy
            n = np.linalg.norm(axis)
            if n < 1e-9:
                return
            ang = math.radians(0.5 * math.hypot(dx, dy))
            R_target = rodrigues(axis / n * ang) @ d["R0"]
            self.asm.rotate_drag(d["cid"], d["pivot"], R_target)
        self.update_poses()

    def release(self, x, y, moved):
        d, self._drag = self._drag, None
        if d is None or not moved:
            return
        self._undo.append(d["snapshot"])
        self._redo.clear()
        self.dirty = True
        self._title()
        self._update_dof()
        self._refresh_tree()
        self._select_component(d["cid"])

    # =====================================================================================
    # mates
    # =====================================================================================
    def start_mate(self):
        cw = getattr(self, 'clearance_win', None)
        if cw is not None:
            cw.close()
        iw = getattr(self, 'interference_win', None)
        if iw is not None:
            iw.close()
        mw = getattr(self, 'measure_win', None)
        if mw is not None:
            mw.close()
        if len(self.asm.components) < 2:
            QMessageBox.information(self, "Mate", "Insert at least two components first.")
            return
        self.mate_panel = True
        self.picks = []
        self.preview_mate = None
        self._saved_poses = {c.id: c.pose.copy() for c in self.asm.components}
        self.left.setCurrentIndex(1)
        self.selected = None
        self._refresh_mate_panel()
        self.status("Click two faces (on different components) to mate")

    def close_mate_panel(self):
        self._revert_preview()
        self.mate_panel = False
        self.picks = []
        self.left.setCurrentIndex(0)
        self.refresh(animate=True)

    def _escape(self):
        check = getattr(self, 'geometry_check_win', None)
        if check is not None and check.isVisible():
            check.close()
            return
        cw = getattr(self, 'clearance_win', None)
        if cw is not None and cw.isVisible():
            cw.close()
            return
        iw = getattr(self, 'interference_win', None)
        if iw is not None and iw.isVisible():
            iw.close()
            return
        mw = getattr(self, 'measure_win', None)
        if mw is not None and mw.isVisible():
            mw.close()
            return
        sw = getattr(self, 'section_win', None)
        if not self.mate_panel and sw is not None and sw.isVisible():
            sw.close()
            return
        if self.mate_panel:
            if self.picks:
                self._clear_picks()
            else:
                self.close_mate_panel()
        else:
            self.selected = None
            self.tree.clearSelection()
            self._update_colors()

    def start_measure(self):
        cw = getattr(self, 'clearance_win', None)
        if cw is not None:
            cw.close()
        from .measure_window import MeasureWindow
        iw = getattr(self, 'interference_win', None)
        if iw is not None:
            iw.close()
        if self.mate_panel:
            self.close_mate_panel()
        self._finish_animation()
        if getattr(self, 'measure_win', None) is None:
            self.measure_win = MeasureWindow(self)
        self.measure_win.model_changed()
        self.measure_win.open()
        self.status('Measure: select faces, edges or vertices across components (Esc to close)')
        return self.measure_win

    def show_dimensions(self):
        self.start_measure().show_dimensions()

    def show_section(self, checked=None):
        if checked is False:
            if getattr(self, "section_win", None) is not None:
                self.section_win.close()
            return
        if getattr(self, 'command', None) is not None:
            self.status('Finish the current command before editing Section View')
            self.a_section.setChecked(self.viewer.section.plane is not None)
            return
        if getattr(self, 'mate_panel', False):
            self.close_mate_panel()
        self.a_section.setChecked(True)
        from .section_view import SectionWindow
        if getattr(self, "section_win", None) is None:
            self.section_win = SectionWindow(self)
        self.section_win.open()
        return self.section_win

    def show_geometry_check(self):
        if getattr(self, 'command', None) is not None:
            self.status('Finish the current command before checking geometry')
            return
        if getattr(self, 'mate_panel', False):
            self.close_mate_panel()
        if hasattr(self, '_finish_animation'):
            self._finish_animation()
        from .geometry_check_window import GeometryCheckWindow
        if getattr(self, 'geometry_check_win', None) is None:
            self.geometry_check_win = GeometryCheckWindow(self)
        self.geometry_check_win.open()
        return self.geometry_check_win

    def show_mass_properties(self):
        from .mass_properties_window import MassPropertiesWindow
        if self.mate_panel:
            self.close_mate_panel()
        self._finish_animation()
        if getattr(self, 'mass_win', None) is None:
            self.mass_win = MassPropertiesWindow(self)
        self.mass_win.open()
        return self.mass_win

    def show_clearance(self):
        from .clearance_window import ClearanceWindow
        if self.mate_panel:
            self.close_mate_panel()
        for name in ('measure_win', 'interference_win'):
            window = getattr(self, name, None)
            if window is not None:
                window.close()
        self._finish_animation()
        if getattr(self, 'clearance_win', None) is None:
            self.clearance_win = ClearanceWindow(self)
        self.clearance_win.open()
        index = self.clearance_win.first.findData(self.selected)
        if index >= 0:
            self.clearance_win.first.setCurrentIndex(index)
        return self.clearance_win

    def show_interference(self):
        cw = getattr(self, 'clearance_win', None)
        if cw is not None:
            cw.close()
        from .interference_window import InterferenceWindow
        if self.mate_panel:
            self.close_mate_panel()
        mw = getattr(self, 'measure_win', None)
        if mw is not None:
            mw.close()
        self._finish_animation()
        if getattr(self, 'interference_win', None) is None:
            self.interference_win = InterferenceWindow(self)
        self.interference_win.open()
        return self.interference_win

    def _add_pick(self, cid, face):
        comp = self.asm.component(cid)
        kind, _, _ = face_geometry(comp.faces[face])
        if kind is None:
            self.mate_msg.setText("<span style='color:#c0392b'>Pick a planar or cylindrical face</span>")
            return
        if (cid, face) in self.picks:
            self.picks.remove((cid, face))
        else:
            if len(self.picks) == 2:
                self.picks.pop(0)
            if self.picks and self.picks[0][0] == cid:
                self.picks = []
            self.picks.append((cid, face))
        self._refresh_mate_panel()

    def _clear_picks(self):
        self.picks = []
        self._refresh_mate_panel()

    def _refresh_mate_panel(self):
        self.pick_list.clear()
        kinds = []
        for cid, f in self.picks:
            comp = self.asm.component(cid)
            k, _, _ = face_geometry(comp.faces[f])
            kinds.append(k)
            self.pick_list.addItem(f"{'Face' if k == 'plane' else 'Axis'}  of {comp.name} (face {f + 1})")
        options = mate_options(*kinds) if len(kinds) == 2 else []
        self.type_group.setExclusive(False)
        for t, b in self.type_buttons.items():
            b.blockSignals(True)
            b.setEnabled(t in options)
            if t not in options:
                b.setChecked(False)
            b.blockSignals(False)
        self.type_group.setExclusive(True)
        if options and not any(b.isChecked() for b in self.type_buttons.values()):
            b = self.type_buttons[options[0]]
            b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(False)
        self._update_colors()
        self._preview()

    def _chosen_type(self):
        return next((t for t, b in self.type_buttons.items() if b.isChecked() and b.isEnabled()), None)

    def _revert_preview(self):
        if self.preview_mate is not None and self.preview_mate in self.asm.mates:
            self.asm.mates.remove(self.preview_mate)
        self.preview_mate = None
        if self._saved_poses:
            for c in self.asm.components:
                if c.id in self._saved_poses:
                    c.pose = self._saved_poses[c.id].copy()

    def _preview(self):
        if not self.mate_panel:
            return
        self._revert_preview()
        t = self._chosen_type()
        self.mate_dist.setEnabled(t == "distance")
        self.mate_angle.setEnabled(t == "angle")
        self.mate_add_btn.setEnabled(False)
        if len(self.picks) < 2 or t is None:
            self.mate_msg.setText("")
            self.animate_poses()
            return
        refs = []
        for cid, f in self.picks:
            comp = self.asm.component(cid)
            refs.append(MateRef(cid, FaceRef.from_face(comp.faces[f]) if t != "lock" else None))
        value = (self.mate_dist.value() if t == "distance" else
                 math.radians(self.mate_angle.value()) if t == "angle" else 0.0)
        try:
            m = self.asm.new_mate(t, refs[0], refs[1], value)
        except AssemblyError as e:
            self.mate_msg.setText(f"<span style='color:#c0392b'>{e}</span>")
            return
        if self.flip.isChecked():
            m.align = -m.align
        if t == "distance" and value < 0:
            m.sign, m.value = -m.sign, abs(value)
        ok, msg = self.asm.add_mate(m)
        if not ok:
            self.mate_msg.setText(f"<span style='color:#c0392b'>{msg}</span>")
            self.animate_poses()
            return
        self.preview_mate = m
        self.mate_msg.setText(f"<span style='color:#2f9e57'>&#10004; {MATE_LABELS[t]} - "
                              "Add Mate (or Space) to keep it</span>")
        self.mate_add_btn.setEnabled(True)
        self.animate_poses()

    def add_preview_mate(self):
        if self.preview_mate is None:
            return
        m = self.preview_mate
        # the undo snapshot is the state before the preview
        current = {c.id: c.pose.copy() for c in self.asm.components}
        self.asm.mates.remove(m)
        for c in self.asm.components:
            c.pose = self._saved_poses[c.id].copy()
        self.checkpoint()
        self.asm.mates.append(m)
        for c in self.asm.components:
            c.pose = current[c.id]
        self.preview_mate = None
        self._saved_poses = {c.id: c.pose.copy() for c in self.asm.components}
        self.picks = []
        self._refresh_tree()
        self._update_dof()
        self._refresh_mate_panel()
        self.status(f"Added {m.name}")

    def edit_mate(self, mid):
        m = self.asm.mate(mid)
        if m is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit {m.name}")
        form = QFormLayout(dlg)
        val = None
        if m.type == "distance":
            val = spin(m.value * m.sign, -1e6, 1e6)
            form.addRow("Distance", val)
        elif m.type == "angle":
            val = spin(math.degrees(m.value), 0, 180, 3, "°")
            form.addRow("Angle", val)
        flip = QCheckBox("Flip alignment")
        form.addRow(flip)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return
        self.checkpoint()
        old = (m.value, m.sign, m.align)
        if m.type == "distance":
            m.sign = 1.0 if val.value() >= 0 else -1.0
            m.value = abs(val.value())
        elif m.type == "angle":
            m.value = math.radians(val.value())
        if flip.isChecked():
            m.align = -m.align
        if not self.asm.solve().ok:
            m.value, m.sign, m.align = old
            self._undo.pop()
            self.asm.solve()
            QMessageBox.warning(self, m.name, "The mates can't be satisfied with this value.")
        self.refresh(animate=True)

    def _flip_mate(self, mid):
        m = self.asm.mate(mid)
        self.checkpoint()
        m.align = -m.align
        if not self.asm.solve().ok:
            m.align = -m.align
            self._undo.pop()
            self.status("Flipping makes the mates unsolvable")
        self.refresh(animate=True)

    def _suppress_mate(self, mid):
        m = self.asm.mate(mid)
        self.checkpoint()
        m.suppressed = not m.suppressed
        self.asm.solve()
        self.refresh(animate=True)

    def _delete_mate(self, mid):
        self.checkpoint()
        self.asm.remove_mate(mid)
        self.refresh(animate=True)

    # =====================================================================================
    # files
    # =====================================================================================
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the assembly?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.file_save()
        return r == QMessageBox.Discard

    def file_new(self):
        if self.mate_panel:
            self.close_mate_panel()
        if self._confirm_discard():
            if getattr(self, 'section_win', None) is not None:
                self.section_win.close()
            self.asm = Assembly()
            self.visibility.hidden.clear()
            self.visibility.isolated = None
            self.dirty = False
            self._undo.clear()
            self._redo.clear()
            self.refresh(fit=True)

    def file_open(self, path=None):
        if self.mate_panel:
            self.close_mate_panel()
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Assembly", str(Path.home()), ASM_FILTER)
        if not path:
            return
        self.load(path)

    def load(self, path):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.asm = Assembly.load(path)
            self.asm.solve()
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Open Assembly", f"Could not open {path}:\n\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        if getattr(self, 'section_win', None) is not None:
            self.section_win.close()
        self.visibility.hidden.clear()
        self.visibility.isolated = None
        self.refresh(fit=True)
        self._report_missing()

    def _report_missing(self):
        """One short message for parts that are missing or shown from a STEP stand-in."""
        missing = sorted({Path(c.source).name for c in self.asm.components if c.load_error
                          and c.load_error.endswith("file not found")})
        broken = sorted({c.load_error for c in self.asm.components if c.load_error
                         and not c.load_error.endswith("file not found")})
        stand = sorted({Path(c.source).name for c in self.asm.components if c.load_warning})
        if not (missing or broken or stand):
            return
        n = sum(1 for c in self.asm.components if c.load_error)
        lines = []
        if missing:
            lines.append(f"{len(missing)} part file(s) are missing, so {n} component(s) are not "
                         f"shown (marked ⚠ in the tree). They should be in:\n"
                         f"{self.asm.base_dir}")
        if stand:
            lines.append(f"{len(stand)} part file(s) are missing but their STEP export was found: "
                         "those components are shown from the STEP (geometry only).")
        if broken:
            lines.append(f"{len(broken)} part(s) could not be read.")
        box = QMessageBox(QMessageBox.Warning, "Open Assembly", "\n\n".join(lines), parent=self)
        detail = []
        if missing:
            detail += ["Missing:"] + [f"  {m}" for m in missing]
        if stand:
            detail += ["Shown from STEP:"] + [f"  {m}" for m in stand]
        if broken:
            detail += ["Errors:"] + [f"  {m}" for m in broken]
        box.setDetailedText("\n".join(detail))
        box.exec()

    def file_save(self) -> bool:
        if self.asm.path is None:
            return self.file_save_as()
        self.asm.save(self.asm.path)
        self.dirty = False
        self._title()
        self.status(f"Saved {self.asm.path}")
        return True

    def pack_and_go(self, path=None):
        from ..core.packaging import pack_and_go
        if not path:
            suggested = self.asm.path.with_suffix('.zip') if self.asm.path else Path.home() / 'Assembly.zip'
            path, _ = QFileDialog.getSaveFileName(self, "Pack and Go (save part edits first)",
                                                  str(suggested), "ZIP package (*.zip)")
        if not path:
            return
        if not str(path).lower().endswith('.zip'):
            path = str(path) + '.zip'
        try:
            result = pack_and_go(self.asm, path)
        except Exception as e:
            QMessageBox.warning(self, "Pack and Go", str(e))
            return
        self.status(f"Packaged {result['components']} components / {result['parts']} part files: {path}")
        return result

    def file_save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save Assembly", str(Path.home() / "Assembly1.bcasm"),
                                              ASM_FILTER)
        if not path:
            return False
        if not path.lower().endswith(FILE_EXTENSION):
            path += FILE_EXTENSION
        self.asm.save(path)
        self.dirty = False
        self.refresh()
        self.status(f"Saved {path}")
        return True

    def _export_parasolid_instead(self, path=None):
        """No SolidWorks here to build the files with feature trees and mates: BauyrCAD
        writes a .SLDASM itself, its parts (.SLDPRT, exact solids as Imported bodies) next to
        it, or a Parasolid .x_t of the assembly."""
        from ..core import exchange
        if QMessageBox.question(
                self, "Export to SolidWorks",
                "SolidWorks is not installed on this PC, so it cannot build the .SLDASM and "
                ".SLDPRT files with feature trees and mates.\n\nExport a SolidWorks assembly "
                "(.SLDASM) with its parts as exact solids instead (or a Parasolid .x_t)? BauyrCAD "
                "writes it itself; the parts are saved next to the assembly, the components "
                "placed and fixed / floating as here.") != QMessageBox.Yes:
            return None
        formats = [exchange.SLDASM, exchange.FORMATS["xt"]]
        if path is None or Path(path).suffix.lower() not in (".sldasm", ".x_t"):
            stem = self.asm.path.stem if self.asm.path else "Assembly1"
            folder = self.asm.path.parent if self.asm.path else Path.home()
            path, chosen = QFileDialog.getSaveFileName(self, "Export to SolidWorks",
                                                       str(folder / f"{stem}.SLDASM"),
                                                       ";;".join(f.filter for f in formats))
            if not path:
                return None
            if Path(path).suffix.lower() not in (".sldasm", ".x_t"):
                fmt = next((f for f in formats if f.filter == chosen), formats[0])
                path += ".SLDASM" if fmt is exchange.SLDASM else fmt.extensions[0]
        key = "sldasm" if Path(path).suffix.lower() == ".sldasm" else "xt"
        try:
            self.asm.export(path, key)
        except Exception as e:  # noqa: BLE001 - shown to the user
            QMessageBox.critical(self, "Export", f"Could not export {path}:\n\n{e}")
            return None
        return path

    def export_solidworks(self, folder=None):
        """Native SolidWorks assembly with its parts, floating components and mates."""
        if isinstance(folder, bool):  # QAction.triggered supplies its checked state
            folder = None
        from .. import solidworks
        if not any(c.shape is not None for c in self.asm.components):
            return None
        if not solidworks.available():
            return self._export_parasolid_instead(folder)
        if self.dirty or self.asm.path is None:
            r = QMessageBox.question(self, "Export to SolidWorks",
                                     "Save the assembly first (the export reads the part files)?",
                                     QMessageBox.Save | QMessageBox.Cancel)
            if r != QMessageBox.Save or not self.file_save():
                return None
        # the known gaps (part histories, mates), before SolidWorks starts
        from . import solidworks_export
        if not solidworks_export.precheck(self, solidworks.assembly_native_issues(self.asm)):
            return None
        if folder is None:
            start = self.asm.path.parent / f"{self.asm.path.stem}_SolidWorks"
            folder = QFileDialog.getExistingDirectory(self, "Folder for the SolidWorks files",
                                                      str(start.parent))
            if not folder:
                return None
            folder = str(Path(folder) / f"{self.asm.path.stem}_SolidWorks")
        asm = self.asm
        return solidworks_export.run(self, "Export to SolidWorks",
                                     lambda: solidworks.export_assembly_files(asm, folder, require_native=True))

    def make_drawing(self):
        """Drawing of the assembly as placed (the drawing links to the saved .bcasm)."""
        if not any(c.shape is not None for c in self.asm.components):
            return None
        if self.dirty or self.asm.path is None:
            r = QMessageBox.question(self, "Make Drawing",
                                     "The drawing links to the saved assembly file. Save it now?",
                                     QMessageBox.Save | QMessageBox.Cancel)
            if r != QMessageBox.Save or not self.file_save():
                return None
        if self.main is not None:
            return self.main.open_drawing(model=str(self.asm.path))
        from .drawing_window import DrawingWindow
        win = DrawingWindow(None)
        win.show()
        win.set_model(str(self.asm.path))
        return win

    def file_export(self):
        if not self.asm.components:
            return
        stem = self.asm.path.stem if self.asm.path else "Assembly1"
        formats = exchange.ASSEMBLY_EXPORT_FORMATS
        filters = ";;".join(f.filter for f in formats)
        folder = self.asm.path.parent if self.asm.path else Path.home()
        path, chosen = QFileDialog.getSaveFileName(self, "Export Assembly", str(folder / f"{stem}.step"),
                                                   filters)
        if not path:
            return
        fmt = next((f for f in formats if f.filter == chosen), None) \
            or next((f for f in formats if Path(path).suffix.lower() in f.extensions), None) \
            or exchange.export_format_for(path)
        if Path(path).suffix.lower() not in fmt.extensions:
            path += fmt.extensions[0]
        try:
            self.asm.export(path, fmt.key)
            n = sum(c.shape is not None for c in self.asm.components)
            self.status(f"Exported {path}" + (f" as a STEP assembly of {n} components"
                                              if fmt.key.startswith("step") else ""))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export", str(e))

    def closeEvent(self, event):
        if self.mate_panel:
            self.close_mate_panel()
        if not self._confirm_discard():
            event.ignore()
            return
        QApplication.instance().removeEventFilter(self._type_filter)
        self.viewer.close()
        if self.main is not None and self in getattr(self.main, "_assembly_windows", []):
            self.main._assembly_windows.remove(self)
        event.accept()
