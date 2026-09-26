"""Base entity class for the Irish Rail integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IrishRailDataUpdateCoordinator


class IrishRailEntity(CoordinatorEntity[IrishRailDataUpdateCoordinator]):
    """Common base for Irish Rail entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: IrishRailDataUpdateCoordinator, entity_key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_key = entity_key
        # Validated before use: the unique ID is an ``assert``-free
        # invariant, and reading a missing one would silently produce
        # "None_<key>" plus a (DOMAIN, None) device identifier.
        entry_unique_id = coordinator.config_entry.unique_id
        if not entry_unique_id:
            raise ValueError(
                "Irish Rail config entry has no unique_id; "
                "reload the entry to re-establish its identity"
            )
        self._attr_unique_id = f"{entry_unique_id}_{entity_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_unique_id)},
            name=coordinator.station_name,
            manufacturer="Iarnród Éireann / Irish Rail",
            model="Irish Rail RTPI",
            configuration_url="https://api.irishrail.ie",
            entry_type=DeviceEntryType.SERVICE,
        )
