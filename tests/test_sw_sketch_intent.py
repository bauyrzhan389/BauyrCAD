from collections import Counter
import pytest
from bauyrcad import solidworks
from bauyrcad.core import geometry
from bauyrcad.core.swsketch import relation_code
from test_swsketch import intent_part, BUILDERS
from test_sw_nativefeatures import _check_part
from sw_support import needs_solidworks,reopen

pytestmark=needs_solidworks


def assert_intent(sw,doc,p):
    feature=sw.T(sw.T(doc,'IPartDoc').FeatureByName('Sketch1'),'IFeature')
    sketch=sw.T(feature.GetSpecificFeature2(),'ISketch')
    manager=sw.T(sketch.RelationManager,'ISketchRelationManager')
    actual=Counter(int(sw.T(r,'ISketchRelation').GetRelationType()) for r in manager.GetRelations(0) or ())
    source=p.doc.features[0].sketch
    expected=Counter(relation_code(c) if c.value is None else {'distance':1,'diameter':15,'radius':3}[c.type] for c in source.constraints)
    expected[17]+=2*sum(c.type=='fix' and c.refs[0][0]=='e' and source.entities[c.refs[0][1]].kind in ('line','arc') for c in source.constraints)
    assert actual==expected
    assert sum(bool(sw.T(seg,'ISketchSegment').ConstructionGeometry) for seg in sketch.GetSketchSegments() or ())==sum(e.construction for e in source.entities.values())
    assert (sketch.GetUserPointsCount() or 0)==sum(e.kind=='point' for e in source.entities.values())


@pytest.mark.parametrize('kind',BUILDERS)
@pytest.mark.parametrize('plane',['front','top','right'])
def test_builder_geometry_and_native_intent(sw,tmp_path,kind,plane):
    p=intent_part(kind,plane,dimension=True)
    doc=_check_part(sw,tmp_path,p)
    assert solidworks._center(sw,doc)==pytest.approx(geometry.center_of_mass(p.doc.body),abs=1e-5)
    assert_intent(sw,doc,p)

    sw.T(doc.Parameter('D1@Boss-Extrude1'),'IDimension').SystemValue=.017
    doc.EditRebuild3()
    p.doc.features[1].depth=17;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
    path=tmp_path/'edited.SLDPRT';sw.save(doc,path);sw.close(doc)
    doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert_intent(sw,doc,p)


@pytest.mark.parametrize('kind',['radius_arc','radius_circle','radius_slot'])
def test_edit_radius_and_reopen(sw,tmp_path,kind):
    import math
    p=intent_part(kind)
    doc=_check_part(sw,tmp_path,p)
    assert_intent(sw,doc,p)

    dimension='D2' if kind=='radius_slot' else 'D1'
    radius=6 if kind=='radius_slot' else 12
    sw.T(doc.Parameter(dimension+'@Sketch1'),'IDimension').SystemValue=radius/1000
    doc.EditRebuild3()
    area=(2*radius*math.hypot(34,11)+math.pi*radius**2 if kind=='radius_slot'
          else math.pi*radius**2*(.5 if kind=='radius_arc' else 1))
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(area*11,rel=1e-6)
    path=tmp_path/'radius_edited.SLDPRT';sw.save(doc,path);sw.close(doc)
    doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(area*11,rel=1e-6)
    assert_intent(sw,doc,p)


@pytest.mark.parametrize('kind',['polygon','center_rect'])
def test_edit_builder_driver(sw,tmp_path,kind):
    import math
    p=intent_part(kind,dimension=True)
    doc=_check_part(sw,tmp_path,p)
    size=50
    sw.T(doc.Parameter('D1@Sketch1'),'IDimension').SystemValue=size/1000
    doc.EditRebuild3()
    if kind=='polygon':
        area=5/2*(size/2)**2*math.sin(2*math.pi/5)
    else:
        area=size*22
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(area*11,rel=1e-6)
    path=tmp_path/'builder_edited.SLDPRT';sw.save(doc,path);sw.close(doc)
    doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(area*11,rel=1e-6)
    assert_intent(sw,doc,p)


@pytest.mark.parametrize('finish',['fillet','chamfer','face_cut'])
def test_construction_intent_survives_later_features(sw,tmp_path,finish):
    p=intent_part('center_rect',dimension=True)
    if finish=='face_cut':
        sketch=p.sketch(on=p.face('+z'))
        sketch.circle((12,17),d=5)
        p.cut(sketch,through=True)
    elif finish=='fillet':
        p.fillet([(-8,6,5.5)],r=2)
    else:
        p.chamfer([(-8,6,5.5)],d=2)
    doc=_check_part(sw,tmp_path,p)
    assert_intent(sw,doc,p)


@pytest.mark.parametrize('kind',['flag','line','circle','arc'])
def test_fixed_geometry_is_native_and_persistent(sw,tmp_path,kind):
    from bauyrcad.api import Part
    p=Part();s=p.sketch('front')
    if kind=='flag':
        lines=s.rect((3,5),(43,27));s.sk.points[lines[0].p1].fixed=True
        s.dim(lines[0]);s.dim(lines[1])
    elif kind=='line':
        lines=s.rect((3,5),(43,27));s.relate('fix',lines[0]);s.dim(lines[1])
    elif kind=='circle':
        c=s.circle((12,17),r=10);s.relate('fix',c)
    else:
        a=s.arc((12,17),(12,7),(12,27));s.line(a.end,a.start);s.relate('fix',a)
    p.extrude(s,11)
    doc=_check_part(sw,tmp_path,p)
    feature=sw.T(sw.T(doc,'IPartDoc').FeatureByName('Sketch1'),'IFeature')
    sketch=sw.T(feature.GetSpecificFeature2(),'ISketch')
    manager=sw.T(sketch.RelationManager,'ISketchRelationManager')
    assert sum(sw.T(r,'ISketchRelation').GetRelationType()==17 for r in manager.GetRelations(0))==(3 if kind in ('line','arc') else 1)
    assert sketch.GetConstrainedStatus()==3
