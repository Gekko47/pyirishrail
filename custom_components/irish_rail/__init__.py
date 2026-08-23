"""The Irish Rail integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IrishRailClient
from .const import DOMAIN
from .coordinator import (
    IrishRailDataUpdateCoordinator,
    empty_data_issue_id,
    resolve_scan_interval,
)
from .types import IrishRailRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Irish Rail from a config entry."""
    session = async_get_clientsession(hass)
    client = IrishRailClient(session)

    coordinator = IrishRailDataUpdateCoordinator(hass, client, entry)

    # First refresh: fetches the data. This triggers ConfigEntryNotReady if it fails
    # satisfying the test-before-setup requirement at runtime.
    await coordinator.async_config_entry_first_refresh()

    # Store the container using modern entry.runtime_data pattern.
    # The container holds both the shared client and coordinator.
    entry.runtime_data = IrishRailRuntimeData(client=client, coordinator=coordinator)

    # Apply option changes (scan interval / number of trains) without a full
    # reload: the coordinator interval is updated in place and the sensors
    # read the train count dynamically on every refresh.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates by applying them to the live coordinator."""
    coordinator = entry.runtime_data.coordinator
    # resolve_scan_interval() guards against invalid/non-numeric stored
    # option values, falling back to the default instead of raising.
    coordinator.update_interval = resolve_scan_interval(entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Remove any pending empty-data repair issue so a stale warning is not
    # left behind for an unloaded entry; a reload re-evaluates from scratch.
    ir.async_delete_issue(hass, DOMAIN, empty_data_issue_id(entry))
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
