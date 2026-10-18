# Architecture

```
bauyrcad/
├── core/                 CAD: no GUI imports, fully testable headless
│   ├── plane.py          SketchPlane: local (u, v) frame in 3D; Front/Top/Right planes
│   ├── sketch/           constraint-based 2D sketcher (no GUI)
│   │   ├── model.py      points / curves (line, circle, arc, spline, point) / constraints
│   │   ├── constraints.py relation + dimension residual equations
│   │   ├── solver.py     Newton solver, drag, DOF analysis, over-definition checks
│   │   ├── builders.py   drawing tools with automatic relations (rectangles, slots, ...)
│   │   ├── ops.py        trim, extend, fillet, chamfer, offset, mirror, transforms, patterns
│   │   └── profiles.py   closed loops → regions with holes for features
│   ├── exchange.py       STEP/IGES/BREP/STL/OBJ/glTF/VRML export, STEP/IGES/BREP import
│   ├── geometry.py       ALL OpenCASCADE calls: faces, prisms, booleans, tessellation, queries
│   ├── step_io.py        STEP read/write (OCC STEPControl)
│   ├── features.py       Parametric features: Sketch, Extrude (boss/cut), Import STEP
│   └── document.py       Feature list + studies, regeneration, .bcad save/load, undo snapshots
├── fea/                  Simulation: no GUI imports
│   ├── study.py          Study, Material library, FixedFace, PointForce, FaceRef
│   ├── mesher.py         Gmsh tetra meshing; maps CAD faces → mesh nodes
│   ├── solver.py         Linear elastic tet4/tet10 FEM: assembly, solve, stress recovery
│   └── analysis.py       Study + Document → mesh → solve → result fields
└── gui/                  Qt (PySide6) + VTK (pyvista)
    ├── main_window.py    Command tabs, feature tree, property panel, files, undo/redo, threading
    ├── commands.py       Interactive commands: Extrude, FixedFace, PointForce, Results
    ├── sketcher.py       Sketch mode: tools, snapping/inference, drag, Smart Dimension, relations
    ├── sketch_draw.py    Dimension graphics, dashed lines, relation glyph placement
    ├── command_line.py   AutoCAD-style type-to-command popup and value parser
    ├── viewer.py         3D viewport: rendering, picking, sketch-plane input, result plots
    ├── dialogs.py        Material, Mesh & Run, background AnalysisWorker, About/Controls
    └── icons.py          Built-in SVG icon set
```

**Dependency rule:** `gui → fea → core`. `core` and `fea` never import Qt, so everything except the
UI can be scripted, tested and later exposed as a Python API or a batch solver.

## Modelling pipeline

```
Sketch entities ──loops()──► oriented Loops ──regions()──► Regions (outer + holes)
      │                                                      │ geometry.region_to_face
      ▼                                                      ▼
 SketchFeature.execute ─────────────────────────────► planar TopoDS_Faces
                                                             │ ExtrudeFeature.tool_shape (prism)
                                                             ▼
 RegenContext.body  ◄── fuse (boss) / cut (cut) ◄──── tool solid
```

