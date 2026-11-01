# Handoff: native SolidWorks files written by BauyrCAD, without SolidWorks

## Current publication validation (2026-11-01)

The user requested targeted REA research plus controlled references and SolidWorks rebuild
checks, then authorized publishing current experimental changes to the existing GitHub
repository after full validation. Four multi-thickness Shell references and nine offline
prototypes now pass analytic mass/centroid, per-face/default thickness and parent-width
edits plus save/reopen. See format section 28 and projects/sw_features/variable_shell/.
REA confirmed dimension numbering from D2 and post-load trimming of unmatched face lists.
No production variable-shell exporter has been added. Origin/clockwise/CLI source changes
passed 35 focused integration checks and 147 offline checks. Full regression completed
2026-11-02: **1286 passed, no skips, 2530.21 s (42:10)**.
Windows build/self-test passed; frozen origin/clockwise archives match source output,
reserved-name failure exits 2 and preserves output, ZIP CRC and executable identity pass.
The implementation is ready for a local commit and the requested private GitHub snapshot.
Public README/description prepared with explicit experimental coverage limits. Use the
existing private-information filtering rules; exclude personal projects and user-owned notes.

Public release is separately on hold for the user's requested legal review: applicable licence
and model-seed redistribution rights remain unverified. Do not treat a green test suite or
README disclaimer as resolving that review. Keep decompiler output and installed binaries
out of the shareable snapshot. The existing GitHub repository is private; a filtered, validated
snapshot may be uploaded there as requested, keeping visibility private. Private review notes:
projects/sw_features/PUBLICATION_REVIEW.md.

## REA native-code trial authorized (2026-11-01)

The user explicitly superseded the earlier no-decompilation constraint for a second REA
trial: permission includes inspecting/decompiling SolidWorks executables and DLLs to
investigate native feature serialization. Preserve installed files and the user's sessions.
The trial completed successfully with REA 6.8.0 + Ghidra 12.1.4: Shell serializer identified
per-face thickness lists, a nullable body reference previously treated as u16, and internal
version branches. Refined research schema round-trips 15 shell samples; populated variants
remain unvalidated. First native query 217.17 s; subsequent targeted queries were fast.
No production support claim, source edits, rebuild or commit. Existing WIP remains intact.
Evidence and next validation: `projects/sw_features/rea_trial/NATIVE_REPORT.md`.

## Paused at user request (2026-11-01)

Latest released commit is `d82c488`: 1237 tests passed, no skips; Windows app/ZIP rebuilt.
Uncommitted work is preserved: native origin anchors (`sworigin.py` plus seed), clockwise
arc orientation/matching, and clean CLI export errors. 145 focused offline tests passed;
two CLI error tests passed separately. The integrated SolidWorks origin/clockwise/shared
run was interrupted at the user's request after two passing tests. This work has NOT
passed the full suite, been committed, or entered the packaged app. Cleanup confirms no
SolidWorks running. Do not resume development until requested.
Research-only judges additionally passed clockwise arcs (3 cases), open/closed shells
(6 cases), and six additional dimension classes (18 cases); these are not universal or
released coverage. Independent dimension and mirror reference files were collected.
See bench session notes for restart details. Subsequently, the user authorized only a bounded
REA saved-file benchmark: official CLI 6.8.0 tested Shell references; no new decoding or
speedup demonstrated. No SolidWorks program code analysed, no development resumed.
Report: `projects/sw_features/rea_trial/REPORT.md`. CLI is in npm cache; no MCP setup.

## Shared profiles and more constraints (2026-11-01)

Implemented in `core/swsharedsketches.py` and `core/swsketch.py`:
shared extrude/revolve profiles, earlier-created consumed sketches, point-pair H/V,
point-on-line, equal circles, collinear lines, point symmetry and fixed geometry.
Fix lines/arcs include endpoint locks; point.fixed flags become native Fix relations.
Reserved default names and case-only duplicate feature names are rejected before writing.
See format section 26. All **87 focused SolidWorks sketch/shared checks passed**.
Windows app rebuilt, frozen self-test passed; ZIP CRC/executable identity and frozen/source
shared fixed-profile archive equality verified. **Full suite: 1237 passed, no skips,
2155.34 s (35:55).** Frozen reserved-name rejection preserves the destination; expected CLI
errors still reach PyInstaller's fatal handler, so handle these cleanly in the next milestone.
Session state is in `projects/sw_features/EXPANDED_COVERAGE_SESSION.md`.
Origin anchors, additional dimensions, clockwise arcs, axes, Shell/Draft and mirrors have
research scripts/reference files in that bench; they are not production capabilities yet.

