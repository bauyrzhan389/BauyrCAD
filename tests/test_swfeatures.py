"""Offline writer integration: native trees, identities, explicit whole-part fallback."""
import json
from pathlib import Path

import pytest

from bauyrcad.api import Part
from bauyrcad.core import mfc, sldprt, swtree, swfile
from bauyrcad.core.swfeatures import SEEDS


def sample():
    p = Part('m4')
    sk = p.sketch('front')
    lines = sk.rect((0, 0), (50, 30))
    sk.dim(lines[0], 50)
    p.extrude(sk, 12)
    sk = p.sketch('front'); sk.circle((45, 15), d=20)
    p.extrude(sk, 25)
    sk = p.sketch('front'); sk.circle((10, 10), d=8)
    p.cut(sk, 12)
    return p


def test_offline_writer_exports_a_complete_tree(tmp_path):
    p = sample()
    path = tmp_path / 'native.SLDPRT'
    report = p.export_solidworks_offline(path, require_native=True)
    assert report['native'] and not report['warnings']
    assert sldprt.tree_mismatches(path, [f.name for f in p.doc.features], exact=True) == []
    _, arc = swtree.load(path)
    params = [o.values for o in arc.table if isinstance(o, mfc.Obj) and o.name == 'moLengthParameter_c']
    assert len({p['id'] for p in params}) == len(params)
    assert sorted(p['value'] for p in params) == pytest.approx([.012, .012, .025, .050])
    body_ids = {swtree._feature_id(f) for f in arc.features if 'counters' in f.values}
    for f in arc.features:
        if 'merge' in f.values:
            assert f.values['merge'].values['id'] in body_ids
    assert not set(swtree.REGENERATED) & set(sldprt.read_sections(path))


def test_custom_names_and_four_bosses(tmp_path):
    p = Part()
    for i in range(4):
        sk = p.sketch('front'); sk.circle((i*5, 0), d=20)
        p.extrude(sk, 10+i)
    for i, f in enumerate(p.doc.features):
        f.name = f'Custom {i}'
    path = tmp_path / 'many.SLDPRT'
    assert p.export_solidworks_offline(path, require_native=True)['native']
    assert sldprt.tree_mismatches(path, [f.name for f in p.doc.features], exact=True) == []


def test_unsupported_operation_falls_back_or_leaves_existing_file_untouched(tmp_path):
    p = Part()
    sk = p.sketch('front'); lines = sk.rect((0, 0), (5, 10))
    p.revolve(sk, axis=lines[3])  # ordinary profile-line axes are not supported offline yet
    path = tmp_path / 'fallback.SLDPRT'
    report = p.export_solidworks_offline(path)
    assert not report['native'] and report['warnings']
    assert sldprt.feature_tree(path)[0].type_name == 'BaseBody'
    before = path.read_bytes()
    with pytest.raises(swfile.SwFileError):
        p.export_solidworks_offline(path, require_native=True)
    assert path.read_bytes() == before


def test_cli_reports_native_result(tmp_path, capsys):
    from bauyrcad.cli import main
    p = sample()
    source = tmp_path / 'part.bcad'
    p.save(source)
    target = tmp_path / 'part.SLDPRT'
    assert main(['export', str(source), str(target), '--features', '--require-native']) == 0
    assert json.loads(capsys.readouterr().out)['native']


@pytest.mark.parametrize('seed', sorted(SEEDS.glob('*.SLDPRT')), ids=lambda p:p.stem)
def test_native_mode_donors_round_trip(seed):
    sections = sldprt.read_sections(seed)
    arc = mfc.load_archive(sections)
    assert all(sections[k] == v for k,v in arc.dumps().items())


def test_body_unit_component_is_not_replaced_by_part_units(tmp_path):
    p=sample()
    path=tmp_path/'components.SLDPRT'
    p.export_solidworks_offline(path,require_native=True)
    _,arc=swtree.load(path)
    part_units=arc.config.root['part'].values['unit_component']
    merges=[f.values['merge'] for f in arc.features if 'merge' in f.values]
    assert merges
    _,donor=swtree.load(SEEDS/'M4_add_blind_0.SLDPRT')
    expected=donor.features[-1].values['merge'].values['component'].values
    assert merges[0].values['component'].values == expected
    assert all(m.values['component'] is not part_units for m in merges)

@pytest.mark.parametrize('seed', sorted(SEEDS.glob('*.SLDPRT')), ids=lambda p:p.stem)
def test_body_donor_model_headers_round_trip(seed):
    if seed.stem=='M4_empty':
        return  # writer initializes this header from a first-body donor
    sections=sldprt.read_sections(seed)
    for key in ('Header2','Contents/Config-0-ModelHeader'):
        stream,_=mfc.loads(sections[key],'model_header',start=1)
        assert mfc.dumps(stream)[0]==sections[key]


@pytest.mark.parametrize('both',[False,True])
def test_through_all_does_not_require_an_unused_depth(tmp_path,both):
    p=Part();s=p.sketch('front');s.rect((0,0),(40,20));p.extrude(s,12)
    s=p.sketch('front');s.circle((10,10),d=4);p.cut(s,through=True,both=both)
    assert p.doc.features[-1].depth==0
    assert p.export_solidworks_offline(tmp_path/'through.SLDPRT',require_native=True)['native']
