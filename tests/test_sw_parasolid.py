"""BauyrCAD's own .x_t files opened in SolidWorks (used only as the judge: the export
itself needs no SolidWorks): one solid body, SolidWorks' fault check clean, Parasolid's
face and edge counts, and the volume SolidWorks measures for the same part through STEP."""
from __future__ import annotations

from pathlib import Path

import pytest

from bauyrcad import solidworks
from bauyrcad.core import geometry
import numpy as np
from parasolid_models import (BSPLINE, MODELS, PEGBOARD_COLOURS, SW_VOLUME_REL, build, pegboard,
                              two_bodies)
from sw_support import needs_solidworks

pytestmark = needs_solidworks


def _open(sw, path):
    doc = sw.open_step(str(Path(path).resolve()))      # LoadFile4 imports .x_t too
    bodies = [sw.T(b, "IBody2") for b in sw.T(doc, "IPartDoc").GetBodies2(0, False) or ()]
    return doc, bodies


@pytest.mark.parametrize("name", list(MODELS))
def test_solidworks_opens_the_xt_as_a_clean_solid(sw, tmp_path, name):
    part = build(name)
    xt = tmp_path / f"{name}.x_t"
    part.export(xt)
    doc, bodies = _open(sw, xt)
    assert len(bodies) == 1 and bodies[0].GetType() == 0           # swSolidBody
    (body,) = bodies
    faults = sw.T(body.Check3, "IFaultEntity")
    assert faults is None or faults.Count == 0
    v, e, f, _ = MODELS[name][1]
    assert (body.GetFaceCount(), body.GetEdgeCount()) == (f, e)
    vol = solidworks._volume(sw, doc)
    # SolidWorks measures the same geometry through STEP to ~1e-8 (the file is exact) ...
    step = tmp_path / f"{name}.step"
    part.export(step)
    sdoc, _ = _open(sw, step)
    step_rel, occ_rel = SW_VOLUME_REL.get(name, (1e-6, None))
    assert vol == pytest.approx(solidworks._volume(sw, sdoc), rel=step_rel)
    # ... and differs from OpenCASCADE's exact volume by up to ~2e-5 on faces bounded by
    # ellipses (the inclined cut is 6750 pi exactly; SolidWorks reports 3e-6 less); on
    # B-spline faces OpenCASCADE's own measure is the uncertain one (the closed ring: 2e-3)
    (want, _), _ = geometry.accurate_mass_properties(part.doc.body)
    assert vol == pytest.approx(want, rel=occ_rel or (3e-3 if name in BSPLINE else 5e-5))


def test_assembly_opens_with_shared_parts_placements_and_colours(sw, tmp_path):
    """The pegboard: three components, the two pegs one shared part, every component where
    the same assembly's STEP puts it, part volumes right, and each part's colour exactly
    its component's (through STEP SolidWorks shows them lightened)."""
    asm = pegboard(tmp_path)
    xt, step = tmp_path / "pegboard.x_t", tmp_path / "pegboard.step"
    asm.export(xt)
    asm.export(step)

    def read(path):
        doc = sw.open_step(str(path.resolve()))
        assert doc.GetType() == 2
        out = []
        for c in sw.T(doc, "IAssemblyDoc").GetComponents(True) or ():
            c = sw.T(c, "IComponent2")
            m = sw.T(c.GetModelDoc2(), "IModelDoc2")
            tr = np.array(sw.T(c.Transform2, "IMathTransform").ArrayData)[:12]
            out.append((m.GetTitle(), tuple(np.round(tr, 6)), round(solidworks._volume(sw, m), 3),
                        np.array(m.MaterialPropertyValues)[:3]))
        return sorted(out, key=lambda x: (x[0], x[1]))

    got, want = read(xt), read(step)
    assert [g[0] for g in got] == ["peg", "peg", "plate"]
    assert [g[1:3] for g in got] == [w[1:3] for w in want]          # placements, volumes
    peg, plate = np.array(PEGBOARD_COLOURS["Peg A"]) / 255, np.array(PEGBOARD_COLOURS["Plate"]) / 255
    assert np.allclose(got[0][3], peg, atol=2e-3) and np.allclose(got[2][3], plate, atol=2e-3)


def test_several_bodies_open_as_an_assembly_of_them(sw, tmp_path):
    """A part with two separate solids is written as SolidWorks writes one (an assembly of
    its bodies), and SolidWorks opens it the same way it opens its own: two components."""
    path = tmp_path / "two.x_t"
    two_bodies().export(path)
    doc = sw.open_step(str(path.resolve()))
    assert doc.GetType() == 2                                       # swDocASSEMBLY
    comps = sw.T(doc, "IAssemblyDoc").GetComponents(True) or ()
    assert len(comps) == 2
