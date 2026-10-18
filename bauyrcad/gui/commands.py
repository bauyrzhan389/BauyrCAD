"""Interactive commands shown in the property panel (like SolidWorks' PropertyManager).

A command owns its panel widget, receives viewer events routed by the main
window, and is finished with OK (accept) or Cancel. New tools are added by
subclassing Command and wiring an action in MainWindow.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..core import geometry
from ..fea.analysis import FIELDS
from ..fea.study import FaceForce, FaceRef, FixedFace, PointForce, Pressure
from .icons import icon
from .viewer import SELECT_RGB

DIM_LIMIT = 1e6


def spin(value=0.0, lo=-DIM_LIMIT, hi=DIM_LIMIT, decimals=3, suffix=" mm", step=1.0):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setSuffix(suffix)
    s.setValue(value)
    s.setKeyboardTracking(False)
    s.setMinimumWidth(110)
    return s


def hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color:#4a5a70;")
    return lbl


class Command:
    title = "Command"
    icon_name = ""
    ok_text = "OK"
    show_cancel = True

    def __init__(self, win):
        self.win = win
        self.doc = win.doc
        self.viewer = win.viewer

    def build_panel(self) -> QWidget:
        return QWidget()

    def start(self):
        pass

    def ok(self) -> bool:
        return True

    def cancel(self):
        pass

    def cleanup(self):
        pass

    # viewer events
    def on_click(self, info):
        pass

    def on_hover(self, info):
        pass

    def on_sketch_click(self, u, v):
        pass

    def on_sketch_move(self, u, v):
        pass

    def on_sketch_finish(self):
        pass

    def on_escape(self) -> bool:
        return False

    def on_delete(self) -> bool:
        return False


# ==========================================================================
# Simulation: fixtures and loads
# ==========================================================================


class FixedFaceCommand(Command):
    icon_name = "fixed"

    def __init__(self, win, study, fixture: FixedFace | None = None):
        super().__init__(win)
        self.study = study
        self.fixture = fixture
        self.title = f"Edit {fixture.name}" if fixture else "Fixed Face"
        self.faces: list[int] = []
        if fixture:
            for ref in fixture.faces:
                i = ref.resolve(self.doc.faces)
                if i is not None:
                    self.faces.append(i)

    def build_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(hint("Click faces of the model to fix them (all translations = 0). "
                           "Click a selected face again to remove it."))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        rm = QPushButton(icon("delete"), "Remove selected face")
        rm.clicked.connect(self._remove)
        lay.addWidget(rm)
        return w

    def start(self):
        self.viewer.pick_faces = True
        self.win.show_study_symbols(self.study, exclude=self.fixture)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for i in self.faces:
            f = self.doc.faces[i]
            it = QListWidgetItem(icon("fixed"), f"Face {i + 1}  (area {f.area:.4g} mm²)")
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        colors = self.win.study_face_colors(self.study, exclude=self.fixture)
        for i in self.faces:
            colors[i] = SELECT_RGB
        self.viewer.set_face_colors(colors)

    def _remove(self):
        for it in self.list.selectedItems():
            self.faces.remove(it.data(Qt.UserRole))
        self._refresh()

    def on_click(self, info):
        if info.kind != "face":
            return
        if info.face in self.faces:
            self.faces.remove(info.face)
        else:
            self.faces.append(info.face)
        self._refresh()

    def ok(self):
        if not self.faces:
            QMessageBox.warning(self.win, self.title, "Select at least one face.")
            return False
        self.win.checkpoint()
        refs = [FaceRef.from_face(self.doc.faces[i]) for i in self.faces]
        if self.fixture:
            self.fixture.faces = refs
        else:
            n = len(self.study.fixtures) + 1
            self.study.fixtures.append(FixedFace(self.doc.new_bc_id(), f"Fixed-{n}", refs))
        self.study.results = None
        self.win.refresh_tree()
        return True

    def cleanup(self):
        self.win.refresh_view()


class FaceLoadCommand(Command):
    """Force (total, spread over the faces) or pressure on selected faces."""

    icon_name = "force"

    def __init__(self, win, study, load=None, mode="force"):
        super().__init__(win)
        self.study = study
        self.load = load
        self.mode = "pressure" if isinstance(load, Pressure) else ("force" if load else mode)
        self.title = f"Edit {load.name}" if load else ("Pressure" if self.mode == "pressure" else "Face Force")
        self.faces: list[int] = []
        if load:
            for ref in load.faces:
                i = ref.resolve(self.doc.faces)
                if i is not None:
                    self.faces.append(i)

    def build_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(hint("Click the faces that carry the load (click again to remove). A face "
                           "force is spread uniformly over the total area - no stress "
                           "singularity like a point force."))
        self.list = QListWidget()
        self.list.setMaximumHeight(120)
        lay.addWidget(self.list)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("Force (total over the faces)", "force")
        self.kind.addItem("Pressure (normal to the faces)", "pressure")
        self.kind.setCurrentIndex(0 if self.mode == "force" else 1)
        form.addRow("Type", self.kind)
        self.fx, self.fy, self.fz = (spin(0, suffix=" N") for _ in range(3))
        self.fy.setValue(-100.0)
        form.addRow("Fx", self.fx)
        form.addRow("Fy", self.fy)
        form.addRow("Fz", self.fz)
        row = QHBoxLayout()
        self.mag = spin(100, 0, DIM_LIMIT, suffix=" N")
        row.addWidget(self.mag)
        self.normal_btn = QPushButton("Normal to face")
        self.normal_btn.setToolTip("Force of this magnitude pushing into the first selected planar face")
        self.normal_btn.clicked.connect(self._along_normal)
        row.addWidget(self.normal_btn)
        self.mag_row = row
        form.addRow("Magnitude", row)
        self.p = spin(1.0, -DIM_LIMIT, DIM_LIMIT, 4, " MPa", 0.1)
        form.addRow("Pressure", self.p)
        lay.addLayout(form)
        self.info = QLabel()
        lay.addWidget(self.info)
        lay.addStretch(1)
        if isinstance(self.load, FaceForce):
            for sp, v in zip((self.fx, self.fy, self.fz), self.load.force):
                sp.setValue(v)
        elif isinstance(self.load, Pressure):
            self.p.setValue(self.load.pressure)
        for sig in (self.kind.currentIndexChanged, self.fx.valueChanged, self.fy.valueChanged,
                    self.fz.valueChanged, self.p.valueChanged):
            sig.connect(self._refresh)
        return w

    def start(self):
        self.viewer.pick_faces = True
        self.win.show_study_symbols(self.study, exclude=self.load)
        self._refresh()

    def _make(self, bc_id="preview", name="preview"):
        refs = [FaceRef.from_face(self.doc.faces[i]) for i in self.faces]
        if self.kind.currentData() == "pressure":
            return Pressure(bc_id, name, refs, self.p.value())
        return FaceForce(bc_id, name, refs, [self.fx.value(), self.fy.value(), self.fz.value()])

    def _refresh(self, *_):
        is_p = self.kind.currentData() == "pressure"
        for wdg in (self.fx, self.fy, self.fz, self.mag, self.normal_btn):
            wdg.setEnabled(not is_p)
        self.p.setEnabled(is_p)
        self.list.clear()
        area = 0.0
        for i in self.faces:
            f = self.doc.faces[i]
            area += f.area
            it = QListWidgetItem(icon("force"), f"Face {i + 1}  ({f.kind}, {f.area:.4g} mm²)")
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        if self.faces and area > 0:
            if is_p:
                self.info.setText(f"Total area {area:.4g} mm²  →  resultant ≈ {self.p.value() * area:.4g} N")
            else:
                F = np.linalg.norm([self.fx.value(), self.fy.value(), self.fz.value()])
                self.info.setText(f"Total area {area:.4g} mm²  →  mean traction {F / area:.4g} MPa")
        else:
            self.info.setText("")
        colors = self.win.study_face_colors(self.study, exclude=self.load)
        for i in self.faces:
            colors[i] = SELECT_RGB
        self.viewer.set_face_colors(colors)
        from ..fea.glyphs import load_symbols
        arrows, _, _ = load_symbols(self._make(), self.doc.faces, 0.18 * self.win.model_size())
        self.viewer.set_arrows("force_preview", arrows, color="#ff7a00")

    def _along_normal(self):
        planar = [self.doc.faces[i] for i in self.faces if self.doc.faces[i].is_planar]
        if not planar:
            return
        F = -planar[0].normal * self.mag.value()
        for sp, v in zip((self.fx, self.fy, self.fz), F):
            sp.blockSignals(True)
            sp.setValue(0.0 if abs(v) < 1e-12 else float(v))
            sp.blockSignals(False)
        self._refresh()

    def on_click(self, info):
        if info.kind != "face":
            return
        if info.face in self.faces:
            self.faces.remove(info.face)
        else:
            self.faces.append(info.face)
        self._refresh()

    def ok(self):
        if not self.faces:
            QMessageBox.warning(self.win, self.title, "Select at least one face.")
            return False
        new = self._make()
        if isinstance(new, FaceForce) and np.linalg.norm(new.force) == 0:
            QMessageBox.warning(self.win, self.title, "The force vector is zero.")
            return False
        self.win.checkpoint()
        if self.load is not None and type(self.load) is type(new):
            self.load.faces = new.faces
            if isinstance(new, Pressure):
                self.load.pressure = new.pressure
            else:
                self.load.force = new.force
        else:
            if self.load is not None:
                self.study.remove(self.load.id)
            n = len(self.study.loads) + 1
            new.id = self.doc.new_bc_id()
            new.name = f"{'Pressure' if isinstance(new, Pressure) else 'Face Force'}-{n}"
            self.study.loads.append(new)
        self.study.results = None
        self.win.refresh_tree()
        return True

    def cleanup(self):
        self.viewer.set_arrows("force_preview", [])
        self.win.refresh_view()


class PointForceCommand(Command):
    icon_name = "force"

    def __init__(self, win, study, load: PointForce | None = None):
        super().__init__(win)
        self.study = study
        self.load = load
        self.title = f"Edit {load.name}" if load else "Point Force"
        self.point = np.array(load.point, float) if load else None
        self.normal = None

    def build_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(hint("Click on the model to place the load point (snaps to vertices "
                           "when close), or type the coordinates."))
        g = QGroupBox("Point of application")
        f = QFormLayout(g)
        self.px, self.py, self.pz = spin(), spin(), spin()
        f.addRow("X", self.px)
        f.addRow("Y", self.py)
        f.addRow("Z", self.pz)
        self.snap = QCheckBox("Snap to model vertices")
        self.snap.setChecked(True)
        f.addRow(self.snap)
        lay.addWidget(g)

        g = QGroupBox("Force vector")
        f = QFormLayout(g)
        self.fx, self.fy, self.fz = (spin(0, suffix=" N") for _ in range(3))
        f.addRow("Fx", self.fx)
        f.addRow("Fy", self.fy)
        f.addRow("Fz", self.fz)
        row = QHBoxLayout()
        self.mag = spin(100, 0, DIM_LIMIT, suffix=" N")
        row.addWidget(self.mag)
        self.normal_btn = QPushButton("Normal to face")
        self.normal_btn.setToolTip("Set the force to the given magnitude, pushing into the picked face")
        self.normal_btn.clicked.connect(self._along_normal)
        self.normal_btn.setEnabled(False)
        row.addWidget(self.normal_btn)
        f.addRow("Magnitude", row)
        self.mag_label = QLabel()
        f.addRow("|F|", self.mag_label)
        lay.addWidget(g)
        lay.addStretch(1)

        if self.load:
            for s, v in zip((self.px, self.py, self.pz), self.load.point):
                s.setValue(v)
            for s, v in zip((self.fx, self.fy, self.fz), self.load.force):
                s.setValue(v)
        else:
            self.fy.setValue(-100.0)
        for s in (self.px, self.py, self.pz, self.fx, self.fy, self.fz):
            s.valueChanged.connect(self._changed)
        return w

    def start(self):
        self.viewer.pick_faces = True
        self.win.show_study_symbols(self.study, exclude=self.load)
        self._changed()

    def _changed(self, *_):
        if self.load or self.point is not None:
            self.point = np.array([self.px.value(), self.py.value(), self.pz.value()])
        F = np.array([self.fx.value(), self.fy.value(), self.fz.value()])
        self.mag_label.setText(f"{np.linalg.norm(F):.4g} N")
        if self.point is not None:
            L = 0.18 * self.win.model_size()
            self.viewer.set_arrows("force_preview", [(self.point, F, L)], color="#ff7a00")
            self.viewer.set_markers("force_point", [self.point], color="#ff7a00")

    def on_click(self, info):
        if info.kind != "face" or info.point is None:
            return
        p = info.point
        if self.snap.isChecked():
            verts = geometry.vertices(self.doc.body)
            if len(verts):
                screen = np.array([self.viewer.world_to_display(v) for v in verts])
                d = np.linalg.norm(screen - np.array(info.display), axis=1)
                k = int(np.argmin(d))
                if d[k] < 14:
                    p = verts[k]
        face = self.doc.faces[info.face]
        self.normal = face.normal if face.is_planar else None
        self.normal_btn.setEnabled(self.normal is not None)
        for s, v in zip((self.px, self.py, self.pz), p):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)
        self.point = np.array(p, float)
        self._changed()

    def _along_normal(self):
        if self.normal is None:
            return
        F = -self.normal * self.mag.value()
        for s, v in zip((self.fx, self.fy, self.fz), F):
            s.blockSignals(True)
            s.setValue(0.0 if abs(v) < 1e-12 else float(v))
            s.blockSignals(False)
        self._changed()

    def ok(self):
        F = [self.fx.value(), self.fy.value(), self.fz.value()]
        if self.point is None:
            QMessageBox.warning(self.win, self.title, "Pick a point on the model.")
            return False
        if np.linalg.norm(F) == 0:
            QMessageBox.warning(self.win, self.title, "The force vector is zero.")
            return False
        self.win.checkpoint()
        if self.load:
            self.load.point, self.load.force = self.point.tolist(), F
        else:
            n = len(self.study.loads) + 1
            self.study.loads.append(PointForce(self.doc.new_bc_id(), f"Force-{n}",
                                               self.point.tolist(), F))
        self.study.results = None
        self.win.refresh_tree()
        return True

    def cleanup(self):
        self.viewer.set_arrows("force_preview", [])
        self.viewer.set_markers("force_point", [])
        self.win.refresh_view()


# ==========================================================================
# Results
# ==========================================================================


class ResultsCommand(Command):
    icon_name = "results"
    ok_text = "Close"
    show_cancel = False

    def __init__(self, win, study):
        super().__init__(win)
        self.study = study
        self.title = f"Results - {study.name}"
        self.res = study.results
        self.grid = self.res.vtk_grid()
        s = self.res.summary()
        lo, hi = geometry.bounding_box(self.doc.body)
        diag = float(np.linalg.norm(hi - lo))
        self.auto_scale = 0.08 * diag / s["max_displacement"] if s["max_displacement"] > 0 else 1.0

    def build_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.field = QComboBox()
        for name in FIELDS:
            self.field.addItem(name)
        form.addRow("Result", self.field)
        self.deform = QCheckBox("Deformed shape")
        self.deform.setChecked(True)
        form.addRow(self.deform)
        self.scale = spin(self.auto_scale, 0, 1e12, decimals=4, suffix=" ×")
        row = QHBoxLayout()
        row.addWidget(self.scale)
        auto = QPushButton("Auto")
        auto.clicked.connect(lambda: self.scale.setValue(self.auto_scale))
        true = QPushButton("True")
        true.clicked.connect(lambda: self.scale.setValue(1.0))
        row.addWidget(auto)
        row.addWidget(true)
        form.addRow("Scale", row)
        self.mesh_cb = QCheckBox("Show mesh")
        self.outline_cb = QCheckBox("Show undeformed edges")
        self.outline_cb.setChecked(True)
        form.addRow(self.mesh_cb)
        form.addRow(self.outline_cb)
        self.legend = QComboBox()
        self.legend.addItem("Full range", 100.0)
        self.legend.addItem("99th percentile", 99.0)
        self.legend.addItem("95th percentile", 95.0)
        self.legend.setToolTip("Clip the legend maximum to hide local peaks such as the "
                               "singular stress at a point load")
        form.addRow("Legend", self.legend)
        lay.addLayout(form)

        s = self.res.summary()
        m = self.res.material
        rx, ry, rz = s["reaction"]
        ax, ay, az = s["applied"]
        fos = s["min_fos"]
        fos_color = "#2f9e57" if fos >= 1.5 else ("#c77700" if fos >= 1.0 else "#c0392b")
        info = QLabel(
            f"<b>Material:</b> {m.name}<br>"
            f"E = {m.E:g} MPa, ν = {m.nu:g}, σ<sub>y</sub> = {m.yield_strength:g} MPa"
            + (f", σ<sub>u</sub> = {m.ultimate_strength:g} MPa" if m.ultimate_strength else "") + "<br><br>"
            f"<b>Max von Mises:</b> {s['max_von_mises']:.4g} MPa<br>"
            f"<b>Max displacement:</b> {s['max_displacement']:.4g} mm<br>"
            f"<b>Min factor of safety:</b> <span style='color:{fos_color}'>{fos:.3g}</span><br>"
            + (f"<b>Away from point loads:</b> σ<sub>vM</sub> {s['max_von_mises_away_from_point_loads']:.4g} MPa, "
               f"FoS {s['min_fos_away_from_point_loads']:.3g}<br>"
               if "max_von_mises_away_from_point_loads" in s else "") + "<br>"
            f"<b>Applied force:</b> ({ax:.4g}, {ay:.4g}, {az:.4g}) N<br>"
            f"<b>Reaction force:</b> ({rx:.4g}, {ry:.4g}, {rz:.4g}) N<br><br>"
            f"Mesh: {s['nodes']} nodes, {s['elements']} elements "
            f"({'quadratic' if self.res.mesh.order == 2 else 'linear'} tetra)<br>"
            f"Solved {s['dof']} DOF in {s['solve_time']:.2f} s<br>Solver: {s['method']}")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.setStyleSheet("background:#f4f7fb;border:1px solid #d5dde8;padding:6px;")
        lay.addWidget(info)
        if self.res.warnings:
            warn = QLabel("<br>".join(f"&#9888; {x}" for x in self.res.warnings))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#a05a00;")
            lay.addWidget(warn)
        if self.res.load_nodes:
            lay.addWidget(hint("Point loads create a local stress singularity: stresses right at "
                               "the load point grow as the mesh is refined. Judge stresses a "
                               "short distance away from it (or use a Face Force)."))
        exp = QPushButton(icon("export"), "Export results (.vtu for ParaView)...")
        exp.clicked.connect(self._export)
        lay.addWidget(exp)
        lay.addStretch(1)
        for sig in (self.field.currentIndexChanged, self.deform.toggled, self.scale.valueChanged,
                    self.mesh_cb.toggled, self.outline_cb.toggled, self.legend.currentIndexChanged):
            sig.connect(self.update_view)
        return w

    def start(self):
        self.win.show_study_symbols(None)
        self.viewer.set_passive_sketches([])
        self.update_view()

    def update_view(self, *_):
        name = self.field.currentText()
        scale = self.scale.value() if self.deform.isChecked() else 0.0
        self.scale.setEnabled(self.deform.isChecked())
        clim = None
        vals = self.res.field(name)
        pct = self.legend.currentData()
        if name == "Factor of Safety":
            clim = [float(vals.min()), float(min(vals.max(), max(10 * vals.min(), 10)))]
        elif pct < 100:
            clim = [float(vals.min()), float(np.percentile(vals, pct))]
        self.viewer.show_results(self.grid, name, FIELDS[name], scale, self.mesh_cb.isChecked(),
                                 self.outline_cb.isChecked(), clim)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self.win, "Export results", f"{self.study.name}.vtu",
                                              "VTK unstructured grid (*.vtu)")
        if path:
            self.grid.save(path)
            self.win.status(f"Results written to {path}")

    def cleanup(self):
        self.viewer.hide_results()
        self.win.refresh_view()
