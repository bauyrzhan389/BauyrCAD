"""Drives the real main window through a full model -> analysis workflow."""
import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def pump(app, n=5):
    for _ in range(n):
        app.processEvents()


def wait_animation(app, w, timeout=3.0):
    import time
    end = time.monotonic() + timeout
    while w._anim is not None and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def activate(app, w, timeout=3.0):
    """Key events only reach the type-to-command filter when the window is active."""
    import time
    from PySide6.QtWidgets import QApplication
    w.raise_()
    w.activateWindow()
    end = time.monotonic() + timeout
    while QApplication.activeWindow() is not w and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_full_workflow(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from bauyrcad.core import geometry
    from bauyrcad.core.plane import FRONT
    from bauyrcad.fea.analysis import run_study
    from bauyrcad.gui.main_window import MainWindow
    from bauyrcad.gui.viewer import PickInfo

    w = MainWindow()
    w.show()
    pump(app, 20)

    # sketch a rectangle with real mouse clicks in the viewport
    w.start_sketch("rect_corner")
    w.command._set_plane(FRONT)
    w.command.grid_cb.setChecked(True)      # exact coordinates from mouse clicks
    pump(app)
    vw = w.viewer.plotter.interactor

    def click(u, v):
        d = w.viewer.world_to_display(FRONT.to_world(u, v))
        r = vw.devicePixelRatioF()
        pos = QPoint(int(round(d[0] / r)), int(round(vw.height() - d[1] / r)))
        QTest.mouseMove(vw, pos)
        pump(app, 2)
        QTest.mouseClick(vw, Qt.LeftButton, Qt.NoModifier, pos)
        pump(app, 2)

    click(-20, -10)
    click(20, 10)
    assert len(w.command.sketch.entities) == 4
    w.command_ok()
    w.start_extrude("boss")
    w.command.depth.setValue(10)
    w.command_ok()
    pump(app)
    assert geometry.volume(w.doc.body) == pytest.approx(40 * 20 * 10)

    # undo / redo
    w.undo()
    assert w.doc.body is None
    w.redo()
    assert w.doc.has_body()

    # face picking in the middle of the front face
    w.viewer.view_front()
    pump(app)
    size = w.viewer.renderer.GetSize()
    info = w.viewer.pick(size[0] // 2, size[1] // 2)
    assert info.kind == "face" and w.doc.faces[info.face].normal[2] > 0.9

    # study
    w.new_study()
    left = next(f for f in w.doc.faces if f.is_planar and f.normal[0] < -0.9)
    w.start_fixture()
    w.command.on_click(PickInfo("face", left.index))
    w.command_ok()
    w.start_force()
    w.command.on_click(PickInfo("face", info.face, None, np.array([20.0, 10.0, 10.0]), (0, 0)))
    w.command_ok()
    st = w.current_study()
    assert len(st.fixtures) == 1 and len(st.loads) == 1

    res = run_study(st, w.doc)
    w._analysis_done(st, res)
    pump(app)
    assert w.viewer.results_visible
    w.command_ok()
    assert not w.viewer.results_visible

    w.doc.save(tmp_path / "gui.bcad")
    w.dirty = False
    w.close()


def test_sketcher_dimension_drag_and_command_line(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from bauyrcad.core.plane import FRONT
    from bauyrcad.core.sketch import solver
    from bauyrcad.gui.main_window import MainWindow

    w = MainWindow()
    w.show()
    w.activateWindow()
    pump(app, 20)
    w.start_sketch("line")
    w.command._set_plane(FRONT)
    cmd = w.command
    w.viewer.renderer.GetActiveCamera().SetParallelScale(60)
    pump(app)
    vw = w.viewer.plotter.interactor

    def pos(u, v):
        d = w.viewer.world_to_display(FRONT.to_world(u, v))
        r = vw.devicePixelRatioF()
        return QPoint(int(round(d[0] / r)), int(round(vw.height() - d[1] / r)))

    def click(u, v, mod=Qt.NoModifier):
        p = pos(u, v)
        QTest.mouseMove(vw, p)
        pump(app, 2)
        QTest.mouseClick(vw, Qt.LeftButton, mod, p)
        pump(app, 2)

    def typed(text):
        activate(app, w)
        vw.setFocus()
        pump(app, 2)
        QTest.keyClick(vw, text[0])
        pump(app, 2)
        QTest.keyClicks(vw, text[1:])
        pump(app, 2)
        QTest.keyClick(w.cmdline.edit, Qt.Key_Return)
        pump(app, 3)

    # AutoCAD-style typing: absolute, relative and polar input, then close with a click
    typed("0,0")
    typed("@40,0")
    typed("@20<90")
    typed("@-40,0")
    click(0, 0)
    assert len(cmd.sketch.entities) == 4
    assert "1 closed profile" in cmd.profile_label.text()

    # typing a command name switches the tool
    typed("rec")
    assert cmd.tool.key == "rect_corner"
    typed("se")
    assert cmd.tool.key == "select"

    # Smart Dimension on the bottom edge, placed below, value typed in the popup
    cmd.set_tool("smart_dim")
    click(20, 0)
    click(20, -10)
    assert cmd._editor.isVisible()
    cmd._editor.edit.setText("50")
    cmd._editor._ok()
    pump(app)
    bottom = next(e for e in cmd.sketch.entities.values()
                  if abs(cmd.sketch.pt(e.p1)[1]) < 1e-6 and abs(cmd.sketch.pt(e.p2)[1]) < 1e-6)
    length = lambda: float(np.linalg.norm(cmd.sketch.pt(bottom.p2) - cmd.sketch.pt(bottom.p1)))  # noqa: E731
    assert length() == pytest.approx(50, abs=1e-6)

    # drag the top-right corner: the dimension holds, the height follows the mouse
    cmd.set_tool("select")
    tr = max(cmd.sketch.points, key=lambda p: cmd.sketch.pt(p).sum())
    start = cmd.sketch.pt(tr)
    p0, p1 = pos(*start), pos(start[0] + 10, start[1] + 10)
    QTest.mouseMove(vw, p0)
    QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, p0)
    for t in np.linspace(0, 1, 6)[1:]:
        QTest.mouseMove(vw, QPoint(int(p0.x() + t * (p1.x() - p0.x())), int(p0.y() + t * (p1.y() - p0.y()))))
        pump(app, 1)
    QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, p1)
    pump(app, 3)
    assert length() == pytest.approx(50, abs=1e-6)
    assert cmd.sketch.pt(tr)[1] > start[1] + 5
    assert solver.Solver(cmd.sketch).solve().ok

    # sketch undo, then finish the sketch and extrude it
    n = len(cmd.sketch.constraints)
    w.undo()
    w.redo()
    assert len(cmd.sketch.constraints) == n
    w.command_ok()
    w.start_extrude("boss")
    w.command_ok()
    assert w.doc.features[-1].error is None
    w.dirty = False
    w.close()


def test_space_repeats_previous_command_like_autocad(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from bauyrcad.core.plane import FRONT
    from bauyrcad.gui.extrude_command import ExtrudeCommand
    from bauyrcad.gui.main_window import MainWindow

    w = MainWindow()
    w.show()
    w.activateWindow()
    pump(app, 20)
    vw = w.viewer.plotter.interactor

    def space():
        activate(app, w)
        vw.setFocus()
        pump(app, 2)
        QTest.keyClick(vw, Qt.Key_Space)
        pump(app, 3)

    def click(u, v):
        d = w.viewer.world_to_display(FRONT.to_world(u, v))
        r = vw.devicePixelRatioF()
        p = QPoint(int(round(d[0] / r)), int(round(vw.height() - d[1] / r)))
        QTest.mouseMove(vw, p)
        pump(app, 2)
        QTest.mouseClick(vw, Qt.LeftButton, Qt.NoModifier, p)
        pump(app, 2)

    # Line started from the toolbar (not typed) is what Space repeats
    w.sketch_actions["line"].trigger()
    w.command._set_plane(FRONT)
    cmd = w.command
    cmd.grid_cb.setChecked(True)
    w.viewer.renderer.GetActiveCamera().SetParallelScale(40)
    pump(app)
    click(0, 0)
    click(20, 0)
    assert cmd.tool.key == "line" and cmd.tool.pts          # chain in progress
    space()
    assert cmd.tool.key == "line" and not cmd.tool.pts      # 1st Space ends the chain
    space()
    assert cmd.tool.key == "select"                         # 2nd Space ends the tool
    space()
    assert cmd.tool.key == "line"                           # 3rd Space repeats LINE
    w.undo()                                                # drop the open line
    w.sketch_actions["rect_corner"].trigger()
    click(0, 10)
    click(20, 30)
    space()
    assert cmd.tool.key == "select"
    w.command_ok()

    # Extruded Boss from the toolbar, cancelled, then repeated and accepted with Space
    w.a_boss.trigger()
    assert isinstance(w.command, ExtrudeCommand)
    w.command_cancel()
    assert w.command is None
    space()
    assert isinstance(w.command, ExtrudeCommand)            # repeated
    space()                                                 # Space accepts the command
    assert w.command is None
    assert w.doc.features[-1].TYPE == "extrude" and w.doc.features[-1].error is None
    w.dirty = False
    w.close()


def test_assembly_window_mates_and_drag(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from bauyrcad.api import Part, select_face
    from bauyrcad.gui.main_window import MainWindow

    base = Part("base")
    s = base.sketch("front")
    s.rect((0, 0), (80, 50))
    s.circle((25, 25), d=10)
    base.extrude(s, 10)
    base.save(tmp_path / "base.bcad")
    pin = Part("pin")
    s = pin.sketch("front")
    s.centerline((0, 0), (0, 10))
    s.rect((0, 0), (5, 36))
    pin.revolve(s)
    pin.save(tmp_path / "pin.bcad")

    main = MainWindow()
    main.show()
    pump(app, 10)
    w = main.open_assembly()
    pump(app, 10)
    b = w.insert_component(str(tmp_path / "base.bcad"))
    p = w.insert_component(str(tmp_path / "pin.bcad"))
    assert b.fixed and not p.fixed

    def face(comp, **kw):
        return select_face(comp.faces, comp.shape, **kw).index

    w.start_mate()
    w._add_pick(p.id, face(p, kind="cylinder"))
    w._add_pick(b.id, face(b, kind="cylinder"))
    assert w._chosen_type() == "concentric" and w.preview_mate is not None
    w.add_preview_mate()
    w._add_pick(p.id, face(p, normal="-y"))
    w._add_pick(b.id, face(b, normal="+z"))
    assert w._chosen_type() == "coincident"
    w.add_preview_mate()
    w.close_mate_panel()
    wait_animation(app, w)
    assert [m.type for m in w.asm.mates] == ["concentric", "coincident"]
    assert np.allclose(p.pose.point([0, 0, 0]), [25, 25, 10], atol=1e-6)
    assert p.pose.vector([0, 1, 0])[2] > 0.999                     # pin stands up
    assert w.asm.dof()[p.id] == 1

    # real mouse drag on the pin: it may only spin, so its axis stays put
    w.viewer.view_isometric()
    w.viewer.fit()
    pump(app, 5)
    vw = w.viewer.plotter.interactor
    rim = p.pose.point([5, 30, 0])
    d = w.viewer.world_to_display(rim)
    r = vw.devicePixelRatioF()
    p0 = QPoint(int(d[0] / r), int(vw.height() - d[1] / r))
    assert w.viewer.pick(*d).component == p.id
    QTest.mouseMove(vw, p0)
    QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, p0)
    for k in range(1, 8):
        QTest.mouseMove(vw, QPoint(p0.x() + 10 * k, p0.y() + 6 * k))
        pump(app, 1)
    QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, QPoint(p0.x() + 70, p0.y() + 42))
    pump(app, 3)
    assert np.allclose(p.pose.point([0, 0, 0]), [25, 25, 10], atol=1e-5)
    assert p.pose.vector([0, 1, 0])[2] > 0.99999

    n_undo = len(w._undo)
    w.undo()
    assert len(w._undo) == n_undo - 1
    w.asm.save(tmp_path / "pin.bcasm")
    w.dirty = False
    w.close()
    main.dirty = False
    main.close()


def test_assembly_mouse_buttons_and_mate_animation(app, tmp_path, monkeypatch):
    import time

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from bauyrcad.api import Part, select_face
    from bauyrcad.gui.main_window import MainWindow

    for name, (w_, h_) in (("plate", (60, 40)), ("block", (20, 20))):
        prt = Part(name)
        sk = prt.sketch("front")
        sk.rect((0, 0), (w_, h_))
        prt.extrude(sk, 10)
        prt.save(tmp_path / f"{name}.bcad")

    main = MainWindow()
    main.show()
    pump(app, 10)
    w = main.open_assembly()
    pump(app, 10)
    plate = w.insert_component(str(tmp_path / "plate.bcad"))
    block = w.insert_component(str(tmp_path / "block.bcad"))
    w.viewer.view_isometric()
    w.viewer.fit()
    pump(app, 5)
    vw = w.viewer.plotter.interactor
    ratio = vw.devicePixelRatioF()

    def qpoint(world):
        d = w.viewer.world_to_display(world)
        return QPoint(int(d[0] / ratio), int(vw.height() - d[1] / ratio)), d

    def drag(button, start_world, dx, dy):
        p0, d = qpoint(start_world)
        assert w.viewer.pick(*d).component == block.id
        QTest.mouseMove(vw, p0)
        QTest.mousePress(vw, button, Qt.NoModifier, p0)
        for k in range(1, 9):
            QTest.mouseMove(vw, QPoint(p0.x() + dx * k // 8, p0.y() + dy * k // 8))
            pump(app, 1)
        QTest.mouseRelease(vw, button, Qt.NoModifier, QPoint(p0.x() + dx, p0.y() + dy))
        pump(app, 3)

    centre = block.pose.point([10, 10, 10])
    R0, t0 = block.pose.R.copy(), block.pose.t.copy()
    drag(Qt.LeftButton, centre, 60, -30)                       # left: translate only
    assert np.allclose(block.pose.R, R0, atol=1e-9)
    assert np.linalg.norm(block.pose.t - t0) > 5

    R1 = block.pose.R.copy()
    drag(Qt.RightButton, block.pose.point([10, 10, 10]), 80, 20)   # right: rotate
    angle = np.degrees(np.arccos(np.clip((np.trace(block.pose.R @ R1.T) - 1) / 2, -1, 1)))
    assert angle > 10

    shown = []                                                  # plain right-click: menu
    monkeypatch.setattr(w, "_show_menu", lambda menu, pos: shown.append(menu))
    p0, _ = qpoint(block.pose.point([10, 10, 10]))
    QTest.mouseClick(vw, Qt.RightButton, Qt.NoModifier, p0)
    pump(app, 3)
    assert shown, "right-click on a component should open its context menu"

    # mate: the block glides onto the plate instead of jumping
    def face(comp, **kw):
        return select_face(comp.faces, comp.shape, **kw).index
    w.start_mate()
    w._add_pick(block.id, face(block, normal="-z"))
    w._add_pick(plate.id, face(plate, normal="+z"))
    assert w._chosen_type() == "coincident"
    assert w._anim is not None                                  # animation started
    time.sleep(0.12)
    app.processEvents()
    mid = w._shown[block.id]
    assert not np.allclose(mid.matrix, block.pose.matrix, atol=1e-6)   # still on the way
    wait_animation(app, w)
    assert np.allclose(w._shown[block.id].matrix, block.pose.matrix, atol=1e-9)
    actor = w.viewer._asm[block.id][0]
    m = actor.GetUserMatrix()
    shown_m = np.array([[m.GetElement(i, j) for j in range(4)] for i in range(4)])
    assert np.allclose(shown_m, block.pose.matrix, atol=1e-9)          # lands exactly
    w.add_preview_mate()
    w.close_mate_panel()
    wait_animation(app, w)
    w.dirty = False
    w.close()
    main.dirty = False
    main.close()


def test_drawing_window(app, tmp_path, monkeypatch):
    """Make Drawing from Part, Smart Dimension by clicking, drag views, export."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from bauyrcad.api import Part
    from bauyrcad.gui.main_window import MainWindow

    prt = Part("bracket")
    sk = prt.sketch("front")
    sk.rect((0, 0), (80, 50))
    sk.circle((20, 15), d=10)
    sk.circle((60, 15), d=10)
    prt.extrude(sk, 12)
    prt.save(tmp_path / "bracket.bcad")

    main = MainWindow()
    main.show()
    pump(app, 10)
    main.file_open(str(tmp_path / "bracket.bcad"))
    pump(app, 5)
    w = main.make_drawing()
    pump(app, 10)
    dr = w.dr
    assert [v.name for v in dr.views] == ["Front", "Top", "Left", "Isometric"]
    c = w.canvas

    def px(p):
        q = c.to_px(p)
        return QPoint(int(round(q.x())), int(round(q.y())))

    def click(p):
        QTest.mouseMove(c, px(p))
        QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, px(p))
        pump(app, 2)

    front = dr.views[0]
    g = dr.geometry(front)
    w.start_dim()
    top_edge = dr.to_sheet(front, ((g.lo[0] + g.hi[0]) / 2, g.hi[1]))
    click(top_edge)                                     # an edge ...
    assert [k for k, _, _ in w.picks] == ["line"]
    click(top_edge + [0, 15])                           # ... placed above: its length
    (cen, r), (cen2, _) = g.circles[:2]
    click(dr.to_sheet(front, cen + [r, 0]))             # a circle -> diameter
    click(dr.to_sheet(front, cen + [r, 0]) + [12, -12])
    click(dr.to_sheet(front, cen))                      # two centres -> distance
    click(dr.to_sheet(front, cen2))
    click((dr.to_sheet(front, cen) + dr.to_sheet(front, cen2)) / 2 - [0, 25])
    assert [(d.kind, round(d.value, 6)) for d in dr.dims] == [
        ("linear", 80.0), ("diameter", 10.0), ("linear", 40.0)]
    assert [d.orient for d in dr.dims if d.kind == "linear"] == ["h", "h"]
    w.escape()

    # dragging the front view drags its projected views along, aligned
    top, side = dr.views[1], dr.views[2]
    p0 = px((front.x, front.y))
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, p0)
    for k in range(1, 6):
        QTest.mouseMove(c, QPoint(p0.x() + 5 * k, p0.y() - 3 * k))
        pump(app, 1)
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(p0.x() + 25, p0.y() - 15))
    pump(app, 2)
    assert top.x == pytest.approx(front.x) and side.y == pytest.approx(front.y)
    assert w.dirty

    # right-click on a view opens its menu; undo brings the view back
    shown = []
    monkeypatch.setattr(w, "_show_menu", lambda menu, pos: shown.append(
        [a.text() for a in menu.actions()]))
    QTest.mouseClick(c, Qt.RightButton, Qt.NoModifier, px((side.x, side.y)))
    pump(app, 2)
    assert shown and "Projected View" in shown[0]
    x_before = front.x
    w.undo()
    assert w.dr.views[0].x != pytest.approx(x_before)

    out = w.file_export("pdf", path=str(tmp_path / "bracket.pdf"))
    assert out and (tmp_path / "bracket.pdf").stat().st_size > 5000
    w.file_export("dxf", view=w.dr.views[0], path=str(tmp_path / "front.dxf"))
    assert (tmp_path / "front.dxf").exists()
    w.dr.save(tmp_path / "bracket.bcdrw")
    w.dirty = False
    w.close()
    w2 = main.open_drawing(str(tmp_path / "bracket.bcdrw"))
    pump(app, 5)
    assert len(w2.dr.views) == 4 and w2.dr.geometry(w2.dr.views[0]).circles
    w2.dirty = False
    w2.close()
    main.dirty = False
    main.close()
