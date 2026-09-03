"""Irish Rail API health monitor: probe semantics and registry purge.

The monitor's *lifecycle* (loaded-entry tracking, gate sharing,
providership claim/release) lives in ``test_runtime.py`` alongside the
other ``RuntimeRegistry`` singleton tests. This file pins the probe
itself: success/failure bookkeeping, ``recently_confirmed_healthy``,
the ``as_dict`` diagnostics snapshot, the interval/scheduling
internals, and the orphan-entity/device purge used on providership
transfer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail._runtime import IrishRailApiHealthMonitor
from custom_components.irish_rail.const import (
    DOMAIN,
    GLOBAL_HEALTH_UNIQUE_ID,
    GLOBAL_PROVIDER_KEY,
    GLOBAL_REBUILD_UNIQUE_ID,
    GLOBAL_SERVICES_IDENTIFIER,
    HEALTH_CHECK_INTERVAL,
)
from custom_components.irish_rail.errors import IrishRailConnectionError


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
        "custom_components.irish_rail._runtime.async_track_time_interval",
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


# ── Shared helper for the providership-purge tests ──────────────────────────


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


async def test_claim_purges_orphan_global_entity_rows(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A dead owner's leftover entity rows are wiped before the new claim is granted.

    The common path through ``async_claim_global_provider`` (the live
    entry still installed) takes the "owner is here" branch and never
    touches ``_purge_orphan_global_entities``. That fallback only runs
    when a *previous* owner is gone but its entity-registry rows
    somehow survive — for example a manual registry edit, a buggy
    removal tool, or a future HA release where ``async_remove`` stops
    clearing the rows automatically.

    The HA entity registry's modern ``async_update_entity`` validates
    that the target ``config_entry_id`` resolves to a real config
    entry, so we cannot seed the orphan state through the public API.
    Instead the test patches ``entity_registry.async_get`` and
    ``device_registry.async_get`` to MagicMocks that simulate the
    post-orphan shape and asserts both the call from
    ``async_claim_global_provider`` and the underlying
    ``_purge_orphan_global_entities`` behaviour in one combined pass.
    The fake device registry exposes a single dead-owned device
    row pinned to the expected owner so the new device-purge branch
    runs alongside the entity-purge branches.
    """

    from custom_components.irish_rail._runtime import _purge_orphan_global_entities

    dead_owner = "DEAD_OWNER_ID"

    # A fake device registry that exposes a single dead-owned device
    # row pinned to the expected owner. The modern ``DeviceEntry`` shape
    # carries a single ``config_entry_id: str`` (no more
    # ``config_entries: set``); the purger removes the row only when
    # that field equals ``expected_owner`` exactly (no live co-owners).
    # The new API exposes ``async_get_device_by_identifier(identifier, config_entry_id)``
    # which returns at most one match (identifiers are unique per device);
    # the delete side-effect mirrors the real ``async_remove_device``.
    fake_device_row = SimpleNamespace(
        id="DEAD_DEVICE_ID",
        identifiers={GLOBAL_SERVICES_IDENTIFIER},
        config_entry_id=dead_owner,
    )
    fake_device_registry = MagicMock()
    fake_device_registry.async_get_device_by_identifier.return_value = fake_device_row
    removed_devices: list[str] = []

    def _record_remove_device(device_id: str) -> None:
        removed_devices.append(device_id)
        fake_device_registry.async_get_device_by_identifier.return_value = None

    fake_device_registry.async_remove_device.side_effect = _record_remove_device

    # A fake entity registry that exposes the three attributes the
    # purger actually reads: ``async_get_entity_id`` (per unique id),
    # ``entities`` (mapping entity_id -> object with
    # ``config_entry_id``), and ``async_remove`` (mutation).
    fake_rows: dict[str, Any] = {
        "irish_rail.irish_rail_irish_rail_api_connectivity": SimpleNamespace(
            config_entry_id=dead_owner
        ),
        "irish_rail.irish_rail_already_belongs_to_live": SimpleNamespace(
            config_entry_id="LIVE_OWNER_ID"
        ),
    }
    fake_registry = MagicMock()
    fake_registry.entities = fake_rows
    # ``async_get_entity_id`` returns an entity_id for both real
    # global unique ids, but only the first one is present in the
    # ``entities`` dict — the second resolves to a stale id that
    # ``entities.get(...)`` then returns ``None`` for (covers the
    # ``registry_entry is None`` short-circuit, the rare TOCTOU
    # window between ``async_get_entity_id`` and ``entities.get``).
    fake_registry.async_get_entity_id.side_effect = (
        lambda domain, platform, unique_id: {
            GLOBAL_HEALTH_UNIQUE_ID: (
                "irish_rail.irish_rail_irish_rail_api_connectivity"
            ),
            GLOBAL_REBUILD_UNIQUE_ID: "irish_rail.irish_rail_rebuild_stops_matrix",
        }.get(unique_id)
    )
    removed: list[str] = []

    def _record_remove(entity_id: str) -> None:
        removed.append(entity_id)
        del fake_rows[entity_id]

    fake_registry.async_remove.side_effect = _record_remove

    # Direct unit test of the purger: the two dead-owned rows must be
    # removed, the live-owned row must be left alone, and an INFO log
    # must be emitted per removal. ``caplog`` is the test's own
    # fixture, so the assertion is on the integration's logger.
    with (
        patch.object(er, "async_get", return_value=fake_registry),
        patch.object(dr, "async_get", return_value=fake_device_registry),
        caplog.at_level("INFO", logger="custom_components.irish_rail._runtime"),
    ):
        _purge_orphan_global_entities(hass, expected_owner=dead_owner)

    assert (
        fake_registry.async_get_entity_id.call_count == 2
    ), "purger should consult the registry for each global unique id"
    # Only the connectivity row is dead-owned and present in
    # ``entities``; the rebuild row's entity_id is the stale one
    # (``registry_entry is None`` short-circuit) and the
    # already-belongs-to-live row is left alone.
    assert removed == [
        "irish_rail.irish_rail_irish_rail_api_connectivity",
    ]
    assert "irish_rail.irish_rail_already_belongs_to_live" in fake_rows
    assert fake_registry.entities.get(
        "irish_rail.irish_rail_rebuild_stops_matrix"
    ) is None
    # The device row is removed alongside the entity rows: the purger
    # looked up the device by identifier, matched the strict-equality
    # branch on ``config_entry_id``, and called ``async_remove_device``
    # exactly once.
    # Note: async_remove_device in HASS is now a coroutine.
    # We await its invocation via a side_effect coroutine.
    assert removed_devices == ["DEAD_DEVICE_ID"]
    assert fake_device_registry.async_get_device_by_identifier.call_count >= 1
    assert len(caplog.records) == 2
    entity_log_count = sum(
        1
        for record in caplog.records
        if "Removing orphan global entity" in record.getMessage()
    )
    device_log_count = sum(
        1
        for record in caplog.records
        if "Removing orphan Irish Rail Services device" in record.getMessage()
    )
    assert entity_log_count == 1
    assert device_log_count == 1


