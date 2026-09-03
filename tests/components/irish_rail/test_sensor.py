"""Tests for the Irish Rail sensor platform."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.const import DOMAIN
from custom_components.irish_rail.errors import IrishRailConnectionError
from custom_components.irish_rail.models import TrainDueTime
from custom_components.irish_rail.sensor import (
    IrishRailDueTrainSensor,
    _parse_expected_arrival,
)


def _mock_train(due_in: int = 10) -> TrainDueTime:
    """Return a representative TrainDueTime for tests."""
    return TrainDueTime(
        code=f"E{due_in}",
        origin="Howth",
        destination="Bray",
        origin_time="12:00",
        destination_time="13:00",
        due_in_mins=due_in,
        late_mins=0,
        expected_arrival_time="12:10",
        expected_departure_time="12:11",
        scheduled_arrival_time="12:00",
        scheduled_departure_time="12:01",
        type="DART",
        direction="Northbound",
        location_type="S",
    )


def _train_with_offset(
    base: TrainDueTime, due_in_mins: int, expected_arrival_time: str
) -> TrainDueTime:
    """Return a copy of ``base`` with the offset + ``HH:MM`` rewritten."""
    return replace(
        base, due_in_mins=due_in_mins, expected_arrival_time=expected_arrival_time
    )


def _train_without_offset(
    base: TrainDueTime, expected_arrival_time: str
) -> TrainDueTime:
    """Return a copy of ``base`` whose ``due_in_mins`` is forced to ``None``.

    ``due_in_mins`` is typed ``int`` on the model, so the test must
    cast through the runtime value to exercise the defensive fallback
    branch the type system says is unreachable.
    """
    cleared = replace(base, due_in_mins=0, expected_arrival_time=expected_arrival_time)
    # The dataclass is frozen, so the field is reset via object.__setattr__
    # to keep the test surface for the defensive fallback intact.
    object.__setattr__(cleared, "due_in_mins", None)
    return cleared


async def _setup_entry(
    hass: HomeAssistant, trains: list[TrainDueTime]
) -> MockConfigEntry:
    """Add and fully set up a mock config entry with the given train data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Northbound)",
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
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=trains,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entity_id_for(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    """Return the entity id of the sensor with the given entity key."""
    registry = er.async_get(hass)
    return next(
        e.entity_id
        for e in registry.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.unique_id.endswith(f"_{key}")
    )


async def test_empty_train_list_reports_api_reachable(
    hass: HomeAssistant,
) -> None:
    """A successful refresh with zero trains still populates attributes."""
    entry = await _setup_entry(hass, [])

    entity_id = _entity_id_for(hass, entry, "next_train_due")
    state = hass.states.get(entity_id)
    assert state is not None
    # The API responded, so the attributes must be present even though
    # there are no trains.
    assert state.attributes["api_reachable"] is True
    assert state.attributes["upcoming_trains"] == []
    # No next-train details exist without trains; the state is unknown.
    assert state.state == "unknown"


async def test_failed_refresh_marks_entity_unavailable(
    hass: HomeAssistant,
) -> None:
    """A failed refresh makes the entity unavailable without attributes."""
    entry = await _setup_entry(hass, [])

    coordinator = entry.runtime_data.coordinator
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        side_effect=Exception("boom"),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    # The coordinator keeps its last successful data ([]), but marks the
    # refresh unsuccessful so the entity becomes unavailable.
    entity_id = _entity_id_for(hass, entry, "next_train_due")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"
    assert "api_reachable" not in state.attributes
    assert "upcoming_trains" not in state.attributes


async def test_all_entities_unavailable_after_failed_refresh_then_recover(
    hass: HomeAssistant,
) -> None:
    """Silver rule ``entity-unavailable``: the sensor always recovers.

    Uses the realistic failure path (the client raises ``IrishRailError``,
    which the coordinator converts into ``UpdateFailed``): the per-station
    sensor must report ``unavailable`` immediately after a failed refresh,
    then become available with fresh values on the next successful refresh.
    """
    entry = await _setup_entry(hass, [_mock_train()])
    coordinator = entry.runtime_data.coordinator

    keys = ("next_train_due",)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        side_effect=IrishRailConnectionError("connection lost"),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    entity_ids = {key: _entity_id_for(hass, entry, key) for key in keys}
    for key in keys:
        state = hass.states.get(entity_ids[key])
        assert state is not None
        assert state.state == "unavailable"

    # Recovery: the next successful refresh restores availability and values.
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train(due_in=15)],
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    due_state = hass.states.get(entity_ids["next_train_due"])
    assert due_state is not None
    # ``next_train_due`` is a TIMESTAMP sensor: the state is the
    # absolute datetime derived from the API's signed ``due_in_mins``
    # offset, not the ``HH:MM`` ``expected_arrival_time``. The mock
    # train's ``due_in_mins`` is 15 (see ``_mock_train(due_in=15)``);
    # the live countdown lives in the ``time_until_arrival``
    # attribute, so we assert the shape of the timestamp rather than
    # the wall-clock time (the test runs against the real wall clock,
    # which is unrelated to the API's ``12:10`` literal).
    import datetime as _dt
    parsed_state = _dt.datetime.fromisoformat(due_state.state)
    # The timestamp is tz-aware and resolves the offset to the same
    # timezone the integration runs in.
    assert parsed_state.tzinfo is not None
    assert due_state.attributes["api_reachable"] is True
    assert due_state.attributes["due_in_mins"] == 15
    # ``time_until_arrival`` is the integer seconds from now to the
    # expected arrival. The mock train's ``due_in_mins`` is 15, so the
    # countdown is approximately 900 s. A few seconds of jitter
    # between the refresh that produced the state and the assertion
    # that reads it are expected, so the assertion accepts a
    # 60-second band on either side of the declared offset.
    countdown = due_state.attributes["time_until_arrival"]
    assert 15 * 60 - 60 <= countdown <= 15 * 60 + 60
    # The new device attributes: full ISO 8601 arrival + train type.
    # The ``expected_arrival`` attribute is the ISO 8601 mirror of the
    # sensor's state, but with the offset-based parsing logic the
    # attribute is recomputed on every read (the state was frozen at
    # the last refresh instant), so a strict equality check is too
    # brittle — the two values agree to within a second of jitter.
    expected_arrival_dt = _dt.datetime.fromisoformat(
        due_state.attributes["expected_arrival"]
    )
    assert abs((expected_arrival_dt - parsed_state).total_seconds()) < 5
    assert due_state.attributes["train_type"] == "DART"
    for key in keys:
        state = hass.states.get(entity_ids[key])
        assert state is not None
        assert state.state != "unavailable"


