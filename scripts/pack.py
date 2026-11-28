#!/usr/bin/env python3
"""跨平台打包：生成 <skill>-vX.Y.Z.zip，并校验版本号一致。

Windows 上直接 `python scripts\\pack.py` 即可，不需要 bash。
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = ROOT.name

EXCLUDE_DIRS = {"__pycache__", ".git", ".jacar", "downloads", "tests", ".idea", ".vscode"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}


def read_versions() -> tuple[str, str]:
    ver_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^\s+version:\s*(\S+)\s*$", skill_md, re.MULTILINE)
    return ver_file, (m.group(1) if m else "")


def main() -> int:
    ver_file, ver_skill = read_versions()
    print(f"VERSION={ver_file}  SKILL.md metadata.version={ver_skill}")
    if not ver_skill:
        print("! SKILL.md 里没找到 metadata.version")
        return 1
    if ver_file != ver_skill:
        print("! 两处版本号不一致，先把它们改成一样再打包")
        return 1

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads"
    if not out_dir.is_dir():
        out_dir = Path.cwd()
    out = out_dir / f"{SKILL_NAME}-v{ver_file}.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if p.name in EXCLUDE_FILES or p.suffix == ".pyc":
                continue
            z.write(p, Path(SKILL_NAME) / rel)
            count += 1

    names = zipfile.ZipFile(out).namelist()
    ok_skill = any(n.endswith("SKILL.md") for n in names)
    ok_clean = not any("__pycache__" in n or n.endswith(".DS_Store") for n in names)

    print(f"\n{out.name}  ({count} 个文件, {out.stat().st_size // 1024} KB)")
    print(("✓" if ok_skill else "✗") + " SKILL.md 在包内")
    print(("✓" if ok_clean else "✗") + " 无 __pycache__ / .DS_Store")
    return 0 if (ok_skill and ok_clean) else 1


if __name__ == "__main__":
    sys.exit(main())
