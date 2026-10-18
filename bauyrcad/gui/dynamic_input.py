"""AutoCAD-style dynamic input: value fields next to the cursor while drawing.

After the first point of a rectangle, typing a number opens the fields (Width, Height).
Tab locks the value and moves to the next field, Enter or a click in the view finishes the
shape; the preview follows the typed values live. Negative values draw to the other side.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit

from .command_line import evaluate


class DynamicInput(QFrame):
    def __init__(self, parent):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("DynInput")
        self.setStyleSheet("#DynInput{background:#fffdf2;border:1px solid #d9b44a;border-radius:4px;}"
                           "QLabel{color:#5b4a14;padding:0 2px;font-size:9pt;}"
                           "QLineEdit{border:1px solid #d8cfa8;padding:2px;font:11pt 'Consolas';"
                           "min-width:70px;max-width:90px;background:#fff;}"
                           "QLineEdit[locked=\"true\"]{background:#e8f1ff;border-color:#7aa6e0;}")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 3, 4, 3)
        self._lay.setSpacing(4)
        self.edits: list[QLineEdit] = []
        self.locked: dict[int, float] = {}
        self.on_change = self.on_commit = self.on_cancel = None

    # ---- open / close --------------------------------------------------------------------
    def open(self, fields, first_text, on_change, on_commit, on_cancel):
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.edits, self.locked = [], {}
        self.on_change, self.on_commit, self.on_cancel = on_change, on_commit, on_cancel
        for i, name in enumerate(fields):
            self._lay.addWidget(QLabel(name))
            ed = QLineEdit()
            ed.installEventFilter(self)
            ed.textEdited.connect(lambda _t, i=i: self._edited(i))
            self._lay.addWidget(ed)
            self.edits.append(ed)
        self.adjustSize()
        self.follow()
        self.show()
        self.raise_()
        self.activateWindow()
        self.edits[0].setFocus()
        self.edits[0].setText(first_text)
        self._edited(0)

    def follow(self, pos: QPoint | None = None):
        self.move((pos or QCursor.pos()) + QPoint(20, 16))

    def close_input(self):
        self.hide()
        self.edits, self.locked = [], {}

    # ---- values --------------------------------------------------------------------------
    def values(self) -> dict[int, float]:
        """Every field that holds a valid number (typed values drive the shape)."""
        out = {}
        for i, ed in enumerate(self.edits):
            t = ed.text().strip()
            if t:
                try:
                    out[i] = float(evaluate(t))
                except (ValueError, SyntaxError, ZeroDivisionError):
                    pass
        return out

    def _edited(self, i):
        if self.on_change:
            self.on_change(self.values())

    def _set_locked(self, i, on):
        ed = self.edits[i]
        ed.setProperty("locked", "true" if on else "false")
        ed.style().unpolish(ed)
        ed.style().polish(ed)

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.KeyPress or obj not in self.edits:
            return False
        i = self.edits.index(obj)
        k = ev.key()
        if k in (Qt.Key_Tab, Qt.Key_Backtab):
            self._set_locked(i, i in self.values())
            j = (i + (1 if k == Qt.Key_Tab else -1)) % len(self.edits)
            self.edits[j].setFocus()
            self.edits[j].selectAll()
            return True
        if k in (Qt.Key_Return, Qt.Key_Enter):
            vals = self.values()
            cb = self.on_commit
            self.close_input()
            if cb:
                cb(vals)
            return True
        if k == Qt.Key_Escape:
            cb = self.on_cancel
            self.close_input()
            if cb:
                cb()
            return True
        return False
