"""Tests for the Irish Rail config flow."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry, entity_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail import _PENDING_IDENTITY_RESTORES
from custom_components.irish_rail.config_flow import IrishRailConfigFlow
from custom_components.irish_rail.const import (
    CONF_DIRECTION,
    CONF_ENABLE_DIRECTION_FILTER,
    CONF_ENABLE_STOPS_AT_FILTER,
    CONF_STATION_CODE,
    CONF_STOPS_AT,
    DOMAIN,
)
from custom_components.irish_rail.coordinator import resolve_stops_at
from custom_components.irish_rail.errors import IrishRailConnectionError
from custom_components.irish_rail.models import (
    Station,
    TrainDueTime,
)
from custom_components.irish_rail.store import get_stops_store


@pytest.fixture(autouse=True)
def _quiet_live_stop_discovery() -> Iterator[None]:
    """Keep the options flow's live stops sample out of the way by default.

    The options flow only reaches live discovery when this install's
    learned matrix and the bundled seed are both empty for the entry,
    which is the normal case for a station the seed does not cover. Tests
    that exercise that path patch the method again.
    """
    with patch(
        "custom_components.irish_rail.client.IrishRailClient."
        "async_get_station_stops_at_options",
        return_value=[],
    ):
        yield


def _mock_station() -> Station:
    """Return a representative station for tests."""
    return Station(
        name="Dublin Pearse",
        alias="",
        latitude=53.3,
        longitude=-6.2,
        code="PEARS",
        id="150",
    )


def _mock_train(due_in: int = 10) -> TrainDueTime:
    """Return a representative TrainDueTime for tests."""
    return TrainDueTime(
        code=f"E{due_in}",
        origin="Howth",
        destination="Bray",
        origin_time="12:00",
        destination_time="13:00",
        due_in_mins=due_in,
        late_mins=0,
        expected_arrival_time="12:10",
        expected_departure_time="12:11",
        scheduled_arrival_time="12:00",
        scheduled_departure_time="12:01",
        type="DART",
        direction="Northbound",
        location_type="S",
    )


def _mock_cork_station() -> Station:
    """Return a representative non-corridor (free-text direction) station."""
    return Station(
        name="Cork",
        alias="",
        latitude=51.9,
        longitude=-8.46,
        code="CORK",
        id="30",
    )


def _train_with_direction(direction: str, due_in: int = 10) -> TrainDueTime:
    """Return the representative train reporting a different Direction."""
    return replace(_mock_train(due_in), direction=direction)


def _both_direction_trains() -> list[TrainDueTime]:
    """Return trains reporting each corridor direction."""
    return [
        _train_with_direction("Northbound"),
        _train_with_direction("Southbound", 20),
    ]


def _add_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a mock config entry matching the mock station."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Northbound)",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add and fully set up a mock config entry."""
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train()],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_config_flow_success(hass: HomeAssistant) -> None:
    """Test successful config flow."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        # Step 1 filters the list; the sole candidate auto-advances.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"

        # Step 2 offers the live-discovered values for that station.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"direction": "Northbound"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "Dublin Pearse (Northbound)"
        assert result["data"] == {
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        }
        # The direction component of the unique ID is always lowercase.
        assert result["result"].unique_id == "PEARS_northbound"


async def test_config_flow_connection_error(hass: HomeAssistant) -> None:
    """Test config flow with connection error on form render."""
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        side_effect=IrishRailConnectionError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_submit_when_stations_unavailable_preserves_error(
    hass: HomeAssistant,
) -> None:
    """Test that submitting while stations are unavailable keeps cannot_connect."""
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        side_effect=IrishRailConnectionError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

        # Submitting must not create an entry; the connection error persists.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_no_matching_stations(hass: HomeAssistant) -> None:
    """A filter matching nothing re-shows the search with an error."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"station_filter": "zzz"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "no_matching_stations"}

        # Correcting the filter auto-advances to the optional-filter page.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"station_filter": "Dublin"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"


async def test_config_flow_duplicate_abort(hass: HomeAssistant) -> None:
    """Test config flow aborts if station already configured."""
    # The flow's unique id is "{station_code}_{direction}" where direction
    # "All" is normalized to "all". Add an existing entry with that id.
    MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": None,
        },
        unique_id="PEARS_all",
    ).add_to_hass(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Step 1 succeeds and auto-advances; duplicate detection fires in
        # the direction step once the full station/direction identity is
        # known.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"direction": "All"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_user_step_offers_only_discovered_directions(
    hass: HomeAssistant,
) -> None:
    """A non-corridor station sees its own "To ..." values, never N/S."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_cork_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[
                _train_with_direction("To Cobh"),
                _train_with_direction("To Dublin Heuston", 20),
                _train_with_direction("To Midleton", 30),
            ],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        direction_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_DIRECTION
        )
        assert set(schema[direction_key].container) == {
            "All",
            "To Cobh",
            "To Dublin Heuston",
            "To Midleton",
        }

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "To Dublin Heuston"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cork (To Dublin Heuston)"
    assert result["data"][CONF_STATION_CODE] == "CORK"
    assert result["data"][CONF_DIRECTION] == "To Dublin Heuston"


async def test_directions_step_falls_back_to_free_text_on_discovery_error(
    hass: HomeAssistant,
) -> None:
    """Discovery failure degrades to free text instead of wrong guesses."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            side_effect=IrishRailConnectionError,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"

        # Without discovered values the field accepts arbitrary text.
        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        direction_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_DIRECTION
        )
        assert schema[direction_key] is str

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "To Limerick"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dublin Pearse (To Limerick)"
    assert result["data"][CONF_DIRECTION] == "To Limerick"


