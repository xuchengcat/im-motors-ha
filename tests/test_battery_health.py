"""Unverified SOH stays enabled as a raw Other signal without health units."""
import hashlib
import inspect
from unittest.mock import patch
import pytest
from homeassistant.helpers import entity_registry as er, label_registry as lr
from conftest import VIN, response
from test_ha import create_entry


@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_soh_is_enabled_raw_other_and_explicit_disabling_survives_reload(hass, account, mode):
    account["transport"].send.side_effect = [response([{"vin": VIN}]),
        response({"category": {"vin": VIN, "period": {"bmsPackSOH": 9735}}})]
    entry = await create_entry(hass, account, access_mode=mode)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        entity = next(e for e in registry.entities.values() if e.unique_id.endswith("_battery_health"))
        assert entity.disabled_by is None
        assert entity.original_name == "Other / period.bmsPackSOH"
        label = next(v.label_id for v in lr.async_get(hass).labels.values() if v.name == "其他")
        assert label in entity.labels
        state = hass.states.get(entity.entity_id)
        assert state.state == "9735"  # Raw value, without a 0..100 percentage filter.
        assert state.attributes["friendly_name"].endswith("Other / period.bmsPackSOH")
        assert "unit_of_measurement" not in state.attributes
        assert "state_class" not in state.attributes
        assert "device_class" not in state.attributes
        assert state.attributes["meaning_status"] == "unknown"
        assert state.attributes["source_field"] == "period.bmsPackSOH"
        registry.async_update_entity(entity.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get(entity.entity_id).disabled_by is er.RegistryEntryDisabler.USER
        assert account["transport"].send.call_count == 2
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_former_health_entity_migrates_to_enabled_raw_without_new_id(hass, account, mode):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    entry = await create_entry(hass, account, access_mode=mode)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    registry = er.async_get(hass)
    parameters = inspect.unwrap(registry.async_get_or_create).__code__.co_varnames
    kwargs = {"config_entry": entry} if "config_entry" in parameters else {"config_entry_id": entry.entry_id}
    vehicle_id = hashlib.sha256(VIN.encode()).hexdigest()
    old = registry.async_get_or_create("sensor", "im_motors", f"{entry.entry_id}_{vehicle_id}_battery_health",
        **kwargs, original_name="电池健康度", unit_of_measurement="%")
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    migrated = registry.async_get(old.entity_id)
    assert migrated.unique_id == old.unique_id
    assert migrated.original_name == "Other / period.bmsPackSOH"
    assert migrated.unit_of_measurement is None
    assert migrated.disabled_by is None
    assert hass.states.get(old.entity_id) is not None
    assert len([e for e in registry.entities.values() if e.unique_id == old.unique_id]) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
