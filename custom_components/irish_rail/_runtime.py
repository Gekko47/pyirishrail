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
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .client import IrishRailClient
from .const import (
    DOMAIN,
    GLOBAL_HEALTH_UNIQUE_ID,
    GLOBAL_LAST_REBUILD_KEY,
    GLOBAL_PROVIDER_KEY,
    GLOBAL_REBUILD_ENTITY_KEY,
    GLOBAL_REBUILD_UNIQUE_ID,
    GLOBAL_SERVICES_IDENTIFIER,
    HEALTH_CHECK_INTERVAL,
    HEALTH_PROBE_STATION_CODE,
    STOPS_STORE_INSTANCE,
)
from .errors import IrishRailError
from .models import TrainMovement
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
        # Providers that unloaded while siblings remained and are waiting
        # to be confirmed removed. See async_promote_on_removal.
        self.pending_promotions: set[str] = set()
        self.request_gate: RequestGate | None = RequestGate()
        self.health_monitor: ConnectivityMonitor | None = None
        # One movement-history cache per Home Assistant instance, handed to
        # every client the integration builds, so a train code serving two
        # monitored stations is fetched once rather than once per entry.
        self.movement_cache: dict[tuple[str, str], list[TrainMovement]] = {}

    @callback
    def ensure_health_monitor(
        self, client: IrishRailClient, *, adopt_client: bool = False
    ) -> ConnectivityMonitor:
        """Return the health monitor, creating it on first call.

        The first-loaded entry's client creates the monitor; later entries
        reuse it. ``adopt_client`` re-points an existing monitor at a new
        client, which the caller sets when no entry owned the monitor a
        moment earlier: a full unload drops the shared gate but keeps the
        monitor, so without the re-point the probe would keep drawing its
        rate budget from the discarded gate. The probe is *not* started
        here.
        """
        if self.health_monitor is None:
            self.health_monitor = ConnectivityMonitor(self.hass, client)
        elif adopt_client and self.health_monitor.client is not client:
            self.health_monitor.rebind_client(client)
        return self.health_monitor

    async def async_release(self) -> None:
        """Release the shared singletons at zero loaded entries.

        The gate is dropped so the next user gets a fresh rate budget.
        The monitor is stopped but its object is kept so a reload
        restarts the same instance and its probe history survives an
        unload/reload cycle.
        """
        self.request_gate = None
        self.movement_cache.clear()
        if self.health_monitor is not None:
            await self.health_monitor.async_stop()
        self.loaded_entry_ids.clear()
        _drop_session_keys(self.hass)
        # The globals exist iff at least one entry is loaded, so the last
        # unload takes the rebuild service and its handle with it. A
        # promotion (owner removed, sibling survives) never reaches here,
        # so the service correctly outlives the original owner.
        from .button import unregister_rebuild_service

        unregister_rebuild_service(self.hass)


# ── Session-scoped key accessors (single writer, see §11) ────────────────────
#
# These four keys live under ``hass.data[DOMAIN]`` but are owned by the
# modules that create them. Routing every access through here keeps the
# registry the only module that touches that mapping, so the unload
# teardown in :meth:`RuntimeRegistry.async_release` provably drains
# everything it needs to.


@callback
def get_session_value(hass: HomeAssistant, key: str) -> Any:
    """Return a session-scoped value, or ``None`` when absent."""
    return hass.data.get(DOMAIN, {}).get(key)


@callback
def set_session_value(hass: HomeAssistant, key: str, value: Any) -> None:
    """Create or replace a session-scoped value."""
    hass.data.setdefault(DOMAIN, {})[key] = value


@callback
def pop_session_value(hass: HomeAssistant, key: str) -> None:
    """Drop a session-scoped value if present."""
    domain_data = hass.data.get(DOMAIN)
    if domain_data is not None:
        domain_data.pop(key, None)


