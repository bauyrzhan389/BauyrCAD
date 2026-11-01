import pytest

from bauyrcad.api import Part
from bauyrcad.core import swtree, swfile
from bauyrcad.core.sworigin import reference_ids


def origin_part(plane='front', kind='circle', shared=False, offset=False):
    part = Part()
    support = part.plane(plane, offset=13) if offset else plane
    sketch = part.sketch(support)
    if kind == 'circle':
        circle = sketch.sk.add_circle(0, 6); sketch.dim(circle)
    elif kind == 'horizontal':
        circle = sketch.circle((12, 0), r=6); sketch.dim(circle)
        sketch.relate('horizontal', ('p', 0), ('p', circle.center))
    else:
        start = 0 if kind == 'rectangle' else (0, 0)
        a = sketch.line(start, (40, 0)); b = sketch.line(a.p2, (40, 20))
        c = sketch.line(b.p2, (0, 20)); sketch.line(c.p2, a.p1)
        if kind == 'coincident': sketch.relate('coincident', ('p', 0), ('p', a.p1))
        sketch.dim(a); sketch.dim(b)
    part.extrude(sketch, 11)
    if shared: part.extrude(sketch, 7, reverse=True)
    return part


@pytest.mark.parametrize('kind', ['circle', 'rectangle', 'coincident', 'horizontal'])
@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
def test_origin_reference_is_serialized(tmp_path, kind, plane):
    part = origin_part(plane, kind)
    path = tmp_path / 'origin.SLDPRT'
    part.export_solidworks_offline(path, require_native=True)
    container, archive = swtree.load(path)
    model = archive.config.root['part'].values['model_data'].values
    assert len(model['point_records']) == 1 and model['_4'] == 0x20000001
    assert len(reference_ids(archive, archive.features)) == 1
    assert all(container.get(key) == value for key, value in archive.dumps().items())


@pytest.mark.parametrize('offset', [False, True])
def test_shared_origin_record_is_not_duplicated(tmp_path, offset):
    part = origin_part('right', shared=True, offset=offset)
    path = tmp_path / 'shared.SLDPRT'; part.export_solidworks_offline(path, require_native=True)
    _, archive = swtree.load(path)
    assert len(archive.config.root['part'].values['model_data'].values['point_records']) == 1
    assert len(reference_ids(archive, archive.features)) == 1


def test_multiple_sketches_use_distinct_origin_records(tmp_path):
    part = Part()
    for i in range(3):
        sketch = part.sketch('front'); circle = sketch.sk.add_circle(0, 20-i*5)
        sketch.dim(circle); part.extrude(sketch, 11+i*7)
    path = tmp_path / 'multiple.SLDPRT'; part.export_solidworks_offline(path, require_native=True)
    _, archive = swtree.load(path)
    assert len(reference_ids(archive, archive.features)) == 3
    assert len(archive.config.root['part'].values['model_data'].values['point_records']) == 3


def test_face_origin_preserves_destination_until_projection_supported(tmp_path):
    part = Part(); sketch = part.sketch('front'); sketch.rect((-40, -20), (40, 20))
    part.extrude(sketch, 11)
    sketch = part.sketch(on=part.face('+z')); sketch.sk.add_circle(0, 6); part.cut(sketch, 3)
    path = tmp_path / 'keep.SLDPRT'; path.write_bytes(b'keep')
    with pytest.raises(swfile.SwFileError, match='origin on a model face'):
        part.export_solidworks_offline(path, require_native=True)
    assert path.read_bytes() == b'keep'
