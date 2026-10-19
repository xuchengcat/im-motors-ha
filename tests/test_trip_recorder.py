"""State transitions and durable uncertainty in independently recorded trips."""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import time

import pytest

from custom_components.im_motors.trip_recorder import TripRecorder, sample
from custom_components.im_motors.trip_store import TripStore
from test_telemetry import snapshot


@pytest.fixture
def recorder(tmp_path):
    store = TripStore(tmp_path / "trips.sqlite3")
    store.initialize()
    return TripRecorder(store, "Asia/Shanghai")


def point(minute, state="parking", odometer=100, *, hour=2):
    instant = datetime(2026, 10, 19, hour, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return {"time": instant.isoformat(), "received_at": instant.isoformat(), "state": state,
            "odometer": odometer, "coordinates": {"latitude": 31. + minute / 10000, "longitude": 121., "source": "app_amap"}}


def trips(recorder):
    return recorder.store.trips("v", "2026-10-01", "2026-10-31")


def test_state_trip_uses_last_parked_odometer_and_observed_times(recorder):
    for p in [point(0), point(5, "driving", 102), point(10, "driving", 110), point(15, "parking", 112)]:
        state = recorder.observe("v", p, 5)
    trip = trips(recorder)[0]
    assert trip["distance_km"] == 12
    assert trip["duration_minutes"] == 10
    assert trip["start_coordinates"] == point(0)["coordinates"]
    assert "10:05:00" in trip["start_time"]
    assert trip["start_time_lower_bound"] == point(0)["time"]
    assert not trip["partial_start"]
    assert not state["active"]
    assert not trip["individual_sample_times_verified"]


def test_duplicate_and_out_of_order_cannot_add_trips(recorder):
    for p in [point(0), point(5, "driving", 101), point(10, "parking", 104)]:
        recorder.observe("v", p, 5)
    recorder.observe("v", point(10, "parking", 104), 5)
    state = recorder.observe("v", point(5, "driving", 101), 5)
    assert state["observation"] == "cached_or_out_of_order"
    assert len(trips(recorder)) == 1


def test_active_trip_survives_process_restart(recorder):
    recorder.observe("v", point(0), 5)
    recorder.observe("v", point(5, "driving", 101), 5)
    restarted = TripRecorder(TripStore(recorder.store.path), "Asia/Shanghai")
    restarted.observe("v", point(10, "parking", 106), 5)
    assert trips(restarted)[0]["distance_km"] == 6


def test_short_trips_missed_between_polls_are_explicit_intervals(recorder):
    recorder.observe("v", point(0), 10)
    recorder.observe("v", point(10, "parking", 108), 10)
    trip = trips(recorder)[0]
    assert trip["detection"] == "odometer_interval"
    assert trip["distance_km"] == 8
    assert trip["duration_minutes"] == 10
    assert trip["partial_start"]


def test_odometer_rollback_never_turns_into_negative_or_wrong_distance(recorder):
    for p in [point(0), point(5, "driving", 101), point(10, "driving", 20), point(15, "parking", 104)]:
        recorder.observe("v", p, 5)
    trip = trips(recorder)[0]
    assert trip["odometer_reset"]
    assert trip["distance_km"] is None
    assert recorder.store.days("v", "2026-10-19", "2026-10-19")[0]["distance_km"] is None


def test_unknown_state_does_not_close_trip_and_marks_gap(recorder):
    for p in [point(0), point(5, "driving", 101), point(10, None, 103)]:
        state = recorder.observe("v", p, 5)
    assert state["active"]
    assert not trips(recorder)
    recorder.observe("v", point(15, "parking", 108), 5)
    assert trips(recorder)[0]["sampling_gap"]


def test_long_gap_preserves_distance_but_marks_incomplete_observation(recorder):
    for p in [point(0), point(5, "driving", 101), point(120, "parking", 150)]:
        recorder.observe("v", p, 5)
    assert trips(recorder)[0]["sampling_gap"]
    assert trips(recorder)[0]["distance_km"] == 50


def test_cross_midnight_uses_ha_timezone_and_start_date_aggregation(recorder):
    for p in [point(45, hour=15), point(50, "driving", 101, hour=15), point(75, "parking", 110, hour=15)]:
        recorder.observe("v", p, 10)
    trip = trips(recorder)[0]
    assert trip["crosses_date"]
    assert trip["date"] == "2026-10-19"
    assert trip["end_time"].startswith("2026-10-20")
    day = recorder.store.days("v", "2026-10-19", "2026-10-19")[0]
    assert day["aggregation"] == "trip_start_date"
    assert not day["official_daily_total"]


def test_starting_while_already_driving_is_marked_partial(recorder):
    recorder.observe("v", point(0, "driving", 100), 5)
    recorder.observe("v", point(5, "parking", 102), 5)
    assert trips(recorder)[0]["partial_start"]


def test_separate_vehicles_and_stationary_snapshots_do_not_make_trips(recorder):
    recorder.observe("v", point(0), 5)
    recorder.observe("v", point(5), 5)
    recorder.observe("other", point(10, "driving", 1000), 5)
    assert not trips(recorder)
    assert recorder.store.state("recorder:other")["active"]


def test_missing_odometer_preserves_unknown(recorder):
    recorder.observe("v", point(0, odometer=None), 5)
    recorder.observe("v", point(5, "driving", None), 5)
    recorder.observe("v", point(10, "parking", None), 5)
    assert trips(recorder)[0]["distance_km"] is None


def test_snapshot_freshness_and_missing_times():
    now = datetime(2026, 10, 19, 2, tzinfo=timezone.utc)
    ms = int(now.timestamp() * 1000)
    reading = snapshot({"updateTime": ms, "basic": {"shifterPosition": 2},
                        "powerTrain": {"eptReadyStatus": 1}, "period": {"vehOdo": 100}})
    value, status = sample(reading, now, 5)
    assert value["state"] == "driving" and status == "fresh"
    assert sample(reading, now + timedelta(hours=1), 5) == (None, "stale_cloud_snapshot")
    assert sample(reading, now - timedelta(minutes=10), 5) == (None, "future_source_time")
    assert sample(snapshot(), now, 5) == (None, "missing_source_time")


async def test_existing_queries_feed_archive_without_additional_cloud_calls(hass, account):
    from conftest import VIN, response
    from test_ha import create_entry
    from homeassistant.config_entries import ConfigEntryState
    from hashlib import sha256
    source = account["now"] - 10 * 60 * 1000
    reading = {"state": "parking", "odo": 100, "time": source}

    def send(request):
        if "/v6/tab/vehicle?" not in request.url:
            return response([{"vin": VIN}])
        moving = reading["state"] == "driving"
        return response({"category": {"vin": VIN, "updateTime": reading["time"],
            "basic": {"shifterPosition": 2 if moving else 1},
            "powerTrain": {"eptReadyStatus": 1 if moving else 0}, "period": {"vehOdo": reading["odo"]}}})

    account["transport"].send.side_effect = send
    entry = await create_entry(hass, account, options={"trip_history_enabled": True})
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    reading.update(state="driving", odo=102, time=source + 5 * 60 * 1000)
    await entry.runtime_data.async_query_vehicle()
    reading.update(state="parking", odo=108, time=source + 10 * 60 * 1000)
    await entry.runtime_data.async_query_vehicle()
    vehicle = sha256(VIN.encode()).hexdigest()
    store = entry.runtime_data.trips.store
    stats = await hass.async_add_executor_job(store.stats, vehicle)
    assert stats["trips"] == 1
    assert account["transport"].send.call_count == 4  # Metadata plus 3 normal v6 reads.
    result = await hass.services.async_call("im_motors", "query_trip_history", {
        "entry_id": entry.entry_id, "vehicle_id": vehicle,
        "start_date": stats["first_day"], "end_date": stats["last_day"]}, blocking=True, return_response=True)
    assert result["trips"][0]["distance_km"] == 8
    assert result["trips"][0]["source"] == "ha_telemetry"
    await hass.config_entries.async_unload(entry.entry_id)
