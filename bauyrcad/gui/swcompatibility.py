"""Editable offline export report, shown before choosing a destination."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)


class CompatibilityDialog(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle('SolidWorks export compatibility')
        self.resize(820, 480)
        layout = QVBoxLayout(self)
        heading = QLabel(report['summary'])
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Feature', 'Offline export'])
        labels = {'ready': 'Editable', 'blocked': 'Unsupported',
                  'not_checked': 'Not verified — resolve blockers first'}
        for row in report['features']:
            item = QTreeWidgetItem([row['name'], labels[row['status']]])
            item.setData(0, Qt.UserRole, row['id'])
            self.tree.addTopLevelItem(item)
        self.tree.setColumnWidth(0, 240)
        layout.addWidget(self.tree)
        self.details = QLabel()
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.details)

        def show_details():
            item = self.tree.currentItem()
            fid = item.data(0, Qt.UserRole) if item else None
            issues = [i for i in report['issues'] if i['feature_id'] in (None, fid)]
            fallback = ('The complete history passed the offline writer check. SolidWorks rebuild validation is separate.'
                        if report['ready'] else 'Resolve the reported blockers, then check again.')
            self.details.setText('\n\n'.join(i['reason'] + '\n' + i['suggestion'] for i in issues) or fallback)

        self.tree.currentItemChanged.connect(show_details)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.export_button = buttons.addButton('Continue to export', QDialogButtonBox.AcceptRole)
        self.export_button.setEnabled(report['ready'])
        locate = QPushButton('Select feature in model')
        buttons.addButton(locate, QDialogButtonBox.ActionRole)

        def select_feature():
            item = self.tree.currentItem()
            if item and parent is not None:
                parent._select_tree_data(('feature', item.data(0, Qt.UserRole)))
                self.reject()

        locate.clicked.connect(select_feature)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        blocked = next((i for i, row in enumerate(report['features']) if row['status'] == 'blocked'), 0)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(blocked))
        else:
            show_details()
