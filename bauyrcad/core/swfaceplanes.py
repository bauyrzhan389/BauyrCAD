"""Native sketch support on a planar face of a polygonal base extrusion."""
import numpy as np
import struct

from . import swtree
from .features import ExtrudeFeature
from .swfeatures import UnsupportedFeature, _load, _plane, _profile, objects


def prepare(features, index):
    sk = features[index]
    if index != 2 or len(features) != 4:
        raise UnsupportedFeature(f'{sk.name}: face support currently requires a single base extrusion')
    added = features[index+1]
    if not isinstance(added, ExtrudeFeature):
        raise UnsupportedFeature(f'{sk.name}: face support currently requires an added extrusion')
    if added.operation == 'boss' and (added.end not in ('blind', 'midplane') or (added.end == 'blind' and (added.reverse or added.flipped))):
        raise UnsupportedFeature(f'{sk.name}: reverse blind face bosses are not yet supported')
    if added.operation == 'cut' and added.end not in ('blind', 'through_all', 'through_all_both'):
        raise UnsupportedFeature(f'{sk.name}: this face-cut end condition is not yet supported')
    base, ex = features[:2]
    if not isinstance(ex, ExtrudeFeature) or ex.operation != 'boss' or ex.end not in ('blind', 'midplane'):
        raise UnsupportedFeature(f'{sk.name}: face support requires a blind or mid-plane base extrusion')
    _plane(base)
    loops, _ = _profile(base)
    if len(loops) != 1 or any(s[0] != 'line' for s in loops[0]):
        raise UnsupportedFeature(f'{sk.name}: face support requires a polygonal base without holes')
    poly = loops[0]
    reverse = bool(ex.reverse) ^ bool(ex.flipped)
    start, end = (0, -ex.depth if reverse else ex.depth) if ex.end == 'blind' else (-ex.depth/2, ex.depth/2)
    direction = 1 if end > start else -1
    for cap_index, sign, distance in ((1, direction, end), (0, -direction, start)):
        kind = 'top' if sign > 0 else 'bottom'
        normal = sign*base.plane.normal
        point = base.plane.origin + distance*base.plane.normal
        if np.allclose(normal, sk.plane.normal, atol=1e-8, rtol=0) and abs(np.dot(sk.plane.origin-point, normal)) < 1e-6:
            return kind, len(poly), cap_index
    matches = []
    for i, segment in enumerate(poly):
        a, b = np.asarray(segment[1]), np.asarray(segment[2])
        tangent = b - a
        normal = tangent[1] * base.plane.xdir - tangent[0] * base.plane.ydir
        normal /= np.linalg.norm(normal)
        point = base.plane.origin + a[0]*base.plane.xdir + a[1]*base.plane.ydir
        if np.allclose(normal, sk.plane.normal, atol=1e-8, rtol=0) and abs(np.dot(sk.plane.origin-point, normal)) < 1e-6:
            matches.append(i)
    if len(matches) != 1:
        raise UnsupportedFeature(f'{sk.name}: requires an unambiguous planar face of the base extrusion')
    i = matches[0]
    return "side", {2: i+1, 3: (i+1) % len(poly)+1, 1: (i-1) % len(poly)+1}, None


def attach(arc, target, source, support):
    kind, mapping, cap_index = support
    _, donor = _load(f'FP_{kind}')
    owner = next(o for o in objects([arc.config.root, arc.resolved.root]) if o.name == 'moExtObject_c')
    pairs = {id(o): owner for o in objects(donor.features[-2]) if o.name == 'moExtObject_c'}
    classes = {o.cls.name:o.cls for o in objects([arc.config.root, arc.resolved.root])}
    plane = swtree._Copier(pairs, classes).obj(donor.features[-2].values['plane'])
    target.values['plane'] = plane
    target.values['plane_tail'] = swtree._Copier({}, classes).val(donor.features[-2].values['plane_tail'])
    data = plane.values['inline']['_0'].values['data'].values
    attachment = data['attachment']
    if kind != 'side':
        face = data['face'].values['face'].values
        face['ids'][0]['_0'].values['_2'] = cap_index
        template = face['ids'][1]['_0']
        segments = ([mapping] + list(range(1, mapping))) if kind == 'top' else list(range(mapping, 0, -1))
        boundary = []
        for index in segments:
            edge = swtree._Copier({id(owner): owner}, classes).obj(template)
            edge.values['segment'] = index
            boundary.append({'_0': edge})
        face['ids'] = face['ids'][:1] + boundary + [{'_0': None}]
        face['n'] = len(face['ids'])
    frame = source.plane
    rep = lambda xs: [{'_0': float(x)} for x in xs]
    matrix = np.array([frame.xdir, frame.ydir, frame.normal]).T
    data['origin'], data['normal'] = rep(frame.origin/1000), rep(frame.normal)
    data['rot'] = attachment['rot2'] = 1
    data['matrix'] = {'_0': rep(matrix.flatten())}
    attachment['matrix2'] = {'_0': rep(matrix.flatten())}
    translation = -matrix.T @ frame.origin/1000
    data['_4'] = rep([*translation, 1])
    attachment['translation2'] = rep(translation)
    parent = arc.features[-3]
    raw = bytearray(attachment['_14'])
    struct.pack_into('<I', raw, 8, parent.values['seq'])
    attachment['_14'] = bytes(raw)
    for obj in objects(plane):
        if obj.name == 'moFromSktEntSurfIdRep_c' and kind == 'side':
            obj.values['segment'] = mapping[obj.values['segment']]
        elif obj.name == 'moFR_c':
            obj.values['id'] = swtree._feature_id(parent)
    # The independent profile has no cached donor body or boundary edges.
    target.values.update(cache_flag=1, cache=[], edges=[], cache_tail=None)
