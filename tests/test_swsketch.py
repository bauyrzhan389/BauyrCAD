import pytest
from bauyrcad.api import Part
from bauyrcad.core import swtree,swfile
from bauyrcad.core.sketch import builders
from pathlib import Path
from bauyrcad.core.sketch import ops

BUILDERS = ['slot', 'center_rect', 'three_rect', 'para', 'polygon', 'concentric',
            'center_slot', 'three_center_rect', 'circumscribed', 'perimeter_circle',
            'three_arc', 'center_arc', 'sketch_fillet', 'sketch_chamfer',
            'equal_circles', 'point_on_line', 'horizontal_points', 'vertical_points', 'symmetric', 'collinear',
            'fixed_point', 'fixed_line']


@pytest.mark.parametrize('path',sorted((Path(__file__).parents[1]/'bauyrcad/data/sw_seeds/native').glob('SI_*.SLDPRT')))
def test_sketch_reference_roundtrip(path):
    container,archive=swtree.load(path)
    assert all(container.get(key)==value for key,value in archive.dumps().items())


def intent_part(kind,plane='front',dimension=False):
    p=Part();s=p.sketch(plane)
    if kind=='slot':s.slot((3,5),(37,16),9)
    elif kind=='radius_slot':
        entities=s.slot((3,5),(37,16),9)
        s.dim(entities[0]);s.dim(entities[1],4.5)
    elif kind=='radius_arc':
        a=s.arc((12,17),(12,7),(12,27));line=s.line(a.end,a.start)
        s.relate('midpoint',('p',a.center),line);s.dim(a,10)
    elif kind=='radius_circle':
        a=s.circle((12,17),r=10);s.dim(a,10,type='radius')
    elif kind=='center_rect':s.center_rect((12,17),40,22)
    elif kind=='three_rect':builders.three_point_rectangle(s.sk,(3,4),(35,16),(29,32))
    elif kind=='para':builders.parallelogram(s.sk,(3,4),(35,16),(44,37))
    elif kind=='polygon':s.polygon((12,17),20,sides=5,angle=17)
    elif kind=='concentric':
        a=s.circle((12,17),d=40);b=s.circle((12,17),d=12);s.relate('concentric',a,b)
    elif kind=='center_slot':builders.center_slot(s.sk,(12,17),(29,22.5),(12,23))
    elif kind=='three_center_rect':builders.three_point_center_rectangle(s.sk,(12,17),(32,25),(21,39))
    elif kind=='circumscribed':builders.polygon(s.sk,(12,17),(31,21),6,inscribed=False)
    elif kind=='perimeter_circle':builders.perimeter_circle(s.sk,(3,7),(27,9),(12,31))
    elif kind in ('three_arc','center_arc'):
        if kind=='three_arc':entities=builders.three_point_arc(s.sk,(12,7),(12,27),(22,17))
        else:entities=builders.center_arc(s.sk,(12,17),(12,7),(12,27))
        arc=entities[0];s.line(arc.end,arc.start)
    elif kind in ('sketch_fillet','sketch_chamfer'):
        lines=s.rect((3,5),(43,27))
        if kind=='sketch_fillet':ops.fillet(s.sk,lines[0].p2,4)
        else:ops.chamfer(s.sk,lines[0].p2,4,6)
    elif kind=='equal_circles':
        s.rect((-30,-20),(30,20));a=s.circle((-10,0),d=8);b=s.circle((10,0),d=8)
        s.relate('equal',a,b)
    elif kind=='point_on_line':
        lines=s.rect((3,5),(43,27));point=s.point((23,5))
        s.relate('coincident',('p',point.point_ids()[0]),lines[0])
    elif kind in ('horizontal_points','vertical_points'):
        s.rect((3,5),(43,27));a=s.point((12,14));b=s.point((29,14) if kind=='horizontal_points' else (12,22))
        s.relate(kind.split('_')[0],('p',a.point_ids()[0]),('p',b.point_ids()[0]))
    elif kind=='symmetric':
        s.rect((3,5),(43,27));axis=s.centerline((17,8),(17,24));a=s.point((12,20));b=s.point((22,20))
        s.relate('symmetric',('p',a.point_ids()[0]),('p',b.point_ids()[0]),axis)
    elif kind=='collinear':
        s.rect((3,5),(43,27));a=s.sk.add_line((6,7),(12,11),construction=True);b=s.sk.add_line((18,15),(30,23),construction=True)
        s.relate('collinear',a,b)
    elif kind=='fixed_point':
        lines=s.rect((3,5),(43,27));s.relate('fix',('p',lines[0].p1))
    elif kind=='fixed_line':
        lines=s.rect((3,5),(43,27));s.sk.constraints.clear();s.relate('fix',lines[0])
    if dimension:
        if kind in ('polygon','circumscribed'):e=next(e for e in s.sk.entities.values() if e.kind=='circle')
        elif kind=='fixed_line':e=lines[1]
        else:e=next(e for e in s.sk.entities.values() if e.kind in ('line','circle') and not e.construction)
        s.dim(e)
    p.extrude(s,11)
    return p


@pytest.mark.parametrize('kind',BUILDERS)
def test_builder_intent_is_written(tmp_path,kind):
    from collections import Counter
    from bauyrcad.core.swsketch import relation_code
    p=intent_part(kind,dimension=True);path=tmp_path/'intent.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    _,a=swtree.load(path);sk=a.features[-2].values['sketch'].values
    got=Counter(r['type'] for i in range(1,9) for r in sk['rel'+str(i)])
    want=Counter(relation_code(c) for c in p.doc.features[0].sketch.constraints if c.value is None)
    if kind=='fixed_line':want[17]+=2
    assert got==want
    assert len(sk['linear_dims'])+len(sk['radial_dims'])==sum(c.value is not None for c in p.doc.features[0].sketch.constraints)


def test_fixed_point_flag_becomes_native_fix(tmp_path):
    p=Part();s=p.sketch('front');lines=s.rect((0,0),(40,20));p.extrude(s,10)
    s.sk.points[lines[0].p1].fixed=True
    path=tmp_path/'fixed.SLDPRT'
    p.export_solidworks_offline(path,require_native=True)
    _,archive=swtree.load(path)
    relations=archive.features[-2].values['sketch'].values['rel1']
    assert sum(r['type']==17 for r in relations)==1


def test_explicit_origin_anchor_is_preserved(tmp_path):
    p=Part();s=p.sketch('front')
    a=s.sk.add_line(0,(40,0));b=s.sk.add_line(a.p2,(0,20));s.sk.add_line(b.p2,0)
    p.extrude(s,10)
    path=tmp_path/'origin.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    _, archive = swtree.load(path)
    data = archive.features[-2].values['sketch'].values
    assert sum(point['external'] is not None for point in data['points']) == 1
    assert any(relation['type'] == 9 for relation in data['rel2'])


def test_equations_are_not_silently_baked_into_values(tmp_path):
    p=intent_part('center_rect')
    p.parameter('depth',11)
    p.bind_parameter(p.doc.features[-1],'depth','depth')
    path=tmp_path/'equation.SLDPRT';path.write_bytes(b'keep')
    with pytest.raises(swfile.SwFileError,match='equations'):
        p.export_solidworks_offline(path,require_native=True)
    assert path.read_bytes()==b'keep'
