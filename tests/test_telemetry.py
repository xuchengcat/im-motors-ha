"""Synthetic wire-to-state regressions; no live accounts or network."""
import json
from unittest.mock import Mock

import pytest

from conftest import VIN, response
from custom_components.im_motors.pyim_china.client import AccountClient, ClientFailure, LoginRequired
from custom_components.im_motors.pyim_china.offline_read_response import parse_read_response, ResponseError
from custom_components.im_motors.pyim_china.restricted_transport import RestrictedHttpsTransport, TransportError
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.telemetry import SENSORS, BINARY_SENSORS, normalized


def snapshot(category=None, *, features=()):
    return parse_read_response("vehicle_tab", json.dumps({"resultCode": "200", "data": {
        "category": {"vin": VIN, **(category or {})},
        "vehicleFunction": {"features": [{"featureKey": key} for key in features]},
        "weatherInfo": {"temperature": "26"}}}))


def values(data):
    return {key: normalized(data, spec) for key, spec in (SENSORS | BINARY_SENSORS).items()}


def test_confirmed_wire_to_state_units_positions_and_zero():
    result = values(snapshot({"isOnLine": 1, "isConnected": 0,
        "updateTime": "1791513600000",
        "period": {"originalBmsPackSOCDsp": 86.1, "cltcVehElecRng": 499,
            "frontLeftTirePressure": 244, "frontRightTirePressure": 248,
            "rearLeftTirePressure": 244, "rearRightTirePressure": 248,
            "flTireTem": 28, "frTireTem": 28, "rlTireTem": 28, "rrTireTem": 30,
            "frontLeftWindowPosition": 0, "acInCarTemperature": 43,
            "outsideCarTemperature": 31.5, "vehOdo": 34825, "chargedPower": 86.4},
        "ac": {"aclTemDspCmd": 24}, "door": {"frontLeftDoorStatus": 0},
        "lock": {"vehLockingState": 3}, "basic": {"shifterPosition": 1},
        "powerTrain": {"eptReadyStatus": 0},
        "imcuCharge": {"imcuChrgTrgtSOCDspCmd": 7, "imcuReserCtrlDspCmd": 1,
            "imcuReserStHourDspCmd": 21, "imcuReserStMinuteDspCmd": 0,
            "imcuReserSpHourDspCmd": 9, "imcuReserSpMinuteDspCmd": 0}}))
    assert [result[key + "_pressure"] for key in ("fl", "fr", "rl", "rr")] == [2.44, 2.48, 2.44, 2.48]
    assert [result[key + "_temperature"] for key in ("fl", "fr", "rl", "rr")] == [28, 28, 28, 30]
    assert result["soc"] == 86.1 and result["cltc_range"] == 499
    assert result["fl_window"] is False and result["fl_door"] is False
    assert result["online"] is True and result["connected"] is False
    assert result["lock_display"] is False and result["driving_state"] == "parking"
    assert result["weather_temperature"] == 26 and result["outside_temperature"] == 31.5
    assert result["charge_target"] == 100
    assert result["reservation_start"] == "21:00" and result["reservation_stop"] == "09:00"
    assert SENSORS["odometer_raw"].unit is None and SENSORS["charged_power_raw"].unit is None


def test_missing_null_unknown_and_invalid_never_default_to_zero():
    result = values(snapshot({"period": {"originalBmsPackSOCDsp": None,
        "frontLeftWindowPosition": 101, "frontLeftTirePressure": 0},
        "ac": {"aclTemDspCmd": 0}, "lock": {"vehLockingState": 99},
        "hvBattery": {"bmsChargeStatus": 99}, "imcuCharge": {"imcuReserCtrlDspCmd": 99}}))
    for key in ("soc", "fl_window", "fl_pressure", "rl_pressure", "fl_temperature",
                "ac_left_temperature", "lock_display", "charge_status", "reservation",
                "charge_target", "fl_SeatHeatLvl", "rr_door", "driving_state"):
        assert result[key] is None, key
    assert result["charge_status_raw"] == 99


@pytest.mark.parametrize("status", range(17))
def test_charging_duration_power_gated_by_explicit_charging_modes(status):
    result = values(snapshot({"hvBattery": {"bmsChargeStatus": status},
        "period": {"chargingRemainTime": 2, "chrgngSpdngTime": 311, "power": 0}}))
    active = status in (1, 10, 12, 16)
    assert result["charge_remaining"] == (2 if active else None)
    assert result["charge_elapsed"] == (311 if active else None)
    assert result["charge_power"] == (0 if active else None)
    assert result["charge_remaining_raw"] == 2 and result["charge_elapsed_raw"] == 311
    assert result["charge_status"] is not None


def test_estimating_target_modes_and_hidden_tyre():
    result = values(snapshot({"hvBattery": {"bmsChargeStatus": 10},
        "period": {"chargingRemainTime": 1023, "frontLeftTirePressure": 244},
        "warning": {"frontLeftTireStatus": 1},
        "imcuCharge": {"imcuChrgTrgtSOCDspCmd": 85}}, features=("support_3.0",)))
    assert result["charge_remaining"] is None
    assert result["charge_target"] == 85 and result["fl_pressure"] is None
    data = parse_read_response("vehicle_tab", json.dumps({"resultCode": "200", "data": {
        "category": {"vin": VIN, "imcuCharge": {"imcuChrgTrgtSOCDspCmd": 7}}}}))
    assert normalized(data, SENSORS["charge_target"]) is None


