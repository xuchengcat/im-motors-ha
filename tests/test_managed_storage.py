"""New installation needs only phone/code; private keys never ship with code."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import zipfile

import pytest

from conftest import response
from test_sms_login import PHONE, CODE, success
from custom_components.im_motors.pyim_china import AccountClient
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path, VaultError
from custom_components.im_motors.pyim_china.ha_sms_login import HaSmsLogin
from custom_components.im_motors.pyim_china.local_production_config import load_local_production_config
from custom_components.im_motors.pyim_china.managed_storage import managed_paths
from custom_components.im_motors.pyim_china.portable_store import load_key_file
from homeassistant.config_entries import ConfigEntryState


def test_common_config_works_without_vault_and_legacy_override_is_preserved(account):
    path = account["data"] / "production.imvault"
    with key_context(account["key"]):
        assert load_local_production_config(path).encrypt_key == "SYNTHETIC-KEY"
        path.unlink()
        default = load_local_production_config(path)
        assert default.base_url.rstrip("/") == "https://mh.immotors.com"
        assert default.app_secret and default.encrypt_key
        path.write_bytes(b"BROKEN-VAULT")
        with pytest.raises(VaultError):
            load_local_production_config(path)


def test_local_keys_and_phone_directories_are_stable_and_private(tmp_path):
    root = tmp_path / "ha" / ".storage" / "im_motors"
    first = managed_paths(root, PHONE)
    key = load_key_file(first["key_file"])
    assert len(key) == 32
    assert managed_paths(root, PHONE) == first
    assert load_key_file(first["key_file"]) == key
    assert managed_paths(root, "13900000000")["data_dir"] != first["data_dir"]
    assert PHONE not in str(first)
    other = managed_paths(tmp_path / "other-ha" / ".storage" / "im_motors", PHONE)
    assert load_key_file(other["key_file"]) != key
    assert Path(other["data_dir"]).name != Path(first["data_dir"]).name
    if os.name == "posix":
        assert Path(first["key_file"]).stat().st_mode & 0o777 == 0o600
        assert Path(first["data_dir"]).stat().st_mode & 0o777 == 0o700
    assert not (Path(first["data_dir"]) / "production.imvault").exists()


@pytest.mark.parametrize("damage", ["missing_key", "wrong_key", "partial_key", "missing_marker", "corrupt_marker"])
def test_key_damage_does_not_silently_create_another_account(tmp_path, damage):
    root = tmp_path / "ha"
    paths = managed_paths(root, PHONE)
    key = Path(paths["key_file"])
    original = key.read_bytes()
    marker = root / "storage.imvault"
    original_marker = marker.read_bytes()
    directories = set((root / "accounts").iterdir())
    if damage == "missing_key":
        key.unlink()
    elif damage == "wrong_key":
        key.write_bytes(b"x" * 32)
    elif damage == "partial_key":
        key.write_bytes(b"x")
    elif damage == "missing_marker":
        marker.unlink()
    else:
        marker.write_bytes(b"broken")
    with pytest.raises(VaultError):
        managed_paths(root, PHONE)
    assert set((root / "accounts").iterdir()) == directories
    if damage == "missing_key":
        assert not key.exists()
    key.write_bytes(original)
    key.chmod(0o600)
    marker.write_bytes(original_marker)
    marker.chmod(0o600)
    assert managed_paths(root, PHONE) == paths


def test_invalid_phone_does_not_create_storage(tmp_path):
    root = tmp_path / "ha"
    with pytest.raises(VaultError):
        managed_paths(root, "invalid")
    assert not root.exists()


def test_another_ha_instance_cannot_decrypt_local_session(tmp_path, account):
    paths = managed_paths(tmp_path / "ha", PHONE)
    sms = HaSmsLogin(**paths, transport_factory=account["factory"], clock=account["clock"])
    identity = sms.prepare()
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}), success(account)]
    sms.start(PHONE)
    sms.login(CODE)
    assert AccountClient(**paths, clock=account["clock"]).validate() == identity
    with key_context(paths["key_file"]):
        session = vault_for_path(Path(paths["data_dir"]) / "session.imvault")._load_payload()
    assert session["access_token"] == "SYNTHETIC-LOGIN"
    wrong_paths = managed_paths(tmp_path / "other-ha", PHONE)
    with key_context(wrong_paths["key_file"]):
        with pytest.raises(VaultError):
            vault_for_path(Path(paths["data_dir"]) / "session.imvault")._load_payload()


async def test_full_new_install_phone_only_restart_duplicate_and_reauth(hass, account):
    created = []
    def make_sms(data_dir, key_file):
        sms = HaSmsLogin(data_dir, key_file, transport_factory=account["factory"], clock=account["clock"])
        created.append(sms)
        return sms
    def make_client(data_dir, key_file):
        return AccountClient(data_dir, key_file, transport_factory=account["factory"], clock=account["clock"])
    metadata = account["transport"].send.return_value
    account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}), success(account), metadata,
        response({"category": {"vin": "LSY00000000000001"}})]
    with patch("custom_components.im_motors.config_flow.HaSmsLogin", wraps=HaSmsLogin) as constructor, \
            patch("custom_components.im_motors.pyim_china.AccountClient", side_effect=make_client):
        constructor.side_effect = make_sms
        menu = await hass.config_entries.flow.async_init("im_motors", context={"source": "user"})
        form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "sms"})
        assert {str(key) for key in form["data_schema"].schema} == {"phone", "telemetry_interval_minutes"}
        form = await hass.config_entries.flow.async_configure(form["flow_id"], {"phone": PHONE})
        assert form["step_id"] == "sms_code"
        paths = {"data_dir": str(created[0].data_dir), "key_file": str(created[0].key_file), "telemetry_interval_minutes": 60}
        initial_key = Path(paths["key_file"]).read_bytes()
        initial_device = (Path(paths["data_dir"]) / "device.imvault").read_bytes()
        hass.config_entries.flow.async_abort(form["flow_id"])
        menu = await hass.config_entries.flow.async_init("im_motors", context={"source": "user"})
        form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "sms"})
        form = await hass.config_entries.flow.async_configure(form["flow_id"], {"phone": PHONE})
        assert account["transport"].send.call_count == 1
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": CODE})
        await hass.async_block_till_done()
        entry = result["result"]
        assert entry.state is ConfigEntryState.LOADED
        assert dict(entry.data) == paths
        assert not (Path(paths["data_dir"]) / "production.imvault").exists()
        assert PHONE not in str(entry.as_dict()) and CODE not in str(entry.as_dict())
        assert account["transport"].send.call_count == 4
        # Restart uses the same key/device and cached metadata; no extra HTTP.
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert account["transport"].send.call_count == 4
        menu = await hass.config_entries.flow.async_init("im_motors", context={"source": "user"})
        form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "sms"})
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {"phone": PHONE})
        assert result["reason"] == "already_configured"
        assert account["transport"].send.call_count == 4
        account["clock"].return_value = account["now"] + 60000
        account["transport"].send.side_effect = [response({"smsStateCode": "SYNTHETIC-STATE"}), success(account), metadata,
            response({"category": {"vin": "LSY00000000000001"}})]
        menu = await hass.config_entries.flow.async_init("im_motors", context={"source": "reauth", "entry_id": entry.entry_id})
        form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "sms"})
        form = await hass.config_entries.flow.async_configure(form["flow_id"], {"phone": PHONE})
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {"code": CODE})
        await hass.async_block_till_done()
        assert result["reason"] == "reauth_successful"
        assert entry.state is ConfigEntryState.LOADED
        assert Path(paths["key_file"]).read_bytes() == initial_key
        assert (Path(paths["data_dir"]) / "device.imvault").read_bytes() == initial_device
        assert dict(entry.data) == paths
        assert account["transport"].send.call_count == 8


async def test_invalid_phone_and_storage_failure_do_not_send_sms(hass, account):
    from custom_components.im_motors.config_flow import ImMotorsConfigFlow
    flow = ImMotorsConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    root = Path(hass.config.path(".storage", "im_motors"))
    form = await flow.async_step_sms({"phone": "abc"})
    assert form["errors"] == {"phone": "invalid_phone"}
    assert not root.exists()
    paths = managed_paths(root, PHONE)
    Path(paths["key_file"]).unlink()
    form = await flow.async_step_sms({"phone": PHONE})
    assert form["errors"] == {"base": "invalid_storage"}
    assert not Path(paths["key_file"]).exists()
    account["transport"].send.assert_not_called()


@pytest.mark.parametrize("content,expected", [("common", 0), ("personal_token", 1),
    ("local_key", 1), ("encoded_local_key", 1), ("extra_phone_field", 1), ("unapproved_location", 1)])
def test_release_audit_only_allows_exact_common_protocol(account, tmp_path, content, expected):
    with key_context(account["key"]):
        protocol = vault_for_path(account["data"] / "production.imvault")._load_payload()
    if content == "extra_phone_field":
        protocol["phoneNumber"] = PHONE
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        location = "other.json" if content == "unapproved_location" else "pyim_china/protocol.json"
        bundle.writestr(location, json.dumps(protocol))
        if content == "personal_token":
            bundle.writestr("bad.json", account["session"]["access_token"])
        elif content == "local_key":
            bundle.writestr("bad.json", account["key"].read_bytes())
        elif content == "encoded_local_key":
            bundle.writestr("bad.json", base64.b64encode(account["key"].read_bytes()))
    tool = Path(__file__).resolve().parents[1] / "tools/audit_release.py"
    result = subprocess.run([sys.executable, str(tool), "--archive", str(archive),
        "--data-dir", str(account["data"]), "--key-file", str(account["key"]),
        "--allow-bundled-protocol"], capture_output=True, text=True, check=False)
    assert result.returncode == expected
    assert "SYNTHETIC-ACCESS" not in result.stdout + result.stderr


def test_bundled_protocol_has_only_common_fields_and_version_matches():
    from custom_components.im_motors.const import INTEGRATION_VERSION
    component = Path(__file__).resolve().parents[1] / "custom_components/im_motors"
    assert json.loads((component / "manifest.json").read_text())["version"] == INTEGRATION_VERSION
    common = json.loads((component / "pyim_china/protocol.json").read_text())
    assert set(common) == {"schema_version", "kind", "app_secret_b64", "encrypt_key", "base_url"}


def test_release_audit_supports_new_accounts_without_production_vault(account, tmp_path):
    (account["data"] / "production.imvault").unlink()
    root = Path(__file__).resolve().parents[1]
    archive = tmp_path / "new-account-bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(root / "custom_components/im_motors/pyim_china/protocol.json", "pyim_china/protocol.json")
    result = subprocess.run([sys.executable, str(root / "tools/audit_release.py"),
        "--archive", str(archive), "--data-dir", str(account["data"]),
        "--key-file", str(account["key"]), "--allow-bundled-protocol"],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0


@pytest.mark.parametrize("payload", ["[]", "null", json.dumps({"schema_version": 1,
    "kind": "production-config", "app_secret_b64": "U1lOVEhFVElD", "encrypt_key": "SYNTHETIC", "base_url": 7})])
def test_malformed_bundled_config_is_reported_safely(monkeypatch, payload):
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: payload)
    with pytest.raises(VaultError):
        load_local_production_config()


def test_missing_bundled_config_does_not_create_local_key(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(Path, "read_text", missing)
    root = tmp_path / "ha"
    with pytest.raises(VaultError):
        managed_paths(root, PHONE)
    assert not root.exists()
