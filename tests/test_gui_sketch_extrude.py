"""Active-sketch extrusion and SVG icons in a real Qt window."""
import time
import numpy as np
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QToolBar
from PySide6.QtTest import QTest
from bauyrcad.api import Part
from bauyrcad.core import geometry
from bauyrcad.core.plane import FRONT
from bauyrcad.core.sketch import builders
from bauyrcad.gui.extrude_command import ExtrudeCommand
from bauyrcad.gui.main_window import MainWindow
from bauyrcad.gui.sketcher import SketchCommand
from bauyrcad.gui import icons

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def pump(app):
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)

@pytest.fixture
def win(app):
    w = MainWindow()
    w.show()
    w.raise_()
    w.activateWindow()
    pump(app)
    yield w
    if w.command is not None:
        w.command_cancel()
    w.dirty = False
    w.close()
    pump(app)

@pytest.mark.parametrize("operation", ["boss", "cut"])
def test_extrude_active_new_sketch(app, win, operation, tmp_path):
    if operation == "cut":
        part = Part()
        sk = part.sketch()
        sk.rect((0, 0), (40, 30))
        part.extrude(sk, 10)
        win._set_document(part.doc)
    win.start_sketch("rect_corner", plane=FRONT)
    cmd = win.command
    builders.corner_rectangle(cmd.sketch, (5, 5), (15, 15))
    cmd.changed(checkpoint=False)
    win.viewer.finish_animation()
    assert win.tabs.currentIndex() == 1  # Extrude is available on the Sketch tab
    action = win.a_boss if operation == "boss" else win.a_cut
    button = win.tabs.widget(1).widgetForAction(action)
    QTest.mouseClick(button, Qt.LeftButton)
    pump(app)
    assert isinstance(win.command, ExtrudeCommand)
    sf = win.doc.sketches()[-1]
    assert len(sf.sketch.entities) == 4
    assert win.command.sketch_cb.currentData() == sf.id
    win.command.depth.setValue(5)
    if operation == "cut":
        win.command.reverse.setChecked(True)
    win.grab().save(str(tmp_path / (operation + "_from_sketch.png")))
    win.command_ok()
    assert win.command is None
    assert geometry.volume(win.doc.body) == pytest.approx(500 if operation == "boss" else 11500)
    win.undo()
    assert len(win.doc.sketches()[-1].sketch.entities) == 4
    if operation == "boss":
        assert win.doc.body is None
    win.redo()
    assert geometry.volume(win.doc.body) == pytest.approx(500 if operation == "boss" else 11500)

def test_extrude_edited_sketch_and_cancel(app, win):
    part = Part()
    sk = part.sketch()
    sk.rect((0, 0), (20, 10))
    win._set_document(part.doc)
    sf = win.doc.sketches()[0]
    win.edit_feature(sf.id)
    builders.circle(win.command.sketch, (10, 5), 2)
    win.command.changed(checkpoint=False)
    win.start_extrude("boss")
    assert isinstance(win.command, ExtrudeCommand)
    assert win.command.sketch_cb.currentData() == sf.id
    assert len(win.doc.sketches()) == 1
    assert len(sf.sketch.entities) == 5
    win.command_cancel()
    assert len(sf.sketch.entities) == 5

def test_empty_sketch_stays_editable(app, win):
    win.start_sketch(plane=FRONT)
    cmd = win.command
    win.start_extrude("boss")
    assert win.command is cmd and isinstance(cmd, SketchCommand)
    assert not win.doc.features

def test_all_icons_render_at_arbitrary_size_and_density(app):
    for name in icons._ICONS:
        ic = icons.icon(name)
        assert not ic.isNull(), name
        for size, dpr in ((30, 1.0), (37, 1.5), (512, 2.0)):
            pm = ic.pixmap(QSize(size, size), dpr, QIcon.Normal, QIcon.Off)
            assert pm.width() == round(size * dpr), name
            assert pm.height() == round(size * dpr), name
            assert pm.devicePixelRatio() == pytest.approx(dpr), name
            im = pm.toImage()
            assert any(im.pixelColor(x, y).alpha() for x in range(0, im.width(), max(1, im.width() // 24))
                       for y in range(0, im.height(), max(1, im.height() // 24))), name
