"""Tests for the Irish Rail config flow."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry, entity_registry
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.api import (
    IrishRailConnectionError,
    Station,
    TrainDueTime,
)
from custom_components.irish_rail.config_flow import IrishRailConfigFlow
from custom_components.irish_rail.const import (
    CONF_DIRECTION,
    CONF_STOPS_AT,
    DOMAIN,
)


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


def _add_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a mock config entry matching the mock station."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse (Northbound)",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
            "num_trains": 3,
        },
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add and fully set up a mock config entry."""
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[_mock_train()],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


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
            "num_trains": 3,
        }
        # The direction component of the unique ID is always lowercase.
        assert result["result"].unique_id == "PEARS_northbound"


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


async def test_config_flow_stores_num_trains(hass: HomeAssistant) -> None:
    """Test the user step stores the requested number of upcoming trains."""
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"station_code": "PEARS", "direction": "All", "num_trains": 5},
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"]["num_trains"] == 5


async def test_reconfigure_flow_success(hass: HomeAssistant) -> None:
    """Test the reconfigure flow updates the direction filter in place."""
    entry = await _setup_entry(hass)

    with (
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
            "num_trains": 3,
        },
        unique_id="PEARS_southbound",
    ).add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
            "num_trains": 3,
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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


async def test_options_flow_updates_interval_and_num_trains(
    hass: HomeAssistant,
) -> None:
    """Test valid option values are stored and applied to the coordinator."""
    entry = await _setup_entry(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 120, "num_trains": 2}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 120, "num_trains": 2}

    # The update listener applies the interval to the live coordinator.
    coordinator = entry.runtime_data.coordinator
    assert coordinator.update_interval == timedelta(seconds=120)


async def test_options_flow_rejects_out_of_range_values(
    hass: HomeAssistant,
) -> None:
    """Test out-of-range and non-numeric values are rejected by the schema."""
    entry = await _setup_entry(hass)

    bad_inputs = (
        {"scan_interval": 10, "num_trains": 3},  # below 30 s minimum
        {"scan_interval": 601, "num_trains": 3},  # above 10 min maximum
        {"scan_interval": "abc", "num_trains": 3},  # non-numeric
        {"scan_interval": 60, "num_trains": 99},  # train count above 5
        {"scan_interval": 60, "num_trains": 0},  # train count below 1
    )
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
            result["flow_id"], {"scan_interval": 90, "num_trains": 4}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 90, "num_trains": 4}


async def test_options_flow_defaults_reflect_current_settings(
    hass: HomeAssistant,
) -> None:
    """Test the options form pre-fills values from data/options."""
    entry = await _setup_entry(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
    num_trains_key = next(
        k for k in schema if getattr(k, "schema", None) == "num_trains"
    )
    assert scan_interval_key.default() == 60
    assert num_trains_key.default() == 3


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
            "num_trains": 3,
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
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

        # The stops_at field is a dropdown of station names plus "All".
        schema = result["data_schema"].schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert stops_at_key.default() == "All"
        assert set(schema[stops_at_key].container) == {"All", "Dublin Pearse"}

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 60, "num_trains": 3, "stops_at": "Dublin Pearse"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Dublin Pearse"


async def test_options_flow_stops_at_all_clears_filter(
    hass: HomeAssistant,
) -> None:
    """Test selecting "All" removes a previously stored filter."""
    entry = _add_entry_with_options(hass, {CONF_STOPS_AT: "Bray"})

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        return_value=[_mock_station()],
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

        # The form pre-fills the currently configured filter.
        schema = result["data_schema"].schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert stops_at_key.default() == "Bray"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 60, "num_trains": 3, "stops_at": "All"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert CONF_STOPS_AT not in entry.options


async def test_options_flow_stops_at_free_text_fallback_on_connection_error(
    hass: HomeAssistant,
) -> None:
    """Test the filter degrades to free text when stations cannot be fetched."""
    entry = _add_entry_with_options(hass, {})

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
        side_effect=IrishRailConnectionError,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "init"

        # Without a station list the field accepts arbitrary text.
        schema = result["data_schema"].schema
        stops_at_key = next(
            k for k in schema if getattr(k, "schema", None) == CONF_STOPS_AT
        )
        assert schema[stops_at_key] is str

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 60, "num_trains": 3, "stops_at": "Howth"},
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_STOPS_AT] == "Howth"


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
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
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
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
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
            result["flow_id"], {"scan_interval": 90, "num_trains": 2}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 90, "num_trains": 2}
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
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
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
    assert len(old_entity_ids) == 4
    old_device_id = device_registry.async_get_device_id_by_identifier(
        hass, (DOMAIN, "PEARS_northbound"), config_entry_id=entry.entry_id
    )
    assert old_device_id is not None

    with (
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
            result["flow_id"], {"direction": "Southbound"}
        )
        await hass.async_block_till_done()

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    # The old entities are gone from the live registry...
    for entity_id in old_entity_ids:
        assert ent_reg.async_get(entity_id) is None
    # ...but kept restorable in the deleted bin, tied to this config entry.
    deleted = [
        deleted_entry
        for deleted_entry in ent_reg.deleted_entities.values()
        if deleted_entry.config_entry_id == entry.entry_id
        and str(deleted_entry.unique_id).startswith("PEARS_northbound")
    ]
    assert len(deleted) == 4

    # Exactly four new entities were registered for the new identity.
    live = entity_registry.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert len(live) == 4
    assert all(
        str(registry_entry.unique_id).startswith("PEARS_southbound")
        for registry_entry in live
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
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
            "num_trains": 3,
        },
        unique_id="PEARS_all",
    )
    all_entry.add_to_hass(hass)
    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
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
    assert len(all_entity_ids) == 4

    northbound_entity_ids = sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, northbound.entry_id
        )
    )
    assert len(northbound_entity_ids) == 4

    # Reconfigure the Northbound entry to Southbound (identity change).
    with (
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            return_value=[_mock_station()],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            return_value=[_mock_train()],
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

    # The reconfigured side behaved as usual: its previous identity's entities
    # were removed (restorable trash), replaced by four southbound ones.
    for entity_id in northbound_entity_ids:
        assert ent_reg.async_get(entity_id) is None
    southbound_live = entity_registry.async_entries_for_config_entry(
        ent_reg, northbound.entry_id
    )
    assert len(southbound_live) == 4
    assert all(
        str(registry_entry.unique_id).startswith("PEARS_southbound")
        for registry_entry in southbound_live
    )

    # The sibling All entry is completely untouched: same live entity IDs,
    # still loaded, own device intact.
    assert all_entry.state is config_entries.ConfigEntryState.LOADED
    assert sorted(
        registry_entry.entity_id
        for registry_entry in entity_registry.async_entries_for_config_entry(
            ent_reg, all_entry.entry_id
        )
    ) == all_entity_ids
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
