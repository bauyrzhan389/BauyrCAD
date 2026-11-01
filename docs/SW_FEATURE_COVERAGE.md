# Native offline SolidWorks coverage

User mandate (2026-11-01): cover every BauyrCAD modelling and assembly method as an
editable SolidWorks 2024 tree, then add missing SolidWorks capabilities incrementally.
Implement existing operations first; adjust semantics only where validation shows a real
mismatch. Preserve existing documents, units and design intent. This is a coverage backlog,
not a claim that every row exports today. A matching final solid alone is insufficient.

Each family needs saved reference files, exact archive round-trips, offline construction,
exact feature identities/order, clean SW rebuild, geometry/mass/bounds comparison,
parameter or parent-reference edits, and save/reopen. Full regression and frozen-app
self-test must pass before delivery. SolidWorks creates reference files and judges output;
the production writer never starts it. Targeted static decompilation is authorized for research; never touch a user's SW session.

## Part feature families

Origin/clockwise/CLI milestone (completed 2026-11-02): **1286 tests passed, no skips (42:10)**.
Windows app self-test passed; frozen origin/clockwise archives match source exports.
Expected export errors return exit 2 and preserve output; ZIP integrity verified.
Variable-thickness Shell remains research-only: four references and nine offline prototypes
pass geometry, thickness/parent edits and save/reopen, but no production exporter is enabled.

Shared-profile/constraint milestone: **1237 tests passed, no skips (35:55)**. Windows app
and ZIP rebuilt, frozen self-test/CRC/executable identity/source-output equality verified.
Reserved native names and case-only duplicates are rejected before replacing a destination.

The first column is the persisted feature type. An inventory test checks this list against
the actual registry so a newly added feature cannot silently disappear from the audit.

| Type | Offline state | Variants / remaining gates |
|---|---|---|
| `sketch` | Partial | Front/Top/Right; polygonal-base side/end-cap face supports. Offset/coincident/midpoint reference-plane supports; shared profiles and earlier consumed sketches. Custom planes, standalone sketches, selected contours and full relations/dimensions remain. See sketch inventory below. |
| `extrude` | Partial | Standard-plane boss/cut: blind/reverse/midplane, through-all and both directions; shared profiles, supported reference planes; face bosses/cuts on blind/reverse/mid-plane polygonal bases. Need full face-mode coverage, contours, arbitrary histories, multibody. |
| `revolve` | Partial | Boss/cut, full/partial/reverse/midplane, own-sketch construction axes, shared profiles and supported reference planes. Need reference/model-edge/external-sketch axes and face planes. |
| `import_step` | Solid-only | STEP/IGES/BREP are legitimately Imported features; preserve that feature and all following native operations, multiple imports and body identities. |
| `fillet` | Partial | Constant radius, selected edges/faces, single forward polygonal extrusion. Need variable radius, curved/holed/reversed bases, later histories, tangent-chain identity. |
| `chamfer` | Partial | Equal-distance, same base limits as fillet. Need two distances, distance-angle, side flip, later histories. |
| `shell` | Pending | Inward/outward, closed hollow body, one/multiple removed faces, thickness edits, later operations. |
| `draft` | Pending | Neutral standard/reference plane or face, angle/reverse, multiple faces, parent edits. |
| `hole` | Pending | Simple/clearance, tapped, counterbore, countersink; standard/custom sizes and fits; blind/through/reverse, drill point, thread depth, multiple positions, face/plane support. |
| `linear_pattern` | Pending | Feature/body patterns, one/two directions, vectors/edge/face references, spacing/count/reverse, skipped instances, nested patterns. |
| `circular_pattern` | Pending | Feature/body patterns, global/reference/face/edge/point-direction axes, full/partial/step angles, reverse, skips, nested patterns. |
| `mirror` | Pending | Feature/body mirror, standard/reference plane or face, topology after parent edits. |
| `sweep` | Pending | Boss/cut, sketch profile/circle/tube, sketch/helix/3D-curve path, contours and path edits. |
| `loft` | Pending | Boss/cut, profile sequence, point ends, smooth/ruled/closed, section edits. |
| `boundary` | Pending | Actual Boundary boss/cut identity and section semantics; do not substitute a Loft merely because geometry agrees. |
| `rib` | Pending | Parallel/normal, thickness, both/one side, flip, open sketch attachment and body intersections. |
| `wrap` | Pending | Emboss/deboss on cylinder, contours/text, depth and face references; preserve Wrap identity. |
| `intersect` | Pending | Planes/faces, excluded material regions and filled voids; preserve region selection. |
| `ref_plane` | Partial | Offset/coincident/midpoint from standard or preceding reference planes; chained dependencies and parameter edits. Angle, three points, normal-to-axis, flip and face parents pending. |
| `ref_axis` | Pending | Two points, cylindrical/conical face, straight edge, two planes, point+direction. |
| `ref_point` | Pending | Coordinates, arc center, face center; dependency updates. |
| `helix` | Pending | Pitch/revolutions, height/pitch, height/revolutions; handedness, taper, start angle, reverse, circle sketch vs direct axis. |
| `curve` | Pending | Open/closed 3D interpolation spline and polyline, point edits, use as sweep path. |

