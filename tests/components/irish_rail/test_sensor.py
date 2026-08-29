"""Tests for the Irish Rail sensor platform."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.const import DOMAIN
from custom_components.irish_rail.sensor import (
    IrishRailDueTrainSensor,
    _parse_expected_arrival,
)
from pyirishrail import (
    IrishRailConnectionError,
    TrainDueTime,
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
        "pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
        "pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
    """Silver rule ``entity-unavailable``: every entity, plus recovery.

    Uses the realistic failure path (the client raises ``IrishRailError``,
    which the coordinator converts into ``UpdateFailed``): all three sensors
    must report ``unavailable`` immediately after a failed refresh, then
    become available with fresh values on the next successful refresh. The
    previous fourth entity (``next_train_type``) was retired; the train
    type now lives on the device's attributes.
    """
    entry = await _setup_entry(hass, [_mock_train()])
    coordinator = entry.runtime_data.coordinator

    keys = (
        "next_train_due",
        "next_train_destination",
        "next_train_delay",
    )

    with patch(
        "pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
        "pyirishrail.api.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train(due_in=15)],
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    due_state = hass.states.get(entity_ids["next_train_due"])
    assert due_state is not None
    # ``next_train_due`` is a TIMESTAMP sensor: the state is the
    # API's expected arrival ``HH:MM`` combined with today's date, in
    # HA's local timezone. The mock train's ``expected_arrival_time``
    # is "12:10" (see ``_mock_train``); the live countdown lives in
    # the ``time_until_arrival`` attribute.
    import datetime as _dt
    parsed_state = _dt.datetime.fromisoformat(due_state.state)
    assert parsed_state.time() == _dt.time(12, 10)
    assert due_state.attributes["api_reachable"] is True
    assert due_state.attributes["due_in_mins"] == 15
    # ``time_until_arrival`` is the integer seconds from now to the
    # expected arrival. It is negative when the API reports a train
    # whose ``HH:MM`` is already in the past relative to the poll
    # instant (overnight services, or a fixed-clock poll running in
    # the evening); the magnitude is bounded by 24 hours, so the
    # attribute is always within ``[-86400, 86400]`` for a single
    # calendar day. The test asserts the shape, not the sign, so it
    # passes whether the CI clock is noon or midnight.
    countdown = due_state.attributes["time_until_arrival"]
    assert -86400 <= countdown <= 86400
    # The new device attributes: full ISO 8601 arrival + train type.
    assert due_state.attributes["expected_arrival"] == parsed_state.isoformat()
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


@pytest.mark.parametrize("key", ["next_train_due", "next_train_destination"])
async def test_non_empty_data_keeps_next_train_attributes(
    hass: HomeAssistant, key: str
) -> None:
    """Non-empty data retains the existing attribute behaviour."""
    entry = await _setup_entry(hass, [_mock_train()])

    entity_id = _entity_id_for(hass, entry, key)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["api_reachable"] is True
    assert len(state.attributes["upcoming_trains"]) == 1
    if key == "next_train_due":
        # ``next_train_due`` is a TIMESTAMP sensor: the state is an
        # ISO 8601 datetime of the API's expected arrival ``HH:MM``
        # combined with today's date in HA's local timezone. The mock
        # train's ``expected_arrival_time`` is "12:10".
        import datetime as _dt
        parsed_state = _dt.datetime.fromisoformat(state.state)
        assert parsed_state.time() == _dt.time(12, 10)
        # The train type is on the device as an attribute, not a
        # dedicated entity.
        assert state.attributes["train_type"] == "DART"
        # The expected arrival is mirrored as an ISO 8601 string.
        assert state.attributes["expected_arrival"] == parsed_state.isoformat()
        # ``next_train_destination`` is plain text and stays as the
        # API returned it.
    else:
        assert state.state == "Bray"


def test_unknown_entity_key_returns_none_value() -> None:
    """An unrecognized entity key falls back to a None native value."""
    coordinator = MagicMock()
    coordinator.data = [_mock_train()]
    coordinator.config_entry.unique_id = "PEARS_northbound"
    coordinator.station_name = "Dublin Pearse"
    coordinator.direction = "Northbound"

    sensor = IrishRailDueTrainSensor(coordinator, "not_a_real_key")
    assert sensor.native_value is None


def test_parse_expected_arrival_handles_blank_and_unparseable_inputs() -> None:
    """Blank / malformed ``expected_arrival_time`` returns ``None``.

    The sensor's ``native_value`` falls back to ``None`` for an
    unparseable value (so the UI shows "unknown" instead of a bogus
    timestamp). Direct unit tests for the failure paths complement
    the end-to-end coverage in the live tests above.
    """
    from datetime import datetime

    now = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)

    # Blank / empty: return None rather than today's midnight.
    assert _parse_expected_arrival("", now) is None
    # Malformed hour: return None.
    assert _parse_expected_arrival("not a time", now) is None
    # Malformed minute: return None.
    assert _parse_expected_arrival("12", now) is None
    assert _parse_expected_arrival("12:xx", now) is None
    # Well-formed: returns a tz-aware datetime on today's date.
    parsed = _parse_expected_arrival("13:45", now)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.time().hour == 13
    assert parsed.time().minute == 45
    assert parsed.date() == now.date()


def test_parse_expected_arrival_keeps_past_timestamp_for_overdue_display() -> None:
    """Past timestamps are kept as-is so HA renders "5 min ago" not "1970".

    A train whose ``expected_arrival_time`` has already passed on
    today's wall clock is a real edge case (poll runs at 00:05 and
    sees a 23:55 service, or the API's data is genuinely overdue). The
    function does *not* roll to yesterday — the past branch is the
    correct user-visible display.
    """
    from datetime import datetime

    now = datetime(2026, 8, 28, 14, 30, tzinfo=UTC)
    parsed = _parse_expected_arrival("13:45", now)
    assert parsed is not None
    # Stays on today's date; the value lands in the past, which HA's
    # "Time" card renders as a relative "X min ago".
    assert parsed.date() == now.date()
    assert parsed < now
