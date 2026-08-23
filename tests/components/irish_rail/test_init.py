"""Tests for the Irish Rail integration setup."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.const import DOMAIN, EMPTY_DATA_ISSUE_THRESHOLD
from custom_components.irish_rail.coordinator import empty_data_issue_id
from custom_components.irish_rail.types import IrishRailRuntimeData


async def test_setup_unload_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test setting up and unloading a config entry."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    entity_ids_before = sorted(
        state.entity_id for state in hass.states.async_all("sensor")
    )
    assert len(entity_ids_before) == 4

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
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            return_value=[],
        ),
        patch(
            "custom_components.irish_rail.coordinator.dt_util.now",
            return_value=datetime(2026, 8, 23, 12, tzinfo=UTC),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

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