def test_cache_restart_interval_and_owned_request_scope(account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]),
        response({"category": {"vin": VIN, "period": {"originalBmsPackSOCDsp": 0}}})]
    first = account["client"].update(include_telemetry=True)
    assert values(first.telemetry[first.vehicles[0].identifier])["soc"] == 0
    requests = [call.args[0] for call in account["transport"].send.call_args_list]
    assert "/v6/tab/vehicle?sourceCode=APP&vin=" in requests[1].url
    assert "userLatitude" not in requests[1].url and "userLongitude" not in requests[1].url
    assert account["factory"].call_args.kwargs == {"enable_vehicle_tab": True, "allowed_vin": VIN}
    client = AccountClient(account["data"], account["key"], clock=account["clock"], transport_factory=account["factory"])
    account["clock"].return_value += 299999
    assert client.update(include_telemetry=True).telemetry == first.telemetry
    assert account["transport"].send.call_count == 2
    account["clock"].return_value += 1
    account["transport"].send.side_effect = [response({"category": {"vin": VIN}})]
    client.update(include_telemetry=True)
    assert account["transport"].send.call_count == 3
    for filename in ("ha-metadata.imvault", "ha-telemetry.imvault", "ha-telemetry-response.imvault"):
        assert VIN.encode() not in (account["data"] / filename).read_bytes()


@pytest.mark.parametrize("category", [{}, {"vin": None}, {"vin": "LSY00000000000002"}])
def test_mismatched_or_missing_response_vin_stops_durably(account, category):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": category})]
    with pytest.raises(ClientFailure):
        account["client"].update(include_telemetry=True)
    with pytest.raises(ClientFailure):
        account["client"].update(include_telemetry=True)
    assert account["transport"].send.call_count == 2
    assert (account["data"] / "ha-telemetry-response.imvault").exists()


def test_tab_token_expired_never_reauthenticates_automatically(account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]),
        response({"category": {"vin": VIN}})]
    account["client"].update(include_telemetry=True)
    account["clock"].return_value += 300000
    from custom_components.im_motors.pyim_china.restricted_transport import HttpResponse
    account["transport"].send.side_effect = [HttpResponse(200, {}, '{"resultCode":"22061"}')]
    with pytest.raises(LoginRequired):
        account["client"].update(include_telemetry=True)
    with pytest.raises(ClientFailure):
        account["client"].update(include_telemetry=True)
    assert account["transport"].send.call_count == 3


def test_crash_pending_prevents_poll_retry(account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), KeyboardInterrupt]
    with pytest.raises(KeyboardInterrupt):
        account["client"].update(include_telemetry=True)
    with pytest.raises(ClientFailure):
        account["client"].update(include_telemetry=True)
    assert account["transport"].send.call_count == 2


def test_vehicle_tab_gate_does_not_enable_refresh_or_location(account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    account["client"].update(include_telemetry=True)
    request = account["transport"].send.call_args.args[0]
    gate = RestrictedHttpsTransport(enable_vehicle_tab=True, allowed_vin=VIN)
    gate._validate(request)
    with pytest.raises(TransportError):
        RestrictedHttpsTransport(enable_vehicle_reads=True, allowed_vin=VIN)._validate(request)
    from dataclasses import replace
    for url in (request.url + "&userLatitude=0", request.url.replace(VIN, "LSY00000000000002"),
                "https://mh.immotors.com/app/vus/v5/tab/vehicle?vin=" + VIN + "&operateDevice=ANDROID"):
        with pytest.raises(TransportError):
            gate._validate(replace(request, url=url))


def test_telemetry_removed_vehicle_discards_previous_cache(account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    account["client"].update(include_telemetry=True)
    account["clock"].return_value += 1800000
    account["transport"].send.side_effect = [response([])]
    result = account["client"].update(include_telemetry=True)
    assert result.vehicles == () and dict(result.telemetry) == {}
    assert account["transport"].send.call_count == 3


def test_multi_vehicle_mappings_are_independent(account):
    second = "LSY00000000000002"
    account["transport"].send.side_effect = [response([{"vin": VIN}, {"vin": second}]),
        response({"category": {"vin": VIN, "period": {"originalBmsPackSOCDsp": 25}}}),
        response({"category": {"vin": second, "period": {"originalBmsPackSOCDsp": 75}}})]
    result = account["client"].update(include_telemetry=True)
    assert [values(result.telemetry[v.identifier])["soc"] for v in result.vehicles] == [25, 75]
    requests = [call.args[0] for call in account["transport"].send.call_args_list[1:]]
    assert VIN in requests[0].url and second in requests[1].url
    assert requests[0].headers["x-nonce"] != requests[1].headers["x-nonce"]


def test_old_metadata_cache_upgrades_without_replacing_device_identity(account):
    first = account["client"].update()
    with key_context(account["key"]):
        cache = vault_for_path(account["data"] / "ha-metadata.imvault")
        saved = cache._load_payload()
        saved.pop("vins")
        cache._save_payload(saved)
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    updated = account["client"].update(include_telemetry=True)
    assert updated.vehicles[0].identifier == first.vehicles[0].identifier
    assert account["transport"].send.call_count == 3


@pytest.mark.parametrize("code", ["22905", "22908"])
def test_vehicle_pending_business_codes_are_not_auth_expiry(account, code):
    from custom_components.im_motors.pyim_china.restricted_transport import HttpResponse
    account["transport"].send.side_effect = [response([{"vin": VIN}]),
        HttpResponse(200, {}, json.dumps({"resultCode": code}))]
    with pytest.raises(ClientFailure) as error:
        account["client"].update(include_telemetry=True)
    assert not isinstance(error.value, LoginRequired)
    with pytest.raises(ClientFailure):
        account["client"].update(include_telemetry=True)
    assert account["transport"].send.call_count == 2
