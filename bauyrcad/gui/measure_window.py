"""Measure (SolidWorks-style): click faces, edges and vertices; the window lists their sizes
and, for two of them, the distance, centre distance and angle. Click an entity again to drop
it, click empty space to start over, Esc or Close to leave."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                               QVBoxLayout)

from ..core import geometry, measure
from .icons import icon
from .viewer import SELECT_RGB


class MeasureWindow(QFrame):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.setWindowTitle("Measure")
        self.setWindowIcon(icon("measure"))
        self.refs: list[tuple] = []
        self.result: dict | None = None
        self._vertices = None
        lay = QVBoxLayout(self)
        self.hint = QLabel("Click faces, edges or vertices to measure them.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#4a5a70;")
        lay.addWidget(self.hint)
        self.text = QTextBrowser()
        self.text.setMinimumSize(380, 170)
        self.text.setStyleSheet("font: 10pt 'Consolas';")
        lay.addWidget(self.text)
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
        v = self.main.viewer
        g = v.mapToGlobal(QPoint(v.width() - 420, 12))
        self.move(g)
        self.show()
        self.raise_()

    def vertices(self):
        body = self.main.doc.body
        if self._vertices is None and body is not None:
            self._vertices = geometry.vertices(body)
        return self._vertices

    def model_changed(self):
        self._vertices = None
        self.clear()

    # ---- picking --------------------------------------------------------------------------
    def pick(self, x, y, hit=None) -> bool:
        """Viewer click at display (x, y). Returns True (the click is consumed)."""
        if self.main.doc.body is None:
            return True
        hit = hit or self.main.viewer.pick_topology(x, y, self.vertices())
        if hit is None:
            self.clear()
            return True
        ref = (hit[0], hit[1])
        if ref in self.refs:
            self.refs.remove(ref)
        else:
            self.refs.append(ref)
        self.update_result()
        return True

    def add(self, kind, index):
        """Select an entity (scripts / tests)."""
        if (kind, index) not in self.refs:
            self.refs.append((kind, index))
        self.update_result()

    def clear(self):
        self.refs = []
        self.result = None
        self.text.setHtml("")
        self._draw()

    def update_result(self):
        body = self.main.doc.body
        if not self.refs or body is None:
            self.clear()
            return
        try:
            self.result = measure.measure(body, self.refs)
        except Exception as e:  # noqa: BLE001 - show, don't crash
            self.result = None
            self.text.setPlainText(f"Cannot measure: {e}")
            self._draw()
            return
        lines = measure.report(self.result)
        html = "<br>".join(f"<b>{l}</b>" if l.startswith(("Distance", "Centre", "Normal", "Axis",
                                                             "Angle", "Total")) else l
                           for l in lines)
        self.text.setHtml(html)
        self.main.status(" | ".join(lines[-3:]))
        self._draw()

    # ---- display ----------------------------------------------------------------------------
    def _draw(self):
        v = self.main.viewer
        tess = getattr(v, "_tess", None)
        polys, pts, faces = [], [], {}
        for kind, i in self.refs:
            if kind == "edge" and tess is not None:
                polys += [pl for pl, eid in zip(tess.edge_polylines, tess.edge_ids) if eid == i]
            elif kind == "vertex" and self.vertices() is not None:
                pts.append(self.vertices()[i])
            elif kind == "face":
                faces[i] = SELECT_RGB
        v.set_face_colors(faces)
        v.set_overlay("measure_sel", polys, pts, "#00c8ff", 4.0)
        r = self.result
        if r is not None and "distance" in r and r["distance"] > 1e-9:
            pa, pb = np.asarray(r["point_a"]), np.asarray(r["point_b"])
            v.set_overlay("measure_dist", [np.array([pa, pb])], [pa, pb], "#ff7a00", 2.5,
                          [((pa + pb) / 2, f"{r['distance']:.4g} mm")])
        else:
            v.set_overlay("measure_dist")

    def closeEvent(self, e):
        self.refs = []
        self.result = None
        v = self.main.viewer
        v.set_overlay("measure_sel")
        v.set_overlay("measure_dist")
        self.main.refresh_view()
        super().closeEvent(e)
