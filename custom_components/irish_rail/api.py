"""Client for the Irish Rail Realtime Passenger Information (RTPI) API."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import logging
from typing import Final
from xml.etree.ElementTree import Element

import aiohttp
import defusedxml.ElementTree as ET

from .const import DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

API_BASE_URL: Final = "https://api.irishrail.ie/realtime/realtime.asmx/"
NAMESPACE: Final = "http://api.irishrail.ie/realtime/"

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


def _find_tag_text(element: Element, tag_name: str) -> str | None:
    """Find a tag text safely considering namespace prefixes."""
    # Attempt with namespace
    elem = element.find(f"{{{NAMESPACE}}}{tag_name}")
    if elem is None:
        # Fallback without namespace
        elem = element.find(tag_name)
    if elem is not None and elem.text is not None:
        return elem.text.strip()
    return None


class IrishRailClient:
    """Client for fetching data from the Irish Rail RTPI API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the client."""
        self._session = session

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
            # Safely parse XML via defusedxml
            return ET.fromstring(content)
        except ET.ParseError as err:
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

        # Elements can be nested under namespaces or directly
        for obj in (
            root.findall(f"{{{NAMESPACE}}}objStation") or root.findall("objStation")
        ):
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

        for obj in (
            root.findall(f"{{{NAMESPACE}}}objTrainPositions")
            or root.findall("objTrainPositions")
        ):
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
        """Get route/stop details for a train code."""
        if date is None:
            # Use the local timezone's current date (Ireland for typical
            # deployments). Callers may pass an explicit date for historical
            # queries. datetime.now().astimezone() is non-blocking and keeps
            # this module free of Home Assistant imports.
            date = datetime.datetime.now().astimezone().date().strftime("%d %b %Y")

        endpoint = "getTrainMovementsXML"
        params = {"TrainId": train_code, "TrainDate": date}

        root = await self._request(endpoint, params)
        movements: list[TrainMovement] = []

        for obj in (
            root.findall(f"{{{NAMESPACE}}}objTrainMovements")
            or root.findall("objTrainMovements")
        ):
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

        return movements

    async def _async_prune_trains(
        self,
        trains: list[TrainDueTime],
        direction: str | None = None,
        destination: str | None = None,
        stops_at: str | None = None,
    ) -> list[TrainDueTime]:
        """Filter list of due trains based on options.

        Note: when ``stops_at`` is used, one extra HTTP request per candidate
        train is made sequentially to fetch its movement history. The number
        of due trains at a station is small (typically fewer than 20), so the
        extra load stays bounded; all other filtering happens locally without
        additional requests.
        """
        pruned_data: list[TrainDueTime] = []
        for train in trains:
            append = True
            if direction and train.direction.lower() != direction.lower():
                append = False

            if destination and train.destination.lower() != destination.lower():
                append = False

            if append and stops_at and stops_at.lower() != train.destination.lower():
                # Extra HTTP call to retrieve full movement history
                try:
                    stops = await self.async_get_train_stops(train.code)
                    append = any(
                        stop.location.lower() == stops_at.lower() for stop in stops
                    )
                except IrishRailError:
                    append = False

            if append:
                pruned_data.append(train)

        return pruned_data


def parse_station_data(root: Element) -> list[TrainDueTime]:
    """Parse a station-data XML root element into a list of TrainDueTime.

    Module-level pure function (no I/O) so it can be unit-tested in
    isolation without an HTTP session.
    """
    trains: list[TrainDueTime] = []
    for obj in (
        root.findall(f"{{{NAMESPACE}}}objStationData")
        or root.findall("objStationData")
    ):
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
