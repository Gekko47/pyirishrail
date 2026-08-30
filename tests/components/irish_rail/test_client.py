"""Tests for the Irish Rail API client."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree.ElementTree import Element, fromstring

import aiohttp
import pytest
from aresponses import ResponsesMockServer

import custom_components.irish_rail.pyirishrail.api as ir_api
from custom_components.irish_rail.pyirishrail import (
    IrishRailClient,
    IrishRailConnectionError,
    IrishRailParseError,
    IrishRailTimeoutError,
    RequestGate,
    TrainDueTime,
    TrainMovement,
    parse_station_data,
)
from custom_components.irish_rail.pyirishrail._const import (
    MOVEMENT_CACHE_MAX_ENTRIES,
)

SAMPLE_STATIONS_XML = """
<ArrayOfObjStation xmlns="http://api.irishrail.ie/realtime/">
    <objStation>
        <StationDesc>Dublin Pearse</StationDesc>
        <StationAlias/>
        <StationLatitude>53.3433</StationLatitude>
        <StationLongitude>-6.24829</StationLongitude>
        <StationCode>PEARS</StationCode>
        <StationId>150</StationId>
    </objStation>
</ArrayOfObjStation>
"""

SAMPLE_TRAIN_POSITIONS_XML = """
<ArrayOfObjTrainPositions xmlns="http://api.irishrail.ie/realtime/">
    <objTrainPositions>
        <TrainStatus>R</TrainStatus>
        <TrainLatitude>53.35</TrainLatitude>
        <TrainLongitude>-6.26</TrainLongitude>
        <TrainCode>E123</TrainCode>
        <TrainDate>01 Jan 2026</TrainDate>
        <PublicMessage>Running on time</PublicMessage>
        <Direction>Northbound</Direction>
    </objTrainPositions>
</ArrayOfObjTrainPositions>
"""

SAMPLE_TRAIN_MOVEMENTS_XML = """
<ArrayOfObjTrainMovements xmlns="http://api.irishrail.ie/realtime/">
    <objTrainMovements>
        <TrainCode>E123</TrainCode>
        <TrainDate>01 Jan 2026</TrainDate>
        <LocationCode>PEARS</LocationCode>
        <LocationFullName>Dublin Pearse</LocationFullName>
        <TrainOrigin>Howth</TrainOrigin>
        <TrainDestination>Bray</TrainDestination>
        <ExpectedArrival>12:10</ExpectedArrival>
        <ExpectedDeparture>12:11</ExpectedDeparture>
        <ScheduledArrival>12:00</ScheduledArrival>
        <ScheduledDeparture>12:01</ScheduledDeparture>
    </objTrainMovements>
</ArrayOfObjTrainMovements>
"""

SAMPLE_STATION_DATA_XML = """
<ArrayOfObjStationData xmlns="http://api.irishrail.ie/realtime/">
    <objStationData>
        <Traincode>E123</Traincode>
        <Origin>Howth</Origin>
        <Destination>Bray</Destination>
        <Origintime>12:00</Origintime>
        <Destinationtime>13:00</Destinationtime>
        <Status>En Route</Status>
        <Lastlocation>Greystones</Lastlocation>
        <Duein>10</Duein>
        <Late>2</Late>
        <Exparrival>12:10</Exparrival>
        <Expdepart>12:11</Expdepart>
        <Scharrival>12:00</Scharrival>
        <Schdepart>12:01</Schdepart>
        <Direction>Southbound</Direction>
        <Traintype>DART</Traintype>
        <Locationtype>S</Locationtype>
    </objStationData>
</ArrayOfObjStationData>
"""


async def test_get_all_stations(aresponses: ResponsesMockServer) -> None:
    """Test getting all stations."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getAllStationsXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATIONS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        stations = await client.async_get_all_stations()

        assert len(stations) == 1
        assert stations[0].name == "Dublin Pearse"
        assert stations[0].code == "PEARS"
        assert stations[0].latitude == 53.3433
        assert stations[0].longitude == -6.24829


async def test_get_station_by_code(aresponses: ResponsesMockServer) -> None:
    """Test getting station data by code."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS")

        assert len(trains) == 1
        assert trains[0].code == "E123"
        assert trains[0].destination == "Bray"
        assert trains[0].due_in_mins == 10
        assert trains[0].late_mins == 2


async def test_api_connection_error(aresponses: ResponsesMockServer) -> None:
    """Test handling of connection errors."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getAllStationsXML",
        "GET",
        aresponses.Response(status=500),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        with pytest.raises(IrishRailConnectionError):
            await client.async_get_all_stations()