async def test_reconfigure_flow_success(hass: HomeAssistant) -> None:
    """Test the reconfigure flow updates the direction filter in place."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DIRECTION] == "Southbound"
    # The unique ID follows the new station/direction identity.
    assert entry.unique_id == "PEARS_southbound"


async def test_reconfigure_flow_direction_all_clears_filter(
    hass: HomeAssistant,
) -> None:
    """Test reconfiguring to "All" stores None and drops the title suffix."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "All"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DIRECTION] is None
    assert entry.title == "Dublin Pearse"
    # The unique ID follows the new station/direction identity.
    assert entry.unique_id == "PEARS_all"


async def test_directions_step_defaults_to_all_when_no_trains_due(
    hass: HomeAssistant,
) -> None:
    """An empty due-train list (overnight quiet period) also degrades."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Blank filter: the sole candidate auto-advances to directions.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"

        # Submitting the form default ("All") stores no filter at all.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dublin Pearse"
    assert result["data"][CONF_DIRECTION] is None


async def test_stops_at_step_lists_discovered_relevant_stops(
    hass: HomeAssistant,
) -> None:
    """The stops-at step offers only stops on the selected services."""
    bray = Station(
        name="Bray",
        alias="",
        latitude=53.2,
        longitude=-6.1,
        code="BRAY",
        id="120",
    )
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station(), bray],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            return_value=["Bray", "Howth"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Blank filter matches both stations: a pick screen appears.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "station_pick"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_STATION_CODE: "PEARS"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        # Opt into the stops-at filter only.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_STOPS_AT_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "stops_at"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert set(schema[stops_at_key].container) == {
            "All",
            "Bray",
            "Howth",
        }

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_STOPS_AT: "Howth"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dublin Pearse"
    assert result["data"][CONF_STOPS_AT] == "Howth"
    assert result["data"][CONF_DIRECTION] is None


async def test_filter_options_both_off_creates_unfiltered_entry(
    hass: HomeAssistant,
) -> None:
    """Unticked checkboxes finalize immediately with no filter keys."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Blank filter: the sole candidate auto-advances to filter options.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        # Both boxes unticked: monitor everything, no extra steps.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dublin Pearse"
    assert result["data"][CONF_DIRECTION] is None
    assert CONF_STOPS_AT not in result["data"]


async def test_reconfigure_preserves_seeded_stops_at(
    hass: HomeAssistant,
) -> None:
    """A data-seeded stops_at survives a direction change on reconfigure."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Northbound)",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
            CONF_STOPS_AT: "Howth",
        },
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train()],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DIRECTION] == "Southbound"
    # The filter is not editable here, so it must be carried forward.
    assert entry.data[CONF_STOPS_AT] == "Howth"


async def test_reconfigure_keeps_stored_value_selectable_when_unsampled(
    hass: HomeAssistant,
) -> None:
    """A stored direction stays selectable even when nothing samples it now."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Southbound)",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Southbound",
        },
        unique_id="PEARS_southbound",
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train()],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound"],
        ),
        patch.object(
            hass.config_entries,
            "async_schedule_reload",
            wraps=hass.config_entries.async_schedule_reload,
        ) as mock_schedule,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        direction_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_DIRECTION
        )
        # "Southbound" is merged back although no train reports it now.
        assert "Southbound" in schema[direction_key].container

        # Resubmitting the stored value stays valid (no-op reconfigure).
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DIRECTION] == "Southbound"
    # Nothing changed, so no reload was scheduled.
    assert mock_schedule.call_count == 0


async def test_reconfigure_flow_rejects_duplicate_identity(
    hass: HomeAssistant,
) -> None:
    """Test reconfiguring to a direction already used by another entry aborts."""
    entry = await _setup_entry(hass)
    MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Southbound)",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Southbound",
        },
        unique_id="PEARS_southbound",
    ).add_to_hass(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # The rejected change must not alter the current entry.
    assert entry.data[CONF_DIRECTION] == "Northbound"
    assert entry.unique_id == "PEARS_northbound"


async def test_reconfigure_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Test the reconfigure flow reports connection errors and allows retry."""
    entry = _add_entry(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        side_effect=IrishRailConnectionError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert result["errors"] == {"base": "cannot_connect"}

        # Submitting while stations are unavailable keeps the error visible.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "All"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_flow_invalid_station(hass: HomeAssistant) -> None:
    """Test the reconfigure flow reports invalid_station for unknown codes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ghost Station",
        data={
            "station": "Ghost Station",
            "station_code": "NOPE",
            "direction": "Northbound",
        },
        unique_id="NOPE_Northbound",
    )
    entry.add_to_hass(hass)

    flow = IrishRailConfigFlow()
    flow.hass = hass
    flow.context = {
        "source": config_entries.SOURCE_RECONFIGURE,
        "entry_id": entry.entry_id,
    }

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await flow.async_step_reconfigure({"direction": "All"})

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_station"}


