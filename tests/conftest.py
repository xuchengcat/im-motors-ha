"""Synthetic fixtures only; tests cannot use real account configuration."""
import base64
import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.im_motors.pyim_china.client import AccountClient
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.portable_store import create_key_file
from custom_components.im_motors.pyim_china.restricted_transport import HttpResponse

VIN = "LSY00000000000001"


def response(data):
    return HttpResponse(200, {}, json.dumps({"resultCode": "200", "data": data}))


@pytest.fixture
def account(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    key = tmp_path / "secrets" / "vault.key"
    create_key_file(key)
    now = time.time_ns() // 1_000_000
    headers = {"versionType": "ANDROID", "deviceType": "android", "x-brand": "IMHA",
               "x-machine-model": "StandaloneSDK", "x-system-version": "Synthetic",
               "deviceId": "9" + "a" * 32, "x-device-id": "", "versionNumber": "3.2.4", "versionCode": "353"}
    session = {"schema_version": 1, "kind": "standalone-sms-attempt",
               "status": "login_accepted_refresh_verified", "push_device_id": "",
               "baseline_headers": headers, "access_token": "SYNTHETIC-ACCESS",
               "refresh_token": "SYNTHETIC-REFRESH", "expiration_time": now + 7200000,
               "suggest_refresh_time": now + 3600000}
    with key_context(key):
        vault_for_path(data / "session.imvault")._save_payload(session)
        vault_for_path(data / "device.imvault")._save_payload(
            {"schema_version": 1, "kind": "sms-device", "baseline_headers": headers, "push_device_id": ""})
        vault_for_path(data / "production.imvault")._save_payload(
            {"schema_version": 1, "kind": "production-config",
             "app_secret_b64": base64.b64encode(b"SYNTHETIC-SECRET").decode(),
             "encrypt_key": "SYNTHETIC-KEY", "base_url": "https://mh.immotors.com/"})
    transport = Mock()
    transport.send.return_value = response([{"vin": VIN, "vehicleName": "SYNTHETIC-PRIVATE-NAME",
                                            "projectCode": "SYNTHETIC-MODEL", "hasSetting": False,
                                            "hasSupport": True}])
    factory = Mock(return_value=transport)
    clock = Mock(return_value=now)
    client = AccountClient(data, key, transport_factory=factory, clock=clock)
    return {"client": client, "data": data, "key": key, "session": session,
            "transport": transport, "factory": factory, "clock": clock, "now": now}


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    import http.client
    def blocked(*args, **kwargs):
        raise AssertionError("Live network prohibited in tests")
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", blocked)


@pytest.fixture
async def hass(tmp_path):
    from homeassistant.core import HomeAssistant
    from homeassistant.loader import async_get_integration, async_setup as loader_setup
    from homeassistant.helpers.frame import async_setup as frame_setup
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.setup import async_setup_component
    config = tmp_path / "ha_config"
    config.mkdir()
    import shutil
    shutil.copytree(ROOT / "custom_components", config / "custom_components",
                    ignore=shutil.ignore_patterns("__pycache__"))
    instance = HomeAssistant(str(config))
    loader_setup(instance)
    frame_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    await async_load_base_functionality(instance)
    await async_setup_component(instance, "persistent_notification", {})
    await async_get_integration(instance, "im_motors")
    yield instance
    await instance.async_stop(force=True)
