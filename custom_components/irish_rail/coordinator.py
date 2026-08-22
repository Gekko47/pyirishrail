"""DataUpdateCoordinator for the Irish Rail integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IrishRailClient, IrishRailError, TrainDueTime
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


def resolve_scan_interval(config_entry: ConfigEntry) -> timedelta:
    """Return the configured polling interval for an entry.

    Reads ``entry.options`` first (set via the options flow), falling back to
    the default of 60 seconds.
    """
    raw = config_entry.options.get(
        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds()
    )
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        seconds = int(DEFAULT_SCAN_INTERVAL.total_seconds())
    seconds = max(
        MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, seconds)
    )
    return timedelta(seconds=seconds)


def resolve_num_trains(config_entry: ConfigEntry) -> int:
    """Return the number of upcoming trains to expose for an entry.

    Precedence: ``entry.options`` (options flow) → ``entry.data``
    (initial setup) → default of 3. Values are clamped to 1-5.
    """
    raw = config_entry.options.get(
        CONF_NUM_TRAINS,
        config_entry.data.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS),
    )
    try:
        num = int(raw)
    except (TypeError, ValueError):
        num = DEFAULT_NUM_TRAINS
    return max(MIN_NUM_TRAINS, min(MAX_NUM_TRAINS, num))


def resolve_stops_at(config_entry: ConfigEntry) -> str | None:
    """Return the "only show trains stopping at" filter for an entry.

    Precedence: ``entry.options`` (options flow) → ``entry.data``
    (initial setup) → no filter (``None``). The sentinel ``"All"`` and
    blank values are treated as no filter.
    """
    raw = config_entry.options.get(
        CONF_STOPS_AT,
        config_entry.data.get(CONF_STOPS_AT),
    )
    if not raw or raw == "All":
        return None
    return str(raw)


class IrishRailDataUpdateCoordinator(DataUpdateCoordinator[list[TrainDueTime]]):
    """Class to manage fetching Irish Rail station data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: IrishRailClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.station_code = config_entry.data[CONF_STATION_CODE]
        self.station_name = config_entry.data[CONF_STATION]
        self.direction = config_entry.data.get(CONF_DIRECTION)
        self.stops_at = resolve_stops_at(config_entry)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.station_code}",
            update_interval=resolve_scan_interval(config_entry),
            config_entry=config_entry,
        )

    async def _async_update_data(self) -> list[TrainDueTime]:
        """Fetch real-time due train data from Irish Rail."""
        try:
            return await self.client.async_get_station_by_code(
                self.station_code,
                direction=self.direction,
                stops_at=resolve_stops_at(self.config_entry),
            )
        except IrishRailError as err:
            raise UpdateFailed(
                f"Error updating Irish Rail station data: {err}"
            ) from err
