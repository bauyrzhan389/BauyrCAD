# Changelog

## 0.4.0 (unreleased)
- **Reliable line input**: a continuing line/centerline adds one endpoint per press;
  mouse movement before release no longer adds an extra endpoint. First-point drag drawing remains available.
- **Interactive Section View**: docked PropertyManager, plane shortcuts, numeric offset/X/Y
  rotations, draggable move arrow and rotation rings, and Keep Section. Model geometry stays
  unchanged; exported viewport images omit the editing handles.
- **CAD workspace layout**: FeatureManager/PropertyManager tabs, CommandManager tabs below
  the tools, tabbed assembly commands, and a compact viewport view toolbar.
- **Display Style**: switch between Shaded and Shaded with Edges for part/assembly views.
  The shared preference survives rebuilds and respects hidden components; analysis-result
  mesh/undeformed outlines keep their own controls.
- **Check Geometry in Evaluate**: inspect kernel validity, closed solid bodies, loose
  geometry and topology counts. Missing components, failed features and kernel errors
  produce incomplete reports. The API exposes the same read-only checks. Inspection
  cancellation is retained when a progress bar reaches its final step.
- **Angled Section View**: select built reference planes (including transformed assembly
  component planes) or pick a planar face, then offset/flip the display cut. Plane choices
  are world-space snapshots and do not add features or change model geometry.
- **Assembly inspection visibility**: Hide/Show, Isolate, Show All and Zoom to Selected
  work from component context menus, View and commands. Hidden components cannot be
  measured or picked; visible-component bounding boxes reject missing geometry. Visibility
  is temporary and does not change saved models, exports or mass properties.
- **Inspection UI**: persistent white/neutral/classic viewport backgrounds, distinct
  Evaluate icons and a separate assembly View/Evaluate toolbar row. Section View toggles
  off from its action or Escape, and cursor orbit respects the retained section.
- **Mass Properties display**: choose length/mass units and report precision without
  recalculating geometry, and show the three principal inertia axes through the centre
  of gravity. Display choices leave saved materials and numerical API units unchanged.
- **Save / Copy View Image**: export the viewport, including sections and inspection
  labels, at 1×/2×/3× resolution with white, current or transparent backgrounds.
  PNG saving is atomic; capture restores the current display settings on failure.
- **Minimum Clearance in Evaluate**: measure a chosen component pair or the nearest
  neighbour, highlight the closest world points, compare against a required gap, and
  distinguish contact from interference. Missing or invalid solids produce incomplete
  checks; moving/rebuilding the assembly clears stale results.
- **Section View**: inspect part and assembly interiors with Front/Top/Right cut planes,
  a position slider, numeric offset, direction flip and optional caps that preserve holes.
  Original visible faces/edges remain selectable; closing restores the full model.
  Sectioning is display-only and pauses during sketch editing and analysis results.
- **Interference Detection in Evaluate**: assembly overlap volumes and red intersection
  solids through transparent components, adjustable volume threshold, temporary pair
  exclusions, copyable reports and cancellation. Results clear after model/pose changes;
  missing geometry and failed intersections produce an incomplete check, never a clean result.
- **Mass Properties in Evaluate**: volume, surface area, mass, centre of gravity, inertia
  tensors about the CG/world origin, and principal moments/axes for parts and assemblies.
  Saved physical materials and per-instance overrides are independent of FEA studies.
  Missing densities remain explicit; rotated components use the parallel-axis theorem.
- **Evaluate measurements**: visible Evaluate tools for parts and assemblies, with face,
  edge, vertex and surface-point selection; minimum/centre/axis distances, circular maximum
  distances, angles, XYZ differences, units and precision, copyable reports and session
  history. Bounding Box shows overall world-axis dimensions. Assembly measurements use
  transformed component geometry and clear when poses or geometry change.
- **Offline export compatibility check**: inspect the complete history before choosing a
  destination, see blocking features and suggestions, and select them in the model tree.
  The scripting API exposes the same check through `part.solidworks_compatibility()`.
- **Native Shell export**: constant-thickness inward/outward shells on one forward blind
  polygonal extrusion, closed or with one end-cap opening. Thickness and parent depth
  remain editable. Planar shells now keep sharp offset corners to match SolidWorks.
- **Real-window export demo**: a 37-second recording of BauyrCAD's offline export and
  SolidWorks' native feature editor, with a verified 20-to-35 mm dimension edit.
- **Project introduction**: explain the personal engineering workflow behind BauyrCAD and
  highlight experimental editable SolidWorks-format export as the main focus.
- **Origin-anchored sketches offline**: explicit sketch-origin references remain constrained
  on standard and supported reference planes, including shared profiles and several sketches.
  Origin projection on model-face sketches remains unsupported and preserves the destination.
- **Clockwise arcs offline**: concave profiles preserve arc orientation and editable radius
  dimensions, including profiles containing complementary arcs.
- **Clear command-line export failures**: unsupported offline exports return an explanatory
  error and exit status 2, preserving existing destination files.
- **Safe native feature names**: offline export rejects names that collide with built-in
  SolidWorks features, including case-only duplicates, before changing the destination.
- **Shared sketches offline**: one native sketch can drive multiple extrusions or revolves;
  earlier sketches retain their original tree order. Supported reference planes work too.
- **More native sketch relations offline**: horizontal/vertical point pairs, point-on-line,
  equal circles, collinear lines, point symmetry and fixed geometry. Fixed lines/arcs also
  fix their endpoints to preserve BauyrCAD's intent; stored fixed-point flags are retained.
- **Offline reference planes**: offset, zero-offset/coincident and midplanes retain native
  parent references and move their sketches/bodies when edited in SolidWorks. Supports
  Front/Top/Right, chained planes, extrusion/revolve histories and unused planes. Angled,
  three-point, face-based and flipped reference planes remain explicit unsupported cases.
- **More sketch routes validated offline**: center slots, three-point centered rectangles,
  circumscribed polygons, perimeter circles, center/three-point arcs, sketch fillets and
  sketch chamfers retain native entities and supported design intent.
