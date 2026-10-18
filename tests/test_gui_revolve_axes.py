import math
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from bauyrcad.api import Part
from bauyrcad.core import geometry
from bauyrcad.core.document import Document
from bauyrcad.core.plane import FRONT
from test_gui_revolve_workflow import win
from test_gui_cursor_orbit import screen


def test_pick_line_from_another_sketch_and_edit_persisted_feature(win, tmp_path):
    p = Part()
    axis_sketch = p.sketch("front")
    line = axis_sketch.line((0, -10), (0, 40))
    profile = p.sketch("front")
    profile.rect((10, 0), (20, 30))
    win._set_document(p.doc)
    win.start_revolve("boss", profile.feature.id)
    cmd = win.command
    key = cmd._axis_key(axis_sketch.feature.id, line.id)
    assert cmd.axis_cb.findData(key) >= 0
    v = win.viewer
    v.view_normal_to(FRONT, animate=False)
    cam = v.renderer.GetActiveCamera()
    cam.SetFocalPoint(10, 15, 0)
    cam.SetPosition(10, 15, 150)
    cam.SetParallelScale(35)
    v.render()
    QTest.mouseClick(cmd.pick_axis, Qt.LeftButton)
    QTest.qWait(50)
    win.grab().save(str(tmp_path / "axis-picking.png"))
    QTest.mouseClick(v.plotter.interactor, Qt.LeftButton, Qt.NoModifier, screen(v, (0, 15, 0)))
    assert cmd.axis_cb.currentData() == key
    assert not cmd.pick_axis.isChecked()
    assert not cmd.msg.text()
    assert not cmd.picks  # axis click must not select a profile contour
    win.grab().save(str(tmp_path / "revolve-external-axis.png"))
    win.command_ok()
    assert geometry.volume(win.doc.body) == pytest.approx(math.pi * 300 * 30)
    feature = win.doc.features[-1]
    assert feature.axis_sketch_id == axis_sketch.feature.id
    win.undo()
    assert win.doc.features[-1].TYPE == "sketch"
    win.redo()
    path = tmp_path / "revolved.bcad"
    win.doc.save(path)
    win._set_document(Document.load(path))
    feature = win.doc.features[-1]
    win.edit_feature(feature.id)
    assert win.command.axis_cb.currentData() == key
    win.command.angle.setValue(180)
    win.command_ok()
    assert geometry.volume(win.doc.body) == pytest.approx(math.pi * 300 * 30 / 2)


def test_same_sketch_ordinary_axis_keeps_contour_selection(win):
    p = Part()
    sk = p.sketch("front")
    axis = sk.line((0, -10), (0, 40))
    sk.rect((10, 0), (20, 30))
    win._set_document(p.doc)
    win.start_revolve("boss", sk.feature.id)
    cmd = win.command
    cmd.axis_cb.setCurrentIndex(cmd.axis_cb.findData(cmd._axis_key(sk.feature.id, axis.id)))
    assert len(cmd.regions) == 1
    assert not cmd.msg.text()
    win.command_ok()
    assert not win.doc.feature(sk.feature.id).sketch.entities[axis.id].construction
    assert geometry.volume(win.doc.body) == pytest.approx(math.pi * 300 * 30)


