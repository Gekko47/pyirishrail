"""Global button to rebuild the bundled "stops at" matrix at runtime.

Integration-level service entity registered exactly once per Home
Assistant session by whichever config entry claims providership first
(see ``health.py``). One press samples the whole network in-process
(a port of ``scripts/build_stops_matrix.py`` merged gap-fill style
into the per-install ``stops_matrix.json`` under
``.storage/``) and refreshes the bundled-seed cache, all
without a Home Assistant restart. The ``CONFIG`` entity category
keeps it out of primary UI surfaces so per-station devices never
have to carry it. The entity is attached to a fixed "Irish Rail
Services" device (shared with the API connectivity binary sensor)
so the two integration-level entities appear together on a single
device card rather than as unattached rows in the Entities tab.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import (
    async_create as pn_create,
)
from homeassistant.components.persistent_notification import (
    async_dismiss as pn_dismiss,
)

# The EntityCategory enum is added by Home Assistant itself but the
# typeshed stub does not re-export it, so a plain import trips mypy.
# ``EntityCategory`` lives in ``homeassistant.const`` in modern HA; the
# typeshed re-exports it from there but not from ``homeassistant.helpers.entity``.
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._runtime import (
    async_claim_global_provider,
    get_health_monitor,
)
from .client import IrishRailClient
from .const import (
    DOMAIN,
    GLOBAL_LAST_REBUILD_KEY,
    GLOBAL_REBUILD_UNIQUE_ID,
    GLOBAL_SERVICES_DEVICE_NAME,
    GLOBAL_SERVICES_IDENTIFIER,
)
from .matrix_rebuild import RebuildResult, async_run_matrix_rebuild
from .types import IrishRailConfigEntry, IrishRailRuntimeData

_LOGGER = logging.getLogger(__name__)

# Automation-facing alias for pressing this button programmatically;
# registered alongside the entity so either route works identically.
SERVICE_REBUILD = "rebuild_stops_matrix"

# Stable notification id so a rebuild that fires while another is still
# running updates the same notification rather than piling up a stack of
# "rebuild started" toasts.
REBUILD_NOTIFICATION_ID = "irish_rail_stops_matrix_rebuild"

# The on-disk key the service handler reaches the live button through.
GLOBAL_REBUILD_ENTITY_KEY = "global_rebuild_entity"

_UNSET_ATTRIBUTES: dict[str, Any] = {"status": "never run since startup"}


def _create_notification(
    hass: HomeAssistant, message: str, *, title: str = "Irish Rail"
) -> None:
    """Create (or refresh) the rebuild persistent notification."""
    pn_create(
        hass,
        message,
        title=title,
        notification_id=REBUILD_NOTIFICATION_ID,
    )


def _dismiss_notification(hass: HomeAssistant) -> None:
    """Dismiss the rebuild persistent notification, if it is still up."""
    pn_dismiss(hass, REBUILD_NOTIFICATION_ID)


class IrishRailRebuildStopsMatrixButton(ButtonEntity):
    """Runs one in-process stops-matrix rebuild when pressed.

    Pressing schedules the network-wide sampling job in the background and
    returns immediately; progress and outcomes land in the state attributes.
    A press while another rebuild is still running is ignored (with a
    warning log) rather than queued, so duplicate heavy request storms can
    never stack against the public API.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "rebuild_stops_matrix"
    _attr_unique_id = GLOBAL_REBUILD_UNIQUE_ID
    _attr_entity_category = EntityCategory.CONFIG
    # The rebuild button and the API connectivity binary sensor share
    # a single fixed-identifier service device so they render together
    # on the integration page (see ``health.py`` for the matching
    # orphan-purge on ownership transfer).
    _attr_device_info = DeviceInfo(
        identifiers={GLOBAL_SERVICES_IDENTIFIER},
        name=GLOBAL_SERVICES_DEVICE_NAME,
        manufacturer="Iarnród Éireann / Irish Rail",
        model="RTPI integration services",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://api.irishrail.ie",
    )

    def __init__(self, hass: HomeAssistant, client: IrishRailClient) -> None:
        """Initialize the rebuild button."""
        self.hass = hass
        self._client = client
        self._lock = asyncio.Lock()
        self.running = False
        self.last_result: RebuildResult | None = None

    @property
    def available(self) -> bool:
        """Return ``False`` while a rebuild is in flight.

        Greys out the button in the UI to give the user immediate visual
        feedback that a press is already being processed; without this,
        the only signal is the ``status: "running"`` attribute which
        requires opening the entity.
        """
        return not self.running

    async def async_press(self) -> None:
        """Handle a press: run one guarded rebuild to completion."""
        if self.running or self._lock.locked():
            _LOGGER.warning(
                "Stops-matrix rebuild is already running; ignoring this press"
            )
            raise RuntimeError(
                "The Irish Rail stops-matrix rebuild is already running"
            )
        async with self._lock:
            self.running = True
            _create_notification(
                self.hass,
                (
                    "Sampling every Irish Rail station in the background to "
                    "refresh the bundled \"stops at\" matrix. The job takes "
                    "a few minutes; this notification will update when it "
                    "finishes. Live progress is in the button's state "
                    "attributes."
                ),
                title="Irish Rail · stops-matrix rebuild started",
            )
            try:
                self.last_result = None
                self._write_state_if_added()
                self.last_result = await async_run_matrix_rebuild(
                    self.hass, self._client
                )
            except Exception as err:  # button must never crash Home Assistant
                _LOGGER.exception("Stops-matrix rebuild failed")
                self.last_result = RebuildResult(error=f"{type(err).__name__}: {err}")
                _create_notification(
                    self.hass,
                    (
                        f"Stops-matrix rebuild failed: {err}. See "
                        f"`home-assistant.log` for the full traceback."
                    ),
                    title="Irish Rail · stops-matrix rebuild failed",
                )
                raise
            else:
                result = self.last_result
                # ``async_run_matrix_rebuild`` always returns a real
                # ``RebuildResult``; the ``None`` guard exists so direct
                # unit tests that patch the function with a side effect
                # (without a return value) do not crash the toast.
                if result is not None:
                    summary = (
                        f"{result.stops_added} stop(s) added across "
                        f"{result.buckets_updated} bucket(s); "
                        f"{result.sampled}/{result.total_stations} "
                        f"stations sampled in {result.duration_seconds:.1f}s."
                    )
                else:
                    summary = "no result recorded (test stub?)"
                _create_notification(
                    self.hass,
                    f"Stops-matrix rebuild finished: {summary}",
                    title="Irish Rail · stops-matrix rebuild finished",
                )
            finally:
                self.running = False
                self.hass.data.setdefault(DOMAIN, {})[
                    GLOBAL_LAST_REBUILD_KEY
                ] = self.last_result
                self._write_state_if_added()

    @callback
    def _write_state_if_added(self) -> None:
        """Refresh state only when the entity is attached to a platform.

        A directly constructed instance (unit tests, service lookups before
        platform add) must not poke the state machine.
        """
        if getattr(self, "platform", None) is not None:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose last-run progress/outcome (including errors)."""
        if self.last_result is not None:
            attrs: dict[str, Any] = {"status": "finished"}
            attrs.update(self.last_result.as_dict())
            return attrs
        if self.running:
            return {
                "status": "running",
                "note": (
                    "Sampling every station takes several minutes; heavy "
                    "request load against the public RTPI API"
                ),
            }
        return dict(_UNSET_ATTRIBUTES)


def _runtime_client(entry: IrishRailConfigEntry) -> IrishRailClient | None:
    """Best-effort fetch of the shared client from entry runtime data."""
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, IrishRailRuntimeData):
        return runtime.client
    return getattr(runtime, "client", None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IrishRailConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the global rebuild button exactly once per session."""
    if not async_claim_global_provider(hass, entry):
        _LOGGER.debug(
            "%s does not own the global Irish Rail entities; skipping",
            entry.title,
        )
        return
    # Prefer the setting-up entry's client; fall back to the singleton
    # monitor's client when a later entry without runtime data claims first.
    client = _runtime_client(entry)
    if client is None:
        monitor = get_health_monitor(hass)
        client = monitor.client if monitor is not None else None
    if client is None:
        _LOGGER.warning("No Irish Rail API client available; rebuild button skipped")
        return

    entity = IrishRailRebuildStopsMatrixButton(hass, client)
    async_add_entities([entity])

    # Keep a session-wide handle so the service alias can reach the same
    # guarded job even though providership pins the entity to one entry.
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[GLOBAL_REBUILD_ENTITY_KEY] = entity

    # Drop the service/handle and dismiss any leftover notification when
    # this entry is removed, so a re-add (or a sibling entry's claim)
    # always starts from a clean slate and the user never sees a stale
    # "rebuild finished" toast for an integration that is no longer loaded.
    async def _async_cleanup() -> None:
        if domain_data.get(GLOBAL_REBUILD_ENTITY_KEY) is entity:
            domain_data.pop(GLOBAL_REBUILD_ENTITY_KEY, None)
        if hass.services.has_service(DOMAIN, SERVICE_REBUILD):
            hass.services.async_remove(DOMAIN, SERVICE_REBUILD)
        _dismiss_notification(hass)

    entry.async_on_unload(_async_cleanup)

    async def _async_handle_rebuild_service(call: ServiceCall) -> None:
        """Forward a service call onto the live button instance."""
        button: Any | None = domain_data.get(GLOBAL_REBUILD_ENTITY_KEY)
        if button is None:
            _LOGGER.warning(
                "No Irish Rail rebuild button is currently loaded; "
                "service call ignored"
            )
            return
        await button.async_press()

    if not hass.services.has_service(DOMAIN, SERVICE_REBUILD):
        hass.services.async_register(
            DOMAIN, SERVICE_REBUILD, _async_handle_rebuild_service
        )
