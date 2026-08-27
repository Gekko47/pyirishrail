"""Shared Irish Rail RTPI API health monitoring and global-entity wiring.

Two integration-wide features must exist exactly once regardless of how many
station config entries are installed:

* the **connectivity binary sensor** driven by a periodic reachability probe;
* the **stops-matrix rebuild button** (see ``matrix_rebuild.py``).

Both are registered as integration-level service entities (no device, with
``EntityCategory.DIAGNOSTIC`` / ``CONFIG`` respectively) by whichever config
entry *claims* providership first (arbitration below). This module hosts
two concerns:

* :class:`IrishRailApiHealthMonitor` - pings a lightweight endpoint on a
  fixed interval and tracks recent reachability so the coordinator can tell
  "the whole API is down" apart from "this station simply has no services
  scheduled inside the RTPI look-ahead window";
* providership arbitration for the global entities (sticky for the session,
  transferred only when the owning entry is removed outright).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import IrishRailClient, IrishRailError
from .const import (
    DOMAIN,
    GLOBAL_PROVIDER_KEY,
    HEALTH_CHECK_INTERVAL,
    HEALTH_MONITOR_INSTANCE,
    HEALTH_PROBE_STATION_CODE,
)

_LOGGER = logging.getLogger(__name__)


class IrishRailApiHealthMonitor:
    """Probe the RTPI API periodically and remember how it responded.

    A probe failing while station polls keep succeeding (or vice versa) is
    diagnostic gold: consecutive empty-but-successful polls at one station
    mean quiet scheduling, whereas an unhealthy probe means any station-level
    emptiness is just downstream fallout. State starts ``healthy=None``
    ("not yet probed") so consumers conservatively fall back to the legacy
    behaviour until the very first probe has landed.
    """

    def __init__(self, hass: HomeAssistant, client: IrishRailClient) -> None:
        """Initialize the monitor bound to a shared API client."""
        self.hass = hass
        self.client = client
        self._unsub_interval: Callable[[], None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self.healthy: bool | None = None
        self.last_success: datetime | None = None
        self.last_failure: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        self.listeners: set[Callable[[], None]] = set()

    async def async_start(self) -> None:
        """Start the periodic probe; safe to call repeatedly."""
        if self._unsub_interval is not None:
            return
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_tick, HEALTH_CHECK_INTERVAL
        )
        self.schedule_ping()

    async def async_stop(self) -> None:
        """Stop probing and cancel any in-flight initial ping."""
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        if self._ping_task is not None:
            self._ping_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None

    @callback
    def schedule_ping(self) -> None:
        """Fire one probe as a background task, coalescing overlaps."""
        if self._ping_task is not None and not self._ping_task.done():
            return
        self._ping_task = self.hass.async_create_task(self.async_ping())

    async def _async_tick(self, now: datetime) -> None:
        """Interval entry point."""
        await self.async_ping()

    async def async_ping(self) -> None:
        """Run one reachability probe and publish the outcome."""
        try:
            # A single lightweight station poll stands in for reachability:
            # it exercises the same HTTP/XML path as station polling without
            # the full ~155-record station-list payload every interval.
            trains = await self.client.async_get_station_by_code(
                HEALTH_PROBE_STATION_CODE
            )
        except IrishRailError as err:
            self.consecutive_failures += 1
            self.healthy = False
            self.last_failure = dt_util.utcnow()
            self.last_error = str(err)
            _LOGGER.warning(
                "Irish Rail API health check failed (%d consecutive): %s",
                self.consecutive_failures,
                err,
            )
        except Exception as err:
            self.consecutive_failures += 1
            self.healthy = False
            self.last_failure = dt_util.utcnow()
            self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning(
                "Irish Rail API health check failed unexpectedly "
                "(%d consecutive): %s",
                self.consecutive_failures,
                self.last_error,
            )
        else:
            self.consecutive_failures = 0
            self.healthy = True
            self.last_success = dt_util.utcnow()
            self.last_error = None
            _LOGGER.debug(
                "Irish Rail API health check succeeded (%d due trains at %s)",
                len(trains),
                HEALTH_PROBE_STATION_CODE,
            )
        self.notify_listeners()

    @callback
    def notify_listeners(self) -> None:
        """Invoke every registered change listener."""
        for listener in list(self.listeners):
            listener()

    @property
    def recently_confirmed_healthy(self) -> bool:
        """True only once a probe has actually succeeded.

        ``None`` (not yet probed) deliberately reports unhealthy so the
        coordinator's empty-data classification keeps its historical
        conservative behaviour until evidence exists.
        """
        return self.healthy is True and self.consecutive_failures == 0

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot for attributes/diagnostics."""
        return {
            "healthy": self.healthy,
            "last_success": (
                self.last_success.isoformat() if self.last_success else None
            ),
            "last_failure": (
                self.last_failure.isoformat() if self.last_failure else None
            ),
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "interval_minutes": HEALTH_CHECK_INTERVAL.total_seconds() / 60,
        }


