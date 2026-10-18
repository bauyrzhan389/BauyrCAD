import math
import pytest
from bauyrcad.api import Part, ApiError
from bauyrcad.core.document import Document
from bauyrcad.core.parameters import ParameterError, values


def definitions(**expressions):
    return {name: {'kind': 'length', 'expression': expr} for name, expr in expressions.items()}


def cylinder():
    part = Part()
    sketch = part.sketch()
    dimension = sketch.dim(sketch.circle((0, 0), d=10), 10)
    feature = part.extrude(sketch, 5)
    return part, sketch, dimension, feature


def test_units_forward_references_and_rejected_expressions():
    assert values(definitions(width2mm='2 mm', result='width2mm + 1e2mm'))['result'].value == 102
    assert values(definitions(a='b + 2 mm', b='1 inch'))['a'].value == pytest.approx(27.4)
    for expr in ('1/0', '1e309', 'missing', 'abs(3)', '(2).__class__', '[1][0]', 'True', '2**3', '1 mm + 1 deg'):
        with pytest.raises(ParameterError):
            values(definitions(a=expr))
    with pytest.raises(ParameterError, match='Circular'):
        values(definitions(a='b', b='a'))
    with pytest.raises(ParameterError, match='different units'):
        values(definitions(a='1 mm', b='a + 2'))


def test_shared_sketch_feature_bindings_roundtrip_and_transaction(tmp_path):
    p, s, d, f = cylinder()
    p.parameter('diam', '20 mm')
    p.parameter('thickness', 'diam / 4')
    p.bind_parameter(s, f'dimension:{d.id}', 'diam')
    p.bind_parameter(f, 'depth', 'thickness')
    p.parameter('diam', '30 mm')
    assert f.depth == 7.5
    assert p.volume() == pytest.approx(math.pi * 15**2 * 7.5)
    before = p.doc.to_dict()
    for expression in ('thickness', '-10 mm', 'unknown'):
        with pytest.raises(ApiError):
            p.parameter('diam', expression)
        assert p.doc.to_dict() == before
    p.rename_parameter('diam', 'diameter')
    assert p.doc.parameters['thickness']['expression'] == 'diameter / 4'
    path = tmp_path / 'equations.bcad'
    p.save(path)
    loaded = Document.load(path)
    assert loaded.to_dict()['version'] == 2
    assert loaded.parameter_bindings == p.doc.parameter_bindings
    assert loaded.feature(f.id).depth == 7.5
    p.bind_parameter(f, 'depth', None)
    p.parameter('diameter', '40 mm')
    assert f.depth == 7.5
    p.doc.remove_feature(s.feature.id)
    assert not p.doc.parameter_bindings


def test_numeric_files_and_angle_units():
    p, s, d, f = cylinder()
    assert p.doc.to_dict()['version'] == 1
    assert p.parameter('turn', 'pi * rad', kind='angle') == pytest.approx(180)
    with pytest.raises(ApiError, match='incompatible units'):
        p.bind_parameter(f, 'depth', 'turn')
    with pytest.raises(ApiError, match='unsupported'):
        p.bind_parameter(f, 'id', '2')


def test_pattern_count_and_revolve_angle():
    p = Part()
    s = p.sketch()
    s.centerline((0, 0), (0, 10))
    s.rect((10, 0), (15, 10))
    f = p.revolve(s, angle=90)
    p.parameter('turn', 'pi * rad', kind='angle')
    p.bind_parameter(f, 'angle', 'turn')
    assert f.angle == pytest.approx(180)
    assert p.volume() == pytest.approx(math.pi * (15**2 - 10**2) * 10 / 2)
    p, s, d, f = cylinder()
    pattern = p.pattern_linear(direction='x', spacing=30, count=2)
    p.parameter('instances', '3', kind='scalar')
    p.bind_parameter(pattern, 'count', 'instances')
    assert p.volume() == pytest.approx(3 * math.pi * 5**2 * 5)
    before = p.doc.to_dict()
    with pytest.raises(ApiError, match='integer'):
        p.parameter('instances', '2.5', kind='scalar')
    assert p.doc.to_dict() == before


