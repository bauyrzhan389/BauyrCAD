"""Parasolid (.x_t) export written by BauyrCAD itself: the node layouts, the text layout,
Parasolid's topology for each part, the checks, and an independent reader (parasolid-kit,
when installed). SolidWorks opening the files: tests/test_sw_parasolid.py."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.api import Part
from bauyrcad.core import exchange, geometry, parasolid as px, xt_brep
from parasolid_models import PEGBOARD_COLOURS, MODELS, build, pegboard, spline_shell, two_bodies


def _schema_dirs():
    """Folders with Parasolid's own schema files (shipped with SolidWorks, Ansys...)."""
    out = [Path(p) for p in os.environ.get("BAUYRCAD_PARASOLID_SCHEMA", "").split(os.pathsep) if p]
    sw = solidworks.install_dir()
    if sw is not None:
        out += [Path(sw) / "schema_18", Path(sw) / "data" / "pschema"]
    return [d for d in out if d.is_dir()]


def _schema_file(key="12006"):
    for d in _schema_dirs():
        for name in (f"sch_{key}.sch_txt", f"sch_{key}.s_t"):
            if (d / name).is_file():
                return d / name
    return None


def test_node_layouts_match_parasolids_schema_file():
    """Our field tables are the transmitted fields of Parasolid's schema 12006, in order."""
    path = _schema_file()
    if path is None:
        pytest.skip("no Parasolid schema files on this PC (set BAUYRCAD_PARASOLID_SCHEMA)")
    schema, cur = {}, None
    for ln in path.read_text(encoding="latin-1").splitlines():
        m = re.match(r"^(\d+) ([A-Z_0-9]+); .*; \d+ \d+ \d+ *$", ln)
        if m:
            cur = int(m.group(1))
            schema[cur] = (m.group(2), [])
            continue
        m = re.match(r"^([a-z_0-9]+); (\w); 1 \d+ \d+ *$", ln)      # xmt_code 1: written
        if m and cur is not None:
            schema[cur][1].append((m.group(1), m.group(2)))
    for t, (name, fields) in px.FIELDS.items():
        assert schema[t] == (name, [(f, k[0]) for f, k in fields]), name


@pytest.mark.parametrize("name", list(MODELS))
def test_topology_is_parasolids(name, tmp_path):
    """Seams and degenerate edges removed, full circles as ring edges, a cone's apex as an
    isolated vertex: the counts Parasolid itself gives (equal to SolidWorks' own .x_t of
    the shaft, frustum and pointed cone)."""
    part = build(name)
    body = xt_brep.body_from_shape(part.doc.body, name)
    px.validate(body)
    got = (len(body.vertices), len(body.edges), len(body.faces),
           sum(len(f.loops) for f in body.faces))
    assert got == MODELS[name][1]
    # edges within Parasolid's 1e-5 mm are accurate; the others are tolerant, with an
    # SP-curve on every fin and tolerant vertices
    for e in body.edges:
        assert (e.tolerance is None) == (e.curve is not None)
    if body.max_tolerance >= 1e-5:
        assert any(e.tolerance for e in body.edges)
    else:
        assert body.vertex_tol is None and not any(e.tolerance for e in body.edges)
    # closed directions of a B-surface are periodic (no seam edges)
    for f in body.faces:
        if f.surface.kind == "bsurface":
            d = f.surface.data
            for k in ("u", "v"):
                assert d[k + "_periodic"] or not d[k + "_closed"]
    path = tmp_path / f"{name}.x_t"
    part.export(path)
    assert path.read_text().splitlines()[0].startswith("**ABCDEFGHIJ")


def test_text_layout(tmp_path):
    """The common header, then one byte stream in records of at most 80 characters, never
    ending in a space (readers drop trailing spaces) and never with two spaces together."""
    path = tmp_path / "box.x_t"
    build("box").export(path)
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    lines = raw.decode("ascii").splitlines()
    assert lines[0] == "**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" + "*" * 26
    head = lines.index("**END_OF_HEADER" + "*" * 65)
    assert "**PART2;SCH=SCH_1200000_12006;USFLD_SIZE=0;" in lines[:head]
    assert any("FORMAT=text;" in ln for ln in lines[:head])
    data = "".join(lines[head + 1:])                  # the records are one byte stream
    assert data.startswith("T51 : TRANSMIT FILE created by modeller version 120000017 "
                              "SCH_1200000_120060 12 1 ")       # the body is node 1
    for ln in lines:
        assert len(ln) <= 80 and not ln.endswith(" ") and "  " not in ln, ln
    assert data.endswith("1 0") and data[-4] in "+-T"     # a char field, then the terminator


