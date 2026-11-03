"""Complete-check guarantees and analytical intersection volumes."""
import numpy as np
import pytest

from bauyrcad.api import Assembly, Part
from bauyrcad.core import geometry
from bauyrcad.core.assembly import AssemblyError


def assembly(tmp_path):
    p=Part('Cube'); s=p.sketch('front'); s.rect((0,0),(10,10)); p.extrude(s,10)
    p.save(tmp_path/'cube.bcad')
    a=Assembly(); first=a.add(tmp_path/'cube.bcad'); second=a.add(tmp_path/'cube.bcad', at=(5,0,0))
    return a, first, second


def test_complete_overlap_touching_gap_and_threshold(tmp_path):
    a, first, second = assembly(tmp_path)
    r=a.check_interference()
    assert r['component_count'] == 2 and r['pair_volume_sum'] == pytest.approx(500)
    assert r['overlaps'] == [{'a': first.name, 'b': second.name, 'volume': pytest.approx(500)}]
    assert not a.check_interference(min_volume=501)['overlaps']
    assert not a.check_interference(ignore=[(first,second)])['overlaps']
    hits=a.asm.interference(strict=True,include_shapes=True)
    assert geometry.volume(hits[0]['shape']) == pytest.approx(500)
    second.move(dx=5)
    assert not a.check_interference()['overlaps']  # exactly touching
    second.move(dx=2)
    assert not a.check_interference()['overlaps']  # positive clearance


def test_rotated_containment_and_identical_instances(tmp_path):
    a, first, second=assembly(tmp_path)
    second.move(dx=-5)
    assert a.check_interference()['pair_volume_sum'] == pytest.approx(1000)
    second.rotate('z',45,about=(5,5,5))
    # Two equal squares, rotated 45° about their common centre.
    assert a.check_interference()['pair_volume_sum'] == pytest.approx((200*np.sqrt(2)-200)*10)
    p=Part(); s=p.sketch('front'); s.rect((2,2),(4,4)); p.extrude(s,2); p.save(tmp_path/'small.bcad')
    a.asm.components.pop(); a.add(tmp_path/'small.bcad',at=(0,0,2))
    assert a.check_interference()['pair_volume_sum'] == pytest.approx(8)


def test_failed_missing_open_invalid_and_cancelled_are_not_clean(tmp_path,monkeypatch):
    a, first, second=assembly(tmp_path)
    for threshold in (-1,float('nan'),float('inf')):
        with pytest.raises(AssemblyError): a.check_interference(threshold)
    original=second.comp.shape
    second.comp.shape=None
    with pytest.raises(AssemblyError,match='incomplete'): a.check_interference()
    second.comp.shape=geometry.subshape(original,'face',0)
    with pytest.raises(AssemblyError,match='closed solid'): a.check_interference()
    second.comp.shape=original
    with pytest.raises(AssemblyError,match='Ignored pairs'):
        a.asm.interference(strict=True,ignore=[('missing',first.comp.id)])
    def cancel(*_): raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError): a.asm.interference(strict=True,progress=cancel)
    def fail(*_): raise geometry.GeometryError('test failure')
    monkeypatch.setattr(geometry,'common',fail)
    with pytest.raises(AssemblyError,match='intersection failed'):
        a.asm.interference(strict=True,include_shapes=True)
