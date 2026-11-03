"""Measure (SolidWorks-style): click faces, edges and vertices; the window lists their sizes
and, for two of them, the distance, centre distance and angle. Click an entity again to drop
it, click empty space to start over, Esc or Close to leave."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QListWidget, QPushButton, QSpinBox, QTextBrowser, QVBoxLayout)

from ..core import geometry, measure
from .icons import icon
from .viewer import SELECT_RGB


class MeasureWindow(QFrame):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.setWindowTitle("Evaluate — Measure")
        self.setWindowIcon(icon("measure"))
        self.refs: list[tuple] = []
        self.result: dict | None = None
        self._vertices = None
        self._assembly_body = None
        self._assembly_tess = None
        self.face_map = {}
        self.face_lookup = {}
        self._dimensions = False
        self.history = []
        lay = QVBoxLayout(self)
        self.hint = QLabel("Click faces, edges or vertices to measure them.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#4a5a70;")
        lay.addWidget(self.hint)
        self.selection_mode = QComboBox()
        for label, value in [('Automatic selection', None), ('Faces', 'face'), ('Edges', 'edge'),
                             ('Vertices', 'vertex'), ('Point on surface', 'point')]:
            self.selection_mode.addItem(label, value)
        self.distance_mode = QComboBox()
        for label, value in [('Minimum distance', 'minimum'), ('Centre / axis distance', 'center'),
                             ('Maximum (points / full circles)', 'maximum')]:
            self.distance_mode.addItem(label, value)
        row = QHBoxLayout()
        row.addWidget(self.selection_mode)
        row.addWidget(self.distance_mode)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.units = QComboBox()
        self.units.addItems(['mm', 'cm', 'm', 'in'])
        self.precision = QSpinBox()
        self.precision.setRange(0, 8)
        self.precision.setValue(4)
        self.precision.setPrefix('Decimals: ')
        self.xyz = QCheckBox('Show XYZ differences')
        self.xyz.setChecked(True)
        for widget in (self.units, self.precision, self.xyz):
            row.addWidget(widget)
        lay.addLayout(row)
        self.selections = QListWidget()
        self.selections.setMaximumHeight(85)
        self.selections.setToolTip('Double-click an entry to remove it. Three points measure the angle at the second point.')
        self.selections.itemDoubleClicked.connect(self.remove_selection)
        lay.addWidget(self.selections)
        self.text = QTextBrowser()
        self.text.setMinimumSize(380, 170)
        self.text.setStyleSheet("font: 10pt 'Consolas';")
        lay.addWidget(self.text)
        row = QHBoxLayout()
        self.bounds_button = QPushButton('Bounding Box')
        self.bounds_button.clicked.connect(self.show_dimensions)
        self.copy_button = QPushButton('Copy')
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        self.keep_button = QPushButton('Keep in history')
        self.keep_button.clicked.connect(self.keep_result)
        for widget in (self.bounds_button, self.copy_button, self.keep_button):
            row.addWidget(widget)
        lay.addLayout(row)
        self.history_list = QComboBox()
        self.history_list.addItem('Measurement history (this session)')
        self.history_list.activated.connect(self.show_history)
        lay.addWidget(self.history_list)
        self.units.currentIndexChanged.connect(self.update_result)
        self.precision.valueChanged.connect(self.update_result)
        self.xyz.toggled.connect(self.update_result)
        self.distance_mode.currentIndexChanged.connect(self.update_result)
        row = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addStretch(1)
        row.addWidget(clear)
        row.addWidget(close)
        lay.addLayout(row)

    # ---- opening ------------------------------------------------------------------------
    def open(self):
        self.clear()
        self.adjustSize()
        v = self.main.viewer
        g = v.mapToGlobal(QPoint(max(0, v.width() - self.width() - 12), 12))
        available = v.screen().availableGeometry()
        g.setX(max(available.left(), min(g.x(), available.right() - self.width())))
        g.setY(max(available.top(), min(g.y(), available.bottom() - self.height())))
        self.move(g)
        self.show()
        self.raise_()

    def vertices(self):
        body = self.body
        if self._vertices is None and body is not None:
            self._vertices = geometry.vertices(body)
        return self._vertices

    def model_changed(self):
        self._vertices = None
        self._assembly_body = None
        self._assembly_tess = None
        self.face_map = {}
        self.face_lookup = {}
        self.clear()

    @property
    def assembly(self):
        return hasattr(self.main, 'asm')

    @property
    def body(self):
        if not self.assembly:
            return self.main.doc.body
        if self._assembly_body is None:
            shapes = []
            offset = 0
            for comp in self.main.asm.components:
                if not self.main.visibility.visible(comp.id):
                    continue
                if comp.shape is None:
                    continue
                shapes.append(comp.world_shape())
                for index in range(len(comp.faces)):
                    self.face_map[offset + index] = (comp.id, index)
                    self.face_lookup[(comp.id, index)] = offset + index
                offset += len(comp.faces)
            if shapes:
                self._assembly_body = geometry.compound(shapes)
                self._assembly_tess = geometry.tessellate(self._assembly_body)
        return self._assembly_body

    # ---- picking --------------------------------------------------------------------------
    def pick(self, x, y, hit=None) -> bool:
        """Viewer click at display (x, y). Returns True (the click is consumed)."""
        if self.body is None:
            return True
        kind = self.selection_mode.currentData()
        if kind == 'point':
            selected = hit if hit and hit[0] == 'face' and len(hit) > 2 else None
            info = self.main.viewer.pick(x, y) if selected is None else None
            hit = None
            if selected is not None or info.kind in ('face', 'asm_face'):
                index = (selected[1] if selected else self.face_lookup[(info.component, info.face)]
                         if self.assembly else info.face)
                clicked = selected[2] if selected else info.point
                # Project the displayed triangle hit onto the exact CAD face.
                _, _, point = geometry.min_distance(geometry.point_shape(clicked),
                                                    geometry.subshape(self.body, 'face', index))
                hit = ('point', tuple(point))
        elif hit is None or (kind is not None and hit[0] != kind):
            hit = self.main.viewer.pick_topology(
                x, y, self.vertices(), kinds={kind} if kind else None,
                tessellation=self._assembly_tess, face_lookup=self.face_lookup)
        if hit is None:
            self.clear()
            return True
        ref = (hit[0], hit[1])
        self._dimensions = False
        if ref in self.refs:
            self.refs.remove(ref)
        else:
            self.refs.append(ref)
        self.update_result()
        return True

    def add(self, kind, index):
        """Select an entity (scripts / tests)."""
        self._dimensions = False
        if kind == 'point':
            index = tuple(index)
        if (kind, index) not in self.refs:
            self.refs.append((kind, index))
        self.update_result()

    def clear(self):
        self.refs = []
        self.result = None
        self._dimensions = False
        self.selections.clear()
        self.text.setHtml("")
        self._draw()

    def update_result(self):
        if self._dimensions and self.assembly:
            missing = [c.name for c in self.main.asm.components if self.main.visibility.visible(c.id)
                       and (c.shape is None or c.load_error)]
            if missing:
                self.result = None
                self.text.setPlainText('Bounding box incomplete — unavailable visible geometry: ' + ', '.join(missing))
                self._draw()
                return
        body = self.body
        self.selections.clear()
        for kind, index in self.refs:
            label = f'{kind.title()} {index + 1}' if kind != 'point' else f'Point {index}'
            if self.assembly and kind == 'face':
                cid, local = self.face_map[index]
                label = f'{self.main.asm.component(cid).name}: Face {local + 1}'
            self.selections.addItem(label)
        if (not self.refs and not self._dimensions) or body is None:
            self.clear()
            return
        try:
            if self._dimensions:
                self.result = measure.model_dimensions(body)
                lines = ['Bounding box — visible components, world axes' if self.assembly else 'Bounding box — world axes']
                lines += [f'{axis}: {self.format_length(value)}' for axis, value in zip('XYZ', self.result['size'])]
                lines.append(f'Diagonal: {self.format_length(self.result["diagonal"])}')
            else:
                self.result = measure.measure(body, self.refs, mode=self.distance_mode.currentData())
                lines = measure.report(self.result, unit=self.units.currentText(),
                                       decimals=self.precision.value(), show_xyz=self.xyz.isChecked())
        except Exception as e:  # noqa: BLE001 - show, don't crash
            self.result = None
            self.text.setPlainText(f"Cannot measure: {e}")
            self._draw()
            return
        self.text.setPlainText('\n'.join(lines))
        self.main.status(" | ".join(lines[-3:]))
        self._draw()

    def format_length(self, value):
        return f'{value * measure.UNIT_FACTORS[self.units.currentText()]:.{self.precision.value()}f} {self.units.currentText()}'

    def remove_selection(self, item):
        self.refs.pop(self.selections.row(item))
        self.update_result()

    def show_dimensions(self):
        self.refs = []
        self._dimensions = True
        self.update_result()

    def keep_result(self):
        if self.result is not None:
            self.history.append(self.text.toPlainText())
            self.history_list.addItem(f'{len(self.history)}: {self.history[-1].splitlines()[-1]}')

    def show_history(self, index):
        if index > 0:
            self.clear()
            self.text.setPlainText('Saved snapshot (not live)\n' + self.history[index - 1])

    # ---- display ----------------------------------------------------------------------------
    def _draw(self):
        v = self.main.viewer
        tess = self._assembly_tess if self.assembly else getattr(v, "_tess", None)
        polys, pts, faces = [], [], {}
        for kind, i in self.refs:
            if kind == "edge" and tess is not None:
                polys += [pl for pl, eid in zip(tess.edge_polylines, tess.edge_ids) if eid == i]
            elif kind == "vertex" and self.vertices() is not None:
                pts.append(self.vertices()[i])
            elif kind == "face":
                faces[i] = SELECT_RGB
            elif kind == 'point':
                pts.append(i)
        if self.assembly:
            v.set_assembly_overrides({self.face_map[i]: color for i, color in faces.items()})
        else:
            v.set_face_colors(faces)
        v.set_overlay("measure_sel", polys, pts, "#00c8ff", 4.0)
        r = self.result
        if r is not None and "measurement" in r and r["measurement"] > 1e-9:
            pa, pb = np.asarray(r["measurement_point_a"]), np.asarray(r["measurement_point_b"])
            v.set_overlay("measure_dist", [np.array([pa, pb])], [pa, pb], "#ff7a00", 2.5,
                          [((pa + pb) / 2, self.format_length(r['measurement']))])
        else:
            v.set_overlay("measure_dist")
        if r is not None and self._dimensions:
            from itertools import product
            corners = np.array([np.where(bits, r['maximum'], r['minimum'])
                                for bits in product((0, 1), repeat=3)])
            lines = [corners[[i, j]] for i in range(8) for j in range(i+1, 8)
                     if i ^ j in (1, 2, 4)]
            labels = [(corners[[0, j]].mean(axis=0), f'{axis}: {self.format_length(size)}')
                      for axis, j, size in zip('XYZ', (4, 2, 1), r['size'])]
            v.set_overlay('measure_bounds', lines, color='#ff7a00', width=2, labels=labels)
        else:
            v.set_overlay('measure_bounds')

    def closeEvent(self, e):
        self.refs = []
        self.result = None
        v = self.main.viewer
        v.set_overlay("measure_sel")
        v.set_overlay("measure_dist")
        v.set_overlay('measure_bounds')
        if self.assembly:
            self.main._update_colors()
        else:
            self.main.refresh_view()
        super().closeEvent(e)
