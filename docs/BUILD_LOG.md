# How BauyrCAD was built: development log

> **For the reader (and for Claude, if this file was uploaded to a chat).** This document
> records how BauyrCAD, a desktop CAD + FEA application, was created by a mechanical
> engineer (Bauyrzhan) working with Claude in the Claude Code desktop app. It covers:
> - what was asked at each step;
> - what was built;
> - which problems came up and how they were solved;
> - the technical design;
> - how the collaboration was organised.
>
> Use it to answer questions about the process, the architecture, or how to do a similar
> project. Numbers and dates come from the project's git history and test suite.

---

## 1. At a glance

| | |
|---|---|
| **What** | BauyrCAD: SolidWorks-style parametric CAD and linear static FEA (finite element analysis) in one desktop app. |
| **Who** | Bauyrzhan, a mechanical engineer who uses SolidWorks and MSC Nastran/Patran. He directed the work in plain language. Claude wrote and tested the code. |
| **Tool** | Claude Code (desktop app, Code tab) with Claude Opus 5.5. Claude read and wrote files, ran commands, drove the real GUI in tests and looked at screenshots. |
| **When** | 26–28 September 2026, about two and a half days of conversation. |
| **Size today** | About 21,000 lines of Python in the app and 3,200 lines of tests. 173 automated tests. |
| **Delivered as** | A Windows app (`BauyrCAD.exe`, zip ≈ 393 MB), a Python scripting API, a command-line interface and a Claude "skill". |
| **Licence** | Own code MIT. Gmsh (the mesher) is GPL. |

What it can do now:
- **Sketching:** SolidWorks-style sketcher with constraints and Smart Dimension, plus an
  AutoCAD-style command line with dynamic input.
- **Modelling:** extrude, cut and revolve.
- **Assemblies:** mates, motion studies, interference and clearance checks.
- **Drawings:** 2D drawings exported as PDF, DXF and DWG.
- **Exchange:** STEP, IGES and STL import and export, plus native SolidWorks parts
  and assemblies built by the user's installed SolidWorks.
- **Analysis:** FEA with fixtures, point, face and pressure loads, results plots and a
  factor of safety.
- **Measure:** a SolidWorks-style Measure tool.

---

## 2. The goal as stated at the start

The very first message (verbatim):

> "i want to create a CAD software from scratch. I work good in solidworks for CAD and MSC
> Package for FEA analysis. I want to create a software where I would be able to model and
> run analysis on one place. Your task is to create a separate software that I can download
> and hopefully publish with capability of further improvement and expansion. For the first
> iteration I want the software to be able to create sketches with commands like line and
> rectangle, create model with commands such as extrude and extrude cut, and to perform
> simple fea analysis with fixed face and a force applied to a point. I want the software to
> support for now step format"

A later message set the long-term vision. Every future 3D-model or analysis job should be
done *in* BauyrCAD, and Claude should improve the app as it goes: "as time passes and we
work on multiple projects together … it will become more functional, optimized, more
convenient to you and me."

---

## 3. Technology choices (and why)

| Area | Choice | Why / alternatives rejected |
|---|---|---|
| Language | Python 3.11 | Fast to build and script. The system default was Python 3.14, which had no OpenCASCADE wheels, so a 3.11 virtual environment was required. |
| CAD kernel | OpenCASCADE via `cadquery-ocp` **pinned to 7.9.3** | Industrial B-rep kernel with STEP/IGES support. The OCCT 8 wheels changed collection APIs (e.g. `TopTools_IndexedMapOfShape` missing), so the version was pinned. |
| Meshing | Gmsh (tetrahedra) | Robust and scriptable. It is GPL, noted as a constraint for any closed-source release. |
| FEA solver | Own code: quadratic 10-node tetrahedra. Sparse solve with **Intel MKL PARDISO** (`pypardiso`), falling back to AMG-CG (`pyamg`) or SciPy SuperLU. | Written in-house so it is small and understandable. PARDISO became the default after thin-plate models proved AMG and SuperLU far too slow (see §6). |
| Sketch solver | Own Newton / min-norm constraint solver | SolveSpace's Python binding is GPL, so it was avoided. |
| GUI | PySide6 (Qt) + VTK through pyvista / pyvistaqt | Mature 3D viewport with picking. |
| Drawings | OCC hidden-line removal. Export with **ezdxf** (DXF, MIT licence) and **matplotlib** (vector PDF/SVG). DWG through the free ODA File Converter. | DWG is closed, and no free writer library exists. |
| Packaging | PyInstaller one-folder build plus a `--selftest` run inside the frozen exe | Catches missing DLLs and data files before anyone downloads the build. |
| Native SolidWorks files | Not possible | `.sldprt` is closed and proprietary. STEP AP214/AP242 is offered instead, and assemblies keep their structure. |

