"""Revolved boss / cut - volumes checked with Pappus' theorem and closed forms."""
import math

import numpy as np
import pytest

from bauyrcad.api import ApiError, Part
from bauyrcad.core import geometry
from bauyrcad.core.document import Document


def ring_part(angle=360.0, **kw):
    part = Part()
    sk = part.sketch("front")
    sk.centerline((0, -10), (0, 50))          # axis = global Y
    sk.rect((10, 0), (20, 30))
    part.revolve(sk, angle, **kw)
    return part


def test_full_revolve_is_a_tube():
    part = ring_part()
    assert part.volume() == pytest.approx(math.pi * (20 ** 2 - 10 ** 2) * 30, rel=1e-6)
    lo, hi = part.bbox()
    assert np.allclose(lo, [-20, 0, -20], atol=1e-6) and np.allclose(hi, [20, 30, 20], atol=1e-6)


def test_partial_revolve_follows_pappus():
    part = ring_part(90)
    area, r_centroid = 10 * 30, 15.0
    assert part.volume() == pytest.approx(area * 2 * math.pi * r_centroid / 4, rel=1e-6)


def test_midplane_and_reverse():
    mid = ring_part(90, midplane=True)
    lo, hi = mid.bbox()
    assert hi[2] == pytest.approx(-lo[2], abs=1e-6)        # symmetric about the sketch plane
    rev = ring_part(90, reverse=True)
    fwd = ring_part(90)
    assert rev.volume() == pytest.approx(fwd.volume())
    assert np.sign(rev.bbox()[0][2] + rev.bbox()[1][2]) == -np.sign(fwd.bbox()[0][2] + fwd.bbox()[1][2])


def test_profile_touching_axis_makes_a_solid():
    part = Part()
    sk = part.sketch("front")
    sk.centerline((0, 0), (0, 10))
    sk.rect((0, 0), (8, 40))                   # one edge lies on the axis
    part.revolve(sk)
    assert part.volume() == pytest.approx(math.pi * 64 * 40, rel=1e-6)


def test_profile_crossing_axis_is_rejected():
    part = Part()
    sk = part.sketch("front")
    sk.centerline((0, 0), (0, 10))
    sk.rect((-5, 0), (8, 40))
    with pytest.raises(ApiError, match="crosses the revolve axis"):
        part.revolve(sk)


def test_axis_selection():
    part = Part()
    sk = part.sketch("front")
    sk.rect((10, 0), (20, 30))
    with pytest.raises(ApiError, match="centerline"):
        part.revolve(sk)
    part = Part()
    sk = part.sketch("front")
    sk.centerline((0, 0), (0, 10))
    ax2 = sk.centerline((0, -5), (30, -5))     # a second centerline, along X at y = -5
    sk.rect((10, 0), (20, 30))
    with pytest.raises(ApiError, match="several centerlines"):
        part.revolve(sk)
    part.doc.features.pop()                    # drop the failed feature
    part.revolve(sk, axis=ax2)                 # about y = -5: centroid radius 20
    assert part.volume() == pytest.approx(300 * 2 * math.pi * 20, rel=1e-6)


def test_revolve_cut_groove_and_persistence(tmp_path):
    part = Part()
    shaft = part.sketch("front")
    shaft.centerline((0, 0), (0, 10))
    shaft.rect((0, 0), (10, 100))
    part.revolve(shaft)                        # Ø20 x 100 shaft
    groove = part.sketch("front")
    groove.centerline((0, 0), (0, 10))
    groove.rect((8, 40), (12, 50))             # 2 mm deep, 10 mm wide groove
    part.revolve_cut(groove)
    expect = math.pi * 100 * 100 - math.pi * (10 ** 2 - 8 ** 2) * 10
    assert part.volume() == pytest.approx(expect, rel=1e-6)
    assert [f.name for f in part.doc.features][-1] == "Cut-Revolve1"
    part.save(tmp_path / "shaft.bcad")
    again = Document.load(tmp_path / "shaft.bcad")
    assert again.features[-1].TYPE == "revolve" and again.features[-1].operation == "cut"
    assert geometry.volume(again.body) == pytest.approx(expect, rel=1e-6)


def test_revolve_selected_contour():
    part = Part()
    sk = part.sketch("front")
    sk.centerline((0, 0), (0, 10))
    sk.rect((10, 0), (20, 10))
    sk.rect((30, 0), (40, 10))
    part.revolve(sk, contours=[(35, 5)])      # only the outer ring
    assert part.volume() == pytest.approx(math.pi * (40 ** 2 - 30 ** 2) * 10, rel=1e-6)


def test_revolved_faces_have_outward_normals():
    """Revolved caps have left-handed plane axes: normals must still point outward."""
    part = Part()
    sk = part.sketch("front")
    sk.centerline((0, 0), (0, 10))
    sk.rect((0, 0), (5, 36))
    part.revolve(sk)
    assert part.face("-y").centroid[1] == pytest.approx(0, abs=1e-9)
    assert part.face("+y").centroid[1] == pytest.approx(36)
    for f in part.faces_where(kind="plane"):
        c = part.faces[f.index].centroid
        inside = c - 0.5 * f.normal                     # just inside must be material
        assert abs(inside[1] - 18) < 18


def test_revolve_cut_missing_the_body_is_an_error():
    part = Part()
    shaft = part.sketch("front")
    shaft.centerline((0, 0), (0, 10))
    shaft.rect((0, 0), (10, 100))
    part.revolve(shaft)
    groove = part.sketch("front")
    groove.centerline((0, 0), (0, 10))
    groove.rect((20, 40), (25, 50))            # outside the Ø20 shaft
    with pytest.raises(ApiError, match="does not intersect the model"):
        part.revolve_cut(groove)
