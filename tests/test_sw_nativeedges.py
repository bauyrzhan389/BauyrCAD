import pytest
from bauyrcad import solidworks
from bauyrcad.core import geometry
from sw_support import needs_solidworks,reopen
from test_swedgefeatures import edge_part
from test_sw_nativefeatures import _check_part

pytestmark=needs_solidworks

@pytest.mark.parametrize('kind',['fillet','chamfer'])
@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('positions',[
    ((0,0,7),),((52,14.5,14),),((26,29,0),),((0,0,7),(52,29,7)),
])
def test_native_finishing_edges(sw,tmp_path,kind,plane,positions):
    p=edge_part(kind,plane,positions)
    doc=_check_part(sw,tmp_path,p)
    expected=geometry.accurate_mass_properties(p.doc.body)[0][1]
    assert solidworks._center(sw,doc)==pytest.approx(expected,abs=1e-5)

@pytest.mark.parametrize('kind',['fillet','chamfer'])
def test_edit_finishing_size_and_depth(sw,tmp_path,kind):
    p=edge_part(kind,positions=((0,0,7),(52,29,7)))
    path=tmp_path/'editable.SLDPRT';p.export_solidworks_offline(path,require_native=True)
    doc=reopen(sw,path)
    sw.T(doc.Parameter('D1@'+p.doc.features[-1].name),'IDimension').SystemValue=.004
    sw.T(doc.Parameter('D1@Boss-Extrude1'),'IDimension').SystemValue=.018
    doc.EditRebuild3()
    setattr(p.doc.features[-1],'radius' if kind=='fillet' else 'distance',4)
    p.doc.features[1].depth=18;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    expected=geometry.accurate_mass_properties(p.doc.body)[0][0]
    assert solidworks._volume(sw,doc)==pytest.approx(expected,rel=1e-6)
    sw.save(doc,path);sw.close(doc);doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(expected,rel=1e-6)

@pytest.mark.parametrize('kind',['fillet','chamfer'])
@pytest.mark.parametrize('selection',['polygon','face','mixed'])
def test_polygon_face_and_mixed_edges(sw,tmp_path,kind,selection):
    from bauyrcad.api import Part
    p=Part();s=p.sketch('front')
    if selection=='polygon':
        s.polyline([(4,3),(61,3),(53,31),(12,38)],close=True)
        p.extrude(s,17)
        edges=[(53,31,8.5)]
    else:
        lines=s.rect((4,3),(61,38));s.dim(lines[0],57);s.dim(lines[1],35)
        p.extrude(s,17)
        edges=[] if selection=='face' else [(4,3,8.5),(32.5,38,17)]
    kw={'faces':[p.face('+z')]} if selection=='face' else {'edges':edges}
    if kind=='fillet':p.fillet(r=2,**kw)
    else:p.chamfer(d=2,**kw)
    p.doc.features[0].name='Profile';p.doc.features[1].name='Blank';p.doc.features[2].name='Finish'
    # The four intersecting fillets give SW's mass integrator a different answer
    # for identical trims. Offline and independent COM-native STEP integrations agree.
    step_mass = selection=='face' and kind=='fillet'
    doc=_check_part(sw,tmp_path,p,measure_step=step_mass)
    expected=geometry.accurate_mass_properties(p.doc.body)[0][1]
    if step_mass:
        from bauyrcad.core import exchange
        center=geometry.accurate_mass_properties(exchange.read(tmp_path/'measured.step'))[0][1]
    else:
        center=solidworks._center(sw,doc)
    assert center==pytest.approx(expected,abs=1e-5)


@pytest.mark.parametrize('kind',['fillet','chamfer'])
@pytest.mark.parametrize('points,edge',[
    ([(0,0),(50,0),(20,40)],(20,40,7)),
    ([(0,0),(50,0),(50,20),(25,20),(25,40),(0,40)],(25,20,7)),
])
def test_nonquadrilateral_profiles(sw,tmp_path,kind,points,edge):
    from bauyrcad.api import Part
    p=Part();s=p.sketch('front');s.polyline(points,close=True);p.extrude(s,14)
    if kind=='fillet':p.fillet([edge],r=2)
    else:p.chamfer([edge],d=2)
    doc=_check_part(sw,tmp_path,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.accurate_mass_properties(p.doc.body)[0][1],abs=1e-5)

def test_com_equal_chamfer_on_oblique_corner(sw,tmp_path):
    from bauyrcad.api import Part
    p=Part();s=p.sketch('front');s.polyline([(4,3),(61,3),(53,31),(12,38)],close=True)
    p.extrude(s,14);p.chamfer([(53,31,7)],d=2);p._ensure_built()
    path=tmp_path/'com_equal.SLDPRT'
    solidworks.export_part(sw,p.doc,path,require_native=True)
    doc=reopen(sw,path)
    volume,center=geometry.accurate_mass_properties(p.doc.body)[0]
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(volume,rel=1e-6)
    assert solidworks._center(sw,doc)==pytest.approx(center,abs=1e-5)
