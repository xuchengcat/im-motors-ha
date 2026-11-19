"""Vehicle settings report cloud observations, never optimistic command values."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import callback
from .control import ImMotorsControlEntity
from .telemetry import raw

PARALLEL_UPDATES = 1
# key: label, action, parameter, range, step, observation group/field, fixed params
SETTINGS = {
    "ac_temperature": ("空调设定温度", "ac_temperature", "temperature", 16, 30, 1,
        "ac", "aclTemDspCmd", {}),
    "charge_target": ("充电目标", "charge_target", "percent", 40, 100, 10, None, None, {}),
    "steering_heat": ("方向盘加热级别", "steering_heat", "level", 0, 3, 1,
        "steeringWheel", "steeringWheelHeatingStatus", {}),
}
for position, label, prefix in (("front_left", "主驾", "fl"), ("front_right", "副驾", "fr")):
    for suffix, action, name in (("heat", "seat_heat", "加热"), ("vent", "seat_vent", "通风")):
        SETTINGS[f"{position}_{suffix}"] = (f"{label}座椅{name}级别", action, "level", 0, 3, 1,
            "seat", f"{prefix}Seat{'Heat' if suffix == 'heat' else 'Vent'}Lvl", {"position": position})


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_vehicles():
        if not coordinator.control_enabled:
            return
        entities = []
        for vehicle in coordinator.data.vehicles:
            for key, spec in SETTINGS.items():
                identity = (vehicle.identifier, key)
                if identity not in known and spec[1] in coordinator.available_actions(vehicle.identifier):
                    known.add(identity)
                    entities.append(ImMotorsControlNumber(coordinator, vehicle.identifier, key, spec))
        if entities:
            async_add_entities(entities)

    add_vehicles()
    entry.async_on_unload(coordinator.async_add_listener(add_vehicles))


class ImMotorsControlNumber(ImMotorsControlEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:car-cog"

    def __init__(self, coordinator, vehicle_id, key, spec):
        super().__init__(coordinator, vehicle_id, "control_setting_" + key)
        self.key, self.spec = key, spec
        self._attr_name = spec[0]
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = spec[3:6]
        if key == "ac_temperature":
            self._attr_native_unit_of_measurement = "°C"
        elif key == "charge_target":
            self._attr_native_unit_of_measurement = "%"

    @property
    def snapshot(self):
        return self.coordinator.data.telemetry.get(self.vehicle_id)

    @property
    def native_step(self):
        return 1 if self.key == "charge_target" and self.snapshot and self.snapshot.supports_feature("support_3.0") else self._attr_native_step

    @property
    def native_value(self):
        if self.key == "charge_target":
            return self.snapshot.charge_target_percent if self.snapshot else None
        value = raw(self.snapshot, self.spec[6], self.spec[7])
        return value if type(value) in (int, float) else None

    @property
    def available(self):
        return super().available and self.spec[1] in self.coordinator.available_actions(self.vehicle_id)

    async def async_set_native_value(self, value):
        parameter = self.spec[2]
        if parameter != "temperature":
            if value != int(value):
                from homeassistant.exceptions import HomeAssistantError
                raise HomeAssistantError("此设置仅接受整数。")
            value = int(value)
        await self.coordinator.async_control(self.vehicle_id, self.spec[1], {**self.spec[8], parameter: value})
