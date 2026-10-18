import math

import numpy as np
import pytest

from bauyrcad.api import Part, ApiError
from bauyrcad.core import geometry
from bauyrcad.core.document import Document
from bauyrcad.core.plane import SketchPlane


@pytest.mark.parametrize("construction", [False, True])
def test_external_line_follows_edits_and_persists(tmp_path, construction):
    p = Part()
    axis_sketch = p.sketch("front")
    axis = axis_sketch.line((0, -10), (0, 40), construction=construction)
    profile = p.sketch("front")
    profile.rect((10, 0), (20, 30))
    f = p.revolve(profile, axis=axis)
    assert p.volume() == pytest.approx(math.pi * 300 * 30)
    assert f.depends_on() == [profile.feature.id, axis_sketch.feature.id]
    path = tmp_path / "external-axis.bcad"
    p.save(path)
    doc = Document.load(path)
    f = doc.features[-1]
    source = doc.feature(f.axis_sketch_id)
    for pid in (axis.p1, axis.p2):
        source.sketch.points[pid].x = -5
    doc.regenerate()
    assert f.error is None
    assert geometry.volume(doc.body) == pytest.approx(math.pi * (25**2 - 15**2) * 30)
    source.sketch.entities.pop(axis.id)
    doc.regenerate()
    assert "axis line no longer exists" in f.error


def test_axis_from_different_sketch_plane_and_cut():
    p = Part()
    # A line in YZ also lies in XY: the world Y axis.
    ax = p.sketch(SketchPlane((0, 0, 0), (1, 0, 0), (0, 1, 0)))
    line = ax.line((-10, 0), (50, 0))
    s = p.sketch("front")
    s.rect((0, 0), (20, 30))
    p.revolve(s, axis=line)
    g = p.sketch("front")
    g.rect((15, 10), (22, 20))
    p.revolve_cut(g, axis=line)
    assert p.volume() == pytest.approx(math.pi * (20**2 * 30 - (20**2 - 15**2) * 10))


def test_ordinary_axis_in_profile_sketch_is_not_a_profile():
    p = Part()
    s = p.sketch("front")
    line = s.line((0, -10), (0, 40))
    s.rect((10, 0), (20, 30))
    p.revolve(s, axis=line, angle=180)
    assert p.volume() == pytest.approx(math.pi * 300 * 30 / 2)
    assert not s.sk.entities[line.id].construction


def test_profile_boundary_can_still_be_the_axis():
    p = Part()
    s = p.sketch("front")
    s.rect((0, 0), (10, 30))
    line = next(e for e in s.sk.entities.values()
                if np.allclose([s.sk.pt(e.p1)[0], s.sk.pt(e.p2)[0]], 0))
    p.revolve(s, axis=line)
    assert p.volume() == pytest.approx(math.pi * 100 * 30)


@pytest.mark.parametrize("kind", ["off_plane", "zero", "suppressed"])
def test_bad_external_axes_report_clear_errors(kind):
    p = Part()
    ax = p.sketch(SketchPlane((0, 0, 5 if kind == "off_plane" else 0), (0, 0, 1), (1, 0, 0)))
    line = ax.line((0, -10), (0, 40))
    s = p.sketch("front")
    s.rect((10, 0), (20, 30))
    if kind == "zero":
        ax.sk.points[line.p2].y = -10
    if kind == "suppressed":
        ax.feature.suppressed = True
    message = {"off_plane": "profile sketch plane", "zero": "zero length", "suppressed": "suppressed"}[kind]
    with pytest.raises(ApiError, match=message):
        p.revolve(s, axis=line)


def test_reference_axis_roundtrip_edit_and_suppression(tmp_path):
    p = Part()
    axis = p.axis(point=(0, 0, 0), direction=(0, 1, 0))
    sk = p.sketch("front")
    sk.rect((10, 0), (20, 30))
    f = p.revolve(sk, axis=axis)
    assert axis.id in f.depends_on()
    path = tmp_path / "reference-axis.bcad"
    p.save(path)
    doc = Document.load(path)
    axis = doc.feature(axis.id)
    axis.point = [-5, 0, 0]
    doc.regenerate()
    assert doc.features[-1].error is None
    assert geometry.volume(doc.body) == pytest.approx(math.pi * 400 * 30)
    axis.suppressed = True
    doc.regenerate()
    assert "suppressed" in doc.features[-1].error


def test_straight_model_edge_axis_roundtrip(tmp_path):
    p = Part()
    base = p.sketch("front")
    base.rect((-4, 0), (0, 30))
    p.extrude(base, 2)
    from bauyrcad.core import modeling
    edge = next(e for e in modeling.edge_infos(p.doc.body)
                if e["kind"] == "line" and np.allclose(e["mid"], (0, 15, 0)))
    sk = p.sketch("front")
    sk.rect((10, 0), (20, 30))
    p.revolve(sk, axis=edge)
    path = tmp_path / "edge-axis.bcad"
    p.save(path)
    doc = Document.load(path)
    assert doc.features[-1].axis_spec["type"] == "edge"
    assert not doc.features[-1].error
    assert geometry.volume(doc.body) == pytest.approx(240 + math.pi * 300 * 30)


def test_reference_axis_outside_plane_and_foreign_part_rejected():
    p = Part()
    axis = p.axis(point=(0, 0, 5), direction=(0, 1, 0))
    sk = p.sketch("front")
    sk.rect((10, 0), (20, 30))
    with pytest.raises(ApiError, match="profile sketch plane"):
        p.revolve(sk, axis=axis)
    foreign = Part().axis(point=(0, 0, 0), direction=(0, 1, 0))
    with pytest.raises(ApiError, match="belong"):
        p.revolve(sk, axis=foreign)
