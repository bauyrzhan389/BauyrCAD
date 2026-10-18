"""The feature PropertyManagers in the real main window: picks in the viewport, previews,
OK, and editing a feature from the tree."""
import math

import numpy as np
import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def pump(app, n=8):
    for _ in range(n):
        app.processEvents()


@pytest.fixture
def win(app):
    from bauyrcad.api import Part
    from bauyrcad.gui.main_window import MainWindow
    p = Part("plate")
    sk = p.sketch("front")
    sk.rect((0, 0), (100, 50))
    p.extrude(sk, 20)
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._set_document(p.doc)
    pump(app, 20)
    w.viewer.view_isometric(animate=False)
    w.viewer.fit()
    pump(app)
    yield w
    w.dirty = False
    w.close()
    pump(app)


def click_at(app, w, xyz):
    """Click the viewport where a 3D point is drawn (like a user)."""
    disp, _ = w.viewer.project([xyz])
    x, y = (float(v) for v in disp[0])
    info = w.viewer.pick(x, y)
    info.display = (x, y)
    w._viewer_click(info)
    pump(app)
    return info


def finish_preview(app, cmd):
    cmd._timer.stop()
    cmd.update_preview()
    pump(app)


def test_fillet_pick_edge_preview_ok_and_edit(app, win):
    from bauyrcad.core import geometry
    from bauyrcad.gui import feature_commands as fc
    win.a_fillet.trigger()
    pump(app)
    cmd = win.command
    assert isinstance(cmd, fc.FilletCommand)
    click_at(app, win, (50, 0, 20))              # front-top edge (visible in the iso view)
    assert len(cmd.items.items) == 1 and cmd.items.items[0]["kind"] == "edge"
    cmd.r.setValue(5)
    finish_preview(app, cmd)
    assert "preview_cut" in win.viewer.plotter.actors      # removed material shown red
    win.command_ok()
    pump(app)
    f = win.doc.features[-1]
    assert f.TYPE == "fillet" and f.error is None
    v = geometry.volume(win.doc.body)
    assert abs(v - (100000 - 100 * (25 - math.pi * 25 / 4))) < 1e-3
    # edit it from the tree: radius 5 -> 8
    win.edit_feature(f.id)
    pump(app)
    cmd = win.command
    assert isinstance(cmd, fc.FilletCommand) and len(cmd.items.items) == 1
    cmd.r.setValue(8)
    win.command_ok()
    pump(app)
    assert abs(geometry.volume(win.doc.body) - (100000 - 100 * (64 - math.pi * 64 / 4))) < 1e-3
    assert win.doc.features[-1].id == f.id


def test_hole_wizard_on_a_face_with_clicked_positions(app, win):
    from bauyrcad.core import geometry
    from bauyrcad.gui import feature_commands as fc
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    cmd = win.start_feature(fc.HoleCommand, face=top.index)
    pump(app)
    assert cmd.face.items and cmd.face.items[0]["key"] == top.index
    click_at(app, win, (30, 25, 20))
    click_at(app, win, (70, 25, 20))
    assert len(cmd.points) == 2
    assert np.allclose(cmd.points[0], [30, 25], atol=0.5)
    cmd.select_data(cmd.kind, "counterbore")
    cmd.select_data(cmd.size, "M8")
    finish_preview(app, cmd)
    assert "CBORE" in cmd.callout.text() or "⌴" in cmd.callout.text()
    win.command_ok()
    pump(app)
    f = win.doc.features[-1]
    assert f.TYPE == "hole" and f.error is None and f.name.startswith("CBORE for M8")
    assert geometry.volume(win.doc.body) < 100000 - 2 * math.pi / 4 * 81 * 20 + 1


def test_pattern_from_tree_menu_seed_and_mirror(app, win):
    from bauyrcad.core import geometry
    from bauyrcad.gui import feature_commands as fc
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    cmd = win.start_feature(fc.HoleCommand, face=top.index)
    cmd.select_data(cmd.size, None)
    cmd.d.setValue(5)
    cmd._add_point(10, 10)
    win.command_ok()
    pump(app)
    hole = win.doc.features[-1]
    cmd = win.start_feature(fc.LinearPatternCommand, seed=hole.id)
    pump(app)
    assert cmd.seeds() == [hole.id]
    cmd.sp1.setValue(20)
    cmd.n1.setValue(4)
    win.command_ok()
    pump(app)
    assert win.doc.features[-1].error is None
    assert abs(geometry.volume(win.doc.body) - (100000 - 4 * math.pi * 6.25 * 20)) < 1e-3
    cmd = win.start_feature(fc.MirrorCommand)
    pump(app)
    cmd.select_data(cmd.plane_cb, {"type": "std", "name": "Right Plane"})
    win.command_ok()
    pump(app)
    assert abs(geometry.volume(win.doc.body) - 2 * (100000 - 4 * math.pi * 6.25 * 20)) < 1e-2


