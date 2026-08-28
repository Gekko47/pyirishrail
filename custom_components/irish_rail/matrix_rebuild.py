"""Runtime rebuild of the bundled "stops at" matrix (global button job).

Faithful in-process port of ``scripts/build_stops_matrix.py``: every network
station is sampled for currently-due trains, each train code's movement
history is scoped to its current journey and cut downstream of the sampled
station (:func:`IrishRailClient.async_get_train_stops` +
:func:`~custom_components.irish_rail.api._scoped_journey_stops`), and the
resulting stops are unioned per direction bucket plus an ``_all`` union.

Differences from the offline script, by design:

* **Gap-fill merge** - existing buckets and stops in the per-install runtime
  matrix are NEVER removed or replaced; observed stops are unioned in. A
  quiet-hours rebuild therefore cannot erase knowledge the script captured
  at peak time (the script wholesale-replaces because git tracks its history).
* **Two files, two roles.** The bundled, read-only seed lives at
  ``stops_matrix.seed.json`` inside the integration folder; the per-install
  runtime output lives at ``stops_matrix.json`` inside
  ``hass.config.path()``. The runtime file is gitignored so HACS updates
  never clobber a user's rebuild data; the seed is refreshed by HACS
  itself when the upstream bundled matrix improves.
* **Incremental dumps** mirror the script's behaviour so an interrupted run
  leaves a valid partial seed rather than nothing.
* The bundled-seed cache is invalidated afterwards so the next config-flow
  lookup immediately benefits from refreshed route knowledge.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import (
    IrishRailClient,
    IrishRailError,
    TrainMovement,
    _scoped_journey_stops,
)
from .const import (
    DUBLIN_TZ,
    REBUILD_DELAY_SECONDS,
    STOPS_MATRIX_FILENAME,
    STOPS_MATRIX_SEED_FILENAME,
)
from .store import (
    ALL_DIRECTIONS_KEY,
    STOPS_STORE_VERSION,
    normalize_direction_key,
    reset_bundled_seed_cache,
)

_LOGGER = logging.getLogger(__name__)

# Path is supplied at runtime via :func:`async_run_matrix_rebuild`; tests
# patch this symbol to redirect writes into a temp directory.
_MATRIX_PATH: Path | None = None


def _matrix_path(hass: HomeAssistant) -> Path:
    """Return the per-install runtime stops-matrix file.

    The file lives inside ``hass.config.path()`` (a HACS install's
    ``config/``) rather than inside the integration folder, so a HACS
    update that overwrites the integration cannot clobber a user's
    runtime rebuild output. The bundled seed at
    ``stops_matrix.seed.json`` inside the integration folder is the
    immutable baseline; the config-flow lookup merges the two layers
    via :func:`custom_components.irish_rail.store.async_load_bundled_stops_matrix`.
    """
    return Path(hass.config.path(STOPS_MATRIX_FILENAME))


def _seed_path() -> Path:
    """Return the bundled, read-only seed file inside the integration dir."""
    return Path(__file__).parent / STOPS_MATRIX_SEED_FILENAME

_RUNTIME_NOTE = (
    "Snapshot maintained by the Irish Rail integration's rebuild button: "
    "services due at runtime were sampled and merged into existing entries "
    "(gap fill only; previously observed stops are never removed)."
)


@dataclass
class RebuildResult:
    """Outcome of one rebuild run, surfaced on the button's attributes."""

    total_stations: int = 0
    sampled: int = 0
    skipped: int = 0
    buckets_updated: int = 0
    stops_added: int = 0
    started: str = ""
    finished: str = ""
    duration_seconds: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Attribute-friendly representation."""
        out: dict[str, Any] = {
            "total_stations": self.total_stations,
            "stations_sampled": self.sampled,
            "stations_skipped": self.skipped,
            "buckets_updated": self.buckets_updated,
            "stops_added": self.stops_added,
            "started": self.started,
            "finished": self.finished,
            "duration_seconds": round(self.duration_seconds, 1),
        }
        if self.error:
            out["error"] = self.error
        return out


def _empty_document(now_iso: str) -> dict[str, Any]:
    """Skeleton shaped exactly like the script's output."""
    return {
        "schema_version": STOPS_STORE_VERSION,
        "generated": now_iso,
        "note": _RUNTIME_NOTE,
        "stations": {},
    }


