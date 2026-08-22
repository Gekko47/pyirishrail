"""Tests for the Irish Rail diagnostics."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_config_entry_diagnostics(hass: HomeAssistant) -> None:
    """Test diagnostics redact station data and report coordinator state."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_Northbound",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["title"] == "Dublin Pearse"
    # Station identifiers are redacted.
    assert result["entry"]["data"]["station"] == "**REDACTED**"
    assert result["entry"]["data"]["station_code"] == "**REDACTED**"
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["due_trains_count"] == 0
