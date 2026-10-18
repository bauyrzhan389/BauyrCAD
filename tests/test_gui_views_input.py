"""Viewer and sketch input: clipping guard, numpad views + transitions, view selector,
Smart Dimension staying active, AutoCAD dynamic input, object snaps, the context toolbar
and Measure - all driven with real mouse / key events."""
import math
import time

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
        time.sleep(0.01)


def activate(app, w, timeout=3.0):
    from PySide6.QtWidgets import QApplication
    w.raise_()
    w.activateWindow()
    end = time.monotonic() + timeout
    while QApplication.activeWindow() is not w and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def plate(w=80, h=50, t=10, hole=20, reverse=False):
    from bauyrcad.api import Part
    p = Part("plate")
    s = p.sketch("front")
    s.rect((0, 0), (w, h))
    s.circle((w / 2, h / 2), d=hole)
    p.extrude(s, t, reverse=reverse)
    return p


@pytest.fixture
def win(app):
    from bauyrcad.gui.main_window import MainWindow
    w = MainWindow()
    w.show()
    activate(app, w)
    pump(app, 10)
    yield w
    if w.command is not None:
        w.command_cancel()
    w.dirty = False
    w.close()
    pump(app, 2)


def screen_pos(w, xyz):
    from PySide6.QtCore import QPoint
    vw = w.viewer.plotter.interactor
    d = w.viewer.world_to_display(xyz)
    r = vw.devicePixelRatioF()
    return QPoint(int(round(d[0] / r)), int(round(vw.height() - d[1] / r)))


def test_camera_never_clips_a_big_model(app, win):
    """Screenshots showed a 1.6 m part sliced after Normal To + rotating: the camera sat
    inside the model (fixed 1000 mm distance) and parallel projection clipped behind it."""
    from bauyrcad.core.plane import FRONT
    p = plate(1600, 1000, 300, 400)
    win._set_document(p.doc)
    pump(app, 5)
    v = win.viewer
    v.view_normal_to(FRONT, animate=False)
    cam = v.renderer.GetActiveCamera()
    for az, el in ((60, 0), (60, 35), (150, -40)):
        cam.Azimuth(az)
        cam.Elevation(el)
        cam.OrthogonalizeViewUp()
        v.render()
        near, far = cam.GetClippingRange()
        depth = (v._body_mesh.points - np.array(cam.GetPosition())) @ np.array(cam.GetDirectionOfProjection())
        assert depth.min() >= near - 1e-6 and depth.max() <= far + 1e-6


