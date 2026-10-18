"""Scripting API, command line and live reload - the automation surface."""
import json
import math
import subprocess
import sys

import numpy as np
import pytest

from bauyrcad.api import ApiError, Part


def bracket():
    part = Part("bracket")
    sk = part.sketch("front")
    sk.rect((0, 0), (120, 40))
    sk.circle((100, 20), d=12)
    part.extrude(sk, 10)
    pocket = part.sketch(on=part.face("+z"))
    pocket.rect((25, 10), (70, 30))            # u, v == x, y on a +Z face
    part.cut(pocket, 4)
    return part


def test_build_bracket_and_face_selection():
    part = bracket()
    assert part.volume() == pytest.approx(120 * 40 * 10 - math.pi * 36 * 10 - 45 * 20 * 4)
    assert part.face("-x").centroid[0] == pytest.approx(0)
    assert part.face("+z", where="min").centroid[2] == pytest.approx(6)   # pocket floor
    hole = part.face(at=(106, 20, 5))
    assert not hole.is_planar
    assert np.allclose(part.vertex((119, 41, 11)), [120, 40, 10])


@pytest.mark.parametrize("normal,uv,expect", [
    ("+z", (10, 5), (10, 5, 10)), ("-z", (10, 5), (10, -5, 0)),
    ("+y", (10, 5), (10, 40, -5)), ("-y", (10, 5), (10, 0, 5)),
    ("+x", (5, 10), (120, 10, -5)), ("-x", (5, 10), (0, 10, 5)),
])
def test_face_sketch_axes_follow_the_documented_rule(normal, uv, expect):
    part = Part()
    s = part.sketch()
    s.rect((0, 0), (120, 40))
    part.extrude(s, 10)
    sk = part.sketch(on=part.face(normal))
    assert np.allclose(sk.to_world(*uv), expect)


def test_study_run_and_renders(tmp_path):
    part = bracket()
    st = part.study(material="Aluminium 6061-T6")
    st.fix(part.face("-x"))
    st.force((120, 40, 10), (0, -200, 0))
    s = st.run()
    assert s["reaction"][1] == pytest.approx(200, rel=1e-6)
    assert 0.03 < s["max_displacement"] < 0.08
    part.render(tmp_path / "m.png", study=st)
    st.render(tmp_path / "vm.png")
    for f in ("m.png", "vm.png"):
        assert (tmp_path / f).stat().st_size > 20000
    part.save(tmp_path / "b.bcad")
    part.export(tmp_path / "b.step")
    again = Part.open(tmp_path / "b.bcad")
    assert again.volume() == pytest.approx(part.volume())


def test_sketch_helpers_and_errors():
    part = Part()
    sk = part.sketch("top")
    L = sk.rect((0, 0), (50, 30))
    sk.fillet_all(4)
    c = sk.dim(L[0], 60)
    assert c.value == 60
    st = sk.status()
    assert st["profiles"].startswith("1 profile")
    assert st["dof"] >= 0
    with pytest.raises(ApiError):
        part.cut(sk, 5)                       # nothing to cut yet
    assert part.doc.features  # the failed feature is kept (and reported) like in the GUI


def test_contour_selection_via_api():
    part = Part()
    sk = part.sketch()
    sk.rect((0, 0), (20, 20))
    sk.rect((10, 10), (30, 30))
    part.extrude(sk, 5, contours=[(15, 15)])
    assert part.volume() == pytest.approx(500)


def test_cli_info_run_export(tmp_path):
    part = bracket()
    st = part.study(material="Plain Carbon Steel")
    st.fix(part.face("-x"))
    st.force((120, 40, 10), (0, -100, 0))
    path = part.save(tmp_path / "b.bcad")
    run = [sys.executable, "-m", "bauyrcad"]
    info = json.loads(subprocess.run(run + ["info", str(path)], capture_output=True, text=True,
                                     check=True).stdout)
    assert info["volume_mm3"] == pytest.approx(part.volume())
    assert info["studies"][0]["fixtures"][0]["faces"] == [part.face("-x").index]
    res = json.loads(subprocess.run(run + ["run", str(path), "--png", str(tmp_path / "r.png")],
                                    capture_output=True, text=True, check=True).stdout)
    assert res["applied"] == [0.0, -100.0, 0.0]
    out = subprocess.run(run + ["export", str(path), str(tmp_path / "b.igs")],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out.endswith("b.igs") and (tmp_path / "b.igs").exists()


def test_gui_live_reload(tmp_path):
    pytest.importorskip("PySide6")
    import time

    from PySide6.QtWidgets import QApplication

    from bauyrcad.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "live.bcad"
    p = Part()
    s = p.sketch()
    s.rect((0, 0), (10, 10))
    p.extrude(s, 5)
    p.save(path)

    w = MainWindow()
    w.show()
    w.file_open(str(path))
    app.processEvents()
    assert w.doc.features[-1].depth == 5

    p2 = Part.open(path)                      # "Claude" edits the model with a script
    p2.doc.features[-1].depth = 25
    p2.doc.regenerate()
    time.sleep(0.05)
    p2.save(path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and w.doc.features[-1].depth != 25:
        app.processEvents()
        time.sleep(0.02)
    assert w.doc.features[-1].depth == 25
    w.dirty = False
    w.close()
