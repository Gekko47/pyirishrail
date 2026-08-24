"""Client for the Irish Rail Realtime Passenger Information (RTPI) API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime
import logging
from typing import Final
from xml.etree.ElementTree import Element

import aiohttp
from defusedxml.common import DefusedXmlException
import defusedxml.ElementTree as ET

from .const import (
    DEFAULT_TIMEOUT,
    MAX_CONCURRENT_MOVEMENT_LOOKUPS,
    MOVEMENT_CACHE_MAX_ENTRIES,
)

_LOGGER = logging.getLogger(__name__)

API_BASE_URL: Final = "https://api.irishrail.ie/realtime/realtime.asmx/"

STATION_TYPE_TO_CODE_DICT: Final[dict[str, str]] = {
    "mainline": "M",
    "suburban": "S",
    "dart": "D",
}


class IrishRailError(Exception):
    """Base exception for Irish Rail API errors."""


class IrishRailConnectionError(IrishRailError):
    """Exception to indicate a connection error."""


class IrishRailTimeoutError(IrishRailError):
    """Exception to indicate an API timeout."""


class IrishRailParseError(IrishRailError):
    """Exception to indicate an XML parsing error."""


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


def _strip_namespaces(root: Element) -> Element:
    """Strip all namespaces from an element tree, in place.

    The RTPI endpoints have historically alternated between documents whose
    elements sit in the default namespace and namespace-free documents.
    Normalizing once, immediately after parsing, lets every lookup below use
    plain tag names instead of dual namespace-or-not fallbacks (roadmap
    item 4.4). The transformation is idempotent, so normalizing an
    already-clean tree is a no-op. Non-element nodes such as comments and
    processing instructions (whose tags are not strings) are left untouched.
    """
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    return root


def _find_tag_text(element: Element, tag_name: str) -> str | None:
    """Return the stripped text of the first matching child, or None.

    ``element`` must come from a namespace-normalized tree (see
    :func:`_strip_namespaces`), so a plain tag name always matches.
    """
    elem = element.find(tag_name)
    if elem is not None and elem.text is not None:
        return elem.text.strip()
    return None


class IrishRailClient:
    """Client for fetching data from the Irish Rail RTPI API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the client."""
        self._session = session
        # Movement histories keyed by ``(train_code, date)``; see
        # MOVEMENT_CACHE_MAX_ENTRIES in const.py.
        self._movement_cache: dict[tuple[str, str], list[TrainMovement]] = {}

    async def _request(
        self, endpoint: str, params: dict[str, str] | None = None
    ) -> Element:
        """Make an HTTP GET request to the Irish Rail RTPI API."""
        url = f"{API_BASE_URL}{endpoint}"
        try:
            async with self._session.get(
                url, params=params, timeout=DEFAULT_TIMEOUT
            ) as response:
                if response.status != 200:
                    raise IrishRailConnectionError(
                        f"Unsuccessful status code from Irish Rail API: "
                        f"{response.status}"
                    )
                content = await response.text()
        except TimeoutError as err:
            raise IrishRailTimeoutError("Timeout connecting to Irish Rail API") from err
        except aiohttp.ClientError as err:
            raise IrishRailConnectionError(
                f"Connection error to Irish Rail API: {err}"
            ) from err

        try:
            # Safely parse XML via defusedxml, then normalize namespaces so
            # every downstream lookup uses plain tag names (roadmap 4.4).
            return _strip_namespaces(ET.fromstring(content))
        except (
            ET.ParseError,
            # Security exceptions raised by defusedxml (e.g. DTDForbidden,
            # EntitiesForbidden, ExternalReferenceForbidden)
            DefusedXmlException,
        ) as err:
            raise IrishRailParseError(
                f"Failed to parse XML response from Irish Rail: {err}"
            ) from err

    async def async_get_all_stations(
        self, station_type: str | None = None
    ) -> list[Station]:
        """Get all stations, optionally filtered by station type."""
        params = None
        if station_type and station_type in STATION_TYPE_TO_CODE_DICT:
            endpoint = "getAllStationsXML_WithStationType"
            params = {"stationType": STATION_TYPE_TO_CODE_DICT[station_type]}
        else:
            endpoint = "getAllStationsXML"

        root = await self._request(endpoint, params)
        stations: list[Station] = []

        for obj in root.findall("objStation"):
            try:
                name = _find_tag_text(obj, "StationDesc") or ""
                alias = _find_tag_text(obj, "StationAlias")
                lat_str = _find_tag_text(obj, "StationLatitude") or "0.0"
                long_str = _find_tag_text(obj, "StationLongitude") or "0.0"
                code = _find_tag_text(obj, "StationCode") or ""
                station_id = _find_tag_text(obj, "StationId") or ""

                stations.append(
                    Station(
                        name=name,
                        alias=alias,
                        latitude=float(lat_str),
                        longitude=float(long_str),
                        code=code,
                        id=station_id,
                    )
                )
            except (ValueError, TypeError) as err:
                _LOGGER.warning("Error parsing station: %s", err)

        return stations

    async def async_get_station_by_name(
        self,
        station_name: str,
        num_minutes: int | None = None,
        direction: str | None = None,
        destination: str | None = None,
        stops_at: str | None = None,
    ) -> list[TrainDueTime]:
        """Get station realtime data by station name."""
        endpoint = "getStationDataByNameXML"
        params = {"StationDesc": station_name}
        if num_minutes:
            endpoint = f"{endpoint}_withNumMins"
            params["NumMins"] = str(num_minutes)

        root = await self._request(endpoint, params)
        trains = parse_station_data(root)

        if direction or destination or stops_at:
            return await self._async_prune_trains(
                trains, direction=direction, destination=destination, stops_at=stops_at
            )

        return trains

    async def async_get_station_by_code(
        self,
        station_code: str,
        num_minutes: int | None = None,
        direction: str | None = None,
        destination: str | None = None,
        stops_at: str | None = None,
    ) -> list[TrainDueTime]:
        """Get station realtime data by station code."""
        endpoint = "getStationDataByCodeXML"
        params = {"StationCode": station_code}
        if num_minutes:
            endpoint = f"{endpoint}_withNumMins"
            params["NumMins"] = str(num_minutes)

        root = await self._request(endpoint, params)
        trains = parse_station_data(root)

        if direction or destination or stops_at:
            return await self._async_prune_trains(
                trains, direction=direction, destination=destination, stops_at=stops_at
            )

        return trains

    async def async_get_all_current_trains(
        self, train_type: str | None = None, direction: str | None = None
    ) -> list[TrainPosition]:
        """Get positions of all current trains."""
        params = None
        if train_type and train_type in STATION_TYPE_TO_CODE_DICT:
            endpoint = "getCurrentTrainsXML_WithTrainType"
            params = {"TrainType": STATION_TYPE_TO_CODE_DICT[train_type]}
        else:
            endpoint = "getCurrentTrainsXML"

        root = await self._request(endpoint, params)
        trains: list[TrainPosition] = []

        for obj in root.findall("objTrainPositions"):
            try:
                status = _find_tag_text(obj, "TrainStatus") or ""
                lat_str = _find_tag_text(obj, "TrainLatitude") or "0.0"
                long_str = _find_tag_text(obj, "TrainLongitude") or "0.0"
                code = _find_tag_text(obj, "TrainCode") or ""
                date = _find_tag_text(obj, "TrainDate") or ""
                message = _find_tag_text(obj, "PublicMessage") or ""
                train_dir = _find_tag_text(obj, "Direction") or ""

                trains.append(
                    TrainPosition(
                        status=status,
                        latitude=float(lat_str),
                        longitude=float(long_str),
                        code=code,
                        date=date,
                        message=message,
                        direction=train_dir,
                    )
                )
            except (ValueError, TypeError) as err:
                _LOGGER.warning("Error parsing train position: %s", err)

        if direction:
            return [t for t in trains if t.direction.lower() == direction.lower()]

        return trains

    async def async_get_train_stops(
        self, train_code: str, date: str | None = None
    ) -> list[TrainMovement]:
        """Get route/stop details for a train code.

        Results are cached per ``(train code, date)`` pair: a running
        train's stop list only grows during its journey, so a cached route
        stays valid for "does this train stop at X?" filtering. Failed
        lookups are never cached, so transient errors retry naturally on
        the next poll; empty results are likewise not cached because they
        may simply mean the train has not reached its first stop yet.
        """
        if date is None:
            # Use the local timezone's current date (Ireland for typical
            # deployments). Callers may pass an explicit date for historical
            # queries. datetime.now().astimezone() is non-blocking and keeps
            # this module free of Home Assistant imports.
            date = datetime.datetime.now().astimezone().date().strftime("%d %b %Y")

        cache_key = (train_code, date)
        cached = self._movement_cache.get(cache_key)
        if cached is not None:
            return cached

        endpoint = "getTrainMovementsXML"
        params = {"TrainId": train_code, "TrainDate": date}

        root = await self._request(endpoint, params)
        movements: list[TrainMovement] = []

        for obj in root.findall("objTrainMovements"):
            movements.append(
                TrainMovement(
                    code=_find_tag_text(obj, "TrainCode") or "",
                    date=_find_tag_text(obj, "TrainDate") or "",
                    location_code=_find_tag_text(obj, "LocationCode") or "",
                    location=_find_tag_text(obj, "LocationFullName") or "",
                    origin=_find_tag_text(obj, "TrainOrigin") or "",
                    destination=_find_tag_text(obj, "TrainDestination") or "",
                    expected_arrival_time=_find_tag_text(obj, "ExpectedArrival") or "",
                    expected_departure_time=(
                        _find_tag_text(obj, "ExpectedDeparture") or ""
                    ),
                    scheduled_arrival_time=(
                        _find_tag_text(obj, "ScheduledArrival") or ""
                    ),
                    scheduled_departure_time=(
                        _find_tag_text(obj, "ScheduledDeparture") or ""
                    ),
                )
            )

        if movements:
            self._movement_cache[cache_key] = movements
            self._evict_movement_cache(current_date=date)

        return movements

    def _evict_movement_cache(self, current_date: str) -> None:
        """Drop entries for other dates when the cache exceeds its cap.

        Eviction is lazy: it only runs once ``MOVEMENT_CACHE_MAX_ENTRIES``
        is exceeded and removes historical-date entries first, so today's
        routes stay warm.
        """
        if len(self._movement_cache) <= MOVEMENT_CACHE_MAX_ENTRIES:
            return
        stale = [key for key in self._movement_cache if key[1] != current_date]
        for key in stale:
            del self._movement_cache[key]

    async def _async_prune_trains(
        self,
        trains: list[TrainDueTime],
        direction: str | None = None,
        destination: str | None = None,
        stops_at: str | None = None,
    ) -> list[TrainDueTime]:
        """Filter list of due trains based on options.

        Direction and destination filters run purely locally. When
        ``stops_at`` is used, every candidate whose destination does not
        already match gets its movement history fetched concurrently,
        bounded by ``MAX_CONCURRENT_MOVEMENT_LOOKUPS``, so worst-case wall
        time stays close to a single request timeout instead of growing
        linearly with the number of due trains. Lookups go through
        :meth:`async_get_train_stops` and are served from its per-day cache;
        a candidate whose movement history cannot be fetched is pruned
        rather than failing the whole poll.
        """
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_MOVEMENT_LOOKUPS)

        async def _movement_matches(train_code: str, target: str) -> bool:
            """Return True when the train's route includes the target stop."""
            try:
                async with semaphore:
                    stops = await self.async_get_train_stops(train_code)
            except IrishRailError:
                # A movement-history failure prunes this train only; the
                # lookup retries naturally on the next poll because failures
                # are never cached.
                return False
            return any(stop.location.lower() == target for stop in stops)

        def _passes_local_filters(train: TrainDueTime) -> bool:
            """Return True when direction/destination filters keep the train."""
            if direction and train.direction.lower() != direction.lower():
                return False
            return not (
                destination and train.destination.lower() != destination.lower()
            )

        # One lookup per distinct candidate train code (codes repeat if the
        # API ever lists the same service twice).
        lookup_targets: dict[str, str] = {}
        if stops_at is not None:
            target = stops_at.lower()
            for train in trains:
                if (
                    _passes_local_filters(train)
                    and target != train.destination.lower()
                    and train.code not in lookup_targets
                ):
                    lookup_targets[train.code] = target

        outcomes = await asyncio.gather(
            *(
                _movement_matches(code, tgt)
                for code, tgt in lookup_targets.items()
            )
        )
        matches: dict[str, bool] = dict(
            zip(lookup_targets.keys(), outcomes, strict=True)
        )

        pruned_data: list[TrainDueTime] = []
        for train in trains:
            if not _passes_local_filters(train):
                continue
            if (
                stops_at
                and stops_at.lower() != train.destination.lower()
                # Verdict was fetched concurrently above.
                and not matches.get(train.code, False)
            ):
                continue
            pruned_data.append(train)

        return pruned_data


