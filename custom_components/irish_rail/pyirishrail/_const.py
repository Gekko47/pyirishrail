"""Library-only constants for the Irish Rail RTPI async client.

The constants in this module are owned by the :mod:`pyirishrail` package
itself (no Home Assistant dependency). Integration-level constants
(``CONF_*``, ``DOMAIN``, options-flow bounds, service-hour gate, etc.)
remain in :mod:`custom_components.irish_rail.const` because they are
specific to how the Home Assistant integration is wired and would mean
nothing to a non-HA consumer of the library.

The ``_const`` module name is private to the package — the public
``pyirishrail`` namespace re-exports only the four public exception
classes and the four public dataclasses from :mod:`.models`. The
constants here are implementation details of :class:`pyirishrail.api.IrishRailClient`
and should not be imported by external consumers; they are kept in a
``_const`` module (rather than baked into ``api.py``) only so
configuration overrides can target them in tests.
"""

from __future__ import annotations

import aiohttp

#: Base URL for every Irish Rail RTPI endpoint.
API_BASE_URL: str = "https://api.irishrail.ie/realtime/realtime.asmx/"

#: Mapping from the user-facing ``stationType`` query value to the
#: short code the API expects.
STATION_TYPE_TO_CODE_DICT: dict[str, str] = {
    "mainline": "M",
    "suburban": "S",
    "dart": "D",
}

#: HTTP timeout for a single Irish Rail API request. The RTPI endpoints
#: are lightweight XML documents; 10 seconds is generous enough for slow
#: mobile connections while ensuring the event loop never waits
#: indefinitely on a hung connection.
DEFAULT_TIMEOUT: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=10)

#: Per-client cache of train movement histories keyed by
#: ``(train_code, date)``. A running train's stop list only grows during
#: its journey, so caching per date is safe for "does this train stop at
#: X?" filtering; failed lookups are never cached. Entries for other
#: dates are evicted lazily once the cap is exceeded.
MOVEMENT_CACHE_MAX_ENTRIES: int = 1024
