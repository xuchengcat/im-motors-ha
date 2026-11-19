"""HA actions for the allowlisted vehicle controls."""
import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.config_entries import ConfigEntryState
from .const import DOMAIN
from .entity import ImMotorsEntity
from .pyim_china.vehicle_control import ACTIONS


class ImMotorsControlEntity(ImMotorsEntity):
    _attr_assumed_state = True

    @property
    def available(self):
        return super().available and self.coordinator.control_enabled

    @property
    def extra_state_attributes(self):
        return {"execution_confirmed": False, "protocol_verified_in_vehicle": False,
                "last_control": self.coordinator.control_results.get(self.vehicle_id),
                "warning": "非官方车控，服务器受理不等于车辆已执行，请现场确认。"}


@callback
def async_setup_services(hass):
    if hass.services.has_service(DOMAIN, "vehicle_control"):
        return

    def coordinator_for(data):
        entry = hass.config_entries.async_get_entry(data["entry_id"])
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise HomeAssistantError("智己集成尚未加载。")
        return entry.runtime_data

    async def vehicle_control(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized
        coordinator = coordinator_for(call.data)
        vehicle_id = call.data.get("vehicle_id")
        vehicles = coordinator.data.vehicles
        if not vehicle_id and len(vehicles) == 1:
            vehicle_id = vehicles[0].identifier
        if not vehicle_id:
            raise HomeAssistantError("多辆车时必须指定车辆哈希 ID。")
        return await coordinator.async_control(vehicle_id, call.data["action"], call.data.get("parameters", {}))

    async def list_controls(call):
        coordinator = coordinator_for(call.data)
        return {"control_enabled": coordinator.control_enabled,
            "vehicles": [{"vehicle_id": v.identifier,
                "actions": [{"action": name, "name": ACTIONS[name][0], "parameters": list(ACTIONS[name][1]),
                             "vehicle_tested": False} for name in coordinator.available_actions(v.identifier)]}
                for v in coordinator.data.vehicles]}

    hass.services.async_register(DOMAIN, "vehicle_control", vehicle_control,
        schema=vol.Schema({vol.Required("entry_id"): str, vol.Optional("vehicle_id"): str,
            vol.Required("action"): vol.In(ACTIONS), vol.Optional("parameters", default=dict): dict}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "list_controls", list_controls,
        schema=vol.Schema({vol.Required("entry_id"): str}), supports_response=SupportsResponse.ONLY)
