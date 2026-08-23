"""Tests for the Irish Rail coordinator."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import (
    IrishRailConnectionError,
    TrainDueTime,
)
from custom_components.irish_rail.const import DOMAIN
from custom_components.irish_rail.coordinator import (
    IrishRailDataUpdateCoordinator,
    resolve_num_trains,
    resolve_scan_interval,
    resolve_stops_at,
)


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
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
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
    mock_fetch.assert_awaited_once_with(
        "PEARS", direction="Northbound", stops_at=None
    )


async def test_coordinator_update_failed(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
) -> None:
    """Test coordinator handles update failure."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()


async def test_coordinator_scan_interval_from_options(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """Test the coordinator honors a scan interval set via entry options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_Northbound",
        options={"scan_interval": 300},
    )

    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, entry)
    assert coordinator.update_interval == timedelta(seconds=300)


def _entry_with(
    data: dict[str, Any] | None = None, options: dict[str, Any] | None = None
) -> Any:
    """Return a mock config entry with the given data/options."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
            **(data or {}),
        },
        unique_id="PEARS_Northbound",
        options=options,
    )


def test_resolve_scan_interval_defaults(hass: HomeAssistant) -> None:
    """Test scan interval resolution falls back to the 60 s default."""
    assert resolve_scan_interval(_entry_with()) == timedelta(seconds=60)
    assert resolve_scan_interval(
        _entry_with(options={"scan_interval": 300})
    ) == timedelta(seconds=300)


def test_resolve_scan_interval_clamps_to_bounds(hass: HomeAssistant) -> None:
    """Test scan interval resolution clamps to the documented 30-600 s bounds."""
    # Below the 30 s minimum clamps up to 30 s.
    assert resolve_scan_interval(
        _entry_with(options={"scan_interval": 10})
    ) == timedelta(seconds=30)

    # Above the 10 min maximum clamps down to 600 s.
    assert resolve_scan_interval(
        _entry_with(options={"scan_interval": 601})
    ) == timedelta(seconds=600)

    # In-range values are preserved.
    assert resolve_scan_interval(
        _entry_with(options={"scan_interval": 300})
    ) == timedelta(seconds=300)

    # Non-numeric values fall back to the 60 s default.
    assert resolve_scan_interval(
        _entry_with(options={"scan_interval": "bad"})
    ) == timedelta(seconds=60)


def test_resolve_num_trains_precedence_and_clamping(hass: HomeAssistant) -> None:
    """Test num_trains resolution: options > data > default, clamped to 1-5."""
    # No configuration at all: default.
    assert resolve_num_trains(_entry_with()) == 3

    # Value from initial setup data.
    assert resolve_num_trains(_entry_with(data={"num_trains": 2})) == 2

    # Options take precedence over data.
    entry = _entry_with(data={"num_trains": 2}, options={"num_trains": 4})
    assert resolve_num_trains(entry) == 4

    # Out-of-range values are clamped.
    assert resolve_num_trains(_entry_with(options={"num_trains": 99})) == 5
    assert resolve_num_trains(_entry_with(options={"num_trains": 0})) == 1

    # Non-numeric values fall back to the default.
    assert resolve_num_trains(_entry_with(options={"num_trains": "bad"})) == 3


def test_resolve_stops_at_precedence(hass: HomeAssistant) -> None:
    """Test stops_at resolution: options > data > no filter."""
    # No configuration at all: no filter.
    assert resolve_stops_at(_entry_with()) is None

    # Value from initial setup data.
    assert resolve_stops_at(_entry_with(data={"stops_at": "Bray"})) == "Bray"

    # Options take precedence over data.
    entry = _entry_with(data={"stops_at": "Bray"}, options={"stops_at": "Howth"})
    assert resolve_stops_at(entry) == "Howth"

    # The "All" sentinel and blank values mean no filter.
    assert resolve_stops_at(_entry_with(options={"stops_at": "All"})) is None
    assert resolve_stops_at(_entry_with(options={"stops_at": ""})) is None


async def test_coordinator_passes_stops_at_filter(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """Test the coordinator forwards the stops_at option to the API client."""
    entry = _entry_with(options={"stops_at": "Bray"})
    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, entry)

    with patch.object(
        mock_api_client,
        "async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ) as mock_fetch:
        await coordinator._async_update_data()

    mock_fetch.assert_awaited_once_with(
        "PEARS", direction="Northbound", stops_at="Bray"
    )


async def test_transition_logging_once_per_direction(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silver rule ``log-when-unavailable``: log once per state transition.

    Exactly one error line must be logged when the coordinator transitions
    from success to failure, and exactly one info line when it recovers —
    not one per failed poll. This behaviour is provided by
    ``DataUpdateCoordinator`` itself (the integration's job is only to raise
    ``UpdateFailed``, which ``_async_update_data`` does); these tests pin the
    behaviour so integration-side changes cannot silently break the rule.
    """
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )
    assert coordinator.last_update_success is True

    coordinator_logger = "custom_components.irish_rail.coordinator"
    with patch.object(
        mock_api_client,
        "async_get_station_by_code",
        side_effect=IrishRailConnectionError("connection refused"),
    ):
        # First failure: exactly one error log (success -> failure transition).
        with caplog.at_level(logging.INFO):
            await coordinator.async_refresh()
        errors = [
            record
            for record in caplog.records
            if record.name == coordinator_logger
            and record.levelno >= logging.ERROR
        ]
        assert len(errors) == 1

        # Second consecutive failure: no additional error log (no spamming).
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await coordinator.async_refresh()
        errors = [
            record
            for record in caplog.records
            if record.name == coordinator_logger
            and record.levelno >= logging.ERROR
        ]
        assert len(errors) == 0
        assert coordinator.last_update_success is False

    # Recovery: exactly one info log announcing recovery.
    caplog.clear()
    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            new=AsyncMock(return_value=[]),
        ),
        caplog.at_level(logging.INFO),
    ):
        await coordinator.async_refresh()

    recovered = [
        record
        for record in caplog.records
        if record.name == coordinator_logger
        and record.levelno == logging.INFO
        and "recovered" in record.getMessage()
    ]
    assert len(recovered) == 1
    assert coordinator.last_update_success is True
