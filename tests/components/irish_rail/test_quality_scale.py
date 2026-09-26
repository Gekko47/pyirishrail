"""Tests for the quality-scale evidence contract.

``quality_scale.yaml`` is the artifact that proves the integration's
Platinum claim, so a pointer naming deleted code makes the whole claim
false. These tests turn that class of stale evidence into a build
failure rather than something a reviewer has to notice by hand.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

INTEGRATION_DIR = Path(__file__).resolve().parents[3] / "custom_components" / "irish_rail"
REPO_ROOT = INTEGRATION_DIR.parents[1]
QUALITY_SCALE = INTEGRATION_DIR / "quality_scale.yaml"
TEST_DIR = REPO_ROOT / "tests" / "components" / "irish_rail"

# ``module.py::symbol.path`` or ``module.py`` in an evidence comment. The
# integration modules are referenced by bare filename throughout, so the
# pattern deliberately anchors on a real .py name. The symbol may be a
# dotted attribute path (``IrishRailClient.__init__``); every segment of
# it is verified, not just the leading class.
_POINTER = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*\.py)"
    r"(?:::((?:[A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z_][A-Za-z0-9_]*)*))?"
)
# Symbols that live in Home Assistant itself, not in this integration.
_EXTERNAL_MODULES = {"__init__.py"}


def _find_named(body: list[ast.stmt], name: str) -> ast.stmt | None:
    """Return the statement in ``body`` that defines ``name``, if any."""
    for node in body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name == name
        ):
            return node
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node
    return None


def _symbol_path_exists(source: str, dotted: str) -> bool:
    """Return whether every segment of a dotted symbol path is defined."""
    scope: ast.Module | ast.ClassDef | ast.stmt = ast.parse(source)
    for name in dotted.split("."):
        if not isinstance(scope, ast.Module | ast.ClassDef):
            # Only a module or a class owns members; a pointer that digs
            # into a function body is naming something that cannot exist.
            return False
        found = _find_named(scope.body, name)
        if found is None:
            return False
        scope = found
    return True


def _rules() -> dict[str, dict[str, str]]:
    """Parse quality_scale.yaml into ``{rule: {status, comment}}``.

    PyYAML ships no type stubs, so the parsed payload is validated here
    rather than reaching for a ``# type: ignore`` (the project keeps the
    integration free of suppressions).
    """
    import yaml

    data = cast(Any, yaml.safe_load(QUALITY_SCALE.read_text(encoding="utf-8")))
    rules = cast(dict[str, Any], data["rules"])
    return {
        rule_id: {
            "status": str(entry["status"]),
            "comment": str(entry["comment"]),
        }
        for rule_id, entry in rules.items()
    }


def test_quality_scale_parses_and_covers_bronze_through_platinum() -> None:
    """The file is valid YAML and declares a non-trivial rule set."""
    rules = _rules()
    assert len(rules) > 40
    for rule_id, entry in rules.items():
        assert entry["status"] in ("done", "exempt"), f"{rule_id}: bad status"
        assert entry.get("comment"), f"{rule_id}: missing evidence comment"


def test_every_exempt_rule_states_a_reason() -> None:
    """An ``exempt`` with no justification is not an exemption."""
    for rule_id, entry in _rules().items():
        if entry["status"] != "exempt":
            continue
        comment = entry["comment"]
        assert len(comment.split()) >= 4, (
            f"{rule_id}: exemption must explain itself, got {comment!r}"
        )


def test_every_evidence_file_pointer_resolves() -> None:
    """Every ``*.py`` named in an evidence comment must exist on disk.

    This is the regression test for the audit finding that several rows
    still pointed at code deleted in 0.4.0 (``next_train_delay``,
    ``next_train_destination``) or at a README section that never existed.
    A stale pointer now fails the suite instead of failing a review.
    """
    missing: list[str] = []
    for rule_id, entry in _rules().items():
        for filename, symbol in _POINTER.findall(entry["comment"]):
            if filename in _EXTERNAL_MODULES:
                continue
            candidates = list(INTEGRATION_DIR.glob(filename))
            candidates += list(REPO_ROOT.glob(f"*/{filename}"))
            # Evidence rows also cite the test that pins the behaviour.
            candidates += list(TEST_DIR.glob(filename))
            if not candidates:
                missing.append(f"{rule_id}: no file named {filename!r}")
                continue
            # A dotted reference such as ``IrishRailClient.__init__``
            # names the attribute path, so every segment of it has to
            # land: a pointer to a method the class does not define is
            # as stale as a pointer to a deleted module.
            if symbol and not any(
                _symbol_path_exists(p.read_text(encoding="utf-8"), symbol)
                for p in candidates
                if p.suffix == ".py"
            ):
                missing.append(f"{rule_id}: {filename} has no {symbol!r}")
    assert not missing, "stale quality-scale evidence:\n" + "\n".join(missing)


def test_readme_sections_cited_by_docs_rules_exist() -> None:
    """``docs_*`` rules must cite a README heading that is really there.

    Several rows previously pointed at "Actions"/"Conditions"/"Triggers"
    sections, none of which exist; the README uses "Examples",
    "Behaviour" and "Sensors".
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    headings = {
        line.lstrip("#").strip()
        for line in readme.splitlines()
        if line.startswith("#")
    }
    for rule_id, entry in _rules().items():
        if not rule_id.startswith("docs_"):
            continue
        cited = re.findall(r'README\s+"([^"]+)"', entry["comment"])
        for section in cited:
            assert section in headings, (
                f"{rule_id} cites README section {section!r}, which does not exist. "
                f"Headings: {sorted(headings)}"
            )


def test_strict_typing_claim_matches_the_tree() -> None:
    """The ``strict_typing`` row asserts no ``type: ignore`` remains.

    That claim became true when the coordinator's private-API override
    was removed; this test keeps it honest if either side regresses.
    """
    ignores = [
        f"{p.name}:{i}"
        for p in INTEGRATION_DIR.glob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "type: ignore" in line
    ]
    assert not ignores, f"integration source has type: ignore at {ignores}"


def test_action_setup_is_not_exempt_while_a_service_is_registered() -> None:
    """``action_setup`` claimed 'no service actions' while one is registered.

    button.py registers ``irish_rail.rebuild_stops_matrix``; the rule must
    therefore be ``done`` and point at it.
    """
    rules = _rules()
    assert rules["action_setup"]["status"] == "done"
    assert "button.py" in rules["action_setup"]["comment"]
    assert "services.yaml" in rules["action_setup"]["comment"]


def test_services_yaml_declares_the_registered_service() -> None:
    """The declared service matches what button.py actually registers."""
    services = (INTEGRATION_DIR / "services.yaml").read_text(encoding="utf-8")
    button = (INTEGRATION_DIR / "button.py").read_text(encoding="utf-8")
    declared = {line.split(":")[0].strip() for line in services.splitlines() if line and not line.startswith((" ", "#", "-"))}
    assert "rebuild_stops_matrix" in declared
    assert "rebuild_stops_matrix" in button


@pytest.mark.parametrize("relative", ["manifest.json", "icons.json", "strings.json"])
def test_json_evidence_files_are_valid(relative: str) -> None:
    """Evidence rows pointing at JSON must point at parseable JSON."""
    json.loads((INTEGRATION_DIR / relative).read_text(encoding="utf-8"))
