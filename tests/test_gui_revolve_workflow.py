import math
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from bauyrcad.api import Part
from bauyrcad.core import geometry
from bauyrcad.core.plane import FRONT
from bauyrcad.gui.main_window import MainWindow
from bauyrcad.gui.extrude_command import RevolveCommand


@pytest.fixture
def win():
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.show(); w.raise_(); w.activateWindow()
    QTest.qWait(150)
    yield w
    if w.command:
        w.command_cancel()
    w.dirty = False
    w.close()
    app.processEvents()


@pytest.mark.parametrize('operation', ['boss', 'cut'])
def test_active_sketch_revolve_and_direct_typing(win, operation, tmp_path):
    if operation == 'cut':
        p = Part()
        s = p.sketch()
        s.centerline((0, 0), (0, 20)); s.rect((0, 0), (10, 20))
        p.revolve(s)
        win._set_document(p.doc)
    win.start_sketch(plane=FRONT)
    cmd = win.command
    cmd.sketch.add_line((0, 0), (0, 20), construction=True)
    cmd.sketch.add_rectangle((2, 5), (5, 15))
    cmd.changed(checkpoint=False)
    win.viewer.finish_animation()
    action = win.a_revolve if operation == 'boss' else win.a_revolve_cut
    QTest.mouseClick(win.tabs.widget(1).widgetForAction(action), Qt.LeftButton)
    QTest.qWait(150)
    cmd = win.command
    assert isinstance(cmd, RevolveCommand)
    assert cmd.sketch_cb.currentData() == win.doc.sketches()[-1].id
    assert cmd.angle.hasFocus() or cmd.angle.lineEdit().hasFocus()
    QTest.keyClicks(QApplication.focusWidget(), '180')
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)
    QTest.qWait(100)
    assert cmd.angle.value() == 180
    win.grab().save(str(tmp_path / f'revolve-{operation}.png'))
    win.command_ok()
    tool_volume = math.pi * (25 - 4) * 10 / 2
    expected = tool_volume if operation == 'boss' else math.pi * 100 * 20 - tool_volume
    assert geometry.volume(win.doc.body) == pytest.approx(expected)
    win.undo()
    assert len(win.doc.sketches()[-1].sketch.entities) == 5
    win.redo()
    assert geometry.volume(win.doc.body) == pytest.approx(expected)


def test_empty_sketch_and_cancelled_revolve(win):
    win.start_sketch(plane=FRONT)
    cmd = win.command
    win.start_revolve('boss')
    assert win.command is cmd
    cmd.sketch.add_line((0, 0), (0, 20), construction=True)
    cmd.sketch.add_rectangle((2, 5), (5, 15))
    cmd.changed(checkpoint=False)
    win.start_revolve('boss')
    win.command_cancel()
    assert len(win.doc.sketches()) == 1
    assert len(win.doc.sketches()[0].sketch.entities) == 5


@pytest.mark.parametrize('action,field', [('a_fillet','r'), ('a_chamfer','d'), ('a_shell','t')])
def test_primary_feature_value_accepts_immediate_typing(win, action, field):
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 20)
    win._set_document(p.doc)
    getattr(win, action).trigger()
    QTest.qWait(150)
    box = getattr(win.command, field)
    assert box.hasFocus() or box.lineEdit().hasFocus()
    QTest.keyClicks(QApplication.focusWidget(), '3.5')
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)
    assert box.value() == pytest.approx(3.5)
