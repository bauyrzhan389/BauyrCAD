"""Native offset, coincident and midpoint reference planes, without COM."""
import copy
import math
import struct
import tempfile
from pathlib import Path

import numpy as np

from . import mfc, swtree, swfeatures, swfile
from .plane import REFERENCE_PLANES
from .swfeatures import UnsupportedFeature, objects


def _rep(values):
    return [{'_0': float(value)} for value in values]


def _frame(data, plane):
    matrix = np.array([plane.xdir, plane.ydir, plane.normal]).T
    data['origin'] = _rep(plane.origin / 1000)
    data['normal'] = _rep(plane.normal)
    data['rot'] = 1
    data['matrix'] = {'_0': _rep(matrix.flatten())}
    data['_4'] = _rep([*(-matrix.T @ plane.origin / 1000), 1])


def _sketch_frame(tail, plane):
    matrix = np.array([plane.xdir, plane.ydir, plane.normal]).T
    tail['rot'] = 1
    tail['matrix'] = {'_0': _rep(matrix.flatten())}
    tail['_4'] = struct.pack('<3d', *(-matrix.T @ plane.origin / 1000))


def _reference_frame(reference, attachment, spec, parent, stamp, parent_frame):
    reference['id'] = swtree._feature_id(parent)
    reference['stamp'] = stamp
    matrix = np.array([parent_frame.xdir, parent_frame.ydir, parent_frame.normal]).T
    attachment['origin2'] = _rep(parent_frame.origin / 1000)
    attachment['normal2'] = _rep(parent_frame.normal)
    attachment['rot2'] = 1
    attachment['matrix2'] = {'_0': _rep(matrix.flatten())}
    attachment['translation2'] = _rep(-matrix.T @ parent_frame.origin / 1000)
    raw = bytearray(attachment['_0'])
    code = {'Front Plane': 3, 'Top Plane': 2, 'Right Plane': 1}.get(spec.get('name'), 0)
    struct.pack_into('<I', raw, 2, code)
    attachment['_0'] = bytes(raw)


def _insert_plane(archive, source, parents):
    """Append a reference record and history entry with fresh native identities."""
    seed = 'RP_offset_negative' if source.distance < 0 else 'RP_offset_positive'
    if source.mode == 'mid':
        seed = 'RP_mid'
    elif source.distance == 0:
        seed = 'RP_zero'
    _, donor = swfeatures._load(seed)
    template = next(f for f in donor.features if swtree.name(f) == ('Plane2' if source.mode == 'mid' else 'Plane1'))
    classes = {o.cls.name: o.cls for o in objects([archive.config.root, archive.resolved.root])}
    copier = swtree._Copier({}, classes)
    target = copier.obj(template)
    history = next(f for f in donor.features if f.name == 'moHistoryFolder_c')
    entry = copier.val(next(row for row in history.values['items']
                            if row['_1'].values['id'] == swtree._feature_id(template)))
    highest = max(o.values['id'] for o in objects([archive.config.root, archive.resolved.root])
                  if o.name in ('moNodeName_c', 'moLengthParameter_c', 'moAngleParameter_c')
                  and o.values['id'] != 0xffffffff)
    identities = {}
    for obj in objects([target, entry]):
        if obj.name in ('moNodeName_c', 'moLengthParameter_c', 'moAngleParameter_c'):
            old = obj.values['id']
            if old != 0xffffffff and old not in identities:
                highest += 1
                identities[old] = highest
    for obj in objects([target, entry]):
        if obj.name in ('moNodeName_c', 'moLengthParameter_c', 'moAngleParameter_c',
                        'moHistoryFeatItemData_c') and obj.values['id'] in identities:
            obj.values['id'] = identities[obj.values['id']]
    target.values['name'].values['name'] = source.name
    data = target.values['data'].values
    _frame(data, source.result_plane)
    if 'attachment' in data:
        _reference_frame(data['face'].values, data['attachment'], source.base, *parents[0])
        handle = data['attachment']['dimension'].values['handle'].values
        value = abs(source.distance) / 1000
        handle['dim'].values['param'].values['value'] = value
        handle['value'] = handle['_19'] = value
    else:
        interface = data['interface'].values
        for key, spec, parent in zip(('first', 'second'), (source.base, source.base2), parents):
            reference = interface[key].values['reference'].values
            _reference_frame(reference['entity'].values, reference['frame'][0], spec, *parent)
    target.values['_40'] = float(source.result_plane.origin[2]) / 1000
    archive.resolved.root['features'].append({'_0': target})
    next(f for f in archive.features if f.name == 'moHistoryFolder_c').values['items'].append(entry)
    swtree.renumber(archive, 1, 0, highest + 1000)
    return target, donor.features[-2].values['plane'].values['stamp']


