import pytest
from bauyrcad import solidworks
from bauyrcad.api import Part
from test_swsharedsketches import shared_part
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks,reopen,user_api_tree

pytestmark=needs_solidworks


@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('kind',['extrude','revolve'])
def test_shared_profile_edit_updates_both_bodies(sw,tmp_path,plane,kind):
    p=shared_part(plane,kind);doc=_check_part(sw,tmp_path,p)
    expected=[f.name for f in p.doc.features]
    assert [n for n,_,_ in user_api_tree(sw,doc)]==expected
    sw.T(doc.Parameter('D1@Sketch1'),'IDimension').SystemValue=.05
    doc.EditRebuild3()
    sketch=p.doc.features[0].sketch
    next(c for c in sketch.constraints if c.type=='distance').value=50
    for point in sketch.points.values():
        if abs(point.x-45)<1e-8:
            point.x=55
    p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
    path=tmp_path/'shared_edited.SLDPRT';sw.save(doc,path);sw.close(doc)
    doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert [n for n,_,_ in user_api_tree(sw,doc)]==expected
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)


def test_renamed_sketch_shared_by_three_bosses(sw,tmp_path):
    p=shared_part();sk=p.doc.features[0]
    from bauyrcad.api import Sketch
    p.extrude(Sketch(p,sk),15)
    sk.name='Shared rectangle'
    doc=_check_part(sw,tmp_path,p)
    assert [n for n,_,_ in user_api_tree(sw,doc)]==[f.name for f in p.doc.features]


def test_sketch_created_before_another_profile(sw,tmp_path):
    p=Part();first=p.sketch('front');first.rect((3,5),(43,27))
    second=p.sketch('top');second.circle((20,-5),d=8)
    p.extrude(first,11);p.extrude(second,20)
    doc=_check_part(sw,tmp_path,p)
    assert [n for n,_,_ in user_api_tree(sw,doc)]==[f.name for f in p.doc.features]


def test_shared_sketch_on_offset_plane(sw,tmp_path):
    p=Part();plane=p.plane('right',offset=13)
    sk=p.sketch(plane);sk.rect((3,5),(43,27));p.extrude(sk,11);p.extrude(sk,7,reverse=True)
    doc=_check_part(sw,tmp_path,p)
    sw.T(doc.Parameter('D1@Plane1'),'IDimension').SystemValue=.019
    doc.EditRebuild3();plane.distance=19;p.doc.regenerate()
    from bauyrcad.core import geometry
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
