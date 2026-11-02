"""Experimental SolidWorks 2024 feature writer. No COM; only saved-file templates.

Unsupported design intent is rejected before writing, so the caller can report a whole-part
Imported-body fallback. Never mix a partial tree with an unrelated final solid.
"""
from pathlib import Path
from functools import lru_cache
import math
import struct

import numpy as np

from . import mfc, swtree, swfile
from .features import SketchFeature, ExtrudeFeature, RevolveFeature
from .plane import REFERENCE_PLANES

SEEDS = Path(__file__).parents[1] / 'data' / 'sw_seeds' / 'native'


class UnsupportedFeature(ValueError):
    pass


def objects(value, seen=None):
    seen = set() if seen is None else seen
    if isinstance(value, mfc.Obj):
        if id(value) in seen:
            return
        seen.add(id(value))
        yield value
        yield from objects(value.values, seen)
    elif isinstance(value, dict):
        for v in value.values():
            yield from objects(v, seen)
    elif isinstance(value, list):
        for v in value:
            yield from objects(v, seen)


def _load(seed):
    path = SEEDS / f'{seed}.SLDPRT'
    if not path.exists():
        raise UnsupportedFeature(f'No validated template for {seed}')
    return swtree.load(path)


@lru_cache(maxsize=1)
def _reserved_names():
    _, donor = _load('M4_front_blind_0')
    return frozenset(swtree.name(feature).casefold() for feature in donor.features[:-2])


def _validate_names(features):
    names = [feature.name for feature in features]
    if len({name.casefold() for name in names}) != len(names) or any(not name or '@' in name for name in names):
        raise UnsupportedFeature('Feature names must be unique, nonempty and contain no @')
    conflicts = [name for name in names if name.casefold() in _reserved_names()]
    if conflicts:
        raise UnsupportedFeature('Reserved SolidWorks feature name: ' + ', '.join(conflicts))


def _plane(feature):
    if feature.plane_spec and feature.plane_spec.get('type') != 'std':
        raise UnsupportedFeature(f'{feature.name}: model-face or custom reference plane')
    for name, plane in REFERENCE_PLANES.items():
        if all(np.allclose(getattr(feature.plane, k), getattr(plane, k), atol=1e-9, rtol=0)
               for k in ('origin', 'normal', 'xdir')):
            return name.split()[0].lower()
    raise UnsupportedFeature(f'{feature.name}: offset or nonstandard sketch plane')


def _profile(feature, axis_id=None):
    from .swsketch import profile
    return profile(feature, axis_id)


def _append(base, donor):
    """Append the donor's last sketch/body pair, remapping shared objects and feature ids."""
    source = donor.features[-2:]
    old = donor.features[:-2]
    bf = {swtree.name(f): f for f in base.features}
    bodies = [f for f in base.features if 'counters' in f.values]
    sketches = [f for f in base.features if f.name == 'moProfileFeature_c']
    pairs, ids = {}, {}
    for f in old:
        target = bodies[-1] if 'counters' in f.values else sketches[-1] if f.name == 'moProfileFeature_c' else bf.get(swtree.name(f))
        if target is not None:
            pairs[id(f)] = target
            ids[swtree._feature_id(f)] = swtree._feature_id(target)
    # The external owner is shared. Unit components are context-specific (plane/body
    # components contain different data); never collapse them to the part's unit object.
    for cname in ('moExtObject_c',):
        targets = [o for o in objects([base.config.root, base.resolved.root]) if o.name == cname]
        if targets:
            for o in objects([donor.config.root, donor.resolved.root]):
                if o.name == cname:
                    pairs[id(o)] = targets[0]
    classes = {o.cls.name: o.cls for o in objects([base.config.root, base.resolved.root])}
    cp = swtree._Copier(pairs, classes)
    new = [cp.obj(f) for f in source]
    max_id = max(o.values['id'] for o in objects([base.config.root, base.resolved.root])
                 if o.name in ('moNodeName_c', 'moLengthParameter_c', 'moAngleParameter_c') and o.values['id'] != 0xFFFFFFFF)
    for f in source:
        max_id += 1
        ids[swtree._feature_id(f)] = max_id
    atoms = [cp.val(d) for d in donor.config.root['part'].values['model_data'].values['atoms']
             if d['_0'].values['feature_id'] == swtree._feature_id(source[-1])]
    history = next(f for f in donor.features if f.name == 'moHistoryFolder_c')
    entries = [cp.val(d) for d in history.values['items'] if d['_1'].values['id'] in
               {swtree._feature_id(f) for f in source}]
    for o in objects([new, atoms, entries]):
        if id(o) not in {id(x) for x in cp.copies.values()}:
            continue
        if o.name in ('moLengthParameter_c', 'moAngleParameter_c'):
            max_id += 1
            o.values['id'] = max_id
        elif o.name in ('moNodeName_c', 'moFR_c', 'moCompFeature_c', 'moCompSolidBody_c',
                        'moHistoryFeatItemData_c', 'moCompRefPlane_c', 'moCompSketchEntHandle_c'):
            if o.values.get('id') in ids:
                o.values['id'] = ids[o.values['id']]
        elif o.name == 'moAtom_c':
            for key in ('feature_id', 'feature_id2'):
                o.values[key] = ids[o.values[key]]
    base.resolved.root['features'].extend({'_0': f} for f in new)
    base.config.root['part'].values['model_data'].values['atoms'][:0] = atoms
    next(f for f in base.features if f.name == 'moHistoryFolder_c').values['items'].extend(entries)
    swtree.renumber(base, 2, len(atoms), max_id + 1000)
    return new


