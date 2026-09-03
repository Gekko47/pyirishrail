"""Persistent and bundled "stops at" matrices.

See docs/architecture.md §13 for matrix learning, storage, seed bundling,
and lookup semantics.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STOPS_MATRIX_FILENAME, STOPS_MATRIX_SEED_FILENAME

_LOGGER = logging.getLogger(__name__)

STOPS_STORE_VERSION = 1

# Bucket holding stops observed without a usable direction value.
ALL_DIRECTIONS_KEY = "_all"

# Key under ``hass.data[DOMAIN]`` for the per-hass singleton store instance.
STOPS_STORE_INSTANCE = "stops_matrix_store"

# Matrix shape: {"stations": {code: {"updated": iso-datetime,
#                                     "directions": {key: [names]}}}}
StopsMatrix = dict[str, Any]


def normalize_direction_key(direction: str | None) -> str:
    """Return the matrix bucket key for a direction filter value."""
    return (direction or "").casefold() or ALL_DIRECTIONS_KEY


def lookup_in_matrix(
    matrix: StopsMatrix | None, station_code: str, direction: str | None
) -> list[str] | None:
    """Return the stops recorded for a station/direction, or ``None``.

    Defensive against malformed data at every level: anything other than the
    expected structure yields ``None`` rather than raising, so a corrupt
    cache or seed can never break configuration. Results are sorted
    case-insensitively for stable dropdown ordering.
    """
    if not isinstance(matrix, dict):
        return None
    stations = matrix.get("stations")
    if not isinstance(stations, dict):
        return None
    entry = stations.get(station_code)
    if not isinstance(entry, dict):
        return None
    directions = entry.get("directions")
    if not isinstance(directions, dict):
        return None
    stops = directions.get(normalize_direction_key(direction))
    if not isinstance(stops, list):
        return None
    # Case-insensitive dedupe with first-seen casing wins, mirroring the
    # API client's union convention.
    seen: dict[str, str] = {}
    for stop in stops:
        if isinstance(stop, str) and stop:
            seen.setdefault(stop.casefold(), stop)
    if not seen:
        return None
    return sorted(seen.values(), key=str.casefold)


class StopsMatrixStore:
    """Merge-and-persist helper for the per-install "stops at" matrix."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store backed by HA's JSON storage."""
        self._hass = hass
        self._store: Store[StopsMatrix] = Store(
            hass, STOPS_STORE_VERSION, f"{DOMAIN}.{STOPS_MATRIX_FILENAME}"
        )
        self._data: StopsMatrix | None = None
        # Serializes read-modify-write across concurrent writers (the
        # coordinator's live learning, the config flow's discovery and the
        # rebuild sweep all record through this store).
        self._record_lock = asyncio.Lock()

    async def _async_ensure_loaded(self) -> StopsMatrix:
        """Load the stored matrix once; tolerate missing or corrupt files."""
        if self._data is None:
            try:
                loaded = await self._store.async_load()
            except (OSError, ValueError) as err:
                # A corrupted storage file must never break configuration;
                # treat it like an absent one and start learning afresh.
                _LOGGER.warning(
                    "Could not load stored stops matrix, starting empty: %s", err
                )
                loaded = None
            self._data = loaded if isinstance(loaded, dict) else {}
        return self._data

    async def async_lookup(
        self, station_code: str, direction: str | None
    ) -> list[str] | None:
        """Return cached stops for a station/direction, or ``None``."""
        data = await self._async_ensure_loaded()
        return lookup_in_matrix(data, station_code, direction)

    async def async_record(
        self, station_code: str, direction: str | None, stops: list[str]
    ) -> bool:
        """Merge observed stops into the matrix, saving only real changes."""
        if not stops:
            return False
        async with self._record_lock:
            data = await self._async_ensure_loaded()
            stations: dict[str, Any] = data.setdefault("stations", {})
            entry: dict[str, Any] = stations.setdefault(station_code, {})
            directions: dict[str, Any] = entry.setdefault("directions", {})
            key = normalize_direction_key(direction)

            existing_raw = directions.get(key)
            existing = (
                {stop for stop in existing_raw if isinstance(stop, str) and stop}
                if isinstance(existing_raw, list)
                else set()
            )
            merged = existing | {stop for stop in stops if stop}
            if merged == existing:
                return False

            directions[key] = sorted(merged, key=str.casefold)
            entry["updated"] = dt_util.utcnow().isoformat()
            await self._store.async_save(data)
            return True


@callback
def get_stops_store(hass: HomeAssistant) -> StopsMatrixStore:
    """Return the per-hass stops-matrix singleton, creating it on demand."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(STOPS_STORE_INSTANCE)
    if not isinstance(store, StopsMatrixStore):
        store = StopsMatrixStore(hass)
        domain_data[STOPS_STORE_INSTANCE] = store
    return store


_SEED_CACHE: StopsMatrix | None = None
# Module-level lock created at import time - eliminates TOCTOU race
# where multiple coroutines could see _SEED_CACHE_LOCK is None simultaneously
_SEED_CACHE_LOCK = asyncio.Lock()


def _get_seed_cache_lock() -> asyncio.Lock:
    """Return the per-process lock guarding the seed cache."""
    return _SEED_CACHE_LOCK


def _read_bundled_matrix() -> StopsMatrix:
    """Read and validate the bundled seed matrix.

    Returns:
        The parsed seed matrix dictionary.

    Raises:
        OSError: If the file cannot be read.
        json.JSONDecodeError: If the file contains invalid JSON.
        TypeError: If the file does not contain a JSON object.
    """
    path = Path(__file__).parent / STOPS_MATRIX_SEED_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        _LOGGER.error(
            "Invalid JSON in bundled stops matrix %s: %s",
            path.name,
            err,
        )
        raise
    if not isinstance(raw, dict):
        raise TypeError(f"{path.name} must contain a JSON object")
    return raw


async def async_load_bundled_stops_matrix() -> StopsMatrix:
    """Load the bundled seed matrix once, degrading to empty on failure."""
    global _SEED_CACHE
    if _SEED_CACHE is None:
        async with _get_seed_cache_lock():
            if _SEED_CACHE is None:
                loop = asyncio.get_running_loop()
                try:
                    _SEED_CACHE = await loop.run_in_executor(None, _read_bundled_matrix)
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as err:
                    _LOGGER.warning(
                        "Could not load bundled stops matrix %s: %s",
                        STOPS_MATRIX_SEED_FILENAME,
                        err,
                    )
                    _SEED_CACHE = {}
    return _SEED_CACHE


@callback
def reset_bundled_seed_cache() -> None:
    """Forget the cached bundled seed so the next load re-reads the file."""
    global _SEED_CACHE
    _SEED_CACHE = None
