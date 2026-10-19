"""Validated phone/manual imports; never requires a vehicle-owner cloud login."""
import csv
from datetime import datetime
import hashlib
import io
import json
import math

from .trip_api import TripError
from .trip_parser import parse_day, coordinates
from .trip_store import validate_range


def measurement(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TripError("invalid_import")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise TripError("invalid_import") from None
    if not math.isfinite(result) or result < 0:
        raise TripError("invalid_import")
    return result


def text(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 2000:
        raise TripError("invalid_import")
    return value.strip() or None


def parse_csv(content):
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames or not {"date", "daily_distance_km"}.issubset(reader.fieldnames):
        raise TripError("invalid_import")
    days, trips = {}, []
    for row in reader:
        if len(trips) > 10000 or len(days) > 366:
            raise TripError("import_too_large")
        day = row.get("date")
        summary = {"date": day, "distance_km": row.get("daily_distance_km"),
                   "duration_minutes": row.get("daily_duration_minutes")}
        if day in days and days[day] != summary:
            raise TripError("invalid_import")
        days[day] = summary
        if row.get("start_time") or row.get("end_time"):
            trips.append(row)
    return {"schema_version": 1, "source": "manual_csv", "days": list(days.values()), "trips": trips}


def parse_import(content, format="json"):
    if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise TripError("import_too_large")
    try:
        bundle = parse_csv(content) if format == "csv" else json.loads(content)
        if not isinstance(bundle, dict):
            raise TripError("invalid_import")
        if {"date", "summary", "pages"}.issubset(bundle):
            result = parse_day(bundle["date"], bundle["summary"], bundle["pages"], bundle.get("details"))
            bundle = {"days": [{k: v for k, v in result.items() if k != "trips"}],
                      "trips": [dict(trip, date=result["date"]) for trip in result["trips"]]}
        raw_days, raw_trips = bundle.get("days"), bundle.get("trips", [])
        if not isinstance(raw_days, list) or not raw_days or not isinstance(raw_trips, list):
            raise TripError("invalid_import")
        if len(raw_days) > 366 or len(raw_trips) > 10000:
            raise TripError("import_too_large")
        days, trips, ids = {}, [], set()
        for row in raw_days:
            day = row["date"]
            validate_range(day, day)
            if day in days:
                raise TripError("invalid_import")
            count = row.get("total_count")
            if count is not None and (type(count) is not int or count < 0):
                raise TripError("invalid_import")
            days[day] = {"date": day, "distance_km": measurement(row.get("distance_km")),
                "duration_minutes": measurement(row.get("duration_minutes")), "total_count": count,
                "status": row.get("status") if row.get("status") in ("normal", "no_trips", "no_access") else "unknown",
                "list_complete": row.get("list_complete") is True,
                "source": "ha_telemetry" if row.get("source") == "ha_telemetry" else "phone_import"}
            if row.get("source") == "ha_telemetry":
                days[day].update(aggregation="trip_start_date", official_daily_total=False)
        for row in raw_trips:
            day = row["date"]
            if day not in days:
                raise TripError("invalid_import")
            start, end = datetime.fromisoformat(row["start_time"]), datetime.fromisoformat(row["end_time"])
            if end < start or not (start.date() <= datetime.fromisoformat(day).date() <= end.date()):
                raise TripError("invalid_import")
            # A date-only value must not turn into an invented midnight trip.
            if not all(" " in row[key] or "T" in row[key] for key in ("start_time", "end_time")):
                raise TripError("invalid_import")
            distance = measurement(row.get("distance_km"))
            identity = "|".join((day, start.isoformat(), end.isoformat(), str(distance)))
            trip_id = row.get("trip_id") or "phone-" + hashlib.sha256(identity.encode()).hexdigest()
            if not isinstance(trip_id, str) or len(trip_id) > 256 or trip_id in ids:
                raise TripError("invalid_import")
            ids.add(trip_id)
            trip = {"trip_id": trip_id, "date": day, "start_time": start.isoformat(sep=" "),
                "end_time": end.isoformat(sep=" "), "crosses_date": start.date() != end.date(),
                "distance_km": distance, "duration_minutes": measurement(row.get("duration_minutes")),
                "start_address": text(row.get("start_address")), "end_address": text(row.get("end_address")),
                "source": "ha_telemetry" if row.get("source") == "ha_telemetry" else "phone_import", "detail_loaded": True,
                "source_present": row.get("source_present", True) is not False}
            for field in ("sampling_gap", "partial_start", "odometer_reset", "individual_sample_times_verified"):
                if field in row:
                    if type(row[field]) is not bool:
                        raise TripError("invalid_import")
                    trip[field] = row[field]
            for field in ("start_time_lower_bound", "start_time_upper_bound", "end_time_lower_bound", "end_time_upper_bound",
                          "start_poi", "end_poi", "start_address_source", "end_address_source"):
                if field in row:
                    trip[field] = text(row[field])
            if row.get("detection") in ("observed_state_change", "odometer_interval"):
                trip["detection"] = row["detection"]
            if row.get("source") == "ha_telemetry":
                trip["odometer_resolution_km"] = 1
            for prefix in ("start", "end"):
                point = row.get(prefix + "_coordinates")
                if point is not None:
                    if not isinstance(point, dict):
                        raise TripError("invalid_import")
                    validated = coordinates({"pLat": point.get("latitude"), "pLon": point.get("longitude")}, "p")
                    if not validated:
                        raise TripError("invalid_import")
                    trip[prefix + "_coordinates"] = validated
            trips.append(trip)
        for day, value in days.items():
            imported = sum(trip["date"] == day and trip["source_present"] for trip in trips)
            if value["list_complete"] and value["total_count"] != imported:
                raise TripError("incomplete_day")
            value["parsed_count"] = imported
        return list(days.values()), trips
    except TripError:
        raise
    except Exception:
        raise TripError("invalid_import") from None
