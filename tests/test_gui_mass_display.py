"""Mass display controls do not change geometry/materials or recompute integration."""
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from test_gui_views_input import app, win, pump
from test_mass_properties import block
from bauyrcad.core import mass_properties as mass


def test_units_and_principal_axes(app, win, monkeypatch):
    win._set_document(block().set_material(density=1000).doc)
    pump(app)
    window = win.show_mass_properties()
    original = window.result
    def no_recalculation(*a, **kw):
        raise AssertionError('Changing display units must not recompute geometry')
    monkeypatch.setattr(mass, 'part', no_recalculation)
    window.length_unit.setCurrentText('cm')
    window.mass_unit.setCurrentText('g')
    window.precision.setValue(6)
    assert window.result is original
    assert 'Mass: 6 g' in window.text.toPlainText()
    assert 'Principal moments (g cm²): 2.5, 5, 6.5' in window.text.toPlainText()
    QTest.mouseClick(window.axes, Qt.LeftButton, pos=QPoint(8, window.axes.height()//2))
    v = win.viewer
    for i in range(3):
        actor = v.plotter.actors[f'mass_axis_{i}_lines']
        points = np.asarray(actor.GetMapper().GetInput().points)
        direction = points[1] - points[0]
        direction /= np.linalg.norm(direction)
        assert np.allclose(direction, original['principal_axes'][:, i])
        assert np.allclose(points.mean(axis=0), original['center_of_gravity'])
    v.fit()
    pump(app)
    target = Path('projects/overnight_evaluate'); target.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(target/'mass-units.png'))
    win.grab().save(str(target/'mass-axes.png'))
    window.close()
    assert all(f'mass_axis_{i}_lines' not in v.plotter.actors for i in range(3))
