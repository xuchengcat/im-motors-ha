"""SMS requests are mocked; exercise durable state and real HA configuration."""
import hashlib
import json
from unittest.mock import patch

import pytest

from conftest import response
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.ha_sms_login import HaSmsLogin, SmsLoginFailure
from custom_components.im_motors.pyim_china.offline_encryption import decrypt_text
from custom_components.im_motors.pyim_china.restricted_transport import HttpResponse
from custom_components.im_motors.pyim_china import ClientFailure
from custom_components.im_motors.config_flow import ImMotorsConfigFlow
from homeassistant.config_entries import ConfigEntryState

PHONE = "13800000000"
CODE = "123456"


def backend(account):
    return HaSmsLogin(account["data"], account["key"], transport_factory=account["factory"],
                      clock=account["clock"])


def success(account):
    return response({"token": "SYNTHETIC-LOGIN", "refreshToken": "SYNTHETIC-NEW-REFRESH",
        "expirationTime": str(account["now"] + 7200000),
        "suggestRefreshTime": str(account["now"] + 3600000)})


def read(account, name="ha-login.imvault"):
    with key_context(account["key"]):
        return vault_for_path(account["data"] / name)._load_payload()


def save(account, value, name="session.imvault"):
    with key_context(account["key"]):
        vault_for_path(account["data"] / name)._save_payload(value)


def setup_send(account):
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}), success(account)]


def test_fresh_sms_login_preserves_identity_and_encrypted_session(account):
    (account["data"] / "session.imvault").unlink()
    (account["data"] / "device.imvault").unlink()
    sms = backend(account)
    identity = sms.prepare()
    account["transport"].send.assert_not_called()
    setup_send(account)
    assert sms.start(PHONE) is False
    with pytest.raises(ClientFailure):
        account["client"].update()
    assert account["transport"].send.call_count == 1
    sms.login(CODE)
    assert account["client"].validate() == identity
    assert read(account)["status"] == "committed"
    session = read(account, "session.imvault")
    assert session["access_token"] == "SYNTHETIC-LOGIN"
    assert session["refresh_token"] == "SYNTHETIC-NEW-REFRESH"
    assert session["baseline_headers"] == read(account, "device.imvault")["baseline_headers"]
    assert sms.phone is None
    assert PHONE not in repr(sms)
    for file in account["data"].glob("*.imvault"):
        assert PHONE.encode() not in file.read_bytes()
        assert CODE.encode() not in file.read_bytes()
    requests = [call.args[0] for call in account["transport"].send.call_args_list]
    send = json.loads(decrypt_text(json.loads(requests[0].body)["cipherText"], "SYNTHETIC-KEY"))
    login = json.loads(decrypt_text(json.loads(requests[1].body)["cipherText"], "SYNTHETIC-KEY"))
    assert send["phoneNumber"] == PHONE
    assert login["phoneNumber"] == PHONE and login["smsCode"] == CODE
    assert login["smsStateCode"] == "SYNTHETIC-STATE"
    assert hashlib.sha256(login["deviceID"].encode()).hexdigest() == identity
    assert all(call.kwargs == {"enable_sms_login": True} for call in account["factory"].call_args_list)


@pytest.mark.parametrize("phone", ["", "1380000000", "+8613800000000", "１３８００００００００"])
def test_invalid_phone_sends_nothing(account, phone):
    with pytest.raises(SmsLoginFailure) as error:
        backend(account).start(phone)
    assert error.value.error == "invalid_phone"
    account["transport"].send.assert_not_called()


