"""Stable config-entry identity helpers.

``build_unique_id`` combines the API-assigned station code with the
normalized direction filter into the stable unique ID shared by the config
entry, its coordinator, and its entities. The helpers live in their own
module — not ``config_flow`` — so the coordinator can snapshot entry
identities without importing the config flow (the ``common-modules``
layering rule: shared logic belongs in a neutral module).
"""

from __future__ import annotations

__all__ = ["build_unique_id", "normalized_direction"]


def normalized_direction(direction: str | None) -> str:
    """Return the canonical unique-ID component for a direction filter.

    The "All" filter is stored as ``None`` in entry data but must still be
    part of the unique ID; it maps to the literal ``all``. Every other
    direction is lowercased so the identity never depends on display casing.
    """
    return (direction or "all").lower()


def build_unique_id(station_code: str, direction: str | None) -> str:
    """Build the stable unique ID for a station/direction combination."""
    return f"{station_code}_{normalized_direction(direction)}"