* `Document.regenerate()` replays every feature from scratch into a `RegenContext`. A failing
  feature stores its message in `feature.error`, and the replay continues so the rest of the
  tree still builds (same as SolidWorks' "rebuild errors").
* After each boolean, `ShapeUpgrade_UnifySameDomain` merges coplanar faces so picking and
  fixtures see clean faces.
* The document serialises to plain JSON (`Document.to_dict`). The same snapshots drive
  undo/redo in the GUI.
* Imported STEP geometry is embedded in the project file, so a `.bcad` is self-contained.

### Adding a feature (e.g. Revolve)
1. In `core/features.py`: subclass `Feature`, set `TYPE`/`NAME_PREFIX`, implement `execute(ctx)`,
   `params()`, `_from_params()`, and decorate with `@register`.
2. Add any new OCC calls to `core/geometry.py` (e.g. `revolve(face, axis, angle)`).
3. In `gui/commands.py`: add a `Command` with a panel and a preview (copy `ExtrudeCommand`).
4. In `gui/main_window.py`: add a `QAction` to the Features tab, and a branch in `edit_feature`.
5. Add tests in `tests/test_modelling.py` checking volumes against hand calculations.

## Sketch solver

* **Unknowns:** x, y of every non-fixed point, plus the radius of every circle. Arcs are
  centre + start + end points with an implicit "equal distance" equation.
* **Constraints:** each relation or dimension type is a residual function in `constraints.py`
  (zero when satisfied). The Jacobian is built from small per-constraint central differences,
  so adding a new constraint type means writing only its residual.
* **Solve:** damped minimum-norm Newton steps `dx = -Jᵀ(JJᵀ + λI)⁻¹ r`, which move the
  geometry as little as possible. **Drag** adds the cursor target as extra equations, then
  re-solves the constraints alone.
* **DOF / colours:** the null space of J tells which points are still free, so entities are
  shown blue (under-defined) or black (fully defined).
* **Over-definition:** a new constraint that doesn't raise the rank of J is redundant or
  conflicting. It is rejected, or offered as a driven dimension.

### Adding a sketch tool
Write a builder in `core/sketch/builders.py` that creates the entities and their automatic
relations. Then add a `Tool` subclass in `gui/sketcher.py` (`n` clicks, `commit_mask`, `build`);
the preview comes for free. Finally register it in `TOOLS`/`PANEL_GROUPS`, give it a
command-line alias in `MainWindow._register_commands`, and add an icon in `gui/icons.py`.

### Adding a constraint type
Add `r_<name>(G, c)` to `constraints.py` and register it in `TYPES`. If it should appear in
*Add Relations*, extend `applicable_relations`/`make_relation`.

## Assemblies (`core/assembly.py`, `gui/assembly_window.py`)

* A `Component` references a part file and has a rigid `Pose` (R, t). Mates reference faces
  by `FaceRef` in the part's own coordinates, and planar faces become (point, normal) while
  cylinders/cones become (point, axis).
* `MateSolver`: 6 unknowns per floating component (small translation + rotation vector),
  re-linearised around the current poses every Newton step (`Pose.moved` uses Rodrigues).
  The same damped minimum-norm steps as the sketch solver make unconstrained parts move as
  little as possible.
  - Axis relations use a sign that is frozen per iteration, so they have no direction and
    no slope singularity at 90°.
  - Face-to-face mates default to anti-aligned. A part facing exactly the wrong way is
    pre-rotated 180° (the linearisation has no direction there).
* Dragging solves a weighted least-squares problem with the mates dominating, so the part
  follows the mouse only along its free motion. Resolved face geometry is cached per solve.
* DOF per component: rank of the null-space block of the mate Jacobian.
* `.bcasm` = JSON with component sources stored relative to the assembly file.

To add a mate type: a residual branch in `mate_residual`, the allowed face kinds in
`mate_options`, orientation defaults in `Assembly.new_mate`, a label in `MATE_LABELS`.

## Drawings (`core/drawing.py`, `core/drawing_export.py`, `gui/drawing_window.py`)

* `geometry.project_hlr` runs OCC's exact hidden-line removal (HLRBRep_Algo) for a view
  direction and returns plain 2D edges (lines, circular arcs, polylines), visible or hidden,
  sharp / smooth (tangent) / outline. `drawing.clean_edges` drops hidden edges that lie under
  visible ones (e.g. the back rim of a through hole), as a drawing shows them.
* `Drawing` is plain data: sheet, views (direction, sheet position, scale, parent + alignment
  for projected views), dimensions (points in view coordinates = model mm) and notes. Views are
  placed by their bounding-box centre, so a projected view shares an axis with its parent.
* Everything renders to a list of primitives in sheet mm (`Line`, `Arc`, `Poly`, `Text`,
  `Solid` with a layer and an owner id). The Qt canvas paints them; `drawing_export` writes
  them with matplotlib (PDF/SVG/PNG) or ezdxf (DXF, with dimensions as real DIMENSION entities
  and `dimlfac = 1/scale` so they show model values). DWG = DXF + ODA File Converter.
* Text height is the cap height and alignment is on the cap box, so all backends agree.
* Associativity: after `reload()` each dimension point moves to the same vertex, or to the
  vertex in the same relative place of the view (a corner stays that corner).
* `.bcdrw` = JSON with the model path relative to the drawing; an unsaved (scripted) model is
  embedded as BREP text.

## Applied and advanced features (`features_more.py`, `modeling.py`, `refs.py`)

* `features_more.py` holds Fillet, Chamfer, Shell, Draft, Hole, Linear / Circular Pattern,
  Mirror, Sweep, Loft, Boundary, Rib and the reference features (Plane, Axis, Point, Helix,
  Curve). They register themselves like the base features; `features.py` imports the module.
  All their OCC work is in `modeling.py`.
* **References** (`refs.py`): edges and faces are stored as `EdgeRef` / `FaceRef` signatures
  and re-found in the body of each regeneration (`refs.resolver(ctx)` caches the face / edge
  lists per body). Planes and axes are small spec dicts (`std`, `face`, `edge`, `feature`,
  `sketch_line`, `explicit`) resolved by `resolve_plane` / `resolve_axis`. Sketches carry an
  optional `plane_spec`, so a sketch on a face or reference plane follows it.
* **Patterns and mirrors** replay tool bodies: every feature that adds / removes a body sets
  `feature.tools = [(shape, "boss" | "cut")]` during regeneration (not saved); a pattern
  transforms those and applies them in one boolean (`geometry.fuse_many / cut_many`).
  Fillets / chamfers have no tool body; pattern the body instead (`bodies=True`).
* Reference features change no body; they publish `result_plane`, `result_axis`,
  `result_point` or `result_wire` for later features.
* GUI: `gui/feature_commands.py` - one `FeatureCommand` base (rolled-back body for picking,
  `PickBox` selection boxes, debounced preview by executing the feature on a copy of the
  regeneration context, replace-in-place on OK) and a small subclass per feature.
  `COMMAND_FOR_TYPE` maps feature types to their command for editing from the tree.

## SolidWorks export (`solidworks.py`, `gui/solidworks_export.py`)

* The closed `.sldprt` / `.sldasm` formats are written by SolidWorks itself, driven through its
  COM API (pywin32). `Session` starts a private hidden instance (or reuses a running one, then
  only touches the documents it created), generates typed bindings from `sldworks.tlb`
  (`gencache.EnsureModule`; into `%LOCALAPPDATA%\BauyrCAD\gen_py` in the exe) and casts every
  returned object to its interface. Arrays must be passed as `VT_ARRAY | VT_R8` SAFEARRAYs.
* Parts replay BauyrCAD's feature list: each sketch goes on the matching standard plane,
  offset plane (`InsertRefPlane`) or model face (found by `GetClosestPointOn` + normal); the
  sketch's `ModelToSketchTransform` maps our world points into SolidWorks sketch coordinates;
  entities are created with `AddToDB`, then relations (`SketchAddConstraints`) and driving
  dimensions (`AddSpecificDimension`, falling back to `AddDimension2` when the value agrees).
  Extrudes / cuts / revolves are created, and the SolidWorks volume is compared with
  `Document.regenerate(upto=i + 1)`; a mismatch retries the other direction, then falls back
  to a solid body (STEP import) for the whole part.
* Selected contours: SolidWorks regions can't be picked by coordinates via the API, so each
  `ISketchRegion`'s edges are sampled and the region containing our contour point is selected.
* The applied features live in `solidworks_features.py`: one handler per feature type,
  called with an `attempt` number for alternatives (flip, swapped chamfer distances, pattern
  direction). `_native_feature` keeps the first attempt whose change of volume and of first
  moment matches BauyrCAD's (per feature, so a small accepted loft difference doesn't fail
  later features) and deletes the rest. Edges / faces are found in the SolidWorks body by
  geometry (points on them). Lessons: Hole Wizard holes are selected by a ray (a point pick
  places them up to 0.1 mm off) and then given our exact sizes through
  `IWizardHoleFeatureData2.ModifyDefinition`; the equal-distance chamfer type and the chamfer
  flip flag do nothing through the API; `InsertRefPlane` returns the IRefPlane, not the feature.
