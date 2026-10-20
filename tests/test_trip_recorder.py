"""Parking clusters, motion evidence, restart continuity and history migration."""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import json

import pytest

from custom_components.im_motors.trip_recorder import TripRecorder, sample, distance_m
from custom_components.im_motors.trip_store import TripStore
from test_telemetry import snapshot


@pytest.fixture
def recorder(tmp_path):
    store = TripStore(tmp_path / "trips.sqlite3")
    store.initialize()
    return TripRecorder(store, "Asia/Shanghai")


def point(minute, state="parking", odometer=100, *, position=0, hour=2, gps=True):
    instant = datetime(2026, 10, 19, hour, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return {"time": instant.isoformat(), "received_at": instant.isoformat(), "state": state,
            "odometer": odometer, "coordinates": {"latitude": 31. + position / 111195,
                "longitude": 121., "source": "app_amap"} if gps else None}


def anchor(recorder, *, odometer=100):
    recorder.observe("v", point(-10, odometer=odometer), 5)
    recorder.observe("v", point(0, odometer=odometer), 5)


def trips(recorder):
    return recorder.store.trips("v", "2026-10-01", "2026-10-31")


def drive_and_stop(recorder):
    for p in [point(5, "driving", 102, position=1000), point(10, "driving", 110, position=4000),
              point(15, "parking", 112, position=5000), point(25, "parking", 112, position=5000)]:
        state = recorder.observe("v", p, 5)
    return state


def test_endpoints_are_stable_parking_points_not_first_state_change(recorder):
    anchor(recorder)
    recorder.observe("v", point(5, "driving", 102, position=1000), 5)
    state = recorder.observe("v", point(15, "parking", 112, position=5000), 5)
    assert state["observation"] == "confirming_parking" and not trips(recorder)
    state = recorder.observe("v", point(25, "parking", 112, position=5000), 5)
    trip = trips(recorder)[0]
    assert trip["distance_km"] == 12 and trip["duration_minutes"] == 10
    assert trip["start_coordinates"] == point(0)["coordinates"]
    assert trip["end_coordinates"] == point(15, position=5000)["coordinates"]
    assert "10:15:00" in trip["end_time"]  # Arrival, not the later confirmation.
    assert trip["parking_confirmed_at"] == point(25)["time"]
    assert trip["start_parking_confirmed"] and trip["end_parking_confirmed"]
    assert not state["active"]


def test_previous_destination_is_reused_after_overnight_gap(recorder):
    anchor(recorder)
    drive_and_stop(recorder)
    recorder.observe("v", point(24*60, "driving", 114, position=6000), 5)
    state = recorder.store.state("recorder:v")
    assert state["active"]["baseline"]["coordinates"] == point(15, position=5000)["coordinates"]
    assert state["active"]["start_parking_confirmed"]
    assert state["active"]["sampling_gap"]


def test_parking_state_while_location_keeps_moving_cannot_finish(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 102, position=1000), point(10, "parking", 105, position=2000),
              point(15, "parking", 110, position=4000)]:
        recorder.observe("v", p, 5)
    assert not trips(recorder)
    recorder.observe("v", point(25, "parking", 110, position=4000), 5)
    assert "10:15:00" in trips(recorder)[0]["end_time"]


def test_brief_stop_followed_by_motion_stays_one_trip(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 102, position=1000), point(10, "parking", 105, position=2000),
              point(15, "driving", 108, position=4000), point(20, "parking", 110, position=5000),
              point(30, "parking", 110, position=5000)]:
        recorder.observe("v", p, 5)
    assert len(trips(recorder)) == 1
    assert "10:20:00" in trips(recorder)[0]["end_time"]


def test_gear_driving_does_not_override_long_stable_location(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 102, position=1000), point(15, "driving", 110, position=5000),
              point(25, "driving", 110, position=5000)]:
        recorder.observe("v", p, 5)
    assert not trips(recorder)  # Stronger confirmation when gear still says driving.
    recorder.observe("v", point(35, "driving", 110, position=5000), 5)
    assert trips(recorder)[0]["parking_confirmation_samples"] == 3
    assert "10:15:00" in trips(recorder)[0]["end_time"]


def test_frozen_coordinates_with_increasing_odometer_are_not_parking(recorder):
    anchor(recorder)
    recorder.observe("v", point(5, "driving", 102), 5)
    recorder.observe("v", point(15, "driving", 110), 5)
    assert not trips(recorder)
    recorder.observe("v", point(25, "parking", 110), 5)
    trip = trips(recorder)[0]
    assert trip["location_progress_unverified"] and trip["distance_km"] == 10


def test_gps_jitter_and_one_outlier_do_not_move_parking_anchor(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 100, position=30), point(10, "parking", 100, position=200),
              point(15, "parking", 100, position=-10)]:
        recorder.observe("v", p, 5)
    assert not trips(recorder)
    recorder.observe("v", point(20, "driving", 103, position=1000), 5)
    baseline = recorder.store.state("recorder:v")["active"]["baseline"]
    assert baseline["coordinates"] == point(0)["coordinates"]