def test_cone_axis_points_where_the_radius_grows():
    """As SolidWorks writes it (frustum.x_t saved by SolidWorks 2024: axis (0, -1, 0) for a
    cone narrowing along +Y), with the outward face normal its natural normal."""
    body = xt_brep.body_from_shape(build("frustum").doc.body)
    (cone,) = [f for f in body.faces if f.surface.kind == "cone"]
    assert np.allclose(cone.surface.data["axis"], (0, -1, 0))
    assert cone.surface.data["radius"] == pytest.approx(0.015)
    assert cone.sense is True


def test_units_are_metres():
    body = xt_brep.body_from_shape(build("box").doc.body)
    assert np.max(np.abs(body.vertices)) == pytest.approx(0.040)


def test_validator_rejects_broken_models():
    body = xt_brep.body_from_shape(build("box").doc.body)
    body.faces[0].loops[0].pop()                     # an edge with one fin, a loop left open
    with pytest.raises(px.XtError):
        px.validate(body)
    body = xt_brep.body_from_shape(build("box").doc.body)
    body.vertices[0] = np.array([600.0, 0, 0])       # outside Parasolid's +-500 m box
    with pytest.raises(px.XtError, match="size box"):
        px.validate(body)


def test_tolerant_edges_carry_sp_curves():
    """A shell of a spline part: OpenCASCADE's edge between the inner face and the offset
    surface lies 7e-5 mm off them, more than Parasolid's 1e-5 mm. It is written tolerant:
    no curve, an SP-curve (2D B-curve in the surface's parameters) on each fin, its vertices
    tolerant, and the accurate edges at those vertices trimmed to their ends."""
    body = xt_brep.body_from_shape(spline_shell().doc.body)
    (k,) = [i for i, e in enumerate(body.edges) if e.tolerance]
    e = body.edges[k]
    assert 1e-8 <= e.tolerance < 1e-6                     # m
    fins = [fin for f in body.faces for loop in f.loops for fin in loop
            if isinstance(fin, px.Fin) and fin.edge == k]
    assert len(fins) == 2 and all(fin.sp and fin.sp["b"]["poles"].shape[1] == 2 for fin in fins)
    for fin in fins:                                      # the trimmed ends meet the vertices
        a, b = px.fin_vertices(body, fin)
        assert np.allclose(fin.sp["p0"], body.vertices[a], atol=e.tolerance)
        assert np.allclose(fin.sp["p1"], body.vertices[b], atol=e.tolerance)
    assert body.vertex_tol[e.start] and body.vertex_tol[e.end]
    trimmed = [x for x in body.edges if x.trim]
    assert trimmed and all({x.start, x.end} & {e.start, e.end} for x in trimmed)
    text = px.to_text(body)
    assert " 137 " in text.replace("\n", "") or "137 " in text   # SP_CURVE nodes are written


def test_too_far_off_is_a_clear_error(tmp_path, monkeypatch):
    """An edge further than MAX_TOLERANCE from its face means a broken model: no file."""
    monkeypatch.setattr(xt_brep, "MAX_TOLERANCE", 1e-6)
    with pytest.raises(exchange.ExchangeError, match="off its face.*needs repair.*STEP works"):
        spline_shell().export(tmp_path / "shell.x_t")
    assert not (tmp_path / "shell.x_t").exists()


