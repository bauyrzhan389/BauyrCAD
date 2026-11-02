import pytest
from bauyrcad import solidworks
from bauyrcad.core import geometry
from test_swshell import shell_part
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks, reopen, feature

pytestmark = needs_solidworks


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
@pytest.mark.parametrize('opening', ['top', 'bottom', 'closed'])
@pytest.mark.parametrize('outward', [False, True])
def test_shell_edit_reopen(sw, tmp_path, plane, opening, outward):
    p = shell_part(plane, opening, outward)
    doc = _check_part(sw, tmp_path, p)
    assert feature(sw, doc, 'Shell1').GetTypeName2() == 'Shell'
    sw.T(doc.Parameter('D1@Shell1'), 'IDimension').SystemValue = .003
    doc.EditRebuild3()
    p.doc.features[-1].thickness = 3; p.doc.regenerate()
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(p.volume(), rel=1e-6)
    assert solidworks._center(sw, doc) == pytest.approx(geometry.center_of_mass(p.doc.body), abs=1e-5)
    sw.T(doc.Parameter('D1@Boss-Extrude1'), 'IDimension').SystemValue = .031
    doc.EditRebuild3()
    p.doc.features[1].depth = 31; p.doc.regenerate()
    assert not any(f.error for f in p.doc.features)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(p.volume(), rel=1e-6)
    assert solidworks._center(sw, doc) == pytest.approx(geometry.center_of_mass(p.doc.body), abs=1e-5)
    path = tmp_path / 'edited.SLDPRT'; sw.save(doc, path); sw.close(doc)
    doc = reopen(sw, path)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert sw.T(doc.Parameter('D1@Shell1'), 'IDimension').SystemValue == pytest.approx(.003)
    assert solidworks._volume(sw, doc) == pytest.approx(p.volume(), rel=1e-6)


@pytest.mark.parametrize('opening', ['top', 'bottom', 'closed'])
@pytest.mark.parametrize('outward', [False, True])
def test_polygon_shell(sw, tmp_path, opening, outward):
    p = shell_part('right', opening, outward=outward, polygon=True)
    p.doc.features[0].name = 'Outline'
    p.doc.features[1].name = 'Prism'
    p.doc.features[-1].name = 'Thin walls'
    _check_part(sw, tmp_path, p)
