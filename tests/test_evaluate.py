"""Analytical checks for Evaluate: exact geometry, units and explicit limitations."""
import math

import numpy as np
import pytest

from bauyrcad.api import Part
from bauyrcad.core import geometry, measure
from test_measure import plate, edges_where


def test_points_units_and_three_point_angle(plate):
    r = plate.measure(('point', (1, 2, 3)), ('point', (26.4, 2, 3)), unit='in', decimals=2)
    assert r['measurement'] == pytest.approx(25.4)
    assert 'Minimum distance 1.00 in' in '\n'.join(r['text'])
    assert np.allclose(r['measurement_delta'], (25.4, 0, 0))
    assert 'dX' not in '\n'.join(measure.report(r, show_xyz=False))
    r = plate.measure(('point', (1, 0, 0)), ('point', (0, 0, 0)), ('point', (0, 3, 0)))
    assert r['three_point_angle'] == pytest.approx(90)
    assert 'distance' not in r
    for bad in ((float('nan'), 0, 0), (1, 2)):
        with pytest.raises(geometry.GeometryError):
            plate.measure(('point', bad))
    with pytest.raises(ValueError):
        plate.measure(('point', (0, 0, 0)), unit='feet')


def test_circle_modes_and_axis_clearance(plate):
    shape = plate.doc.body
    large = edges_where(shape, kind='circle', radius=10.0)[0]
    small = edges_where(shape, kind='circle', radius=3.0)[0]
    refs = [('edge', large), ('edge', small)]
    distance = math.hypot(30, 15)
    for mode, expected in [('minimum', distance - 13), ('center', distance), ('maximum', distance + 13)]:
        r = measure.measure(shape, refs, mode=mode)
        assert r['measurement'] == pytest.approx(expected)
        assert np.linalg.norm(r['measurement_point_b'] - r['measurement_point_a']) == pytest.approx(expected)
        assert r['distance'] == pytest.approx(distance - 13)
    axes = [('face', plate.faces_where(kind='cylinder', d=d)[0].index) for d in (20, 6)]
    assert measure.measure(shape, axes, mode='center')['measurement'] == pytest.approx(distance)
    with pytest.raises(ValueError, match='Maximum distance'):
        measure.measure(shape, axes, mode='maximum')
    with pytest.raises(ValueError, match='Center distance'):
        plate.measure(plate.face('+z'), plate.face('-z'), mode='center')


def test_parallel_circles_separated_along_axis(plate):
    edges = edges_where(plate.doc.body, kind='circle', radius=10.0)
    r = plate.measure(*[('edge', e) for e in edges], mode='maximum')
    assert r['measurement'] == pytest.approx(math.hypot(20, 10))
    for point in ('measurement_point_a', 'measurement_point_b'):
        assert np.linalg.norm(r[point][:2] - (40, 25)) == pytest.approx(10)


def test_world_dimensions_and_area_units(plate):
    bounds = plate.dimensions()
    assert np.allclose(bounds['minimum'], (0, 0, 0))
    assert np.allclose(bounds['maximum'], (80, 50, 10))
    assert np.allclose(bounds['size'], (80, 50, 10))
    assert bounds['diagonal'] == pytest.approx(math.sqrt(80**2 + 50**2 + 10**2))
    r = plate.measure(plate.face('+x'), unit='cm', decimals=2)
    assert '5.00 cm²' in r['text'][0]


def test_trimmed_sphere_uses_analytic_center():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Pnt
    # Hemisphere's face centroid is not the centre of the sphere.
    shape = BRepPrimAPI_MakeSphere(gp_Pnt(10, 20, 30), 5, 0, math.pi/2).Shape()
    face = next(f for f in geometry.face_infos(shape) if f.kind == 'sphere')
    r = measure.measure(shape, [('face', face.index), ('point', (10, 20, 40))], mode='center')
    assert r['measurement'] == pytest.approx(10)
    assert np.allclose(r['items'][0]['center'], (10, 20, 30))


def test_maximum_rejects_nonparallel_circles():
    a = dict(kind='circle', center=np.zeros(3), radius=10, axis=np.array([0., 0., 1.]))
    b = dict(kind='circle', center=np.ones(3), radius=3, axis=np.array([1., 0., 0.]))
    with pytest.raises(ValueError, match='parallel planes'):
        measure._maximum_points(a, b)
