"""Preserve native sketch entities, shared points, construction geometry and relations."""
import copy
import struct
from pathlib import Path
from functools import lru_cache
import numpy as np
from . import swtree, mfc
from .swfeatures import UnsupportedFeature
from .sketch.profiles import LineSeg, ArcSeg, CircleSeg

CODES = {
    'horizontal': 4, 'vertical': 5, 'tangent': 6, 'parallel': 7,
    'perpendicular': 8, 'coincident': 9, 'concentric': 10,
    'midpoint': 12, 'equal': 14, 'symmetric': 11, 'collinear': 27, 'fix': 17,
}


def relation_code(constraint):
    if constraint.type in ('horizontal', 'vertical') and len(constraint.refs) == 2:
        return 25 if constraint.type == 'horizontal' else 26
    return CODES[constraint.type]


@lru_cache(maxsize=1)
def _templates():
    templates = {}
    for name in ('center_rect', 'concentric', 'para', 'polygon', 'slot', 'three_rect',
                 'equal_circles', 'horizontal_points', 'vertical_points', 'point_on_line', 'symmetric', 'collinear',
                 'fixed_point', 'fixed_line'):
        _, donor = swtree.load(Path(__file__).parents[1] / 'data' / 'sw_seeds' / 'native' / f'SI_{name}.SLDPRT')
        templates[name] = donor.features[-2].values['sketch'].values
    return templates


