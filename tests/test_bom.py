"""BOM identity, refresh, linked balloons and sheet export acceptance tests."""
import json

import numpy as np
import pytest

from bauyrcad.api import Assembly, Drawing, Part
from bauyrcad.core.document import Document
from bauyrcad.core.drawing import DrawingError, Text
from bauyrcad.core.drawing_export import build_dxf


def fixture_assembly(folder):
    parts = []
    for sub, size, number in (("a", 25, "PL-001"), ("b", 15, "PL-002")):
        path = folder / sub / "plate.bcad"
        path.parent.mkdir(parents=True, exist_ok=True)
        part = Part().properties(part_number=number, description=f"Plate {size} mm")
        s = part.sketch()
        s.rect((0, 0), (size, size))
        part.extrude(s, 6)
        part.save(path)
        parts.append(part)
    assembly = Assembly()
    a = assembly.add(parts[0], at=(0, 0, 0))
    assembly.add(parts[0], at=(45, 0, 0))
    b = assembly.add(parts[1], at=(90, 0, 0))
    assembly.save(folder / "fixture.bcasm")
    return assembly, a, b, parts


def test_metadata_and_legacy_defaults(tmp_path):
    doc = Document()
    assert doc.part_number == doc.description == ""
    doc.part_number, doc.description = "123", "Bracket"
    doc.save(tmp_path / "part.bcad")
    back = Document.load(tmp_path / "part.bcad")
    assert (back.part_number, back.description) == ("123", "Bracket")
    old = doc.to_dict()
    del old["part_number"], old["description"]
    assert Document.from_dict(old).part_number == ""


def test_identity_stability_refresh_and_reused_component_id(tmp_path):
    asm, a, b, parts = fixture_assembly(tmp_path)
    dr = Drawing(asm)
    table = dr.bom()
    assert [r["quantity"] for r in table.rows] == [2, 1]
    assert [r["part_number"] for r in table.rows] == ["PL-001", "PL-002"]
    balloon = dr.balloon("Front", a, (12, 12, 6), (40, 200))
    assert dr.dr.balloon_target(balloon)[0] == "1"
    table.edit(1, description="Custom drawing description")
    dr.save(tmp_path / "fixture.bcdrw")
    dr = Drawing.open(tmp_path / "fixture.bcdrw")
    balloon = dr.dr.balloons[0]
    assert dr.dr.balloon_target(balloon)[0] == "1"
    asm.asm.components.reverse()
    asm.save(tmp_path / "fixture.bcasm")
    dr.refresh_bom()
    assert [r["quantity"] for r in dr.dr.bom.rows] == [2, 1]
    assert dr.dr.bom.displayed(dr.dr.bom.rows[0])["description"] == "Custom drawing description"
    asm.asm.remove_component(a.comp.id)
    replacement = asm.add(parts[0])
    assert replacement.comp.id == a.comp.id  # the old visible ID is reused
    asm.save(tmp_path / "fixture.bcasm")
    dr.refresh_bom()
    assert dr.dr.balloon_target(balloon) == ("?", None)
    # Removing all of item 1 never causes item 2 to inherit number 1.
    asm.asm.components = [b.comp]
    asm.save(tmp_path / "fixture.bcasm")
    dr.refresh_bom()
    assert [r["item"] for r in dr.dr.bom.rows] == [2]
    asm.add(parts[0])
    asm.save(tmp_path / "fixture.bcasm")
    dr.refresh_bom()
    assert [r["item"] for r in dr.dr.bom.rows] == [1, 2]