async def test_reconfigure_flow_reload_failure_still_updates_data(
    hass: HomeAssistant,
) -> None:
    """Test a failing reload after reconfigure aborts but keeps new data."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            side_effect=Exception("boom"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert entry.data[CONF_DIRECTION] == "Southbound"


async def test_options_flow_updates_interval(
    hass: HomeAssistant,
) -> None:
    """Test valid option values are stored and applied to the coordinator.

    The entry carries no ``stops_at`` filter in ``entry.data``, so there is
    no redundant data-level value to defer to and the key is still written
    explicitly as ``None``.
    """
    entry = await _setup_entry(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 120}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 120, CONF_STOPS_AT: None}

    # The update listener applies the interval to the live coordinator.
    coordinator = entry.runtime_data.coordinator
    assert coordinator.update_interval == timedelta(seconds=120)


async def test_options_interval_change_preserves_data_level_stops_at(
    hass: HomeAssistant,
) -> None:
    """Changing only the scan interval must not drop a setup-time filter.

    ``resolve_stops_at`` reads options before ``entry.data``, so writing an
    options value for a filter the user never touched would silently shadow
    (and here, drop) the data-level filter.
    """
    entry = await _setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STOPS_AT: "Bray"}
    )

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 120}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_STOPS_AT not in entry.options
    assert resolve_stops_at(entry) == "Bray"


async def test_options_explicit_stops_at_change_overrides_data_level_value(
    hass: HomeAssistant,
) -> None:
    """An explicit filter change still wins over the data-level value."""
    entry = await _setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STOPS_AT: "Bray"}
    )

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 60, "stops_at": "Dublin Pearse"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Dublin Pearse"
    assert resolve_stops_at(entry) == "Dublin Pearse"


async def test_options_all_selection_still_clears_data_level_filter(
    hass: HomeAssistant,
) -> None:
    """Choosing "All" against a data-level filter is a real change."""
    entry = await _setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STOPS_AT: "Bray"}
    )

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 60, "stops_at": "All"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] is None
    assert resolve_stops_at(entry) is None


async def test_options_flow_rejects_out_of_range_values(
    hass: HomeAssistant,
) -> None:
    """Test out-of-range and non-numeric values are rejected by the schema."""
    entry = await _setup_entry(hass)

    bad_inputs = (
        {"scan_interval": 10},  # below 30 s minimum
        {"scan_interval": 601},  # above 10 min maximum
        {"scan_interval": "abc"},  # non-numeric
    )
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        for bad_input in bad_inputs:
            result = await hass.config_entries.options.async_init(entry.entry_id)
            assert result["type"] == data_entry_flow.FlowResultType.FORM

            # The flow manager rejects schema-invalid input outright; the UI
            # validates against the same schema so users never see this path.
            with pytest.raises(InvalidData):
                await hass.config_entries.options.async_configure(
                    result["flow_id"], bad_input
                )

        # Options remain untouched after every rejection.
        assert entry.options == {}

        # A subsequent valid submission still succeeds (recovery).
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 90}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "scan_interval": 90,
        CONF_STOPS_AT: None,
    }


async def test_options_flow_defaults_reflect_current_settings(
    hass: HomeAssistant,
) -> None:
    """Test the options form pre-fills values from data/options."""
    entry = await _setup_entry(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    data_schema = result["data_schema"]
    assert data_schema is not None
    schema = data_schema.schema
    scan_interval_key = next(
        k for k in schema if getattr(k, "schema", None) == "scan_interval"
    )
    stops_at_key = next(
        k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
    )
    assert scan_interval_key.default() == 60
    assert stops_at_key.default() == "All"


def _add_entry_with_options(
    hass: HomeAssistant, options: dict[str, Any]
) -> MockConfigEntry:
    """Add a mock config entry (not set up) carrying the given options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_northbound",
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_stops_at_dropdown_and_selection(
    hass: HomeAssistant,
) -> None:
    """Test the options flow offers a station dropdown and stores the filter."""
    entry = await _setup_entry(hass)

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

        # The stops_at field is a dropdown of station names plus "All".
        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert stops_at_key.default() == "All"
        assert set(schema[stops_at_key].container) == {"All", "Dublin Pearse"}

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 60, "stops_at": "Dublin Pearse"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Dublin Pearse"


async def test_options_flow_stops_at_all_clears_filter(
    hass: HomeAssistant,
) -> None:
    """Test selecting "All" removes a previously stored filter."""
    entry = _add_entry_with_options(hass, {CONF_STOPS_AT: "Bray"})

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

        # The form pre-fills the currently configured filter.
        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert stops_at_key.default() == "Bray"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 60, "stops_at": "All"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] is None


async def test_stops_at_dropdown_keeps_stored_value_absent_from_station_list(
    hass: HomeAssistant,
) -> None:
    """A stored filter missing from the fetched list stays selectable.

    Without the merge a transiently-short station list renders a select
    whose default is not one of its options, and submitting it drops the
    filter silently.
    """
    entry = _add_entry_with_options(hass, {CONF_STOPS_AT: "Bray"})

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert "Bray" in schema[stops_at_key].container
        assert stops_at_key.default() == "Bray"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 60, "stops_at": "Bray"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Bray"
    assert resolve_stops_at(entry) == "Bray"


async def test_stops_at_dropdown_free_text_fallback_keeps_stored_value(
    hass: HomeAssistant,
) -> None:
    """The free-text fallback still pre-fills the stored filter."""
    entry = _add_entry_with_options(hass, {CONF_STOPS_AT: "Bray"})

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        side_effect=IrishRailConnectionError,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert stops_at_key.default() == "Bray"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 60, "stops_at": "Bray"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Bray"


async def test_options_flow_stops_at_free_text_fallback_on_connection_error(
    hass: HomeAssistant,
) -> None:
    """Test the filter degrades to free text when stations cannot be fetched."""
    entry = _add_entry_with_options(hass, {})

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        side_effect=IrishRailConnectionError,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

        # Without a station list the field accepts arbitrary text.
        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert schema[stops_at_key] is str

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 60, "stops_at": "Howth"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Howth"


