# Nastran export and the next simulation milestone

## Implemented on 7 October 2026

Simulation > Export BDF writes the active part study as an MSC Nastran SOL 101 deck.
The BDF/NASTRAN/EXPORTBDF aliases and `Study.export_bdf()` use the same path. Mesh and
boundary-condition preparation is shared with the internal solver. The GUI uses a
serialized document snapshot and a background worker; no analysis solve is required.

Exported entries: GRID, CTETRA, PSOLID, MAT1, SPC1, FORCE, PARAM/AUTOSPC. Large fixed
fields preserve numerical precision. Density is converted to tonne/mm³, matching
mm/N/MPa/s. Surface forces and pressure are equivalent nodal forces, not PLOAD cards.
They preserve the discretized load used by BauyrCAD. Changing the mesh externally
requires reconstructing those surface loads.

Installed solver discovered: MSC Nastran 2024.1, `D:\Programs\Nastran\bin\nastran.exe`.
The application exporter does not require or launch this executable. A tested local
invocation, from a folder containing the deck, is:

```powershell
& 'D:\Programs\Nastran\bin\nastran.exe' model.bdf scr=yes old=no
```

## Verification

A 10 x 10 x 30 mm bar fixed at z=0, loaded at z=30 with (0,-20,100) N, was meshed at
5 mm and solved by both BauyrCAD and MSC Nastran using identical meshes and loads.

| Mesh | Nodes | Elements | Relative L2 displacement difference | Nastran reaction, N |
|---|---:|---:|---:|---|
| tet4 | 258 | 771 | 5.97e-8 | approximately (0,20,-100) |
| tet10 | 1512 | 771 | 1.70e-7 | approximately (0,20,-100) |

Both jobs completed without fatal messages. Reactions agree within 1e-4 N; printed
F06 precision limits the comparison. This verifies this linear elastic workflow,
not general nonlinear/contact analysis. Automated tests also check pressure sign,
force/moment resultants, density conversion, restraints, quadratic edge ordering,
failure-safe output, unsupported loads and a real GUI export.

Card formatting reference: [MSC Nastran 2024.1 Getting Started Guide](https://documentation-be.hexagon.com/bundle/MSC_Nastran_2024.1_Getting_Started_Guide/raw/resource/enus/MSC_Nastran_2024.1_Getting_Started_Guide.pdf).

## Next: interaction properties

Keep the CAD-facing study definition independent of backend syntax. Add named body
surfaces; reusable normal/tangential properties; contact pair assignments; explicit
steps; and convergence/penetration diagnostics. The initial UI should offer only
properties that the selected adapter can actually export and verify.

First evaluate the installed MSC Nastran's nonlinear/contact solution support and
available licensed options against a two-body fixture. Separately, an Abaqus INP
adapter can be built without installing Abaqus, but native execution verification
will require an installed, usable Abaqus solver. Do not interpret similarity of input
syntax or interface labels as equivalence of contact formulations.

Initial acceptance cases: bonded interface, frictionless separation, frictional
sliding. Check load balance, contact pressure, penetration, slip and refinement.
Do not map these studies to the current SOL 101 exporter or silently omit interactions.
This milestone requires real multi-body study definitions and solver verification;
it is not completed by adding a friction coefficient field to the current dialog.
