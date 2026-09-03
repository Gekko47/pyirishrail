"""DataUpdateCoordinator for the Irish Rail integration.

See docs/architecture.md §9 for adaptive backoff, the empty-data
repair issue, and downstream-stops learning.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ._runtime import get_health_monitor
from .client import IrishRailClient
from .const import (
    BACKOFF_MULTIPLIER,
    CONF_DIRECTION,
    CONF_SCAN_INTERVAL,
    CONF_STATION,
    CONF_STATION_CODE,
    CONF_STOPS_AT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DUBLIN_TZ,
    EMPTY_DATA_ISSUE_THRESHOLD,
    MAX_BACKOFF_INTERVAL,
    MAX_RETAINED_TRAINS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    SERVICE_HOURS_END_HOUR,
    SERVICE_HOURS_START_HOUR,
)
from .errors import IrishRailError
from .identity import build_unique_id
from .models import TrainDueTime
from .store import get_stops_store
from .types import IrishRailConfigEntry

_LOGGER = logging.getLogger(__name__)


def empty_data_issue_id(config_entry: IrishRailConfigEntry) -> str:
    """Return the repair-issue ID for an entry's persistent-empty-data state."""
    return f"empty_data_{config_entry.entry_id}"


def resolve_scan_interval(config_entry: IrishRailConfigEntry) -> timedelta:
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