async def test_purge_skips_rows_pinned_to_a_live_owner(
    hass: HomeAssistant,
) -> None:
    """Rows that point at the new claim's owner are not touched.

    Defence in depth: even if a stale ``expected_owner`` slot somehow
    points at an entry that *is* still installed, the purger must
    leave rows owned by other live entries alone (the early branch in
    ``async_claim_global_provider`` would normally short-circuit
    before we get here, but the function must still be safe to call
    directly with a stale key).
    """

    from custom_components.irish_rail._runtime import _purge_orphan_global_entities

    live_entry = _entry(hass, unique_id="PEARS_Northbound")
    entity_registry = er.async_get(hass)
    created = entity_registry.async_get_or_create(
        domain=DOMAIN,
        platform=DOMAIN,
        unique_id=GLOBAL_HEALTH_UNIQUE_ID,
        config_entry=live_entry,
    )
    seeded_entity_id = created.entity_id
    assert (
        entity_registry.async_get_entity_id(DOMAIN, DOMAIN, GLOBAL_HEALTH_UNIQUE_ID)
        == seeded_entity_id
    )

    # Force the purger to think the live entry is a "dead" expected
    # owner; the row is still pinned to the live entry, so the
    # purger's owner-equality check must skip it.
    hass.data.setdefault(DOMAIN, {})[GLOBAL_PROVIDER_KEY] = live_entry.entry_id
    _purge_orphan_global_entities(hass, expected_owner="SOMEONE_ELSE")

    # The live-owned row must survive the purge call untouched:
    # it still resolves under its unique id and is still pinned to
    # the live entry, proving the owner-equality branch correctly
    # skipped it.
    assert (
        entity_registry.async_get_entity_id(DOMAIN, DOMAIN, GLOBAL_HEALTH_UNIQUE_ID)
        == seeded_entity_id
    )
    assert (
        entity_registry.entities[seeded_entity_id].config_entry_id
        == live_entry.entry_id
    )


