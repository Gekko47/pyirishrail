"""Config flow for the Irish Rail integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import IrishRailClient, IrishRailError, Station
from .const import CONF_DIRECTION, CONF_STATION, CONF_STATION_CODE, DOMAIN

_LOGGER = logging.getLogger(__name__)

DIRECTION_OPTIONS: list[str] = ["All", "Northbound", "Southbound"]


class IrishRailConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Irish Rail.

    The flow presents a dropdown of all Irish Rail stations (fetched once
    per flow instance and cached) and an optional direction filter. The
    connection is validated before the entry is created, satisfying the
    ``test_before_configure`` quality-scale rule.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow with an empty station cache."""
        self._stations: list[Station] = []
        self._client: IrishRailClient | None = None

    def _get_client(self) -> IrishRailClient:
        """Return (lazily creating) the API client for this flow."""
        if self._client is None:
            self._client = IrishRailClient(async_get_clientsession(self.hass))
        return self._client

    async def _async_fetch_stations(self) -> list[Station]:
        """Fetch the station list once, caching it on the flow instance."""
        if not self._stations:
            self._stations = await self._get_client().async_get_all_stations()
        return self._stations

    def _build_schema(self, stations: list[Station]) -> vol.Schema:
        """Build the user form schema from the available stations."""
        station_options = {
            s.code: s.name for s in sorted(stations, key=lambda x: x.name)
        }
        if not station_options:
            station_options = {"": "None available (error)"}

        return vol.Schema(
            {
                vol.Required(CONF_STATION_CODE): vol.In(station_options),
                vol.Optional(CONF_DIRECTION, default="All"): vol.In(
                    DIRECTION_OPTIONS
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: station selection and direction filter."""
        errors: dict[str, str] = {}

        # Fetch (and cache) the station list so the connection is validated
        # before the user can submit (test-before-configure).
        try:
            stations = await self._async_fetch_stations()
        except IrishRailError as err:
            _LOGGER.error("Failed to fetch Irish Rail stations: %s", err)
            errors["base"] = "cannot_connect"
            stations = []

        schema = self._build_schema(stations)

        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        if not stations:
            # The station list could not be loaded; keep the connection error
            # visible instead of reporting an invalid station.
            errors.setdefault("base", "cannot_connect")
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        station_code: str = user_input[CONF_STATION_CODE]
        direction: str | None = user_input.get(CONF_DIRECTION)
        if direction == "All":
            direction = None

        selected_station = next(
            (s for s in stations if s.code == station_code), None
        )
        if selected_station is None:
            errors["base"] = "invalid_station"
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        # Unique ID combines the API-assigned station code with the fixed
        # direction enum value, so the same station can be monitored once per
        # direction filter. Both parts are stable and never free-text.
        unique_direction = direction or "all"
        await self.async_set_unique_id(f"{station_code}_{unique_direction}")
        self._abort_if_unique_id_configured()

        title = selected_station.name
        if direction:
            title += f" ({direction})"

        return self.async_create_entry(
            title=title,
            data={
                CONF_STATION: selected_station.name,
                CONF_STATION_CODE: station_code,
                CONF_DIRECTION: direction,
            },
        )