# ── Reachable-stops scoping for the options dropdown (G4) ────────────────────


def _stops_field_container(
    result: config_entries.ConfigFlowResult, key: str = CONF_STOPS_AT
) -> set[str]:
    """Return the dropdown options the rendered schema offers for one key."""
    data_schema = result["data_schema"]
    assert data_schema is not None
    schema = data_schema.schema
    field_key = next(k for k in schema if getattr(k, "schema", None) == key)
    return set(schema[field_key].container)


async def test_options_stops_at_is_scoped_to_learned_reachable_stops(
    hass: HomeAssistant,
) -> None:
    """A learned stop is offered; a station list is not consulted.

    The full station list would let the user pick a stop upstream of
    their own station, which no train from there can ever satisfy.
    """
    entry = await _setup_entry(hass)
    await get_stops_store(hass).async_record(
        "PEARS", "Northbound", ["Howth", "Malahide"]
    )

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ) as stations:
        result = await hass.config_entries.options.async_init(entry.entry_id)

    # The learned matrix answers, so the station list is never fetched.
    stations.assert_not_called()
    assert _stops_field_container(result) == {"All", "Howth", "Malahide"}


async def test_options_stops_at_falls_back_to_the_bundled_seed(
    hass: HomeAssistant,
) -> None:
    """With nothing learned, the bundled seed scopes the dropdown."""
    entry = await _setup_entry(hass)
    seed: dict[str, Any] = {
        "schema_version": 1,
        "stations": {
            "PEARS": {
                "updated": "2026-08-25T16:20:42+01:00",
                "directions": {"northbound": ["Howth", "Malahide"]},
            }
        },
    }

    with (
        patch(
            "custom_components.irish_rail.config_flow.async_load_bundled_stops_matrix",
            new=AsyncMock(return_value=seed),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ) as stations,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    stations.assert_not_called()
    assert _stops_field_container(result) == {"All", "Howth", "Malahide"}


async def test_options_stops_at_falls_back_to_live_discovery(
    hass: HomeAssistant,
) -> None:
    """An uncovered station is sampled live before any list is offered."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.config_flow.async_load_bundled_stops_matrix",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient."
            "async_get_station_stops_at_options",
            return_value=["Greystones"],
        ) as discover,
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ) as stations,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    discover.assert_awaited_once_with(
        "PEARS", direction="Northbound", exclude="Dublin Pearse"
    )
    stations.assert_not_called()
    assert _stops_field_container(result) == {"All", "Greystones"}


async def test_options_stops_at_last_resort_is_the_labelled_station_list(
    hass: HomeAssistant,
) -> None:
    """With no source at all, the full list is offered rather than a dead end."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.config_flow.async_load_bundled_stops_matrix",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient."
            "async_get_station_stops_at_options",
            side_effect=IrishRailConnectionError,
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert _stops_field_container(result) == {"All", "Dublin Pearse"}


async def test_options_stops_at_keeps_a_stored_value_outside_the_matrix(
    hass: HomeAssistant,
) -> None:
    """Scoping never makes a stored filter unsubmittable.

    A stop learned for a different direction is not in this entry's
    reachable set, but a no-op resubmit must still validate rather than
    silently drop the filter.
    """
    entry = await _setup_entry(hass)
    await get_stops_store(hass).async_record(
        "PEARS", "Northbound", ["Howth", "Malahide"]
    )
    hass.config_entries.async_update_entry(
        entry, options={CONF_STOPS_AT: "Greystones"}
    )

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert _stops_field_container(result) == {
            "All",
            "Greystones",
            "Howth",
            "Malahide",
        }

        data_schema = result["data_schema"]
        assert data_schema is not None
        stops_at_key = next(
            k
            for k in data_schema.schema
            if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert stops_at_key.default() == "Greystones"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 60, "stops_at": "Greystones"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Greystones"


# ── Update-listener-owned reload (HA >= 2026.6 deprecation, 2026.12 hard) ────


def _no_deprecation_warning(caplog: pytest.LogCaptureFixture) -> bool:
    """Return True when HA's update-listener/reload warning is absent."""
    return not any(
        "should use it for scheduling a reload" in record.getMessage()
        for record in caplog.records
    )


async def test_reconfigure_schedules_single_reload_via_listener(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A data-changing reconfigure schedules exactly one reload (listener)."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
        patch.object(
            hass.config_entries,
            "async_schedule_reload",
            wraps=hass.config_entries.async_schedule_reload,
        ) as mock_schedule,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DIRECTION] == "Southbound"
    assert entry.unique_id == "PEARS_southbound"
    # The update listener owns reload scheduling: exactly one was scheduled.
    assert mock_schedule.call_count == 1
    # The deprecated flow+listener combination must never be triggered.
    assert _no_deprecation_warning(caplog)


async def test_options_change_does_not_schedule_reload(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Option-only changes apply live without scheduling any reload."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch.object(
            hass.config_entries,
            "async_schedule_reload",
            wraps=hass.config_entries.async_schedule_reload,
        ) as mock_schedule,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 90}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "scan_interval": 90,
        CONF_STOPS_AT: None,
    }
    coordinator = entry.runtime_data.coordinator
    assert coordinator.update_interval == timedelta(seconds=90)
    assert mock_schedule.call_count == 0
    assert _no_deprecation_warning(caplog)


