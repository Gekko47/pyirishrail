"""Tests for the Irish Rail integration setup."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail import (
    _async_capture_identity_customisations,
    _async_restore_identity_customisations,
    _async_update_listener,
)
from custom_components.irish_rail._runtime import get_health_monitor
from custom_components.irish_rail.const import DOMAIN, EMPTY_DATA_ISSUE_THRESHOLD
from custom_components.irish_rail.coordinator import empty_data_issue_id
from custom_components.irish_rail.types import (
    IrishRailConfigEntry,
    IrishRailRuntimeData,
)


async def test_setup_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    verify_cleanup: Generator[None],
) -> None:
    """Test setting up and unloading a config entry."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify the entry was set up and runtime_data is a container.
    assert mock_config_entry.state is ConfigEntryState.LOADED
    entry_data = mock_config_entry.runtime_data
    assert isinstance(entry_data, IrishRailRuntimeData)
    assert entry_data.coordinator is not None
    assert entry_data.client is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Read through a fresh variable annotated with the full enum: mypy
    # narrows ``.state`` to LOADED after the earlier assert and cannot see
    # the unload mutating it.
    state_after_unload: ConfigEntryState = mock_config_entry.state
    assert state_after_unload is ConfigEntryState.NOT_LOADED


async def test_setup_config_entry_not_ready(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test setup fails with ConfigEntryNotReady when first refresh fails."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.coordinator.IrishRailDataUpdateCoordinator.async_config_entry_first_refresh",
        side_effect=ConfigEntryNotReady("Simulated coordinator failure"),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # The entry should remain in a state that requires retry
    assert result is False
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_and_reload_restores_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    verify_cleanup: Generator[None],
) -> None:
    """Silver rule ``config-entry-unloading``: unload + reload round-trip.

    Unloading must remove the entry's entities from the state machine and put
    the entry into NOT_LOADED; reloading must re-run setup (including the
    coordinator first refresh) and restore the same entities under the same
    unique IDs without any restart.
    """
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    entity_ids_before = sorted(
        state.entity_id for state in hass.states.async_all("sensor")
    )
    # Two sensor entities per station.
    assert len(entity_ids_before) == 2

    # Unload: platforms unloaded and entities removed from the state machine.
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    state_after_unload: ConfigEntryState = mock_config_entry.state
    assert state_after_unload is ConfigEntryState.NOT_LOADED
    for entity_id in entity_ids_before:
        post_unload_state = hass.states.get(entity_id)
        # Registered entities either disappear entirely or keep a
        # placeholder restored state marked unavailable.
        if post_unload_state is not None:
            assert post_unload_state.state == "unavailable"
            assert post_unload_state.attributes.get("restored") is True

    # Reload: setup runs again and the same entities are restored.
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state_after_reload: ConfigEntryState = mock_config_entry.state
    assert state_after_reload is ConfigEntryState.LOADED
    assert isinstance(mock_config_entry.runtime_data, IrishRailRuntimeData)
    for entity_id in entity_ids_before:
        reloaded_state = hass.states.get(entity_id)
        assert reloaded_state is not None
        # Successful-but-empty refresh => sensors available reporting unknown.
        assert reloaded_state.state == "unknown"


