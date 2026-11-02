import pytest
from bauyrcad.api import Part
from bauyrcad.core import swtree, sldprt
from bauyrcad.core.swfeatures import SEEDS


def shell_part(plane='front', opening='top', outward=False, polygon=False):
    p = Part('Shell check')
    s = p.sketch(plane)
    if polygon:
        s.polyline([(7, 9), (79, 9), (70, 40), (30, 53), (7, 40)], close=True)
    else:
        lines = s.rect((7, 9), (79, 53))
        s.dim(lines[0]); s.dim(lines[1])
    p.extrude(s, 25)
    axis = {'front': 'z', 'top': 'y', 'right': 'x'}[plane]
    remove = () if opening == 'closed' else p.face(('+' if opening == 'top' else '-') + axis)
    p.shell(2.5, remove=remove, outward=outward)
    return p


@pytest.mark.parametrize('seed', ['SH_open', 'SH_closed'])
def test_shell_seed_roundtrip(seed):
    container, archive = swtree.load(SEEDS / (seed + '.SLDPRT'))
    assert all(container.get(key) == value for key, value in archive.dumps().items())


@pytest.mark.parametrize('opening', ['top', 'bottom', 'closed'])
def test_shell_offline(opening, tmp_path):
    p = shell_part(opening=opening)
    path = tmp_path / 'shell.SLDPRT'
    assert p.export_solidworks_offline(path, require_native=True)['native']
    assert not sldprt.tree_mismatches(path, [f.name for f in p.doc.features], exact=True)
    assert not set(swtree.REGENERATED) & set(sldprt.read_sections(path))


def test_side_shell_refused_without_replacing_file(tmp_path):
    p = Part(); s = p.sketch(); s.rect((0, 0), (40, 30)); p.extrude(s, 25)
    p.shell(2, remove=p.face('+x'))
    target = tmp_path / 'keep.SLDPRT'; target.write_bytes(b'keep')
    with pytest.raises(ValueError, match='end-cap'):
        p.export_solidworks_offline(target, require_native=True)
    assert target.read_bytes() == b'keep'


@pytest.mark.parametrize('opening', ['top', 'bottom', 'closed'])
def test_planar_outward_shell_has_sharp_corners(opening):
    p = shell_part(opening=opening, outward=True)
    expected = 77 * 49 * (30 if opening == 'closed' else 27.5) - 72 * 44 * 25
    assert p.volume() == pytest.approx(expected, rel=1e-8)
