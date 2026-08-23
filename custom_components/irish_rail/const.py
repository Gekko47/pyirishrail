"""Constants for the Irish Rail integration."""

from datetime import timedelta
import logging

import aiohttp

DOMAIN = "irish_rail"

_LOGGER = logging.getLogger(__package__)

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
# Gold rule ``repair-issues``). Irish Rail services run roughly 06:00-23:30;
# an empty response outside these hours is a normal overnight quiet period,
# while a persistent empty result during service hours suggests an API or
# schema change worth surfacing to the user.
SERVICE_HOURS_START_HOUR = 6
SERVICE_HOURS_END_HOUR = 23

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
