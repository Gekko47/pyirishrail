"""Shared Irish Rail RTPI API health monitoring and global-entity wiring.

See docs/architecture.md §11 for probe semantics, ping coalescing, the
singleton lifecycle, and providership arbitration.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    GLOBAL_HEALTH_UNIQUE_ID,
    GLOBAL_PROVIDER_KEY,
    GLOBAL_REBUILD_UNIQUE_ID,
    GLOBAL_SERVICES_IDENTIFIER,
    HEALTH_CHECK_INTERVAL,
    HEALTH_MONITOR_INSTANCE,
    HEALTH_PROBE_STATION_CODE,
)
from .pyirishrail import IrishRailClient, IrishRailError
from .types import IrishRailConfigEntry

_LOGGER = logging.getLogger(__name__)

# Unique IDs the global entities register under; cleared from the entity
# registry when providership transfers to a new owner so the new entry's
# ``async_add_entities`` does not collide with a stale orphan row.
_GLOBAL_UNIQUE_IDS = (GLOBAL_HEALTH_UNIQUE_ID, GLOBAL_REBUILD_UNIQUE_ID)


class IrishRailApiHealthMonitor:
    """Probe the RTPI API periodically and remember how it responded.

    See docs/architecture.md §11 for the probe contract and ``healthy: bool | None``
    initial state.
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
            # See docs/architecture.md §11 for why HEALTH_PROBE_STATION_CODE.
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
        except Exception as err:  # noqa: BLE001 - the probe must survive any unexpected failure
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
            "timer_active": self._unsub_interval is not None,
            "probe_in_flight": (
                self._ping_task is not None and not self._ping_task.done()
            ),
        }


# ── Singleton lifecycle ─────────────────────────────────────────────────────

# Single source of truth for shared singleton lifetime (see docs/architecture.md §11).
LOADED_ENTRY_IDS_KEY = "loaded_entry_ids"


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
    hass: HomeAssistant, entry_id: str, client: IrishRailClient
) -> bool:
    """Register a loaded config entry; return True when it is the first."""
    loaded: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault(
        LOADED_ENTRY_IDS_KEY, set()
    )
    is_first = not loaded
    loaded.add(entry_id)
    monitor = ensure_health_monitor_started(hass, client)
    await monitor.async_start()
    return is_first


async def async_note_entry_unloaded(
    hass: HomeAssistant, entry_id: str
) -> bool:
    """Deregister a loaded config entry; return True when none remain."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    loaded: set[str] = domain_data.get(LOADED_ENTRY_IDS_KEY, set())
    loaded.discard(entry_id)
    if not loaded:
        monitor = get_health_monitor(hass)
        if monitor is not None:
            await monitor.async_stop()
    return not loaded


# ── Global-entity providership arbitration ──────────────────────────────────


@callback
def async_claim_global_provider(
    hass: HomeAssistant, entry: IrishRailConfigEntry
) -> bool:
    """Claim providership of the global entities (see docs/architecture.md §11)."""
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
        # Wipe orphan entity rows from previous dead owner before re-claiming.
        _purge_orphan_global_entities(hass, expected_owner=current_owner)
    domain_data[GLOBAL_PROVIDER_KEY] = entry.entry_id
    return True


@callback
def _purge_orphan_global_entities(
    hass: HomeAssistant, *, expected_owner: str
) -> None:
    """Remove global entity rows and device still pinned to a removed config entry."""
    entity_registry = er.async_get(hass)
    for unique_id in _GLOBAL_UNIQUE_IDS:
        entity_id = entity_registry.async_get_entity_id(DOMAIN, DOMAIN, unique_id)
        if entity_id is None:
            continue
        registry_entry = entity_registry.entities.get(entity_id)
        if registry_entry is None or registry_entry.config_entry_id != expected_owner:
            continue
        _LOGGER.info(
            "Removing orphan global entity %s (config_entry_id=%s)",
            entity_id,
            expected_owner,
        )
        entity_registry.async_remove(entity_id)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_device_by_identifier(
        GLOBAL_SERVICES_IDENTIFIER, expected_owner
    )
    if device_entry is not None and device_entry.config_entry_id == expected_owner:
        _LOGGER.info(
            "Removing orphan Irish Rail Services device %s (config_entry_id=%s)",
            device_entry.id,
            expected_owner,
        )
        device_registry.async_remove_device(device_entry.id)
