"""Diagnostics support for the Irish Rail integration."""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_STATION,
    CONF_STATION_CODE,
    CONF_STATION_FILTER,
    DOMAIN,
    GLOBAL_LAST_REBUILD_KEY,
)
from .health import get_health_monitor
from .types import IrishRailConfigEntry, IrishRailRuntimeData

# Sensitive fields are partially masked (not fully redacted) so maintainers
# can still tell what a user's setup looks like without exposing the full
# station name. Non-sensitive configuration choices (scan interval, num
# trains, direction, stops-at filter) are kept as-is because they are
# never personally identifying.
_SENSITIVE_KEYS = frozenset({CONF_STATION, CONF_STATION_CODE, CONF_STATION_FILTER})

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


def _project_entry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a diagnostics-friendly view of entry ``data`` or ``options``.

    Sensitive identifiers are partially masked, non-sensitive configuration
    choices are kept as-is. ``None`` values and unknown keys are passed
    through unchanged so newly-added options surface in the report without
    a code change.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SENSITIVE_KEYS and isinstance(value, str):
            out[key] = _mask_identifier(value)
        else:
            out[key] = value
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: IrishRailConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, redacting station identifiers."""
    data: IrishRailRuntimeData | None = getattr(entry, "runtime_data", None)

    coordinator_info: dict[str, Any] = {}
    if data is not None:
        coordinator = data.coordinator
        trains = coordinator.data or []
        # The DataUpdateCoordinator base class exposes ``last_exception``
        # (the most recent UpdateFailed reason) and ``last_update_success``
        # (a bool). Capture both plus the configured scan interval, the
        # failure-streak driving adaptive backoff, the effective backed-off
        # interval, and whether the coordinator has ever produced data —
        # a maintainer reading a "sensor stuck on unknown" report can
        # finally tell whether the API is failing or the coordinator is
        # wedged.
        coordinator_info = {
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                str(coordinator.last_exception)
                if coordinator.last_exception
                else None
            ),
            "failure_streak": coordinator.failure_streak,
            "due_trains_count": len(trains),
            "data_available": coordinator.data is not None,
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
            "data": _project_entry_payload(dict(entry.data)),
            # Non-sensitive options pass through unchanged; sensitive
            # identifiers follow the same masking policy as ``data``.
            "options": _project_entry_payload(dict(entry.options)),
        },
        "coordinator": coordinator_info,
        "api_health": health_info,
        "stops_matrix_rebuild": rebuild_info,
    }
