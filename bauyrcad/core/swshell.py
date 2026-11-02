"""Native constant-thickness shells on one polygonal forward extrusion."""
import math
import numpy as np

from . import mfc, swfile, swtree, swsketch
from .features import SketchFeature, ExtrudeFeature, RegenContext
from .features_more import ShellFeature
from .refs import resolver
from .swfeatures import UnsupportedFeature, _load, _model_header, _plane, _profile, objects
from .swedges import _dimension


def write(document, path, name):
    fs = document.features
    if (len(fs) != 3 or not isinstance(fs[0], SketchFeature) or
            not isinstance(fs[1], ExtrudeFeature) or not isinstance(fs[2], ShellFeature)):
        raise UnsupportedFeature('Shell currently requires one sketch, one forward blind extrusion and one Shell')
    sk, ex, shell = fs
    def reject(reason):
        raise UnsupportedFeature(f'{shell.name}: {reason}')
    if any(f.suppressed or f.error for f in fs) or ex.regions or ex.sketch_id != sk.id:
        reject('suppressed, failed or selected-contour feature')
    if ex.operation != 'boss' or ex.end != 'blind' or ex.reverse or ex.flipped:
        reject('requires a forward blind boss')
    if not math.isfinite(ex.depth) or ex.depth <= 0 or not math.isfinite(shell.thickness) or shell.thickness <= 0:
        reject('requires positive finite depth and thickness')
    plane = _plane(sk)
    loops, dims = _profile(sk)
    if len(loops) != 1 or any(segment[0] != 'line' for segment in loops[0]):
        reject('requires a polygonal profile without holes')
    if len(shell.faces) > 1:
        reject('only a closed shell or one removed end-cap face is currently supported')
    cap = None
    if shell.faces:
        ctx = RegenContext()
        for feature in (sk, ex):
            ctx.features[feature.id] = feature
            feature.execute(ctx)
        r = resolver(ctx)
        face = r.faces[r.face(shell.faces[0])]
        normal = np.asarray(face.normal)
        if np.allclose(normal, sk.plane.normal, atol=1e-7, rtol=0):
            cap = 1
        elif np.allclose(normal, -sk.plane.normal, atol=1e-7, rtol=0):
            cap = 0
        else:
            reject('only an end-cap face can be removed; side-face shells are not yet supported')
    container, arc = _load('SH_closed' if cap is None else 'SH_open')
    target_sk, target_ex, target = arc.features[-3:]
    for native, source in zip((target_sk, target_ex, target), fs):
        native.values['name'].values['name'] = source.name
    arc.config.root['part'].values['name'].values['name'] = name
    classes = {o.cls.name: o.cls for o in objects([arc.config.root, arc.resolved.root])}
    _, donor = _load(f'M4_{plane}_blind_0')
    cp = swtree._Copier({}, classes)
    target_sk.values['plane'] = cp.obj(donor.features[-2].values['plane'])
    target_sk.values['plane_tail'] = cp.val(donor.features[-2].values['plane_tail'])
    swsketch.write(arc, target_sk, sk, loops, dims)
    _dimension(target_ex.values['end_spec'].values['dim'], ex.depth / 1000)
    _dimension(target.values['thickness'], shell.thickness / 1000)
    target.values['outward'] = int(shell.outward)
    if cap is not None:
        ref = next(o for o in objects(target.values['faces']) if o.name == 'moFaceRef_c')
        ids = ref.values['ids']
        ids[0]['_0'].values['_2'] = cap
        template = ids[1]['_0']
        count = len(loops[0])
        order = [count] + list(range(1, count)) if cap else list(range(count, 0, -1))
        # Preserve parent ownership while allocating each boundary reference separately.
        parents = {id(o): o for o in objects(template) if o.name == 'moExtObject_c'}
        boundary = []
        for index in order:
            item = swtree._Copier(parents, classes).obj(template)
            item.values['segment'] = index
            boundary.append({'_0': item})
        ref.values['ids'] = ids[:1] + boundary + [{'_0': None}]
        ref.values['n'] = len(ref.values['ids'])
    base, _ = _load('M4_empty')
    base = swfile.Container(base.build({key: _model_header(container.get(key), arc)
                                      for key in ('Header2', 'Contents/Config-0-ModelHeader')}))
    swtree.save(base, arc, path)
    return dict(path=str(path), native=True, features=[f.name for f in fs], warnings=[])
