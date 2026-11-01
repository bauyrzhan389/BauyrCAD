"""SolidWorks is only the judge of M4's offline output; never the writer under test."""
import pytest

from bauyrcad import solidworks
from bauyrcad.core import sldprt, geometry
from sw_support import needs_solidworks, reopen
from test_swfeatures import sample
from bauyrcad.api import Part

pytestmark = needs_solidworks


def test_native_writer_rebuild_edit_and_reopen(sw, tmp_path):
    p = sample()
    output = tmp_path / 'm4_native.SLDPRT'
    report = p.export_solidworks_offline(output, require_native=True)
    assert report['native']
    assert sldprt.tree_mismatches(output, [f.name for f in p.doc.features], exact=True) == []
    doc = reopen(sw, output)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(p.volume(), rel=1e-6)
    dim = sw.T(doc.Parameter('D1@Boss-Extrude1'), 'IDimension')
    dim.SystemValue = .016
    doc.EditRebuild3()
    p.doc.features[1].depth = 16
    p.doc.regenerate()
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert solidworks._volume(sw, doc) == pytest.approx(geometry.volume(p.doc.body), rel=1e-6)
    saved = tmp_path / 'edited.SLDPRT'
    sw.save(doc, saved)
    sw.close(doc)
    doc = reopen(sw, saved)
    assert solidworks._volume(sw, doc) == pytest.approx(geometry.volume(p.doc.body), rel=1e-6)


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
@pytest.mark.parametrize('end,reverse', [('blind',False), ('blind',True), ('midplane',False)])
def test_first_boss_modes(sw, tmp_path, plane, end, reverse):
    p=Part()
    sk=p.sketch(plane); sk.rect((3,5),(51,27))
    p.extrude(sk,14,midplane=end=='midplane',reverse=reverse)
    _check_part(sw,tmp_path,p)


@pytest.mark.parametrize('operation,end,reverse', [
    ('boss','blind',False), ('boss','blind',True), ('boss','midplane',False),
    ('cut','blind',False), ('cut','blind',True), ('cut','midplane',False),
    ('cut','through_all',False), ('cut','through_all',True), ('cut','through_all_both',False),
])
def test_added_feature_modes(sw,tmp_path,operation,end,reverse):
    p=Part()
    sk=p.sketch('front'); sk.rect((0,0),(50,30)); p.extrude(sk,24,midplane=True)
    sk=p.sketch('front'); sk.circle((48,15) if operation=='boss' else (20,15),d=12)
    if operation=='boss':
        p.extrude(sk,18,midplane=end=='midplane',reverse=reverse)
    else:
        p.cut(sk,7,through=end.startswith('through_all'),both=end=='through_all_both',reverse=reverse)
        p.doc.features[-1].end=end
        p.doc.regenerate()
    _check_part(sw,tmp_path,p)


@pytest.mark.parametrize('plane', ['top','right'])
def test_added_boss_on_other_plane(sw,tmp_path,plane):
    p=Part()
    sk=p.sketch('front'); sk.rect((-20,-20),(20,20)); p.extrude(sk,40,midplane=True)
    sk=p.sketch(plane); sk.circle((0,0),d=12); p.extrude(sk,30)
    _check_part(sw,tmp_path,p)


def _check_part(sw,tmp_path,p,measure_step=False,expected_bounds=None):
    def measured_volume(doc):
        if not measure_step:
            return solidworks._volume(sw,doc)
        # Curved intersection trims can change SW's numerical mass integration even
        # for identical geometry. Independently integrate the actual regenerated B-rep.
        from bauyrcad.core import exchange
        step=tmp_path/'measured.step'
        sw.save(doc,step)
        return geometry.accurate_mass_properties(exchange.read(step))[0][0]
    path=tmp_path/'mode.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    assert sldprt.tree_mismatches(path,[f.name for f in p.doc.features],exact=True)==[]
    doc=reopen(sw,path)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    expected_volume=geometry.accurate_mass_properties(p.doc.body)[0][0]
    assert measured_volume(doc)==pytest.approx(expected_volume,rel=1e-6)
    expected=expected_bounds if expected_bounds is not None else [float(x) for v in geometry.bounding_box(p.doc.body) for x in v]
    actual=[x*1000 for x in sw.T(doc,'IPartDoc').GetPartBox(True)]
    assert actual==pytest.approx(expected,abs=1e-4)
    saved = tmp_path / 'm4_saved.SLDPRT'
    sw.save(doc, saved)
    sw.close(doc)
    doc = reopen(sw, saved)
    assert solidworks.rebuild_health(sw, doc) == (True, [])
    assert measured_volume(doc) == pytest.approx(expected_volume, rel=1e-6)
    return doc

@pytest.mark.parametrize('plane', ['front','top','right'])
def test_automatic_annotation_positions_survive_reopen(sw,tmp_path,plane):
    import struct
    import numpy as np
    from bauyrcad.core import swtree
    p=Part();sk=p.sketch(plane)
    edges=sk.rect((100,50),(180,90));sk.dim(edges[0],80);sk.dim(edges[1],40)
    c=sk.circle((140,70),d=12);sk.dim(c,12);p.extrude(sk,10)
    path=tmp_path/'annotations.SLDPRT'
    p.export_solidworks_offline(path,require_native=True)
    _,arc=swtree.load(path)
    items=arc.features[-2].values['sketch'].values['list1'].values['items']
    expected=[struct.unpack_from('<3d',i['_0'].values['_2']) for i in items]
    for iteration in range(2):
        doc=reopen(sw,path)
        feat=sw.T(sw.T(doc,'IPartDoc').FeatureByName('Sketch1'),'IFeature')
        dd=feat.GetFirstDisplayDimension(); positions=[]
        while dd is not None:
            dd=sw.T(dd,'IDisplayDimension')
            pos=sw.T(dd.GetAnnotation(),'IAnnotation').GetPosition()
            # Annotation Z offset is display-only; compare in-plane model coordinates.
            pl=p.doc.features[0].plane
            positions.append((np.dot(pos,pl.xdir),np.dot(pos,pl.ydir)))
            dd=feat.GetNextDisplayDimension(dd)
        assert len(positions)==3
        for actual,want in zip(positions,expected):
            assert actual==pytest.approx(want[:2],abs=1e-8)
        assert solidworks.rebuild_health(sw,doc)==(True,[])
        assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
        path=tmp_path/'annotations_saved.SLDPRT'
        if iteration==0: sw.save(doc,path)
        sw.close(doc)
