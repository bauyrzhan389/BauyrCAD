"""SolidWorks assemblies of sub-assemblies written without SolidWorks
(api.export_solidworks_tree, swfile.write_assembly with .SLDASM references)."""
from __future__ import annotations

import re

import numpy as np
import pytest

from bauyrcad import api
from bauyrcad.core import sldprt, swfile
from parasolid_models import pegboard


def _doc_types(sec: bytes) -> dict:
    """{referenced file stem: document type byte} of a model header (the assembly's own
    entry at the end is not a reference)."""
    strings = list(swfile.cstrings(sec))
    stems = {re.split(r"[\\\\/]", t)[-1].rsplit(".", 1)[0] for _, _, t in strings
             if t.lower().endswith((".sldprt", ".sldasm"))}
    return {t: sec[p + n] for p, n, t in strings if t in stems}


def test_a_tree_references_its_sub_assemblies_as_assemblies(tmp_path):
    """Two pegboards as sub-assemblies plus one part at the top: the subs written first as
    their own .SLDASM (with their parts), the top referencing them with document type 3
    (assembly) and the part with 2, placements and fixed flags as given."""
    board = pegboard(tmp_path)
    board.save(tmp_path / "board.bcasm")
    out = tmp_path / "out"
    R = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1.0]])
    M1, M2 = np.eye(4), np.eye(4)
    M2[:3, :3], M2[:3, 3] = R, (250.0, 10.0, -5.0)
    plate = tmp_path / "plate.bcad"
    written = api.export_solidworks_tree(out / "top.SLDASM", [
        (board, M1, True), (tmp_path / "board.bcasm", M2, False), (plate, None, False)])
    assert sorted(p.name for p in out.iterdir()) == ["board.SLDASM", "peg.SLDPRT", "plate.SLDPRT",
                                                     "top.SLDASM"]
    assert out / "top.SLDASM" in written and out / "board.SLDASM" in written
    secs = sldprt.read_sections(out / "top.SLDASM")
    for s in (swfile.MODEL_HEADER, swfile.HEADER2):
        texts = [t for _, _, t in swfile.cstrings(secs[s])]
        refs = [t for t in texts if t.lower().endswith((".sldprt", ".sldasm"))]
        assert refs == [str((out / f).resolve()) for f in ("board.SLDASM", "board.SLDASM",
                                                            "plate.SLDPRT")]
        types = _doc_types(secs[s])
        assert types["board"] == swfile.DOC_ASSEMBLY and types["plate"] == swfile.DOC_PART
    comps = swfile.assembly_components(out / "top.SLDASM")
    assert [c[0] for c in comps] == ["board-1", "board-2", "plate-1"]
    assert np.allclose(comps[1][1], R) and np.allclose(comps[1][2], (250, 10, -5))
    assert [c[3] for c in comps] == [True, False, False]
    # the sub-assembly itself is a plain parts assembly
    sub = sldprt.read_sections(out / "board.SLDASM")[swfile.MODEL_HEADER]
    assert set(_doc_types(sub).values()) == {swfile.DOC_PART}


def test_a_parts_assembly_keeps_part_document_types(tmp_path):
    asm = pegboard(tmp_path)
    path = tmp_path / "o" / "pegboard.SLDASM"
    asm.export(path)
    sec = sldprt.read_sections(path)[swfile.MODEL_HEADER]
    assert set(_doc_types(sec).values()) == {swfile.DOC_PART}


def test_tree_errors(tmp_path):
    with pytest.raises(api.ApiError, match=".SLDASM"):
        api.export_solidworks_tree(tmp_path / "t.step", [])
    with pytest.raises(swfile.SwFileError, match="not found"):
        swfile.write_assembly([("s", tmp_path / "missing.SLDASM", None)],
                              [(0, "s", np.eye(4), False)], tmp_path / "t.SLDASM")


def test_a_tree_can_reference_an_existing_solidworks_assembly(tmp_path):
    """An .SLDASM already in the folder (e.g. saved by SolidWorks) goes in as it is."""
    board = pegboard(tmp_path)
    out = tmp_path / "out"
    api.export_solidworks_tree(out / "inner.SLDASM", [(board, None, True)])
    before = (out / "inner.SLDASM").read_bytes()
    api.export_solidworks_tree(out / "outer.SLDASM", [(out / "inner.SLDASM", None, True),
                                                      (tmp_path / "plate.bcad", None, False)])
    assert (out / "inner.SLDASM").read_bytes() == before
    sec = sldprt.read_sections(out / "outer.SLDASM")[swfile.MODEL_HEADER]
    assert _doc_types(sec) == {"inner": swfile.DOC_ASSEMBLY, "plate": swfile.DOC_PART}
    with pytest.raises(api.ApiError, match="same folder"):
        api.export_solidworks_tree(tmp_path / "x.SLDASM", [(out / "inner.SLDASM", None, True)])


def test_an_assembly_can_reference_existing_solidworks_parts(tmp_path):
    """A .SLDPRT already in the folder (e.g. saved by SolidWorks, with its feature tree) is
    referenced as a part, not rewritten; a missing one is an error."""
    pegboard(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    api.Part.open(tmp_path / "plate.bcad").export(out / "plate.SLDPRT")
    before = (out / "plate.SLDPRT").read_bytes()
    M = np.eye(4)
    M[:3, 3] = (10.0, 0.0, 0.0)
    swfile.write_assembly([("plate", out / "plate.SLDPRT", None)],
                          [(0, "plate", np.eye(4), True), (0, "plate", M, False)], out / "a.SLDASM")
    assert (out / "plate.SLDPRT").read_bytes() == before
    sec = sldprt.read_sections(out / "a.SLDASM")[swfile.MODEL_HEADER]
    assert _doc_types(sec) == {"plate": swfile.DOC_PART}
    comps = swfile.assembly_components(out / "a.SLDASM")
    assert [c[0] for c in comps] == ["plate-1", "plate-2"] and np.allclose(comps[1][2], (10, 0, 0))
    with pytest.raises(swfile.SwFileError, match="part .* not found"):
        swfile.write_assembly([("q", out / "missing.SLDPRT", None)], [(0, "q", np.eye(4), False)],
                              out / "b.SLDASM")
