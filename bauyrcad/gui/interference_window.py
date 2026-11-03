"""Evaluate assembly intersections, with complete-check status and temporary highlighting."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                               QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout)

from ..core import geometry


class InterferenceWindow(QFrame):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.setWindowTitle('Evaluate — Interference Detection')
        self.resize(660, 490)
        self.result = None
        self.ignored = set()
        self.generation = 0
        self.checking = False
        layout = QVBoxLayout(self)
        hint = QLabel('Check the current assembly position. Touching faces are not interference.\n'
                      'Select a result to show the overlapping solid in red.')
        hint.setWordWrap(True); layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel('Minimum overlap (mm³):'))
        self.minimum = QDoubleSpinBox(); self.minimum.setRange(0, 1e12)
        self.minimum.setDecimals(6); self.minimum.setValue(.001)
        self.minimum.valueChanged.connect(lambda: self.invalidate(False))
        row.addWidget(self.minimum)
        self.check_button = QPushButton('Calculate')
        self.check_button.clicked.connect(self.calculate); row.addWidget(self.check_button)
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Component A', 'Component B', 'Overlap (mm³)'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.highlight)
        layout.addWidget(self.table)
        self.text = QTextBrowser(); self.text.setMaximumHeight(115); layout.addWidget(self.text)
        row = QHBoxLayout()
        self.ignore_button = QPushButton('Ignore selected pair')
        self.ignore_button.clicked.connect(self.ignore_selected)
        self.reset_button = QPushButton('Reset ignored pairs')
        self.reset_button.clicked.connect(self.reset_ignored)
        copy = QPushButton('Copy report'); copy.clicked.connect(self.copy_report)
        close = QPushButton('Close'); close.clicked.connect(self.close)
        for widget in (self.ignore_button, self.reset_button, copy, close):
            row.addWidget(widget)
        layout.addLayout(row)

    def open(self):
        self.model_changed()
        self.show(); self.raise_()
        self.calculate()

    def clear_highlight(self):
        self.main.viewer.set_preview(None, name='interference')
        self.main.viewer.set_assembly_opacities()
        self.main._update_colors()

    def invalidate(self, reset_ignored=True):
        self.generation += 1
        self.result = None
        if reset_ignored:
            self.ignored.clear()
        self.table.setRowCount(0)
        self.clear_highlight()
        self.text.setPlainText('Not checked. Click Calculate to check the current geometry and positions.')

    def model_changed(self):
        self.invalidate()

    def calculate(self):
        if self.checking:
            return
        self.main._finish_animation()
        self.invalidate(False)
        generation = self.generation
        self.checking = True
        progress = QProgressDialog('Checking component intersections…', 'Cancel', 0,
                                   max(1, len(self.main.asm.components)), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(250)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self.check_button.setEnabled(False)
        def tick(done, total):
            progress.setMaximum(max(1, total)); progress.setValue(done)
            QApplication.processEvents()
            if progress.wasCanceled() or generation != self.generation:
                raise InterruptedError('Check cancelled; no complete result is available')
        try:
            result = self.main.asm.interference(self.minimum.value(), self.ignored,
                                               strict=True, include_shapes=True, progress=tick)
            if generation != self.generation:
                raise InterruptedError('Assembly changed; calculate again')
            self.result = result
            self.table.setRowCount(len(result))
            for i, hit in enumerate(result):
                names = [self.main.asm.component(hit[key]).name for key in ('a', 'b')]
                for j, value in enumerate(names + [f"{hit['volume']:.8g}"]):
                    self.table.setItem(i, j, QTableWidgetItem(value))
            count = len(self.main.asm.components)
            summary = (f'Checked {count} components. {len(result)} overlapping pairs above '
                       f'{self.minimum.value():g} mm³. Ignored pairs: {len(self.ignored)} (this session).')
            if count < 2:
                summary = 'Fewer than two components: no component pairs to check.'
            if result:
                summary += f"\nPairwise overlap sum: {sum(h['volume'] for h in result):.8g} mm³."
                summary += '\nTriple overlaps can be counted more than once in this sum.'
            self.text.setPlainText(summary)
            self.table.resizeColumnsToContents()
            if result:
                self.table.selectRow(0)
        except Exception as error:
            self.result = None
            self.table.setRowCount(0)
            self.clear_highlight()
            self.text.setPlainText(f'Check incomplete: {error}')
        finally:
            progress.close()
            progress.deleteLater()
            self.checking = False
            self.check_button.setEnabled(True)

    def highlight(self):
        self.clear_highlight()
        index = self.table.currentRow()
        if self.result is None or not 0 <= index < len(self.result):
            return
        hit = self.result[index]
        self.main.viewer.set_assembly_overrides({(hit[key], None): (225, 150, 80) for key in ('a', 'b')})
        self.main.viewer.set_assembly_opacities({c.id: .18 for c in self.main.asm.components})
        self.main.viewer.set_preview(geometry.tessellate(hit['shape']), color='#e32727',
                                     opacity=1, name='interference')

    def ignore_selected(self):
        index = self.table.currentRow()
        if self.result is not None and 0 <= index < len(self.result):
            hit = self.result[index]
            self.ignored.add(frozenset((hit['a'], hit['b'])))
            self.calculate()

    def reset_ignored(self):
        self.ignored.clear(); self.calculate()

    def copy_report(self):
        lines = [self.text.toPlainText()]
        for pair in sorted((tuple(sorted(p)) for p in self.ignored)):
            names = [self.main.asm.component(cid).name for cid in pair]
            lines.append('Ignored: ' + ' / '.join(names))
        if self.result is not None:
            for hit in self.result:
                a, b = (self.main.asm.component(hit[k]).name for k in ('a', 'b'))
                lines.append(f"{a} / {b}: {hit['volume']:.8g} mm³")
        QApplication.clipboard().setText('\n'.join(lines))

    def closeEvent(self, event):
        self.invalidate()
        super().closeEvent(event)
