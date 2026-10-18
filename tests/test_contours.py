"""Selected-contour extrusion (SolidWorks 'Selected Contours')."""
import math

import pytest

from bauyrcad.core import geometry
from bauyrcad.core.document import Document
from bauyrcad.core.features import ExtrudeFeature, SketchFeature
from bauyrcad.core.plane import FRONT
from bauyrcad.core.sketch import builders


def doc_with(make):
    doc = Document()
    s = doc.add_feature(SketchFeature(FRONT))
    make(s.sketch)
    return doc, s


def test_regions_of_nested_profiles():
    doc, s = doc_with(lambda sk: (builders.corner_rectangle(sk, (0, 0), (40, 20)),
                                  sk.add_circle((20, 10), 5)))
    regions = s.regions()
    areas = sorted(round(r.area, 4) for r in regions)
    assert areas == [pytest.approx(math.pi * 25), pytest.approx(800 - math.pi * 25)]


def test_default_extrudes_all_profiles_with_holes():
    doc, s = doc_with(lambda sk: (builders.corner_rectangle(sk, (0, 0), (40, 20)),
                                  sk.add_circle((20, 10), 5)))
    doc.add_feature(ExtrudeFeature(s.id, 10))
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx((800 - math.pi * 25) * 10)


def test_extrude_only_the_island():
    doc, s = doc_with(lambda sk: (builders.corner_rectangle(sk, (0, 0), (40, 20)),
                                  sk.add_circle((20, 10), 5)))
    doc.add_feature(ExtrudeFeature(s.id, 10, regions=[(20, 10)]))
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx(math.pi * 25 * 10, rel=1e-6)


def test_both_contours_merge_into_a_full_plate():
    doc, s = doc_with(lambda sk: (builders.corner_rectangle(sk, (0, 0), (40, 20)),
                                  sk.add_circle((20, 10), 5)))
    doc.add_feature(ExtrudeFeature(s.id, 10, regions=[(20, 10), (2, 2)]))
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx(800 * 10, rel=1e-6)
    assert geometry.count_solids(doc.body) == 1


def test_crossing_contours_are_selectable():
    def make(sk):
        builders.corner_rectangle(sk, (0, 0), (20, 20))
        builders.corner_rectangle(sk, (10, 10), (30, 30))
    doc, s = doc_with(make)
    assert len(s.regions()) == 3
    bad = doc.add_feature(ExtrudeFeature(s.id, 5))            # default: crossing -> error
    doc.regenerate()
    assert bad.error and "select the contours" in bad.error
    bad.regions = [(15, 15)]                                  # just the overlap
    doc.regenerate()
    assert bad.error is None
    assert geometry.volume(doc.body) == pytest.approx(100 * 5, rel=1e-6)
    bad.regions = [(5, 5), (15, 15), (25, 25)]                # the union
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx(700 * 5, rel=1e-6)


def test_cut_through_selected_contour_and_persistence(tmp_path):
    doc = Document()
    base = doc.add_feature(SketchFeature(FRONT))
    builders.corner_rectangle(base.sketch, (0, 0), (60, 30))
    doc.add_feature(ExtrudeFeature(base.id, 10))
    s2 = doc.add_feature(SketchFeature(FRONT))
    s2.sketch.add_circle((15, 15), 5)
    s2.sketch.add_circle((45, 15), 5)
    doc.add_feature(ExtrudeFeature(s2.id, 0, "cut", "through_all_both", regions=[(45, 15)]))
    doc.regenerate()
    assert geometry.volume(doc.body) == pytest.approx((1800 - math.pi * 25) * 10, rel=1e-6)
    doc.save(tmp_path / "c.bcad")
    d2 = Document.load(tmp_path / "c.bcad")
    assert d2.features[-1].regions == [[45.0, 15.0]]
    assert geometry.volume(d2.body) == pytest.approx(geometry.volume(doc.body), rel=1e-9)
    # moving the picked circle away from the stored point breaks the selection clearly
    circ = list(d2.features[2].sketch.entities.values())[1]
    d2.features[2].sketch.set_pt(circ.center, (30, 15))
    d2.regenerate()
    assert "no longer exists" in d2.features[-1].error
