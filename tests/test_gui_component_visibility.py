"""View-only isolation preserves models while excluding hidden topology from inspection."""
import json
import numpy as np
import pytest
from PySide6.QtCore import Qt
from test_gui_views_input import app, pump
from test_interference_evaluate import assembly


def test_visibility_isolation_caps_measure_and_restore(app, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    a, first, second = assembly(tmp_path)
    second.move(dx=25)
    third = a.add(tmp_path/'cube.bcad', at=(60, 0, 0))
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        snapshot = json.dumps(a.asm.to_dict())
        w.show_section()
        assert len(w.viewer.section.caps) == 3
        w._select_component(third.comp.id)
        w.a_hide.trigger()
        assert not w.visibility.visible(third.comp.id)
        assert len(w.viewer.section.caps) == 2
        measure = w.start_measure()
        measure.show_dimensions()
        assert np.allclose(measure.result['size'], (40, 10, 10))
        assert 'visible components' in measure.text.toPlainText()
        assert np.max(measure.vertices()[:, 0]) == pytest.approx(40)
        w._select_component(first.comp.id)
        w.a_isolate.trigger()
        assert w.visibility.isolated == first.comp.id
        assert len(w.viewer.section.caps) == 1
        w.grab().save('projects/overnight_evaluate/visibility-root.png')
        assert measure.result is None
        measure.show_dimensions()
        assert np.allclose(measure.result['size'], (10, 10, 10))
        w.refresh()
        assert w.visibility.isolated == first.comp.id
        assert len(w.viewer.section.caps) == 1
        w.a_isolate.trigger()
        assert w.visibility.isolated is None and not w.visibility.visible(third.comp.id)
        assert len(w.viewer.section.caps) == 2
        assert not w.dirty and json.dumps(a.asm.to_dict()) == snapshot
        w.a_show_all.trigger()
        assert len(w.viewer.section.caps) == 3
        root = w.tree.topLevelItem(0)
        assert all(not root.child(i).font(0).italic() for i in range(3))
        # Every hidden component is still included in mass (density may be unassigned).
        from bauyrcad.core import mass_properties
        w.visibility.toggle(third.comp.id)
        assert mass_properties.assembly(a.asm)['volume'] == pytest.approx(3000)
        from bauyrcad.core import geometry
        assert geometry.volume(w.start_measure().body) == pytest.approx(2000)
        w.viewer.set_view('front', animate=False)
        x, y = w.viewer.world_to_display((65, 5, 5))[:2]
        assert w.viewer.pick(x, y).kind is None
    finally:
        w.dirty = False; w.close()


def test_zoom_selected_reveals_component_and_missing_bbox(app, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    a, first, second = assembly(tmp_path)
    second.move(dx=1000)
    w = AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        w._select_component(second.comp.id)
        w.a_hide.trigger()
        w.a_zoom_selected.trigger()
        assert w.visibility.visible(second.comp.id)
        assert np.allclose(w.viewer.renderer.GetActiveCamera().GetFocalPoint(), (1010, 5, 5))
        assert w.viewer.renderer.GetActiveCamera().GetParallelScale() < 20
        menu = w._component_menu(second.comp.id)
        assert {'Hide', 'Isolate', 'Zoom to Selected', 'Show All Components'} <= {a.text() for a in menu.actions()}
        second.comp.shape = None
        w.refresh()
        mw = w.start_measure(); mw.show_dimensions()
        assert mw.result is None and 'incomplete' in mw.text.toPlainText()
        w.visibility.toggle(second.comp.id)
        mw.show_dimensions()
        assert np.allclose(mw.result['size'], (10, 10, 10))
        w.file_new()
        assert not w.visibility.hidden and w.visibility.isolated is None
    finally:
        w.dirty = False; w.close()