* Assemblies: parts are opened (AddComponent5 needs them loaded), components placed with
  `Transform2`, fixed / floated, mates added from faces matched in each component body, then
  every component pose is checked against BauyrCAD's after rebuilding.

## FEA pipeline

```
Study ─► FaceRef.resolve (centroid/area match) ─► CAD face indices
Body  ─► mesher.mesh_shape (Gmsh, BREP file) ─► nodes, tet10 elements, face → node sets
      ─► solver.solve_static:  K = Σ ∫ BᵀDB dV (vectorised),  K_ff u_f = F_f
      ─► nodal-averaged stresses, von Mises, reactions
      ─► analysis.StudyResults.field(name) / vtk_grid()
```

* **Face references**: CAD face indices change when the model is rebuilt, so fixtures remember
  each face by index *and* by geometric signature (centroid + area), and are re-resolved after
  every regeneration.
* **Mesh ↔ CAD mapping**: Gmsh renumbers surfaces on import. Each CAD face is matched to the Gmsh
  surface with the same centroid and area, and the nodes on that surface (including mid-edge
  nodes) become the fixture's node set.
* **Solver selection** (`solver._solve`): PARDISO (`pypardiso`, a default dependency; the exe
  bundles the MKL runtime in `Library/bin`). Before it runs, `direct_memory_gb` estimates the
  factorisation memory (~145 bytes per nonzero of K, measured) and a model bigger than the
  computer is refused with advice (coarser global mesh + `mesh_control`); PARDISO's own
  out-of-memory error becomes the same message. Without pypardiso: SuperLU (symmetric mode)
  below 30 000 DOF, above that CG preconditioned by smoothed-aggregation AMG (pyamg) with the six
  rigid-body modes as near-null space; SuperLU is never attempted above 150 000 equations (it
  takes many minutes) - the error says to install pypardiso instead. Thin plates are where AMG
  stalls: on a 6 mm plate with 502 000 equations PARDISO takes 20 s, AMG-CG + SuperLU > 9 min.
