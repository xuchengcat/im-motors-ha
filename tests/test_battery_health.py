"""SOH is a shared read-only measurement, independent of control access."""
from unittest.mock import patch
import pytest
from homeassistant.helpers import entity_registry as er
from conftest import VIN, response
from test_ha import create_entry


@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_health_entity_enabled_in_both_modes_and_missing_stays_unknown(hass, account, mode):
    account["transport"].send.side_effect = [response([{"vin": VIN}]),
        response({"category": {"vin": VIN, "period": {"bmsPackSOH": 96.75}}})]
    entry = await create_entry(hass, account, access_mode=mode)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    entity = next(e for e in er.async_get(hass).entities.values() if e.unique_id.endswith("_battery_health"))
    assert entity.disabled_by is None
    state = hass.states.get(entity.entity_id)
    assert state.state == "96.75"
    assert state.attributes["friendly_name"].endswith("电池健康度")
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["state_class"] == "measurement"
    assert "device_class" not in state.attributes  # Health differs from remaining charge.
    assert state.attributes["source_field"] == "period.bmsPackSOH"
    assert account["transport"].send.call_count == 2
    account["transport"].send.side_effect = [response({"category": {"vin": VIN, "period": {}}})]
    await entry.runtime_data.async_query_vehicle()
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state == "unknown"
    assert account["transport"].send.call_count == 3
    assert all(call.args[0].method == "GET" for call in account["transport"].send.call_args_list)
    assert await hass.config_entries.async_unload(entry.entry_id)
