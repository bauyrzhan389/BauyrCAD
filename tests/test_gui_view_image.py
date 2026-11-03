"""Viewport exports preserve sections and restore rendering state after errors."""
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from test_gui_views_input import app, win, pump, plate
from bauyrcad.gui.view_image import save_png, capture, copy_view


def test_png_transparency_resolution_and_clipboard(app, win, tmp_path):
    win._set_document(plate().doc)
    win.show_section()
    pump(app)
    v = win.viewer
    gradient = v.renderer.GetGradientBackground()
    shape = win.doc.body
    cap = v.section.caps[0]
    path = tmp_path/'view.png'
    size = save_png(v, path, scale=2, background='transparent')
    image = QImage(str(path))
    assert not image.isNull() and image.hasAlphaChannel()
    assert size.width() == 2*v.plotter.window_size[0]
    assert size.height() == 2*v.plotter.window_size[1]
    assert image.pixelColor(0, 0).alpha() == 0
    assert v.renderer.GetGradientBackground() == gradient
    assert v.section.caps[0] is cap and win.doc.body is shape
    white = capture(v, background='white')
    assert white.pixelColor(0, 0).getRgb() == (255, 255, 255, 255)
    assert v.renderer.GetGradientBackground() == gradient
    copy_view(win)
    assert not QApplication.clipboard().image().isNull()
    assert QApplication.clipboard().image().size().width() == v.plotter.window_size[0]
    save_png(v, tmp_path/'view-current-background.png', scale=1)


def test_failed_capture_restores_view_and_existing_file(app, win, tmp_path, monkeypatch):
    win._set_document(plate().doc)
    v = win.viewer
    original = v.renderer.GetGradientBackground()
    path = tmp_path/'existing.png'; path.write_bytes(b'keep-existing')
    def fail(*a, **kw):
        raise RuntimeError('capture failed')
    monkeypatch.setattr(v.plotter, 'screenshot', fail)
    with pytest.raises(RuntimeError, match='capture failed'):
        save_png(v, path, background='transparent')
    assert v.renderer.GetGradientBackground() == original
    assert path.read_bytes() == b'keep-existing'
    with pytest.raises(ValueError):
        capture(v, scale=0)


def test_assembly_save_action_and_copy(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from test_interference_evaluate import assembly
    a, _, _ = assembly(tmp_path)
    window = AssemblyWindow(assembly=a.asm)
    window.show()
    try:
        pump(app)
        window.show_section()
        path = tmp_path/'assembly.png'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(path), 'PNG'))
        def accept_options():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, QDialog)
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.Save).click()
        QTimer.singleShot(100, accept_options)
        window.a_view_image.trigger()
        image = QImage(str(path))
        assert not image.isNull()
        assert image.width() == 2*window.viewer.plotter.window_size[0]
        assert image.pixelColor(0, 0).getRgb() == (255, 255, 255, 255)
        assert window.viewer.section.active
        window.a_copy_view.trigger()
        assert QApplication.clipboard().image().width() == window.viewer.plotter.window_size[0]
        assert not window.dirty
    finally:
        window.dirty = False
        window.close()