def test_balloon_pose_projection_and_exports(tmp_path):
    asm, a, b, parts = fixture_assembly(tmp_path)
    dr = Drawing(asm, standard=False)
    v = dr.add_view("front", at=(120, 150))
    dr.bom(at=(220, 260))
    balloon = dr.balloon(v, b, (5, 5, 0), (180, 200))
    assert np.allclose(dr.dr.balloon_target(balloon)[1], dr.dr.to_sheet(v, (95, 5)))
    b.comp.pose.t += [0, 20, 0]
    asm.save(tmp_path / "fixture.bcasm")
    dr.dr.reload()
    assert np.allclose(dr.dr.balloon_target(balloon)[1], dr.dr.to_sheet(v, (95, 25)))
    primitives = dr.dr.primitives()
    assert any(isinstance(p, Text) and p.text == "PL-001" for p in primitives)
    msp = build_dxf(dr.dr).modelspace()
    texts = [e.dxf.text for e in msp.query("TEXT")]
    assert "PL-001" in texts and "PL-002" in texts
    assert len(list(msp.query("CIRCLE"))) >= 1
    for ext in ("pdf", "dxf", "png"):
        dr.export(tmp_path / f"drawing.{ext}")
        assert (tmp_path / f"drawing.{ext}").stat().st_size > 1000
    # Changing the referenced file creates a new item and invalidates the old balloon.
    b.comp.source = str(parts[0].doc.path)
    asm.save(tmp_path / "fixture.bcasm")
    dr.refresh_bom()
    assert dr.dr.balloon_target(balloon) == ("?", None)
    assert "UNRESOLVED" in [e.dxf.text for e in build_dxf(dr.dr).modelspace().query("TEXT")]


def test_legacy_assembly_and_missing_source(tmp_path):
    asm, a, b, parts = fixture_assembly(tmp_path)
    path = tmp_path / "fixture.bcasm"
    data = json.loads(path.read_text())
    for c in data["components"]:
        del c["instance_id"]
    path.write_text(json.dumps(data))
    dr = Drawing(path)
    dr.bom()
    balloon = dr.balloon("Front", a.comp.id, (0, 0, 0), (40, 200))
    dr.refresh_bom()
    assert dr.dr.balloon_target(balloon)[0] == "1"
    parts[0].doc.path.unlink()
    dr.refresh_bom()
    assert dr.dr.bom.rows[0]["quantity"] == 2
    assert dr.dr.bom.rows[0]["status"]
    assert dr.dr.balloon_target(balloon) == ("?", None)
    with pytest.raises(DrawingError, match="saved .bcasm"):
        Drawing(parts[1]).bom()


def test_imported_fallback_and_duplicate_part_numbers(tmp_path):
    asm, a, b, parts = fixture_assembly(tmp_path)
    parts[1].properties(part_number="PL-001").save(parts[1].doc.path)
    parts[0].export(tmp_path / "imported.step")
    asm.add(tmp_path / "imported.step")
    asm.asm.reload()
    rows = asm.bom()
    assert len(rows) == 3
    assert [r["part_number"] for r in rows] == ["PL-001", "PL-001", "imported"]


def test_empty_assembly_and_deleted_view_do_not_retarget(tmp_path):
    asm, a, b, parts = fixture_assembly(tmp_path)
    dr = Drawing(asm, standard=False)
    view = dr.add_view("front")
    dr.bom()
    balloon = dr.balloon(view, a, (0, 0, 0), (40, 200))
    dr.dr.remove_view(view)
    path = dr.save(tmp_path / "empty.bcdrw")
    dr = Drawing.open(path)
    # Exercise counter restart as well as ordinary in-session IDs.
    for _ in range(10):
        assert dr.add_view("front").id != view.id
    assert dr.dr.balloon_target(dr.dr.balloons[0])[0] == "?"
    asm.asm.components = []
    asm.save(tmp_path / "fixture.bcasm")
    dr.dr.reload()
    assert dr.dr.bom.rows == []
    assert all(not dr.dr.geometry(v).edges for v in dr.views)
    assert "UNRESOLVED" in [p.text for p in dr.dr.primitives() if isinstance(p, Text)]
    dr.export(tmp_path / "empty.pdf")


def test_reopen_refreshes_source_metadata(tmp_path):
    asm, a, b, parts = fixture_assembly(tmp_path)
    dr = Drawing(asm)
    dr.bom()
    path = dr.save(tmp_path / "refresh.bcdrw")
    parts[0].properties(part_number="REVISED").save(parts[0].doc.path)
    dr = Drawing.open(path)
    assert dr.dr.bom.rows[0]["part_number"] == "REVISED"
