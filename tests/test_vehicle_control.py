"""No live controls: mode isolation, scoped POSTs and durable unknown outcomes."""
import hashlib
import json
from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from conftest import VIN, response
from test_client import save_session
from test_ha import create_entry
from custom_components.im_motors.config_flow import ImMotorsConfigFlow
from custom_components.im_motors.const import control_enabled
from custom_components.im_motors.pyim_china.client import AccountClient, ClientFailure
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.restricted_transport import RestrictedHttpsTransport, TransportError
from custom_components.im_motors.pyim_china.vehicle_control import (
    ACTIONS, compile_command, ControlError, LEGACY_PATH, V3_PATH)

VEHICLE_ID = hashlib.sha256(VIN.encode()).hexdigest()


def ready(account, *, v3=False, unknown=False):
    save_session(account, user_account_id="SYNTHETIC-UID")
    category = {"vin": VIN, "ac": {"aclTemDspCmd": 24}}
    data = {"category": category,
            "vehicleFunction": {"features": [{"featureKey": "support_3.0"}] if v3 else []}}
    if unknown:
        data.pop("vehicleFunction")
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response(data)]
    account["client"].update(include_telemetry=True)
    account["transport"].send.side_effect = None
    account["transport"].send.return_value = response({"eventId": "SYNTHETIC-EVENT"})
    return account["client"]


@pytest.mark.parametrize("v3,command,value", [(False, "TAILGATE_SWITCH", "5"), (True, "RMT_TAILGATE", "1:0")])
def test_scoped_signed_control_never_claims_execution(account, v3, command, value):
    client = ready(account, v3=v3)
    with pytest.raises(ClientFailure, match="read-only"):
        client.control(VEHICLE_ID, "open_trunk")
    assert account["transport"].send.call_count == 2
    client.set_control_enabled(True)
    result = client.control(VEHICLE_ID, "open_trunk")
    assert result == {"status": "accepted", "execution_confirmed": False, "action": "open_trunk"}
    request = account["transport"].send.call_args.args[0]
    payload = json.loads(request.body)
    assert payload == {"vin": VIN, "userAccountId": "SYNTHETIC-UID", "commandType": command,
        "commandValue": value, "duration": 0, "operateDevice": "ANDROID"}
    assert request.url.endswith(V3_PATH if v3 else LEGACY_PATH)
    assert request.headers["Authorization"] == "Bearer SYNTHETIC-ACCESS"
    assert request.headers["x-signature"]
    assert VIN not in repr(request)
    assert "SYNTHETIC" not in repr(request)
    cap = RestrictedHttpsTransport(**account["factory"].call_args.kwargs)
    cap._validate(request)
    with pytest.raises(TransportError):
        RestrictedHttpsTransport(allowed_vin=VIN)._validate(request)
    with pytest.raises(TransportError):
        cap._validate(replace(request, body=request.body.replace(b"SYNTHETIC-UID", b"OTHER-UID")))
    with pytest.raises(TransportError):
        RestrictedHttpsTransport(control_request=request, allowed_vin="LSY00000000000002")._validate(request)
    for path in (account["data"] / "controls").glob("*.imvault"):
        assert b"SYNTHETIC" not in path.read_bytes() and VIN.encode() not in path.read_bytes()


@pytest.mark.parametrize("v3,action,params,wire", [
    (False, "charge_target", {"percent": 80}, "5"),
    (True, "charge_target", {"percent": 81}, "81"),
    (False, "seat_vent", {"level": 2, "position": "rear_right"}, "2:8"),
    (True, "seat_vent", {"level": 2, "position": "rear_right"}, "2:2:4"),
    (True, "steering_heat", {"level": 3}, "3:3:9"),
    (True, "charge_current", {"current": "16A"}, "3"),
    (True, "ac_temperature", {"temperature": 24.5}, "6:10:24.5"),
    (False, "ac_on", {}, "1:10"),
    (True, "schedule_charge", {"start_hour": 21, "start_minute": 30, "end_hour": 7, "end_minute": 0}, "1:21:30:7:0"),
])
def test_traced_protocol_vectors(v3, action, params, wire):
    assert compile_command(action, params, v3=v3)[1]["commandValue"] == wire


@pytest.mark.parametrize("v3,action,command,value", [
    (False, "vent_windows", "WINDOW_SWITCH", "1"),
    (True, "vent_windows", "RMT_WINDOW", "1:254"),
    (False, "close_windows", "WINDOW_SWITCH", "2"),
    (True, "close_windows", "RMT_WINDOW", "2:254"),
    (False, "open_windows", "WINDOW_SWITCH", "3"),
    (True, "open_windows", "RMT_WINDOW", "3:254"),
])
def test_window_codes_follow_field_report(v3, action, command, value):
    path, body = compile_command(action, {}, v3=v3)
    assert path == (V3_PATH if v3 else LEGACY_PATH)
    assert body == {"commandType": command, "commandValue": value,
                    "duration": 0, "operateDevice": "ANDROID"}


