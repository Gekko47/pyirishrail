"""DataUpdateCoordinator for the Irish Rail integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IrishRailClient, IrishRailError, TrainDueTime
from .const import (
    CONF_DIRECTION,
    CONF_STATION,
    CONF_STATION_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


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

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.station_code}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=config_entry,
        )

    async def _async_update_data(self) -> list[TrainDueTime]:
        """Fetch real-time due train data from Irish Rail."""
        try:
            return await self.client.async_get_station_by_code(
                self.station_code, direction=self.direction
            )
        except IrishRailError as err:
            raise UpdateFailed(
                f"Error updating Irish Rail station data: {err}"
            ) from err
