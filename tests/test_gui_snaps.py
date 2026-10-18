"""SolidWorks-style sketch snapping: tangent / perpendicular / parallel / angle snaps with the
relations they imply, Quick Snaps (one snap type for the next click), switching snap types
off, and dragging a point onto another point / midpoint to join them."""
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


@pytest.fixture
def cmd(app):
    from bauyrcad.core.plane import FRONT
    from bauyrcad.gui.main_window import MainWindow
    from bauyrcad.gui.sketcher import SNAP_TYPES
    w = MainWindow()
    w.show()
    pump(app, 10)
    w.start_sketch("line")
    c = w.command
    c._set_plane(FRONT)
    w.viewer.finish_animation()
    w.viewer.renderer.GetActiveCamera().SetParallelScale(120)
    w.viewer.render()
    pump(app, 3)
    # independent of the user's saved snap settings
    c.snap_enabled = {k: k != "grid" for k, _ in SNAP_TYPES}
    c.angle_step = 15.0
    c.quick_snap = None
    yield c
    if w.command is not None:
        w.command_cancel()
    w.dirty = False
    w.close()
    pump(app, 2)


def tol(cmd):
    return 10 * cmd.viewer.world_per_pixel()


def test_tangent_snap_makes_a_tangent_line(cmd):
    from bauyrcad.gui.sketcher import Snap
    sk = cmd.sketch
    sk.add_circle(sk.add_point(60, 0), 20)
    a = np.array([0.0, 0.0])
    phi = math.acos(20 / 60)
    want = np.array([60, 0]) + 20 * np.array([math.cos(math.pi - phi), math.sin(math.pi - phi)])
    s = cmd.snap(*(want + [0.4, 0.3]), anchor=a)
    assert s.kind == "tangent" and np.allclose(s.uv, want, atol=1e-9)
    t = cmd.tool
    t.click(Snap(a, "free"))
    t.click(s)
    ln = [e for e in sk.entities.values() if e.kind == "line"][-1]
    assert any(c.type == "tangent" for c in sk.constraints_of("e", ln.id))
    # still tangent after the solve: distance centre-line == radius
    p1, p2 = sk.pt(ln.p1), sk.pt(ln.p2)
    u, w = p2 - p1, np.array([60, 0]) - p1
    d = abs(u[0] * w[1] - u[1] * w[0]) / np.linalg.norm(u)
    assert d == pytest.approx(20, abs=1e-6)


def test_perpendicular_and_parallel_snaps(cmd):
    from bauyrcad.gui.sketcher import Snap
    sk = cmd.sketch
    ref = sk.add_line(sk.add_point(0, 60), sk.add_point(100, 10))
    a = np.array([-20.0, -20.0])                    # its foot is well away from the midpoint
    u = np.array([100.0, -50.0]) / math.hypot(100, 50)
    foot = np.array([0, 60]) + u * ((a - [0, 60]) @ u)
    s = cmd.snap(*(foot + [0.3, -0.2]), anchor=a)
    assert s.kind == "perpendicular" and np.allclose(s.uv, foot, atol=1e-9)
    t = cmd.tool
    t.click(Snap(a, "free"))
    t.click(s)
    ln = [e for e in sk.entities.values() if e.kind == "line"][-1]
    assert any(c.type == "perpendicular" for c in sk.constraints_of("e", ln.id))
    t.finish()
    # parallel: from a new start, a cursor near the reference direction snaps onto it
    b = np.array([-60.0, -60.0])
    want = b + u * 70
    s = cmd.snap(*(want + np.array([u[1], -u[0]]) * 0.5 * tol(cmd)), anchor=b)
    assert s.kind == "parallel" and s.ref == ("e", ref.id)
    assert np.allclose(s.uv, b + u * ((s.uv - b) @ u), atol=1e-9)


def test_angle_snap_steps(cmd):
    a = np.array([0.0, 0.0])
    def angle(s):
        return math.degrees(math.atan2(s.uv[1], s.uv[0]))

    want = 50 * np.array([math.cos(math.radians(30)), math.sin(math.radians(30))])
    s = cmd.snap(*(want + [0.0, 0.4 * tol(cmd)]), anchor=a)
    assert s.kind == "angle" and angle(s) == pytest.approx(30, abs=1e-9)   # length kept
    cmd.angle_step = 45.0
    want = 50 * np.array([math.cos(math.radians(45)), math.sin(math.radians(45))])
    s = cmd.snap(*(want + [0.3 * tol(cmd), 0.0]), anchor=a)
    assert s.kind == "angle" and angle(s) == pytest.approx(45, abs=1e-9)


