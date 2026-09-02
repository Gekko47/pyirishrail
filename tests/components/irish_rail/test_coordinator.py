"""Tests for the Irish Rail coordinator."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.const import (
    BACKOFF_MULTIPLIER,
    CONF_STATION,
    CONF_STATION_CODE,
    CONF_STOPS_AT,
    DOMAIN,
    EMPTY_DATA_ISSUE_THRESHOLD,
)
from custom_components.irish_rail.coordinator import (
    IrishRailDataUpdateCoordinator,
    empty_data_issue_id,
    resolve_num_trains,
    resolve_scan_interval,
    resolve_stops_at,
)
from custom_components.irish_rail.errors import IrishRailConnectionError
from custom_components.irish_rail.models import TrainDueTime


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


async def test_backoff_doubles_on_consecutive_failures(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """Each consecutive failed poll doubles the effective interval."""
    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, _entry_with())
    assert coordinator.update_interval == timedelta(seconds=60)

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=120)

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=240)


async def test_backoff_caps_at_max_interval(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """Backoff growth is capped at MAX_BACKOFF_INTERVAL (15 minutes)."""
    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, _entry_with())

    for _ in range(10):
        with (
            patch.object(
                mock_api_client,
                "async_get_station_by_code",
                side_effect=IrishRailConnectionError,
            ),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()

    # 60s * 2^streak far exceeds the cap after enough failures.
    assert coordinator.update_interval == timedelta(minutes=15)


async def test_backoff_restores_after_success(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first successful poll restores the configured interval."""
    entry = _entry_with(options={"scan_interval": 300})
    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, entry)
    assert coordinator.update_interval == timedelta(seconds=300)

    for _ in range(2):
        with (
            patch.object(
                mock_api_client,
                "async_get_station_by_code",
                side_effect=IrishRailConnectionError,
            ),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=900)

    with patch.object(
        mock_api_client,
        "async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        data = await coordinator._async_update_data()

    assert data == []
    assert coordinator.update_interval == timedelta(seconds=300)

    recovery_logs = [
        record
        for record in caplog.records
        if record.name == "custom_components.irish_rail.coordinator"
        and record.levelno == logging.INFO
        and "polling restored" in record.getMessage()
    ]
    assert len(recovery_logs) == 1


async def test_backoff_uses_new_base_after_options_change(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """An options-driven base-interval change applies during backoff too."""
    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, _entry_with())

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=120)

    # Mirrors _async_update_listener applying a changed options value.
    coordinator.update_interval = resolve_scan_interval(
        _entry_with(options={"scan_interval": 120})
    )
    assert coordinator.update_interval == timedelta(seconds=240)


async def test_schedule_refresh_mirrors_backed_off_interval_into_cache(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """HA 2026.8+ schedules from the seconds cache, not the property.

    ``_schedule_refresh()`` must therefore sync that cache from the
    effective interval so consecutive failures genuinely widen polling.
    """
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, _entry_with(options={"scan_interval": 300})
    )
    # Constructor-time assignment already populated the scheduler cache.
    assert coordinator._update_interval_seconds == 300.0

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()

    coordinator._schedule_refresh()
    # One failure backs the effective interval off once from 300 s.
    assert coordinator._update_interval_seconds == pytest.approx(
        300 * BACKOFF_MULTIPLIER
    )

    # Recovery restores the configured interval at the next schedule point.
    coordinator._failure_streak = 0
    coordinator._schedule_refresh()
    assert coordinator._update_interval_seconds == 300.0

    coordinator._unschedule_refresh()


async def test_failed_refresh_cycle_reschedules_with_widened_interval(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """A failed cycle reschedules through HA's loop with the backed-off wait.

    End-to-end through ``async_refresh``: HA's ``_async_refresh`` finally
    block calls ``_schedule_refresh``, which must hand ``loop.call_at`` a
    deadline one backoff step beyond now (300 s configured -> doubled once
    after the first failure).
    """
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, _entry_with(options={"scan_interval": 300})
    )
    # HA only reschedules while something listens (mirrors entity setup).
    remove_listener = coordinator.async_add_listener(lambda: None)

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
        patch.object(hass.loop, "call_at", wraps=hass.loop.call_at) as mock_call_at,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator._failure_streak == 1

    assert mock_call_at.call_count == 1
    delta = mock_call_at.call_args.args[0] - hass.loop.time()
    # Doubled once from 300 s; allow ~1 s slack each way for the base
    # class's int()-floored deadline and its sub-second stagger.
    assert 300 * BACKOFF_MULTIPLIER - 1 < delta < 300 * BACKOFF_MULTIPLIER + 1

    remove_listener()
    coordinator._unschedule_refresh()


