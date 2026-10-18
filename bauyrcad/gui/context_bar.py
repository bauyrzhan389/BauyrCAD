"""Context toolbar (SolidWorks-style): a small row of buttons that appears next to the cursor
when a face or plane is clicked (Sketch, Normal To, ...). It takes no focus and does not
block the next click; it fades out when the mouse moves away, on Esc, or after a while."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from .icons import icon
from .command_help import tooltip

FADE_PX = 170          # hide once the mouse is this far away
LIFETIME_MS = 6000


class ContextBar(QFrame):
    def __init__(self, parent):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("ContextBar")
        self.setStyleSheet("#ContextBar{background:#ffffff;border:1px solid #9db6d8;"
                           "border-radius:5px;} QToolButton{border:0;padding:3px;}"
                           "QToolButton:hover{background:#e3edfa;border-radius:3px;}")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 3, 4, 3)
        self._lay.setSpacing(2)
        self._anchor = QPoint()
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._check)
        self._shown_ms = 0
        self.actions_shown: list[str] = []

    def show_at(self, pos: QPoint, items):
        """items: [(icon name, tooltip, callback)]."""
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.actions_shown = []
        for ic, tip, cb in items:
            b = QToolButton()
            b.setIcon(icon(ic))
            b.setIconSize(QSize(22, 22))
            b.setProperty("commandName", tip)
            b.setToolTip(tooltip(tip, icon_name=ic))
            b.clicked.connect(lambda _=False, cb=cb: self._run(cb))
            self._lay.addWidget(b)
            self.actions_shown.append(tip)
        self.adjustSize()
        self._anchor = QPoint(pos)
        self.move(pos + QPoint(14, -self.height() - 10))
        self._shown_ms = 0
        self.show()
        self.raise_()
        self._timer.start()

    def trigger(self, tip: str):
        """Press the button with this tooltip (scripts / tests)."""
        for i in range(self._lay.count()):
            b = self._lay.itemAt(i).widget()
            if isinstance(b, QToolButton) and b.property("commandName") == tip:
                b.click()
                return True
        return False

    def _run(self, cb):
        self.hide_bar()
        cb()

    def hide_bar(self):
        self._timer.stop()
        self.hide()

    def _check(self):
        self._shown_ms += self._timer.interval()
        if self.geometry().adjusted(-12, -12, 12, 12).contains(QCursor.pos()):
            self._shown_ms = 0                     # hovering the bar keeps it
            return
        d = QCursor.pos() - self._anchor
        if (d.x() ** 2 + d.y() ** 2) ** 0.5 > FADE_PX or self._shown_ms > LIFETIME_MS:
            self.hide_bar()