def test_numpad_views_repeat_and_transition(app, win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win._set_document(plate().doc)
    pump(app, 5)
    v = win.viewer
    vw = v.plotter.interactor

    def press(key):
        activate(app, win)
        vw.setFocus()
        QTest.keyClick(vw, key, Qt.KeypadModifier)
        pump(app, 2)
        v.finish_animation()
        return np.round(v.camera_frame()[:, 2], 6), np.round(v.camera_frame()[:, 1], 6)

    assert np.allclose(press(Qt.Key_4)[0], [-1, 0, 0])          # left view
    r2 = 1 / math.sqrt(2)
    assert np.allclose(press(Qt.Key_4)[0], [-r2, 0, -r2])       # again: turned 45 deg
    assert np.allclose(press(Qt.Key_4)[0], [0, 0, -1])          # and again: 90 deg in all
    assert np.allclose(press(Qt.Key_6)[0], [1, 0, 0])           # right
    assert np.allclose(press(Qt.Key_6)[0], [r2, 0, -r2])        # again: 45 deg the other way
    back, up = press(Qt.Key_8)
    assert np.allclose(back, [0, 1, 0]) and np.allclose(up, [0, 0, -1])   # top
    assert np.allclose(press(Qt.Key_8)[0], [0, r2, -r2])        # again: tipped 45 deg
    assert np.allclose(press(Qt.Key_2)[0], [0, -1, 0])          # bottom
    assert np.allclose(press(Qt.Key_2)[0], [0, -r2, -r2])       # again: tipped 45 deg
    assert np.allclose(press(Qt.Key_3)[0], [0, 0, 1])           # front
    assert np.allclose(press(Qt.Key_3)[0], [0, 0, -1])          # again: back
    iso = press(Qt.Key_1)[0]
    assert np.allclose(iso, np.ones(3) / math.sqrt(3), atol=1e-6)
    assert np.allclose(press(Qt.Key_1)[0], [-1 / math.sqrt(3), 1 / math.sqrt(3), 1 / math.sqrt(3)], atol=1e-6)
    _, up0 = press(Qt.Key_8)
    _, up1 = press(Qt.Key_9)                                    # roll 90 deg
    assert abs(float(up0 @ up1)) < 1e-6
    assert np.allclose(press(Qt.Key_Up)[0], [0, 1, 0])          # NumLock off: 8 = Up
    # a real transition: part-way through, then exactly on the target
    v.view_front()
    time.sleep(0.12)
    app.processEvents()
    mid = v.camera_frame()[:, 2]
    assert v.animating and not np.allclose(mid, [0, 0, 1], atol=1e-3)
    end = time.monotonic() + 2
    while v.animating and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    assert np.allclose(v.camera_frame()[:, 2], [0, 0, 1], atol=1e-9)


def test_view_selector(app, win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win._set_document(plate().doc)
    v = win.viewer
    v.set_view("isometric", animate=False)
    activate(app, win)
    v.plotter.interactor.setFocus()
    QTest.keyClick(v.plotter.interactor, Qt.Key_5, Qt.KeypadModifier)
    pump(app, 5)
    sel = v._selector
    assert sel.isVisible()
    d = sel.cube.region_at(sel.cube.rect().center())             # the corner facing us
    assert np.allclose(d, [1, 1, 1])
    sel.choose((0, 1, 0))                                       # the TOP face
    v.finish_animation()
    assert np.allclose(v.camera_frame()[:, 2], [0, 1, 0]) and not sel.isVisible()


def _sketch(app, win, tool, scale=120, doc=None):
    from bauyrcad.core.plane import FRONT
    if doc is not None:
        win._set_document(doc)
        pump(app, 3)
    win.start_sketch(tool)
    win.command._set_plane(FRONT)
    win.viewer.finish_animation()
    win.viewer.renderer.GetActiveCamera().SetParallelScale(scale)
    win.viewer.render()
    pump(app, 3)
    return win.command


def _click(app, win, u, v, button=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from bauyrcad.core.plane import FRONT
    vw = win.viewer.plotter.interactor
    p = screen_pos(win, FRONT.to_world(u, v))
    QTest.mouseMove(vw, p)
    pump(app, 2)
    QTest.mouseClick(vw, button or Qt.LeftButton, Qt.NoModifier, p)
    pump(app, 3)


def _move(app, win, u, v):
    from PySide6.QtTest import QTest
    from bauyrcad.core.plane import FRONT
    QTest.mouseMove(win.viewer.plotter.interactor, screen_pos(win, FRONT.to_world(u, v)))
    pump(app, 2)


def _type_start(app, win, ch):
    from PySide6.QtTest import QTest
    activate(app, win)
    win.viewer.plotter.interactor.setFocus()
    pump(app, 2)
    QTest.keyClick(win.viewer.plotter.interactor, ch)
    pump(app, 3)


def test_smart_dimension_stays_until_esc(app, win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    cmd = _sketch(app, win, "rect_corner", 60)
    _click(app, win, 0, 0)
    _click(app, win, 40, 20)
    cmd.set_tool("smart_dim")
    _click(app, win, 20, 0)
    _click(app, win, 20, -10)
    ed = cmd._editor
    assert ed.isVisible()
    QTest.keyClicks(ed.edit, "50")
    QTest.keyClick(ed.edit, Qt.Key_Return)                      # this Enter used to end the tool
    pump(app, 5)
    assert cmd.tool.key == "smart_dim"
    _click(app, win, 5, 30, Qt.RightButton)
    assert cmd.tool.key == "smart_dim"
    cmd.on_escape()
    assert cmd.tool.key == "select"


def test_rectangle_dynamic_input(app, win):
    """AutoCAD: first corner, type the width, Tab, the height, Enter."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    cmd = _sketch(app, win, "rect_corner")
    _click(app, win, 10, 5)
    _move(app, win, 40, 30)
    _type_start(app, win, "8")
    dyn = cmd._dyn
    assert dyn.isVisible() and dyn.edits[0].text() == "8"
    QTest.keyClicks(dyn.edits[0], "0")
    QTest.keyClick(dyn.edits[0], Qt.Key_Tab)
    assert dyn.edits[1].hasFocus()
    QTest.keyClicks(dyn.edits[1], "50")
    QTest.keyClick(dyn.edits[1], Qt.Key_Return)
    pump(app, 5)
    sk = cmd.sketch
    pts = np.array([sk.pt(e.p1) for e in sk.entities.values() if hasattr(e, "p1")])
    assert np.allclose(pts.max(0) - pts.min(0), [80, 50], atol=1e-9)
    dims = sorted((c.type, c.value) for c in sk.constraints if c.type in ("hdistance", "vdistance"))
    assert dims == [("hdistance", 80.0), ("vdistance", 50.0)]
    assert not dyn.isVisible() and cmd.tool.key == "rect_corner"
    # width typed, height from a click; the cursor side sets the direction
    n0 = len(sk.entities)
    _click(app, win, -10, -10)
    _move(app, win, -30, -40)
    _type_start(app, win, "2")
    QTest.keyClicks(cmd._dyn.edits[0], "5")
    _click(app, win, -30, -42)
    new = [e for e in list(sk.entities.values())[n0:] if hasattr(e, "p1")]
    xs = [sk.pt(e.p1)[0] for e in new]
    assert max(xs) - min(xs) == pytest.approx(25) and max(xs) < 0
    # circle: diameter; line: length and angle
    cmd.set_tool("circle")
    _click(app, win, 0, 80)
    _move(app, win, 10, 85)
    _type_start(app, win, "2")
    QTest.keyClicks(cmd._dyn.edits[0], "4")
    QTest.keyClick(cmd._dyn.edits[0], Qt.Key_Return)
    pump(app, 3)
    circles = [e for e in sk.entities.values() if type(e).__name__ == "Circle"]
    assert circles and 2 * circles[-1].r == pytest.approx(24)
    cmd.set_tool("line")
    _click(app, win, 100, 60)
    _move(app, win, 130, 70)
    _type_start(app, win, "6")
    QTest.keyClicks(cmd._dyn.edits[0], "0")
    QTest.keyClick(cmd._dyn.edits[0], Qt.Key_Tab)
    QTest.keyClicks(cmd._dyn.edits[1], "30")
    QTest.keyClick(cmd._dyn.edits[1], Qt.Key_Return)
    pump(app, 3)
    ln = list(sk.entities.values())[-1]
    d = sk.pt(ln.p2) - sk.pt(ln.p1)
    assert np.linalg.norm(d) == pytest.approx(60) and math.degrees(math.atan2(d[1], d[0])) == pytest.approx(30)


def test_object_snaps(app, win):
    cmd = _sketch(app, win, "line", 80, doc=plate(reverse=True).doc)   # front face = Front plane
    sk = cmd.sketch
    a, b = sk.add_point(100, -10), sk.add_point(200, 10)
    sk.add_line(a, b)
    sk.add_circle(sk.add_point(150, 0), 12)
    cases = {(80.4, 49.7): ("model_point", (80, 50)), (40.3, 25.2): ("model_center", (40, 25)),
             (40.2, 35.3): ("model_quad", (40, 35)), (40.2, 0.3): ("model_mid", (40, 0)),
             (80.4, 17.3): ("model_edge", (80, 17.3)), (150.2, 0.2): ("center", (150, 0)),
             (162.2, 0.3): ("quadrant", (162, 0)), (0.3, 0.2): ("point", (0, 0))}
    for q, (kind, at) in cases.items():
        s = cmd.snap(*q)
        assert s.kind == kind and np.allclose(s.uv, at, atol=1e-6), (q, s.kind, s.uv)
    d = np.array([100.0, 20.0]) / math.hypot(100, 20)
    x = np.array([100.0, -10.0])
    t = min(np.real(np.roots([1, 2 * d @ (x - [150, 0]), (x - [150, 0]) @ (x - [150, 0]) - 144])))
    hit = x + t * d
    s = cmd.snap(*(hit + [0.3, 0.2]))
    assert s.kind == "intersection" and np.allclose(s.uv, hit, atol=1e-6)
    pid = cmd.commit(s)                                          # on both curves
    assert sum(1 for c in sk.constraints if c.type == "coincident" and ("p", pid) in c.refs) == 2


def test_context_toolbar_and_plane_items(app, win):
    from bauyrcad.core.plane import REFERENCE_PLANES
    from bauyrcad.gui.sketcher import SketchCommand
    from bauyrcad.gui.viewer import PickInfo
    p = plate()
    win._set_document(p.doc)
    pump(app, 3)
    top = p.face("+z")
    win._viewer_click(PickInfo("face", top.index, None, top.centroid, (0, 0)))
    bar = win.context_bar
    assert bar.isVisible() and bar.actions_shown[:3] == ["Sketch", "Normal To", "Measure"]
    assert bar.trigger("Sketch")
    assert isinstance(win.command, SketchCommand) and win.command.plane is not None
    assert np.allclose(win.command.plane.normal, [0, 0, 1])
    win.command_cancel()
    # tree: Front / Top / Right Plane items; selecting one offers Sketch on it
    part = win.tree.topLevelItem(0)
    names = [part.child(i).text(0) for i in range(3)]
    assert names == list(REFERENCE_PLANES)
    # Emulate the cursor position of a real tree click; otherwise the context
    # bar's distance timer correctly hides it when the cursor is in the viewport.
    from PySide6.QtTest import QTest
    QTest.mouseMove(win.tree.viewport(), win.tree.visualItemRect(part.child(1)).center())
    win.tree.setCurrentItem(part.child(1))                      # Top Plane
    pump(app, 3)
    assert bar.isVisible() and bar.trigger("Sketch")
    assert win.command.plane is REFERENCE_PLANES["Top Plane"]
    win.command_cancel()


def test_measure_by_clicking(app, win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    p = plate()
    win._set_document(p.doc)
    pump(app, 5)
    win.viewer.finish_animation()
    vw = win.viewer.plotter.interactor

    def click(xyz):
        q = screen_pos(win, xyz)
        QTest.mouseMove(vw, q)
        pump(app, 2)
        QTest.mouseClick(vw, Qt.LeftButton, Qt.NoModifier, q)
        pump(app, 3)

    mw = win.start_measure()
    click((80, 50, 10))                                          # a corner vertex
    click((40 + 10 * math.cos(2.0), 25 + 10 * math.sin(2.0), 10))   # the hole's rim
    assert [k for k, _ in mw.refs] == ["vertex", "edge"]
    assert mw.result["center_distance"] == pytest.approx(math.hypot(40, 25))
    hidden = win.viewer.pick_topology(*win.viewer.world_to_display((0, 0, 0)), mw.vertices())
    assert hidden[0] == "face"                                   # the hidden corner is not picked
    click((80, 50, 10))                                          # click again: dropped
    assert [k for k, _ in mw.refs] == ["edge"]
    win._escape()
    assert not mw.isVisible()


def test_closed_regions_are_shaded(app, win):
    """Closed areas are shaded while sketching (an open outline is not), so it is visible
    when Extrude will work."""
    from bauyrcad.core.sketch import builders
    cmd = _sketch(app, win, "line")

    def u(sk):
        a, b, c, d = (sk.add_point(*p) for p in ((-60, 0), (-60, 30), (-20, 30), (-20, 0)))
        for p, q in ((a, b), (b, c), (c, d)):
            builders.line(sk, p, q)
    cmd.apply(u)
    assert cmd.region_count == 0 and "sk_regions" not in win.viewer.plotter.actors
    cmd.apply(lambda sk: builders.corner_rectangle(sk, (0, 0), (60, 40)))
    assert cmd.region_count == 1 and "sk_regions" in win.viewer.plotter.actors
    cmd.apply(lambda sk: builders.line(sk, sk.add_point(30, -10), sk.add_point(30, 50)))
    assert cmd.region_count == 2                        # the crossing line splits it
    cmd.fill_cb.setChecked(False)
    assert cmd.region_count == 0 and "sk_regions" not in win.viewer.plotter.actors


def test_left_drag_keeps_camera_and_middle_drag_rotates(app, win, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    win._set_document(plate().doc)
    v = win.viewer
    v.set_view("isometric", animate=False)
    pump(app, 5)
    vw = v.plotter.interactor
    start = QPoint(vw.width() // 2, vw.height() // 2)
    end = start + QPoint(70, 40)
    before = np.array(v.camera_frame())
    clicked = []
    v.clicked.connect(clicked.append)
    QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(vw, end, 30)
    pump(app, 3)
    QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, end)
    pump(app, 3)
    assert np.allclose(v.camera_frame(), before)
    assert not clicked
    QTest.mouseClick(vw, Qt.LeftButton, Qt.NoModifier, start)
    pump(app, 3)
    assert len(clicked) == 1
    QTest.mousePress(vw, Qt.MiddleButton, Qt.NoModifier, start)
    QTest.mouseMove(vw, end, 30)
    pump(app, 3)
    QTest.mouseRelease(vw, Qt.MiddleButton, Qt.NoModifier, end)
    pump(app, 3)
    assert not np.allclose(v.camera_frame(), before)
    win.grab().save(str(tmp_path / "mouse_controls.png"))
