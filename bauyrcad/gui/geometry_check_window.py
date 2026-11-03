"""Read-only Evaluate geometry checks with explicit incomplete/cancelled states."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QProgressDialog,
                               QPushButton, QTextBrowser, QVBoxLayout)
from ..core import geometry_check
from .icons import icon


class GeometryCheckWindow(QFrame):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.result = None
        self.generation = 0
        self.checking = False
        self.setWindowTitle('Evaluate — Check Geometry')
        self.setWindowIcon(icon('check_geometry'))
        self.resize(650, 500)
        layout = QVBoxLayout(self)
        hint = QLabel('Check kernel validity, closed solids and loose geometry.\n'
                      'Assembly checks include every component, including hidden components.')
        hint.setWordWrap(True); layout.addWidget(hint)
        self.text = QTextBrowser(); self.text.setStyleSheet("font: 10pt 'Consolas';")
        layout.addWidget(self.text)
        bar = QHBoxLayout()
        self.check_button = QPushButton('Check geometry')
        self.check_button.clicked.connect(self.calculate)
        copy = QPushButton('Copy report')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        close = QPushButton('Close'); close.clicked.connect(self.close)
        for button in (self.check_button, copy, close):
            bar.addWidget(button)
        layout.addLayout(bar)

    def open(self):
        self.show(); self.raise_(); self.model_changed()

    def model_changed(self):
        self.generation += 1
        self.result = None
        self.text.setPlainText('Check the current model. Results clear when geometry or the assembly changes.')

    def calculate(self):
        if self.checking:
            return
        self.model_changed()
        self.checking = True
        self.check_button.setEnabled(False)
        generation = self.generation
        progress = QProgressDialog('Checking geometry…', 'Cancel', 0, 1, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(250)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        def tick(done, total):
            progress.setMaximum(max(total, 1)); progress.setValue(done)
            QApplication.processEvents()
            if progress.wasCanceled() or generation != self.generation:
                raise InterruptedError('Check cancelled or model changed; no complete result is available')
        try:
            tick(0, 1)
            result = (geometry_check.assembly(self.main.asm, progress=tick) if hasattr(self.main, 'asm')
                      else geometry_check.part(self.main.doc))
            tick(1, 1)
            self.result = result
            self.text.setPlainText(geometry_check.report(result))
        except Exception as error:
            self.result = None
            self.text.setPlainText(f'CHECK INCOMPLETE\n{error}')
        finally:
            progress.close(); progress.deleteLater()
            self.checking = False
            self.check_button.setEnabled(True)

    def closeEvent(self, event):
        self.generation += 1
        self.result = None
        super().closeEvent(event)
