"""Real read-only geometry checks and stale/cancelled/incomplete reporting."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_gui_views_input import app, win, pump, plate


def test_part_geometry_check_and_rebuild_invalidation(app, win):
    win._set_document(plate().doc)
    win.a_check_geometry.trigger()
    window = win.geometry_check_win
    QTest.mouseClick(window.check_button, Qt.LeftButton)
    assert window.result['complete'] and window.result['closed_solid']
    assert 'VALID CLOSED SOLID GEOMETRY' in window.text.toPlainText()
    window.grab().save('projects/overnight_evaluate/geometry-check-root.png')
    assert not win.dirty
    win.doc.features[-1].depth = 20
    win.rebuild()
    assert window.result is None
    QTest.mouseClick(window.check_button, Qt.LeftButton)
    assert window.result['closed_solid']
    win._escape()
    assert not window.isVisible()


def test_assembly_missing_hidden_geometry_and_cancel(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QProgressDialog
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from test_interference_evaluate import assembly
    a, first, second = assembly(tmp_path)
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        w.visibility.toggle(second.comp.id)
        window = w.show_geometry_check()
        QTest.mouseClick(window.check_button, Qt.LeftButton)
        assert window.result['ok'] and len(window.result['components']) == 2
        second.comp.shape = None
        w.refresh()
        assert window.result is None
        QTest.mouseClick(window.check_button, Qt.LeftButton)
        assert not window.result['complete'] and 'CHECK INCOMPLETE' in window.text.toPlainText()
        monkeypatch.setattr(QProgressDialog, 'wasCanceled', lambda _: True)
        QTest.mouseClick(window.check_button, Qt.LeftButton)
        assert window.result is None and 'cancelled' in window.text.toPlainText()
        assert not w.dirty
    finally:
        w.dirty = False; w.close()


@pytest.mark.parametrize('tool', ['geometry', 'clearance', 'interference'])
def test_cancellation_survives_the_final_progress_step(app, tmp_path, monkeypatch, tool):
    from PySide6.QtWidgets import QProgressDialog
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from bauyrcad.core import geometry_check
    from test_interference_evaluate import assembly
    a, _, _ = assembly(tmp_path)
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        window = {'geometry': w.show_geometry_check, 'clearance': w.show_clearance,
                  'interference': w.show_interference}[tool]()
        def cancelled_at_completion(*args, progress, **kwargs):
            # Interference checks once on opening; its old dialog may await deleteLater.
            dialog = window.findChildren(QProgressDialog)[-1]
            dialog.cancel()
            progress(1, 1)
            raise AssertionError('Completion callback did not abort')
        target, method = ((geometry_check, 'assembly') if tool == 'geometry' else
                          (w.asm, 'clearance' if tool == 'clearance' else 'interference'))
        monkeypatch.setattr(target, method, cancelled_at_completion)
        QTest.mouseClick(window.check_button, Qt.LeftButton)
        assert 'Check cancelled' in window.text.toPlainText()
    finally:
        w.dirty = False; w.close()
