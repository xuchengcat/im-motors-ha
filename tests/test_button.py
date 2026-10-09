"""Manual query bypasses only telemetry cache and preserves durable failure stops."""
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from conftest import VIN, response
from custom_components.im_motors.pyim_china import ClientFailure
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from test_ha import create_entry


def telemetry(soc):
    return response({"category": {"vin": VIN, "period": {"originalBmsPackSOCDsp": soc}}})


async def test_press_queries_cloud_inside_hour_and_resets_cache(hass, account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), telemetry(80)]
    entry = await create_entry(hass, account)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    button = next(e.entity_id for e in registry.entities.values() if e.domain == "button")
    soc = next(e.entity_id for e in registry.entities.values() if e.unique_id.endswith("_soc"))
    assert hass.states.get(soc).state == "80"
    account["clock"].return_value += 1000
    account["transport"].send.side_effect = [telemetry(79)]
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(soc).state == "79"
    assert account["transport"].send.call_count == 3
    assert entry.runtime_data.data.telemetry_time_ms == account["clock"]()
    await entry.runtime_data.async_refresh()
    assert account["transport"].send.call_count == 3
    assert hass.states.get(button).state not in ("unknown", "unavailable")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(button).state == "unavailable"


def test_manual_query_respects_latched_failure(account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), telemetry(80)]
    account["client"].update(include_telemetry=True)
    with key_context(account["key"]):
        vault_for_path(account["data"] / "ha-fault.imvault")._save_payload(
            {"kind": "ha-fault", "reason": "telemetry_request_pending"})
    with pytest.raises(ClientFailure, match="paused"):
        account["client"].update(include_telemetry=True, force_telemetry=True)
    assert account["transport"].send.call_count == 2


async def test_manual_query_failure_marks_unavailable_without_leaking(hass, account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), telemetry(80)]
    entry = await create_entry(hass, account)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    with patch.object(account["client"], "update", side_effect=ClientFailure("PRIVATE")):
        with pytest.raises(HomeAssistantError) as error:
            await entry.runtime_data.async_query_vehicle()
    assert "PRIVATE" not in str(error.value)
    assert not entry.runtime_data.last_update_success
