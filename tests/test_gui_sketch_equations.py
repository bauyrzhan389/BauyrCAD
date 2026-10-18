from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from bauyrcad.core.document import Document
from bauyrcad.core.plane import FRONT
from bauyrcad.core.sketch import Constraint, solver
from test_gui_revolve_workflow import win
from test_parameters import cylinder


def enter(command, cid, text):
    command.edit_dimension(command.sketch.constraint(cid))
    editor = command._editor
    editor.edit.selectAll()
    QTest.keyClicks(editor.edit, text)
    QTest.keyClick(editor.edit, Qt.Key_Return)
    return editor


def test_equations_are_staged_with_sketch_history_and_cancel(win, tmp_path):
    p, s, d, f = cylinder()
    p.parameter('diameter', '20 mm')
    win._set_document(p.doc)
    win.edit_feature(s.feature.id)
    cmd = win.command
    enter(cmd, d.id, '=diameter * 1.5')
    assert cmd.sketch.constraint(d.id).value == pytest.approx(30)
    assert not p.doc.parameter_bindings
    assert s.sk.constraint(d.id).value == pytest.approx(10)
    cmd.undo()
    assert not cmd.dimension_equations
    assert cmd.sketch.constraint(d.id).value == pytest.approx(10)
    cmd.redo()
    assert cmd.dimension_equations[d.id] == 'diameter * 1.5'
    win.command_cancel()
    assert not p.doc.parameter_bindings
    assert s.sk.constraint(d.id).value == pytest.approx(10)
    win.edit_feature(s.feature.id)
    enter(win.command, d.id, '=diameter')
    win.command_ok()
    assert win.doc.parameter_bindings[0]['expression'] == 'diameter'
    path = tmp_path / 'inline-equation.bcad'
    win.doc.save(path)
    assert Document.load(path).parameter_bindings == win.doc.parameter_bindings
    win.undo()
    assert not win.doc.parameter_bindings
    win.redo()
    assert win.doc.parameter_bindings[0]['expression'] == 'diameter'


def test_numeric_calculator_unlinks_and_invalid_input_stays_open(win):
    p, s, d, f = cylinder()
    p.parameter('diameter', '20 mm')
    p.bind_parameter(s, f'dimension:{d.id}', 'diameter')
    win._set_document(p.doc)
    win.edit_feature(s.feature.id)
    cmd = win.command
    editor = enter(cmd, d.id, '=missing + 2 mm')
    assert editor.isVisible()
    assert 'Unknown parameter' in editor.error.text()
    assert not cmd.history
    assert cmd.dimension_equations[d.id] == 'diameter'
    folder = Path('projects/sketch-equations-verification'); folder.mkdir(parents=True, exist_ok=True)
    editor.grab().save(str(folder / 'invalid-expression.png'))
    editor.edit.setText('=diameter + 5 mm')
    QTest.keyClick(editor.edit, Qt.Key_Return)
    assert not editor.isVisible()
    assert cmd.sketch.constraint(d.id).value == pytest.approx(25)
    enter(cmd, d.id, 'sqrt(900)')
    assert not cmd.dimension_equations
    assert cmd.sketch.constraint(d.id).value == pytest.approx(30)
    cmd.undo()
    assert cmd.dimension_equations[d.id] == 'diameter + 5 mm'
    cmd.redo()
    assert not cmd.dimension_equations
    win.command_ok()
    assert not win.doc.parameter_bindings
    p.parameter('diameter', '40 mm')
    assert p.doc.feature(s.feature.id).sketch.constraint(d.id).value == pytest.approx(30)


def test_new_sketch_equation_commit_and_popup_cancel(win):
    from bauyrcad.api import Part
    p = Part()
    p.parameter('diameter', '24 mm')
    win._set_document(p.doc)
    win.start_sketch(plane=FRONT)
    cmd = win.command
    circle = cmd.sketch.add_circle((0, 0), 5)
    c = Constraint(0, 'diameter', [('e', circle.id)], value=10)
    assert solver.try_add(cmd.sketch, c)[0]
    cmd.changed(checkpoint=False)
    cmd.edit_dimension(c)
    cmd._editor.edit.setText('=diameter')
    QTest.keyClick(cmd._editor.edit, Qt.Key_Escape)
    assert not cmd.dimension_equations
    assert cmd.sketch.constraint(c.id).value == pytest.approx(10)
    enter(cmd, c.id, '=diameter')
    cmd.edit_dimension(cmd.sketch.constraint(c.id))
    assert cmd._editor.edit.text() == '=diameter'
    folder = Path('projects/sketch-equations-verification'); folder.mkdir(parents=True, exist_ok=True)
    cmd._editor.grab().save(str(folder / 'linked-dimension.png'))
    win.command_ok()
    assert not cmd._editor.isVisible()
    assert win.command is None
    sketch = p.doc.sketches()[0]
    assert p.doc.parameter_bindings == [{'feature': sketch.id, 'field': f'dimension:{c.id}', 'expression': 'diameter'}]
    p.parameter('diameter', '36 mm')
    assert sketch.sketch.constraint(c.id).value == pytest.approx(36)
