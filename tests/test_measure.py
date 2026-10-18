"""Measure: entity sizes, distances, centre / axis / normal distances and angles."""
import math

import numpy as np
import pytest

from bauyrcad.api import Part
from bauyrcad.core import geometry, measure


@pytest.fixture
def plate():
    p = Part("plate")
    s = p.sketch("front")
    s.rect((0, 0), (80, 50))
    s.circle((40, 25), d=20)
    s.circle((10, 10), d=6)
    p.extrude(s, 10)
    return p


def edges_where(shape, **kw):
    n = len(geometry.indexed_map(shape, geometry.TopAbs_EDGE))
    out = []
    for i in range(n):
        e = geometry.edge_info(shape, i)
        if all(abs(e.get(k, -1) - v) < 1e-6 if isinstance(v, float) else e.get(k) == v
               for k, v in kw.items()):
            out.append(i)
    return out


def test_single_entities(plate):
    sh = plate.doc.body
    top = plate.face("+z")
    r = measure.measure(sh, [("face", top.index)])
    assert r["items"][0]["area"] == pytest.approx(4000 - math.pi * (100 + 9))
    circle = edges_where(sh, kind="circle", radius=10.0)[0]
    e = measure.measure(sh, [("edge", circle)])["items"][0]
    assert e["radius"] == pytest.approx(10) and e["length"] == pytest.approx(20 * math.pi)
    assert "⌀20" in measure.report(measure.measure(sh, [("edge", circle)]))[0]
    line = edges_where(sh, kind="line", length=80.0)[0]
    assert measure.measure(sh, [("edge", line)])["items"][0]["length"] == pytest.approx(80)


def test_pairs(plate):
    sh = plate.doc.body
    front, back, side = plate.face("+z"), plate.face("-z"), plate.face("+x")
    r = measure.measure(sh, [("face", back.index), ("face", front.index)])
    assert r["distance"] == pytest.approx(10) and r["normal_distance"] == pytest.approx(10)
    assert r["angle"] == pytest.approx(0)
    assert measure.measure(sh, [("face", front.index), ("face", side.index)])["angle"] == pytest.approx(90)
    big = plate.faces_where(kind="cylinder", d=20)[0].index
    small = plate.faces_where(kind="cylinder", d=6)[0].index
    r = measure.measure(sh, [("face", big), ("face", small)])
    assert r["axis_distance"] == pytest.approx(math.hypot(30, 15))
    assert r["distance"] == pytest.approx(math.hypot(30, 15) - 13)    # wall to wall
    c20 = edges_where(sh, kind="circle", radius=10.0)
    c6 = edges_where(sh, kind="circle", radius=3.0)
    r = measure.measure(sh, [("edge", c20[0]), ("edge", c6[0])])
    assert r["center_distance"] == pytest.approx(math.hypot(30, 15))
    # a line edge on the front face vs the back face: parallel, 10 apart
    line = next(i for i in edges_where(sh, kind="line", length=80.0)
                if geometry.edge_info(sh, i)["p0"][2] == pytest.approx(10))
    r = measure.measure(sh, [("edge", line), ("face", back.index)])
    assert r["distance"] == pytest.approx(10) and r["angle"] == pytest.approx(0)
    v = geometry.vertices(sh)
    i0 = int(np.argmin(np.linalg.norm(v - [0, 0, 0], axis=1)))
    i1 = int(np.argmin(np.linalg.norm(v - [80, 50, 10], axis=1)))
    r = measure.measure(sh, [("vertex", i0), ("vertex", i1)])
    assert r["distance"] == pytest.approx(math.sqrt(80**2 + 50**2 + 10**2))
    assert np.allclose(r["delta"], [80, 50, 10]) and "center_distance" not in r
    r = measure.measure(sh, [("edge", c20[0]), ("edge", c20[1]), ("edge", line)])
    assert r["total_length"] == pytest.approx(40 * math.pi + 80)


def test_api_measure(plate):
    r = plate.measure(plate.face("-z"), plate.face("+z"))
    assert r["normal_distance"] == pytest.approx(10) and any("Normal distance" in t for t in r["text"])
    e = plate.edge(near=(40, 35, 10))                     # the d20 rim on the front face
    r = plate.measure(("edge", e), (80, 50, 10))           # edge + the vertex nearest a point
    assert r["items"][0]["radius"] == pytest.approx(10)
    assert r["center_distance"] == pytest.approx(math.hypot(40, 25))
