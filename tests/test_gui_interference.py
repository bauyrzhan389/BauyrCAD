"""Actual Evaluate window: overlap solid, exclusions, invalidation and failure states."""
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_gui_views_input import app, pump
from test_interference_evaluate import assembly


def test_interference_window(app,tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    a, first, second=assembly(tmp_path)
    w=AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        w.a_interference.trigger(); iw=w.interference_win
        assert iw.isVisible() and iw.result[0]['volume'] == pytest.approx(500)
        assert 'interference' in w.viewer.plotter.actors
        assert all(entry[0].GetProperty().GetOpacity() == pytest.approx(.18) for entry in w.viewer._asm.values())
        target=Path('projects/interference'); target.mkdir(parents=True,exist_ok=True)
        iw.grab().save(str(target/'interference-results.png'))
        w.grab().save(str(target/'interference-window.png'))
        iw.copy_report(); assert '500' in QApplication.clipboard().text()
        QTest.mouseClick(iw.ignore_button,Qt.LeftButton)
        assert iw.result == [] and len(iw.ignored) == 1
        assert 'Ignored pairs: 1' in iw.text.toPlainText()
        QTest.mouseClick(iw.reset_button,Qt.LeftButton)
        assert len(iw.result) == 1
        second.move(dx=10); w.update_poses()
        assert iw.result is None and not iw.ignored
        assert 'interference' not in w.viewer.plotter.actors
        assert all(entry[0].GetProperty().GetOpacity() == 1 for entry in w.viewer._asm.values())
        QTest.mouseClick(iw.check_button,Qt.LeftButton)
        assert iw.result == [] and '0 overlapping pairs' in iw.text.toPlainText()
        second.comp.shape=None
        QTest.mouseClick(iw.check_button,Qt.LeftButton)
        assert iw.result is None and 'incomplete' in iw.text.toPlainText()
        w._escape(); assert not iw.isVisible()
    finally:
        w.dirty=False; w.close()


def test_cancelled_window_does_not_report_clear(app,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QProgressDialog
    from bauyrcad.gui.assembly_window import AssemblyWindow
    a,_,_=assembly(tmp_path)
    w=AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        monkeypatch.setattr(QProgressDialog,'wasCanceled',lambda _:True)
        iw=w.show_interference()
        assert iw.result is None
        assert 'cancelled' in iw.text.toPlainText()
        assert 'interference' not in w.viewer.plotter.actors
        iw.close()
    finally:
        w.dirty=False; w.close()
