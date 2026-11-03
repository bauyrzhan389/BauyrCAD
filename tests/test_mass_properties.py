"""Independent analytical mass/inertia and material-persistence checks."""
import math
import numpy as np
import pytest

from bauyrcad.api import Part, Assembly
from bauyrcad.core import geometry, mass_properties as mass


def block():
    p = Part('Block')
    s = p.sketch('front'); s.rect((0, 0), (10, 20)); p.extrude(s, 30)
    return p


def test_box_analytic_and_units():
    p = block().set_material(density=1000)
    r = p.mass_properties()
    assert r['volume'] == pytest.approx(6000)
    assert r['area'] == pytest.approx(2200)
    assert r['mass'] == pytest.approx(.006)
    assert np.allclose(r['center_of_gravity'], (5, 10, 15))
    expected = np.diag([.006*(20**2+30**2)/12, .006*(10**2+30**2)/12, .006*(10**2+20**2)/12])
    assert np.allclose(r['inertia'], expected)
    assert np.allclose(r['inertia_origin'], [[2.6,-.3,-.45],[-.3,2,-.9],[-.45,-.9,1]])
    assert np.allclose(r['principal_axes'] @ np.diag(r['principal_moments']) @ r['principal_axes'].T, expected)
    assert np.linalg.det(r['principal_axes']) == pytest.approx(1)


def test_cylinder_analytic():
    p = Part(); s = p.sketch('front'); s.circle((0, 0), r=5); p.extrude(s, 20)
    p.set_material(density=7800)
    r = p.mass_properties(); m = math.pi*25*20*7800e-9
    assert r['mass'] == pytest.approx(m)
    assert np.allclose(r['inertia'], np.diag([m*(3*25+400)/12]*2+[m*25/2]))


def test_missing_invalid_density_and_open_shape():
    p = block()
    assert p.mass_properties()['mass'] is None
    assert p.mass_properties()['center_of_gravity'] is None
    for value in (0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError, match='Density'):
            p.set_material(density=value)
    with pytest.raises(ValueError, match='Unknown material'):
        p.set_material('Unknown')
    with pytest.raises(geometry.GeometryError, match='solids'):
        mass.part(geometry.subshape(p.doc.body, 'face', 0))


def test_part_material_roundtrip_and_study_independence(tmp_path):
    p = block().set_material('Aluminium 6061-T6')
    study = p.study(material='Plain Carbon Steel')
    p.save(tmp_path/'p.bcad')
    q = Part.open(tmp_path/'p.bcad')
    assert q.doc.mass_material == p.doc.mass_material
    assert q.mass_properties()['mass'] == pytest.approx(6000*2700e-9)
    assert study.study.material.density == 7800
    q.set_material()
    assert q.mass_properties()['mass'] is None


def test_assembly_rotations_mixed_densities_and_roundtrip(tmp_path):
    p = block().set_material(density=1000); p.save(tmp_path/'p.bcad')
    a = Assembly()
    first = a.add(tmp_path/'p.bcad')
    second = a.add(tmp_path/'p.bcad', at=(100,0,0))
    second.rotate('z', 90).set_material(density=2000)
    r = a.mass_properties()
    centers = np.array([[5,10,15], [90,5,15]])
    weights = np.array([.006,.012]); center = weights @ centers / weights.sum()
    # Second rectangle rotated 90 degrees exchanges X/Y principal inertias.
    own = [np.diag([.65,.5,.25]), np.diag([1,1.3,.5])]
    expected = sum(I + m*((d@d)*np.eye(3)-np.outer(d,d))
                   for I,m,d in zip(own,weights,centers-center))
    assert r['mass'] == pytest.approx(.018)
    assert np.allclose(r['center_of_gravity'], center)
    assert np.allclose(r['inertia'], expected)
    assert np.allclose(r['components'][1]['inertia'], own[1])
    a.save(tmp_path/'a.bcasm')
    b = Assembly.open(tmp_path/'a.bcasm')
    assert np.allclose(b.mass_properties()['inertia'], expected)
    second.set_material()
    assert a.mass_properties()['mass'] == pytest.approx(.012)
    assert Part.open(tmp_path/'p.bcad').doc.mass_material['density'] == 1000
    first.comp.doc.mass_material = None
    assert a.mass_properties()['mass'] is None
    assert a.mass_properties()['missing_density'] == [first.name]


def test_missing_component_is_not_silently_excluded(tmp_path):
    p=block(); p.save(tmp_path/'p.bcad')
    a=Assembly(); c=a.add(tmp_path/'p.bcad')
    c.comp.shape = None
    with pytest.raises(ValueError, match='unavailable'):
        a.mass_properties()