async def test_reconfigure_unchanged_direction_skips_reload(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Reconfiguring to the same direction changes nothing and never reloads."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound"],
        ),
        patch.object(
            hass.config_entries,
            "async_schedule_reload",
            wraps=hass.config_entries.async_schedule_reload,
        ) as mock_schedule,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Northbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DIRECTION] == "Northbound"
    # The unchanged identity must be preserved verbatim: forwarding an
    # explicit ``unique_id=None`` to ``async_update_entry`` would erase it
    # and break entity/device registry linkage after the next restart.
    assert entry.unique_id == "PEARS_northbound"
    # Nothing changed, so async_update_entry fired no listeners at all.
    assert mock_schedule.call_count == 0
    assert _no_deprecation_warning(caplog)


async def test_reconfigure_direction_change_drops_old_entities_and_device(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The previous direction's entities/device are removed on identity change."""
    entry = await _setup_entry(hass)

    ent_reg = entity_registry.async_get(hass)
    dev_reg = device_registry.async_get(hass)

    old_entity_ids = [
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
    ]
    # Two station sensors plus the two shared service entities (health
    # sensor + rebuild button) registered with this entry via the
    # binary_sensor / button platforms.
    assert len(old_entity_ids) == 4
    old_device_id = device_registry.async_get_device_id_by_identifier(
        hass, (DOMAIN, "PEARS_northbound"), config_entry_id=entry.entry_id
    )
    assert old_device_id is not None

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    # The old station-sensor entities are gone from the live registry...
    # (the two shared service entities are excluded: they deliberately
    # survive reconfigures, registered with this entry by design)
    for registry_entry in entity_registry.async_entries_for_config_entry(
        ent_reg, entry.entry_id
    ):
        if not str(registry_entry.unique_id).startswith("PEARS_northbound"):
            continue
        assert ent_reg.async_get(registry_entry.entity_id) is None
    # ...but kept restorable in the deleted bin, tied to this config entry.
    deleted = [
        deleted_entry
        for deleted_entry in ent_reg.deleted_entities.values()
        if deleted_entry.config_entry_id == entry.entry_id
        and str(deleted_entry.unique_id).startswith("PEARS_northbound")
    ]
    assert len(deleted) == 2

    # The two new station-sensor entities carry the new identity; the two
    # shared service entities keep their fixed unique IDs on the same entry.
    live = entity_registry.async_entries_for_config_entry(ent_reg, entry.entry_id)
    southbound_sensors = [
        registry_entry
        for registry_entry in live
        if str(registry_entry.unique_id).startswith("PEARS_southbound")
    ]
    assert len(southbound_sensors) == 2
    assert all(
        str(registry_entry.unique_id).startswith("PEARS_southbound")
        for registry_entry in southbound_sensors
    )

    # The abandoned old device is gone; a fresh one serves the new identity.
    assert dev_reg.async_get(old_device_id) is None
    assert (
        device_registry.async_get_device_id_by_identifier(
            hass, (DOMAIN, "PEARS_southbound"), config_entry_id=entry.entry_id
        )
        is not None
    )
    assert _no_deprecation_warning(caplog)


async def test_reconfigure_preserves_entity_customisations(
    hass: HomeAssistant,
) -> None:
    """A direction reconfigure carries the user's customisations across.

    The old identity's rows are still removed (the entity IDs have to
    change), but the name, icon and area are re-applied to the
    re-created entities instead of being lost.
    """
    entry = await _setup_entry(hass)
    ent_reg = entity_registry.async_get(hass)

    before = {
        str(registry_entry.unique_id).removeprefix("PEARS_northbound_"): (
            registry_entry.entity_id
        )
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
        if str(registry_entry.unique_id).startswith("PEARS_northbound_")
    }
    assert set(before) == {"next_train_due", "following_train_due"}

    ent_reg.async_update_entity(
        before["next_train_due"],
        name="My train",
        icon="mdi:star",
    )

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert entry.unique_id == "PEARS_southbound"

    after = {
        str(registry_entry.unique_id).removeprefix("PEARS_southbound_"): (
            registry_entry
        )
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
        if str(registry_entry.unique_id).startswith("PEARS_southbound_")
    }
    assert set(after) == {"next_train_due", "following_train_due"}
    # The customisation survived the identity change.
    assert after["next_train_due"].name == "My train"
    assert after["next_train_due"].icon == "mdi:star"
    # An entity that was never customised stays on the translated default.
    assert after["following_train_due"].name is None


async def test_direction_flip_back_restores_prior_customization(
    hass: HomeAssistant,
) -> None:
    """Switching back reclaims prior entity IDs with customizations intact."""
    entry = await _setup_entry(hass)

    ent_reg = entity_registry.async_get(hass)
    original_ids = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
    )

    # Customize one sensor the way a user would from the UI.
    due_sensor = next(
        registry_entry
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
        if str(registry_entry.unique_id).endswith("next_train_due")
    )
    ent_reg.async_update_entity(due_sensor.entity_id, name="My Custom Sensor")

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

        # Flip back to the original direction.
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Northbound"}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_DIRECTION] == "Northbound"

    # All original entity IDs are live again, customization included.
    restored = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
    )
    assert restored == original_ids

    restored_due = next(
        registry_entry
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
        if str(registry_entry.unique_id).endswith("next_train_due")
    )
    assert restored_due.name == "My Custom Sensor"


