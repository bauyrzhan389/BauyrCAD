"""Session-only assembly inspection state; never changes parts, mates or exports."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


class ComponentVisibility:
    def __init__(self, main):
        self.main = main
        self.hidden = set()
        self.isolated = None

    def visible(self, cid):
        return cid == self.isolated if self.isolated is not None else cid not in self.hidden

    def apply(self):
        ids = {c.id for c in self.main.asm.components}
        self.hidden.intersection_update(ids)
        if self.isolated not in ids:
            self.isolated = None
        for cid, (actor, mesh, edges) in self.main.viewer._asm.items():
            actor.SetVisibility(self.visible(cid))
            if edges is not None:
                edges.SetVisibility(self.visible(cid))
        self.main.a_isolate.setChecked(self.isolated is not None)
        self.main.viewer.render()

    def decorate_tree(self):
        root = self.main.tree.topLevelItem(0)
        if root is None:
            return
        for index in range(root.childCount()):
            item = root.child(index)
            kind, cid = item.data(0, Qt.UserRole)
            if kind != "comp":
                continue
            component = self.main.asm.component(cid)
            visible = self.visible(cid)
            font = item.font(0); font.setItalic(not visible); item.setFont(0, font)
            color = (QColor("#c0392b") if component.load_error else
                     self.main.palette().text().color() if visible else QColor("#777777"))
            item.setForeground(0, color)
            original = item.data(0, Qt.UserRole + 1)
            if original is None:
                original = item.toolTip(0)
                item.setData(0, Qt.UserRole + 1, original)
            item.setToolTip(0, original + ("" if visible else "\nHidden (view only)"))

    def changed(self):
        if self.main.mate_panel:
            self.main.close_mate_panel()
        self.main._finish_animation()
        self.main.viewer.close_selection_menu()
        for name in ('clearance_win', 'interference_win'):
            window = getattr(self.main, name, None)
            if window is not None:
                window.close()
        self.apply()
        window = getattr(self.main, 'measure_win', None)
        if window is not None:
            window.model_changed()
        self.decorate_tree()
        self.main.a_isolate.setChecked(self.isolated is not None)
        if self.isolated is not None:
            name = self.main.asm.component(self.isolated).name
            self.main.status(f'Isolated: {name}. Exit Isolate or Show All restores the assembly view.')
        else:
            self.main.status(f'{len(self.hidden)} hidden components. Visibility does not change exports or mass.')

    def toggle(self, cid):
        if self.main.asm.component(cid) is None:
            self.main.status('Select a component first')
            return
        was_visible = self.visible(cid)
        self.isolated = None
        if was_visible:
            self.hidden.add(cid)
        else:
            self.hidden.discard(cid)
        self.changed()

    def isolate(self, cid):
        if self.isolated == cid and cid is not None:
            self.isolated = None
        elif self.main.asm.component(cid) is not None:
            self.isolated = cid
        else:
            self.main.a_isolate.setChecked(self.isolated is not None)
            self.main.status('Select a component first')
            return
        self.changed()

    def exit_isolate(self):
        self.isolated = None
        self.changed()

    def show_all(self):
        self.hidden.clear()
        self.isolated = None
        self.changed()

    def zoom(self, cid):
        entry = self.main.viewer._asm.get(cid)
        if entry is None:
            self.main.status('Select a loaded component first')
            return
        if not self.visible(cid):
            self.isolated = None
            self.hidden.discard(cid)
            self.changed()
        self.main.viewer.fit_bounds(entry[0].GetBounds())
