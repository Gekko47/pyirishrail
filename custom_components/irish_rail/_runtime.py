"""Runtime registry for the Irish Rail integration's shared singletons.

The per-HA :class:`RuntimeRegistry` is the single writer to the
integration's shared state (loaded-entry set, shared request gate,
API-health monitor), so the singleton lifecycles are structural rather
than by-convention. Module-level functions are thin delegates onto
``get_runtime(hass)``. See docs/architecture.md §2, §3 and §11.
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

from .client import IrishRailClient
from .const import (
    DOMAIN,
    GLOBAL_HEALTH_UNIQUE_ID,
    GLOBAL_PROVIDER_KEY,
    GLOBAL_REBUILD_UNIQUE_ID,
    GLOBAL_SERVICES_IDENTIFIER,
    HEALTH_CHECK_INTERVAL,
    HEALTH_PROBE_STATION_CODE,
)
from .errors import IrishRailError
from .request_gate import RequestGate
from .types import IrishRailConfigEntry

_LOGGER = logging.getLogger(__name__)

# Key under ``hass.data[DOMAIN]`` holding the per-HA registry singleton.
_RUNTIME_KEY = "runtime"

# Unique IDs the global entities register under; cleared from the entity
# registry when providership transfers to a new owner so the new entry's
# ``async_add_entities`` does not collide with a stale orphan row.
_GLOBAL_UNIQUE_IDS = (GLOBAL_HEALTH_UNIQUE_ID, GLOBAL_REBUILD_UNIQUE_ID)


class RuntimeRegistry:
    """Registry holding the integration's shared singletons.

    Owns the loaded-entry set, the shared :class:`RequestGate` and the
    :class:`ConnectivityMonitor`; no other code writes to them.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the registry with a fresh gate and no monitor."""
        self.hass = hass
        self.loaded_entry_ids: set[str] = set()
        self.request_gate: RequestGate | None = RequestGate()
        self.health_monitor: ConnectivityMonitor | None = None

    @callback
    def ensure_health_monitor(
        self, client: IrishRailClient
    ) -> ConnectivityMonitor:
        """Return the health monitor, creating it on first call.

        Idempotent: the first-loaded entry's client wins and later
        entries reuse the same monitor regardless of their own client.
        The probe is *not* started here.
        """
        if self.health_monitor is None:
            self.health_monitor = ConnectivityMonitor(self.hass, client)
        return self.health_monitor

    async def async_release(self) -> None:
        """Release the shared singletons at zero loaded entries.

        The gate is dropped so the next user gets a fresh rate budget.
        The monitor is stopped but its object is kept so a reload
        restarts the same instance and its probe history survives an
        unload/reload cycle.
        """
        self.request_gate = None
        if self.health_monitor is not None:
            await self.health_monitor.async_stop()
class ConnectivityMonitor:
    """Probe the RTPI API periodically and remember how it responded.

    See docs/architecture.md §11 for the probe contract and the
    ``healthy: bool | None`` initial state.
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
        """Invoke every registered change listener.

        Each listener is guarded so a single misbehaving listener cannot
        abort the loop for the others or propagate out of ``async_ping()``
        (which is reached via a fire-and-forget ``hass.async_create_task()``).
        """
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 — listeners must never break the notify loop
                _LOGGER.warning(
                    "Irish Rail health monitor listener %s raised; skipping",
                    listener,
                    exc_info=True,
                )

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
@callback
def get_runtime(hass: HomeAssistant) -> RuntimeRegistry | None:
    """Return the per-HA runtime registry if one exists (read-only)."""
    registry = hass.data.get(DOMAIN, {}).get(_RUNTIME_KEY)
    return registry if isinstance(registry, RuntimeRegistry) else None


@callback
def _ensure_runtime(hass: HomeAssistant) -> RuntimeRegistry:
    """Return the per-HA registry, creating it on first call."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    registry = domain_data.get(_RUNTIME_KEY)
    if not isinstance(registry, RuntimeRegistry):
        registry = RuntimeRegistry(hass)
        domain_data[_RUNTIME_KEY] = registry
    return registry


# ── Gate accessors (thin delegates onto the registry) ───────────────────────


def get_request_gate(hass: HomeAssistant) -> RequestGate | None:
    """Return the per-hass shared request gate, if it exists.

    ``None`` is returned when no entry has created one yet (e.g. before
    the first config entry is loaded, or after the last one has been
    unloaded and the registry released it).
    """
    registry = get_runtime(hass)
    if registry is None or registry.request_gate is None:
        return None
    return registry.request_gate


def async_get_request_gate(hass: HomeAssistant) -> RequestGate:
    """Return the per-hass shared request gate, creating it on first call.

    Idempotent: every ``IrishRailClient`` the integration constructs is
    wired to the same gate, so the public-API rate budget is shared.
    """
    registry = _ensure_runtime(hass)
    if registry.request_gate is None:
        registry.request_gate = RequestGate()
    return registry.request_gate


def async_release_request_gate(hass: HomeAssistant) -> None:
    """Drop the per-hass shared request gate if present.

    A subsequent :func:`async_get_request_gate` creates a fresh gate,
    which is cheap (the gate's only state is an ``asyncio.Lock`` and
    two counters initialised on first acquire).
    """
    registry = get_runtime(hass)
    if registry is not None:
        registry.request_gate = None
# ── Health-monitor accessors (thin delegates onto the registry) ─────────────


def get_health_monitor(hass: HomeAssistant) -> ConnectivityMonitor | None:
    """Return the per-hass health monitor singleton, if it exists."""
    registry = get_runtime(hass)
    if registry is None or registry.health_monitor is None:
        return None
    return registry.health_monitor


def ensure_health_monitor_started(
    hass: HomeAssistant, client: IrishRailClient
) -> ConnectivityMonitor:
    """Return the health monitor singleton, creating it on first call."""
    return _ensure_runtime(hass).ensure_health_monitor(client)


# ── Loaded-entry lifecycle (single source of truth, see §11) ────────────────


async def async_note_entry_loaded(
    hass: HomeAssistant, entry_id: str, client: IrishRailClient
) -> bool:
    """Register a loaded config entry; return True when it is the first."""
    registry = _ensure_runtime(hass)
    is_first = not registry.loaded_entry_ids
    registry.loaded_entry_ids.add(entry_id)
    monitor = registry.ensure_health_monitor(client)
    await monitor.async_start()
    return is_first


async def async_note_entry_unloaded(
    hass: HomeAssistant, entry_id: str
) -> bool:
    """Deregister a loaded config entry; return True when none remain.

    At zero loaded entries the registry releases its singletons: the
    shared gate is dropped and the probe is stopped (the monitor object
    itself survives a reload cycle; see :meth:`RuntimeRegistry.async_release`).
    """
    registry = get_runtime(hass)
    if registry is None:
        return True
    registry.loaded_entry_ids.discard(entry_id)
    if not registry.loaded_entry_ids:
        await registry.async_release()
    return not registry.loaded_entry_ids


# ── Global-entity providership arbitration ──────────────────────────────────


@callback
def claim_service_entities(
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