def resolve_stops_at(config_entry: IrishRailConfigEntry) -> str | None:
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

    config_entry: IrishRailConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: IrishRailClient,
        config_entry: IrishRailConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.station_code = config_entry.data[CONF_STATION_CODE]
        self.station_name = config_entry.data[CONF_STATION]
        self.direction = config_entry.data.get(CONF_DIRECTION)

        # Snapshot of the entry data this coordinator was built from; used by
        # requires_reload() so the update listener can tell option-only
        # changes (applied live) apart from data/identity changes (reload).
        self._applied_entry_data = dict(config_entry.data)

        # Adaptive-backoff state. Initialized before super().__init__()
        # because the base class assigns update_interval, which flows
        # through the setter below.
        self._configured_interval = resolve_scan_interval(config_entry)
        self._failure_streak = 0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.station_code}",
            update_interval=self._configured_interval,
            config_entry=config_entry,
        )

        # Persistent-empty-data repair-issue tracking.
        self._empty_streak = 0
        self._empty_issue_reported = False

    @property
    def update_interval(self) -> timedelta:
        """Return the effective (possibly backed-off) polling interval.

        See docs/architecture.md §9 for the adaptive-backoff shape and
        why the configured interval applies whenever the last refresh
        succeeded.
        """
        if self._failure_streak == 0:
            return self._configured_interval
        # Iterative (not pow) because typeshed types int.__pow__ as Any.
        interval = self._configured_interval
        for _ in range(min(self._failure_streak, 16)):
            interval = min(interval * BACKOFF_MULTIPLIER, MAX_BACKOFF_INTERVAL)
        return interval

    @update_interval.setter
    def update_interval(self, value: timedelta) -> None:
        """Store a newly configured base interval (e.g. options updates).

        Delegates to the base-class setter so its internal scheduler
        bookkeeping stays in sync; the getter keeps deriving the
        effective backed-off interval from ``_configured_interval``.
        See docs/architecture.md §9.
        """
        self._configured_interval = value
        # ``fset`` is invisible to mypy's class-level property view.
        DataUpdateCoordinator.update_interval.fset(self, value)  # type: ignore[attr-defined]

    @property
    def failure_streak(self) -> int:
        """Number of consecutive failed refreshes driving the backoff."""
        return self._failure_streak

    @callback
    def _schedule_refresh(self) -> None:
        """Schedule a refresh using the effective (backed-off) interval.

        Mirrors the property into the base class' cached seconds value
        before delegating; see docs/architecture.md §9.
        """
        self._update_interval_seconds = self.update_interval.total_seconds()
        super()._schedule_refresh()

    def _register_refresh_failure(self) -> None:
        """Advance the consecutive-failure streak driving adaptive backoff."""
        self._failure_streak += 1
        _LOGGER.debug(
            "Station %s (%s) poll failed (%d consecutive); backing off to %s",
            self.station_name,
            self.station_code,
            self._failure_streak,
            self.update_interval,
        )

    def requires_reload(self) -> bool:
        """Return True when entry data changed since this coordinator loaded.

        See docs/architecture.md §8.
        """
        return self._applied_entry_data != dict(self.config_entry.data)

    def previous_unique_id(self) -> str | None:
        """Return the entry unique ID this coordinator was loaded for.

        Derived from the entry-data snapshot at construction time, so
        it identifies the pre-reconfigure identity even after
        ``config_entry.unique_id`` has been rewritten. See
        docs/architecture.md §8.
        """
        station_code = self._applied_entry_data.get(CONF_STATION_CODE)
        if not isinstance(station_code, str) or not station_code:
            return None
        return build_unique_id(
            station_code,
            self._applied_entry_data.get(CONF_DIRECTION),
        )

    def _in_service_hours(self) -> bool:
        """Return True when Dublin local time is within Irish Rail service hours."""
        # Evaluate against Irish civil time regardless of the host's configured
        # Home Assistant timezone; DUBLIN_TZ handles IST/GMT DST shifts.
        hour = dt_util.now(DUBLIN_TZ).hour
        return SERVICE_HOURS_START_HOUR <= hour < SERVICE_HOURS_END_HOUR

    def _health_monitor_is_healthy(self) -> bool:
        """Return True when a shared health probe recently succeeded.

        Unit-level coordinators built without full entry setup have no
        shared monitor; they keep the legacy conservative behaviour.
        """
        monitor = get_health_monitor(self.hass)
        return monitor is not None and monitor.recently_confirmed_healthy

    def _async_update_empty_data_issue(self, trains: list[TrainDueTime]) -> None:
        """Raise or clear the persistent-empty-data repair issue.

        See docs/architecture.md §9 for the full semantics (service-hours
        window, healthy-API suppression, registry-as-source-of-truth).
        """
        if trains:
            self._empty_streak = 0
            issue_id = empty_data_issue_id(self.config_entry)
            # The registry is authoritative: this coordinator may have been
            # reconstructed (entry reload/re-setup) after a previous instance
            # raised the issue, leaving ``_empty_issue_reported`` False while
            # the issue is still registered.
            had_issue = self._empty_issue_reported or (
                ir.async_get(self.hass).async_get_issue(DOMAIN, issue_id)
                is not None
            )
            if had_issue:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                self._empty_issue_reported = False
                _LOGGER.info(
                    "Station %s (%s) is reporting train data again",
                    self.station_name,
                    self.station_code,
                )
            return

        # A confirmed-recently-healthy API means the empty result reflects
        # scheduling reality (nothing due inside the look-ahead window),
        # not an integration or schema problem: suppress the repair issue,
        # clear any stale one right away, and reset the streak.
        if self._health_monitor_is_healthy():
            self._empty_streak = 0
            self._empty_issue_reported = False
            issue_id = empty_data_issue_id(self.config_entry)
            if (
                ir.async_get(self.hass).async_get_issue(DOMAIN, issue_id)
                is not None
            ):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                _LOGGER.info(
                    "Station %s (%s) has no trains because the Irish Rail "
                    "API reports no scheduled services in the look-ahead "
                    "window; cleared the persistent-empty-data issue",
                    self.station_name,
                    self.station_code,
                )
            _LOGGER.debug(
                "Station %s (%s) returned no trains while the API is "
                "healthy: nothing scheduled in the look-ahead window",
                self.station_name,
                self.station_code,
            )
            return

        if not self._in_service_hours():
            # Empty results outside service hours are a normal overnight
            # quiet period: they neither advance nor carry over the streak,
            # so only consecutive service-hour polls can reach the
            # repair-issue threshold.
            self._empty_streak = 0
            return

        self._empty_streak += 1
        if (
            not self._empty_issue_reported
            and self._empty_streak >= EMPTY_DATA_ISSUE_THRESHOLD
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
                # Direct users to the README's "Known Limitations" section
                # so they can confirm whether the symptom is a real outage
                # or an expected quiet period; the issue is raised only
                # during service hours so this anchor is the relevant
                # destination.
                learn_more_url=(
                    "https://github.com/Gekko47/pyirishrail/blob/master/"
                    "README.md#known-limitations"
                ),
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
            self._register_refresh_failure()
            raise UpdateFailed(
                f"Error updating Irish Rail station data: {err}"
            ) from err
        except Exception:
            # Unexpected failures count toward backoff too, mirroring the
            # base coordinator's treatment of any exception as a failed
            # refresh; the exception itself propagates unchanged.
            self._register_refresh_failure()
            raise

        if self._failure_streak:
            _LOGGER.info(
                "Station %s (%s) polling restored after %d consecutive "
                "failed poll(s); interval back to %s",
                self.station_name,
                self.station_code,
                self._failure_streak,
                self._configured_interval,
            )
            self._failure_streak = 0

        # Keep only the next two trains: the devices show the next train due
        # and the following train due. The API's look-ahead window can return
        # many services; only the retained slice is exposed via the coordinator.
        trains = trains[:MAX_RETAINED_TRAINS]

        self._async_update_empty_data_issue(trains)
        await self._async_learn_downstream_stops()
        return trains

    async def _async_learn_downstream_stops(self) -> None:
        """Merge stops observed this poll into the persistent stops matrix.

        See docs/architecture.md §9 (downstream-stops learning) and §10
        (stops-matrix store).
        """
        if resolve_stops_at(self.config_entry) is None:
            return
        # Only polls that actually pruned candidates carry observations; an
        # empty due-list has nothing new to learn either way.
        downstream = self.client.last_downstream_stop_names
        if not downstream:
            _LOGGER.debug(
                "No downstream stops observed for %s (%s) this poll",
                self.station_name,
                self.station_code,
            )
            return
        try:
            changed = await get_stops_store(self.hass).async_record(
                self.station_code,
                self.direction,
                sorted(downstream),
            )
        except Exception:
            # Deliberate broad guard: any storage failure must degrade to
            # "matrix not updated", never to a failed coordinator refresh.
            _LOGGER.warning(
                "Could not persist observed stops for %s (%s)",
                self.station_name,
                self.station_code,
                exc_info=True,
            )
            return
        if changed:
            _LOGGER.debug(
                "Stops matrix updated for %s (%s, direction=%s)",
                self.station_name,
                self.station_code,
                self.direction,
            )
