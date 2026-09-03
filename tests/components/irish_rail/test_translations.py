"""Translation completeness guards (roadmap Phase 3 Gold rules).

The integration raises no ``HomeAssistantError`` toward users (read-only
integration, no service actions), satisfying Gold rule
``exception-translations`` vacuously; every user-facing failure surface is
translation-keyed instead:

- config-flow ``errors``/``abort`` bases referenced from ``config_flow.py``
- repair issues created via ``ir.async_create_issue`` (translation_key)
- entity names via entity translation keys

These tests pin that every translation key referenced in code resolves in
both ``strings.json`` and ``translations/en.json``, and that the two files
stay structurally aligned (standing requirement, Skill 09).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_DIR = Path(__file__).parents[3] / "custom_components" / "irish_rail"

ENTITY_SENSOR_KEYS = {
    "next_train_due",
}


def _load_json(relative_path: str) -> dict[str, Any]:
    """Load a JSON document from the integration directory."""
    data: dict[str, Any] = json.loads(
        (INTEGRATION_DIR / relative_path).read_text(encoding="utf-8")
    )
    return data


def _flatten_keys(value: Any, prefix: str = "") -> set[str]:
    """Flatten nested translation dicts into dotted key paths."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, sub in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(sub, dict):
                keys |= _flatten_keys(sub, path)
            else:
                keys.add(path)
    return keys


def test_no_homeassistanterror_raised_to_users() -> None:
    """No module raises HomeAssistantError, so no English can leak to users.

    Evidence for treating ``exception-translations`` as satisfied-by-design:
    the integration registers no service actions and its typed API
    exceptions surface only in logs, never in the UI.
    """
    offenders = [
        py_file.name
        for py_file in sorted(INTEGRATION_DIR.glob("*.py"))
        if "HomeAssistantError" in py_file.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_strings_and_translations_are_structurally_aligned() -> None:
    """strings.json and translations/en.json define identical key trees."""
    assert _flatten_keys(_load_json("strings.json")) == _flatten_keys(
        _load_json("translations/en.json")
    )


@pytest.mark.parametrize("file_name", ["strings.json", "translations/en.json"])
def test_flow_error_keys_referenced_by_config_flow_resolve(file_name: str) -> None:
    """Every error base used in config_flow.py exists in the strings files."""
    source = (INTEGRATION_DIR / "config_flow.py").read_text(encoding="utf-8")
    referenced = set(
        re.findall(r'errors\["base"\]\s*=\s*"([^"]+)"', source)
    ) | set(re.findall(r'errors\.setdefault\("base",\s*"([^"]+)"\)', source))
    assert referenced, "expected config_flow.py to reference flow error keys"

    defined = set(_load_json(file_name)["config"]["error"])
    assert referenced <= defined


@pytest.mark.parametrize("file_name", ["strings.json", "translations/en.json"])
def test_repair_issue_keys_match_strings_files(file_name: str) -> None:
    """Every ir.async_create_issue translation key has an issues entry."""
    coordinator_source = (INTEGRATION_DIR / "coordinator.py").read_text(
        encoding="utf-8"
    )
    issue_keys_in_code = set(
        re.findall(r'translation_key="([a-z0-9_]+)"', coordinator_source)
    )
    assert issue_keys_in_code, "expected repair-issue translation keys"

    issues = _load_json(file_name)["issues"]
    assert issue_keys_in_code == set(issues)
    for key in issue_keys_in_code:
        # The coordinator supplies the {station} placeholder.
        assert "{station}" in issues[key]["title"], key


@pytest.mark.parametrize("file_name", ["strings.json", "translations/en.json"])
def test_translations_contain_no_unclosed_html_tags(file_name: str) -> None:
    """Flow descriptions render via markdown; angle brackets are risky.

    Regression pin: a literal ``<destination>`` placeholder once surfaced to
    users as an ``unclosed_tag`` rendering error on the direction step.
    """
    data = _load_json(file_name)

    def _walk(value: Any) -> Iterator[str]:
        if isinstance(value, dict):
            for sub in value.values():
                yield from _walk(sub)
        elif isinstance(value, str):
            yield value

    offenders = [text for text in _walk(data) if "<" in text or ">" in text]
    assert offenders == []


@pytest.mark.parametrize("file_name", ["strings.json", "translations/en.json"])
def test_entity_sensor_names_cover_all_platform_entities(file_name: str) -> None:
    """Each sensor translation key used by sensor.py has a name entry."""
    source = (INTEGRATION_DIR / "sensor.py").read_text(encoding="utf-8")
    referenced = set(
        re.findall(r'IrishRailDueTrainSensor\(coordinator, "([^"]+)"\)', source)
    )
    assert referenced == ENTITY_SENSOR_KEYS
    assert referenced == set(_load_json(file_name)["entity"]["sensor"])
