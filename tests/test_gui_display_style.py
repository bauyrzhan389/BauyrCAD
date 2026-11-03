"""Shaded styles persist across model rebuilds without exposing hidden components."""
import pytest
from PySide6.QtCore import QSettings
from test_gui_views_input import app, win, pump, plate


def test_shaded_part_rebuild_section_and_results_outline(app, win):
    from bauyrcad import APP_NAME
    from bauyrcad.gui import appearance
    settings=QSettings(APP_NAME,APP_NAME)
    existed=settings.contains(appearance.DISPLAY_SETTING)
    previous=settings.value(appearance.DISPLAY_SETTING)
    try:
        win._set_document(plate().doc)
        action=next(a for a in win.display_actions.actions() if a.data()=='shaded')
        action.trigger()
        assert not win.viewer.plotter.actors['body_edges'].GetVisibility()
        assert appearance.current_display_style()=='shaded'
        win.rebuild(mark_dirty=False)
        win.show_section()
        assert win.viewer.section.caps and not win.viewer.plotter.actors['body_edges'].GetVisibility()
        win.grab().save('projects/overnight_evaluate/shaded-root.png')
        edges=win.viewer.plotter.actors['body_edges']
        win.viewer.results_visible=True
        edges.SetVisibility(True)
        win.viewer.render()
        assert edges.GetVisibility()  # results own the explicit undeformed outline
        win.viewer.results_visible=False
        win.viewer.render()
        assert not edges.GetVisibility()
        appearance.set_display_style('shaded_edges')
        assert edges.GetVisibility() and not win.dirty
        with pytest.raises(ValueError): appearance.set_display_style('wireframe')
    finally:
        win.viewer.results_visible=False
        appearance.set_display_style(previous if previous in appearance.DISPLAY_STYLES else 'shaded_edges')
        if existed: settings.setValue(appearance.DISPLAY_SETTING,previous)
        else: settings.remove(appearance.DISPLAY_SETTING)


def test_shared_assembly_style_and_hidden_edges(app, win, tmp_path):
    from bauyrcad import APP_NAME
    from bauyrcad.gui import appearance
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from test_interference_evaluate import assembly
    original=appearance.current_display_style()
    settings=QSettings(APP_NAME,APP_NAME)
    existed=settings.contains(appearance.DISPLAY_SETTING)
    previous=settings.value(appearance.DISPLAY_SETTING)
    a, first, second=assembly(tmp_path)
    w=AssemblyWindow(assembly=a.asm); w.show()
    try:
        pump(app)
        w.visibility.toggle(second.comp.id)
        appearance.set_display_style('shaded')
        assert all(not e.GetVisibility() for actor, mesh, e in w.viewer._asm.values())
        w.refresh()
        assert all(not e.GetVisibility() for actor, mesh, e in w.viewer._asm.values())
        appearance.set_display_style('shaded_edges')
        assert w.viewer._asm[first.comp.id][2].GetVisibility()
        assert not w.viewer._asm[second.comp.id][2].GetVisibility()
        assert win.display_actions.checkedAction().data()=='shaded_edges'
        w.close()
        appearance.set_display_style('shaded')  # closed viewers are ignored
    finally:
        w.dirty=False; w.close()
        appearance.set_display_style(original)
        if existed: settings.setValue(appearance.DISPLAY_SETTING,previous)
        else: settings.remove(appearance.DISPLAY_SETTING)
