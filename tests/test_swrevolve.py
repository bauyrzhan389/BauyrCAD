"""M5 offline revolve structure; geometry/rebuild gates are in test_sw_nativerevolve."""
import math
import pytest
from bauyrcad.api import Part
from bauyrcad.core import swtree, mfc, sldprt
from bauyrcad.core.swfeatures import objects


def revolved(plane='front', angle=145, mode='blind', reverse=False, operation='first'):
    p=Part('revolved')
    if operation != 'first':
        sk=p.sketch('front');sk.rect((-35,-10),(35,45));p.extrude(sk,24,midplane=True)
    sk=p.sketch(plane);sk.rect((5,3),(23,32));axis=sk.centerline((0,-4),(0,40))
    fn=p.revolve_cut if operation=='cut' else p.revolve
    fn(sk,angle,axis=axis,midplane=mode=='midplane',reverse=reverse)
    return p


@pytest.mark.parametrize('operation',['first','add','cut'])
@pytest.mark.parametrize('mode,reverse',[('blind',False),('blind',True),('midplane',False)])
def test_revolve_graph_and_axis_roundtrip(tmp_path,operation,mode,reverse):
    p=revolved(mode=mode,reverse=reverse,operation=operation)
    path=tmp_path/'revolve.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    assert sldprt.tree_mismatches(path,[f.name for f in p.doc.features],exact=True)==[]
    _,arc=swtree.load(path)
    assert mfc.load_archive(arc.dumps()).dumps()==arc.dumps()
    sk,rev=arc.features[-2:];ss=sk.values['sketch'].values
    axis=rev.values['axis'].values['entity'].values
    assert axis['id']==swtree._feature_id(sk)
    assert axis['segment'].values['index']==4
    assert sk.values['axis'].values['index']==4
    line=ss['seglists'][0]['segs'][4]
    assert line['_5']==0
    assert ss['seglists'][0]['segs'][3]['arc'] == 2 << 16
    for key,xy in [('p0',(0,-.004)),('p1',(0,.04))]:
        pt=ss['points'][line[key]]
        assert (pt['x'],pt['y'])==pytest.approx(xy)
    assert all(5 not in [s['_0'] for s in c['_0'].values['segments']] for c in sk.values['chains'])
    params=[o.values for o in objects([arc.config.root,arc.resolved.root])
            if o.name in ('moLengthParameter_c','moAngleParameter_c')]
    assert len({p['id'] for p in params})==len(params)
    assert rev.values['end_spec'].values['dim'].values['handle'].values['dim'].values['param'].values['value']==pytest.approx(math.radians(145))
    for key in ('Header2','Contents/Config-0-ModelHeader'):
        header,_=mfc.loads(sldprt.read_sections(path)[key],'model_header',start=1)
        log=next(x['_0'].values for x in header.root['header'].values['logs'].values['items']
                 if x['_0'].values['id']==axis['id'])
        assert log['name']==swtree.name(sk)
        assert all(x['_0'].values['_1']==axis['stamp'] for x in log['items'])


def test_extrusion_after_revolve_keeps_unique_parameter_ids(tmp_path):
    p=revolved(angle=360)
    sk=p.sketch('front');sk.circle((20,15),d=5);p.extrude(sk,30)
    path=tmp_path/'mixed.SLDPRT'
    p.export_solidworks_offline(path,require_native=True)
    _,arc=swtree.load(path)
    ids=[o.values['id'] for o in objects([arc.config.root,arc.resolved.root])
         if o.name in ('moLengthParameter_c','moAngleParameter_c')]
    assert len(ids)==len(set(ids))
    assert sldprt.tree_mismatches(path,[f.name for f in p.doc.features],exact=True)==[]
