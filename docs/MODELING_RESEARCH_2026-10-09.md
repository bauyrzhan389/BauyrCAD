# BauyrCAD: faster, more capable modeling

Research date: 9 October 2026. Repository baseline: `ad445e2` on `bauyrcad-0.3`.

## Implementation tracker

The user authorized progressing through this roadmap on 9 October 2026. Keep this
file as the durable backlog and update this tracker as each tested increment lands.
The research baseline below remains historical; consult this tracker and CHANGELOG
for subsequent implementation.

Continuation is authorized across scheduled runs until completion or a material
decision is needed. The hourly `continue-bauyrcad-modeling-roadmap` heartbeat resumes
unfinished work when available; usage limits cannot be bypassed. Preserve checkpoints
here and do not mark partially implemented subsystems complete.

| Item | Status | Delivered increment / remaining scope |
|---|---|---|
| 01 Shared selection | In progress | Revolve accepts straight model edges/reference axes with overlapping-candidate previews and filters. Added viewport Select Other for part faces, feature-input faces/edges/vertices, Measure and assembly mates; hidden-face outlines and component names aid disambiguation. Remaining: consistent selection chips/invalid-input explanations, broader sketch entity cycling and simulation-command adoption. |
| 07 Named parameters | In progress | Typed length/angle/scalar values, persistent equations, cycle/unit validation, safe model trials, central GUI table and API. Driving sketch dimensions, extrude/revolve, fillet/chamfer, shell/draft and pattern fields participate. Sketch dimension popups now support staged =equations, unlinking, local undo/redo and cancellation. Supported feature controls now offer staged equation entry, precise previews and safe commits. Added holes, ribs, reference-plane offsets/angles and helix dimensions with active-mode validation. Remaining: broader specialist fields as their commands evolve; next priority is reference repair (08). |
| 08 Reference repair | In progress | Compatible, unique exact matches and bounded, clearly preferred moved matches; read-only API diagnostics; unresolved applied-feature selections remain visible and can be replaced explicitly. Ranked candidate menus preview hidden faces/edges before explicit replacement. Revolve now recognizes moved edge axes and blocks invalid-axis commits. Remaining: sketch support and simulation repair panels, persistent provenance. |
| 14 Feature end conditions | Queued | Up-to-face/next and offset-from-face. |
| SW Native export | Priority / In progress | User request 11 October: automatic editable SLDPRT creation steps and SLDASM mates for every BauyrCAD tool. Fix QAction boolean destination bug; require complete native export in GUI. Existing feature/mate bridges remain; full coverage gaps tracked below. |
| Other items | Backlog | Preserve the priorities and dependencies below; mark complete only after implementation and verification. |

First increment verification: 279 non-SolidWorks tests passed in the full run;
the seven SolidWorks checks passed when rerun outside the restricted environment
after COM server startup failed inside it (286 tests total). The focused GUI
checks exercised overlapping picks, previews, filters, undo/redo and save/reopen;
the screenshot was inspected. Windows executable and ZIP rebuilt; packaged
self-test passed. Native SolidWorks feature transfer for the new axis types remains
an explicit exact-solid fallback.

Second increment verification: full regression suite **291 passed** (including
SolidWorks, run outside the restricted environment). Real-window checks cover
occluded Shell face selection, transformed/rotated assembly geometry, mate picks,
Escape cancellation, active-input filters, hidden-object exclusion and measurement
between front/rear faces. Screenshots inspected under
`projects/select-other-final/`. Windows executable and ZIP rebuilt; packaged
self-test passed. No saved-file format change was needed.

