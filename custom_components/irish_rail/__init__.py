"""The Irish Rail integration.

See docs/architecture.md §12 for entry setup, update listener, and unload.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ._runtime import (
    async_get_request_gate,
    async_note_entry_loaded,
    async_note_entry_unloaded,
)
from .client import IrishRailClient
from .const import DOMAIN
from .coordinator import (
    IrishRailDataUpdateCoordinator,
    empty_data_issue_id,
    resolve_scan_interval,
)
from .types import IrishRailConfigEntry, IrishRailRuntimeData

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: IrishRailConfigEntry) -> bool:
    """Set up Irish Rail from a config entry."""
    session = async_get_clientsession(hass)
    # Pass the per-HA shared request gate so every client the
    # integration creates (coordinator, both config flows, rebuild,
    # health probe) draws from one rate budget against the public
    # api.irishrail.ie endpoints. See ``gate.py`` for the rationale.
    client = IrishRailClient(session, gate=async_get_request_gate(hass))

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
    # The loaded-entry set keeps this idempotent across setup retries.
    await async_note_entry_loaded(hass, entry.entry_id, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


@callback
def _async_capture_identity_customisations(
    hass: HomeAssistant, entry: IrishRailConfigEntry, previous_uid: str
) -> dict[str, dict[str, Any]]:
    """Snapshot the old identity's per-entity customisations.

    A direction reconfigure changes the entry's unique ID, so the entities
    are re-created under new unique IDs. Deleting the old rows outright
    destroyed the user's entity name, icon, area and disabled state for
    good; capturing them first lets the post-reload restore put them back.
    """
    entity_registry = er.async_get(hass)
    old_prefix = f"{previous_uid}_"
    captured: dict[str, dict[str, Any]] = {}
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if not registry_entry.unique_id.startswith(old_prefix):
            continue
        # Key by the entity-key suffix so old -> new is deterministic.
        captured[registry_entry.unique_id[len(old_prefix) :]] = {
            "name": registry_entry.name,
            "original_name": registry_entry.original_name,
            "icon": registry_entry.icon,
            "entity_category": registry_entry.entity_category,
            "area_id": registry_entry.area_id,
            "hidden_by": registry_entry.hidden_by,
            "disabled_by": registry_entry.disabled_by,
            "translation_key": registry_entry.translation_key,
        }
    return captured


@callback
def _async_drop_stale_identity_registries(
    hass: HomeAssistant, entry: IrishRailConfigEntry, previous_uid: str
) -> None:
    """Remove registry entries belonging to the entry's previous identity."""
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


@callback
def _async_restore_identity_customisations(
    hass: HomeAssistant,
    entry: IrishRailConfigEntry,
    new_uid: str,
    captured: dict[str, dict[str, Any]],
) -> None:
    """Re-apply customisations onto the re-created entities."""
    if not captured:
        return
    entity_registry = er.async_get(hass)
    new_prefix = f"{new_uid}_"
    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if not registry_entry.unique_id.startswith(new_prefix):
            continue
        saved = captured.get(registry_entry.unique_id[len(new_prefix) :])
        if not saved:
            continue
        entity_registry.async_update_entity(
            registry_entry.entity_id,
            name=saved["name"],
            original_name=saved["original_name"],
            icon=saved["icon"],
            entity_category=saved["entity_category"],
            area_id=saved["area_id"],
            hidden_by=saved["hidden_by"],
            disabled_by=saved["disabled_by"],
        )


async def _async_update_listener(
    hass: HomeAssistant, entry: IrishRailConfigEntry
) -> None:
    """Handle config-entry updates: reload on data changes, options in place."""
    coordinator = entry.runtime_data.coordinator
    if coordinator.requires_reload():
        previous_uid = coordinator.applied_unique_id()
        new_uid = entry.unique_id
        # Capture before dropping: the rows are about to be removed, and
        # the re-created entities inherit their customisations from here.
        captured = (
            _async_capture_identity_customisations(hass, entry, previous_uid)
            if previous_uid is not None
            else {}
        )
        if previous_uid is not None:
            _async_drop_stale_identity_registries(hass, entry, previous_uid)
        if captured and new_uid:
            # Applied once the reload has re-registered the new entities.
            async def _async_restore() -> None:
                await hass.async_block_till_done()
                _async_restore_identity_customisations(
                    hass, entry, new_uid, captured
                )

            entry.async_create_task(
                hass,
                _async_restore(),
                name=f"{DOMAIN}_restore_customisations_{entry.entry_id}",
            )
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    coordinator.async_set_configured_interval(resolve_scan_interval(entry))


async def async_unload_entry(hass: HomeAssistant, entry: IrishRailConfigEntry) -> bool:
    """Unload a config entry."""
    ir.async_delete_issue(hass, DOMAIN, empty_data_issue_id(entry))
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Also releases the shared gate and stops the probe when this was
    # the last loaded entry (single registry write path, see _runtime.py).
    await async_note_entry_unloaded(hass, entry.entry_id)
    return unloaded