**Architecture rule:** `gui → fea → core`. The `core` and `fea` packages never import Qt,
so everything except the UI can be scripted and tested headless. All OpenCASCADE calls are
kept in a few modules (`geometry.py`, `exchange.py`, `step_io.py`), so the kernel can be
swapped later.

```
bauyrcad/
├── core/        CAD, no GUI: plane, sketch/ (model, constraints, solver, builders, ops,
│                profiles), features, document (.bcad), geometry (all OCC calls), exchange,
│                step_io, assembly (mate solver, motion, interference, clearance),
│                drawing + drawing_export (sheets, HLR views, dimensions, PDF/DXF/DWG), measure
├── fea/         study (materials, fixtures, loads), mesher (Gmsh), solver (tet10 FEM), analysis
├── gui/         main_window, viewer (VTK), sketcher, command_line (AutoCAD-style), assembly
│                and drawing windows, view selector, dynamic input, measure window, context bar
├── api.py       scripting API (Part, Sketch, Study, Assembly, Drawing)
├── cli.py       python -m bauyrcad info | render | run | export | drawing | script
└── render.py    off-screen PNG rendering (so Claude can look at models and results)
```

---

## 4. Timeline: what was asked, what was built, what went wrong

Each phase starts with the user's request (paraphrased, or quoted where useful) and ends
with a working, tested state.

### Phase 1: first version (v0.1, 26 Sep)
- **Asked:** sketch line and rectangle, extrude and extruded cut, FEA with a fixed face and a
  point force, STEP support.
- **Built:**
  - a sketch → region → face pipeline;
  - extruded boss and cut features, with a parametric document that replays its features;
  - STEP import and export;
  - Gmsh meshing, with CAD faces mapped to mesh nodes;
  - a tet10 linear static solver, a von Mises results plot and a Windows build.
- **Problem:** the user couldn't open the app ("Failed to load Python DLL …
  `build\bauyrcad\_internal\python311.dll`"). The cause was that they had launched the exe
  from PyInstaller's **work folder** (`build\`) instead of the real output in `dist\`.
