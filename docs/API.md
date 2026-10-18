# Scripting API and command line

BauyrCAD can be driven entirely from Python, without the GUI. Scripts produce normal
`.bcad` files that open, fully editable, in the application. **If the file is already open in
the GUI, it reloads automatically**, so you can watch a script build the model.

Run scripts with the project's Python (3.11 venv):

```powershell
.\.venv\Scripts\python.exe projects\bracket\model.py
```

Units: **mm, N, MPa** (density in kg/m³).

## Minimal example

```python
from bauyrcad.api import Part

part = Part("bracket")
sk = part.sketch("front")                 # "front" (XY), "top" (XZ), "right" (YZ)
sk.rect((0, 0), (120, 40))
sk.circle((100, 20), d=12)
part.extrude(sk, 10)                      # boss, +Z

pocket = part.sketch(on=part.face("+z"))  # sketch on the top face
pocket.rect((25, 10), (70, 30))           # on a +Z face (u, v) == (x, y)
part.cut(pocket, 4)                       # blind cut into the material

st = part.study(material="Aluminium 6061-T6")
st.fix(part.face("-x"))
st.force(part.vertex((120, 40, 10)), (0, -200, 0))
summary = st.run()                        # dict: max_von_mises, max_displacement, min_fos, ...

part.render("bracket.png", study=st)      # model + fixtures + loads
st.render("bracket_vm.png")               # von Mises contour (99th-percentile legend)
st.render("bracket_disp.png", field="Displacement Magnitude")
part.save("bracket.bcad")
part.export("bracket.step")               # or .igs .stl .obj .glb .wrl .brep (by extension)
```

## Part

