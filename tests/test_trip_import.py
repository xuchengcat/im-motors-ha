import json
import pytest

from custom_components.im_motors.trip_import import parse_import
from custom_components.im_motors.trip_api import TripError
from custom_components.im_motors.trip_store import TripStore


def bundle():
    return {"days": [{"date": "2026-10-18", "distance_km": 12.4}], "trips": [
        {"date": "2026-10-18", "start_time": "2026-10-18 23:40:00", "end_time": "2026-10-19 00:20:00",
         "distance_km": 12.3, "duration_minutes": 40, "start_address": "测试起点", "end_address": "测试终点"}]}


def test_import_idempotent_and_preserves_independent_day_total(tmp_path):
    store = TripStore(tmp_path / "history.sqlite3")
    store.initialize()
    days, trips = parse_import(json.dumps(bundle()))
    store.import_rows("v", days, trips)
    store.import_rows("v", days, trips)
    assert store.stats("v")["trips"] == 1
    assert store.days("v", "2026-10-18", "2026-10-18")[0]["distance_km"] == 12.4
    assert store.trips("v", "2026-10-18", "2026-10-18")[0]["crosses_date"]


def test_csv_no_trip_day_and_cross_midnight():
    content = "date,daily_distance_km,start_time,end_time,distance_km\n2026-10-18,0,,,\n2026-10-19,12.3,2026-10-19 23:50:00,2026-10-20 00:10:00,12.3\n"
    days, trips = parse_import(content, "csv")
    assert len(days) == 2 and len(trips) == 1
    assert trips[0]["crosses_date"]
    assert not days[0]["list_complete"]  # No claim that every phone page was captured.


@pytest.mark.parametrize("field,value", [("start_time", "2026-10-18"), ("end_time", "2026-10-18 10:00:00"),
    ("distance_km", -1), ("distance_km", True), ("distance_km", "NaN"), ("date", "wrong-date")])
def test_bad_import_fails_before_database_write(field, value):
    data = bundle()
    data["trips"][0][field] = value
    with pytest.raises(TripError):
        parse_import(json.dumps(data))


def test_query_export_can_be_imported_with_archived_records(tmp_path):
    data = bundle()
    data["days"][0].update(total_count=0, list_complete=True)
    data["trips"][0]["source_present"] = False
    days, trips = parse_import(json.dumps(data))
    store = TripStore(tmp_path / "history.sqlite3")
    store.initialize()
    store.import_rows("v", days, trips)
    assert not store.trips("v", "2026-10-18", "2026-10-18")[0]["source_present"]


def test_self_recorded_export_keeps_detection_and_uncertainty():
    data = bundle()
    data["days"][0].update(source="ha_telemetry")
    data["trips"][0].update(source="ha_telemetry", detection="odometer_interval", sampling_gap=True, partial_start=True)
    days, trips = parse_import(json.dumps(data))
    assert days[0]["source"] == "ha_telemetry"
    assert trips[0]["detection"] == "odometer_interval"
    assert trips[0]["sampling_gap"] and trips[0]["partial_start"]
