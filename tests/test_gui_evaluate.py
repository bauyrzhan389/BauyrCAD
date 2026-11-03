"""Drive Evaluate in the actual part and assembly windows."""
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolBar

from test_gui_views_input import app, win, plate, pump, screen_pos


def test_evaluate_toolbar_units_history_and_bounds(app, win):
    p = plate()
    win._set_document(p.doc)
    pump(app)
    index = next(i for i in range(win.tabs.count()) if win.tabs.tabText(i) == 'Evaluate')
    QTest.mouseClick(win.tabs.tabBar(), Qt.LeftButton, pos=win.tabs.tabBar().tabRect(index).center())
    tab = win.tabs.widget(index)
    bar = tab if isinstance(tab, QToolBar) else tab.findChild(QToolBar)
    QTest.mouseClick(bar.widgetForAction(win.a_measure), Qt.LeftButton)
    mw = win.measure_win
    assert mw.isVisible()
    mw.add('face', p.face('+z').index)
    mw.add('face', p.face('-z').index)
    assert mw.result['measurement'] == pytest.approx(10)
    mw.units.setCurrentText('cm')
    mw.precision.setValue(2)
    assert 'Minimum distance 1.00 cm' in mw.text.toPlainText()
    QTest.mouseClick(mw.copy_button, Qt.LeftButton)
    assert QApplication.clipboard().text() == mw.text.toPlainText()
    QTest.mouseClick(mw.keep_button, Qt.LeftButton)
    assert len(mw.history) == 1
    QTest.mouseClick(mw.bounds_button, Qt.LeftButton)
    assert np.allclose(mw.result['size'], (80, 50, 10))
    assert 'X: 8.00 cm' in mw.text.toPlainText()
    assert 'measure_bounds_lines' in win.viewer.plotter.actors
    target = Path('projects/evaluate'); target.mkdir(exist_ok=True, parents=True)
    win.grab().save(str(target / 'part-window.png'))
    mw.grab().save(str(target / 'measure-window.png'))
    mw.show_history(1)
    assert 'Saved snapshot' in mw.text.toPlainText()
    assert 'Minimum distance 1.00 cm' in mw.text.toPlainText()
    assert mw.result is None
    assert 'measure_bounds_lines' not in win.viewer.plotter.actors
    win._escape()
    assert not mw.isVisible()


def test_filter_and_point_on_surface(app, win):
    p = plate()
    win._set_document(p.doc)
    win.viewer.set_view('front', animate=False)
    pump(app)
    mw = win.start_measure()
    mw.selection_mode.setCurrentIndex(mw.selection_mode.findData('face'))
    vw = win.viewer.plotter.interactor
    QTest.mouseClick(vw, Qt.LeftButton, pos=screen_pos(win, (80, 50, 10)))
    pump(app)
    assert mw.refs[0][0] == 'face'  # no vertex priority when faces are requested
    mw.clear()
    mw.selection_mode.setCurrentIndex(mw.selection_mode.findData('point'))
    for point in ((10, 10, 10), (20, 10, 10)):
        QTest.mouseClick(vw, Qt.LeftButton, pos=screen_pos(win, point))
        pump(app)
    assert [ref[0] for ref in mw.refs] == ['point', 'point']
    assert mw.result['measurement'] == pytest.approx(10, abs=win.viewer.world_per_pixel()*2)
    assert all(ref[1][2] == pytest.approx(10) for ref in mw.refs)


def test_assembly_measurement_world_coordinates_and_invalidation(app, tmp_path):
    from bauyrcad.core.assembly import Assembly, Pose
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from bauyrcad.core.plane import FRONT
    p = plate(hole=10)
    path = tmp_path / 'plate.bcad'; p.save(path)
    asm = Assembly()
    a = asm.add_component(path, pose=Pose(t=(0, 0, 0)))
    b = asm.add_component(path, pose=Pose(t=(100, 0, 30)))
    w = AssemblyWindow(assembly=asm)
    w.show()
    try:
        pump(app, 10)
        w.viewer.view_normal_to(FRONT, animate=False)
        w.viewer.fit()
        w.a_measure.trigger()
        mw = w.measure_win
        mw.selection_mode.setCurrentIndex(mw.selection_mode.findData('face'))
        for point in ((20, 20, 10), (120, 20, 40)):
            QTest.mouseClick(w.viewer.plotter.interactor, Qt.LeftButton, pos=screen_pos(w, point))
            pump(app)
        assert len(mw.refs) == 2
        assert {mw.face_map[i][0] for _, i in mw.refs} == {a.id, b.id}
        assert mw.result['distance'] == pytest.approx(np.hypot(20, 30))
        assert w._drag is None
        target = Path('projects/evaluate'); target.mkdir(exist_ok=True, parents=True)
        w.grab().save(str(target / 'assembly-window.png'))
        mw.grab().save(str(target / 'assembly-measure.png'))
        # Explicit Select Other preserves the hidden back face instead of repicking front.
        info = next(i for i in w.viewer.pick_candidates(*w.viewer.world_to_display((20, 20, 10)))
                    if i.component == a.id and i.face == p.face('-z').index)
        mw.clear(); w._clicked(info)
        assert mw.face_map[mw.refs[0][1]] == (a.id, p.face('-z').index)
        b.pose.t += np.array([0, 0, 20])
        w.update_poses()
        assert not mw.refs and mw.result is None
        mw.show_dimensions()
        assert np.allclose(mw.result['size'], (180, 50, 60))
        w._escape()
        assert not mw.isVisible()
    finally:
        w.dirty = False
        w.close()