def test_cached_and_out_of_order_samples_cannot_confirm_parking(recorder):
    recorder.observe("v", point(0), 5)
    recorder.observe("v", dict(point(0), received_at=point(20)["time"]), 5)
    recorder.observe("v", point(-10), 5)
    assert not recorder.store.state("recorder:v").get("parking_anchor")
    with recorder.store.connection() as db:
        assert db.execute("SELECT count(*) FROM samples").fetchone()[0] == 1


def test_restart_preserves_pending_stop_and_parking_origin(recorder):
    anchor(recorder)
    recorder.observe("v", point(5, "driving", 102, position=1000), 5)
    recorder.observe("v", point(15, "parking", 112, position=5000), 5)
    restarted = TripRecorder(TripStore(recorder.store.path), "Asia/Shanghai")
    restarted.observe("v", point(25, "parking", 112, position=5000), 5)
    assert trips(restarted)[0]["distance_km"] == 12


def test_short_trips_between_two_confirmed_parking_places_are_intervals(recorder):
    anchor(recorder)
    recorder.observe("v", point(10, "parking", 108, position=5000), 10)
    assert not trips(recorder)
    recorder.observe("v", point(20, "parking", 108, position=5000), 10)
    trip = trips(recorder)[0]
    assert trip["detection"] == "stable_parking_interval"
    assert trip["distance_km"] == 8 and trip["partial_start"]


def test_missing_location_cannot_end_trip_from_gear_alone(recorder):
    anchor(recorder)
    recorder.observe("v", point(5, "driving", 102, position=1000), 5)
    state = recorder.observe("v", point(15, "parking", 110, gps=False), 5)
    assert state["active"] and state["observation"] == "awaiting_location"
    assert not trips(recorder)


def test_missing_odometer_keeps_distance_unknown(recorder):
    anchor(recorder, odometer=None)
    for p in [point(5, "driving", None, position=1000), point(15, "parking", None, position=5000),
              point(25, "parking", None, position=5000)]:
        recorder.observe("v", p, 5)
    assert trips(recorder)[0]["distance_km"] is None


def test_missing_odometer_does_not_hide_later_mileage_growth(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 102, position=1000), point(15, "driving", 110, position=5000),
              point(25, "driving", None, position=5000), point(35, "driving", 111, position=5000)]:
        state = recorder.observe("v", p, 5)
    assert state["active"] and not trips(recorder)
    assert state["parking_candidate"]["count"] == 1


def test_odometer_rollback_not_misinterpreted_as_zero_or_negative_distance(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 101, position=1000), point(10, "driving", 20, position=2000),
              point(15, "parking", 104, position=3000), point(25, "parking", 104, position=3000)]:
        recorder.observe("v", p, 5)
    assert trips(recorder)[0]["odometer_reset"]
    assert trips(recorder)[0]["distance_km"] is None


def test_gap_after_arrival_is_confirmation_gap_not_driving_time(recorder):
    anchor(recorder)
    for p in [point(5, "driving", 102, position=1000), point(15, "parking", 110, position=5000),
              point(120, None, 110, position=5000)]:
        recorder.observe("v", p, 5)
    trip = trips(recorder)[0]
    assert trip["duration_minutes"] == 10
    assert not trip["sampling_gap"] and trip["parking_confirmation_gap"]


def test_cross_midnight_keeps_first_arrival_and_start_date(recorder):
    for p in [point(30, hour=15), point(40, hour=15), point(50, "driving", 102, position=1000, hour=15),
              point(75, "parking", 110, position=5000, hour=15), point(85, "parking", 110, position=5000, hour=15)]:
        recorder.observe("v", p, 10)
    trip = trips(recorder)[0]
    assert trip["crosses_date"] and trip["date"] == "2026-10-19"
    assert trip["end_time"].startswith("2026-10-20 00:15")


def test_partial_start_never_invents_a_confirmed_origin(recorder):
    for p in [point(0, "driving", 100), point(5, "driving", 102, position=1000),
              point(15, "parking", 110, position=5000), point(25, "parking", 110, position=5000)]:
        recorder.observe("v", p, 5)
    assert trips(recorder)[0]["partial_start"]
    assert not trips(recorder)[0]["start_parking_confirmed"]


