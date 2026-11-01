import pytest
from bauyrcad.api import Part
from bauyrcad.core import sldprt, swtree, swfile


def shared_part(plane='front', kind='extrude'):
    p = Part('Shared profile')
    sk = p.sketch(plane)
    lines = sk.rect((5,3),(45,25)); sk.dim(lines[0],40)
    if kind == 'revolve':
        axis = sk.centerline((0,-5),(0,35))
        p.revolve(sk,100,axis=axis)
        p.revolve(sk,65,axis=axis,reverse=True)
    else:
        p.extrude(sk,11)
        p.extrude(sk,7,reverse=True)
    return p


@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('kind',['extrude','revolve'])
def test_shared_sketch_archive(tmp_path,plane,kind):
    p=shared_part(plane,kind);path=tmp_path/'shared.SLDPRT'
    p.export_solidworks_offline(path,require_native=True)
    assert sldprt.tree_mismatches(path,[f.name for f in p.doc.features],exact=True)==[]
    container,archive=swtree.load(path)
    assert all(container.get(k)==v for k,v in archive.dumps().items())
    assert sum(f.name=='moProfileFeature_c' for f in archive.features)==1


def test_unused_sketch_preserves_destination(tmp_path):
    p=shared_part();p.sketch('top').circle((4,6),d=3)
    path=tmp_path/'keep.SLDPRT';path.write_bytes(b'keep')
    with pytest.raises(swfile.SwFileError,match='Standalone'):
        p.export_solidworks_offline(path,require_native=True)
    assert path.read_bytes()==b'keep'
