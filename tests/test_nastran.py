"""Nastran export checks independent of an installed solver."""
import numpy as np
import pytest

from bauyrcad.api import Part
from bauyrcad.fea.analysis import AnalysisError, PreparedStudy, prepare_study
from bauyrcad.fea.mesher import Mesh
from bauyrcad.fea.nastran import write_bdf
from bauyrcad.fea.study import MATERIAL_LIBRARY


def cards(path):
    """Read large fixed-field output without calling the exporter's formatter."""
    result, current, values = [], None, []
    for line in path.read_text().splitlines():
        if line.startswith("*"):
            values.extend(line[i:i + 16].strip() for i in range(8, 72, 16))
        elif "*" in line[:8]:
            if current:
                result.append((current, values))
            current, values = line[:8].strip().rstrip("*"), [line[i:i + 16].strip() for i in range(8, 72, 16)]
        elif current:
            result.append((current, values))
            current, values = None, []
    return result


def make_study(order=2):
    p = Part("Nastran verification")
    sk = p.sketch("front")
    sk.rect((0, 0), (10, 10))
    p.extrude(sk, 30)
    st = p.study()
    st.fix(p.face("-z"))
    st.mesh(size=5, order=order)
    return p, st


@pytest.mark.parametrize("order", [1, 2])
@pytest.mark.parametrize("kind", ["pressure", "face_force", "point"])
def test_setup_load_resultants_and_units(tmp_path, order, kind):
    p, st = make_study(order)
    if kind == "pressure":
        st.pressure(p.face("+z"), 2)
        expected = [0, 0, -200]
    elif kind == "face_force":
        st.face_force(p.face("+z"), (0, -80, 0))
        expected = [0, -80, 0]
    else:
        st.force((10, 10, 30), (0, -80, 0))
        expected = [0, -80, 0]
    target = tmp_path / "study.bdf"
    summary = st.export_bdf(target)
    data = cards(target)
    grid = {int(v[0]): np.array(list(map(float, v[2:5]))) for k, v in data if k == "GRID"}
    loads = [(int(v[1]), float(v[3]) * np.array(list(map(float, v[4:7])))) for k, v in data if k == "FORCE"]
    assert np.sum([v for n, v in loads], axis=0) == pytest.approx(expected, abs=1e-7)
    assert np.sum([np.cross(grid[n], v) for n, v in loads], axis=0) == pytest.approx(summary['applied_moment'], abs=1e-6)
    fixed = [int(x) for k, v in data if k == "SPC1" and v[1] == "123" for x in v[2:] if x]
    assert fixed and all(abs(grid[n][2]) < 1e-8 for n in fixed)
    mat = next(v for k, v in data if k == "MAT1")
    assert float(mat[4]) == pytest.approx(7800e-12)
    assert st.results is None  # exporting does not solve
    assert summary['applied_force'] == pytest.approx(expected, abs=1e-7)
    assert all(len([x for x in v[2:] if x]) == (4 if order == 1 else 10) for k, v in data if k == "CTETRA")


def test_quadratic_tet_edge_order(tmp_path):
    corners = np.array([[0., 0, 0], [2., 0, 0], [0., 3, 0], [0., 0, 4]])
    gmsh_edges = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]
    xyz = np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in gmsh_edges]])
    prepared = PreparedStudy(Mesh(xyz, np.arange(10)[None, :], 2), np.array([0, 1, 2]),
                             [(3, np.array([0., 0, 10]))], np.array([0., 0, 10]), [], [])
    path = tmp_path / 'order.bdf'
    write_bdf(prepared, MATERIAL_LIBRARY['Plain Carbon Steel'], path)
    conn = [int(x) - 1 for k, v in cards(path) if k == 'CTETRA' for x in v[2:] if x]
    for mid, (a, b) in zip(conn[4:], [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]):
        assert xyz[mid] == pytest.approx((xyz[conn[a]] + xyz[conn[b]]) / 2)


def test_invalid_export_preserves_existing_file(tmp_path):
    p, st = make_study()
    st.face_force(p.face("+z"), (0, 0, 0))
    target = tmp_path / 'keep.bdf'
    target.write_text('original')
    with pytest.raises(AnalysisError, match="nonzero"):
        st.export_bdf(target)
    assert target.read_text() == 'original'
    assert list(tmp_path.iterdir()) == [target]


def test_unknown_load_rejected(tmp_path):
    p, st = make_study()
    st.study.loads.append(object())
    with pytest.raises(AnalysisError, match="Unsupported load"):
        st.export_bdf(tmp_path / 'bad.bdf')


def test_export_worker_and_real_window(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtTest import QTest
    from bauyrcad.gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    p, st = make_study()
    st.pressure(p.face('+z'), 2)
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[2]))
    w = MainWindow()
    w._set_document(p.doc)
    w.show()
    w.tabs.setCurrentIndex(2)
    QTest.qWait(200)
    target = tmp_path / 'gui.bdf'
    try:
        assert w.a_bdf.isEnabled()
        w.export_bdf(str(target))
        for _ in range(1000):
            QTest.qWait(20)
            if w._thread is not None and not w._thread.isRunning():
                break
        assert not w._thread.isRunning()
        assert target.exists() and messages
        assert 'SOL 101' in target.read_text()
        w.grab().save(str(tmp_path / 'nastran-export.png'))
    finally:
        if w._thread is not None and w._thread.isRunning():
            w._thread.quit()
            w._thread.wait(30000)
        w.dirty = False
        w.close()
        app.processEvents()
