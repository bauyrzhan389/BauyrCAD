import numpy as np
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

from bauyrcad.api import Part
from bauyrcad.core import geometry
from bauyrcad.core.plane import FRONT
from test_gui_revolve_workflow import win


def screen(v, point):
    widget = v.plotter.interactor
    xy = v.world_to_display(point) / widget.devicePixelRatioF()
    return QPoint(round(xy[0]), round(widget.height() - xy[1]))


def setup_plate(win):
    p = Part()
    s = p.sketch("front")
    s.rect((0, 0), (100, 60))
    p.extrude(s, 10)
    win._set_document(p.doc)
    v = win.viewer
    v.view_normal_to(FRONT, animate=False)
    QTest.qWait(80)
    return v


def test_cursor_pivot_stays_stationary_during_real_middle_drag(win, tmp_path):
    v = setup_plate(win)
    v.pick_faces = False  # rotation must work inside feature commands too
    widget = v.plotter.interactor
    start = screen(v, (15, 15, 10))
    frame = v.camera_frame().copy()
    scale = v.renderer.GetActiveCamera().GetParallelScale()
    QTest.mousePress(widget, Qt.MiddleButton, Qt.NoModifier, start)
    pivot = v._orbit_pivot.copy()
    assert np.allclose(pivot, (15, 15, 10), atol=v.world_per_pixel() * 2)
    before = v.world_to_display(pivot)
    for step in ((20, 10), (40, 20), (60, 25)):
        QTest.mouseMove(widget, start + QPoint(*step), 20)
        assert np.allclose(v.world_to_display(pivot), before, atol=0.05)
        assert np.allclose(v._orbit_pivot, pivot)
    assert not np.allclose(v.camera_frame(), frame)
    assert v.renderer.GetActiveCamera().GetParallelScale() == pytest.approx(scale)
    QTest.mouseRelease(widget, Qt.MiddleButton, Qt.NoModifier, start + QPoint(60, 25))
    assert v._orbit_pivot is None
    win.grab().save(str(tmp_path / "cursor-orbit.png"))


def test_nearest_silhouette_point_and_transformed_assembly(win):
    v = setup_plate(win)
    xy = v.world_to_display((110, 27, 10))
    assert np.allclose(v.rotation_pivot(*xy), (100, 27, 10), atol=1e-5)
    tess = geometry.tessellate(win.doc.body)
    v.set_body(None)
    transform = np.eye(4)
    transform[:3, 3] = (40, 20, 30)
    v.set_assembly([("c1", tess, transform, (150, 170, 180))])
    xy = v.world_to_display((60, 40, 40))
    assert np.allclose(v.rotation_pivot(*xy), (60, 40, 40), atol=1e-5)
    xy = v.world_to_display((150, 47, 40))
    assert np.allclose(v.rotation_pivot(*xy), (140, 47, 40), atol=1e-5)
    v.set_assembly([])
    assert np.allclose(v.rotation_pivot(20, 20), v.renderer.GetActiveCamera().GetFocalPoint())


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.ShiftModifier])
def test_modified_middle_drag_still_pans(win, modifier):
    v = setup_plate(win)
    widget = v.plotter.interactor
    start = screen(v, (30, 20, 10))
    frame = v.camera_frame().copy()
    focal = np.array(v.renderer.GetActiveCamera().GetFocalPoint())
    QTest.mousePress(widget, Qt.MiddleButton, modifier, start)
    QTest.mouseMove(widget, start + QPoint(30, 20), 20)
    QTest.mouseRelease(widget, Qt.MiddleButton, modifier, start + QPoint(30, 20))
    assert np.allclose(v.camera_frame(), frame)
    assert not np.allclose(v.renderer.GetActiveCamera().GetFocalPoint(), focal)
