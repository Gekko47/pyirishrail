"""Diagnostics support for the Irish Rail integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_STATION, CONF_STATION_CODE
from .types import IrishRailRuntimeData

TO_REDACT = {CONF_STATION, CONF_STATION_CODE}


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

    return {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "coordinator": coordinator_info,
    }
