"""Display-only world-space clipping; original model topology remains untouched."""
import numpy as np
import pyvista as pv
import vtk
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QFrame, QLabel, QPushButton, QSlider, QVBoxLayout, QHBoxLayout, QScrollArea)


class SectionDisplay:
    def __init__(self, viewer):
        self.viewer = viewer
        self.plane = None
        self.capped = True
        self.caps = []
        self._signature = None
        self._actors = []
        self.active = False
        self.control = None

    def set(self, origin=None, normal=(0, 0, -1), capped=True):
        if origin is None:
            self.plane = None
        else:
            origin, normal = np.asarray(origin, float), np.asarray(normal, float)
            if origin.shape != (3,) or normal.shape != (3,) or not np.all(np.isfinite([origin, normal])) or np.linalg.norm(normal) == 0:
                raise ValueError('Section plane needs a finite origin and nonzero normal')
            self.plane = vtk.vtkPlane()
            self.plane.SetOrigin(*origin)
            self.plane.SetNormal(*(normal / np.linalg.norm(normal)))
        self.capped = capped
        self._signature = None
        self.viewer.render()

    def sources(self):
        v = self.viewer
        if v._body_actor is not None:
            yield v._body_actor, v._body_mesh, v.plotter.actors.get('body_edges')
        yield from v._asm.values()

    def sync(self):
        v = self.viewer
        active = self.plane is not None and v.mode != 'sketch' and not v.results_visible
        sources = list(self.sources())
        signature = (active, self.capped, tuple((id(a), id(m), a.GetMatrix().GetMTime(),
                     tuple(a.GetMatrix().GetElement(i, j) for i in range(4) for j in range(4)),
                     a.GetVisibility(), a.GetProperty().GetOpacity()) for a, m, e in sources))
        if signature == self._signature:
            return
        self._signature, self.active = signature, active
        for a in self._actors:
            a.GetMapper().RemoveAllClippingPlanes()
        self._actors = []
        for a in self.caps:
            v.renderer.RemoveActor(a)
        self.caps = []
        if not active:
            return
        for actor, mesh, edges in sources:
            for a in (actor, edges):
                if a is not None:
                    a.GetMapper().AddClippingPlane(self.plane)
                    self._actors.append(a)
            if not self.capped or not actor.GetVisibility():
                continue
            world = pv.PolyData(v._actor_points(actor, mesh.points), mesh.faces)
            cutter = vtk.vtkCutter()
            cutter.SetInputData(world)
            cutter.SetCutFunction(self.plane)
            clean = vtk.vtkCleanPolyData()
            clean.SetInputConnection(cutter.GetOutputPort())
            triangles = vtk.vtkContourTriangulator()
            triangles.SetInputConnection(clean.GetOutputPort())
            triangles.Update()
            if not triangles.GetOutput().GetNumberOfCells():
                continue
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(triangles.GetOutput())
            mapper.ScalarVisibilityOff()
            cap = vtk.vtkActor()
            cap.SetMapper(mapper)
            cap.GetProperty().SetColor(.83, .78, .65)
            cap.GetProperty().SetOpacity(actor.GetProperty().GetOpacity())
            cap.GetProperty().SetAmbient(.65)
            cap.GetProperty().SetDiffuse(.35)
            v.renderer.AddActor(cap)
            self.caps.append(cap)

    def contains(self, points):
        points = np.asarray(points)
        if not self.active:
            return np.ones(points.shape[:-1], dtype=bool)
        return (points - self.plane.GetOrigin()) @ np.array(self.plane.GetNormal()) >= -1e-8

    def lines(self, polyline):
        """Clip each segment, without joining disconnected retained pieces."""
        if not self.active:
            yield polyline
            return
        p = np.asarray(polyline)
        d = (p - self.plane.GetOrigin()) @ np.array(self.plane.GetNormal())
        for a, b, da, db in zip(p[:-1], p[1:], d[:-1], d[1:]):
            if da < 0 and db < 0:
                continue
            if da < 0:
                a = a + (b - a) * da / (da - db)
            elif db < 0:
                b = a + (b - a) * da / (da - db)
            yield np.array([a, b])


