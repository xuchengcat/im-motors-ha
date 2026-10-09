"""Import a portable session without putting tokens or secret keys in HA storage."""
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_DATA_DIR, CONF_KEY_FILE, CONF_RESUME
from .pyim_china import AccountClient, ClientFailure, LoginRequired


SCHEMA = vol.Schema({vol.Required(CONF_DATA_DIR, default="/config/im_motors/data"): str,
                     vol.Required(CONF_KEY_FILE, default="/run/secrets/im_vault_key"): str})


async def validate_input(hass: HomeAssistant, data):
    if any(not Path(data[key]).is_absolute() for key in (CONF_DATA_DIR, CONF_KEY_FILE)):
        raise ClientFailure("Absolute container paths required")
    client = AccountClient(data[CONF_DATA_DIR], data[CONF_KEY_FILE])
    return await hass.async_add_executor_job(client.validate)


class ImMotorsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                identity = await validate_input(self.hass, user_input)
            except LoginRequired:
                errors["base"] = "login_required"
            except ClientFailure:
                errors["base"] = "invalid_vault"
            else:
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="智己汽车（只读）", data=user_input)
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                identity = await validate_input(self.hass, user_input)
                if identity != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                client = AccountClient(user_input[CONF_DATA_DIR], user_input[CONF_KEY_FILE])
                await self.hass.async_add_executor_job(client.resume)
            except LoginRequired:
                errors["base"] = "login_required"
            except ClientFailure:
                errors["base"] = "invalid_vault"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(step_id="reauth_confirm", data_schema=SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        schema = SCHEMA.extend({vol.Required(CONF_RESUME, default=False): bool})
        if user_input is not None:
            data = {key: user_input[key] for key in (CONF_DATA_DIR, CONF_KEY_FILE)}
            try:
                identity = await validate_input(self.hass, data)
                if identity != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                if user_input.get(CONF_RESUME):
                    client = AccountClient(data[CONF_DATA_DIR], data[CONF_KEY_FILE])
                    await self.hass.async_add_executor_job(client.resume)
            except LoginRequired:
                errors["base"] = "login_required"
            except ClientFailure:
                errors["base"] = "invalid_vault"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)
