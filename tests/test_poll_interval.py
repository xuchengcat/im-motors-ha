"""Polling preferences are validated before account I/O and survive recovery."""
from unittest.mock import patch

import pytest

from conftest import VIN, response
from custom_components.im_motors.config_flow import ImMotorsConfigFlow
from custom_components.im_motors.const import CONF_TELEMETRY_INTERVAL
from custom_components.im_motors.coordinator import ImMotorsCoordinator
from custom_components.im_motors.pyim_china.client import AccountClient
from test_client import save_session
from test_ha import create_entry


@pytest.mark.parametrize("minutes", [5, 60, 120])
async def test_import_interval_and_runtime_match(hass, account, minutes):
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    result = await flow.async_step_import({"data_dir": str(account["data"]),
        "key_file": str(account["key"]), CONF_TELEMETRY_INTERVAL: minutes})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_TELEMETRY_INTERVAL] == minutes
    account["transport"].send.assert_not_called()


@pytest.mark.parametrize("minutes", [4, 0, -1, 5.5, "5", True])
async def test_invalid_interval_never_sends_sms_or_imports(hass, account, minutes):
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    result = await flow.async_step_sms({"phone": "13800000000", CONF_TELEMETRY_INTERVAL: minutes})
    assert result["errors"][CONF_TELEMETRY_INTERVAL] == "invalid_interval"
    result = await flow.async_step_import({"data_dir": str(account["data"]),
        "key_file": str(account["key"]), CONF_TELEMETRY_INTERVAL: minutes})
    assert result["errors"][CONF_TELEMETRY_INTERVAL] == "invalid_interval"
    account["transport"].send.assert_not_called()


async def test_default_schema_and_legacy_entry_use_one_hour(hass, account):
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    form = await flow.async_step_sms()
    interval = next(key for key in form["data_schema"].schema if str(key) == CONF_TELEMETRY_INTERVAL)
    assert interval.default() == 60
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    hass.config_entries.async_update_entry(entry, data={key: value for key, value in entry.data.items()
        if key != CONF_TELEMETRY_INTERVAL})
    coordinator = ImMotorsCoordinator(hass, entry, account["client"])
    assert coordinator.telemetry_interval_minutes == 60
    assert account["client"].telemetry_interval_ms == 3600000


async def test_reconfigure_changes_interval_and_reauth_preserves_it(hass, account):
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        invalid = await flow.async_step_paths(dict(entry.data, telemetry_interval_minutes=4))
        assert invalid["errors"][CONF_TELEMETRY_INTERVAL] == "invalid_interval"
        result = await flow.async_step_paths(dict(entry.data, telemetry_interval_minutes=15))
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.data[CONF_TELEMETRY_INTERVAL] == 15
        flow = ImMotorsConfigFlow()
        flow.hass = hass
        flow.context = {"source": "reauth", "entry_id": entry.entry_id}
        form = await flow.async_step_sms()
        interval = next(key for key in form["data_schema"].schema if str(key) == CONF_TELEMETRY_INTERVAL)
        assert interval.default() == 15
        paths = {key: entry.data[key] for key in ("data_dir", "key_file")}
        result = await flow.async_step_import(paths)
        assert result["reason"] == "reauth_successful"
        await hass.async_block_till_done()
        assert entry.data[CONF_TELEMETRY_INTERVAL] == 15
    account["transport"].send.assert_not_called()


def test_hourly_cache_boundary_and_restart(account):
    save_session(account, suggest_refresh_time=account["now"] + 7200000,
                 expiration_time=account["now"] + 10800000)
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    account["client"].update(include_telemetry=True)
    restarted = AccountClient(account["data"], account["key"], clock=account["clock"], transport_factory=account["factory"])
    assert restarted.telemetry_interval_ms == 3600000
    account["clock"].return_value += 3599999
    account["transport"].send.side_effect = [response([{"vin": VIN}])]
    restarted.update(include_telemetry=True)  # Metadata expires, vehicle cache does not.
    assert account["transport"].send.call_count == 3
    account["clock"].return_value += 1
    account["transport"].send.side_effect = [response({"category": {"vin": VIN}})]
    restarted.update(include_telemetry=True)
    assert account["transport"].send.call_count == 4


@pytest.mark.parametrize("minutes", [0, 4, 4.9, 5.0, "5", True])
def test_runtime_rejects_bypassing_ui(account, minutes):
    with pytest.raises(ValueError):
        account["client"].set_telemetry_interval(minutes)
    account["transport"].send.assert_not_called()
