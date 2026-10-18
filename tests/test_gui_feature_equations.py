from pathlib import Path
import math
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from bauyrcad.api import Part
from bauyrcad.core.document import Document
from test_gui_revolve_workflow import win
from test_parameters import cylinder


def equation(command, widget, text):
    widget.setFocus()
    QTest.keyClicks(widget, '=')
    editor = command.equations.editor
    assert editor.isVisible()
    editor.edit.selectAll()
    QTest.keyClicks(editor.edit, text)
    QTest.keyClick(editor.edit, Qt.Key_Return)
    return editor


def test_existing_extrude_equations_cancel_commit_and_unlink(win, tmp_path):
    p, sk, dim, f = cylinder()
    p.parameter('thickness', '8 mm')
    win._set_document(p.doc)
    win.edit_feature(f.id)
    cmd = win.command
    equation(cmd, cmd.depth, '=thickness / 3')
    assert cmd.equations.expressions == {'depth': 'thickness / 3'}
    assert not p.doc.parameter_bindings
    assert f.depth == 5
    win.command_cancel()
    assert f.depth == 5 and not p.doc.parameter_bindings
    win.edit_feature(f.id)
    cmd = win.command
    equation(cmd, cmd.depth, '=thickness / 3')
    assert cmd.depth.isReadOnly()
    cmd.equations.open('depth')
    folder = Path('projects/feature-equations-verification'); folder.mkdir(parents=True, exist_ok=True)
    cmd.equations.editor.grab().save(str(folder / 'depth-equation.png'))
    cmd.equations.editor.close()
    QTest.qWait(60)
    win.grab().save(str(folder / 'feature-panel.png'))
    cmd.equations.editor.close()
    win.command_ok()
    assert f.depth == pytest.approx(8 / 3)  # no rounding to the spinbox's 3 decimals
    path = tmp_path / 'feature-equation.bcad'
    p.doc.save(path)
    assert Document.load(path).feature(f.id).depth == pytest.approx(8 / 3)
    win.undo()
    assert win.doc.feature(f.id).depth == 5
    win.redo()
    assert win.doc.parameter_bindings[0]['expression'] == 'thickness / 3'
    win.edit_feature(f.id)
    cmd = win.command
    equation(cmd, cmd.depth, 'sqrt(81)')
    assert not cmd.depth.isReadOnly()
    win.command_ok()
    assert win.doc.feature(f.id).depth == 9
    assert not win.doc.parameter_bindings


def test_new_feature_link_and_rejected_units(win):
    p = Part()
    s = p.sketch()
    s.rect((0, 0), (10, 10))
    p.parameter('depth', '12 mm')
    p.parameter('turn', '90 deg', kind='angle')
    win._set_document(p.doc)
    win.start_extrude('boss', s.feature.id)
    cmd = win.command
    editor = equation(cmd, cmd.depth, '=turn')
    assert editor.isVisible()
    assert 'incompatible units' in editor.error.text()
    assert not cmd.equations.expressions
    editor.edit.setText('=depth')
    QTest.keyClick(editor.edit, Qt.Key_Return)
    assert not editor.isVisible()
    win.command_ok()
    boss = p.doc.features[-1]
    assert boss.depth == 12
    assert p.doc.parameter_bindings[0]['feature'] == boss.id
    p.parameter('depth', '20 mm')
    assert boss.depth == 20


def test_pattern_count_is_integer_and_revolve_uses_degrees(win):
    p, sk, dim, f = cylinder()
    pattern = p.pattern_linear(direction='x', spacing=30, count=2)
    p.parameter('copies', '3', kind='scalar')
    win._set_document(p.doc)
    win.edit_feature(pattern.id)
    cmd = win.command
    editor = equation(cmd, cmd.n1, '=copies / 2')
    assert editor.isVisible()
    assert 'integer' in editor.error.text()
    assert not cmd.equations.expressions
    editor.edit.setText('=copies')
    QTest.keyClick(editor.edit, Qt.Key_Return)
    win.command_ok()
    assert p.doc.feature(pattern.id).count == 3
    assert p.volume() == pytest.approx(3 * math.pi * 25 * 5)
    p = Part()
    s = p.sketch()
    s.centerline((0, 0), (0, 10))
    s.rect((10, 0), (15, 10))
    r = p.revolve(s, angle=90)
    p.parameter('turn', 'pi * rad', kind='angle')
    win._set_document(p.doc)
    win.edit_feature(r.id)
    equation(win.command, win.command.angle, '=turn')
    win.command_ok()
    assert r.angle == pytest.approx(180)
    assert p.volume() == pytest.approx(math.pi * (225 - 100) * 10 / 2)


def test_fillet_equation_geometry_failure_does_not_commit(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    p = Part()
    sk = p.sketch()
    sk.rect((0, 0), (10, 10))
    p.extrude(sk, 5)
    fillet = p.fillet(edges=[0], r=0.5)
    p.parameter('rounding', '20 mm')
    win._set_document(p.doc)
    win.edit_feature(fillet.id)
    cmd = win.command
    before, undo_count = p.doc.to_dict(), len(win._undo)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    equation(cmd, cmd.r, '=rounding')
    win.command_ok()
    assert warnings
    assert win.command is cmd
    assert p.doc.to_dict() == before
    assert len(win._undo) == undo_count
    assert not p.doc.parameter_bindings
    equation(cmd, cmd.r, '=rounding / 40')
    win.command_ok()
    assert win.command is None
    assert p.doc.feature(fillet.id).radius == 0.5
    assert p.doc.parameter_bindings[0]['field'] == 'radius'


def test_plane_and_helix_equations_and_mode_changes(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    p = Part()
    plane = p.plane('top', offset=10)
    p.parameter('offset', '-12 mm')
    win._set_document(p.doc); win.edit_feature(plane.id)
    cmd = win.command
    equation(cmd, cmd.dist, '=offset')
    win.command_ok()
    assert p.doc.feature(plane.id).distance == -12
    helix = p.helix(center=(0, 0, 0), d=20, pitch=4, turns=3)
    p.parameter('turns', '2.5', kind='scalar')
    win._set_document(p.doc); win.edit_feature(helix.id)
    cmd = win.command
    equation(cmd, cmd.revs, '=turns')
    folder = Path('projects/extended-equations-verification'); folder.mkdir(parents=True, exist_ok=True)
    QTest.qWait(60); win.grab().save(str(folder / 'helix.png'))
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    before = p.doc.to_dict()
    cmd.by.setCurrentIndex(cmd.by.findData('height_pitch'))
    win.command_ok()
    assert warnings and win.command is cmd and p.doc.to_dict() == before
    cmd.by.setCurrentIndex(cmd.by.findData('pitch_revolutions'))
    win.command_ok()
    assert p.doc.feature(helix.id).revolutions == 2.5


def test_custom_hole_equation_and_standard_override_guard(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (30, 30)); p.extrude(sk, 10)
    hole = p.hole('front', (15, 15), d=4, through=True)
    p.parameter('bore', '6 mm')
    win._set_document(p.doc); win.edit_feature(hole.id)
    cmd = win.command
    equation(cmd, cmd.d, '=bore')
    win.command_ok()
    assert p.volume() == pytest.approx(9000 - math.pi * 9 * 10)
    win.edit_feature(hole.id); cmd = win.command
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    cmd.size.setCurrentIndex(cmd.size.findData('M6')); cmd.override.setChecked(False)
    before = p.doc.to_dict(); win.command_ok()
    assert warnings and win.command is cmd and p.doc.to_dict() == before
    cmd.override.setChecked(True); win.command_ok()
    assert p.doc.feature(hole.id).d == 6
