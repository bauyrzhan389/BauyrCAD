"""Shared CAD workspace: manager tabs and a compact viewport command strip."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QTabBar,QToolBar
from .icons import icon

def manager(main):
    container=QWidget(); layout=QVBoxLayout(container); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
    tabs=QTabBar(); tabs.setExpanding(True)
    tabs.addTab(icon('part'),'FeatureManager'); tabs.addTab(icon('properties'),'PropertyManager')
    tabs.setTabToolTip(0,'Model feature tree'); tabs.setTabToolTip(1,'Active command properties')
    layout.addWidget(tabs); layout.addWidget(main.left,1)
    main.manager_tabs=tabs; main._property_page=None
    def changed(index):
        if index:
            main._property_page=main.left.widget(index)
        tabs.blockSignals(True); tabs.setCurrentIndex(1 if index else 0); tabs.blockSignals(False)
    def chosen(index):
        if index==0: main.left.setCurrentWidget(main.tree)
        else:
            section=getattr(main,'section_win',None)
            if getattr(main,'command',None) is not None or getattr(main,'mate_panel',False):
                main.left.setCurrentIndex(1)
            elif section is not None and main.viewer.section.plane is not None:
                main.left.setCurrentWidget(section.panel)
            else:
                tabs.blockSignals(True); tabs.setCurrentIndex(0); tabs.blockSignals(False)
                main.status('Choose a modelling or inspection command to edit its properties')
        main.viewer.render()
    main.left.currentChanged.connect(changed); tabs.currentChanged.connect(chosen)
    return container

def viewport_tools(main):
    row=QWidget(); layout=QHBoxLayout(row); layout.setContentsMargins(0,0,0,0)
    toolbar=QToolBar(); toolbar.setIconSize(QSize(20,20)); toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
    toolbar.setStyleSheet('QToolBar {background:#ffffff;border:0;spacing:4px;} QToolButton {padding:3px;}')
    for name in ('a_fit','a_front','a_top','a_right','a_iso','a_section','a_measure'):
        action=getattr(main,name,None)
        if action is not None: toolbar.addAction(action)
    toolbar.addSeparator()
    from .appearance import set_display_style
    for name,label in [('shaded_edges','Shaded with Edges'),('shaded','Shaded')]:
        action=toolbar.addAction(icon(name),label)
        action.triggered.connect(lambda checked=False,key=name:set_display_style(key))
    layout.addStretch(); layout.addWidget(toolbar); layout.addStretch()
    main.viewer.layout().insertWidget(0,row)
    main.viewport_toolbar=toolbar
