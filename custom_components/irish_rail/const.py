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
