"""Sensor platform for the Irish Rail integration."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import IrishRailDataUpdateCoordinator, resolve_num_trains
from .entity import IrishRailEntity
from .models import TrainDueTime
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
    train: TrainDueTime, now: datetime
) -> datetime | None:
    """Convert an API train record into a real ``datetime`` of expected arrival.

    The Irish Rail API exposes the wall-clock ``expected_arrival_time``
    (``HH:MM``) **and** a signed ``due_in_mins`` offset measured from the
    API's server clock. The offset is the canonical source of truth for
    the *date direction*: a positive value means the service is due in
    the future, a negative value means it has already passed (or the
    poll crossed midnight and is reporting yesterday's last service).

    The function therefore builds the absolute arrival as
    ``now + timedelta(minutes=due_in_mins)``:

    * A future service (positive offset) lands in the future, regardless
      of whether its ``HH:MM`` is before or after the current wall-clock
      time — so a 00:30 service polled at 23:55 correctly resolves to
      the next day 00:30 rather than being misread as today 00:30 in
      the past.
    * An overdue service (negative offset) lands in the past, which HA's
      "Time" card renders as a relative "X min ago". A 23:55 service
      observed at 00:05 yields a 23:55 timestamp on the previous
      calendar day, and the UI shows it as "departed 10 min ago".

    ``expected_arrival_time`` (``HH:MM``) is retained as a defensive
    fallback: if the API omits ``due_in_mins`` but still reports an
    arrival time, the function falls back to the ``HH:MM`` + today
    date combination, so a partial API payload still produces a
    timestamp rather than ``None``. The fallback is not used for the
    overnight case (the API always sends ``due_in_mins``); it exists
    only to keep a degraded response from breaking the sensor.

    Returns ``None`` when both fields are blank or unparseable, so the
    sensor state can fall back to ``None`` rather than publish a bogus
    datetime.
    """
    due_in_mins = train.due_in_mins
    if due_in_mins is not None:
        # The signed offset is the canonical source: it carries the
        # date direction (future vs past) and naturally resolves
        # overnight services without any HH:MM+date inference. HA's
        # TIMESTAMP renderer turns a negative offset into "X min ago".
        return now + timedelta(minutes=due_in_mins)

    expected_arrival_time = train.expected_arrival_time
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
    # Defensive fallback path: the API omitted ``due_in_mins`` but did
    # send an ``HH:MM``. Use ``now.date()`` as the date so the
    # timestamp sits on today's calendar; an HH:MM already in the past
    # lands in the past (overdue), an HH:MM in the future lands in
    # today's future. Note this fallback cannot disambiguate a true
    # overnight service (00:30 polled at 23:55) without the offset,
    # but the API always supplies the offset, so this is a degraded
    # path only.
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

    # Two sensors per station: next and following train due, both
    # TIMESTAMP countdown states. The previous ``next_train_destination`` /
    # ``next_train_delay`` entities are gone; both trains share the same
    # sensor class.
    sensors = [
        IrishRailDueTrainSensor(coordinator, "next_train_due"),
        IrishRailDueTrainSensor(coordinator, "following_train_due"),
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

        # Every per-station sensor is a TIMESTAMP: the state is a datetime
        # of the API's expected arrival time, resolved via the signed
        # ``due_in_mins`` offset (see ``_parse_expected_arrival``). The
        # TIMESTAMP device class tells HA to render the value with the
        # relative-time chip in the default "Time" card, so a user reading
        # the dashboard sees both the wall-clock arrival and the live
        # "in 5 min" / "5 min ago" subtitle in a single place. No
        # ``native_unit_of_measurement`` is set because timestamp sensors
        # carry no unit.
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the expected arrival as a timezone-aware datetime.

        The TIMESTAMP sensor returns a ``datetime`` of the expected
        arrival; HA's "Time" card renders it as a live minutes-and-seconds
        countdown ("in 5 min") or "5 min ago" for an overdue service.
        """
        if not self.coordinator.data:
            return None

        if self.entity_key == "following_train_due":
            # The following train is the second item in the response; when
            # only one service is scheduled the state falls back to ``None``
            # (→ unknown), never crashing.
            if len(self.coordinator.data) < 2:
                return None
            following_train: TrainDueTime = self.coordinator.data[1]
            return _parse_expected_arrival(following_train, dt_util.utcnow())

        # Next train is the first item in the response.
        next_train: TrainDueTime = self.coordinator.data[0]
        return _parse_expected_arrival(next_train, dt_util.utcnow())

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
            next_train, now
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