- **Offline sketch design intent**: slots, centered/three-point rectangles, parallelograms
  and regular polygons retain construction geometry, points and their supported relations.
  Driving arc/circle radii join lengths and diameters; editing a slot radius or polygon
  construction-circle diameter changes the native body. Equation bindings remain unsupported; origin anchoring is covered by the newer entry above.
- **Broader offline face support**: end-cap sketches, reversed and mid-plane base
  extrusions, face cuts (blind/through-all/both directions), and mid-plane face bosses.
  Through-all cuts now accept the API default with no unused depth supplied. Added a
  tracked inventory of all modelling, constraint and mate families for native export.
- **Offline face-attached sketches**: a second blind boss on a planar side face of a
  polygonal base extrusion exports with its editable sketch and native face attachment.
  Covers slanted faces and Front/Top/Right bases; unsupported face histories still report
  a reason rather than silently flattening the part.
- **Offline SolidWorks export in the Windows app**: a dedicated editable-part menu command,
  clearly separate from the installed-SolidWorks exporter. GUI feature-tree exports reject
  unsupported models instead of silently producing an Imported body. Windows builds now
  include all native/dimension templates and verify offline feature export in their self-test.
- **Offline fillets and chamfers (M5 second family)**: a constant-radius fillet or
  equal-distance chamfer after a forward blind polygonal extrusion, including multiple
  edges and face selections on Front/Top/Right. Sizes and extrusion depth remain editable.
  Longer histories, variable radii and asymmetric chamfers retain explicit fallback.
  Also fixed COM equal-distance chamfers on nonperpendicular faces.
- **Automatic offline sketch-dimension placement**: length and diameter labels now use
  the profile bounds, outward offsets and spacing between labels instead of donor positions.
- **Offline revolved bosses and cuts (M5 first family)**: standard planes, full/partial
  angles, reverse and mid-plane, about a construction centerline in the profile sketch.
  Supports mixed extrusion/revolve stacks and editable angles. SolidWorks gates cover
  rebuilds, geometry, renamed features, diagonal axes, dimension edits and save/reopen.
- **SolidWorks feature trees without SolidWorks (M4)**: `Part.export_solidworks_offline`,
  `swfile.write_part(..., features=True)`, CLI `export --features`, and a GUI export format.
  Supports independent sketch/extrude pairs on Front/Top/Right, blind/reverse/mid-plane
  bosses, blind/reverse/mid-plane/through-all/both-directions cuts, and M3 sketch dimensions.
  Unsupported parts fall back as a whole to Imported solids with a reason; strict mode
  refuses the export. SolidWorks gates check volumes, bounds, edits and save/reopen.
- **Driving sketch dimensions written without SolidWorks (M3 part 2)**:
  `swtree.add_dimension` and `set_profile(..., dims=...)` write line lengths and circle
  diameters with editable D1/D2 names. The archive reader now decodes the dimension donors
  byte for byte and keeps the sketch reference separate from the dimension index.
- `swfile.write_assembly` references an existing `.SLDPRT` next to the assembly (e.g. one saved by
  SolidWorks, feature tree kept) instead of rewriting it, like a `.SLDASM` sub-assembly. Used to bring a
  SolidWorks 2025 assembly back to SolidWorks 2024 (client V5).
- `export_solidworks_tree` also takes an existing `.SLDASM` in the same folder (for example one saved
  by SolidWorks) as a child and references it as it is, so cables or fixtures can be added around a
  user's own assembly without rewriting it (client rev. D6).
- **SolidWorks assemblies of sub-assemblies, written without SolidWorks**:
  `bauyrcad.api.export_solidworks_tree("top.SLDASM", [(sub_asm, pose, fixed), ...])` writes each
  sub-assembly as its own `.SLDASM` (with its parts) next to the top and the top as an assembly
  whose components are those sub-assemblies (and any parts), marked as assemblies the way
  SolidWorks marks them (document type 3 after the referenced file's stem in the model header).
  Every level may hold up to 46 components, so a machine of any size goes in as a tree (the
  gripper project rev. D5: 198 parts in 13 sub-assemblies). Not yet opened in SolidWorks by the
  test suite (SolidWorks was not available): `tests/test_swfile_tree.py` checks the written
  references, document types and placements. `asm.add(..., pose=4x4, color=(r, g, b))` places a
  component by a full transform (e.g. one read from another CAD assembly) and colours it.
- **Exact model of SolidWorks' feature archive (groundwork for native feature trees without
  SolidWorks)**: `bauyrcad.core.mfc` reads `Contents/Config-0` and `Config-0-ResolvedFeatures`
  object by object (MFC load map, per-class field layouts) and writes them back byte for byte,
  renumbering every object reference from object identity. Covers the default folders and
  planes, sketches of lines and arcs with their relations, and blind boss extrudes (rectangle,
  triangle, square and circle sketches of SolidWorks 2024 parts), and also Imported bodies
  (STEP opened in SolidWorks, BauyrCAD's own seed), sketch-only parts and the extrude form
  other tools save; 80 parts on this PC read and write back byte for byte. Not user-visible yet.
- `bauyrcad.core.swtree.transplant` adds features to a SolidWorks part's tree without
  SolidWorks (a sketch and an Instant3D boss copied from a SolidWorks-made donor, every
  reference and counter renumbered): SolidWorks 2024 opens the result with the new features,
  the exact volume, a clean rebuild and editable dimensions. Groundwork for native trees.
  `swtree.set_profile` writes a sketch's lines, arcs and circles (several loops, holes,
  horizontal / vertical relations) from plain geometry; SolidWorks rebuilds the extrude from
  them to BauyrCAD's volume.
- **SolidWorks 2024 assemblies (`.SLDASM`) written by BauyrCAD itself, no SolidWorks
  needed**: `asm.export("a.SLDASM")` (and File > Export in the assembly window) writes the
  assembly with each part once as a `.SLDPRT` next to it (exact solid as an Imported body),
  components placed exactly and fixed / floating as in BauyrCAD, a part used twice shared.
  File > Export to SolidWorks in the assembly window offers it first (then `.x_t`) when
  SolidWorks is not installed. Up to 46 components; no mates or colours yet (those need
  SolidWorks: Export to SolidWorks). SolidWorks 2024 opens the test assembly with every
  component where it puts the same assembly's STEP, a clean rebuild, and saves it again.