Third increment verification (10 October): **299 tests passed**, including
SolidWorks integration. Real-window tests cover the parameter table, validation,
linked controls, sketch editing/deletion and undo/redo; screenshot inspected at
`projects/parameters-verification/parameters.png`. Core tests cover save/reopen,
atomic rejected changes, units, sketch radians/feature degrees, pattern counts,
and a 200-parameter forward chain and cycle. Windows executable and ZIP rebuilt;
packaged self-test passed, including the named-parameter engine and GUI dialog.
Numeric-only files remain format 1; equation-enabled files use format 2.
Fourth increment verification (10 October): **302 tests passed**, including
SolidWorks integration. Inline sketch equations are staged with geometry; tests
cover local undo/redo, whole-sketch cancellation, new-sketch IDs, unlinking with
legacy calculator input, invalid-input correction, save/reopen and document undo.
Real-window popup screenshots inspected under `projects/sketch-equations-verification/`.
Windows executable and ZIP rebuilt; packaged self-test passed.
Fifth increment verification (11 October): **306 tests passed**, including
SolidWorks integration. Supported feature controls accept staged equations with
precise previews. Tests cover new/existing features, cancellation, unlinking,
save/reopen, undo/redo, degree conversion, integer pattern counts and rejection of
impossible fillets without changing the model or undo history. Real-window panel
and popup screenshots inspected under `projects/feature-equations-verification/`.
Windows executable and ZIP rebuilt; packaged self-test passed, including feature
equation editing inside the frozen application.
Sixth increment (11 October): added typed bindings and inline equation controls
for holes, ribs, reference planes and helices. Signed plane offsets and fractional
turns are valid; inactive fields are rejected rather than ignored. Fixed standard
countersink angle overrides. Focused core/real-window tests passed (16); helix
screenshot inspected under `projects/extended-equations-verification/`.
Full regression: **312 tests passed**, including SolidWorks integration. Windows
executable and ZIP rebuilt; packaged self-test passed.
Seventh increment (11 October): conservative face/edge matching and explicit
unresolved-row repair in applied-feature selection boxes. Core/API tests cover
incompatible/duplicate exact signatures, nearby ambiguity, distant candidates,
legacy references and non-mutating historical-body diagnostics. Real-window test
covers blocked commit, repair, Cancel, undo/redo and save/reopen; screenshot
inspected at `projects/reference-repair-verification/missing-edge.png`.
Full regression: **316 tests passed**, including SolidWorks integration (saved
log: `projects/reference-repair-verification/full-tests.txt`). Windows executable
and ZIP rebuilt; packaged self-test passed.
Eighth increment (11 October): applied-feature selection boxes offer ranked
replacement candidates with nearby/distant labels, preview outlines and explicit
acceptance. Tests cover face/edge preview, Escape, Cancel, stale menu rejection,
empty candidates and commit; screenshots inspected under
`projects/reference-repair-verification/edge-candidate-preview.png` and
`edge-candidates.png`. Full suite: **319 passed**, including SolidWorks. Windows
app and ZIP rebuilt; packaged self-test passed. Saved full-suite log:
`projects/reference-repair-verification/preview-full-tests.txt`.
Ninth increment (11 October): Revolve resolves saved edge signatures against its
historical body before selecting the axis-list entry. Missing/ambiguous references
remain available for explicit replacement, and axis validation blocks invalid OK
without an undo checkpoint. Focused GUI tests cover upstream geometry movement,
Cancel, rebuilding the choices, save/reopen, undo/redo and missing/ambiguous repair.
Screenshot inspected: `projects/revolve-repair-verification/moved-axis.png`.
Full regression: **322 tests passed**, including SolidWorks integration. Saved
log: `projects/revolve-repair-verification/full-tests.txt`. Windows executable
and ZIP rebuilt; packaged self-test passed.
Next: continue item 08 with sketch-support and simulation repair panels, then
persistent feature provenance. Candidate previews and basic Revolve repair are
now delivered; geometry-signature matching still has inherent identity limits.
The new `match()` diagnostics are shared by FaceRef/EdgeRef; `resolve()` remains
backward-compatible (index or None). Do not claim geometry signatures guarantee
identity through topology changes. Item 14 follows this reference foundation.

## User-priority addition: complete native SolidWorks export (11 October)

Requested result: the standard Export to SolidWorks action automatically produces
editable `.SLDPRT` feature histories and `.SLDASM` assemblies with native mates.
A single imported solid is not completion of this request. Prioritize this work
before further sketch-support repair; continue the original backlog afterward.

- Immediate fix: QAction checked=False was being treated as path/folder. Both
  part and assembly actions must open their destination dialogs correctly.
- GUI must require a complete native result, never silently flatten a part or
  save an assembly with omitted mates. Script callers can explicitly retain
  legacy geometry fallback. Known gaps should fail with feature names before
  file creation; runtime mismatches must not save a simplified replacement.
- Existing translators: sketches, extrude/cut, revolve/cut, fillet, chamfer,
  shell, draft, Hole Wizard, linear/circular patterns, mirror, reference planes,
  helix, sweep and loft (some modes unsupported).
