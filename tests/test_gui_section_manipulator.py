"""Section handle gestures are view-only and coexist with manager tabs and camera input."""
from pathlib import Path
import numpy as np
import pytest
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from test_gui_views_input import app,win,pump,plate

def pos(viewer,xy):
    widget=viewer.plotter.interactor; ratio=widget.devicePixelRatioF()
    return QPoint(round(xy[0]/ratio),round(widget.height()-xy[1]/ratio))

def drag_handle(app,w,name):
    control=w.section_win; m=control.manipulator; viewer=w.viewer
    path=m.paths[name].copy()
    found=None
    for index in range(8,len(path)-8) if name!='offset' else [0]:
        start=path[index] if name!='offset' else path[0]*.15+path[1]*.85
        if m.press(*start):
            matched=m.drag['name']==name; m.release()
            if matched:
                found=(start,path[min(index+10,len(path)-1)] if name!='offset' else start+(path[1]-path[0])*.25)
                break
    assert found is not None, name
    a,b=found; widget=viewer.plotter.interactor
    QTest.mousePress(widget,Qt.LeftButton,pos=pos(viewer,a))
    assert viewer._section_drag
    assert m.drag['name']==name, m.drag
    for fraction in np.linspace(.1,1,7): QTest.mouseMove(widget,pos(viewer,a+(b-a)*fraction),15)
    QTest.mouseRelease(widget,Qt.LeftButton,pos=pos(viewer,b)); pump(app)
    assert not viewer._section_drag

@pytest.mark.parametrize('name',['offset','x','y'])
def test_drag_section_handles_without_geometry_or_camera_changes(app,win,name):
    win._set_document(plate().doc); win.viewer.view_isometric(animate=False)
    control=win.show_section(); pump(app)
    body=win.doc.body; camera=win.viewer.renderer.GetActiveCamera(); position=camera.GetPosition()
    before=control.offset.value() if name=='offset' else control.angles[0 if name=='x' else 1].value()
    drag_handle(app,win,name)
    after=control.offset.value() if name=='offset' else control.angles[0 if name=='x' else 1].value()
    assert abs(after-before)>.1
    assert win.doc.body is body and not win.dirty and camera.GetPosition()==position
    origin,normal=control.basis()
    assert np.allclose(win.viewer.section.plane.GetOrigin(),origin+normal*control.offset.value())
    assert np.allclose(win.viewer.section.plane.GetNormal(),-normal)
    Path('projects/section_interaction').mkdir(exist_ok=True)
    win.grab().save(f'projects/section_interaction/section-{name}.png')

def test_keep_edit_close_and_manager_tabs(app,win):
    win._set_document(plate().doc); c=win.show_section(); c.angles[0].setValue(30)
    origin=np.array(win.viewer.section.plane.GetOrigin()); normal=np.array(win.viewer.section.plane.GetNormal())
    assert win.left.currentWidget() is c.panel and win.manager_tabs.currentIndex()==1
    c.accept(); pump(app)
    assert win.left.currentWidget() is win.tree and win.viewer.section.active
    assert all(not a.GetVisibility() for a in c.manipulator.actors)
    win.manager_tabs.setCurrentIndex(1); pump(app)
    assert c.isVisible()
    win.show_section()
    assert np.allclose(win.viewer.section.plane.GetNormal(),normal)
    assert np.allclose(win.viewer.section.plane.GetOrigin(),origin)
    c.close(); assert not win.viewer.section.active and win.left.currentWidget() is win.tree
    assert not win.a_section.isChecked()

def test_assembly_section_rotation_and_workspace(app,tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from test_interference_evaluate import assembly
    a,first,second=assembly(tmp_path); w=AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app); w.viewer.view_isometric(animate=False)
        c=w.show_section(); c.angles[1].setValue(25); pump(app)
        normal=np.array(w.viewer.section.plane.GetNormal())
        assert not np.allclose(normal,(0,0,-1))
        drag_handle(app,w,'offset')
        assert not w.dirty
        w.grab().save('projects/section_interaction/assembly-section.png')
        c.accept(); assert w.left.currentWidget() is w.tree
    finally:
        w.dirty=False; w.close()


def test_image_capture_hides_edit_handles_and_restores_on_failure(app,win,monkeypatch):
    from bauyrcad.gui.view_image import capture
    win._set_document(plate().doc); c=win.show_section(); pump(app)
    assert any(a.GetVisibility() for a in c.manipulator.actors)
    def screenshot(**kwargs):
        assert all(not a.GetVisibility() for a in c.manipulator.actors)
        raise RuntimeError('capture failed')
    monkeypatch.setattr(win.viewer.plotter,'screenshot',screenshot)
    with pytest.raises(RuntimeError,match='capture failed'): capture(win.viewer)
    assert all(a.GetVisibility() for a in c.manipulator.actors)
    assert not c.manipulator.capture_hidden


def test_property_tab_returns_to_section_after_sketch_and_new_file_clears_it(app,win):
    from test_gui_views_input import _sketch
    win._set_document(plate().doc); c=win.show_section(); c.accept()
    _sketch(app,win,'line'); win.command_cancel()
    win.manager_tabs.setCurrentIndex(1); pump(app)
    assert win.left.currentWidget() is c.panel
    win._set_document(plate(100,60).doc)
    assert not win.viewer.section.active and not win.a_section.isChecked()
    assert win.left.currentWidget() is win.tree
