# PyInstaller spec for the Windows desktop build.
#   pyinstaller packaging/bauyrcad.spec --noconfirm
# Produces dist/BauyrCAD/BauyrCAD.exe (one-folder app).
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SITE = Path(next(p for p in sys.path if p.endswith("site-packages")))


def tree(src: Path, dest: str):
    """All files below src, placed under dest/ in the bundle (keeps layout)."""
    out = []
    for f in src.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            out.append((str(f), str(Path(dest) / f.parent.relative_to(src))))
    return out


binaries = []
datas = tree(ROOT / "bauyrcad" / "data" / "sw_seeds", "bauyrcad/data/sw_seeds")
hiddenimports = []

# OpenCASCADE: one big extension + stub packages; its DLLs are found through
# os.add_dll_directory("<bundle>/cadquery_ocp.libs") and "<bundle>/vtk.libs".
binaries += tree(SITE / "cadquery_ocp.libs", "cadquery_ocp.libs")
binaries += tree(SITE / "vtk.libs", "vtk.libs")
binaries += [(str(p), "OCP") for p in (SITE / "OCP").glob("*.pyd")]
hiddenimports += collect_submodules("OCP", filter=lambda n: n.count(".") <= 1)

# Gmsh: gmsh.py looks for the DLL next to itself (bundle root).
gmsh_dll = next(Path(sys.prefix).rglob("gmsh-*.dll"))
binaries += [(str(gmsh_dll), ".")]

hiddenimports += collect_submodules("pyamg")
hiddenimports += ["vtkmodules.all", "vtkmodules.util.numpy_support", "pyvistaqt"]
datas += collect_data_files("pyvista")
# PARDISO direct solver: pypardiso + the Intel MKL runtime it loads (mkl_rt picks a CPU-specific
# kernel library at run time, so every CPU variant is bundled). launcher.py puts Library/bin on
# the DLL path. ~415 MB of the 595 MB MKL install; the rest (ScaLAPACK, VML, SYCL) is unused.
hiddenimports += ["pypardiso"]
_mkl = Path(sys.prefix) / "Library" / "bin"
for _pat in ("mkl_rt.*.dll", "mkl_core.*.dll", "mkl_intel_thread.*.dll", "mkl_def.*.dll",
             "mkl_mc3.*.dll", "mkl_avx2.*.dll", "mkl_avx512.*.dll", "mkl_avx10.*.dll",
             "libiomp5md.dll"):
    binaries += [(str(p), "Library/bin") for p in _mkl.glob(_pat)]

# SolidWorks export: pywin32 COM bridge (makepy generates SolidWorks' bindings at run time)
hiddenimports += ["pythoncom", "pywintypes", "win32com", "win32com.client"]
hiddenimports += collect_submodules("win32com.client")

# drawings: DXF writer (+ its fonts / resources) and the vector PDF/SVG backend
hiddenimports += collect_submodules("ezdxf")
datas += collect_data_files("ezdxf")
hiddenimports += ["matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg",
                  "matplotlib.backends.backend_agg"]
datas += collect_data_files("matplotlib")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PyQt6", "IPython", "jupyter", "notebook", "trame",
              "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia"],
    noarchive=False,
)
# Qt uses Windows' system ICU (unversioned function names). A third-party
# Poppler directory on PATH can supply an incompatible icuuc.dll with suffixed
# symbols instead. Never shadow the system ICU in the bundle.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != "icuuc.dll"]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BauyrCAD",
    icon=str(ROOT / "packaging" / "bauyrcad.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BauyrCAD")
