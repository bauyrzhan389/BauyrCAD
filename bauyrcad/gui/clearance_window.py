"""Evaluate minimum component clearance with complete-check status."""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QProgressDialog,
                               QPushButton, QTextBrowser, QVBoxLayout)


class ClearanceWindow(QFrame):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.setWindowTitle('Evaluate — Minimum Clearance')
        self.resize(560, 420)
        self.result = None
        self.generation = 0
        self.checking = False
        layout = QVBoxLayout(self)
        hint = QLabel('Check two components, or find the nearest neighbour of one component.\n'
                      'The result applies to the current assembly position.')
        hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout()
        self.first, self.second = QComboBox(), QComboBox()
        form.addRow('Component A', self.first)
        form.addRow('Component B', self.second)
        self.required = QDoubleSpinBox()
        self.required.setRange(0, 1e9); self.required.setDecimals(4)
        self.required.setSuffix(' mm')
        form.addRow('Required clearance', self.required)
        layout.addLayout(form)
        self.check_button = QPushButton('Calculate')
        self.check_button.clicked.connect(self.calculate)
        layout.addWidget(self.check_button)
        self.transparent = QCheckBox('Transparent components — reveal closest points')
        self.transparent.setChecked(True)
        self.transparent.toggled.connect(self.highlight)
        layout.addWidget(self.transparent)
        self.text = QTextBrowser(); layout.addWidget(self.text)
        bar = QHBoxLayout()
        copy = QPushButton('Copy report')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        close = QPushButton('Close'); close.clicked.connect(self.close)
        bar.addWidget(copy); bar.addStretch(); bar.addWidget(close); layout.addLayout(bar)
        self.first.currentIndexChanged.connect(self.change_first)
        self.second.currentIndexChanged.connect(self.invalidate)
        self.required.valueChanged.connect(self.invalidate)

    def open(self):
        self.model_changed()
        self.show(); self.raise_()

    def clear_highlight(self):
        self.main.viewer.set_overlay('clearance')
        self.main.viewer.set_assembly_opacities()
        self.main._update_colors()

    def invalidate(self, *_):
        self.generation += 1
        self.result = None
        self.clear_highlight()
        self.text.setPlainText('Not checked. Click Calculate to measure the current positions.')

    def model_changed(self):
        current = self.first.currentData()
        self.first.blockSignals(True)
        self.first.clear()
        for component in self.main.asm.components:
            self.first.addItem(component.name, component.id)
        index = self.first.findData(current)
        if index >= 0:
            self.first.setCurrentIndex(index)
        self.first.blockSignals(False)
        self.change_first()

    def change_first(self, *_):
        current = self.second.currentData()
        self.second.blockSignals(True)
        self.second.clear()
        self.second.addItem('Nearest other component', None)
        for component in self.main.asm.components:
            if component.id != self.first.currentData():
                self.second.addItem(component.name, component.id)
        index = self.second.findData(current)
        self.second.setCurrentIndex(max(0, index))
        self.second.blockSignals(False)
        self.invalidate()

    def calculate(self):
        if self.checking:
            return
        self.main._finish_animation()
        self.invalidate()
        generation = self.generation
        self.checking = True
        self.check_button.setEnabled(False)
        progress = QProgressDialog('Measuring component clearance…', 'Cancel', 0,
                                   max(1, len(self.main.asm.components)), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(250)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        def tick(done, total):
            progress.setMaximum(max(1, total)); progress.setValue(done)
            QApplication.processEvents()
            if progress.wasCanceled() or generation != self.generation:
                raise InterruptedError('Check cancelled; no complete result is available')
        try:
            result = self.main.asm.clearance(self.first.currentData(), self.second.currentData(),
                                             strict=True, progress=tick)
            if generation != self.generation:
                raise InterruptedError('Assembly changed; calculate again')
            self.result = result
            self.report()
            self.highlight()
        except Exception as error:
            self.result = None
            self.clear_highlight()
            self.text.setPlainText(f'Check incomplete: {error}')
        finally:
            progress.close(); progress.deleteLater()
            self.checking = False
            self.check_button.setEnabled(True)

    def report(self):
        r = self.result
        a, b = (self.main.asm.component(r[k]).name for k in ('a', 'b'))
        required = self.required.value()
        if r['overlap'] > 1e-9:
            state = 'INTERFERENCE — components overlap'
        elif r['distance'] + 1e-9 < required:
            state = 'BELOW REQUIRED CLEARANCE'
        elif r['distance'] < 1e-9:
            state = 'TOUCHING — no positive clearance'
        else:
            state = 'MEETS REQUIRED CLEARANCE'
        lines = [state, f'{a} / {b}', f"Minimum distance: {r['distance']:.8g} mm",
                 f'Required clearance: {required:.8g} mm', f"Overlap: {r['overlap']:.8g} mm³"]
        for key, label in [('point_a', 'Point A'), ('point_b', 'Point B')]:
            lines.append(label + ' (world mm): ' + ', '.join(f'{x:.8g}' for x in r[key]))
        lines.append('Current pose only; not a swept-motion or whole-assembly interference check.')
        if self.second.currentData() is None:
            lines.append('Nearest neighbour shown; equally close neighbours may exist.')
        self.text.setPlainText('\n'.join(lines))

    def highlight(self):
        self.clear_highlight()
        if self.result is None:
            return
        r = self.result
        self.main.viewer.set_assembly_overrides({(r['a'], None): (225, 155, 70),
                                                (r['b'], None): (110, 175, 135)})
        if self.transparent.isChecked():
            self.main.viewer.set_assembly_opacities({c.id: .25 for c in self.main.asm.components})
        a, b = np.asarray(r['point_a']), np.asarray(r['point_b'])
        self.main.viewer.set_overlay('clearance', polylines=[np.array([a, b])], points=[a, b],
                                     color='#b35b00', width=4,
                                     labels=[((a + b) / 2, f"{r['distance']:.6g} mm")])

    def closeEvent(self, event):
        self.invalidate()
        super().closeEvent(event)
