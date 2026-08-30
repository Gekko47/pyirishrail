"""Async client library for the Irish Rail RTPI API.

Bundled inside the ``irish_rail`` Home Assistant custom integration
at ``custom_components/irish_rail/pyirishrail/``. The client is fully
framework-agnostic — it does not import Home Assistant and could be
consumed by non-HA code, but the in-tree location keeps the HACS
install self-contained (the ``pyirishrail`` name on PyPI is owned by
an unrelated project, so the package is not published; see
``pyirishrail/README.md`` for the full rationale).

Public surface
--------------

Importing the package gives you the client, the gate, the four public
exception classes, the four public dataclasses, and the pure parser
helper::

    from custom_components.irish_rail.pyirishrail import (
        IrishRailClient,            # the async client
        RequestGate,                # concurrency-and-pacing gate
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
(``from custom_components.irish_rail.pyirishrail.api import
_scoped_journey_stops``).

Internal package: framework-agnostic async client for the Irish Rail RTPI
API. Importing the package gives you the client, the gate, the four public
exception classes, the four public dataclasses, and the pure parser
helper::

    from custom_components.irish_rail.pyirishrail import (
        IrishRailClient,            # the async client
        RequestGate,                # concurrency-and-pacing gate
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
(``from custom_components.irish_rail.pyirishrail.api import
_scoped_journey_stops``).

Zero-dep XML parsing
--------------------

The client uses Python's standard library
``xml.etree.ElementTree`` for XML parsing — there is no third-party
dependency. On the Home Assistant 2026.8 floor (Python 3.14.2's bundled
expat 2.7.5) stdlib ET already rejects entity declarations and
external-entity resolution with ``ParseError``; the only gap it leaves
open is silently allowing a DTD *without* entities, which is harmless
by itself but weakens the policy to be version-dependent on the bundled
expat. The client closes that gap with an explicit pre-parse guard
(:data:`pyirishrail.api._DTD_DECL_RE`) that rejects ``<!doctype``,
``<!entity``, ``<!element``, ``<!attlist`` and ``<!notation`` before the
parser is invoked, raising :class:`IrishRailParseError` on match. The
guard runs in microseconds on the small XML documents the RTPI
endpoints return, keeps the integration's manifest requirements
intentionally empty, and makes the policy independent of whichever
expat version ships with the target Python.

The pre-parse guard is the integration's only XML policy surface; every
``IrishRailClient._request`` path goes through it, so the rebuild
button, the config flow's live discovery and the script
``scripts/build_stops_matrix.py`` inherit the hardening automatically.

Shared ``RequestGate``
----------------------

The integration passes one :class:`RequestGate` per
``HomeAssistant`` instance to every client it creates (see
``custom_components/irish_rail/gate.py``). The gate is the single
admission point for every outbound request a client makes, so all
requests — live polling, config-flow lookups, stops-matrix rebuild,
health probe — draw from one shared rate budget against the public
``api.irishrail.ie`` endpoints.

Type checking
-------------

This package ships ``py.typed`` (PEP 561). Strict mypy passes clean
on the bundled surface via
``mypy custom_components/irish_rail tests/components/irish_rail``.
"""

from __future__ import annotations

from ._gate import RequestGate
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
    "RequestGate",
    "Station",
    "TrainDueTime",
    "TrainMovement",
    "TrainPosition",
    "__version__",
    "parse_station_data",
]
