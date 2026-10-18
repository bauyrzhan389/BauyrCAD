import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QDialog, QTableWidget, QDialogButtonBox

from bauyrcad.api import Drawing
from bauyrcad.gui.drawing_window import DrawingWindow
from test_bom import fixture_assembly


def test_bom_balloon_window_undo_and_export(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    asm, a, b, parts = fixture_assembly(tmp_path)
    drawing = Drawing(asm, standard=False)
    view = drawing.add_view("front", at=(150, 135))
    w = DrawingWindow(drawing=drawing.dr)
    w.show()
    QTest.qWait(150)
    try:
        w.a_bom.trigger()
        assert w.dr.bom.rows[0]["quantity"] == 2
        # Drag the BOM using actual canvas mouse events.
        start = np.array([w.dr.bom.x + 2, w.dr.bom.y - 4])
        end = start + [-10, -8]
        QTest.mousePress(w.canvas, Qt.LeftButton, pos=w.canvas.to_px(start).toPoint())
        QTest.mouseMove(w.canvas, w.canvas.to_px(end).toPoint())
        QTest.mouseRelease(w.canvas, Qt.LeftButton, pos=w.canvas.to_px(end).toPoint())
        assert abs(w.dr.bom.x - 220) < 1
        def edit_dialog():
            dlg = QApplication.activeModalWidget()
            assert isinstance(dlg, QDialog)
            table = dlg.findChild(QTableWidget)
            table.item(0, 2).setText("Fixture mounting plate")
            QTest.mouseClick(dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)
        QTimer.singleShot(50, edit_dialog)
        w.edit_bom()
        assert w.dr.bom.displayed(w.dr.bom.rows[0])["description"] == "Fixture mounting plate"
        w.selected = [("view", view)]
        monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][0], True))
        w.a_balloon.trigger()
        assert w.mode == "balloon"
        tip = w.dr.to_sheet(view, (12, 12))
        QTest.mouseClick(w.canvas, Qt.LeftButton, pos=w.canvas.to_px(tip).toPoint())
        QTest.mouseClick(w.canvas, Qt.LeftButton, pos=w.canvas.to_px((85, 185)).toPoint())
        assert len(w.dr.balloons) == 1
        assert w.dr.balloon_target(w.dr.balloons[0])[0] == "1"
        w.undo()
        assert not w.dr.balloons
        w.redo()
        assert len(w.dr.balloons) == 1
        # Refresh quantities and undo them without losing text overrides.
        asm.asm.remove_component(a.comp.id)
        asm.save(tmp_path / "fixture.bcasm")
        w.a_refresh_bom.trigger()
        assert w.dr.bom.rows[0]["quantity"] == 1
        assert w.dr.balloon_target(w.dr.balloons[0])[0] == "?"
        w.undo()
        assert w.dr.bom.rows[0]["quantity"] == 2
        assert w.dr.balloon_target(w.dr.balloons[0])[0] == "1"
        w.redo()
        assert w.dr.balloon_target(w.dr.balloons[0])[0] == "?"
        w.dr.save(tmp_path / "drawing.bcdrw")
        w.dr.export(tmp_path / "drawing.pdf")
        w.dr.export(tmp_path / "drawing.dxf")
        QTest.qWait(150)
        w.grab().save(str(tmp_path / "bom-window.png"))
    finally:
        w.dirty = False
        w.close()
        app.processEvents()


def test_part_properties_are_undoable(tmp_path, monkeypatch):
    from bauyrcad.gui.main_window import MainWindow
    from bauyrcad.core.document import Document
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    values = iter([("PN-123", True), ("Mount plate", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: next(values))
    try:
        w.a_part_properties.trigger()
        assert (w.doc.part_number, w.doc.description) == ("PN-123", "Mount plate")
        w.doc.save(tmp_path / "properties.bcad")
        w.undo()
        assert w.doc.part_number == ""
        w.redo()
        assert w.doc.part_number == "PN-123"
        assert Document.load(tmp_path / "properties.bcad").description == "Mount plate"
    finally:
        w.dirty = False
        w.close()
        app.processEvents()