async def test_successful_refresh_cycle_reschedules_at_configured_interval(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """A healthy cycle keeps the configured spacing (override is inert)."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, _entry_with(options={"scan_interval": 300})
    )
    remove_listener = coordinator.async_add_listener(lambda: None)

    with (
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(hass.loop, "call_at", wraps=hass.loop.call_at) as mock_call_at,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator._failure_streak == 0

    assert mock_call_at.call_count == 1
    delta = mock_call_at.call_args.args[0] - hass.loop.time()
    assert 300 - 1 < delta < 300 + 1

    remove_listener()
    coordinator._unschedule_refresh()


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


# ── Persistent-empty-data repair issue (roadmap Phase 3, Gold rule
#    ``repair-issues``) ──────────────────────────────────────────────────────


def _service_hours(hour: int) -> Any:
    """Return a patcher pinning the coordinator clock to a specific hour."""
    return patch(
        "custom_components.irish_rail.coordinator.dt_util.now",
        return_value=datetime(2026, 8, 23, hour, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("utc_stamp", "expected"),
    [
        # August IST (UTC+1): 23:15Z is already 00:15 the next day -> outside
        # hours even though the UTC hour is inside them.
        (datetime(2026, 8, 23, 23, 15, tzinfo=UTC), False),
        # December GMT (UTC+0): 06:30Z is 06:30 Dublin -> inside hours.
        (datetime(2026, 12, 20, 6, 30, tzinfo=UTC), True),
    ],
)
async def test_in_service_hours_evaluates_dublin_local_time(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
    utc_stamp: datetime,
    expected: bool,
) -> None:
    """The gate converts now() to Europe/Dublin before reading ``hour``."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    seen_timezones: list[Any] = []

    def fake_now(time_zone: Any = None) -> datetime:
        # Mimic the real dt_util.now() contract: localize the current instant
        # to the requested timezone so .hour reflects civil time there.
        seen_timezones.append(time_zone)
        return utc_stamp.astimezone(time_zone) if time_zone else utc_stamp

    with patch(
        "custom_components.irish_rail.coordinator.dt_util.now",
        side_effect=fake_now,
    ):
        assert coordinator._in_service_hours() is expected

    assert seen_timezones == [ZoneInfo("Europe/Dublin")]


def _active_issue(hass: HomeAssistant, entry: Any) -> Any:
    """Return the entry's persistent-empty-data issue from the registry."""
    return ir.async_get(hass).async_get_issue(DOMAIN, empty_data_issue_id(entry))


async def _refresh_empty(
    coordinator: IrishRailDataUpdateCoordinator,
    client: MagicMock,
    times: int = 1,
) -> None:
    """Run ``times`` coordinator refreshes that each return zero trains."""
    with patch.object(
        client, "async_get_station_by_code", new=AsyncMock(return_value=[])
    ):
        for _ in range(times):
            await coordinator.async_refresh()


async def test_empty_polls_below_threshold_create_no_issue(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
) -> None:
    """Fewer than threshold consecutive empty polls never raise the issue."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    with _service_hours(12):
        await _refresh_empty(
            coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD - 1
        )

    assert _active_issue(hass, mock_config_entry) is None
    assert coordinator._empty_issue_reported is False


async def test_persistent_empty_data_creates_issue_once_during_service_hours(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gold rule ``repair-issues``: one translation-keyed issue per streak."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    with (
        _service_hours(12),
        patch(
            "homeassistant.helpers.issue_registry.async_create_issue",
            autospec=True,
            side_effect=ir.async_create_issue,
        ) as create_spy,
        caplog.at_level(logging.WARNING),
    ):
        # Reaching the threshold raises exactly one issue.
        await _refresh_empty(coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD)

    issue = _active_issue(hass, mock_config_entry)
    assert issue is not None
    assert issue.translation_key == "empty_data_during_service_hours"
    assert issue.translation_placeholders == {"station": "Dublin Pearse"}
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    # The repair issue carries a Learn-more link so users land on the
    # README's "Known Limitations" section instead of staring at a bare
    # warning. The exact URL is exposed for stable pinning — if it ever
    # drifts, a maintainer can update both sides together.
    assert issue.learn_more_url == (
        "https://github.com/Gekko47/pyirishrail/blob/master/"
        "README.md#known-limitations"
    )
    assert create_spy.call_count == 1
    assert len(caplog.records) == 1

    # Subsequent empty polls must not re-create the issue (once per streak).
    with (
        _service_hours(12),
        patch(
            "homeassistant.helpers.issue_registry.async_create_issue",
            autospec=True,
            side_effect=ir.async_create_issue,
        ) as create_spy_again,
    ):
        await _refresh_empty(coordinator, mock_api_client, 3)

    assert _active_issue(hass, mock_config_entry) is not None
    assert create_spy_again.call_count == 0


@pytest.mark.parametrize(
    ("hour", "expected"), [(5, False), (6, True), (23, True), (0, False)]
)
async def test_empty_data_issue_respects_service_hour_boundaries(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
    hour: int,
    expected: bool,
) -> None:
    """The issue is gated to [SERVICE_HOURS_START_HOUR, SERVICE_HOURS_END_HOUR)."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    with _service_hours(hour):
        await _refresh_empty(coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD)

    assert (_active_issue(hass, mock_config_entry) is not None) is expected


async def test_overnight_empty_streak_does_not_carry_into_first_service_hour_poll(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
) -> None:
    """An overnight empty streak cannot pre-seed the next morning's count."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    # Overnight quiet period (outside service hours): far more than the
    # threshold of consecutive empty polls must not accumulate any streak.
    with _service_hours(2):
        await _refresh_empty(
            coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD * 2
        )
    assert coordinator._empty_streak == 0

    # The first service-hour poll of the morning is also empty: it starts a
    # fresh streak at 1 instead of inheriting the overnight empties.
    with _service_hours(6):
        await _refresh_empty(coordinator, mock_api_client, 1)
    assert coordinator._empty_streak == 1
    assert _active_issue(hass, mock_config_entry) is None

    # A full fresh run of consecutive service-hour polls is still required:
    # with the streak at 1, the issue must stay absent until the fresh
    # in-service streak itself reaches the threshold, firing exactly there.
    with _service_hours(6):
        await _refresh_empty(
            coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD - 2
        )
        assert _active_issue(hass, mock_config_entry) is None
        await _refresh_empty(coordinator, mock_api_client, 1)
        assert _active_issue(hass, mock_config_entry) is not None
    assert coordinator._empty_issue_reported is True


async def test_recovery_clears_issue_and_second_streak_re_reports(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First data-bearing refresh deletes the issue; a new streak re-raises."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )

    with _service_hours(12), caplog.at_level(logging.INFO):
        # Streak 1: raise the issue.
        await _refresh_empty(coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD)
        assert _active_issue(hass, mock_config_entry) is not None

        # Recovery: the very first refresh returning trains removes it again.
        with patch.object(
            mock_api_client,
            "async_get_station_by_code",
            new=AsyncMock(return_value=[_make_train()]),
        ):
            await coordinator.async_refresh()

        assert _active_issue(hass, mock_config_entry) is None
        assert coordinator._empty_issue_reported is False

        recovered = [
            record
            for record in caplog.records
            if record.name == "custom_components.irish_rail.coordinator"
            and record.levelno == logging.INFO
            and "reporting train data again" in record.getMessage()
        ]
        assert len(recovered) == 1

        # Streak 2: the issue is raised again only after a fresh threshold of
        # empty polls, proving the streak counter was reset on recovery.
        await _refresh_empty(
            coordinator, mock_api_client, EMPTY_DATA_ISSUE_THRESHOLD - 1
        )
        assert _active_issue(hass, mock_config_entry) is None
        await _refresh_empty(coordinator, mock_api_client, 1)
        assert _active_issue(hass, mock_config_entry) is not None


async def test_recovery_deletes_issue_raised_by_previous_coordinator_instance(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    mock_config_entry: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rebuilt coordinator still removes a registry issue on first data.

    The issue may outlive the coordinator instance that raised it (entry
    reload/re-setup); the fresh instance's ``_empty_issue_reported`` flag
    starts False, so recovery must consult the issue registry itself.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        empty_data_issue_id(mock_config_entry),
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="empty_data_during_service_hours",
        translation_placeholders={"station": "Dublin Pearse"},
    )
    assert _active_issue(hass, mock_config_entry) is not None

    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, mock_config_entry
    )
    assert coordinator._empty_issue_reported is False

    with (
        caplog.at_level(logging.INFO),
        patch.object(
            mock_api_client,
            "async_get_station_by_code",
            new=AsyncMock(return_value=[_make_train()]),
        ),
    ):
        await coordinator.async_refresh()

    assert _active_issue(hass, mock_config_entry) is None
    assert coordinator._empty_issue_reported is False
    recovered = [
        record
        for record in caplog.records
        if record.name == "custom_components.irish_rail.coordinator"
        and record.levelno == logging.INFO
        and "reporting train data again" in record.getMessage()
    ]
    assert len(recovered) == 1


def test_previous_unique_id_none_without_station_code(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """previous_unique_id() yields None when applied data lacks a code."""
    entry = _entry_with(data={"station_code": ""})
    coordinator = IrishRailDataUpdateCoordinator(hass, mock_api_client, entry)
    assert coordinator.previous_unique_id() is None

    # Sanity check: a normal entry still produces its identity.
    healthy = IrishRailDataUpdateCoordinator(hass, mock_api_client, _entry_with())
    assert healthy.previous_unique_id() == "PEARS_northbound"


# ── Downstream-stop learning persistence guards ─────────────────────────────


def _stops_at_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build an entry with an active ``stops_at`` filter."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            CONF_STATION: "Dublin Pearse",
            CONF_STATION_CODE: "PEARS",
            CONF_STOPS_AT: "Bray",
        },
        unique_id="PEARS_stopsat",
    )
    entry.add_to_hass(hass)
    return entry


async def test_learn_downstream_stops_survives_storage_failure(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A persisting store failure warns but never breaks the poll path."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, _stops_at_entry(hass)
    )
    mock_api_client.last_downstream_stop_names = {"Greystones"}

    failing_store = MagicMock()
    failing_store.async_record = AsyncMock(side_effect=OSError("disk full"))

    with (
        patch(
            "custom_components.irish_rail.coordinator.get_stops_store",
            return_value=failing_store,
        ),
        caplog.at_level(logging.WARNING),
    ):
        await coordinator._async_learn_downstream_stops()

    failing_store.async_record.assert_awaited_once()
    call_args = failing_store.async_record.await_args.args
    assert call_args[0] == "PEARS"
    assert call_args[1] == coordinator.direction
    assert call_args[2] == ["Greystones"]
    assert "Could not persist observed stops" in caplog.text


async def test_learn_downstream_stops_logs_matrix_updates(
    hass: HomeAssistant,
    mock_api_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A changed matrix is recorded with sorted stops and a debug line."""
    coordinator = IrishRailDataUpdateCoordinator(
        hass, mock_api_client, _stops_at_entry(hass)
    )
    mock_api_client.last_downstream_stop_names = {"Bray", "Greystones"}

    changing_store = MagicMock()
    changing_store.async_record = AsyncMock(return_value=True)

    with (
        patch(
            "custom_components.irish_rail.coordinator.get_stops_store",
            return_value=changing_store,
        ),
        caplog.at_level(logging.DEBUG),
    ):
        await coordinator._async_learn_downstream_stops()

    changing_store.async_record.assert_awaited_once()
    call_args = changing_store.async_record.await_args.args
    assert call_args[0] == "PEARS"
    assert call_args[2] == ["Bray", "Greystones"]
    assert "Stops matrix updated" in caplog.text
