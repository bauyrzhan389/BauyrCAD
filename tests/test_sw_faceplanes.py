import pytest
from bauyrcad import solidworks
from bauyrcad.core import geometry
from test_swfaceplanes import side_part
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks,reopen

pytestmark=needs_solidworks

@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('triangle',[False,True])
def test_face_plane_rebuild_and_reopen(sw,tmp_path,plane,triangle):
    p=side_part(plane,triangle)
    doc=_check_part(sw,tmp_path,p)
    if not triangle:
        dim=sw.T(doc.Parameter('D1@Sketch1'),'IDimension')
        dim.SystemValue=.05;doc.EditRebuild3()
        assert solidworks.rebuild_health(sw,doc)==(True,[])
        assert solidworks._volume(sw,doc)==pytest.approx(10080,rel=1e-6)
        bounds=[x*1000 for x in sw.T(doc,'IPartDoc').GetPartBox(True)]
        pl=p.doc.features[0].plane
        axis=max(range(3),key=lambda i:abs(pl.xdir[i]))
        assert bounds[axis+3 if pl.xdir[axis]>0 else axis]==pytest.approx(55*pl.xdir[axis],abs=1e-4)
        path=tmp_path/'changed.SLDPRT';sw.save(doc,path);sw.close(doc)
        doc=reopen(sw,path)
        assert solidworks.rebuild_health(sw,doc)==(True,[])
        assert solidworks._volume(sw,doc)==pytest.approx(10080,rel=1e-6)


@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('side',['top','bottom'])
@pytest.mark.parametrize('triangle',[False,True])
def test_cap_attachment_follows_depth(sw,tmp_path,plane,side,triangle):
    from test_swfaceplanes import cap_part
    p=cap_part(plane,side,triangle)
    doc=_check_part(sw,tmp_path,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    sw.T(doc.Parameter('D1@Boss-Extrude1'),'IDimension').SystemValue=.019
    doc.EditRebuild3()
    p.doc.features[1].depth=19;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
    expected=[float(x) for v in geometry.bounding_box(p.doc.body) for x in v]
    actual=[x*1000 for x in sw.T(doc,'IPartDoc').GetPartBox(True)]
    assert actual==pytest.approx(expected,abs=1e-4)
    path=tmp_path/'edited_cap.SLDPRT';sw.save(doc,path);sw.close(doc)
    doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)


@pytest.mark.parametrize('base_mode',['blind','reverse','midplane'])
@pytest.mark.parametrize('side',['top','bottom','side'])
@pytest.mark.parametrize('operation',['boss_blind','boss_midplane','cut_blind','cut_through','cut_both'])
@pytest.mark.parametrize('plane',['front','top','right'])
def test_face_extrusion_modes(sw,tmp_path,base_mode,side,operation,plane):
    from test_swfaceplanes import face_mode_part
    p=face_mode_part(base_mode,side,operation,plane)
    doc=_check_part(sw,tmp_path,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    sw.T(doc.Parameter('D1@Boss-Extrude1'),'IDimension').SystemValue=.018
    doc.EditRebuild3()
    p.doc.features[1].depth=18;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
    expected=[float(x) for v in geometry.bounding_box(p.doc.body) for x in v]
    assert [x*1000 for x in sw.T(doc,'IPartDoc').GetPartBox(True)]==pytest.approx(expected,abs=1e-4)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    saved=tmp_path/'edited_mode.SLDPRT';sw.save(doc,saved);sw.close(doc)
    doc=reopen(sw,saved)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