- **SolidWorks 2024 parts (`.SLDPRT`) written by BauyrCAD itself, no SolidWorks needed**:
  `part.export("p.SLDPRT")`, the CLI and File > Export write a SolidWorks part whose tree is
  one Imported feature holding the exact solid (as SolidWorks itself stores an imported body).
  File > Export to SolidWorks offers it when SolidWorks is not installed. SolidWorks 2024 opens
  15 test parts (analytic, B-spline, tolerant edges, a cavity, two solids) with the exact body,
  a clean rebuild and What's Wrong, and saves them again. The editable feature tree still
  needs SolidWorks (Export to SolidWorks).
- **Parasolid export (`.x_t`) of assemblies, with names and colours**: `asm.export("a.x_t")`
  (and File > Export to SolidWorks in the assembly window when SolidWorks is not installed)
  writes each part once and places it by its components (a part used twice is shared, as in
  SolidWorks), with the assembly, part and component names and each part's colour on its
  faces. SolidWorks 2024 opens it as an assembly with the components where the STEP puts
  them and the colours exact (through STEP it shows them lightened). Part exports carry the
  part name.
- **Parasolid export (`.x_t`): tolerant edges, inner voids and several bodies.** An edge
  OpenCASCADE leaves further from its faces than Parasolid's 1e-5 mm (a shell of a spline
  part) is written as a Parasolid tolerant edge, with its curve on each face (an SP-curve)
  and tolerant vertices, so such parts export instead of being refused. A closed void inside
  a solid is its own void region. Several solids are written as SolidWorks writes a
  multi-body part (an assembly of the bodies, which SolidWorks opens as an assembly). Offset
  surfaces are approximated to 1e-7 mm and every converted surface is checked by projection.
  28 parts and a two-body part open in SolidWorks 2024 with no faults.
- **Parasolid export (`.x_t`) now writes B-spline curves and surfaces**: splines, extruded
  and revolved splines, lofts, sweeps (open, closed, helical springs) and sketch text export
  too. Closed B-surfaces are written periodic (no seam edges, as Parasolid has them: a sweep
  along a closed spline is one face with no edges), and knots where a sweep's parameter lines
  turn are split as SolidWorks does on import, so Parasolid sees a smooth surface. Seven such
  parts open in SolidWorks 2024 with no faults, SolidWorks' own face and edge counts, and the
  volume it measures for them through STEP. A part whose edges lie further than 1e-5 mm from
  their faces (a shell of a spline part) reports it (tolerant edges come next; use STEP).
- **Parasolid export (`.x_t`), written by BauyrCAD itself**: File > Export (and
  `part.export("part.x_t")`, the CLI `export`) writes a Parasolid transmit file that
  SolidWorks, NX, Solid Edge and Onshape open as an exact solid, with no SolidWorks needed.
  File > Export to SolidWorks offers it when SolidWorks is not installed. This first step
  covers one solid with plane, cylinder, cone, sphere and torus faces and line, circle and
  ellipse edges (extrudes, revolves, cuts, fillets, chamfers, shells, Hole Wizard holes,
  patterns); 19 such parts open in SolidWorks 2024 with no faults, Parasolid's own face and
  edge counts and the volume SolidWorks measures for them through STEP. Splines, lofts,
  sweeps, text, several bodies and assemblies come next (they report it; use STEP).
- **Scripting API: `part.sketch("front")` references the Front Plane** (stored as the
  standard plane, as the GUI does). The SolidWorks export puts such a sketch on the Front
  Plane even when an earlier reference plane (e.g. a mid plane between two faces) lies there;
  before, it was parented to that plane and moved with it when the model was edited in
  SolidWorks. Explicit `SketchPlane`s also prefer the standard plane, and never reuse a
  transferred reference plane.
- **SolidWorks export: narrow spline-bounded features export strictly** (D8: the spline's
  shape difference is a note). BauyrCAD's volume is now measured exactly on spline faces
  (split at the knots: the whole-face integration was ~0.01 mm³ off per spline, the
  adaptive one several %), and a spline step has an allowance along the spline (1 µm times
  its free-form face area). A 0.3 mm spline strip was refused; it now exports with a note.
- **SolidWorks export: a Point placed on a line end or circle centre** (the GUI's Point
  tool snapped to it) is exported as a SolidWorks point tied with a checked Coincident.
- **SolidWorks export never closes the user's SolidWorks**: when the running SolidWorks
  processes cannot be listed, the export no longer assumes the instance is its own, so it
  never hides it, closes its documents unsaved or exits it.
- **SolidWorks export: tighter, position-independent step check**: each step's change of
  volume and of first moment is now judged against the step itself, not the whole body.
  SolidWorks measures at its Higher mass-property accuracy (it agrees with OpenCASCADE to
  ~1e-9), the measurement floor dropped from 2e-6 to 1e-8 of the body (plus any difference
  an earlier loft / spline already left, and OpenCASCADE's own integration uncertainty on
  B-spline faces, measured per step: 0 on analytic faces), and the first moment is taken about the step's
  neighbourhood instead of the world origin. A small boss patterned the wrong way or a
  chamfer with its distances swapped on a 200 x 200 mm plate is now rejected (it was
  accepted), and a loft or sweep modelled 1 m from the origin exports as it does at the
  origin (it was refused).
- **SolidWorks export: helices run where BauyrCAD has them**: a helix has no volume, so its
  curve is now compared with BauyrCAD's (every point on it, the same end points). The
  direction about a negative axis (`axis="-y"`), the hand, which SolidWorks reads about its
  sketch normal, and the start angle, which SolidWorks counts from its own zero (it was
  90 deg off about Y and X), are derived and checked. Before, a helix about `-y` was saved
  growing +Y, and any helix about Y or X started a quarter turn early.