async def test_reconfigure_leaves_sibling_direction_entries_untouched(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Reconfiguring one entry never cleans up sibling entries at the station."""
    northbound = await _setup_entry(hass)

    # A second, independent entry for the same station: the "All" filter.
    all_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": None,
        },
        unique_id="PEARS_all",
    )
    all_entry.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train()],
    ):
        assert await hass.config_entries.async_setup(all_entry.entry_id)
        await hass.async_block_till_done()
    assert all_entry.state is config_entries.ConfigEntryState.LOADED

    ent_reg = entity_registry.async_get(hass)

    all_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, all_entry.entry_id
        )
    )
    # Two station sensors per entry.
    assert len(all_entity_ids) == 2

    northbound_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, northbound.entry_id
        )
    )
    # Owner of the two shared globals plus its own two station sensors.
    assert len(northbound_entity_ids) == 4

    # Reconfigure the Northbound entry to Southbound (identity change).
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": northbound.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert northbound.unique_id == "PEARS_southbound"

    # The reconfigured side behaved as usual: its previous identity's sensor
    # entities were removed (restorable trash) and two fresh southbound ones
    # registered; the two shared service entities registered with this entry
    # simply persist across the change.
    southbound_live = entity_registry.async_entries_for_config_entry(
        ent_reg, northbound.entry_id
    )
    for registry_entry in southbound_live:
        assert not str(registry_entry.unique_id).startswith("PEARS_northbound")
    southbound_live = entity_registry.async_entries_for_config_entry(
        ent_reg, northbound.entry_id
    )
    southbound_sensors = [
        registry_entry
        for registry_entry in southbound_live
        if str(registry_entry.unique_id).startswith("PEARS_southbound")
    ]
    # Two station sensors per entry.
    assert len(southbound_sensors) == 2
    assert all(
        str(registry_entry.unique_id).startswith("PEARS_southbound")
        for registry_entry in southbound_sensors
    )

    # The sibling All entry is completely untouched: same live entity IDs,
    # still loaded, own device intact.
    assert all_entry.state is config_entries.ConfigEntryState.LOADED
    assert (
        sorted(
            registry_entry.entity_id
            for registry_entry in entity_registry.async_entries_for_config_entry(
                ent_reg, all_entry.entry_id
            )
        )
        == all_entity_ids
    )
    assert (
        device_registry.async_get_device_id_by_identifier(
            hass, (DOMAIN, "PEARS_all"), config_entry_id=all_entry.entry_id
        )
        is not None
    )

    # And the southbound identity now exists alongside it.
    assert (
        device_registry.async_get_device_id_by_identifier(
            hass, (DOMAIN, "PEARS_southbound"), config_entry_id=northbound.entry_id
        )
        is not None
    )
    assert _no_deprecation_warning(caplog)


async def test_blank_filter_shows_pick_screen_listing_all(
    hass: HomeAssistant,
) -> None:
    """A blank filter lists every station on a dedicated pick screen."""
    cork = Station(
        name="Cork", alias="", latitude=51.9, longitude=-8.46, code="CORK", id="30"
    )
    bray = Station(
        name="Bray", alias="", latitude=53.2, longitude=-6.1, code="BRAY", id="120"
    )
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station(), cork, bray],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "station_pick"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        station_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STATION_CODE
        )
        assert set(schema[station_key].container) == {"PEARS", "CORK", "BRAY"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STATION_CODE: "CORK"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "All"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cork"
    assert result["data"][CONF_STATION_CODE] == "CORK"


async def test_pick_step_out_of_order_restarts_to_user_step(
    hass: HomeAssistant,
) -> None:
    """Entering the pick step without candidates restarts the flow."""
    flow = IrishRailConfigFlow()
    flow.hass = hass
    flow.flow_id = "test-pick-out-of-order"
    flow.handler = DOMAIN
    flow.context = {}

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await flow.async_step_station_pick(None)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        # An impossible code (vol.In bypassed) also routes back to search.
        flow._candidates = [_mock_station()]
        result = await flow.async_step_station_pick({CONF_STATION_CODE: "NOPE"})
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        # Finalizing without a station likewise routes back to search.
        flow._station_code = None
        result = await flow._async_finalize_entry()
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"


@pytest.mark.parametrize("mode", ["error", "empty"])
async def test_stops_at_step_falls_back_to_full_list(
    hass: HomeAssistant, mode: str
) -> None:
    """Discovery failure or zero live services degrades to all stations."""
    kwargs: dict[str, Any] = (
        {"side_effect": IrishRailConnectionError}
        if mode == "error"
        else {"return_value": []}
    )
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            **kwargs,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_STOPS_AT_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "stops_at"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        # Full cached station list instead of a dead end.
        assert set(schema[stops_at_key].container) == {
            "All",
            "Dublin Pearse",
        }

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_STOPS_AT: "All"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_STOPS_AT not in result["data"]


async def test_stops_at_step_out_of_order_restarts_to_user_step(
    hass: HomeAssistant,
) -> None:
    """Entering the stops-at step without a station restarts the flow."""
    flow = IrishRailConfigFlow()
    flow.hass = hass
    flow.flow_id = "test-stops-out-of-order"
    flow.handler = DOMAIN
    flow.context = {}

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await flow.async_step_stops_at(None)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_filter_options_both_on_chains_direction_then_stops(
    hass: HomeAssistant,
) -> None:
    """Direction submit chains into relevant stops when both are opted in."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            return_value=["Howth"],
        ) as mock_stops,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ENABLE_DIRECTION_FILTER: True,
                CONF_ENABLE_STOPS_AT_FILTER: True,
            },
        )
        assert result["step_id"] == "directions"

        # Direction submit chains into the stops screen for that direction.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Northbound"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "stops_at"
        mock_stops.assert_awaited_once_with(
            "PEARS", direction="Northbound", exclude="Dublin Pearse"
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_STOPS_AT: "Howth"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dublin Pearse (Northbound)"
    assert result["data"][CONF_DIRECTION] == "Northbound"
    assert result["data"][CONF_STOPS_AT] == "Howth"


async def test_directions_step_out_of_order_restarts_to_user_step(
    hass: HomeAssistant,
) -> None:
    """Entering the direction step without a station restarts the flow."""
    flow = IrishRailConfigFlow()
    flow.hass = hass
    flow.flow_id = "test-out-of-order"
    flow.handler = DOMAIN
    flow.context = {}

    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await flow.async_step_directions(None)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_station_filter_word_prefix_and_multi_term(
    hass: HomeAssistant,
) -> None:
    """Filtering mirrors irishrail.ie: word-start, case-insensitive, AND."""
    cork = Station(
        name="Cork", alias="", latitude=51.9, longitude=-8.46, code="CORK", id="30"
    )
    bray = Station(
        name="Bray", alias="", latitude=53.2, longitude=-6.1, code="BRAY", id="120"
    )
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station(), cork, bray],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        # Word-prefix hit on one station: auto-advance straight to the
        # optional-filter page, then on through the direction step.
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"station_filter": "pear"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "All"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_STATION_CODE] == "PEARS"

        # Multi-term queries AND together, case-insensitively.
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"station_filter": "DUBLIN p"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        # A mid-word fragment is not a word prefix: nothing matches.
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"station_filter": "earse"}
        )
        assert result["errors"] == {"base": "no_matching_stations"}


