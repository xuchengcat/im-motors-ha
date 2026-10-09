"""A minute auth heartbeat with SDK-enforced metadata caching and failure stops."""
from datetime import timedelta
from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .pyim_china import AccountClient, AccountSnapshot, ClientFailure, LoginRequired

LOGGER = logging.getLogger(__name__)


class ImMotorsCoordinator(DataUpdateCoordinator[AccountSnapshot]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AccountClient):
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=60), always_update=False)
        self.client = client

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(partial(self.client.update, include_telemetry=True))
        except LoginRequired:
            raise ConfigEntryAuthFailed("Manual account login required") from None
        except ClientFailure:
            raise UpdateFailed("Account requests paused; review protected state and reconfigure to resume") from None
