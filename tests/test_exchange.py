import math

import pytest

from bauyrcad.core import exchange, geometry
from bauyrcad.core.document import Document
from bauyrcad.core.features import ExtrudeFeature, SketchFeature
from bauyrcad.core.plane import FRONT
from bauyrcad.core.sketch import builders, ops


def slotted_plate():
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    L = builders.corner_rectangle(s.sketch, (0, 0), (60, 30))
    ops.fillet(s.sketch, L[0].p2, 5)                  # rounded corner (arc in profile)
    builders.straight_slot(s.sketch, (15, 15), (35, 15), (15, 19))
    doc.add_feature(ExtrudeFeature(s.id, 8))
    doc.regenerate()
    return doc


def test_arcs_in_profiles_give_exact_volumes():
    doc = slotted_plate()
    assert doc.features[1].error is None
    area = 60 * 30 - (25 - math.pi * 25 / 4) - (20 * 8 + math.pi * 16)
    assert geometry.volume(doc.body) == pytest.approx(area * 8, rel=1e-9)


def test_spline_profile_extrudes():
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    sk = s.sketch
    a, b = sk.add_point(0, 0), sk.add_point(40, 0)
    sk.add_line(a, b)
    builders.spline(sk, [b, (30, 15), (10, 20), a])
    doc.add_feature(ExtrudeFeature(s.id, 5))
    doc.regenerate()
    assert doc.features[1].error is None
    (region,) = sk.regions()
    assert geometry.volume(doc.body) == pytest.approx(region.area() * 5, rel=2e-3)


@pytest.mark.parametrize("fmt", [f.key for f in exchange.EXPORT_FORMATS])
def test_every_export_format_writes_a_file(tmp_path, fmt):
    doc = slotted_plate()
    ext = exchange.FORMATS[fmt].extensions[0]
    path = tmp_path / f"part{ext}"
    doc.export(path, fmt)
    assert path.stat().st_size > 200


@pytest.mark.parametrize("fmt,schema", [("step214", "AUTOMOTIVE_DESIGN"),
                                        ("step242", "AP242"),
                                        ("step203", "CONFIG_CONTROL_DESIGN")])
def test_step_schemas(tmp_path, fmt, schema):
    path = tmp_path / "p.step"
    slotted_plate().export(path, fmt)
    header = path.read_text()[:2000]
    assert schema in header


@pytest.mark.parametrize("fmt", ["step214", "step242", "iges", "brep"])
def test_exact_formats_roundtrip_as_solids(tmp_path, fmt):
    doc = slotted_plate()
    path = tmp_path / f"p{exchange.FORMATS[fmt].extensions[0]}"
    doc.export(path, fmt)
    d2 = Document()
    d2.import_file(path)
    d2.regenerate()
    assert geometry.count_solids(d2.body) == 1
    assert geometry.volume(d2.body) == pytest.approx(geometry.volume(doc.body), rel=1e-5)


def test_imported_iges_saved_in_project(tmp_path):
    doc = slotted_plate()
    doc.export(tmp_path / "p.igs")
    d2 = Document()
    d2.import_file(tmp_path / "p.igs")
    d2.save(tmp_path / "p.bcad")
    d3 = Document.load(tmp_path / "p.bcad")
    assert d3.features[0].format == "iges"
    assert geometry.volume(d3.body) == pytest.approx(geometry.volume(doc.body), rel=1e-5)
