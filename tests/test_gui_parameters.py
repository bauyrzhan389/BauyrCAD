from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from bauyrcad.gui.parameters_dialog import ParametersDialog
from test_gui_revolve_workflow import win
from test_parameters import cylinder


def test_parameter_dialog_transaction_undo_and_locked_edit(win):
    p, s, d, f = cylinder()
    win._set_document(p.doc)
    def fill():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ParametersDialog)
        dialog.add_parameter(name='thickness', expression='8 mm')
        dialog.add_binding(binding={'feature': f.id, 'field': 'depth', 'expression': 'thickness'})
        assert dialog.validate()
        folder = Path('projects/parameters-verification'); folder.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(folder / 'parameters.png'))
        dialog.accept()
    QTimer.singleShot(100, fill)
    win.a_parameters.trigger()
    assert win.doc.feature(f.id).depth == 8
    win.undo()
    assert win.doc.feature(f.id).depth == 5
    win.redo()
    assert win.doc.feature(f.id).depth == 8
    win.edit_feature(f.id)
    assert win.command.depth.isReadOnly()
    win.command_cancel()
    dialog = ParametersDialog(win.doc, win)
    dialog.table.item(0, 2).setText('thickness')
    dialog.show(); QTest.qWait(40)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert 'Circular' in dialog.message.text()
    assert win.doc.feature(f.id).depth == 8
    dialog.close()


def test_bound_sketch_edits_keep_equations_and_deletion_unlinks(win):
    p, s, d, f = cylinder()
    p.parameter('diameter', '20 mm')
    p.bind_parameter(s, f'dimension:{d.id}', 'diameter')
    win._set_document(p.doc)
    win.edit_feature(s.feature.id)
    command = win.command
    command.edit_dimension(command.sketch.constraint(d.id))
    # Finishing an unchanged sketch retains the driving equation.
    win.command_ok()
    assert win.command is None
    assert len(win.doc.parameter_bindings) == 1
    win.edit_feature(s.feature.id)
    win.command.sketch.remove_constraint(d.id)
    win.command_ok()
    assert not win.doc.parameter_bindings
    win.undo()
    assert len(win.doc.parameter_bindings) == 1
