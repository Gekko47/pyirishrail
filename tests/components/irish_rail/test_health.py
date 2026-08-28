"""Tests for the shared Irish Rail API health monitor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import IrishRailConnectionError
from custom_components.irish_rail.const import (
    DOMAIN,
    HEALTH_CHECK_INTERVAL,
)
from custom_components.irish_rail.health import (
    IrishRailApiHealthMonitor,
    async_claim_global_provider,
    async_note_entry_loaded,
    async_note_entry_unloaded,
    get_health_monitor,
)


def _client(error: Exception | None = None) -> MagicMock:
    """Build a mock IrishRailClient whose station probe can fail."""
    client = MagicMock()
    if error is not None:
        client.async_get_station_by_code = AsyncMock(side_effect=error)
    else:
        client.async_get_station_by_code = AsyncMock(return_value=[MagicMock()])
    return client


async def test_ping_success_records_health(hass: HomeAssistant) -> None:
    """A successful probe flips every health indicator to green."""
    monitor = IrishRailApiHealthMonitor(hass, _client())
    fired: list[bool] = []
    monitor.listeners.add(lambda: fired.append(True))

    await monitor.async_ping()

    assert monitor.healthy is True
    assert monitor.recently_confirmed_healthy is True
    assert monitor.consecutive_failures == 0
    assert monitor.last_error is None
    assert monitor.last_success is not None
    assert fired == [True]
    snapshot = monitor.as_dict()
    assert snapshot["healthy"] is True
    assert snapshot["last_success"] is not None
    assert snapshot["last_error"] is None
    assert snapshot["interval_minutes"] == (
        HEALTH_CHECK_INTERVAL.total_seconds() / 60
    )


async def test_not_yet_probed_reports_unconservative_true_only_on_evidence(
    hass: HomeAssistant,
) -> None:
    """Until a probe succeeds, reachability stays conservatively negative."""
    monitor = IrishRailApiHealthMonitor(hass, _client())
    assert monitor.healthy is None
    assert monitor.recently_confirmed_healthy is False


async def test_ping_failure_then_recovery(hass: HomeAssistant) -> None:
    """Failures accumulate consecutively and reset cleanly on success."""
    monitor = IrishRailApiHealthMonitor(
        hass, _client(error=IrishRailConnectionError("api down"))
    )

    await monitor.async_ping()
    await monitor.async_ping()

    assert monitor.healthy is False
    assert monitor.recently_confirmed_healthy is False
    assert monitor.consecutive_failures == 2
    assert monitor.last_failure is not None
    assert monitor.last_error is not None
    assert "api down" in monitor.last_error

    monitor.client = _client()
    await monitor.async_ping()

    assert monitor.healthy is True
    assert monitor.consecutive_failures == 0
    assert monitor.last_error is None


async def test_ping_catches_unexpected_exceptions(hass: HomeAssistant) -> None:
    """Any non-IrishRail exception still lands as an unhealthy probe."""
    monitor = IrishRailApiHealthMonitor(
        hass, _client(error=RuntimeError("socket exploded"))
    )

    await monitor.async_ping()

    assert monitor.healthy is False
    assert monitor.recently_confirmed_healthy is False
    assert monitor.last_error == "RuntimeError: socket exploded"
    assert monitor.consecutive_failures == 1


async def test_as_dict_reports_monitor_lifecycle_flags() -> None:
    """The diagnostics snapshot distinguishes "timer on" from "probe in flight".

    A maintainer reading a "sensor is stuck on unknown" report needs to
    know whether the periodic probe is actually still running and
    whether one is in flight; both pieces of state are surfaced in
    ``as_dict()`` as ``timer_active`` and ``probe_in_flight``.
    """
    monitor = IrishRailApiHealthMonitor(MagicMock(), _client())
    snapshot = monitor.as_dict()
    assert snapshot["timer_active"] is False
    assert snapshot["probe_in_flight"] is False

    fake_task = MagicMock()
    fake_task.done.return_value = False
    monitor._unsub_interval = lambda: None
    monitor._ping_task = fake_task
    snapshot = monitor.as_dict()
    assert snapshot["timer_active"] is True
    assert snapshot["probe_in_flight"] is True

    # A done task is no longer "in flight".
    fake_task.done.return_value = True
    snapshot = monitor.as_dict()
    assert snapshot["probe_in_flight"] is False


async def test_async_start_is_idempotent_and_probes_immediately(
    hass: HomeAssistant,
) -> None:
    """Repeated starts subscribe once; the initial probe fires right away."""
    client = _client()
    monitor = IrishRailApiHealthMonitor(hass, client)

    intervals: list[timedelta] = []
    unsub_calls: list[bool] = []

    def fake_track(
        _hass: HomeAssistant,
        _cb: object,
        interval: timedelta,
    ) -> Callable[[], None]:
        intervals.append(interval)
        return lambda: unsub_calls.append(True)

    with patch(
        "custom_components.irish_rail.health.async_track_time_interval",
        side_effect=fake_track,
    ):
        await monitor.async_start()
        await monitor.async_start()  # idempotent: no double subscription

        assert intervals == [HEALTH_CHECK_INTERVAL]
        await hass.async_block_till_done()
        assert client.async_get_station_by_code.await_count == 1

        await monitor.async_stop()
        await monitor.async_stop()  # double stop is harmless
        assert unsub_calls == [True]


# ── Singleton lifecycle across config entries ───────────────────────────────


def _entry(
    hass: HomeAssistant, unique_id: str = "PEARS_Northbound"
) -> MockConfigEntry:
    """Register one minimal Irish Rail config entry on hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    return entry


