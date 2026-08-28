"""Base entity class for the Irish Rail integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IrishRailDataUpdateCoordinator


class IrishRailEntity(CoordinatorEntity[IrishRailDataUpdateCoordinator]):
    """Common base for Irish Rail entities.

    The per-station ``DeviceInfo`` deliberately keeps ``name`` to just the
    station (without the direction suffix). HA renders the config-entry
    title (``"Dublin Pearse (Northbound)"``) next to the device, so adding
    the suffix to the device name duplicates text in the UI. ``model``,
    ``sw_version`` and ``configuration_url`` are populated so the device
    card has something to display and the manufacturer link works.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: IrishRailDataUpdateCoordinator, entity_key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_key = entity_key
        # Build stable unique ID from unique_id of the config entry
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{entity_key}"
        # The config flow always sets a unique ID for entries of this domain.
        assert coordinator.config_entry.unique_id is not None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.unique_id)},
            name=coordinator.station_name,
            manufacturer="Iarnród Éireann / Irish Rail",
            model="Irish Rail RTPI",
            configuration_url="https://api.irishrail.ie",
            entry_type=DeviceEntryType.SERVICE,
        )
