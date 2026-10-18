# BauyrCAD

**Parametric CAD modelling and finite element analysis in one desktop application.**

Sketch → extrude → cut → fix → load → mesh → solve → review stresses, all in one window and
one project file, with no export/import step between CAD and FEA.

| Modelling | Simulation |
|---|---|
| Constraint-based sketcher with the SolidWorks tool set (see below) | Linear static analysis |
| Smart Dimension, geometric relations, drag-to-modify, fully/under-defined colouring | Quadratic 10-node tetrahedra (Gmsh), with an optional linear mode |
| Extruded Boss: Blind, Mid Plane, reverse, **selected contours** (any closed area, even of crossing geometry) | Fixed Face fixtures |
| Extruded Cut: Blind, Through All, Through All-Both | Point Force loads (vertex snap, normal-to-face helper) |
| Revolved Boss / Revolved Cut: about any straight sketch line, including another sketch; any angle, mid-plane | Face Force and Pressure loads, local mesh control |
| **Assemblies:** insert parts, mates (coincident, parallel, perpendicular, distance, angle, concentric, lock), left-drag to move / right-drag to rotate components under their mates, animated mating, fixed/floating, DOF status, STEP export as a real assembly (SolidWorks opens it as parts + assembly) | |
| **Drawings:** Make Drawing from Part/Assembly, Standard 3 View (first/third angle), model and projected views, hidden lines, centre marks, Smart Dimension, notes, ISO title block; save as **PDF, DXF, DWG**, SVG, PNG, or one view at 1:1 DXF for laser/CNC | |
| Parametric feature tree: edit, suppress, delete, undo/redo | Material library (steels, aluminium, titanium, plastics) and custom materials |
| Export: STEP AP214/AP242/AP203, IGES, BREP, STL, OBJ, glTF, VRML | von Mises, principal, component stresses, displacements, factor of safety |
| Import: STEP, IGES, BREP. Native `.bcad` project file | Deformed plots, mesh overlay, legend clipping, VTU export for ParaView |
| AutoCAD-style command line: just type (`L`, `REC`, `TR`, `@50<30`) | |

### Sketch tools

| Draw | Modify | Dimensions & relations |
|---|---|---|
| Line, Centerline, Midpoint Line | Trim (click or drag across) | Smart Dimension: length, horizontal/vertical, point-line, parallel distance, angle, radius, diameter, centre distance |
| Corner, Center, 3-Point Corner, 3-Point Center Rectangle, Parallelogram | Extend | Driven (reference) dimensions |
| Straight Slot, Centerpoint Slot | Offset (chain, side picked by cursor) | Coincident, Midpoint, Horizontal, Vertical |
| Circle, Perimeter Circle | Mirror (with symmetric relations) | Parallel, Perpendicular, Collinear, Equal |
| Centerpoint Arc, Tangent Arc, 3-Point Arc | Sketch Fillet, Sketch Chamfer | Tangent, Concentric, Symmetric, Fix |
| Polygon (inscribed/circumscribed), Spline, Point | Move, Copy, Rotate, Scale, Linear & Circular Pattern, Construction | Automatic relations and inference while drawing |

Sketch geometry is driven by an in-house geometric constraint solver. Drag any under-defined
entity and everything connected follows while relations and dimensions hold. Blue means
under-defined and black means fully defined, as in SolidWorks. Adding a dimension that would
over-define the sketch offers to make it a driven (reference) dimension.

> **SolidWorks files:** `.sldprt` / `.sldasm` are closed formats that only SolidWorks can write,
> so on a PC with SolidWorks installed, **File > Export to SolidWorks** has *your* SolidWorks
> build them: parts with an editable feature tree (sketches, relations, dimensions, extrudes,
> cuts, revolves) and assemblies with floating components and real mates, so they move in
> SolidWorks. Without SolidWorks, use **STEP AP214 / AP242** (assemblies keep their structure).

**Extrude while sketching:** click **Extruded Boss** or **Extruded Cut** on the Sketch tab.
BauyrCAD accepts the current sketch and opens Extrude with that sketch selected; set the depth
and confirm. The distance starts at 10 mm and is selected automatically, so type your own
value immediately. No separate Exit Sketch step is needed. Cancelling Extrude keeps the saved sketch.

All built-in feature icons render from vector SVG at the requested size and display scaling.
Hover over a command to see its name and description; complex tools also show an input/result
illustration.

Units are consistent **mm · N · MPa** throughout.

---

## Installing and running

### Option 1: Windows app (no Python needed)
Download `BauyrCAD-<version>-win64.zip` (or the installer) from the Releases page, unzip it
and run `BauyrCAD.exe`.

