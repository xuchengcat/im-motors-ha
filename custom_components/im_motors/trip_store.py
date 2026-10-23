"""Private SQLite trip archive, independent of Recorder retention."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import calendar
import json
import os
from pathlib import Path
import sqlite3

from .trip_api import TripError


def retention_start(today):
    year, month = today.year, today.month - 2
    if month < 1:
        year, month = year - 1, month + 12
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def validate_range(start, end):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise TripError("invalid_date_range") from None
    if first.isoformat() != start or last.isoformat() != end or first > last or (last - first).days > 3660:
        raise TripError("invalid_date_range")
    return first, last


class TripStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=20000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS days (
                    vehicle TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL,
                    complete INTEGER NOT NULL, updated TEXT NOT NULL,
                    PRIMARY KEY (vehicle, day));
                CREATE TABLE IF NOT EXISTS trips (
                    vehicle TEXT NOT NULL, trip_id TEXT NOT NULL, day TEXT NOT NULL,
                    payload TEXT NOT NULL, source_present INTEGER NOT NULL DEFAULT 1,
                    updated TEXT NOT NULL, PRIMARY KEY (vehicle, trip_id));
                CREATE INDEX IF NOT EXISTS trips_day ON trips(vehicle, day);
                CREATE TABLE IF NOT EXISTS geocache (location TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples (
                    vehicle TEXT NOT NULL, source_time TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (vehicle, source_time));
                CREATE TABLE IF NOT EXISTS trip_revisions (
                    vehicle TEXT NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL,
                    record_key TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(vehicle, revision, kind, record_key));
                PRAGMA user_version=2;
            """)

    def save_day(self, vehicle, result):
        no_access = result["status"] == "no_access"
        if not result["list_complete"] and not no_access:
            raise TripError("incomplete_day")
        # One transaction per day: a failed page/detail never destroys prior data.
        updated = datetime.now().astimezone().isoformat()
        summary = {key: value for key, value in result.items() if key != "trips"}
        with self.connection() as db:
            if no_access and db.execute("SELECT 1 FROM days WHERE vehicle=? AND day=?", (vehicle, result["date"])).fetchone():
                return  # Losing remote permission never erases a prior archive.
            db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)",
                       (vehicle, result["date"], json.dumps(summary), int(result["list_complete"]), updated))
            if no_access:
                return
            db.execute("UPDATE trips SET source_present=0 WHERE vehicle=? AND day=?", (vehicle, result["date"]))
            for trip in result["trips"]:
                db.execute("INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?)",
                           (vehicle, trip["trip_id"], result["date"], json.dumps(trip), 1, updated))

    def days(self, vehicle, start, end):
        validate_range(start, end)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM days WHERE vehicle=? AND day BETWEEN ? AND ? ORDER BY day DESC", (vehicle, start, end)).fetchall()
        result = [dict(json.loads(row["payload"]), updated=row["updated"]) for row in rows]
        trips = self.trips(vehicle, start, end)
        for day in result:
            current = [trip for trip in trips if trip["date"] == day["date"] and trip["source_present"]]
            day["addresses_complete"] = day["list_complete"] and all(trip.get("start_address") and trip.get("end_address") for trip in current)
        return result

    def trips(self, vehicle, start, end):
        validate_range(start, end)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM trips WHERE vehicle=? AND day BETWEEN ? AND ? ORDER BY day DESC, trip_id", (vehicle, start, end)).fetchall()
        return [dict(json.loads(row["payload"]), date=row["day"], source_present=bool(row["source_present"]), archived_at=row["updated"]) for row in rows]

    def stats(self, vehicle):
        with self.connection() as db:
            row = db.execute("SELECT COUNT(*) AS days, MIN(day) AS first_day, MAX(day) AS last_day FROM days WHERE vehicle=?", (vehicle,)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM trips WHERE vehicle=?", (vehicle,)).fetchone()[0]
        return dict(row, trips=count)

    def state(self, key, value=None):
        with self.connection() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value)))
                return value
            row = db.execute("SELECT payload FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def location_key(point):
        return f'{point["latitude"]:.6f},{point["longitude"]:.6f}'

    def address(self, point, value=None):
        key = self.location_key(point)
        with self.connection() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO geocache VALUES (?,?)", (key, json.dumps(value)))
                return value
            row = db.execute("SELECT payload FROM geocache WHERE location=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_trip(self, vehicle, trip):
        """Merge geocoding without overwriting a concurrently confirmed endpoint."""
        with self.connection() as db:
            row = db.execute("SELECT payload FROM trips WHERE vehicle=? AND trip_id=?", (vehicle, trip["trip_id"])).fetchone()
            if row is None:
                return
            payload = json.loads(row[0])
            for prefix in ("start", "end"):
                if payload.get(prefix + "_coordinates") == trip.get(prefix + "_coordinates"):
                    for suffix in ("_address", "_poi", "_address_source"):
                        if prefix + suffix in trip:
                            payload[prefix + suffix] = trip[prefix + suffix]
            db.execute("UPDATE trips SET payload=? WHERE vehicle=? AND trip_id=?", (json.dumps(payload), vehicle, trip["trip_id"]))

    def detail(self, vehicle, trip_id):
        """Return retained observations only, scoped to this vehicle and trip."""
        from .location import gcj02_to_wgs84
        with self.connection() as db:
            row = db.execute("SELECT payload,day FROM trips WHERE vehicle=? AND trip_id=?", (vehicle, trip_id)).fetchone()
            if row is None:
                raise TripError("trip_unavailable")
            trip = dict(json.loads(row["payload"]), date=row["day"])
            def utc(value):
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    from zoneinfo import ZoneInfo
                    dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                return dt.astimezone(timezone.utc).isoformat()
            first, last = utc(trip["start_time"]), utc(trip["end_time"])
            rows = db.execute("SELECT payload FROM samples WHERE vehicle=? AND source_time>=? AND source_time<=? ORDER BY source_time", (vehicle, first, last)).fetchall()
        points = []
        for row in rows:
            point = json.loads(row[0])
            coord = point.get("coordinates")
            location = None
            if coord:
                latitude, longitude = gcj02_to_wgs84(coord["latitude"], coord["longitude"])
                location = {"latitude": latitude, "longitude": longitude}
            points.append({"time": point["time"], "received_at": point.get("received_at"),
                           "state": point.get("state"), "powered": point.get("powered"),
                           "odometer": point.get("odometer"), "coordinates": coord,
                           "map_coordinates": location})
        endpoints = []
        for prefix in ("start", "end"):
            coord = trip.get(prefix + "_coordinates")
            if coord and trip.get(prefix + "_parking_confirmed"):
                latitude, longitude = gcj02_to_wgs84(coord["latitude"], coord["longitude"])
                endpoints.append({"role": prefix, "map_coordinates": {"latitude": latitude, "longitude": longitude},
                                  "time": trip.get(prefix + "_location_sample_time")})
        return {"trip": trip, "points": points, "endpoints": endpoints, "coordinate_system": "WGS-84",
                "source_coordinate_system": "GCJ-02", "route_source": "retained_telemetry",
                "individual_sample_times_verified": False}

    def import_rows(self, vehicle, days, trips):
        updated = datetime.now().astimezone().isoformat()
        with self.connection() as db:
            # Validate the entire file first, then merge everything atomically.
            # Imports never delete or mark absent any previously archived trip.
            for day in days:
                existing = db.execute("SELECT payload FROM days WHERE vehicle=? AND day=?", (vehicle, day["date"])).fetchone()
                previous = json.loads(existing[0]) if existing else {}
                merged = dict(previous, **{k: v for k, v in day.items() if v is not None})
                db.execute("INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)", (vehicle, day["date"], json.dumps(merged), int(merged["list_complete"]), updated))
            for trip in trips:
                existing = db.execute("SELECT payload FROM trips WHERE vehicle=? AND trip_id=?", (vehicle, trip["trip_id"])).fetchone()
                previous = json.loads(existing[0]) if existing else {}
                merged = dict(previous, **{k: v for k, v in trip.items() if v is not None})
                db.execute("INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?)", (vehicle, trip["trip_id"], trip["date"], json.dumps(merged), int(trip.get("source_present", True)), updated))
        return {"days": len(days), "trips": len(trips)}