- **SolidWorks export pre-check: revolves about a line in another sketch or an ordinary
  line outside the profile** are refused before SolidWorks starts, with the fix (draw the
  axis as a centerline in the profile sketch). SolidWorks made no feature for them.
- **Offline SolidWorks file check (`sldprt.feature_tree`) is more reliable**: a class
  reference now names the class whose header comes in the right place in the file, not the
  one most often seen with that KeyWords type, so later boss extrudes read as `Boss`,
  suppressed Instant3D bosses and cuts as `ICE`, and no two class indices share a class.
  Absorbed sketches are found for every loft profile, a sweep's path and features
  suppressed in the active configuration; `SwFeature.unlinked` counts any it still can't
  identify. The documented check builds the expected steps from the part itself
  (`[f.name for f in part.doc.features]`), not from the export report.
- **SolidWorks export: reference geometry**: every reference plane, axis, point and curve
  becomes the native SolidWorks feature of the same kind and name, with BauyrCAD's
  references, and later features select it by its BauyrCAD id. Planes: offset (either
  side, 0 = coincident) from a plane or face, mid plane, at angle about an axis, edge,
  reference axis or sketch line, through three points, normal to an axis / edge through a
  point. Axes: cylinder / cone face, a planar face's normal, a straight or circular edge,
  two planes, two points, point + direction. Points: coordinates, arc centre, face
  centre. Curves: Curve Through XYZ Points (open and closed) and polylines (3D sketch).
  Each one is read back in SolidWorks (plane place and normal, axis line, point, curve
  points). Patterns can use a reference axis or a circular edge as their axis. These
  types no longer block the GUI export; what SolidWorks can't define (an 'At angle'
  plane whose axis is not parallel to its base, a planar face's normal as its rotation
  axis) is refused before SolidWorks starts.
- **Curve Through XYZ Points**: the curve is now the cubic spline SolidWorks makes from the
  same points (chord-length parameters, natural ends; periodic when closed), so the two
  programs give the same curve and sweep. Curves in existing parts change shape slightly
  between their points.
- **API**: `part.plane(sketch, ...)` (and mirror / draft / hole planes given as a sketch)
  now references the sketch's standard or reference plane instead of a copy of its plane.
- **SolidWorks export: sketch relations and dimensions**: every relation arrives as the
  SolidWorks relation of the same kind and each one is checked in SolidWorks (the sketch
  must gain that relation type): coincident (point-point, and a point on a line, arc,
  circle or spline), midpoint, Horizontal / Vertical on lines and between points (also 3+
  points and the origin), parallel / perpendicular / collinear / equal on 2 or more lines,
  equal arcs and circles, concentric, tangent (line side, a line end on a circle,
  external and internal arcs), Fix on points, curves and Point entities, and symmetric
  about a centerline or an ordinary line. A relation SolidWorks refuses (redundant or
  conflicting there) is listed by name and blocks the GUI export instead of disappearing.
  Every dimension is read back after it is made (kind, value, driving / driven):
  aligned point-to-point, centre-to-centre and point-to-centre distances (measured from
  the centres), perpendicular point-line, centre-line and line-line distances, radius
  and diameter of arcs and circles, and angles in the quadrant BauyrCAD measures (a
  supplementary or explementary angle SolidWorks picks is made again elsewhere).
  **Driven (reference) dimensions** are now exported as driven SolidWorks dimensions
  (they used to be dropped silently). Dimension texts sit where BauyrCAD shows them. The
  SolidWorks sketch must be exactly as defined as BauyrCAD's (fully defined stays fully
  defined, never over defined). Before SolidWorks starts, the strict export refuses an
  Equal between a line and an arc, a relation to a point no entity uses, an angle
  between parallel lines and relations that do not hold in the sketch.
- **Sketch**: a point Coincident with a spline now stays on the spline (the relation
  used to be stored but not enforced). An Equal between a line and an arc or circle
  (length = radius) is refused, as in SolidWorks (`relate` raises; the GUI never offered
  it).
