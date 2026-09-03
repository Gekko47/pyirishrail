"""Replicate the A4 sub-gate of .github/workflows/ci.yml (lines 110-123).

Counts project-internal cross-references (``Skill N``, ``Phase N``,
``roadmap N.X``, ``roadmap N.``) inside the integration source tree.
Exits non-zero when any are present.

Run: python scripts/check_streamline_a4.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path("custom_components/irish_rail")
PATTERN = re.compile(r"\b(Skill\s+\d+|Phase\s+\d|roadmap\s+\d\.\d|roadmap\s+\d\.)")


def main() -> int:
    py_files = [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    offenders: list[str] = []
    for p in py_files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if PATTERN.search(line):
                offenders.append(f"{p}:{i}: {line.rstrip()}")
    if offenders:
        for o in offenders:
            print(o)
        print(
            f"\n{len(offenders)} project-internal cross-reference(s) found in source",
            file=sys.stderr,
        )
        return 1
    print("OK: 0 project-internal cross-references in custom_components/irish_rail/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
