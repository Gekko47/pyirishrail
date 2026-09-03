"""Tests for the persistent and bundled "stops at" matrices."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

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


async def test_concurrent_records_serialize_and_preserve_every_stop(
    hass: HomeAssistant,
) -> None:
    """Concurrent ``async_record`` writers serialize on the record lock.

    The coordinator's live learning, the config flow's discovery and the
    rebuild sweep can all record through one store concurrently; each merge
    must observe the previous merge's result and every recorded stop must
    survive in the final matrix.
    """
    store = StopsMatrixStore(hass)

    async def record(index: int) -> bool:
        return await store.async_record(
            f"S{index:02d}", "Northbound", [f"Stop {index}", "Shared"]
        )

    results = await asyncio.gather(*(record(i) for i in range(25)))

    assert all(results)
    for index in range(25):
        assert await store.async_lookup(f"S{index:02d}", "Northbound") == [
            "Shared",
            f"Stop {index}",
        ]


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
    path.write_text("{not json", encoding="utf-8")

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


async def test_bundled_seed_concurrent_loads_share_one_disk_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent loaders must not duplicate the disk read.

    Regression test for the check-then-set race in
    :func:`async_load_bundled_stops_matrix`: before the
    ``asyncio.Lock`` guard, two coroutines arriving while the cache
    was empty would both see ``_SEED_CACHE is None`` and both read
    the file. The lock collapses N concurrent arrivals into a
    single read; the remaining arrivals reuse the cached object.

    The test holds the patched read inside a barrier that waits
    until every concurrent caller has entered the read path, so
    the race window is wide enough to fire deterministically
    without the lock. With the lock in place, the second and
    later arrivals wait at ``async with`` and never re-enter the
    read; without the lock, all N see the empty cache and all N
    increment the counter.
    """
    import asyncio

    import custom_components.irish_rail.store as store_module

    # Hermetic reset: a prior test in the session may have populated
    # the cache and instantiated the lock.
    monkeypatch.setattr(store_module, "_SEED_CACHE", None)
    # Don't reset _SEED_CACHE_LOCK - it's now a module-level constant lock
    # that should not be modified between tests

    n_callers = 10
    started = 0
    calls = 0

    def counting_read() -> ir_store.StopsMatrix:
        nonlocal started, calls
        calls += 1
        started += 1
        # Block until every concurrent caller has reached this point.
        # ``asyncio.Event`` is not safe across threads, so we busy-wait
        # on a plain int — the read runs in an executor thread that
        # has no event loop.
        import time

        deadline = time.monotonic() + 5.0
        while started < n_callers and time.monotonic() < deadline:
            time.sleep(0.001)
        return {
            "schema_version": ir_store.STOPS_STORE_VERSION,
            "stations": {},
        }

    monkeypatch.setattr(store_module, "_read_bundled_matrix", counting_read)

    results = await asyncio.gather(
        *(async_load_bundled_stops_matrix() for _ in range(n_callers))
    )

    # The lock collapsed N arrivals into exactly one read.
    assert calls == 1, f"expected 1 read, got {calls}"
    # And every caller got the same object — the second arrival
    # waited for the first to publish, then returned its result.
    first = results[0]
    assert all(result is first for result in results)


def test_lookup_tolerates_broken_direction_layers_and_blank_buckets() -> None:
    """Non-dict direction layers and stop-free buckets yield ``None``."""
    broken_layer = {"stations": {"PEARS": {"directions": "garbage"}}}
    assert lookup_in_matrix(broken_layer, "PEARS", "Northbound") is None

    blank_bucket = {"stations": {"TARA": {"directions": {"northbound": [""]}}}}
    assert lookup_in_matrix(blank_bucket, "TARA", "Northbound") is None

    mixed_bucket = {
        "stations": {"KENT": {"directions": {"northbound": [123, None, "", "Cobh"]}}}
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


async def test_bundled_seed_invalid_json_degrades_to_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A seed file containing syntactically invalid JSON degrades safely.

    A HACS update can momentarily leave the bundled seed truncated or
    half-written; ``json.loads`` then raises ``JSONDecodeError``. The
    loader must log the parse failure explicitly and degrade to an empty
    matrix (integration keeps working) instead of crashing setup.
    """
    import json as json_module

    with (
        patch.object(
            json_module,
            "loads",
            side_effect=json_module.JSONDecodeError("Expecting value", "{", 0),
        ),
        caplog.at_level(logging.WARNING),
    ):
        assert await async_load_bundled_stops_matrix() == {}

    # The decode error is logged distinctly from the generic load warning
    # so a corrupted bundle is diagnosable from the log alone.
    assert "Invalid JSON in bundled stops matrix" in caplog.text
    assert "Could not load bundled stops matrix" in caplog.text
    # The degraded-empty result is cached like any successful load.
    assert await async_load_bundled_stops_matrix() == {}


def test_seed_cache_lock_is_a_stable_module_level_singleton() -> None:
    """The seed-cache guard is one module-level lock, never lazily rebuilt.

    The previous lazily-initialised lock had a check-then-set race: two
    coroutines arriving while ``_SEED_CACHE_LOCK`` was ``None`` each built
    their own lock and both read the seed file. The lock is now created
    once at import time; ``_get_seed_cache_lock`` must hand back that
    exact instance on every call.
    """
    assert isinstance(ir_store._SEED_CACHE_LOCK, asyncio.Lock)
    assert ir_store._get_seed_cache_lock() is ir_store._SEED_CACHE_LOCK
    assert ir_store._get_seed_cache_lock() is ir_store._get_seed_cache_lock()
