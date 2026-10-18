"""AutoCAD-style dynamic command input.

Typing anywhere in the main window (outside text fields) opens a small command box next to
the cursor. Letters filter the command list by name or alias (L = Line, TR = Trim, ...);
Enter or Space runs the highlighted command; Enter on an empty box repeats the last
command. While a sketch tool is waiting for input, numbers are sent to the tool instead
(coordinates, lengths, radii: "50,20", "@30,0", "@40<30", "25").
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QCursor, QKeyEvent
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QFrame, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QTextEdit, QVBoxLayout, QWidget)

from .icons import icon


@dataclass
class Command:
    name: str                       # "LINE"
    label: str                      # "Line"
    run: Callable[[], None]
    aliases: tuple = ()
    icon: str = ""
    description: str = ""
    enabled: Callable[[], bool] = field(default=lambda: True)

    def keys(self):
        return (self.name, *self.aliases)


class CommandRegistry:
    def __init__(self):
        self.commands: list[Command] = []
        self.last: Command | None = None

    def add(self, *a, **kw) -> Command:
        c = Command(*a, **kw)
        self.commands.append(c)
        return c

    def find(self, text: str) -> list[Command]:
        t = text.strip().upper()
        if not t:
            return []
        exact, prefix, contains = [], [], []
        for c in self.commands:
            if not c.enabled():
                continue
            keys = [k.upper() for k in c.keys()]
            if t in keys:
                exact.append(c)
            elif any(k.startswith(t) for k in keys) or c.label.upper().startswith(t):
                prefix.append(c)
            elif t in c.name.upper() or t in c.label.upper() or t in c.description.upper():
                contains.append(c)
        prefix.sort(key=lambda c: (len(c.name), c.name))
        return exact + prefix + contains

    def remember(self, c: Command):
        """Record c as the command Space/Enter repeats (however it was started)."""
        self.last = c

    def run(self, c: Command):
        self.last = c
        c.run()


# ---- safe numeric expressions -------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.UAdd: operator.pos}
_NAMES = {"pi": math.pi, "e": math.e}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)),
          "cos": lambda d: math.cos(math.radians(d)), "tan": lambda d: math.tan(math.radians(d)),
          "abs": abs}
_UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, '"': 25.4, "ft": 304.8}


def evaluate(text: str) -> float:
    """Evaluate a number or arithmetic expression, with an optional length unit suffix
    ("25.4", "2*12.5", "1in", "sqrt(2)*10")."""
    t = text.strip().lower()
    factor = 1.0
    for u, f in sorted(_UNITS.items(), key=lambda kv: -len(kv[0])):
        if t.endswith(u) and len(t) > len(u):
            t, factor = t[: -len(u)].strip(), f
            break
    if t.endswith("°") or t.endswith("deg"):
        t = t.rstrip("°").removesuffix("deg")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _NAMES:
            return _NAMES[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(f"Cannot evaluate {text!r}")

    return ev(ast.parse(t, mode="eval")) * factor


NUMERIC_START = set("0123456789.-+@<(")

# numpad keys (NumLock on: digits; off: navigation keys), with Qt.KeypadModifier
_KEYPAD = {Qt.Key_0: "0", Qt.Key_1: "1", Qt.Key_2: "2", Qt.Key_3: "3", Qt.Key_4: "4",
           Qt.Key_5: "5", Qt.Key_6: "6", Qt.Key_7: "7", Qt.Key_8: "8", Qt.Key_9: "9",
           Qt.Key_Period: ".", Qt.Key_Comma: ".", Qt.Key_Insert: "0", Qt.Key_End: "1",
           Qt.Key_Down: "2", Qt.Key_PageDown: "3", Qt.Key_Left: "4", Qt.Key_Clear: "5",
           Qt.Key_Right: "6", Qt.Key_Home: "7", Qt.Key_Up: "8", Qt.Key_PageUp: "9",
           Qt.Key_Delete: "."}


def keypad_key(ev) -> str | None:
    """"0".."9" / "." for a numpad key press, else None."""
    if not (ev.modifiers() & Qt.KeypadModifier):
        return None
    return _KEYPAD.get(ev.key())


def is_text_input(w) -> bool:
    while w is not None:
        if isinstance(w, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
            return True
        if isinstance(w, QComboBox) and w.isEditable():
            return True
        w = w.parentWidget() if isinstance(w, QWidget) else None
    return False


class CommandLine(QFrame):
    """Floating command / value input box."""

    submitted_value = Signal(str)

    def __init__(self, registry: CommandRegistry, parent: QWidget, numeric_target=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.registry = registry
        self.numeric_target = numeric_target   # callable -> (prompt, handler) or None
        self.setObjectName("CommandLine")
        self.setStyleSheet("""
            #CommandLine { background: #ffffff; border: 1px solid #7aa6e0; border-radius: 4px; }
            QLineEdit { border: 0; font: 11pt 'Consolas'; padding: 3px 4px; }
            QListWidget { border: 0; border-top: 1px solid #dbe3ee; }
            QLabel { color: #4a5a70; padding: 2px 6px; font-size: 9pt; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.prompt = QLabel()
        lay.addWidget(self.prompt)
        self.edit = QLineEdit()
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setMaximumHeight(230)
        self.list.itemClicked.connect(lambda it: self._run_item(it))
        lay.addWidget(self.list)
        self.edit.textChanged.connect(self._filter)
        self._numeric = None
        self.setMinimumWidth(320)

    # ---- opening ---------------------------------------------------------------
    def open_with(self, text: str = "", pos: QPoint | None = None):
        numeric = self.numeric_target() if self.numeric_target else None
        self._numeric = numeric if (numeric and (not text or text[0] in NUMERIC_START)) else None
        if self._numeric:
            self.prompt.setText(self._numeric[0])
            self.prompt.show()
            self.list.hide()
        else:
            last = self.registry.last
            self.prompt.setText("Command:" + (f"   (Enter = repeat {last.name})" if last else ""))
            self.prompt.show()
            self.list.show()
        pos = pos or QCursor.pos() + QPoint(18, 18)
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        self.adjustSize()
        pos.setX(min(pos.x(), g.right() - self.width() - 4))
        pos.setY(min(pos.y(), g.bottom() - 280))
        self.move(pos)
        self.edit.setText(text)
        self._filter(text)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        self.edit.end(False)

    def _filter(self, text):
        if self._numeric:
            return
        self.list.clear()
        for c in self.registry.find(text)[:12]:
            alias = f"  ({', '.join(c.aliases)})" if c.aliases else ""
            it = QListWidgetItem(icon(c.icon), f"{c.name}{alias}  -  {c.label}")
            it.setData(Qt.UserRole, c)
            it.setToolTip(c.description or c.label)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.setVisible(self.list.count() > 0)
        self.adjustSize()

    # ---- running ------------------------------------------------------------------
    def _run_item(self, it):
        c = it.data(Qt.UserRole)
        self.hide()
        self.registry.run(c)

    def _submit(self):
        text = self.edit.text().strip()
        if self._numeric:
            handler = self._numeric[1]
            self.hide()
            if text:
                handler(text)
            return
        if not text:
            self.hide()
            if self.registry.last:
                self.registry.run(self.registry.last)
            return
        it = self.list.currentItem()
        if it is None:
            self.prompt.setText(f"Unknown command: {text}")
            return
        self._run_item(it)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._submit()
                return True
            if k == Qt.Key_Space and not self._numeric:
                self._submit()
                return True
            if k == Qt.Key_Escape:
                self.hide()
                return True
            if k in (Qt.Key_Down, Qt.Key_Up) and self.list.isVisible():
                row = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
        if obj is self.edit and ev.type() == QEvent.FocusOut:
            if not self.list.underMouse():
                self.hide()
        return super().eventFilter(obj, ev)


class TypeToCommandFilter(QWidget):
    """Application-wide key filter that opens the command line when the user types."""

    def __init__(self, window, command_line: CommandLine):
        super().__init__(window)
        self.window, self.cl = window, command_line
        self.hide()

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.KeyPress or not isinstance(ev, QKeyEvent):
            return False
        if QApplication.activeModalWidget() is not None:
            return False
        if self.cl.isVisible():
            # keys typed before the popup took the focus: forward them into it
            if not isinstance(obj, QWidget) or obj is self.cl.edit or obj.window() is self.cl:
                return False
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.cl._submit()
                return True
            if ev.text() and ev.text().isprintable():
                self.cl.edit.insert(ev.text())
                return True
            return False
        if QApplication.activeWindow() is not self.window:
            return False
        # a key the Qt parent chain passes on from a popup (e.g. the Enter that confirmed a
        # dimension value) is not a command key for this window
        if isinstance(obj, QWidget) and obj.window() is not self.window:
            return False
        if ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        if is_text_input(QApplication.focusWidget()):
            return False
        # numpad = view keys (SolidWorks-style), unless a drawing step is waiting for numbers
        kp = keypad_key(ev)
        if kp is not None and hasattr(self.window, "numpad_view"):
            wants_numbers = getattr(self.window, "expects_numbers", lambda: False)()
            if not wants_numbers:
                if not ev.isAutoRepeat():
                    self.window.numpad_view(kp)
                return True
        text = ev.text()
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not ev.isAutoRepeat():
            if self.window.command_enter_pressed():
                return True
            if self.cl.registry.last is not None:
                self.cl.registry.run(self.cl.registry.last)
                return True
            return False
        if text and text.isprintable() and not text.isspace():
            # AutoCAD dynamic input: a number typed while drawing goes to the value fields
            dyn = getattr(self.window, "dynamic_input", None)
            if dyn is not None and dyn(text):
                return True
            self.cl.open_with(text)
            return True
        return False