def test_none_data_returns_no_attributes() -> None:
    """Unsuccessful or incomplete refreshes (data is None) yield no attrs."""
    coordinator = MagicMock()
    coordinator.data = None
    sensor = IrishRailDueTrainSensor(coordinator, "next_train_due")
    assert sensor.extra_state_attributes is None


async def test_non_empty_data_keeps_next_train_attributes(
    hass: HomeAssistant,
) -> None:
    """Non-empty data retains the existing attribute behaviour."""
    entry = await _setup_entry(hass, [_mock_train()])

    entity_id = _entity_id_for(hass, entry, "next_train_due")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["api_reachable"] is True
    assert len(state.attributes["upcoming_trains"]) == 1
    # ``next_train_due`` is a TIMESTAMP sensor: the state is an
    # ISO 8601 datetime derived from the API's signed
    # ``due_in_mins`` offset (the ``HH:MM`` ``expected_arrival_time``
    # is the legacy fallback). The mock train's ``due_in_mins`` is
    # 10 (see ``_mock_train``); the live countdown lives in the
    # ``time_until_arrival`` attribute, so we assert the shape of
    # the timestamp rather than the wall-clock time (the test runs
    # against the real wall clock, which is unrelated to the API's
    # ``12:10`` literal).
    import datetime as _dt
    parsed_state = _dt.datetime.fromisoformat(state.state)
    assert parsed_state.tzinfo is not None
    assert state.attributes["train_type"] == "DART"
    # The expected arrival is mirrored as an ISO 8601 string. With
    # the offset-based parsing logic the attribute is recomputed on
    # every read (the state was frozen at the last refresh
    # instant), so a strict equality check is too brittle — the
    # two values agree to within a second of jitter.
    expected_arrival_dt = _dt.datetime.fromisoformat(
        state.attributes["expected_arrival"]
    )
    assert abs((expected_arrival_dt - parsed_state).total_seconds()) < 5
    # The countdown is approximately 10 minutes (the default
    # ``due_in_mins`` the mock provides), with a 60-second band
    # on either side for the gap between the refresh and the
    # assertion.
    countdown = state.attributes["time_until_arrival"]
    assert 10 * 60 - 60 <= countdown <= 10 * 60 + 60


