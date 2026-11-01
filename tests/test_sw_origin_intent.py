import math
import pytest

from bauyrcad import solidworks
from bauyrcad.api import Part
from test_sw_origin import origin_part
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks, reopen

pytestmark = needs_solidworks


def constrained_status(sw, doc, name='Sketch1'):
    feature = sw.T(sw.T(doc, 'IPartDoc').FeatureByName(name), 'IFeature')
    return sw.T(feature.GetSpecificFeature2(), 'ISketch').GetConstrainedStatus()


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
@pytest.mark.parametrize('kind', ['circle', 'rectangle', 'coincident', 'horizontal'])
def test_origin_is_constrained_after_edit_and_reopen(sw, tmp_path, plane, kind):
    part = origin_part(plane, kind)
    doc = _check_part(sw, tmp_path, part)
    if kind != 'horizontal': assert constrained_status(sw, doc) == 3
    value = .05 if kind in ('rectangle', 'coincident') else .018
    expected = 50*20*11 if kind in ('rectangle', 'coincident') else math.pi*81*11
    sw.T(doc.Parameter('D1@Sketch1'), 'IDimension').SystemValue = value
    doc.EditRebuild3()
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
    path = tmp_path / 'origin_edited.SLDPRT'; sw.save(doc, path); sw.close(doc)
    doc = reopen(sw, path)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
    assert sw.T(doc.Parameter('D1@Sketch1'), 'IDimension').SystemValue == pytest.approx(value)
    if kind != 'horizontal': assert constrained_status(sw, doc) == 3


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
@pytest.mark.parametrize('offset', [False, True])
def test_shared_origin_and_reference_plane_edits(sw, tmp_path, plane, offset):
    part = origin_part(plane, shared=True, offset=offset)
    doc = _check_part(sw, tmp_path, part)
    assert constrained_status(sw, doc) == 3
    sw.T(doc.Parameter('D1@Sketch1'), 'IDimension').SystemValue = .018
    doc.EditRebuild3()
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    if offset:
        before = solidworks._center(sw, doc)
        sw.T(doc.Parameter('D1@Plane1'), 'IDimension').SystemValue = .019
        doc.EditRebuild3()
        from bauyrcad.core.plane import REFERENCE_PLANES
        frame = REFERENCE_PLANES[plane.title() + ' Plane']
        assert solidworks._center(sw, doc) == pytest.approx(before + 6*frame.normal, abs=1e-5)
    expected = math.pi*81*18
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
    path = tmp_path / 'shared_origin.SLDPRT'; sw.save(doc, path); sw.close(doc)
    doc = reopen(sw, path)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert constrained_status(sw, doc) == 3
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
def test_multiple_origin_dependencies(sw, tmp_path, plane):
    part = Part()
    for i in range(3):
        sketch = part.sketch(plane); circle = sketch.sk.add_circle(0, 20-i*5)
        sketch.dim(circle); part.extrude(sketch, 11+i*7)
    doc = _check_part(sw, tmp_path, part)
    for i in range(1, 4): assert constrained_status(sw, doc, f'Sketch{i}') == 3
    sw.T(doc.Parameter('D1@Sketch2'), 'IDimension').SystemValue = .026
    doc.EditRebuild3()
    expected = part.volume() - math.pi*(225-169)*7
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
    path = tmp_path / 'multi_origin.SLDPRT'; sw.save(doc, path); sw.close(doc)
    doc = reopen(sw, path)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
