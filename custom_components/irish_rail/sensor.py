"""Sensor platform for the Irish Rail integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import TrainDueTime
from .coordinator import IrishRailDataUpdateCoordinator
from .entity import IrishRailEntity
from .types import IrishRailRuntimeData


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

        if entity_key == "next_train_due" or entity_key == "next_train_delay":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | int | None:
        """Return the state of the sensor."""
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
        """Return extra state attributes if next train exists."""
        if not self.coordinator.data:
            return None

        next_train: TrainDueTime = self.coordinator.data[0]
        return {
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