## Reference-plane milestone (2026-11-01)

`core/swreferenceplanes.py` writes offset (+/-), chained offset, coincident and midpoint
planes with standard or earlier reference-plane parents. Extrusion/revolve histories,
later cuts and unused planes are supported. Parent edits and save/reopen pass on all
three standard orientations. Angle, three-point, normal-to-axis, flipped and face-parent
planes remain unsupported; so do mixed face/reference-plane and finishing histories.
See format section 25 and `tests/test_sw_nativeplanes.py` (33 SolidWorks gates).
Additional sketch builder/edit routes are now in the regular regression suite.
**Full suite: 1148 passed, no skips, 2023.29 s (33:43).** Windows app/ZIP rebuilt;
frozen self-test includes an offset/midpoint/coincident chain. ZIP CRC/executable identity
and frozen/source midpoint archive equality verified. Next research: shared sketches,
origin anchors/fixed geometry and remaining relations; bench notes below remain current.

## Current sketch-intent milestone (2026-11-01)

Working implementation in `core/swsketch.py`: construction lines/circles, standalone points,
shared point identity, parallel/perpendicular, arc-line tangent, line/arc equal, circle
concentric, midpoint and point-circle coincidence. Radius dimensions now support circles
and arcs. See format section 24, `tests/test_swsketch.py`, `tests/test_sw_sketch_intent.py`
and the tracked `SW_FEATURE_COVERAGE.md` backlog. Full suite: **1051 passed, no skips,
1777.83 s (29:37)**. Windows executable and ZIP rebuilt, frozen self-test passed;
ZIP CRC/executable identity and source/frozen center-slot archive equality verified.
Do not claim universal feature coverage.

**Face-mode coverage expansion (2026-11-01):** the second sketch can use sides or end caps
of a polygonal base on Front/Top/Right. Blind/reverse/mid-plane bases, forward blind and
mid-plane face bosses, blind/through-all/both-directions face cuts are gated. Through-all
cuts now accept the API default of no unused depth. All **995 tests passed, no skips,
1609.70 s (26:49)**, including 153 SolidWorks face cases with parent edits, geometry,
centers of mass and save/reopen. Windows executable/ZIP rebuilt; frozen self-test passes,
including a default-depth through cut. A frozen reversed-base cap-cut file matches the
source sections exactly; ZIP CRC and executable identity pass.

The comprehensive mandate is tracked in **docs/SW_FEATURE_COVERAGE.md**: 23 persisted
feature types, 20 sketch constraint types and 7 mate types, with registry completeness
checks. Many families remain pending; this is not a universal native-export claim.
Next: remaining sketch builder/edit routes, standalone/reusable sketches and reference geometry.
Bench state and scripts: projects/sw_features/EXPANDED_COVERAGE_SESSION.md.

**Face-attached sketches (2026-11-01):** `core/swfaceplanes.py` supports a second forward
blind boss on a planar side of a forward blind polygonal base extrusion on Front/Top/Right.
Preserves native face references; no donor solid/cache retained. Read format section 23.
Six SolidWorks gates cover slanted/rectangular faces, parent edits and save/reopen.
End-cap faces, longer histories and other face-based modes remain explicit unsupported
cases. Actual Downloads `asdfasdf.bcad` is the motivating regression. Full regression:
**833 passed, no skips, 1078.41 s (17:58)**. Windows app and ZIP rebuilt, frozen self-test
passes including a face sketch. Frozen CLI exported Downloads/asdfasdf_offline.SLDPRT;
its sections exactly match the source output validated in SolidWorks. ZIP CRC and
executable identity verified. The user then authorized a comprehensive autonomous audit
and expansion of every BauyrCAD modelling/assembly method; start with existing feature
coverage, then incremental SolidWorks parity. Work plan: projects/sw_features/EXPANDED_COVERAGE_SESSION.md.

