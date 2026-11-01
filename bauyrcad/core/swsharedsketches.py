"""Compose native body features that reuse an earlier profile sketch."""
import copy
import tempfile
from pathlib import Path

from . import mfc, swtree, swfeatures, swfile
from .swfeatures import UnsupportedFeature, objects


def write(document, path, name):
    features = document.features
    names = [feature.name for feature in features]
    if len(set(names)) != len(names) or any(not n or '@' in n for n in names):
        raise UnsupportedFeature('Feature names must be unique, nonempty and contain no @')
    sketches, used, duplicates, paired = {}, set(), {}, []
    for feature in features:
        if feature.TYPE == 'sketch':
            sketches[feature.id] = feature
            continue
        if feature.TYPE not in ('extrude', 'revolve'):
            raise UnsupportedFeature(f'{feature.name}: unsupported offline feature {feature.TYPE}')
        if feature.sketch_id not in sketches:
            raise UnsupportedFeature(f'{feature.name}: requires an earlier profile sketch')
        source = sketches[feature.sketch_id]
        if source.plane_spec and source.plane_spec.get('type') != 'std':
            raise UnsupportedFeature(f'{source.name}: shared sketches currently require a standard plane')
        if source.id in used:
            duplicate = copy.copy(source)
            duplicate.id = 'native-shared-' + str(len(paired))
            duplicate.name = 'NativeSharedProfile' + str(len(paired))
            while duplicate.name.casefold() in {name.casefold() for name in names}:
                duplicate.name += '_'
            duplicates[duplicate.name] = source.name
            body = copy.copy(feature)
            body.sketch_id = duplicate.id
            if body.TYPE == 'revolve' and body.axis_sketch_id == source.id:
                body.axis_sketch_id = duplicate.id
            paired.extend((duplicate, body))
        else:
            paired.extend((source, feature))
            used.add(source.id)
    if not paired or set(sketches) != used:
        raise UnsupportedFeature('Standalone sketches are not yet supported offline')
    prepared = copy.copy(document)
    prepared.features = paired
    with tempfile.TemporaryDirectory(prefix='bauyrcad-shared-sketch-') as temporary:
        intermediate = Path(temporary) / 'paired.SLDPRT'
        swfeatures.write(prepared, intermediate, name)
        container, archive = swtree.load(intermediate)
    native = {swtree.name(f): f for f in archive.features}
    replacements = {native[old]: native[new] for old, new in duplicates.items()}
    from . import sworigin
    origin_records = sworigin.reference_ids(archive, list(replacements))
    ids = {swtree._feature_id(old): swtree._feature_id(new) for old, new in replacements.items()}
    seen = set()

    def replace(value):
        if isinstance(value, mfc.Obj):
            if value in replacements:
                return replacements[value]
            if id(value) not in seen:
                seen.add(id(value))
                value.values = replace(value.values)
        elif isinstance(value, mfc.Ref):
            value.target = replacements.get(value.target, value.target)
        elif isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        elif isinstance(value, list):
            return [replace(item) for item in value]
        return value

    defaults = [f for f in archive.features if swtree.name(f) not in names + list(duplicates)]
    ordered = [native[n] for n in names]
    archive.resolved.root['features'] = [{'_0': f} for f in defaults + ordered]
    replace(archive.config.root)
    replace(archive.resolved.root)
    sworigin.prune(archive, origin_records)
    for obj in objects([archive.config.root, archive.resolved.root]):
        if obj.name == 'moCompSketchEntHandle_c' and obj.values['id'] in ids:
            obj.values['id'] = ids[obj.values['id']]
    history = next(f for f in archive.features if f.name == 'moHistoryFolder_c')
    history.values['items'] = [row for row in history.values['items'] if row['_1'].values['id'] not in ids]
    first_seq = max(f.values.get('seq', 101) for f in defaults) + 1
    for index, feature in enumerate(ordered):
        feature.values['seq'] = first_seq + index
    sketch_stamps = {}
    for feature in ordered:
        if feature.name in ('moRevolution_c', 'moRevCut_c'):
            axis = feature.values['axis'].values
            reference = axis['entity'].values
            reference['stamp'] = sketch_stamps.setdefault(reference['id'], reference['stamp'])
            axis['sketch_seq'] = next(f.values['seq'] for f in ordered
                                      if swtree._feature_id(f) == axis['entity'].values['id'])
    header_container, _ = swfeatures._load('M4_front_blind_0')
    container = swfile.Container(container.build({
        key: swfeatures._model_header(header_container.get(key), archive)
        for key in ('Header2', 'Contents/Config-0-ModelHeader')}))
    swtree.save(container, archive, path)
    return {'path': str(path), 'native': True, 'features': names, 'warnings': []}
