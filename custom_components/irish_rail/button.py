"""Global button to rebuild the bundled "stops at" matrix at runtime.

One press samples the whole network in-process (a port of
``scripts/build_stops_matrix.py`` merged gap-fill style into
``stops_matrix.json``) and refreshes the bundled-seed cache, all without a
Home Assistant restart. Runs on the integration-wide "Irish Rail API" hub
device next to the connectivity sensor.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IrishRailClient
from .const import DOMAIN, GLOBAL_LAST_REBUILD_KEY, GLOBAL_REBUILD_UNIQUE_ID
from .health import (
    async_claim_global_provider,
    get_health_monitor,
    global_device_info,
)
from .matrix_rebuild import RebuildResult, async_run_matrix_rebuild
from .types import IrishRailRuntimeData

_LOGGER = logging.getLogger(__name__)

# Automation-facing alias for pressing this button programmatically;
# registered alongside the entity so either route works identically.
SERVICE_REBUILD = "rebuild_stops_matrix"

_UNSET_ATTRIBUTES: dict[str, Any] = {"status": "never run since startup"}


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

    def __init__(self, hass: HomeAssistant, client: IrishRailClient) -> None:
        """Initialize the rebuild button."""
        self.hass = hass
        self._client = client
        self._lock = asyncio.Lock()
        self.running = False
        self.last_result: RebuildResult | None = None
        self._attr_device_info = global_device_info()

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
            try:
                self.last_result = None
                self._write_state_if_added()
                self.last_result = await async_run_matrix_rebuild(
                    self.hass, self._client
                )
            except Exception as err:  # button must never crash Home Assistant
                _LOGGER.error("Stops-matrix rebuild failed: %s", err, exc_info=True)
                self.last_result = RebuildResult(error=f"{type(err).__name__}: {err}")
                raise
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


def _runtime_client(entry: ConfigEntry) -> IrishRailClient | None:
    """Best-effort fetch of the shared client from entry runtime data."""
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, IrishRailRuntimeData):
        return runtime.client
    return getattr(runtime, "client", None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
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
    domain_data["global_rebuild_entity"] = entity

    async def _async_handle_rebuild_service(call: ServiceCall) -> None:
        """Forward a service call onto the live button instance."""
        button: Any | None = domain_data.get("global_rebuild_entity")
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