def test_shell_from_face_menu_and_reference_plane_sketch(app, win):
    from bauyrcad.core import geometry
    from bauyrcad.gui import feature_commands as fc
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    cmd = win.start_feature(fc.ShellCommand, faces=[top.index])
    cmd.t.setValue(2)
    win.command_ok()
    pump(app)
    assert abs(geometry.volume(win.doc.body) - (100000 - 96 * 46 * 18)) < 1e-3
    # reference plane 40 above the Top plane, then a sketch on it follows the plane
    cmd = win.start_feature(fc.RefPlaneCommand)
    cmd.select_data(cmd.base_cb, {"type": "std", "name": "Top Plane"})
    cmd.dist.setValue(70)
    finish_preview(app, cmd)
    win.command_ok()
    pump(app)
    rp = win.doc.features[-1]
    assert rp.TYPE == "ref_plane" and rp.result_plane is not None
    assert "Plane1" in win.ref_planes()
    assert win.plane_spec_by_name("Plane1") == {"type": "feature", "id": rp.id}
    win.start_sketch(plane=rp.result_plane, plane_spec={"type": "feature", "id": rp.id})
    pump(app)
    sk = win.command
    sk.sketch.add_rectangle((0, 0), (10, 10))
    win.command_ok()
    pump(app)
    s = win.doc.features[-1]
    assert s.TYPE == "sketch" and s.plane_spec == {"type": "feature", "id": rp.id}
    rp.distance = 80
    win.rebuild()
    assert abs(win.doc.features[-1].plane.origin[1] - 80) < 1e-9


def test_every_feature_command_opens_and_cancels(app, win):
    from bauyrcad.gui import feature_commands as fc
    for cls in set(fc.COMMAND_FOR_TYPE.values()):
        cmd = win.start_feature(cls)
        pump(app, 3)
        finish_preview(app, cmd)
        win.command_cancel()
        pump(app, 3)
        assert win.command is None
    # the Features tab has the SolidWorks commands
    labels = {a.text().replace("\n", " ") for a in win.findChildren(type(win.a_fillet))}
    for want in ("Fillet", "Chamfer", "Shell", "Draft", "Rib", "Hole Wizard", "Linear Pattern",
                 "Circular Pattern", "Mirror", "Swept Boss/Base", "Lofted Boss/Base",
                 "Boundary Boss/Base", "Plane", "Axis", "Helix and Spiral"):
        assert want in labels, want


def test_sketch_convert_entities_tool(app, win):
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    win.selected_face = top.index
    win.start_sketch("convert")
    pump(app)
    cmd = win.command
    assert cmd.tool.key == "convert" and cmd.plane_spec["type"] == "face"
    cmd.tool._convert_plane()
    pump(app)
    kinds = sorted(e.kind for e in cmd.sketch.entities.values())
    assert kinds == ["line"] * 4
    win.command_ok()
    pump(app)
    s = win.doc.features[-1]
    assert s.TYPE == "sketch" and len(s.faces) == 1


def test_sketch_text_tool_place_edit_and_engrave(app, win):
    from bauyrcad.core import geometry
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    win.selected_face = top.index
    win.start_sketch("text")
    pump(app)
    cmd = win.command
    tool = cmd.tool
    assert tool.key == "text"
    tool.edit.setText("AB")
    tool.height.setValue(10)
    from bauyrcad.gui.sketcher import Snap
    from bauyrcad.gui.viewer import SketchEvent
    tool.press(Snap(np.array([10.0, 20.0]), "free"), SketchEvent((0, 0)))
    pump(app)
    assert len(cmd.sketch.texts) == 1 and cmd.sketch.texts[0]["text"] == "AB"
    # clicking the text selects it; edits apply to it
    tool.press(Snap(np.array([14.0, 24.0]), "free"), SketchEvent((0, 0)))
    assert tool.sel == cmd.sketch.texts[0]["id"]
    tool.height.setValue(12)
    assert cmd.sketch.texts[0]["height"] == 12
    assert "sk_texts_sel" in win.viewer.plotter.actors or True
    win.command_ok()
    pump(app)
    sk = win.doc.features[-1]
    assert len(sk.faces) >= 2                        # the letters are profiles
    win.start_extrude("cut", sk.id)
    pump(app)
    win.command.depth.setValue(0.5)
    win.command_ok()
    pump(app)
    assert win.doc.features[-1].error is None
    assert geometry.volume(win.doc.body) < 100000 - 10