async def test_station_filter_matches_alias_tokens(
    hass: HomeAssistant,
) -> None:
    """Alias words participate in filtering."""
    junction = Station(
        name="CITY JUNCTION",
        alias="Dublin Belfast",
        latitude=54.6,
        longitude=-5.9,
        code="CITYJ",
        id="1516",
    )
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station(), junction],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # "belfast" only appears in the alias of the junction entry.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"station_filter": "belfast"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "filter_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_DIRECTION_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "directions"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "All"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_STATION_CODE] == "CITYJ"


# ── stops-at option fallback chain: live → cache → seed → full list ─────────


async def test_stops_at_step_persists_live_discovery(hass: HomeAssistant) -> None:
    """A successful live discovery is learned into the per-install matrix."""
    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            return_value=["Bray", "Howth"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "filter_options"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_STOPS_AT_FILTER: True},
        )
        assert result["step_id"] == "stops_at"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STOPS_AT: "Howth"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    store = get_stops_store(hass)
    assert await store.async_lookup("PEARS", None) == ["Bray", "Howth"]


async def test_stops_at_step_survives_persistence_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A persisting store failure warns but never dead-ends stops-at setup."""
    failing_store = MagicMock()
    failing_store.async_record = AsyncMock(side_effect=OSError("disk full"))

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            return_value=["Bray", "Howth"],
        ),
        patch(
            "custom_components.irish_rail.config_flow.get_stops_store",
            return_value=failing_store,
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "filter_options"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ENABLE_STOPS_AT_FILTER: True}
        )
        assert result["step_id"] == "stops_at"
        # The live-discovered stops are still offered despite the failure,
        # so choosing one completes setup normally.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STOPS_AT: "Howth"}
        )

    failing_store.async_record.assert_awaited()
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOPS_AT] == "Howth"
    assert "Could not persist discovered stops" in caplog.text


async def test_stops_at_step_prefers_cached_matrix_when_live_unavailable(
    hass: HomeAssistant,
) -> None:
    """With no samplable services, the learned matrix beats the full list."""
    store = get_stops_store(hass)
    assert await store.async_record("PEARS", None, ["Bray"]) == 1

    async def _empty_seed() -> dict[str, Any]:
        return {}

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            side_effect=IrishRailConnectionError,
        ),
        patch(
            "custom_components.irish_rail.config_flow.async_load_bundled_stops_matrix",
            new=_empty_seed,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_STOPS_AT_FILTER: True},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "stops_at"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        # Cached bucket offered instead of the full national station list.
        assert set(schema[stops_at_key].container) == {"All", "Bray"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STOPS_AT: "All"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_STOPS_AT not in result["data"]


async def test_stops_at_step_uses_bundled_seed_before_full_list(
    hass: HomeAssistant,
) -> None:
    """The bundled seed matrix is consulted before degrading to all stations."""

    async def _pearse_seed() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "stations": {
                "PEARS": {
                    "updated": "2026-08-25T12:00:00+00:00",
                    "directions": {"_all": ["Howth"]},
                }
            },
        }

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            side_effect=IrishRailConnectionError,
        ),
        patch(
            "custom_components.irish_rail.config_flow.async_load_bundled_stops_matrix",
            new=_pearse_seed,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENABLE_STOPS_AT_FILTER: True},
        )
        assert result["step_id"] == "stops_at"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        # Seed bucket offered instead of the full national station list.
        assert set(schema[stops_at_key].container) == {"All", "Howth"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STOPS_AT: "Howth"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOPS_AT] == "Howth"


async def test_stops_matrix_cache_is_direction_scoped(hass: HomeAssistant) -> None:
    """A cached bucket is only offered for its own direction."""
    store = get_stops_store(hass)
    assert await store.async_record("PEARS", "Northbound", ["Howth"]) == 1

    async def _empty_seed() -> dict[str, Any]:
        return {}

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_stops_at_options",
            side_effect=IrishRailConnectionError,
        ),
        patch(
            "custom_components.irish_rail.config_flow.async_load_bundled_stops_matrix",
            new=_empty_seed,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ENABLE_DIRECTION_FILTER: True,
                CONF_ENABLE_STOPS_AT_FILTER: True,
            },
        )
        assert result["step_id"] == "directions"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        assert result["step_id"] == "stops_at"

        data_schema = result["data_schema"]
        assert data_schema is not None
        schema = data_schema.schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        # The Northbound cache must never leak into a Southbound setup.
        assert set(schema[stops_at_key].container) == {"All", "Dublin Pearse"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_STOPS_AT: "All"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DIRECTION] == "Southbound"


# ── Transactional identity reconfigure (G2) ──────────────────────────────────


def _due_sensor_row(
    hass: HomeAssistant, entry: MockConfigEntry, suffix: str = "next_train_due"
) -> Any:
    """Return the entry's live station-sensor registry row for one entity key."""
    ent_reg = entity_registry.async_get(hass)
    return next(
        registry_entry
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
        if str(registry_entry.unique_id).endswith(suffix)
    )


