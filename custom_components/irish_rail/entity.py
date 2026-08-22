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
        # Build stable unique ID from unique_id of the config entry
        self._attr_unique_id = (
            f"{coordinator.config_entry.unique_id}_{entity_key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.station_code)},
            name=coordinator.station_name,
            manufacturer="Iarnród Éireann / Irish Rail",
            entry_type=DeviceEntryType.SERVICE,
        )
