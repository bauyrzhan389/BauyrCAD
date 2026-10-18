"""Main application window."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import time

from PySide6.QtCore import QFileSystemWatcher, QSettings, QSize, Qt, QThread, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QToolButton,
                               QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QScrollArea, QSplitter, QStackedWidget, QTabWidget, QToolBar,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import geometry
from ..core.document import FILE_EXTENSION, Document
from ..core.features import ExtrudeFeature, ImportFeature, RevolveFeature, SketchFeature
from ..core.plane import REFERENCE_PLANES, SketchPlane
from ..fea.study import FaceForce, FixedFace, PointForce, Pressure
from ..core import exchange
from .command_line import CommandLine, CommandRegistry, TypeToCommandFilter
from ..fea.glyphs import describe as describe_load
from ..fea.glyphs import load_symbols
from .commands import FaceLoadCommand, FixedFaceCommand, PointForceCommand, ResultsCommand
from .extrude_command import ExtrudeCommand, RevolveCommand
from . import feature_commands as fc
from .sketch_draw import dashed
from .sketcher import TOOL_BY_KEY, SketchCommand
from .dialogs import ABOUT_HTML, CONTROLS_HTML, AnalysisWorker, MaterialDialog, MeshDialog
from .dialogs import BdfExportWorker
from .icons import icon
from .command_help import tooltip, description
from .viewer import FIXED_RGB, SELECT_RGB, Viewer

LOAD_RGB = [240, 150, 60]

UNDO_LIMIT = 60
FILE_FILTER = f"BauyrCAD part (*{FILE_EXTENSION})"
STEP_FILTER = "STEP files (*.step *.stp)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.doc = Document()
        self.dirty = False
        self.command = None
        self.selected_face: int | None = None
        self.select_feature_later = None
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._shown_tess = None
        self._thread = None
        self.settings = QSettings(APP_NAME, APP_NAME)

        self.setWindowIcon(icon("app"))
        self.resize(1500, 920)
        self._build_actions()
        self._build_menus()
        self._build_ui()
        for menu in self.findChildren(QMenu):
            menu.setToolTipsVisible(True)
        self._connect_viewer()
        from .context_bar import ContextBar
        from .instant3d import Instant3D
        self.context_bar = ContextBar(self)
        self.instant3d = Instant3D(self)
        self.viewer.drag_handler = self.instant3d
        self._build_command_line()
        self._update_title()
        self.refresh_tree()
        self.refresh_view()
        self._update_actions()
        geom = self.settings.value("geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    # ======================================================================
    # construction
    # ======================================================================
    def _act(self, text, icon_name, slot, shortcut=None, tip=None):
        a = QAction(icon(icon_name), text, self) if icon_name else QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setToolTip(tooltip(text, tip, icon_name))
        a.setStatusTip(description(text, tip, icon_name))
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("New", "new", self.file_new, QKeySequence.New)
        self.a_open = A("Open...", "open", self.file_open, QKeySequence.Open)
        self.a_save = A("Save", "save", self.file_save, QKeySequence.Save)
        self.a_save_as = A("Save As...", "", self.file_save_as, QKeySequence.SaveAs)
        self.a_part_properties = A("Part Properties...", "title", self.part_properties, None,
                                   "Part number and description used by assembly Bills of Materials")
        self.a_reload = A("Reload", "", self.file_reload, "Ctrl+R", "Reload the part from disk")
        self.a_new_asm = A("New Assembly", "assembly", lambda: self.open_assembly(None), "Ctrl+Shift+N",
                           "Open an assembly window to put parts together with mates")
        self.a_open_asm = A("Open Assembly...", "assembly", self.file_open_assembly, None)
        self.a_make_drawing = A("Make Drawing from Part", "drawing", self.make_drawing, "Ctrl+Shift+D",
                                "Open a drawing sheet with standard views of this part "
                                "(save as PDF, DXF or DWG)")
        self.a_open_drw = A("Open Drawing...", "drawing", self.file_open_drawing, None)
        self.a_autoreload = QAction("Auto-reload when the file changes on disk", self)
        self.a_autoreload.setCheckable(True)
        self.a_autoreload.setChecked(True)
        self.a_autoreload.toggled.connect(lambda _: self._watch())
        self.a_import = A("Import...", "import", self.file_import, "Ctrl+I",
                          "Import a STEP, IGES or BREP file as a body")
        self.a_export = A("Export...", "export", self.file_export, "Ctrl+E",
                          "Export the model: STEP, IGES, STL, OBJ, glTF, VRML, BREP")
        self.a_exit = A("Exit", "", self.close)
        self.a_undo = A("Undo", "undo", self.undo, QKeySequence.Undo)
        self.a_redo = A("Redo", "redo", self.redo, "Ctrl+Y")
        self.a_delete = A("Delete", "delete", self.delete_selected, QKeySequence.Delete)

        self.a_sketch = A("Sketch", "sketch", lambda: self.start_sketch(), None,
                          "Start a sketch on a plane or planar face")
        self.sketch_actions = {}
        for key, T in TOOL_BY_KEY.items():
            a = A(T.label, T.icon, lambda _=False, k=key: self.sketch_tool(k), None, T.label)
            a.setCheckable(True)
            self.sketch_actions[key] = a
        self.a_line = self.sketch_actions["line"]
        self.a_construction = A("Construction\nGeometry", "construction",
                                lambda: self._sketch_call("toggle_construction"), None,
                                "Toggle construction geometry for the selected sketch entities")
        self.a_repair = A("Repair\nSketch", "trim", lambda: self._sketch_call("repair_sketch"), None,
                          "Repair Sketch: close small gaps, delete zero-length and overlapping "
                          "duplicate curves")
        self.a_cmdline = A("Command Line", "command", lambda: self.cmdline.open_with(""), "Ctrl+K",
                           "Type a command (or just start typing anywhere)")
        self.a_exit_sketch = A("Exit Sketch", "exit_sketch", self.command_ok, None,
                               "Finish the sketch")
        self.a_boss = A("Extruded\nBoss", "extrude", lambda: self.start_extrude("boss"), None,
                        "Extruded Boss/Base: add material by extruding a sketch")
        self.a_cut = A("Extruded\nCut", "cut", lambda: self.start_extrude("cut"), None,
                       "Extruded Cut: remove material by extruding a sketch")
        self.a_revolve = A("Revolved\nBoss", "revolve", lambda: self.start_revolve("boss"), None,
                           "Revolved Boss/Base: add material by revolving a sketch about an axis")
        self.a_revolve_cut = A("Revolved\nCut", "revolve_cut", lambda: self.start_revolve("cut"), None,
                               "Revolved Cut: remove material by revolving a sketch about an axis")

        F = lambda cls, **kw: (lambda: self.start_feature(cls, **kw))  # noqa: E731
        self.a_sweep = A("Swept\nBoss/Base", "sweep", F(fc.SweepCommand, operation="boss"), None,
                         "Swept Boss/Base: sweep a profile (or a circle) along a path")
        self.a_sweep_cut = A("Swept Cut", "sweep_cut", F(fc.SweepCommand, operation="cut"), None,
                             "Swept Cut: remove material by sweeping a profile along a path")
        self.a_loft = A("Lofted\nBoss/Base", "loft", F(fc.LoftCommand, operation="boss"), None,
                        "Lofted Boss/Base: blend between profiles")
        self.a_loft_cut = A("Lofted Cut", "loft_cut", F(fc.LoftCommand, operation="cut"), None,
                            "Lofted Cut: remove material between profiles")
        self.a_boundary = A("Boundary\nBoss/Base", "boundary", F(fc.BoundaryCommand, operation="boss"),
                            None, "Boundary Boss/Base: smooth (curvature-continuous) blend between profiles")
        self.a_boundary_cut = A("Boundary Cut", "boundary_cut", F(fc.BoundaryCommand, operation="cut"),
                                None, "Boundary Cut")
        self.a_hole = A("Hole\nWizard", "hole", lambda: self.start_feature(fc.HoleCommand, face=self.selected_face),
                        None, "Hole Wizard: clearance, tapped, counterbore and countersink holes")
        self.a_fillet = A("Fillet", "fillet3d", F(fc.FilletCommand), None,
                          "Fillet: round edges (constant or variable radius)")
        self.a_chamfer = A("Chamfer", "chamfer3d", F(fc.ChamferCommand), None,
                           "Chamfer: bevel edges (distance, distance-distance, angle-distance)")
        self.a_lpattern = A("Linear\nPattern", "lpattern3d", F(fc.LinearPatternCommand), None,
                            "Linear Pattern: copies of features (or the body) in one or two directions")
        self.a_cpattern = A("Circular\nPattern", "cpattern3d", F(fc.CircularPatternCommand), None,
                            "Circular Pattern: copies of features (or the body) about an axis")
        self.a_mirror3d = A("Mirror", "mirror3d", F(fc.MirrorCommand), None,
                            "Mirror features (or the whole body) about a plane or planar face")
        self.a_rib = A("Rib", "rib", F(fc.RibCommand), None, "Rib: a thin wall from an open sketch line")
        self.a_draft = A("Draft", "draft", F(fc.DraftCommand), None, "Draft: taper faces about a neutral plane")
        self.a_shell = A("Shell", "shell", F(fc.ShellCommand), None,
                         "Shell: hollow the part, leaving selected faces open")
        self.a_wrap = A("Wrap", "wrap", F(fc.WrapCommand), None,
                        "Wrap: emboss / deboss a sketch (or text) onto a cylindrical face")
        self.a_intersect = A("Intersect", "intersect", F(fc.IntersectCommand), None,
                             "Intersect: split the body with planes, remove regions or fill closed voids")
        self.a_instant3d = A("Instant3D", "instant3d", lambda on: self.instant3d.set_enabled(on), None,
                             "Instant3D: click a face made by an extrude and drag its arrow to change "
                             "the depth")
        self.a_instant3d.setCheckable(True)
        self.a_instant3d.setChecked(True)
        self.a_ref_plane = A("Plane", "ref_plane", F(fc.RefPlaneCommand), None,
                             "Reference plane: offset, at an angle, mid plane, through points, normal to a curve")
        self.a_ref_axis = A("Axis", "ref_axis", F(fc.RefAxisCommand), None, "Reference axis")
        self.a_ref_point = A("Point", "ref_point", F(fc.RefPointCommand), None, "Reference point")
        self.a_helix = A("Helix and Spiral", "helix", F(fc.HelixCommand), None,
                         "Helix / spiral curve (a sweep path for springs and threads)")
        self.a_curve = A("Curve Through XYZ Points", "curve3d", F(fc.CurveCommand), None,
                         "3D curve through points (a sweep path)")

        self.a_study = A("New\nStudy", "study", self.new_study, None, "New linear static study")
        self.a_material = A("Material", "material", self.edit_material)
        self.a_fixed = A("Fixed\nFace", "fixed", lambda: self.start_fixture(), None,
                         "Fixed geometry: select faces to hold")
        self.a_face_force = A("Face\nForce", "force", lambda: self.start_face_load("force"), None,
                              "Force spread over faces (no singularity)")
        self.a_pressure = A("Pressure", "force", lambda: self.start_face_load("pressure"), None,
                            "Pressure normal to faces")
        self.a_force = A("Point\nForce", "force", lambda: self.start_force(), None,
                         "Apply a force at a point")
        self.a_run = A("Mesh &&\nRun", "run", self.run_study, "F5", "Mesh the model and run the study")
        self.a_results = A("Show\nResults", "results", self.show_results, None)
        self.a_bdf = A("Export\nBDF", "export", self.export_bdf, None,
                       "Export the current simulation setup to MSC Nastran SOL 101: mesh, material, fixtures and loads")

        self.a_iso = A("Isometric", "iso", lambda: self.viewer.view_isometric(), "Ctrl+4")
        self.a_front = A("Front", "front", lambda: self.viewer.view_front(), "Ctrl+1")
        self.a_top = A("Top", "top", lambda: self.viewer.view_top(), "Ctrl+2")
        self.a_right = A("Right", "right", lambda: self.viewer.view_right(), "Ctrl+3")
        self.a_back = A("Back", "", lambda: self.viewer.view_back())
        self.a_bottom = A("Bottom", "", lambda: self.viewer.view_bottom())
        self.a_left = A("Left", "", lambda: self.viewer.view_left())
        self.a_normal = A("Normal To", "normal_to", self.view_normal_to, None,
                          "Look straight at the selected face / active sketch")
        self.a_fit = A("Zoom to Fit", "fit", lambda: self.viewer.fit(), None,
                       "Zoom to fit (command: Z / ZE)")
        self.a_export_sw = A("Export to SolidWorks...", "solidworks", self.export_solidworks, None,
                             "Native SolidWorks part (.SLDPRT) with the feature tree rebuilt, made "
                             "by the SolidWorks installed on this PC")
        self.a_parameters = A("Parameters and Equations", "", self.edit_parameters)
        self.a_measure = A("Measure", "measure", self.start_measure, None,
                           "Measure faces, edges and vertices: sizes, distance, angle "
                           "(command: MEA)")
        self.a_view_selector = A("View\nSelector", "iso", lambda: self.viewer.show_view_selector(), None,
                                 "Pick a view on a cube (numpad 5)")

        self.a_controls = A("Mouse && Keyboard", "", lambda: QMessageBox.information(
            self, "Controls", CONTROLS_HTML))
        self.a_about = A(f"About {APP_NAME}", "app", lambda: QMessageBox.about(
            self, f"About {APP_NAME}", ABOUT_HTML))

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_new_asm, self.a_make_drawing, self.a_open, self.a_open_asm,
                  self.a_open_drw, self.a_save, self.a_save_as, self.a_part_properties,
                  self.a_reload, self.a_autoreload,
                  None, self.a_import,
                  self.a_export, self.a_export_sw, None, self.a_exit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_parameters, None, self.a_delete):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&View")
        for a in (self.a_front, self.a_top, self.a_right, self.a_iso, self.a_back, self.a_bottom,
                  self.a_left, None, self.a_normal, self.a_fit, self.a_view_selector, None,
                  self.a_measure):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Insert")
        sub = m.addMenu("Boss/Base")
        for a in (self.a_boss, self.a_revolve, self.a_sweep, self.a_loft, self.a_boundary):
            sub.addAction(a)
        sub = m.addMenu("Cut")
        for a in (self.a_cut, self.a_revolve_cut, self.a_sweep_cut, self.a_loft_cut, self.a_boundary_cut):
            sub.addAction(a)
        sub = m.addMenu("Features")
        for a in (self.a_hole, self.a_fillet, self.a_chamfer, self.a_rib, self.a_draft, self.a_shell,
                  self.a_wrap, self.a_intersect):
            sub.addAction(a)
        sub = m.addMenu("Pattern/Mirror")
        for a in (self.a_lpattern, self.a_cpattern, self.a_mirror3d):
            sub.addAction(a)
        sub = m.addMenu("Reference Geometry")
        for a in (self.a_ref_plane, self.a_ref_axis, self.a_ref_point):
            sub.addAction(a)
        sub = m.addMenu("Curves")
        for a in (self.a_helix, self.a_curve):
            sub.addAction(a)
        m.addSeparator()
        m.addAction(self.a_sketch)
        m = mb.addMenu("&Simulation")
        for a in (self.a_study, self.a_material, self.a_fixed, self.a_face_force, self.a_pressure,
                  self.a_force, None, self.a_run,
                  self.a_results, self.a_bdf):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Help")
        m.addAction(self.a_controls)
        m.addAction(self.a_about)

    def _toolbar(self, actions, icon_size=30):
        tb = QToolBar()
        tb.setIconSize(QSize(icon_size, icon_size))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        for a in actions:
            if a is None:
                tb.addSeparator()
            elif isinstance(a, (list, tuple)):
                tb.addWidget(self._group_button(a, icon_size))
            elif isinstance(a, QWidget):
                tb.addWidget(a)
            else:
                tb.addAction(a)
        return tb

    def _group_button(self, actions, icon_size):
        """Tool button with a drop-down of related commands (last used becomes default)."""
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setIconSize(QSize(icon_size, icon_size))
        b.setPopupMode(QToolButton.MenuButtonPopup)
        b.setDefaultAction(actions[0])
        m = QMenu(b)
        for a in actions:
            m.addAction(a)
            a.triggered.connect(lambda _=False, a=a, b=b: b.setDefaultAction(a))
        b.setMenu(m)
        return b

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # quick-access bar + command manager tabs
        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 0)
        quick = QToolBar()
        quick.setIconSize(QSize(20, 20))
        for a in (self.a_new, self.a_open, self.a_save, self.a_undo, self.a_redo, self.a_cmdline):
            quick.addAction(a)
        top.addWidget(quick)
        top.addStretch(1)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._toolbar([
            self.a_boss, self.a_revolve, [self.a_sweep, self.a_loft, self.a_boundary], None,
            self.a_cut, self.a_hole, self.a_revolve_cut,
            [self.a_sweep_cut, self.a_loft_cut, self.a_boundary_cut], None,
            [self.a_fillet, self.a_chamfer], [self.a_lpattern, self.a_cpattern, self.a_mirror3d],
            self.a_rib, [self.a_wrap, self.a_intersect], self.a_draft, self.a_shell, None,
            [self.a_ref_plane, self.a_ref_axis, self.a_ref_point], [self.a_helix, self.a_curve],
            self.a_instant3d, None,
            self.a_import, self.a_export]), "Features")
        S = self.sketch_actions
        self.tabs.addTab(self._toolbar([
            self.a_sketch, self.a_exit_sketch, self.a_boss, self.a_cut,
            self.a_revolve, self.a_revolve_cut, None, S["smart_dim"], S["add_relations"],
            self._quick_snaps_button(), None,
            [S["line"], S["centerline"], S["midpoint_line"]],
            [S["rect_corner"], S["rect_center"], S["rect_3pt"], S["rect_3pt_center"], S["parallelogram"]],
            [S["slot"], S["slot_center"]],
            [S["circle"], S["circle_perimeter"]],
            [S["arc_center"], S["arc_tangent"], S["arc_3pt"]],
            S["polygon"], S["spline"], S["point"], S["text"], None,
            S["trim"], S["extend"], S["convert"], S["offset"], S["mirror"], [S["fillet"], S["chamfer"]],
            [S["move"], S["copy"], S["rotate"], S["scale"]],
            [S["lin_pattern"], S["circ_pattern"]], self.a_construction, None,
            S["relations"], self.a_repair, None, S["select"],
        ], icon_size=24), "Sketch")
        self.tabs.addTab(self._toolbar([self.a_study, self.a_material, None, self.a_fixed,
                                        self.a_face_force, self.a_pressure, self.a_force, None,
                                        self.a_run, self.a_results, self.a_bdf]),
                         "Simulation")
        self.tabs.addTab(self._toolbar([self.a_front, self.a_top, self.a_right, self.a_iso,
                                        None, self.a_normal, self.a_fit, self.a_view_selector,
                                        None, self.a_measure]), "View")
        self.tabs.setMaximumHeight(96)
        self.tabs.currentChanged.connect(lambda _: self.refresh_view())
        root.addWidget(self.tabs)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#c9d3df;")
        root.addWidget(line)

        split = QSplitter(Qt.Horizontal)
        self.left = QStackedWidget()
        self.left.setMinimumWidth(340)

        # feature tree page
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemDoubleClicked.connect(self._tree_double_click)
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        self.left.addWidget(self.tree)

        # property manager page
        prop = QWidget()
        pl = QVBoxLayout(prop)
        pl.setContentsMargins(6, 6, 6, 6)
        head = QHBoxLayout()
        self.prop_icon = QLabel()
        self.prop_title = QLabel()
        self.prop_title.setStyleSheet("font-size:15px;font-weight:600;color:#1f3b5c;")
        head.addWidget(self.prop_icon)
        head.addWidget(self.prop_title, 1)
        pl.addLayout(head)
        btns = QHBoxLayout()
        self.ok_btn = QPushButton("OK")
        self.ok_btn.setIcon(icon("exit_sketch"))
        self.ok_btn.clicked.connect(self.command_ok)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.command_cancel)
        btns.addWidget(self.ok_btn)
        btns.addWidget(self.cancel_btn)
        btns.addStretch(1)
        pl.addLayout(btns)
        self.prop_scroll = QScrollArea()
        self.prop_scroll.setWidgetResizable(True)
        self.prop_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.prop_scroll.setFrameShape(QFrame.NoFrame)
        pl.addWidget(self.prop_scroll, 1)
        self.left.addWidget(prop)

        split.addWidget(self.left)
        self.viewer = Viewer()
        split.addWidget(self.viewer)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1140])
        root.addWidget(split, 1)
        self.setCentralWidget(central)

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.info_label = QLabel()
        self.statusBar().addPermanentWidget(self.info_label)
        self.statusBar().addPermanentWidget(QLabel("  mm · N · MPa  "))

        QShortcut(QKeySequence(Qt.Key_Escape), self, self._escape)

        self.setStyleSheet("""
            QTabWidget::pane { border: 0; background: #f3f6fa; }
            QTabBar::tab { padding: 5px 16px; background: #e4eaf2; border: 1px solid #c9d3df;
                           border-bottom: 0; margin-right: 2px; }
            QTabBar::tab:selected { background: #f3f6fa; font-weight: 600; }
            QToolBar { background: #f3f6fa; border: 0; spacing: 2px; }
            QToolButton { padding: 2px 6px; }
            QToolButton:checked { background: #cfe0f7; border: 1px solid #7aa6e0; border-radius: 3px; }
            QTreeWidget { background: #fbfcfe; border: 0; }
        """)

    def _connect_viewer(self):
        v = self.viewer
        v.normal_to_provider = self._numpad_normal_plane
        v.clicked.connect(self._viewer_click)
        v.hovered.connect(lambda info: self.command and self.command.on_hover(info))
        v.rightClicked.connect(self._viewer_right_click)
        v.sketchPress.connect(lambda u, w, e: self._sketch_call("on_sketch_press", u, w, e))
        v.sketchMove.connect(lambda u, w, e: self._sketch_call("on_sketch_move", u, w, e))
        v.sketchRelease.connect(lambda u, w, e: self._sketch_call("on_sketch_release", u, w, e))
        v.sketchDouble.connect(lambda u, w, e: self._sketch_call("on_sketch_double", u, w, e))
        v.sketchFinish.connect(lambda: self._sketch_call("on_sketch_finish"))

    def _sketch_call(self, name, *args):
        if isinstance(self.command, SketchCommand) and self.command.drawing:
            return getattr(self.command, name)(*args)
        return None

    # ======================================================================
    # command line (AutoCAD-style typing)
    # ======================================================================
    def _build_command_line(self):
        self.commands = CommandRegistry()
        self._register_commands()
        self.cmdline = CommandLine(self.commands, self, numeric_target=self._numeric_target)
        self._make_actions_repeatable()
        self._type_filter = TypeToCommandFilter(self, self.cmdline)
        QApplication.instance().installEventFilter(self._type_filter)

    def _make_actions_repeatable(self):
        """Toolbar / menu / right-click commands become the one Space repeats (AutoCAD)."""
        from .command_line import Command as LineCommand
        for key, a in self.sketch_actions.items():
            cmd = self._tool_commands.get(key)
            if cmd is not None:
                a.triggered.connect(lambda _=False, c=cmd: self.commands.remember(c))
        repeatable = [self.a_sketch, self.a_boss, self.a_cut, self.a_revolve, self.a_revolve_cut,
                      self.a_sweep, self.a_sweep_cut, self.a_loft, self.a_loft_cut, self.a_boundary,
                      self.a_boundary_cut, self.a_hole, self.a_fillet, self.a_chamfer, self.a_lpattern,
                      self.a_cpattern, self.a_mirror3d, self.a_rib, self.a_draft, self.a_shell,
                      self.a_wrap, self.a_intersect,
                      self.a_ref_plane, self.a_ref_axis, self.a_ref_point, self.a_helix, self.a_curve,
                      self.a_construction, self.a_study,
                      self.a_material, self.a_fixed, self.a_face_force, self.a_pressure,
                      self.a_force, self.a_run, self.a_results, self.a_import, self.a_export,
                      self.a_iso, self.a_front, self.a_top, self.a_right, self.a_back,
                      self.a_bottom, self.a_left, self.a_normal, self.a_fit]
        for a in repeatable:
            label = a.text().replace("\n", " ").replace("&&", "&").rstrip(".")
            cmd = LineCommand(label.upper().replace(" ", ""), label, a.trigger)
            a.triggered.connect(lambda _=False, c=cmd: self.commands.remember(c))

    def numpad_view(self, key: str):
        self.viewer.numpad(key)

    def dynamic_input(self, text: str) -> bool:
        c = self.command
        return isinstance(c, SketchCommand) and c.start_dynamic_input(text)

    def expects_numbers(self) -> bool:
        """A sketch tool has a point placed and waits for a length / coordinate: numpad
        digits type values then, instead of changing the view."""
        c = self.command
        return (isinstance(c, SketchCommand) and c.drawing and c.tool is not None
                and c.tool.numeric and bool(getattr(c.tool, "pts", None)))

    def _numpad_normal_plane(self):
        """Plane for numpad 0 (Normal To): the selected planar face."""
        if self.selected_face is not None and self.selected_face < len(self.doc.faces):
            f = self.doc.faces[self.selected_face]
            if f.is_planar:
                return SketchPlane.from_face(f.centroid, f.normal)
        return None

    def _numeric_target(self):
        if isinstance(self.command, SketchCommand):
            return self.command.numeric_prompt()
        return None

    def command_enter_pressed(self) -> bool:
        """Enter/Space with nothing typed: finish the current step / accept the command."""
        if isinstance(self.command, SketchCommand):
            return self.command.command_enter()
        if self.command is not None:
            self.command_ok()
            return True
        return False

    def _register_commands(self):
        R = self.commands.add
        sketching = lambda: isinstance(self.command, SketchCommand) and self.command.drawing  # noqa: E731
        tools = [
            ("LINE", "line", ("L", "PL")), ("CENTERLINE", "centerline", ("CL", "XL")),
            ("MIDPOINTLINE", "midpoint_line", ("ML",)), ("RECTANGLE", "rect_corner", ("REC", "RECT")),
            ("CENTERRECTANGLE", "rect_center", ("CREC",)), ("3POINTRECTANGLE", "rect_3pt", ("3REC",)),
            ("3POINTCENTERRECTANGLE", "rect_3pt_center", ("3CREC",)),
            ("PARALLELOGRAM", "parallelogram", ("PARA",)), ("SLOT", "slot", ("SL",)),
            ("CENTERSLOT", "slot_center", ("CSL",)), ("CIRCLE", "circle", ("C",)),
            ("PERIMETERCIRCLE", "circle_perimeter", ("3C", "PC")), ("ARC", "arc_center", ("A", "CA")),
            ("TANGENTARC", "arc_tangent", ("TA",)), ("3POINTARC", "arc_3pt", ("3A", "3P")),
            ("POLYGON", "polygon", ("POL",)), ("SPLINE", "spline", ("SPL",)), ("POINT", "point", ("PO",)), ("TEXT", "text", ("TX", "DT", "MTEXT")),
            ("SMARTDIMENSION", "smart_dim", ("D", "DIM", "SD")), ("TRIM", "trim", ("TR",)),
            ("EXTEND", "extend", ("EX",)), ("OFFSET", "offset", ("O",)),
            ("CONVERT", "convert", ("CE", "CONVERTENTITIES")),
            ("RELATIONS", "relations", ("DISPLAYRELATIONS", "DDR")),
            ("ADDRELATIONS", "add_relations", ("AR",)), ("MIRROR", "mirror", ("MI",)),
            ("FILLET", "fillet", ("F",)), ("CHAMFER", "chamfer", ("CHA",)), ("MOVE", "move", ("M",)),
            ("COPY", "copy", ("CO", "CP")), ("ROTATE", "rotate", ("RO",)), ("SCALE", "scale", ("SC",)),
            ("LINEARPATTERN", "lin_pattern", ("AR", "ARRAY", "LP")),
            ("CIRCULARPATTERN", "circ_pattern", ("PAR", "CP2", "ARRAYPOLAR")),
            ("SELECT", "select", ("SE",)),
        ]
        self._tool_commands = {}
        # outside a sketch these names mean the 3D feature (SolidWorks: the same command
        # is the sketch tool while sketching)
        feature_3d = {"fillet": fc.FilletCommand, "chamfer": fc.ChamferCommand,
                      "mirror": fc.MirrorCommand, "lin_pattern": fc.LinearPatternCommand,
                      "circ_pattern": fc.CircularPatternCommand}
        for name, key, aliases in tools:
            T = TOOL_BY_KEY[key]
            fn = (lambda k=key: self.sketch_tool(k))
            if key in feature_3d:
                fn = (lambda k=key, c=feature_3d[key]: self.sketch_tool(k) if sketching()
                      else self.start_feature(c))
            self._tool_commands[key] = R(name, T.label, fn, aliases, T.icon, T.label)
        for rt, aliases in (("horizontal", ("H", "HOR")), ("vertical", ("V", "VER")),
                            ("coincident", ("COI",)), ("parallel", ("PA",)),
                            ("perpendicular", ("PER",)), ("tangent", ("TAN",)), ("equal", ("EQ",)),
                            ("concentric", ("CON",)), ("collinear", ("COL",)), ("midpoint", ("MID",)),
                            ("fix", ("FIX",)), ("symmetric", ("SYM",))):
            R(rt.upper(), f"{rt.capitalize()} relation", lambda t=rt: self._sketch_call("add_relation", t),
              aliases, "relation", f"Add a {rt} relation to the selected sketch entities", sketching)
        for name, kind, aliases in (("SNAPEND", "point", ("END", "ENDP")), ("SNAPMID", "mid", ("MIDP",)),
                                    ("SNAPCEN", "center", ("CEN",)), ("SNAPQUA", "quadrant", ("QUA", "QUAD")),
                                    ("SNAPINT", "intersection", ("INTS",)), ("SNAPNEA", "curve", ("NEA",)),
                                    ("SNAPTAN", "tangent", ("TAN2", "TANSNAP")),
                                    ("SNAPPER", "perpendicular", ("PERP",)),
                                    ("SNAPPAR", "parallel", ("PAR2", "PARSNAP"))):
            R(name, f"Quick Snap: {kind}", lambda k=kind: self.set_quick_snap(k), aliases, "snap",
              "Snap the next click only to this kind of point", sketching)
        R("REPAIRSKETCH", "Repair sketch", lambda: self._sketch_call("repair_sketch"),
          ("REPAIR", "RS"), "trim", "Close small gaps, delete zero-length / duplicate curves",
          sketching)
        R("CONSTRUCTION", "Construction geometry", lambda: self._sketch_call("toggle_construction"),
          ("CONST", "CG"), "construction", "Toggle construction for the selection", sketching)
        R("SKETCH", "Sketch", self.start_sketch, ("SK",), "sketch", "Start a sketch")
        R("EXITSKETCH", "Exit sketch", self.command_ok, ("ES", "EXIT"), "exit_sketch",
          "Finish the sketch", lambda: isinstance(self.command, SketchCommand))
        R("EXTRUDE", "Extruded Boss", lambda: self.start_extrude("boss"), ("EXT", "BOSS"), "extrude")
        R("EXTRUDECUT", "Extruded Cut", lambda: self.start_extrude("cut"), ("CUT", "EXC"), "cut")
        R("REVOLVE", "Revolved Boss", lambda: self.start_revolve("boss"), ("REV",), "revolve")
        R("REVOLVECUT", "Revolved Cut", lambda: self.start_revolve("cut"), ("REVC", "RCUT"), "revolve_cut")
        ICONS_3D = {"SWEEP": "sweep", "SWEEPCUT": "sweep_cut", "LOFT": "loft", "LOFTCUT": "loft_cut",
                    "BOUNDARY": "boundary", "BOUNDARYCUT": "boundary_cut", "HOLEWIZARD": "hole",
                    "FILLET3D": "fillet3d", "CHAMFER3D": "chamfer3d", "PATTERNLINEAR": "lpattern3d",
                    "PATTERNCIRCULAR": "cpattern3d", "MIRRORFEATURE": "mirror3d", "RIB": "rib",
                    "DRAFT": "draft", "SHELL": "shell", "PLANE": "ref_plane", "AXIS": "ref_axis",
                    "REFPOINT": "ref_point", "HELIX": "helix", "CURVE": "curve3d", "WRAP": "wrap",
                    "INTERSECT": "intersect"}
        for name, label, act, aliases in (
                ("SWEEP", "Swept Boss/Base", self.a_sweep, ("SWP",)),
                ("SWEEPCUT", "Swept Cut", self.a_sweep_cut, ("SWC",)),
                ("LOFT", "Lofted Boss/Base", self.a_loft, ("LO",)),
                ("LOFTCUT", "Lofted Cut", self.a_loft_cut, ("LOC",)),
                ("BOUNDARY", "Boundary Boss/Base", self.a_boundary, ("BND",)),
                ("BOUNDARYCUT", "Boundary Cut", self.a_boundary_cut, ("BNDC",)),
                ("HOLEWIZARD", "Hole Wizard", self.a_hole, ("HOLE", "HW")),
                ("FILLET3D", "Fillet (3D edges)", self.a_fillet, ("FIL",)),
                ("CHAMFER3D", "Chamfer (3D edges)", self.a_chamfer, ("CHA3",)),
                ("PATTERNLINEAR", "Linear Pattern (features)", self.a_lpattern, ("LPAT",)),
                ("PATTERNCIRCULAR", "Circular Pattern (features)", self.a_cpattern, ("CPAT",)),
                ("MIRRORFEATURE", "Mirror (features / body)", self.a_mirror3d, ("MIRF",)),
                ("RIB", "Rib", self.a_rib, ()),
                ("DRAFT", "Draft", self.a_draft, ("DR",)),
                ("SHELL", "Shell", self.a_shell, ("SH",)),
                ("WRAP", "Wrap onto a cylinder", self.a_wrap, ("WR",)),
                ("INTERSECT", "Intersect with planes", self.a_intersect, ("INT", "SPLIT")),
                ("PLANE", "Reference plane", self.a_ref_plane, ("RP", "REFPLANE")),
                ("AXIS", "Reference axis", self.a_ref_axis, ("RA", "REFAXIS")),
                ("REFPOINT", "Reference point", self.a_ref_point, ("RPT",)),
                ("HELIX", "Helix / spiral", self.a_helix, ("HX", "SPIRAL")),
                ("CURVE", "Curve through XYZ points", self.a_curve, ("CXYZ",))):
            R(name, label, act.trigger, aliases, ICONS_3D.get(name, "part"), act.statusTip())
        R("NEW", "New part", self.file_new, ("NE",), "new")
        R("ASSEMBLY", "New assembly window", lambda: self.open_assembly(None), ("ASM", "NEWASM"), "assembly")
        R("DRAWING", "Make drawing from part", self.make_drawing, ("DRW", "MAKEDRAWING"), "drawing")
        R("OPEN", "Open", self.file_open, ("OP",), "open")
        R("SAVE", "Save", self.file_save, ("QSAVE",), "save")
        R("SAVEAS", "Save As (any format)", self.file_save_as, ("SA",), "save")
        R("EXPORT", "Export (STEP, IGES, STL...)", self.file_export, ("EXP",), "export")
        R("IMPORT", "Import (STEP, IGES, BREP)", self.file_import, ("IMP", "I"), "import")
        R("UNDO", "Undo", self.undo, ("U",), "undo")
        R("REDO", "Redo", self.redo, ("RED",), "redo")
        R("ERASE", "Delete selection", self.delete_selected, ("E", "DEL"), "delete")
        R("ZOOMFIT", "Zoom to fit", lambda: self.viewer.fit(), ("Z", "ZE", "FIT"), "fit")
        R("FRONT", "Front view", self.viewer.view_front, ("FR",), "front")
        R("TOP", "Top view", self.viewer.view_top, ("TO",), "top")
        R("RIGHT", "Right view", self.viewer.view_right, ("RI",), "right")
        R("ISOMETRIC", "Isometric view", self.viewer.view_isometric, ("ISO",), "iso")
        R("NORMALTO", "Normal to", self.view_normal_to, ("NT",), "normal_to")
        R("MEASURE", "Measure", self.start_measure, ("MEA", "DIST"), "measure")
        R("SOLIDWORKS", "Export to SolidWorks (.SLDPRT)", self.export_solidworks, ("SW", "SLDPRT"), "solidworks")
        R("VIEWSELECTOR", "View selector", self.viewer.show_view_selector, ("VS",), "iso")
        R("STUDY", "New study", self.new_study, ("ST",), "study")
        R("MATERIAL", "Material", self.edit_material, ("MAT",), "material")
        R("FIXEDFACE", "Fixed face", self.start_fixture, ("FF", "FIXED"), "fixed")
        R("FORCE", "Point force", self.start_force, ("FO", "LOAD"), "force")
        R("FACEFORCE", "Force on faces", lambda: self.start_face_load("force"), ("FFO", "FACELOAD"), "force")
        R("PRESSURE", "Pressure on faces", lambda: self.start_face_load("pressure"), ("PR", "PRES"), "force")
        R("RUN", "Mesh and run", self.run_study, ("RUNSTUDY", "SOLVE"), "run")
        R("BDF", "Export Nastran study", self.export_bdf, ("NASTRAN", "EXPORTBDF"), "export")
        R("RESULTS", "Show results", self.show_results, ("RES",), "results")

    # ======================================================================
    # status / helpers
    # ======================================================================
    def status(self, msg: str):
        self.status_label.setText(msg)

    def model_size(self) -> float:
        if self.doc.has_body():
            lo, hi = geometry.bounding_box(self.doc.body)
            return max(float(np.linalg.norm(hi - lo)), 1.0)
        return 100.0

    def _update_title(self):
        name = self.doc.path.name if self.doc.path else "Part1"
        self.setWindowTitle(f"{name}{' *' if self.dirty else ''} - {APP_NAME} {__version__}")

    def _update_info(self):
        if self.doc.has_body():
            v = geometry.volume(self.doc.body)
            st = self.current_study()
            txt = f"Volume {v:,.1f} mm³"
            if st:
                txt += f"   Mass {v * 1e-9 * st.material.density * 1000:,.1f} g ({st.material.name})"
            self.info_label.setText(txt)
        else:
            self.info_label.setText("No body")

    def _update_actions(self):
        busy = self.command is not None
        sketching = isinstance(self.command, SketchCommand) and self.command.drawing
        self.a_exit_sketch.setEnabled(isinstance(self.command, SketchCommand))
        for a in self.sketch_actions.values():
            a.setEnabled(not busy or sketching)
        self.a_construction.setEnabled(sketching)
        self.a_repair.setEnabled(sketching)
        has_study = bool(self.doc.studies)
        st = self.current_study()
        self.a_material.setEnabled(has_study)
        self.a_run.setEnabled(has_study and self.doc.has_body())
        self.a_bdf.setEnabled(has_study and self.doc.has_body())
        self.a_results.setEnabled(bool(st and st.results))
        self.a_export.setEnabled(self.doc.has_body())
        self.a_undo.setEnabled((bool(self._undo) and not busy) or sketching)
        self.a_redo.setEnabled((bool(self._redo) and not busy) or sketching)

    # ======================================================================
    # document state
    # ======================================================================
    def checkpoint(self):
        """Snapshot the document for undo before a modification."""
        self._undo.append(json.dumps(self.doc.to_dict()))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self.dirty = True
        self._update_title()

    def part_properties(self):
        from PySide6.QtWidgets import QInputDialog
        if self.command:
            return
        number, ok = QInputDialog.getText(self, "Part Properties", "Part number (empty = filename):",
                                         text=self.doc.part_number)
        if not ok:
            return
        description, ok = QInputDialog.getText(self, "Part Properties", "Description:",
                                              text=self.doc.description)
        if ok:
            self.checkpoint()
            self.doc.part_number, self.doc.description = number, description
            self._update_actions()

    def _restore(self, snapshot: str):
        self.instant3d.clear()
        path = self.doc.path
        self.doc = Document.from_dict(json.loads(snapshot))
        self.doc.path = path
        self.selected_face = None
        self.rebuild(mark_dirty=True)

    def undo(self):
        if isinstance(self.command, SketchCommand) and self.command.drawing:
            self.command.undo()
            return
        if not self._undo or self.command:
            return
        self._redo.append(json.dumps(self.doc.to_dict()))
        self._restore(self._undo.pop())

    def redo(self):
        if isinstance(self.command, SketchCommand) and self.command.drawing:
            self.command.redo()
            return
        if not self._redo or self.command:
            return
        self._undo.append(json.dumps(self.doc.to_dict()))
        self._restore(self._redo.pop())

    def rebuild(self, mark_dirty=True):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.doc.regenerate()
        finally:
            QApplication.restoreOverrideCursor()
        if mark_dirty:
            self.dirty = True
        self.selected_face = None
        if getattr(self, "measure_win", None) is not None:
            self.measure_win.model_changed()
        self._update_title()
        self.refresh_tree()
        self.refresh_view()
        errs = [f for f in self.doc.features if f.error]
        if errs:
            self.status(f"⚠ {errs[0].name}: {errs[0].error}")

    def _set_document(self, doc: Document, keep_view: bool = False):
        if hasattr(self, "instant3d"):
            self.instant3d.clear()
        self.doc = doc
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        self.selected_face = None
        self._shown_tess = None
        if getattr(self, "measure_win", None) is not None:
            self.measure_win.model_changed()
        self._update_title()
        self.refresh_tree()
        self.refresh_view()
        self._watch()
        if not keep_view:
            self.viewer.view_isometric()
            QTimer.singleShot(60, self.viewer.fit)  # again once the window has its final size

    # ---- live reload: scripts (and Claude) can rewrite the open .bcad file ----------------
    def _watch(self):
        if not hasattr(self, "_watcher"):
            self._watcher = QFileSystemWatcher(self)
            self._watcher.fileChanged.connect(self._file_changed)
            self._own_save = 0.0
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)
        if self.doc.path is not None and self.doc.path.exists() and self.a_autoreload.isChecked():
            self._watcher.addPath(str(self.doc.path))

    def _mark_own_save(self):
        self._own_save = time.monotonic()

    def _file_changed(self, path):
        # editors/scripts often replace the file: re-arm the watch and debounce
        QTimer.singleShot(400, lambda: self._reload_external(path))

    def _reload_external(self, path):
        if self.doc.path is None or str(self.doc.path) != path:
            return
        self._watch()
        if time.monotonic() - getattr(self, "_own_save", 0.0) < 2.0 or not self.doc.path.exists():
            return
        if self.command is not None or (self._thread is not None and self._thread.isRunning()):
            self.status("The file changed on disk - finish the current command, then File > Reload")
            return
        if self.dirty and QMessageBox.question(
                self, APP_NAME, f"{self.doc.path.name} was changed by another program.\n\n"
                                "Reload it and discard your unsaved changes?") != QMessageBox.Yes:
            return
        self.file_reload()

    def file_reload(self):
        if self.doc.path is None:
            return
        try:
            doc = Document.load(self.doc.path)
        except Exception as e:  # noqa: BLE001 - e.g. file half written: try again shortly
            self.status(f"Could not reload yet: {e}")
            QTimer.singleShot(800, lambda: self._reload_external(str(self.doc.path)))
            return
        self._set_document(doc, keep_view=True)
        self.status(f"Reloaded {self.doc.path.name} ({time.strftime('%H:%M:%S')})")

    # ======================================================================
    # view refresh
    # ======================================================================
    def refresh_view(self):
        tess = self.doc.tessellation
        if tess is not self._shown_tess:
            self.viewer.set_body(tess)
            self._shown_tess = tess
        polys = []
        for sk in self.doc.sketches():
            if sk.suppressed or self.doc.is_consumed(sk.id):
                continue
            if isinstance(self.command, SketchCommand) and self.command.feature is sk:
                continue
            px = self.viewer.world_per_pixel()
            for e in sk.sketch.entities.values():
                if e.kind == "point":
                    continue
                s = sk.sketch.sample(e)
                for part in (dashed(s, px) if e.construction else [s]):
                    polys.append(sk.plane.to_world_many(part))
            for t in sk.sketch.texts:
                try:
                    polys += [sk.plane.to_world_many(q) for q in geometry.text_outlines(t, sk.plane)]
                except Exception:  # noqa: BLE001
                    pass
        self.viewer.set_passive_sketches(polys)
        self.viewer.extra_planes = self.ref_planes()
        self.show_reference_geometry()
        sim = self.tabs.currentIndex() == 2
        st = self.current_study() if sim else None
        if self.command is None:
            self.show_study_symbols(st)
            colors = self.study_face_colors(st) if st else {}
            if self.selected_face is not None:
                colors[self.selected_face] = SELECT_RGB
            mw = getattr(self, "measure_win", None)
            if mw is not None and mw.isVisible():
                colors.update({i: SELECT_RGB for k, i in mw.refs if k == "face"})
            self.viewer.set_face_colors(colors)
        self._update_info()
        self._update_actions()

    def show_reference_geometry(self):
        """Reference planes (outline), axes, points and 3D curves stay visible, like SolidWorks."""
        from ..core import modeling
        L = self.model_size() * 0.6
        polys, curves, pts = [], [], []
        editing = getattr(self.command, "feature", None)
        for f in self.doc.features:
            if f.suppressed or f is editing:
                continue
            pl = getattr(f, "result_plane", None)
            if pl is not None:
                h = L / 2
                polys.append(np.array([pl.to_world(u, v) for u, v in
                                       ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]))
            ax = getattr(f, "result_axis", None)
            if ax is not None:
                p, d = ax
                px = self.viewer.world_per_pixel()
                polys += dashed(np.array([p - d * L, p + d * L]), px, 14, 5)
            pt = getattr(f, "result_point", None)
            if pt is not None:
                pts.append(pt)
            w = getattr(f, "result_wire", None)
            if w is not None and not self.doc.is_consumed(f.id):
                try:
                    curves.append(modeling.wire_points(w, 64))
                except Exception:  # noqa: BLE001
                    pass
        self.viewer.set_polylines("ref_geometry", polys, "#5b8fd0", 1.4)
        self.viewer.set_polylines("ref_curves", curves, "#1f5fd6", 2.0)
        self.viewer.set_markers("ref_points", pts, color="#1f5fd6", size=9)

    def study_face_colors(self, study, exclude=None) -> dict:
        colors = {}
        if study is None:
            return colors
        faces = self.doc.faces
        for fx in study.fixtures:
            if fx is exclude:
                continue
            for ref in fx.faces:
                i = ref.resolve(faces)
                if i is not None:
                    colors[i] = FIXED_RGB
        for ld in study.loads:
            if ld is exclude:
                continue
            for i in load_symbols(ld, faces, 1.0)[2]:
                colors[i] = LOAD_RGB
        return colors

    def show_study_symbols(self, study, exclude=None):
        arrows, pts = [], []
        if study is not None:
            L = 0.18 * self.model_size()
            for ld in study.loads:
                if ld is exclude:
                    continue
                a, p, _ = load_symbols(ld, self.doc.faces, L)
                arrows += a
                pts += p
        self.viewer.set_arrows("loads", arrows)
        self.viewer.set_markers("load_points", pts)
        if study is not None and self.command is not None:
            self.viewer.set_face_colors(self.study_face_colors(study, exclude))

    # ======================================================================
    # feature tree
    # ======================================================================
    def refresh_tree(self):
        sel = self._tree_current()
        self.tree.blockSignals(True)
        self.tree.clear()
        name = self.doc.path.stem if self.doc.path else "Part1"
        part = QTreeWidgetItem([name])
        part.setIcon(0, icon("part"))
        part.setData(0, Qt.UserRole, ("part", None))
        self.tree.addTopLevelItem(part)
        for pname in REFERENCE_PLANES:
            pit = QTreeWidgetItem([pname])
            pit.setIcon(0, icon("plane"))
            pit.setData(0, Qt.UserRole, ("plane", pname))
            part.addChild(pit)
        for f in self.doc.features:
            ic = self.feature_icon(f)
            label = f.name
            if isinstance(f, (ExtrudeFeature, RevolveFeature)):
                sk = self.doc.feature(f.sketch_id)
                label += f"  ({sk.name if sk else '?'})"
            it = QTreeWidgetItem([label])
            it.setIcon(0, icon("warning") if f.error else icon(ic))
            it.setData(0, Qt.UserRole, ("feature", f.id))
            if f.error:
                it.setForeground(0, QColor("#c0392b"))
                it.setToolTip(0, f.error)
            elif getattr(f, "warning", None):
                it.setForeground(0, QColor("#a05a00"))
                it.setToolTip(0, f.warning)
            elif f.suppressed:
                it.setForeground(0, QColor("#9aa5b1"))
                it.setToolTip(0, "Suppressed")
            part.addChild(it)
        part.setExpanded(True)

        if self.doc.studies:
            sim = QTreeWidgetItem(["Simulation"])
            sim.setIcon(0, icon("study"))
            sim.setData(0, Qt.UserRole, ("sim", None))
            self.tree.addTopLevelItem(sim)
            for st in self.doc.studies:
                s = QTreeWidgetItem([st.name])
                s.setIcon(0, icon("study"))
                s.setData(0, Qt.UserRole, ("study", st.id))
                sim.addChild(s)
                m = QTreeWidgetItem([f"Material: {st.material.name}"])
                m.setIcon(0, icon("material"))
                m.setData(0, Qt.UserRole, ("material", st.id))
                s.addChild(m)
                fx = QTreeWidgetItem(["Fixtures"])
                fx.setIcon(0, icon("folder"))
                fx.setData(0, Qt.UserRole, ("fixtures", st.id))
                s.addChild(fx)
                for b in st.fixtures:
                    c = QTreeWidgetItem([f"{b.name}  ({len(b.faces)} face{'s' if len(b.faces) != 1 else ''})"])
                    c.setIcon(0, icon("fixed"))
                    c.setData(0, Qt.UserRole, ("bc", st.id, b.id))
                    fx.addChild(c)
                ld = QTreeWidgetItem(["External Loads"])
                ld.setIcon(0, icon("folder"))
                ld.setData(0, Qt.UserRole, ("loads", st.id))
                s.addChild(ld)
                for b in st.loads:
                    c = QTreeWidgetItem([describe_load(b)])
                    c.setIcon(0, icon("force"))
                    c.setData(0, Qt.UserRole, ("bc", st.id, b.id))
                    ld.addChild(c)
                size = f"{st.mesh_size:g} mm" if st.mesh_size else "auto"
                ctl = f", {len(st.mesh_controls)} local control(s)" if st.mesh_controls else ""
                me = QTreeWidgetItem([f"Mesh ({size}, {'quadratic' if st.element_order == 2 else 'linear'}{ctl})"])
                me.setIcon(0, icon("mesh"))
                me.setData(0, Qt.UserRole, ("mesh", st.id))
                s.addChild(me)
                if st.results is not None:
                    r = QTreeWidgetItem(["Results"])
                    r.setIcon(0, icon("results"))
                    r.setData(0, Qt.UserRole, ("results", st.id))
                    s.addChild(r)
                s.setExpanded(True)
                fx.setExpanded(True)
                ld.setExpanded(True)
            sim.setExpanded(True)
        self.tree.blockSignals(False)

        want = ("feature", self.select_feature_later) if self.select_feature_later else sel
        self.select_feature_later = None
        if want:
            self._select_tree_data(want)
        self._update_actions()

    def feature_icon(self, f) -> str:
        cut = getattr(f, "operation", "") == "cut"
        return {"sketch": "sketch", "extrude": "cut" if cut else "extrude",
                "revolve": "revolve_cut" if cut else "revolve", "import_step": "import_feature",
                "fillet": "fillet3d", "chamfer": "chamfer3d", "shell": "shell", "draft": "draft",
                "hole": "hole", "linear_pattern": "lpattern3d", "circular_pattern": "cpattern3d",
                "mirror": "mirror3d", "sweep": "sweep_cut" if cut else "sweep",
                "loft": "loft_cut" if cut else "loft",
                "boundary": "boundary_cut" if cut else "boundary", "rib": "rib",
                "ref_plane": "ref_plane", "ref_axis": "ref_axis", "ref_point": "ref_point",
                "helix": "helix", "curve": "curve3d", "wrap": "wrap",
                "intersect": "intersect"}.get(f.TYPE, "part")

    # ---- planes: standard + reference plane features ---------------------------------------
    def ref_planes(self) -> dict:
        """name -> SketchPlane of the (built) reference plane features."""
        return {f.name: f.result_plane for f in self.doc.features
                if f.TYPE == "ref_plane" and not f.suppressed and getattr(f, "result_plane", None) is not None}

    def plane_by_name(self, name):
        return REFERENCE_PLANES.get(name) or self.ref_planes().get(name)

    def plane_spec_by_name(self, name):
        if name in REFERENCE_PLANES:
            return {"type": "std", "name": name}
        f = next((f for f in self.doc.features if f.TYPE == "ref_plane" and f.name == name), None)
        return None if f is None else {"type": "feature", "id": f.id}

    def _select_tree_data(self, data):
        it_ = self.tree.invisibleRootItem()
        stack = [it_.child(i) for i in range(it_.childCount())]
        while stack:
            it = stack.pop()
            if it.data(0, Qt.UserRole) == data:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(it)
                self.tree.blockSignals(False)
                return
            stack += [it.child(i) for i in range(it.childCount())]

    def _tree_current(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _tree_selection(self):
        data = self._tree_current()
        if data and data[0] == "plane" and self.command is None:
            it = self.tree.currentItem()
            r = self.tree.visualItemRect(it)
            self._show_plane_bar(data[1], self.tree.viewport().mapToGlobal(r.center()))
            return
        if data and data[0] == "feature" and self.command is None:
            f = self.doc.feature(data[1])
            if f is not None and f.TYPE == "ref_plane" and f.name in self.ref_planes():
                it = self.tree.currentItem()
                r = self.tree.visualItemRect(it)
                self._show_plane_bar(f.name, self.tree.viewport().mapToGlobal(r.center()))
                return
        self.viewer.highlight_plane(None)
        if data and data[0] in ("study", "material", "fixtures", "loads", "bc", "mesh", "results"):
            if self.tabs.currentIndex() != 2:
                self.tabs.setCurrentIndex(2)
                return
        self.refresh_view()

    def current_study(self):
        data = self._tree_current()
        if data and data[0] in ("study", "material", "fixtures", "loads", "bc", "mesh", "results"):
            return self.doc.study(data[1])
        return self.doc.studies[-1] if self.doc.studies else None

    def selected_sketch_id(self):
        data = self._tree_current()
        if data and data[0] == "feature":
            f = self.doc.feature(data[1])
            if isinstance(f, SketchFeature):
                return f.id
        return None

    def _tree_double_click(self, item, _col):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        kind = data[0]
        if kind == "plane":
            self.start_sketch(plane=REFERENCE_PLANES[data[1]])
        elif kind == "feature":
            self.edit_feature(data[1])
        elif kind == "material":
            self.edit_material()
        elif kind == "bc":
            self.edit_bc(data[1], data[2])
        elif kind == "mesh":
            self.run_study()
        elif kind == "results":
            self.show_results()
        elif kind == "fixtures":
            self.start_fixture()
        elif kind == "loads":
            self.start_force()

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or self.command is not None:
            return
        self.tree.setCurrentItem(item)
        data = item.data(0, Qt.UserRole)
        kind = data[0]
        menu = QMenu(self)
        if kind == "feature":
            f = self.doc.feature(data[1])
            menu.addAction(icon("sketch") if isinstance(f, SketchFeature) else icon("extrude"),
                           "Edit Feature", lambda: self.edit_feature(f.id))
            if isinstance(f, SketchFeature):
                menu.addAction(icon("extrude"), "Extrude this sketch",
                               lambda: self.start_extrude("boss", f.id))
                menu.addAction(icon("cut"), "Cut with this sketch",
                               lambda: self.start_extrude("cut", f.id))
                menu.addAction(icon("revolve"), "Revolve this sketch",
                               lambda: self.start_revolve("boss", f.id))
                menu.addAction(icon("revolve_cut"), "Revolved cut with this sketch",
                               lambda: self.start_revolve("cut", f.id))
            if f.TYPE in fc.TOOL_TYPES:
                menu.addAction(icon("lpattern3d"), "Linear Pattern this feature",
                               lambda: self.start_feature(fc.LinearPatternCommand, seed=f.id))
                menu.addAction(icon("cpattern3d"), "Circular Pattern this feature",
                               lambda: self.start_feature(fc.CircularPatternCommand, seed=f.id))
                menu.addAction(icon("mirror3d"), "Mirror this feature",
                               lambda: self.start_feature(fc.MirrorCommand, seed=f.id))
            if f.TYPE == "ref_plane" and getattr(f, "result_plane", None) is not None:
                menu.addAction(icon("sketch"), "Sketch on this plane", lambda: self.start_sketch(
                    plane=f.result_plane, plane_spec={"type": "feature", "id": f.id}))
            menu.addAction("Unsuppress" if f.suppressed else "Suppress",
                           lambda: self.toggle_suppress(f.id))
            menu.addSeparator()
            menu.addAction(icon("delete"), "Delete", lambda: self.delete_feature(f.id))
        elif kind == "study":
            st = self.doc.study(data[1])
            menu.addAction(icon("material"), "Apply Material...", self.edit_material)
            menu.addAction(icon("fixed"), "Fixed Face...", self.start_fixture)
            menu.addAction(icon("force"), "Face Force...", lambda: self.start_face_load("force"))
            menu.addAction(icon("force"), "Pressure...", lambda: self.start_face_load("pressure"))
            menu.addAction(icon("force"), "Point Force...", self.start_force)
            menu.addAction(icon("run"), "Mesh and Run", self.run_study)
            if st.results is not None:
                menu.addAction(icon("results"), "Show Results", self.show_results)
            menu.addSeparator()
            menu.addAction(icon("delete"), "Delete Study", lambda: self.delete_study(st.id))
        elif kind == "material":
            menu.addAction(icon("material"), "Edit Material...", self.edit_material)
        elif kind == "fixtures":
            menu.addAction(icon("fixed"), "Fixed Face...", self.start_fixture)
        elif kind == "loads":
            menu.addAction(icon("force"), "Face Force...", lambda: self.start_face_load("force"))
            menu.addAction(icon("force"), "Pressure...", lambda: self.start_face_load("pressure"))
            menu.addAction(icon("force"), "Point Force...", self.start_force)
        elif kind == "bc":
            menu.addAction("Edit...", lambda: self.edit_bc(data[1], data[2]))
            menu.addAction(icon("delete"), "Delete", lambda: self.delete_bc(data[1], data[2]))
        elif kind in ("mesh", "results"):
            menu.addAction(icon("run"), "Mesh and Run", self.run_study)
            if kind == "results":
                menu.addAction(icon("results"), "Show Results", self.show_results)
        else:
            return
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ======================================================================
    # command management
    # ======================================================================
    def start_command(self, cmd):
        if self.command is not None:
            self.command_cancel()
        self.instant3d.clear()
        self.command = cmd
        panel = cmd.build_panel()
        self.prop_scroll.setWidget(panel)
        self.prop_title.setText(cmd.title)
        self.prop_icon.setPixmap(icon(cmd.icon_name or "part").pixmap(24, 24))
        self.ok_btn.setText(cmd.ok_text)
        self.cancel_btn.setVisible(cmd.show_cancel)
        self.left.setCurrentIndex(1)
        self.selected_face = None
        self.viewer.set_face_colors({})
        self._update_actions()
        cmd.start()

    def _end_command(self):
        cmd = self.command
        self.command = None
        if cmd is not None:
            cmd.cleanup()
        self.left.setCurrentIndex(0)
        self.prop_scroll.takeWidget()
        self.viewer.pick_faces = True
        self.viewer.hover_enabled = True
        self.refresh_tree()
        self.refresh_view()
        self.status("")

    def command_ok(self):
        if self.command is None:
            return
        try:
            done = self.command.ok()
        except Exception as e:
            QMessageBox.critical(self, self.command.title, str(e))
            return
        if done:
            self._end_command()

    def command_cancel(self):
        if self.command is None:
            return
        self.command.cancel()
        self._end_command()

    def _escape(self):
        if self.context_bar.isVisible():
            self.context_bar.hide_bar()
        mw = getattr(self, "measure_win", None)
        if mw is not None and mw.isVisible():
            mw.close()
            return
        if self.command is None:
            self.viewer.highlight_plane(None)
            if self.selected_face is not None:
                self.selected_face = None
                self.refresh_view()
            return
        if not self.command.on_escape():
            self.command_cancel()

    def _viewer_click(self, info):
        mw = getattr(self, "measure_win", None)
        if mw is not None and mw.isVisible() and self.command is None:
            mw.pick(*info.display, hit=info.topology)
            return
        if self.command is not None:
            self.command.on_click(info)
            return
        self.selected_face = info.face if info.kind == "face" else None
        if info.kind == "plane":
            self._show_plane_bar(info.plane)
            return
        if self.selected_face is not None:
            f = self.doc.faces[self.selected_face]
            kind = "planar" if f.is_planar else "curved"
            self.status(f"Face {f.index + 1}: {kind}, area {f.area:.4g} mm²")
            self._show_face_bar(f)
        else:
            self.status("")
            self.context_bar.hide_bar()
            self.viewer.highlight_plane(None)
        self.refresh_view()
        self.instant3d.face_selected(self.selected_face)

    def _face_bar_items(self, f):
        items = []
        if f.is_planar:
            items.append(("sketch", "Sketch", lambda: self.start_sketch()))
            items.append(("normal_to", "Normal To", self.view_normal_to))
        items.append(("measure", "Measure", lambda i=f.index: self.start_measure(("face", i))))
        if f.is_planar:
            items.append(("hole", "Hole Wizard", lambda i=f.index: self.start_feature(fc.HoleCommand, face=i)))
        items.append(("fillet3d", "Fillet (face edges)", lambda i=f.index: self.start_feature(
            fc.FilletCommand, faces=[i])))
        items.append(("fit", "Zoom to Fit", lambda: self.viewer.fit(animate=True)))
        return items

    def _show_face_bar(self, f):
        self.context_bar.show_at(QCursor.pos(), self._face_bar_items(f))

    def _show_plane_bar(self, name, pos=None):
        """Plane picked (tree or viewport): show it, and offer to sketch on it."""
        self.selected_face = None
        self.viewer.extra_planes = self.ref_planes()
        self.viewer.highlight_plane(name, self.model_size() * 1.2)
        self.status(f"{name}: Sketch, or Normal To")
        plane = self.plane_by_name(name)
        spec = self.plane_spec_by_name(name)
        if plane is None:
            return
        items = [("sketch", "Sketch", lambda: self.start_sketch(plane=plane, plane_spec=spec)),
                 ("normal_to", "Normal To", lambda: self.viewer.view_normal_to(plane))]
        if spec and spec.get("type") == "feature":
            items.append(("ref_plane", "Edit Plane", lambda: self.edit_feature(spec["id"])))
        self.context_bar.show_at(pos or QCursor.pos(), items)

    def export_solidworks(self, path=None):
        """File > Export to SolidWorks: native .SLDPRT with an editable feature tree."""
        if isinstance(path, bool):  # QAction.triggered supplies its checked state
            path = None
        from .. import solidworks
        from . import solidworks_export
        if self.doc.body is None and not self.doc.features:
            QMessageBox.information(self, "Export to SolidWorks", "There is nothing to export yet.")
            return None
        if not solidworks.available():
            QMessageBox.warning(self, "Export to SolidWorks",
                                "SolidWorks is not installed on this PC (it builds the .SLDPRT). "
                                "Use STEP instead: File > Export.")
            return None
        # the known gaps, before SolidWorks starts; the export then works on a private copy
        # (the worker thread never touches the document being edited here)
        from ..core.document import Document
        doc = Document.from_dict(self.doc.to_dict())
        doc.path = self.doc.path
        if not solidworks_export.precheck(self, solidworks.native_export_issues(doc)):
            return None
        if path is None:
            stem = self.doc.path.stem if self.doc.path else "Part1"
            path, _ = QFileDialog.getSaveFileName(self, "Export to SolidWorks",
                                                  str(Path(self._last_dir()) / f"{stem}.SLDPRT"),
                                                  "SolidWorks part (*.SLDPRT)")
            if not path:
                return None
        return solidworks_export.run(self, "Export to SolidWorks",
                                     lambda: solidworks.export_part_file(doc, path, require_native=True))

    def edit_parameters(self):
        if self.command is not None:
            self.status("Finish the current command before editing parameters")
            return
        from .parameters_dialog import ParametersDialog
        dialog = ParametersDialog(self.doc, self)
        if dialog.exec() == 1:
            self.checkpoint()
            self.doc.configure_parameters(dialog.result_parameters, dialog.result_bindings)
            self.rebuild()

    def start_measure(self, first=None):
        """Tools > Measure (SolidWorks-style floating window)."""
        if self.command is not None:
            self.status("Finish the current command first, then Measure")
            return None
        from .measure_window import MeasureWindow
        if getattr(self, "measure_win", None) is None:
            self.measure_win = MeasureWindow(self)
        self.measure_win.open()
        self.selected_face = None
        if first is not None:
            self.measure_win.add(*first)
        self.status("Measure: click faces, edges or vertices (Esc to close)")
        return self.measure_win

    def _viewer_right_click(self, info):
        mw = getattr(self, "measure_win", None)
        if mw is not None and mw.isVisible() and self.command is None:
            self.viewer.select_other(info.display, kinds={"face", "edge", "vertex"}, vertices=mw.vertices())
            return
        if self.command is not None:
            if hasattr(self.command, "select_other"):
                self.command.select_other(info.display)
            return
        menu = QMenu(self)
        menu.addAction("Select Other...", lambda: self.viewer.select_other(info.display))
        menu.addSeparator()
        if info.kind == "face":
            self.selected_face = info.face
            self.refresh_view()
            if self.doc.faces[info.face].is_planar:
                menu.addAction(icon("sketch"), "Sketch on this face", lambda: self.start_sketch())
            fi = info.face
            if self.doc.faces[fi].is_planar:
                menu.addAction(icon("hole"), "Hole Wizard on this face",
                               lambda: self.start_feature(fc.HoleCommand, face=fi))
            menu.addAction(icon("fillet3d"), "Fillet the edges of this face",
                           lambda: self.start_feature(fc.FilletCommand, faces=[fi]))
            menu.addAction(icon("shell"), "Shell (remove this face)",
                           lambda: self.start_feature(fc.ShellCommand, faces=[fi]))
            menu.addAction(icon("fixed"), "Fix this face", lambda: self.start_fixture())
            menu.addAction(icon("force"), "Point force here", lambda: self.start_force(info))
            menu.addSeparator()
            menu.addAction(icon("normal_to"), "Normal To", self.view_normal_to)
        menu.addAction(icon("fit"), "Zoom to Fit", self.viewer.fit)
        menu.addAction(icon("iso"), "Isometric", self.viewer.view_isometric)
        menu.exec(self.cursor().pos())

    # ======================================================================
    # modelling commands
    # ======================================================================
    def start_sketch(self, tool="line", plane=None, plane_spec=None):
        if isinstance(self.command, SketchCommand):
            return
        self.context_bar.hide_bar()
        self.viewer.highlight_plane(None)
        if plane is None and self.selected_face is not None:
            f = self.doc.faces[self.selected_face]
            if f.is_planar:
                from ..core.refs import plane_on_face
                from ..fea.study import FaceRef
                plane = plane_on_face(f)
                plane_spec = {"type": "face", "ref": FaceRef.from_face(f).to_dict()}
        if plane is not None and plane_spec is None:
            name = next((n for n, p in REFERENCE_PLANES.items() if p is plane), None)
            if name:
                plane_spec = {"type": "std", "name": name}
        self.tabs.setCurrentIndex(1)
        self.start_command(SketchCommand(self, plane=plane, tool=tool, plane_spec=plane_spec))

    def start_feature(self, cls, faces=None, face=None, **opts):
        """Start one of the feature commands (feature_commands.py). faces / face: model faces
        to pre-select (right-click / context bar shortcuts)."""
        if self.command is not None:
            self.command_cancel()
        self.context_bar.hide_bar()
        self.viewer.highlight_plane(None)
        self.tabs.setCurrentIndex(0)
        cmd = cls(self, face=face, **opts)
        self.start_command(cmd)
        if faces:
            box = next((b for b in cmd.boxes if b.accepts("face")), None)
            if box is not None:
                for i in faces:
                    if 0 <= i < len(cmd.faces_):
                        k, key, ref, label = cmd.face_item(i)
                        box.toggle(k, key, ref, label)
        return cmd

    def sketch_tool(self, tool):
        if isinstance(self.command, SketchCommand):
            if self.command.drawing:
                self.command.set_tool(tool)
            else:
                self.command.initial_tool = tool
        else:
            self.start_sketch(tool)
        self.sync_sketch_tool(tool)

    # ---- Quick Snaps (SolidWorks) --------------------------------------------------------------
    def snap_settings(self) -> dict:
        """Which snap types are on (remembered between sessions), plus "_angle" (degrees)."""
        from .sketcher import SNAP_DEFAULT_OFF, SNAP_TYPES
        try:
            stored = json.loads(self.settings.value("sketch/snaps", "{}") or "{}")
        except (TypeError, ValueError):
            stored = {}
        out = {k: bool(stored.get(k, k not in SNAP_DEFAULT_OFF)) for k, _ in SNAP_TYPES}
        out["_angle"] = float(stored.get("_angle", 15.0))
        return out

    def _store_snaps(self, d):
        self.settings.setValue("sketch/snaps", json.dumps(d))
        c = self.command
        if isinstance(c, SketchCommand):
            c.snap_enabled = {k: v for k, v in d.items() if not k.startswith("_")}
            c.angle_step = float(d.get("_angle", 15.0))

    def _quick_snaps_button(self):
        """Toolbar drop-down like SolidWorks' Quick Snaps: pick one snap type for the next
        click, or switch snap types on / off."""
        from .sketcher import SNAP_TYPES
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setIconSize(QSize(24, 24))
        b.setIcon(icon("snap"))
        b.setText("Quick\nSnaps")
        b.setToolTip("Quick Snaps: snap the next click to one kind of point only, or choose "
                     "which snaps are on")
        b.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(b)
        head = m.addAction("Next click snaps only to:")
        head.setEnabled(False)
        self.quick_snap_actions = {}
        for k, label in SNAP_TYPES:
            if k in ("align",):
                continue
            a = m.addAction(icon("snap"), label)
            a.setCheckable(True)
            a.triggered.connect(lambda on, k=k: self.set_quick_snap(k if on else None))
            self.quick_snap_actions[k] = a
        m.addSeparator()
        sub = m.addMenu("Turn snaps on / off")
        self.snap_toggle_actions = {}
        cur = self.snap_settings()
        for k, label in SNAP_TYPES:
            a = sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(cur.get(k, True))
            a.toggled.connect(lambda on, k=k: self._toggle_snap(k, on))
            self.snap_toggle_actions[k] = a
        sub.addSeparator()
        allon = sub.addAction("All snaps on")
        ang = m.addAction("Angle snap step...")
        ang.triggered.connect(self._ask_angle_step)
        allon.triggered.connect(lambda: [a.setChecked(k != "grid") for k, a in self.snap_toggle_actions.items()])
        b.setMenu(m)
        self.quick_snaps_button = b
        return b

    def _toggle_snap(self, kind, on):
        d = self.snap_settings()
        d[kind] = bool(on)
        self._store_snaps(d)
        if kind == "grid" and isinstance(self.command, SketchCommand) and hasattr(self.command, "grid_cb"):
            self.command.grid_cb.setChecked(on)

    def _ask_angle_step(self):
        from PySide6.QtWidgets import QInputDialog
        d = self.snap_settings()
        v, ok = QInputDialog.getDouble(self, "Angle snap", "Snap lines to multiples of (degrees):",
                                       d["_angle"], 1.0, 90.0, 1)
        if ok:
            d["_angle"] = v
            self._store_snaps(d)

    def set_quick_snap(self, kind):
        """Force one snap type for the next click (None = back to all snaps)."""
        c = self.command
        if not isinstance(c, SketchCommand):
            if kind is not None:
                self.status("Quick Snaps work while sketching - start a sketch tool first")
            self.sync_quick_snap(None)
            return
        c.quick_snap = kind
        self.sync_quick_snap(kind)
        if kind is not None:
            from .sketcher import SNAP_TYPES
            self.status(f"Quick Snap: the next click snaps only to {dict(SNAP_TYPES)[kind]}")

    def sync_quick_snap(self, kind):
        for k, a in getattr(self, "quick_snap_actions", {}).items():
            a.blockSignals(True)
            a.setChecked(k == kind)
            a.blockSignals(False)

    def sync_sketch_tool(self, tool):
        for k, a in self.sketch_actions.items():
            a.setChecked(k == tool)

    def set_sketch_tools_enabled(self, on: bool):
        if not on:
            self.sync_sketch_tool(None)
        self._update_actions()

    def start_extrude(self, operation, sketch_id=None):
        if isinstance(self.command, SketchCommand):
            sketch_cmd = self.command
            if not sketch_cmd.drawing or sketch_cmd.sketch.is_empty():
                self.status("Draw a sketch profile before starting Extrude.")
                return
            self.command_ok()
            if self.command is not None:  # acceptance failed; keep the sketch editable
                return
            sketch_id = (sketch_cmd.feature.id if sketch_cmd.feature is not None
                         else self.doc.sketches()[-1].id)
        self.tabs.setCurrentIndex(0)
        self.start_command(ExtrudeCommand(self, operation, sketch_id=sketch_id or self.selected_sketch_id()))

    def start_revolve(self, operation, sketch_id=None):
        if isinstance(self.command, SketchCommand):
            sketch_cmd = self.command
            if not sketch_cmd.drawing or sketch_cmd.sketch.is_empty():
                self.status("Draw a sketch profile before starting Revolve.")
                return
            self.command_ok()
            if self.command is not None:
                return
            sketch_id = (sketch_cmd.feature.id if sketch_cmd.feature is not None
                         else self.doc.sketches()[-1].id)
        self.tabs.setCurrentIndex(0)
        self.start_command(RevolveCommand(self, operation, sketch_id=sketch_id or self.selected_sketch_id()))

    def edit_feature(self, fid):
        f = self.doc.feature(fid)
        if f is not None and f.TYPE in fc.COMMAND_FOR_TYPE:
            self.tabs.setCurrentIndex(0)
            self.start_command(fc.COMMAND_FOR_TYPE[f.TYPE](self, feature=f))
            return
        if isinstance(f, RevolveFeature):
            self.tabs.setCurrentIndex(0)
            self.start_command(RevolveCommand(self, f.operation, feature=f))
            return
        if isinstance(f, SketchFeature):
            self.tabs.setCurrentIndex(1)
            self.start_command(SketchCommand(self, feature=f))
        elif isinstance(f, ExtrudeFeature):
            self.tabs.setCurrentIndex(0)
            self.start_command(ExtrudeCommand(self, f.operation, feature=f))
        elif isinstance(f, ImportFeature):
            QMessageBox.information(self, f.name, f"Imported from STEP file:\n{f.filename}")

    def view_normal_to(self):
        if isinstance(self.command, SketchCommand) and self.command.plane is not None:
            self.viewer.view_normal_to(self.command.plane)
            return
        if self.selected_face is not None:
            f = self.doc.faces[self.selected_face]
            if f.is_planar:
                self.viewer.view_normal_to(SketchPlane.from_face(f.centroid, f.normal))
                self.viewer.fit()

    def toggle_suppress(self, fid):
        f = self.doc.feature(fid)
        self.checkpoint()
        f.suppressed = not f.suppressed
        self.rebuild()

    def delete_feature(self, fid):
        f = self.doc.feature(fid)
        deps = self.doc.dependents(fid)
        msg = f"Delete {f.name}?"
        if deps:
            msg += "\n\nThese dependent features will also be deleted:\n  " + \
                   "\n  ".join(d.name for d in deps)
        if QMessageBox.question(self, "Delete", msg) != QMessageBox.Yes:
            return
        self.checkpoint()
        self.doc.remove_feature(fid)
        self.rebuild()

    def delete_selected(self):
        if self.command is not None:
            self.command.on_delete()
            return
        data = self._tree_current()
        if not data:
            return
        if data[0] == "feature":
            self.delete_feature(data[1])
        elif data[0] == "bc":
            self.delete_bc(data[1], data[2])
        elif data[0] == "study":
            self.delete_study(data[1])

    # ======================================================================
    # simulation commands
    # ======================================================================
    def _ensure_study(self):
        st = self.current_study()
        if st is None:
            self.checkpoint()
            st = self.doc.add_study()
            self.refresh_tree()
        return st

    def new_study(self):
        self.checkpoint()
        st = self.doc.add_study()
        self.tabs.setCurrentIndex(2)
        self.refresh_tree()
        self._select_tree_data(("study", st.id))
        self.refresh_view()
        self.status(f"{st.name} created - apply a material, fixtures and loads, then Mesh & Run")

    def edit_material(self):
        st = self._ensure_study()
        dlg = MaterialDialog(self, st.material)
        if dlg.exec():
            self.checkpoint()
            st.material = dlg.material()
            st.results = None
            self.refresh_tree()
            self._update_info()

    def _require_body(self) -> bool:
        if not self.doc.has_body():
            QMessageBox.information(self, APP_NAME, "Create or import a solid body first.")
            return False
        return True

    def start_fixture(self):
        if not self._require_body():
            return
        st = self._ensure_study()
        self.tabs.setCurrentIndex(2)
        pre = self.selected_face
        cmd = FixedFaceCommand(self, st)
        if pre is not None:
            cmd.faces.append(pre)
        self.start_command(cmd)

    def start_face_load(self, mode="force"):
        if not self._require_body():
            return
        st = self._ensure_study()
        self.tabs.setCurrentIndex(2)
        cmd = FaceLoadCommand(self, st, mode=mode)
        if self.selected_face is not None:
            cmd.faces.append(self.selected_face)
        self.start_command(cmd)

    def start_force(self, info=None):
        if not self._require_body():
            return
        st = self._ensure_study()
        self.tabs.setCurrentIndex(2)
        cmd = PointForceCommand(self, st)
        self.start_command(cmd)
        if info is not None:
            cmd.on_click(info)

    def edit_bc(self, sid, bid):
        st = self.doc.study(sid)
        bc = st.find(bid)
        if isinstance(bc, FixedFace):
            self.start_command(FixedFaceCommand(self, st, bc))
        elif isinstance(bc, PointForce):
            self.start_command(PointForceCommand(self, st, bc))
        elif isinstance(bc, (FaceForce, Pressure)):
            self.start_command(FaceLoadCommand(self, st, bc))

    def delete_bc(self, sid, bid):
        self.checkpoint()
        st = self.doc.study(sid)
        st.remove(bid)
        st.results = None
        self.refresh_tree()
        self.refresh_view()

    def delete_study(self, sid):
        st = self.doc.study(sid)
        if QMessageBox.question(self, "Delete", f"Delete study {st.name}?") != QMessageBox.Yes:
            return
        self.checkpoint()
        self.doc.studies.remove(st)
        self.refresh_tree()
        self.refresh_view()

    def run_study(self):
        if self._thread is not None and self._thread.isRunning():
            return
        if not self._require_body():
            return
        st = self._ensure_study()
        if not st.fixtures or not st.loads:
            QMessageBox.information(self, st.name, "The study needs at least one Fixed Face and one "
                                                   "load before it can be run.")
            return
        dlg = MeshDialog(self, st, self.doc.body)
        if not dlg.exec():
            return
        size, order = dlg.values()
        if (size, order) != (st.mesh_size, st.element_order):
            self.checkpoint()
            st.mesh_size, st.element_order = size, order
        if self.command is not None:
            self.command_cancel()

        self._progress = QProgressDialog("Preparing...", None, 0, 0, self)
        self._progress.setWindowTitle(f"Running {st.name}")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setMinimumWidth(420)
        self._progress.show()

        self._thread = QThread(self)
        self._worker = AnalysisWorker(st, self.doc)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress.setLabelText)
        self._worker.finished.connect(lambda res, s=st: self._analysis_done(s, res))
        self._worker.failed.connect(self._analysis_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def export_bdf(self, path=None):
        if self._thread is not None and self._thread.isRunning():
            return
        st = self.current_study()
        if st is None or not self._require_body():
            return
        if not st.fixtures or not st.loads:
            QMessageBox.information(self, "Export Nastran BDF", "Add a fixed face and a load first.")
            return
        if self.command is not None:
            self.command_cancel()
        if not path:
            dlg = MeshDialog(self, st, self.doc.body)
            if not dlg.exec():
                return
            size, order = dlg.values()
            if (size, order) != (st.mesh_size, st.element_order):
                self.checkpoint()
                st.mesh_size, st.element_order = size, order
            path, _ = QFileDialog.getSaveFileName(self, "Export Nastran BDF",
                                                  str(Path(self._last_dir()) / "study.bdf"),
                                                  "Nastran bulk data (*.bdf)")
        if not path:
            return
        self._remember_dir(path)
        # Serialize now so later edits cannot change the model being exported.
        snapshot = json.loads(json.dumps(self.doc.to_dict()))
        self._progress = QProgressDialog("Preparing mesh...", None, 0, 0, self)
        self._progress.setWindowTitle("Export Nastran BDF")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.show()
        self._thread = QThread(self)
        self._worker = BdfExportWorker(snapshot, st.id, str(path))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress.setLabelText)
        self._worker.finished.connect(self._bdf_done)
        self._worker.failed.connect(self._bdf_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def _bdf_done(self, summary):
        self._close_progress()
        self.status_label.setText(f"BDF exported: {summary['nodes']:,} nodes, {summary['elements']:,} elements")
        details = (f"Saved {summary['path']}\n\nMSC Nastran SOL 101; mm, N, MPa.\n"
                   "Uses the study's current mesh settings. Pressure and face forces are equivalent nodal loads.\n"
                   "Contact and nonlinear behaviour are not included.")
        if summary['warnings']:
            details += "\n\n" + "\n".join(summary['warnings'])
        QMessageBox.information(self, "Nastran BDF exported", details)

    def _bdf_failed(self, message):
        self._close_progress()
        QMessageBox.critical(self, "Nastran export failed", message)

    def _close_progress(self):
        if getattr(self, "_progress", None) is not None:
            self._progress.close()
            self._progress = None

    def _analysis_done(self, study, res):
        self._close_progress()
        study.results = res
        self.tabs.setCurrentIndex(2)
        self.refresh_tree()
        self._select_tree_data(("results", study.id))
        self.start_command(ResultsCommand(self, study))

    def _analysis_failed(self, msg):
        self._close_progress()
        QMessageBox.critical(self, "Analysis failed", msg)

    def show_results(self):
        st = self.current_study()
        if st is None or st.results is None:
            return
        self.tabs.setCurrentIndex(2)
        self.start_command(ResultsCommand(self, st))

    # ======================================================================
    # files
    # ======================================================================
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the current part?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.file_save()
        return r == QMessageBox.Discard

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", str(Path.home())))

    def _remember_dir(self, path):
        self.settings.setValue("last_dir", str(Path(path).parent))

    def file_new(self):
        if self.command:
            self.command_cancel()
        if self._confirm_discard():
            self._set_document(Document())

    def file_open(self, path=None):
        if path and Path(path).suffix.lower() == ".bcasm":
            self.open_assembly(path)
            return
        if path and Path(path).suffix.lower() == ".bcdrw":
            self.open_drawing(path)
            return
        if self.command:
            self.command_cancel()
        if not self._confirm_discard():
            return
        if not path:
            exts = " ".join("*" + e for f in exchange.IMPORT_FORMATS for e in f.extensions)
            path, _ = QFileDialog.getOpenFileName(
                self, "Open", self._last_dir(),
                f"All supported (*{FILE_EXTENSION} *.bcasm *.bcdrw {exts});;{FILE_FILTER};;"
                f"BauyrCAD assembly (*.bcasm);;BauyrCAD drawing (*.bcdrw);;{exchange.import_filter()}")
        if not path:
            return
        if Path(path).suffix.lower() in (".bcasm", ".bcdrw"):
            self._remember_dir(path)
            (self.open_assembly if path.lower().endswith(".bcasm") else self.open_drawing)(path)
            return
        self._remember_dir(path)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if Path(path).suffix.lower() == FILE_EXTENSION:
                doc = Document.load(path)
            else:
                doc = Document()
                doc.import_file(path)
                doc.regenerate()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Open", f"Could not open {path}:\n\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._set_document(doc)
        self.status(f"Opened {path}")

    # ---- assemblies -----------------------------------------------------------------------
    def open_assembly(self, path=None):
        """Open an assembly window (new, or loading a .bcasm file)."""
        from .assembly_window import AssemblyWindow
        win = AssemblyWindow(self)
        if not hasattr(self, "_assembly_windows"):
            self._assembly_windows = []
        self._assembly_windows.append(win)
        win.show()
        if path:
            win.load(path)
        win.raise_()
        win.activateWindow()
        return win

    # ---- drawings -------------------------------------------------------------------------
    def open_drawing(self, path=None, model=None):
        """Open a drawing window: a .bcdrw file, or a new drawing of `model` (standard views)."""
        from .drawing_window import DrawingWindow
        win = DrawingWindow(self)
        if not hasattr(self, "_drawing_windows"):
            self._drawing_windows = []
        self._drawing_windows.append(win)
        win.show()
        if path:
            win.load(path)
        elif model:
            win.set_model(model)
        win.raise_()
        win.activateWindow()
        return win

    def make_drawing(self):
        """SolidWorks' Make Drawing from Part: the part must be saved (the drawing links to it)."""
        if self.doc.body is None:
            QMessageBox.information(self, "Make Drawing", "There is no solid body to draw yet.")
            return None
        if self.dirty or self.doc.path is None or self.doc.path.suffix.lower() != FILE_EXTENSION:
            r = QMessageBox.question(self, "Make Drawing",
                                     "The drawing links to the saved part file. Save the part now?",
                                     QMessageBox.Save | QMessageBox.Cancel)
            if r != QMessageBox.Save or not self.file_save():
                return None
        return self.open_drawing(model=str(self.doc.path))

    def file_open_drawing(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Drawing", self._last_dir(),
                                              "BauyrCAD drawing (*.bcdrw)")
        if path:
            self._remember_dir(path)
            self.open_drawing(path)

    def file_open_assembly(self):
        from ..core.assembly import FILE_EXTENSION as ASM_EXT
        path, _ = QFileDialog.getOpenFileName(self, "Open Assembly", self._last_dir(),
                                              f"BauyrCAD assembly (*{ASM_EXT})")
        if path:
            self._remember_dir(path)
            self.open_assembly(path)

    def file_save(self) -> bool:
        if self.doc.path is None or self.doc.path.suffix.lower() != FILE_EXTENSION:
            return self.file_save_as()
        self._mark_own_save()
        self.doc.save(self.doc.path)
        self.dirty = False
        self._update_title()
        self.status(f"Saved {self.doc.path}")
        return True

    def _export_dialog(self, title, include_native):
        stem = self.doc.path.stem if self.doc.path else "Part1"
        filters = ([FILE_FILTER] if include_native else []) + [f.filter for f in exchange.EXPORT_FORMATS]
        path, chosen = QFileDialog.getSaveFileName(
            self, title, str(Path(self._last_dir()) / f"{stem}{FILE_EXTENSION if include_native else '.step'}"),
            ";;".join(filters))
        if not path:
            return None, None
        if chosen == FILE_FILTER:
            if not path.lower().endswith(FILE_EXTENSION):
                path += FILE_EXTENSION
            return path, None
        fmt = next((f for f in exchange.EXPORT_FORMATS if f.filter == chosen), None)
        if fmt is None:
            fmt = exchange.export_format_for(path)
        if Path(path).suffix.lower() not in fmt.extensions:
            path += fmt.extensions[0]
        return path, fmt

    def file_save_as(self) -> bool:
        """Save As: native .bcad, or a copy in any neutral format (like SolidWorks Save As)."""
        path, fmt = self._export_dialog("Save As", include_native=True)
        if not path:
            return False
        self._remember_dir(path)
        if fmt is not None:
            return self._export_to(path, fmt)
        self._mark_own_save()
        self.doc.save(path)
        self.dirty = False
        self._update_title()
        self._watch()
        self.refresh_tree()
        self.status(f"Saved {path}")
        return True

    def file_export(self):
        if not self.doc.has_body():
            QMessageBox.information(self, "Export", "There is no solid body to export yet.")
            return
        path, fmt = self._export_dialog("Export", include_native=False)
        if path:
            self._remember_dir(path)
            self._export_to(path, fmt)

    def _export_to(self, path, fmt) -> bool:
        if not self.doc.has_body():
            QMessageBox.information(self, "Export", "There is no solid body to export yet.")
            return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.doc.export(path, fmt.key)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export", f"Could not export {path}:\n\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        self.status(f"Exported {fmt.label}: {path}")
        return True

    file_export_step = file_export

    def file_import(self):
        if self.command:
            self.command_cancel()
        path, _ = QFileDialog.getOpenFileName(self, "Import", self._last_dir(), exchange.import_filter())
        if not path:
            return
        self._remember_dir(path)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.checkpoint()
            self.doc.import_file(path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._undo.pop()
            QMessageBox.critical(self, "Import", f"Could not import {path}:\n\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.rebuild()
        self.viewer.fit()

    file_import_step = file_import

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            event.ignore()
            return
        if self.command:
            self.command_cancel()
        if not self._confirm_discard():
            event.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.viewer.close()
        event.accept()

    def open_initial(self, path):
        QTimer.singleShot(0, lambda: self.file_open(path))
