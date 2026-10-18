import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from bauyrcad.core import geometry
from bauyrcad.core.plane import FRONT
from bauyrcad.gui import feature_commands as fc
from test_gui_revolve_workflow import win
from test_gui_cursor_orbit import setup_plate, screen


def test_back_face_popup_preview_and_actual_feature_selection(win, tmp_path):
    v = setup_plate(win)
    win.start_feature(fc.ShellCommand)
    cmd = win.command
    QTest.qWait(80)
    xy = v.world_to_display((50, 30, 10))
    hits = v.pick_candidates(*xy)
    assert len(hits) == 2
    assert hits[0].point[2] > hits[1].point[2]
    rear = hits[1]
    # A real right click during the command opens the shared picker.
    QTest.mouseClick(v.plotter.interactor, Qt.RightButton, Qt.NoModifier, screen(v, (50, 30, 10)))
    QTest.qWait(60)
    menu = v._selection_menu
    action = next(a for a in menu.actions() if getattr(a.data(), 'face', None) == rear.face)
    menu.setActiveAction(action)
    action.hover()
    assert v._candidate_outline is not None
    win.grab().save(str(tmp_path / 'select-other-rear-face.png'))
    menu.grab().save(str(tmp_path / 'select-other-menu.png'))
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())
    assert cmd.active.items[0]['key'] == rear.face
    assert v._candidate_outline is None
    win.command_ok()
    assert win.doc.features[-1].TYPE == 'shell'
    assert not win.doc.features[-1].error
    win.undo()
    win.redo()
    assert win.doc.features[-1].TYPE == 'shell'


def test_filters_hidden_objects_and_transformed_components(win):
    v = setup_plate(win)
    xy = v.world_to_display((0, 30, 10))
    edges = v.pick_candidates(*xy, kinds={'edge'})
    assert edges and all(h.kind == 'edge' for h in edges)
    assert v.pick_candidates(*xy, kinds={'plane'}) == []
    v.set_body_visible(False)
    assert v.pick_candidates(*xy, kinds={'edge', 'face'}) == []
    tess = geometry.tessellate(win.doc.body)
    v.set_body(None)
    a, b = np.eye(4), np.eye(4)
    a[:3, 3] = (40, 20, 30)
    b[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    b[:3, 3] = (120, 20, -20)
    v.set_assembly([('near', tess, a, (150, 170, 180)), ('far', tess, b, (180, 170, 150))])
    v.fit()
    v.view_normal_to(FRONT, animate=False)
    xy = v.world_to_display((80, 45, 40))
    hits = v.pick_candidates(*xy)
    assert [h.component for h in hits] == ['near', 'near', 'far', 'far']
    assert np.allclose([h.point[2] for h in hits], [40, 30, -10, -20])
    v._asm['near'][0].SetVisibility(False)
    assert all(h.component == 'far' for h in v.pick_candidates(*xy))
    v.results_visible = True
    assert not v.pick_candidates(*xy)
    v.results_visible = False


def test_assembly_mate_picks_hidden_component_and_cancel_keeps_selection(win, tmp_path):
    from bauyrcad.core.assembly import Assembly, Pose
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from bauyrcad.api import Part
    p = Part()
    sk = p.sketch('front'); sk.rect((0, 0), (80, 50)); p.extrude(sk, 10)
    path = tmp_path / 'plate.bcad'
    p.save(path)
    asm = Assembly()
    near = asm.add_component(path, pose=Pose(t=(20, 0, 20)))
    far = asm.add_component(path, pose=Pose(t=(20, 0, -20)))
    w = AssemblyWindow(assembly=asm)
    w.show()
    try:
        v = w.viewer
        v.view_normal_to(FRONT, animate=False)
        QTest.qWait(80)
        xy = v.world_to_display((60, 25, 30))
        w.start_mate()
        QTest.mouseClick(v.plotter.interactor, Qt.RightButton, Qt.NoModifier, screen(v, (60, 25, 30)))
        menu = v._selection_menu
        action = next(a for a in menu.actions() if getattr(a.data(), 'component', None) == far.id)
        picked = action.data()
        menu.setActiveAction(action)
        action.hover()
        menu.grab().save(str(tmp_path / 'select-other-assembly-menu.png'))
        QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())
        assert w.picks == [(far.id, picked.face)]
        w._right_clicked(v.pick(*xy))
        menu = v._selection_menu
        QTest.keyClick(menu, Qt.Key_Escape)
        assert w.picks == [(far.id, picked.face)]
        assert v._candidate_outline is None
        assert v._selection_menu is None
    finally:
        w.close_mate_panel()
        w.dirty = False
        w.close()


def test_measure_uses_chosen_back_face_instead_of_repicking_front(win):
    v = setup_plate(win)
    mw = win.start_measure()
    xy = v.world_to_display((50, 30, 10))
    hits = v.pick_candidates(*xy)
    win._viewer_click(hits[0])
    win._viewer_click(hits[1])
    assert set(mw.refs) == {('face', h.face) for h in hits}
    assert abs(mw.result['distance'] - 10) < 1e-6
    mw.close()
