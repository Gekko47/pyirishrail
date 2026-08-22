"""Tests for the Irish Rail coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.irish_rail.api import (
    IrishRailConnectionError,
    TrainDueTime,
)
from custom_components.irish_rail.coordinator import IrishRailDataUpdateCoordinator


def _make_train() -> TrainDueTime:
    """Return a representative TrainDueTime for tests."""
    return TrainDueTime(
        code="E123",
        origin="Howth",
        destination="Bray",
        origin_time="12:00",
        destination_time="13:00",
        due_in_mins=10,
        late_mins=2,
        expected_arrival_time="12:10",
        expected_departure_time="12:11",
        scheduled_arrival_time="12:00",
        scheduled_departure_time="12:01",
        type="DART",
        direction="Southbound",
        location_type="S",
    )


async def test_coordinator_update_success(
    hass: HomeAssistant, mock_api_client, mock_config_entry
) -> None:
    """Test a successful coordinator update returns the parsed trains."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    expected = [_make_train()]
    with patch.object(
        mock_api_client,
        "async_get_station_by_code",
        new=AsyncMock(return_value=expected),
    ) as mock_fetch:
        data = await coordinator._async_update_data()

    assert data == expected
    mock_fetch.assert_awaited_once_with("PEARS", direction="Northbound")


async def test_coordinator_update_failed(
    hass: HomeAssistant, mock_api_client, mock_config_entry
) -> None:
    """Test coordinator handles update failure."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    with patch.object(
        mock_api_client,
        "async_get_station_by_code",
        side_effect=IrishRailConnectionError,
    ), pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
