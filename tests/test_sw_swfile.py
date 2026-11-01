"""SolidWorks 2024 opening the .SLDPRT and .SLDASM files BauyrCAD wrote without SolidWorks
(SolidWorks is only the judge here): our exact body as the Imported feature, kept by a forced
rebuild, nothing in What's Wrong, the volume SolidWorks measures for the same part through
STEP, assembly components placed as SolidWorks places the same assembly's STEP, and
SolidWorks can save the files again."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bauyrcad import solidworks
from bauyrcad.core import geometry
from parasolid_models import BSPLINE, MODELS, build, pegboard, spline_shell, two_bodies
from sw_support import needs_solidworks

pytestmark = needs_solidworks

CASES = ["box", "shaft", "cone_tip", "sphere", "holes", "fillet_corner", "incline", "cavity",
         "spline_revolve", "loft", "sweep_closed", "spring", "text"]


def _open(sw, path):
    r = sw.sw.OpenDoc6(str(Path(path).resolve()), 1, 1, "", 0, 0)
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None, r
    sw._docs.append(doc)
    return doc


def _bodies(sw, doc):
    return [sw.T(b, "IBody2") for b in sw.T(doc, "IPartDoc").GetBodies2(0, False) or ()]


def _check(sw, part, path, faces, edges, tmp_path, rel):
    part.export(path)
    doc = _open(sw, path)
    (body,) = _bodies(sw, doc)
    assert (body.GetFaceCount(), body.GetEdgeCount()) == (faces, edges)
    doc.ForceRebuild3(False)
    (body,) = _bodies(sw, doc)
    faults = sw.T(body.Check3, "IFaultEntity")
    assert faults is None or faults.Count == 0
    assert sw.T(doc.Extension, "IModelDocExtension").GetWhatsWrongCount() == 0
    vol = solidworks._volume(sw, doc)
    (want, _), _ = geometry.accurate_mass_properties(part.doc.body)
    assert vol == pytest.approx(want, rel=rel)
    again = tmp_path / f"again_{path.name}"                    # SolidWorks saves it again
    sw.save(doc, str(again.resolve()))
    sw.close(doc)
    doc2 = _open(sw, again)
    assert solidworks._volume(sw, doc2) == pytest.approx(vol, rel=1e-9)


@pytest.mark.parametrize("name", CASES)
def test_solidworks_opens_our_sldprt(sw, tmp_path, name):
    _, e, f, _ = MODELS[name][1]
    _check(sw, build(name), tmp_path / f"{name}.SLDPRT", f, e, tmp_path,
           3e-3 if name in BSPLINE else 5e-5)


def test_tolerant_edges_in_sldprt(sw, tmp_path):
    _check(sw, spline_shell(), tmp_path / "shell.SLDPRT", 11, 24, tmp_path, 3e-4)


def test_several_solids_are_one_body_of_lumps(sw, tmp_path):
    path = tmp_path / "two.SLDPRT"
    two_bodies().export(path)
    doc = _open(sw, path)
    (body,) = _bodies(sw, doc)
    assert body.GetFaceCount() == 12
    assert solidworks._volume(sw, doc) == pytest.approx(13000)


def test_solidworks_opens_our_sldasm(sw, tmp_path):
    """The pegboard as a .SLDASM written without SolidWorks: an assembly of 3 components,
    both pegs one part file, every component where SolidWorks puts it for the same
    assembly's STEP, part volumes right, the plate fixed and the pegs floating, a clean
    forced rebuild, and SolidWorks saves it again."""
    asm = pegboard(tmp_path)
    step, path = tmp_path / "step" / "pegboard.step", tmp_path / "sw" / "pegboard.SLDASM"
    step.parent.mkdir()
    asm.export(step)
    asm.export(path)

    def read(doc):
        out = []
        for c in sw.T(doc, "IAssemblyDoc").GetComponents(True) or ():
            c = sw.T(c, "IComponent2")
            m = sw.T(c.GetModelDoc2(), "IModelDoc2")
            tr = np.array(sw.T(c.Transform2, "IMathTransform").ArrayData)[:12]
            out.append((round(solidworks._volume(sw, m), 3), tuple(np.round(tr, 6)),
                        c.Name2, Path(c.GetPathName()).name.lower(), bool(c.IsFixed())))
        return sorted(out, key=lambda x: x[:2])

    sdoc = sw.open_step(str(step.resolve()))
    want = read(sdoc)
    sw.close(sdoc)
    r = sw.sw.OpenDoc6(str(path.resolve()), 2, 1, "", 0, 0)      # swDocASSEMBLY, silent
    doc = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    assert doc is not None, r
    sw._docs.append(doc)
    assert doc.GetType() == 2
    got = read(doc)
    assert [g[:2] for g in got] == [w[:2] for w in want]            # volumes, placements
    assert sorted(g[2] for g in got) == ["peg-1", "peg-2", "plate-1"]
    assert sorted(g[3] for g in got) == ["peg.sldprt", "peg.sldprt", "plate.sldprt"]
    assert {g[3]: g[4] for g in got} == {"plate.sldprt": True, "peg.sldprt": False}
    doc.ForceRebuild3(False)
    assert read(doc) == got
    assert sw.T(doc.Extension, "IModelDocExtension").GetWhatsWrongCount() == 0
    again = tmp_path / "sw" / "again.SLDASM"                        # SolidWorks saves it again
    sw.save(doc, again)
    sw.close(doc)
    r = sw.sw.OpenDoc6(str(again.resolve()), 2, 1, "", 0, 0)
    doc2 = sw.T(r[0] if isinstance(r, tuple) else r, "IModelDoc2")
    sw._docs.append(doc2)
    assert read(doc2) == got
