"""Shared display preferences and section-aware controls/orbit in real windows."""
import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from test_gui_views_input import app, win, pump, plate, activate


def test_backgrounds_shared_persistent_and_restored(app, win):
    from bauyrcad import APP_NAME
    from bauyrcad.gui import appearance
    from bauyrcad.gui.assembly_window import AssemblyWindow
    settings = QSettings(APP_NAME, APP_NAME)
    existed = settings.contains(appearance.SETTING)
    previous = settings.value(appearance.SETTING)
    assembly = AssemblyWindow(); assembly.show()
    try:
        for style in appearance.STYLES:
            action = next(a for a in win.background_actions.actions() if a.data() == style)
            action.trigger()
            pump(app)
            assert appearance.current_style() == style
            for window in (win, assembly):
                assert window.viewer.background_style == style
                assert window.background_actions.checkedAction().data() == style
                assert bool(window.viewer.renderer.GetGradientBackground()) == (style != 'white')
        assembly.close()
        appearance.set_style('white')  # stale Qt wrappers cannot break other windows
        assert np.allclose(win.viewer.renderer.GetBackground(), (1, 1, 1))
        with pytest.raises(ValueError):
            appearance.set_style('unknown')
    finally:
        assembly.dirty = False; assembly.close()
        appearance.set_style(previous if previous in appearance.STYLES else 'white')
        if existed:
            settings.setValue(appearance.SETTING, previous)
        else:
            settings.remove(appearance.SETTING)


def test_section_toggle_escape_and_orbit(app, win):
    win._set_document(plate().doc)
    v = win.viewer
    v.set_view('front', animate=False)
    pump(app)
    win.a_section.trigger()
    assert win.a_section.isChecked() and v.section.active
    x, y = v.world_to_display((10, 10, 5))[:2]
    assert v.rotation_pivot(x, y)[2] == pytest.approx(5)
    outside = v.world_to_display((100, 10, 5))[:2]
    assert v.section.contains(v.rotation_pivot(*outside))
    win.section_win.cap.setChecked(False)
    assert v.rotation_pivot(x, y)[2] == pytest.approx(0)
    win.a_section.trigger()
    assert not win.a_section.isChecked() and not v.section.active
    window = win.show_section()
    activate(app, window)
    QTest.keyClick(window, Qt.Key_Escape)
    pump(app)
    assert not window.isVisible() and not win.a_section.isChecked()
    win.show_section()
    win._escape()
    assert not v.section.active


def test_assembly_inspection_toolbar_fits_and_section_toggle(app, tmp_path):
    from bauyrcad.gui.assembly_window import AssemblyWindow
    from test_interference_evaluate import assembly
    a, _, _ = assembly(tmp_path)
    w = AssemblyWindow(assembly=a.asm)
    w.resize(1100, 800); w.show()
    try:
        pump(app)
        w.tabs.setCurrentIndex(1)
        pump(app)
        button = w.evaluate_toolbar.widgetForAction(w.a_clearance)
        assert button.isVisibleTo(w) and button.geometry().right() <= w.evaluate_toolbar.width()
        assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ['Assembly', 'Evaluate', 'View']
        w.a_section.trigger()
        assert w.a_section.isChecked()
        w._escape()
        assert not w.a_section.isChecked() and not w.viewer.section.active
        w.grab().save(str(tmp_path/'inspection-toolbar.png'))
    finally:
        w.dirty = False; w.close()
