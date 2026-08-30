"""Config flow for the Irish Rail integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DIRECTION,
    CONF_ENABLE_DIRECTION_FILTER,
    CONF_ENABLE_STOPS_AT_FILTER,
    CONF_NUM_TRAINS,
    CONF_SCAN_INTERVAL,
    CONF_STATION,
    CONF_STATION_CODE,
    CONF_STATION_FILTER,
    CONF_STOPS_AT,
    DEFAULT_NUM_TRAINS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_NUM_TRAINS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_NUM_TRAINS,
    MIN_SCAN_INTERVAL_SECONDS,
)
from .gate import async_get_request_gate
from .identity import build_unique_id
from .pyirishrail import IrishRailClient, IrishRailError, Station
from .store import async_load_bundled_stops_matrix, get_stops_store, lookup_in_matrix
from .types import IrishRailConfigEntry

_LOGGER = logging.getLogger(__name__)

NO_FILTER_SENTINEL = "All"


def build_stops_at_schema_field(
    stations: list[Station], current: str
) -> dict[Any, Any]:
    """Build the ``stops_at`` schema field for the available stations.

    With a station list a dropdown of canonical station names (plus an
    ``All`` no-filter entry) is offered to prevent typos. If the station
    list could not be fetched the field degrades to free text so the form
    remains usable offline.
    """
    if not stations:
        return {vol.Optional(CONF_STOPS_AT, default=current): str}
    options = {NO_FILTER_SENTINEL: NO_FILTER_SENTINEL}
    options.update({s.name: s.name for s in sorted(stations, key=lambda x: x.name)})
    return {vol.Optional(CONF_STOPS_AT, default=current): vol.In(options)}


def filter_stations(stations: list[Station], text: str) -> list[Station]:
    """Return the stations matching a free-text filter.

    Mirrors the word-prefix semantics of irishrail.ie's own station search
    (verified against ``getStationsFilterXML``): case-insensitively, every
    whitespace-separated term must be a prefix of some whitespace-delimited
    word of the station name or alias. Blank text matches everything so the
    full list stays browsable; there is deliberately no fuzziness.
    """
    terms = text.casefold().split()
    if not terms:
        return list(stations)
    matched: list[Station] = []
    for station in stations:
        words = station.name.casefold().split()
        if station.alias:
            words.extend(station.alias.casefold().split())
        if all(any(word.startswith(term) for word in words) for term in terms):
            matched.append(station)
    return matched


class IrishRailConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Irish Rail.

    Step one narrows the station list with an optional free-text filter
    using the same word-prefix semantics as irishrail.ie's own search; a
    single match skips straight ahead, otherwise a pick screen lists the
    candidates (the full list when the filter is left blank). The
    connection is validated up front (``test_before_configure``). The
    final step offers only the direction values that are actually valid
    *for the chosen station*, discovered live from its due-trains list:
    ``Northbound`` / ``Southbound`` on the Dundalk-Rosslare and
    Sligo-Dublin corridors, free-text values such as ``To Cork``
    everywhere else. When nothing is currently due (e.g. overnight) the
    field degrades to free text so setup never blocks.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: IrishRailConfigEntry,
    ) -> IrishRailOptionsFlow:
        """Create the options flow handler."""
        return IrishRailOptionsFlow()

    def __init__(self) -> None:
        """Initialize the flow with empty caches and no selection yet."""
        self._stations: list[Station] = []
        self._client: IrishRailClient | None = None
        self._directions_cache: dict[str, list[str] | None] = {}
        self._station_code: str | None = None
        self._num_trains: int = DEFAULT_NUM_TRAINS
        self._stops_at: str | None = None
        self._station_filter: str = ""
        self._candidates: list[Station] | None = None
        self._want_direction = False
        self._want_stops_at = False
        self._direction: str | None = None

    def _get_client(self) -> IrishRailClient:
        """Return (lazily creating) the API client for this flow."""
        if self._client is None:
            # Share the per-HA request gate with the coordinator's
            # client so the config flow's discovery lookups and the
            # live polling share one rate budget against the public
            # API. See ``gate.py`` for the rationale.
            self._client = IrishRailClient(
                async_get_clientsession(self.hass),
                gate=async_get_request_gate(self.hass),
            )
        return self._client

    async def _async_fetch_stations(self) -> list[Station]:
        """Fetch the station list once, caching it on the flow instance."""
        if not self._stations:
            self._stations = await self._get_client().async_get_all_stations()
        return self._stations

    async def _async_discover_directions(self, station_code: str) -> list[str] | None:
        """Return live direction values for a station, or None if unavailable.

        ``None`` means discovery could not be performed (connection error);
        an empty list means it succeeded but no trains are currently due.
        Both degrade identically to free text downstream. Results are
        cached per station code, so re-rendering the form after a
        validation error does not hit the API again.
        """
        if station_code in self._directions_cache:
            return self._directions_cache[station_code]
        try:
            directions = await self._get_client().async_get_station_directions(
                station_code
            )
        except IrishRailError as err:
            _LOGGER.warning(
                "Could not discover directions for %s: %s", station_code, err
            )
            directions = None
        self._directions_cache[station_code] = directions
        return directions

    def _build_direction_step_schema(
        self, directions: list[str] | None, current: str | None = None
    ) -> vol.Schema:
        """Build the direction-selection schema for one station.

        With discovered values a dropdown of exactly those values (plus
        ``All``) is offered; the strings shown are verbatim what the API
        reports, so the case-insensitive local pruner can always match
        them. A previously stored value that is not currently sampled is
        merged back into the options, keeping a no-op resubmit valid and
        allowing a switch to a direction outside the current lookahead
        window. Without any discovered value (API failure, or no due
        trains overnight) the field degrades to free text rather than
        guessing corridor semantics that may not apply.
        """
        default = current or NO_FILTER_SENTINEL
        if not directions:
            return vol.Schema(
                {
                    vol.Optional(CONF_DIRECTION, default=default): str,
                }
            )
        options: dict[str, str] = {NO_FILTER_SENTINEL: NO_FILTER_SENTINEL}
        lowered = {value.lower() for value in directions}
        for value in directions:
            options[value] = value
        if current and current != NO_FILTER_SENTINEL and current.lower() not in lowered:
            options[current] = current
        return vol.Schema(
            {
                vol.Required(CONF_DIRECTION, default=default): vol.In(options),
            }
        )

    def _build_schema(self) -> vol.Schema:
        """Build the first-step schema (filter text and train count)."""
        return vol.Schema(
            {
                vol.Optional(CONF_STATION_FILTER, default=""): str,
                vol.Optional(CONF_NUM_TRAINS, default=DEFAULT_NUM_TRAINS): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_NUM_TRAINS, max=MAX_NUM_TRAINS),
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the first step: narrow the station list by name.

        The station-list fetch doubles as the connection check, keeping
        ``test_before_configure`` satisfied before any form is offered.
        An empty filter browses every station; a single match skips the
        pick screen entirely.
        """
        errors: dict[str, str] = {}

        # Fetch (and cache) the station list so the connection is validated
        # before the user can submit (test-before-configure).
        try:
            stations = await self._async_fetch_stations()
        except IrishRailError as err:
            _LOGGER.error("Failed to fetch Irish Rail stations: %s", err)
            errors["base"] = "cannot_connect"
            stations = []

        schema = self._build_schema()

        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        if not stations:
            # The station list could not be loaded; keep the connection error
            # visible instead of reporting an unmatched filter.
            errors.setdefault("base", "cannot_connect")
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        # Remember the step-one answers up front: only station/direction
        # form part of the entry identity created later.
        self._num_trains = int(user_input.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS))
        self._station_filter = str(user_input.get(CONF_STATION_FILTER, "")).strip()

        candidates = filter_stations(stations, self._station_filter)

        if not candidates:
            errors["base"] = "no_matching_stations"
            return self.async_show_form(
                step_id="user", data_schema=schema, errors=errors
            )

        if len(candidates) == 1:
            # A single hit skips the pick screen entirely.
            self._station_code = candidates[0].code
            return await self.async_step_filter_options()

        self._candidates = candidates
        return await self.async_step_station_pick()

    async def async_step_station_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle choosing one station from the filtered candidates."""
        if not self._candidates:
            # Step entered out of order; restart defensively.
            return await self.async_step_user(None)

        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_CODE): vol.In(
                    {s.code: s.name for s in self._candidates}
                ),
            }
        )

        if user_input is None:
            return self.async_show_form(
                step_id="station_pick",
                data_schema=schema,
                description_placeholders={"filter": self._station_filter},
            )

        station_code: str = user_input[CONF_STATION_CODE]
        selected_station = next(
            (s for s in self._candidates if s.code == station_code), None
        )
        if selected_station is None:
            # Unreachable through vol.In; restart defensively.
            return await self.async_step_user(None)

        self._station_code = station_code
        return await self.async_step_filter_options()

    async def async_step_filter_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which optional filters to configure; unticked means All."""
        selected_station = next(
            (s for s in self._stations if s.code == self._station_code), None
        )
        station_name = (
            selected_station.name if selected_station else (self._station_code or "")
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_ENABLE_DIRECTION_FILTER, default=False): bool,
                vol.Required(CONF_ENABLE_STOPS_AT_FILTER, default=False): bool,
            }
        )
        if user_input is None:
            return self.async_show_form(
                step_id="filter_options",
                data_schema=schema,
                description_placeholders={"station": station_name},
            )

        self._want_direction = bool(user_input[CONF_ENABLE_DIRECTION_FILTER])
        self._want_stops_at = bool(user_input[CONF_ENABLE_STOPS_AT_FILTER])

        if self._want_direction:
            return await self.async_step_directions()
        if self._want_stops_at:
            return await self.async_step_stops_at()
        return await self._async_finalize_entry()

    async def async_step_stops_at(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer only the stops served by the filtered services."""
        station_code = self._station_code
        if station_code is None:
            # Step entered out of order; restart defensively.
            return await self.async_step_user(None)
        selected_station = next(
            (s for s in self._stations if s.code == station_code), None
        )
        station_name = selected_station.name if selected_station else station_code

        try:
            stops = await self._get_client().async_get_station_stops_at_options(
                station_code,
                direction=self._direction,
                exclude=station_name,
            )
        except IrishRailError as err:
            _LOGGER.warning("Could not discover stops for %s: %s", station_code, err)
            stops = None

        if stops:
            # Live discovery succeeded: heal the per-install matrix so the
            # answer survives quiet hours (see store.py). A persistence
            # failure must not dead-end setup — the live-discovered stops
            # already found are still offered below, so degrade to a warning.
            try:
                await get_stops_store(self.hass).async_record(
                    station_code, self._direction, stops
                )
            except Exception:
                # Deliberate broad guard mirroring the coordinator: any
                # storage failure degrades to "matrix not persisted", never
                # to a failed setup step.
                _LOGGER.warning(
                    "Could not persist discovered stops for %s (%s)",
                    station_name,
                    station_code,
                    exc_info=True,
                )
        else:
            # No live services to sample (or discovery failed): fall back to
            # this install's learned matrix, then the bundled seed, and only
            # then degrade to the full cached station list instead of
            # dead-ending setup.
            stops = await get_stops_store(self.hass).async_lookup(
                station_code, self._direction
            )
            if not stops:
                seed = await async_load_bundled_stops_matrix()
                stops = lookup_in_matrix(seed, station_code, self._direction)

        field: dict[Any, Any]
        if stops:
            options: dict[str, str] = {NO_FILTER_SENTINEL: NO_FILTER_SENTINEL}
            for stop in stops:
                options[stop] = stop
            field = {
                vol.Optional(CONF_STOPS_AT, default=NO_FILTER_SENTINEL): vol.In(
                    options
                )
            }
        else:
            field = build_stops_at_schema_field(self._stations, NO_FILTER_SENTINEL)

        schema = vol.Schema({**field})

        if user_input is None:
            return self.async_show_form(
                step_id="stops_at",
                data_schema=schema,
                description_placeholders={"station": station_name},
            )

        raw = user_input.get(CONF_STOPS_AT)
        self._stops_at = None if not raw or raw == NO_FILTER_SENTINEL else raw
        return await self._async_finalize_entry()

    async def _async_finalize_entry(self) -> ConfigFlowResult:
        """Claim the entry identity and create it once all steps are done."""
        station_code = self._station_code
        if station_code is None:
            # Defensive: finalize is only reached after a station is chosen.
            return await self.async_step_user(None)
        selected_station = next(
            (s for s in self._stations if s.code == station_code), None
        )
        station_name = selected_station.name if selected_station else station_code

        await self.async_set_unique_id(
            build_unique_id(station_code, self._direction)
        )
        self._abort_if_unique_id_configured()

        title = station_name
        if self._direction:
            title += f" ({self._direction})"

        entry_data: dict[str, Any] = {
            CONF_STATION: station_name,
            CONF_STATION_CODE: station_code,
            CONF_DIRECTION: self._direction,
            CONF_NUM_TRAINS: self._num_trains,
        }
        # "All" (or blank free text) seeds no filter at all, mirroring the
        # options flow's normalization convention.
        if self._stops_at and self._stops_at != NO_FILTER_SENTINEL:
            entry_data[CONF_STOPS_AT] = self._stops_at

        return self.async_create_entry(title=title, data=entry_data)

    async def async_step_directions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the second step: direction filter for the chosen station.

        The dropdown lists only the direction values the API actually
        reports for this station right now — Northbound/Southbound on the
        two corridors, e.g. To Cobh / To Dublin Heuston / To Midleton at
        Cork — instead of offering filters that could never match.
        """
        station_code = self._station_code
        if station_code is None:
            # Step entered out of order; restart the flow defensively.
            return await self.async_step_user(None)

        directions = await self._async_discover_directions(station_code)
        schema = self._build_direction_step_schema(directions)

        if user_input is None:
            selected_station = next(
                (s for s in self._stations if s.code == station_code), None
            )
            return self.async_show_form(
                step_id="directions",
                data_schema=schema,
                description_placeholders={
                    "station": (
                        selected_station.name if selected_station else station_code
                    )
                },
            )

        direction: str | None = user_input.get(CONF_DIRECTION)
        if direction == NO_FILTER_SENTINEL:
            direction = None
        self._direction = direction

        # The stops-at step (when requested) narrows on this direction's
        # services, so chain there before finalizing the entry. The unique
        # ID combines the API-assigned station code with the normalized
        # (lowercased) direction value and is claimed at finalization time.
        if self._want_stops_at:
            return await self.async_step_stops_at()
        return await self._async_finalize_entry()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry.

        The station is fixed; only the direction filter can be changed, so
        the relevant options are discovered live for that one station. On
        success the entry data (and identity) are updated in place and the
        integration's update listener schedules the single required reload
        — since HA 2026.6 the listener, not the flow, must own reload
        scheduling when one exists.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        try:
            stations = await self._async_fetch_stations()
        except IrishRailError as err:
            _LOGGER.error("Failed to fetch Irish Rail stations: %s", err)
            errors["base"] = "cannot_connect"
            stations = []

        station_code: str = entry.data[CONF_STATION_CODE]
        selected_station = next((s for s in stations if s.code == station_code), None)

        if user_input is None:
            # Build the form from live discovery for this station. The
            # stored value is merged back into the options (see helper), so
            # resubmitting the current setting always validates even when
            # that direction has no trains within the lookahead window.
            directions = (
                await self._async_discover_directions(station_code)
                if selected_station is not None
                else None
            )
            current = entry.data.get(CONF_DIRECTION) or NO_FILTER_SENTINEL
            schema = self._build_direction_step_schema(directions, current)
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=schema,
                errors=errors,
                description_placeholders={
                    "station": (
                        selected_station.name if selected_station else station_code
                    )
                },
            )

        if not stations:
            errors.setdefault("base", "cannot_connect")
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._build_direction_step_schema(None),
                errors=errors,
            )

        if selected_station is None:
            errors["base"] = "invalid_station"
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._build_direction_step_schema(None),
                errors=errors,
            )

        direction: str | None = user_input.get(CONF_DIRECTION)
        if direction == NO_FILTER_SENTINEL:
            direction = None

        title = selected_station.name
        if direction:
            title += f" ({direction})"

        # The unique ID combines the API-assigned station code with the
        # normalized direction, mirroring the initial flow. Reconfiguring to a
        # different direction therefore changes the entry's identity: claim the
        # new unique ID and reject the change if another entry already uses it.
        # When the submitted direction matches the current one the identity is
        # unchanged, so the uniqueness claim is skipped entirely — claiming it
        # would find this very entry and wrongly abort with
        # ``already_configured``.
        new_unique_id = build_unique_id(station_code, direction)
        if new_unique_id != entry.unique_id:
            await self.async_set_unique_id(new_unique_id)
            # No ``updates=`` argument is passed, so HA's reload_on_update
            # machinery (and its update-listener conflict deprecation,
            # breaking in 2026.12) cannot engage here; this call is pure
            # duplicate-identity detection.
            self._abort_if_unique_id_configured()

        # Reload ownership belongs to the integration's update listener since
        # HA 2026.6 (hard error in 2026.12): a flow-scheduled reload alongside
        # an existing listener can double-reload or race. The entry is updated
        # here and the listener detects the data change, scheduling the single
        # required reload itself; option-only changes keep applying in place.
        new_data: dict[str, Any] = {
            CONF_STATION: selected_station.name,
            CONF_STATION_CODE: station_code,
            CONF_DIRECTION: direction,
            CONF_NUM_TRAINS: entry.data.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS),
        }
        # The "stops at" filter is not editable here (the options flow owns
        # it), so any existing value must survive the identity rewrite.
        if preserved_stops_at := entry.data.get(CONF_STOPS_AT):
            new_data[CONF_STOPS_AT] = preserved_stops_at

        # Only forward the identity when this flow actually claimed a new
        # one: HA 2026.8's ``async_update_entry`` treats an explicit
        # ``unique_id=None`` as a real value and reindexes the entry to
        # ``None``, silently erasing the identity that entity/device
        # registry linkage depends on. A same-direction reconfigure never
        # claims a unique ID, so nothing identity-related is passed here.
        updates: dict[str, Any] = {"data": new_data, "title": title}
        if self.unique_id is not None and self.unique_id != entry.unique_id:
            updates["unique_id"] = self.unique_id
        self.hass.config_entries.async_update_entry(entry, **updates)
        return self.async_abort(reason="reconfigure_successful")


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
            # Share the per-HA request gate with the coordinator's
            # client and the user config flow so the options flow's
            # discovery lookups do not displace live polling. See
            # ``gate.py`` for the rationale.
            self._client = IrishRailClient(
                async_get_clientsession(self.hass),
                gate=async_get_request_gate(self.hass),
            )
        return self._client

    async def _async_fetch_stations(self) -> list[Station]:
        """Fetch the station list once, caching it on the flow instance."""
        if not self._stations:
            self._stations = await self._get_client().async_get_all_stations()
        return self._stations

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the integration options."""
        entry: IrishRailConfigEntry = self.config_entry
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
                **build_stops_at_schema_field(stations, current_stops_at),
            }
        )

        if user_input is None:
            return self.async_show_form(step_id="init", data_schema=schema)

        # Normalize the filter: "All" (or blank free text) means no filter.
        # An explicit None is stored rather than omitting the key so that a
        # value seeded into entry.data by the initial config flow cannot
        # resurface afterwards: whatever was saved here last always wins.
        stops_at: str | None = user_input.get(CONF_STOPS_AT)
        if not stops_at or stops_at == NO_FILTER_SENTINEL:
            user_input[CONF_STOPS_AT] = None

        return self.async_create_entry(title="", data=user_input)
