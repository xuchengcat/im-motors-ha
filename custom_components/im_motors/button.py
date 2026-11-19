"""User-triggered queries and opt-in experimental vehicle controls."""
from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback

from .entity import ImMotorsEntity
from .control import ImMotorsControlEntity
from .pyim_china.vehicle_control import ACTIONS

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_vehicles():
        entities = []
        for vehicle in coordinator.data.vehicles:
            if vehicle.identifier not in known:
                known.add(vehicle.identifier)
                entities.append(ImMotorsQueryButton(coordinator, vehicle.identifier))
            if coordinator.control_enabled:
                for action in coordinator.available_actions(vehicle.identifier):
                    identity = (vehicle.identifier, action)
                    if not ACTIONS[action][1] and identity not in known:
                        known.add(identity)
                        entities.append(ImMotorsControlButton(coordinator, vehicle.identifier, action))
        if entities:
            async_add_entities(entities)

    add_vehicles()
    entry.async_on_unload(coordinator.async_add_listener(add_vehicles))


class ImMotorsQueryButton(ImMotorsEntity, ButtonEntity):
    _attr_name = "立即重新查询车况"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, vehicle_id):
        super().__init__(coordinator, vehicle_id, "query_vehicle")

    async def async_press(self):
        await self.coordinator.async_query_vehicle()


class ImMotorsControlButton(ImMotorsControlEntity, ButtonEntity):
    _attr_icon = "mdi:car-connected"

    def __init__(self, coordinator, vehicle_id, action):
        super().__init__(coordinator, vehicle_id, "control_" + action)
        self.action = action
        self._attr_name = ACTIONS[action][0]

    @property
    def available(self):
        return super().available and self.action in self.coordinator.available_actions(self.vehicle_id)

    async def async_press(self):
        await self.coordinator.async_control(self.vehicle_id, self.action)
