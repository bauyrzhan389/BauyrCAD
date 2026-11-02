import json
from bauyrcad.api import Part
from bauyrcad.core.features_more import DraftFeature, LinearPatternFeature
from test_swfeatures import sample


def test_check_is_offline_and_preserves_model(monkeypatch, tmp_path):
    from bauyrcad import solidworks
    monkeypatch.setattr(solidworks, 'Session', lambda *a, **k: (_ for _ in ()).throw(AssertionError('COM')))
    p = sample()
    before = json.dumps(p.doc.to_dict(), sort_keys=True)
    body = p.doc.body
    report = p.solidworks_compatibility()
    assert report['ready'] and all(row['status'] == 'ready' for row in report['features'])
    assert json.dumps(p.doc.to_dict(), sort_keys=True) == before
    assert p.doc.body is body
    assert p.export_solidworks_offline(tmp_path / 'part.SLDPRT', require_native=True)['native']


def test_collects_multiple_unknown_features():
    p = sample()
    p.doc.features.extend([DraftFeature(name='Draft A'), LinearPatternFeature(name='Pattern B')])
    report = p.solidworks_compatibility()
    assert not report['ready']
    assert {i['feature'] for i in report['issues']} == {'Draft A', 'Pattern B'}
    assert all(i['suggestion'] for i in report['issues'])
    assert report['features'][0]['status'] == 'not_checked'


def test_history_restriction_is_caught():
    p = sample(); p.shell(1)
    report = p.solidworks_compatibility()
    assert not report['ready']
    assert 'one sketch' in report['issues'][0]['reason']


def test_empty_and_suppressed():
    assert not Part().solidworks_compatibility()['ready']
    p = sample(); p.doc.features[0].suppressed = True
    report = p.solidworks_compatibility()
    assert report['features'][0]['status'] == 'blocked'