# ── Singleton lifecycle ─────────────────────────────────────────────────────

_HEALTH_ENTRY_COUNT_KEY = "health_entry_count"


def get_health_monitor(hass: HomeAssistant) -> IrishRailApiHealthMonitor | None:
    """Return the per-hass health monitor singleton, if it exists."""
    monitor = hass.data.setdefault(DOMAIN, {}).get(HEALTH_MONITOR_INSTANCE)
    if isinstance(monitor, IrishRailApiHealthMonitor):
        return monitor
    return None


def ensure_health_monitor_started(
    hass: HomeAssistant, client: IrishRailClient
) -> IrishRailApiHealthMonitor:
    """Return the running monitor singleton, creating it on first call."""
    monitor = get_health_monitor(hass)
    if monitor is None:
        monitor = IrishRailApiHealthMonitor(hass, client)
        hass.data.setdefault(DOMAIN, {})[HEALTH_MONITOR_INSTANCE] = monitor
    return monitor


async def async_note_entry_loaded(
    hass: HomeAssistant, client: IrishRailClient
) -> None:
    """Account for one more loaded config entry (starts the monitor)."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    count = int(domain_data.get(_HEALTH_ENTRY_COUNT_KEY, 0)) + 1
    domain_data[_HEALTH_ENTRY_COUNT_KEY] = count
    monitor = ensure_health_monitor_started(hass, client)
    await monitor.async_start()


async def async_note_entry_unloaded(hass: HomeAssistant) -> None:
    """Account for one fewer loaded config entry (stops at zero).

    A partial platform-unload failure still counts down here: the entry is
    leaving anyway, and any automatic retry re-runs ``async_setup_entry``,
    which starts a fresh monitor.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    remaining = max(0, int(domain_data.get(_HEALTH_ENTRY_COUNT_KEY, 0)) - 1)
    domain_data[_HEALTH_ENTRY_COUNT_KEY] = remaining
    if remaining == 0:
        monitor = get_health_monitor(hass)
        if monitor is not None:
            await monitor.async_stop()


# ── Global-entity providership arbitration ──────────────────────────────────


@callback
def async_claim_global_provider(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Claim (once per session) providership of the global entities.

    Returns ``True`` when ``entry`` should add them. The first successful
    claim sticks until the owning entry disappears entirely or HA restarts:
    entity-registry rows permanently reference their original config entry,
    so transferring mid-session would duplicate or rename entities instead.
    Unloading the owner therefore hides the globals temporarily (documented
    behaviour), while removing it frees the claim for the next setup.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    current_owner = domain_data.get(GLOBAL_PROVIDER_KEY)
    if current_owner == entry.entry_id:
        return True
    if isinstance(current_owner, str):
        owner_still_installed = any(
            candidate.entry_id == current_owner
            for candidate in hass.config_entries.async_entries(DOMAIN)
        )
        if owner_still_installed:
            return False
    domain_data[GLOBAL_PROVIDER_KEY] = entry.entry_id
    return True