**Desktop delivery fix (2026-11-01):** the distributed executable was still the October23
build, and the PyInstaller spec included only Imported/assembly seeds. The Windows build
now bundles the entire seed tree; its self-test strictly exports dimensioned fillet and
revolve trees offline. Rebuilt executable and ZIP both pass the frozen self-test.
File now has **Export editable SolidWorks part (offline)** separately from **Export using
installed SolidWorks**. GUI feature-tree exports require native output and preserve the
destination on unsupported intent; API fallback remains opt-in through existing defaults.
Do not claim desktop delivery based only on source tests: rebuild and verify the actual
`dist/BauyrCAD/BauyrCAD.exe`. Full regression: **820 passed, no skips, 1074.78 s (17:54)**.

**M5 second family (2026-11-01, after `c5c313b`):** offline constant-radius fillets and
true equal-distance chamfers now support one forward blind polygonal extrusion on
Front/Top/Right, multiple edges and face selections. Sizes and extrusion depth remain
editable; dimensioned/renamed, triangular, oblique and concave profiles are gated.
Sixteen saved reference files round-trip exactly. Read format section 22 and
`core/swedges.py`: surface-owner words are ref16 object references, and true equal chamfers
use type `2 | 16` rather than an angle of 45 degrees (also fixed in COM export).
The intersecting four-edge fillet gate integrates regenerated STEP geometry: independently
created COM-native and offline STEP results agree; SW's mass integration differs.
**Full suite: 818 passed, no skips, in 1102.94 s (18:22).** The focused checks also pass.
Longer histories, holes/curved profiles, reverse/mid-plane bases, variable radii and
asymmetric chamfers remain explicit fallback. Next: oriented asymmetric chamfers and
finishing features after more general histories, with separate donors and gates.
Session notes: `projects/sw_features/M5_EDGES_SESSION.md`.

**M5 first family + automatic annotation placement (2026-11-01, after M4 `7bf0e5c`):**
implemented and gated in SolidWorks: full/partial/reverse/mid-plane revolved bosses/cuts,
standard planes, translated/diagonal/reversed construction axes, circular profiles,
renamed sketches, mixed stacks, editable angles and save/reopen. Annotation placement
and saved positions pass on Front/Top/Right. The user closed SolidWorks and authorized
validation to resume. **56 targeted SolidWorks checks and 146 focused offline tests pass.**
Fifteen new mode donors round-trip exactly. Read format section 21 for two critical fixes:
construction markers and the model-header creation-stamp registry required by axes.
The full suite passed: **739 passed, no skips**, in 1013.22 s (16:53). Session notes are in
`projects/sw_features/M5_SESSION.md`. Next M5 family: fillet/chamfer, separately gated.

State on 2026-11-01, branch `bauyrcad-0.3`; M3 part 2 was committed as `5a71812`, and M4
is described below. Read this, then `AGENTS.md`, then the named sections of
`docs/SLDPRT_FORMAT.md`.

**M3 part 2 update:** implemented `swtree.add_dimension` and `set_profile(..., dims=...)`.
R1/R2/C0/C1 now round-trip, with bundled R2/C1 annotation templates. Read section 19 of
`docs/SLDPRT_FORMAT.md` for the corrected layouts, API and validation. SolidWorks opened
both generated parts with clean rebuilds and editing D1 changed the volumes as expected.
The initially pending follow-up tests now pass, including second-length edits, mixed
dimensions and save/reopen. Never force the running-session guard.