def _apply(arc, feature, source):
    """Remap profile identities, append construction entities, then write relations.

    Native segment order follows the contour walk, not source insertion order. Keep
    an explicit source-to-native mapping so construction and relation handles survive
    contour reversal and shared-point compaction.
    """
    v=feature.values['sketch'].values
    templates = _templates()
    relations = [r for sk in templates.values() for i in range(1, 4) for r in sk['rel'+str(i)]]
    point_template=copy.copy(v['points'][0])
    segments=[l['segs'] for l in v['seglists']]
    entities={};point_candidates={}
    close=lambda a,b:np.allclose(a,b,atol=1e-7,rtol=0)
    xy=lambda i:np.array([v['points'][i]['x'],v['points'][i]['y']])*1000
    def remember(pid,index):point_candidates.setdefault(pid,[]).append(index)
    for eid,e in source.entities.items():
        if e.construction or e.kind=='point':continue
        matches=[]
        for i,t in enumerate(segments[0 if e.kind=='line' else 2]):
            if e.kind=='line':
                ends=(e.p1,e.p2)
                ok=(close(source.pt(ends[0]),xy(t['p0'])) and close(source.pt(ends[1]),xy(t['p1']))) or (close(source.pt(ends[0]),xy(t['p1'])) and close(source.pt(ends[1]),xy(t['p0'])))
            else:
                ci=t['arc_data']['centre'];radius=np.linalg.norm(xy(t['p0'])-xy(ci))
                r=e.r if e.kind=='circle' else np.linalg.norm(source.pt(e.start)-source.pt(e.center))
                ok=close(source.pt(e.center),xy(ci)) and abs(radius-r)<1e-7 and ((t['p0']==t['p1'])==(e.kind=='circle'))
                if ok and e.kind == 'arc':
                    first, last = (e.start, e.end)
                    native_first, native_last = (t['p1'], t['p0']) if t['arc'] == 0xffffffff else (t['p0'], t['p1'])
                    ok = close(source.pt(first), xy(native_first)) and close(source.pt(last), xy(native_last))
            if ok:matches.append((i,t))
        if len(matches)!=1:raise UnsupportedFeature(f'{swtree.name(feature)}: entity {eid} does not identify one native segment')
        i,t=matches[0];entities[eid]=('line' if e.kind=='line' else 'arc',i)
        if e.kind!='line':remember(e.center,t['arc_data']['centre'])
        ends=(e.p1,e.p2) if e.kind=='line' else (e.start,e.end) if e.kind=='arc' else ()
        for pid in ends:
            remember(pid,next(t[k] for k in ('p0','p1') if close(source.pt(pid),xy(t[k]))))
    # Merge only points sharing the same source identity, never merely nearby coordinates.
    parent=list(range(len(v['points'])))
    def root(i):
        while parent[i]!=i:i=parent[i]
        return i
    # Explicit point-point coincidences use one native shared point.
    for constraint in source.constraints:
        if constraint.value is None and constraint.type == 'coincident' and all(k == 'p' for k, _ in constraint.refs):
            first, second = (i for _, i in constraint.refs)
            ids = point_candidates.get(first, []) + point_candidates.get(second, [])
            if ids:
                point_candidates[first] = point_candidates[second] = ids
    for ids in point_candidates.values():
        for i in ids[1:]:parent[root(i)]=root(ids[0])
    old_to_new={};kept=[]
    for i,p in enumerate(v['points']):
        r=root(i)
        if r not in old_to_new:old_to_new[r]=len(kept);kept.append(copy.copy(v['points'][r]))
        old_to_new[i]=old_to_new[r]
    points={pid:old_to_new[root(ids[0])] for pid,ids in point_candidates.items()}
    for row in segments:
        for t in row:
            for k in ('p0','p1'):t[k]=old_to_new[t[k]]
            if t['arc_data']:t['arc_data']['centre']=old_to_new[t['arc_data']['centre']]
    v['points']=kept
    def add_point(at,a=0):
        p=copy.copy(point_template);base=bytearray(p['base']);base[17]=0
        p.update(base=bytes(base),id=len(kept)+1,x=float(at[0])/1000,y=float(at[1])/1000,a=a,b=0,c=0,seg0=None,seg1=None)
        kept.append(p);return len(kept)-1
    def source_point(pid):
        if pid not in points:points[pid]=add_point(source.pt(pid))
        return points[pid]
    for eid,e in source.entities.items():
        if e.kind=='point':
            index=source_point(e.point_ids()[0]);kept[index]['a']=1;entities[eid]=('point',index)
        elif e.construction:
            kind='line' if e.kind=='line' else 'arc'
            row=segments[0 if kind=='line' else 2];index=len(row)
            ts=next(t for l in templates['center_rect' if kind=='line' else 'polygon']['seglists'] for t in l['segs'] if t['_0']==0 and bool(t['arc_data'])==(kind=='arc'))
            t=copy.deepcopy(ts);t.update(_0=0,id=index+1)
            if kind=='line':
                t.update(p0=source_point(e.p1),p1=source_point(e.p2),arc=0)
                if row:row[-1]['arc']|=2<<16
                else:v['seglists'][0]['_1']=2
            else:
                assert e.kind=='circle'
                ci=source_point(e.center);rim=add_point(source.pt(e.center)+[e.r,0],a=2)
                t.update(p0=rim,p1=rim,arc=1);t['arc_data']['centre']=ci
            row.append(t);entities[eid]=(kind,index)
    for constraint in source.constraints:
        for kind, point_id in constraint.refs:
            if kind == 'p':
                source_point(point_id)
    # Incidence counts include construction geometry and shared arc centers.
    for i,p in enumerate(kept):
        p['id']=i+1;p['b']=p['c']=0
        b=bytearray(p['base']);b[17]=0;p['base']=bytes(b)
    for row in segments:
        for t in row:
            for k in ('p0','p1'):kept[t[k]]['b']+=1
            if t['arc_data']:
                p=kept[t['arc_data']['centre']];p['b']+=1;p['c']+=1
            b=bytearray(t['base']);b[17]=0;t['base']=bytes(b)
    for i in range(1,9):v['rel'+str(i)]=[];v['n_rel'+str(i)]=0
    from .sketch.model import Constraint
    fixed_by_relation = {pid for c in source.constraints if c.type == 'fix'
                         for typ, index in c.refs
                         for pid in ([index] if typ == 'p' else source.entities[index].point_ids())}
    constraints = list(source.constraints) + [Constraint(-pid, 'fix', [('p', pid)])
        for pid, point in source.points.items() if pid and point.fixed and pid not in fixed_by_relation and pid in points]
    # SolidWorks fixes a supporting line/circle; BauyrCAD also fixes segment ends.
    for constraint in source.constraints:
        if constraint.type == 'fix' and constraint.refs[0][0] == 'e':
            entity = source.entities[constraint.refs[0][1]]
            ends = ((entity.start, entity.end) if entity.kind == 'arc' else
                    (entity.p1, entity.p2) if entity.kind == 'line' else ())
            already = {c.refs[0][1] for c in constraints if c.type == 'fix' and c.refs[0][0] == 'p'}
            constraints.extend(Constraint(-pid, 'fix', [('p', pid)]) for pid in ends if pid and pid not in already)
    for c in constraints:
        if c.value is not None or (c.type=='coincident' and all(k=='p' for k,_ in c.refs)):continue
        code=relation_code(c)
        refs=[('point',points[i]) if typ=='p' else entities[i] for typ,i in c.refs]
        if code in (4,5):template=swtree._relation(arc,code,refs[0][1])
        else:
            template=next(r for r in relations if r['type']==code and len(r['entities'])==len(refs))
            if code==6:refs.sort(key=lambda x:x[0]=='line')
            if code in (9,12):refs.sort(key=lambda x:x[0]=='point')
        row=copy.copy(template)
        row['entities']=[]
        for kind,index in refs:
            handle=mfc.Obj(swtree._class(arc,{'line':'sgLineHandle','arc':'sgArcHandle','point':'sgPointHandle'}[kind]))
            handle.values={'index':index,'_1':-1,'_2':0}
            row['entities'].append({'_0':handle})
            obj=kept[index] if kind=='point' else segments[0 if kind=='line' else 2][index]
            b=bytearray(obj['base']);b[17]+=1;obj['base']=bytes(b)
        v['rel'+str(len(refs))].append(row)
    for i in range(1,9):v['n_rel'+str(i)]=len(v['rel'+str(i)])
    for i,row in enumerate(segments):v['seglists'][i]['n']=len(row)
    v['npts']=len(kept)
    n=sum(len(row) for row in segments);nc=sum(e.construction for e in source.entities.values())
    v['_29']=struct.pack('<I',n+nc+sum(v['n_rel'+str(i)] for i in range(1,9))+1)+v['_29'][4:]
    for i,val in enumerate((n-nc+1,len(kept)+1,len(segments[0])+1,len(segments[2])+1)):v['table'][i]['_0']=val
    if 0 in points:
        from .sworigin import attach
        attach(arc, feature, points[0])
    feature.values['axis']=None
    return entities