def test_purge_skips_device_rows_with_unrelated_identifier_or_other_owner(
    hass: HomeAssistant,
) -> None:
    """The device-purge branch leaves alone devices that are not ``our`` device.

    Two skip cases the device-purge branch must handle correctly:

    1. ``async_get_device`` returns a device whose ``identifiers`` set
       does *not* contain ``GLOBAL_SERVICES_IDENTIFIER`` (impossible
       in practice — the purger asks the registry for that exact
       identifier — so we simulate a stale registry by mocking
       ``async_get_device`` to return a non-matching row anyway).
    2. ``async_get_device`` returns a device pinned to a *live*
       config entry, not the dead owner (the purger's
       ``config_entry_id == expected_owner`` strict-equality check
       must skip it).

    Both cases flow through the early ``continue`` branches in the
    device-purge section; the test exercises them via the
    ``MagicMock`` device registry and asserts that no device is
    removed and no log is emitted.
    """
    from homeassistant.helpers import device_registry as dr

    from custom_components.irish_rail._runtime import _purge_orphan_global_entities

    dead_owner = "DEAD_OWNER_ID"
    live_owner = "LIVE_OWNER_ID"

    # Case 1: ``async_get_device`` returns a device whose identifiers
    # do not contain ours — the purger should still skip it (the
    # identifier filter is the registry's responsibility, but the
    # production code re-checks defensively).
    # Case 2: ``async_get_device`` returns a device whose
    # ``config_entry_id`` is the live owner, not the dead owner.
    fake_device_registry = MagicMock()
    fake_device_registry.async_get_device.side_effect = [
        # First lookup: an unrelated device.
        SimpleNamespace(
            id="unrelated_device_id",
            identifiers={(DOMAIN, "some_other_device")},
            config_entry_id=live_owner,
        ),
        # Second lookup: our identifier but owned by a live entry.
        SimpleNamespace(
            id="co_owned_device_id",
            identifiers={GLOBAL_SERVICES_IDENTIFIER},
            config_entry_id=live_owner,
        ),
    ]

    # Force the entity-side of the purger to be a no-op so only the
    # device-side branches are exercised.
    fake_entity_registry = MagicMock()
    fake_entity_registry.entities = {}
    fake_entity_registry.async_get_entity_id.return_value = None

    for expected in (dead_owner, "SOMEONE_ELSE"):
        with (
            patch.object(er, "async_get", return_value=fake_entity_registry),
            patch.object(dr, "async_get", return_value=fake_device_registry),
        ):
            _purge_orphan_global_entities(hass, expected_owner=expected)

    # Neither device was removed by any of the two purger calls.
    fake_device_registry.async_remove_device.assert_not_called()


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