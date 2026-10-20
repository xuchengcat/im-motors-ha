"""Find journey endpoints from persistent, spatially stable parking clusters."""
from datetime import datetime, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from .location import location_status
from .telemetry import raw, normalized, SENSORS, timestamp

ALGORITHM_VERSION = 2
PARKING_RADIUS_M = 50
DEPARTURE_RADIUS_M = 100
PARKING_SECONDS = 10 * 60


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


def distance_m(first, second):
    if not first or not second:
        return None
    a, b = math.radians(first["latitude"]), math.radians(second["latitude"])
    dlat = b - a
    dlon = math.radians(second["longitude"] - first["longitude"])
    value = math.sin(dlat / 2)**2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(value)))


def elapsed(first, last):
    return (datetime.fromisoformat(last["time"]) - datetime.fromisoformat(first["time"])).total_seconds()


def odo_changed(first, second):
    a, b = first.get("odometer"), second.get("odometer")
    return a is not None and b is not None and a != b


def odo_increased(first, second):
    a, b = first.get("odometer"), second.get("odometer")
    return a is not None and b is not None and b > a


class TripRecorder:
    def __init__(self, store, time_zone="UTC"):
        self.store = store
        self.zone = ZoneInfo(time_zone)

    @staticmethod
    def _candidate(candidate, point, active, gap):
        if not point.get("coordinates"):
            return None
        same = candidate and distance_m(candidate["first"]["coordinates"], point["coordinates"]) <= PARKING_RADIUS_M
        known = candidate.get("known_odometer") if candidate else None
        changed = known is not None and point["odometer"] is not None and known != point["odometer"]
        if not same or changed:
            return {"first": point, "last": point, "count": 1,
                    "known_odometer": point["odometer"],
                    "all_driving": point["state"] == "driving",
                    "park_hint": point["state"] in ("parking", "ready"),
                    "gap_at_arrival": bool(active and active["sampling_gap"]),
                    "confirmation_gap": False}
        candidate = dict(candidate, last=point, count=candidate["count"] + 1,
                         known_odometer=known if known is not None else point["odometer"],
                         all_driving=candidate["all_driving"] and point["state"] == "driving",
                         park_hint=candidate["park_hint"] or point["state"] in ("parking", "ready"),
                         confirmation_gap=candidate["confirmation_gap"] or gap)
        return candidate

    @staticmethod
    def _confirmed(candidate):
        if not candidate or candidate["count"] < 2:
            return False
        known_odo = candidate["first"]["odometer"] is not None and candidate["last"]["odometer"] is not None
        strong = candidate["all_driving"] or not known_odo and not candidate["park_hint"]
        return (candidate["count"] >= (3 if strong else 2)
                and elapsed(candidate["first"], candidate["last"]) >= PARKING_SECONDS * (2 if strong else 1))

    @staticmethod
    def _active(point, baseline, anchor, gap, interval=False):
        return {"start": baseline if interval else point, "baseline": baseline,
                "departure_observed_at": point["time"], "sample_count": 1,
                "partial_start": anchor is None or interval, "sampling_gap": gap,
                "odometer_reset": False, "last_odometer": point["odometer"],
                "last_driving_time": baseline["time"], "movement_observed": False,
                "max_displacement_m": 0, "start_parking_confirmed": anchor is not None,
                "interval": interval}

    def transition(self, state, point, interval_minutes):
        state = dict(state)
        previous, active = state.get("previous"), state.get("active")
        if previous and point["time"] <= previous["time"]:
            return dict(state, observation="cached_or_out_of_order"), None
        gap = bool(previous and elapsed(previous, point) > max(interval_minutes * 3 * 60, 900))
        anchor = state.get("parking_anchor")
        if active:
            active = dict(active)
            active["sample_count"] += 1
            active["sampling_gap"] |= gap or not point.get("coordinates")
            last = active.get("last_odometer")
            if last is not None and point["odometer"] is not None and point["odometer"] < last:
                active["odometer_reset"] = True
            if point["odometer"] is not None:
                active["last_odometer"] = point["odometer"]
        elif anchor:
            baseline = dict(anchor["last"], coordinates=anchor["first"]["coordinates"],
                            odometer=anchor["last"]["odometer"] if anchor["last"]["odometer"] is not None else anchor.get("odometer"))
            displacement = distance_m(anchor["first"]["coordinates"], point.get("coordinates"))
            departure = state.get("departure")
            far = displacement is not None and displacement > DEPARTURE_RADIUS_M
            evidence = odo_increased(baseline, point) or point["state"] == "driving" or departure is not None
            if odo_increased(baseline, point) or far and evidence:
                interval = point["state"] != "driving" and (not previous or previous["state"] != "driving")
                first_move = departure or point
                active = self._active(first_move, baseline, anchor, gap, interval)
                state["departure"] = None
            elif far:
                state["departure"] = point  # One isolated GPS jump is not a departure.
            else:
                state["departure"] = None
        elif point["state"] == "driving" or previous and (odo_increased(previous, point)
                or (distance_m(previous.get("coordinates"), point.get("coordinates")) or 0) > DEPARTURE_RADIUS_M):
            active = self._active(point, point, None, gap)
        if active:
            displacement = distance_m(active["baseline"].get("coordinates"), point.get("coordinates"))
            active["max_displacement_m"] = max(active["max_displacement_m"], displacement or 0)
            active["movement_observed"] |= odo_increased(active["baseline"], point) or (displacement or 0) > DEPARTURE_RADIUS_M
            if previous and (odo_increased(previous, point) or (distance_m(previous.get("coordinates"), point.get("coordinates")) or 0) > PARKING_RADIUS_M):
                active["last_driving_time"] = previous["time"]
        candidate = self._candidate(state.get("parking_candidate"), point, active, gap)
        completed = None
        if self._confirmed(candidate):
            if active and active["movement_observed"]:
                completed = self.finish(active, candidate)
            active = None
            anchor = {"first": candidate["first"], "last": candidate["last"],
                      "odometer": candidate["known_odometer"],
                      "confirmed_at": point["time"], "sample_count": candidate["count"]}
            state["departure"] = None
        if active:
            if not point.get("coordinates"):
                observation = "awaiting_location"
            elif candidate and candidate["count"] > 1 or point["state"] in ("parking", "ready"):
                observation = "confirming_parking"
            else:
                observation = "recording"
        elif anchor:
            observation = "possible_departure" if state.get("departure") else "parked"
        else:
            observation = "waiting_for_parking" if point.get("coordinates") else "awaiting_location"
        state.update(algorithm_version=ALGORITHM_VERSION, previous=point, active=active,
                     parking_anchor=anchor, parking_candidate=candidate, observation=observation)
        if completed:
            state["last_trip_id"] = completed["trip_id"]
        return state, completed

    def observe(self, vehicle, point, interval_minutes):
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM metadata WHERE key=?", ("recorder:" + vehicle,)).fetchone()
            state, completed = self.transition(json.loads(row[0]) if row else {}, point, interval_minutes)
            if state["observation"] == "cached_or_out_of_order":
                return state
            db.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?)", (vehicle, point["time"], json.dumps(point)))
            if completed:
                self._save_trip(db, vehicle, completed)
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", ("recorder:" + vehicle, json.dumps(state)))
            return state

    def finish(self, active, candidate):
        start, baseline, end = active["start"], active["baseline"], candidate["first"]
        start_dt, end_dt = datetime.fromisoformat(start["time"]), datetime.fromisoformat(end["time"])
        first, last = baseline["odometer"], end["odometer"]
        reset = active["odometer_reset"] or first is not None and last is not None and last < first
        distance = last - first if first is not None and last is not None and not reset else None
        local_start, local_end = start_dt.astimezone(self.zone), end_dt.astimezone(self.zone)
        identity = hashlib.sha256((start["time"] + "|" + end["time"]).encode()).hexdigest()
        return {"trip_id": "ha-" + identity, "date": local_start.date().isoformat(),
            "start_time": local_start.isoformat(sep=" "), "end_time": local_end.isoformat(sep=" "),
            "crosses_date": local_start.date() != local_end.date(),
            "distance_km": distance, "duration_minutes": round((end_dt - start_dt).total_seconds() / 60, 1),
            "start_coordinates": baseline.get("coordinates"), "end_coordinates": end.get("coordinates"),
            "start_address": None, "end_address": None,
            "start_time_lower_bound": baseline["time"], "start_time_upper_bound": active["departure_observed_at"],
            "end_time_lower_bound": active.get("last_driving_time", start["time"]), "end_time_upper_bound": end["time"],
            "source": "ha_telemetry", "detection": "stable_parking_interval" if active["interval"] else "stable_parking",
            "algorithm_version": ALGORITHM_VERSION, "sample_count": active["sample_count"],
            "sampling_gap": candidate["gap_at_arrival"], "partial_start": active["partial_start"],
            "start_parking_confirmed": active["start_parking_confirmed"], "end_parking_confirmed": True,
            "parking_confirmed_at": candidate["last"]["time"], "parking_confirmation_samples": candidate["count"],
            "parking_confirmation_gap": candidate["confirmation_gap"], "parking_radius_m": PARKING_RADIUS_M,
            "location_progress_unverified": bool(distance and active["max_displacement_m"] <= DEPARTURE_RADIUS_M),
            "odometer_reset": reset, "odometer_resolution_km": 1,
            "individual_sample_times_verified": False, "source_present": True}

    def rebuild_if_needed(self, vehicle, interval_minutes):
        """Replay retained samples once; retain pre-upgrade rows in revisions."""
        with self.store.connection() as db:
            old = db.execute("SELECT payload FROM metadata WHERE key=?", ("recorder:" + vehicle,)).fetchone()
            if old and json.loads(old[0]).get("algorithm_version") == ALGORITHM_VERSION:
                return False
            rows = db.execute("SELECT payload FROM samples WHERE vehicle=? ORDER BY source_time", (vehicle,)).fetchall()
            state, completed = {}, []
            for row in rows:
                state, trip = self.transition(state, json.loads(row[0]), interval_minutes)
                if trip:
                    completed.append(trip)
            state["algorithm_version"] = ALGORITHM_VERSION
            revision = "parking-v2-" + datetime.now(timezone.utc).isoformat()
            originals, touched_days = {}, set()
            first_time = datetime.fromisoformat(json.loads(rows[0][0])["time"]) if rows else None
            last_time = datetime.fromisoformat(json.loads(rows[-1][0])["time"]) if rows else None
            for row in db.execute("SELECT trip_id, payload FROM trips WHERE vehicle=?", (vehicle,)).fetchall():
                item = json.loads(row["payload"])
                start = datetime.fromisoformat(item["start_time"])
                end = datetime.fromisoformat(item["end_time"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=self.zone)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=self.zone)
                if item.get("source") == "ha_telemetry" and first_time is not None and first_time <= start <= end <= last_time:
                    originals[row["trip_id"]] = item
                    touched_days.add(start.astimezone(self.zone).date().isoformat())
                    db.execute("INSERT INTO trip_revisions VALUES (?,?,?,?,?)", (vehicle, revision, "trip", row["trip_id"], row["payload"]))
                    db.execute("DELETE FROM trips WHERE vehicle=? AND trip_id=?", (vehicle, row["trip_id"]))
            for row in db.execute("SELECT day, payload FROM days WHERE vehicle=?", (vehicle,)).fetchall():
                if row["day"] in touched_days and json.loads(row["payload"]).get("source") == "ha_telemetry":
                    db.execute("INSERT INTO trip_revisions VALUES (?,?,?,?,?)", (vehicle, revision, "day", row["day"], row["payload"]))
                    db.execute("DELETE FROM days WHERE vehicle=? AND day=?", (vehicle, row["day"]))
            if old:
                db.execute("INSERT INTO trip_revisions VALUES (?,?,?,?,?)", (vehicle, revision, "recorder", vehicle, old[0]))
            for trip in completed:
                original = originals.get(trip["trip_id"], {})
                for prefix in ("start", "end"):
                    if original.get(prefix + "_coordinates") == trip.get(prefix + "_coordinates"):
                        for suffix in ("_address", "_poi", "_address_source"):
                            if original.get(prefix + suffix):
                                trip[prefix + suffix] = original[prefix + suffix]
                self._save_trip(db, vehicle, trip)
            for day in touched_days:
                self._save_day(db, vehicle, day, datetime.now(timezone.utc).isoformat())
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", ("recorder:" + vehicle, json.dumps(state)))
            return True

    @staticmethod
    def _save_trip(db, vehicle, trip):
        updated = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT OR IGNORE INTO trips VALUES (?,?,?,?,?,?)", (vehicle, trip["trip_id"], trip["date"], json.dumps(trip), 1, updated))
        TripRecorder._save_day(db, vehicle, trip["date"], updated)

    @staticmethod
    def _save_day(db, vehicle, day, updated):
        rows = db.execute("SELECT payload FROM trips WHERE vehicle=? AND day=?", (vehicle, day)).fetchall()
        recorded = [json.loads(row[0]) for row in rows]
        own = [row for row in recorded if row.get("source") == "ha_telemetry"]
        if not own:
            return
        distances = [row["distance_km"] for row in own]
        summary = {"date": day, "status": "normal", "source": "ha_telemetry",
                   "aggregation": "trip_start_date", "distance_km": sum(distances) if all(d is not None for d in distances) else None,
                   "duration_minutes": round(sum(row["duration_minutes"] for row in own), 1),
                   "total_count": len(own), "parsed_count": len(own), "list_complete": False,
                   "addresses_complete": False, "official_daily_total": False}
        db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)", (vehicle, day, json.dumps(summary), 0, updated))
