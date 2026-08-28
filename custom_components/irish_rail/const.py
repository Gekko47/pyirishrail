"""Constants for the Irish Rail integration."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import aiohttp

DOMAIN = "irish_rail"

# Configuration keys
CONF_STATION = "station"
CONF_STATION_CODE = "station_code"
CONF_STATION_FILTER = "station_filter"
CONF_DIRECTION = "direction"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NUM_TRAINS = "num_trains"
CONF_STOPS_AT = "stops_at"
CONF_ENABLE_DIRECTION_FILTER = "enable_direction_filter"
CONF_ENABLE_STOPS_AT_FILTER = "enable_stops_at_filter"

# Bounds for the user-configurable polling interval (roadmap 1.2).
MIN_SCAN_INTERVAL_SECONDS = 30
MAX_SCAN_INTERVAL_SECONDS = 600

# Number of upcoming trains exposed via the ``upcoming_trains`` attribute
# (roadmap 1.3). Configurable at setup and changeable later via options.
DEFAULT_NUM_TRAINS = 3
MIN_NUM_TRAINS = 1
MAX_NUM_TRAINS = 5

# Service hours for the persistent-empty-data repair issue (roadmap Phase 3,
# Gold rule ``repair-issues``). Irish Rail services run until around
# midnight, so the gate below stays open through every evening hour; empty
# responses between 00:00 and 06:00 are a normal overnight quiet period,
# while a persistent empty result during service hours suggests an API or
# schema change worth surfacing to the user.
SERVICE_HOURS_START_HOUR = 6
# 24 means "through the end of the day": a local (Europe/Dublin) hour never
# reaches 24, so the half-open check keeps the gate open for all of 06:00-23:59
# without ever wrapping into the early-morning quiet period.
SERVICE_HOURS_END_HOUR = 24

# Consecutive successful-but-empty polls required before the repair issue is
# raised (about 10 minutes at the default 60-second polling interval).
EMPTY_DATA_ISSUE_THRESHOLD = 10

# HTTP timeout for a single Irish Rail API request.
# The RTPI endpoints are lightweight XML documents; 10 seconds is generous
# enough for slow mobile connections while ensuring the event loop never
# waits indefinitely on a hung connection.
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Scan interval.
# Real-time train updates generally change roughly once a minute server-side
# on Irish Rail. A 60-second scan interval is appropriate: it prevents
# overloading the public plain-HTTP/HTTPS API while ensuring the real-time
# passenger information (RTPI) in Home Assistant remains fresh.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=1)

# Adaptive backoff polling (roadmap 4.3). On consecutive failed refreshes
# the effective polling interval grows geometrically from the user-configured
# interval, capped at MAX_BACKOFF_INTERVAL; any successful refresh restores
# the configured interval immediately. The cap deliberately exceeds the
# normal 600 s maximum so a downed public API is not hammered.
BACKOFF_MULTIPLIER = 2
MAX_BACKOFF_INTERVAL = timedelta(minutes=15)

# "stops_at" pruning hardening. Movement-history lookups for candidate trains
# are issued concurrently, bounded by a small semaphore to stay polite to the
# public API.
MAX_CONCURRENT_MOVEMENT_LOOKUPS = 5

# Per-client cache of train movement histories keyed by
# ``(train_code, date)``. A running train's stop list only grows during its
# journey, so caching per date is safe for "does this train stop at X?"
# filtering; failed lookups are never cached. Entries for other dates are
# evicted lazily once the cap is exceeded.
MOVEMENT_CACHE_MAX_ENTRIES = 1024

# "Stops at" option discovery (roadmap 4.8). Live sampling of due trains is
# the source of truth; its results are persisted per install in a versioned
# Store file and layered over a bundled seed matrix so the config flow can
# still offer valid options when no services are currently due (e.g.
# overnight).
#
# Two distinct files now exist:
# * ``stops_matrix.seed.json`` ships inside the integration folder (read-
#   only; the HACS update overwrites it with the upstream bundled seed).
# * ``stops_matrix.json`` is the per-install runtime file the rebuild
#   button writes into ``hass.config.path()``; it is gitignored and
#   survives HACS updates, so a user-triggered rebuild is never silently
#   clobbered when the integration is updated.
STOPS_MATRIX_SEED_FILENAME = "stops_matrix.seed.json"
STOPS_MATRIX_FILENAME = "stops_matrix.json"

# ── Shared API-health infrastructure ────────────────────────────────────────
# The connectivity binary_sensor and the stops-matrix rebuild button are
# registered as integration-level service entities (no device, with
# EntityCategory.DIAGNOSTIC / CONFIG respectively) so the per-station devices
# never have to carry them. The first loaded config entry "claims" providership
# (see health.py) for the lifetime of the Home Assistant session; if that entry
# is unloaded the globals disappear with it until reload/restart rather than
# fighting over entity-registry ownership mid-session.

# Fixed unique IDs (not derived from any config entry unique_id) so registry
# identity survives reloads and ownership changes alike.
GLOBAL_HEALTH_UNIQUE_ID = "irish_rail_api_connectivity"
GLOBAL_REBUILD_UNIQUE_ID = "irish_rail_rebuild_stops_matrix"

# How often the reachability probe fires. Independent from station polling
# because its job is distinguishing "the whole API is down" from "this
# station has nothing scheduled in the RTPI look-ahead window".
HEALTH_CHECK_INTERVAL = timedelta(minutes=5)

# Station code used by the connectivity probe. Probing one lightweight
# single-station poll (a small XML document of its due trains) is far
# cheaper than fetching the whole ~155-record station list every interval
# and is every bit as conclusive for "did the API answer?". Dublin Pearse
# is a permanent major terminus, and the probe treats any successful
# response -- even one with no trains currently due -- as healthy, so it
# never depends on a service actually being scheduled.
HEALTH_PROBE_STATION_CODE = "PEARS"

# Pause between station samples during a stops-matrix rebuild, mirroring
# scripts/build_stops_matrix.py's polite pacing against the public API.
REBUILD_DELAY_SECONDS = 0.3

# Keys under ``hass.data[DOMAIN]`` for the shared-global runtime objects.
HEALTH_MONITOR_INSTANCE = "api_health_monitor"
GLOBAL_PROVIDER_KEY = "global_provider_entry_id"
# Stores the most recent RebuildResult dict (from button.py) so diagnostics
# can report the last stops-matrix rebuild outcome without importing it.
GLOBAL_LAST_REBUILD_KEY = "global_last_result"

# Irish civil-time zone shared by service-hours gating and rebuild dating;
# host-installed Home Assistant instances abroad must still follow Dublin.
DUBLIN_TZ = ZoneInfo("Europe/Dublin")