def test_parse_expected_arrival_handles_blank_and_unparseable_inputs() -> None:
    """Blank / malformed ``expected_arrival_time`` returns ``None``.

    The sensor's ``native_value`` falls back to ``None`` for an
    unparseable value (so the UI shows "unknown" instead of a bogus
    timestamp). Direct unit tests for the failure paths complement
    the end-to-end coverage in the live tests above.
    """
    from datetime import datetime

    now = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    # The blank / malformed cases force the fallback ``HH:MM`` path
    # by clearing ``due_in_mins`` (the API's signed offset is the
    # canonical path, so a degraded payload without it must still
    # fail safe). The well-formed case keeps the offset and uses it
    # to resolve the absolute timestamp. ``due_in_mins`` is typed
    # ``int`` on the model, so the ``None`` cases are cast on the
    # argument to exercise the defensive branch the type system says
    # is unreachable.
    base = _mock_train(due_in=10)

    # Blank / empty: return None rather than today's midnight.
    assert _parse_expected_arrival(
        _train_without_offset(base, expected_arrival_time=""),
        now,
    ) is None
    # Malformed hour: return None.
    assert _parse_expected_arrival(
        _train_without_offset(base, expected_arrival_time="not a time"),
        now,
    ) is None
    # Malformed minute: return None.
    assert _parse_expected_arrival(
        _train_without_offset(base, expected_arrival_time="12"),
        now,
    ) is None
    assert _parse_expected_arrival(
        _train_without_offset(base, expected_arrival_time="12:xx"),
        now,
    ) is None
    # Well-formed: the signed offset drives the absolute timestamp.
    # ``now=10:00`` and ``due_in_mins=225`` (3h45m) lands at 13:45
    # today, which matches the ``HH:MM`` carried alongside.
    parsed = _parse_expected_arrival(
        _train_with_offset(base, due_in_mins=225, expected_arrival_time="13:45"),
        now,
    )
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.time().hour == 13
    assert parsed.time().minute == 45
    assert parsed.date() == now.date()


def test_parse_expected_arrival_fallback_resolves_today_from_hhmm() -> None:
    """Well-formed ``HH:MM`` without the offset resolves onto today.

    The defensive fallback (no signed offset, valid ``HH:MM``) must
    still produce a timestamp: the arrival is placed on ``now``'s
    calendar with ``now``'s timezone, so a degraded payload renders a
    real time instead of ``unknown``.
    """
    from datetime import UTC, datetime

    now = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    base = _mock_train(due_in=10)

    parsed = _parse_expected_arrival(
        _train_without_offset(base, expected_arrival_time="13:45"),
        now,
    )
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.hour == 13
    assert parsed.minute == 45
    assert parsed.date() == now.date()


def test_parse_expected_arrival_keeps_past_timestamp_for_overdue_display() -> None:
    """Past timestamps are kept as-is so HA renders "5 min ago" not "1970".

    A train whose ``due_in_mins`` is already negative on today's wall
    clock is a real edge case (the API's data is genuinely overdue).
    The function lands the timestamp in the past so HA's "Time" card
    renders a relative "X min ago".
    """
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 8, 28, 14, 30, tzinfo=UTC)
    parsed = _parse_expected_arrival(
        replace(_mock_train(due_in=10), due_in_mins=-45, expected_arrival_time="13:45"),
        now,
    )
    assert parsed is not None
    # The signed offset drives the absolute timestamp: 45 min before
    # ``now`` lands at 13:45 today, which is in the past. HA's
    # "Time" card renders it as a relative "X min ago".
    assert parsed == now + timedelta(minutes=-45)
    assert parsed < now


def test_parse_expected_arrival_handles_overnight_poll_at_00_05() -> None:
    """Overnight regression: poll at 00:05 with an API time of 23:55.

    The poll runs after midnight and the API still reports the last
    service of the previous evening (``expected_arrival_time=23:55``,
    ``due_in_mins=-10`` — the API's signed offset correctly identifies
    it as departed). The function must land the timestamp 10 minutes
    before ``now`` on the previous calendar day, so HA renders it as
    "departed 10 min ago" rather than misreading it as a future 23:55
    today (which would be ~24 hours away and show as "in 23 h").
    """
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
    parsed = _parse_expected_arrival(
        replace(
            _mock_train(due_in=10),
            due_in_mins=-10,
            expected_arrival_time="23:55",
        ),
        now,
    )
    assert parsed is not None
    # The signed offset drives the absolute timestamp: 10 min before
    # the poll instant, which lands on the previous calendar day at
    # 23:55 — the same instant the API reported in ``HH:MM``.
    assert parsed == now + timedelta(minutes=-10)
    assert parsed.time() == datetime(2026, 8, 27, 23, 55, tzinfo=UTC).time()
    # The timestamp sits in the past so HA's TIMESTAMP renderer turns
    # it into a relative "10 min ago" (the documented overdue-service
    # display), regardless of the calendar date the value lands on.
    assert parsed < now
    # And it is *not* today's wall clock 23:55 (which would imply a
    # ~24 h countdown): the offset path is what fixed this.
    assert parsed.date() != now.date()