async def test_api_parse_error(aresponses: ResponsesMockServer) -> None:
    """Test handling of malformed XML."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getAllStationsXML",
        "GET",
        aresponses.Response(text="<invalid", status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        with pytest.raises(IrishRailParseError):
            await client.async_get_all_stations()


async def test_get_station_by_name(aresponses: ResponsesMockServer) -> None:
    """Test getting station data by name."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByNameXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_name("Dublin Pearse")

        assert len(trains) == 1
        assert trains[0].destination == "Bray"
        assert trains[0].due_in_mins == 10


async def test_get_station_by_code_with_num_minutes(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that num_minutes switches to the _withNumMins endpoint."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML_withNumMins",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS", num_minutes=30)

        assert len(trains) == 1


async def test_get_station_by_code_direction_filter(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that a non-matching direction filter prunes all trains."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS", direction="Northbound")

        # The fixture train is Southbound, so filtering removes it.
        assert trains == []


async def test_get_all_current_trains(aresponses: ResponsesMockServer) -> None:
    """Test getting positions of all current trains."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getCurrentTrainsXML",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_POSITIONS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_all_current_trains()

        assert len(trains) == 1
        assert trains[0].code == "E123"
        assert trains[0].latitude == 53.35
        assert trains[0].direction == "Northbound"


async def test_get_all_current_trains_direction_filter(
    aresponses: ResponsesMockServer,
) -> None:
    """Test direction filtering of current trains."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getCurrentTrainsXML",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_POSITIONS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_all_current_trains(direction="Southbound")

        assert trains == []


async def test_get_train_stops(aresponses: ResponsesMockServer) -> None:
    """Test getting movement/stop details for a train code."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_MOVEMENTS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        movements = await client.async_get_train_stops("E123", date="01 Jan 2026")

        assert len(movements) == 1
        assert movements[0].location == "Dublin Pearse"
        assert movements[0].location_code == "PEARS"
        assert movements[0].scheduled_arrival_time == "12:00"


async def test_parse_station_data_non_numeric_values() -> None:
    """Test defensive handling of non-numeric Duein/Late values."""
    xml = """
    <ArrayOfObjStationData xmlns="http://api.irishrail.ie/realtime/">
        <objStationData>
            <Traincode>E999</Traincode>
            <Duein>due</Duein>
            <Late>late</Late>
        </objStationData>
    </ArrayOfObjStationData>
    """
    trains = parse_station_data(fromstring(xml))

    assert len(trains) == 1
    assert trains[0].due_in_mins == 0
    assert trains[0].late_mins == 0


async def test_api_client_error_conversion() -> None:
    """Test that aiohttp.ClientError is converted to IrishRailConnectionError."""
    session = MagicMock()
    response_cm = MagicMock()
    response_cm.__aenter__ = AsyncMock(
        side_effect=aiohttp.ClientConnectionError("boom")
    )
    response_cm.__aexit__ = AsyncMock(return_value=None)
    session.get.return_value = response_cm

    client = IrishRailClient(session)
    with pytest.raises(IrishRailConnectionError):
        await client.async_get_all_stations()


async def test_get_all_stations_with_station_type(
    aresponses: ResponsesMockServer,
) -> None:
    """Test the station-type filtered endpoint is used."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getAllStationsXML_WithStationType",
        "GET",
        aresponses.Response(text=SAMPLE_STATIONS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        stations = await client.async_get_all_stations(station_type="dart")

        assert len(stations) == 1


async def test_get_all_stations_malformed_record(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that a malformed station record is skipped with a warning."""
    bad_xml = """
    <ArrayOfObjStation xmlns="http://api.irishrail.ie/realtime/">
        <objStation>
            <StationDesc>Bad Station</StationDesc>
            <StationLatitude>not-a-float</StationLatitude>
            <StationLongitude>-6.2</StationLongitude>
            <StationCode>BAD</StationCode>
            <StationId>1</StationId>
        </objStation>
    </ArrayOfObjStation>
    """
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getAllStationsXML",
        "GET",
        aresponses.Response(text=bad_xml, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        stations = await client.async_get_all_stations()

        assert stations == []


async def test_get_all_current_trains_with_train_type(
    aresponses: ResponsesMockServer,
) -> None:
    """Test the train-type filtered endpoint is used."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getCurrentTrainsXML_WithTrainType",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_POSITIONS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_all_current_trains(train_type="dart")

        assert len(trains) == 1


async def test_get_all_current_trains_malformed_record(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that a malformed train position record is skipped with a warning."""
    bad_xml = """
    <ArrayOfObjTrainPositions xmlns="http://api.irishrail.ie/realtime/">
        <objTrainPositions>
            <TrainStatus>R</TrainStatus>
            <TrainLatitude>not-a-float</TrainLatitude>
            <TrainLongitude>-6.26</TrainLongitude>
            <TrainCode>E123</TrainCode>
        </objTrainPositions>
    </ArrayOfObjTrainPositions>
    """
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getCurrentTrainsXML",
        "GET",
        aresponses.Response(text=bad_xml, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_all_current_trains()

        assert trains == []


async def test_get_train_stops_default_date(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that omitting the date uses today's local date."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_MOVEMENTS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        movements = await client.async_get_train_stops("E123")

        assert len(movements) == 1


async def test_station_by_code_stops_at_filter(
    aresponses: ResponsesMockServer,
) -> None:
    """Test stops_at pruning fetches movement history per candidate train."""
    # First request: station data with a train whose destination differs
    # from the stops_at value, triggering the movement-history lookup.
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )
    # Second request: movement history showing the train stops at Greystones.
    movements_xml = SAMPLE_TRAIN_MOVEMENTS_XML.replace(
        "Dublin Pearse", "Greystones"
    ).replace("PEARS", "GREYS")
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=movements_xml, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS", stops_at="Greystones")

        assert len(trains) == 1
        assert trains[0].code == "E123"


async def test_get_station_by_name_with_num_minutes_and_filter(
    aresponses: ResponsesMockServer,
) -> None:
    """Test by-name lookup using num_minutes endpoint and direction filter."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByNameXML_withNumMins",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_name(
            "Dublin Pearse", num_minutes=30, direction="Southbound"
        )

        assert len(trains) == 1


async def test_get_station_by_code_destination_filter(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that a non-matching destination filter prunes all trains."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS", destination="Howth")

        # The fixture train is bound for Bray, so filtering removes it.
        assert trains == []


async def test_stops_at_prune_handles_movement_error(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that a movement-history failure prunes the train instead of raising."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
    )
    # Movement-history lookup fails; the train must be pruned, not raised.
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(status=500),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS", stops_at="Greystones")

        assert trains == []


async def test_api_timeout_error() -> None:
    """Test handling of aiohttp timeout raises IrishRailTimeoutError."""
    # aresponses 3.x exposes ``Response`` as ``aiohttp.web.Response``, which has no
    # ``abort`` kwarg, and a real network timeout is slow/flaky in CI. Instead we
    # deterministically make the aiohttp session raise ``asyncio.TimeoutError`` -
    # exactly what aiohttp raises when its ``ClientTimeout`` expires - and confirm
    # the client converts it into ``IrishRailTimeoutError``.
    session = MagicMock()
    response_cm = MagicMock()
    response_cm.__aenter__ = AsyncMock(side_effect=TimeoutError())
    response_cm.__aexit__ = AsyncMock(return_value=None)
    session.get.return_value = response_cm

    client = IrishRailClient(session)
    with pytest.raises(IrishRailTimeoutError):
        await client.async_get_all_stations()


# ── stops_at pruning hardening & movement cache (roadmap Phase 4.6) ──────────

def _station_data_xml(codes: list[str]) -> str:
    """Return a station-data document with one Bray-bound train per code."""
    records = "\n".join(
        f"""    <objStationData>
        <Traincode>{code}</Traincode>
        <Origin>Howth</Origin>
        <Destination>Bray</Destination>
        <Origintime>12:00</Origintime>
        <Destinationtime>13:00</Destinationtime>
        <Duein>10</Duein>
        <Late>0</Late>
        <Direction>Southbound</Direction>
        <Traintype>DART</Traintype>
        <Locationtype>S</Locationtype>
    </objStationData>"""
        for code in codes
    )
    return f"""
<ArrayOfObjStationData xmlns="http://api.irishrail.ie/realtime/">
{records}
</ArrayOfObjStationData>
"""


def _movement(location: str, code: str = "E777") -> TrainMovement:
    """Return a TrainMovement record for the given location."""
    return TrainMovement(
        code=code,
        date="01 Jan 2026",
        location_code="TGT",
        location=location,
        origin="Howth",
        destination="Bray",
        expected_arrival_time="12:10",
        expected_departure_time="12:11",
        scheduled_arrival_time="12:00",
        scheduled_departure_time="12:01",
    )


def _due_train(code: str) -> TrainDueTime:
    """Return a due train bound for Bray (distinct from stops_at targets)."""
    return TrainDueTime(
        code=code,
        origin="Howth",
        destination="Bray",
        origin_time="12:00",
        destination_time="13:00",
        due_in_mins=10,
        late_mins=0,
        expected_arrival_time="12:10",
        expected_departure_time="12:11",
        scheduled_arrival_time="12:00",
        scheduled_departure_time="12:01",
        type="DART",
        direction="Southbound",
        location_type="S",
    )


async def test_station_by_code_stops_at_multiple_candidates_concurrently(
    aresponses: ResponsesMockServer,
) -> None:
    """stops_at lookups overlap and never exceed the gate's concurrency cap.

    The library no longer maintains its own per-client semaphore for
    movement-history fan-outs: the shared :class:`RequestGate` is the
    single point of admission for every outbound HTTP call, so a
    fan-out of N concurrent movement lookups is bounded by the gate's
    ``max_concurrent`` setting instead. This test exercises that
    contract end-to-end by stubbing ``_request`` to (a) cross the
    real gate (``async with self._gate.acquire(priority)``) so the
    cap is genuinely the gate's, and (b) block on a release event
    once admitted, letting the assertions read the gate's own
    ``_in_flight`` counter to verify the cap held.
    """
    cap = 2
    # More candidates than the gate's cap, so the cap must engage.
    codes = [f"E{700 + index}" for index in range(cap + 2)]
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=_station_data_xml(codes), status=200),
    )

    release = asyncio.Event()
    saturated = asyncio.Event()
    gate: RequestGate  # populated in the ``async with`` below
    in_flight_samples: list[int] = []

    async def blocking_request(
        self: IrishRailClient,
        endpoint: str,
        params: dict[str, str] | None = None,
        priority: str = "normal",
    ) -> Element:
        """Stand-in for ``_request`` that crosses the gate then blocks.

        The ``async with self._gate.acquire(priority)`` line is the
        part that makes this test prove the gate's cap: every stubbed
        call has to wait for an actual slot, so two callers admitted
        in parallel are the gate's doing, not a coincidence of the
        test's own counter. The gate's own ``_in_flight`` is the
        authoritative value sampled by the assertion.

        The outer station-data call returns immediately so the
        ``_async_prune_trains`` fan-out can begin; only the inner
        movement-history lookups block on the release event. That
        way the test can observe the cap being held by the fan-out
        alone, which is what ``MAX_CONCURRENT_MOVEMENT_LOOKUPS``
        used to enforce and what the gate now enforces instead.
        """
        from xml.etree.ElementTree import fromstring

        from custom_components.irish_rail.pyirishrail.api import _strip_namespaces

        async with self._gate.acquire(priority):
            # Inside the gate's critical section, so the sample is
            # not racy.
            in_flight_samples.append(self._gate._in_flight)
            if self._gate._in_flight == self._gate._max_concurrent:
                saturated.set()
            if endpoint == "getStationDataByCodeXML":
                # The outer call must return quickly or the fan-out
                # never starts; the gate still records this slot
                # briefly above.
                return _strip_namespaces(fromstring(_station_data_xml(codes)))
            await release.wait()
            code = (params or {}).get("TrainId", "E777")
            # Two movements: PEARS (the station we're polling) followed
            # by Greystones (a downstream stop on the same journey).
            # ``_scoped_journey_stops`` cuts at the PEARS row, leaving
            # Greystones in the outcome so the ``stops_at`` filter can
            # match. The real ``_request`` strips namespaces before
            # returning; the stub has to do the same so plain tag
            # lookups in the parser see the rows.
            return _strip_namespaces(
                fromstring(
                    f"""
<ArrayOfObjTrainMovements xmlns="http://api.irishrail.ie/realtime/">
    <objTrainMovements>
        <TrainCode>{code}</TrainCode>
        <LocationCode>PEARS</LocationCode>
        <LocationFullName>Dublin Pearse</LocationFullName>
        <LocationOrder>1</LocationOrder>
    </objTrainMovements>
    <objTrainMovements>
        <TrainCode>{code}</TrainCode>
        <LocationCode>GRSTN</LocationCode>
        <LocationFullName>Greystones</LocationFullName>
        <LocationOrder>2</LocationOrder>
    </objTrainMovements>
</ArrayOfObjTrainMovements>
"""
                )
            )

    async with aiohttp.ClientSession() as session:
        gate = RequestGate(max_concurrent=cap, min_interval_seconds=0)
        client = IrishRailClient(session, gate=gate)
        with patch.object(ir_api.IrishRailClient, "_request", blocking_request):
            poll = asyncio.create_task(
                client.async_get_station_by_code("PEARS", stops_at="Greystones")
            )
            # Fires only when the gate is genuinely saturated (every
            # slot occupied at the same moment); a serialized
            # implementation would never reach this and the timeout
            # would trip.
            async with asyncio.timeout(30):
                await saturated.wait()

            assert gate._in_flight == cap, (
                f"gate cap {cap} not engaged; saw {gate._in_flight}"
            )

            release.set()
            trains = await poll

    # Every sample taken while a stubbed call was inside the gate's
    # critical section stayed at or under the cap. The cap is the
    # gate's, not the test's — the assertion reads the gate's own
    # ``_in_flight`` counter.
    assert all(sample <= cap for sample in in_flight_samples), (
        f"cap violation: samples={in_flight_samples}, cap={cap}"
    )
    # The cap was actually reached — without this we would not have
    # proven anything about overlap.
    assert max(in_flight_samples) == cap, in_flight_samples
    # No slots leaked past the test.
    assert gate._in_flight == 0
    # Every candidate whose route matched was kept in API response
    # order.
    assert [train.code for train in trains] == codes


async def test_prune_trains_partial_failure_isolates_single_train() -> None:
    """A failing movement lookup prunes only that candidate, not others."""
    client = IrishRailClient(MagicMock())
    with patch.object(
        client,
        "async_get_train_stops",
        new=AsyncMock(
            side_effect=[IrishRailParseError("boom"), [_movement("Greystones")]]
        ),
    ) as mock_stops:
        result = await client._async_prune_trains(
            [_due_train("E777"), _due_train("E888")],
            stops_at="Greystones",
        )

    # E777's lookup raised, so only E888 survives; both were fetched.
    assert [train.code for train in result] == ["E888"]
    assert mock_stops.await_count == 2


async def test_prune_trains_unexpected_lookup_error_isolates_single_train(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-IrishRailError failure prunes only that candidate, not all."""
    client = IrishRailClient(MagicMock())
    with patch.object(
        client,
        "async_get_train_stops",
        new=AsyncMock(
            side_effect=[RuntimeError("unexpected"), [_movement("Greystones")]]
        ),
    ) as mock_stops:
        result = await client._async_prune_trains(
            [_due_train("E777"), _due_train("E888")],
            stops_at="Greystones",
        )

    # E777's lookup raised an unexpected error: it is pruned with a logged
    # warning while E888 survives and the poll as a whole still succeeds.
    assert [train.code for train in result] == ["E888"]
    assert mock_stops.await_count == 2
    assert "failed unexpectedly" in caplog.text


async def test_stops_at_second_poll_uses_cached_routes(
    aresponses: ResponsesMockServer,
) -> None:
    """The second poll filters correctly without re-fetching known routes."""
    for _ in range(2):
        aresponses.add(
            "api.irishrail.ie",
            "/realtime/realtime.asmx/getStationDataByCodeXML",
            "GET",
            aresponses.Response(text=SAMPLE_STATION_DATA_XML, status=200),
        )
    movements_xml = SAMPLE_TRAIN_MOVEMENTS_XML.replace(
        "Dublin Pearse", "Greystones"
    ).replace("PEARS", "GREYS")
    # Only ONE movement response is queued: the second poll must be served
    # from the per-day cache instead of hitting the network again.
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=movements_xml, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)

        first = await client.async_get_station_by_code("PEARS", stops_at="Greystones")
        second = await client.async_get_station_by_code("PEARS", stops_at="Greystones")

        assert len(first) == 1
        assert [train.code for train in second] == ["E123"]


async def test_train_stops_cached_per_day(aresponses: ResponsesMockServer) -> None:
    """Repeat lookups for the same train/date are served from the cache."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_MOVEMENTS_XML, status=200),
    )
    # A different date is a separate cache entry and hits the network again.
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(
            text=SAMPLE_TRAIN_MOVEMENTS_XML.replace("01 Jan 2026", "02 Jan 2026"),
            status=200,
        ),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)

        first = await client.async_get_train_stops("E123", date="01 Jan 2026")
        second = await client.async_get_train_stops("E123", date="01 Jan 2026")
        other_day = await client.async_get_train_stops("E123", date="02 Jan 2026")

        assert len(first) == 1
        assert second is first  # cached instance returned unchanged
        assert len(other_day) == 1


async def test_train_stops_failure_not_cached(aresponses: ResponsesMockServer) -> None:
    """A failed lookup retries on the next call instead of being cached."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(status=500),
    )
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=SAMPLE_TRAIN_MOVEMENTS_XML, status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)

        with pytest.raises(IrishRailConnectionError):
            await client.async_get_train_stops("E123", date="01 Jan 2026")

        movements = await client.async_get_train_stops("E123", date="01 Jan 2026")
        assert len(movements) == 1


async def test_station_directions_dedupes_and_skips_empty() -> None:
    """Direction discovery ignores blanks and dedupes case-insensitively."""
    client = IrishRailClient(MagicMock())
    base = _due_train("A1")
    trains = [
        replace(base, code="A1", direction="To Cobh"),
        replace(base, code="A2", direction=""),
        replace(base, code="A3", direction="to cobh"),
        replace(base, code="A4", direction="Northbound"),
    ]
    with patch.object(
        client,
        "async_get_station_by_code",
        new_callable=AsyncMock,
        return_value=trains,
    ):
        directions = await client.async_get_station_directions("CORK")

    # First-seen casing wins for duplicates; blank values never appear.
    assert directions == ["Northbound", "To Cobh"]


def test_evict_movement_cache_drops_stale_dates_only_when_over_cap() -> None:
    """Lazy eviction keeps today's routes and only runs past the cap."""
    client = IrishRailClient(MagicMock())
    today = "2026-08-24"
    stale_key = ("A1 ", "2020-01-01")
    fresh_key = ("A2 ", today)

    # Under the cap nothing is evicted, even with stale entries present.
    client._movement_cache = {stale_key: [], fresh_key: []}
    client._evict_movement_cache(current_date=today)
    assert stale_key in client._movement_cache

    # Over the cap: every non-current-date entry is dropped, today's kept.
    filler: dict[tuple[str, str], list[TrainMovement]] = {
        (f"T{i} ", "2019-05-05"): [] for i in range(MOVEMENT_CACHE_MAX_ENTRIES)
    }
    client._movement_cache = {
        **filler,
        stale_key: [],
        fresh_key: [_movement("kept")],
    }
    client._evict_movement_cache(current_date=today)
    assert list(client._movement_cache) == [fresh_key]
    assert client._movement_cache[fresh_key] == [_movement("kept")]


def test_evict_movement_cache_enforces_cap_for_current_date_entries() -> None:
    """Same-date entries are oldest-first evicted when still over the cap."""
    client = IrishRailClient(MagicMock())
    today = "2026-08-24"
    filler: dict[tuple[str, str], list[TrainMovement]] = {
        (f"T{i} ", today): [] for i in range(MOVEMENT_CACHE_MAX_ENTRIES)
    }
    newest_key = ("NEW ", today)
    client._movement_cache = {**filler, newest_key: [_movement("newest")]}

    # Stale removal cannot help here (no other-date entries), yet the cap
    # must still hold: the oldest inserted key is dropped, the newest kept.
    client._evict_movement_cache(current_date=today)

    assert len(client._movement_cache) == MOVEMENT_CACHE_MAX_ENTRIES
    assert ("T0 ", today) not in client._movement_cache
    assert client._movement_cache[newest_key] == [_movement("newest")]

    # Under the cap afterwards, nothing further is evicted.
    client._evict_movement_cache(current_date=today)
    assert next(iter(client._movement_cache)) == ("T1 ", today)


def test_parse_station_data_skips_record_on_unexpected_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record raising mid-parse is skipped with a warning, not fatal."""
    root = fromstring(SAMPLE_STATION_DATA_XML)
    original = ir_api._find_tag_text

    def flaky(element: Element, tag: str) -> str | None:
        if tag == "Traincode":
            raise ValueError("unexpected malformed value")
        return original(element, tag)

    with patch.object(ir_api, "_find_tag_text", flaky):
        trains = parse_station_data(root)

    assert trains == []
    assert any(
        "Error parsing station data record" in record.getMessage()
        for record in caplog.records
    )


async def test_station_stops_at_options_union_dedupe_and_exclude() -> None:
    """Relevant-stops discovery unions routes minus home station."""
    client = IrishRailClient(MagicMock())
    base = _due_train("A1")
    trains = [base, replace(base, code="A2")]

    def _movement(code: str, location: str) -> TrainMovement:
        return TrainMovement(
            code=code,
            date="01 Jan 2026",
            location_code=f"L-{location}",
            location=location,
            origin="",
            destination="",
            expected_arrival_time="",
            expected_departure_time="",
            scheduled_arrival_time="",
            scheduled_departure_time="",
        )

    async def fake_stops(
        train_code: str,
        date: str | None = None,
        priority: str = "normal",
    ) -> list[TrainMovement]:
        if train_code.strip() == "A2":
            raise IrishRailConnectionError("route unavailable")
        return [
            _movement(train_code, "Dublin Pearse"),
            _movement(train_code, "Bray"),
            _movement(train_code, "Howth"),
        ]

    with (
        patch.object(
            client,
            "async_get_station_by_code",
            new_callable=AsyncMock,
            return_value=trains,
        ),
        patch.object(client, "async_get_train_stops", new=fake_stops),
    ):
        stops = await client.async_get_station_stops_at_options(
            "PEARS", exclude="Dublin Pearse"
        )

    # Home station excluded; failed route tolerated; dedupe keeps first
    # casing ("Bray" from the successful route); blanks skipped; sorted.
    assert stops == ["Bray", "Howth"]

    with patch.object(
        client,
        "async_get_station_by_code",
        new_callable=AsyncMock,
        return_value=[],
    ):
        assert await client.async_get_station_stops_at_options("EMPT") == []


# ── journey-scoped routes & stops-matrix learning (roadmap 4.8) ─────────────

def _journey_movement(
    location: str,
    location_code: str,
    code: str = "E932",
    destination: str = "Howth",
) -> TrainMovement:
    """Return a movement row on a journey bound for ``destination``."""
    return TrainMovement(
        code=code,
        date="01 Jan 2026",
        location_code=location_code,
        location=location,
        origin="Somewhere",
        destination=destination,
        expected_arrival_time="12:10",
        expected_departure_time="12:11",
        scheduled_arrival_time="12:00",
        scheduled_departure_time="12:01",
    )


def test_scoped_journey_stops_cuts_upstream_and_other_journeys() -> None:
    """Only the current journey's stops past the monitored station remain."""
    movements = [
        # An earlier Northbound journey of the same train code today:
        _journey_movement("Howth", "HOWTH", destination="Howth"),
        _journey_movement("Dublin Pearse", "PEARS", destination="Howth"),
        # The current Southbound journey towards Greystones:
        _journey_movement("Dublin Pearse", "PEARS", destination="Greystones"),
        _journey_movement("Bray", "BRAY", destination="Greystones"),
        _journey_movement("Greystones", "GREYS", destination="Greystones"),
        # A later return leg (Northbound again):
        _journey_movement("Greystones", "GREYS", destination="Howth"),
        _journey_movement("Bray", "BRAY", destination="Howth"),
    ]

    scoped = ir_api._scoped_journey_stops(
        movements, "Greystones", station_code="PEARS"
    )

    assert [stop.location for stop in scoped] == ["Bray", "Greystones"]


def test_scoped_journey_stops_cuts_by_station_name_fallback() -> None:
    """Without a code hit, the monitored station is found by display name."""
    movements = [
        _journey_movement("Dublin Pearse", "PERSE", destination="Greystones"),
        _journey_movement("Bray", "BRAY", destination="Greystones"),
    ]

    scoped = ir_api._scoped_journey_stops(
        movements,
        "Greystones",
        station_code="UNKNOWN",
        station_name="Dublin Pearse",
    )

    assert [stop.location for stop in scoped] == ["Bray"]


def test_scoped_journey_stops_blank_destination_keeps_all_rows() -> None:
    """Blank/malformed destinations degrade to the unscoped day history."""
    movements = [
        _journey_movement("Dublin Pearse", "PEARS", destination=""),
        _journey_movement("Bray", "BRAY", destination=""),
    ]

    scoped = ir_api._scoped_journey_stops(
        movements, "", station_code="PEARS"
    )

    assert [stop.location for stop in scoped] == ["Bray"]


def test_scoped_journey_stops_unknown_station_returns_rows_uncut() -> None:
    """An unmatched station must never silently empty the result."""
    movements = [
        _journey_movement("Dublin Pearse", "PEARS", destination="Greystones"),
        _journey_movement("Bray", "BRAY", destination="Greystones"),
    ]

    scoped = ir_api._scoped_journey_stops(
        movements, "Greystones", station_code="NOWHERE"
    )

    assert [stop.location for stop in scoped] == ["Dublin Pearse", "Bray"]


def test_scoped_journey_stops_cut_limited_to_contiguous_run() -> None:
    """Later same-day journeys sharing the destination do not leak in."""
    movements = [
        # The current Southbound journey towards Greystones:
        _journey_movement("Dublin Pearse", "PEARS", destination="Greystones"),
        _journey_movement("Greystones", "GREYS", destination="Greystones"),
        # An opposite-direction return leg (filtered out of the match):
        _journey_movement("Greystones", "GREYS", destination="Bray"),
        _journey_movement("Bray", "BRAY", destination="Bray"),
        # A later journey of the same train code, again bound for Greystones:
        # its stops must not appear in the result.
        _journey_movement("Dublin Connolly", "CONNY", destination="Greystones"),
        _journey_movement("Howth", "HOWTH", destination="Greystones"),
    ]

    scoped = ir_api._scoped_journey_stops(
        movements, "Greystones", station_code="PEARS"
    )

    # Only the current journey's downstream stop remains, not the later one.
    assert [stop.location for stop in scoped] == ["Greystones"]


async def test_stops_at_options_exclude_upstream_and_other_direction() -> None:
    """Option discovery only offers stops reached after the station."""
    client = IrishRailClient(MagicMock())
    train = replace(_due_train("A1"), destination="Greystones")

    async def fake_stops(
        train_code: str,
        date: str | None = None,
        priority: str = "normal",
    ) -> list[TrainMovement]:
        return [
            # Earlier Northbound journeys of the same train code today:
            _journey_movement("Howth", "HOWTH", destination="Malahide"),
            _journey_movement("Dublin Pearse", "PEARS", destination="Malahide"),
            # The current Southbound journey towards Greystones:
            _journey_movement("Dublin Pearse", "PEARS", destination="Greystones"),
            _journey_movement("Bray", "BRAY", destination="Greystones"),
            _journey_movement("Greystones", "GREYS", destination="Greystones"),
            # A later return leg (Northbound again):
            _journey_movement("Bray", "BRAY", destination="Howth"),
        ]

    with (
        patch.object(
            client,
            "async_get_station_by_code",
            new_callable=AsyncMock,
            return_value=[train],
        ),
        patch.object(client, "async_get_train_stops", new=fake_stops),
    ):
        stops = await client.async_get_station_stops_at_options(
            "PEARS", direction="Southbound", exclude="Dublin Pearse"
        )

    assert stops == ["Bray", "Greystones"]


async def test_prune_ignores_target_stop_on_other_journey() -> None:
    """A target only visited by the train's other journey prunes the train."""
    client = IrishRailClient(MagicMock())
    # A stale observation set must be replaced, never merged into.
    client.last_downstream_stop_names = frozenset({"STALE"})

    async def fake_stops(
        train_code: str,
        date: str | None = None,
        priority: str = "normal",
    ) -> list[TrainMovement]:
        return [
            # Earlier Northbound leg that does call at Greystones:
            _journey_movement("Dublin Pearse", "PEARS", destination="Howth"),
            _journey_movement("Greystones", "GREYS", destination="Howth"),
            # Current Southbound leg towards Bray: no Greystones downstream.
            _journey_movement("Dublin Pearse", "PEARS", destination="Bray"),
            _journey_movement("Dun Laoghaire", "DLGHY", destination="Bray"),
        ]

    with patch.object(client, "async_get_train_stops", new=fake_stops):
        result = await client._async_prune_trains(
            [_due_train("E777")],
            stops_at="Greystones",
            station_code="PEARS",
        )

    assert result == []
    # Only the current journey's downstream stops were observed.
    assert client.last_downstream_stop_names == frozenset({"Dun Laoghaire"})


async def test_prune_keeps_target_on_current_journey_and_records_observation() -> None:
    """Journey-scoped matching keeps the train and learns its stops."""
    client = IrishRailClient(MagicMock())

    async def fake_stops(
        train_code: str,
        date: str | None = None,
        priority: str = "normal",
    ) -> list[TrainMovement]:
        return [
            _journey_movement("Dublin Pearse", "PEARS", destination="Bray"),
            _journey_movement("Greystones", "GREYS", destination="Bray"),
            _journey_movement("Bray", "BRAY", destination="Bray"),
        ]

    with patch.object(client, "async_get_train_stops", new=fake_stops):
        result = await client._async_prune_trains(
            [_due_train("E777")],
            stops_at="Greystones",
            station_code="PEARS",
        )

    assert [train.code for train in result] == ["E777"]
    assert client.last_downstream_stop_names == frozenset({"Greystones", "Bray"})


async def test_prune_without_stops_at_resets_observations() -> None:
    """Passes without a stops_at filter carry no stale observations."""
    client = IrishRailClient(MagicMock())
    client.last_downstream_stop_names = frozenset({"STALE"})

    with patch.object(
        client,
        "async_get_train_stops",
        new=AsyncMock(side_effect=AssertionError("must not look up")),
    ):
        await client._async_prune_trains([_due_train("E777")], direction="Southbound")

    assert client.last_downstream_stop_names == frozenset()


async def test_stops_at_options_skip_blank_and_excluded_locations() -> None:
    """Blank rows and the excluded departure station never join options."""
    client = IrishRailClient(MagicMock())

    def _movement(location: str) -> TrainMovement:
        return TrainMovement(
            code="E700",
            date="01 Jan 2026",
            location_code=f"L-{location or 'BLANK'}",
            location=location,
            origin="Somewhere",
            destination="Greystones",
            expected_arrival_time="12:10",
            expected_departure_time="12:11",
            scheduled_arrival_time="12:00",
            scheduled_departure_time="12:01",
        )

    async def fake_stops(
        train_code: str,
        date: str | None = None,
        priority: str = "normal",
    ) -> list[TrainMovement]:
        return [
            _movement(""),
            _movement("dublin pearse"),
            _movement("Bray"),
            _movement(""),
        ]

    with (
        patch.object(
            client,
            "async_get_station_by_code",
            new_callable=AsyncMock,
            return_value=[_due_train("E700")],
        ),
        patch.object(client, "async_get_train_stops", new=fake_stops),
    ):
        options = await client.async_get_station_stops_at_options(
            "PEARS", exclude="Dublin Pearse"
        )

    assert options == ["Bray"]
