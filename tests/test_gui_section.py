"""Section caps preserve holes, topology selection and world-space assembly positions."""
from pathlib import Path
import numpy as np
import pyvista as pv
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from test_gui_views_input import app, win, pump, plate


def test_section_hole_picking_and_restore(app, win):
    p = plate()
    win._set_document(p.doc)
    v = win.viewer
    v.set_view('front', animate=False)
    pump(app)
    shape = p.doc.body
    win.a_section.trigger()
    sw = win.section_win
    pump(app)
    assert sw.offset.value() == 5
    assert len(v.section.caps) == 1
    cap = pv.wrap(v.section.caps[0].GetMapper().GetInput())
    # Triangulated circle area is approximate, but the hole must remain open.
    assert cap.area == pytest.approx(80 * 50 - np.pi * 10**2, rel=.002)
    assert np.allclose(cap.points[:, 2], 5)
    x, y = v.world_to_display((10, 10, 5))[:2]
    assert v.pick(x, y).kind == 'section_cap'
    x, y = v.world_to_display((40, 25, 5))[:2]
    assert v.pick(x, y).kind is None  # through-hole, including cap
    QTest.mouseClick(sw.cap, Qt.LeftButton, pos=QPoint(8, sw.cap.height() // 2))
    x, y = v.world_to_display((10, 10, 5))[:2]
    hit = v.pick(x, y)
    assert hit.kind == 'face' and hit.point[2] == pytest.approx(0)
    assert all(h.point[2] <= 5.00001 for h in v.pick_candidates(x, y))
    QTest.mouseClick(sw.flip, Qt.LeftButton, pos=QPoint(8, sw.flip.height() // 2))
    assert v.pick(x, y).point[2] == pytest.approx(10)
    sw.plane.setCurrentIndex(2)
    sw.slider.setValue(250)
    assert sw.offset.value() == 20
    QTest.mouseClick(sw.cap, Qt.LeftButton, pos=QPoint(8, sw.cap.height() // 2))
    sw.flip.setChecked(False)
    sw.plane.setCurrentIndex(0)
    first_cap = v.section.caps[0]
    v.render()
    assert v.section.caps[0] is first_cap
    v.view_isometric(animate=False)
    pump(app)
    target = Path('projects/overnight_evaluate'); target.mkdir(parents=True, exist_ok=True)
    win.grab().save(str(target / 'section-part.png'))
    sw.grab().save(str(target / 'section-controls.png'))
    sw.close()
    assert not v.section.active and not v.section.caps
    assert v._body_actor.GetMapper().GetNumberOfClippingPlanes() == 0
    assert p.doc.body is shape


def test_section_assembly_movement_and_suspension(app, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from test_interference_evaluate import assembly
    from bauyrcad.core.plane import FRONT
    a, first, second = assembly(tmp_path)
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        sw = w.show_section()
        v = w.viewer
        assert len(v.section.caps) == 2
        second.move(dz=20)
        w.update_poses()
        assert len(v.section.caps) == 1
        second.move(dz=-20)
        second.rotate('x', 45, about=(5, 5, 5))
        w.update_poses()
        assert len(v.section.caps) == 2
        for cap in v.section.caps:
            assert np.allclose(pv.wrap(cap.GetMapper().GetInput()).points[:, 2], 5)
        v.enter_sketch_mode(FRONT)
        assert not v.section.active and not v.section.caps
        v.exit_sketch_mode()
        assert v.section.active and len(v.section.caps) == 2
        w.grab().save('projects/overnight_evaluate/section-assembly.png')
        sw.close()
        assert all(a.GetMapper().GetNumberOfClippingPlanes() == 0 for a, m, e in v._asm.values())
    finally:
        w.dirty = False; w.close()


def test_removed_vertices_and_partial_edges(app, win):
    win._set_document(plate().doc)
    v = win.viewer
    v.section.set((40, 0, 0), (1, 0, 0), False)
    assert list(v.section.lines(np.array([[0, 0, 0], [10, 0, 0]]))) == []
    segment = list(v.section.lines(np.array([[0, 0, 0], [80, 0, 0]])))[0]
    assert np.allclose(segment, [[40, 0, 0], [80, 0, 0]])
    v.set_view('front', animate=False)
    x, y = v.world_to_display((0, 0, 10))[:2]
    assert v.pick_topology(x, y, vertices=[[0, 0, 10]], kinds={'vertex'}) is None
    assert not v.pick_candidates(x, y, kinds={'vertex'}, vertices=[[0, 0, 10]])
