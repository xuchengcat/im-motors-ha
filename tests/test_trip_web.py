"""Use HA's actual async websocket wrappers, including admin authorization."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.im_motors.trip_web import ws_entries, ws_query
from test_ha import create_entry


async def test_websocket_query_with_real_ha_wrappers(hass, account):
    from conftest import response, VIN
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    entry = await create_entry(hass, account)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done(wait_background_tasks=True)
    conn = Mock(user=SimpleNamespace(is_admin=True))
    conn.async_handle_exception = Mock()
    ws_entries(hass, conn, {"id": 1, "type": "im_motors/trips/entries"})
    await hass.async_block_till_done(wait_background_tasks=True)
    entries = conn.send_result.call_args.args[1]
    assert entries[0]["entry_id"] == entry.entry_id
    assert entries[0]["source"] == "ha_telemetry"
    ws_query(hass, conn, {"id": 2, "type": "im_motors/trips/query", "entry_id": entry.entry_id,
        "vehicle_id": entries[0]["vehicles"][0]["id"], "start_date": "2026-10-01", "end_date": "2026-10-31", "include_trips": True})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = conn.send_result.call_args.args[1]
    assert result["stats"]["trips"] == 0
    assert "recording" in result["status"]
    conn.async_handle_exception.assert_not_called()


async def test_non_admin_cannot_read_archive(hass):
    conn = Mock(user=SimpleNamespace(is_admin=False))
    ws_entries(hass, conn, {"id": 1, "type": "im_motors/trips/entries"})
    await hass.async_block_till_done(wait_background_tasks=True)
    conn.send_result.assert_not_called()
    # HA's wrapper routes Unauthorized to the connection error handler.
    conn.async_handle_exception.assert_called_once()
