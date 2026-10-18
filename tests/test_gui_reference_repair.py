from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from bauyrcad.api import Part
from bauyrcad.core.document import Document
from test_gui_revolve_workflow import win


def test_missing_edge_remains_visible_and_repair_is_undoable(win, monkeypatch, tmp_path):
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 10)
    fillet = p.fillet(edges=[0], r=1)
    fillet.edges[0].mid = [1000, 1000, 1000]
    p.doc.regenerate()
    win._set_document(p.doc); win.edit_feature(fillet.id)
    cmd = win.command
    assert len(cmd.items.items) == 1 and cmd.items.items[0]['unresolved']
    before = p.doc.to_dict()
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    win.command_ok()
    assert warnings and win.command is cmd and p.doc.to_dict() == before
    folder = Path('projects/reference-repair-verification'); folder.mkdir(parents=True, exist_ok=True)
    QTest.qWait(200); win.grab().save(str(folder / 'missing-edge.png'))
    row = cmd.items.list.item(0)
    QTest.mouseClick(cmd.items.list.viewport(), Qt.LeftButton,
                     pos=cmd.items.list.visualItemRect(row).center())
    cmd.on_click(SimpleNamespace(kind='edge', topology=('edge', 0, cmd.edges_[0]['mid'])))
    assert len(cmd.items.items) == 1 and not cmd.items.items[0].get('unresolved')
    win.command_cancel()
    assert p.doc.to_dict() == before
    win.edit_feature(fillet.id); cmd = win.command
    cmd.items.list.setCurrentRow(0)
    cmd.on_click(SimpleNamespace(kind='edge', topology=('edge', 0, cmd.edges_[0]['mid'])))
    win.command_ok()
    assert win.command is None
    assert not win.doc.feature(fillet.id).error
    path = tmp_path / 'repaired.bcad'; win.doc.save(path)
    assert not Document.load(path).feature(fillet.id).error
    win.undo()
    assert win.doc.feature(fillet.id).edges[0].mid == [1000, 1000, 1000]
    win.redo()
    assert not win.doc.feature(fillet.id).error


def test_replacement_menu_previews_without_changing_reference(win):
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 10)
    fillet = p.fillet(edges=[0], r=1)
    fillet.edges[0].mid = [1000, 1000, 1000]
    p.doc.regenerate()
    win._set_document(p.doc); win.edit_feature(fillet.id)
    cmd = win.command; box = cmd.items
    assert not box.replacements.isEnabled()
    box.list.setCurrentRow(0)
    assert box.replacements.isEnabled()
    before = p.doc.to_dict()
    QTest.mouseClick(box.replacements, Qt.LeftButton)
    menu = box.repair_menu
    actions = [a for a in menu.actions() if a.data() is not None]
    assert actions and all('distant' in a.text() for a in actions)
    action = next(a for a in actions if a.data() == 0)
    QTest.qWait(100)
    menu.setActiveAction(action); QTest.qWait(60)
    assert win.viewer._candidate_outline is not None
    assert box.items[0]['unresolved'] and p.doc.to_dict() == before
    folder = Path('projects/reference-repair-verification')
    win.grab().save(str(folder / 'edge-candidate-preview.png'))
    menu.grab().save(str(folder / 'edge-candidates.png'))
    QTest.keyClick(menu, Qt.Key_Escape)
    assert box.repair_menu is None and win.viewer._candidate_outline is None
    assert p.doc.to_dict() == before
    QTest.mouseClick(box.replacements, Qt.LeftButton)
    menu = box.repair_menu
    action = next(a for a in menu.actions() if a.data() == 0)
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
    assert box.items[0]['key'] == 0 and not box.items[0].get('unresolved')
    assert win.viewer._candidate_outline is None
    assert p.doc.to_dict() == before
    win.command_ok()
    assert not win.doc.feature(fillet.id).error


def test_face_replacement_preview_clears_on_command_cancel(win):
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 10)
    shell = p.shell(1, remove=[p.face('+z')])
    shell.faces[0].centroid = [1000, 1000, 1000]
    p.doc.regenerate()
    win._set_document(p.doc); win.edit_feature(shell.id)
    cmd = win.command; box = cmd.faces
    box.list.setCurrentRow(0)
    QTest.mouseClick(box.replacements, Qt.LeftButton)
    menu = box.repair_menu
    action = next(a for a in menu.actions() if a.data() is not None)
    QTest.qWait(100)
    menu.setActiveAction(action); QTest.qWait(60)
    assert win.viewer._candidate_outline is not None
    before = p.doc.to_dict()
    win.command_cancel()
    assert win.viewer._candidate_outline is None and p.doc.to_dict() == before


def test_replacement_menu_empty_and_changed_row_cannot_apply_stale_pick(win):
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 10)
    fillet = p.fillet(edges=[0], r=1)
    fillet.edges[0].mid = [1000, 1000, 1000]
    win._set_document(p.doc); win.edit_feature(fillet.id)
    box = win.command.items
    box.list.setCurrentRow(0)
    menu = box.show_replacements()
    action = next(a for a in menu.actions() if a.data() is not None)
    box.clear()
    action.trigger()
    assert not box.items and box.repair_menu is None
    assert not box.replacements.isVisible()
    win.command_cancel()
    fillet.edges[0].kind = 'nonexistent'
    win.edit_feature(fillet.id); box = win.command.items
    box.list.setCurrentRow(0)
    menu = box.show_replacements()
    assert not [a for a in menu.actions() if a.data() is not None]
    assert 'No compatible replacements' in menu.actions()[-1].text()
    assert not menu.actions()[-1].isEnabled()
