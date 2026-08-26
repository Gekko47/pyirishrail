"""Health-aware suppression of the persistent-empty-data repair issue.

The coordinator must stop warning about stations that simply have no
scheduled services inside the RTPI look-ahead window whenever the shared
health probe proves the API is reachable, while keeping every historical
warning behaviour when the API genuinely looks broken.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import zoneinfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import IrishRailConnectionError
from custom_components.irish_rail.const import (
    DOMAIN,
    EMPTY_DATA_ISSUE_THRESHOLD,
    HEALTH_MONITOR_INSTANCE,
)
from custom_components.irish_rail.coordinator import (
    IrishRailDataUpdateCoordinator,
    empty_data_issue_id,
)


def _fake_now_factory(stamp_utc: datetime) -> Any:
    """Mimic dt_util.now()'s contract for a fixed instant."""

    def fake_now(time_zone: zoneinfo.ZoneInfo | None = None) -> datetime:
        return stamp_utc.astimezone(time_zone) if time_zone else stamp_utc

    return fake_now


@contextmanager
def _dublin_service_hours() -> Iterator[None]:
    """Force Dublin-local midday (inside service hours) for all polls."""
    with patch(
        "custom_components.irish_rail.coordinator.dt_util.now",
        side_effect=_fake_now_factory(datetime(2026, 8, 24, 12, tzinfo=UTC)),
    ):
        yield


def _active_issue(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Return the entry's persistent-empty-data issue from the registry."""
    return ir.async_get(hass).async_get_issue(DOMAIN, empty_data_issue_id(entry))


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up one entry whose API answers successfully but with no trains."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
            "num_trains": 3,
        },
        unique_id="PEARS_Northbound",
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _refresh_empty(
    hass: HomeAssistant,
    coordinator: IrishRailDataUpdateCoordinator,
    times: int,
) -> None:
    """Run ``times`` refreshes against a class-level empty station poll."""
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        for _ in range(times):
            await coordinator.async_refresh()
    await hass.async_block_till_done()

async def test_healthy_api_suppresses_issue_clears_stale_one(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty polls while the probe is green never warn, and reset streaks."""
    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data.coordinator

    # Setup's automatic probe hit the (mocked) API successfully.
    monitor = hass.data[DOMAIN][HEALTH_MONITOR_INSTANCE]
    assert monitor.recently_confirmed_healthy is True

    # Pre-seed a stale issue and an in-flight streak as if earlier polls
    # had warned before the health feature existed.
    ir.async_create_issue(
        hass,
        DOMAIN,
        empty_data_issue_id(entry),
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="empty_data_during_service_hours",
        translation_placeholders={"station": "Dublin Pearse"},
    )
    coordinator._empty_streak = EMPTY_DATA_ISSUE_THRESHOLD - 1
    assert _active_issue(hass, entry) is not None

    with _dublin_service_hours(), caplog.at_level(logging.DEBUG):
        await _refresh_empty(hass, coordinator, EMPTY_DATA_ISSUE_THRESHOLD)

    assert _active_issue(hass, entry) is None
    assert coordinator._empty_streak == 0
    assert coordinator._empty_issue_reported is False

    warnings = [
        record
        for record in caplog.records
        if record.name == "custom_components.irish_rail.coordinator"
        and record.levelno >= logging.WARNING
    ]
    assert warnings == []

    info_cleared = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO
        and "no scheduled services in the look-ahead window"
        in record.getMessage()
    ]
    assert len(info_cleared) == 1

    debug_lines = [
        record
        for record in caplog.records
        if record.levelno == logging.DEBUG
        and "nothing scheduled in the look-ahead window" in record.getMessage()
    ]
    assert debug_lines  # quiet classification stays visible at DEBUG level


async def test_unhealthy_monitor_restores_then_heals_legacy_warnings(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing probe re-arms every historical warning, then self-heals."""
    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    monitor = hass.data[DOMAIN][HEALTH_MONITOR_INSTANCE]

    # The probe starts failing: emptiness must look suspicious again.
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(side_effect=IrishRailConnectionError("api down")),
    ):
        await monitor.async_ping()
    assert monitor.recently_confirmed_healthy is False

    with _dublin_service_hours(), caplog.at_level(logging.INFO):
        await _refresh_empty(hass, coordinator, EMPTY_DATA_ISSUE_THRESHOLD)

    issue = _active_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "empty_data_during_service_hours"
    assert coordinator._empty_issue_reported is True

    # Probe recovers: the very next empty poll heals the situation.
    with (
        _dublin_service_hours(),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await monitor.async_ping()
        await _refresh_empty(hass, coordinator, 1)

    assert _active_issue(hass, entry) is None
    assert coordinator._empty_streak == 0
    assert coordinator._empty_issue_reported is False


async def test_absent_monitor_preserves_legacy_behavior(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
) -> None:
    """Without any shared monitor, thresholds behave exactly as before."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )
    assert coordinator._health_monitor_is_healthy() is False

    with (
        _dublin_service_hours(),
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await _refresh_empty(hass, coordinator, EMPTY_DATA_ISSUE_THRESHOLD)

    assert _active_issue(hass, mock_config_entry) is not None