def test_quick_snap_and_switching_snaps_off(cmd):
    from bauyrcad.gui.sketcher import Snap
    sk = cmd.sketch
    ln = sk.add_line(sk.add_point(0, 0), sk.add_point(40, 0))
    # near the end point, the endpoint snap wins ...
    s = cmd.snap(39.6, 0.3)
    assert s.kind == "point"
    # ... a Quick Snap "Midpoint" forces the midpoint (with a wider pick radius)
    cmd.win.set_quick_snap("mid")
    s = cmd.snap(20 + 1.3 * tol(cmd), 0.2)
    assert s.kind == "mid" and np.allclose(s.uv, [20, 0])
    # the Quick Snap is used for one click only
    cmd.on_sketch_press(20.1, 0.1, type("E", (), {"display": (0, 0), "ctrl": False, "shift": False,
                                                   "dragging": False})())
    assert cmd.quick_snap is None
    assert not any(a.isChecked() for a in cmd.win.quick_snap_actions.values())
    cmd.tool.finish()
    # switching the midpoint snap off
    cmd.snap_enabled["mid"] = False
    s = cmd.snap(20.2, 0.2)
    assert s.kind != "mid"
    assert ln.id in sk.entities


def test_drag_a_point_onto_another_point_or_a_midpoint(cmd):
    from bauyrcad.gui.sketcher import SelectTool
    sk = cmd.sketch
    l1 = sk.add_line(sk.add_point(0, 0), sk.add_point(40, 0))
    l2 = sk.add_line(sk.add_point(60, 30), sk.add_point(90, 30))
    cmd.set_tool("select")
    assert isinstance(cmd.tool, SelectTool)
    E = type("E", (), {})

    def ev(drag):
        e = E()
        e.display, e.ctrl, e.shift, e.dragging = (0, 0), False, False, drag
        return e

    from bauyrcad.gui.sketcher import Snap
    p = l2.p1
    cmd.on_sketch_press(*sk.pt(p), ev(False))
    for k in range(1, 11):                          # drag it onto the end of the first line
        q = sk.pt(p) + (np.array([40.3, 0.2]) - sk.pt(p)) * k / 10
        cmd.on_sketch_move(*q, ev(True))
    assert cmd.tool.cursor is not None and cmd.tool.cursor.kind == "point"
    cmd.on_sketch_release(40.3, 0.2, ev(True))
    assert l2.p1 == l1.p2                           # joined: one shared point
    assert np.allclose(sk.pt(l1.p2), [40, 0], atol=1e-9)
    # drag the other end of the second line onto the middle of the first one
    q0 = sk.pt(l2.p2).copy()
    cmd.on_sketch_press(*q0, ev(False))
    for k in range(1, 11):
        q = q0 + (np.array([20.2, 0.3]) - q0) * k / 10
        cmd.on_sketch_move(*q, ev(True))
    cmd.on_sketch_release(20.2, 0.3, ev(True))
    assert any(c.type == "midpoint" and ("p", l2.p2) in c.refs for c in sk.constraints)
    assert np.allclose(sk.pt(l2.p2), [20, 0], atol=1e-6)


def test_tangent_arcs_get_unique_relation_ids(cmd):
    """The Tangent Arc tool adds its tangent relation through Sketch.add_constraint, so two
    arcs never share a relation id (ids name SolidWorks dimensions and equation links)."""
    from bauyrcad.gui.sketcher import Snap
    sk = cmd.sketch
    ln = sk.add_line(sk.add_point(0, 0), sk.add_point(40, 0))
    cmd.set_tool("arc_tangent")
    t = cmd.tool
    t.click(Snap(sk.pt(ln.p2), "point", ("p", ln.p2)))
    t.click(Snap(np.array([40.0, 20.0]), "free"))
    arc = [e for e in sk.entities.values() if e.kind == "arc"][-1]
    end = arc.end if np.allclose(sk.pt(arc.end), (40, 20)) else arc.start
    t = cmd.tool
    t.click(Snap(sk.pt(end), "point", ("p", end)))
    t.click(Snap(np.array([20.0, 40.0]), "free"))
    tangents = [c for c in sk.constraints if c.type == "tangent"]
    assert len(tangents) == 2
    ids = [c.id for c in sk.constraints]
    assert len(ids) == len(set(ids)) and 0 not in [c.id for c in tangents]