def write(document, path, name):
    """Compose body pairs, insert reference planes and reconnect their sketches."""
    features = document.features
    names = [f.name for f in features]
    if len(set(names)) != len(names) or any(not n or '@' in n for n in names):
        raise UnsupportedFeature('Feature names must be unique, nonempty and contain no @')
    if any(f.TYPE not in ('ref_plane', 'sketch', 'extrude', 'revolve') for f in features):
        raise UnsupportedFeature('Reference-plane histories currently require extrusion/revolve pairs')
    references = {}
    source_sketches = {}
    prepared = []
    for feature in features:
        if feature.TYPE == 'ref_plane':
            if (feature.suppressed or feature.error or feature.mode not in ('offset', 'mid')
                    or feature.flip or not math.isfinite(feature.distance)):
                raise UnsupportedFeature(f'{feature.name}: requires an unflipped offset plane or midplane')
            for spec in ([feature.base, feature.base2] if feature.mode == 'mid' else [feature.base]):
                if spec and spec['type'] == 'std':
                    if spec['name'] not in REFERENCE_PLANES:
                        raise UnsupportedFeature(f'{feature.name}: unknown standard plane')
                elif not spec or spec['type'] != 'feature' or spec.get('id') not in references:
                    raise UnsupportedFeature(f'{feature.name}: requires standard or preceding reference planes')
            references[feature.id] = feature
            continue
        if feature.TYPE == 'sketch' and feature.plane_spec:
            spec = feature.plane_spec
            if spec['type'] == 'face':
                raise UnsupportedFeature('Mixed model-face and reference-plane histories are not yet supported')
            if spec['type'] == 'feature':
                if spec.get('id') not in references:
                    raise UnsupportedFeature(f'{feature.name}: missing or later reference plane')
                source_sketches[feature.id] = feature
                feature = copy.copy(feature)
                feature.plane = REFERENCE_PLANES['Front Plane']
                feature.plane_spec = {'type': 'std', 'name': 'Front Plane'}
        prepared.append(feature)
    body_document = copy.copy(document)
    body_document.features = prepared
    # Build only in a temporary destination. Unsupported body intent must not replace
    # an existing user file before every plane/reference check has completed.
    with tempfile.TemporaryDirectory(prefix='bauyrcad-native-planes-') as temporary:
        intermediate = Path(temporary) / 'body.SLDPRT'
        swfeatures.write(body_document, intermediate, name)
        container, archive = swtree.load(intermediate)
    native = {swtree.name(f): f for f in archive.features}
    default_stamps = {}
    for obj in objects(archive.resolved.root):
        if obj.name == 'moCompRefPlane_c' and obj.values['id'] != 0xffffffff:
            default_stamps[obj.values['id']] = obj.values['stamp']
    # Standard planes share the template's document-creation stamp.
    default_stamp = next(iter(default_stamps.values()))
    by_source = {}
    stamps = {}
    for feature in features:
        if feature.TYPE == 'ref_plane':
            parents = []
            for spec in ([feature.base, feature.base2] if feature.mode == 'mid' else [feature.base]):
                if spec['type'] == 'std':
                    parent = native[spec['name']]
                    stamp = default_stamps.get(swtree._feature_id(parent), default_stamp)
                    parent_frame = REFERENCE_PLANES[spec['name']]
                else:
                    parent = by_source[spec['id']]
                    stamp = stamps[spec['id']]
                    parent_frame = references[spec['id']].result_plane
                parents.append((parent, stamp, parent_frame))
            target, stamp = _insert_plane(archive, feature, parents)
            by_source[feature.id] = target
            stamps[feature.id] = stamp
            native[feature.name] = target
        elif feature.id in source_sketches:
            parent_id = feature.plane_spec['id']
            target = native[feature.name]
            target.values['plane'].values['id'] = swtree._feature_id(by_source[parent_id])
            target.values['plane'].values['stamp'] = stamps[parent_id]
            target.values['plane_tail']['_1'] = 0
            _sketch_frame(target.values['plane_tail'], feature.plane)
    # Keep the original default items, then exactly the user's feature order. Reassign
    # the user sequence numbers after insertion; body atom identities stay unchanged.
    defaults = [f for f in archive.features if swtree.name(f) not in names]
    ordered = [native[n] for n in names]
    archive.resolved.root['features'] = [{'_0': f} for f in defaults + ordered]
    first_seq = max(f.values.get('seq', 101) for f in defaults) + 1
    for index, feature in enumerate(ordered):
        feature.values['seq'] = first_seq + index
    for feature in ordered:
        if feature.name == 'moRefPlane_c':
            data = feature.values['data'].values
            frames = ([data['attachment']] if 'attachment' in data else
                      [obj.values['frame'][0] for obj in objects(data) if obj.name == 'moPlaneRef_w'])
            for frame in frames:
                raw = bytearray(frame['_0'])
                struct.pack_into('<I', raw, 14, feature.values['seq'] - 1)
                frame['_0'] = bytes(raw)
        if feature.name in ('moRevolution_c', 'moRevCut_c'):
            axis = feature.values['axis'].values
            axis['sketch_seq'] = next(f.values['seq'] for f in ordered
                                      if swtree._feature_id(f) == axis['entity'].values['id'])
    header_container, _ = swfeatures._load('M4_front_blind_0')
    container = swfile.Container(container.build({
        key: swfeatures._model_header(header_container.get(key), archive)
        for key in ('Contents/Config-0-ModelHeader', 'Header2')}))
    swtree.save(container, archive, path)
    return {'path': str(path), 'native': True, 'features': names, 'warnings': []}