def test_missing_config_and_pending_refresh_stop_before_sms(account):
    session = dict(account["session"], pending_refresh_result="attempts/refresh.imvault")
    save(account, session)
    with pytest.raises(SmsLoginFailure) as error:
        backend(account).prepare()
    assert error.value.error == "unresolved_request"
    (account["data"] / "production.imvault").unlink()
    with pytest.raises(SmsLoginFailure) as error:
        backend(account).prepare()
    assert error.value.error == "unresolved_request"
    (account["data"] / "production.imvault").write_bytes(b"BROKEN-VAULT")
    with pytest.raises(SmsLoginFailure) as error:
        backend(account).prepare()
    assert error.value.error == "invalid_vault"
    account["transport"].send.assert_not_called()


def test_challenge_resume_and_explicit_resend(account):
    account["transport"].send.return_value = response({"smsStateCode": "SYNTHETIC-STATE"})
    first = backend(account)
    first.start(PHONE)
    resumed = backend(account)
    resumed.start(PHONE)
    assert account["transport"].send.call_count == 1
    assert first.attempt_id == resumed.attempt_id
    with pytest.raises(SmsLoginFailure) as error:
        resumed.start(PHONE, resend=True)
    assert error.value.error == "sms_cooldown"
    account["clock"].return_value = account["now"] + 60000
    resumed.start(PHONE, resend=True)
    assert account["transport"].send.call_count == 2
    assert first.attempt_id != resumed.attempt_id
    with pytest.raises(SmsLoginFailure):
        first.login(CODE)
    assert account["transport"].send.call_count == 2


def test_bad_code_and_expired_code_do_not_send_login(account):
    setup_send(account)
    sms = backend(account)
    sms.start(PHONE)
    for value in ("", "１２３４５６", "abcd", "123456789"):
        with pytest.raises(SmsLoginFailure) as error:
            sms.login(value)
        assert error.value.error == "invalid_code"
    account["clock"].return_value = account["now"] + 600000
    with pytest.raises(SmsLoginFailure) as error:
        sms.login(CODE)
    assert error.value.error == "code_expired"
    assert account["transport"].send.call_count == 1


@pytest.mark.parametrize("operation", ["send", "login"])
def test_unknown_network_result_is_never_replayed(account, operation):
    sms = backend(account)
    if operation == "login":
        account["transport"].send.return_value = response({"smsStateCode": "SYNTHETIC-STATE"})
        sms.start(PHONE)
    account["transport"].send.side_effect = OSError("SYNTHETIC-PRIVATE-ERROR")
    with pytest.raises(SmsLoginFailure) as error:
        sms.login(CODE) if operation == "login" else sms.start(PHONE)
    assert error.value.error == "unresolved_request"
    assert "PRIVATE" not in str(error.value)
    assert read(account)["status"] == "request_unknown"
    calls = account["transport"].send.call_count
    with pytest.raises(SmsLoginFailure):
        backend(account).start(PHONE)
    with pytest.raises(ClientFailure):
        account["client"].update()
    assert account["transport"].send.call_count == calls


def test_hard_interruption_leaves_attempt_marker(account):
    account["transport"].send.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        backend(account).start(PHONE)
    assert read(account)["status"] == "send_attempted"
    with pytest.raises(SmsLoginFailure):
        backend(account).start(PHONE)
    assert account["transport"].send.call_count == 1


@pytest.mark.parametrize("code,expected", [("20003", "captcha_required"),
    ("20009", "captcha_required"), ("10004", "account_binding_required"), ("21999", "sms_rejected")])
def test_send_business_errors_are_sanitized(account, code, expected):
    account["transport"].send.return_value = HttpResponse(200, {}, json.dumps(
        {"resultCode": code, "message": "SYNTHETIC-PRIVATE-ERROR", "data": None}))
    with pytest.raises(SmsLoginFailure) as error:
        backend(account).start(PHONE)
    assert error.value.error == expected
    assert read(account)["status"] == "sms_rejected"
    assert "PRIVATE" not in str(error.value)


