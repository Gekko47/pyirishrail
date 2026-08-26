"""The Irish Rail integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IrishRailClient
from .const import DOMAIN
from .coordinator import (
    IrishRailDataUpdateCoordinator,
    empty_data_issue_id,
    resolve_scan_interval,
)
from .health import async_note_entry_loaded, async_note_entry_unloaded
from .types import IrishRailRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]


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

    # Start/attach the shared API-health probe before platforms are
    # forwarded so the global connectivity sensor appears with live state.
    await async_note_entry_loaded(hass, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


@callback
def _async_drop_stale_identity_registries(
    hass: HomeAssistant, entry: ConfigEntry, previous_uid: str
) -> None:
    """Remove registry entries belonging to the entry's previous identity.

    Reconfiguring a station/direction pair rewrites the entry's unique ID,
    which mints fresh entity/device identities. Without cleanup, the previous
    direction's entities would linger forever as unavailable ghosts (HA never
    sweeps live registry entries that a reloaded platform stops providing).

    Matching is strictly positive: only items carrying the exact previous
    identity are removed, and enumeration is scoped to this config entry.
    Sibling entries at the same station — e.g. an "All" direction alongside
    a reconfigured Northbound — are different config entries and therefore
    can never be touched.

    Removal goes through the registries' normal removal, so the entries move
    into the restorable deleted state tied to this config entry: switching
    back to the prior direction re-registers them (unique IDs match again)
    with names, area assignments and customizations intact.
    """
    entity_registry = er.async_get(hass)
    old_prefix = f"{previous_uid}_"
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if registry_entry.unique_id.startswith(old_prefix):
            entity_registry.async_remove(registry_entry.entity_id)

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        if (DOMAIN, previous_uid) in device_entry.identifiers:
            device_registry.async_remove_device(device_entry.id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config-entry updates: reload on data changes, options in place.

    Since HA 2026.6 an integration with an update listener must own reload
    scheduling itself (a flow-scheduled reload alongside the listener can
    double-reload or race; hard error in 2026.12). A change to
    ``entry.data`` (station/direction identity) therefore schedules exactly
    one reload here, while option-only changes apply to the live
    coordinator without any reload.
    """
    coordinator = entry.runtime_data.coordinator
    if coordinator.requires_reload():
        # Drop the previous identity's entities/device before reloading so
        # post-reload setup registers only the new direction's entities.
        previous_uid = coordinator.previous_unique_id()
        if previous_uid is not None:
            _async_drop_stale_identity_registries(hass, entry, previous_uid)
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    # resolve_scan_interval() guards against invalid/non-numeric stored
    # option values, falling back to the default instead of raising.
    coordinator.update_interval = resolve_scan_interval(entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Remove any pending empty-data repair issue so a stale warning is not
    # left behind for an unloaded entry; a reload re-evaluates from scratch.
    ir.async_delete_issue(hass, DOMAIN, empty_data_issue_id(entry))
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Account for this entry leaving so the shared health probe stops once
    # the last Irish Rail entry unloads.
    await async_note_entry_unloaded(hass)
    return unloaded
