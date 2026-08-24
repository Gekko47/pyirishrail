"""Tests for the Irish Rail API client."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree.ElementTree import fromstring

import aiohttp
from aresponses import ResponsesMockServer
import pytest

from custom_components.irish_rail import api as ir_api
from custom_components.irish_rail.api import (
    IrishRailClient,
    IrishRailConnectionError,
    IrishRailParseError,
    IrishRailTimeoutError,
    TrainDueTime,
    TrainMovement,
    parse_station_data,
)
from custom_components.irish_rail.const import MOVEMENT_CACHE_MAX_ENTRIES

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

TWO_TRAIN_STATION_DATA_XML = """
<ArrayOfObjStationData xmlns="http://api.irishrail.ie/realtime/">
    <objStationData>
        <Traincode>E777</Traincode>
        <Origin>Howth</Origin>
        <Destination>Bray</Destination>
        <Origintime>12:00</Origintime>
        <Destinationtime>13:00</Destinationtime>
        <Duein>10</Duein>
        <Late>0</Late>
        <Direction>Southbound</Direction>
        <Traintype>DART</Traintype>
        <Locationtype>S</Locationtype>
    </objStationData>
    <objStationData>
        <Traincode>E888</Traincode>
        <Origin>Howth</Origin>
        <Destination>Bray</Destination>
        <Origintime>12:30</Origintime>
        <Destinationtime>13:30</Destinationtime>
        <Duein>40</Duein>
        <Late>0</Late>
        <Direction>Southbound</Direction>
        <Traintype>DART</Traintype>
        <Locationtype>S</Locationtype>
    </objStationData>
</ArrayOfObjStationData>
"""


def _movements_xml(code: str, location: str) -> str:
    """Return a single-stop movement document for the given train/location."""
    return f"""
<ArrayOfObjTrainMovements xmlns="http://api.irishrail.ie/realtime/">
    <objTrainMovements>
        <TrainCode>{code}</TrainCode>
        <TrainDate>01 Jan 2026</TrainDate>
        <LocationCode>TGT</LocationCode>
        <LocationFullName>{location}</LocationFullName>
        <TrainOrigin>Howth</TrainOrigin>
        <TrainDestination>Bray</TrainDestination>
        <ExpectedArrival>12:10</ExpectedArrival>
        <ExpectedDeparture>12:11</ExpectedDeparture>
        <ScheduledArrival>12:00</ScheduledArrival>
        <ScheduledDeparture>12:01</ScheduledDeparture>
    </objTrainMovements>
</ArrayOfObjTrainMovements>
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
    """Every stops_at candidate gets its route looked up concurrently."""
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getStationDataByCodeXML",
        "GET",
        aresponses.Response(text=TWO_TRAIN_STATION_DATA_XML, status=200),
    )
    # Exactly two movement responses are queued, one per candidate train.
    # Both routes include Greystones so both trains are kept regardless of
    # response arrival order under concurrency; input order is preserved.
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=_movements_xml("E777", "Greystones"), status=200),
    )
    aresponses.add(
        "api.irishrail.ie",
        "/realtime/realtime.asmx/getTrainMovementsXML",
        "GET",
        aresponses.Response(text=_movements_xml("E888", "Greystones"), status=200),
    )

    async with aiohttp.ClientSession() as session:
        client = IrishRailClient(session)
        trains = await client.async_get_station_by_code("PEARS", stops_at="Greystones")

        assert [train.code for train in trains] == ["E777", "E888"]


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
    filler = {
        (f"T{i} ", "2019-05-05"): [] for i in range(MOVEMENT_CACHE_MAX_ENTRIES)
    }
    client._movement_cache = {**filler, stale_key: [], fresh_key: ["kept"]}
    client._evict_movement_cache(current_date=today)
    assert list(client._movement_cache) == [fresh_key]
    assert client._movement_cache[fresh_key] == ["kept"]


def test_parse_station_data_skips_record_on_unexpected_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record raising mid-parse is skipped with a warning, not fatal."""
    root = fromstring(SAMPLE_STATION_DATA_XML)
    original = ir_api._find_tag_text

    def flaky(element, tag):
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
