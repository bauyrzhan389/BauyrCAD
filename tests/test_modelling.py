import math

import pytest

from bauyrcad.core import geometry
from bauyrcad.core.document import Document
from bauyrcad.core.features import ExtrudeFeature, SketchFeature
from bauyrcad.core.plane import FRONT, RIGHT, TOP, SketchPlane


def plate_with_hole():
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (100, 50))
    s.sketch.add_circle((25, 25), 10)
    doc.add_feature(ExtrudeFeature(s.id, 20))
    doc.regenerate()
    return doc


def test_extrude_with_hole_volume():
    doc = plate_with_hole()
    assert doc.features[1].error is None
    assert geometry.volume(doc.body) == pytest.approx(100 * 50 * 20 - math.pi * 100 * 20)


@pytest.mark.parametrize("plane", [FRONT, TOP, RIGHT])
def test_extrude_on_each_reference_plane(plane):
    doc = Document()
    s = doc.add_feature(SketchFeature(plane))
    s.sketch.add_rectangle((0, 0), (10, 20))
    doc.add_feature(ExtrudeFeature(s.id, 5))
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx(1000)
    lo, hi = geometry.bounding_box(doc.body)
    # extrusion goes along the plane normal
    assert float((hi - lo) @ plane.normal) == pytest.approx(5, abs=1e-6)


def test_midplane_extrude_is_symmetric():
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (10, 10))
    doc.add_feature(ExtrudeFeature(s.id, 8, end="midplane"))
    doc.regenerate()
    lo, hi = geometry.bounding_box(doc.body)
    assert lo[2] == pytest.approx(-4, abs=1e-6) and hi[2] == pytest.approx(4, abs=1e-6)


def test_blind_cut_from_top_face_goes_into_material():
    doc = plate_with_hole()
    top = next(f for f in doc.faces if f.is_planar and f.normal[2] > 0.9)
    s = doc.add_feature(SketchFeature(SketchPlane.from_face([0, 0, 20], top.normal)))
    s.sketch.add_rectangle((50, 10), (70, 20))  # local coords == global x, y here
    cut = doc.add_feature(ExtrudeFeature(s.id, 5, "cut"))
    v0 = geometry.volume(doc.body)
    doc.regenerate()
    assert cut.error is None
    assert geometry.volume(doc.body) == pytest.approx(v0 - 20 * 10 * 5)


def test_through_all_both_cut():
    doc = plate_with_hole()
    s = doc.add_feature(SketchFeature(SketchPlane([0, 0, 10], [0, 0, 1], [1, 0, 0])))
    s.sketch.add_circle((75, 25), 5)
    doc.add_feature(ExtrudeFeature(s.id, 0, "cut", "through_all_both"))
    v0 = geometry.volume(doc.body)
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx(v0 - math.pi * 25 * 20)


def test_cut_without_body_is_an_error():
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (1, 1))
    cut = doc.add_feature(ExtrudeFeature(s.id, 1, "cut"))
    doc.regenerate()
    assert cut.error and "Nothing to cut" in cut.error


def test_open_sketch_extrude_reports_error_and_later_features_survive():
    doc = plate_with_hole()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_line((0, 0), (10, 0))
    bad = doc.add_feature(ExtrudeFeature(s.id, 5))
    doc.regenerate()
    assert bad.error and "open" in bad.error
    assert geometry.volume(doc.body) > 0


def test_edit_and_regenerate():
    doc = plate_with_hole()
    doc.features[1].depth = 40
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx((5000 - math.pi * 100) * 40)


def test_suppress_and_delete_with_dependents():
    doc = plate_with_hole()
    doc.features[1].suppressed = True
    doc.regenerate()
    assert doc.body is None
    removed = doc.remove_feature(doc.features[0].id)
    assert len(removed) == 2 and not doc.features


def test_save_load_roundtrip(tmp_path):
    doc = plate_with_hole()
    st = doc.add_study()
    doc.save(tmp_path / "p.bcad")
    doc2 = Document.load(tmp_path / "p.bcad")
    assert [f.name for f in doc2.features] == ["Sketch1", "Boss-Extrude1"]
    assert doc2.studies[0].name == st.name
    assert geometry.volume(doc2.body) == pytest.approx(geometry.volume(doc.body))


def test_step_export_import_roundtrip(tmp_path):
    doc = plate_with_hole()
    path = tmp_path / "plate.step"
    doc.export_step(path)
    assert path.read_text().startswith("ISO-10303-21")
    doc2 = Document()
    doc2.import_step(path)
    doc2.regenerate()
    assert geometry.volume(doc2.body) == pytest.approx(geometry.volume(doc.body), rel=1e-6)
    # an imported body can be modified further
    s = doc2.add_feature(SketchFeature(SketchPlane([0, 0, 20], [0, 0, 1], [1, 0, 0])))
    s.sketch.add_circle((75, 25), 5)
    doc2.add_feature(ExtrudeFeature(s.id, 0, "cut", "through_all"))
    doc2.regenerate()
    assert all(f.error is None for f in doc2.features)
    assert geometry.volume(doc2.body) == pytest.approx(
        geometry.volume(doc.body) - math.pi * 25 * 20, rel=1e-6)