**M4 implemented:** `core/swfeatures.py` builds independent
sketch/extrude pairs from bundled mode donors; allocates feature/parameter ids,
rewrites shared references, geometry, names and depth, and transfers M3 dimensions.
`swfile.write_part(..., features=True, document=...)`,
`Part.export_solidworks_offline`, CLI `export --features --require-native`, and the GUI's
feature-tree export format are connected. Unsupported input produces a reported whole-part
Imported fallback (strict mode leaves the destination untouched). The initial Front/blind
tree and custom four-boss tree pass offline `tree_mismatches(..., exact=True)`.
`sldprt.feature_tree` now uses exact MFC class identities when the archive is decodable,
retaining its heuristic path for other files. A GUI export was exercised and screenshot
inspected (`projects/sw_features/out/M4_gui.png`).
Validation: 106 focused offline tests and all 27 M3/M4 private SolidWorks integration tests
passed. The final full suite passed: **647 passed, no skips**, in 1095.43 s. The 19 bundled
mode donors all round-trip exactly. Read format section 20 for the
empty-container/first-body initialization and the important context-specific unit-component
fix. `projects/sw_features/make_m4.py` regenerates the donors only with SolidWorks closed.
Supported modes: Front/Top/Right, blind/reverse/mid-plane bosses, blind/reverse/mid-plane/
through-all/both-direction cuts. Unsupported intent falls back explicitly. M5 revolve and
automatic annotation placement are described in section 21. Do not silently broaden
the supported scope without donors and SolidWorks gates.

## 1. Goal

Bauyrzhan (mechanical engineer, SolidWorks + Nastran user) wants BauyrCAD to export to
**SolidWorks 2024 files without SolidWorks installed**. Today's state:

| Output | Status |
|---|---|
| Parasolid `.x_t` / `.x_b` (exact solids, assemblies, names, colours) | done (`core/parasolid.py`, `core/xt_brep.py`) |
| `.SLDPRT` with one **Imported** solid (no feature tree) | done (`core/swfile.py`, `write_part`) |
| `.SLDASM` (up to 46 components per level; trees of sub-assemblies) | done (`swfile.write_assembly`, `api.export_solidworks_tree`) |
| `.SLDPRT` with a **real FeatureManager tree** (sketches + features, editable dimensions) | M4 sketch/extrude + M5 revolve; later feature families pending |

The feature-tree work is a long project in milestones M1-M4 (+M5 families). The approved
plan is `projects/sw_features/PLAN.md` (gitignored, on this PC).

## 2. Hard rules for this work

* **SolidWorks is only the judge**, never the writer. Donor files are made by SolidWorks
  through BauyrCAD's COM export (`part.export_solidworks(path, require_native=True)`), and
  written files are opened in a **private hidden** SolidWorks session to check them.
* Analyse only files SolidWorks saved (differential analysis). **Never decompile SolidWorks.**
* **Never touch a SolidWorks window of the user's.** Before running SolidWorks, check that no
  `SLDWORKS.exe` with a window runs. The SolidWorks tests skip while SolidWorks runs
  (`BAUYRCAD_SW_TESTS=1` forces them).
