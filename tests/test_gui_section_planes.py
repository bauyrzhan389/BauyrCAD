"""Angled reference planes, world-space assembly transforms and planar face picking."""
import json
import numpy as np
import pyvista as pv
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_gui_views_input import app, win, pump, plate, screen_pos
from bauyrcad.api import Part, Assembly
from bauyrcad.gui.viewer import PickInfo


def angled_part():
    p = Part('Angled section')
    sketch = p.sketch('front'); sketch.rect((0, 0), (10, 10)); p.extrude(sketch, 10)
    plane = p.plane(points=[(10, 0, 0), (0, 10, 0), (10, 0, 10)])
    return p, plane


def test_reference_plane_section_offset_and_hole_cap(app, win):
    p, reference = angled_part()
    win._set_document(p.doc)
    shape = p.doc.body
    sw = win.show_section()
    sw.plane.setCurrentIndex(sw.plane.findText(reference.name))
    assert sw.offset.value() == 0
    assert sw.position_label.text() == 'Offset from plane'
    assert sw.position_label.width() >= sw.position_label.fontMetrics().horizontalAdvance(sw.position_label.text())
    cap = pv.wrap(win.viewer.section.caps[0].GetMapper().GetInput())
    assert cap.area == pytest.approx(100*np.sqrt(2))
    assert np.allclose(cap.points[:, 0] + cap.points[:, 1], 10)
    sw.offset.setValue(1)
    plane = win.viewer.section.plane
    assert np.allclose(plane.GetOrigin(), reference.result_plane.origin + reference.result_plane.normal)
    origin_before_flip = np.array(plane.GetOrigin())
    sw.flip.setChecked(True)
    assert np.allclose(origin_before_flip, win.viewer.section.plane.GetOrigin())
    assert np.allclose(win.viewer.section.plane.GetNormal(), reference.result_plane.normal)
    sw.grab().save('projects/overnight_evaluate/section-planes-root.png')
    assert p.doc.body is shape and not win.dirty


def test_assembly_component_reference_plane_world_transform(app, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    p, reference = angled_part(); p.save(tmp_path/'angled.bcad')
    a = Assembly(); c = a.add(tmp_path/'angled.bcad', at=(30, 20, 10))
    c.rotate('z', 30, about=(0, 0, 0))
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        snapshot = json.dumps(a.asm.to_dict())
        sw = w.show_section()
        sw.plane.setCurrentIndex(sw.plane.findText(f'{c.name} / {reference.name}'))
        origin = c.comp.pose.point(reference.result_plane.origin)
        normal = c.comp.pose.R @ reference.result_plane.normal
        assert np.allclose(w.viewer.section.plane.GetOrigin(), origin)
        assert np.allclose(w.viewer.section.plane.GetNormal(), -normal)
        sw.offset.setValue(2)
        for actor in w.viewer.section.caps:
            points = pv.wrap(actor.GetMapper().GetInput()).points
            assert np.allclose((points-origin) @ normal, 2)
        assert json.dumps(a.asm.to_dict()) == snapshot and not w.dirty
    finally:
        w.dirty = False; w.close()


def test_pick_planar_face_rejects_curves_and_captures_real_click(app, win):
    p = plate()
    win._set_document(p.doc)
    v = win.viewer; v.set_view('front', animate=False)
    sw = win.show_section(); sw.cap.setChecked(False)
    QTest.mouseClick(sw.pick_face, Qt.LeftButton)
    curved = next(f.index for f in p.doc.faces if not f.is_planar)
    win._viewer_click(PickInfo(kind='face', face=curved))
    assert sw.awaiting_face
    win._viewer_click(PickInfo(kind='section_cap'))
    assert sw.awaiting_face
    pos = screen_pos(win, (10, 10, 0))
    QTest.mouseClick(v.plotter.interactor, Qt.LeftButton, pos=pos)
    pump(app)
    assert not sw.awaiting_face and sw.plane.currentText() == 'Picked face'
    assert sw.offset.value() == 0
    assert np.allclose(sw.basis()[1], (0, 0, -1))
    sw.offset.setValue(-2)
    assert np.allclose(v.section.plane.GetOrigin()[2], 2)
    assert not win.dirty
