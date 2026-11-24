"""A minute auth heartbeat with SDK-enforced metadata caching and failure stops."""
import asyncio
from datetime import timedelta
from dataclasses import replace
from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import account_type, DOMAIN, CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL, control_enabled
from .pyim_china import AccountClient, AccountSnapshot, ClientFailure, LoginRequired
from .pyim_china.client import TemporaryReadFailure

LOGGER = logging.getLogger(__name__)


class ImMotorsCoordinator(DataUpdateCoordinator[AccountSnapshot]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AccountClient):
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=60), always_update=False)
        self.client = client
        self.telemetry_interval_minutes = entry.options.get(CONF_TELEMETRY_INTERVAL,
            entry.data.get(CONF_TELEMETRY_INTERVAL, DEFAULT_TELEMETRY_INTERVAL))
        self.client.set_telemetry_interval(self.telemetry_interval_minutes)
        self.client.set_control_enabled(self.control_enabled)
        self.control_results = {}
        self.hybrid_capabilities = {}
        from .odometer import OdometerReader
        self.odometer_reader = OdometerReader(client, account_kind=account_type(entry))
        self._request_lock = asyncio.Lock()

    @property
    def control_enabled(self):
        return control_enabled(self.config_entry)

    def available_actions(self, vehicle_id):
        from .pyim_china.vehicle_control import supported_actions
        snapshot = self.data.telemetry.get(vehicle_id) if self.data else None
        return supported_actions(snapshot.supports_feature("support_3.0") if snapshot else None)

    async def async_control(self, vehicle_id, action, parameters=None):
        async with self._request_lock:
            if not self.control_enabled:
                raise HomeAssistantError("此集成配置为只读模式，禁止车控；运行模式仅在首次配置时选择，后续不可切换。")
            if (not self.data or vehicle_id not in {v.identifier for v in self.data.vehicles}
                    or action not in self.available_actions(vehicle_id)):
                raise HomeAssistantError("车辆或车控协议不可用，请先查询车况。")
            self.client.set_control_enabled(self.control_enabled)
            try:
                # Normal auth maintenance runs first; never retry a control POST.
                data = await self._async_fetch_data()
                self.async_set_updated_data(data)
                result = await self.hass.async_add_executor_job(partial(
                    self.client.control, vehicle_id, action, parameters))
            except LoginRequired:
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("车控认证失效，请重新登录；指令未自动重试。") from None
            except ClientFailure as error:
                self.control_results[vehicle_id] = {"status": "failed_or_unknown", "action": action,
                                                    "execution_confirmed": False}
                self.async_update_listeners()
                raise HomeAssistantError(str(error)) from None
            except ConfigEntryAuthFailed:
                self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("账号认证不可用，未发送车控；请重新登录。") from None
            except UpdateFailed:
                raise HomeAssistantError("账号请求不可用，未发送车控；请检查集成状态。") from None
            self.control_results[vehicle_id] = result
            self.async_update_listeners()
            return result

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
            snapshot = await self.hass.async_add_executor_job(partial(
                self.client.update, include_telemetry=True, force_telemetry=force_telemetry))
            odometer = await self.hass.async_add_executor_job(partial(
                self.odometer_reader.update, snapshot, force=force_telemetry))
            snapshot = replace(snapshot, odometer=odometer)
            self.control_results.update(snapshot.control_status)
            if getattr(self, "trips", None):
                await self.trips.observe(snapshot)
            if getattr(self, "energy", None):
                await self.energy.observe(snapshot)
            return snapshot
        except LoginRequired:
            raise ConfigEntryAuthFailed("Manual account login required") from None
        except TemporaryReadFailure:
            raise UpdateFailed("Temporary cloud read failure; automatic retry scheduled") from None
        except ClientFailure:
            raise UpdateFailed("Account requests paused; review protected state and reconfigure to resume") from None
