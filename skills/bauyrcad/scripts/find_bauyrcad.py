"""Locate the BauyrCAD repository and the Python that runs it. Standard library only.

    python find_bauyrcad.py            -> JSON: repo, python, ready, how to fix if not
    python find_bauyrcad.py --quiet    -> just the repo path (exit code 1 if not found)

Search order: $BAUYRCAD_HOME, the known Windows location, the current folder and its parents,
then folders mounted into a Cowork / sandbox session (/sessions/*/mnt, /mnt, /workspace, ~),
a few levels deep. A folder counts as BauyrCAD when it has bauyrcad/api.py and CLAUDE.md.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

KNOWN = [r"C:\Users\User\Everything\Bauyrzhan\Claude_Projects\Bauyr_CAD"]
SKIP = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "AppData", "site-packages",
        "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin", "proc", "sys"}


def is_repo(p: Path) -> bool:
    try:
        return (p / "bauyrcad" / "api.py").is_file() and (p / "CLAUDE.md").is_file()
    except OSError:
        return False


def walk(root: Path, depth: int):
    if depth < 0:
        return
    try:
        if is_repo(root):
            yield root
            return
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in SKIP and not child.name.startswith("."):
                yield from walk(child, depth - 1)
    except (OSError, PermissionError):
        return


def candidates():
    env = os.environ.get("BAUYRCAD_HOME")
    if env:
        yield Path(env)
    for k in KNOWN:
        yield Path(k)
    here = Path.cwd().resolve()
    yield from [here, *here.parents]
    roots = [Path(p) for p in glob.glob("/sessions/*/mnt")] + \
            [Path(p) for p in ("/mnt", "/workspace", "/home", str(Path.home()))]
    for r in roots:
        if r.is_dir():
            yield from walk(r, 5)


def find():
    seen = set()
    for c in candidates():
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen:
            continue
        seen.add(c)
        if is_repo(c):
            return c
    return None


def python_for(repo: Path):
    win = repo / ".venv" / "Scripts" / "python.exe"
    nix = repo / ".venv" / "bin" / "python"
    for p in (win, nix):
        if p.is_file():
            return p
    return None


def check(py: Path, repo: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run([str(py), "-c", "import bauyrcad, OCP; print(bauyrcad.__version__)"],
                           cwd=str(repo), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip()[-400:])


def main():
    quiet = "--quiet" in sys.argv
    repo = find()
    if repo is None:
        if quiet:
            return 1
        print(json.dumps({"found": False, "fix": (
            "BauyrCAD is not visible from this session. Ask the user to share the folder "
            f"{KNOWN[0]} with the session (in Cowork: add it as a folder; in Claude Code: open "
            "or add that directory), or set BAUYRCAD_HOME to its path.")}, indent=1))
        return 1
    if quiet:
        print(repo)
        return 0
    py = python_for(repo)
    out = {"found": True, "repo": str(repo), "claude_md": str(repo / "CLAUDE.md"),
           "api_doc": str(repo / "docs" / "API.md"),
           "improvements_log": str(repo / "docs" / "IMPROVEMENTS.md"),
           "gui_exe": str(repo / "dist" / "BauyrCAD" / "BauyrCAD.exe")}
    if py is not None:
        ok, msg = check(py, repo)
        out.update(python=str(py), ready=ok, version=msg if ok else None, error=None if ok else msg)
    else:
        out.update(python=None, ready=False)
    if not out["ready"]:
        same_os = (os.name == "nt") == (repo / ".venv" / "Scripts").is_dir()
        out["fix"] = (
            "Windows: py -3.11 -m venv .venv; .\\.venv\\Scripts\\python.exe -m pip install -e .[dev]"
            if os.name == "nt" else
            "Linux/macOS sandbox (the Windows .venv cannot run here): python3.11 -m venv /tmp/bc && "
            f"/tmp/bc/bin/pip install -e '{repo}' ; then use /tmp/bc/bin/python and "
            "PYVISTA_OFF_SCREEN=true" + ("" if same_os else " (the repo's .venv is for Windows)"))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