def test_names_are_attributes(tmp_path):
    """The part's name is an SDL/TYSA_NAME attribute on its body, listed in the body's
    attribute chains (a LIST and a 20-entry POINTER_LIS_BLOCK, as Parasolid writes them)."""
    body = xt_brep.body_from_shape(build("box").doc.body, "Bracket  2\\a")
    nodes = px._nodes(body)
    b = nodes[0]
    (name,) = [n for n in nodes if n.type == 81]
    assert name.f["owner"] is b and b.f["attributes_features"] is name
    assert name.f["fields"][0].f["values"] == "Bracket 2_a"      # one space, no backslash
    assert name.f["definition"].f["type_id"] == 8017
    lst = b.f["attribute_chains"]
    assert lst.type == 70 and lst.f["list_block"].f["entries"][0] is name
    assert len(lst.f["list_block"].f["entries"]) == 20


def test_assembly_is_written_with_shared_parts_placements_names_and_colours(tmp_path):
    """Each part once (the peg is used twice: one BODY, two INSTANCEs chained as its
    instances), each placement a TRANSFORM (rotation row-major as SolidWorks writes it,
    translation in m, flag 1 translation + 2 rotation), names on the assembly, bodies and
    instances, and each part's faces coloured from its first component (0-255 -> 0-1)."""
    asm = pegboard(tmp_path)
    path = tmp_path / "pegboard.x_t"
    asm.export(path)
    text = path.read_text()
    assert "SDL/TYSA_NAME" in text and "SDL/TYSA_COLOUR" in text
    seen = {}                                     # the model the export writes
    from bauyrcad.core import parasolid
    orig = parasolid.write
    parasolid.write = lambda model, p, name=None: seen.setdefault("m", model)
    try:
        asm.export(tmp_path / "again.x_t")
    finally:
        parasolid.write = orig
    model = seen["m"]
    assert [b.name for b in model.parts] == ["plate", "peg"]
    assert [(i.part, i.name) for i in model.instances] == [(0, "Plate"), (1, "Peg A"), (1, "Peg B")]
    nodes = px._nodes(model)
    assert nodes[0].type == 10
    insts = [n for n in nodes if n.type == 11]
    peg = insts[1].f["part"]
    assert insts[2].f["part"] is peg and peg.f["ref_instance"] is insts[1]
    assert insts[1].f["next_of_part"] is insts[2]
    flags = [i.f["transform"].f["flag"] for i in insts]
    assert flags == [0, 1, 3]
    t = insts[2].f["transform"].f
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    assert np.allclose(t["rotation_matrix"], [c, -s, 0, s, c, 0, 0, 0, 1])
    assert np.allclose(t["translation_vector"], (0.04, 0.02, 0.008))
    for body, comp in zip(model.parts, ("Plate", "Peg A")):
        want = np.array(PEGBOARD_COLOURS[comp]) / 255
        assert all(np.allclose(f.color, want) for f in body.faces)
    defs = [n for n in nodes if n.type == 80]
    assert sorted(d.f["type_id"] for d in defs) == [8001, 8017]   # one definition each


def test_cavity_is_its_own_void_region():
    """A closed void: the solid region has two shells (outer, cavity) and the cavity is a
    finite void region of its own, as SolidWorks writes it."""
    body = xt_brep.body_from_shape(build("cavity").doc.body)
    (lump,) = body.lumps()
    assert [len(s) for s in lump] == [6, 6]
    nodes = px._nodes([body])
    regions = [n for n in nodes if n.type == 19]
    assert [r.f["type"] for r in regions] == ["V", "S", "V"]
    solid = regions[1]
    assert solid.f["shell"].f.get("next") is not None             # two shells


def test_several_bodies_are_an_assembly_of_bodies(tmp_path):
    """Two separate solids: an ASSEMBLY root with one INSTANCE (identity TRANSFORM) per
    BODY, as SolidWorks writes a multi-body part (and opens again as an assembly)."""
    part = two_bodies()
    bodies = xt_brep.bodies_from_shape(part.doc.body, "two")
    assert len(bodies) == 2
    nodes = px._nodes(bodies)
    assert nodes[0].type == 10 and nodes[0].index == 1
    insts = [n for n in nodes if n.type == 11]
    assert len(insts) == 2 and {i.f["part"].type for i in insts} == {12}
    assert all(i.f["transform"].f["rotation_matrix"] == [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0]
               for i in insts)
    path = tmp_path / "two.x_t"
    part.export(path)
    assert path.stat().st_size > 1000


