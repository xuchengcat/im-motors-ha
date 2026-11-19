"""Home Assistant entry lifecycle."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .const import CONF_DATA_DIR, CONF_KEY_FILE, PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .coordinator import ImMotorsCoordinator
    from .pyim_china import AccountClient

    client = AccountClient(entry.data[CONF_DATA_DIR], entry.data[CONF_KEY_FILE])
    coordinator = ImMotorsCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    from .trips import TripManager
    from .trip_web import async_setup_web
    coordinator.trips = TripManager(hass, entry, coordinator)
    await coordinator.trips.setup()
    await async_setup_web(hass)
    from .control import async_setup_services
    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from homeassistant.helpers import entity_registry as er
    from .telemetry import SENSORS, BINARY_SENSORS
    from .entity import async_label_other_entity
    registry = er.async_get(hass)
    debug_ids = {f"{entry.entry_id}_{vehicle.identifier}_{key}"
                 for vehicle in coordinator.data.vehicles
                 for key, spec in SENSORS.items() if spec.debug}
    enabled_ids = debug_ids | {f"{entry.entry_id}_{vehicle.identifier}_{key}"
                 for vehicle in coordinator.data.vehicles
                 for key, spec in {**SENSORS, **BINARY_SENSORS}.items()
                 if coordinator.control_enabled and spec.diagnostic}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id in enabled_ids and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity.entity_id, disabled_by=None)
        if entity.unique_id in debug_ids:
            async_label_other_entity(hass, entity.entity_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data.client.set_control_enabled(False)
    if getattr(entry.runtime_data, "trips", None):
        await entry.runtime_data.trips.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass, entry):
    from .const import control_enabled
    entry.runtime_data.client.set_control_enabled(control_enabled(entry))
    await hass.config_entries.async_reload(entry.entry_id)
