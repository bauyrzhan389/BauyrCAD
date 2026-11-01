import pytest
from bauyrcad.api import Part
from bauyrcad.core import swfile


@pytest.mark.parametrize('name',['Front Plane','front plane','Origin','Top Plane'])
@pytest.mark.parametrize('history',['ordinary','shared','reference'])
def test_reserved_name_cannot_replace_native_defaults(tmp_path,name,history):
    p=Part()
    support=p.plane('right',offset=13) if history=='reference' else 'front'
    sk=p.sketch(support);sk.rect((3,5),(43,27));p.extrude(sk,11)
    if history=='shared':p.extrude(sk,7,reverse=True)
    next(f for f in p.doc.features if f.TYPE=='sketch').name=name
    path=tmp_path/'keep.SLDPRT';path.write_bytes(b'existing user output')
    with pytest.raises(swfile.SwFileError,match='Reserved SolidWorks feature name'):
        p.export_solidworks_offline(path,require_native=True)
    assert path.read_bytes()==b'existing user output'


def test_feature_names_are_unique_without_case(tmp_path):
    p=Part();sk=p.sketch('front');sk.rect((3,5),(43,27));p.extrude(sk,11)
    p.doc.features[-1].name='sketch1'
    path=tmp_path/'keep.SLDPRT';path.write_bytes(b'keep')
    with pytest.raises(swfile.SwFileError,match='unique'):
        p.export_solidworks_offline(path,require_native=True)
    assert path.read_bytes()==b'keep'