def _drop_session_keys(hass: HomeAssistant) -> None:
    """Remove every session-scoped key."""
    for key in (
        GLOBAL_PROVIDER_KEY,
        GLOBAL_LAST_REBUILD_KEY,
        GLOBAL_REBUILD_ENTITY_KEY,
        STOPS_STORE_INSTANCE,
    ):
        pop_session_value(hass, key)


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

    @callback
    def rebind_client(self, client: IrishRailClient) -> None:
        """Point the probe at a new client, keeping the probe history."""
        self.client = client

    async def async_start(self) -> None:
        """Start the periodic probe; safe to call repeatedly."""
        if self._unsub_interval is not None:
            return
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_tick, HEALTH_CHECK_INTERVAL
        )
        self.schedule_ping()

    async def async_stop(self) -> None:
        """Stop probing, cancel any in-flight ping, and clear listeners."""
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        if self._ping_task is not None:
            self._ping_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None

        # Clear all listeners to prevent memory leaks from entities
        # that hold references to this monitor
        self.listeners.clear()

        # Reset state for clean restart
        self.healthy = None
        self.consecutive_failures = 0

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Add a listener and return a removal callback.

        Args:
            listener: Callback to invoke on state changes.

        Returns:
            A callable that removes the listener when called.
        """
        self.listeners.add(listener)

        def remove_listener() -> None:
            self.listeners.discard(listener)

        return remove_listener

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
                "Irish Rail API health check failed unexpectedly (%d consecutive): %s",
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
            except Exception:  # listeners must never break the notify loop
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


def async_get_movement_cache(
    hass: HomeAssistant,
) -> dict[tuple[str, str], list[TrainMovement]]:
    """Return the per-hass movement-history cache, creating it on demand.

    Shared by every client the integration builds so a train code that
    serves two monitored stations is resolved once. Cleared when the last
    entry unloads, like the request gate.
    """
    return _ensure_runtime(hass).movement_cache


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
    # Sampled before the id was added: an idle registry means this entry is
    # starting from scratch, so the retained monitor should adopt its
    # client rather than keep one bound to the discarded gate.
    monitor = registry.ensure_health_monitor(client, adopt_client=is_first)
    await monitor.async_start()
    return is_first


async def async_note_entry_unloaded(hass: HomeAssistant, entry_id: str) -> bool:
    """Deregister a loaded config entry; return True when none remain.

    At zero loaded entries the registry releases its singletons: the
    shared gate is dropped and the probe is stopped (the monitor object
    itself survives a reload cycle so its probe history is retained; see
    :meth:`RuntimeRegistry.async_release`). The session-scoped keys under
    ``hass.data[DOMAIN]`` (global-entity handle, last rebuild result,
    stops-matrix store singleton) are also dropped to avoid holding
    references after the last entry is gone; each is recreated lazily on
    demand.

    When the departing entry was the global-entity provider but others
    remain, the globals are re-elected onto a survivor so the
    connectivity sensor, the rebuild button and the service survive the
    owner's removal. See docs/architecture.md §11.
    """
    registry = get_runtime(hass)
    if registry is None:
        return True
    registry.loaded_entry_ids.discard(entry_id)
    if not registry.loaded_entry_ids:
        # Release the shared gate and stop the probe. The registry itself
        # (and the monitor object) stay alive so a reload cycle reuses the
        # same monitor instance - pinned by test_runtime.py.
        # async_release also drops the session-scoped keys, so nothing keeps
        # referencing entity objects, results, or cached matrices after the
        # last entry is gone. Every one of those is recreated lazily on the
        # next use, and the stops matrix itself is persisted on disk, so
        # dropping is lossless.
        await registry.async_release()
    elif disown_provider_if(hass, entry_id):
        # The provider left while siblings are still loaded. Disowning
        # alone would leave the globals with nobody, so remember the
        # departed id; ``async_promote_on_removal`` re-elects a
        # survivor when (and only when) the entry is actually removed.
        # Promotion is deliberately *not* attempted here: an unload is
        # also the first half of a reload, and re-electing during that
        # window would hand the globals to a sibling for the duration
        # of a routine reload.
        registry.pending_promotions.add(entry_id)

    return not registry.loaded_entry_ids


@callback
def async_promote_on_removal(hass: HomeAssistant, removed_entry_id: str) -> None:
    """Re-elect the global-entity provider after an entry is removed.

    Driven by ``ConfigEntryChange.REMOVED``, which Home Assistant
    dispatches only once the entry has left the store — so this never
    races a reload. Returns immediately unless the removed entry was a
    provider waiting for a survivor. See docs/architecture.md §11.
    """
    registry = get_runtime(hass)
    if registry is None:
        return
    if removed_entry_id not in registry.pending_promotions:
        return
    registry.pending_promotions.discard(removed_entry_id)
    if get_session_value(hass, GLOBAL_PROVIDER_KEY) is not None:
        # Already re-claimed by the departing entry's own reload.
        return
    hass.async_create_task(
        async_promote_provider(hass),
        name=f"irish_rail_promote_provider_{removed_entry_id}",
        eager_start=False,
    )


async def async_promote_provider(hass: HomeAssistant) -> bool:
    """Re-elect the global-entity provider onto a surviving entry.

    Picks the lowest loaded entry id for determinism, so the same
    survivor wins regardless of unload order. Re-adds the two entities to
    that entry's already-running platforms rather than reloading it, so a
    station that did nothing wrong does not see its own sensors blink out.

    Returns True when a promotion happened.
    """
    if get_session_value(hass, GLOBAL_PROVIDER_KEY) is not None:
        return False
    registry = get_runtime(hass)
    if registry is None or not registry.loaded_entry_ids:
        return False

    survivor_id = min(registry.loaded_entry_ids)
    survivor = hass.config_entries.async_get_entry(survivor_id)
    if survivor is None:
        _LOGGER.warning(
            "Cannot promote the global Irish Rail entities: entry %s is gone",
            survivor_id,
        )
        return False

    set_session_value(hass, GLOBAL_PROVIDER_KEY, survivor_id)
    _LOGGER.info(
        "Promoted the global Irish Rail entities to %s after its owner left",
        survivor.title,
    )

    from .binary_sensor import build_connectivity_sensor
    from .button import build_rebuild_button, register_rebuild_service

    connectivity = build_connectivity_sensor(hass, survivor)
    button = build_rebuild_button(hass, survivor)
    if button is not None:
        set_session_value(hass, GLOBAL_REBUILD_ENTITY_KEY, button)
    register_rebuild_service(hass)
    if button is not None:
        await _async_add_to_platforms(
            hass, survivor, button, Platform.BUTTON
        )
    if connectivity is not None:
        await _async_add_to_platforms(
            hass, survivor, connectivity, Platform.BINARY_SENSOR
        )
    return True


async def _async_add_to_platforms(
    hass: HomeAssistant,
    entry: IrishRailConfigEntry,
    entity: Entity,
    domain: Platform,
) -> None:
    """Add one entity to ``entry``'s live platform for ``domain``.

    Entities are added to the platform rather than rebuilt, so the
    survivor's existing entities are untouched. A platform that is gone
    (the entry is mid-reload) is logged and skipped; the next full setup
    claims and registers normally.
    """
    # ``async_get_platforms`` is keyed by the *integration* name
    # (the config entry's domain) and returns every platform of that
    # integration, so the entity's own platform domain is the filter.
    for platform in async_get_platforms(hass, DOMAIN):
        if platform.config_entry is not entry or platform.domain != domain:
            continue
        await platform.async_add_entities([entity])
        return
    _LOGGER.debug(
        "No live %s platform for %s; skipping promotion of %s",
        domain,
        entry.title,
        entity.entity_id or entity.unique_id,
    )


# ── Global-entity providership arbitration ──────────────────────────────────


@callback
def elect_provider(hass: HomeAssistant, entry: IrishRailConfigEntry) -> bool:
    """Return True when ``entry`` owns the global entities after this call.

    Ownership is *derived* from ``loaded_entry_ids`` rather than cached
    at first claim, so it is self-healing: a departed owner is detected
    because it is no longer in the loaded set. Idempotent, and safe to
    call from any number of entries. See docs/architecture.md §11.
    """
    registry = _ensure_runtime(hass)
    current_owner = get_session_value(hass, GLOBAL_PROVIDER_KEY)

    # Fast path: already the owner, or the entry is not loaded yet (the
    # caller is mid-setup, before async_note_entry_loaded runs).
    if current_owner == entry.entry_id:
        return True

    # A *loaded* owner keeps the globals. The previous check consulted
    # hass.config_entries.async_entries(DOMAIN), which ignores load state
    # and so pinned ownership to a dead entry for the rest of the
    # session; that was the HIGH-1 defect.
    if isinstance(current_owner, str) and current_owner in registry.loaded_entry_ids:
        return False

    # No owner, or the recorded owner is no longer loaded: elect this
    # entry, sweeping any rows the departed owner still holds.
    if isinstance(current_owner, str):
        _purge_orphan_global_entities(hass, expected_owner=current_owner)

    set_session_value(hass, GLOBAL_PROVIDER_KEY, entry.entry_id)
    return True


@callback
def disown_provider_if(hass: HomeAssistant, entry_id: str) -> bool:
    """Clear providership when it names ``entry_id``; report whether it did."""
    if get_session_value(hass, GLOBAL_PROVIDER_KEY) != entry_id:
        return False
    pop_session_value(hass, GLOBAL_PROVIDER_KEY)
    return True


@callback
def _purge_orphan_global_entities(hass: HomeAssistant, *, expected_owner: str) -> None:
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
