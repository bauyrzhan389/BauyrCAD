"""Shared, persistent viewport backgrounds for part and assembly windows."""
from weakref import WeakSet
from shiboken6 import isValid
from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QActionGroup
from .. import APP_NAME

STYLES = {
    'white': ('White', '#ffffff', None),
    'neutral': ('Neutral grey', '#fafafa', '#bdbdbd'),
    'classic': ('Classic blue', '#fbfcfe', '#b9c8dc'),
}
_VIEWERS = WeakSet()
_GROUPS = WeakSet()
SETTING = 'viewport/background'


def current_style():
    value = QSettings(APP_NAME, APP_NAME).value(SETTING, 'white')
    return value if isinstance(value, str) and value in STYLES else 'white'


def _apply(viewer, name):
    _, bottom, top = STYLES[name]
    viewer.plotter.set_background(bottom, top=top)
    viewer.background_style = name


def register_viewer(viewer):
    _VIEWERS.add(viewer)
    viewer.display_style = current_display_style()
    _apply(viewer, current_style())


def set_style(name):
    if name not in STYLES:
        raise ValueError('Unknown viewport background')
    QSettings(APP_NAME, APP_NAME).setValue(SETTING, name)
    for viewer in list(_VIEWERS):
        if isValid(viewer) and viewer.plotter.render_window is not None:
            _apply(viewer, name)
            viewer.render()
    for group in list(_GROUPS):
        if not isValid(group):
            continue
        for action in group.actions():
            action.setChecked(action.data() == name)


def add_menu(parent, view_menu):
    menu = view_menu.addMenu('Viewport Background')
    group = QActionGroup(parent)
    group.setExclusive(True)
    for name, (label, _, _) in STYLES.items():
        action = QAction(label, group)
        group.addAction(action)
        action.setCheckable(True)
        action.setData(name)
        action.setChecked(name == current_style())
        action.triggered.connect(lambda checked=False, name=name: set_style(name))
        menu.addAction(action)
    _GROUPS.add(group)
    return group


DISPLAY_STYLES = {'shaded_edges': 'Shaded with Edges', 'shaded': 'Shaded'}
DISPLAY_SETTING = 'viewport/display-style'
_DISPLAY_GROUPS = WeakSet()


def current_display_style():
    value = QSettings(APP_NAME, APP_NAME).value(DISPLAY_SETTING, 'shaded_edges')
    return value if isinstance(value, str) and value in DISPLAY_STYLES else 'shaded_edges'


def sync_model_edges(viewer):
    # Results have their own explicit undeformed-outline option.
    if getattr(viewer, 'results_visible', False) or not hasattr(viewer, 'section'):
        return
    visible = getattr(viewer, 'display_style', 'shaded_edges') == 'shaded_edges'
    for actor, mesh, edges in viewer.section.sources():
        if edges is not None:
            edges.SetVisibility(visible and bool(actor.GetVisibility()))


def set_display_style(name):
    if name not in DISPLAY_STYLES:
        raise ValueError('Unknown display style')
    QSettings(APP_NAME, APP_NAME).setValue(DISPLAY_SETTING, name)
    for viewer in list(_VIEWERS):
        if isValid(viewer) and viewer.plotter.render_window is not None:
            viewer.display_style = name
            viewer.render()
    for group in list(_DISPLAY_GROUPS):
        if isValid(group):
            for action in group.actions():
                action.setChecked(action.data() == name)


def add_display_menu(parent, view_menu):
    from .icons import icon
    menu = view_menu.addMenu('Display Style')
    group = QActionGroup(parent)
    group.setExclusive(True)
    for name, label in DISPLAY_STYLES.items():
        action = QAction(icon(name), label, group)
        group.addAction(action)
        action.setCheckable(True)
        action.setData(name)
        action.setChecked(name == current_display_style())
        action.triggered.connect(lambda checked=False, name=name: set_display_style(name))
        menu.addAction(action)
    _DISPLAY_GROUPS.add(group)
    return group
