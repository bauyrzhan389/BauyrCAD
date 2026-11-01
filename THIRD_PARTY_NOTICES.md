# Third-party software

BauyrCAD is built on the following open-source components. Binary distributions
(the Windows app) include them; each remains under its own license.

| Component | Use | License |
|---|---|---|
| [Open CASCADE Technology](https://dev.opencascade.org/) via [cadquery-ocp](https://github.com/CadQuery/OCP) | B-rep geometry kernel, STEP translator | LGPL-2.1 with OCCT exception / Apache-2.0 bindings |
| [Gmsh](https://gmsh.info/) | Tetrahedral meshing | **GPL-2.0-or-later** (with linking exceptions) |
| [VTK](https://vtk.org/) | 3D rendering | BSD-3-Clause |
| [PyVista](https://pyvista.org/), pyvistaqt | VTK convenience layer | MIT |
| [Qt 6](https://www.qt.io/) via [PySide6](https://doc.qt.io/qtforpython/) | User interface | LGPL-3.0 |
| [NumPy](https://numpy.org/), [SciPy](https://scipy.org/) | Numerics, sparse direct solver | BSD-3-Clause |
| [PyAMG](https://github.com/pyamg/pyamg) | Algebraic multigrid preconditioner | MIT |
| [Matplotlib](https://matplotlib.org/) (colour maps via PyVista) | Colour maps | PSF-style (Matplotlib license) |
| [pypardiso](https://github.com/haasad/PyPardiso) + Intel MKL runtime (bundled in the Windows app) | Fast direct solver (PARDISO) | BSD-3-Clause / Intel Simplified Software License (redistribution permitted) |
| [ezdxf](https://ezdxf.mozman.at/) | DXF drawing export | MIT |
| [pywin32](https://github.com/mhammond/pywin32) (Windows) | COM bridge for Export to SolidWorks | PSF |

## What this means for distributing BauyrCAD

* BauyrCAD's own source code is MIT licensed.
* Binary releases containing **Gmsh (GPL)** must meet the applicable GPL requirements,
  including corresponding source and licence notices. A link to BauyrCAD's own source
  alone does not establish compliance for all bundled dependencies.
* For a **closed-source / commercial** release, either replace the mesher (the whole Gmsh
  dependency is isolated in `bauyrcad/fea/mesher.py`; Netgen is LGPL) or buy a commercial
  Gmsh license from its authors.
* The Intel MKL runtime may be redistributed under the Intel Simplified Software License;
  keep its license text with the binaries.
* BauyrCAD does not bundle SolidWorks executables, DLLs, SDKs or decompiler output.
  The offline exporter does include SolidWorks model seed files. Their provenance and
  redistribution rights require separate review; the MIT licence for BauyrCAD's own code
  does not grant rights to third-party material. COM export uses the user's installation.
  See the README's *Trademarks and interoperability* section.
* Qt/PySide6 (LGPL-3.0) is dynamically loaded. Keeping DLLs separate supports replacement,
  but does not by itself satisfy every LGPL obligation. Binary releases must also address
  applicable notices, source availability and users' modification/relinking rights.
