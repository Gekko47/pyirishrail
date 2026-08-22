"""Tests for the Irish Rail config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import IrishRailConnectionError, Station
from custom_components.irish_rail.config_flow import IrishRailConfigFlow
from custom_components.irish_rail.const import DOMAIN


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


async def test_config_flow_success(hass: HomeAssistant) -> None:
    """Test successful config flow."""
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"station_code": "PEARS", "direction": "Northbound"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["title"] == "Dublin Pearse (Northbound)"
        assert result["data"] == {
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        }


async def test_config_flow_connection_error(hass: HomeAssistant) -> None:
    """Test config flow with connection error on form render."""
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
            {"station_code": "", "direction": "All"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_invalid_station(hass: HomeAssistant) -> None:
    """Test config flow reports invalid_station for an unknown code."""
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Bypass the dropdown validation by invoking the step handler
        # directly with a code that does not exist.
        flow = IrishRailConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(
            {"station_code": "NOPE", "direction": "All"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_station"}


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

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"station_code": "PEARS", "direction": "All"},
        )
        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"
