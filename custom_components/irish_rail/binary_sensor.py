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
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry

# The EntityCategory enum is added by Home Assistant itself but the
# typeshed stub does not re-export it, so a plain import trips mypy.
# ``type: ignore[attr-defined]`` suppresses the false positive without
# hiding a real mistake (the attribute is present at runtime in HA 2024+).
# ``EntityCategory`` lives in ``homeassistant.const`` in modern HA; the
# typeshed re-exports it from there but not from ``homeassistant.helpers.entity``.
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GLOBAL_HEALTH_UNIQUE_ID
from .health import (
    IrishRailApiHealthMonitor,
    async_claim_global_provider,
    ensure_health_monitor_started,
    get_health_monitor,
)

_LOGGER = logging.getLogger(__name__)

# A standalone ``mdi:api`` icon makes the entity unambiguous in the
# integrations page alongside the station sensors (which all use the
# ``mdi:train`` family). The ``DeviceClass.CONNECTIVITY`` default would
# otherwise be ``mdi:lan-connect`` / ``mdi:lan-disconnect`` — semantically
# correct for a network adapter, but misleading for a public RTPI
# reachability probe.
_CONNECTIVITY_DESCRIPTION = BinarySensorEntityDescription(
    key="irish_rail_api_connectivity",
    translation_key="status",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    icon="mdi:api",
)


class IrishRailApiConnectivitySensor(BinarySensorEntity):
    """Reports whether the RTPI API answered its latest reachability probe."""

    entity_description = _CONNECTIVITY_DESCRIPTION
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_unique_id = GLOBAL_HEALTH_UNIQUE_ID

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
    def available(self) -> bool:
        """Return ``False`` until the first probe has landed.

        A connectivity sensor that hasn't been probed yet is not "on" or
        "off" — it's *unknown*. HA renders the ``False`` return here as
        ``unavailable`` (grey badge with a question-mark tooltip), which is
        the correct visual cue during the first five-minute window after
        startup. Without this, ``is_on=None`` is rendered as "Off" with
        the ``mdi:lan-disconnect`` icon, falsely signalling an outage.
        """
        return self._monitor.healthy is not None

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
