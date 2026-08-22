"""The Irish Rail integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IrishRailClient
from .coordinator import IrishRailDataUpdateCoordinator
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