| Call | Meaning |
|---|---|
| `Part(name)` / `Part.open(path)` | new / existing part |
| `part.sketch(plane="front")` | new sketch on a reference plane (`front/top/right`, or `xy/xz/yz`), or a `SketchPlane` |
| `part.sketch(on=face)` | sketch on a planar face (see *Sketch axes on faces*); it moves with the face when earlier features change |
| `part.sketch("top", offset=40)` / `part.sketch(plane)` | sketch on a parallel plane (a reference plane is created) / on a `part.plane(...)` |
| `part.extrude(sk, depth, midplane=False, reverse=False, contours=None)` | extruded boss |
| `part.cut(sk, depth)` / `part.cut(sk, through=True, both=False)` | extruded cut. Default direction: into the material, like SolidWorks (against the sketch normal; a one-sided blind/through cut whose sketch lies on a boundary of the body, e.g. the plane it was extruded from, flips to the side that has material). `reverse=True` forces the other side. A cut that removes nothing is a feature error "The cut does not intersect the model" |
| `part.revolve(sk, angle=360, axis=None, midplane=False, reverse=False)` | revolved boss about a sketch line, straight model edge, reference axis, standard axis or explicit point/direction (default: the profile sketch's only centerline) |
| `part.revolve_cut(sk, angle=360, axis=None, ...)` | revolved cut (grooves, bores); the profile may touch but not cross the axis |
| `contours=[(u, v), ...]` | extrude/revolve only the closed areas containing these sketch points (works with crossing geometry) |
| `part.fillet(edges, r=2, faces=(), r2=None)` | fillet edges; tangent chains are followed; `faces=` fillets all edges of those faces; `r2` = linear variable radius |
| `part.chamfer(edges, d=1, d2=None, angle=None, faces=(), flip=False)` | chamfer: equal distance, two distances, or distance + angle (deg) |
| `part.shell(t, remove=faces, outward=False)` | hollow the part; `remove` = faces left open (none = closed hollow body) |
| `part.draft(faces, angle, neutral="top", reverse=False)` | taper faces about a neutral plane / face; they lean inwards along its normal |
| `part.hole(on, points, d=None, size=None, kind="simple", depth=None, through=...)` | Hole Wizard on a face / plane at (u, v) points. `kind`: `simple` (with `size="M6"`: ISO 273 clearance, `fit=` close/normal/loose), `tapped` (tap drill + `M6x1` callout), `counterbore` (ISO 4762 / DIN 974), `countersink` (ISO 10642). Override `cbore=(D, depth)`, `csk=D`. Default through all; `depth=` blind with a 118° drill point. `feature.callout()` = drawing text |
| `part.pattern_linear(features, "x", spacing, count, direction2=, spacing2=, count2=, skip=[(i, j)])` | linear pattern of features (extrudes, cuts, revolves, holes, sweeps, lofts, ribs, patterns) - or of the body with `features=None`. Directions: `"x"`/`"-y"`, a vector, an edge (point near it) or a planar face |
| `part.pattern_circular(features, axis="z", count=4, angle=360, equal_spacing=True)` | circular pattern about `"x"/"y"/"z"`, a cylinder face (`part.face(kind="cylinder", ...)`), an edge, a `part.axis()` or `(point, direction)` |
| `part.mirror(features=None, plane="right")` | mirror features, or the whole body (`features=None`), about a plane / planar face |
| `part.sweep(path, profile=None, d=None, inner_d=None, cut=False)` | swept boss / cut of a profile sketch, or a circle of diameter `d` (tube: `inner_d`), along a sketch, `helix()` or `curve()` |
| `part.loft([sk1, sk2, ...], cut=False, ruled=False, closed=False)` | loft through one-contour sketches in order; `(x, y, z)` at either end lofts to a point |
| `part.boundary([sk1, sk2], cut=False)` | curvature-smooth loft (SolidWorks Boundary, direction 1 curves only) |
| `part.rib(sk, thickness, normal=False, side="both", flip=False)` | rib from an open sketch line: fills to the body in the sketch plane (gusset), or `normal=True` (extruded normal to the sketch to the body) |
| `part.wrap(sk, cylinder_face, depth=1, deboss=False)` | wrap the sketch's closed profiles (or text) onto a cylinder: emboss / deboss; sketch on a plane parallel to the axis (e.g. `part.sketch("front", offset=R)` for a cylinder about Y); arc lengths are kept |
| `part.intersect(planes, exclude=[pts], fill=[pts])` / `part.regions(planes)` | split the body with planes / planar faces; remove the material regions containing `exclude` points, fill the closed voids containing `fill` points; `regions()` lists them (volume, inside point, material) |
| `part.plane(base, offset=d)` / `plane(base, angle=a, axis=...)` / `plane(points=[a, b, c])` / `plane(normal_to=edge, at=p)` / `plane(mid=(f1, f2))` | reference plane (`base` = plane name, face or plane). Use in `part.sketch(plane)`, `hole(on=...)`, `mirror(plane=...)` |
| `part.axis(through=(a, b) / face= / edge= / planes=(p1, p2) / point=, direction=)` | reference axis (patterns, angled planes) |
| `part.point(xyz / arc_center=edge / face_center=face)` | reference point |
| `part.helix(center=, axis="y", d=, pitch=, turns=, height=, left_hand=False, taper=0)` / `helix(sketch=circle_sketch, ...)` | helix / spiral (give two of pitch, turns, height) - a sweep path for springs and threads |
| `part.curve(points, closed=False, polyline=False)` | 3D curve through XYZ points (sweep path) |
| `part.edges_where(kind=, direction=, face=, r=, d=, length=, near=)` | model edges as dicts (`index, kind, length, mid, p0, p1, center, radius, direction`), usable in fillet / chamfer / patterns |
| `part.import_file(path)` | STEP / IGES / BREP body |
| `part.face(normal="+z", where="max")` | outermost planar face with that normal (`where="min"` for the innermost) |
| `part.face(at=(x, y, z))` | face nearest to a point (any face type, e.g. hole walls) |
| `part.face(kind="cylinder", d=9, at=...)` | filter by surface type / diameter (add `at=` when several match) |
| `part.holes(d=None)` / `part.faces_where(kind=, d=, r=, normal=)` | lists of matching faces |
| `part.face(index=i)` | by index from `part.info()` |

Faces carry `index, kind` (plane/cylinder/cone/sphere/torus/bspline/other), `area, centroid`,
`normal` (planar), `radius / diameter, axis, axis_point` (cylinders).
| `part.export_solidworks(path, native=True, require_native=False)` | native `.SLDPRT` built by the SolidWorks on this PC: feature tree (sketches with every relation and dimension, driving and driven, each read back in SolidWorks, extrudes, cuts, revolves, fillets, chamfers, shells, drafts, Hole Wizard holes, patterns, mirrors, reference planes, lofts, sweeps, helices); returns a report: `native`, `features` (BauyrCAD steps rebuilt, in order), `warnings` (real losses: a step, relation, dimension, name or rebuild error that did not transfer; they block `require_native=True`), `notes` (approximations within tolerance, e.g. a spline or font-outline shape or a loft surface; never blocking), `helpers` (hidden helper geometry made for SolidWorks, named `<step> Plane/Axis/Points`), `steps` (a step that needed several SolidWorks features: `<name>`, `<name>-2`...), `volume`. Every step is checked by its change of volume and of first moment (where the material went), the saved part by a forced rebuild (What's Wrong) and its final volume and centre of mass; new parts use MMGS units. A part without a solid (sketches, planes, helices) exports too. A first imported solid preserves subsequent supported steps. Later imports, rib, wrap, intersect, boundary, body patterns, and sketch text sharing a sketch with other curves trigger reported solid-body fallback, or an error with `require_native=True` (found before SolidWorks starts: `solidworks.native_export_issues(part.doc)`) |
| `part.edge(near)` | index of the model edge nearest a point |

Edges for fillet / chamfer can be given as points near them (`(x, y, z)` tuples), edge indices
(a list of ints), or `edges_where()` items. Features remember their edges and faces by
geometry, so they follow edits of earlier features (change a depth and the fillets, holes
and shells rebuild).
| `part.measure(*items)` | SolidWorks Measure: faces (`part.face(...)`), edges (`("edge", i)`), vertices (`("vertex", i)` or a 3D point). Returns sizes and, for two items, `distance`, `delta`, `center_distance`, `axis_distance`, `normal_distance`, `angle`; `text` = the report lines |
| `part.vertex(near)` | model vertex nearest to a point, e.g. for point loads |
| `part.volume()`, `part.bbox()`, `part.info()` | queries (`info` = features, faces with index/normal/centroid/area, studies) |
| `part.render(png, view="iso", study=None)` | picture. Views: `iso iso2 iso_back iso_below front back top bottom right left` |
| `part.save(path)`, `part.export(path, fmt=None)` | `.bcad`, or any export format |

Failed features raise `ApiError` with the same message the GUI would show.

## Sketch

Drawing (coordinates are the sketch's (u, v)):
`line(a, b)`, `centerline(a, b)`, `polyline(pts, close=True)`, `rect(p1, p2)`,
`center_rect(c, w, h)`, `circle(c, r=|d=)`, `arc(center, start, end, ccw=True)`,
`arc3(start, through, end)`, `slot(a, b, width)`, `polygon(c, radius, sides, angle=0)`,
`spline(pts)`, `point(p)`, `text("M6", at=(u, v), height=5, font="Arial", bold=False,
italic=False, angle=0)` (letters are profiles: cut 0.5 to engrave, extrude to emboss; inside
a closed profile of the same sketch they are holes in it, like SolidWorks).

Modify: `fillet(at, r)`, `chamfer(at, d)`, `fillet_all(r)`, `trim(entity, at)`,
`offset(entities, dist)`, `mirror(entities, axis_line)`,
`pattern_circular(entities, center, count, total_deg=360)`,
`pattern_linear(entities, angle_deg, spacing, count, angle2, spacing2, count2)`,
`convert(face=part.face(...))` / `convert(edges=part.edges_where(...))` (Convert Entities:
model edges projected into the sketch, lines / arcs / circles exact, fixed unless `fix=False`).

Design intent (optional, the GUI shows it): `relate("parallel", l1, l2)`,
`dim(line, 60)`, `dim(circle, 12)` (diameter), `dim(arc, 5)` (radius), `status()`
(DOF, profile check, list of contours with interior points), `to_world(u, v)`, `local(xyz)`.

Points passed as an existing point id (`int`) share that point (connected geometry). Tuples
create new points, and an exact coordinate match with an existing point reuses it.

### Sketch axes on faces
The origin is the projection of the global origin onto the face plane.

| face normal | u | v |
|---|---|---|
| +Z | x | y |
| −Z | x | −y |
| +Y | x | −z |
| −Y | x | z |
| +X | −z | y |
| −X | z | y |

Reference planes: Front (u, v) = (x, y), Top (x, −z) with normal +Y, Right (−z, y) with normal +X.

## Study

| Call | Meaning |
|---|---|
| `part.study(name=None, material="...")` | linear static study |
| `st.material("AISI 304 Stainless")` / `st.material(E=..., nu=..., yield_strength=..., density=..., name=...)` | material |
| `st.fix(face, ...)` | fixed geometry |
| `st.face_force(face_or_list, (Fx, Fy, Fz))` | **total** force [N] spread uniformly over the faces (consistent tri6 loads). Preferred over point forces |
| `st.pressure(face_or_list, p)` | pressure [MPa] normal to the faces, positive pushes into the material (works on hole walls / curved faces) |
| `st.force(point, (Fx, Fy, Fz))` | point force [N] at *any* point, applied at the nearest surface corner node (singular) |
| `st.mesh(size=0, order=2)` | global element size (0 = automatic); order 1 = linear tets |
| `st.mesh_control(faces, size)` | local element size on faces (fillets, holes, bends), blending into the global size. Use this instead of refining the whole part |
| `st.run(verbose=False)` | mesh + solve; `verbose=True` prints timed progress. Returns the summary dict (below) |
| `st.field(name)`, `st.nodes` | nodal array of a result field; (N, 3) node coordinates in the same order |
| `st.probe(point, field)` | `(value, node_xyz, distance)` at the nearest node |
| `st.max_in(field, box=((x0,y0,z0),(x1,y1,z1)) \| center=, radius= \| face=, exclude_point_loads=True)` | `(max, xyz)` in a region, excluding the singular zone around point loads |
| `st.mean_on(face, field)` | mean of a field over a face, e.g. loaded-face deflection |
| `st.render(png, field="von Mises Stress", view="iso", deform=None, clip="auto")` | contour plot. clip = legend percentile; auto = 99 only for stresses with point loads |
| `st.export_vtu(path)` | ParaView file |

Summary keys: `max_von_mises`, `min_fos` (global, **including** singular peaks),
`max_von_mises_away_from_point_loads`, `min_fos_away_from_point_loads`,
`point_load_exclusion_radius` (present when point loads exist), `max_displacement`,
`reaction`, `applied`, `nodes`, `elements`, `dof`, `element_size`, `method`, `warnings`,
`material`, `solve_time`.

Results are **not saved** in the `.bcad` file (only the study definition is). Re-run the
study after opening. Models above about 300k DOF log a warning: they need several GB of RAM
and minutes of solve time, so use `mesh_control` rather than a finer global size.

Fields: von Mises Stress, Displacement Magnitude/X/Y/Z, Max/Min Principal Stress, Normal
Stress X/Y/Z, Shear Stress XY/YZ/ZX, Factor of Safety.

Materials (`bauyrcad.fea.study.MATERIAL_LIBRARY`, each with E, ν, yield, ultimate, density):
Plain Carbon Steel, AISI 1020, AISI 304, S235JR / S275JR / S355J2 (EN 10025),
St3sp / Ст3сп, 09G2S / 09Г2С, Steel 45 / Сталь 45, 40Kh / 40Х, 12Kh18N10T / 12Х18Н10Т,
Aluminium 6061-T6 / 7075-T6, AMg6 / АМг6, D16T / Д16Т, Ti-6Al-4V, Copper, ABS, Nylon 6/6.
Use the exact library name (e.g. `"S355J2 (EN 10025)"`), or define a custom material.

Point loads create a singular peak stress under the load. Report stresses away from the
load point (the 99th-percentile legend clip helps), and check mesh convergence with `st.mesh(size=...)`.

## Assemblies

```python
from bauyrcad.api import Assembly
asm = Assembly("hinge")
base = asm.add("base.bcad")                        # first component is fixed (grounded)
pin = asm.add("pin.bcad", at=(100, 0, 0), rotate=("x", 90))   # optional initial placement
asm.mate("concentric", pin.face(kind="cylinder"), base.face(kind="cylinder", d=10))
asm.mate("coincident", pin.face("-y"), base.face("+z"))       # faces oppose (touch)
asm.mate("distance", lever.face("-z"), base.face("+z"), 4)    # 4 mm gap
asm.mate("angle", lever.face("+y"), base.face("+y"), 30)      # degrees
print(asm.dof())                                   # {"base<1>": 0, "pin<1>": 1, ...}
asm.render("hinge.png"); asm.save("hinge.bcasm"); asm.export("hinge.step")
```

| Call | Meaning |
|---|---|
| `Assembly(name)` / `Assembly.open(path)` | new / existing `.bcasm` |
| `asm.add(path_or_saved_Part, at=None, rotate=(axis, deg), fixed=None, name=None)` | insert a part (.bcad or STEP/IGES/BREP); returns a component |
| `comp.face(...)`, `comp.holes(d=)` | face selectors like `part.face`, in the **part's own coordinates** |
| `comp.move(dx, dy, dz)`, `comp.rotate(axis, deg, about=None)`, `comp.fix(True/False)` | reposition / ground (then `asm.solve()`) |
| `asm.mate(type, face_a, face_b, value=None, flip=False)` | types: `coincident parallel perpendicular distance angle concentric lock` (lock takes two components). Raises if the mate over-defines or can't be satisfied |
| `asm.solve()`, `asm.dof()`, `asm.info()` | solve mates; remaining DOF per component; JSON-able summary |
| `asm.asm.drag_to(comp.comp.id, local_point, world_target, allow_rotation=True)` | move a component as far as its mates allow (what mouse-dragging does; `allow_rotation=False` = left-drag, translation only) |
| `asm.mobility()` | total DOF of the assembly (a 3-axis arm: 3). `dof()` counts, per component, every motion it can make including its parents' in a chain |
| `asm.set(mate, value)`, `asm.drive({name: value, ...})` | change driving **distance [mm] / angle [deg]** mates and re-solve: the mechanism moves, in small steps so it stays on its branch (closed loops such as parallelograms follow) |
| `asm.suppress(mate, True/False)` | suppress a mate (e.g. save driven joints free, so they can be dragged in the GUI) |
| `asm.motion(keyframes, frames=12)` | motion study: poses through keyframes `[{"Shoulder": 60, "Yaw": 0}, ...]` with eased interpolation |
| `asm.animate(gif, keyframes, frames=12, view="iso", size=(900, 680), fps=15, title=None, trace=(comp, local_point))` | the motion as an animated GIF (or a folder of PNG frames); `trace` draws the path of a point, e.g. the tool tip |
| `asm.export_solidworks(folder, native=True, require_native=False)` | `folder/<name>.SLDASM` + one `.SLDPRT` per part: components placed and fixed / floating as here, mates recreated as SolidWorks mates, preserving permitted movement. Save the assembly first. Keep the exported SLDASM and all SLDPRT files together; STEP does not transfer mates. Check the report for failed mates. Report: parts (each with the part report keys above), mates (`ok`, `note`), warnings. With `require_native=True` the known gaps of every part and mate are reported before SolidWorks starts (`solidworks.assembly_native_issues(asm.asm)`) |
| `asm.clearance(a, b=None)` | minimum distance [mm] between two components, or from `a` to the nearest other one: `{a, b, distance, point_a, point_b, overlap}` (distance 0 = touching or overlapping; `overlap` [mm3] > 0 = interference) |
| `asm.clearance_sweep(a, b=None, keyframes=[...], frames=12)` or `(..., poses=asm.motion(...))` | the smallest clearance over a motion study, with the frame where it happens and `distances` per frame (e.g. gripper vs. robot wrist over J5/J6 sweeps) |
| `asm.interference(min_volume=1e-3, ignore=[(a, b)])` | overlapping components at the current pose `[{a, b, volume}]`; touching faces (a shaft in a same-size bore) are not interference. Call it at each keyframe of a motion |
| `asm.render(png, view="iso")`, `asm.save(path)`, `asm.export(path, fmt=None)` | picture, `.bcasm` (paths stored relative to it), export. **STEP keeps the assembly structure** (one product per part file, placed instances, names and colours), so SolidWorks opens it as an assembly; IGES/STL/... get one compound |

Mate conventions: face-to-face coincident and distance mates are **anti-aligned** by default
(the faces oppose each other, touching or facing across the gap). `flip=True` gives aligned.
Axis relations (concentric, parallel axes) have no direction. Faces are matched after part
edits by position, kind and normal, so a face that moved (e.g. a thicker plate) keeps its mates.

## Drawings

A drawing sheet of a saved part (`.bcad`), assembly (`.bcasm`) or STEP file. Views are exact
hidden-line projections; dimensions measure the model and follow it when it changes.

```python
from bauyrcad.api import Drawing

dr = Drawing("projects/bracket/bracket.bcad", sheet="A3")   # or part.drawing() / asm.drawing()
front = dr.view("Front")                           # also "Top", "Left"/"Right", "Isometric"
dr.dim(front, (0, 0, 0), (120, 0, 0))              # 3D model points (or 2D view points)
dr.dim(front, (0, 0, 0), (0, 40, 0), offset=15)    # dimension line 15 mm from the outline
dr.diameter(front, d=12)                           # the 12 mm hole (or near=(x, y, z))
dr.auto_dimension(dr.view("Top"))                  # overall sizes + each hole diameter
dr.note("NOTES:\n1. BREAK SHARP EDGES", at=(30, 60))
dr.title(drawing_no="BC-001", material="S235JR", drawn_by="...")
dr.save("bracket.bcdrw")
dr.export("bracket.pdf"); dr.export("bracket.dxf"); dr.export("bracket.dwg")
dr.export("front_1to1.dxf", view=front)            # one view at 1:1 in model mm (laser/CNC)
dr.render("sheet.png")                             # look at it
```

| Call | Meaning |
|---|---|
| `Drawing(model, sheet="A3", portrait=False, projection="first", scale=None, standard=True, iso=True)` | new sheet; `standard=True` places Front, Top, Left (first angle, ISO) or Right (`projection="third"`, ANSI) aligned, plus an isometric; `scale` like `"1:2"` (default: largest standard scale that fits). Unsaved models are drawn from memory and embedded in the `.bcdrw` |
| `Drawing.open(path)` | open a `.bcdrw` |
| `dr.view(name)`, `dr.add_view(orientation, at=(x, y), scale=None, hidden=None)` | find / add a view: `front back top bottom left right isometric`; `at` in sheet mm from the bottom-left corner |
| `dr.projected(parent, side)` | projected view on `left/right/up/down` of a view (stays aligned) |
| `dr.move(view, x, y)` | move a view (projected children follow) |
| `dr.dim(view, p1, p2, orient=None, offset=None, text=None, at=None)` | linear: `orient` `h` / `v` / `a` (aligned); `text` may contain `<>` for the value, e.g. `"2x <>"`, `"⌀<> HUB"`; `offset` puts the line that far outside the outline on the side of the points, `at=` (a model or view point) puts it exactly there, either side or inside |
| `dr.diameter(view, near=None, d=None, at=None)` | diameter of a full circle (`near` = its centre) |
| `dr.radius(view, near=None, d=None, at=None)` | radius of a circle **or arc** (rounded ends, fillets); `near` = a point on the arc |
| `dr.auto_dimension(view)`, `dr.note(text, at)`, `dr.title(**fields)` | quick dimensions (overall sizes are exact over rounded ends), notes (`at` = **bottom-left** corner of the text block), title block (`title drawing_no material company drawn_by checked_by date revision finish`) |
| `dr.save(path)`, `dr.export(path, view=None)`, `dr.info()` | `.bcdrw`; `.pdf .svg .png .dxf .dwg`; JSON summary (views, dims with values) |

DXF files have layers (VISIBLE, HIDDEN, CENTER, DIM, ...), ISO linetypes and real DIMENSION
entities that AutoCAD can edit. DWG is written through the free ODA File Converter
(<https://www.opendesign.com/guestfiles/oda_file_converter>). Without it, `export(".dwg")` raises
a clear error; every AutoCAD opens DXF directly.

## Command line

```text
python -m bauyrcad info   part.bcad                     JSON: features, volume, faces, studies
python -m bauyrcad render part.bcad -o part.png [--view iso] [--study NAME] [--size 1400x1000]
python -m bauyrcad run    part.bcad [--study NAME] [--png vm.png] [--field F] [--vtu out.vtu]
python -m bauyrcad export part.bcad out.step [--format step242]
python -m bauyrcad drawing model.bcad out.pdf [out.dxf out.bcdrw ...] [--sheet A3] [--portrait]
                   [--third-angle] [--scale 1:2] [--no-iso] [--auto-dims] [--title T] [--view Front]
python -m bauyrcad solidworks part.bcad out.SLDPRT | assembly.bcasm out_folder [--solid]
python -m bauyrcad.core.sldprt file.SLDPRT --tree [--all] [--config N] [--json]
python -m bauyrcad.core.sldprt file.SLDASM --mates [--json]    (no SolidWorks needed)
python -m bauyrcad formats
python -m bauyrcad script model.py [args]
python -m bauyrcad [part.bcad]                           open the GUI
```

Use the command line from the source checkout (`.venv`). The packaged `BauyrCAD.exe` is a
windowed app without a console.
# Nastran simulation export

`study.export_bdf(path, progress=None)` meshes the current part study and writes an
MSC Nastran **SOL 101** BDF without running a solver. Returns a dictionary containing
`path`, `solution`, `nodes`, `elements`, `fixed_nodes`, `loaded_nodes`, `applied_force`,
`applied_moment` (about the global origin, N mm), and `warnings`.

```python
from bauyrcad.api import Part
p = Part("bar")
sk = p.sketch("front")
sk.rect((0, 0), (10, 10))
p.extrude(sk, 30)
st = p.study(material="Plain Carbon Steel")
st.fix(p.face("-z"))
st.face_force(p.face("+z"), (0, -20, 100))
st.mesh(size=5, order=2)
print(st.export_bdf("bar.bdf"))
```

Supported: 4/10-node tetrahedra, one isotropic elastic material, global/local mesh
settings, fixed faces, point forces, face forces and pressure. Surface loads are
exported as consistent equivalent nodal `FORCE` entries using the same preparation
as the internal solver. Quadratic connectivity is reordered from Gmsh to Nastran.
Units are mm, N, MPa, tonne, s; material density converts from kg/m³ to tonne/mm³.
No gravity, contact, friction, nonlinear materials, large deformation, assembly
study export, result import or automatic Nastran launch is provided by this method.
The deck explicitly restrains unused solid-node rotations and disables AUTOSPC
so insufficient translational restraints are not silently repaired.

GUI: Simulation > Export BDF, or type `BDF`, `NASTRAN`, `EXPORTBDF`. Select the active
study in the tree. Choose mesh settings and destination; export works from a frozen
snapshot in a background worker. Warnings are shown at completion. Existing files
are replaced only after successful deck generation.
# Portable assemblies: Pack and Go

`assembly.pack_and_go("project.zip")` packages the current assembly state and its
referenced **saved** `.bcad`, STEP, IGES or BREP part files. Returns `path`,
`components`, `parts` (unique file count) and `assembly` (archive entry name).
Extract the whole ZIP and open `assembly.bcasm`; keep its `parts` folder beside it.
The package preserves poses, component IDs, fixed/floating state and mates without
changing the open assembly's path or references. Repeated parts share one file;
different files with identical names remain separate. Missing source files fail
the operation without replacing an existing ZIP. Saved BauyrCAD parts include
their embedded imported geometry.

Save edits in separately open part windows first. This packages directly referenced
parts, not scripts, drawings, native SolidWorks exports or unsupported nested
assembly documents. The current assembly itself does not need to be saved first.
GUI: assembly File > Pack and Go, toolbar button, or `PACK` / `PACKANDGO`.

# Assembly BOM and drawing balloons

```python
part.properties(part_number="PL-001", description="Mounting plate")
part.save("plate.bcad")
# After adding saved parts to the assembly:
assembly.save("fixture.bcasm")
print(assembly.bom())                         # current in-memory assembly, flat rows
dr = Drawing(assembly)
table = dr.bom(at=(225, 275))                 # top-left corner, sheet mm
table.edit(1, description="Drawing-specific description")
dr.balloon("Front", assembly.components[0], anchor=(10, 10, 0), at=(50, 200))
# anchor: component-local 3D mm; at: bubble centre in sheet mm
dr.save("fixture.bcdrw")
dr.export("fixture.pdf")
dr.export("fixture.dxf")
dr.dr.reload()                               # saved geometry, BOM and leader poses
dr.refresh_bom()                             # saved quantities/metadata/poses only
```

Part GUI: **File > Part Properties**. Save part edits before refreshing drawings.
Older `.bcad` files and imported geometry use the source filename stem as the
displayed part number, with an empty description. A display number is **not** an
identity: grouping uses the canonical full source path (case-normalized on Windows).
Two files named `plate.bcad`, or two different files with the same part number,
remain separate rows. Copied files are separate identities; relocating/replacing
a source is treated as an identity change, not automatically repaired.

Drawing GUI: **Insert > Bill of Materials**, or `BOM`. Drag the table to position
it; double-click to edit part number/description overrides for this drawing only.
Reset in the dialog restores source text. Item and quantity remain assembly-driven.
Select a view, choose **Balloon** (`BAL`), select a component from the list, click
the leader tip, then click the bubble centre. Drag the bubble to move it. Delete
and recreate a balloon to retarget it. `REFRESHBOM` / Reload Model updates the
saved assembly and marks the drawing dirty when annotation data changes; these
changes support undo/redo. Opening a drawing also refreshes its BOM from the model.

Item numbers are retained in the drawing for removed sources, never reassigned
to a different source. Reintroduced sources recover their number. Component
instance IDs persist in `.bcasm`, including through undo and Pack and Go. Reusing
a deleted component's visible ID cannot redirect its balloon to a replacement.
Legacy assemblies use stable legacy instance tokens until saved by this version.
Missing/replaced components and deleted views show `? UNRESOLVED`; missing source
rows carry `!` and `[UNRESOLVED]`. Removing a BOM leaves its balloons unresolved.

PDF, SVG, PNG and whole-sheet DXF contain the same table and balloon primitives.
DXF uses editable lines, circles and text, not associative AutoCAD table/leader
entities. DWG uses the existing ODA conversion of that DXF. Single-view 1:1
DXF/DWG intentionally exports view geometry/dimensions only, omitting sheet
annotations (BOM, balloons and notes); use whole-sheet export to include them.

Current limits: one flat BOM per sheet, fixed columns, no automatic pagination
or view avoidance. A saved `.bcasm` is required; an embedded compound has no
component identities. API anchors follow component-local coordinates when poses
change, not edited topology. GUI leader picks are projected onto a plane through
the component origin, not snapped to an actual 3D face; after rotations, inspect
and replace the balloon if necessary. Save source edits before refresh.

# Revolve axes across sketches

```python
part = Part("ring")
axis_sketch = part.sketch("front")
axis = axis_sketch.line((0, -10), (0, 40))  # ordinary line; construction is optional
profile = part.sketch("front")
profile.rect((10, 0), (20, 30))
part.revolve(profile, axis=axis)            # same axis= argument for revolve_cut
part.save("ring.bcad")
```

The axis reference stores both the sketch and line identity. Moving its endpoints
or changing its sketch plane updates the revolve on regeneration. The line must
be nonzero, lie in the profile's plane and exist before the revolve feature in the
tree; the profile must not cross it. A missing/suppressed axis fails explicitly.
Ordinary profile boundary lines remain part of the profile. When a separate
ordinary axis in that same sketch prevents closed-profile construction, Revolve
temporarily treats only that line as construction; the saved sketch is unchanged.
Other extrudes/features of the sketch retain their usual closed-contour rules.

GUI: choose the profile sketch, then select **Sketch name — Line/Centerline number**
under Axis, or press **Pick axis line in view** and click the line. Earlier sketches,
including consumed sketches, are shown while picking; the selected axis is red.
Axis picking does not change contour selection. To repair a missing reference,
edit the revolve and choose a replacement from the same list or picker.

**Select Other in the viewport:** outside a command, right-click and choose
Select Other to list faces under the pointer from front to back. Hover a candidate
to see its orange outline through the model; click it to select. In a feature
command, right-click opens the list directly and limits it to the active selection
box's accepted faces/edges/vertices. In Measure, it offers all three entity kinds.
In the assembly Mate panel, right-click offers faces with component names, including
occluded components. Escape dismisses the list without changing selection. Objects
explicitly hidden from the viewport and result-plot geometry are excluded.
This is a GUI selection workflow; scripting references and saved-file formats are
unchanged. Display-mesh hits are converted back to model entity identifiers.

Straight **model edges** and earlier **reference-axis features** are also accepted.
Use the Pick filter to restrict viewport candidates to sketch lines, model edges
or reference axes. When several overlap, Select Other lists them: select a row to
preview it, then click Use selected reference (or double-click the row).
The same coplanarity and profile-crossing checks apply to every axis type.

The API accepts `axis=part.axis(point=(0, 0, 0), direction=(0, 1, 0))`, a straight
edge returned by `edges_where()`, an `EdgeRef`, or an edge index. Standard axes
such as `axis="y"` and explicit `(point, direction)` pairs are also accepted.
Reference axes must belong to this part. Edge references use the existing geometric
matching system; unresolved references require reselection. SolidWorks native
export of model-edge/reference-axis revolves currently falls back to an exact solid
with an explicit warning.

SolidWorks export resolves the chosen axis sketch, but cases rejected by SolidWorks
fall back to a solid body with an explicit export warning. The editable BauyrCAD
feature tree and sketch references remain in the `.bcad` file.

# Mouse rotation

Hold the mouse-wheel button (middle button) and drag to orbit around the visible
model point under the cursor. If the cursor is outside the model or in a hole,
the nearest projected surface point is used. The point is chosen once at press
and remains fixed throughout the drag, without recentring or changing zoom.
Release and start another drag to choose a new pivot. Empty scenes use the current
camera focal point. Reference planes and selection overlays are ignored; visible
parts, transformed assembly components, results and previews participate.
Ctrl/Shift + middle remains pan, wheel scrolling remains cursor-centred zoom,
and left-drag remains selection/editing.

# Sketch geometric relations

In an active sketch, click **Add Relations** on the Sketch toolbar (or type
`ADDRELATIONS` / `AR`). Click each point or line to add it to the selection;
click it again to remove it. Click empty space to clear the selection. Choose
one of the applicable relation buttons in the left panel. This mode does not
move geometry while selecting. Normal Select mode also supports Ctrl-click.

- Two or more points: Horizontal or Vertical; two points also offer Coincident.
- One or more lines: Horizontal or Vertical.
- Two lines: Parallel, Perpendicular, Collinear or Equal; multiple lines also
  support Parallel, Collinear and Equal.
- Point and line: Coincident or Midpoint.
- Line and arc/circle: Tangent.
- Two arcs/circles: Concentric, Equal or Tangent.
- Two points and a line: Symmetric about the line.
- One point or entity: Fix.

Relations persist in the sketch, participate in undo/redo, and constrain later
edits. Conflicting additions are rejected with a message. Use **Display/Delete
Relations** to inspect or remove existing relations. Horizontal and Vertical are
relative to the sketch coordinates.

The scripting API uses `sketch.relate(kind, *items)`; items are entities or point
references such as `("p", line.p1)`. For example:

```python
sketch.relate("horizontal", ("p", first.p2), ("p", second.p1))
sketch.relate("collinear", first, second, third)
```

Equal relates lines (length) or arcs and circles (radius), as in SolidWorks; an Equal
between a line and an arc or circle raises `ApiError`. A point Coincident with a spline
stays on the spline.


## Named parameters and equations

Use **Edit > Parameters and Equations** in a part to define reusable values and
link existing dimensions. Check model validates the complete proposed document;
OK applies one undoable change. Rename selected updates dependent expressions.
Remove binding restores ordinary editing at the last evaluated value. Finish an
active modeling command before opening the table. Linked feature controls and sketch dimensions can also be edited directly as
described below. Instant3D skips linked depths.

```python
part = Part()
sk = part.sketch()
diameter = sk.dim(sk.circle((0, 0), d=20), 20)
boss = part.extrude(sk, 5)
part.parameter("diameter", "20 mm")
part.parameter("thickness", "diameter / 4")
part.bind_parameter(sk, f"dimension:{diameter.id}", "diameter")
part.bind_parameter(boss, "depth", "thickness")
part.parameter("diameter", "30 mm")  # circle becomes 30 mm; depth becomes 7.5 mm
part.rename_parameter("diameter", "outside_diameter")
part.bind_parameter(boss, "depth", None)  # unlink, retaining 7.5 mm
```

`parameter(name, expression, kind="length")` returns the evaluated value in mm,
degrees or scalar units. Types are `length`, `angle`, `scalar`. Arithmetic supports
`+ - * /`, parentheses, `pi`, named values and `mm`, `cm`, `m`, `inch`, `deg`, `rad`.
Pure numeric expressions default to the declared units. Mixed expressions require
explicit units (`thickness + 2 mm`); a scalar becomes a length with `instances * mm`.
Calls, indexing, attributes and exponentiation are rejected. References may point
forward in a table proposal; cycles, unknown names and incompatible units fail
without changing the live model. At most 200 parameters, 1000 characters and 200
syntax nodes per expression are accepted. Sketch angles remain radians internally;
feature angles and API parameter return values use degrees.

Bindings support these fields:

| Feature | Fields |
|---|---|
| Sketch | `dimension:<constraint id>` for driving distance, horizontal/vertical distance, point-line distance, diameter, radius and angle |
| Extrude | `depth` |
| Revolve | `angle` |
| Fillet | `radius`, `radius2` when present |
| Chamfer | `distance`, `distance2` when present, `angle` |
| Shell | `thickness` |
| Draft | `angle` |
| Linear pattern | `spacing`, `spacing2`, `count`, `count2` |
| Circular pattern | `angle`, `count` |
| Rib | `thickness` |
| Reference plane | `distance` in offset mode (signed or zero); `angle` in angle mode |
| Hole | explicit `d`; blind `depth`; explicit `cbore_d`, `cbore_depth` for counterbores; explicit `csk_d` and `csk_angle` for countersinks |
| Helix | explicit `diameter` without a circle sketch; the selected pair of `pitch`, `height`, `revolutions`; `taper`, `start_angle` |

Helix revolutions can be fractional and must be positive. Hole, rib and helix
lengths must be positive. Only active fields appear in the parameter table;
changing modes cannot silently discard or ignore their equations. Switch back
and unlink an equation before changing to a mode that does not use its field.
For standard hole diameters, enable **Override the standard dimensions** first
(or supply explicit dimensions through the API). Countersink angle overrides
also apply when the hole retains its standard screw size.

Pattern counts must be integers from 1 to 10000. Geometry and sketch validation
reject unusable sizes. Deleting a feature removes its bindings; deleting a bound
sketch dimension removes that binding, and undo restores it. Parameters without
bindings may remain as reusable values. `part.doc.configure_parameters(definitions,
bindings)` validates a complete batch, useful when changing several dependent values.

Bindings persist in `.bcad` format 2, which older applications reject explicitly.
Existing numeric files remain format 1. STEP uses the evaluated geometry/dimensions.
BauyrCAD equations are not transferred as native SolidWorks global variables yet, so a
part with parameters or equation links is refused by the strict (GUI) SolidWorks export
instead of arriving with plain values; scripts without `require_native` export the
evaluated values. Assemblies, drawings, configurations and other feature
fields do not yet accept these bindings.

For the supported feature fields in the table above, focus the numeric control
and press `=`, or right-click and choose **Edit equation / value**. Enter
`=thickness / 3` to link it, or a number/calculator expression to unlink it. Linked
controls show an `=` prefix and their evaluated value; double-click also reopens
their equation. The normal spinbox stays read-only while linked. Numeric input
without a link keeps its ordinary behavior. Define new names in the parameter
table before starting the feature command.

Feature previews use the exact equation value, independent of display rounding.
Length/angle units and integer pattern counts are checked before accepting an
expression; new inputs also respect the control's numeric range. Links remain
local to the active command until OK validates the full model. Cancel discards
them; failed geometry leaves the live document and undo history untouched. New
features and existing features support the same workflow. If a mode removes an
optional field that has a link, unlink it before accepting that mode change.
Document undo/redo restores geometry and equations together. The scripting
interface remains `part.bind_parameter(feature, "depth", "thickness / 3")`.

In the sketch dimension popup, enter `=thickness / 2` to save an equation referring
to existing named parameters. A linked dimension reopens with its expression.
Enter a plain number or a numeric calculator expression such as `sqrt(900)` to
unlink it. Without `=`, the existing calculator syntax and degree convention are
unchanged. With `=`, the shared length/angle unit rules above apply. Invalid input
keeps the popup open and changes neither geometry nor sketch history.

Equation changes participate in sketch-local undo/redo and remain staged until
Exit Sketch validates the complete model. Cancel Sketch discards the staged links;
Escape in the popup discards only that unaccepted input. This also works for a
new sketch before it has a saved feature ID. Define new parameter names in the
central table before starting the sketch. The scripting equivalent remains
`part.bind_parameter(sketch, f"dimension:{constraint.id}", "thickness / 2")`.


### Inspecting and repairing geometry references

`part.reference_status(feature)` (or a feature ID) inspects stored face/edge
references against the body immediately before that feature, without changing
the live document. Each row contains `path` (keys/list positions in feature
parameters), `kind`, `status`, `index`, and `candidates`. Status is `exact`,
`moved`, `ambiguous`, or `missing`; unresolved indices are `None`. Candidates
contain historical-body indices, normalized `score` (lower is closer), and an
`exact` flag. These indices are not indices of the final part after the feature.
This currently covers geometric signatures, not missing sketch/feature IDs.

Matching checks compatible geometry before accepting an exact signature. Duplicate
matches and close competing candidates stay unresolved. Moved matches must be
near enough (normalized score at most 1) and clearly better than alternatives.
Old face references without geometry type only accept unique exact signatures.
Matching remains geometric rather than persistent topology provenance.

In the applied-feature editor (fillet, shell, Hole Wizard, reference geometry,
etc.), unresolved references remain as **Missing** or **Ambiguous** rows. Select
that row and pick a replacement of the same kind; normal viewport picking and
Select Other both work. Remove/Clear explicitly discard selections. Preview and
OK remain blocked until unresolved rows are repaired or explicitly removed.
Cancel discards replacements; accepted edits support document undo/redo and
save/reopen. Dedicated Revolve/sketch support editors and simulation repair
panels are separate follow-up work.


For an unresolved row in an applied-feature selection box, **Preview replacements**
opens compatible candidates in the same order as `reference_status()` candidate
scores. Hover or navigate the menu with the keyboard to outline a candidate,
including hidden edges/faces. Click (or activate the menu item) to stage that
replacement. Nearby/distant labels describe geometric similarity, not guaranteed
identity; distant candidates require the same explicit selection. Already-selected
geometry is omitted. An empty list directs you to pick on the model. Escape,
changing the selected row, or cancelling the command closes the preview without
accepting a candidate. The existing feature OK/Cancel and undo behavior applies.


Revolve editing uses the same edge matcher as `part.reference_status(feature)`
to recognize saved axes after upstream edits. A uniquely resolved edge is shown
by its current model-edge entry; accepting the command saves the refreshed
signature, while Cancel preserves the original. Missing and ambiguous axes keep
their saved reference and are labelled in the axis list. Select a replacement
from the list or with **Pick axis line in view**. OK validates the axis before
creating an undo checkpoint, so an invalid axis keeps the command open.


### Complete editable SolidWorks export

File > Export to SolidWorks now requires native feature-history export automatically.
The part format is `.SLDPRT`; assemblies use `.SLDASM` and referenced `.SLDPRT` files.
Known missing translators (including suppressed features/reference items) stop
with the feature names. Runtime feature/sketch warnings, unsuccessful mates or
changed assembly positions also stop before saving the final requested document.
Assembly part files already completed may remain if a later mate fails; they are
not a successfully exported assembly. Existing destination files are not removed
on failure. Universal native feature/mode coverage is still in progress; use
normal STEP export when only the final geometry is needed.

Scripts can request this check with
`part.export_solidworks(path, require_native=True)` or
`asm.export_solidworks(folder, require_native=True)`. Legacy script defaults still
allow reported geometry fallback. `native_export_issues(document)` in
`bauyrcad.solidworks` lists the known gaps WITHOUT SolidWorks: unsupported feature
types (`solidworks.unsupported_types()`), unsupported modes of supported types (each
handler's pre-check, registered with `@solidworks_features.precheck("type")` and run
again by the handler itself), suppressed features, features in an error state,
parameters/equations, later or multi-solid imports, sketch text sharing a sketch with
other curves (or multi-line, or with '<'), sketches whose face reference is lost,
sketch relations SolidWorks can't hold (an Equal between a line and an arc, a relation
to a point no entity uses, an angle between parallel lines, a relation that does not hold),
revolves about a model or global axis. The GUI part and assembly exports and the strict one-call exports run it
before starting SolidWorks; an empty list is not a guarantee that every step will match,
because each result is still validated in SolidWorks.

How the export works (for every feature family):

* It works on a private copy of the Document, regenerated once; the document being
  edited is never touched by the export thread.
* Each step is rebuilt by its handler (`solidworks_features.HANDLERS`); attempt 0 is the
  predicted definition (e.g. the Reverse Direction derived from BauyrCAD's direction,
  the auto-flip of a cut and the SolidWorks sketch normal), later attempts are
  alternatives. The first attempt whose change of volume and of first moment matches
  BauyrCAD's within a tolerance relative to the step's own volume change is kept
  (exact steps 1e-5, spline-based 1e-4, sweeps 1e-3, lofts/boundaries 1e-2; the
  difference is a report note). `report.attempts` says which attempt was kept.
* Entities SolidWorks needs but BauyrCAD does not store as features (an axis along X, a
  plane for an explicit sketch plane, coordinate points) become hidden helpers named
  after the step (`Sketch3 Plane`, `LPattern1 Axis` + `LPattern1 Axis Sketch`) and are
  listed in `report.helpers`. Features SolidWorks names itself (Hole Wizard's sketches)
  are named after their step, so they never take a later step's name; a name SolidWorks
  refuses is a blocking warning.
* Sketches go on what they reference in BauyrCAD: a standard plane, the reference plane
  feature itself, or the model face (found through a point inside BauyrCAD's face, so a
  face away from the origin or lying on another plane still parents the sketch to its
  feature). An explicit `SketchPlane` uses a standard / earlier plane in the same place,
  else a hidden `<sketch> Plane`. SolidWorks' sketch axes are turned onto BauyrCAD's when
  Horizontal / Vertical relations, h/v dimensions or text depend on them, and references
  to the origin of an explicit plane whose origin is not the model origin's projection go
  to a hidden helper point `<sketch> Points`. Lines, arcs, circles, splines (through
  BauyrCAD's own points: editable spline points), Point entities and construction flags
  arrive as the same SolidWorks entities, shared points as one SolidWorks point, checked
  before the sketch is closed. Empty and unused sketches are kept. Sketch text becomes
  editable Sketch Text (text, font, bold / italic, height; an angle runs along a
  construction line); the letters' volume may differ by a few % (a note), their centroid
  must agree within 10 % of the text height. Every relation becomes the SolidWorks relation of the same
  kind, each checked in SolidWorks (a refused one is a blocking warning naming it);
  every dimension is read back (kind, value, driving / driven, text position), driven
  BauyrCAD dimensions stay driven, and the SolidWorks sketch must be exactly as defined
  as BauyrCAD's.
* Before saving, the part is force-rebuilt; What's Wrong items and feature error codes
  are blocking warnings. The final volume and centre of mass are compared too.
* New documents get MMGS units (mm, g, s; degrees) whatever the user's template says;
  SolidWorks preferences the export changes (input dimension value, sketch inference
  and automatic relations, update component names) are put back afterwards.

An imported solid at the **start** of a part's history can now be exported with
`require_native=True`: STEP, IGES and BREP bases become a SolidWorks Imported
feature, followed by the supported native operations added in BauyrCAD. Original
creation steps absent from the source file cannot be recovered. Imports inserted
later in the history are not supported by the complete-history translator yet.
See [native export coverage](SOLIDWORKS_EXPORT_COVERAGE.md).

**Checking a saved file without SolidWorks** (`bauyrcad.core.sldprt`, format notes in
[SLDPRT_FORMAT.md](SLDPRT_FORMAT.md) section 7):

```python
from bauyrcad.core import sldprt
rep = part.export_solidworks("out/plate.SLDPRT")
sldprt.tree_mismatches(rep["path"], rep["features"])     # [] = every step is native, in order
for f in sldprt.feature_tree(rep["path"]):               # FeatureManager tree of the file
    print(f.name, f.type_name, f.suppressed, f.dims)     # 'Boss-Extrude1' 'Extrusion' False {'D1': '20'}
sldprt.mates("out/hinge.SLDASM")                         # name, type, components, alignment, values
```

`tree_mismatches` accepts `(name, GetTypeName2)` or `(name, type, suppressed)` items and
reports missing or reordered steps, wrong types, a step name that belongs to a sketch
inside another feature (Hole Wizard's), and any body without history (`BaseBody`).
