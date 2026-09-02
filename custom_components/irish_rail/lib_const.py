"""Library-only constants for the Irish Rail RTPI async client.

No Home Assistant dependency. See ``const.py`` for integration-level
constants; this module holds client-only defaults.
"""

from __future__ import annotations

import aiohttp

API_BASE_URL: str = "https://api.irishrail.ie/realtime/realtime.asmx/"

STATION_TYPE_TO_CODE_DICT: dict[str, str] = {
    "mainline": "M",
    "suburban": "S",
    "dart": "D",
}

DEFAULT_TIMEOUT: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=10)

# Per-client cache of train movement histories keyed by
# ``(train_code, date)``. Failed lookups are never cached; other-date
# entries are evicted lazily once the cap is exceeded.
MOVEMENT_CACHE_MAX_ENTRIES: int = 1024
