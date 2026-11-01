from PySide6.QtWidgets import QMessageBox

from bauyrcad import solidworks
from bauyrcad.core import xt_brep
from parasolid_models import MODELS, pegboard
from test_gui_revolve_workflow import win  # noqa: F401 - the shared main window fixture


def test_export_to_solidworks_without_solidworks_offers_parasolid(win, monkeypatch, tmp_path):
    """File > Export to SolidWorks on a PC without SolidWorks: BauyrCAD offers the .x_t it
    writes itself, and writes it on Yes (nothing on No)."""
    part = MODELS["shaft"][0]()
    win._set_document(part.doc)
    monkeypatch.setattr(solidworks, "available", lambda: False)
    asked = []

    def answer(reply):
        def question(parent, title, text, *a, **k):
            asked.append(text)
            return reply
        return question

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.No))
    assert win.export_solidworks(str(tmp_path / "no.x_t")) is None
    assert not (tmp_path / "no.x_t").exists()
    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.Yes))
    path = tmp_path / "shaft.x_t"
    assert win.export_solidworks(str(path)) == str(path)
    assert "Parasolid" in asked[-1] and "offline" in asked[-1]
    assert "requires installed SolidWorks" in asked[-1]
    header = "".join(path.read_text().split("**END_OF_HEADER")[0].splitlines())  # 80-col
    assert "SCH=SCH_1200000_12006;" in header and "KEY=Part1;FILE=shaft.x_t;" in header   # unsaved document
    body = xt_brep.body_from_shape(part.doc.body)
    assert (len(body.faces), len(body.edges)) == (8, 14)


def test_assembly_export_to_solidworks_without_solidworks_offers_sldasm(monkeypatch, tmp_path):
    """The assembly window offers the .SLDASM BauyrCAD writes itself (its parts next to it)
    first and a Parasolid .x_t second, and writes either on Yes."""
    from PySide6.QtWidgets import QFileDialog

    from bauyrcad.core import swfile
    from bauyrcad.gui.assembly_window import AssemblyWindow
    asm = pegboard(tmp_path)
    monkeypatch.setattr(solidworks, "available", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    offered = []

    def save_as(parent, title, start, filters, *a, **k):
        offered.append((start, filters))
        return str(tmp_path / "sw" / "board"), filters.split(";;")[0]

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_as)
    w = AssemblyWindow(assembly=asm.asm)
    try:
        path = tmp_path / "sw" / "board.SLDASM"
        assert w.export_solidworks() == str(path)                    # the dialog: .SLDASM first
        assert offered[0][0].endswith(".SLDASM") and offered[0][1].startswith("SolidWorks 2024 assembly")
        assert "Parasolid" in offered[0][1]
        assert sorted(p.name for p in path.parent.iterdir()) == ["board.SLDASM", "peg.SLDPRT",
                                                                "plate.SLDPRT"]
        assert [c[0] for c in swfile.assembly_components(path)] == ["plate-1", "peg-1", "peg-2"]
        path = tmp_path / "board.x_t"                                # a path given: as it says
        assert w.export_solidworks(str(path)) == str(path)
        assert "SDL/TYSA_COLOUR" in path.read_text()
    finally:
        w.close()