* **Element ordering** follows Gmsh (edges 01, 12, 20, 03, 23, 13). `analysis.vtk_grid` swaps
  nodes 8/9 for VTK's quadratic tetra ordering.
* Analysis runs in a `QThread` (`dialogs.AnalysisWorker`) so the UI stays responsive.

### Adding a load or fixture type (e.g. pressure, or a roller/slider)
1. Add a dataclass to `fea/study.py` with `to_dict` and a branch in `_bc_from_dict`.
2. In `analysis.run_study`, convert it into nodal forces or constrained DOFs. For pressure,
   integrate over the face's surface triangles (tri6 for quadratic meshes) to get consistent
   nodal forces.
3. In `solver.solve_static`, per-DOF constraints are already supported via the `fixed` mask.
   Generalise `fixed_nodes` into a `(node, component)` list for rollers.
4. Add a GUI command (copy `FixedFaceCommand` / `PointForceCommand`) and a tree entry.

## Conventions

* Units: mm, N, MPa (so E in MPa, stress in MPa, displacement in mm). Density is kg/m³, for mass display only.
* Coordinates: SolidWorks-style with Y up. Front = XY (+Z normal), Top = XZ (+Y), Right = YZ (+X).
* All OCC types stay inside `core/geometry.py` and `core/step_io.py`. Everything else passes numpy
  arrays and plain data. That keeps a kernel upgrade (OCCT 8) or swap contained.
