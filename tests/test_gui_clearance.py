"""Real clearance controls, highlighted points, stale results and incomplete checks."""
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_gui_views_input import app, pump
from test_interference_evaluate import assembly


def test_clearance_window(app, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    a, first, second = assembly(tmp_path)
    second.move(dx=8)
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        w.a_clearance.trigger(); cw = w.clearance_win
        assert cw.isVisible() and cw.result is None
        cw.required.setValue(4)
        QTest.mouseClick(cw.check_button, Qt.LeftButton)
        assert cw.result['distance'] == pytest.approx(3)
        assert 'BELOW REQUIRED' in cw.text.toPlainText()
        assert 'clearance_lines' in w.viewer.plotter.actors
        assert all(actor.GetProperty().GetOpacity() == .25 for actor, _, _ in w.viewer._asm.values())
        assert cw.second.findData(first.comp.id) < 0
        cw.required.setValue(2)
        assert cw.result is None and 'clearance_lines' not in w.viewer.plotter.actors
        QTest.mouseClick(cw.check_button, Qt.LeftButton)
        assert 'MEETS REQUIRED' in cw.text.toPlainText()
        w.viewer.fit()
        pump(app)
        target = Path('projects/overnight_evaluate'); target.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(target/'clearance-assembly.png'))
        cw.grab().save(str(target/'clearance-report.png'))
        second.move(dx=-4); w.update_poses()
        assert cw.result is None
        QTest.mouseClick(cw.check_button, Qt.LeftButton)
        assert 'INTERFERENCE' in cw.text.toPlainText()
        w.start_measure()
        assert not cw.isVisible() and 'clearance_lines' not in w.viewer.plotter.actors
        assert all(actor.GetProperty().GetOpacity() == 1 for actor, _, _ in w.viewer._asm.values())
        w.show_clearance()
        second.comp.shape = None
        QTest.mouseClick(cw.check_button, Qt.LeftButton)
        assert cw.result is None and 'incomplete' in cw.text.toPlainText()
        w._escape()
        assert not cw.isVisible()
    finally:
        w.dirty = False; w.close()


def test_clearance_cancel(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QProgressDialog
    from bauyrcad.gui.assembly_window import AssemblyWindow
    a, _, _ = assembly(tmp_path)
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        cw = w.show_clearance()
        monkeypatch.setattr(QProgressDialog, 'wasCanceled', lambda _: True)
        QTest.mouseClick(cw.check_button, Qt.LeftButton)
        assert cw.result is None and 'cancelled' in cw.text.toPlainText()
        assert 'clearance_lines' not in w.viewer.plotter.actors
    finally:
        w.dirty = False; w.close()
