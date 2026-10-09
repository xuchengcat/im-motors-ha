"""Exercise the real Home Assistant flow, coordinator, platforms and diagnostics."""
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.im_motors.const import DOMAIN
from custom_components.im_motors.config_flow import ImMotorsConfigFlow
from custom_components.im_motors.coordinator import ImMotorsCoordinator
from custom_components.im_motors.diagnostics import async_get_config_entry_diagnostics
from custom_components.im_motors.pyim_china import ClientFailure, LoginRequired
from custom_components.im_motors.sensor import PendingSensor
from custom_components.im_motors.pyim_china.client import AccountSnapshot, Vehicle


async def create_entry(hass, account):
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    result = await flow.async_step_user({"data_dir": str(account["data"]), "key_file": str(account["key"])})
    assert result["type"] == "create_entry"
    return config_entries.ConfigEntry(version=1, minor_version=1, domain=DOMAIN,
        title=result["title"], data=result["data"], source="user", unique_id=flow.unique_id,
        discovery_keys={}, options={}, subentries_data=[])


async def test_flow_offline_and_invalid_paths(hass, account):
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    form = await flow.async_step_user({"data_dir": "relative", "key_file": "missing"})
    assert form["errors"]["base"] == "invalid_vault"
    entry = await create_entry(hass, account)
    assert set(entry.data) == {"data_dir", "key_file"}
    assert "SYNTHETIC" not in repr(dict(entry.data))
    account["transport"].send.assert_not_called()


async def test_full_entry_load_entities_diagnostics_and_unload(hass, account):
    entry = await create_entry(hass, account)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    states = [state for state in hass.states.async_all() if state.entity_id.startswith(("sensor.", "binary_sensor."))]
    assert len(states) == 7
    assert any(state.state == "待定" for state in states)
    assert any(state.state == "off" for state in states)
    assert any(state.state == "on" for state in states)
    assert all("SYNTHETIC-PRIVATE-NAME" not in str(state.as_dict()) for state in states)
    devices = list(dr.async_get(hass).devices.values())
    assert len(devices) == 1
    assert devices[0].manufacturer == "IM Motors"
    registry = er.async_get(hass)
    entities = [entity for entity in registry.entities.values() if entity.config_entry_id == entry.entry_id]
    assert len(entities) == 18
    assert sum(entity.disabled_by is not None for entity in entities) == 11
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["associated_vehicle_count"] == 1
    assert "SYNTHETIC" not in str(diagnostics)
    assert "data_dir" not in str(diagnostics)
    assert "key_file" not in str(diagnostics)
    snapshot = entry.runtime_data.data
    second = Vehicle("b" * 64, "智己汽车", None, None, None)
    entry.runtime_data.async_set_updated_data(AccountSnapshot(
        (second,), snapshot.metadata_time_ms, snapshot.expiration_time_ms, snapshot.refresh_time_ms))
    await hass.async_block_till_done()
    entities = [entity for entity in registry.entities.values() if entity.config_entry_id == entry.entry_id]
    assert len(entities) == 36
    assert all(hass.states.get(state.entity_id).state == "unavailable" for state in states)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_coordinator_auth_and_failure_mapping(hass, account):
    entry = await create_entry(hass, account)
    coordinator = ImMotorsCoordinator(hass, entry, account["client"])
    with patch.object(account["client"], "update", side_effect=LoginRequired("SYNTHETIC")):
        with pytest.raises(ConfigEntryAuthFailed, match="Manual account"):
            await coordinator._async_update_data()
    with patch.object(account["client"], "update", side_effect=ClientFailure("SYNTHETIC")):
        with pytest.raises(UpdateFailed) as error:
            await coordinator._async_update_data()
    assert "SYNTHETIC" not in str(error.value)


async def test_pending_sensor_is_disabled_unavailable_and_has_no_unit(hass, account):
    entry = await create_entry(hass, account)
    coordinator = ImMotorsCoordinator(hass, entry, account["client"])
    coordinator.async_set_updated_data(account["client"].update())
    pending = PendingSensor(coordinator, coordinator.data.vehicles[0].identifier, "SOC", 0)
    assert pending.available is False
    assert pending.entity_registry_enabled_default is False
    assert pending.native_value is None
    assert pending.native_unit_of_measurement is None
    assert pending.extra_state_attributes["解析状态"] == "待定"


async def test_reconfigure_resume_and_same_identity(hass, account):
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id, "source": "reconfigure"}
    with patch.object(account["client"], "resume") as resume, \
            patch("custom_components.im_motors.config_flow.AccountClient", return_value=account["client"]), \
            patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await flow.async_step_reconfigure(dict(entry.data, resume_requests=True))
        await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    resume.assert_called_once()


async def test_managed_config_flow_and_duplicate_identity(hass, account):
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert form["type"] == "form"
        result = await hass.config_entries.flow.async_configure(form["flow_id"],
            {"data_dir": str(account["data"]), "key_file": str(account["key"])})
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["result"].state is ConfigEntryState.LOADED
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(form["flow_id"],
        {"data_dir": str(account["data"]), "key_file": str(account["key"])})
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    assert account["transport"].send.call_count == 1


async def test_reauth_import_resumes_valid_same_identity_offline(hass, account):
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id, "source": "reauth"}
    with patch("custom_components.im_motors.config_flow.AccountClient", return_value=account["client"]), \
            patch.object(account["client"], "resume") as resume, \
            patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await flow.async_step_reauth_confirm(dict(entry.data))
        await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    resume.assert_called_once()
    account["transport"].send.assert_not_called()
