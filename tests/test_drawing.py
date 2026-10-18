"""Drawings: hidden-line views, projection, dimensions, associativity and export."""
import json
import math
import subprocess
import sys

import numpy as np
import pytest

from bauyrcad.api import ApiError, Assembly, Drawing, Part
from bauyrcad.core import drawing as D


def plate(path=None, w=80.0, h=50.0, t=10.0, hole=10.0):
    p = Part("plate")
    s = p.sketch("front")
    s.rect((0, 0), (w, h))
    s.circle((w / 2, h / 2), d=hole)
    p.extrude(s, t)
    if path:
        p.save(path)
    return p


@pytest.fixture
def plate_file(tmp_path):
    return plate(tmp_path / "plate.bcad").doc.path


def test_hidden_lines_behind_visible_ones_are_removed(plate_file):
    dr = D.Drawing(plate_file)
    front = dr.add_view("front")
    g = dr.geometry(front)
    circles = [e for e in g.edges if e.kind == "circle"]
    assert len(circles) == 1 and circles[0].visible            # back rim hidden under the front rim
    assert circles[0].r == pytest.approx(5.0)
    assert np.allclose(g.hi - g.lo, [80, 50], atol=1e-6)
    top = dr.add_view("top")
    g = dr.geometry(top)
    hidden = [e for e in g.edges if not e.visible]
    # only the two bore silhouettes, through the 10 mm thickness
    assert len(hidden) == 2 and all(e.kind == "line" for e in hidden)
    assert sorted(round(e.p0[0], 6) for e in hidden) == [35.0, 45.0]
    assert all(np.linalg.norm(e.p1 - e.p0) == pytest.approx(10.0) for e in hidden)
    removed = D.Drawing(plate_file)
    v = removed.add_view("top", hidden=False)
    assert all(e.visible for e in removed.geometry(v).edges)


@pytest.mark.parametrize("projection", ["first", "third"])
def test_standard_views_projection_and_alignment(plate_file, projection):
    dr = D.Drawing(plate_file, projection=projection)
    front, top, side, iso = dr.standard_views()
    assert [front.name, top.name, iso.name] == ["Front", "Top", "Isometric"]
    if projection == "first":                  # ISO: plan below, view from the left on the right
        assert top.y < front.y and side.name == "Left"
    else:                                      # ANSI: plan above, right view on the right
        assert top.y > front.y and side.name == "Right"
    assert side.x > front.x
    assert top.x == pytest.approx(front.x) and side.y == pytest.approx(front.y)
    # projected views stay aligned when their parent moves; they only slide along one axis
    dr.move_view(front, front.x - 12, front.y + 7)
    assert top.x == pytest.approx(front.x) and side.y == pytest.approx(front.y)
    dr.move_view(top, top.x + 40, top.y - 5)
    assert top.x == pytest.approx(front.x)
    # views fit inside the frame, above the title block
    x0, y0, x1, y1 = dr.usable_area()
    for v in (front, top, side, iso):
        a, b, c, d = dr.view_box(v)
        assert x0 - 30 <= a and c <= x1 + 30 and y0 - 30 <= b and d <= y1 + 30
    # every standard orientation projects the model's true extent
    lo, hi = dr.model_bounds()
    ext = hi - lo
    for name, want in (("front", ext[[0, 1]]), ("top", ext[[0, 2]]), ("right", ext[[2, 1]]),
                       ("back", ext[[0, 1]]), ("bottom", ext[[0, 2]]), ("left", ext[[2, 1]])):
        g = dr.geometry(dr.add_view(name))
        assert np.allclose(g.hi - g.lo, want, atol=1e-6), name


