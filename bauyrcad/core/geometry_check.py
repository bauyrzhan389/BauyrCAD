"""Complete solid-suitability checks for documents and assembly component instances."""
from . import geometry


def part(doc):
    result = geometry.check_geometry(doc.body)
    errors = [f'{f.name}: {f.error}' for f in doc.features if f.error and not f.suppressed]
    if errors:
        result['complete'] = False
        result['issues'].extend('Feature failed: ' + error for error in errors)
    return result


def assembly(asm, progress=None):
    components = list(asm.components)
    rows = []
    if not components:
        return dict(complete=False, ok=False, components=[], issues=['The assembly has no components'])
    for index, comp in enumerate(components):
        if progress is not None:
            progress(index, len(components))
        result = geometry.check_geometry(comp.shape)
        if comp.load_error:
            result['complete'] = False
            result['issues'].append('Component load failed: ' + comp.load_error)
        if comp.doc is not None:
            errors = [f'{f.name}: {f.error}' for f in comp.doc.features if f.error and not f.suppressed]
            if errors:
                result['complete'] = False
                result['issues'].extend('Feature failed: ' + error for error in errors)
        result.update(id=comp.id, name=comp.name)
        rows.append(result)
    if progress is not None:
        progress(len(components), len(components))
    return dict(complete=all(row['complete'] for row in rows),
                ok=all(row['complete'] and row['closed_solid'] for row in rows), components=rows, issues=[])


def report(result):
    rows = result.get('components', [dict(result, name='Part')])
    complete = result['complete']
    ok = complete and (result['ok'] if 'components' in result else result['closed_solid'])
    lines = ['CHECK INCOMPLETE' if not complete else 'VALID CLOSED SOLID GEOMETRY' if ok else 'GEOMETRY ISSUES FOUND']
    for row in rows:
        state = 'incomplete' if not row['complete'] else 'valid closed solid' if row['closed_solid'] else 'issues found'
        lines += [f"\n{row['name']}: {state}",
                  f"Solids: {row['solids']} | Faces: {row['faces']} | Edges: {row['edges']} | Vertices: {row['vertices']}"]
        if row['volume'] is not None:
            lines.append(f"Volume: {row['volume']:.8g} mm³")
        lines.extend('- ' + issue for issue in row['issues'])
    if 'components' in result:
        lines.extend(result['issues'])
        lines.append('\nAll component instances are checked, including hidden components.')
    lines.append('Checks kernel validity and closed solid suitability; does not check interference, wall thickness or manufacturability.')
    return '\n'.join(lines)
