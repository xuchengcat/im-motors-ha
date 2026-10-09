"""User-triggered read-only cloud vehicle queries."""
from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback

from .entity import ImMotorsEntity

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
