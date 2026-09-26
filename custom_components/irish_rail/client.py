"""Client for the Irish Rail Realtime Passenger Information (RTPI) API."""

from __future__ import annotations

import asyncio
import datetime
import logging
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element

import aiohttp

from .errors import (
    IrishRailConnectionError,
    IrishRailError,
    IrishRailParseError,
    IrishRailTimeoutError,
)
from .lib_const import (
    API_BASE_URL,
    DEFAULT_TIMEOUT,
    MOVEMENT_CACHE_MAX_ENTRIES,
    STATION_TYPE_TO_CODE_DICT,
)
from .models import (
    Station,
    TrainDueTime,
    TrainMovement,
    TrainPosition,
)
from .request_gate import RequestGate

_LOGGER = logging.getLogger(__name__)

# XML safety: pre-parse substring guard for DTD/enabling keywords
# followed by stdlib ``ET.fromstring``. See docs/architecture.md §4
# for the policy (why both layers, why no third tree-walk layer,
# and the documented CDATA-rejection tradeoff).

_DTD_KEYWORDS: tuple[str, ...] = (
    "<!doctype",
    "<!entity",
    "<!element",
    "<!attlist",
    "<!notation",
)


__all__ = [
    "API_BASE_URL",
    "DEFAULT_TIMEOUT",
    "MOVEMENT_CACHE_MAX_ENTRIES",
    "STATION_TYPE_TO_CODE_DICT",
    "IrishRailClient",
    "IrishRailConnectionError",
    "IrishRailError",
    "IrishRailParseError",
    "IrishRailTimeoutError",
    "Station",
    "TrainDueTime",
    "TrainMovement",
    "TrainPosition",
    "parse_station_data",
]


def _strip_namespaces(root: Element) -> Element:
    """Strip all namespaces from an element tree, in place.

    Idempotent, and non-element nodes (whose tags are not strings) are left
    untouched. See docs/architecture.md §4.
    """
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    return root


# Public re-export of the namespace-normalizing helper. The leading-
# underscore name is the implementation; this alias is the documented
# entry point for cross-module consumers (e.g. test stubs that need to
# mimic the client's parse-side normalization) that should not reach
# for a private symbol. Adding a new public name rather than renaming
# the underlying function keeps ``git log -p -- _strip_namespaces``
# intact for anyone diagnosing the normalization step.
strip_namespaces = _strip_namespaces


def _find_tag_text(element: Element, tag_name: str) -> str | None:
    """Return the stripped text of the first matching child, or None.

    ``element`` must come from a namespace-normalized tree.
    """
    elem = element.find(tag_name)
    if elem is not None and elem.text is not None:
        return elem.text.strip()
    return None


def _scoped_journey_stops(
    movements: list[TrainMovement],
    journey_destination: str | None,
    station_code: str | None = None,
    station_name: str | None = None,
) -> list[TrainMovement]:
    """Return the stops of the train's current journey past the station.

    Cuts the whole-day movement history three ways: keep only rows sharing the
    due train's ``TrainDestination``, drop everything up to and including the
    monitored station, and bound the result to the contiguous run holding that
    station so a later same-day journey cannot leak in. A destination with no
    matching row, or a station that is not found, degrades to the unscoped
    result rather than to an empty one. See docs/architecture.md §5.
    """
    rows = list(movements)
    # Position of each retained row within ``movements`` (the whole-day
    # history). Consecutive rows of one journey are adjacent here; rows
    # belonging to separate same-destination journeys are not, because the
    # other journeys' (now destination-filtered) rows sat between them.
    row_positions = list(range(len(rows)))
    destination_cf = (journey_destination or "").casefold()
    if destination_cf:
        matched_indices = [
            index
            for index, movement in enumerate(movements)
            if (movement.destination or "").casefold() == destination_cf
        ]
        if matched_indices:
            rows = [movements[index] for index in matched_indices]
            row_positions = matched_indices

    code_cf = (station_code or "").casefold()
    name_cf = (station_name or "").casefold()
    cut_index: int | None = None
    if code_cf or name_cf:
        for index, movement in enumerate(rows):
            location_code_cf = (movement.location_code or "").casefold()
            location_cf = (movement.location or "").casefold()
            if (code_cf and location_code_cf == code_cf) or (
                name_cf and location_cf == name_cf
            ):
                cut_index = index
                break
    if cut_index is None:
        return rows

    # Bound the downstream cut to the contiguous run containing the matched
    # station: slicing from ``cut_index + 1`` across the whole list would leak
    # stops of later same-day journeys that happen to share the destination.
    run_end = cut_index + 1
    while (
        run_end < len(rows) and row_positions[run_end] == row_positions[run_end - 1] + 1
    ):
        run_end += 1
    return rows[cut_index + 1 : run_end]


