"""Exception hierarchy for the Irish Rail RTPI async client.

This module is the single source of truth for typed exceptions raised by
:mod:`pyirishrail`. Consumers should ``from pyirishrail import IrishRailError``
(imported via :mod:`pyirishrail.__init__`) and catch the base class — the
subclasses exist purely so callers can distinguish failure modes
(connection, timeout, parse) without resorting to string matching.

The hierarchy never returns an empty list merely because the server
failed: every API failure path raises one of these subclasses, so the
integration layer can map it to a typed ``UpdateFailed`` and surface a
clear "sensor unavailable" state in the Home Assistant UI. See
docs/architecture.md §16 for the framework-agnostic contract that
underpins this design.
"""

from __future__ import annotations


class IrishRailError(Exception):
    """Base exception for Irish Rail API errors."""


class IrishRailConnectionError(IrishRailError):
    """Exception to indicate a connection error."""


class IrishRailTimeoutError(IrishRailError):
    """Exception to indicate an API timeout."""


class IrishRailParseError(IrishRailError):
    """Exception to indicate an XML parsing error."""


__all__ = [
    "IrishRailConnectionError",
    "IrishRailError",
    "IrishRailParseError",
    "IrishRailTimeoutError",
]
