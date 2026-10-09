"""Verified account metadata and explicit pending telemetry placeholders."""
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .const import PENDING_FIELDS
from .entity import ImMotorsEntity

PARALLEL_UPDATES = 0
DESCRIPTIONS = {
    "metadata_updated": ("元数据更新时间", SensorDeviceClass.TIMESTAMP),
    "token_expires": ("账号凭据到期时间", SensorDeviceClass.TIMESTAMP),
    "token_refresh": ("建议刷新时间", SensorDeviceClass.TIMESTAMP),
    "project_code": ("车型项目代码", None),
    "telemetry_status": ("车况解析状态", None),
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_vehicles():
        entities = []
        for vehicle in coordinator.data.vehicles:
            if vehicle.identifier in known:
                continue
            known.add(vehicle.identifier)
            entities.extend(ImMotorsSensor(coordinator, vehicle.identifier, key) for key in DESCRIPTIONS)
            entities.extend(PendingSensor(coordinator, vehicle.identifier, name, index)
                            for index, name in enumerate(PENDING_FIELDS))
        if entities:
            async_add_entities(entities)

    add_vehicles()
    entry.async_on_unload(coordinator.async_add_listener(add_vehicles))


class ImMotorsSensor(ImMotorsEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, vehicle_id, key):
        super().__init__(coordinator, vehicle_id, key)
        self.key = key
        self._attr_name, self._attr_device_class = DESCRIPTIONS[key]

    @property
    def native_value(self):
        if self.key == "telemetry_status":
            return "待定"
        if self.key == "project_code":
            return self.vehicle.project_code if self.vehicle else None
        value = {"metadata_updated": self.coordinator.data.metadata_time_ms,
                 "token_expires": self.coordinator.data.expiration_time_ms,
                 "token_refresh": self.coordinator.data.refresh_time_ms}[self.key]
        return datetime.fromtimestamp(value / 1000, timezone.utc) if value > 0 else None

    @property
    def extra_state_attributes(self):
        if self.key == "telemetry_status":
            return {"pending_fields": list(PENDING_FIELDS), "vehicle_telemetry_enabled": False,
                    "reason": "车况查询副作用及真实字段尚未完成验证"}
        return None


class PendingSensor(ImMotorsEntity, SensorEntity):
    _attr_entity_registry_enabled_default = False
    _attr_native_value = None

    def __init__(self, coordinator, vehicle_id, name, index):
        super().__init__(coordinator, vehicle_id, f"pending_{index}")
        self._attr_name = name

    @property
    def available(self):
        return False

    @property
    def extra_state_attributes(self):
        return {"解析状态": "待定", "reason": "尚未验证真实车况、单位或查询副作用"}
