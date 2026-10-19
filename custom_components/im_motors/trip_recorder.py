"""Infer trips from sampled telemetry, retaining uncertainty and restart state."""
from datetime import datetime, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

from .location import location_status
from .telemetry import raw, normalized, SENSORS, timestamp


def sample(snapshot, received_at, interval_minutes):
    source = timestamp(raw(snapshot, "root", "updateTime"))
    if source is None:
        return None, "missing_source_time"
    age = (received_at - source).total_seconds()
    if age < -300:
        return None, "future_source_time"
    if age > max(interval_minutes * 3 * 60, 900):
        return None, "stale_cloud_snapshot"
    point = None
    if location_status(snapshot) == "available":
        point = {"latitude": float(snapshot.location_fields["latitude"].value),
                 "longitude": float(snapshot.location_fields["longitude"].value), "source": "app_amap"}
    return {"time": source.isoformat(), "received_at": received_at.isoformat(),
            "state": normalized(snapshot, SENSORS["driving_state"]),
            "odometer": normalized(snapshot, SENSORS["odometer"]), "coordinates": point}, "fresh"


class TripRecorder:
    def __init__(self, store, time_zone="UTC"):
        self.store = store
        self.zone = ZoneInfo(time_zone)

    def observe(self, vehicle, point, interval_minutes):
        """Persist sample, active trip and any completed segment atomically."""
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM metadata WHERE key=?", ("recorder:" + vehicle,)).fetchone()
            state = json.loads(row[0]) if row else {}
            previous, active = state.get("previous"), state.get("active")
            if previous and point["time"] <= previous["time"]:
                return dict(state, observation="cached_or_out_of_order")
            db.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?)", (vehicle, point["time"], json.dumps(point)))
            gap = False
            if previous:
                gap = (datetime.fromisoformat(point["time"]) - datetime.fromisoformat(previous["time"])).total_seconds() > max(interval_minutes * 3 * 60, 900)
            completed = None
            moving, parked = point["state"] == "driving", point["state"] in ("parking", "ready")
            if active:
                active["sample_count"] += 1
                active["sampling_gap"] |= gap or point["state"] is None
                last_odo = active.get("last_odometer")
                if last_odo is not None and point["odometer"] is not None and point["odometer"] < last_odo:
                    active["odometer_reset"] = True
                if point["odometer"] is not None:
                    active["last_odometer"] = point["odometer"]
                if moving:
                    active["last_driving_time"] = point["time"]
                if parked:
                    completed = self.finish(active, point, "observed_state_change")
                    active = None
            elif moving:
                baseline = previous if previous and previous["state"] in ("parking", "ready") and not gap else point
                active = {"start": point, "baseline": baseline, "sample_count": 1,
                          "partial_start": baseline is point, "sampling_gap": gap,
                          "odometer_reset": False, "last_odometer": point["odometer"],
                          "last_driving_time": point["time"]}
            elif parked and previous and previous["state"] in ("parking", "ready"):
                first, last = previous["odometer"], point["odometer"]
                if first is not None and last is not None and last > first:
                    # May contain several short trips: label it as an interval.
                    inferred = {"start": previous, "baseline": previous, "sample_count": 2,
                                "partial_start": True, "sampling_gap": gap, "odometer_reset": False}
                    completed = self.finish(inferred, point, "odometer_interval")
            if completed:
                self._save_trip(db, vehicle, completed)
            # Unknown state is still retained as a sample; it must not close a trip.
            state.update(previous=point, active=active, observation="recording" if active else "parked" if parked else "unknown_state")
            if completed:
                state["last_trip_id"] = completed["trip_id"]
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", ("recorder:" + vehicle, json.dumps(state)))
            return state

    def finish(self, active, end, detection):
        start, baseline = active["start"], active["baseline"]
        start_dt, end_dt = datetime.fromisoformat(start["time"]), datetime.fromisoformat(end["time"])
        first, last = baseline["odometer"], end["odometer"]
        reset = active["odometer_reset"] or first is not None and last is not None and last < first
        distance = last - first if first is not None and last is not None and not reset else None
        # Source timestamp is a cloud-root time, not a verified GPS sampling time.
        local_start, local_end = start_dt.astimezone(self.zone), end_dt.astimezone(self.zone)
        identity = hashlib.sha256((start["time"] + "|" + end["time"]).encode()).hexdigest()
        return {"trip_id": "ha-" + identity, "date": local_start.date().isoformat(),
            "start_time": local_start.isoformat(sep=" "), "end_time": local_end.isoformat(sep=" "),
            "crosses_date": local_start.date() != local_end.date(),
            "distance_km": distance, "duration_minutes": round((end_dt - start_dt).total_seconds() / 60, 1),
            "start_coordinates": baseline.get("coordinates"), "end_coordinates": end.get("coordinates"),
            "start_address": None, "end_address": None,
            "start_time_lower_bound": baseline["time"], "start_time_upper_bound": start["time"],
            "end_time_lower_bound": active.get("last_driving_time", start["time"]),
            "end_time_upper_bound": end["time"],
            "source": "ha_telemetry", "detection": detection, "sample_count": active["sample_count"],
            "sampling_gap": active["sampling_gap"], "partial_start": active["partial_start"],
            "odometer_reset": reset, "odometer_resolution_km": 1,
            "individual_sample_times_verified": False, "source_present": True}

    @staticmethod
    def _save_trip(db, vehicle, trip):
        updated = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT OR IGNORE INTO trips VALUES (?,?,?,?,?,?)", (vehicle, trip["trip_id"], trip["date"], json.dumps(trip), 1, updated))
        rows = db.execute("SELECT payload FROM trips WHERE vehicle=? AND day=?", (vehicle, trip["date"])).fetchall()
        recorded = [json.loads(row[0]) for row in rows]
        # Only self-recorded trips contribute to the derived daily total.
        own = [row for row in recorded if row.get("source") == "ha_telemetry"]
        distances = [row["distance_km"] for row in own]
        summary = {"date": trip["date"], "status": "normal", "source": "ha_telemetry",
                   "aggregation": "trip_start_date", "distance_km": sum(distances) if all(d is not None for d in distances) else None,
                   "duration_minutes": round(sum(row["duration_minutes"] for row in own), 1),
                   "total_count": len(own), "parsed_count": len(own), "list_complete": False,
                   "addresses_complete": False, "official_daily_total": False}
        db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)", (vehicle, trip["date"], json.dumps(summary), 0, updated))
