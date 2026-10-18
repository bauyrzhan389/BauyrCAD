"""Frozen-app entry point used by PyInstaller."""
import glob
import os
import sys

if getattr(sys, "frozen", False):
    # Intel MKL (PARDISO solver) ships in <bundle>/Library/bin; mkl_rt loads its CPU-specific
    # kernels from there at run time, so that folder must be on the DLL search path.
    _mkl = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "Library", "bin")
    if os.path.isdir(_mkl):
        os.add_dll_directory(_mkl)
        os.environ["PATH"] = _mkl + os.pathsep + os.environ.get("PATH", "")
        _rt = sorted(glob.glob(os.path.join(_mkl, "mkl_rt*.dll")))
        if _rt:
            os.environ.setdefault("PYPARDISO_MKL_RT", _rt[-1])

from bauyrcad.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
