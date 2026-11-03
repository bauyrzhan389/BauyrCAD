"""Solid suitability: valid solids, surfaces, loose topology and unavailable geometry."""
import pytest
from bauyrcad.api import Part
from bauyrcad.core import geometry


def box():
    p=Part(); s=p.sketch('front'); s.rect((0,0),(10,20)); p.extrude(s,30)
    return p


def test_valid_solid_and_compound_check():
    shape=box().doc.body
    report=geometry.check_geometry(shape)
    assert report['complete'] and report['valid'] and report['closed_solid']
    assert report['issues'] == []
    assert (report['solids'], report['faces'], report['edges'], report['vertices']) == (1,6,12,8)
    assert report['volume'] == pytest.approx(6000)
    cylinder=Part(); s=cylinder.sketch('front'); s.circle((0,0),d=10); cylinder.extrude(s,20)
    assert geometry.check_geometry(cylinder.doc.body)['closed_solid']


def test_surface_missing_reversed_and_loose_geometry():
    shape=box().doc.body
    surface=geometry.subshape(shape,'face',0)
    report=geometry.check_geometry(surface)
    assert report['complete'] and report['valid'] and not report['closed_solid']
    assert report['solids'] == 0 and report['volume'] is None
    assert not geometry.check_geometry(None)['complete']
    reversed_report=geometry.check_geometry(shape.Reversed())
    assert reversed_report['complete'] and not reversed_report['closed_solid']
    assert any('volume' in issue for issue in reversed_report['issues'])
    extra=geometry.point_shape((100,100,100))
    mixed=geometry.check_geometry(geometry.compound([shape,extra]))
    assert not mixed['closed_solid'] and any('Vertices' in issue for issue in mixed['issues'])


def test_failed_kernel_query_is_incomplete(monkeypatch):
    shape=box().doc.body
    def fail(*a,**kw):
        raise RuntimeError('simulated kernel failure')
    monkeypatch.setattr(geometry,'volume',fail)
    report=geometry.check_geometry(shape)
    assert not report['complete'] and not report['closed_solid']
    assert 'simulated kernel failure' in report['issues'][-1]


def test_api_failed_features_and_missing_assembly_components(tmp_path):
    from bauyrcad.api import Assembly
    from bauyrcad.core.geometry_check import report
    p = box()
    assert p.check_geometry()['closed_solid']
    p.doc.features[-1].error = 'Feature could not rebuild'
    result = p.check_geometry()
    assert not result['complete'] and 'Feature failed' in report(result)
    p.doc.features[-1].error = None
    p.save(tmp_path/'part.bcad')
    a = Assembly(); first = a.add(tmp_path/'part.bcad')
    assert a.check_geometry()['ok']
    first.comp.shape = None
    result = a.check_geometry()
    assert not result['complete'] and not result['ok']
    assert 'CHECK INCOMPLETE' in report(result)
    assert not Assembly().check_geometry()['ok']
