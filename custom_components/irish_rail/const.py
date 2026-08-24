"""Constants for the Irish Rail integration."""

from datetime import timedelta

import aiohttp

DOMAIN = "irish_rail"

# Configuration keys
CONF_STATION = "station"
CONF_STATION_CODE = "station_code"
CONF_DIRECTION = "direction"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_NUM_TRAINS = "num_trains"
CONF_STOPS_AT = "stops_at"

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
# 24 means "through the end of the day": dt_util.now().hour never reaches 24,
# so the half-open check keeps the gate open for all of 06:00-23:59 without
# ever wrapping into the early-morning quiet period.
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