def test_rejected_code_allows_user_correction(account):
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}),
        HttpResponse(200, {}, '{"resultCode":"21999","data":null}'), success(account)]
    sms = backend(account)
    sms.start(PHONE)
    with pytest.raises(SmsLoginFailure) as error:
        sms.login(CODE)
    assert error.value.error == "login_rejected"
    assert read(account)["status"] == "awaiting_code"
    assert account["transport"].send.call_count == 2
    sms.login("654321")
    assert account["transport"].send.call_count == 3
    assert read(account)["status"] == "committed"


def test_retained_login_response_can_be_recovered_offline(account):
    setup_send(account)
    sms = backend(account)
    sms.start(PHONE)
    with patch("custom_components.im_motors.pyim_china.ha_sms_login.parse_sms_login", side_effect=ValueError):
        with pytest.raises(SmsLoginFailure) as error:
            sms.login(CODE)
    assert error.value.error == "login_response_invalid"
    assert read(account)["login_response_body"] == success(account).body
    assert read(account)["status"] == "login_response_parse_failed"
    backend(account).recover()
    assert read(account, "session.imvault")["access_token"] == "SYNTHETIC-LOGIN"
    assert read(account)["status"] == "committed"
    assert account["transport"].send.call_count == 2


def test_retained_response_does_not_overwrite_changed_session(account):
    setup_send(account)
    sms = backend(account)
    sms.start(PHONE)
    with patch("custom_components.im_motors.pyim_china.ha_sms_login.parse_sms_login", side_effect=ValueError):
        with pytest.raises(SmsLoginFailure):
            sms.login(CODE)
    save(account, dict(account["session"], access_token="SYNTHETIC-ROTATED-LATER"))
    with pytest.raises(SmsLoginFailure):
        backend(account).recover()
    assert read(account, "session.imvault")["access_token"] == "SYNTHETIC-ROTATED-LATER"
    assert account["transport"].send.call_count == 2


def test_commit_interruption_is_idempotent_and_preserves_later_rotation(account):
    from custom_components.im_motors.pyim_china.portable_store import PortableCredentialVault
    original = PortableCredentialVault._save_payload
    def interrupt(vault, payload):
        if vault.path.name == "ha-login.imvault" and payload.get("status") == "committed":
            raise KeyboardInterrupt()
        return original(vault, payload)
    setup_send(account)
    sms = backend(account)
    sms.start(PHONE)
    with patch.object(PortableCredentialVault, "_save_payload", interrupt):
        with pytest.raises(KeyboardInterrupt):
            sms.login(CODE)
    assert read(account)["status"] == "login_response_received"
    session = read(account, "session.imvault")
    assert session["access_token"] == "SYNTHETIC-LOGIN"
    session["access_token"] = "SYNTHETIC-ROTATED-LATER"
    save(account, session)
    backend(account).recover()
    assert read(account)["status"] == "committed"
    assert read(account, "session.imvault")["access_token"] == "SYNTHETIC-ROTATED-LATER"
    assert account["transport"].send.call_count == 2


@pytest.mark.parametrize("server_code,expected", [("20003", "captcha_required"),
    ("10004", "account_binding_required")])
def test_login_extra_verification_stops_without_retry(account, server_code, expected):
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}),
        HttpResponse(200, {}, json.dumps({"resultCode": server_code, "data": None}))]
    sms = backend(account)
    sms.start(PHONE)
    with pytest.raises(SmsLoginFailure) as error:
        sms.login(CODE)
    assert error.value.error == expected
    assert read(account)["status"] == "login_rejected"
    with pytest.raises(SmsLoginFailure):
        sms.login(CODE)
    assert account["transport"].send.call_count == 2