def test_tessellation_face_ids_cover_all_faces():
    doc = plate_with_hole()
    t = doc.tessellation
    assert set(t.face_ids.tolist()) == set(range(len(doc.faces)))
    assert len(t.edge_polylines) > 0


# ---- cut direction (SolidWorks: a cut from a boundary of the body goes into the material) ----

def block_with_front_sketch(circle=(10, 10), r=3):
    """60x40 block extruded +Z from the Front plane (z 0..10) and a circle on the Front plane."""
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (60, 40))
    doc.add_feature(ExtrudeFeature(s.id, 10))
    s2 = doc.add_feature(SketchFeature(FRONT))
    s2.sketch.add_circle(circle, r)
    return doc, s2


@pytest.mark.parametrize("end, depth, removed", [("through_all", 0, 10), ("blind", 4, 4)])
def test_cut_from_body_boundary_flips_into_material(end, depth, removed):
    doc, s2 = block_with_front_sketch()
    cut = doc.add_feature(ExtrudeFeature(s2.id, depth, "cut", end))
    doc.regenerate()
    assert cut.error is None and cut.flipped
    assert geometry.volume(doc.body) == pytest.approx(60 * 40 * 10 - math.pi * 9 * removed)
    # the preview shows the tool on the side that is actually cut
    lo, hi = geometry.bounding_box(cut.tool_shape(doc.context_before(cut)))
    assert lo[2] == pytest.approx(0, abs=1e-6) and hi[2] > 0


@pytest.mark.parametrize("end, depth, removed", [("through_all", 0, 10), ("blind", 4, 4)])
def test_explicit_reverse_cut_is_kept(end, depth, removed, tmp_path):
    doc, s2 = block_with_front_sketch()
    cut = doc.add_feature(ExtrudeFeature(s2.id, depth, "cut", end, reverse=True))
    doc.regenerate()
    assert cut.error is None and not cut.flipped
    assert geometry.volume(doc.body) == pytest.approx(60 * 40 * 10 - math.pi * 9 * removed)
    doc.save(tmp_path / "p.bcad")
    doc2 = Document.load(tmp_path / "p.bcad")
    assert doc2.features[-1].reverse and doc2.features[-1].error is None
    assert geometry.volume(doc2.body) == pytest.approx(geometry.volume(doc.body))


def test_explicit_reverse_out_of_the_body_is_an_error():
    doc = plate_with_hole()
    top = SketchPlane.from_face([0, 0, 20], [0, 0, 1])
    s = doc.add_feature(SketchFeature(top))
    s.sketch.add_circle((75, 25), 5)
    v0 = geometry.volume(doc.body)
    cut = doc.add_feature(ExtrudeFeature(s.id, 0, "cut", "through_all", reverse=True))
    doc.regenerate()
    assert cut.error and "does not intersect" in cut.error
    assert geometry.volume(doc.body) == pytest.approx(v0)


def test_cut_missing_the_body_is_an_error():
    doc, s2 = block_with_front_sketch(circle=(100, 100))
    cut = doc.add_feature(ExtrudeFeature(s2.id, 0, "cut", "through_all"))
    doc.regenerate()
    assert cut.error == "The cut does not intersect the model"
    assert geometry.volume(doc.body) == pytest.approx(60 * 40 * 10)


def test_cut_from_mid_plane_keeps_default_direction():
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    s.sketch.add_rectangle((0, 0), (60, 40))
    doc.add_feature(ExtrudeFeature(s.id, 10))
    s2 = doc.add_feature(SketchFeature(SketchPlane([0, 0, 6], [0, 0, 1], [1, 0, 0])))
    s2.sketch.add_circle((10, 10), 3)
    cut = doc.add_feature(ExtrudeFeature(s2.id, 0, "cut", "through_all"))
    doc.regenerate()
    assert cut.error is None and not cut.flipped
    assert geometry.volume(doc.body) == pytest.approx(60 * 40 * 10 - math.pi * 9 * 6)


def test_api_through_cut_on_front_plane():
    from bauyrcad.api import Part
    b = Part("p")
    s = b.sketch("front")
    s.rect((0, 0), (60, 40))
    b.extrude(s, 10)
    s2 = b.sketch("front")
    s2.circle((10, 10), d=6)
    b.cut(s2, through=True)
    assert b.volume() == pytest.approx(60 * 40 * 10 - math.pi * 9 * 10)