- Remaining feature coverage: imports inserted after other steps; rib,
  wrap, intersect, boundary; reference axes/points/curves; body patterns, sketch
  text, tube sweeps, ruled/point lofts and other mode-specific restrictions.
  Preserve suppressed steps/states rather than skipping them. Transfer named
  parameters/equations and audit all sketch relation/dimension warnings.
- All seven current mate types have translators: coincident, concentric,
  parallel, perpendicular, distance, angle and lock. Expand native reopen tests
  to every type and suppression, verify names/values/alignment and remaining
  motion; fail explicitly if a mate is omitted or changes placement.
- Add a feature/mode coverage matrix and real SolidWorks reopen fixtures as each
  translator lands. Native file extension alone does not prove editable history.
- Imported source geometry cannot recover creation history that was never in
  BauyrCAD; represent it as a native Imported base and preserve later operations.

Current increment verified: boolean destination fix and strict GUI export checks implemented.
All 326 tests passed, including actual SolidWorks part exports and native assembly mates.
The real export report screenshot was inspected at
`projects/solidworks-export-verification/native-export-report.png`; it lists Sketch1 and
Boss-Extrude1 in the saved SLDPRT. Windows app rebuilt, frozen self-test passed and
`dist/BauyrCAD-0.4.0-win64.zip` regenerated. Universal feature and mate-state coverage
remains unfinished and is the next priority.

Imported-base increment verified: a first imported solid is preserved as a native
Imported base, with supported later operations rebuilt. STEP/IGES/BREP fixtures
reopen exported files and suppress a later cut to recover the preceding geometry.
Full regression: **329 passed**, including SolidWorks; log saved at
`projects/solidworks-export-verification/imported-full-tests.txt`. Windows app and
ZIP rebuilt; packaged self-test passed. Coverage matrix:
[SOLIDWORKS_EXPORT_COVERAGE.md](SOLIDWORKS_EXPORT_COVERAGE.md). Later imports,
suppression states and other translator gaps remain open.

## Recommendation

Build a coherent editing workflow before adding many specialist commands. The best next investments are **shared selection tools, named parameters and equations, reliable references, and richer feature end conditions**. Then add direct editing of imported solids, proper multibody parts, configurations, and reusable components. Sheet metal and structural frames should be the first substantial specialist workbenches for mechanical fabrication.

This ordering is my assessment of BauyrCAD's needs, informed by the sources below and the local code. It is not a vendor ranking or a measured productivity comparison. No comparative modeling benchmark was run.

## Research method and coverage

I read the complete available transcripts of the linked TECHNIA video, SolidProfessor's accompanying SOLIDWORKS 2027 overview, Shapr3D's variables tutorial, Autodesk's dynamic-block flip/rotate tutorial, and Autodesk's Fusion Press Pull tutorial. YouTube transcripts were automatically generated; terminology was interpreted in context. I also reviewed primary vendor documentation and written tutorials from SOLIDWORKS, Autodesk, Onshape and Shapr3D.

The source ledger distinguishes transcripts from written material. A tutorial landing page alone was not treated as evidence that its video had been watched. Findings were checked against current feature implementations, selection/navigation code, regeneration, reference resolution, the changelog and existing improvement log. “Missing” below means no exposed implementation was found in this audit, not a claim that OpenCASCADE cannot perform the operation.

Older tutorials remain useful for established workflows. The 2027 release videos describe what their presenters demonstrated; they do not establish availability in every license or deployment. The report deliberately gives everyday modeling more weight than novelty.

## What the linked video actually suggests

