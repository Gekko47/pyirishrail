"""Diagnostics support for the Irish Rail integration."""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_STATION,
    CONF_STATION_CODE,
    CONF_STOPS_AT,
    DOMAIN,
    GLOBAL_LAST_REBUILD_KEY,
)
from .health import get_health_monitor
from .types import IrishRailRuntimeData

# Only truly sensitive fields are fully redacted. Entry-level identifiers
# (title/unique_id) are kept useful for debugging by partial masking instead.
TO_REDACT = {CONF_STATION, CONF_STATION_CODE, CONF_STOPS_AT}

_MASK_PREFIX_LENGTH = 3
_MASK_HASH_LENGTH = 8


def _mask_identifier(value: str | None) -> str | None:
    """Partially mask an identifier, preserving debuggability.

    Keeps a short prefix plus a stable hash suffix so entries can still be
    correlated across diagnostics reports without exposing the full value.
    """
    if value is None:
        return None
    digest = hashlib.sha256(value.encode()).hexdigest()[:_MASK_HASH_LENGTH]
    prefix = value[:_MASK_PREFIX_LENGTH]
    return f"{prefix}...{digest}"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, redacting station identifiers."""
    data: IrishRailRuntimeData | None = getattr(entry, "runtime_data", None)

    coordinator_info: dict[str, Any] = {}
    if data is not None:
        coordinator = data.coordinator
        trains = coordinator.data or []
        coordinator_info = {
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_update_success": coordinator.last_update_success,
            "due_trains_count": len(trains),
        }

    monitor = get_health_monitor(hass)
    health_info = monitor.as_dict() if monitor is not None else None
    rebuild_result = hass.data.get(DOMAIN, {}).get(GLOBAL_LAST_REBUILD_KEY)
    rebuild_info = rebuild_result.as_dict() if rebuild_result is not None else None

    return {
        "entry": {
            # Identifiers are partially masked rather than hidden entirely so
            # diagnostics remain actionable.
            "title": _mask_identifier(entry.title),
            "unique_id": _mask_identifier(entry.unique_id),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            # Redact options through the same policy so any sensitive field
            # added there later is covered automatically.
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": coordinator_info,
        "api_health": health_info,
        "stops_matrix_rebuild": rebuild_info,
    }
