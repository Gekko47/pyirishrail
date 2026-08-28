"""Sensor platform for the Irish Rail integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TrainDueTime
from .coordinator import IrishRailDataUpdateCoordinator, resolve_num_trains
from .entity import IrishRailEntity
from .types import IrishRailRuntimeData

# Number of entities updated in parallel on this platform (Silver rule
# ``parallel-updates``). This platform is read-only and every entity shares a
# single DataUpdateCoordinator refresh, so per-entity updates are pure
# in-memory property reads with no outbound calls. Per the official rule
# guidance for coordinator-based read-only platforms (sensor), 0 explicitly
# declares that no artificial serialization limit is needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Irish Rail sensors."""
    data: IrishRailRuntimeData = entry.runtime_data
    coordinator = data.coordinator

    # We create several sensors for the configured station
    sensors = [
        IrishRailDueTrainSensor(coordinator, "next_train_due"),
        IrishRailDueTrainSensor(coordinator, "next_train_destination"),
        IrishRailDueTrainSensor(coordinator, "next_train_delay"),
        IrishRailDueTrainSensor(coordinator, "next_train_type"),
    ]

    async_add_entities(sensors)


class IrishRailDueTrainSensor(IrishRailEntity, SensorEntity):
    """Sensor showing next due train details."""

    def __init__(
        self, coordinator: IrishRailDataUpdateCoordinator, entity_key: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entity_key)
        self._attr_translation_key = entity_key

        if entity_key in ("next_train_due", "next_train_delay"):
            # The Irish Rail API exposes times in whole minutes, so we
            # publish the raw minute count as a plain int with the DURATION
            # device class and ``UnitOfTime.MINUTES``; HA's modern DURATION
            # rendering then formats the value live as "X min" (or
            # "H h M min" over an hour) in the UI and in template results.
            # This is the supported HA 2026.x path for a DURATION sensor
            # with a minute unit — ``timedelta`` values are rejected by the
            # numeric-rendering fast path the moment a unit is declared.
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> str | int | None:
        """Return the state of the sensor.

        The DURATION sensors (``next_train_due`` and ``next_train_delay``)
        return a whole-minute ``int``; HA's modern DURATION device class
        formats the value live as "X min" in the UI. Textual sensors
        return the raw API value.
        """
        if not self.coordinator.data:
            return None

        # Next train is the first item in the response
        next_train: TrainDueTime = self.coordinator.data[0]

        if self.entity_key == "next_train_due":
            return next_train.due_in_mins
        if self.entity_key == "next_train_destination":
            return next_train.destination
        if self.entity_key == "next_train_delay":
            return next_train.late_mins
        if self.entity_key == "next_train_type":
            return next_train.type

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes if any train data exists."""
        data = self.coordinator.data
        if data is None:
            # Unsuccessful or incomplete refresh — no attributes.
            return None

        if not data:
            # Successful refresh with zero trains scheduled. The API is
            # reachable, so report that explicitly instead of exiting
            # before the attributes are populated.
            return {"api_reachable": True, "upcoming_trains": []}

        next_train: TrainDueTime = data[0]
        attrs: dict[str, Any] = {
            "origin": next_train.origin,
            "origin_time": next_train.origin_time,
            "destination_time": next_train.destination_time,
            "expected_arrival_time": next_train.expected_arrival_time,
            "expected_departure_time": next_train.expected_departure_time,
            "scheduled_arrival_time": next_train.scheduled_arrival_time,
            "scheduled_departure_time": next_train.scheduled_departure_time,
            "direction": next_train.direction,
            "train_code": next_train.code,
        }

        # Expose the raw minute counts as attributes so automations and
        # templates that need an integer (e.g. ``{{ states('sensor.x') |
        # int }}``) keep working even though the primary state is now a
        # ``timedelta`` rendered by HA's DURATION device class.
        attrs["due_in_mins"] = next_train.due_in_mins
        attrs["late_mins"] = next_train.late_mins

        # Explicitly distinguish "API reachable, zero trains scheduled"
        # (this attribute is present and True) from an API failure. On a
        # failed refresh the coordinator marks the entity unavailable, so
        # this attribute can only be read when the API responded.
        attrs["api_reachable"] = True

        # Upcoming trains: the next N trains as configured for the entry
        # (default 3). Read defensively — the list may hold fewer trains
        # than requested; it simply comes back shorter.
        num_trains = resolve_num_trains(self.coordinator.config_entry)
        attrs["upcoming_trains"] = [
            {
                "due_in_mins": train.due_in_mins,
                "destination": train.destination,
                "late_mins": train.late_mins,
                "type": train.type,
                "train_code": train.code,
                "origin_time": train.origin_time,
                "destination_time": train.destination_time,
            }
            for train in data[:num_trains]
        ]

        return attrs
