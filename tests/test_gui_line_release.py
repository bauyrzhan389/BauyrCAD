"""A continuing line commits once per press, never again on a jittered release."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_gui_views_input import app, win, pump, _sketch, _click, _move, screen_pos

@pytest.mark.parametrize('tool',['line','centerline'])
def test_chain_release_and_hover_do_not_add_points(app,win,tool):
    cmd=_sketch(app,win,tool)
    _click(app,win,0,0)
    widget=win.viewer.plotter.interactor
    start=screen_pos(win,(30,10,0)); end=screen_pos(win,(35,14,0))
    QTest.mousePress(widget,Qt.LeftButton,pos=start)
    pump(app)
    count=len(cmd.sketch.entities); points=len(cmd.sketch.points)
    assert count==1
    QTest.mouseMove(widget,end,30)
    QTest.mouseRelease(widget,Qt.LeftButton,pos=end)
    _move(app,win,50,20)
    assert len(cmd.sketch.entities)==count and len(cmd.sketch.points)==points
    _click(app,win,60,30)
    assert len(cmd.sketch.entities)==2

def test_first_point_drag_still_draws_one_line(app,win):
    cmd=_sketch(app,win,'line')
    widget=win.viewer.plotter.interactor
    start=screen_pos(win,(0,0,0)); end=screen_pos(win,(30,20,0))
    QTest.mousePress(widget,Qt.LeftButton,pos=start)
    QTest.mouseMove(widget,end,30)
    QTest.mouseRelease(widget,Qt.LeftButton,pos=end)
    pump(app)
    assert len(cmd.sketch.entities)==1
