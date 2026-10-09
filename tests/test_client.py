"""Persistence, query boundaries, refresh rotation and crash-stop regressions."""
from pathlib import Path
import json
from unittest.mock import patch

import pytest

from conftest import VIN, response
from custom_components.im_motors.pyim_china import AccountClient, ClientFailure, LoginRequired
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.restricted_transport import TransportError
from custom_components.im_motors.pyim_china.standalone_refresh_probe import recover_refresh
from custom_components.im_motors.pyim_china.local_production_config import load_local_production_config
from custom_components.im_motors.pyim_china.offline_read_response import ResponseError


def save_session(account, **changes):
    account["session"].update(changes)
    with key_context(account["key"]):
        vault_for_path(account["data"] / "session.imvault")._save_payload(account["session"])


def test_offline_validation_and_persistent_cache(account):
    client = account["client"]
    assert len(client.validate()) == 64
    account["transport"].send.assert_not_called()
    first = client.update()
    assert first.vehicles[0].has_setting is False
    assert first.vehicles[0].has_support is True
    assert VIN not in repr(first)
    assert "SYNTHETIC-PRIVATE-NAME" not in repr(first.vehicles)
    restarted = AccountClient(account["data"], account["key"], transport_factory=account["factory"], clock=account["clock"])
    assert restarted.update() == first
    assert account["transport"].send.call_count == 1
    assert account["factory"].call_args.kwargs == {"enable_isc_vehicles": True}
    request = account["transport"].send.call_args.args[0]
    assert request.url.endswith("/app/vus/v3/isc/scene/vehicleList")
    assert "category" not in request.url
    assert VIN.encode() not in (account["data"] / "ha-metadata.imvault").read_bytes()


def test_metadata_interval_and_removed_vehicle(account):
    account["client"].update()
    account["clock"].return_value = account["now"] + 1799999
    account["client"].update()
    assert account["transport"].send.call_count == 1
    account["clock"].return_value = account["now"] + 1800000
    account["transport"].send.return_value = response([])
    assert account["client"].update().vehicles == ()
    assert account["transport"].send.call_count == 2


def test_refresh_due_rotates_and_restart_reads_new_credentials(account):
    save_session(account, suggest_refresh_time=account["now"] - 1)
    account["transport"].send.side_effect = [response({"accessToken": "SYNTHETIC-NEW",
        "refreshToken": "SYNTHETIC-ROTATED", "expirationTime": str(account["now"] + 7200000),
        "suggestRefreshTime": str(account["now"] + 3600000)}),
        response([{"vin": VIN}])]
    snapshot = account["client"].update()
    assert snapshot.expiration_time_ms == account["now"] + 7200000
    requests = [call.args[0] for call in account["transport"].send.call_args_list]
    assert requests[0].url.endswith("/refresh-token")
    assert json.loads(requests[0].body)["deviceID"] == account["session"]["baseline_headers"]["deviceId"]
    assert requests[1].headers["Authorization"] == "Bearer SYNTHETIC-NEW"
    assert len(list((account["data"] / "attempts").glob("*.imvault"))) == 1
    account["client"].update()
    assert account["transport"].send.call_count == 2


@pytest.mark.parametrize("failure", [TransportError("SYNTHETIC-SECRET"), response({}),
    response([{"vin": "bad"}]), response([{"vin": VIN}, {"vin": VIN}])])
def test_failed_metadata_is_durable_and_sanitized(account, failure):
    if isinstance(failure, Exception):
        account["transport"].send.side_effect = failure
    else:
        account["transport"].send.return_value = failure
    with pytest.raises(ClientFailure) as exc:
        account["client"].update()
    assert "SYNTHETIC" not in str(exc.value)
    restarted = AccountClient(account["data"], account["key"], transport_factory=account["factory"], clock=account["clock"])
    with pytest.raises(ClientFailure):
        restarted.update()
    assert account["transport"].send.call_count == 1
    assert (account["data"] / "ha-fault.imvault").exists()


def test_crash_before_metadata_response_blocks_restart(account):
    account["transport"].send.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        account["client"].update()
    with pytest.raises(ClientFailure):
        account["client"].update()
    assert account["transport"].send.call_count == 1


def test_expired_credentials_no_http(account):
    save_session(account, expiration_time=account["now"] - 1, suggest_refresh_time=account["now"] - 2)
    with pytest.raises(LoginRequired):
        account["client"].validate()
    with pytest.raises(LoginRequired):
        account["client"].update()
    account["transport"].send.assert_not_called()


def test_pending_refresh_blocks_resume_and_all_queries(account):
    save_session(account, pending_refresh_result="SYNTHETIC-PENDING")
    with pytest.raises(ClientFailure):
        account["client"].resume()
    with pytest.raises(ClientFailure):
        account["client"].update()
    account["transport"].send.assert_not_called()


def test_refresh_network_uncertainty_never_retries(account):
    save_session(account, suggest_refresh_time=account["now"] - 1)
    account["transport"].send.side_effect = TransportError("SYNTHETIC-UNKNOWN")
    with pytest.raises(ClientFailure):
        account["client"].update()
    with pytest.raises(ClientFailure):
        account["client"].resume()
    with pytest.raises(ClientFailure):
        account["client"].update()
    assert account["transport"].send.call_count == 1
    with key_context(account["key"]):
        state = vault_for_path(account["data"] / "session.imvault")._load_payload()
    assert state.get("pending_refresh_result")


def test_two_account_keys_do_not_share_global_environment(account, tmp_path):
    other = AccountClient(account["data"], tmp_path / "missing-key", transport_factory=account["factory"])
    with pytest.raises(ClientFailure):
        other.validate()
    assert account["client"].validate()
    account["transport"].send.assert_not_called()


def test_manual_resume_after_metadata_failure(account):
    account["transport"].send.side_effect = TransportError("SYNTHETIC")
    with pytest.raises(ClientFailure):
        account["client"].update()
    account["transport"].send.side_effect = None
    account["client"].resume()
    assert len(account["client"].update().vehicles) == 1
    assert account["transport"].send.call_count == 2


def test_missing_flags_stay_unknown(account):
    account["transport"].send.return_value = response([{"vin": VIN, "hasSupport": None}])
    vehicle = account["client"].update().vehicles[0]
    assert vehicle.has_support is None
    assert vehicle.has_setting is None


def test_retained_refresh_recovers_offline_without_http(account, capsys):
    save_session(account, suggest_refresh_time=account["now"] - 1)
    account["transport"].send.return_value = response({"accessToken": "SYNTHETIC-NEW",
        "refreshToken": "SYNTHETIC-ROTATED", "expirationTime": account["now"] + 7200000,
        "suggestRefreshTime": account["now"] + 3600000})
    module = "custom_components.im_motors.pyim_china.standalone_refresh_probe"
    with patch(module + ".parse_refresh_response", side_effect=ResponseError("Synthetic parser fault")):
        with pytest.raises(ClientFailure):
            account["client"].update()
    result = next((account["data"] / "attempts").glob("*.imvault"))
    with key_context(account["key"]):
        config = load_local_production_config(account["data"] / "production.imvault")
        updated = recover_refresh(account["data"] / "session.imvault", result, load_config=lambda: config)
    assert updated.access_token == "SYNTHETIC-NEW"
    assert account["transport"].send.call_count == 1
    assert "SYNTHETIC" not in capsys.readouterr().out
    account["client"].resume()
    account["transport"].send.return_value = response([{"vin": VIN}])
    account["client"].update()
    assert account["transport"].send.call_count == 2