def test_select_other_previews_overlapping_references_and_filter(win, tmp_path):
    p = Part()
    axis = p.axis(point=(0, 0, 0), direction=(0, 1, 0))
    source = p.sketch("front")
    source.line((0, -10), (0, 40))
    sk = p.sketch("front")
    sk.rect((10, 0), (20, 30))
    win._set_document(p.doc)
    win.start_revolve("boss", sk.feature.id)
    cmd, v = win.command, win.viewer
    v.view_normal_to(FRONT, animate=False)
    cam = v.renderer.GetActiveCamera()
    cam.SetFocalPoint(10, 15, 0)
    cam.SetPosition(10, 15, 150)
    cam.SetParallelScale(35)
    v.render()
    QTest.mouseClick(cmd.pick_axis, Qt.LeftButton)
    QTest.mouseClick(v.plotter.interactor, Qt.LeftButton, Qt.NoModifier, screen(v, (0, 15, 0)))
    assert cmd.axis_choices.isVisible()
    assert len(cmd.axis_choices.candidates) == 2
    index = next(i for i, c in enumerate(cmd.axis_choices.candidates) if c.kind == "axis")
    cmd.axis_choices.items.setCurrentRow(index)
    QTest.qWait(100)
    win.grab().save(str(tmp_path / "select-other-axis.png"))
    QTest.mouseClick(cmd.axis_choices.use, Qt.LeftButton)
    assert cmd.make().axis_spec == {"type": "feature", "id": axis.id}
    assert not cmd.pick_axis.isChecked()
    assert not cmd.axis_choices.isVisible()
    assert not cmd.picks
    cmd.axis_filter.setCurrentIndex(cmd.axis_filter.findData("axis"))
    assert len(cmd._filtered_candidates()) == 1
    win.command_ok()
    assert geometry.volume(win.doc.body) == pytest.approx(math.pi * 300 * 30)
    win.undo()
    win.redo()
    win.edit_feature(win.doc.features[-1].id)
    assert win.command.make().axis_spec == {"type": "feature", "id": axis.id}


def model_edge_revolve():
    p = Part()
    base = p.sketch('front'); base.rect((0, 0), (10, 20)); p.extrude(base, 10)
    edge = p.edges_where(kind='line', direction='y', near=(10, 10, 0))[0]
    profile = p.sketch('front'); profile.rect((12, 0), (15, 10))
    revolve = p.revolve(profile, angle=90, axis=edge)
    return p, base, revolve


def test_moved_model_axis_is_recognized_without_changing_document_on_cancel(win, tmp_path):
    from pathlib import Path
    p, base, revolve = model_edge_revolve()
    original = revolve.axis_spec.copy()
    for point in base.sk.points.values():
        if point.x == 10:
            point.x = 11
    p.doc.regenerate()
    assert not revolve.error
    assert p.reference_status(revolve)[0]['status'] == 'moved'
    win._set_document(p.doc); win.edit_feature(revolve.id)
    cmd = win.command
    assert cmd.axis_cb.currentText().startswith('Model edge')
    assert cmd.make().axis_spec['ref']['mid'][0] == pytest.approx(11)
    before = p.doc.to_dict()
    cmd.sketch_loaded()  # rebuilding choices retains the recognized edge
    assert cmd.axis_cb.currentText().startswith('Model edge')
    folder = Path('projects/revolve-repair-verification'); folder.mkdir(parents=True, exist_ok=True)
    QTest.qWait(150); win.grab().save(str(folder / 'moved-axis.png'))
    win.command_cancel()
    assert p.doc.to_dict() == before and revolve.axis_spec == original
    win.edit_feature(revolve.id); win.command_ok()
    assert win.doc.feature(revolve.id).axis_spec['ref']['mid'][0] == pytest.approx(11)
    path = tmp_path / 'moved-axis.bcad'; win.doc.save(path)
    assert not Document.load(path).feature(revolve.id).error
    win.undo(); assert win.doc.feature(revolve.id).axis_spec == original
    win.redo(); assert not win.doc.feature(revolve.id).error


@pytest.mark.parametrize('mid,label', [([1000,1000,1000], 'Missing'), ([5,10,0], 'Ambiguous')])
def test_unresolved_revolve_axis_retained_until_explicit_replacement(win, monkeypatch, mid, label):
    from PySide6.QtWidgets import QMessageBox
    p, base, revolve = model_edge_revolve()
    revolve.axis_spec['ref']['mid'] = mid
    p.doc.regenerate()
    win._set_document(p.doc); win.edit_feature(revolve.id)
    cmd = win.command
    assert cmd.axis_cb.currentText().startswith(label)
    assert cmd.make().axis_spec == revolve.axis_spec
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    before = p.doc.to_dict(); win.command_ok()
    assert win.command is cmd and warnings and p.doc.to_dict() == before
    candidate = next(c for c in cmd._axis_candidates if c.kind == 'edge'
                     and cmd._axis_specs[c.key]['ref']['mid'] == [10.0,10.0,0.0])
    cmd._choose_candidate(candidate)
    win.command_ok()
    assert win.command is None and not win.doc.feature(revolve.id).error
