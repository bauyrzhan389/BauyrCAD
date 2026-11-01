"""Offline finishing features on a straight polygonal extrusion.

Edge references identify the two incident generating faces and the two end faces.
No cached body or SolidWorks installation is used to produce the output.
"""
import math

import numpy as np

from . import mfc, modeling, swfile, swtree
from .features import ExtrudeFeature, RegenContext, SketchFeature
from .features_more import ChamferFeature, FilletFeature
from .swfeatures import UnsupportedFeature, _load, _model_header, _plane, _profile, objects


def _selection(sk, ex, feature, loops):
    ctx = RegenContext()
    for f in (sk, ex):
        ctx.features[f.id] = f
        f.execute(ctx)
    infos = modeling.edge_infos(ctx.body)
    selected = feature.edge_indices(ctx)
    polygon = loops[0]
    n = len(polygon)
    result = []
    for index in selected:
        edge = infos[index]
        if edge['kind'] != 'line':
            raise UnsupportedFeature(f'{feature.name}: curved edge')
        ends = []
        for p in (edge['p0'], edge['p1']):
            delta = np.asarray(p) - sk.plane.origin
            ends.append(np.array([np.dot(delta, sk.plane.xdir), np.dot(delta, sk.plane.ydir),
                                  np.dot(delta, sk.plane.normal)]))
        a, b = ends
        if np.allclose(a[:2], b[:2], atol=1e-6, rtol=0):
            matches = [i for i, s in enumerate(polygon) if np.allclose(a[:2], s[1], atol=1e-6, rtol=0)]
            tag = 'vertical'
        else:
            matches = [i for i, s in enumerate(polygon) if
                       (np.allclose(a[:2], s[1], atol=1e-6, rtol=0) and np.allclose(b[:2], s[2], atol=1e-6, rtol=0)) or
                       (np.allclose(b[:2], s[1], atol=1e-6, rtol=0) and np.allclose(a[:2], s[2], atol=1e-6, rtol=0))]
            tag = 'bottom' if abs(a[2]) < 1e-6 else 'top'
        if len(matches) != 1:
            raise UnsupportedFeature(f'{feature.name}: edge does not identify one generating segment')
        i = matches[0]
        # The saved one-edge donor uses segment 1, its predecessor 4 and successor 2.
        result.append((tag, {1: i + 1, 4: (i - 1) % n + 1, 2: (i + 1) % n + 1}))
    return result


def _dimension(display, value):
    handle = display.values['handle'].values
    handle['dim'].values['param'].values['value'] = value
    if handle['dim'].name != 'AngleDim_c':
        handle['value'] = value
    handle['_19'] = value


