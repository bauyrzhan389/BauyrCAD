---
name: bauyrcad
description: >-
  Bauyrzhan's own CAD + FEA desktop application, BauyrCAD. Use it to create 3D parts and
  assemblies, make 2D drawings (PDF / DXF / DWG, laser-cut DXF), export STEP, and run
  structural FEA (stress, deflection, factor of safety, stiffness), and to suggest and build
  improvements to the app while working. Use this skill whenever Bauyrzhan mentions BauyrCAD,
  or asks to design, model, sketch, dimension, draw or analyse a mechanical part or assembly
  (bracket, plate, flange, beam, shaft, housing, mount, weldment, fixture...), even if they
  don't name BauyrCAD and would otherwise use SolidWorks or MSC Nastran/Patran. Also use it
  when they ask to fix, extend or improve BauyrCAD itself, or ask what BauyrCAD can do.
---

# BauyrCAD

BauyrCAD is Bauyrzhan's own SolidWorks-style CAD + linear static FEA application. You build it
together with them: every CAD/FEA job is done **in** BauyrCAD, and whenever the job needs
something the app can't do yet, you either build it (tested) or log it as a suggestion. Over
time the app becomes the tool that fits their work and your way of working with them.

## 1. Find the application first

Run the bundled finder with any Python 3:

```
python <this skill's folder>/scripts/find_bauyrcad.py
```

It prints JSON: the repo path, the Python to use and whether it is `ready`. It searches
`$BAUYRCAD_HOME`, the known location
`C:\Users\User\Everything\Bauyrzhan\Claude_Projects\Bauyr_CAD`, the current folder, and
folders mounted into a Cowork/sandbox session.

- **Found and ready:** read `CLAUDE.md` in the repo before doing anything else. It is the
  up-to-date working agreement (environment, project workflow, verification, conventions).
  `docs/API.md` is the scripting reference, `CHANGELOG.md` shows what exists, and
  `docs/IMPROVEMENTS.md` lists known gaps.
- **Found but not ready** (e.g. a Linux sandbox, where the Windows `.venv` can't run): follow
  the `fix` it prints, i.e. make a Python 3.10-3.12 venv and `pip install -e <repo>`, and set
  `PYVISTA_OFF_SCREEN=true` to render.
- **Not found:** the folder isn't shared with this session. If a directory-request tool
  exists, request `C:\Users\User\Everything\Bauyrzhan\Claude_Projects\Bauyr_CAD`. Otherwise
  ask Bauyrzhan to add that folder (in Cowork: add it as a working folder; in Claude Code:
  open it). Don't fake the work in another tool.
- **No file access at all** (a plain chat): say so in one line, then still help. Write the
  complete `bauyrcad.api` script they can save as `projects/<name>/model.py` and run with
  `.\.venv\Scripts\python.exe projects\<name>\model.py` from the repo. Tell them what it
  produces, and note any improvement suggestions (section 3) so they can paste them into
  `docs/IMPROVEMENTS.md` or bring them to a session with access.

## 2. How to work

1. **Model by script.** Write `projects/<name>/model.py` with `bauyrcad.api` (`Part`,
   `sketch`, `extrude`/`cut`/`revolve`, `face(...)`, `study(...)`, `Assembly`, `Drawing`).
   Keep named dimensions at the top, so revisions are one-line edits. Use the real features
   instead of drawing them into sketches: `fillet`, `chamfer`, `shell`, `draft`, `hole`
   (Hole Wizard: clearance / tapped / counterbore / countersink by size, e.g. `size="M6"`),
   `pattern_linear` / `pattern_circular` / `mirror`, `sweep` (circular profile `d=`, helix
   springs), `loft`, `rib`, and reference `plane(...)` / `axis(...)` - see `docs/API.md`.
2. **Run it with the repo's Python** (from the finder). Use `st.run(verbose=True)` for long
   solves.
3. **Look before you report.** `part.render(png, study=st)`, `st.render(png)`,
   `asm.render(png)` and `drawing.render(png)` write images you can open. Check the geometry,
   fixtures, load arrows, stress plot and the drawing sheet. Reactions must balance the loads;
   compare with a hand calculation where one exists; converge key results with local mesh
   refinement; read stresses away from point loads and fixture edges.
4. **Deliver:**
   - the `.bcad` / `.bcasm` (opens editable in the GUI and live-reloads if already open);
   - STEP for SolidWorks (assemblies keep their structure);
   - drawings as `.bcdrw` + PDF/DXF/DWG when wanted;
   - the renders;
   - an engineering summary: assumptions, material, BCs, mesh, max von Mises and where, max
     displacement, FoS, convergence, limitations.
5. **Build what's missing.** When a job needs a capability, implement it properly. Follow
   `CLAUDE.md`: core, then API, GUI, tests, docs, CHANGELOG. Run the full test suite, then
   commit the finished feature (standing permission; never push unless asked).

## 3. Suggest improvements as you go

Treat every job as a field test of BauyrCAD. Whenever you notice friction, note it. Friction
includes:
- a missing feature, load, mate, view or export;
- a workaround you had to script around the API;
- something slow, a confusing error, or a result you had to double-check by hand;
- a GUI step that would annoy an engineer used to SolidWorks.

Then:

- **Log it** in `docs/IMPROVEMENTS.md`: date, what you were doing, what happened, the
  suggested improvement, size (S/M/L), status. Newest first, one row each. Skip duplicates;
  update the existing row instead.
- **Small and clearly useful (S), and it unblocks the current job:** just build it, with
  tests, and mention it in the summary.
- **Anything bigger:** don't derail the job. Finish the task with the current app, then end
  your report with a short **"BauyrCAD suggestions"** section: 1-3 items, most valuable
  first, each with one line on why it matters for their work and its size. Offer to build the
  top one.
- Prefer suggestions that remove repeated manual work for Bauyrzhan or for you, over
  nice-to-haves.

## Quick API taste

```python
from bauyrcad.api import Part, Drawing
part = Part("plate")
sk = part.sketch("front"); sk.rect((0, 0), (120, 40)); sk.circle((100, 20), d=12)
part.extrude(sk, 10)
part.fillet(part.edges_where(direction="z"), r=5)                  # round the 4 corners
h = part.hole(part.face("+z"), [(20, 20)], size="M6", kind="counterbore")
part.pattern_linear(h, "x", 30, 3)
part.save("projects/plate/plate.bcad")
st = part.study(material="Aluminium 6061-T6")
st.fix(part.face("-x")); st.face_force(part.face("+x"), (0, -200, 0))
print(st.run()); st.render("projects/plate/plate_vm.png")
dr = Drawing(part); dr.auto_dimension("Front"); dr.export("projects/plate/plate.pdf")
```
