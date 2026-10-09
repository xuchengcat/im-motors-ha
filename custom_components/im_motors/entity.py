"""Vehicle identifiers use hashes; VIN and user-assigned names never enter HA."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .telemetry import normalized, observed, raw, timestamp
from datetime import datetime, timezone


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


class ImMotorsTelemetryEntity(ImMotorsEntity):
    """Only normalized approved fields enter state; identity stays in the SDK."""

    def __init__(self, coordinator, vehicle_id, key, spec):
        super().__init__(coordinator, vehicle_id, key)
        self.spec = spec
        self._attr_name = spec.name
        self._attr_device_class = spec.device_class
        self._attr_entity_registry_enabled_default = not spec.diagnostic or key in (
            "vehicle_updated", "vehicle_series", "tab_project_code")
        if spec.diagnostic:
            from homeassistant.const import EntityCategory
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def snapshot(self):
        return self.coordinator.data.telemetry.get(self.vehicle_id)

    @property
    def mapped_value(self):
        return normalized(self.snapshot, self.spec)

    @property
    def available(self):
        return super().available and self.snapshot is not None

    @property
    def extra_state_attributes(self):
        item = observed(self.snapshot, self.spec.group, self.spec.field)
        updated = timestamp(raw(self.snapshot, "root", "updateTime"))
        retrieved = timestamp(self.coordinator.data.telemetry_time_ms)
        return {"source_field": f"{self.spec.group}.{self.spec.field}",
                "source_presence": item.presence.value if item else "missing",
                "cloud_updated": updated.isoformat() if updated else None,
                "retrieved_at": retrieved.isoformat() if retrieved else None,
                "cloud_snapshot_age_seconds": round((datetime.now(timezone.utc) - updated).total_seconds())
                    if updated and updated <= datetime.now(timezone.utc) else None,
                "individual_sample_time_verified": False}