def _model_header(data, arc):
    """Keep the feature ID/name registry used to resolve revolve sketch axes current."""
    header, table = mfc.loads(data, 'model_header', start=1)
    values = header.root['header'].values
    logs = values['logs'].values['items']
    by_id = {item['_0'].values['id']: item['_0'] for item in logs}
    template = logs[-1]['_0']
    classes = {c.name: c for c in table if isinstance(c, mfc.MfcClass)}
    for f in arc.features:
        fid = swtree._feature_id(f)
        if fid in by_id:
            by_id[fid].values['name'] = swtree.name(f)
        elif f.name in ('moProfileFeature_c', 'moRefPlane_c') or 'counters' in f.values:
            log = swtree._Copier({}, classes).obj(template)
            log.values['id'], log.values['name'] = fid, swtree.name(f)
            logs.append({'_0': log})
            by_id[fid] = log
    # A sketch-axis reference carries its creation stamp. SolidWorks validates it
    # against this registry; an ID/name match alone is insufficient (error 51).
    for obj in objects([arc.config.root, arc.resolved.root]):
        if obj.name == 'moCompSketchEntHandle_c':
            ref = obj.values
            for item in by_id[ref['id']].values['items']:
                item['_0'].values['_1'] = ref['stamp']
    for obj in objects(arc.resolved.root):
        if obj.name == 'moCompRefPlane_c' and obj.values['id'] in by_id:
            for item in by_id[obj.values['id']].values['items']:
                item['_0'].values['_1'] = obj.values['stamp']
        if obj.name == 'moFaceRefPlnData_c':
            for ref in objects(obj.values['face']):
                if ref.name == 'moFR_c':
                    for item in by_id[ref.values['id']].values['items']:
                        item['_0'].values['_1'] = ref.values['stamp']
    counters = bytearray(values['counters'])
    ids = [o.values['id'] for o in objects([arc.config.root, arc.resolved.root])
           if o.name in ('moNodeName_c','moLengthParameter_c','moAngleParameter_c') and o.values['id'] != 0xFFFFFFFF]
    struct.pack_into('<I', counters, 4, max(ids)+1)
    struct.pack_into('<I', counters, 10, max(f.values.get('seq', 0) for f in arc.features)+1)
    values['counters'] = bytes(counters)
    return mfc.dumps(header)[0]