- **Fix:** the build script now puts the work folder in `%TEMP%`, so only `dist\` contains an
  exe. The docs say where the runnable app is.

### Phase 2: real sketching (v0.2, 26 Sep)
- **Asked:**
  - save to many formats, including SolidWorks if possible;
  - every basic SolidWorks sketch command;
  - drag sketches and Smart Dimension;
  - a command box that pops up when typing, as in AutoCAD.
- **Built:**
  - **Constraint solver:** an in-house geometric constraint solver (Newton iterations,
    finite-difference Jacobian, degrees of freedom from the null space, over-definition
    checks).
  - **Tools and relations:** the SolidWorks tool set (lines, rectangles of every kind, slots,
    circles, arcs, polygon, spline, point); relations; trim, extend, offset, mirror, fillet,
    chamfer, move, copy, rotate, scale and patterns.
  - **Colouring:** under-defined blue, fully defined black.
  - **Command line:** an AutoCAD-style type-to-command popup with aliases (`L`, `REC`, `TR`)
    and typed coordinates (`x,y`, `@dx,dy`, `@len<ang`).
  - **Export:** STEP AP203/214/242, IGES, BREP, STL, OBJ, glTF and VRML.
- **Decision:** SolidWorks native export is impossible, so STEP was recommended instead.

### Phase 3: contour selection, and "work in BauyrCAD from now on" (v0.3, 26 Sep)
- **Asked:**
  - during Extrude, show the sketch and let the user pick which enclosed areas to extrude;
  - Claude should use BauyrCAD for all future CAD and FEA chats and keep improving it.
- **Built:**
  - **Contour selection:** OpenCASCADE's splitter builds the planar arrangement, so any
    enclosed area can be picked, even areas formed by crossing lines.
  - **Scripting and CLI:** a **scripting API** (`bauyrcad.api`) and a CLI, with off-screen
    rendering so Claude can check its own work visually.
  - **Live reload** of the open part when a script rewrites it.
  - **Working agreement:** a `CLAUDE.md` file with the environment, the project workflow, the
    engineering checks and the conventions.
  - **Context for future chats:** a `projects/` folder, a **Claude skill** (`bauyrcad`) and
    persistent memory notes, so future chats find the app.
- **First commit.**

### Phase 4: first real job, loads, and Space repeat (27 Sep)
- **Asked:** Space should repeat the previous command, as in AutoCAD; delete example parts.
- **Built:**
  - Space repeat;
  - face force and pressure loads, validated against F/A, Lamé and cantilever theory;
  - local mesh refinement on faces;
  - stress readings away from point-load singularities;
  - EN and GOST steel and aluminium materials.
- **Why:** a first test project (an L-bracket) showed what the FEA side lacked.

### Phase 5: revolve and assemblies (27 Sep)
- **Asked:** revolve and revolve-cut; an assembly window where parts move according to
  SolidWorks-style mates.
- **Built:** revolved boss and cut (checked with Pappus' theorem), and an assembly solver:
  - six unknowns per floating component, with poses re-linearised each iteration;
  - coincident, parallel, perpendicular, distance, angle, concentric and lock mates;
  - DOF analysis and drag-under-mates;
  - `.bcasm` files.
- **Problems solved along the way:**
  - Revolved caps had inward face normals. Fixed by taking the normal as the cross product of
    the plane axes.
  - Angle mates were singular at 0°/180°. Fixed with a signed angle about a stored axis.
  - A cross product degenerated at 90°. Replaced by the sign-frozen form `a1 - s·a2`.
  - "Coincident" put a pin *inside* the plate. Fixed by making face-to-face mates
    anti-aligned by default, with a 180° pre-orientation.
  - Dragging tore the mates apart. Replaced by a weighted least-squares drag.
  - Dragging was slow. Face geometry is now cached per solve (about 47 ms per frame).
  - Mates broke after a part edit. Face references now fall back to kind, normal and radius.

### Phase 6: assembly mouse and animation (27 Sep)
- **Asked:** left-drag moves a component without rotating it, right-drag rotates it, and mates
  animate.
- **Built:**
  - translation-only drag;
  - rotation from the mouse motion;
  - a smooth animation when a mate is applied, where the centre of mass moves in a straight
    line and the rotation is interpolated.

### Phase 7: STEP assemblies and drawings (27 Sep)
- **Asked:** export the assembly as STEP; a SolidWorks-style drawing window with DXF, DWG and
  PDF output.
- **Built:**
  - **STEP with real assembly structure:** one product per part and placed instances, written
    through OCC's XCAF. SolidWorks opens it as an assembly, not as one multi-body part.
  - **Drawing sheets:** A4 to A0 with an ISO title block and projection symbol; standard
    3-view layout (first angle ISO or third angle ANSI) plus isometric; projected views that
    stay aligned with their parent.
  - **Views and dimensions:** exact hidden-line views; Smart Dimension on the sheet; notes;
    dimensions driven by the model that follow part edits.
  - **Export:** PDF and SVG (matplotlib); DXF with real DIMENSION entities, layers and ISO
    linetypes (ezdxf); DWG through ODA; single views at 1:1 DXF for laser cutting.
- **Details worth knowing:**
  - Hidden edges lying exactly behind visible ones are removed, as a drawing shows them.
  - Text is laid out by cap height, so PDF, DXF and the screen agree.
  - The ISO projection symbol was checked against references.

### Phase 8: autonomy and cross-session context (27 Sep)
- **Asked:** "what does commit changes mean?". After the explanation, the user said to
  commit future finished features without asking.
- **Asked:** a rule or skill so that regular Claude chats and Cowork find the app, work in
  it, and suggest improvements.
- **Built:**
  - a finder script bundled with the skill, which locates the repository on the PC or in a
    Cowork sandbox;
  - a user-level rule;
  - an **improvement log** (`docs/IMPROVEMENTS.md`) that every session appends to whenever
    something was missing, slow or awkward;
  - a build script that packages the skill for claude.ai.

### Phase 9: real projects in other sessions (27–28 Sep)
Parallel Claude sessions used BauyrCAD for real engineering work:
- a heavy industrial robot gripper, with vendor STEP models, FEA of welded plates and payload
  checks;
- a small robot arm, with motion studies and drawings;
- a cleanroom pass-through window.

These sessions added motion studies (driving mates through keyframes, animated GIFs),
interference detection and drawing fixes. They also logged about 20 friction points.

Two sessions fixed the same bug (a cut that removes nothing) independently. The better
version, which respects an explicit "reverse" and covers revolved cuts, was merged, and the
duplicate was dropped.

### Phase 10: acting on the friction log (28 Sep)
- **Asked:** "what suggestions for improvements do you see?", then "yes go ahead".
- **Findings and fixes:**
  - **FEA solve:**
    - *Before:* a 502,000-equation, 6 mm thin steel plate took **more than 9 minutes**
      (AMG-CG stalls on thin walls, then SuperLU crawls).
    - *After:* **20 s** with PARDISO.
    - PARDISO became a standard dependency, and the exe bundles about 415 MB of MKL
      libraries.
    - A memory estimate (measured at about 145 bytes per stiffness-matrix nonzero) now stops
      oversized models with a clear message instead of a crash.
  - **Assembly render:**
    - *Before:* over 10 minutes for 66 components.
    - *Cause:* one VTK actor per edge, 7,876 of them.
    - *After:* all edges merged into one mesh: **8 s**, then 1 s per view.
  - **Clearance:** `asm.clearance(a, b)` and `asm.clearance_sweep(...)` replace hand-written
    OpenCASCADE scripts.
  - **Regression:** a regression from one of the other sessions was caught by the tests and
    fixed. Clicking a circle picked its quadrant point instead of the circle; quadrant
    snapping now needs Shift, as in SolidWorks.

### Phase 11: a big usability batch (28 Sep)
- **Asked:**
  - rectangle sizes typed while drawing, as in AutoCAD;
  - numpad view keys;
  - a Measure tool;
  - Smart Dimension that stays active until Esc;
  - object snapping;
  - transitions between sketch planes;
  - a popup to sketch on a clicked face or plane;
  - a fix for "solid models are obscured when I rotate".
- **The "obscured" bug:**
  - *Cause:* the camera sat a fixed 1000 mm from its target, and the part was 1.6 m wide. So
    the camera ended up *inside* the model, and parallel projection clipped away everything
    behind it.
  - *Fix:* before every render, the camera is pushed back outside the model's bounding
    sphere, which changes nothing on screen, and the clipping range is re-fitted.
  - The bug was reproduced first (18 of 48 vertices clipped) and verified fixed (0).
- **Smart Dimension exiting:** the Enter that confirmed a dimension value was passed up Qt's
  parent chain from the popup to the main window, which treated it as "end the tool".
- **Built:**
  - **Dynamic input:** width, Tab, height, Enter; typed values become driving dimensions.
  - **Numpad views:**
    - 1 isometric, 3 front;
    - 4/6 and 8/2 side, top and bottom views, turning 45° per repeat press (first 90°,
      changed to 45° on request);
    - 7/9 roll;
    - 5 opens a **View Selector** cube;
    - smooth camera transitions (rotation interpolated about one axis).
  - **Object snaps:** endpoint, midpoint, centre, quadrant, intersection, nearest, and the
    model's own edges and vertices on the sketch plane, shown with AutoCAD-style markers.
  - **Context toolbar:** "Sketch / Normal To / Measure" appears on a face or plane click.
  - **Measure:** faces, edges and vertices; distance with dX/dY/dZ; centre, axis and normal
    distance; angle. Hidden edges can't be picked.
- **Then:** closed sketch regions are shaded while sketching, so it is obvious when Extrude
  will work.

### Phase 12: native SolidWorks files (30 Sep)
- **Asked:** export parts and assemblies as SolidWorks files, so that assemblies can be
  **moved** in SolidWorks. STEP imports every component fixed and carries no mates.
- **Why it's hard:** `.sldprt` / `.sldasm` are closed formats that only SolidWorks writes.
- **Built:** BauyrCAD now drives the user's own SolidWorks through its COM API:
  - Parts are rebuilt **feature by feature**: sketches with relations and driving
    dimensions, extrudes, cuts, revolves and selected contours. Every feature is checked
    against BauyrCAD's volume, retrying the other direction if needed.
  - Assemblies get floating components and **real mates**. A test reopens the file and
    shoves a mated pin sideways; SolidWorks pulls it back.
- **Problems solved along the way:**
  - SolidWorks misread plain Python lists (garbage coordinates), so arrays are now passed as
    typed double arrays.
  - Sketch regions can't be picked by coordinates, so regions are matched by sampling their
    edges.
  - SolidWorks' spline through the same points has a different shape, so points are sampled
    densely from BauyrCAD's curve.
  - Components can only be inserted into an assembly when their part is loaded.
  - COM handles have to be released before SolidWorks exits.

### Phase 13: the SolidWorks Features and Sketch tabs (30 Sep)
- **Asked:** "add all of the features from SolidWorks, starting from the ones in these
  screenshots" (the Features and Sketch command bars). The user's reasoning: one Fillet call
  is faster for Claude than drawing an arc and extruding it, so modelling takes fewer steps
  and fewer tokens.
- **Built, Features tab:**
  - Fillet, Chamfer, Shell, Draft, Hole Wizard (ISO clearance / tapped / counterbore /
    countersink sizes, callouts);
  - Linear / Circular Pattern and Mirror (of features or the body);
  - Swept, Lofted and Boundary Boss / Cut (including circular-profile sweeps along helices,
    i.e. springs);
  - Rib, Wrap (emboss / deboss onto cylinders), Intersect (regions from planes, filling
    closed voids);
  - reference Plane / Axis / Point, Helix, Curve Through Points, and Instant3D (drag an
    arrow to change an extrude's depth).
- **Built, Sketch tab:** Convert Entities, sketch Text (engrave part numbers),
  Display/Delete Relations, Repair Sketch.
- **The key design decision:** features store their edges and faces as geometric
  signatures, re-found on every rebuild (and sketches on faces or reference planes follow
  them). So changing an early dimension re-positions the fillets, holes and shells instead
  of breaking them - the "topological naming" problem every parametric CAD must solve.
- **Patterns** replay the tool bodies of seed features in one boolean, so a 100-hole pattern
  is one cut, not 100.
- **Checked** like before: volumes against hand formulas (fillet segment, counterbore,
  frustum, spring length from the helix), GUI tests that click edges in the real window,
  and screenshots of each panel.
- **Found along the way:** a shell of a body with fillets and counterbores makes OCC return
  an invalid solid (the error now suggests shelling earlier); a Python attribute named
  `tools` collided with the runtime tool list; PowerShell 5.1 re-encodes UTF-8 when used to
  edit files, so edits go through small Python patch scripts.

---

## 5. How the work was organised (the method)

1. **Plain-language requests, one batch at a time.** The user described features in
   SolidWorks and AutoCAD terms. Claude translated them into design decisions and asked only
   when a choice was really the user's (e.g. which improvement to build first).
2. **Claude verifies its own work before reporting.** Four kinds of check:
   - **Automated tests** for every feature (168 now), including GUI tests that drive the
     real window with real mouse and key events (Qt's QTest). Examples: clicking a hole to
     dimension it; typing `8`, `0`, Tab, `5`, `0`, Enter into dynamic input; pressing numpad
     keys and checking the camera direction.
   - **Screenshots and renders** that Claude opens and looks at. This caught layout problems
     such as overlapping title-block text, and confirmed the fixes.
   - **Engineering validation:** FEA against hand calculations (F/A, Lamé, cantilever),
     volumes against Pappus' theorem, and mate positions against analytic values.
   - **A self-test inside the packaged exe.** It runs every build and covers modelling,
     STEP round trip, FEA with PARDISO, every export format, assembly STEP, drawing export
     and the GUI.
3. **Reproduce before fixing.** Bugs were first reproduced with a small script: the
   clipping bug, the Smart Dimension exit and the slow render. The script then became a
   regression test.
4. **Measure before optimising.** Solver benchmarks, memory measurements and render profiling
   decided what to change. The render problem turned out to be actor count, not
   tessellation.
5. **Git as the safety net.** At first Claude asked before each commit. Later the user gave
   standing permission to commit finished, tested features, but never to push. Commits use
   descriptive messages and credit Claude as co-author.
6. **Context that survives between chats:**
   - `CLAUDE.md`: the working agreement read at the start of every session;
   - `docs/API.md` and `docs/ARCHITECTURE.md`: how to use and extend the code;
   - a Claude **skill** that finds the app from any chat;
   - **memory notes** holding key facts and decisions;
   - **`docs/IMPROVEMENTS.md`**: the friction log that drives what to build next.
7. **Parallel sessions.** Several Claude sessions worked on different projects at once, some
   in separate git worktrees. When their work overlapped, it was reviewed, the better
   version was kept, and it was merged with all tests green.

---

## 6. Notable problems and lessons

| Problem | Root cause | Lesson / fix |
|---|---|---|
| Exe "Failed to load Python DLL" | Ran the exe from PyInstaller's work folder | Keep build intermediates out of sight; self-test the frozen app |
| No CAD kernel for Python 3.14; OCCT 8 broke APIs | Wheel availability; API changes | Pin exact versions in a dedicated environment |
| Mate solver singular / flipping | Angle and cross-product formulations degenerate at 0/90/180° | Sign-frozen formulations, sensible default orientations |
| Mates lost after part edits | Faces were identified by exact geometry | Robust face references (kind, normal, radius fallback) |
| FEA minutes-long / out of memory | Thin walls defeat AMG; SuperLU fill-in | Use a proper direct solver (PARDISO); estimate memory first |
| Assembly render > 10 min | One VTK actor per edge | Batch geometry into few actors |
| Model "obscured" when rotating | Camera inside a large model (parallel projection) | Keep the camera outside the bounding sphere before every render |
| Smart Dimension exited after one dimension | Enter propagated from a popup to the window | Ignore keys aimed at other windows |
| Diameter picks broke after another session's change | Quadrant snap points beat the circle | Shift-click for quadrant snaps; tests caught it |
| Two sessions fixed the same bug | Parallel work | Compare, keep the tested version, merge carefully |
| DWG export | Closed format, no free writer | Convert DXF with the free ODA File Converter |

---

## 7. What it looks like to use

- **Part window:**
  - feature tree with Front/Top/Right planes;
  - sketcher with the command line, dynamic input, snaps and shading of closed regions;
  - Extrude/Cut/Revolve with contour picking;
  - Simulation tab: material, fixtures, loads, mesh, run, results;
  - View tab: views, View Selector, Measure.
- **Assembly window:** insert parts, mates, left-drag to move, right-drag to rotate, animated
  mating, STEP export, Make Drawing.
- **Drawing window:** standard, model and projected views; Smart Dimension; title block;
  save as PDF, DXF or DWG.
- **For scripts and Claude:** `from bauyrcad.api import Part, Assembly, Drawing`. A model is a
  short Python script with named dimensions. Claude renders PNGs to check it and writes
  `.bcad`, `.bcasm`, `.bcdrw` and STEP files, which the GUI opens and live-reloads.

---

## 8. Current limitations (roadmap)

- **Modelling:** no sheet metal, weldments (separate parts for now), multi-body parts,
  variable-section sweeps or guide curves yet. Shell can fail on complex bodies (kernel
  limitation).
- **FEA:** no contact, bolt elements or partial-face fixtures yet.
- **Drawings:** no section or detail views, GD&T, hole callouts or BOM tables yet.
- **Assemblies:** no limit mates, a finite-difference mate Jacobian (slower for big
  assemblies), and no motion-study panel in the GUI (the API has it).
- **SolidWorks files:** written by the user's SolidWorks via COM; the new features (fillet,
  hole, shell, pattern...) are not mapped yet, so such parts arrive as a solid body.
- **Licensing:** Gmsh is GPL, which constrains a closed-source commercial release.

---

## 9. If you want to build something similar with Claude

1. **Start small but real.** One end-to-end slice (sketch → extrude → FEA → STEP) that
   produces a working app. Then grow it in batches.
2. **Describe features the way an expert user thinks** ("like SolidWorks Smart Dimension",
   "like AutoCAD dynamic input"). Claude maps that onto known behaviour.
3. **Insist on verification.** Ask for tests, screenshots and hand-calc checks, and for
   bugs to be reproduced before they are fixed.
4. **Give Claude a written working agreement** (`CLAUDE.md`) and a place to log friction.
   Knowledge then carries across sessions.
5. **Use the tool on real work** as early as possible. The best improvements came from real
   projects (thin-plate FEA speed, vendor STEP handling, clearance checks).
6. **Use git.** Once you trust the test suite, let Claude commit finished work on its own,
   but keep pushing and publishing under your control.
7. **Expect the environment to matter.** Python versions, pinned libraries, packaging and
   large native dependencies (CAD kernel, MKL) took real effort. A self-test inside the
   packaged app is worth it.