class IrishRailClient:
    """Client for fetching data from the Irish Rail RTPI API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        gate: RequestGate | None = None,
        movement_cache: dict[tuple[str, str], list[TrainMovement]] | None = None,
    ) -> None:
        """Initialize the client.

        ``gate`` and ``movement_cache`` are the sharing seams: the Home
        Assistant integration hands every client it creates the same
        per-instance gate and movement cache, so the coordinator, both
        config flows, the rebuild button and the health probe draw on one
        rate budget and one route cache. Omitted, the client owns private
        ones. See docs/architecture.md §3.
        """
        self._session = session
        self._gate = gate if gate is not None else RequestGate()
        # Movement histories keyed by ``(train_code, date)``; see
        # MOVEMENT_CACHE_MAX_ENTRIES in const.py.
        self._movement_cache: dict[tuple[str, str], list[TrainMovement]] = (
            movement_cache if movement_cache is not None else {}
        )

    async def _request(
        self,
        endpoint: str,
        params: dict[str, str] | None = None,
        priority: str = "normal",
    ) -> Element:
        """Make an HTTP GET request to the Irish Rail RTPI API.

        Every outbound request crosses the client's request gate, so callers
        cannot bypass the pacing; cached lookups never reach here at all.
        """
        url = f"{API_BASE_URL}{endpoint}"
        try:
            async with (
                self._gate.acquire(priority),
                self._session.get(
                    url, params=params, timeout=DEFAULT_TIMEOUT
                ) as response,
            ):
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
            # Layer 1: well-formedness via the stdlib parser. Catches
            # whitespace-obfuscated forms (``<! DOCTYPE`` etc.),
            # unbalanced tags, missing references. ``ET.ParseError``
            # is mapped onto :class:`IrishRailParseError` below.
            # The substring scan is deliberately conservative: a CDATA
            # section whose text merely contains ``<!doctype`` is
            # rejected too (see docs/architecture.md §4).
            lowered = content.lower()
            for keyword in _DTD_KEYWORDS:
                if keyword in lowered:
                    raise IrishRailParseError(
                        "DTD or entity declarations are not allowed in API responses"
                    )
            root = _strip_namespaces(ET.fromstring(content))
        except ET.ParseError as err:
            raise IrishRailParseError(
                f"Failed to parse XML response from Irish Rail: {err}"
            ) from err

        return root

    async def async_get_all_stations(
        self,
        station_type: str | None = None,
        priority: str = "normal",
    ) -> list[Station]:
        """Get all stations, optionally filtered by station type.

        Bulk callers pass ``priority="background"`` so they yield to live
        polling without being starved when no normal traffic is queued.
        """
        params = None
        if station_type and station_type in STATION_TYPE_TO_CODE_DICT:
            endpoint = "getAllStationsXML_WithStationType"
            params = {"stationType": STATION_TYPE_TO_CODE_DICT[station_type]}
        else:
            endpoint = "getAllStationsXML"

        root = await self._request(endpoint, params, priority=priority)
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
        observed_stops: set[str] | None = None,
    ) -> list[TrainDueTime]:
        """Get station realtime data by station name.

        ``observed_stops``, when supplied, is cleared then filled with the
        downstream stop names the pruning pass resolved.
        """
        endpoint = "getStationDataByNameXML"
        params = {"StationDesc": station_name}
        if num_minutes:
            endpoint = f"{endpoint}_withNumMins"
            params["NumMins"] = str(num_minutes)

        root = await self._request(endpoint, params)
        trains = parse_station_data(root)

        if direction or destination or stops_at:
            return await self._async_prune_trains(
                trains,
                direction=direction,
                destination=destination,
                stops_at=stops_at,
                station_name=station_name,
                observed_stops=observed_stops,
            )

        return trains

    async def async_get_station_by_code(
        self,
        station_code: str,
        num_minutes: int | None = None,
        direction: str | None = None,
        destination: str | None = None,
        stops_at: str | None = None,
        priority: str = "normal",
        observed_stops: set[str] | None = None,
        service_date: str | None = None,
    ) -> list[TrainDueTime]:
        """Get station realtime data by station code.

        ``priority="background"`` yields to live polling;
        ``observed_stops`` is cleared then filled with the downstream stop
        names the pruning pass resolved; ``service_date`` pins the
        movement-history lookups to an Irish civil date.
        """
        endpoint = "getStationDataByCodeXML"
        params = {"StationCode": station_code}
        if num_minutes:
            endpoint = f"{endpoint}_withNumMins"
            params["NumMins"] = str(num_minutes)

        root = await self._request(endpoint, params, priority=priority)
        trains = parse_station_data(root)

        if direction or destination or stops_at:
            return await self._async_prune_trains(
                trains,
                direction=direction,
                destination=destination,
                stops_at=stops_at,
                station_code=station_code,
                observed_stops=observed_stops,
                service_date=service_date,
            )

        return trains

    async def async_get_station_directions(self, station_code: str) -> list[str]:
        """Return the distinct direction values currently due at a station.

        The API has no static direction directory, so the station's own
        due-trains list is the only authoritative source. Values are
        deduplicated and sorted case-insensitively; an empty result means no
        trains are due inside the lookahead window, never an error.
        """
        trains = await self.async_get_station_by_code(station_code)
        seen: dict[str, str] = {}
        for train in trains:
            if not train.direction:
                continue
            seen.setdefault(train.direction.lower(), train.direction)
        return sorted(seen.values(), key=str.lower)

    def scope_journey_stops(
        self,
        movements: list[TrainMovement],
        journey_destination: str | None,
        *,
        station_code: str | None = None,
        station_name: str | None = None,
    ) -> list[TrainMovement]:
        """Return a movement list scoped to one journey and cut downstream of a station.

        Pure transformation on the supplied rows — no I/O, no gate, no shared
        state — delegating to the module-private helper, which carries the
        algorithm and its regression coverage. This public wrapper exists so
        the rebuild button and the offline seed generator can reuse the same
        scoping without reaching for a leading-underscore symbol. The cut
        point is the first ``station_code`` match, else the first
        ``station_name`` match; no match returns the rows uncut.
        """
        return _scoped_journey_stops(
            movements,
            journey_destination,
            station_code=station_code,
            station_name=station_name,
        )

    async def async_get_station_stops_at_options(
        self,
        station_code: str,
        direction: str | None = None,
        exclude: str | None = None,
    ) -> list[str]:
        """Return the stops served by trains currently due at a station.

        Each distinct due train's route is resolved at background priority
        and cut downstream of this station, so the union holds only stops
        the selected services actually reach. A route that cannot be fetched
        is skipped rather than failing the union; ``exclude`` drops the
        departure station; names are deduplicated and sorted
        case-insensitively.
        """
        trains = await self.async_get_station_by_code(station_code, direction=direction)

        async def _route_stops(train_code: str) -> list[TrainMovement]:
            try:
                return await self.async_get_train_stops(
                    train_code, priority="background"
                )
            except IrishRailError:
                return []

        outcomes = await asyncio.gather(
            *(_route_stops(train.code) for train in trains),
            return_exceptions=True,
        )

        exclude_lower = exclude.lower() if exclude else None
        seen: dict[str, str] = {}
        for train, route in zip(trains, outcomes, strict=True):
            if isinstance(route, BaseException):
                # A non-IrishRailError bug in one route lookup must not
                # escape into the config flow; skip this train's route
                # exactly like a known failure and keep the union intact.
                _LOGGER.warning(
                    "Route lookup for train %s failed unexpectedly: %s",
                    train.code,
                    route,
                )
                continue
            journey = _scoped_journey_stops(
                route,
                train.destination,
                station_code=station_code,
                station_name=exclude,
            )
            for stop in journey:
                name = stop.location
                if not name or (exclude_lower and name.lower() == exclude_lower):
                    continue
                seen.setdefault(name.lower(), name)
        return sorted(seen.values(), key=str.lower)

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
        self,
        train_code: str,
        date: str | None = None,
        priority: str = "normal",
    ) -> list[TrainMovement]:
        """Get route/stop details for a train code.

        Cached per ``(train code, date)``: a running train's stop list only
        grows, so a cached route stays valid for filtering. Failures and
        empty results are never cached, so they retry on the next poll.
        Cache hits never cross the request gate; bulk callers pass
        ``priority="background"``.
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

        root = await self._request(endpoint, params, priority)
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

        Lazy, and historical dates go first so today's routes stay warm;
        if every remaining entry is today's, the oldest are dropped until
        the cap holds. See docs/architecture.md §6.
        """
        if len(self._movement_cache) <= MOVEMENT_CACHE_MAX_ENTRIES:
            return
        stale = [key for key in self._movement_cache if key[1] != current_date]
        for key in stale:
            del self._movement_cache[key]
        while len(self._movement_cache) > MOVEMENT_CACHE_MAX_ENTRIES:
            del self._movement_cache[next(iter(self._movement_cache))]

    async def _async_prune_trains(
        self,
        trains: list[TrainDueTime],
        direction: str | None = None,
        destination: str | None = None,
        stops_at: str | None = None,
        station_code: str | None = None,
        station_name: str | None = None,
        observed_stops: set[str] | None = None,
        service_date: str | None = None,
    ) -> list[TrainDueTime]:
        """Filter list of due trains based on options.

        Direction and destination filters run purely locally. When
        ``stops_at`` is used, every candidate whose destination does not
        already match gets its movement history fetched concurrently at
        background priority, paced by the client's request gate (its
        ``max_concurrent`` keeps worst-case wall time close to a single
        request timeout instead of growing linearly with the number of
        due trains). Lookups go through
        :meth:`async_get_train_stops` and are served from its per-day cache;
        a candidate whose movement history cannot be fetched is pruned
        rather than failing the whole poll.

        Matching is journey-scoped like :meth:`scope_journey_stops`: a
        candidate only counts as "stopping at" the target when the target is
        reached *after* the monitored station on its current journey, not
        merely somewhere in the train code's whole-day history. Successfully
        resolved journeys are reported through the caller's
        ``observed_stops`` set so it can learn the monitored station's
        reachable stops from ordinary polling.

        ``service_date`` is the ``%d %b %Y`` schedule date the movement
        lookup should use. It must be derived in *Irish* civil time: the
        default inside :meth:`async_get_train_stops` is the host's local
        date, so a host configured to another zone queries between 00:00
        and 05:00 Dublin time would ask for yesterday's schedule and
        prune every train. Callers in the integration pass
        ``DUBLIN_TZ``-derived dates; the standalone library default is
        unchanged.
        """
        # Cleared per pass: the observations must describe this pass only,
        # so a stale set from an earlier poll can never be merged by callers.
        if observed_stops is not None:
            observed_stops.clear()

        async def _journey_stops(
            train_code: str, journey_destination: str
        ) -> list[TrainMovement]:
            """Return the train's current-journey stops past the station."""
            try:
                movements = await self.async_get_train_stops(
                    train_code, date=service_date, priority="background"
                )
            except IrishRailError:
                # A movement-history failure prunes this train only; the
                # lookup retries naturally on the next poll because failures
                # are never cached.
                return []
            return _scoped_journey_stops(
                movements,
                journey_destination,
                station_code=station_code,
                station_name=station_name,
            )

        def _passes_local_filters(train: TrainDueTime) -> bool:
            """Return True when direction/destination filters keep the train."""
            if direction and train.direction.lower() != direction.lower():
                return False
            return not (
                destination and train.destination.lower() != destination.lower()
            )

        # One lookup per distinct candidate train code (codes repeat if the
        # API ever lists the same service twice); each candidate remembers
        # its own journey destination for the scoping cut.
        candidates: dict[str, tuple[str, str]] = {}
        if stops_at is not None:
            target = stops_at.lower()
            for train in trains:
                if (
                    _passes_local_filters(train)
                    and target != train.destination.lower()
                    and train.code not in candidates
                ):
                    candidates[train.code] = (target, train.destination)

        outcomes = await asyncio.gather(
            *(
                _journey_stops(code, journey_destination)
                for code, (_, journey_destination) in candidates.items()
            ),
            return_exceptions=True,
        )
        matches: dict[str, bool] = {}
        observed: set[str] = set()
        for code, outcome in zip(candidates.keys(), outcomes, strict=True):
            if isinstance(outcome, BaseException):
                # Unexpected failures (e.g. a parse bug raising something
                # other than IrishRailError) prune this train exactly like
                # a known failure instead of failing the whole poll; the
                # other in-flight lookups keep running to completion.
                _LOGGER.warning(
                    "Movement lookup for train %s failed unexpectedly; "
                    "pruning it from this poll",
                    code,
                    exc_info=outcome,
                )
                matches[code] = False
                continue
            matches[code] = any(
                stop.location.lower() == candidates[code][0] for stop in outcome
            )
            observed.update(stop.location for stop in outcome if stop.location)

        if observed and observed_stops is not None:
            observed_stops.update(observed)

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
            due_str = _find_tag_text(obj, "Duein") or ""
            late_str = _find_tag_text(obj, "Late") or "0"

            # A missing or malformed 'Duein' becomes None rather than 0 so
            # consumers fall back to 'Exparrival'; coercing to 0 reported a
            # misformatted train as "due in 0 minutes" with a wrong state
            # and no usable signal. The warning stays because a silent
            # coercion is what users hit when upstream changes a format.
            try:
                due_in_mins = int(due_str)
            except ValueError:
                _LOGGER.warning(
                    "Non-numeric 'Duein' value from Irish Rail API, treated as unknown: %r",
                    due_str,
                )
                due_in_mins = None

            try:
                late_mins = int(late_str)
            except ValueError:
                _LOGGER.warning(
                    "Non-numeric 'Late' value from Irish Rail API, coerced to 0: %r",
                    late_str,
                )
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