@pytest.mark.parametrize("action,params,v3", [
    ("charge_target", {"percent": 81}, False), ("charge_target", {"percent": True}, True),
    ("seat_heat", {"level": 4, "position": "front_left"}, True),
    ("open_trunk", {"commandValue": "injected"}, True),
    ("charge_current", {"current": "32A"}, True),
    ("ac_temperature", {"temperature": float("nan")}, True),
    ("ac_temperature", {"temperature": 24.25}, True),
    ("open_bonnet", {}, False), ("RAW_COMMAND", {}, True), ("open_trunk", {}, None),
])
def test_untraced_or_invalid_controls_are_rejected(action, params, v3):
    with pytest.raises(ControlError):
        compile_command(action, params, v3=v3)


@pytest.mark.parametrize("failure", [TransportError("PRIVATE"), KeyboardInterrupt()])
def test_unknown_outcome_blocks_restart_but_not_reads(account, failure):
    client = ready(account)
    client.set_control_enabled(True)
    account["transport"].send.side_effect = failure
    with pytest.raises((ClientFailure, KeyboardInterrupt)) as error:
        client.control(VEHICLE_ID, "open_trunk")
    assert "PRIVATE" not in str(error.value)
    assert account["transport"].send.call_count == 3
    restarted = AccountClient(account["data"], account["key"], transport_factory=account["factory"], clock=account["clock"])
    restarted.set_control_enabled(True)
    account["clock"].return_value += 5000
    with pytest.raises(ClientFailure, match="Previous control outcome"):
        restarted.control(VEHICLE_ID, "open_trunk")
    assert account["transport"].send.call_count == 3
    snapshot = restarted.update(include_telemetry=True)
    assert snapshot.control_status[VEHICLE_ID]["status"] in ("pending", "unknown")
    restarted.resume()
    with pytest.raises(ClientFailure, match="Previous control outcome"):
        restarted.control(VEHICLE_ID, "open_trunk")
    restarted.resume(review_control=True)
    account["transport"].send.side_effect = None
    restarted.control(VEHICLE_ID, "close_trunk")
    assert account["transport"].send.call_count == 4


def test_rejection_never_falls_back_or_retries(account):
    client = ready(account, v3=True)
    client.set_control_enabled(True)
    account["transport"].send.return_value = replace(response({}), body='{"resultCode":"400","message":"PRIVATE"}')
    with pytest.raises(ClientFailure, match="rejected"):
        client.control(VEHICLE_ID, "open_trunk")
    assert account["transport"].send.call_count == 3
    assert account["transport"].send.call_args.args[0].url.endswith(V3_PATH)


@pytest.mark.parametrize("problem", ["uid", "vehicle", "protocol", "fault"])
def test_missing_identity_capability_and_fault_never_send(account, problem):
    client = ready(account, unknown=problem == "protocol")
    client.set_control_enabled(True)
    if problem == "uid":
        save_session(account, user_account_id="")
    if problem == "fault":
        with key_context(account["key"]):
            vault_for_path(account["data"] / "ha-fault.imvault")._save_payload({"reason": "pending"})
    with pytest.raises(ClientFailure):
        client.control("unknown" if problem == "vehicle" else VEHICLE_ID, "open_trunk")
    assert account["transport"].send.call_count == 2


async def test_setup_requires_risk_ack_before_io_and_default_is_read_only(hass, account):
    flow = ImMotorsConfigFlow(); flow.hass = hass; flow.context = {"source": "user"}
    paths = {"data_dir": str(account["data"]), "key_file": str(account["key"])}
    form = await flow.async_step_import({**paths, "access_mode": "full"})
    assert form["errors"] == {"base": "control_risk_required"}
    form = await flow.async_step_sms({"phone": "13800000000", "access_mode": "full"})
    assert form["errors"] == {"base": "control_risk_required"}
    result = await flow.async_step_import(paths)
    assert result["data"]["access_mode"] == "read_only"
    assert not result["data"]["accept_control_risk"]
    account["transport"].send.assert_not_called()


@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_options_hide_mode_and_preserve_initial_choice(hass, account, mode):
    from custom_components.im_motors.config_flow import access_input
    import voluptuous as vol
    entry = await create_entry(hass, account, access_mode=mode)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {str(key) for key in form["data_schema"].schema}
    assert "access_mode" not in fields and "accept_control_risk" not in fields
    opposite = "full" if mode == "read_only" else "read_only"
    with pytest.raises(vol.Invalid, match="access_mode_locked"):
        access_input({"access_mode": opposite, "accept_control_risk": opposite == "full"}, entry)
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.options.async_configure(form["flow_id"], {
            "trip_history_enabled": False, "telemetry_interval_minutes": 15})
        assert result["type"] == "create_entry"
        assert control_enabled(entry) is (mode == "full")
        assert entry.data["access_mode"] == mode
        assert "access_mode" not in entry.options and "accept_control_risk" not in entry.options
    account["transport"].send.assert_not_called()


