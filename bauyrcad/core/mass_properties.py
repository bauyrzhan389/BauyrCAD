"""Physical mass properties: kg, mm, kg mm²; density is specified in kg/m³."""
from __future__ import annotations

import numpy as np

from . import geometry


def material(name=None, density=None):
    """Return a fresh physical material assignment; no arguments clears it."""
    if name is None and density is None:
        return None
    if density is None:
        from ..fea.study import MATERIAL_LIBRARY
        if name not in MATERIAL_LIBRARY:
            raise ValueError(f'Unknown material: {name}')
        density = MATERIAL_LIBRARY[name].density
    density = float(density)
    if not np.isfinite(density) or density <= 0:
        raise ValueError('Density must be a finite positive value in kg/m³')
    return dict(name=str(name or 'Custom'), density=density)


def assignment(value):
    return material(value.get('name'), value.get('density')) if value is not None else None


def parallel_axis(mass, offset):
    d = np.asarray(offset, float)
    return mass * ((d @ d)*np.eye(3) - np.outer(d, d))


def finish(result, mass, center, inertia):
    inertia = (inertia + inertia.T) / 2
    moments, axes = np.linalg.eigh(inertia)
    # Consistent signs and a right-handed frame; repeated eigenvalues have no unique axes.
    for i in range(3):
        if axes[np.argmax(np.abs(axes[:, i])), i] < 0:
            axes[:, i] *= -1
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    result.update(mass=float(mass), center_of_gravity=np.asarray(center), inertia=inertia,
                  inertia_origin=inertia + parallel_axis(mass, center),
                  principal_moments=moments, principal_axes=axes)
    return result


def part(shape, physical_material=None):
    result = geometry.solid_mass_geometry(shape)
    mat = assignment(physical_material)
    result.update(material=mat, mass=None, center_of_gravity=None, inertia=None,
                  inertia_origin=None, principal_moments=None, principal_axes=None,
                  missing_density=[] if mat else ['Part'])
    if mat:
        scale = mat['density'] * 1e-9  # kg/m³ -> kg/mm³
        finish(result, result['volume']*scale, result['centroid'], result['volume_inertia']*scale)
    return result


def assembly(asm):
    """Sum distinct component instances (including overlaps); never fuse their volumes."""
    rows = []
    if not asm.components:
        raise ValueError('The assembly has no components')
    for comp in asm.components:
        if comp.shape is None or comp.load_error:
            raise ValueError(f'{comp.name}: component geometry is unavailable')
        mat = comp.mass_material or (comp.doc.mass_material if comp.doc is not None else None)
        row = part(comp.shape, mat)
        row.update(id=comp.id, name=comp.name)
        row['centroid'] = comp.pose.point(row['centroid'])
        row['volume_inertia'] = comp.pose.R @ row['volume_inertia'] @ comp.pose.R.T
        if row['mass'] is not None:
            finish(row, row['mass'], row['centroid'], comp.pose.R @ row['inertia'] @ comp.pose.R.T)
        rows.append(row)
    volume = sum(r['volume'] for r in rows)
    result = dict(volume=volume, area=sum(r['area'] for r in rows),
                  centroid=sum(r['volume']*r['centroid'] for r in rows)/volume,
                  components=rows, missing_density=[r['name'] for r in rows if r['mass'] is None],
                  mass=None, center_of_gravity=None, inertia=None, inertia_origin=None,
                  principal_moments=None, principal_axes=None)
    if not result['missing_density']:
        mass = sum(r['mass'] for r in rows)
        center = sum(r['mass']*r['centroid'] for r in rows)/mass
        inertia = sum(r['inertia'] + parallel_axis(r['mass'], r['centroid']-center) for r in rows)
        finish(result, mass, center, inertia)
    return result


def report(result, *, length_unit='mm', mass_unit='kg', precision=8):
    """Format physical properties; input values remain mm, kg and kg mm²."""
    from .measure import UNIT_FACTORS
    mass_factors = {'kg': 1.0, 'g': 1000.0, 't': .001, 'lb': 1/.45359237}
    if length_unit not in UNIT_FACTORS or mass_unit not in mass_factors:
        raise ValueError('Choose mm, cm, m or in; and kg, g, t or lb')
    if not isinstance(precision, int) or not 3 <= precision <= 12:
        raise ValueError('Choose 3–12 significant digits')
    length_scale, mass_scale = UNIT_FACTORS[length_unit], mass_factors[mass_unit]
    inertia_scale = mass_scale * length_scale**2
    inertia_unit = f'{mass_unit} {length_unit}²'
    def number(value):
        return f'{value:.{precision}g}'
    def vector(values, scale=1.0):
        values = np.asarray(values) * scale
        tolerance = max(abs(x) for x in values) * 1e-12
        return ', '.join(number(0.0 if abs(x) <= tolerance else x) for x in values)
    lines = [f"Volume: {number(result['volume'] * length_scale**3)} {length_unit}³",
             f"Surface area: {number(result['area'] * length_scale**2)} {length_unit}²",
             f"Volume centroid (world XYZ): {vector(result['centroid'], length_scale)} {length_unit}"]
    if 'components' in result:
        lines.append('Assembly totals sum component instances; overlaps and contact surfaces are counted.')
        for row in result['components']:
            mat = row['material']
            lines.append(f"{row['name']}: " +
                         (f"{mat['name']}, {mat['density']:g} kg/m³, {number(row['mass'] * mass_scale)} {mass_unit}"
                          if mat else 'density unassigned'))
    elif result.get('material'):
        mat = result['material']
        lines.append(f"Material: {mat['name']} — {mat['density']:g} kg/m³")
    if result['mass'] is None:
        lines.append('Assign density to calculate mass, centre of gravity and inertia: ' + ', '.join(result['missing_density']))
        return '\n'.join(lines)
    lines += [f"Mass: {number(result['mass'] * mass_scale)} {mass_unit}",
              f"Centre of gravity (world XYZ): {vector(result['center_of_gravity'], length_scale)} {length_unit}"]
    for key, label in [('inertia', 'Inertia about centre of gravity'), ('inertia_origin', 'Inertia about world origin')]:
        lines += [f'{label} (world XYZ, {inertia_unit}):'] + [f'  {vector(row, inertia_scale)}' for row in result[key]]
    lines += ['Tensor off-diagonals are negative products of inertia.',
              f'Principal moments ({inertia_unit}): ' + vector(result['principal_moments'], inertia_scale),
              'Principal axes (unit vectors in world XYZ; ascending moments):']
    lines += [f'  Axis {i+1}: {vector(axis)}' for i, axis in enumerate(result['principal_axes'].T)]
    lines.append('Equal principal moments have non-unique principal-axis directions.')
    return '\n'.join(lines)