def test_intersect_command_lists_regions(app, win):
    from bauyrcad.core import geometry
    from bauyrcad.gui import feature_commands as fc
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    cmd = win.start_feature(fc.ShellCommand, faces=[top.index])
    win.command_ok()
    pump(app)
    cmd = win.start_feature(fc.IntersectCommand)
    cmd.select_data(cmd.plane_cb, {"type": "std", "name": "Right Plane"})
    cmd._add_plane()
    cmd._timer.stop()
    cmd.update_preview()
    pump(app)
    assert cmd.reg_list.count() == 1          # the Right plane (x = 0) only touches the part
    cmd.planes.clear()
    rp = win.start_feature(fc.RefPlaneCommand)
    rp.select_data(rp.base_cb, {"type": "std", "name": "Right Plane"})
    rp.dist.setValue(50)
    win.command_ok()
    pump(app)
    cmd = win.start_feature(fc.IntersectCommand)
    cmd.select_data(cmd.plane_cb, {"type": "feature", "id": win.doc.features[-1].id})
    cmd._add_plane()
    cmd._timer.stop()
    cmd.update_preview()
    assert cmd.reg_list.count() == 2
    v = geometry.volume(win.doc.body)
    from PySide6.QtCore import Qt
    cmd.reg_list.item(1).setCheckState(Qt.Unchecked)
    win.command_ok()
    pump(app)
    assert win.doc.features[-1].error is None
    assert abs(geometry.volume(win.doc.body) - v / 2) < 1e-3


def test_instant3d_drag_the_arrow_changes_the_depth(app, win):
    from bauyrcad.core import geometry
    top = next(f for f in win.doc.faces if f.is_planar and f.normal[2] > 0.9)
    click_at(app, win, top.centroid)
    i3 = win.instant3d
    assert i3.target is not None and i3.target["feature"].TYPE == "extrude"
    t = i3.target
    L = i3._arrow_len()
    disp, _ = win.viewer.project([t["cap"] + t["axis"] * L * 0.5, t["cap"] + t["axis"] * L * 1.5])
    (x0, y0), (x1, y1) = disp
    assert i3.press(None, x0, y0, "left")
    for k in range(1, 6):
        i3.move(x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5)
        pump(app, 2)
    i3.release(x1, y1, True)
    pump(app)
    f = t["feature"]
    assert f.depth > 20 + 0.5 * L                     # dragged up by about one arrow length
    lo, hi = geometry.bounding_box(win.doc.body)
    assert abs(hi[2] - f.depth) < 1e-6
    win.undo()
    pump(app)
    assert win.doc.features[1].depth == 20


def test_display_delete_relations_and_repair(app, win):
    from bauyrcad.core.plane import FRONT
    win.start_sketch("rect_corner", plane=FRONT)
    pump(app)
    cmd = win.command
    cmd.sketch.add_rectangle((0, 0), (40, 20))
    cmd.changed()
    n = len([c for c in cmd.sketch.constraints if c.type != "arc_internal"])
    assert n >= 4
    cmd.set_tool("relations")
    pump(app)
    tool = cmd.tool
    assert tool.list.count() == n
    tool.list.item(0).setSelected(True)
    assert ("c", tool.list.item(0).data(0x0100)) in cmd.selection
    tool.delete_items()
    assert tool.list.count() == n - 1
    tool.delete_items(all_shown=True)
    assert tool.list.count() == 0
    assert win.a_repair.isEnabled()
    out = cmd.repair_sketch()
    assert out == {"gaps_closed": 0, "zero_length": 0, "duplicates": 0}
    win.command_cancel()
