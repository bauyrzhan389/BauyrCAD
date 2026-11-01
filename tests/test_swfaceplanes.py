import numpy as np
import pytest
from bauyrcad.api import Part
from bauyrcad.core import swtree,swfile


def side_part(plane='front',triangle=False):
    p=Part();s=p.sketch(plane)
    if triangle:
        s.polyline([(-30,25),(40,5),(-28,-30)], close=True)
    else:
        edges=s.rect((0,0),(40,20));s.dim(edges[0],40)
    p.extrude(s,10)
    base=p.doc.features[0].plane
    if triangle:
        # Sloping side between the second and third vertices.
        point=base.xdir*6+base.ydir*(-12.5)+base.normal*5
        face=p.face(at=tuple(point))
    else:
        face=p.face(at=tuple(base.xdir*40+base.ydir*10+base.normal*5))
    s=p.sketch(on=face);pl=s.feature.plane
    delta=face.centroid-pl.origin
    u,v=np.dot(delta,pl.xdir),np.dot(delta,pl.ydir)
    s.rect((u-2,v-2),(u+2,v+2));p.extrude(s,5)
    return p


@pytest.mark.parametrize('kind', ['side', 'top', 'bottom'])
def test_side_donor_roundtrip(kind):
    from bauyrcad.core.swfeatures import _load
    c,a=_load(f'FP_{kind}')
    assert all(c.get(k)==v for k,v in a.dumps().items())


@pytest.mark.parametrize('plane',['front','top','right'])
def test_side_offline(tmp_path,plane):
    p=side_part(plane);out=tmp_path/'face.SLDPRT'
    assert p.export_solidworks_offline(out,require_native=True)['native']
    _,a=swtree.load(out)
    sk=a.features[-2]
    assert sk.values['plane'].values['inline']['_0'].values['data'].name=='moFaceRefPlnData_c'
    assert sk.values['cache']==[]


def test_unsupported_face_history_preserves_destination(tmp_path):
    p=Part();s=p.sketch('front');s.rect((0,0),(40,20));p.extrude(s,10)
    s=p.sketch(on=p.face('+z'));s.rect((5,5),(10,10));p.extrude(s,5)
    s=p.sketch(on=p.face('+z'));s.rect((6,6),(8,8));p.extrude(s,2)
    out=tmp_path/'keep.SLDPRT';out.write_bytes(b'keep')
    with pytest.raises(swfile.SwFileError,match='single base extrusion'):
        p.export_solidworks_offline(out,require_native=True)
    assert out.read_bytes()==b'keep'


def cap_part(plane='front', side='top', triangle=False):
    p=Part();s=p.sketch(plane)
    if triangle:
        s.polyline([(-30,25),(40,5),(-28,-30)],close=True)
    else:
        s.rect((-7,3),(47,29))
    p.extrude(s,13)
    normal=p.doc.features[0].plane.normal*(1 if side=='top' else -1)
    face=p.face(tuple(normal));s=p.sketch(on=face);pl=s.feature.plane
    delta=face.centroid-pl.origin
    u,v=np.dot(delta,pl.xdir),np.dot(delta,pl.ydir)
    edges=s.rect((u-2,v-3),(u+2,v+3));s.dim(edges[0],4)
    p.extrude(s,6)
    return p


@pytest.mark.parametrize('side',['top','bottom'])
@pytest.mark.parametrize('triangle',[False,True])
def test_cap_offline(tmp_path,side,triangle):
    p=cap_part(side=side,triangle=triangle);path=tmp_path/'cap.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    _,a=swtree.load(path)
    face=a.features[-2].values['plane'].values['inline']['_0'].values['data'].values['face'].values['face'].values
    assert face['n']==(5 if triangle else 6)
    assert face['ids'][0]['_0'].values['_2']==(1 if side=='top' else 0)


def face_mode_part(base_mode='blind', side='top', operation='boss_blind', plane='front'):
    p=Part();s=p.sketch(plane);s.rect((-3,2),(43,28))
    p.extrude(s,14,reverse=base_mode=='reverse',midplane=base_mode=='midplane')
    base=p.doc.features[0].plane
    normal=base.xdir if side=='side' else base.normal*(1 if side=='top' else -1)
    face=p.face(tuple(normal));s=p.sketch(on=face);pl=s.feature.plane
    d=face.centroid-pl.origin;u,v=np.dot(d,pl.xdir),np.dot(d,pl.ydir)
    lines=s.rect((u-2,v-3),(u+2,v+3));s.dim(lines[0],4)
    if operation=='boss_blind':p.extrude(s,6)
    elif operation=='boss_midplane':p.extrude(s,6,midplane=True)
    else:p.cut(s,3 if operation=='cut_blind' else None,through=operation!='cut_blind',both=operation=='cut_both')
    return p
