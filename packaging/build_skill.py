"""Package the `bauyrcad` Claude skill.

    python packaging/build_skill.py            -> dist/bauyrcad.skill (upload in claude.ai:
                                                  Settings > Capabilities > Skills)
    python packaging/build_skill.py --install  -> also copy it to ~/.claude/skills/bauyrcad
                                                  (every Claude Code session on this PC)
"""

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "skills" / "bauyrcad"


def files():
    return [p for p in sorted(SRC.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]


def main():
    out = ROOT / "dist" / "bauyrcad.skill"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files():
            z.write(p, Path("bauyrcad") / p.relative_to(SRC))
    print(out)
    if "--install" in sys.argv:
        dest = Path.home() / ".claude" / "skills" / "bauyrcad"
        if dest.exists():
            shutil.rmtree(dest)
        for p in files():
            target = dest / p.relative_to(SRC)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        print(dest)


if __name__ == "__main__":
    main()