@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_reconfigure_and_reauth_cannot_change_mode(hass, account, mode):
    entry = await create_entry(hass, account, access_mode=mode)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    paths = {"data_dir": str(account["data"]), "key_file": str(account["key"])}
    opposite = "full" if mode == "read_only" else "read_only"
    for source in ("reconfigure", "reauth"):
        flow = ImMotorsConfigFlow(); flow.hass = hass
        flow.context = {"source": source, "entry_id": entry.entry_id}
        for step in (flow.async_step_sms, flow.async_step_paths if source == "reconfigure" else flow.async_step_import):
            form = await step()
            assert not {"access_mode", "accept_control_risk"} & {str(key) for key in form["data_schema"].schema}
            form = await step({**paths, "phone": "13800000000", "access_mode": opposite})
            assert form["errors"] == {"base": "access_mode_locked"}
        with patch.object(hass.config_entries, "async_reload", return_value=True):
            result = await (flow.async_step_paths(paths) if source == "reconfigure" else flow.async_step_import(paths))
            assert result["type"] == "abort"
            assert entry.data["access_mode"] == mode
            assert control_enabled(entry) is (mode == "full")
    account["transport"].send.assert_not_called()


def test_options_cannot_override_initial_or_legacy_mode():
    from types import SimpleNamespace
    for data, expected in (({}, False), ({"access_mode": "read_only"}, False),
            ({"access_mode": "full", "accept_control_risk": True}, True)):
        for options in ({"access_mode": "full", "accept_control_risk": True},
                        {"access_mode": "read_only", "accept_control_risk": False}):
            assert control_enabled(SimpleNamespace(data=data, options=options)) is expected


def test_login_uid_retained_only_in_encrypted_session(account):
    from custom_components.im_motors.pyim_china.ha_sms_login import HaSmsLogin
    from test_sms_login import PHONE, CODE, success
    sms = HaSmsLogin(account["data"], account["key"], transport_factory=account["factory"], clock=account["clock"])
    sms.prepare()
    login = json.loads(success(account).body)
    login["data"]["uid"] = "SYNTHETIC-USER-ID"
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}), response(login["data"])]
    sms.start(PHONE)
    sms.login(CODE)
    with key_context(account["key"]):
        saved = vault_for_path(account["data"] / "session.imvault")._load_payload()
    assert saved["user_account_id"] == "SYNTHETIC-USER-ID"
    assert b"SYNTHETIC-USER-ID" not in (account["data"] / "session.imvault").read_bytes()


async def test_full_entities_service_and_options_cannot_disable_mode(hass, account):
    ready(account)
    entry = await create_entry(hass, account, access_mode="full")
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    assert control_enabled(entry)
    registry = er.async_get(hass)
    controls = [e for e in registry.entities.values() if "_control_" in e.unique_id]
    assert any(e.domain == "button" and e.unique_id.endswith("open_trunk") for e in controls)
    assert any(e.domain == "number" for e in controls)
    catalog = await hass.services.async_call("im_motors", "list_controls", {"entry_id": entry.entry_id},
        blocking=True, return_response=True)
    assert catalog["control_enabled"]
    assert catalog["vehicles"][0]["vehicle_id"] == VEHICLE_ID
    assert VIN not in repr(catalog) and "SYNTHETIC" not in repr(catalog)
    result = await hass.services.async_call("im_motors", "vehicle_control", {"entry_id": entry.entry_id,
        "action": "open_trunk"}, blocking=True, return_response=True)
    assert not result["execution_confirmed"]
    assert result["status"] == "accepted"
    count = account["transport"].send.call_count
    # Conflicting options never change the mode fixed by initial setup.
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        hass.config_entries.async_update_entry(entry, options={"access_mode": "read_only"})
        await hass.async_block_till_done()
    assert entry.runtime_data.control_enabled
    assert account["client"].control_enabled
    assert account["transport"].send.call_count == count
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_read_only_has_no_control_entities_and_service_cannot_bypass(hass, account):
    ready(account)
    entry = await create_entry(hass, account)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    assert not any("_control_" in e.unique_id for e in er.async_get(hass).entities.values())
    count = account["transport"].send.call_count
    with pytest.raises(HomeAssistantError, match="只读"):
        await hass.services.async_call("im_motors", "vehicle_control", {"entry_id": entry.entry_id,
            "action": "open_trunk"}, blocking=True)
    assert account["transport"].send.call_count == count
    assert await hass.config_entries.async_unload(entry.entry_id)
