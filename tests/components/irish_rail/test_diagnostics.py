"""Tests for the Irish Rail diagnostics."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.diagnostics import (
    _mask_identifier,
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
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    # Entry identifiers are partially masked, not hidden entirely.
    assert result["entry"]["title"] == _mask_identifier("Dublin Pearse")
    assert result["entry"]["unique_id"] == _mask_identifier("PEARS_northbound")
    assert result["entry"]["title"] != "**REDACTED**"
    assert result["entry"]["unique_id"] != "**REDACTED**"
    # Station identifiers are redacted.
    assert result["entry"]["data"]["station"] == "**REDACTED**"
    assert result["entry"]["data"]["station_code"] == "**REDACTED**"
    # Options are included and pass through the same redaction policy.
    assert "options" in result["entry"]
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["due_trains_count"] == 0
