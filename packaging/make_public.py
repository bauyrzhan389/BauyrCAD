"""Build the shareable snapshot of BauyrCAD on the `github` branch.

The `github` branch has its own, fresh history: each run adds one commit holding the code of
the current HEAD (committed work only - uncommitted changes are never included), with the
private-information rules applied. The full development history stays local.

    .\\.venv\\Scripts\\python.exe packaging\\make_public.py [--author "Name <id+user@users.noreply.github.com>"]
                                                     [--message "..."] [--dry-run]
    git push -u origin github:main          # then publish (only when you mean to)

Rules live in packaging/public_rules.local.json (gitignored, so the words to scrub are never
published themselves):

    {
      "author": "Name <id+user@users.noreply.github.com>",
      "exclude": ["tests/data/sw"],                    # paths left out of the snapshot
      "replace": [["regex", "replacement"], ...],      # applied to every text file
      "forbid": ["word", ...]                          # abort if any remains (any file, any case)
    }

The run aborts, committing nothing, when a forbidden word is still found anywhere in the
snapshot (text or binary files).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "packaging" / "public_rules.local.json"
BRANCH = "github"
TEXT_EXT = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yml", ".yaml", ".ps1",
            ".spec", ".iss", ".csv", ".svg", ".html", ".bat", ".gitignore", ""}


def git(*args, cwd=ROOT, check=True, binary=False):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stderr.decode(errors='replace')}")
    return r.stdout if binary else r.stdout.decode("utf-8", errors="replace").strip()


def branch_exists(name) -> bool:
    return bool(git("branch", "--list", name))


def is_text(path: Path, data: bytes) -> bool:
    return (path.suffix.lower() in TEXT_EXT or path.name.startswith(".")) and b"\x00" not in data[:4096]


def build(rules: dict, work: Path) -> list[str]:
    """Export HEAD into `work`, apply the rules; returns the forbidden-word hits."""
    for item in work.iterdir():                    # clear the previous snapshot (keep .git)
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    archive = git("archive", "--format=zip", "HEAD", binary=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        z.extractall(work)
    for rel in rules.get("exclude", []):
        p = work / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    reps = [(re.compile(a), b) for a, b in rules.get("replace", [])]
    forbid = [w.lower().encode() for w in rules.get("forbid", [])]
    hits = []
    for p in sorted(work.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(work).parts:
            continue
        data = p.read_bytes()
        if is_text(p, data) and reps:
            text = data.decode("utf-8", errors="surrogateescape")
            new = text
            for rx, b in reps:
                new = rx.sub(b, new)
            if new != text:
                data = new.encode("utf-8", errors="surrogateescape")
                p.write_bytes(data)
        low = data.lower()
        for w in forbid:
            if w in low or w in low.replace(b"\x00", b""):
                hits.append(f"{p.relative_to(work).as_posix()}: '{w.decode()}'")
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--author", help='commit author, e.g. "Name <id+user@users.noreply.github.com>"')
    ap.add_argument("--message", help="commit message")
    ap.add_argument("--dry-run", action="store_true", help="build and check, commit nothing")
    a = ap.parse_args(argv)
    if not RULES.exists():
        raise SystemExit(f"{RULES} is missing - create it first (see this script's docstring)")
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    author = a.author or rules.get("author")
    if not author or "<" not in author:
        raise SystemExit('Give the commit author: --author "Name <email>" (a GitHub no-reply address)')
    name, email = author.split("<", 1)
    name, email = name.strip(), email.strip(" >")

    work = Path(tempfile.gettempdir()) / "bauyrcad_public_snapshot"
    if work.exists():
        git("worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)
    git("worktree", "prune")
    if branch_exists(BRANCH):
        git("worktree", "add", str(work), BRANCH)
    else:
        git("worktree", "add", "--detach", str(work), "HEAD")
        git("checkout", "--orphan", BRANCH, cwd=work)
        git("rm", "-r", "-q", "--cached", ".", cwd=work, check=False)
    try:
        hits = build(rules, work)
        if hits:
            print("Forbidden words found - nothing committed:")
            print("\n".join("  " + h for h in hits))
            return 1
        git("add", "-A", cwd=work)
        changed = git("status", "--porcelain", cwd=work)
        files = git("ls-files", cwd=work).splitlines()
        if a.dry_run:
            print(f"Dry run OK: {len(files)} files, {len(changed.splitlines())} changed, no forbidden words.")
            return 0
        if not changed:
            print("Nothing new to publish (the snapshot matches the last one).")
            return 0
        head = git("log", "-1", "--format=%cd", "--date=short")
        msg = a.message or f"BauyrCAD snapshot {date.today().isoformat()} (code as of {head})"
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
        r = subprocess.run(["git", "commit", "-q", "-m", msg], cwd=work, env=env,
                           capture_output=True)
        if r.returncode != 0:
            raise SystemExit(r.stderr.decode(errors="replace"))
        sha = git("rev-parse", "--short", "HEAD", cwd=work)
        print(f"Committed {sha} on '{BRANCH}': {len(files)} files, author {name} <{email}>.")
        print(f"Publish with:  git push -u origin {BRANCH}:main")
        return 0
    finally:
        git("worktree", "remove", "--force", str(work), check=False)


if __name__ == "__main__":
    sys.exit(main())
