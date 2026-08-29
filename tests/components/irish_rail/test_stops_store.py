"""Tests for the persistent and bundled "stops at" matrices."""

from __future__ import annotations

from collections.abc import Generator
import logging
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.irish_rail import store as ir_store
from custom_components.irish_rail.const import DOMAIN
from custom_components.irish_rail.store import (
    ALL_DIRECTIONS_KEY,
    StopsMatrixStore,
    async_load_bundled_stops_matrix,
    get_stops_store,
    lookup_in_matrix,
    normalize_direction_key,
)


@pytest.fixture(autouse=True)
def _reset_seed_cache() -> Generator[None]:
    """Keep the module-level seed cache isolated between tests."""
    ir_store._SEED_CACHE = None
    yield
    ir_store._SEED_CACHE = None


def test_normalize_direction_key_buckets_directionless_filters() -> None:
    """Direction values lowercase into buckets; absence maps to ``_all``."""
    assert normalize_direction_key("Northbound") == "northbound"
    assert normalize_direction_key("To Cork") == "to cork"
    assert normalize_direction_key(None) == ALL_DIRECTIONS_KEY
    assert normalize_direction_key("") == ALL_DIRECTIONS_KEY


def test_lookup_in_matrix_tolerates_malformed_shapes() -> None:
    """Every malformed layer yields ``None`` instead of raising."""
    assert lookup_in_matrix(None, "PEARS", None) is None
    # The two malformed inputs below are deliberately typed as the
    # static ``dict[str, Any] | None`` the function expects, so mypy's
    # strict check on the contract still holds. At runtime the function
    # inspects the value's shape and returns ``None`` when it is not
    # the expected dict.
    assert lookup_in_matrix(cast("dict[str, Any] | None", []), "PEARS", None) is None
    assert lookup_in_matrix(cast("dict[str, Any] | None", {}), "PEARS", None) is None
    assert lookup_in_matrix({"stations": []}, "PEARS", None) is None
    assert lookup_in_matrix({"stations": {"PEARS": None}}, "PEARS", None) is None

    matrix = {
        "stations": {
            "PEARS": {
                "directions": {"northbound": ["Bray", "", 5, "bray"]},
            }
        }
    }
    # Non-string and blank entries are dropped; dedupe keeps one casing.
    assert lookup_in_matrix(matrix, "PEARS", "Northbound") == ["Bray"]
    # Lookups never fall across direction buckets or unknown stations.
    assert lookup_in_matrix(matrix, "PEARS", "Southbound") is None
    assert lookup_in_matrix(matrix, "TARA", "Northbound") is None


async def test_store_roundtrip_merge_and_persistence(hass: HomeAssistant) -> None:
    """Records merge, skip redundant saves, and persist across instances."""
    store = StopsMatrixStore(hass)

    assert await store.async_record("PEARS", "Northbound", ["Howth"]) is True
    assert await store.async_lookup("PEARS", "Northbound") == ["Howth"]

    with patch.object(store._store, "async_save") as mock_save:
        # Re-recording identical stops must not rewrite storage.
        assert await store.async_record("PEARS", "Northbound", ["Howth"]) is False
        mock_save.assert_not_called()
        # New stops merge into the bucket.
        assert await store.async_record("PEARS", "Northbound", ["Malahide"]) is True

    assert await store.async_lookup("PEARS", "Northbound") == [
        "Howth",
        "Malahide",
    ]
    # Lookups never leak across direction buckets.
    assert await store.async_lookup("PEARS", "Southbound") is None
    # Directionless filters land in the shared ``_all`` bucket.
    assert await store.async_record("TARA", None, ["Dublin Connolly"]) is True
    assert await store.async_lookup("TARA", None) == ["Dublin Connolly"]
    # A separate instance sees the data persisted by the first one.
    fresh = StopsMatrixStore(hass)
    assert await fresh.async_lookup("PEARS", "Northbound") == [
        "Howth",
        "Malahide",
    ]


