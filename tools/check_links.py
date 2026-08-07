#!/usr/bin/env python3
"""Verify every relative markdown link in the repo resolves.

Twenty-one cross-referenced documents accumulate broken links quickly, and the
alternative discovery mechanism is a reader hitting a 404.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

def main() -> int:
    broken: list[str] = []
    checked = 0

    for md in sorted(REPO.rglob("*.md")):
        if "node_modules" in md.parts or ".git" in md.parts:
            continue
        for raw in LINK.findall(md.read_text(encoding="utf-8")):
            target = raw.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            if not (md.parent / target).resolve().exists():
                broken.append(f"{md.relative_to(REPO)} -> {raw}")

    print(f"checked {checked} relative links across markdown files")
    if broken:
        print(f"\n{len(broken)} BROKEN:")
        for b in broken:
            print("  " + b)
        return 1
    print("all resolve")
    return 0

if __name__ == "__main__":
    sys.exit(main())