def test_dimensions_measure_the_model_at_any_scale(plate_file):
    dr = Drawing(plate_file, scale="1:2")
    assert dr.dr.scale == 0.5
    front = dr.view("Front")
    w = dr.dim(front, (0, 0, 0), (80, 0, 0))
    h = dr.dim(front, (0, 0, 0), (0, 50, 0))
    a = dr.dim(front, (0, 0, 0), (80, 50, 0))                   # diagonal -> aligned
    dia = dr.diameter(front, d=10)
    assert (w.orient, h.orient, a.orient) == ("h", "v", "a")
    assert [w.value, h.value, dia.value] == pytest.approx([80, 50, 10])
    assert a.value == pytest.approx(math.hypot(80, 50))
    assert dia.label() == "⌀10" and dr.radius(front, d=10).label() == "R5"
    forced = dr.dim(front, (0, 0, 0), (80, 50, 0), orient="h")
    assert forced.value == pytest.approx(80)
    # the same dimension drawn at 2:1 measures the same, and its line is twice as long
    lens = []
    for s in (0.5, 2.0):
        dr.dr.scale = s
        prims = [p for p in dr.dr.dim_primitives(w) if isinstance(p, D.Line)]
        lens.append(max(np.linalg.norm(p.b - p.a) for p in prims))
        assert w.label() == "80"
    assert lens[1] / lens[0] == pytest.approx(4.0, rel=1e-6)
    top = dr.view("Top")
    t = dr.dim(top, (0, 0, 0), (0, 0, 10))
    assert t.value == pytest.approx(10) and t.orient == "v"
    with pytest.raises(ApiError):
        dr.diameter(front, d=33)
    with pytest.raises(ApiError):
        dr.title(colour="red")
    auto = dr.auto_dimension(front)
    assert sorted(round(d.value, 6) for d in auto) == [10, 50, 80]


def test_dimensions_follow_model_changes(plate_file):
    dr = Drawing(plate_file)
    front = dr.view("Front")
    w = dr.dim(front, (0, 0, 0), (80, 0, 0))
    dia = dr.diameter(front, d=10)
    # widen the plate to 100 and open the hole to 14, as an edit in the part would
    plate(plate_file, w=100.0, hole=14.0)
    dr.dr.reload()
    assert w.value == pytest.approx(100.0)
    assert dia.value == pytest.approx(14.0)


def test_save_load_and_embedded_model(tmp_path, plate_file):
    dr = Drawing(plate_file)
    dr.dim("Front", (0, 0, 0), (80, 0, 0))
    dr.note("NOTE: DEBURR", at=(40, 60))
    dr.title(drawing_no="P-001", material="S235")
    f = dr.save(tmp_path / "plate.bcdrw")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["model"] == "plate.bcad" and data["embedded_brep"] is None  # linked, relative
    again = Drawing.open(f)
    assert [v.name for v in again.views] == [v.name for v in dr.views]
    assert again.dr.dims[0].value == pytest.approx(80) and again.dr.title["drawing_no"] == "P-001"
    # an unsaved part is drawn from memory and embedded, so the file still opens
    mem = Drawing(plate(None, w=30, h=20))
    f2 = mem.save(tmp_path / "mem.bcdrw")
    back = Drawing.open(f2)
    g = back.dr.geometry(back.view("Front"))
    assert np.allclose(g.hi - g.lo, [30, 20], atol=1e-6)


def test_exports(tmp_path, plate_file):
    ezdxf = pytest.importorskip("ezdxf")
    dr = Drawing(plate_file)
    front = dr.view("Front")
    dr.dim(front, (0, 0, 0), (80, 0, 0))
    dr.dim(front, (0, 0, 0), (0, 50, 0))
    dr.diameter(front, d=10)
    for ext in ("pdf", "svg", "png"):
        out = dr.export(tmp_path / f"p.{ext}")
        assert out.stat().st_size > 5000
    assert (tmp_path / "p.pdf").read_bytes()[:5] == b"%PDF-"
    dr.export(tmp_path / "p.dxf")
    doc = ezdxf.readfile(tmp_path / "p.dxf")
    msp = doc.modelspace()
    dims = msp.query("DIMENSION")
    assert len(dims) == 3
    scale = dr.dr.scale
    shown = sorted(round(d.get_measurement() / scale, 6) if d.dimtype in (0, 32, 160) else 0
                   for d in dims)
    assert 80.0 in shown and 50.0 in shown
    assert {"VISIBLE", "HIDDEN", "CENTER", "DIM"} <= {l.dxf.name for l in doc.layers}
    assert len(msp.query('LINE[layer=="HIDDEN"]')) >= 2        # bore silhouettes in the side views
    assert doc.header["$INSUNITS"] == 4
    # one view at 1:1 in model mm (for laser cutting): true size, no border
    dr.export(tmp_path / "front.dxf", view=front)
    doc = ezdxf.readfile(tmp_path / "front.dxf")
    circles = doc.modelspace().query("CIRCLE")
    assert [round(c.dxf.radius, 6) for c in circles] == [5.0]
    xs = [p for e in doc.modelspace().query('LINE[layer=="VISIBLE"]') for p in (e.dxf.start, e.dxf.end)]
    xs = np.array([[p[0], p[1]] for p in xs])
    assert np.allclose(xs.max(0) - xs.min(0), [80, 50], atol=1e-6)


