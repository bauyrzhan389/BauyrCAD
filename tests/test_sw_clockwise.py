import math
import pytest

from bauyrcad.api import Part
from bauyrcad.core import swtree


def notch_part(plane='front'):
    part = Part()
    sketch = part.sketch(plane)
    a = sketch.line((3, 5), (43, 5))
    b = sketch.line(a.p2, (43, 27))
    sketch.line(b.p2, (28, 27))
    arc = sketch.arc((23, 27), (28, 27), (18, 27), ccw=False)
    d = sketch.line((18, 27), (3, 27))
    sketch.line(d.p2, a.p1)
    sketch.dim(arc, 5)
    part.extrude(sketch, 11)
    return part


@pytest.mark.parametrize('plane', ['front', 'top', 'right'])
def test_concave_clockwise_arc_is_native(tmp_path, plane):
    part = notch_part(plane)
    path = tmp_path / 'notch.SLDPRT'
    assert part.export_solidworks_offline(path, require_native=True)['native']
    container, archive = swtree.load(path)
    sketch = archive.features[-2].values['sketch'].values
    assert sketch['seglists'][2]['segs'][0]['arc'] == 0xffffffff
    assert all(container.get(key) == value for key, value in archive.dumps().items())
    assert part.volume() == pytest.approx((40*22 - math.pi*25/2)*11)


def test_complementary_arcs_keep_distinct_handles(tmp_path):
    part = Part(); sketch = part.sketch('front')
    first = sketch.arc((12, 17), (22, 17), (2, 17))
    sketch.arc((12, 17), (2, 17), (22, 17))
    sketch.dim(first, 10)
    part.extrude(sketch, 11)
    path = tmp_path / 'two_arcs.SLDPRT'
    part.export_solidworks_offline(path, require_native=True)
    _, archive = swtree.load(path)
    arcs = archive.features[-2].values['sketch'].values['seglists'][2]['segs']
    assert len(arcs) == 2
    assert arcs[0]['p0'] == arcs[1]['p1'] and arcs[0]['p1'] == arcs[1]['p0']
