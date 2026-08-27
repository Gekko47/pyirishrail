"""Global Irish Rail API connectivity binary sensor.

Integration-level service entity (no device) reporting whether the Irish Rail
RTPI API answered its latest reachability probe. Registered exactly once
per Home Assistant session by whichever config entry claims providership
first (see ``health.py``); the ``DIAGNOSTIC`` entity category keeps it
out of primary UI surfaces so per-station devices never have to carry it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GLOBAL_HEALTH_UNIQUE_ID
from .health import (
    IrishRailApiHealthMonitor,
    async_claim_global_provider,
    ensure_health_monitor_started,
    get_health_monitor,
)

_LOGGER = logging.getLogger(__name__)


class IrishRailApiConnectivitySensor(BinarySensorEntity):
    """Reports whether the RTPI API answered its latest reachability probe."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "status"
    _attr_unique_id = GLOBAL_HEALTH_UNIQUE_ID
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, monitor: IrishRailApiHealthMonitor) -> None:
        """Initialize the global connectivity sensor."""
        self.hass = hass
        self._monitor = monitor

    async def async_added_to_hass(self) -> None:
        """Register for monitor updates while this entity is alive."""
        await super().async_added_to_hass()
        self._monitor.listeners.add(self._handle_monitor_update)
        self.async_on_remove(self._detach_monitor_listener)

    @callback
    def _detach_monitor_listener(self) -> None:
        """Drop this entity from the monitor's listener set."""
        self._monitor.listeners.discard(self._handle_monitor_update)

    @callback
    def _handle_monitor_update(self) -> None:
        """Push the fresh probe outcome into the state machine."""
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """True when the latest probe succeeded; unknown until then."""
        return self._monitor.healthy

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose probe history for diagnostics and automations."""
        attrs: dict[str, Any] = {
            "api_reachable": self._monitor.recently_confirmed_healthy,
            **self._monitor.as_dict(),
        }
        return attrs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the global connectivity sensor exactly once per session."""
    if not async_claim_global_provider(hass, entry):
        _LOGGER.debug(
            "%s does not own the global Irish Rail entities; skipping",
            entry.title,
        )
        return
    monitor = get_health_monitor(hass)
    if monitor is None:
        # Normal setups already started the monitor before platforms are
        # forwarded; this covers direct platform setup in isolation.
        runtime = getattr(entry, "runtime_data", None)
        client = getattr(runtime, "client", None)
        if client is None:
            _LOGGER.warning(
                "No Irish Rail API client available; global health sensor skipped"
            )
            return
        monitor = ensure_health_monitor_started(hass, client)
    async_add_entities([IrishRailApiConnectivitySensor(hass, monitor)])
