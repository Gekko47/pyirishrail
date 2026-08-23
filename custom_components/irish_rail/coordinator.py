"""DataUpdateCoordinator for the Irish Rail integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
    EMPTY_DATA_ISSUE_THRESHOLD,
    MAX_NUM_TRAINS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_NUM_TRAINS,
    MIN_SCAN_INTERVAL_SECONDS,
    SERVICE_HOURS_END_HOUR,
    SERVICE_HOURS_START_HOUR,
)

_LOGGER = logging.getLogger(__name__)


def empty_data_issue_id(config_entry: ConfigEntry) -> str:
    """Return the repair-issue ID for an entry's persistent-empty-data state."""
    return f"empty_data_{config_entry.entry_id}"


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

        # Persistent-empty-data repair-issue tracking (roadmap Phase 3).
        self._empty_streak = 0
        self._empty_issue_reported = False

    def _in_service_hours(self) -> bool:
        """Return True when the local time is within Irish Rail service hours."""
        hour = dt_util.now().hour
        return SERVICE_HOURS_START_HOUR <= hour < SERVICE_HOURS_END_HOUR

    def _async_update_empty_data_issue(self, trains: list[TrainDueTime]) -> None:
        """Raise or clear the persistent-empty-data repair issue.

        A station that keeps returning an empty list during service hours
        suggests the API or its schema changed rather than a genuine quiet
        period. The issue is created exactly once per streak and removed on
        the first refresh that returns actual trains (Gold rule
        ``repair-issues``; roadmap Phase 3).
        """
        if trains:
            self._empty_streak = 0
            if self._empty_issue_reported:
                ir.async_delete_issue(
                    self.hass, DOMAIN, empty_data_issue_id(self.config_entry)
                )
                self._empty_issue_reported = False
                _LOGGER.info(
                    "Station %s (%s) is reporting train data again",
                    self.station_name,
                    self.station_code,
                )
            return

        self._empty_streak += 1
        if (
            not self._empty_issue_reported
            and self._empty_streak >= EMPTY_DATA_ISSUE_THRESHOLD
            and self._in_service_hours()
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                empty_data_issue_id(self.config_entry),
                is_fixable=False,
                issue_domain=DOMAIN,
                severity=ir.IssueSeverity.WARNING,
                translation_key="empty_data_during_service_hours",
                translation_placeholders={"station": self.station_name},
            )
            self._empty_issue_reported = True
            _LOGGER.warning(
                "Station %s (%s) returned no trains for %d consecutive "
                "polls during service hours",
                self.station_name,
                self.station_code,
                self._empty_streak,
            )

    async def _async_update_data(self) -> list[TrainDueTime]:
        """Fetch real-time due train data from Irish Rail."""
        try:
            trains = await self.client.async_get_station_by_code(
                self.station_code,
                direction=self.direction,
                stops_at=resolve_stops_at(self.config_entry),
            )
        except IrishRailError as err:
            raise UpdateFailed(
                f"Error updating Irish Rail station data: {err}"
            ) from err

        self._async_update_empty_data_issue(trains)
        return trains
