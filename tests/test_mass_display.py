"""Physical unit powers and principal-axis directions in human-readable reports."""
import numpy as np
import pytest
from bauyrcad.core import mass_properties as mass
from test_mass_properties import block


def test_mass_report_converts_every_dimension_consistently():
    r = block().set_material(density=1000).mass_properties()
    before = r['inertia'].copy()
    text = mass.report(r, length_unit='cm', mass_unit='g')
    assert 'Volume: 6 cm³' in text
    assert 'Surface area: 22 cm²' in text
    assert 'Mass: 6 g' in text
    assert 'Centre of gravity (world XYZ): 0.5, 1, 1.5 cm' in text
    assert 'Principal moments (g cm²): 2.5, 5, 6.5' in text
    assert 'Axis 1: 0, 0, 1' in text
    assert np.array_equal(r['inertia'], before)
    meters = mass.report(r, length_unit='m', mass_unit='kg')
    assert 'Principal moments (kg m²): 2.5e-07, 5e-07, 6.5e-07' in meters
    imperial = mass.report(r, length_unit='in', mass_unit='lb')
    displayed_mass = float(next(line for line in imperial.splitlines() if line.startswith('Mass:')).split()[1])
    assert displayed_mass == pytest.approx(.006/.45359237, rel=1e-7)
    assert 'Principal moments (lb in²)' in imperial


def test_missing_density_and_invalid_display_units():
    r = block().mass_properties()
    text = mass.report(r, length_unit='cm')
    assert 'Volume: 6 cm³' in text and 'Assign density' in text
    for args in [dict(length_unit='feet'), dict(mass_unit='stone'), dict(precision=0)]:
        with pytest.raises(ValueError):
            mass.report(r, **args)
