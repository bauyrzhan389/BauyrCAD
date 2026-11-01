"""Native external-origin references for supported sketch planes."""
from pathlib import Path
import struct
from functools import lru_cache
from bauyrcad.core import mfc, swtree
from bauyrcad.core.swfeatures import objects


@lru_cache(maxsize=1)
def reference_sketch():
    _, archive = swtree.load(Path(__file__).parents[1] / 'data/sw_seeds/native/SI_origin_circle.SLDPRT')
    return archive.features[-2].values['sketch'].values


def attach(archive, feature, point_index):
    """Anchor a native local point to the part origin's sketch projection."""
    values = feature.values['sketch'].values
    original = reference_sketch()
    classes = {o.cls.name: o.cls for o in objects([archive.config.root, archive.resolved.root])}
    copier = swtree._Copier({}, classes)
    external = copier.val(next(p for p in original['points'] if p['external'] is not None))
    external_index = len(values['points'])
    external['id'] = external_index + 1
    values['points'].append(external)
    values['npts'] += 1
    values['table'][1]['_0'] += 1
    relation = copier.val(next(r for r in original['rel2'] if r['type'] == 9))
    for handle, index in zip(relation['entities'], (external_index, point_index)):
        handle['_0'].values['index'] = index
    values['rel2'].append(relation)
    values['n_rel2'] += 1
    point = values['points'][point_index]
    raw = bytearray(point['base']); raw[17] += 1; point['base'] = bytes(raw)
    raw = bytearray(values['_29'])
    struct.pack_into('<I', raw, 0, struct.unpack_from('<I', raw)[0] + 1)
    values['_29'] = bytes(raw)
    model = archive.config.root['part'].values['model_data'].values
    persistent = max(model['_3'], 10000) + 1
    model['_3'] = persistent
    model['point_records'].insert(0, {'_0': struct.pack('<8I', persistent, 20, 0, 0xffffffff, 1, 0xffffffff, 0, 0)})
    model['_4'] = 0x20000000 | len(model['point_records'])
    origin = next(f for f in archive.features if f.name == 'moOriginProfileFeature_c')
    origin.values['sketch'].values['points'][0]['_1'] = 1
    reference = next(o for o in objects(external) if o.name == 'moCompSketchEntHandle_c')
    reference.values['id'] = swtree._feature_id(origin)
    raw = bytearray(reference.values['_1'])
    struct.pack_into('<I', raw, 3, persistent)
    reference.values['_1'] = bytes(raw)


def reference_ids(archive, features):
    origin = next(f for f in archive.features if f.name == 'moOriginProfileFeature_c')
    return {struct.unpack_from('<I', o.values['_1'], 3)[0] for o in objects(features)
            if o.name == 'moCompSketchEntHandle_c' and o.values['id'] == swtree._feature_id(origin)}


def prune(archive, candidates):
    """Discard persistent point records belonging only to collapsed duplicate profiles."""
    model = archive.config.root['part'].values['model_data'].values
    used = reference_ids(archive, [archive.config.root, archive.resolved.root])
    model['point_records'] = [row for row in model['point_records']
                              if struct.unpack_from('<I', row['_0'])[0] not in candidates - used]
    model['_4'] = (0x20000000 | len(model['point_records'])) if model['point_records'] else 0