[TECHNIA: What's New in SOLIDWORKS 2027?](https://www.youtube.com/watch?v=e1xKbMm_c0A), published 6 October 2026:

| Segment | Demonstrated idea | Recommendation for BauyrCAD |
|---|---|---|
| [1:01](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=61s) | E57 point-cloud reference import | Later: scan references, clipping and measurement. This is not automatic conversion into editable solids. |
| [2:13](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=133s) | Assembly rotation locks | Improve mate controls and remaining-motion feedback. This concerns components, not camera orbit. |
| [3:00](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=180s) | Variability tables | High value: begin with named parameters, then configurations and assembly variants. |
| [4:27](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=267s) | Wires/cables represented as parts | Later routing workbench; preserve length, identifiers and BOM participation. |
| [5:43](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=343s) | Chord-width fillets | Useful small extension after reliable fillet selection and failure feedback. |
| [6:01](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=361s) | Automatic weld paths | Build structural members, joints and cut lists first; require review of inferred welds. |
| [6:49](https://www.youtube.com/watch?v=e1xKbMm_c0A&t=409s) | Background export tasks | Adapt to a local queued export system; an enterprise cloud service is unnecessary for the initial workflow. |

[SolidProfessor's Top 10 overview](https://www.youtube.com/watch?v=ZqXsa9KuAic) adds useful qualifications. At 3:00 it describes coincident rotation locking for linear edges/conic faces, explicitly excluding planar faces. It also demonstrates sheet-metal stamps across bends, selected drawing-view freezing, tolerance tables, and interface-stress plots. For BauyrCAD these imply later sheet-metal, drawing and analysis improvements, not immediate prerequisites for ordinary solid modeling. Its weld demonstration still requires manual review. Drawing freezes should always have visible stale-state warnings.

## What BauyrCAD already has

Do not restart these features as new projects:

| Area | Current baseline | Remaining opportunity |
|---|---|---|
| Revolve | Earlier straight sketch lines can be selected as axes, with saved references | Model edges/reference-axis objects; consistent selection across commands |
| Navigation | Middle-drag pivots near the cursor on the model; modified middle-drag pans | Section, isolate, saved views and navigation preferences |
| Sketch relationships | Add Relations, additive selection, multi-point horizontal/vertical, collinear and other standard relations | Better explanations of remaining freedom and conflicting constraints |
| Precision input | Snaps, numeric expressions, command aliases/search and repeat | Saved named variables and linked expressions, rather than one-time arithmetic |
| Solid features | Extrude/revolve, fillet/chamfer/shell/draft, holes, patterns/mirror, sweep/loft/boundary/rib, references and curves | Deeper end conditions, direct editing, body management and surface controls |
| Editing | History, feature suppression, feature-edit rollback display, limited Instant3D | General rollback/reorder workflow, dependency display and reference repair |
| Assemblies | Components, mates, dragging, motion/interference capabilities | Easier placement, connectors, reuse, configurations and broader GUI access |
| Manufacturing output | Drawings, exports, flat assembly BOM and balloons, Pack and Go | Associative annotations, richer sheets, configurable BOM and complete export packages |

Important implementation findings: `ExtrudeFeature` stores numeric depth; the command-line evaluator does not provide a persistent parameter graph. `RegenContext` primarily carries one aggregate shape, not a named body collection. `Document.regenerate()` rebuilds from the start. `EdgeRef` uses geometric signatures; this is useful but cannot provide the same guarantee as persistent feature provenance. Loft/boundary commands should not be mistaken for a full surface-modeling workbench.

Audit anchors: [document regeneration](../bauyrcad/core/document.py), [feature data](../bauyrcad/core/features.py), [additional features](../bauyrcad/core/features_more.py), [reference resolution](../bauyrcad/core/refs.py), [numeric input](../bauyrcad/gui/command_line.py), [viewer](../bauyrcad/gui/viewer.py), [assembly model](../bauyrcad/core/assembly.py), [change history](../CHANGELOG.md). These paths identify the inspected code; statements describe the baseline above.

## Prioritized feature list

P1 = next foundation; P2 = next major capability; P3 = specialist/later. Size is relative: S = small addition, M = bounded feature, L = several sessions, XL = a subsystem likely requiring multiple releases. These are scope estimates, not delivery promises. Sources explain the inspiration; the proposed scope and priorities are my recommendations.

### Faster everyday work

| ID | Feature and concrete workflow | Priority / size | Basis and dependencies |
|---|---|---|---|
| 01 | **Select Other and shared reference picker:** cycle overlapping faces/edges/sketch entities, hover-preview candidates, filter valid inputs, retain selections while editing fields | P1 / M–L | [AutoCAD selection cycling][S6], [Onshape selection][S7]; extend the existing Revolve improvement |
| 02 | **Context command palette:** selected profile offers extrude/revolve; edge offers fillet/chamfer; face offers sketch/offset. Customizable S-key palette and recent commands | P1 / M | [SOLIDWORKS UI][S8], [Shapr3D adaptive UI][S9]; build on current context actions and command search |
| 03 | **Section, isolate and temporary transparency:** inspect an internal feature, edit it and restore prior visibility in one action | P1 / M | Local workflow gap; shared selection and consistent part/assembly behavior |
| 04 | **Consistent feature panels:** remembered width, units, numeric focus, selection chips, preview, Apply/Enter/Escape and meaningful invalid-input feedback | P1 / M | Local friction; [SolidProfessor][S2] panel-width example. Existing focus fixes are the baseline |
| 05 | **Rebuild/preview responsiveness:** measure feature timings, debounce edits, cache valid intermediate results, discard obsolete previews, show progress/cancel | P1 / L | Local full-regeneration audit; protect correctness before optimizing |
| 06 | **Recovery and design checkpoints:** recoverable autosaves and named local milestones, with an explicit restore preview | P2 / M–L | Local recommendation; complement undo and saved files rather than silently overwrite them |

### More reliable design intent

| ID | Feature and concrete workflow | Priority / size | Basis and dependencies |
|---|---|---|---|
| 07 | **Named parameters and equations:** `plate_t`, `hole_pitch`, `clearance`; drive sketches, extrusions, shells and patterns from a central table | P1 / L | [Shapr3D transcript][S3], [variables reference][S10]; units, dependency graph, cycle detection and serialization |
| 08 | **Reference repair and dependency inspector:** show which face/edge was lost, affected children, and candidate replacements; confirm remapping | P1 / L–XL | Local signature-reference audit; prerequisite for reliable extensive edits |
| 09 | **Sketch intent assistant:** show remaining movement, highlight the smallest relevant conflict, preview suggested dimensions/relations before accepting | P1 / L | [SOLIDWORKS automatic sketch operations][S11]; extend existing relations/DOF/repair |
| 10 | **Feature-specific profile diagnosis:** highlight open gaps, duplicate segments, self-intersections and unsuitable contours before extrusion/revolution | P1 / M | [Check Sketch for Feature][S12]; integrate current Sketch Repair instead of duplicating it |
| 11 | **Associative projections:** projected edges follow their source; list external dependencies; offer freeze, update or break-link | P2 / L | Current Convert Entities is a copy; [managed context][S13] informs explicit update controls |
| 12 | **Configurations/design table:** one bracket with several lengths, suppression states and part numbers; select a configuration per assembly instance | P2 / XL | [Fusion configuration parameters][S14], [TECHNIA][S1]; depends on 07,08 and identity rules |
| 13 | **History navigation and safe reordering:** rollback bar, parent/child highlighting, folders/search and legal drag-reorder with preview | P2 / L | Extend existing feature-edit rollback; dependency validation first |

### More complete part modeling

| ID | Feature and concrete workflow | Priority / size | Basis and dependencies |
|---|---|---|---|
| 14 | **Up-to-face/next and offset-from-face:** a boss ends at its mating surface and follows later changes; separate start offset and second-direction controls | P1 / L | [SOLIDWORKS surface uses][S15]; shared picker and persistent references |
| 15 | **Direct editing of STEP solids:** offset/move planar faces, change cylindrical hole diameter, remove simple holes with healing; later replace faces | P2 / XL | [Fusion Press Pull transcript][S5], [modify tools][S16]; start with explicitly supported geometry |
| 16 | **Named multibody parts:** new-body/join/cut/intersect, target bodies, visibility and material per body, split and save bodies as parts | P2 / XL | Local aggregate-body limitation; foundation for tooling, weldments and derived parts |
| 17 | **Reusable feature/component library:** insert a mounting pattern, rib or connector by a few dimensions and placement references | P2 / L | [AutoCAD dynamic blocks][S4], [Onshape custom features][S17]; parameters and versioned definitions |
| 18 | **Pattern extensions:** follow a curve, sketch points or a table; maintain spacing/orientation and allow instance exceptions | P2 / L | Extend existing linear/circular/mirror patterns; body/reference foundations |
| 19 | **Fillet diagnostics and controls:** show failing edges, allow tangent-chain selection and preview alternatives; add chord-width mode where supported | P2 / M–L | [TECHNIA][S1], [SolidProfessor][S2]; avoid promising arbitrary kernel success |
| 20 | **Surface workbench:** surface extrude/revolve, trim/extend, stitch/knit, thicken and replace-face; add continuity inspection and guide-controlled lofts | P3 / XL | [SOLIDWORKS surfaces][S15]; separate from existing solid loft commands |

### Faster assemblies and fabrication

| ID | Feature and concrete workflow | Priority / size | Basis and dependencies |
|---|---|---|---|
| 21 | **Mate connectors and placement preview:** reusable coordinate frames on a part; snap to another frame, flip orientation and show remaining movement | P2 / L | [Onshape mate connectors][S18]; extend existing mates rather than add another solver |
| 22 | **Assembly reuse:** component patterns, replace-component with reference repair, organized subassemblies and reusable insertion points | P2 / XL | Local mechanical workflows; 08,12,17,21 |
| 23 | **Managed top-down design:** edit a bracket against surrounding parts using a captured context; explicit update/freeze and cycle warnings | P2 / XL | [Onshape managed context][S13]; 08,11,16 and stable component identity |
| 24 | **Motion and clearance panel:** expose existing API tools through keyframes, mate values and a collision/clearance timeline | P2 / L | Existing roadmap/API; use bounding-box screening and report sampling limits |
| 25 | **Sheet-metal fundamentals:** thickness/radius rules, flange, relief, bend allowance/K-factor, unfold/refold, synchronized flat pattern and DXF | P2 / XL | [Onshape sheet-metal model][S19], [flat-pattern drawings][S20]; validate against known bend examples |
| 26 | **Structural frames/weldments:** profile library, members along paths, trim/extend joints, end treatment and length cut lists | P2 / XL | Local frame/rib fabrication needs; named bodies first; automatic weld finding later |
| 27 | **Manufacturing drawings that follow edits:** reliable dimension/balloon references, sections/details, tolerances and model dimensions; mark unresolved/stale annotations | P2 / XL | Existing drawing/BOM baseline; [SolidProfessor][S2] informs selected-view update controls |
| 28 | **Local export queue:** checked PDF/DXF/STEP packages with naming templates, revision/configuration selection, progress and failure report | P2 / M–L | [TECHNIA][S1]; immutable job snapshot and stale-output checks |
| 29 | **Large-assembly mode:** shared tessellation for repeated parts, coarse previews, selective loading and simplified representations | P2 / L–XL | Existing rendering improvements are the baseline; profile before implementation |
| 30 | **Routing:** 3D paths, bend-radius checks, tube/cable lengths, endpoints and BOM properties | P3 / XL | [TECHNIA][S1]; curves, assemblies and component metadata first |
| 31 | **Scan references:** point-cloud import, clipping, alignment and measurement; later separately scoped reconstruction | P3 / XL | [TECHNIA][S1]; justify with actual scan-based projects and memory benchmarks |
| 32 | **Versioned custom features:** package recurring Python constructions with a parameter form, preview, references and regeneration | P3 / L–XL | [Onshape custom features][S17]; build on BauyrCAD's existing scripting API |

## What the first features must actually pass

1. **Selection:** choose a rear face in a dense assembly without hiding five components; preview the selected item and report why invalid inputs cannot be used. Revolve should accept valid straight edges/reference axes while retaining its coplanarity and nonzero-axis checks. Save/reopen must preserve the chosen reference.
2. **Parameters:** change `plate_t` once and update sketch dimensions, extrusion and dependent clearance. Rename a variable without breaking links. Reject unit mismatch and circular equations before committing. Undo, save/reopen and scripting must agree.
3. **Reference repair:** alter upstream topology in a fixture model; a dependent fillet must either retain its intended edge or become visibly unresolved. It must never quietly jump to a plausible but wrong edge. Remapping must be undoable.
4. **Up-to-face:** move the target face and obtain the intended feature extent. A deleted target, reversed direction or absent intersection must produce an actionable error. Preview and final geometry must agree.
5. **Sketch diagnosis:** display the offending gap or relation, retain the user's geometry on failure, and let the user accept or reject proposed changes. Existing fully constrained sketches must remain unchanged.
6. **Direct editing:** import a STEP plate, move a supported planar face and resize a cylindrical hole; save editable history and export a valid solid. Unsupported blends should fail clearly without damaging the previous body.
7. **Multibody:** create two touching bodies without unrequested fusion; cut only the chosen body; preserve names, materials and references after save/reopen and export.
8. **Configurations:** produce three pin lengths from one file, place different variants in an assembly and obtain correct quantities/part numbers in BOM/export. Changing one instance's choice must not alter every instance.
9. **Sheet metal:** a known bracket must produce consistent formed and flat geometry, bend notes and a 1:1 DXF. Changing thickness must update bend allowance. Manufacturing rules need explicit values, not hidden defaults assumed correct for every shop.
10. **Background export:** edit the live model while exporting a captured revision. Output must identify its revision and configuration; failed/stale jobs must not masquerade as current successful exports.

## Delivery sequence and architecture

**Wave 1 — everyday interaction:** 01–04 and 10, plus timing instrumentation from 05. Share the picker across commands; do not create another isolated Revolve-only widget. Keep the current camera pivot and sketch relations as regression cases.

**Wave 2 — editable design intent:** 07–09 and 14, then 11 and 13. Introduce typed parameter values and a dependency graph in the Qt-free core. Preserve backwards compatibility with numeric-only files. Add provenance to references incrementally; provide explicit repair where certainty is impossible.

**Wave 3 — imported and reusable designs:** 15–18, 21–23. A named body model and stable identities need a documented file migration before configurations, derived parts or top-down links proliferate. Store direct edits as features instead of mutating an imported shape invisibly.

**Wave 4 — fabrication:** prioritize sheet metal or frames according to the next real project; complete drawings and export packages alongside them. Avoid building both specialist workbenches simultaneously before either has a validated end-to-end example.

**Wave 5 — specialist expansion:** surfaces, routing, scan references and custom-feature packaging as justified by actual jobs. Advanced contact/nonlinear FEA is a separate roadmap: retain the existing Nastran export investment and do not confuse a stress-plot option with an implemented contact solver.

All geometry operations should remain in the approved core geometry layer. GUI, scripting and saved documents should use the same feature definitions. Background geometry jobs need controlled execution or process isolation; moving unrestricted OCC work into arbitrary threads is not a sufficient design. Incremental regeneration must invalidate downstream geometry, drawings and analysis consistently.

## How to measure whether this makes modeling faster

Create repeatable fixtures before declaring a speed improvement:

| Task | Measure |
|---|---|
| Fully constrain a slotted bracket and change its width | Completion time, clicks, unresolved DOF, unintended changes |
| Pick a rear cylindrical surface among overlapping components | Time to correct selection, wrong picks, hide/show operations |
| Produce three related bracket sizes | Repeated edits, elapsed time, correct dimensions/BOM identities |
| Modify a vendor STEP mounting plate | Successful supported changes, validity, export/reopen accuracy |
| Assemble a hinge and check clearance | Mate actions, remaining DOF, missed interference at sampled positions |
| Edit an upstream dimension in a 100-feature test part | Median/p95 preview and rebuild time; broken or misdirected references |
| Export a drawing/STEP/DXF package | Manual steps, stale output count, recovery from a failed job |

Use the same hardware, files and scripted/recorded tasks before and after. Proposed first targets: halve wrong-selection retries, reduce repetitive parameter-edit steps by at least half, and eliminate silent reference remapping in the regression fixtures. These are acceptance ambitions, not measured results. Record failures as well as successful times; faster wrong geometry is not a gain.

## Sources and further viewing

Sources below were read as indicated. The priority list is an adaptation to BauyrCAD, not a claim that every competitor implements every proposed detail.

- **S1 — transcript:** [TECHNIA, SOLIDWORKS 2027 overview][S1]. User's linked video; timestamped above.
- **S2 — transcript:** [SolidProfessor, SOLIDWORKS 2027 Top 10][S2]. Qualifications and adjacent manufacturing features.
- **S3 — transcript:** [Shapr3D, Parametric design with variables and expressions][S3]. Especially 0:35–6:50 for global values/feature dimensions and 16:19 onward for repeated design changes.
- **S4 — transcript:** [Autodesk, Define a dynamic block that flips and rotates][S4]. Reusable definitions with controlled actions; the proposed 3D library is an adaptation, not an AutoCAD block clone.
- **S5 — transcript:** [Autodesk, Modify solid bodies with Press Pull][S5]. Profile/face/edge selection maps to different appropriate commands.
- **S6 — written tutorial with silent video:** [Autodesk, Work with Stacked Objects][S6]. Selection cycling and previews; no spoken transcript claimed.
- **S7 — written tech tip:** [Onshape, Getting the Right Selection][S7]. Select Other and selection techniques.
- **S8 — written tutorial:** [SOLIDWORKS, Customizing the UI][S8]. Context shortcuts and optional gestures. Preserve BauyrCAD's established mouse mappings by default.
- **S9 — documentation:** [Shapr3D, Adaptive user interface][S9]. Selection-sensitive command suggestions; vendor speed claims were not adopted.
- **S10 — documentation:** [Shapr3D, Variables and expressions][S10]. Parameter types, expressions and management.
- **S11 — documentation:** [SOLIDWORKS, Automatic Sketch Operations][S11]. Fully Define Sketch and SketchXpert; established functionality, not presented as new in 2027.
- **S12 — documentation:** [SOLIDWORKS, Check Sketch for Feature Usage][S12]. Profile validity depends on the intended operation.
- **S13 — written tech tip:** [Onshape, Managed In-Context Design][S13]. Explicit context updates and versioned references.
- **S14 — documentation:** [Fusion, Configure user parameters][S14]. Parameters exposed as configuration-table columns.
- **S15 — documentation:** [SOLIDWORKS, Surfaces][S15]. Surface operations and their use in solid modeling.
- **S16 — documentation:** [Fusion, Modify tools for solid bodies][S16]. Offset, replace and split operations.
- **S17 — vendor feature description:** [Onshape, Custom Features][S17]. First-class reusable features; marketing reliability claims are not treated as guarantees.
- **S18 — written tech tip:** [Onshape, Using Mate Connectors][S18]. Frames, placement and mate orientation.
- **S19 — documentation:** [Onshape, Sheet Metal Model][S19]. Formed/flat model and sheet-metal controls.
- **S20 — written tech tip:** [Onshape, Inserting Flat Patterns into Drawings][S20]. Bend notes/lines and flat-pattern views.

Optional follow-up viewing, not counted as reviewed transcripts: [Onshape Sheet Metal Essentials, 40 minutes](https://www.onshape.com/en/resource-center/videos/sheet-metal-essentials) and [SOLIDWORKS: An Hour of Tips and Tricks](https://www.solidworks.com/media/level-hour-solidworks-tips-and-tricks). Their landing-page descriptions were reviewed only.

[S1]: https://www.youtube.com/watch?v=e1xKbMm_c0A
[S2]: https://www.youtube.com/watch?v=ZqXsa9KuAic
[S3]: https://support.shapr3d.com/hc/en-us/articles/18763796906396-Parametric-design-with-variables-and-expressions
[S4]: https://www.autodesk.com/learn/ondemand/curated/dynamic-blocks/4BRF9qIvsyO0L5pfUmlCZI
[S5]: https://www.autodesk.com/learn/ondemand/tutorial/modify-solid-bodies-with-press-pull?guid=AP-SOLID-PRESS-PULL
[S6]: https://help.autodesk.com/cloudhelp/2023/ENU/AutoCAD-DidYouKnow/files/GUID-D6B80776-EB76-4B70-BAC3-1E3778B910DA.htm
[S7]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-getting-the-right-selection
[S8]: https://blogs.solidworks.com/products/solidworks/making-solidworks-feel-like-home-customizing-the-ui/
[S9]: https://support.shapr3d.com/hc/en-us/articles/7873882619548-Adaptive-user-interface
[S10]: https://support.shapr3d.com/hc/en-us/articles/18320182069916-Variables-and-expressions
[S11]: https://help.solidworks.com/2019/english/SolidWorks/acadhelp/c_Automatic_Sketch_Operations_AcadHelp.htm
[S12]: https://help.solidworks.com/2026/english/SolidWorks/Sldworks/HIDD_SKETCH_DIAGNOSTICS.htm
[S13]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-using-managed-in-context-design
[S14]: https://help.autodesk.com/cloudhelp/ENU/Fusion-Configurations/files/CFG-USER-PARAMETERS.htm
[S15]: https://help.solidworks.com/2023/english/SolidWorks/Sldworks/c_Surfaces_Overview.htm
[S16]: https://help.autodesk.com/cloudhelp/ENU/Fusion-Model/files/SLD-MODIFY-SOLID-BODY.htm
[S17]: https://www.onshape.com/en/features/custom-features
[S18]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-using-mate-connectors
[S19]: https://cad.onshape.com/help/Content/PartStudio/sheet_metal_model.htm?cshid=sheetmetalstart
[S20]: https://www.onshape.com/en/resource-center/tech-tips/tech-tip-inserting-flat-patterns-into-drawings
