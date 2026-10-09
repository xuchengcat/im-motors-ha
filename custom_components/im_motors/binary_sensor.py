"""Scene feature flags are capabilities, never current vehicle state."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import ImMotorsEntity, ImMotorsTelemetryEntity
from .telemetry import BINARY_SENSORS

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_vehicles():
        entities = []
        for vehicle in coordinator.data.vehicles:
            if vehicle.identifier not in known:
                known.add(vehicle.identifier)
                entities.extend(ImMotorsCapability(coordinator, vehicle.identifier, key, name)
                                for key, name in (("has_setting", "支持场景设置"),
                                                  ("has_support", "支持场景功能")))
                entities.extend(ImMotorsTelemetryBinarySensor(coordinator, vehicle.identifier, key, spec)
                                for key, spec in BINARY_SENSORS.items())
        if entities:
            async_add_entities(entities)

    add_vehicles()
    entry.async_on_unload(coordinator.async_add_listener(add_vehicles))


class ImMotorsCapability(ImMotorsEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, vehicle_id, key, name):
        super().__init__(coordinator, vehicle_id, key)
        self.key = key
        self._attr_name = name

    @property
    def is_on(self):
        return getattr(self.vehicle, self.key) if self.vehicle else None


class ImMotorsTelemetryBinarySensor(ImMotorsTelemetryEntity, BinarySensorEntity):
    @property
    def is_on(self):
        return self.mapped_value
