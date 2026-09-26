"""Release consistency gate: manifest, CHANGELOG and git tag must agree.

Home Assistant derives an integration's version from
``custom_components/irish_rail/manifest.json``, and HACS derives a release
from the git tag. When the two drift apart the user sees one version in the
UI and another in the release notes, and a tag can ship a manifest version
nobody tested. This gate fails the build instead.

Run: python scripts/check_release_version.py v0.5.1
     python scripts/check_release_version.py   (no tag: checks the pair only)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MANIFEST = Path("custom_components/irish_rail/manifest.json")
CHANGELOG = Path("CHANGELOG.md")
HEADING = re.compile(r"^##\s+\[?v?(?P<version>\d+\.\d+\.\d+[^\]\s]*)\]?")


def manifest_version(manifest: Path) -> str:
    """Return the version declared in ``manifest.json``."""
    return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])


def top_changelog_version(changelog: Path) -> str:
    """Return the version of the newest released CHANGELOG section."""
    for line in changelog.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if match:
            return match.group("version")
    raise ValueError(f"no released version heading found in {changelog}")


def check(manifest: Path, changelog: Path, tag: str | None) -> list[str]:
    """Return one message per disagreement; empty when all three agree."""
    failures: list[str] = []
    declared = manifest_version(manifest)
    released = top_changelog_version(changelog)
    if declared != released:
        failures.append(
            f"{manifest} declares {declared} but the top CHANGELOG entry is {released}"
        )
    if tag is not None:
        expected = tag.removeprefix("v")
        if declared != expected:
            failures.append(f"tag {tag} does not match manifest version {declared}")
    return failures


def main(argv: list[str]) -> int:
    """Run the gate and report the combined result."""
    tag = argv[1] if len(argv) > 1 else None
    try:
        failures = check(MANIFEST, CHANGELOG, tag)
    except (OSError, KeyError, ValueError) as err:
        print(f"cannot read release metadata: {err}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(f"OK: manifest, CHANGELOG and {tag or 'each other'} agree")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