async def test_store_ignores_empty_observations(hass: HomeAssistant) -> None:
    """Empty observation sets neither save nor create entries."""
    store = StopsMatrixStore(hass)
    with patch.object(store._store, "async_save") as mock_save:
        assert await store.async_record("PEARS", None, []) is False
        mock_save.assert_not_called()


async def test_store_survives_corrupt_storage_file(hass: HomeAssistant) -> None:
    """A corrupt storage file degrades to empty and then recovers."""
    path = Path(hass.config.path(".storage", f"{DOMAIN}.stops_matrix.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # Test setup, not async I/O: writing the corrupt fixture directly is fine.
    path.write_text("{not json", encoding="utf-8")  # noqa: ASYNC240

    store = StopsMatrixStore(hass)
    assert await store.async_lookup("PEARS", None) is None
    # Recording afterwards writes fresh data over the corrupt file.
    assert await store.async_record("PEARS", None, ["Bray"]) is True
    assert await store.async_lookup("PEARS", None) == ["Bray"]


def test_get_stops_store_is_a_per_hass_singleton(hass: HomeAssistant) -> None:
    """Repeated lookups reuse one store instance per hass."""
    assert get_stops_store(hass) is get_stops_store(hass)


async def test_bundled_seed_loads_and_caches() -> None:
    """The shipped seed parses, carries real stations, and loads once."""
    matrix = await async_load_bundled_stops_matrix()
    assert isinstance(matrix, dict)
    assert matrix.get("schema_version")
    assert matrix["stations"]
    assert await async_load_bundled_stops_matrix() is matrix


async def test_bundled_seed_failure_degrades_to_empty_and_caches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Seed read failures warn once and degrade to an empty matrix."""
    with patch.object(ir_store, "_read_bundled_matrix", side_effect=OSError("gone")):
        assert await async_load_bundled_stops_matrix() == {}
        assert "Could not load bundled stops matrix" in caplog.text
    # The empty result is cached: later calls do not re-read or re-warn.
    assert await async_load_bundled_stops_matrix() == {}


def test_lookup_tolerates_broken_direction_layers_and_blank_buckets() -> None:
    """Non-dict direction layers and stop-free buckets yield ``None``."""
    broken_layer = {"stations": {"PEARS": {"directions": "garbage"}}}
    assert lookup_in_matrix(broken_layer, "PEARS", "Northbound") is None

    blank_bucket = {"stations": {"TARA": {"directions": {"northbound": [""]}}}}
    assert lookup_in_matrix(blank_bucket, "TARA", "Northbound") is None

    mixed_bucket = {
        "stations": {
            "KENT": {"directions": {"northbound": [123, None, "", "Cobh"]}}
        }
    }
    assert lookup_in_matrix(mixed_bucket, "KENT", "Northbound") == ["Cobh"]


async def test_store_corrupt_load_exception_degrades_to_empty(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raised load error is warned about once and treated as absent."""
    store = StopsMatrixStore(hass)
    with (
        patch.object(store._store, "async_load", side_effect=ValueError("corrupt")),
        caplog.at_level(logging.WARNING),
    ):
        assert await store.async_lookup("PEARS", None) is None
    assert "Could not load stored stops matrix" in caplog.text
    # The instance continues usable from its degraded empty state.
    assert await store.async_record("PEARS", None, ["Bray"]) is True


async def test_bundled_seed_with_nondict_json_degrades_to_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A seed file holding a JSON array is rejected by validation."""
    # Patch the stdlib ``json.loads`` the store module imported, not
    # ``ir_store.json``: the latter is a mypy-inferred attribute and
    # patching it triggers ``attr-defined`` complaints.
    import json as json_module

    with patch.object(json_module, "loads", return_value=["not", "a", "dict"]):
        assert await async_load_bundled_stops_matrix() == {}
    assert "Could not load bundled stops matrix" in caplog.text
