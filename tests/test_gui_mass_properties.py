"""Real-window material assignment, mass calculation, undo and geometry refresh."""
from pathlib import Path
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from test_gui_views_input import app, win, pump
from test_mass_properties import block


def test_part_material_mass_undo_and_rebuild(app, win, tmp_path):
    win._set_document(block().doc)
    pump(app)
    win.a_mass.trigger()
    mw = win.mass_win
    assert mw.isVisible() and mw.result['mass'] is None
    combo = mw.table.cellWidget(0, 1)
    combo.setCurrentIndex(combo.findData('Aluminium 6061-T6'))
    QTest.mouseClick(mw.apply_button, Qt.LeftButton)
    assert mw.result['mass'] == pytest.approx(.0162)
    assert win.dirty
    assert 'mass_cg_pts' in win.viewer.plotter.actors
    win.doc.save(tmp_path/'physical.bcad')
    from bauyrcad.core.document import Document
    assert Document.load(tmp_path/'physical.bcad').mass_material['density'] == 2700
    win.undo(); pump(app)
    assert mw.result['mass'] is None
    assert 'mass_cg_pts' not in win.viewer.plotter.actors
    win.redo(); pump(app)
    assert mw.result['mass'] == pytest.approx(.0162)
    win.doc.features[-1].depth = 60
    win.rebuild(); pump(app)
    assert mw.result['mass'] == pytest.approx(.0324)
    assert np.allclose(mw.result['center_of_gravity'], (5,10,30))
    target = Path('projects/mass_properties'); target.mkdir(parents=True, exist_ok=True)
    mw.grab().save(str(target/'part-mass.png'))
    win.grab().save(str(target/'part-window.png'))
    mw.close()
    assert 'mass_cg_pts' not in win.viewer.plotter.actors


def test_assembly_inheritance_overrides_and_pose_refresh(app, tmp_path):
    from bauyrcad.core.assembly import Assembly, Pose
    from bauyrcad.gui.assembly_window import AssemblyWindow
    p=block().set_material(density=1000); p.save(tmp_path/'p.bcad')
    asm=Assembly(); asm.add_component(tmp_path/'p.bcad')
    second=asm.add_component(tmp_path/'p.bcad', pose=Pose(t=(100,0,0)))
    w=AssemblyWindow(assembly=asm); w.show()
    try:
        pump(app)
        w.a_mass.trigger(); mw=w.mass_win
        assert mw.result['mass'] == pytest.approx(.012)
        combo=mw.table.cellWidget(1,1); combo.setCurrentIndex(1)
        mw.table.cellWidget(1,2).setValue(2000)
        QTest.mouseClick(mw.apply_button, Qt.LeftButton)
        assert mw.result['mass'] == pytest.approx(.018)
        assert second.doc.mass_material['density'] == 1000
        assert second.mass_material['density'] == 2000
        second.pose.t += np.array([30,0,0]); w.update_poses()
        assert mw.result['center_of_gravity'][0] == pytest.approx((.006*5+.012*135)/.018)
        target=Path('projects/mass_properties'); target.mkdir(parents=True,exist_ok=True)
        mw.grab().save(str(target/'assembly-mass.png'))
        w.undo(); pump(app)
        assert mw.result['mass'] == pytest.approx(.012)
        mw.close()
    finally:
        w.dirty=False; w.close()
