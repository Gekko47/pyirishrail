"""Config flow for the Irish Rail integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import IrishRailClient, IrishRailError, Station
from .const import (
    CONF_DIRECTION,
    CONF_NUM_TRAINS,
    CONF_SCAN_INTERVAL,
    CONF_STATION,
    CONF_STATION_CODE,
    CONF_STOPS_AT,
    DEFAULT_NUM_TRAINS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_NUM_TRAINS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_NUM_TRAINS,
    MIN_SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

DIRECTION_OPTIONS: list[str] = ["All", "Northbound", "Southbound"]


def normalized_direction(direction: str | None) -> str:
    """Return the canonical unique-ID component for a direction filter.

    The "All" filter is stored as ``None`` in entry data but must still be
    part of the unique ID; it maps to the literal ``all``. Every other
    direction is lowercased so the identity never depends on display casing.
    """
    return (direction or "all").lower()


def build_unique_id(station_code: str, direction: str | None) -> str:
    """Build the stable unique ID for a station/direction combination."""
    return f"{station_code}_{normalized_direction(direction)}"


class IrishRailConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Irish Rail.

    The flow presents a dropdown of all Irish Rail stations (fetched once
    per flow instance and cached) and an optional direction filter. The
    connection is validated before the entry is created, satisfying the
    ``test_before_configure`` quality-scale rule.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> IrishRailOptionsFlow:
        """Create the options flow handler."""
        return IrishRailOptionsFlow()

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
                vol.Optional(CONF_DIRECTION, default="All"): vol.In(DIRECTION_OPTIONS),
                vol.Optional(CONF_NUM_TRAINS, default=DEFAULT_NUM_TRAINS): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_NUM_TRAINS, max=MAX_NUM_TRAINS),
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

        selected_station = next((s for s in stations if s.code == station_code), None)
        if selected_station is None:
            errors["base"] = "invalid_station"
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        # Unique ID combines the API-assigned station code with the fixed
        # direction enum value, so the same station can be monitored once per
        # direction filter. Both parts are stable and never free-text.
        await self.async_set_unique_id(build_unique_id(station_code, direction))
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
                CONF_NUM_TRAINS: user_input.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry.

        The station is fixed; only the direction filter can be changed.
        On success the entry data is updated and the entry reloaded.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        try:
            stations = await self._async_fetch_stations()
        except IrishRailError as err:
            _LOGGER.error("Failed to fetch Irish Rail stations: %s", err)
            errors["base"] = "cannot_connect"
            stations = []

        current_direction = entry.data.get(CONF_DIRECTION) or "All"
        schema = vol.Schema(
            {
                vol.Required(CONF_DIRECTION, default=current_direction): vol.In(
                    DIRECTION_OPTIONS
                ),
            }
        )

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure", data_schema=schema, errors=errors
            )

        if not stations:
            errors.setdefault("base", "cannot_connect")
            return self.async_show_form(
                step_id="reconfigure", data_schema=schema, errors=errors
            )

        station_code: str = entry.data[CONF_STATION_CODE]
        selected_station = next((s for s in stations if s.code == station_code), None)
        if selected_station is None:
            errors["base"] = "invalid_station"
            return self.async_show_form(
                step_id="reconfigure", data_schema=schema, errors=errors
            )

        direction: str | None = user_input[CONF_DIRECTION]
        if direction == "All":
            direction = None

        title = selected_station.name
        if direction:
            title += f" ({direction})"

        # The unique ID combines the API-assigned station code with the
        # normalized direction, mirroring the initial flow. Reconfiguring to a
        # different direction therefore changes the entry's identity: claim the
        # new unique ID and reject the change if another entry already uses it
        # (the current entry itself is exempt from that check). If the reload
        # fails the flow aborts with ``update_entry_failed``.
        await self.async_set_unique_id(build_unique_id(station_code, direction))
        self._abort_if_unique_id_configured()

        return self.async_update_reload_and_abort(
            entry,
            data={
                CONF_STATION: selected_station.name,
                CONF_STATION_CODE: station_code,
                CONF_DIRECTION: direction,
                CONF_NUM_TRAINS: entry.data.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS),
            },
            title=title,
            unique_id=self.unique_id,
        )


class IrishRailOptionsFlow(OptionsFlow):
    """Handle an options flow for Irish Rail.

    Allows changing the polling interval (30 s - 10 min), the number of
    upcoming trains exposed via attributes, and an optional "only show
    trains stopping at <station>" filter without reloading the entry.
    """

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

    def _build_stops_at_schema_field(
        self, stations: list[Station], current: str
    ) -> dict[Any, Any]:
        """Build the ``stops_at`` schema field for the available stations.

        With a station list a dropdown of canonical station names (plus an
        ``All`` no-filter entry) is offered to prevent typos. If the station
        list could not be fetched the field degrades to free text so the
        options dialog remains usable offline.
        """
        if not stations:
            return {vol.Optional(CONF_STOPS_AT, default=current): str}
        options = {"All": "All"}
        options.update({s.name: s.name for s in sorted(stations, key=lambda x: x.name)})
        return {vol.Optional(CONF_STOPS_AT, default=current): vol.In(options)}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the integration options."""
        entry: ConfigEntry = self.config_entry
        current_interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds())
        )
        current_num_trains = int(
            entry.options.get(
                CONF_NUM_TRAINS,
                entry.data.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS),
            )
        )
        current_stops_at = entry.options.get(
            CONF_STOPS_AT, entry.data.get(CONF_STOPS_AT) or "All"
        )

        try:
            stations = await self._async_fetch_stations()
        except IrishRailError as err:
            _LOGGER.warning(
                "Could not load station list for options flow, "
                "falling back to free-text filter: %s",
                err,
            )
            stations = []

        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_SCAN_INTERVAL_SECONDS,
                        max=MAX_SCAN_INTERVAL_SECONDS,
                    ),
                ),
                vol.Required(CONF_NUM_TRAINS, default=current_num_trains): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_NUM_TRAINS, max=MAX_NUM_TRAINS),
                ),
                **self._build_stops_at_schema_field(stations, current_stops_at),
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="init", data_schema=schema)

        # Normalize the filter: "All" (or blank free text) means no filter,
        # mirroring the direction convention. The key is omitted entirely
        # when unset so options stay clean and legacy entries are unaffected.
        stops_at: str | None = user_input.get(CONF_STOPS_AT)
        if not stops_at or stops_at == "All":
            user_input.pop(CONF_STOPS_AT, None)

        return self.async_create_entry(title="", data=user_input)