def parse_station_data(root: Element) -> list[TrainDueTime]:
    """Parse a station-data XML root element into a list of TrainDueTime.

    Module-level pure function (no I/O) so it can be unit-tested in
    isolation without an HTTP session. Accepts both namespaced and
    namespace-free roots: namespaces are normalized once up front (the
    operation is idempotent).
    """
    root = _strip_namespaces(root)
    trains: list[TrainDueTime] = []
    for obj in root.findall("objStationData"):
        try:
            due_str = _find_tag_text(obj, "Duein") or "0"
            late_str = _find_tag_text(obj, "Late") or "0"

            # Defensive check for non-numeric or unexpectedly formatted strings
            try:
                due_in_mins = int(due_str)
            except ValueError:
                _LOGGER.debug("Non-numeric 'Duein' value: %s", due_str)
                due_in_mins = 0

            try:
                late_mins = int(late_str)
            except ValueError:
                _LOGGER.debug("Non-numeric 'Late' value: %s", late_str)
                late_mins = 0

            trains.append(
                TrainDueTime(
                    code=_find_tag_text(obj, "Traincode") or "",
                    origin=_find_tag_text(obj, "Origin") or "",
                    destination=_find_tag_text(obj, "Destination") or "",
                    origin_time=_find_tag_text(obj, "Origintime") or "",
                    destination_time=_find_tag_text(obj, "Destinationtime") or "",
                    due_in_mins=due_in_mins,
                    late_mins=late_mins,
                    expected_arrival_time=_find_tag_text(obj, "Exparrival") or "",
                    expected_departure_time=_find_tag_text(obj, "Expdepart") or "",
                    scheduled_arrival_time=_find_tag_text(obj, "Scharrival") or "",
                    scheduled_departure_time=_find_tag_text(obj, "Schdepart") or "",
                    type=_find_tag_text(obj, "Traintype") or "",
                    direction=_find_tag_text(obj, "Direction") or "",
                    location_type=_find_tag_text(obj, "Locationtype") or "",
                )
            )
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Error parsing station data record: %s", err)

    return trains
