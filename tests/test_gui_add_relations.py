import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from test_gui_snaps import cmd, app, pump
from test_gui_cursor_orbit import screen
from bauyrcad.core.sketch import Sketch, constraints, solver


def click(cmd, uv, modifier=Qt.NoModifier):
    QTest.mouseClick(cmd.viewer.plotter.interactor, Qt.LeftButton, modifier,
                    screen(cmd.viewer, cmd.plane.to_world(*uv)))


def relation_button(cmd, kind):
    name = constraints.RELATION_NAMES[kind]
    return next(cmd.rel_buttons.itemAt(i).widget() for i in range(cmd.rel_buttons.count())
                if cmd.rel_buttons.itemAt(i).widget().text().endswith(name))


@pytest.mark.parametrize('kind,axis', [('horizontal', 1), ('vertical', 0)])
def test_real_point_selection_alignment_and_persistence(cmd, tmp_path, kind, axis):
    sk = cmd.sketch
    a = sk.add_line((-65, -35), (-40, -20))
    b = sk.add_line((20, 25), (65, 40))
    cmd.changed()
    cmd.win.sketch_actions['add_relations'].trigger()
    click(cmd, sk.pt(a.p2))
    click(cmd, sk.pt(b.p1))
    assert cmd.selection == [('p', a.p2), ('p', b.p1)]
    QTest.mouseClick(relation_button(cmd, kind), Qt.LeftButton)
    assert sk.pt(a.p2)[axis] == pytest.approx(sk.pt(b.p1)[axis], abs=1e-6)
    assert any(c.type == kind for c in sk.constraints)
    cmd.undo()
    assert not any(c.type == kind for c in cmd.sketch.constraints)
    cmd.redo()
    sk = cmd.sketch
    assert sk.pt(a.p2)[axis] == pytest.approx(sk.pt(b.p1)[axis], abs=1e-6)
    restored = Sketch.from_dict(sk.to_dict())
    assert solver.solve(restored).ok
    assert restored.pt(a.p2)[axis] == pytest.approx(restored.pt(b.p1)[axis], abs=1e-6)
    QTest.qWait(60)
    assert relation_button(cmd, kind).isVisible()
    cmd.win.grab().save(str(tmp_path / f'{kind}-relation.png'))


def test_collinear_lines_and_selection_toggle(cmd):
    sk = cmd.sketch
    a = sk.add_line((-70, -35), (-30, -25))
    b = sk.add_line((20, 20), (70, 35))
    cmd.changed()
    cmd.set_tool('add_relations')
    for e in (a, b):
        click(cmd, (sk.pt(e.p1) + sk.pt(e.p2)) / 2)
    assert cmd.selection == [('e', a.id), ('e', b.id)]
    QTest.mouseClick(relation_button(cmd, 'collinear'), Qt.LeftButton)
    p, q = sk.pt(a.p1), sk.pt(a.p2)
    d = q - p
    for pid in (b.p1, b.p2):
        r = sk.pt(pid) - p
        assert abs(d[0]*r[1] - d[1]*r[0]) / np.linalg.norm(d) < 1e-5
    click(cmd, (sk.pt(a.p1) + sk.pt(a.p2)) / 2)
    assert ('e', a.id) not in cmd.selection
    click(cmd, (-95, 75))
    assert cmd.selection == []


def test_ctrl_selection_and_conflict_rollback(cmd):
    sk = cmd.sketch
    a = sk.add_line((-60, -30), (-40, -20))
    b = sk.add_line((30, 30), (60, 40))
    for pid in (a.p2, b.p1):
        for c in constraints.make_relation(sk, 'fix', [('p', pid)]):
            assert solver.try_add(sk, c)[0]
    cmd.changed()
    cmd.set_tool('select')
    click(cmd, sk.pt(a.p2))
    click(cmd, sk.pt(b.p1), Qt.ControlModifier)
    assert cmd.selection == [('p', a.p2), ('p', b.p1)]
    before = sk.to_dict()
    messages = []
    cmd.message = messages.append
    QTest.mouseClick(relation_button(cmd, 'horizontal'), Qt.LeftButton)
    assert cmd.sketch.to_dict() == before
    assert messages and 'Horizontal' in messages[-1]


@pytest.mark.parametrize('kind', ['horizontal', 'vertical', 'collinear', 'parallel', 'equal'])
def test_relations_for_multiple_lines(kind):
    sk = Sketch()
    lines = [sk.add_line((i*40, i*7), (i*40+20, i*7+10)) for i in range(3)]
    refs = [('e', e.id) for e in lines]
    assert kind in constraints.applicable_relations(sk, refs)
    for c in constraints.make_relation(sk, kind, refs):
        ok, msg = solver.try_add(sk, c)
        assert ok, msg
    assert solver.solve(sk).ok


@pytest.mark.parametrize('kind,axis', [('horizontal', 1), ('vertical', 0)])
def test_three_point_alignment(kind, axis):
    sk = Sketch()
    pts = [sk.add_point(x, y) for x, y in [(10, 20), (40, 25), (70, 55)]]
    refs = [('p', p) for p in pts]
    assert kind in constraints.applicable_relations(sk, refs)
    for c in constraints.make_relation(sk, kind, refs):
        ok, msg = solver.try_add(sk, c)
        assert ok, msg
    assert np.ptp([sk.pt(p)[axis] for p in pts]) < 1e-6