- **SolidWorks export: sketch planes and entities**: a sketch now sits on what it
  references in BauyrCAD (the standard plane, the reference plane itself, or the model
  face, also one away from the origin, inclined, or lying on a standard or earlier
  plane), so it follows that face in SolidWorks and no extra plane is made. On inclined
  faces and angled planes SolidWorks' sketch axes are turned onto BauyrCAD's, so
  Horizontal / Vertical relations and horizontal / vertical dimensions keep their meaning
  (they used to block the export); on an explicit plane whose origin is not the model
  origin's projection, dimensions to the sketch origin go to a hidden helper point
  (`<sketch> Points`). Every entity arrives as the same SolidWorks entity, counted before
  the sketch is closed: arcs of every kind (also on back faces), splines through
  BauyrCAD's own points (editable spline points, no longer re-fitted, so the GUI export
  accepts them; more than 4 points can differ by ~1e-5 of the volume, a note), Point
  entities (Fix, midpoint), construction circles / arcs / splines, shared centres (slots,
  offsets) as one SolidWorks point, and the results of the rectangle, polygon, slot,
  fillet, chamfer, offset, mirror, trim / extend and convert tools. **Sketch text**
  becomes editable SolidWorks Sketch Text (text, font, bold / italic, height; angled text
  runs along a construction line), checked by the letters' volume (font outlines differ
  by a few %, a note) and position; text sharing a sketch with other curves, multi-line
  text and '<' are refused before SolidWorks starts. Empty and unused sketches are kept.
  A sketch whose face reference is lost is refused by the strict export. A dimension
  SolidWorks would measure differently is never kept. Selected contours of a sketch on a
  face could pick the wrong region (one bounded by the face's edges): fixed.
- **SolidWorks export**: a hidden SolidWorks started for an export now always exits,
  also after a feature SolidWorks failed to make.
- **Offline SolidWorks decoder**: a 2D sketch saved after 3D sketches is decoded as a 2D
  sketch (class indices follow the order of their headers).
- **SolidWorks export foundations**: File > Export to SolidWorks (part and assembly)
  now lists the known gaps before SolidWorks starts (unsupported feature types and
  modes, suppressed or failing features, parameters/equations that would arrive as plain
  values, later or multi-solid imports, sketch text), instead of failing halfway. Every
  step, extrudes and revolves included, is validated by where the material went (first
  moment), not only by volume, with a tolerance relative to the step itself, and the
  extrude/revolve direction is derived instead of guessed, so a mirrored result can't be
  saved. The saved part is force-rebuilt and any What's Wrong item stops a strict
  export. The report separates real losses (warnings) from small approximations
  (notes: lofts, re-fitted splines, which no longer block the GUI export), and lists the
  hidden helper planes/axes made for SolidWorks (named after their step, e.g.
  `Sketch3 Plane`) and steps that became several features (`Hole1`, `Hole1-2`).
  Hole Wizard's own sketches no longer take a later step's name. Parts with only
  sketches, planes or helices can be exported. Exports work on a private copy of the
  part (the open document is never touched), use MMGS units whatever the SolidWorks
  template says, and put back every SolidWorks preference they change. A SolidWorks
  that hangs on a startup message now gives a clear error.
- **Offline SolidWorks decoder**: a 3D sketch saved after a 2D one is no longer decoded
  as a 2D sketch (each MFC class has one index in the archive).
- **Offline check of exported SolidWorks files (no SolidWorks needed)**:
  `python -m bauyrcad.core.sldprt file --tree` / `--mates` / `--keywords` reads the
  FeatureManager tree of a saved .SLDPRT / .SLDASM (names, feature types as
  `GetTypeName2`, suppression per configuration, absorbed sketches, shown dimensions)
  and an assembly's mates (type, components, alignment, distance, angle, suppression).
  `sldprt.tree_mismatches` proves an export holds the expected native steps and flags
  a body without history. The real-SolidWorks export tests now decode every file they
  save and compare it with the SolidWorks API, and also check rebuild errors and the
  centre of mass. They share one SolidWorks session (`tests/sw_support.py`).
- **SolidWorks file reader (research prototype, no SolidWorks needed)**:
  `python -m bauyrcad.core.sldprt file.sldprt` reads both SolidWorks containers
  (OLE2 up to 2014, the ZIP-like 2015+ format). `--stl` writes the stored
  display-list mesh, and `--parasolid` extracts the exact Parasolid B-rep. Not yet
  in File > Open. Format notes and the STEP roadmap: `docs/SLDPRT_FORMAT.md`.
- **SolidWorks imported-base history**: a first STEP, IGES or BREP import is kept
  as an Imported base feature; supported later sketches, bosses, cuts and other
  operations are rebuilt as separate native features. Later imports still report
  an explicit coverage gap when complete native export is required.
- **SolidWorks export fix and native-history requirement**: part and assembly
  menu actions now open the destination dialog instead of passing a boolean as
  the path. GUI exports require a native feature history and complete mates;
  unsupported/skipped features or failed mates produce an explicit error instead
  of saving a simplified substitute. API callers can opt in with `require_native`.
- **Revolve axis repair**: editing a Revolve recognizes a moved model-edge axis
  using the shared geometry matcher instead of marking changed signatures missing.
  Missing and ambiguous axes remain selected until explicitly replaced; invalid
  axes block OK without changing the document or closing the editor.
- **Preview reference replacements**: select a missing/ambiguous row in an
  applied-feature editor and choose Preview replacements. Compatible candidates
  are ordered by geometric match, with nearby/distant labels; hover or keyboard
  selection outlines even hidden geometry. Click stages a replacement; Escape
  closes the preview, and Cancel discards staged changes.
- **Safer geometry references and explicit repair**: exact face/edge matching
  checks geometry compatibility and rejects duplicate matches; moved matches
  require a nearby, clearly better candidate. Applied-feature editors retain
  missing/ambiguous selections and let a selected row be replaced by a model
  pick, with Cancel and undo/redo. `Part.reference_status()` exposes read-only
  diagnostics and candidate scores to scripts.
- **More equation-driven features**: holes, rib thickness, reference-plane offsets
  and angles, and helix dimensions now support named equations in the API and
  feature controls. Signed plane offsets and fractional helix turns are supported;
  inactive dimensions cannot silently consume equations after mode changes.
  Countersink angle overrides now take effect for standard screw sizes too.
- **Feature-control equations**: press `=` or right-click supported extrusion,
  Revolve, fillet, chamfer, shell, draft and pattern controls to edit a saved
  equation. Linked values have an `=` marker; previews retain full precision.
  Cancel discards staged links, and OK validates the complete model before an
  undoable commit. Enter a number in the equation editor to unlink a value.
- **Equations inside sketch dimension editors**: type `=thickness / 2` to link a
  dimension to named parameters, or enter a number to unlink it. Sketch-local
  undo/redo includes equation changes; Cancel discards them, and Exit Sketch
  validates and commits geometry plus links together. Invalid input stays open
  with an explanation, and the existing numeric calculator remains available.
- **Named parameters and equations**: Edit > Parameters and Equations defines shared
  length, angle and scalar values and binds driving sketch dimensions and common
  feature sizes. Changes validate units, dependencies, sketch solutions and geometry
  before applying; rename, undo/redo and saved bindings preserve design intent.
  Linked feature controls show their evaluated value and provide equation editing. Parts use
  file format 2; numeric-only parts retain format 1.
- **Select Other across modeling and assemblies**: right-click to choose occluded
  faces without hiding geometry. Feature commands filter candidates to the active
  input's faces, edges or vertices; assembly mate picking includes component names.
  Hover previews remain visible through the model. Measure honors the explicitly
  chosen face/edge/vertex instead of selecting the front surface again.
- **Revolve Select Other**: overlapping axis candidates have a preview list and
  sketch-line/model-edge/reference-axis pick filters. Straight model edges and
  earlier reference axes now persist as Revolve inputs in the GUI and scripting API.
- **Sketch Add Relations**: a visible Sketch toolbar action and `ADDRELATIONS` / `AR`
  command accumulate clicked points/lines without Ctrl and expose suitable relations
  in a compact panel. Align two or more points horizontally/vertically, apply
  horizontal/vertical to multiple lines, or make several lines collinear.
- **Revolve axis selection** now includes ordinary lines and centerlines from any
  earlier sketch, with sketch names in the list and a viewport axis picker. External
  references persist and follow edits. An isolated ordinary axis in the profile
  sketch is excluded from the revolved area without changing the saved sketch.
- **Cursor-centred middle-mouse rotation** chooses the model surface under the
  cursor (or its nearest projected point outside the silhouette) once at drag start.
  The pivot stays stationary on screen; parts, positioned assembly components and
  visible results/previews are supported. Ctrl/Shift + middle still pans.
- **Assembly Bills of Materials and drawing balloons**: drawing Insert menu,
  annotation toolbar and `BOM` / `BALLOON` / `REFRESHBOM` commands. Move tables
  and balloons by dragging; double-click tables to edit drawing text overrides.
  Source-file grouping keeps repeated quantities and distinct same-name parts;
  item numbers survive refresh and deleted instances display unresolved balloons.
  Part Properties stores part number/description in `.bcad`. API, `.bcdrw`
  persistence, undo/redo and whole-sheet PDF/DXF/SVG/PNG export are supported.
  Flat assemblies only; leader tips follow local coordinates, not face/edge edits.
- **Pack and Go** for assemblies: File menu / toolbar / `PACK` command and
  `Assembly.pack_and_go(path)` create a portable ZIP with the current poses/mates
  and all referenced saved part files. Relative paths, repeated parts, duplicate
  filenames, missing-file checks and atomic output are handled. Extract the whole
  ZIP and open `assembly.bcasm`. Save external part edits first; drawings/scripts
  and native SolidWorks files are not included.
- Revolved Boss/Cut can start directly from an active sketch, with both commands
  on the Sketch tab and the angle selected for immediate typing. Fillet radius,
  Chamfer distance and Shell thickness also receive immediate numeric focus.
- **Nastran BDF export** from the Simulation tab/menu or `BDF` / `NASTRAN` command:
  choose mesh settings and export the active linear-static study without solving it.
  Includes tet4/tet10, elastic material, fixed faces, point forces and equivalent nodal
  face-force/pressure loads, with consistent mm/N/MPa units. Export runs in a background
  worker against a document snapshot and writes atomically. API: `study.export_bdf(path)`.
  Verified with MSC Nastran 2024.1 for both element orders. Contact/nonlinear studies and
  Nastran result import are not yet supported.
- **Sketch snapping like SolidWorks Quick Snaps**: new Tangent, Perpendicular, Parallel and
  Angle snaps (15 deg steps, adjustable) besides Point, Center, Midpoint, Quadrant,
  Intersection, Nearest, Horizontal / Vertical and the model snaps; a line drawn to a
  tangent / perpendicular / parallel snap gets that relation. **Quick Snaps** button (Sketch
  tab): force one snap type for the next click, turn each snap type on / off (remembered),
  set the angle step; command line overrides `END`, `MIDP`, `CEN`, `QUA`, `INTS`, `NEA`,
  `TANSNAP`, `PERP`, `PARSNAP`. Dragging a sketch point snaps too, and dropping it on another
  point joins them (coincident) - or on a midpoint / curve adds that relation (Shift: no snap).
- Extrude automatically focuses and selects the distance, keeping the 10 mm default
  while allowing immediate typing to replace it.
- Command hover help includes a name and description across parts, assemblies,
  drawings and sketch tools, with input/result illustrations for complex operations.
- Extruded Boss/Cut buttons are available on the Sketch tab and can start directly from an active sketch: it is accepted and
  selected automatically, including edits to an existing sketch.
- All built-in icons now render directly from SVG at the requested size and display
  pixel density instead of scaling a fixed set of raster images.
- Windows packaging no longer bundles an incompatible ICU library from an unrelated
  Poppler installation on PATH, which prevented the Qt interface from loading.
- Native SolidWorks assembly regression check now verifies that a mated floating
  pin remains free to rotate after reopening the exported assembly.
- Left-drag no longer rotates the model view; middle-drag rotates, while selection,
  sketch editing and assembly component dragging retain their existing controls.
- Assembly interference checks use an axis sweep to skip separated component pairs,
  preserving exact overlap checks and result ordering.
- Assemblies: a component whose part file is missing but has a STEP export of the same name
  next to it is shown from the STEP (and marked in the tree); the open message lists the
  missing files in a short summary with the details folded away.
- **Export to SolidWorks now rebuilds the new features natively**: Fillet, Chamfer, Shell,
  Draft, Hole Wizard holes (counterbore / countersink / drilled / tap drill, our exact sizes),
  Linear / Circular Pattern, Mirror (features and body), reference planes (offset, mid),
  Loft, Sweep (profile or circular, along sketches and helices) and Helix. Each feature is
  checked in SolidWorks against BauyrCAD by the change of volume *and* of first moment (so a
  misplaced hole or a mirrored chamfer is caught). Rib, Wrap, Intersect, Boundary, body
  patterns and sketch text still bring the part in as a solid body.
- Sweeps along paths with sharp corners are mitred (they stopped at the first corner).
- Instant3D: dragging far could under-shoot the depth (the arrow's base moved during the
  live rebuild).
- **SolidWorks Features tab**: Fillet (constant / variable, edges or all edges of faces),
  Chamfer (distance, distance-distance, angle-distance), Shell (open, closed, outward), Draft
  (neutral plane), **Hole Wizard** (clearance / tapped / counterbore / countersink, ISO metric
  sizes M2-M24, blind with drill point or through, callouts), Linear / Circular Pattern and
  Mirror (of features or the whole body), Swept Boss / Cut (profile sketch or circular
  profile, along sketches, helices and 3D curves), Lofted and Boundary Boss / Cut, Rib,
  reference Plane (offset, angle, mid, 3 points, normal to curve), Axis and Point, Helix /
  Spiral and Curve Through XYZ Points. Each has a PropertyManager with SolidWorks-style
  selection boxes (click edges / faces in the model), a live preview (added material yellow,
  removed red) and edits from the tree; the Features tab is laid out like SolidWorks.
- **Instant3D** (on by default, Features tab): click a face made by an extrude and drag the orange
  arrow to change its depth live; the depth snaps to round values (finer when zoomed in), undo
  with Ctrl+Z.
- **Wrap** (emboss / deboss sketch profiles or text onto a cylindrical face, arc lengths kept)
  and **Intersect** (split the body with planes: remove regions, or fill closed voids such as
  the inside of a shelled part under a capping plane).
- Sketch: **Display/Delete Relations** (every relation and dimension in one filtered list;
  selecting shows them, Delete / Delete All) and **Repair Sketch** (closes small gaps between
  curve ends, deletes zero-length and overlapping duplicate curves).
- Sketch: **Text** (tool `TEXT`, `sk.text(...)`): any installed font, bold / italic, height =
  capital letter height, angle; click a text to edit it. The letters are profiles - engrave
  part numbers with Extruded Cut, emboss with Extruded Boss.
- Sketch: **Convert Entities** (tool `CONVERT` / `CE`, `sk.convert(face=...)`): project model
  edges into the sketch - hover and click edges, or convert every edge in the sketch plane
  (a face outline with its holes) in one click; the converted curves are fixed.
- Edges and faces used by features are remembered by geometry, so fillets, holes, shells and
  patterns follow edits of earlier features. Sketches on faces and reference planes now move
  with them too.
- Right-click / context bar on a face: Hole Wizard, Fillet its edges, Shell (remove it).
  Command line: `HOLE`, `SHELL`, `DRAFT`, `RIB`, `SWEEP`, `LOFT`, `BOUNDARY`, `PLANE`, `AXIS`,
  `HELIX`, `CURVE`; `FILLET` / `CHAMFER` / `MIRROR` / patterns are the 3D features outside a
  sketch and the sketch tools inside one.
- API: `part.fillet/chamfer/shell/draft/hole/pattern_linear/pattern_circular/mirror/sweep/
  loft/boundary/rib/plane/axis/point/helix/curve/edges_where`, `part.sketch(..., offset=)`.
- **Export to SolidWorks** (File > Export to SolidWorks, assembly window button, command `SW`,
  `part.export_solidworks()`, `asm.export_solidworks()`, `python -m bauyrcad solidworks`):
  BauyrCAD drives the SolidWorks installed on the PC and has it build native files.
  - Parts get an **editable feature tree**: sketches on the same planes / faces / offset
    planes, with relations and driving dimensions (a fully defined sketch stays fully defined),
    then Boss/Cut-Extrude (blind, mid plane, through all, both directions, selected contours)
    and Revolved Boss/Cut. Every feature is checked against BauyrCAD's volume.
  - Assemblies (.SLDASM) get fixed / floating components exactly placed and every mate as a
    real SolidWorks mate (coincident, concentric, parallel, perpendicular, distance, angle,
    lock), so the assembly **moves in SolidWorks**.
  - Anything that can't be rebuilt exactly (imported STEP bodies) comes in as a solid body; the
    report says what was rebuilt. SolidWorks runs hidden and is closed afterwards; an already
    open SolidWorks is used but only the new documents are touched.
- Fixed: big models were sliced ("obscured") after Normal To / a sketch / rotating: the camera
  sat inside the model at a fixed 1000 mm and parallel projection clipped everything behind it.
  The camera now always stays outside the model.
- **Closed regions are shaded while sketching** (also areas formed by crossing curves): what
  Extrude will use is visible at a glance; an open outline stays unshaded. Option in the sketch
  panel.
- **Smart Dimension stays active** until Esc (the Enter that confirmed a value, and a right-click,
  used to end it).
- **Dynamic input** (AutoCAD): after the first corner of a rectangle type the width, Tab, the
  height, Enter (or click); lines take length / angle, circles the diameter. The preview follows
  the typed values; they become driving dimensions (optional).
- **Numpad views** with smooth camera transitions: 1 isometric (again: next corner), 3 front
  (again: back), 4 / 6 left / right and 8 / 2 top / bottom (again: turn 45 deg), 7 / 9 roll,
  5 **View Selector** (SolidWorks cube: faces, edges, corners), 0 Normal To, . zoom to fit. Views,
  Normal To and entering a sketch on another plane now glide instead of jumping.
- **Object snaps** while sketching, with markers and labels: centre, quadrant, intersection
  (relations added), and the model's edges, vertices, midpoints and hole centres on the sketch
  plane.
- **Context toolbar**: clicking a flat face, or a plane (Front / Top / Right Plane are now in the
  feature tree), pops up Sketch / Normal To / Measure next to the cursor.
- **Measure** (View > Measure, command MEA): click faces, edges and vertices for length, area,
  diameter, minimum distance with dX/dY/dZ, centre / axis / normal distance and angle; the
  distance is drawn in the view. Scripting: `part.measure(...)`, `part.edge(near=...)`.
- **FEA solver: PARDISO by default** (`pypardiso` is now a standard dependency and the exe ships
  the Intel MKL runtime). A 6 mm plate with 502 000 equations solves in 20 s instead of more
  than 9 minutes. Before solving, the memory needed is estimated: a model too big for the
  computer stops at once with advice (coarser global mesh + local refinement) instead of
  crashing with "PARDISO error -2", and the slow SuperLU fallback is no longer attempted on
  big models.
- **Assembly renders are fast**: all edges go into one line mesh (they were one VTK actor per
  edge, so a 67-part gripper with vendor STEPs took over 10 minutes; now 8 s, then 1 s per view).
- **Clearance**: `asm.clearance(a, b)` gives the exact minimum distance and closest points (or the
  nearest component to `a`), and `asm.clearance_sweep(...)` the smallest clearance over a motion
  study.
- **Extruded cuts go into the material like SolidWorks**: a blind or Through All cut sketched
  on a boundary of the body (e.g. a hole sketched on the Front plane of a part extruded from
  it) now flips to the side that has material instead of silently removing nothing. An explicit
  Reverse direction is still honoured, and a cut (extruded or revolved) that removes no
  material is now a feature error "The cut does not intersect the model". The Extrude Cut
  preview shows the direction actually used.
- **Motion studies** (from the mini robot arm project): drive angle / distance mates
  (`asm.set`, `asm.drive`) and the mechanism follows, including closed loops; keyframed
  motion with eased interpolation (`asm.motion`) and an animated GIF with a traced point
  (`asm.animate`); `asm.suppress` to save driven joints free for dragging.
- **Interference detection**: `asm.interference()` lists overlapping components with the
  overlap volume (touching faces and same-size shaft/bore fits are not interference).
- `asm.mobility()`: total DOF of an assembly (per-component DOF counts parents' motion too).
- Drawings: overall sizes over rounded ends are exact (were the chord of the tessellated arc,
  e.g. 119.89 for 120); Shift-click an arc in Smart Dimension to measure to its max/min
  point (a plain click on a circle still picks the circle); `dr.radius` picks arcs (a point on
  the arc selects it); `dr.dim(..., at=point)` places a linear dimension anywhere.
- **Revolved Boss / Revolved Cut**: revolve about a sketch centerline (automatic when there
  is only one) or any sketch line; angle, mid-plane, reverse, contour selection; the axis is
  highlighted and the preview is live. Volumes checked with Pappus' theorem.
- **Assemblies** (`.bcasm`, File > New Assembly, command `ASM`): insert parts (.bcad, STEP,
  IGES), SolidWorks-style mates (coincident, parallel, perpendicular, distance, angle,
  concentric, lock), fixed / floating components, drag to move or rotate components within
  what their mates allow, `(f)` / `(-)` markers and DOF status, undo, live reload when a part
  file changes, STEP/IGES/STL export of the whole assembly. Scripting: `bauyrcad.api.Assembly`.
- Mated faces and FEA fixtures follow part edits that move or resize a face (FaceRef now
  matches by kind and normal when the exact signature changed).
- `bauyrcad` Claude skill: finds the app from any chat or Cowork session
  (`scripts/find_bauyrcad.py`), and logs friction and improvement ideas in
  `docs/IMPROVEMENTS.md` while working. `packaging/build_skill.py [--install]` packages it.
- **Drawings** (`.bcdrw`, File > Make Drawing from Part, Assembly > Make Drawing, command
  `DRW`): SolidWorks-style sheets A4-A0 with an ISO title block and projection symbol,
  Standard 3 View (first angle ISO / third angle ANSI) plus isometric, Model View, Projected
  View (stays aligned with its parent), exact hidden-line views (hidden lines visible/removed,
  tangent edges, centre marks), Smart Dimension (length, horizontal/vertical/aligned distance,
  point-to-line, diameter, radius), Auto Dimension, notes, text overrides, undo. Dimensions are
  driven by the model and follow part edits (live reload). Export **PDF**, **DXF** (layers, ISO
  linetypes, real DIMENSION entities), **DWG** (via the free ODA File Converter), SVG, PNG, and a
  single view at 1:1 DXF for laser/CNC. Scripting: `bauyrcad.api.Drawing`; CLI
  `python -m bauyrcad drawing`.
- **STEP export of assemblies keeps the assembly structure**: one product per part file with
  placed instances, names and colours, so SolidWorks opens it as an assembly of parts (it was a
  single multi-body part before).
- Assembly mouse: **left-drag** a component moves it without rotating it, **right-drag**
  rotates it (a plain right-click still opens its menu). Both respect the mates. The separate
  Move / Rotate modes are gone. Scripting: `drag_to(..., allow_rotation=False)`.
- **Mate animation**: components glide into place when a mate is previewed, added, edited,
  flipped, suppressed, deleted or undone (toggle: Animate Mates).
- Fixed: planar faces with left-handed axes (revolved caps) reported inward normals.

## 0.3.0 (unreleased)
- Extrude / Cut shows the sketch and lets you **select contours**: click inside closed areas
  to choose which ones to extrude, including areas formed by crossing geometry. Selections are
  saved with the feature. Editing a feature shows the model rolled back to before it.
- **Scripting API** `bauyrcad.api` (Part / Sketch / Study) and **command line**
  (`python -m bauyrcad info|render|run|export|script`), with off-screen PNG rendering of
  models and results. See `docs/API.md`.
- The GUI **reloads a part automatically** when its file changes on disk (File > Reload,
  Ctrl+R; can be switched off).
- `projects/` folder for personal CAD work, and `CLAUDE.md` working agreement.
- **Face Force** and **Pressure** loads (GUI + API), validated against F/A, Lamé and
  cantilever theory. **Local mesh control** on faces (API).
- Results summary also reports stress and FoS away from point-load singularities. New
  `probe`, `max_in` and `mean_on` post-processing, and `run(verbose=True)` progress. Warnings
  for large models and iterative-solver fallback.
- Faces know their type and diameter: `part.holes(d=9)`, `part.face(kind="cylinder", d=9, at=...)`.
- Materials: EN 10025 (S235/S275/S355) and GOST grades (Ст3сп, 09Г2С, Сталь 45, 40Х,
  12Х18Н10Т, АМг6, Д16Т); ultimate strength for all materials.
- Fixed: result legends could show negative von Mises (quadratic overshoot); displacement
  plots no longer clip the legend by default.
- **Space repeats the previous command** (AutoCAD), whether it was started from the toolbar,
  a menu, a right-click or typed. In a sketch, Space ends the chain, then the tool, then
  repeats.

## 0.2.0
- Constraint-based sketcher with the SolidWorks tool set, Smart Dimension, relations,
  drag-to-modify and fully/under-defined colouring.
- AutoCAD-style type-to-command line with aliases and typed coordinates.
- Export STEP AP203/214/242, IGES, BREP, STL, OBJ, glTF, VRML; import IGES and BREP.

## 0.1.0
- Sketch (line, rectangle, circle), extruded boss/cut, STEP import/export.
- Linear static FEA (tet10), fixed faces, point forces, result plots. Windows build.