def write(document, path, name):
    fs = document.features
    if len(fs) != 3 or not isinstance(fs[0], SketchFeature) or not isinstance(fs[1], ExtrudeFeature) or not isinstance(fs[2], (FilletFeature, ChamferFeature)):
        raise UnsupportedFeature('Finishing features currently require one sketch, one blind extrusion and one fillet or chamfer')
    sk, ex, finish = fs
    names = [f.name for f in fs]
    if len(set(names)) != len(names) or any(not n or '@' in n for n in names):
        raise UnsupportedFeature('Feature names must be unique, nonempty and contain no @')
    if any(f.suppressed or f.error for f in fs) or ex.regions or ex.sketch_id != sk.id:
        raise UnsupportedFeature(f'{finish.name}: suppressed, failed, shared sketch or selected-contour feature')
    if ex.operation != 'boss' or ex.end != 'blind' or ex.reverse or ex.flipped or not math.isfinite(ex.depth) or ex.depth <= 0:
        raise UnsupportedFeature(f'{finish.name}: requires a forward blind boss')
    plane = _plane(sk)
    loops, dims = _profile(sk)
    if len(loops) != 1 or any(s[0] != 'line' for s in loops[0]):
        raise UnsupportedFeature(f'{finish.name}: requires a polygonal profile without holes')
    fillet = isinstance(finish, FilletFeature)
    size = finish.radius if fillet else finish.distance
    if not math.isfinite(size) or size <= 0 or (fillet and finish.radius2 is not None):
        raise UnsupportedFeature(f'{finish.name}: requires a positive constant size')
    if not fillet and finish.kind != 'distance':
        raise UnsupportedFeature(f'{finish.name}: asymmetric chamfer orientation is not yet supported offline')
    selected = _selection(sk, ex, finish, loops)
    kind = 'fillet' if fillet else 'chamfer_equal'
    container, arc = _load(f'FC_{kind}_{selected[0][0]}')
    target_sk, target_ex, target = arc.features[-3:]
    for f, actual in zip((target_sk, target_ex, target), fs):
        f.values['name'].values['name'] = actual.name
    arc.config.root['part'].values['name'].values['name'] = name
    classes = {o.cls.name: o.cls for o in objects([arc.config.root, arc.resolved.root])}
    _, plane_donor = _load(f'M4_{plane}_blind_0')
    target_sk.values['plane'] = swtree._Copier({}, classes).obj(plane_donor.features[-2].values['plane'])
    target_sk.values['plane_tail'] = swtree._Copier({}, classes).val(plane_donor.features[-2].values['plane_tail'])
    from . import swsketch
    swsketch.write(arc, target_sk, sk, loops, dims)
    _dimension(target_ex.values['end_spec'].values['dim'], ex.depth / 1000)
    owners = [o for o in objects(target) if o.name == 'moExtObject_c']
    # Each edge contributes a surface, selection, component, and (for fillets) a
    # face/edge association and radius entry. Only one driving radius dimension exists.
    assembled = []
    for ordinal, (tag, mapping) in enumerate(selected, 1):
        _, donor = _load(f'FC_{kind}_{tag}')
        source = donor.features[-1]
        pairs = {id(o): owners[0] for o in objects(source) if o.name == 'moExtObject_c'}
        copied = swtree._Copier(pairs, classes).obj(source)
        for o in objects(copied):
            if o.name == 'moFromSktEntSurfIdRep_c':
                o.values['segment'] = mapping[o.values['segment']]
            elif o.name == 'moFR_c':
                # All saved seeds use one boss; copy its feature creation stamp too.
                original_id = o.values['id']
                dest = target_ex if original_id == swtree._feature_id(donor.features[-2]) else target
                o.values['id'] = swtree._feature_id(dest)
                reference = next(x for x in objects(target) if x.name == 'moFR_c' and x.values['id'] == o.values['id'])
                o.values['stamp'] = reference.values['stamp']
            elif o.name == 'moFilletSurfIdRep_c':
                o.values['_2'] = ordinal
            if o.name in ('moSurfaceIdRep_c', 'moFilletSurfIdRep_c'):
                o.values['_0'] = mfc.Ref(0, target)
        assembled.append(copied.values)
    v = target.values
    v['surfaces'] = [v['surfaces'][0]] + [x['surfaces'][1] for x in assembled]
    v['edges'] = [x['edges'][0] for x in assembled]
    v['edge_components'].values['items'] = [x['edge_components'].values['items'][0] for x in assembled]
    if fillet:
        v['faces'] = [x['faces'][0] for x in assembled]
        v['values'] = [x['values'][0] for x in assembled]
        for row in v['values']:
            row['_2'] = size / 1000
        # The dimension's reference follows the first selected edge.
        v['radii'] = assembled[0]['radii']
        _dimension(v['radii'].values['items'][0]['_0'].values['dim'], size / 1000)
    else:
        v['edge'] = assembled[0]['edge']
        _dimension(v['dim1'], size / 1000)
    base, _ = _load('M4_empty')
    base = swfile.Container(base.build({key: _model_header(container.get(key), arc)
                                       for key in ('Header2', 'Contents/Config-0-ModelHeader')}))
    swtree.save(base, arc, path)
    return {'path': str(path), 'native': True, 'features': names, 'warnings': []}
