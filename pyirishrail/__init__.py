"""Async client library for the Irish Rail RTPI API.

This is the published Python package form of the original
``custom_components/irish_rail/api.py`` — the in-repo sibling layout
chosen for the Platinum ``async-dependency`` / ``strict-typing`` /
PEP-561 requirements (roadmap Phase 5.3, revised 2026-08-27). The
client is fully framework-agnostic; the Home Assistant integration in
``custom_components/irish_rail/`` re-consumes it as an external
dependency declared in its ``manifest.json``.

Public surface
--------------

Importing the package gives you the four public exception classes and
the four public dataclasses::

    from pyirishrail import (
        IrishRailClient,            # the async client
        IrishRailError,             # base exception
        IrishRailConnectionError,   # network failures
        IrishRailTimeoutError,      # aiohttp timeouts
        IrishRailParseError,        # XML/security errors
        Station, TrainDueTime, TrainMovement, TrainPosition,
        parse_station_data,         # pure parser helper
    )

The pure XML helper ``parse_station_data`` is re-exported here because
it is used by external code (notably the integration's ``matrix_rebuild``
button). Private helpers in :mod:`pyirishrail.api` (prefixed with
``_``) are deliberately not re-exported; cross-package consumers that
genuinely need them should import from the submodule explicitly
(``from pyirishrail.api import _scoped_journey_stops``).

Defusedxml / async-dependency justification
-------------------------------------------

The Platinum ``async-dependency`` rule states that every declared
dependency in the integration's ``manifest.json`` must be async, and
the rule has no exceptions clause. ``defusedxml`` is a *pure XML
parser*; it never opens a socket and never blocks. The client
retrieves the response body via ``await session.get(...)`` and
``await response.text()`` on an injected ``aiohttp.ClientSession``
(``inject-websession``), then passes the already-fetched ``str`` to
``defusedxml.ElementTree.fromstring(...)``. The dependency is therefore
async at the transport boundary and only the parser — which is
deliberately synchronous and runs in microseconds on small XML
documents — sees the bytes. This is the same pattern the Home
Assistant core codebase uses for its own XML integrations.

Type checking
-------------

This package ships ``py.typed`` (PEP 561) and is built with
``pyproject.toml`` configured for ``setuptools`` packages. Strict mypy
passes clean on the published surface.
"""

from __future__ import annotations

from .api import (
    DEFAULT_TIMEOUT,
    IrishRailClient,
    parse_station_data,
)
from .errors import (
    IrishRailConnectionError,
    IrishRailError,
    IrishRailParseError,
    IrishRailTimeoutError,
)
from .models import (
    Station,
    TrainDueTime,
    TrainMovement,
    TrainPosition,
)

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_TIMEOUT",
    "IrishRailClient",
    "IrishRailConnectionError",
    "IrishRailError",
    "IrishRailParseError",
    "IrishRailTimeoutError",
    "Station",
    "TrainDueTime",
    "TrainMovement",
    "TrainPosition",
    "__version__",
    "parse_station_data",
]
