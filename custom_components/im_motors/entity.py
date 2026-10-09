"""Vehicle identifiers use hashes; VIN and user-assigned names never enter HA."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class ImMotorsEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, vehicle_id, key):
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{vehicle_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, vehicle_id)},
                                            manufacturer="IM Motors", name="智己汽车")

    @property
    def vehicle(self):
        return next((v for v in self.coordinator.data.vehicles if v.identifier == self.vehicle_id), None)

    @property
    def available(self):
        return super().available and self.vehicle is not None