async def test_monitor_lifecycle_counts_loaded_entries(hass: HomeAssistant) -> None:
    """The monitor starts once, survives sibling loads and stops at zero."""
    client = _client()

    await async_note_entry_loaded(hass, client)
    first = get_health_monitor(hass)
    assert isinstance(first, IrishRailApiHealthMonitor)

    # A second entry reuses the same singleton without restarting anything.
    await async_note_entry_loaded(hass, client)
    assert get_health_monitor(hass) is first
    # Internal detail, checked deliberately: one running subscription.
    assert first._unsub_interval is not None

    await async_note_entry_unloaded(hass)
    assert get_health_monitor(hass) is first
    assert first._unsub_interval is not None

    # Only when the last entry unloads does probing pause.
    await async_note_entry_unloaded(hass)
    assert get_health_monitor(hass) is first
    assert first._unsub_interval is None

    # And it restarts cleanly for subsequent entries.
    await async_note_entry_loaded(hass, client)
    assert first._unsub_interval is not None

    # Leave no lingering interval timer for the next test.
    await first.async_stop()


async def test_first_setup_claims_global_provider(hass: HomeAssistant) -> None:
    """The first claiming entry wins; siblings are denied, owner sticky."""
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")

    assert async_claim_global_provider(hass, entry_one) is True
    assert async_claim_global_provider(hass, entry_two) is False
    # Owner re-claiming stays True.
    assert async_claim_global_provider(hass, entry_one) is True


async def test_claim_is_freed_when_owner_is_removed(
    hass: HomeAssistant,
) -> None:
    """Removing the owning entry (not merely unloading) frees the claim."""
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")

    assert async_claim_global_provider(hass, entry_one) is True

    # Unload must NOT transfer ownership mid-session.
    assert await hass.config_entries.async_unload(entry_one.entry_id)
    assert async_claim_global_provider(hass, entry_two) is False

    # Full removal frees the claim for the next setup.
    await hass.config_entries.async_remove(entry_one.entry_id)
    assert async_claim_global_provider(hass, entry_two) is True


# ── Scheduling internals ────────────────────────────────────────────────────


async def test_async_tick_runs_one_probe_directly(hass: HomeAssistant) -> None:
    """The interval callback entry point performs exactly one probe."""
    client = _client()
    monitor = IrishRailApiHealthMonitor(hass, client)
    await monitor._async_tick(dt_util.now())
    assert client.async_get_station_by_code.await_count == 1
    assert monitor.recently_confirmed_healthy is True


async def test_schedule_ping_coalesces_while_a_probe_is_pending(
    hass: HomeAssistant,
) -> None:
    """A second schedule call never stacks a second in-flight probe."""
    monitor = IrishRailApiHealthMonitor(hass, _client())
    release = asyncio.Event()
    started = asyncio.Event()
    calls = {"count": 0}

    async def slow_ping() -> None:
        calls["count"] += 1
        started.set()
        await release.wait()

    first_task: asyncio.Task[None] | None = None
    with patch.object(monitor, "async_ping", new=slow_ping):
        monitor.schedule_ping()
        await started.wait()
        pending = monitor._ping_task
        assert pending is not None

        # While that probe is still running, rescheduling must be a no-op.
        monitor.schedule_ping()
        assert monitor._ping_task is pending

        release.set()
        first_task = monitor._ping_task
        if first_task is not None:
            await first_task

    assert calls["count"] == 1
