"""Evaluate mass properties and assign physical materials without changing FEA studies."""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                               QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QSpinBox)

from ..core import mass_properties as mass
from ..fea.study import MATERIAL_LIBRARY


class MassPropertiesWindow(QFrame):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.setWindowTitle('Evaluate — Mass Properties')
        self.resize(690, 660)
        self.result = None
        self.rows = []
        layout = QVBoxLayout(self)
        hint = QLabel('Assign physical materials below. FEA study materials are independent.\n'
                      'Assembly overrides are saved in the assembly; source part files are unchanged.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Part / component', 'Physical material', 'Density (kg/m³)'])
        self.table.setMaximumHeight(190)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        bar = QHBoxLayout()
        self.apply_button = QPushButton('Apply materials')
        self.apply_button.clicked.connect(self.apply_materials)
        refresh = QPushButton('Recalculate')
        refresh.clicked.connect(self.recalculate)
        self.marker = QCheckBox('Show centre of gravity')
        self.marker.setChecked(True)
        self.marker.toggled.connect(self.draw)
        for w in (self.apply_button, refresh, self.marker):
            bar.addWidget(w)
        layout.addLayout(bar)
        self.axes = QCheckBox('Show principal axes')
        self.axes.toggled.connect(self.draw)
        layout.addWidget(self.axes)
        units = QHBoxLayout()
        units.addWidget(QLabel('Report units:'))
        self.length_unit = QComboBox(); self.length_unit.addItems(['mm', 'cm', 'm', 'in'])
        self.mass_unit = QComboBox(); self.mass_unit.addItems(['kg', 'g', 't', 'lb'])
        self.precision = QSpinBox(); self.precision.setRange(3, 12); self.precision.setValue(8)
        self.precision.setPrefix('Significant digits: ')
        for widget in (self.length_unit, self.mass_unit, self.precision):
            units.addWidget(widget)
        layout.addLayout(units)
        self.length_unit.currentIndexChanged.connect(self.update_report)
        self.mass_unit.currentIndexChanged.connect(self.update_report)
        self.precision.valueChanged.connect(self.update_report)
        self.text = QTextBrowser()
        self.text.setStyleSheet("font: 10pt 'Consolas';")
        layout.addWidget(self.text)
        bar = QHBoxLayout()
        copy = QPushButton('Copy report')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        bar.addWidget(copy); bar.addStretch(); bar.addWidget(close)
        layout.addLayout(bar)

    @property
    def assembly(self):
        return hasattr(self.main, 'asm')

    def open(self):
        self.show()
        self.raise_()
        self.model_changed()

    def model_changed(self):
        self.result = None
        self.draw()
        if not self.isVisible():
            return
        targets = ([(c.name, c, c.doc.mass_material if c.doc else None) for c in self.main.asm.components]
                   if self.assembly else [('Part', self.main.doc, None)])
        # Qt deletes replaced cell widgets later; hide them immediately so refresh/undo
        # cannot leave the previous density editor painted over another table cell.
        for row in range(self.table.rowCount()):
            for column in (1, 2):
                widget = self.table.cellWidget(row, column)
                if widget is not None:
                    widget.hide()
        self.table.clearContents()
        self.rows = []
        self.table.setRowCount(len(targets))
        for i, (name, target, inherited) in enumerate(targets):
            label = QTableWidgetItem(name)
            label.setFlags(label.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, label)
            combo, density = QComboBox(), QDoubleSpinBox()
            combo.addItem('Use source part' if self.assembly else 'Unassigned', None)
            combo.addItem('Custom', 'custom')
            for key in MATERIAL_LIBRARY:
                combo.addItem(key, key)
            density.setRange(0, 1e9); density.setDecimals(6)
            value = target.mass_material
            if value:
                known = MATERIAL_LIBRARY.get(value['name'])
                combo.setCurrentIndex(combo.findData(value['name']) if known and known.density == value['density'] else 1)
            density.setValue((value or inherited or {}).get('density', 0))
            density.setEnabled(combo.currentData() == 'custom')
            def choose(_, combo=combo, density=density, inherited=inherited):
                key = combo.currentData()
                density.setEnabled(key == 'custom')
                if key in MATERIAL_LIBRARY:
                    density.setValue(MATERIAL_LIBRARY[key].density)
                elif key is None:
                    density.setValue((inherited or {}).get('density', 0))
            combo.currentIndexChanged.connect(choose)
            self.table.setCellWidget(i, 1, combo); self.table.setCellWidget(i, 2, density)
            self.rows.append((target, combo, density))
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(1, 255)
        self.recalculate()

    def apply_materials(self):
        try:
            values = [None if combo.currentData() is None else
                      mass.material('Custom' if combo.currentData() == 'custom' else combo.currentData(), density.value())
                      for target, combo, density in self.rows]
        except ValueError as error:
            self.text.setPlainText(str(error)); return
        if any(target.mass_material != value for (target, _, _), value in zip(self.rows, values)):
            self.main.checkpoint()
            for (target, _, _), value in zip(self.rows, values):
                target.mass_material = value
            if not self.assembly:
                self.main._update_actions()
        self.recalculate()

    def recalculate(self):
        try:
            self.result = (mass.assembly(self.main.asm) if self.assembly else
                           mass.part(self.main.doc.body, self.main.doc.mass_material))
            self.update_report()
        except Exception as error:
            self.result = None
            self.text.setPlainText(f'Cannot calculate mass properties: {error}')
        self.draw()

    def update_report(self, *_):
        if self.result is not None:
            self.text.setPlainText(mass.report(self.result, length_unit=self.length_unit.currentText(),
                                               mass_unit=self.mass_unit.currentText(), precision=self.precision.value()))

    def draw(self):
        center = self.result.get('center_of_gravity') if self.result else None
        if center is not None and self.marker.isChecked() and self.isVisible():
            self.main.viewer.set_overlay('mass_cg', points=[center], color='#e57b18',
                                         labels=[(np.asarray(center), 'CG')])
        else:
            self.main.viewer.set_overlay('mass_cg')

        show_axes = center is not None and self.axes.isChecked() and self.isVisible()
        if show_axes:
            # Display length follows the overall model size; directions are physical.
            bounds = []
            from itertools import product
            for actor, mesh, edges in self.main.viewer.section.sources():
                b = mesh.bounds
                corners = np.array(list(product(b[0:2], b[2:4], b[4:6])))
                bounds.extend(self.main.viewer._actor_points(actor, corners))
            length = max(float(np.linalg.norm(np.ptp(bounds, axis=0))) * .65, 1.0) if bounds else 10.
        for i in range(3):
            name = f'mass_axis_{i}'
            if show_axes:
                direction = self.result['principal_axes'][:, i]
                a, b = center - direction*length, center + direction*length
                self.main.viewer.set_overlay(name, polylines=[np.array([a, b])],
                                             color=('#b83d3d', '#34824c', '#3d62a0')[i], width=2,
                                             labels=[(b, f'I{i+1}')])
            else:
                self.main.viewer.set_overlay(name)

    def closeEvent(self, event):
        self.main.viewer.set_overlay('mass_cg')
        for i in range(3):
            self.main.viewer.set_overlay(f'mass_axis_{i}')
        super().closeEvent(event)
