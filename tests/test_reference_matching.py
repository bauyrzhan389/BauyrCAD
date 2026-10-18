from types import SimpleNamespace
import numpy as np
from bauyrcad.core.refs import EdgeRef
from bauyrcad.fea.study import FaceRef
from bauyrcad.api import Part


def edge(index=0, **kw):
    return dict(index=index, kind='line', mid=np.array([0., 0., 0.]), length=10.,
                direction=np.array([1., 0., 0.]), **kw)


def face(index=0, x=0, kind='plane', normal=(0, 0, 1)):
    return SimpleNamespace(index=index, centroid=np.array([x, 0., 0.]), area=100.,
                           kind=kind, normal=np.array(normal), radius=None)


def test_edge_exact_matches_require_compatible_geometry_and_unique_candidate():
    original = edge()
    ref = EdgeRef.from_info(original)
    wrong = {**original, 'kind': 'circle'}
    assert ref.resolve([wrong]) is None
    assert ref.resolve([{**original, 'direction': np.array([0., 1., 0.])}]) is None
    assert ref.match([original, {**original, 'index': 1}])['status'] == 'ambiguous'
    assert ref.resolve([{**original, 'direction': -original['direction']}]) == 0
    near = {**original, 'index': 2, 'mid': np.array([0., 1., 0.])}
    almost = {**near, 'index': 3, 'mid': np.array([0., 1.1, 0.])}
    assert ref.match([near, almost])['status'] == 'ambiguous'
    assert ref.match([near])['status'] == 'moved'
    assert ref.resolve([{**near, 'mid': np.array([0., 100., 0.])}]) is None


def test_face_exact_and_fallback_are_bounded_and_ambiguity_safe():
    ref = FaceRef.from_face(face())
    assert ref.resolve([face(kind='cylinder')]) is None
    assert ref.resolve([face(normal=(0, 0, -1))]) is None
    assert ref.match([face(), face(1)])['status'] == 'ambiguous'
    assert ref.match([face(x=2)])['status'] == 'moved'
    assert ref.resolve([face(x=100)]) is None
    assert ref.match([face(x=2), face(1, x=2.1)])['status'] == 'ambiguous'
    legacy = FaceRef(0, [0, 0, 0], 100)
    assert legacy.resolve([face()]) == 0
    assert legacy.resolve([face(x=2)]) is None


def test_api_reference_diagnostics_preserve_document():
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (20, 20)); p.extrude(sk, 10)
    fillet = p.fillet(edges=[0], r=1)
    before = p.doc.to_dict()
    rows = p.reference_status(fillet)
    assert rows[0]['path'] == ['edges', 0]
    assert rows[0]['status'] == 'exact'
    assert p.doc.to_dict() == before
    fillet.edges[0].mid = [1000, 1000, 1000]
    assert p.reference_status(fillet)[0]['status'] == 'missing'