### Option 2: from source
Requires **Python 3.10–3.12** (the OpenCASCADE bindings don't ship wheels for 3.13+ yet).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\activate
pip install -e .[dev]
python -m bauyrcad            # or just: bauyrcad
```

The FEA solver is Intel MKL PARDISO (`pypardiso`, installed with the rest): a 500 000-equation
thin plate solves in about 20 s. BauyrCAD estimates the memory a solve needs first and says so
clearly when a model is too big for the computer.

---

## Quick start: a loaded bracket in 2 minutes

1. **Sketch** tab → **Sketch** → click **Front Plane** (or just type `SK`).
2. Type `REC` → Enter, click the origin, then type `@100,40` → Enter.
   Type `C` → Enter, click inside the plate, type `6` → Enter (radius).
3. **Smart Dimension** (`D`): click the bottom edge, click below it to place it, and type 100.
   Click the circle, place it, and type 12. Drag the circle: it moves, and the dimensions hold.
4. **Exit Sketch**, then **Extruded Boss** (`EXT`) → depth 10 → **OK**.
5. **Simulation** → **New Study** → **Material** → pick *Aluminium 6061-T6*.
6. **Fixed Face** → click the left end face (x = 0) → **OK**.
7. **Point Force** → click the top-right corner (snaps to the vertex) → Fy = −200 N → **OK**.
8. **Mesh & Run** (`RUN`) → **Run**. Set *Legend* to *99th percentile* to hide the point-load
   singularity.
9. **File → Save As** → choose *STEP AP214* to take the part into SolidWorks.

Ready-made examples are in [`examples/`](examples/).

### Mouse & keyboard

| Action | Control |
|---|---|
| Rotate | middle-drag around the model surface nearest the cursor when the drag starts; left-drag is reserved for editing/selection |
| Pan | right-drag, or Ctrl/Shift + middle-drag |
| Zoom | wheel (about the cursor) |
| Select / place a point | left click (Ctrl+click adds to the selection) |
| Drag geometry / box select | left-drag in sketch Select mode (right-to-left = crossing) |
| Edit a dimension | double click it |
| Finish a chain / step | right click, Space or Enter |
| Repeat the previous command | Space or Enter (AutoCAD style, also for toolbar/menu commands) |
| Cancel a step, back to Select | Esc |
| Any command | just type it, e.g. `L`, `REC`, `TR`, `D`, `EXT`, `Z` |
| Typed input while drawing | `x,y` · `@dx,dy` · `@length<angle` · `length` (expressions and `in`/`cm` units allowed) |
| Front / Top / Right / Iso view | Ctrl+1 / 2 / 3 / 4 |
| Undo / Redo | Ctrl+Z / Ctrl+Y (also inside a sketch) |

---

## Verification

The solver is checked against closed-form solutions in the test suite (`tests/test_fea.py`):

| Case | Quantity | Theory | BauyrCAD (tet10, auto mesh) |
|---|---|---|---|
| Cantilever 10×10×100 mm, steel, 100 N tip load | tip deflection | 0.1919 mm (Timoshenko) | 0.1906 mm (−0.7 %) |
| same | bending stress at mid-span | 30.00 MPa | 29.96 MPa |
| same | Σ reactions | 100 N | 100.000 N |

Linear tetrahedra are 17 % too stiff on the same case. This is why quadratic elements are the
default, and why you should do the same in any FEA package.

> **Note on point loads.** A force at a single point produces a theoretically infinite stress
> right under the load. The displacement field and the stresses a short distance away converge
> normally; the peak value at the load node does not. This is the same behaviour you see in
> SolidWorks Simulation or Nastran. Use the legend clip, or look at stresses away from the point.

---

## Scripting and automation

Everything the GUI does can be scripted, which is how Claude works in BauyrCAD:

```python
from bauyrcad.api import Part
part = Part("plate")
sk = part.sketch("front"); sk.rect((0, 0), (120, 40)); sk.circle((100, 20), d=12)
part.extrude(sk, 10)
st = part.study(material="Aluminium 6061-T6")
st.fix(part.face("-x")); st.force(part.vertex((120, 40, 10)), (0, -200, 0))
print(st.run()); st.render("plate_vm.png"); part.save("plate.bcad")
```

```powershell
python -m bauyrcad info part.bcad            # JSON summary incl. face list
python -m bauyrcad run part.bcad --png vm.png
python -m bauyrcad render part.bcad -o part.png
python -m bauyrcad export part.bcad part.step
```

A part that's open in the GUI reloads automatically when a script rewrites its file.
Full reference: [docs/API.md](docs/API.md). Personal jobs go in `projects/` (kept out of git).

## Development

```powershell
pip install -e .[dev]
pytest                                         # 91 tests: solver, sketch tools, contours, modelling, formats, FEA, API/CLI, GUI
.\packaging\build_windows.ps1                  # -> dist\BauyrCAD\BauyrCAD.exe + zip
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the code is organised and how to add
new sketch tools, features, loads and element types, and [docs/ROADMAP.md](docs/ROADMAP.md)
for the planned work.

## License

BauyrCAD's own source code is MIT licensed (see [LICENSE](LICENSE)). Binary distributions
bundle third-party libraries under their own licenses. In particular, Gmsh is GPL-2.0-or-later,
which means a distributed bundle that includes Gmsh must be offered under GPL-compatible terms.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Trademarks and interoperability

BauyrCAD is an independent project. It is **not affiliated with, endorsed by or sponsored by
Dassault Systèmes or Siemens**. SOLIDWORKS is a registered trademark of Dassault Systèmes
SolidWorks Corporation; Parasolid is a trademark of Siemens Digital Industries Software. Other
names are trademarks of their owners and are used only to describe compatibility.

* **Export to SolidWorks** drives a SolidWorks installation the user already has, through its
  public COM API. No SolidWorks code, libraries or files are part of BauyrCAD.
* **Reading `.sldprt` / `.sldasm` files** (`bauyrcad/core/sldprt.py`) works from the structure of
  the data files alone, for interoperability. No SolidWorks or Parasolid program code was
  disassembled, and no Dassault or Siemens code, SDK or library is used. See
  [docs/SLDPRT_FORMAT.md](docs/SLDPRT_FORMAT.md).
* The user interface follows familiar CAD conventions (feature names, toolbars) so engineers
  feel at home; it contains no artwork or text taken from other products.