class SectionWindow(QFrame):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle('Section View')
        from .icons import icon
        self.setWindowIcon(icon('section'))
        self.resize(360, 270)
        layout = QVBoxLayout(self)
        title=QLabel('Section View'); title.setStyleSheet('font-size:15px;font-weight:600;')
        layout.addWidget(title)
        buttons=QHBoxLayout()
        keep=QPushButton('✓ Keep section'); keep.clicked.connect(self.accept)
        cancel=QPushButton('✕ Close'); cancel.clicked.connect(self.close)
        buttons.addWidget(keep); buttons.addWidget(cancel); layout.addLayout(buttons)
        self._previous_page=main.tree
        self.panel=QScrollArea(); self.panel.setWidgetResizable(True)
        self.panel.setFrameShape(QFrame.NoFrame); self.panel.setWidget(self)
        main.left.addWidget(self.panel)
        main.viewer.section.control=self
        self.model_center=np.zeros(3)
        self.angles=[]
        hint = QLabel('Cut the display to inspect the interior.\nThe model and exported geometry stay unchanged.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.plane = QComboBox()
        self.awaiting_face = False
        form.addRow('Section plane', self.plane)
        presets=QHBoxLayout()
        for index,label in enumerate(('Front','Top','Right')):
            button=QPushButton(label); button.clicked.connect(lambda checked=False,i=index:self.plane.setCurrentIndex(i))
            presets.addWidget(button)
        layout.addLayout(presets)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1e9, 1e9)
        self.offset.setDecimals(3)
        self.offset.setSuffix(' mm')
        self.position_label = QLabel('Position')
        self.position_label.setMinimumWidth(self.position_label.fontMetrics().horizontalAdvance('Offset from plane'))
        form.addRow(self.position_label, self.offset)
        layout.addLayout(form)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        layout.addWidget(self.slider)
        rotations=QFormLayout()
        for label in ('X rotation','Y rotation'):
            field=QDoubleSpinBox(); field.setRange(-3600,3600); field.setDecimals(2); field.setSuffix('°')
            field.setSingleStep(5); field.valueChanged.connect(self.apply)
            self.angles.append(field); rotations.addRow(label,field)
        layout.addLayout(rotations)
        self.handles=QCheckBox('Show move / rotate handles'); self.handles.setChecked(True)
        self.handles.toggled.connect(lambda _:self.main.viewer.render())
        layout.addWidget(self.handles)
        help_text=QLabel('Drag the blue arrow to move the cut. Drag the red or green ring to rotate. Middle-drag still rotates the model.')
        help_text.setWordWrap(True); layout.addWidget(help_text)
        reset=QPushButton('Reset rotation'); reset.clicked.connect(self.reset_rotation); layout.addWidget(reset)
        self.flip = QCheckBox('Flip direction')
        self.cap = QCheckBox('Show section caps')
        self.cap.setChecked(True)
        layout.addWidget(self.flip)
        layout.addWidget(self.cap)
        hint = QLabel('Caps are visual only; select original faces and edges to measure.\nSectioning pauses while editing a sketch or viewing analysis results.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.pick_face = QPushButton('Pick planar face')
        self.pick_face.setCheckable(True)
        self.pick_face.clicked.connect(self.begin_pick)
        layout.addWidget(self.pick_face)
        self.center = QPushButton('Centre on model')
        self.center.clicked.connect(self.center_plane)
        layout.addWidget(self.center)
        close = QPushButton('Close section view')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self.populate_planes()
        self.plane.currentIndexChanged.connect(self.select_plane)
        self.offset.valueChanged.connect(self.apply)
        self.slider.valueChanged.connect(self.slide)
        self.flip.toggled.connect(self.apply)
        self.cap.toggled.connect(self.apply)
        self._range = (0., 1.)
        from .section_manipulator import SectionManipulator
        self.manipulator=SectionManipulator(self)
        layout.addStretch(1)

    def open(self):
        if self.main.left.currentWidget() is not self.panel:
            self._previous_page=self.main.left.currentWidget()
        retained=self.main.viewer.section.plane is not None
        if not retained: self.populate_planes()
        self.main.left.setCurrentWidget(self.panel)
        self.show()
        if not retained: self.select_plane()
        else: self.apply()

    def accept(self):
        self.awaiting_face=False; self.pick_face.setChecked(False)
        self.main.left.setCurrentWidget(self._previous_page)
        self.main.viewer.render()

    def reset_rotation(self):
        for field in self.angles:
            field.blockSignals(True); field.setValue(0); field.blockSignals(False)
        self.apply()

    def rotation_frame(self):
        from .viewer import _rot
        normal=np.asarray(self.plane.currentData()['normal'],float)
        seed=np.array([1.,0,0]) if abs(normal[0])<.9 else np.array([0.,1,0])
        x=seed-normal*np.dot(seed,normal); x/=np.linalg.norm(x)
        y=np.cross(normal,x)
        rotation=_rot(y,self.angles[1].value()) @ _rot(x,self.angles[0].value())
        return rotation @ np.column_stack([x,y,normal])

    def populate_planes(self):
        self.plane.blockSignals(True)
        self.plane.clear()
        for label, normal in [('Front (XY)', (0, 0, 1)), ('Top (XZ)', (0, 1, 0)), ('Right (YZ)', (1, 0, 0))]:
            self.plane.addItem(label, {'origin': [0, 0, 0], 'normal': list(normal), 'standard': True})
        if hasattr(self.main, 'asm'):
            for comp in self.main.asm.components:
                if comp.doc is None:
                    continue
                for feature in comp.doc.features:
                    plane = getattr(feature, 'result_plane', None)
                    if feature.TYPE == 'ref_plane' and not feature.suppressed and plane is not None:
                        self.plane.addItem(f'{comp.name} / {feature.name}', {
                            'origin': comp.pose.point(plane.origin).tolist(),
                            'normal': (comp.pose.R @ plane.normal).tolist(), 'standard': False})
        else:
            for name, plane in self.main.ref_planes().items():
                self.plane.addItem(name, {'origin': plane.origin.tolist(), 'normal': plane.normal.tolist(), 'standard': False})
        self.plane.blockSignals(False)

    def basis(self):
        data = self.plane.currentData()
        origin=np.asarray(data['origin'],float); normal=np.asarray(data['normal'],float)
        frame=self.rotation_frame(); rotated=frame[:,2]
        distance=np.dot(self.model_center-origin,normal)
        # Rotate about the model centre while keeping zero-angle origins compatible.
        in_plane=self.model_center-origin-normal*distance
        seed=np.array([1.,0,0]) if abs(normal[0])<.9 else np.array([0.,1,0])
        x=seed-normal*np.dot(seed,normal); x/=np.linalg.norm(x)
        base=np.column_stack([x,np.cross(normal,x),normal])
        rotation=frame @ base.T
        return origin + in_plane-rotation@in_plane+(normal-rotated)*distance, rotated

    def update_range(self):
        origin, normal = self.basis()
        clouds = [(self.main.viewer._actor_points(a, m.points) - origin) @ normal
                  for a, m, e in self.main.viewer.section.sources() if a.GetVisibility()]
        low = min((float(p.min()) for p in clouds), default=-50.)
        high = max((float(p.max()) for p in clouds), default=50.)
        self._range = (low, max(high, low + .001))
        return low, high

    def select_plane(self, *_):
        self.awaiting_face = False
        self.pick_face.setChecked(False)
        clouds=[self.main.viewer._actor_points(a,m.points) for a,m,e in self.main.viewer.section.sources() if a.GetVisibility()]
        if clouds:
            points=np.vstack(clouds); self.model_center=(points.min(axis=0)+points.max(axis=0))/2
        for field in self.angles:
            field.blockSignals(True); field.setValue(0); field.blockSignals(False)
        low, high = self.update_range()
        standard = self.plane.currentData()['standard']
        self.position_label.setText('Position' if standard else 'Offset from plane')
        self.offset.setValue((low + high) / 2 if standard else 0.)
        self.apply()

    def center_plane(self, *_):
        low, high = self.update_range()
        self.offset.setValue((low + high) / 2)
        self.apply()

    def begin_pick(self, checked):
        if checked and getattr(self.main, 'command', None) is not None:
            self.pick_face.setChecked(False)
            self.main.status('Finish the modelling command before choosing a section face')
            return
        self.awaiting_face = checked
        if checked:
            if getattr(self.main, 'mate_panel', False):
                self.main.close_mate_panel()
            if hasattr(self.main, '_finish_animation'):
                self.main._finish_animation()
            for name in ('measure_win', 'clearance_win', 'interference_win'):
                window = getattr(self.main, name, None)
                if window is not None:
                    window.close()
            self.main.status('Section View: click an original planar face; section caps are visual only')
        else:
            self.main.status('Section face selection cancelled')

    def consume_pick(self, info):
        if not self.isVisible() or not self.awaiting_face:
            return False
        face = None
        if hasattr(self.main, 'asm') and info.kind == 'asm_face':
            comp = self.main.asm.component(info.component)
            if comp is not None and info.face is not None and 0 <= info.face < len(comp.faces):
                face = comp.faces[info.face]
                origin = comp.pose.point(face.centroid)
                normal = comp.pose.R @ np.asarray(face.normal) if face.is_planar else None
        elif not hasattr(self.main, 'asm') and info.kind == 'face':
            if info.face is not None and 0 <= info.face < len(self.main.doc.faces):
                face = self.main.doc.faces[info.face]
                origin, normal = face.centroid, face.normal
        if face is None or not face.is_planar:
            self.main.status('Choose an original planar face, not a curved face or section cap')
            return True
        index = self.plane.findText('Picked face')
        if index >= 0:
            self.plane.removeItem(index)
        self.plane.addItem('Picked face', {'origin': np.asarray(origin).tolist(),
                                         'normal': np.asarray(normal).tolist(), 'standard': False})
        self.plane.setCurrentIndex(self.plane.count() - 1)
        self.main.status('Section plane captured from face; offset moves it along the face normal')
        return True

    def slide(self, value):
        low, high = self._range
        self.offset.setValue(low + (high - low) * value / 1000)

    def apply(self, *_):
        if not self.isVisible():
            return
        low, high = self.update_range()
        self.slider.blockSignals(True)
        self.slider.setValue(round(1000 * (self.offset.value() - low) / (high - low)))
        self.slider.blockSignals(False)
        origin, normal = self.basis()
        origin = origin + normal * self.offset.value()
        normal = normal if self.flip.isChecked() else -normal
        self.main.viewer.section.set(origin, normal, self.cap.isChecked())

    def closeEvent(self, event):
        self.awaiting_face = False
        self.pick_face.setChecked(False)
        self.main.a_section.setChecked(False)
        self.main.viewer.section.set()
        if self.main.left.currentWidget() is self.panel:
            self.main.left.setCurrentWidget(self._previous_page)
        super().closeEvent(event)