* A crash leaves a hung hidden SolidWorks behind. End it **only** with
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/end_hidden_solidworks.ps1`
  (ends windowless, COM-started instances only). End processes no other way.
* Commit on your own when a milestone is finished and the **full** suite is green
  (`.\.venv\Scripts\python.exe -m pytest -q`; about 15 min with the SolidWorks tests),
  author the repository owner's name and e-mail. Never push. Leave `docs/IMPROVEMENTS.md` and
  `docs/RESEARCH_ROADMAP_2026-10-06.md` uncommitted: they hold Bauyrzhan's own edits.
* Never `pip install parasolid-kit[occt]` (it replaces the pinned OCP with a build
  without VTK).

## 3. How a .SLDPRT is built (what is known)

* Container: a ZIP behind an 8-byte prefix, disguised names; CRCs are not checked
  (`swfile.Container`: read, replace, drop sections, rebuild byte-identical).
  `_MO_VERSION_17000/Biography` and `/History` are mandatory, or SolidWorks crashes.
* Without the Parasolid partition (`Contents/Config-0-Partition`) and the display caches,
  **SolidWorks regenerates the whole solid from the feature records on opening**. So a
  native writer writes features only, no geometry. `swtree.REGENERATED` lists the sections
  to drop.
* The feature tree lives in two MFC `CArchive` streams forming one archive:
  `Contents/Config-0` (part settings, units, views, model data with one atom per body feature)
  and `Contents/Config-0-ResolvedFeatures` (the feature objects).
* For extrusions, the base's `Config-0-ModelHeader`, `Header2`, `CMgr` and `CMgrHdr2` may
  stay stale (tested). **Revolve axes also require current model-header ID/name and
  creation-stamp records** (section 21). Without `CMgr` at all SolidWorks crashes.
* Wrong integers (references, ids, counters) crash SolidWorks silently. Timestamps,
  persistent ids (`.. e5 db 6a`), plane display sizes and an extrude's face-naming records
  may be stale: SolidWorks recomputes them. Axis component creation stamps must match the
  referenced sketch's model-header record.

## 4. The code

* `bauyrcad/core/mfc.py`: exact CArchive model.
  * `load_archive(sections)` returns an `Archive` (`.config`, `.resolved`, `.features`), and
    `Archive.dumps()` writes it back **byte for byte**, numbering objects afresh from object
    identity. Inserted objects therefore renumber every tag and every untagged u16/u32
    reference automatically.
  * Per-class field layouts are in `LAYOUTS`, using a small layout language (see the module
    header): `arr`, `if`/`ifnot`, `rep`, `objs0`, `ifback`, `ifobj`, `opaque`.
  * The map of `Config-0` starts at 2. `moRelMgr_c` and `moCThreadRefMgr_c` stay opaque,
    bounded by the next class header.
* `bauyrcad/core/swtree.py`: editing trees.
  * `load(path) -> (container, archive)` and `save(container, archive, path)`.
  * `transplant(base, donor, names)` copies the features `names` from a SolidWorks-made donor
    on top of a base. Shared objects are paired, the rest is copied, and `renumber()` then sets
    every counter.
  * `set_profile(arc, sketch_feature, loops, hv=True)` writes a sketch from geometry: loops of
    `("line", p, q)`, `("arc", centre, p, q)` (counter-clockwise) and `("circle", centre, r)`,
    in mm. It writes points, segments, chains and horizontal/vertical relations.
* `bauyrcad/core/sldprt.py` is the reader (sections, feature tree, KeyWords, mates). It uses
  exact `mfc` class identities for supported archives and `_mfc_indices` as a fallback.
* Tests:
  * `tests/test_mfc.py`: round trips, variants, the `ifobj` branch.
  * `tests/test_swtree.py`: counters equal SolidWorks' own files, profiles equal SolidWorks'
    sketches.
  * `tests/test_sw_swtree.py`: SolidWorks as the judge.
  * Fixtures: `tests/data/sw/mfc/*.SLDPRT`. These are SolidWorks-made donors: P1 rectangle,
    P1s, P2, P2t, P3, T3 triangle, T3B, Q4, H1 holed rectangle, S0 sketch only, I1 Imported
    body, N6 hexagon, DS D-shape.
* Docs:
  * `docs/SLDPRT_FORMAT.md` sections 12-18, which cover the experiments, writing parts and
    assemblies, the archive object by object (16), adding features (17) and sketch profiles
    (18).
  * `docs/API.md` and `CHANGELOG.md`.

## 5. Milestones

| # | What | State |
|---|---|---|
| Phase 0 | Feasibility: patching works, grafting needs every untagged reference | done, `c1ea281` |
| M1 | Exact archive model (`mfc.py`): 13 donors + seed byte-identical, 80/80 decodable corpus parts | done, `6f72574`, `9748120` |
| M2 | `swtree.transplant` + counters; gate: boss grafted onto the triangle opens with T3B's exact volume 5,120.575 mm³ | done, `c1b8722` |
| M3 part 1 | `set_profile`: lines, arcs, circles, holes, H/V relations; SolidWorks rebuilds to BauyrCAD's volumes | done, `54ca53c` |
| **M3 part 2** | **Sketch dimensions** (line length, circle diameter) | done, `5a71812`; follow-up SolidWorks tests passed during M4 |
| M4 | Writer v1: `swfile.write_part(..., features=True)` from a BauyrCAD part (sketches on the standard planes + boss/cut extrudes), falling back to the Imported body | implemented and SolidWorks-gated; see section 20 of format notes |
| M5 revolve | Full/partial/reverse/mid-plane bosses/cuts about own-sketch construction axes; mixed stacks and automatic dimension placement | implemented and SolidWorks-gated; format section 21 |
| M5+ remaining | Fillet/chamfer, Hole Wizard, patterns, shell, sweep/loft, reference geometry; then features inside .SLDASM | later |

## 6. Original M3 part 2 plan (retained as research context; see update above)

Donors already made by SolidWorks are in `projects/sw_features/family/`:
* `R1` is P1's rectangle with the bottom line dimensioned (40), and `R2` adds the height (20).
* `C0` and `C1` are a d12 circle extruded 10 mm, without and with a diameter dimension.
* `make_m3d.py` made them.

None of them walks yet. What is known:
* In `sgSketch`, the field `_3` (`"raw:38"` in `SG_SKETCH`) is really `raw:32`, a u16 count of
  sketch dimensions, the dimension objects, and then the rest (4 bytes in P1).
* R1's dimension is an `sgPntPntDist` object. It holds a u32, a `moLengthParameter_c` "D1" in
  a **shorter sketch form** than the extrude's D1 layout (the `NodeName` "D1", then the value
  0.04 as f64, ...), and two `sgPointHandle`s (the dimensioned points).
* The display dimension `moDisplayDistanceDim_c` sits in the sketch's first `suObList`
  (`list1`) and contains a `moSkDimHandleValG2_c`.
* A circle's diameter brings `sgCircleDim`, `sgEntHandle`, `moDisplayRadialDim_c` and
  `moSkDimHandleRadial_c`.
* The same `moLengthParameter_c` class has two forms (feature dimension vs sketch dimension).
  Branch on something in the stream (`if`/`ifobj`/`ifback`), never on the file.

Method (as for every milestone so far):
1. Decode the layouts with `projects/sw_features/pf.py PATH 30 "{'cls': [...]}"` (walk any
   file with trial layouts and show where it stops) until `rt.py R1 R2 C1` round-trips byte
   for byte. Keep all old donors byte-identical (`rt.py P1 P1s P2 P2b P2t P3 Q4 T3 T3B H1 S0
   I1 N5 N6 DS`) and `tests/test_mfc.py` green.
2. Diff R1 vs P1 and R2 vs R1 object by object (`m2diff.py`) to find the counters a dimension
   moves (the sketch's `_29` = segments + relations + 1 probably counts dimensions too; the
   18-word table; feature ids; the dimension's name D1, D2...).
3. Add `set_profile(..., dims=[...])` (or `add_dimension`): a length on a line, a diameter on
   a circle, with a template copied from the donors.
4. Gate in SolidWorks (`judge.py FILE --edit D1@Sketch1=50`): the dimension is listed, and
   editing it rebuilds to the new volume.
5. Tests (offline equality with R1/R2/C1, plus a SolidWorks test), docs section 19, CHANGELOG,
   then the full suite and a commit.

Also open:
* SolidWorks' slot donor `SL` does not walk (tangent relations).
* Config-0 variants in the corpus: materials and appearances in the model data, one vendor
  folder without `uoModelData_c`, and PMark data that holds objects. They don't block M4,
  which writes Config-0 from its own template.

## 7. Bench (`projects/sw_features/`, gitignored, on this PC)

* `judge.py FILES [--save] [--edit D1@X=30]`: opens each file in a private hidden SolidWorks,
  prints the tree, dimensions, volume, rebuild/What's Wrong, the edit and SaveAs (JSON lines).
* `make_family.py`, `make_m1b.py`, `make_m3.py`, `make_m3d.py`: make donors in SolidWorks.
* `rt.py NAMES`: round trip of donors. `pf.py PATH`: walk any part with trial layouts.
  `sec.py`: walk the CMgr/ModelHeader archives.
* `corpus_walk.py`: gate over the SolidWorks 2024 parts on this PC (`corpus2024.txt`).
  `missing.py` ranks the undecoded classes.
* `m2diff.py A B` (object-level diff), `tokdiff.py`, `bdiff2.py`, `skdump.py NAME`
  (sketch dump).
* `m2graft.py` (M2 grafts), `m3sk.py` (M3 profiles), `lib.py` (shared helpers, `OPTIONAL`
  sections).

## 8. Usage etiquette

Bauyrzhan keeps the last 10% of his Claude/Codex plan limits for small tasks. Pause long work
at 90% of any limit, at a clean point (commit or saved notes). Before demanding steps, check
that usage is below 85%.
