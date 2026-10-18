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
* Because the Windows bundle includes **Gmsh (GPL)**, the bundle as a whole must be distributed
  under GPL-compatible terms: provide the source code (this repository) along with the binaries.
  That is straightforward for an open-source release.
* For a **closed-source / commercial** release, either replace the mesher (the whole Gmsh
  dependency is isolated in `bauyrcad/fea/mesher.py`; Netgen is LGPL) or buy a commercial
  Gmsh license from its authors.
* The Intel MKL runtime may be redistributed under the Intel Simplified Software License;
  keep its license text with the binaries.
* SolidWorks is **not** part of BauyrCAD in any form: Export to SolidWorks uses the user's own
  installation through its COM API, and the `.sldprt` reader contains no Dassault code. See the
  README's *Trademarks and interoperability* section.
* Qt/PySide6 (LGPL-3.0) is used as dynamically loaded libraries, which satisfies the LGPL as long
  as users can replace them (the one-folder build keeps the DLLs as separate files).