def _load_base_document(
    raw_text: str | None, source_name: str, now_iso: str
) -> dict[str, Any]:
    """Parse the on-disk base, degrading corrupt content to a skeleton.

    Runs inside an executor thread; must stay synchronous and pure.
    ``source_name`` appears in the warning log so a user can tell which
    of the seed or runtime file was malformed.
    """
    if raw_text is None:
        return _empty_document(now_iso)
    try:
        parsed = json.loads(raw_text)
    except ValueError:
        _LOGGER.warning(
            "Existing %s is malformed; starting from an empty matrix",
            source_name,
        )
        return _empty_document(now_iso)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("stations"), dict):
        _LOGGER.warning(
            "Existing %s has an unexpected shape; starting from an empty matrix",
            source_name,
        )
        return _empty_document(now_iso)
    parsed["schema_version"] = STOPS_STORE_VERSION
    parsed["generated"] = now_iso
    parsed["note"] = _RUNTIME_NOTE
    return parsed


def _dump_document(document: dict[str, Any], target_path: Path) -> None:
    """Write the seed document to a temp file, then atomically swap it in.

    The serialized JSON goes to a sibling temporary file first so a crash
    mid-write can never leave a truncated ``stops_matrix.json``; the real
    path is replaced only after that write fully succeeds. In either case
    the temporary file is removed afterwards.
    """
    temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, target_path)
    finally:
        # Best-effort cleanup; the target path was already replaced or the
        # temp write failed, so no state is at risk here.
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)


def _merge_station(
    document: dict[str, Any],
    station_code: str,
    sampled_directions: dict[str, set[str]],
    now_iso: str,
) -> tuple[int, int]:
    """Gap-fill merge one station's buckets; return (buckets_touched, stops_added).

    Existing buckets gain stops only; neither stale stops nor whole buckets
    disappear. The station timestamp refreshes whenever anything changed so
    the file stays auditable about where freshness came from.
    """
    stations: dict[str, Any] = document.setdefault("stations", {})
    entry = stations.setdefault(station_code, {})
    if not isinstance(entry, dict):
        entry = {}
        stations[station_code] = entry
    directions_value = entry.setdefault("directions", {})
    if not isinstance(directions_value, dict):
        directions_value = {}
        entry["directions"] = directions_value

    buckets_touched = 0
    stops_added = 0
    changed_any = False
    for key, sampled in sampled_directions.items():
        # Case-insensitive comparison with first-seen casing preserved,
        # mirroring store.lookup_in_matrix's union convention.
        existing_map: dict[str, str] = {}
        existing_raw = directions_value.get(key)
        if isinstance(existing_raw, list):
            for stop in existing_raw:
                if isinstance(stop, str) and stop:
                    existing_map.setdefault(stop.casefold(), stop)
        sampled_map: dict[str, str] = {}
        for stop in sampled:
            if isinstance(stop, str) and stop:
                sampled_map.setdefault(stop.casefold(), stop)
        newly_observed = [
            sampled_map[cased]
            for cased in sampled_map
            if cased not in existing_map
        ]
        if not newly_observed:
            continue
        # Only genuinely-new stops join; existing entries (first-seen
        # casing wins) can never be re-added under a different casing,
        # so repeated rebuilds converge to stable bucket contents.
        merged = sorted([*existing_map.values(), *newly_observed], key=str.casefold)
        directions_value[key] = merged
        buckets_touched += 1
        stops_added += len(newly_observed)
        changed_any = True
    if changed_any:
        entry["updated"] = now_iso
    return buckets_touched, stops_added


