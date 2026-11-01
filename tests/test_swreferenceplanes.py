import pytest
from bauyrcad.api import Part
from bauyrcad.core import swtree, swfile


def offset_part(plane='front', negative=False, chained=False):
    p=Part('offset-plane')
    first=p.plane(plane,offset=-17 if negative else 17)
    support=p.plane(first,offset=9) if chained else first
    sketch=p.sketch(support)
    lines=sketch.rect((3,5),(37,23));sketch.dim(lines[0],34)
    p.extrude(sketch,11)
    return p


@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('negative',[False,True])
@pytest.mark.parametrize('chained',[False,True])
def test_offset_history_offline(tmp_path,plane,negative,chained):
    p=offset_part(plane,negative,chained)
    path=tmp_path/'planes.SLDPRT'
    assert p.export_solidworks_offline(path,require_native=True)['native']
    container,archive=swtree.load(path)
    assert all(container.get(k)==v for k,v in archive.dumps().items())


@pytest.mark.parametrize('mode',['angle','flip'])
def test_unsupported_plane_keeps_existing_file(tmp_path,mode):
    p=Part()
    if mode=='angle':plane=p.plane('front',angle=31,axis='y')
    else:plane=p.plane('front',offset=17,flip=True)
    sketch=p.sketch(plane);sketch.rect((3,5),(37,23));p.extrude(sketch,11)
    path=tmp_path/'keep.SLDPRT';path.write_bytes(b'keep')
    with pytest.raises(swfile.SwFileError,match='offset plane'):
        p.export_solidworks_offline(path,require_native=True)
    assert path.read_bytes()==b'keep'