def test_sketch_angle_binding_keeps_radians():
    from bauyrcad.core.sketch.model import Constraint
    from bauyrcad.core.sketch import constraints, solver
    p = Part()
    s = p.sketch()
    a = s.sk.add_line((0, 0), (10, 0))
    b = s.sk.add_line((0, 0), (10, 10))
    c = Constraint(0, 'angle', [('e', a.id), ('e', b.id)])
    c.value = constraints.measure(s.sk, c)
    assert solver.try_add(s.sk, c)[0]
    p.parameter('slope', '30 deg', kind='angle')
    p.bind_parameter(s, f'dimension:{c.id}', 'slope')
    assert c.value == pytest.approx(math.pi / 6)
    assert constraints.measure(s.sk, c) == pytest.approx(math.pi / 6)


def test_maximum_forward_chain_uses_no_recursive_dependency_stack():
    parameters = {f'p{i}': {'kind': 'length', 'expression': f'p{i+1}' if i < 199 else '1 mm'}
                  for i in range(200)}
    assert values(parameters)['p0'].value == 1
    parameters['p199']['expression'] = 'p0'
    with pytest.raises(ParameterError, match='Circular'):
        values(parameters)


def test_plane_equations_allow_signed_offsets_and_move_dependents(tmp_path):
    p = Part()
    plane = p.plane('top', offset=10)
    sk = p.sketch(plane); sk.rect((0, 0), (10, 10))
    p.extrude(sk, 5)
    p.parameter('offset', '-12 mm')
    p.bind_parameter(plane, 'distance', 'offset')
    assert p.bbox()[0][1] == pytest.approx(-12)
    p.parameter('offset', '0 mm')
    assert p.bbox()[0][1] == pytest.approx(0)
    path = tmp_path / 'plane.bcad'; p.save(path)
    assert Document.load(path).feature(plane.id).distance == 0
    with pytest.raises(ApiError, match='unsupported'):
        p.bind_parameter(plane, 'angle', '30 deg')


def test_hole_and_rib_equations_change_material_volume():
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (30, 30)); p.extrude(sk, 10)
    hole = p.hole('front', (15, 15), d=4, through=True)
    p.parameter('bore', '6 mm'); p.bind_parameter(hole, 'd', 'bore')
    assert p.volume() == pytest.approx(9000 - math.pi * 9 * 10)
    before = p.doc.to_dict()
    with pytest.raises(ApiError, match='positive'):
        p.parameter('bore', '0 mm')
    assert p.doc.to_dict() == before
    with pytest.raises(ApiError, match='unsupported'):
        p.bind_parameter(hole, 'depth', '2 mm')
    p = Part()
    sk = p.sketch()
    sk.polyline([(0, 0), (60, 0), (60, 8), (8, 8), (8, 60), (0, 60)], close=True)
    p.extrude(sk, 40)
    sk = p.sketch('front', offset=20); sk.line((8, 40), (40, 8))
    rib = p.rib(sk, 6)
    p.bind_parameter(rib, 'thickness', '4 mm')
    assert p.volume() == pytest.approx((60 * 8 + 52 * 8) * 40 + 0.5 * 32 * 32 * 4)


def test_helix_fractional_turns_and_active_dimensions():
    p = Part()
    helix = p.helix(center=(0, 0, 0), d=20, pitch=4, turns=3)
    p.parameter('turns', '2.5', kind='scalar')
    p.bind_parameter(helix, 'revolutions', 'turns')
    assert helix.pitch_turns() == (4, 2.5)
    p.bind_parameter(helix, 'start_angle', 'pi * rad')
    assert helix.start_angle == pytest.approx(180)
    with pytest.raises(ApiError, match='unsupported'):
        p.bind_parameter(helix, 'height', '10 mm')
    before = p.doc.to_dict()
    with pytest.raises(ApiError, match='positive'):
        p.parameter('turns', '-1', kind='scalar')
    assert p.doc.to_dict() == before


def test_standard_countersink_angle_equation_is_not_ignored():
    p = Part()
    sk = p.sketch(); sk.rect((0, 0), (30, 30)); p.extrude(sk, 15)
    hole = p.hole('front', (15, 15), size='M6', kind='countersink', through=True)
    initial = p.volume()
    p.bind_parameter(hole, 'csk_angle', '60 deg')
    assert hole.dims()['csk_angle'] == pytest.approx(60)
    assert p.volume() < initial
    with pytest.raises(ApiError, match='between'):
        p.bind_parameter(hole, 'csk_angle', '180 deg')