def test_dwg_needs_the_oda_converter(tmp_path, plate_file, monkeypatch):
    from bauyrcad.core import drawing_export
    monkeypatch.setattr(drawing_export, "find_oda_converter", lambda: None)
    dr = Drawing(plate_file)
    with pytest.raises(D.DrawingError, match="ODA File Converter"):
        dr.export(tmp_path / "p.dwg")


def test_assembly_drawing(tmp_path):
    plate(tmp_path / "base.bcad")
    pin = Part("pin")
    s = pin.sketch("front")
    s.circle((0, 0), d=10)
    pin.extrude(s, 40)
    pin.save(tmp_path / "pin.bcad")
    asm = Assembly()
    base = asm.add(tmp_path / "base.bcad")
    p = asm.add(tmp_path / "pin.bcad", at=(100, 0, 0))
    asm.mate("concentric", p.face(kind="cylinder"), base.face(kind="cylinder"))
    asm.mate("coincident", p.face("-z"), base.face("+z"))
    asm.save(tmp_path / "hinge.bcasm")
    dr = asm.drawing(sheet="A4", iso=False)
    top = dr.view("Top")
    g = dr.dr.geometry(top)
    assert g.hi[0] - g.lo[0] == pytest.approx(80, abs=1e-6)
    assert g.hi[1] - g.lo[1] == pytest.approx(50, abs=1e-6)     # plate 10 + pin sticking out 40
    out = dr.export(tmp_path / "hinge.pdf")
    assert out.exists() and dr.info()["model"].endswith("hinge.bcasm")


def test_cli_drawing(tmp_path, plate_file):
    r = subprocess.run([sys.executable, "-m", "bauyrcad", "drawing", str(plate_file),
                        str(tmp_path / "c.pdf"), str(tmp_path / "c.dxf"), str(tmp_path / "c.bcdrw"),
                        "--sheet", "A4", "--auto-dims", "--title", "Plate"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout)
    assert info["sheet"] == "A4" and len(info["dims"]) == 3
    assert all((tmp_path / f"c.{e}").exists() for e in ("pdf", "dxf", "bcdrw"))


def test_picking_a_circle_rim_picks_the_circle(plate_file):
    """A plain click on a hole's rim (even at a quadrant) picks the circle; Shift-click
    (quadrants=True) picks the quadrant point, for sizes over rounded ends."""
    dr = D.Drawing(plate_file)
    front = dr.add_view("front", at=(200, 150))
    rim = dr.to_sheet(front, (45.0, 25.0))                      # 0 deg on the d10 hole
    kind, v, data = dr.pick_entity(rim, 1.0)
    assert kind == "circle" and data[1] == pytest.approx(5.0)
    kind, v, data = dr.pick_entity(rim, 1.0, quadrants=True)
    assert kind == "point" and np.allclose(data, (45.0, 25.0))
    kind, _, data = dr.pick_entity(dr.to_sheet(front, (0.0, 0.0)), 1.0)
    assert kind == "point" and np.allclose(data, (0.0, 0.0))      # corners still snap
