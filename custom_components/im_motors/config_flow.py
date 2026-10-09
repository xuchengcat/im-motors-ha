"""SMS login and protected session import; HA stores paths and hashed identity."""
from functools import partial
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import DOMAIN, CONF_DATA_DIR, CONF_KEY_FILE, CONF_RESUME
from .pyim_china import AccountClient, ClientFailure, LoginRequired
from .pyim_china.ha_sms_login import HaSmsLogin, SmsLoginFailure
from .pyim_china.managed_storage import managed_paths
from .pyim_china.credential_store import VaultError


async def validate_input(hass: HomeAssistant, data):
    if any(not Path(data[key]).is_absolute() for key in (CONF_DATA_DIR, CONF_KEY_FILE)):
        raise ClientFailure("Absolute container paths required")
    client = AccountClient(data[CONF_DATA_DIR], data[CONF_KEY_FILE])
    return await hass.async_add_executor_job(client.validate)


class ImMotorsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _sms = None
    _paths = None

    def _login_entry(self):
        if self.source == "reauth":
            return self._get_reauth_entry()
        if self.source == "reconfigure":
            return self._get_reconfigure_entry()
        return None

    def _path_schema(self):
        entry = self._login_entry()
        defaults = entry.data if entry else {}
        return vol.Schema({
            vol.Required(CONF_DATA_DIR, default=defaults.get(CONF_DATA_DIR, "/config/im_motors/data")): str,
            vol.Required(CONF_KEY_FILE, default=defaults.get(CONF_KEY_FILE, "/run/secrets/im_vault_key")): str,
        })

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return await self.async_step_import(user_input)
        return self.async_show_menu(step_id="user", menu_options=["sms", "import"])

    async def async_step_import(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                identity = await validate_input(self.hass, user_input)
                if self.source == "reauth":
                    entry = self._get_reauth_entry()
                    if identity != entry.unique_id:
                        return self.async_abort(reason="wrong_account")
                    client = AccountClient(user_input[CONF_DATA_DIR], user_input[CONF_KEY_FILE])
                    await self.hass.async_add_executor_job(client.resume)
            except LoginRequired:
                errors["base"] = "login_required"
            except ClientFailure:
                errors["base"] = "invalid_vault"
            else:
                if self.source == "reauth":
                    return self.async_update_reload_and_abort(entry, data_updates=user_input)
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="智己汽车（只读）", data=user_input)
        return self.async_show_form(step_id="import", data_schema=self._path_schema(), errors=errors)

    async def async_step_sms(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not HaSmsLogin.valid_phone(user_input["phone"]):
                errors["phone"] = "invalid_phone"
            else:
                try:
                    entry = self._login_entry()
                    if entry:
                        paths = {key: entry.data[key] for key in (CONF_DATA_DIR, CONF_KEY_FILE)}
                    else:
                        paths = await self.hass.async_add_executor_job(managed_paths,
                            self.hass.config.path(".storage", DOMAIN), user_input["phone"])
                    sms = HaSmsLogin(paths[CONF_DATA_DIR], paths[CONF_KEY_FILE])
                    identity = await self.hass.async_add_executor_job(sms.prepare)
                    if entry:
                        if identity != entry.unique_id:
                            return self.async_abort(reason="wrong_account")
                    else:
                        await self.async_set_unique_id(identity)
                        self._abort_if_unique_id_configured()
                    self._sms, self._paths = sms, paths
                    completed = await self.hass.async_add_executor_job(sms.start, user_input["phone"])
                except (VaultError, OSError):
                    errors["base"] = "invalid_storage"
                except SmsLoginFailure as error:
                    if error.error in ("captcha_required", "account_binding_required",
                                       "unresolved_request", "login_response_invalid"):
                        return self.async_abort(reason=error.error)
                    errors["base"] = error.error
                else:
                    if completed:
                        return await self._finish_login()
                    return await self.async_step_sms_code()
        schema = vol.Schema({vol.Required("phone"): str})
        return self.async_show_form(step_id="sms", data_schema=schema, errors=errors)

    async def async_step_sms_code(self, user_input=None):
        errors = {}
        if self._sms is None:
            return self.async_abort(reason="unresolved_request")
        if user_input is not None:
            try:
                if user_input.get("resend_code"):
                    await self.hass.async_add_executor_job(
                        partial(self._sms.start, self._sms.phone, resend=True))
                elif not HaSmsLogin.valid_code(user_input.get("code", "")):
                    errors["code"] = "invalid_code"
                else:
                    await self.hass.async_add_executor_job(self._sms.login, user_input["code"])
                    return await self._finish_login()
            except SmsLoginFailure as error:
                if error.error in ("captcha_required", "account_binding_required",
                                   "unresolved_request", "login_response_invalid"):
                    return self.async_abort(reason=error.error)
                errors["base"] = error.error
        schema = vol.Schema({
            vol.Optional("code"): selector.TextSelector(selector.TextSelectorConfig(type="password")),
            vol.Required("resend_code", default=False): bool,
        })
        return self.async_show_form(step_id="sms_code", data_schema=schema, errors=errors)

    async def _finish_login(self):
        self._sms = None
        data = self._paths
        entry = self._login_entry()
        if entry:
            return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_create_entry(title="智己汽车（只读）", data=data)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is not None:
            return await self.async_step_import(user_input)
        return self.async_show_menu(step_id="reauth_confirm", menu_options=["sms", "import"])

    async def async_step_reconfigure(self, user_input=None):
        if user_input is not None:
            return await self.async_step_paths(user_input)
        return self.async_show_menu(step_id="reconfigure", menu_options=["sms", "paths"])

    async def async_step_paths(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        schema = self._path_schema().extend({vol.Required(CONF_RESUME, default=False): bool})
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
        return self.async_show_form(step_id="paths", data_schema=schema, errors=errors)
