"""Archive durability, complete pagination, account scope, UI configuration."""
import asyncio
from datetime import date
import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.im_motors.trip_api import TripError, AmapGeocoder
from custom_components.im_motors.trip_parser import parse_day
from custom_components.im_motors.trip_store import TripStore, retention_start
from custom_components.im_motors.trips import TripManager, settings
from custom_components.im_motors.pyim_china.offline_encryption import encrypt_text
from test_ha import create_entry
from conftest import VIN


def envelope(data):
    return {"resultCode": "200", "data": data}


def row(index=1):
    return {"tripId": f"synthetic-{index}", "tripStartTime": "2026-10-18 23:40:00",
            "tripEndTime": "2026-10-19 00:20:00", "tripMileage": 12.3, "tripDuration": 40}


def parsed(blocks=None, summary=None):
    blocks = [row()] if blocks is None else blocks
    return parse_day("2026-10-18", envelope(summary or {"tripStatus": 1, "dayMileage": 12.3}),
                     [envelope({"totalCount": len(blocks), "blocks": blocks})])


@pytest.fixture
def store(tmp_path):
    value = TripStore(tmp_path / "archive" / "trips.sqlite3")
    value.initialize()
    return value


def test_archive_survives_reopen_and_cloud_deletion(store):
    result = parsed()
    store.save_day("vehicle", result)
    reopened = TripStore(store.path)
    reopened.initialize()
    assert reopened.stats("vehicle")["trips"] == 1
    reopened.save_day("vehicle", parsed([], {"tripStatus": 2, "dayMileage": 0}))
    historical = reopened.trips("vehicle", "2026-10-18", "2026-10-18")
    assert len(historical) == 1
    assert not historical[0]["source_present"]
    assert historical[0]["distance_km"] == 12.3
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_incomplete_day_does_not_overwrite_archive(store):
    store.save_day("vehicle", parsed())
    bad = parsed([row(2)])
    bad["list_complete"] = False
    with pytest.raises(TripError, match="incomplete_day"):
        store.save_day("vehicle", bad)
    assert store.trips("vehicle", "2026-10-18", "2026-10-18")[0]["trip_id"] == "synthetic-1"


def test_losing_access_does_not_overwrite_archive(store):
    store.save_day("vehicle", parsed())
    denied = parse_day("2026-10-18", envelope({"tripStatus": 3}), [])
    store.save_day("vehicle", denied)
    assert store.days("vehicle", "2026-10-18", "2026-10-18")[0]["distance_km"] == 12.3
    assert store.trips("vehicle", "2026-10-18", "2026-10-18")[0]["source_present"]


def test_address_cache_and_historical_address_completion(store):
    store.save_day("vehicle", parsed())
    point = {"latitude": 31., "longitude": 121.}
    store.address(point, {"address": "合成测试地址", "poi": "测试POI"})
    assert store.address(point)["address"] == "合成测试地址"
    trip = store.trips("vehicle", "2026-10-18", "2026-10-18")[0]
    trip.update(start_address="起点", end_address="终点")
    store.update_trip("vehicle", trip)
    assert store.days("vehicle", "2026-10-18", "2026-10-18")[0]["addresses_complete"]
    assert not store.trips("other-vehicle", "2026-10-18", "2026-10-18")


def test_database_symlink_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("do not touch")
    link = tmp_path / "trips.sqlite3"
    link.symlink_to(target)
    with pytest.raises(OSError):
        TripStore(link).initialize()
    assert target.read_text() == "do not touch"


def test_two_calendar_month_window_and_old_history_retention(store):
    assert retention_start(date(2026, 1, 31)) == date(2025, 11, 30)
    assert retention_start(date(2026, 4, 30)) == date(2026, 2, 28)
    value = parsed()
    value["date"] = "2020-01-01"
    store.save_day("vehicle", value)
    assert store.days("vehicle", "2020-01-01", "2026-10-19")






def test_amap_uses_lon_lat_and_rejects_quota_without_leaking_key():
    get = Mock(return_value=(json.dumps({"status": "1", "regeocode": {"formatted_address": "测试完整地址", "pois": [{"name": "测试POI"}]}}), {}))
    coder = AmapGeocoder("PRIVATE-KEY", get=get)
    assert coder.reverse(31., 121.) == {"address": "测试完整地址", "poi": "测试POI"}
    assert "location=121.000000%2C31.000000" in get.call_args.args[1]
    get.return_value = ('{"status":"0","info":"PRIVATE-KEY"}', {})
    with pytest.raises(TripError) as caught:
        coder.reverse(31., 121.)
    assert str(caught.value) == "amap_rejected"


@pytest.fixture
def manager(hass, account):
    vehicle = hashlib.sha256(VIN.encode()).hexdigest()
    coordinator = SimpleNamespace(client=account["client"], _request_lock=asyncio.Lock(),
        data=SimpleNamespace(vehicles=[SimpleNamespace(identifier=vehicle)]))
    entry = SimpleNamespace(options={"trip_history_enabled": True})
    result = TripManager(hass, entry, coordinator)
    return result, vehicle












async def test_options_key_encrypted_and_blank_preserves(hass, account):
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["type"] == "form"
    key = "abcd" * 8
    result = await hass.config_entries.options.async_configure(form["flow_id"],
        {"trip_history_enabled": True, "amap_api_key": key, "clear_amap_key": False})
    assert result["type"] == "create_entry"
    assert key not in repr(dict(entry.options))
    assert settings(account["client"])["amap_api_key"] == key
    assert key.encode() not in (account["data"] / "trip-settings.imvault").read_bytes()
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(form["flow_id"],
        {"trip_history_enabled": True, "amap_api_key": "", "clear_amap_key": False})
    assert result["type"] == "create_entry"
    assert settings(account["client"])["amap_api_key"] == key
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(form["flow_id"],
        {"trip_history_enabled": False, "amap_api_key": "", "clear_amap_key": True})
    assert result["type"] == "create_entry"
    assert settings(account["client"])["amap_api_key"] == ""
