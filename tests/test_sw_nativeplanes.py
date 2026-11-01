import numpy as np
import pytest
from bauyrcad import solidworks
from bauyrcad.core import geometry
from test_swreferenceplanes import offset_part
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks, reopen, feature, plane_def

pytestmark=needs_solidworks


def check_planes(sw,doc,p):
    for source in p.doc.features:
        if source.TYPE!='ref_plane':continue
        got=plane_def(sw,doc,feature(sw,doc,source.name))
        assert got['normal']==pytest.approx(source.result_plane.normal,abs=1e-8)
        delta=got['origin']-source.result_plane.origin
        assert abs(np.dot(delta,source.result_plane.normal))<1e-6
        if source.mode=='offset' and source.distance:
            assert abs(got['distance'])==pytest.approx(abs(source.distance))
        specs=[source.base,source.base2] if source.mode=='mid' else [source.base]
        expected=[spec['name'] if spec['type']=='std'
                  else next(f.name for f in p.doc.features if f.id==spec['id']) for spec in specs]
        assert got['refs']==expected


@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('negative',[False,True])
@pytest.mark.parametrize('chained',[False,True])
def test_offset_dependencies_edit_and_reopen(sw,tmp_path,plane,negative,chained):
    p=offset_part(plane,negative,chained)
    doc=_check_part(sw,tmp_path,p)
    check_planes(sw,doc,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    sw.T(doc.Parameter('D1@Plane1'),'IDimension').SystemValue=.023
    doc.EditRebuild3()
    p.doc.features[0].distance=-23 if negative else 23;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    check_planes(sw,doc,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    saved=tmp_path/'plane_edited.SLDPRT';sw.save(doc,saved);sw.close(doc)
    doc=reopen(sw,saved)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    check_planes(sw,doc,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)

@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('kind',['revolve','cut','unused'])
def test_reference_plane_in_mixed_history(sw,tmp_path,plane,kind):
    from bauyrcad.api import Part
    p=Part()
    if kind=='revolve':
        support=p.plane(plane,offset=13)
        sk=p.sketch(support);sk.rect((5,3),(23,32));axis=sk.centerline((0,-4),(0,40))
        p.revolve(sk,145,axis=axis)
    else:
        sk=p.sketch(plane);sk.rect((0,0),(50,30));p.extrude(sk,24,midplane=True)
        support=p.plane(plane,offset=7)
        if kind=='cut':
            sk=p.sketch(support);sk.circle((20,15),d=8);p.cut(sk,through=True,both=True)
    support.name='Offset support'
    doc=_check_part(sw,tmp_path,p)
    check_planes(sw,doc,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    saved=tmp_path/'mixed_planes.SLDPRT';sw.save(doc,saved);sw.close(doc)
    doc=reopen(sw,saved)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    check_planes(sw,doc,p)
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)

@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('kind',['mid','zero'])
@pytest.mark.parametrize('negative',[False,True])
def test_midplane_and_coincident_plane_follow_parent(sw,tmp_path,plane,kind,negative):
    from bauyrcad.api import Part
    p=Part();parent=p.plane(plane,offset=-24 if negative else 24)
    support=p.plane(mid=(plane,parent)) if kind=='mid' else p.plane(parent,offset=0)
    sk=p.sketch(support);sk.rect((3,5),(37,23));p.extrude(sk,11)
    doc=_check_part(sw,tmp_path,p);check_planes(sw,doc,p)
    sw.T(doc.Parameter('D1@Plane1'),'IDimension').SystemValue=.030
    doc.EditRebuild3();parent.distance=-30 if negative else 30;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    check_planes(sw,doc,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    path=tmp_path/'midplane_edited.SLDPRT';sw.save(doc,path);sw.close(doc);doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    check_planes(sw,doc,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