def write(document, path, name='Part1'):
    """Write supported sketch/extrude/revolve features, rejecting unsupported intent before output."""
    if getattr(document, 'parameters', None) or getattr(document, 'parameter_bindings', None):
        raise UnsupportedFeature('Named parameters and equations are not yet preserved by the offline writer')
    fs = document.features
    _validate_names(fs)
    from . import geometry
    if geometry.count_solids(document.body) != 1:
        raise UnsupportedFeature('Native writer currently requires one connected solid')
    if any(f.TYPE == 'shell' for f in fs):
        from .swshell import write
        return write(document, path, name)
    if any(f.TYPE == 'ref_plane' for f in fs):
        from .swreferenceplanes import write
        return write(document, path, name)
    if any(f.TYPE in ('fillet', 'chamfer') for f in fs):
        from .swedges import write
        return write(document, path, name)
    if not fs or len(fs) % 2 or any(
            not isinstance(fs[i], SketchFeature) or
            not isinstance(fs[i + 1], (ExtrudeFeature, RevolveFeature)) or
            fs[i + 1].sketch_id != fs[i].id for i in range(0, len(fs), 2)):
        from .swsharedsketches import write
        return write(document, path, name)
    prepared = []
    face_planes = {}
    names = [f.name for f in fs]
    if len(set(names)) != len(names) or any(not n or '@' in n for n in names):
        raise UnsupportedFeature('Feature names must be unique, nonempty and contain no @')
    for i in range(0, len(fs), 2):
        sk, ex = fs[i:i+2]
        if not isinstance(sk, SketchFeature) or not isinstance(ex, (ExtrudeFeature, RevolveFeature)) or ex.sketch_id != sk.id:
            raise UnsupportedFeature(f'{sk.name}: requires independent sketch/extrude or sketch/revolve pairs')
        if sk.suppressed or ex.suppressed or sk.error or ex.error or ex.regions:
            raise UnsupportedFeature(f'{ex.name}: suppressed, failed or selected-contour feature')
        if sk.plane_spec and sk.plane_spec.get('type') == 'face':
            from . import swfaceplanes
            face_planes[i//2] = swfaceplanes.prepare(fs, i)
            plane = 'front'
        else:
            plane = _plane(sk)
        revolve = isinstance(ex, RevolveFeature)
        allowed = ('blind', 'midplane') if revolve else ('blind', 'midplane', 'through_all', 'through_all_both')
        if ex.operation not in ('boss', 'cut') or ex.end not in allowed:
            raise UnsupportedFeature(f'{ex.name}: unsupported feature mode')
        size = ex.angle if revolve else ex.depth
        if not math.isfinite(size) or (size <= 0 and ex.end not in ('through_all', 'through_all_both')) or (revolve and size > 360):
            raise UnsupportedFeature(f'{ex.name}: invalid angle or depth')
        reverse = bool(ex.reverse) ^ (False if revolve else bool(ex.flipped))
        if ex.end in ('midplane', 'through_all_both'):
            reverse = False
        family = 'M5' if revolve else 'M4'
        seed = f'{family}_{plane}_{ex.end}_{int(reverse)}' if i == 0 else f'{family}_{"add" if ex.operation == "boss" else "cut"}_{ex.end}_{int(reverse)}'
        if i == 0 and ex.operation != 'boss':
            raise UnsupportedFeature('First body feature must be a boss')
        axis = None
        if revolve:
            if ex.axis_spec or ex.axis_sketch_id not in (None, sk.id):
                raise UnsupportedFeature(f'{ex.name}: requires an axis in its own profile sketch')
            entity = ex.axis_line(sk)
            if not entity.construction:
                raise UnsupportedFeature(f'{ex.name}: currently requires a construction centerline axis')
            axis = (tuple(sk.sketch.pt(entity.p1)), tuple(sk.sketch.pt(entity.p2)))
        loops, dims = _profile(sk, entity.id if revolve else None)
        container, donor = _load(seed)
        prepared.append((sk, ex, plane, loops, dims, axis, container, donor))
    # The empty donor supplies the outer container. The first-boss archive initializes
    # the settings/counters that SolidWorks creates with a part's first body; subsequent
    # features are appended below. No cached donor solid is retained.
    base_container, _ = _load('M4_empty')
    arc = prepared[0][-1]
    arc.config.root['part'].values['name'].values['name'] = name
    for i, (sk, ex, plane, loops, dims, axis, container, donor) in enumerate(prepared):
        target_sk, target_ex = arc.features[-2:] if i == 0 else _append(arc, donor)
        if i:
            _, pl = _load(f'M4_{plane}_blind_0')
            classes = {o.cls.name:o.cls for o in objects([arc.config.root,arc.resolved.root])}
            cp = swtree._Copier({}, classes)
            target_sk.values['plane'] = cp.obj(pl.features[-2].values['plane'])
            target_sk.values['plane_tail'] = cp.val(pl.features[-2].values['plane_tail'])
        if i in face_planes:
            swfaceplanes.attach(arc, target_sk, sk, face_planes[i])
        target_sk.values['name'].values['name'] = sk.name
        target_ex.values['name'].values['name'] = ex.name
        from . import swsketch
        axis_id = ex.axis_line(sk).id if axis is not None else None
        entity_map = swsketch.write(arc, target_sk, sk, loops, dims, axis_id)
        if axis is not None:
            axis_index = entity_map[axis_id][1]
            ref = target_ex.values['axis'].values['entity'].values
            ref['id'] = swtree._feature_id(target_sk)
            ref['segment'].values['index'] = axis_index
            target_ex.values['axis'].values['sketch_seq'] = target_sk.values['seq']
        display = target_ex.values['end_spec'].values['dim']
        if display is not None:  # through-all has no depth parameter/display dimension
            handle = display.values['handle'].values
            value = math.radians(ex.angle) if axis is not None else ex.depth / 1000
            handle['dim'].values['param'].values['value'] = value
            if axis is None:
                handle['value'] = value
            handle['_19'] = value
        # Refresh the load map between appended pairs, including new dimension objects.
        arc = mfc.load_archive(arc.dumps())
    if (face_planes or any(isinstance(item[1], RevolveFeature) for item in prepared) or
            arc.config.root['part'].values['model_data'].values['point_records']):
        first_container = prepared[0][-2]
        base_container = swfile.Container(base_container.build({
            key: _model_header(first_container.get(key), arc)
            for key in ('Contents/Config-0-ModelHeader', 'Header2')}))
    swtree.save(base_container, arc, path)
    return {'path': str(path), 'native': True, 'features': names, 'warnings': []}
