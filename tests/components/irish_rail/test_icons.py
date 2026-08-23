"""Validate icons.json structure and alignment with translation keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_DIR = Path(__file__).parents[3] / "custom_components" / "irish_rail"

EXPECTED_SENSOR_KEYS = {
    "next_train_due",
    "next_train_destination",
    "next_train_delay",
    "next_train_type",
}


def _load_json(relative_path: str) -> dict[str, Any]:
    """Load a JSON document from the integration directory."""
    data: dict[str, Any] = json.loads(
        (INTEGRATION_DIR / relative_path).read_text(encoding="utf-8")
    )
    return data


def test_icons_json_covers_all_sensors_with_mdi_defaults() -> None:
    """Every sensor translation key has an icon translation."""
    sensor_icons = _load_json("icons.json")["entity"]["sensor"]
    assert set(sensor_icons) == EXPECTED_SENSOR_KEYS
    for key, entry in sensor_icons.items():
        assert entry["default"].startswith("mdi:"), key


@pytest.mark.parametrize("file_name", ["strings.json", "translations/en.json"])
def test_icons_align_with_translation_keys(file_name: str) -> None:
    """Icon keys stay aligned with entity translation keys in strings files."""
    icons = set(_load_json("icons.json")["entity"]["sensor"])
    translations = set(_load_json(file_name)["entity"]["sensor"])
    assert icons == translations
