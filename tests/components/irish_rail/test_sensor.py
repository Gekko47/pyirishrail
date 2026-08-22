"""Tests for the Irish Rail sensor platform."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import TrainDueTime


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
        direction="Northbound",
        location_type="S",
    )


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a mock config entry with one due train."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_Northbound",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[_make_train()],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


async def test_sensors_created_with_stable_unique_ids(
    hass: HomeAssistant,
) -> None:
    """Test that four sensors are created with stable unique IDs."""
    await _setup_entry(hass)

    expected_suffixes = [
        "next_train_due",
        "next_train_destination",
        "next_train_delay",
        "next_train_type",
    ]
    for suffix in expected_suffixes:
        entity_id = f"sensor.dublin_pearse_{suffix}"
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} missing"

    # Unique IDs are derived from the config entry unique ID + static suffix.
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    for suffix in expected_suffixes:
        entry = registry.async_get_entity_id(
            "sensor", "irish_rail", f"PEARS_Northbound_{suffix}"
        )
        assert entry is not None, f"unique id PEARS_Northbound_{suffix} missing"


async def test_sensor_values_and_attributes(hass: HomeAssistant) -> None:
    """Test sensor native values and extra state attributes."""
    await _setup_entry(hass)

    due_state = hass.states.get("sensor.dublin_pearse_next_train_due")
    assert due_state is not None
    assert due_state.state == "10"
    assert due_state.attributes["unit_of_measurement"] == "min"
    assert due_state.attributes["device_class"] == "duration"

    dest_state = hass.states.get("sensor.dublin_pearse_next_train_destination")
    assert dest_state is not None
    assert dest_state.state == "Bray"

    delay_state = hass.states.get("sensor.dublin_pearse_next_train_delay")
    assert delay_state is not None
    assert delay_state.state == "2"

    type_state = hass.states.get("sensor.dublin_pearse_next_train_type")
    assert type_state is not None
    assert type_state.state == "DART"

    # Extra attributes are attached to every sensor.
    attrs = due_state.attributes
    assert attrs["origin"] == "Howth"
    assert attrs["train_code"] == "E123"
    assert attrs["scheduled_arrival_time"] == "12:00"


async def test_sensor_unknown_when_no_trains(hass: HomeAssistant) -> None:
    """Test sensors report unknown when the API returns no trains."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_Northbound",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.dublin_pearse_next_train_due")
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_sensor_unavailable_after_update_failure(
    hass: HomeAssistant,
) -> None:
    """Test sensors become unavailable when a coordinator update fails."""
    entry = await _setup_entry(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        side_effect=Exception("boom"),
    ):
        # Force a refresh that fails.
        coordinator = entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get("sensor.dublin_pearse_next_train_due")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
