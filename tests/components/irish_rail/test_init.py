"""Tests for the Irish Rail integration setup."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
