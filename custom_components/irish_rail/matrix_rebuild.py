"""Runtime rebuild of the bundled "stops at" matrix (global button job).

Every network station is sampled for currently-due trains, each train code's
movement history is scoped to its current journey and cut downstream of the
sampled station (:func:`IrishRailClient.async_get_train_stops` +
:meth:`IrishRailClient.scope_journey_stops`), and the resulting stops are
unioned per direction bucket plus an ``_all`` union.

The core loop is :func:`sample_stops_matrix`, which supports two output modes
selected by flags:

* **gap_fill=True** - union stops into the running
  :class:`~custom_components.irish_rail.store.StopsMatrixStore`. Existing
  stops are never removed; a quiet-hours rebuild cannot erase knowledge the
  live learning path captured at peak time.
* **gap_fill=False, atomic_dump=True** - build a seed document and write it
  atomically to a file. Used by the offline seed script.

The runtime rebuild button uses ``gap_fill=True, atomic_dump=False,
priority="background"``; the offline seed script uses ``gap_fill=False,
atomic_dump=True, priority="normal"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .client import IrishRailClient
from .const import DUBLIN_TZ, REBUILD_DELAY_SECONDS
from .errors import IrishRailError
from .models import TrainMovement
from .store import (
    ALL_DIRECTIONS_KEY,
    STOPS_STORE_VERSION,
    get_stops_store,
    normalize_direction_key,
    reset_bundled_seed_cache,
)

_LOGGER = logging.getLogger(__name__)


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



def _dump_document(output: Path, document: dict[str, Any]) -> None:
    """Write the seed document to a temp file, then atomically swap it in.

    The serialized JSON goes to a sibling temporary file first so an
    interrupted (mid-write) run never leaves a truncated seed; the real
    path is replaced only after that write fully succeeds. In either case
    the temporary file is removed afterwards.
    """
    temp_path = output.with_suffix(output.suffix + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, output)
    finally:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)


async def sample_stops_matrix(
    client: IrishRailClient,
    *,
    gap_fill: bool,
    atomic_dump: bool,
    priority: str,
    hass: HomeAssistant | None = None,
    delay: float = REBUILD_DELAY_SECONDS,
    limit: int | None = None,
    output_path: Path | None = None,
) -> RebuildResult:
    """Sample every station and build the stops matrix.

    Two output modes, selected by flags:

    * ``gap_fill=True`` - union stops into the running
      :class:`~custom_components.irish_rail.store.StopsMatrixStore`.
      ``hass`` is required.
    * ``gap_fill=False, atomic_dump=True`` - build a seed document and
      write it atomically to ``output_path``.
    * ``gap_fill=False, atomic_dump=False`` - build the document in memory
      only (useful for testing the sampling logic without I/O).

    ``priority`` is the request-gate priority for outbound HTTP calls.
    ``delay`` is the seconds to sleep between stations. ``limit`` samples
    only the first N stations (smoke testing).
    """
    if gap_fill and hass is None:
        raise ValueError("hass is required when gap_fill=True")
    if atomic_dump and output_path is None:
        raise ValueError("output_path is required when atomic_dump=True")

    started = dt_util.utcnow()
    result = RebuildResult(started=started.isoformat())

    try:
        stations = await client.async_get_all_stations(priority=priority)
    except IrishRailError as err:
        result.error = f"Could not fetch station list: {err}"
        _LOGGER.error(result.error)
        return result

    result.total_stations = len(stations)
    if limit is not None:
        stations = stations[:limit]

    today = dt_util.now(DUBLIN_TZ).strftime("%d %b %Y")
    movement_cache: dict[tuple[str, str], list[TrainMovement]] = {}

    if gap_fill:
        assert hass is not None  # guarded by ValueError above
        stops_store = get_stops_store(hass)
    else:
        stops_store = None
    document: dict[str, Any] | None = None
    if not gap_fill:
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        document = {
            "schema_version": STOPS_STORE_VERSION,
            "generated": now_iso,
            "note": (
                "Snapshot generated by sample_stops_matrix from services "
                "due at generation time; refreshed opportunistically at "
                "runtime by the integration."
            ),
            "stations": {},
        }

    for index, station in enumerate(stations):
        direction_buckets: dict[str, set[str]] = {}
        try:
            trains = await client.async_get_station_by_code(station.code, priority=priority)
        except IrishRailError as err:
            _LOGGER.warning(
                "Skipping %s (%s): %s", station.name, station.code, err
            )
            result.skipped += 1
        else:
            for train in trains:
                cache_key = (train.code, today)
                if cache_key not in movement_cache:
                    try:
                        movement_cache[cache_key] = await client.async_get_train_stops(
                            train.code, date=today, priority=priority
                        )
                    except IrishRailError as err:
                        _LOGGER.warning(
                            "Movement lookup failed for %s: %s", train.code, err
                        )
                        movement_cache[cache_key] = []
                journey = client.scope_journey_stops(
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

            result.sampled += 1
            if direction_buckets:
                if stops_store is not None:
                    for key, sampled in direction_buckets.items():
                        try:
                            changed = await stops_store.async_record(
                                station.code, key, sorted(sampled)
                            )
                        except Exception:
                            _LOGGER.warning(
                                "Could not persist sampled stops for %s (%s, %s)",
                                station.name,
                                station.code,
                                key,
                                exc_info=True,
                            )
                            continue
                        if changed:
                            result.buckets_updated += 1
                            result.stops_added += len(sampled)
                elif document is not None:
                    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
                    document["stations"][station.code] = {
                        "updated": now_iso,
                        "directions": {
                            key: sorted(stops, key=str.casefold)
                            for key, stops in sorted(direction_buckets.items())
                        },
                    }
                    result.buckets_updated += len(direction_buckets)
                    result.stops_added += sum(len(s) for s in direction_buckets.values())

                summary = f"{len(direction_buckets)} bucket(s) sampled"
            else:
                summary = "no due services to sample"

            _LOGGER.info(
                "[%d/%d] %s (%s): %s",
                index + 1,
                len(stations),
                station.name,
                station.code,
                summary,
            )

            if atomic_dump and document is not None and output_path is not None:
                await asyncio.to_thread(_dump_document, output_path, document)

        finally:
            await asyncio.sleep(delay)


    finished = dt_util.utcnow()
    result.finished = finished.isoformat()
    result.duration_seconds = (finished - started).total_seconds()
    return result


async def async_run_matrix_rebuild(
    hass: HomeAssistant, client: IrishRailClient
) -> RebuildResult:
    """Run the in-process stops-matrix rebuild (runtime button path).

    Wraps :func:`sample_stops_matrix` with gap-fill mode and background
    priority, then invalidates the bundled-seed cache so the next
    config-flow lookup immediately benefits from refreshed data.
    """
    _LOGGER.warning(
        "Stops-matrix rebuild starting: this samples every Irish Rail "
        "station (~150 polls plus movement lookups) against the public "
        "RTPI API and takes several minutes"
    )
    result = await sample_stops_matrix(
        client,
        gap_fill=True,
        atomic_dump=False,
        priority="background",
        hass=hass,
    )
    reset_bundled_seed_cache()
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
