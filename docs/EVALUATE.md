# Inspecting a model in BauyrCAD

The Evaluate tools inspect parts and assemblies without changing their shape. In a part,
use the **Evaluate** tab or **Tools > Evaluate**. Assemblies have an **Evaluate** menu and
toolbar. Commands can also be entered in the command line.

## Look inside

Open **View > Section View** (`SECTION` or `SEC`) or use its viewport-toolbar button.
The controls appear in the left **PropertyManager**. Choose Front, Top or Right, then move
the slider or enter a position in mm. **Flip direction** keeps the other side. Section
caps fill solid material while leaving holes open. Turn the checked Section View action
off, choose **Close section view**, or press Escape while editing the section to restore the full display.

Drag the **blue arrow** to translate the cut along its current normal. Drag the **red/green
rings**, or enter **X rotation / Y rotation**, to tilt it. Rotation is relative to the selected
plane, around a model-centred pivot. The offset slider follows the current normal. Middle-drag
still orbits the model. **Reset rotation** returns to the selected plane's orientation.

**Keep section** returns to the feature tree while retaining the cut. Click the **PropertyManager**
tab to edit it again. Hide the handles using their checkbox; viewport image export hides them
automatically. Opening a different model clears the section. A model reload retains the current
world-space viewing cut.

The controls follow the offset/rotation workflow described in the
[SolidWorks Section View documentation](https://help.solidworks.com/2024/english/solidworks/sldworks/HIDD_DVE_DYNAMIC_SECTION.htm),
with BauyrCAD's own implementation and icons.

For an angled cut, choose a built reference plane or use **Pick planar face**. The offset
is measured along that plane's positive normal. A picked/reference plane is captured in
world coordinates; reselect it after geometry or assembly positions change.

Section View is a display cut. It does not change saved bodies, exports, mass or analysis.
Select original retained faces, edges and vertices for measurements; the caps themselves
are not model faces. Sectioning pauses while editing sketches or viewing analysis results.

## Measure sizes and distances

Open **Measure** (`MEA`, `DIST` or `EVALUATE`) and select faces, edges, vertices or points
on surfaces. Click the same entity again, or double-click its list entry, to remove it.
The selection filter helps distinguish
an edge from a nearby face. Two selections give distance and, where applicable, angle;
three points measure an angle at the second point.

Choose minimum distance, centre/axis distance or maximum distance for supported point/
full-circle selections. Units, precision and XYZ differences are display choices. Copy
the report or keep a snapshot in the session history. History entries do not update after
the model changes.

**Bounding Box** (`BBOX`) gives overall world-axis dimensions, not an optimally rotated
minimum box. In assemblies it measures visible components. Sectioning does not reduce
the exact geometry used for a component's measurements.

## Inspect assembly fits

**Minimum Clearance** (`CLEARANCE` or `GAP`) checks a component against a chosen partner
or its nearest neighbour. Enter the required gap and calculate. The report distinguishes
a positive gap, contact and overlap. Transparent highlighting reveals the closest points.
Use **Interference** for an all-pairs overlap check; one nearest neighbour is not a complete
assembly interference report.

Checks use the current poses, not the swept motion path. Moving/rebuilding the assembly
clears old results. Missing geometry or failed kernel operations produce an incomplete
check. Cancellation takes effect between geometry operations.

Right-click a component for **Hide / Show**, **Isolate**, or **Zoom to Selected**. Exiting
Isolate restores the previous hidden set; **Show All Components** restores everything.
Hidden components are grey and italic in the tree. Visibility is temporary: it does not
change mates, exports, mass properties, interference or clearance checks.

## Inspect mass and inertia

**Mass Properties** (`MASSPROP` or `MASS`) reports volume, surface area, mass, centre of
gravity, inertia tensors and principal moments/axes. Assign physical materials or custom
densities to obtain mass and inertia. These assignments are saved but remain independent
of FEA study materials; an assembly override does not change its source part file.

Choose length/mass units and significant digits for the report. The numerical API retains
mm, kg and kg mm². **Show principal axes** draws I1, I2 and I3 through the centre of gravity
in increasing-moment order. Their displayed line lengths do not represent inertia magnitudes.
Equal moments have non-unique axis directions.

Assembly totals sum component instances, including hidden components and overlaps; they
are not the mass of a Boolean union. Unassigned densities remain explicit.

## Check solid geometry

**Check Geometry** (`CHECK`) reports kernel validity, closed solid suitability, topology
counts and solid volume. A valid surface is not necessarily a closed solid. Missing
components, failed features and kernel errors are reported as incomplete. All assembly
components are checked, including hidden ones.

This check does not repair geometry or assess interference, wall thickness or manufacturability.

## Share an inspection image

Use **File / View > Save View Image** (`VIEWIMAGE`) for a PNG at 1×, 2× or 3× viewport
resolution. Choose white, current-view or transparent background. Visible section caps and
measurement labels are included; toolbars and floating controls are excluded.

**Copy View Image** (`COPYVIEW`) copies the current viewport at 1× resolution. To change
the working background, use **View > Viewport Background**. White, Neutral grey and Classic
blue are shared preferences for open part/assembly windows.

**View > Display Style** offers Shaded with Edges and Shaded. It controls model outlines,
not the geometry or selection topology. Analysis-result mesh and undeformed outlines keep
their own controls. The chosen style is included in viewport images.
