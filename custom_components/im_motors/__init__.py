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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
