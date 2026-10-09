"""A minute auth heartbeat with SDK-enforced metadata caching and failure stops."""
import asyncio
from datetime import timedelta
from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL
from .pyim_china import AccountClient, AccountSnapshot, ClientFailure, LoginRequired

LOGGER = logging.getLogger(__name__)


class ImMotorsCoordinator(DataUpdateCoordinator[AccountSnapshot]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AccountClient):
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=60), always_update=False)
        self.client = client
        self.telemetry_interval_minutes = entry.data.get(CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL)
        self.client.set_telemetry_interval(self.telemetry_interval_minutes)
        self._request_lock = asyncio.Lock()

    async def _async_update_data(self):
        async with self._request_lock:
            return await self._async_fetch_data()

    async def async_query_vehicle(self):
        """Read cloud telemetry now, keeping authentication and failure checks."""
        async with self._request_lock:
            try:
                data = await self._async_fetch_data(force_telemetry=True)
            except (ConfigEntryAuthFailed, UpdateFailed) as error:
                self.async_set_update_error(error)
                if isinstance(error, ConfigEntryAuthFailed):
                    self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(str(error)) from None
            self.async_set_updated_data(data)

    async def _async_fetch_data(self, *, force_telemetry=False):
        try:
            return await self.hass.async_add_executor_job(partial(
                self.client.update, include_telemetry=True, force_telemetry=force_telemetry))
        except LoginRequired:
            raise ConfigEntryAuthFailed("Manual account login required") from None
        except ClientFailure:
            raise UpdateFailed("Account requests paused; review protected state and reconfigure to resume") from None
