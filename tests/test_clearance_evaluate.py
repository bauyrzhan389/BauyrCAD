"""Complete clearance checks: gaps, contact, overlap, containment, missing geometry."""
import numpy as np
import pytest
from bauyrcad.api import Assembly, Part
from bauyrcad.core import geometry
from bauyrcad.core.assembly import AssemblyError
from test_interference_evaluate import assembly


def test_clearance_states_and_same_component(tmp_path):
    a, first, second = assembly(tmp_path)
    for movement, distance, volume in [(0, 0, 500), (5, 0, 0), (3, 3, 0)]:
        second.move(dx=movement)
        r = a.clearance(first, second)
        assert r['distance'] == pytest.approx(distance)
        assert r['overlap'] == pytest.approx(volume, abs=1e-7)
        assert np.linalg.norm(np.asarray(r['point_b'])-r['point_a']) == pytest.approx(distance)
    with pytest.raises(AssemblyError, match='different'):
        a.clearance(first, first)
    second.rotate('z', 45, about=(18, 5, 5))
    assert a.clearance(first, second)['distance'] == pytest.approx(8 - 5*np.sqrt(2))


def test_strict_nearest_missing_and_cancelled(tmp_path):
    a, first, second = assembly(tmp_path)
    third = a.add(tmp_path/'cube.bcad', at=(100, 0, 0))
    third.comp.shape = None
    with pytest.raises(AssemblyError, match='incomplete'):
        a.clearance(first)  # API is a complete check by default
    assert a.clearance(first, second)['overlap'] == pytest.approx(500)
    third.comp.shape = geometry.subshape(first.comp.shape, 'face', 0)
    with pytest.raises(AssemblyError, match='closed solid'):
        a.clearance(first)
    third.comp.shape = first.comp.shape
    def cancel(*_):
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        a.asm.clearance(first.comp.id, strict=True, progress=cancel)
    assert a.clearance(first)['b'] == second.name


def test_containment_is_interference(tmp_path):
    p = Part(); s = p.sketch('front'); s.rect((0,0),(20,20)); p.extrude(s,20)
    p.save(tmp_path/'outer.bcad')
    p = Part(); s = p.sketch('front'); s.rect((0,0),(2,2)); p.extrude(s,2)
    p.save(tmp_path/'inner.bcad')
    a = Assembly(); outer = a.add(tmp_path/'outer.bcad'); inner = a.add(tmp_path/'inner.bcad',at=(5,5,5))
    r = a.clearance(outer,inner)
    assert r['distance'] == 0 and r['overlap'] == pytest.approx(8)