async def test_restore_customisations_is_a_noop_without_a_capture(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Restoring with nothing captured must not touch the registries.

    Guards the two early-outs: an empty capture, and a re-created entity
    whose key is absent from the capture (a new sensor added since the
    reconfigure was requested).
    """
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entry = cast(IrishRailConfigEntry, mock_config_entry)

        # Empty capture: returns before reading the registry at all.
        _async_restore_identity_customisations(hass, entry, "PEARS_northbound", {})

        # Capture that does not mention one of the live entity keys: that
        # entity is left on its defaults instead of raising or being cleared.
        _async_restore_identity_customisations(
            hass,
            entry,
            "PEARS_northbound",
            {"some_retired_sensor": {"name": "x", "original_name": "x"}},
        )

    ent_reg = er.async_get(hass)
    live = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert live, "entities should still be registered"
    assert all(e.name is None for e in live), "no entity should have been renamed"


async def test_restore_customisations_carries_disabled_state(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A user's disabled state survives the identity reconfigure.

    A user who disabled the following-train sensor must not find it back
    after a direction reconfigure re-creates the entity rows.
    """
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    entry = cast(IrishRailConfigEntry, mock_config_entry)
    ent_reg = er.async_get(hass)
    target = next(
        e
        for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if e.unique_id.endswith("_following_train_due")
    )
    ent_reg.async_update_entity(
        target.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    assert ent_reg.entities[target.entity_id].disabled_by is er.RegistryEntryDisabler.USER

    captured = _async_capture_identity_customisations(hass, entry, "PEARS_northbound")
    assert captured["following_train_due"]["disabled_by"] is er.RegistryEntryDisabler.USER

    _async_restore_identity_customisations(hass, entry, "PEARS_northbound", captured)
    assert (
        ent_reg.entities[target.entity_id].disabled_by is er.RegistryEntryDisabler.USER
    ), "disabled state was lost across the identity change"


async def test_unload_removes_pending_empty_data_repair_issue(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unloading an entry deletes a raised persistent-empty-data issue."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[],
        ),
        patch(
            "custom_components.irish_rail.coordinator.dt_util.now",
            return_value=datetime(2026, 8, 23, 12, tzinfo=UTC),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Deliberately withhold API-health confirmation: the entity-scan path
        # is healthy while the shared probe is not recent/confident, so empty
        # polls must fall back to the legacy persistent-empty-data warning.
        monitor = get_health_monitor(hass)
        assert monitor is not None
        monitor.healthy = False
        monitor.consecutive_failures = 1

        # Drive enough consecutive empty polls during service hours to raise
        # the repair issue (Gold rule ``repair-issues``).
        coordinator = mock_config_entry.runtime_data.coordinator
        for _ in range(EMPTY_DATA_ISSUE_THRESHOLD):
            await coordinator.async_refresh()
        await hass.async_block_till_done()

    issue_id = empty_data_issue_id(mock_config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_global_provider_purges_orphan_entities_when_owner_removed(
    hass: HomeAssistant,
) -> None:
    """A removed claiming entry's entity rows are wiped so the next claim is clean.

    With the global entities decoupled from any device, the only thing
    pinning them to the original config entry is the entity registry's
    ``config_entry_id`` column. Removing the original entry leaves an
    orphan row whose entity_id renders as "not available" in the UI
    forever. The new claiming entry's ``async_add_entities`` would
    otherwise trigger a "restore?" prompt instead of cleanly
    re-registering. The fix in ``health.py`` removes the orphan row
    before granting the new claim.
    """
    from homeassistant.helpers import entity_registry as er

    from custom_components.irish_rail.const import (
        GLOBAL_HEALTH_UNIQUE_ID,
        GLOBAL_REBUILD_UNIQUE_ID,
    )

    def _find_by_unique_id(reg: er.EntityRegistry, unique_id: str) -> str | None:
        for entry in reg.entities.values():
            if entry.unique_id == unique_id:
                return entry.entity_id
        return None

    first = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Northbound)",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        unique_id="PEARS_northbound",
    )
    first.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert _find_by_unique_id(registry, GLOBAL_HEALTH_UNIQUE_ID) is not None
    assert _find_by_unique_id(registry, GLOBAL_REBUILD_UNIQUE_ID) is not None

    assert await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()

    # ``ConfigEntries.async_remove`` calls ``ent_reg.async_clear_config_entry``,
    # which sweeps every entity-registry row pinned to the removed
    # ``first.entry_id`` -- so the two global-entity rows are gone before
    # the next entry's ``claim_service_entities`` even runs. The
    # ``_purge_orphan_global_entities`` path in ``health.py`` is the
    # fallback that handles the *uncommon* case where a row is left
    # pinned to a dead owner through some other channel (e.g. a manual
    # registry edit or a future removal path that bypasses HA core's
    # cleanup). Asserting the clean state here pins the contract and
    # would catch a regression that re-introduced a stale orphan row.
    assert _find_by_unique_id(registry, GLOBAL_HEALTH_UNIQUE_ID) is None
    assert _find_by_unique_id(registry, GLOBAL_REBUILD_UNIQUE_ID) is None
    assert not any(
        candidate.entry_id == first.entry_id
        for candidate in hass.config_entries.async_entries(DOMAIN)
    )

    second = MockConfigEntry(
        domain=DOMAIN,
        title="Cork Kent",
        data={"station": "Cork Kent", "station_code": "KENT"},
        unique_id="KENT_all",
    )
    second.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    health_id = _find_by_unique_id(registry, GLOBAL_HEALTH_UNIQUE_ID)
    rebuild_id = _find_by_unique_id(registry, GLOBAL_REBUILD_UNIQUE_ID)
    assert health_id is not None
    assert rebuild_id is not None
    assert registry.entities[health_id].config_entry_id == second.entry_id
    assert registry.entities[rebuild_id].config_entry_id == second.entry_id

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()


async def test_globals_survive_the_owner_being_removed(
    hass: HomeAssistant,
) -> None:
    """The invariant: globals exist iff at least one station entry is loaded.

    Removing the entry that *owned* the "Irish Rail Services" device used
    to strand the connectivity sensor, the rebuild button and the
    ``rebuild_stops_matrix`` service for the rest of the session: the
    provider key was only cleared at zero loaded entries, and the
    surviving sibling had already skipped platform setup. Ownership is
    now re-elected onto the survivor, which re-registers all three.
    """
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.irish_rail.button import SERVICE_REBUILD
    from custom_components.irish_rail.const import (
        DOMAIN as IRISH_RAIL_DOMAIN,
    )
    from custom_components.irish_rail.const import (
        GLOBAL_HEALTH_UNIQUE_ID,
        GLOBAL_PROVIDER_KEY,
        GLOBAL_REBUILD_UNIQUE_ID,
        GLOBAL_SERVICES_IDENTIFIER,
    )

    def _entry_for(unique_id: str, station_code: str) -> MockConfigEntry:
        return MockConfigEntry(
            domain=IRISH_RAIL_DOMAIN,
            title=f"Station {station_code}",
            data={
                "station": f"Station {station_code}",
                "station_code": station_code,
            },
            unique_id=unique_id,
        )

    owner = _entry_for("PEARS_all", "PEARS")
    sibling = _entry_for("KENT_all", "KENT")
    owner.add_to_hass(hass)
    sibling.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        # Setting up one entry sets up the whole domain, so both are
        # loaded here; the owner is whichever claimed providership first.
        assert await hass.config_entries.async_setup(owner.entry_id)
        await hass.async_block_till_done()
        assert owner.state is ConfigEntryState.LOADED
        assert sibling.state is ConfigEntryState.LOADED

        registry = er.async_get(hass)
        devices = dr.async_get(hass)

        def _global_owner(unique_id: str) -> str | None:
            for candidate in registry.entities.values():
                if candidate.unique_id == unique_id:
                    return candidate.config_entry_id
            return None

        assert hass.data[IRISH_RAIL_DOMAIN][GLOBAL_PROVIDER_KEY] == owner.entry_id
        assert _global_owner(GLOBAL_HEALTH_UNIQUE_ID) == owner.entry_id
        assert _global_owner(GLOBAL_REBUILD_UNIQUE_ID) == owner.entry_id
        assert hass.services.has_service(IRISH_RAIL_DOMAIN, SERVICE_REBUILD)

        # Remove the owner outright while the sibling stays loaded.
        await hass.config_entries.async_remove(owner.entry_id)
        await hass.async_block_till_done()

        survivor = sibling.entry_id
        assert hass.data[IRISH_RAIL_DOMAIN][GLOBAL_PROVIDER_KEY] == survivor
        assert _global_owner(GLOBAL_HEALTH_UNIQUE_ID) == survivor
        assert _global_owner(GLOBAL_REBUILD_UNIQUE_ID) == survivor
        assert hass.services.has_service(IRISH_RAIL_DOMAIN, SERVICE_REBUILD)
        assert hass.states.async_entity_ids("binary_sensor")
        assert hass.states.async_entity_ids("button")

        # The globals live on their own fixed-identifier device, never on
        # a station's: neither global's device carries a station
        # identifier, and the device belongs to the current provider.
        global_entity_ids = {
            entity_id
            for entity_id, entity in registry.entities.items()
            if entity.unique_id
            in (GLOBAL_HEALTH_UNIQUE_ID, GLOBAL_REBUILD_UNIQUE_ID)
        }
        assert global_entity_ids
        services_devices = {
            registry.entities[entity_id].device_id for entity_id in global_entity_ids
        }
        assert len(services_devices) == 1
        services_device_id = services_devices.pop()
        assert services_device_id is not None
        services_device = devices.devices[services_device_id]
        assert services_device.identifiers == {GLOBAL_SERVICES_IDENTIFIER}
        assert services_device.config_entry_id == survivor

        # Removing the last station takes the globals and the service with it.
        await hass.config_entries.async_remove(sibling.entry_id)
        await hass.async_block_till_done()

        assert GLOBAL_PROVIDER_KEY not in hass.data[IRISH_RAIL_DOMAIN]
        assert _global_owner(GLOBAL_HEALTH_UNIQUE_ID) is None
        assert _global_owner(GLOBAL_REBUILD_UNIQUE_ID) is None
        assert not hass.services.has_service(IRISH_RAIL_DOMAIN, SERVICE_REBUILD)
        assert not any(
            device.identifiers == {GLOBAL_SERVICES_IDENTIFIER}
            for device in devices.devices.values()
        )


async def test_connectivity_sensor_is_unavailable_before_first_probe(
    hass: HomeAssistant,
) -> None:
    """The connectivity sensor renders as ``unavailable`` until a probe lands.

    The sensor's ``is_on`` returns ``None`` until the first successful
    probe. Without an ``available`` override, HA renders ``is_on=None``
    as "Off" with the connectivity-class ``mdi:lan-disconnect`` icon —
    falsely signalling an outage during the five-minute startup window.
    The override returns ``False`` until a probe has actually landed,
    which HA renders as the grey "unavailable" state with a question-
    mark tooltip, the correct semantic for "I haven't checked yet".
    """
    from custom_components.irish_rail._runtime import ConnectivityMonitor
    from custom_components.irish_rail.binary_sensor import (
        IrishRailApiConnectivitySensor,
    )

    monitor = ConnectivityMonitor(hass, MagicMock())
    sensor = IrishRailApiConnectivitySensor(hass, monitor)

    # No probe has landed yet.
    assert sensor.available is False
    assert sensor.is_on is None

    # A successful probe flips both flags.
    monitor.healthy = True
    assert sensor.available is True
    assert sensor.is_on is True

    # A failed probe keeps availability but reports ``is_on=False``.
    monitor.healthy = False
    assert sensor.available is True
    assert sensor.is_on is False


async def test_a_data_change_without_an_applied_identity_still_reloads(
    hass: HomeAssistant,
) -> None:
    """A reload that has no previous identity to preserve still happens.

    ``applied_unique_id`` is None for an entry with no station code, so
    there is nothing to capture. The reload must still be scheduled:
    skipping it would leave the entry running its old data forever.
    """
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        unique_id="PEARS_northbound",
    )
    mock_entry.add_to_hass(hass)
    entry = cast(IrishRailConfigEntry, mock_entry)
    coordinator = MagicMock()
    coordinator.requires_reload.return_value = True
    coordinator.applied_unique_id.return_value = None
    entry.runtime_data = MagicMock(coordinator=coordinator)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
        await _async_update_listener(hass, entry)

    schedule.assert_called_once_with(entry.entry_id)
