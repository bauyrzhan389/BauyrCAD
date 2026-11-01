from pathlib import Path
import pytest
from PySide6.QtWidgets import QFileDialog,QMessageBox
from PySide6.QtTest import QTest
from bauyrcad.api import Part
from bauyrcad.core import exchange,sldprt
from bauyrcad import solidworks
from test_gui_revolve_workflow import win


def test_offline_action_writes_tree_without_com(win,monkeypatch,tmp_path):
    p=Part();s=p.sketch('front');lines=s.rect((0,0),(40,20));s.dim(lines[0],40)
    p.extrude(s,10);p.fillet([(0,0,5)],r=2)
    win._set_document(p.doc)
    monkeypatch.setattr(solidworks,'available',lambda: pytest.fail('offline path queried SolidWorks'))
    monkeypatch.setattr(solidworks,'export_part_file',lambda *a,**k: pytest.fail('COM export'))
    target=tmp_path/'editable.SLDPRT'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(target),''))
    win.a_export_sw_offline.trigger()
    assert sldprt.tree_mismatches(target,[f.name for f in p.doc.features],exact=True)==[]
    if Path('projects/sw_features/out').exists():
        win.menuBar().actions()[0].menu().popup(win.mapToGlobal(win.rect().topLeft()))
        QTest.qWait(150)
        win.grab().save('projects/sw_features/out/offline_export_menu.png')
        win.menuBar().actions()[0].menu().grab().save('projects/sw_features/out/offline_export_choices.png')
        win.menuBar().actions()[0].menu().hide()


def test_gui_feature_export_never_silently_flattens(win,monkeypatch,tmp_path):
    p=Part();s=p.sketch('front');s.rect((0,0),(40,20));p.extrude(s,10);p.shell(1)
    win._set_document(p.doc)
    target=tmp_path/'existing.SLDPRT';target.write_bytes(b'previous file')
    errors=[]
    monkeypatch.setattr(QMessageBox,'critical',lambda *a:errors.append(a[-1]))
    assert not win._export_to(str(target),exchange.FORMATS['sldprt_features'])
    assert target.read_bytes()==b'previous file'
    assert errors and 'unsupported' in errors[0].lower()
