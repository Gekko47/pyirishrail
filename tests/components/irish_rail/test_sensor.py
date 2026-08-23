"""Tests for the Irish Rail sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import (
    IrishRailConnectionError,
    TrainDueTime,
)
from custom_components.irish_rail.const import DOMAIN
from custom_components.irish_rail.sensor import IrishRailDueTrainSensor


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
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
    which the coordinator converts into ``UpdateFailed``): all four sensors
    must report ``unavailable`` immediately after a failed refresh, then
    become available with fresh values on the next successful refresh.
    """
    entry = await _setup_entry(hass, [_mock_train()])
    coordinator = entry.runtime_data.coordinator

    keys = (
        "next_train_due",
        "next_train_destination",
        "next_train_delay",
        "next_train_type",
    )

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train(due_in=15)],
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    due_state = hass.states.get(entity_ids["next_train_due"])
    assert due_state is not None
    assert due_state.state == "15"
    assert due_state.attributes["api_reachable"] is True
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
        assert state.state == "10"