def test_periodic_form_is_solidworks_own():
    """OCC's periodic knots as Parasolid stores them: SolidWorks wrote the closed sweep's
    circle direction as [-2pi, 0, 2pi, 4pi] x [1, 6, 6, 1] with 7 vertices."""
    tau = 2 * math.pi
    kn, mu, n = xt_brep.periodic_form([0.0, tau], [6, 6], 6)
    assert np.allclose(kn, [-tau, 0, tau, 2 * tau]) and mu == [1, 6, 6, 1] and n == 7
    kn, mu, n = xt_brep.periodic_form([0.0, 1.0, 2.0, 3.0], [1, 1, 1, 1], 3)   # C2 cubic
    assert np.allclose(kn, [-3, -2, -1, 0, 1, 2, 3, 4, 5, 6]) and n == 6 and sum(mu) == n + 4


def test_periodic_curve_round_trip():
    """A periodic OCC B-spline written in Parasolid's form evaluates to the same curve."""
    from OCP.Geom import Geom_BSplineCurve
    from OCP.gp import gp_Pnt
    from OCP.TColgp import TColgp_Array1OfPnt
    pts = [(20, 0, 0), (0, 20, 3), (-20, 0, 0), (0, -20, -3), (5, -5, 2)]
    arr = TColgp_Array1OfPnt(1, len(pts))
    for i, q in enumerate(pts, start=1):
        arr.SetValue(i, gp_Pnt(*q))
    bc = Geom_BSplineCurve(arr, xt_brep._arr1([0, 1, 2, 3, 4, 5], float),
                           xt_brep._arr1([1] * 6, int), 3, True)
    g = xt_brep._bcurve(bc, 50.0).data
    assert g["periodic"] and sum(g["mults"]) == len(g["poles"]) + 4
    c = xt_brep._curve_from(g["poles"] / xt_brep.M, None, g["knots"], g["mults"], 3)
    assert all(c.Value(t).Distance(bc.Value(t)) < 1e-9 for t in np.linspace(0, 5, 41))


def test_sweep_knots_where_parameter_lines_turn_are_split():
    """The sweep's surface turns its parameter lines at knots of full multiplicity (its
    normal is continuous): Parasolid calls that not G1, so they are split as SolidWorks
    does on import (degree - 1 and 1, a hair apart); the circle's knots are left alone."""
    body = xt_brep.body_from_shape(build("sweep_open").doc.body)
    (tube,) = [f.surface.data for f in body.faces if f.surface.kind == "bsurface"]
    assert max(tube["v_mults"][1:-1]) < tube["v_degree"]
    assert tube["u_periodic"]


def test_xt_is_an_export_format():
    f = exchange.export_format_for("part.X_T")
    assert f.key == "xt" and f.exact and "*.x_t" in f.filter


@pytest.mark.parametrize("name", ["box", "cylinder", "frustum", "sphere", "torus"])
def test_an_independent_reader_rebuilds_the_same_solid(name, tmp_path):
    """parasolid-kit (MIT/Apache, Rust) reads our file back into OpenCASCADE: the same
    volume. It does not read everything Parasolid does (vertex-trimmed arcs, a cone's apex,
    rational surfaces, a planar face bounded by a B-curve), hence the subset; SolidWorks
    checks them all (tests/test_sw_parasolid.py)."""
    pk = pytest.importorskip("parasolid_kit")
    from parasolid_kit.interop import occt
    schema = _schema_file()
    if schema is None or schema.suffix != ".sch_txt":
        pytest.skip("parasolid-kit needs Parasolid's sch_12006.sch_txt")
    path = tmp_path / f"{name}.x_t"
    part = build(name)
    part.export(path)
    r = pk.read_brep(str(path), schema_dir=str(schema.parent))
    assert r.complete
    res = occt.to_occt(r.brep, source_unit="m", target_unit="mm")
    assert res.report.occt_valid
    (want, _), _ = geometry.accurate_mass_properties(part.doc.body)
    assert res.report.metrics.volume == pytest.approx(want, rel=1e-9)


def test_exact_incline_volume_is_kept():
    """A cylinder cut by an inclined plane: 6750 pi mm³ exactly (ellipse edges)."""
    assert geometry.volume(build("incline").doc.body) == pytest.approx(6750 * math.pi, rel=1e-9)
