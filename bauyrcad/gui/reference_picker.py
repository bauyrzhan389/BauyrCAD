"""Reusable candidate list for commands picking overlapping linear references."""
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget


@dataclass
class ReferenceCandidate:
    key: str
    label: str
    kind: str
    points: np.ndarray


def line_hits(candidates, project, display, tolerance):
    """All candidates within a screen-space tolerance, ordered by distance then depth."""
    hits = []
    for candidate in candidates:
        xy, depth = project(candidate.points)
        if not np.all(np.isfinite(xy)) or np.min(depth) <= 0:
            continue
        delta = xy[1] - xy[0]
        t = np.clip(np.dot(np.asarray(display) - xy[0], delta) / max(float(delta @ delta), 1e-12), 0, 1)
        distance = float(np.linalg.norm(xy[0] + t * delta - display))
        if distance < tolerance:
            hits.append((distance, float(np.mean(depth)), candidate))
    hits.sort(key=lambda h: (round(h[0], 6), h[1]))
    return [h[2] for h in hits]


class ReferenceChoices(QWidget):
    preview = Signal(object)
    chosen = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Select Other — preview a candidate, then use it:"))
        self.items = QListWidget()
        self.items.setMaximumHeight(130)
        layout.addWidget(self.items)
        self.use = QPushButton("Use selected reference")
        layout.addWidget(self.use)
        self.candidates = []
        self.items.currentRowChanged.connect(self._preview)
        self.items.itemActivated.connect(self.accept)
        self.use.clicked.connect(self.accept)
        self.hide()

    def show_candidates(self, candidates):
        self.items.blockSignals(True)
        self.items.clear()
        self.candidates = list(candidates)
        self.items.addItems([c.label for c in candidates])
        self.items.blockSignals(False)
        self.setVisible(bool(candidates))
        if candidates:
            self.items.setCurrentRow(0)
            self.items.setFocus()

    def _preview(self, row):
        self.preview.emit(self.candidates[row] if 0 <= row < len(self.candidates) else None)

    def accept(self, *_):
        row = self.items.currentRow()
        if 0 <= row < len(self.candidates):
            self.chosen.emit(self.candidates[row])

    def clear(self):
        self.show_candidates([])
