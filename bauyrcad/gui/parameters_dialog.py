"""Named values and explicit dimension bindings, validated as a single transaction."""
import math
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)
from ..core.document import Document
from ..core.parameters import KINDS, ParameterError, renamed, targets, values


def validate_feature_edit(doc, original, replacement, equations=None):
    """Check a prospective edit against all equations before creating an undo entry."""
    if not doc.parameter_bindings and not (equations and equations.expressions):
        return []
    trial = Document.from_dict(doc.to_dict())
    candidate = type(replacement).from_dict(replacement.to_dict())
    if original is None:
        trial.add_feature(candidate)
    else:
        candidate.id, candidate.name, candidate.suppressed = original.id, original.name, original.suppressed
        trial.features[doc.features.index(original)] = candidate
    bindings = equations.bindings(candidate.id) if equations else trial.parameter_bindings
    trial.configure_parameters(trial.parameters, bindings)
    return bindings


class ParametersDialog(QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Parameters and Equations")
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        intro = QLabel("Define reusable values, then link existing dimensions below. Examples: 6 mm, thickness / 2, 30 deg.\n"
                       "Bare numbers use mm or degrees. For mixed expressions use units: width + 2 mm.\n"
                       "Link dimensions here, or press = in supported dimension controls. Remove a binding to unlink.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Expression", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for label, slot in (("Add parameter", self.add_parameter), ("Remove selected", self.remove_parameter),
                            ("Rename selected…", self.rename_parameter)):
            button = QPushButton(label); button.clicked.connect(slot); row.addWidget(button)
        layout.addLayout(row)
        layout.addWidget(QLabel("Dimension bindings — select a dimension and enter its expression"))
        self.bindings = QTableWidget(0, 2)
        self.bindings.setHorizontalHeaderLabels(["Dimension", "Expression"])
        self.bindings.setColumnWidth(0, 420)
        self.bindings.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.bindings)
        row = QHBoxLayout()
        for label, slot in (("Add binding", self.add_binding), ("Remove binding", self.remove_binding),
                            ("Check model", self.validate)):
            button = QPushButton(label); button.clicked.connect(slot); row.addWidget(button)
        layout.addLayout(row)
        self.message = QLabel(); self.message.setWordWrap(True); self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load(doc.parameters, doc.parameter_bindings)

    def add_parameter(self, checked=False, name="", kind="length", expression=""):
        i = self.table.rowCount(); self.table.insertRow(i)
        self.table.setItem(i, 0, QTableWidgetItem(name))
        combo = QComboBox(); combo.addItems(list(KINDS)); combo.setCurrentText(kind)
        self.table.setCellWidget(i, 1, combo)
        self.table.setItem(i, 2, QTableWidgetItem(expression))
        value = QTableWidgetItem(); value.setFlags(value.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(i, 3, value)

    def add_binding(self, checked=False, binding=None):
        i = self.bindings.rowCount(); self.bindings.insertRow(i)
        combo = QComboBox()
        for fid, field, kind, label in targets(self.doc):
            combo.addItem(f"{label} ({kind})", (fid, field))
        if binding:
            index = next((j for j in range(combo.count()) if tuple(combo.itemData(j)) == (binding["feature"], binding["field"])), -1)
            if index < 0:
                combo.addItem("Missing dimension — choose a replacement", (binding["feature"], binding["field"]))
                index = combo.count() - 1
            combo.setCurrentIndex(index)
        self.bindings.setCellWidget(i, 0, combo)
        self.bindings.setItem(i, 1, QTableWidgetItem(binding["expression"] if binding else ""))

    def remove_parameter(self):
        self.table.removeRow(self.table.currentRow())

    def remove_binding(self):
        self.bindings.removeRow(self.bindings.currentRow())

    def load(self, parameters, bindings):
        self.table.setRowCount(0); self.bindings.setRowCount(0)
        for name, row in parameters.items():
            self.add_parameter(name=name, kind=row["kind"], expression=row["expression"])
        for binding in bindings:
            self.add_binding(binding=binding)

    def proposal(self):
        parameters, bindings = {}, []
        for i in range(self.table.rowCount()):
            name = self.table.item(i, 0).text().strip()
            if name in parameters:
                raise ParameterError(f"Duplicate parameter: {name}")
            parameters[name] = {"kind": self.table.cellWidget(i, 1).currentText(),
                                "expression": self.table.item(i, 2).text().strip()}
        for i in range(self.bindings.rowCount()):
            target = self.bindings.cellWidget(i, 0).currentData()
            if target is None:
                raise ParameterError("Create a supported feature or driving sketch dimension first")
            bindings.append({"feature": target[0], "field": target[1],
                             "expression": self.bindings.item(i, 1).text().strip()})
        return parameters, bindings

    def rename_parameter(self):
        i = self.table.currentRow()
        if i < 0:
            return
        old = self.table.item(i, 0).text().strip()
        new, ok = QInputDialog.getText(self, "Rename parameter", "New name:", text=old)
        if ok and new != old:
            try:
                parameters, bindings = self.proposal()
                self.load(*renamed(parameters, bindings, old, new.strip()))
            except ValueError as exc:
                self.message.setText(str(exc))

    def validate(self):
        try:
            parameters, bindings = self.proposal()
            trial = Document.from_dict(self.doc.to_dict())
            trial.configure_parameters(parameters, bindings)
            evaluated = values(parameters)
            for i, (name, row) in enumerate(parameters.items()):
                q = evaluated[name]
                value = math.degrees(q.value) if row["kind"] == "angle" else q.value
                unit = {"length": " mm", "angle": " deg", "scalar": ""}[row["kind"]]
                self.table.item(i, 3).setText(f"{value:.8g}{unit}")
            self.message.setText("Valid: dimensions and model rebuilt successfully.")
            self.result_parameters, self.result_bindings = parameters, bindings
            return True
        except (ValueError, KeyError, TypeError) as exc:
            self.message.setText(str(exc))
            return False

    def accept(self):
        if self.validate():
            super().accept()