def profile(feature, axis_id=None):
    """Validate the complete sketch intent before writing any output."""
    sk = feature.sketch
    fail = lambda message: UnsupportedFeature(f'{feature.name}: {message}')
    if sk.texts:
        raise fail('sketch text is not yet supported offline')
    if any(e.kind not in ('line', 'arc', 'circle', 'point') or
           (e.construction and e.kind not in ('line', 'circle')) for e in sk.entities.values()):
        raise fail('spline or unsupported construction geometry')
    origin_used = (any(0 in entity.point_ids() for entity in sk.entities.values()) or
                   any(('p', 0) in constraint.refs for constraint in sk.constraints))
    if origin_used and feature.plane_spec and feature.plane_spec.get('type') == 'face':
        raise fail('external sketch origin on a model face is not yet supported offline')
    regions = sk.regions()
    if len(regions) != 1:
        raise fail('requires one outer profile, with optional holes')
    loops = []
    for loop in [regions[0].outer] + regions[0].holes:
        row = []
        for segment in loop.oriented(True).segments:
            if isinstance(segment, LineSeg):
                row.append(('line', tuple(segment.a), tuple(segment.b)))
            elif isinstance(segment, CircleSeg):
                row.append(('circle', tuple(segment.center), segment.radius))
            elif isinstance(segment, ArcSeg):
                row.append(('arc', tuple(segment.center), tuple(segment.a), tuple(segment.b), segment.ccw))
            else:
                raise fail('spline in a profile')
        loops.append(row)
    profile_points = {0} | {pid for entity in sk.entities.values()
                      if not entity.construction and entity.kind != 'point' for pid in entity.point_ids()}
    entity_points = {0} | {pid for entity in sk.entities.values() for pid in entity.point_ids()}
    dimensions = []
    for constraint in sk.constraints:
        refs = constraint.refs
        if any(kind not in ('e', 'p') or (kind == 'p' and index not in entity_points) for kind, index in refs):
            raise fail('relation requiring an external origin anchor')
        kinds = ['point' if kind == 'p' else sk.entities[index].kind for kind, index in refs]
        if constraint.value is not None:
            if not constraint.driving or len(refs) != 1 or refs[0][0] != 'e':
                raise fail('reference or multi-entity dimension')
            entity = sk.entities[refs[0][1]]
            if entity.kind == 'line' and constraint.type == 'distance':
                kind = 'length'
            elif entity.kind == 'circle' and constraint.type == 'diameter':
                kind = 'diameter'
            elif entity.kind in ('circle', 'arc') and constraint.type == 'radius':
                kind = 'radius'
            else:
                raise fail(f'dimension {constraint.type} on {entity.kind}')
            dimensions.append((kind, entity.id))
            continue
        typ = constraint.type
        valid = False
        if typ in ('horizontal', 'vertical'):
            valid = kinds in (['line'], ['point', 'point'])
        elif typ in ('parallel', 'perpendicular', 'collinear'):
            valid = kinds == ['line', 'line']
        elif typ == 'tangent':
            valid = sorted(kinds) == ['arc', 'line']
        elif typ == 'equal':
            valid = kinds in (['line', 'line'], ['arc', 'arc'], ['circle', 'circle'])
        elif typ == 'concentric':
            valid = kinds == ['circle', 'circle']
        elif typ == 'midpoint':
            valid = sorted(kinds) == ['line', 'point']
        elif typ == 'symmetric':
            valid = kinds == ['point', 'point', 'line']
        elif typ == 'fix':
            valid = len(kinds) == 1 and kinds[0] in ('line', 'arc', 'circle', 'point')
        elif typ == 'coincident':
            valid = sorted(kinds) in (['circle', 'point'], ['line', 'point'])
            if all(kind == 'p' for kind, _ in refs) and len(refs) == 2:
                valid = all(index in profile_points for _, index in refs) and np.allclose(sk.pt(refs[0][1]), sk.pt(refs[1][1]), atol=1e-6, rtol=0)
        if not valid:
            raise fail(f'unsupported {typ} relation on {", ".join(kinds)}')
    return loops, dimensions


def write(arc, target, source, loops, dimensions, axis_id=None):
    """Write profile and intent together; return native handles by source entity id."""
    swtree.set_profile(arc, target, loops, hv=False)
    entities = _apply(arc, target, source.sketch)
    for kind, entity_id in dimensions:
        swtree.add_dimension(arc, target, kind, entities[entity_id][1])
    if axis_id is not None:
        target.values['axis'] = swtree._handle(arc, 'line', entities[axis_id][1])
    return entities