async def async_run_matrix_rebuild(
    hass: HomeAssistant, client: IrishRailClient
) -> RebuildResult:
    """Sample the whole network and gap-fill the bundled seed matrix.

    The base document is read from the per-install runtime file (when
    present, so a previous rebuild's data is preserved) and otherwise
    falls back to the bundled ``stops_matrix.seed.json``; the merged
    result is then written back to the per-install runtime file under
    ``hass.config.path()``.
    """
    started = dt_util.utcnow()
    result = RebuildResult(started=started.isoformat())

    loop = asyncio.get_running_loop()
    runtime_path = _matrix_path(hass)
    seed_path = _seed_path()

    def _read_runtime() -> str | None:
        try:
            return runtime_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _read_seed() -> str | None:
        try:
            return seed_path.read_text(encoding="utf-8")
        except OSError:
            return None

    raw_text = await loop.run_in_executor(None, _read_runtime)
    if raw_text is None:
        # First rebuild on a fresh install (or after the runtime file was
        # removed) — start from the bundled seed instead of an empty doc.
        raw_text = await loop.run_in_executor(None, _read_seed)
        source_name = seed_path.name
    else:
        source_name = runtime_path.name
    now_iso = started.isoformat()
    document = await loop.run_in_executor(
        None, _load_base_document, raw_text, source_name, now_iso
    )

    today = dt_util.now(DUBLIN_TZ).strftime("%d %b %Y")
    movement_cache: dict[tuple[str, str], list[TrainMovement]] = {}

    # The heavy-request warning: one prominent WARNING per run plus the same
    # caution in strings/README, per the rebuild-button requirement.
    _LOGGER.warning(
        "Stops-matrix rebuild starting: this samples every Irish Rail "
        "station (~150 polls plus movement lookups) against the public "
        "RTPI API and takes several minutes"
    )

    stations = await client.async_get_all_stations()
    result.total_stations = len(stations)
    for index, station in enumerate(stations):
        try:
            try:
                trains = await client.async_get_station_by_code(station.code)
            except IrishRailError as err:
                result.skipped += 1
                _LOGGER.warning(
                    "[%d/%d] Skipping %s (%s): %s",
                    index + 1,
                    len(stations),
                    station.name,
                    station.code,
                    err,
                )
                continue
            result.sampled += 1
            direction_buckets: dict[str, set[str]] = {}
            for train in trains:
                cache_key = (train.code, today)
                if cache_key not in movement_cache:
                    try:
                        movement_cache[cache_key] = await client.async_get_train_stops(
                            train.code, date=today
                        )
                    except IrishRailError as err:
                        _LOGGER.warning(
                            "Movement lookup failed for %s: %s", train.code, err
                        )
                        movement_cache[cache_key] = []
                journey = _scoped_journey_stops(
                    movement_cache[cache_key],
                    train.destination,
                    station_code=station.code,
                    station_name=station.name,
                )
                stops = {movement.location for movement in journey if movement.location}
                stops.discard(station.name)
                if not stops:
                    continue
                bucket_key = normalize_direction_key(train.direction)
                direction_buckets.setdefault(bucket_key, set()).update(stops)
                direction_buckets.setdefault(ALL_DIRECTIONS_KEY, set()).update(stops)

            buckets_touched, stops_added = _merge_station(
                document, station.code, direction_buckets, now_iso
            )
            result.buckets_updated += buckets_touched
            result.stops_added += stops_added
            summary = (
                f"{buckets_touched} bucket(s) updated, {stops_added} stop(s) added"
                if buckets_touched or stops_added
                else f"{len(direction_buckets)} bucket(s) unchanged"
            )
            _LOGGER.info(
                "[%d/%d] %s (%s): %s",
                index + 1,
                len(stations),
                station.name,
                station.code,
                summary,
            )
            # Incremental dump after every station keeps an interrupted run valid.
            await asyncio.to_thread(_dump_document, document, runtime_path)
        finally:
            # Polite API pacing applies after every station — including ones
            # skipped for an error — so a burst of failures cannot hammer the
            # public endpoint without the usual pause.
            await asyncio.sleep(REBUILD_DELAY_SECONDS)

    await asyncio.to_thread(_dump_document, document, runtime_path)
    reset_bundled_seed_cache()

    finished = dt_util.utcnow()
    result.finished = finished.isoformat()
    result.duration_seconds = (finished - started).total_seconds()
    _LOGGER.info(
        "Stops-matrix rebuild finished in %.1fs: %d/%d stations sampled, "
        "%d skipped, %d bucket(s) updated, %d stop(s) added; bundled seed "
        "cache refreshed",
        result.duration_seconds,
        result.sampled,
        result.total_stations,
        result.skipped,
        result.buckets_updated,
        result.stops_added,
    )
    return result
