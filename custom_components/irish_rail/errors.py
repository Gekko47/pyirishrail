"""Typed exception hierarchy for the Irish Rail RTPI async client.

Catch :class:`IrishRailError` for every API failure. See
docs/architecture.md §4 for the framework-agnostic contract.
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
