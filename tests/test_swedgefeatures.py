import pytest
from bauyrcad.api import Part
from bauyrcad.core import swfile,swtree,sldprt


def edge_part(kind='fillet',plane='front',positions=((0,0,7),)):
    p=Part();s=p.sketch(plane);s.rect((0,0),(52,29));p.extrude(s,14)
    pl=p.doc.features[0].plane
    points=[tuple(pl.origin+pl.xdir*x+pl.ydir*y+pl.normal*z) for x,y,z in positions]
    if kind=='fillet':p.fillet(points,r=3)
    else:p.chamfer(points,d=3)
    return p

@pytest.mark.parametrize('kind',['fillet','chamfer'])
def test_native_edges_offline(tmp_path,kind):
    p=edge_part(kind,positions=((0,0,7),(52,29,7)))
    path=tmp_path/'edges.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    assert sldprt.tree_mismatches(path,[f.name for f in p.doc.features],exact=True)==[]
    _,a=swtree.load(path)
    assert len(a.features[-1].values['edges'])==2


def test_unsupported_edge_history_preserves_destination(tmp_path):
    p=edge_part();p.chamfer([(26,29,14)],d=1)
    path=tmp_path/'existing.SLDPRT';path.write_bytes(b'existing')
    with pytest.raises(swfile.SwFileError,match='one sketch'):
        p.export_solidworks_offline(path,require_native=True)
    assert path.read_bytes()==b'existing'
    assert not p.export_solidworks_offline(path)['native']

@pytest.mark.parametrize('case',['variable','asymmetric','reverse','holes'])
def test_unsupported_finishing_intent_is_explicit(tmp_path,case):
    p=Part();s=p.sketch('front');s.rect((0,0),(50,30))
    if case=='holes':s.circle((25,15),d=6)
    p.extrude(s,14,reverse=case=='reverse')
    if case=='asymmetric':p.chamfer([(0,0,7)],d=2,d2=3)
    else:p.fillet([(0,0,-7 if case=='reverse' else 7)],r=2,r2=3 if case=='variable' else None)
    target=tmp_path/'strict.SLDPRT'
    with pytest.raises(swfile.SwFileError):p.export_solidworks_offline(target,require_native=True)
    assert not target.exists()

@pytest.mark.parametrize('kind',['fillet','chamfer'])
def test_surface_owner_references_follow_archive_reindexing(tmp_path,kind):
    from bauyrcad.core import swfeatures
    p=Part();s=p.sketch('front');lines=s.rect((4,3),(61,38))
    s.dim(lines[0],57);s.dim(lines[1],35);p.extrude(s,17)
    if kind=='fillet':p.fillet([(4,3,8.5),(61,38,8.5)],r=2)
    else:p.chamfer([(4,3,8.5),(61,38,8.5)],d=2)
    path=tmp_path/'owners.SLDPRT';p.export_solidworks_offline(path,require_native=True)
    _,a=swtree.load(path);finish=a.features[-1]
    owners=[o for o in swfeatures.objects(finish) if o.name in ('moSurfaceIdRep_c','moFilletSurfIdRep_c')]
    assert len(owners)>=3
    assert all(o.values['_0'].target is finish for o in owners)

def test_low_level_renumber_rejects_finishing_counters_before_mutation():
    from bauyrcad.core.swfeatures import _load
    _,arc=_load('FC_fillet_vertical')
    before=arc.dumps()
    with pytest.raises(swtree.TreeError,match='counter renumbering'):
        swtree.renumber(arc,0,0)
    assert arc.dumps()==before
