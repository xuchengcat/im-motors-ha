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
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if getattr(entry.runtime_data, "trips", None):
        await entry.runtime_data.trips.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
