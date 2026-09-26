"""Source-hygiene gate for ``custom_components/irish_rail``.

Three checks, all enforced by the build rather than by convention:

1. **Docstring density** — docstring lines must stay at or below
   ``MAX_DENSITY`` per non-blank source line.
2. **Project-internal cross-references** — ``Skill N`` / ``Phase N`` /
   ``roadmap`` breadcrumbs are plan scratchpad, not source.
3. **Backticked module pointers** — a ```` `foo.py` ```` reference in a
   docstring or comment must name a file that exists in the integration
   tree, so a pointer to a deleted module cannot outlive its deletion.

Exits non-zero and prints every offender when any check fails.

Run: python scripts/check_streamline_a4.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path("custom_components/irish_rail")

# 0.21 was set when the documentation-only phase closed; the ceiling has not
# moved since, so the prose gate and the enforced gate agree.
MAX_DENSITY = 0.21

CROSS_REF = re.compile(r"\b(Skill\s+\d+|Phase\s+\d|\broadmap\b)")
MODULE_REF = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*\.py)`")
DOCSTRING = re.compile(r'"""(.*?)"""', re.DOTALL)


def _py_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def check_docstring_density(py_files: list[Path]) -> list[str]:
    """Return the density failures; empty when the ratio is within budget."""
    total_loc = 0
    total_doc = 0
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        total_loc += sum(1 for line in text.splitlines() if line.strip())
        total_doc += sum(
            len(match.group(1).splitlines()) for match in DOCSTRING.finditer(text)
        )
    density = total_doc / total_loc if total_loc else 0.0
    print(
        f"docstring lines: {total_doc}  source LOC: {total_loc}  "
        f"density: {density:.3f} (max {MAX_DENSITY:.2f})"
    )
    if density > MAX_DENSITY:
        return [f"docstring density {density:.3f} exceeds {MAX_DENSITY:.2f}"]
    return []


def check_cross_references(py_files: list[Path]) -> list[str]:
    """Return every project-internal cross-reference found in source."""
    return [
        f"{path}:{number}: {line.rstrip()}"
        for path in py_files
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        )
        if CROSS_REF.search(line)
    ]


def check_module_pointers(py_files: list[Path]) -> list[str]:
    """Return backticked ``.py`` references naming a file that does not exist."""
    tree = {path.name for path in ROOT.rglob("*.py")}
    return [
        f"{path}: {name} is referenced in source but is not in {ROOT}"
        for path in py_files
        for name in sorted(set(MODULE_REF.findall(path.read_text(encoding="utf-8"))))
        if name not in tree
    ]


def main() -> int:
    """Run every check and report the combined result."""
    py_files = _py_files()
    failures: list[str] = []
    failures += check_docstring_density(py_files)
    failures += check_cross_references(py_files)
    failures += check_module_pointers(py_files)
    if failures:
        for failure in failures:
            print(failure)
        print(f"\n{len(failures)} source-hygiene failure(s)", file=sys.stderr)
        return 1
    print(f"OK: source hygiene clean in {ROOT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
