"""Keyboard-first Extrude and native delayed rich hover help."""
import time
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QLabel, QToolTip, QToolButton
from PySide6.QtTest import QTest
from bauyrcad.api import Part
from bauyrcad.core import geometry
from bauyrcad.gui.main_window import MainWindow
from bauyrcad.gui.command_help import tooltip, _DIAGRAMS, _ALIASES

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])

def pump(app):
    for _ in range(8):
        app.processEvents()
        time.sleep(0.01)

@pytest.fixture
def win(app):
    w=MainWindow()
    w.show();w.raise_();w.activateWindow()
    pump(app)
    yield w
    QToolTip.hideText()
    if w.command is not None:
        w.command_cancel()
    w.dirty=False
    w.close()
    pump(app)

@pytest.mark.parametrize("operation", ["boss", "cut"])
def test_type_depth_immediately_after_toolbar_click(app, win, operation):
    part=Part()
    sk=part.sketch()
    sk.rect((0,0),(20,10))
    if operation == "cut":
        part.extrude(sk,30)
    win._set_document(part.doc)
    action=win.a_boss if operation == "boss" else win.a_cut
    button=win.tabs.widget(0).widgetForAction(action)
    QTest.mouseClick(button,Qt.LeftButton)
    pump(app)
    cmd=win.command
    assert cmd.depth.value() == 10
    assert cmd.depth.hasFocus() or cmd.depth.lineEdit().hasFocus()
    assert "10" in cmd.depth.lineEdit().selectedText()
    # Send keys to the actual focus widget, without selecting or clicking the depth.
    QTest.keyClicks(QApplication.focusWidget(),"7.5")
    QTest.keyClick(QApplication.focusWidget(),Qt.Key_Return)
    pump(app)
    assert cmd.depth.value() == pytest.approx(7.5)
    assert not win.cmdline.isVisible()
    if operation == "cut":
        cmd.reverse.setChecked(True)
    win.command_ok()
    assert geometry.volume(win.doc.body) == pytest.approx(1500 if operation == "boss" else 4500)

def test_native_tooltip_shows_name_description_and_diagram(app,win,tmp_path):
    button=next(b for b in win.tabs.widget(0).findChildren(QToolButton)
                if b.defaultAction() is win.a_shell)
    pos=button.rect().center()
    QTest.mouseMove(button,pos)
    assert not QToolTip.isVisible()
    # The native hover timer sends this event once the hover delay expires.
    QApplication.sendEvent(button,QHelpEvent(QEvent.ToolTip,pos,button.mapToGlobal(pos)))
    pump(app)
    assert QToolTip.isVisible()
    assert "<b>Shell</b>" in QToolTip.text()
    assert "hollow" in QToolTip.text().lower()
    assert "data:image/png;base64," in QToolTip.text()
    popup=next(w for w in QApplication.topLevelWidgets() if isinstance(w,QLabel) and w.isVisible()
               and "data:image/png;base64," in w.text())
    image=popup.grab().toImage()
    assert image.width() >= 310 and image.height() > 150
    assert any(image.pixelColor(x,y).blue() > image.pixelColor(x,y).red()+15
               for x in range(10,image.width()-10,5) for y in range(80,image.height()-10,5))
    popup.grab().save(str(tmp_path/"shell-hover-help.png"))
    QTest.mouseClick(button,Qt.LeftButton)
    pump(app)
    assert not QToolTip.isVisible()

def test_every_complex_tool_has_embedded_visual(app):
    for name in set(_DIAGRAMS)|set(_ALIASES):
        text=tooltip("Example command",icon_name=name)
        assert "<b>Example command</b>" in text
        assert "<p>" in text and "data:image/png;base64," in text
    assert "&lt;" in tooltip("<Name>","Description & details")
    assert "&amp;" in tooltip("<Name>","Description & details")


def test_name_only_sketch_tooltips_get_a_description(app,win):
    text=win.sketch_actions["circle"].toolTip()
    assert "center and radius" in text
    assert "<b>" in text
    # Command search must retain plain descriptions, not tooltip HTML or image data.
    assert "<" not in win.a_shell.statusTip()
    assert "data:image" not in win.a_shell.statusTip()


def test_context_buttons_and_group_menus_have_help(app,win):
    from PySide6.QtWidgets import QMenu
    win.context_bar.show_at(win.mapToGlobal(QPoint(100,100)),
                            [("fillet3d","Fillet",lambda: None)])
    button=win.context_bar.findChild(QToolButton)
    assert "Round selected" in button.toolTip()
    assert "data:image/png;base64," in button.toolTip()
    assert win.context_bar.trigger("Fillet")
    assert all(menu.toolTipsVisible() for menu in win.findChildren(QMenu))
