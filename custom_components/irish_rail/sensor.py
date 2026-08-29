"""Sensor platform for the Irish Rail integration."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from pyirishrail import TrainDueTime

from .coordinator import IrishRailDataUpdateCoordinator, resolve_num_trains
from .entity import IrishRailEntity
from .types import IrishRailConfigEntry, IrishRailRuntimeData

_LOGGER = logging.getLogger(__name__)

# Number of entities updated in parallel on this platform (Silver rule
# ``parallel-updates``). This platform is read-only and every entity shares a
# single DataUpdateCoordinator refresh, so per-entity updates are pure
# in-memory property reads with no outbound calls. Per the official rule
# guidance for coordinator-based read-only platforms (sensor), 0 explicitly
# declares that no artificial serialization limit is needed.
PARALLEL_UPDATES = 0


def _parse_expected_arrival(
    expected_arrival_time: str, now: datetime
) -> datetime | None:
    """Convert the API's ``HH:MM`` expected-arrival string into a real datetime.

    The Irish Rail API only exposes the wall-clock time of day, not a full
    timestamp. The date has to be inferred:

    * If the parsed ``HH:MM`` is in the **future** relative to ``now``,
      the service runs today — the date is ``now.date()``.
    * If the parsed ``HH:MM`` is already in the **past**, the service is
      either (a) overdue (it should have arrived minutes ago) or (b) a
      service that was scheduled for an earlier day. The honest
      representation of both is to keep the date as ``now.date()`` and let
      the timestamp land in the past — HA's "Time" card will then render
      "5 min ago" for the overdue case and an absolute past datetime for
      the scheduled-elsewhere case, both of which are user-visible and
      correct.

    The function never wraps into a different day: a poll at 00:15 that
    catches a 00:30 service sees the 00:30 timestamp as future and the
    state holds today's date. The overnight-edge case is the *past*
    branch, which is also covered: a 23:55 service observed at 00:05 is
    in the past, the timestamp lands in the past, and HA renders it as
    "departed".

    Returns ``None`` when the API field is blank or unparseable, so the
    sensor state can fall back to ``None`` rather than publish a bogus
    datetime.
    """
    if not expected_arrival_time:
        return None
    try:
        hour, minute = expected_arrival_time.split(":", 1)
        parsed_time = time(hour=int(hour), minute=int(minute))
    except (ValueError, IndexError):
        _LOGGER.debug(
            "Could not parse expected_arrival_time=%r as HH:MM",
            expected_arrival_time,
        )
        return None
    # ``datetime.combine`` yields a *naive* datetime, but ``now`` from
    # ``dt_util.utcnow()`` is timezone-aware. Combine with ``now``'s date
    # (so the year/month/day matches HA's clock) and attach the same
    # ``tzinfo`` so the subtraction below is well-defined. The HH:MM
    # wall-clock time is interpreted in HA's local timezone (matching
    # how the user reads the dashboard).
    naive = datetime.combine(now.date(), parsed_time)
    return naive.replace(tzinfo=now.tzinfo)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IrishRailConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Irish Rail sensors."""
    data: IrishRailRuntimeData = entry.runtime_data
    coordinator = data.coordinator

    # Three sensors per station: arrival timestamp, destination, and
    # delay. The previous ``next_train_type`` dedicated entity is gone
    # — its value now lives on the device's attributes (see
    # ``IrishRailDueTrainSensor.extra_state_attributes`` and the device
    # card), which is the canonical HA shape for a value that does not
    # warrant its own card-level state.
    sensors = [
        IrishRailDueTrainSensor(coordinator, "next_train_due"),
        IrishRailDueTrainSensor(coordinator, "next_train_destination"),
        IrishRailDueTrainSensor(coordinator, "next_train_delay"),
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

        if entity_key == "next_train_due":
            # The state is a datetime of the API's expected arrival time,
            # combined with today's date (or left in the past for an
            # overdue / earlier-day service — see
            # ``_parse_expected_arrival``). The TIMESTAMP device class
            # tells HA to render the value with the relative-time chip
            # in the default "Time" card, so a user reading the
            # dashboard sees both the wall-clock arrival and the live
            # "in 5 min" / "5 min ago" subtitle in a single place. No
            # ``native_unit_of_measurement`` is set because timestamp
            # sensors carry no unit.
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif entity_key == "next_train_delay":
            # "Late by X minutes" is a duration, not a clock time; keep
            # the DURATION + minutes shape. HA's DURATION renderer
            # formats the value live as "X min" (or "H h M min" over an
            # hour) in the UI and in template results.
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> str | int | datetime | None:
        """Return the state of the sensor.

        The TIMESTAMP sensor (``next_train_due``) returns a ``datetime``
        of the expected arrival. The DURATION sensor
        (``next_train_delay``) returns a whole-minute ``int``; HA's
        DURATION device class formats the value live as "X min" in the
        UI. Textual sensors return the raw API value.
        """
        if not self.coordinator.data:
            return None

        # Next train is the first item in the response
        next_train: TrainDueTime = self.coordinator.data[0]

        if self.entity_key == "next_train_due":
            return _parse_expected_arrival(
                next_train.expected_arrival_time,
                dt_util.utcnow(),
            )
        if self.entity_key == "next_train_destination":
            return next_train.destination
        if self.entity_key == "next_train_delay":
            return next_train.late_mins

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes if any train data exists.

        The ``next_train_due`` sensor carries the **live** countdown
        (``time_until_arrival``) so any consumer that wants the "X min"
        view can compute it once per read without polling. The
        ``expected_arrival`` attribute mirrors the sensor's primary
        state as an ISO 8601 string so dashboard widgets that prefer
        string attributes over timestamp states still have something to
        render. Both are present on the **device** as well as on the
        sensor, so the per-station device card surfaces them next to
        the destination and delay.
        """
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
        now = dt_util.utcnow()
        expected_arrival = _parse_expected_arrival(
            next_train.expected_arrival_time, now
        )
        time_until_arrival: timedelta | None = (
            expected_arrival - now if expected_arrival is not None else None
        )
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

        # The full datetime of expected arrival (the sensor's primary
        # state on the ``next_train_due`` entity) is mirrored here as
        # ``expected_arrival`` so device-level attribute readers and
        # string-rendering widgets have a single canonical key.
        if expected_arrival is not None:
            attrs["expected_arrival"] = expected_arrival.isoformat()
            if time_until_arrival is not None:
                # ``time_until_arrival`` is recomputed on every read, so
                # the value is genuinely live (the sensor state itself
                # is frozen at the last poll instant). Templates and
                # automations can read this directly: a "5 min" countdown
                # chip or a trigger condition ``< 1 min`` both work
                # without a custom template.
                attrs["time_until_arrival"] = int(time_until_arrival.total_seconds())

        # The previous dedicated ``next_train_type`` entity now lives
        # as a device-level attribute. The per-station device card
        # surfaces the train type alongside the other arrival metadata.
        attrs["train_type"] = next_train.type

        # Expose the raw minute counts as attributes so automations and
        # templates that need an integer (e.g.
        # ``{{ state_attr(..., "due_in_mins") }}``) keep working even
        # though the primary state is now a ``datetime`` rendered by
        # HA's TIMESTAMP device class.
        attrs["due_in_mins"] = next_train.due_in_mins
        attrs["late_mins"] = next_train.late_mins

        # Explicitly distinguish "API reachable, zero trains scheduled"
        # (this attribute is present and True) from an API failure. On
        # a failed refresh the coordinator marks the entity
        # unavailable, so this attribute can only be read when the API
        # responded.
        attrs["api_reachable"] = True

        # Upcoming trains: the next N trains as configured for the
        # entry (default 3). Read defensively — the list may hold fewer
        # trains than requested; it simply comes back shorter.
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
