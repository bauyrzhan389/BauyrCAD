"""View Selector (numpad 5): a cube drawn in the current orientation. Click a face for a
standard view, an edge for a view between two faces, a corner for an isometric one (like
SolidWorks' View Selector). Buttons below give the named views."""

from __future__ import annotations

import itertools

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGridLayout, QToolButton, QVBoxLayout, QWidget

FACES = {  # normal: (label, in-plane axes a, b)
    (1, 0, 0): ("RIGHT", (0, 0, -1), (0, 1, 0)), (-1, 0, 0): ("LEFT", (0, 0, 1), (0, 1, 0)),
    (0, 1, 0): ("TOP", (1, 0, 0), (0, 0, -1)), (0, -1, 0): ("BOTTOM", (1, 0, 0), (0, 0, 1)),
    (0, 0, 1): ("FRONT", (1, 0, 0), (0, 1, 0)), (0, 0, -1): ("BACK", (-1, 0, 0), (0, 1, 0)),
}
BANDS = [(-1.0, -0.62, -1), (-0.62, 0.62, 0), (0.62, 1.0, 1)]
NAMED = [("Front", "front"), ("Back", "back"), ("Left", "left"), ("Right", "right"),
         ("Top", "top"), ("Bottom", "bottom"), ("Isometric", "isometric"),
         ("Dimetric", "dimetric"), ("Trimetric", "trimetric")]


def up_for(direction) -> tuple:
    """Screen up for a view direction: world Y, except looking straight up / down."""
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    if abs(d[1]) > 0.98:
        return (0, 0, -1) if d[1] > 0 else (0, 0, 1)
    return (0, 1, 0)


class CubeWidget(QWidget):
    def __init__(self, selector, frame):
        super().__init__(selector)
        self.sel = selector
        self.F = frame                                   # camera (right, up, back) columns
        self.setMinimumSize(240, 240)
        self.setMouseTracking(True)
        self.hover = None
        self.regions = []                                # (QPolygonF, direction, face normal)

    def _layout(self):
        w, h = self.width(), self.height()
        k = min(w, h) * 0.27
        cx, cy = w / 2, h / 2
        r, u, b = self.F[:, 0], self.F[:, 1], self.F[:, 2]

        def to2d(P):
            return QPointF(cx + k * float(P @ r), cy - k * float(P @ u))

        out = []
        faces = [(np.array(n, float), v) for n, v in FACES.items()]
        faces.sort(key=lambda t: float(t[0] @ b))        # far faces first
        for n, (label, a, bb) in faces:
            if n @ b < 0.02:                             # facing away
                continue
            a, bb = np.array(a, float), np.array(bb, float)
            for (u0, u1, i), (v0, v1, j) in itertools.product(BANDS, BANDS):
                poly = QPolygonF([to2d(n + a * uu + bb * vv)
                                  for uu, vv in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))])
                direction = n + a * i + bb * j
                out.append((poly, direction, n, label))
        return out

    def region_at(self, pos):
        for poly, d, n, _ in reversed(self.regions):
            if poly.containsPoint(QPointF(pos), Qt.OddEvenFill):
                return d
        return None

    def paintEvent(self, _e):
        self.regions = self._layout()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        b = self.F[:, 2]
        for poly, d, n, label in self.regions:
            light = np.array([0.35, 0.85, 0.4]) / np.linalg.norm([0.35, 0.85, 0.4])
            shade = 0.62 + 0.38 * max(0.0, float(n @ light))
            base = QColor.fromRgbF(0.80 * shade, 0.86 * shade, 0.95 * shade)
            hot = self.hover is not None and np.allclose(d, self.hover)
            qp.setBrush(QColor(255, 150, 50) if hot else base)
            qp.setPen(QPen(QColor(90, 110, 140), 1))
            qp.drawPolygon(poly)
        f = QFont("Arial")
        f.setPixelSize(12)
        f.setBold(True)
        qp.setFont(f)
        qp.setPen(QColor(30, 50, 80))
        seen = set()
        for poly, d, n, label in self.regions:           # label at each visible face centre
            if label in seen or np.count_nonzero(np.abs(d - n) > 1e-9):
                continue
            if n @ b > 0.35:
                seen.add(label)
                qp.drawText(poly.boundingRect(), Qt.AlignCenter, label)
        qp.end()

    def mouseMoveEvent(self, e):
        h = self.region_at(e.position().toPoint())
        if (h is None) != (self.hover is None) or (h is not None and not np.allclose(h, self.hover)):
            self.hover = h
            self.update()

    def mousePressEvent(self, e):
        d = self.region_at(e.position().toPoint())
        if d is not None:
            self.sel.choose(d)


class ViewSelector(QWidget):
    def __init__(self, viewer):
        super().__init__(viewer, Qt.Popup)
        self.viewer = viewer
        self.setStyleSheet("ViewSelector, QWidget#vs {background:#f7f9fc;} "
                           "QToolButton{padding:3px 6px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.cube = CubeWidget(self, viewer.camera_frame())
        lay.addWidget(self.cube)
        grid = QGridLayout()
        grid.setSpacing(3)
        for k, (label, key) in enumerate(NAMED):
            btn = QToolButton()
            btn.setText(label)
            btn.clicked.connect(lambda _=False, key=key: self.choose_named(key))
            grid.addWidget(btn, k // 3, k % 3)
        plane = viewer.normal_to_provider() if viewer.normal_to_provider else None
        plane = plane or viewer.sketch_plane
        if plane is not None:
            btn = QToolButton()
            btn.setText("Normal To")
            btn.clicked.connect(lambda _=False, p=plane: self._normal(p))
            grid.addWidget(btn, 3, 0, 1, 3)
        lay.addLayout(grid)

    def popup_over(self, widget):
        self.adjustSize()
        g = widget.mapToGlobal(widget.rect().center())
        self.move(g.x() - self.width() // 2, g.y() - self.height() // 2)
        self.show()

    def choose(self, direction):
        self.close()
        self.viewer.set_view(tuple(direction), up_for(direction))

    def choose_named(self, key):
        self.close()
        self.viewer.set_view(key)

    def _normal(self, plane):
        self.close()
        self.viewer.view_normal_to(plane)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)
