# Roadmap

Ordered roughly by value for an engineer doing CAD + FEA day to day.

## 0.2: Sketching that feels like a CAD system (done)
- [x] Constraint solver with drag, DOF analysis and over-definition checks
- [x] SolidWorks draw tools: lines, rectangles, slots, circles, arcs, polygon, spline, point
- [x] Smart Dimension (linear/H/V/point-line/angle/radius/diameter), driven dimensions
- [x] Relations: coincident, midpoint, H/V, parallel, perpendicular, collinear, equal, tangent, concentric, symmetric, fix
- [x] Trim, extend, offset, mirror, fillet, chamfer, move/copy/rotate/scale, patterns, construction
- [x] AutoCAD-style command line with aliases and typed coordinates
- [x] STEP AP203/214/242, IGES, BREP, STL, OBJ, glTF, VRML export; IGES/BREP import

## 0.2.x: Sketcher polish
- [ ] Ellipse, partial ellipse, conic, 3-point arc slot
- [ ] Convert Entities / Intersection Curve from model edges
- [ ] Spline tangency handles and curvature control
- [ ] Equation-driven dimensions and global variables (`D1 = D2 / 2`)
- [ ] Automatic relations while dragging onto other geometry
- [ ] Relation glyph icons (SolidWorks-style bitmaps) instead of text symbols

## 0.3.x: from the first real job (L-bracket skill test)
- [x] Face force and pressure loads, local mesh control, robust (non-singular) summary,
      probe / max_in / mean_on, hole-wall face filters, EN/GOST materials with ultimate strength
- [ ] Bolt / bearing fixtures and compression-only (contact) support; split lines for washer
      footprints
- [ ] Sheet metal: base flange, bend with K-factor, flat pattern
- [ ] 3D fillet / chamfer features
- [ ] Study clone and a `convergence(sizes)` helper; save results with the part (optional)
- [ ] GUI editor for mesh controls (the API already supports them)
- [ ] Edge loads, remote loads, gravity

## 0.4: Assemblies (first version done)
- [x] Components, mates (coincident / parallel / perpendicular / distance / angle / concentric /
      lock), mate solver with drag, DOF analysis, over-definition checks, STEP export
- [ ] Interference detection and clearance check
- [ ] Width, symmetric, gear, cam, limit-distance / limit-angle mates; mate to edges, vertices,
      reference planes
- [ ] Sub-assemblies, patterns of components, exploded views, BOM table
- [ ] Analytic mate Jacobian (dragging is ~20 fps now; faster for big assemblies)
- [ ] Assembly FEA: bonded contact between components, then frictionless contact
- [ ] Edit a part in the context of the assembly

## 0.5: Drawings (first version done)
- [x] Sheets, title block, standard / model / projected views, HLR, hidden lines, centre marks
- [x] Smart Dimension (linear, aligned, diameter, radius), auto dimension, notes; PDF/DXF/DWG
- [ ] Section views (A-A) with hatching, detail views, broken-out sections, crop views
- [ ] Angular and ordinate dimensions, chamfer dims, tolerances, GD&T frames, surface finish
- [ ] Centre lines for cylinders seen from the side, thread symbols, hole callouts
- [ ] Import the part's sketch dimensions (SolidWorks Model Items)
- [ ] Multiple sheets, BOM table and balloons for assembly drawings
- [ ] Shaded / shaded-with-edges views

## 0.3: More features
- [x] Revolve boss/cut
- [ ] Fillet, chamfer, shell, hole wizard
- [ ] Linear / circular pattern, mirror
- [ ] Reference planes (offset, angle), axes
- [ ] Up-to-surface / up-to-next / offset-from-surface end conditions, draft
- [ ] Rollback bar and insert-in-the-middle of the feature tree
- [ ] Measure tool, mass properties dialog
- [x] STEP export of assemblies with names/colours (XDE/STEPCAF)

## 0.4: Simulation depth
- [ ] Pressure and distributed force on faces, remote loads, gravity/acceleration, bearing load
- [ ] Roller/slider, pin, prescribed displacement, symmetry fixtures
- [ ] Mesh controls (local size on faces/edges), mesh quality report, adaptive h-refinement
- [ ] Result probing, section plots, iso-surfaces, animation of the deformed shape
- [ ] Report generation (HTML/PDF) with study summary, plots and factor of safety
- [ ] Multiple load cases per study, result comparison
- [ ] Frequency (modal) analysis: generalised eigenproblem with `scipy.sparse.linalg.eigsh`
- [ ] Linear buckling
- [ ] Thermal steady-state and thermo-mechanical coupling
- [ ] Contact between bodies (bonded first, then frictionless) → assemblies
- [ ] Nastran BDF export/import, to cross-check against MSC Nastran

## 0.5: Assemblies and platform
- [ ] Multi-body parts, assemblies with mates
- [ ] Python scripting console and macro recording (the core is already GUI-free)
- [ ] Parametric design tables, optimisation loops (thickness vs. max stress)
- [ ] Plugin system for new features, loads and solvers
- [ ] macOS/Linux builds (all dependencies are cross-platform)
- [ ] Auto-update, crash reporting, signed installer

## Technical debt / known limitations in 0.1
- Sketches on faces are placed by value (origin/normal), not associatively linked to the face.
- The whole feature tree is regenerated on each change (fine for small parts, slow for big ones):
  add caching of intermediate bodies.
- Point loads go to the single nearest corner node. Distributed loads are the physically better option.
- Gmsh (GPL) is the mesher. For a closed-source commercial release, replace it behind
  `fea/mesher.py` (e.g. Netgen, LGPL) or obtain a commercial Gmsh license.