def test_rebuild_archives_old_rows_and_runs_only_once(recorder):
    samples = [point(0, "driving", 100), point(5, "driving", 102, position=1000),
               point(15, "parking", 110, position=5000), point(25, None, 110, position=5000)]
    old_trip = {"trip_id": "old-trip", "source": "ha_telemetry", "start_time": samples[0]["time"],
                "end_time": samples[2]["time"], "distance_km": 10, "duration_minutes": 15}
    with recorder.store.connection() as db:
        for p in samples:
            db.execute("INSERT INTO samples VALUES (?,?,?)", ("v", p["time"], json.dumps(p)))
        db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?)", ("v", "old-trip", "2026-10-19", json.dumps(old_trip), 1, samples[-1]["time"]))
        db.execute("INSERT INTO metadata VALUES (?,?)", ("recorder:v", json.dumps({"previous": samples[-1]})))
    assert recorder.rebuild_if_needed("v", 5)
    assert trips(recorder)[0]["algorithm_version"] == 2
    assert recorder.store.state("recorder:v")["parking_anchor"]
    assert not recorder.rebuild_if_needed("v", 5)
    with recorder.store.connection() as db:
        assert db.execute("SELECT count(*) FROM samples").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM trip_revisions WHERE kind='trip'").fetchone()[0] == 1


def test_rebuild_does_not_erase_imported_or_uncovered_history(recorder):
    imported = {"trip_id": "imported", "source": "ha_telemetry", "start_time": point(0)["time"], "end_time": point(5)["time"]}
    with recorder.store.connection() as db:
        db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?)", ("v", "imported", "2026-10-19", json.dumps(imported), 1, point(5)["time"]))
    recorder.rebuild_if_needed("v", 5)
    assert trips(recorder)[0]["trip_id"] == "imported"


def test_rebuild_recounts_uncovered_trips_when_covered_trip_is_pending(recorder):
    with recorder.store.connection() as db:
        for identity, first, last, mileage in [("uncovered", -20, -10, 2), ("covered", 0, 5, 8)]:
            trip = {"trip_id": identity, "source": "ha_telemetry", "date": "2026-10-19",
                    "start_time": point(first)["time"], "end_time": point(last)["time"],
                    "distance_km": mileage, "duration_minutes": last-first}
            recorder._save_trip(db, "v", trip)
        for p in [point(0, "driving", 100), point(5, "parking", 108, position=5000)]:
            db.execute("INSERT INTO samples VALUES (?,?,?)", ("v", p["time"], json.dumps(p)))
    recorder.rebuild_if_needed("v", 5)
    assert len(trips(recorder)) == 1
    day = recorder.store.days("v", "2026-10-19", "2026-10-19")[0]
    assert day["total_count"] == 1 and day["distance_km"] == 2


def test_snapshot_freshness_and_missing_times():
    now = datetime(2026, 10, 19, 2, tzinfo=timezone.utc)
    reading = snapshot({"updateTime": int(now.timestamp()*1000), "basic": {"shifterPosition": 2},
                        "powerTrain": {"eptReadyStatus": 1}, "period": {"vehOdo": 100}})
    assert sample(reading, now, 5)[0]["state"] == "driving"
    assert sample(reading, now+timedelta(hours=1), 5) == (None, "stale_cloud_snapshot")
    assert sample(reading, now-timedelta(minutes=10), 5) == (None, "future_source_time")
    assert sample(snapshot(), now, 5) == (None, "missing_source_time")


async def test_existing_queries_feed_archive_without_additional_cloud_calls(hass, account):
    from conftest import VIN, response
    from test_ha import create_entry
    from homeassistant.config_entries import ConfigEntryState
    from hashlib import sha256
    source = account["now"]-26*60*1000
    reading = {"state": "parking", "odo": 100, "minute": 0, "position": 0}
    def send(request):
        if "/v6/tab/vehicle?" not in request.url:
            return response([{"vin": VIN}])
        moving = reading["state"] == "driving"
        return response({"category": {"vin": VIN, "updateTime": source+reading["minute"]*60000,
            "basic": {"shifterPosition": 2 if moving else 1}, "powerTrain": {"eptReadyStatus": 1 if moving else 0},
            "period": {"vehOdo": reading["odo"], "latitude": str(31+reading["position"]/111195),
                       "longitude": "121", "coOdntSysFmt": 0}}})
    account["transport"].send.side_effect = send
    entry = await create_entry(hass, account, options={"trip_history_enabled": True})
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    for state, odo, minute, position in [("parking",100,10,0),("driving",102,15,1000),
                                       ("parking",108,20,5000),("parking",108,30,5000)]:
        reading.update(state=state,odo=odo,minute=minute,position=position)
        await entry.runtime_data.async_query_vehicle()
    vehicle = sha256(VIN.encode()).hexdigest()
    stats = await hass.async_add_executor_job(entry.runtime_data.trips.store.stats, vehicle)
    assert stats["trips"] == 1 and account["transport"].send.call_count == 6
    result = await hass.services.async_call("im_motors", "query_trip_history", {"entry_id": entry.entry_id,
        "vehicle_id": vehicle, "start_date": stats["first_day"], "end_date": stats["last_day"]}, blocking=True, return_response=True)
    assert result["trips"][0]["distance_km"] == 8
    assert result["trips"][0]["start_parking_confirmed"]
    await hass.config_entries.async_unload(entry.entry_id)