async def test_managed_sms_flow_creates_paths_only_and_loads_entities(hass, account):
    metadata = account["transport"].send.return_value
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}),
        success(account), metadata, response({"category": {"vin": "LSY00000000000001"}})]
    sms = backend(account)
    with patch("custom_components.im_motors.config_flow.HaSmsLogin", wraps=HaSmsLogin) as constructor, \
         patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]), \
         patch("custom_components.im_motors.config_flow.managed_paths", return_value={
             "data_dir": str(account["data"]), "key_file": str(account["key"])}):
        constructor.return_value = sms
        menu = await hass.config_entries.flow.async_init("im_motors", context={"source": "user"})
        assert menu["type"] == "menu"
        form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "sms"})
        assert form["step_id"] == "sms"
        assert {str(key) for key in form["data_schema"].schema} == {"phone", "telemetry_interval_minutes"}
        form = await hass.config_entries.flow.async_configure(form["flow_id"], {"phone": PHONE, "telemetry_interval_minutes": 15})
        assert form["step_id"] == "sms_code"
        bad = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": "abc", "resend_code": False})
        assert bad["errors"] == {"code": "invalid_code"}
        assert account["transport"].send.call_count == 1
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": CODE, "resend_code": False})
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert set(entry.data) == {"data_dir", "key_file", "telemetry_interval_minutes"}
    assert entry.data["telemetry_interval_minutes"] == 15
    assert PHONE not in str(entry.as_dict()) and CODE not in str(entry.as_dict())
    assert account["transport"].send.call_count == 4


async def test_sms_reauth_retains_entry_and_device_identity(hass, account):
    from test_ha import create_entry
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    save(account, dict(account["session"], expiration_time=account["now"] - 1))
    setup_send(account)
    sms = backend(account)
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}
    with patch("custom_components.im_motors.config_flow.HaSmsLogin", wraps=HaSmsLogin) as constructor, \
            patch.object(hass.config_entries, "async_reload", return_value=True):
        constructor.return_value = sms
        menu = await flow.async_step_reauth(dict(entry.data))
        assert menu["type"] == "menu"
        form = await flow.async_step_sms(dict(entry.data, phone=PHONE))
        assert form["step_id"] == "sms_code"
        result = await flow.async_step_sms_code({"code": CODE})
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert account["client"].validate() == entry.unique_id
    assert set(entry.data) == {"data_dir", "key_file", "telemetry_interval_minutes"}


async def test_duplicate_sms_identity_aborts_before_sending(hass, account):
    from test_ha import create_entry
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    menu = await hass.config_entries.flow.async_init("im_motors", context={"source": "user"})
    form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "sms"})
    with patch("custom_components.im_motors.config_flow.managed_paths", return_value=dict(entry.data)):
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {"phone": PHONE})
    assert result["reason"] == "already_configured"
    account["transport"].send.assert_not_called()


async def test_reconfigure_can_resume_cancelled_sms_login(hass, account):
    from test_ha import create_entry
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    setup_send(account)
    backend(account).start(PHONE)  # A previous UI flow was closed before submitting the code.
    sms = backend(account)
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    with patch("custom_components.im_motors.config_flow.HaSmsLogin", wraps=HaSmsLogin) as constructor, \
            patch.object(hass.config_entries, "async_reload", return_value=True):
        constructor.return_value = sms
        menu = await flow.async_step_reconfigure()
        assert menu["menu_options"] == ["sms", "paths"]
        form = await flow.async_step_sms(dict(entry.data, phone=PHONE))
        assert form["step_id"] == "sms_code"
        assert account["transport"].send.call_count == 1
        result = await flow.async_step_sms_code({"code": CODE})
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert account["client"].validate() == entry.unique_id
    assert account["transport"].send.call_count == 2


async def test_wrong_identity_reauth_stops_before_sms(hass, account):
    from test_ha import create_entry
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    device = read(account, "device.imvault")
    headers = dict(device["baseline_headers"], deviceId="9" + "b" * 32)
    save(account, dict(device, baseline_headers=headers), "device.imvault")
    save(account, dict(account["session"], baseline_headers=headers))
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}
    result = await flow.async_step_sms(dict(entry.data, phone=PHONE))
    assert result["reason"] == "wrong_account"
    account["transport"].send.assert_not_called()
