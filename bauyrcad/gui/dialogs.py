"""Modal dialogs and the background analysis worker."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QVBoxLayout)

from .. import APP_NAME, __version__
from ..fea.mesher import auto_mesh_size
from ..fea.study import MATERIAL_LIBRARY, Material
from .commands import spin
from .icons import icon

CUSTOM = "Custom"


class MaterialDialog(QDialog):
    def __init__(self, parent, material: Material):
        super().__init__(parent)
        self.setWindowTitle("Material")
        self.setWindowIcon(icon("material"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.lib = QComboBox()
        self.lib.addItems([*MATERIAL_LIBRARY, CUSTOM])
        form.addRow("Library", self.lib)
        self.E = spin(material.E, 1e-6, 1e9, 1, " MPa", 1000)
        self.nu = spin(material.nu, 0.0, 0.499, 4, "", 0.01)
        self.sy = spin(material.yield_strength, 1e-6, 1e9, 2, " MPa", 10)
        self.rho = spin(material.density, 1e-6, 1e6, 1, " kg/m³", 100)
        self.su = spin(material.ultimate_strength, 0, 1e9, 2, " MPa", 10)
        self.su.setToolTip("Ultimate tensile strength (0 = unknown)")
        form.addRow("Elastic modulus E", self.E)
        form.addRow("Poisson's ratio ν", self.nu)
        form.addRow("Yield strength", self.sy)
        form.addRow("Ultimate strength", self.su)
        form.addRow("Density", self.rho)
        lay.addLayout(form)
        lay.addWidget(QLabel("Linear elastic isotropic model."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.lib.setCurrentText(material.name if material.name in MATERIAL_LIBRARY else CUSTOM)
        self._custom_name = material.name
        self.lib.currentTextChanged.connect(self._pick)
        for s in (self.E, self.nu, self.sy, self.rho, self.su):
            s.valueChanged.connect(self._edited)

    def _pick(self, name):
        m = MATERIAL_LIBRARY.get(name)
        if m is None:
            return
        for s, v in ((self.E, m.E), (self.nu, m.nu), (self.sy, m.yield_strength), (self.rho, m.density),
                     (self.su, m.ultimate_strength)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)

    def _edited(self):
        m = MATERIAL_LIBRARY.get(self.lib.currentText())
        if m and (m.E, m.nu, m.yield_strength, m.density, m.ultimate_strength) != self._values():
            self.lib.blockSignals(True)
            self.lib.setCurrentText(CUSTOM)
            self.lib.blockSignals(False)

    def _values(self):
        return (self.E.value(), self.nu.value(), self.sy.value(), self.rho.value(), self.su.value())

    def material(self) -> Material:
        name = self.lib.currentText()
        if name == CUSTOM:
            name = self._custom_name if self._custom_name not in MATERIAL_LIBRARY else "Custom material"
        return Material(name, *self._values())


class MeshDialog(QDialog):
    def __init__(self, parent, study, body):
        super().__init__(parent)
        self.setWindowTitle(f"Mesh and Run - {study.name}")
        self.setWindowIcon(icon("mesh"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.auto = auto_mesh_size(body)
        self.size = spin(study.mesh_size or self.auto, 1e-4, 1e6, 3, " mm", 0.5)
        form.addRow("Element size", self.size)
        form.addRow("", QLabel(f"(automatic: {self.auto:.3g} mm - smaller is more accurate but slower)"))
        self.order = QComboBox()
        self.order.addItem("Quadratic tetrahedra (10-node, recommended)", 2)
        self.order.addItem("Linear tetrahedra (4-node, fast, stiff)", 1)
        self.order.setCurrentIndex(0 if study.element_order == 2 else 1)
        form.addRow("Elements", self.order)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Run")
        bb.button(QDialogButtonBox.Ok).setIcon(icon("run"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        size = self.size.value()
        return (0.0 if abs(size - self.auto) < 1e-9 else size), self.order.currentData()


class BdfExportWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, snapshot, study_id, path):
        super().__init__()
        self.snapshot, self.study_id, self.path = snapshot, study_id, path

    @Slot()
    def run(self):
        from ..core.document import Document
        from ..fea.nastran import export_bdf
        try:
            doc = Document.from_dict(self.snapshot)
            self.progress.emit("Rebuilding export snapshot...")
            doc.regenerate()
            errors = [f"{f.name}: {f.error}" for f in doc.features if f.error]
            if errors:
                raise ValueError("Fix model rebuild errors before exporting:\n" + "\n".join(errors))
            self.finished.emit(export_bdf(doc.study(self.study_id), doc, self.path, self.progress.emit))
        except Exception as e:
            self.failed.emit(str(e))


class AnalysisWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, study, doc):
        super().__init__()
        self.study, self.doc = study, doc

    @Slot()
    def run(self):
        from ..fea.analysis import AnalysisError, run_study
        from ..fea.mesher import MeshError
        try:
            res = run_study(self.study, self.doc, progress=self.progress.emit)
            self.finished.emit(res)
        except (AnalysisError, MeshError) as e:
            self.failed.emit(str(e))
        except Exception as e:  # unexpected - show details
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


ABOUT_HTML = f"""
<h2>{APP_NAME} {__version__}</h2>
<p>Integrated parametric CAD modelling and finite element analysis.</p>
<p><b>Modelling:</b> constraint-based sketches (SolidWorks tool set, relations, Smart Dimension),
extruded boss/cut, STEP / IGES / STL / OBJ / glTF / VRML / BREP exchange.<br>
<b>Simulation:</b> linear static analysis with quadratic tetrahedra, fixed faces, point forces.</p>
<p>Built on OpenCASCADE (geometry kernel), Gmsh (meshing), NumPy/SciPy (solver),
VTK and Qt (graphics and UI).</p>
"""

CONTROLS_HTML = """
<h3>Mouse</h3>
<table cellpadding=3>
<tr><td><b>Left click</b></td><td>Select / place sketch point</td></tr>
<tr><td><b>Left drag</b></td><td>Drag geometry or box-select (sketch); move components (assembly)</td></tr>
<tr><td><b>Middle drag</b></td><td>Rotate around the model point nearest the cursor at drag start &nbsp; (Ctrl/Shift + middle: pan)</td></tr>
<tr><td><b>Right drag</b></td><td>Pan</td></tr>
<tr><td><b>Right click</b></td><td>End the current chain / step (sketch) &middot; context menu</td></tr>
<tr><td><b>Double click</b></td><td>Edit a dimension &middot; finish a spline</td></tr>
<tr><td><b>Wheel</b></td><td>Zoom about the cursor</td></tr>
</table>
<h3>Command line (AutoCAD style)</h3>
<p>Just start typing anywhere: a command box opens at the cursor. Type a command or alias
(L line, C circle, REC rectangle, A arc, SL slot, POL polygon, SPL spline, D smart dimension,
TR trim, EX extend, O offset, MI mirror, F fillet, M move, CO copy, RO rotate, AR pattern,
H/V horizontal/vertical, EXT extrude, CUT, Z zoom fit, U undo ...) and press Enter or Space.</p>
<p><b>Space / Enter repeats the previous command</b>, however it was started (toolbar, menu,
right-click or typed). In a sketch, Space first ends the current chain, then ends the tool,
and then repeats the last command, as in AutoCAD.</p>
<p>While drawing, type values instead of clicking:
<code>x,y</code> absolute &middot; <code>@dx,dy</code> relative &middot;
<code>@length&lt;angle</code> polar &middot; <code>length</code> along the cursor direction.
Expressions and units work: <code>2*12.5</code>, <code>1in</code>.</p>
<p><b>Dynamic input</b> (rectangles, lines, circles): after the first point just type a number -
fields open at the cursor. Rectangle: width <b>Tab</b> height, then <b>Enter</b> or click
anywhere (the cursor picks the side; a negative value goes the other way). Line: length Tab
angle. Circle: diameter. Typed values become driving dimensions (option in the sketch panel).</p>
<p><b>Shading</b>: closed areas of the sketch are shaded blue - those are what Extrude can use. An outline that stays white is not closed (open ends show as red dots).</p>
<p><b>Snaps</b> while sketching (marker + label at the cursor): endpoint &#9633;, midpoint
&#9651;, centre &#9675;, quadrant &#9671;, intersection &times;, nearest, and the model's own
edges, vertices and hole centres on the sketch plane (teal).</p>
<h3>Keyboard</h3>
<table cellpadding=3>
<tr><td><b>Space / Enter</b></td><td>Finish the step &middot; end the tool &middot; accept the command &middot; with nothing active: repeat the previous command</td></tr>
<tr><td><b>Esc</b></td><td>Cancel the step, then return to Select</td></tr>
<tr><td><b>Delete</b></td><td>Delete the selection</td></tr>
<tr><td><b>Ctrl+Z / Ctrl+Y</b></td><td>Undo / Redo (inside a sketch too)</td></tr>
<tr><td><b>Ctrl+1..4</b></td><td>Front / Top / Right / Isometric view</td></tr>
<tr><td><b>Numpad 1</b></td><td>Isometric (again: the next isometric corner)</td></tr>
<tr><td><b>Numpad 3</b></td><td>Front (again: back)</td></tr>
<tr><td><b>Numpad 4 / 6</b></td><td>Left / right view (again: turn 45&deg; about the vertical)</td></tr>
<tr><td><b>Numpad 8 / 2</b></td><td>Top / bottom view (again: tip 45&deg; about the horizontal)</td></tr>
<tr><td><b>Numpad 7 / 9</b></td><td>Roll the view 90&deg; counter-clockwise / clockwise</td></tr>
<tr><td><b>Numpad 5</b></td><td>View Selector: click a face, edge or corner of the cube</td></tr>
<tr><td><b>Numpad 0 / .</b></td><td>Normal To (selected face / sketch plane) / zoom to fit</td></tr>
<tr><td></td><td><i>While a drawing step waits for a value, numpad digits type numbers.</i></td></tr>
<tr><td><b>Ctrl+K</b></td><td>Open the command line</td></tr>
</table>
"""
