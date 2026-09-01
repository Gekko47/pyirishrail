"""Typed dataclasses for the Irish Rail RTPI async client.

The four models below are the public data contract that :mod:`pyirishrail.api`
exposes. They are immutable (``frozen=True``) so a parsed response cannot
be mutated in place after construction, and every field uses a concrete
type (including explicit ``str`` defaults for fields the API may omit)
so consumers can rely on attribute access without ``Optional``-checking
in hot paths.

These types deliberately do *not* include any Home Assistant imports:
the library is framework-agnostic by design so it can be unit-tested
without an HA fixture and could be consumed by non-HA applications in
the future. See docs/architecture.md §16 for the vendoring rationale
and the no-HA-imports invariant.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    """Represents an Irish Rail station."""

    name: str
    alias: str | None
    latitude: float
    longitude: float
    code: str
    id: str


@dataclass(frozen=True)
class TrainDueTime:
    """Represents a train due at a station."""

    code: str
    origin: str
    destination: str
    origin_time: str
    destination_time: str
    due_in_mins: int
    late_mins: int
    expected_arrival_time: str
    expected_departure_time: str
    scheduled_arrival_time: str
    scheduled_departure_time: str
    type: str
    direction: str
    location_type: str


@dataclass(frozen=True)
class TrainPosition:
    """Represents the real-time position of a train."""

    status: str
    latitude: float
    longitude: float
    code: str
    date: str
    message: str
    direction: str


@dataclass(frozen=True)
class TrainMovement:
    """Represents a movement/stop of a train."""

    code: str
    date: str
    location_code: str
    location: str
    origin: str
    destination: str
    expected_arrival_time: str
    expected_departure_time: str
    scheduled_arrival_time: str
    scheduled_departure_time: str


__all__ = [
    "Station",
    "TrainDueTime",
    "TrainMovement",
    "TrainPosition",
]