## Sketch relation inventory

| Type | Offline state / required check |
|---|---|
| `horizontal` | Single-line and point-pair supported. |
| `vertical` | Single-line and point-pair supported. |
| `coincident` | Shared profile endpoints, point-on-circle and point-on-line supported; origin anchors on standard/supported reference planes supported; model-face origin projection and other external references pending. |
| `distance` | Driving line length supported; arbitrary point/entity distances pending. |
| `diameter` | Driving circle diameter supported; additional entity forms pending. |
| `arc_internal` | Arc geometry written; internal-radius semantics audit pending. |
| `midpoint` | Point-line supported. |
| `parallel` | Line-line supported. |
| `perpendicular` | Line-line supported. |
| `collinear` | Line-line supported. |
| `equal` | Line lengths, arc radii and circle equality supported. |
| `concentric` | Circle-circle supported. |
| `tangent` | Arc-line supported, including straight slots. |
| `fix` | Points, lines, circles, arcs and stored point flags supported. Lines/arcs include endpoint locks; converted-entity workflows remain to audit. |
| `symmetric` | Point-point about a line supported; entity symmetry remains. |
| `hdistance` | Pending. |
| `vdistance` | Pending. |
| `point_line_distance` | Pending. |
| `radius` | Driving circle/arc radii supported; edit/reopen gates. |
| `angle` | Pending for sketch lines/arcs. |

Also check driving/driven state, dimension names and placements, equations/parameter links,
under/fully defined behavior and editing after reopen. Do not bake a constraint into
coordinates and call it preserved design intent.

## Sketch construction and editing routes

Validated: straight slots, center/three-point rectangles, parallelograms, regular polygons,
concentric circles, center slots, three-point centered rectangles, circumscribed polygons,
perimeter circles, center/three-point arcs, sketch fillets/chamfers, construction
lines/circles and explicit sketch points.

Inventory the result of every GUI builder as well as the scripting API: line/polyline,
centerline/midpoint line, corner/center/three-point/three-point-center rectangle,
parallelogram, regular polygon (inscribed/circumscribed), center/perimeter circle,
center/three-point/tangent arc, straight/center slot, point, spline and text.

Editing routes: sketch fillet/chamfer, trim/extend, offset, mirror, linear/circular pattern,
move/rotate/scale, convert model edges (fixed and unfixed), construction toggles. Test the
resulting entities AND generated relations. These are often ordinary sketch records,
not separate SolidWorks features, but still require semantic coverage.

## Assembly mate inventory

Offline assembly containers currently preserve component placement/hierarchy with solid
parts. Editable component histories and native mate transfer require separate gates.

| Type | Offline state / required check |
|---|---|
| `coincident` | Pending: plane/plane, plane/axis and aligned/opposed states. |
| `concentric` | Pending: axis/axis, alignment and free rotation. |
| `parallel` | Pending: plane/plane, axis/axis, plane/axis. |
| `perpendicular` | Pending: plane/plane, axis/axis, plane/axis. |
| `distance` | Pending: value, side, alignment and edit/drive. |
| `angle` | Pending: signed angle, reference direction and edit/drive. |
| `lock` | Pending: relative translation/rotation and fixed/free component state. |

Assembly cases: part vs nested subassembly, repeated instances and shared source files,
component names, poses, fixed/free/suppressed states, mate suppression, save/reopen,
external path relocation, native part parameter changes, mate-reference recovery and DOF.
The current 46-component-per-level container limit must be explicit until broadened.
BOM, colours and names are metadata checks; motion/clearance are analysis outcomes and
must not be presented as native features unless equivalent records are implemented.

## Cross-cutting gates and sequence

1. Complete common sketch supports and face extrusion/cut modes; broaden relations and
   dimensions needed by all builders. Add shared/standalone sketch and reference geometry.
2. Generalize topology references across feature histories; broaden fillet/chamfer.
3. Hole Wizard, patterns/mirror, shell/draft, sweep/loft/boundary, rib/wrap/intersect.
4. Imported/native mixed histories, multibody semantics, suppression and equations.
5. Native component part histories inside assemblies, then native mate families and edits.
6. Add missing SolidWorks modelling options, one validated family at a time.

Every family also needs renamed/reordered inputs, meaningful parameter variations,
non-origin placement, different standard orientations, and interactions with earlier/later
features. Test positive intent plus strict refusal that preserves an existing destination.
Keep unsupported capabilities explicit; a successful fallback is not a native pass.

Delivery baseline: side-face support commit `871b722`, 833 tests passed, rebuilt Windows
app/self-test and actual Downloads model checked. End-cap/face-mode expansion: 995 full-suite tests pass with no skips; rebuilt desktop and ZIP verified.

Sketch-intent milestone: 1051 full-suite tests passed with no skips; desktop self-test, ZIP
integrity and frozen/source archive equality verified.

Reference-plane milestone: 1148 full-suite tests passed with no skips (33:43), including
33 new SolidWorks plane dependency/edit/reopen cases and expanded sketch builders.
Desktop self-test, ZIP integrity and frozen/source midpoint archive equality verified.
