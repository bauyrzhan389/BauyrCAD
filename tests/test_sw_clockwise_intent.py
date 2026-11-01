import math
import pytest

from bauyrcad import solidworks
from test_sw_clockwise import notch_part
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks, reopen

pytestmark = needs_solidworks


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
def test_concave_arc_radius_edit_and_reopen(sw, tmp_path, plane):
    part = notch_part(plane)
    doc = _check_part(sw, tmp_path, part)
    sw.T(doc.Parameter('D1@Sketch1'), 'IDimension').SystemValue = .006
    doc.EditRebuild3()
    expected = (40*22 - math.pi*36/2)*11
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
    path = tmp_path / 'radius_edited.SLDPRT'
    sw.save(doc, path); sw.close(doc)
    doc = reopen(sw, path)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(expected, rel=1e-6)
    assert sw.T(doc.Parameter('D1@Sketch1'), 'IDimension').SystemValue == pytest.approx(.006)