def _rows_for_identity(
    hass: HomeAssistant, entry: MockConfigEntry, unique_id_prefix: str
) -> dict[str, Any]:
    """Return the entry's live station-sensor rows keyed by entity key."""
    ent_reg = entity_registry.async_get(hass)
    return {
        str(registry_entry.unique_id).removeprefix(unique_id_prefix): registry_entry
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        )
        if str(registry_entry.unique_id).startswith(unique_id_prefix)
    }


async def test_reconfigure_failed_reload_leaves_the_old_identity_intact(
    hass: HomeAssistant,
) -> None:
    """A reconfigure whose reload fails destroys nothing.

    The registry swap is deferred to the end of the *next* successful
    setup, so a reload that ends in SETUP_RETRY still shows the previous
    identity's entities and device, and the capture survives so a later
    successful reload can still carry the customisations across.
    """
    entry = await _setup_entry(hass)
    ent_reg = entity_registry.async_get(hass)
    dev_reg = device_registry.async_get(hass)

    old_due = _due_sensor_row(hass, entry)
    ent_reg.async_update_entity(old_due.entity_id, name="My train")
    old_device_id = device_registry.async_get_device_id_by_identifier(
        hass, (DOMAIN, "PEARS_northbound"), config_entry_id=entry.entry_id
    )
    assert old_device_id is not None

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            side_effect=IrishRailConnectionError,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY

    # Nothing was removed: the old rows, their customisation and the old
    # station device are all still registered against the entry.
    survivor = ent_reg.async_get(old_due.entity_id)
    assert survivor is not None
    assert survivor.name == "My train"
    assert dev_reg.async_get(old_device_id) is not None
    assert not _rows_for_identity(hass, entry, "PEARS_southbound_")
    # The capture is still pending, waiting for a setup that gets that far.
    assert entry.entry_id in _PENDING_IDENTITY_RESTORES

    # Once the API recovers, the retry completes the swap and carries the
    # customisation across.
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=_both_direction_trains(),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state.value == "loaded"
    assert entry.entry_id not in _PENDING_IDENTITY_RESTORES
    after = _rows_for_identity(hass, entry, "PEARS_southbound_")
    assert set(after) == {"next_train_due", "following_train_due"}
    assert after["next_train_due"].name == "My train"
    assert ent_reg.async_get(old_due.entity_id) is None
    assert dev_reg.async_get(old_device_id) is None


async def test_reconfigure_reload_does_not_trip_the_unload_task_timeout(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A reconfigure reload finishes without the ten-second unload stall.

    The customisation restore used to run as an entry-owned task that
    waited on the reload while the reload's unload waited on that task, so
    every reconfigure logged "Task ... did not complete in time".
    """
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
            return_value=_both_direction_trains(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.async_get_station_directions",
            return_value=["Northbound", "Southbound"],
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert not [
        record.getMessage()
        for record in caplog.records
        if "did not complete in time" in record.getMessage()
    ]


async def test_removing_a_sibling_drops_its_unconsumed_capture(
    hass: HomeAssistant,
) -> None:
    """A capture whose entry is removed is discarded, not left behind.

    Only a *loaded* sibling can observe the removal signal, which is why
    the assertion is made with a second entry still running.
    """
    survivor = await _setup_entry(hass)
    doomed = MockConfigEntry(
        domain=DOMAIN,
        title="Galway (All)",
        data={
            "station": "Galway",
            "station_code": "GALWAY",
            "direction": None,
        },
        unique_id="GALWAY_all",
    )
    doomed.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train()],
    ):
        assert await hass.config_entries.async_setup(doomed.entry_id)
        await hass.async_block_till_done()

    # Give the doomed entry a pending capture without letting the reload run.
    with patch.object(hass.config_entries, "async_schedule_reload"):
        hass.config_entries.async_update_entry(
            doomed,
            data={**doomed.data, CONF_DIRECTION: "To Dublin Heuston"},
            unique_id="GALWAY_todublinheuston",
        )
        await hass.async_block_till_done()
    assert doomed.entry_id in _PENDING_IDENTITY_RESTORES

    assert await hass.config_entries.async_remove(doomed.entry_id)
    await hass.async_block_till_done()

    assert doomed.entry_id not in _PENDING_IDENTITY_RESTORES
    assert survivor.state is config_entries.ConfigEntryState.LOADED
