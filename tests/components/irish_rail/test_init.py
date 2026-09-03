"""Tests for the Irish Rail integration setup."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail._runtime import get_health_monitor
from custom_components.irish_rail.const import DOMAIN, EMPTY_DATA_ISSUE_THRESHOLD
from custom_components.irish_rail.coordinator import empty_data_issue_id
from custom_components.irish_rail.types import IrishRailRuntimeData


async def test_setup_unload_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
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
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
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

    def _find_by_unique_id(
        reg: er.EntityRegistry, unique_id: str
    ) -> str | None:
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


