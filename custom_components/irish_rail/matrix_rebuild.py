"""Runtime rebuild of the bundled "stops at" matrix (global button job).

Faithful in-process port of ``scripts/build_stops_matrix.py``: every network
station is sampled for currently-due trains, each train code's movement
history is scoped to its current journey and cut downstream of the sampled
station (:func:`IrishRailClient.async_get_train_stops` +
:meth:`IrishRailClient.scope_journey_stops`), and the
resulting stops are unioned per direction bucket plus an ``_all`` union.

Differences from the offline script, by design:

* **Gap-fill merge** - existing stops in the per-install learned matrix
  are NEVER removed or replaced; observed stops are unioned in. A
  quiet-hours rebuild therefore cannot erase knowledge the live learning
  path captured at peak time (the script wholesale-replaces because git
  tracks its history).
* **One writer, one file.** The rebuild routes every observation through
  :class:`~custom_components.irish_rail.store.StopsMatrixStore` — the
  same store the coordinator's ``_async_learn_downstream_stops`` and the
  config flow's live discovery use. The config flow reads back through the
  same store, so a rebuild's output reaches the next config-flow lookup
  without a separate file the lookup would have to discover.
* **Background-priority traffic.** Every outbound HTTP call passes
  ``priority="background"`` to the shared request gate, so a rebuild
  running alongside live polling yields to any queued normal caller and
  never delays the stations the user is actively watching.
* The bundled-seed cache is invalidated afterwards so the next config-flow
  lookup immediately benefits from refreshed bundled-seed data.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DUBLIN_TZ, REBUILD_DELAY_SECONDS
from .pyirishrail import (
    IrishRailClient,
    IrishRailError,
    TrainMovement,
)
from .store import (
    ALL_DIRECTIONS_KEY,
    get_stops_store,
    normalize_direction_key,
    reset_bundled_seed_cache,
)

_LOGGER = logging.getLogger(__name__)

# Rebuild sweeps the whole network; it must never displace live polling
# on a shared gate. The gate's strict-priority rule admits a queued
# "background" caller only when no "normal" caller is waiting, and serves
# background callers in FIFO order otherwise (see RequestGate design notes).
_REBUILD_PRIORITY = "background"


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


async def async_run_matrix_rebuild(
    hass: HomeAssistant, client: IrishRailClient
) -> RebuildResult:
    """Sample the whole network and gap-fill the learned stops matrix.

    Every observed (station, direction, stop) tuple is persisted through
    the shared :class:`StopsMatrixStore`, so the rebuild's output lands
    in the same file the coordinator's live learning and the config
    flow's lookup use. ``StopsMatrixStore.async_record`` is itself a
    gap-fill union (existing entries are preserved, case-insensitive,
    first-seen casing wins) and returns ``False`` on a no-op so a
    re-observation of already-known stops does not rewrite the file.

    The bundled-seed cache is invalidated at the end so the next
    config-flow lookup reads the freshest possible seed even if no
    per-install learning happened during this run.
    """
    started = dt_util.utcnow()
    result = RebuildResult(started=started.isoformat())

    today = dt_util.now(DUBLIN_TZ).strftime("%d %b %Y")
    movement_cache: dict[tuple[str, str], list[TrainMovement]] = {}
    stops_store = get_stops_store(hass)

    # The heavy-request warning: one prominent WARNING per run plus the same
    # caution in strings/README, per the rebuild-button requirement.
    _LOGGER.warning(
        "Stops-matrix rebuild starting: this samples every Irish Rail "
        "station (~150 polls plus movement lookups) against the public "
        "RTPI API and takes several minutes"
    )

    stations = await client.async_get_all_stations(priority=_REBUILD_PRIORITY)
    result.total_stations = len(stations)
    for index, station in enumerate(stations):
        try:
            try:
                trains = await client.async_get_station_by_code(
                    station.code, priority=_REBUILD_PRIORITY
                )
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
                            train.code, date=today, priority=_REBUILD_PRIORITY
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

            # Every bucket for this station goes through the shared store.
            # StopsMatrixStore.async_record is the gap-fill union: existing
            # stops in the bucket are preserved, new ones are unioned in,
            # and a no-op returns False without writing. Persistence
            # failures degrade to a per-station warning so one bad write
            # does not abort the whole sweep.
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
            summary = (
                f"{len(direction_buckets)} bucket(s) sampled"
                if direction_buckets
                else "no due services to sample"
            )
            _LOGGER.info(
                "[%d/%d] %s (%s): %s",
                index + 1,
                len(stations),
                station.name,
                station.code,
                summary,
            )
        finally:
            # Polite API pacing applies after every station — including ones
            # skipped for an error — so a burst of failures cannot hammer the
            # public endpoint without the usual pause.
            await asyncio.sleep(REBUILD_DELAY_SECONDS)

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
