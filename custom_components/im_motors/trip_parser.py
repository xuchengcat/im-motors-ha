"""Offline parsing of driving-calendar responses. Does not access an account.

Input JSON: {"date": "YYYY-MM-DD", "summary": <response>,
             "pages": [<response>, ...], "details": {<tripId>: <response>}}.
All responses must be decrypted JSON envelopes with resultCode == "200".
"""
import argparse
from datetime import date, datetime
import json
import math
from pathlib import Path


H5_BASE = "https://m.immotors.com/app/planet/api/v1/drive"
DETAIL_PATH = "/app/vus/v3/drive/daily/trip/detail"
PAGE_SIZE = 5


def request_plan(day, page=1):
    """Return verified paths/queries, not signed or authenticated requests."""
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("date must be YYYY-MM-DD")
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive integer")
    return {
        "summary": {"method": "GET", "url": H5_BASE + "/daily/new",
                    "query": {"tripDate": day}},
        "trips": {"method": "GET", "url": H5_BASE + "/daily/trips",
                  "query": {"tripDate": day, "offset": page, "limit": PAGE_SIZE}},
    }


def response_data(envelope):
    if not isinstance(envelope, dict) or envelope.get("resultCode") != "200":
        raise ValueError("expected a successful decrypted response (resultCode=200)")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise ValueError("response data must be an object")
    return data


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def timestamp(value):
    # Calendar list supplies local date-time strings; preserve the entire date.
    # Do not assign a timezone or interpret undocumented numeric timestamps.
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat(sep=" ") if " " in value or "T" in value else None


def coordinates(data, prefix):
    """Keep App coordinates; the native screen feeds them directly to AMap."""
    values = []
    for suffix, maximum in (("Lat", 90), ("Lon", 180)):
        raw = data.get(prefix + suffix)
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            return None
        try:
            value = float(raw)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(value) or not -maximum <= value <= maximum or value == 0:
            return None
        values.append(value)
    return {"latitude": values[0], "longitude": values[1], "source": "app_amap"}


def detail_query(trip):
    """Native detail query uses epoch milliseconds carried by the deep link."""
    from zoneinfo import ZoneInfo
    result = {"tripId": trip["trip_id"]}
    for source, key in (("start_time", "tripStartTime"), ("end_time", "tripEndTime")):
        if not trip.get(source):
            raise ValueError("detail query requires complete trip timestamps")
        value = datetime.fromisoformat(trip[source])
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        result[key] = str(int(value.timestamp() * 1000))
    return result


def parse_day(day, summary, pages, details=None):
    request_plan(day)  # Validate date independently of response contents.
    data = response_data(summary)
    details = details or {}
    if not isinstance(pages, list) or not isinstance(details, dict):
        raise ValueError("pages must be a list and details must be an object")
    status = {1: "normal", 2: "no_trips", 3: "no_access"}.get(data.get("tripStatus"), "unknown")
    trips, seen, counts = [], set(), []
    for envelope in pages:
        page = response_data(envelope)
        total = page.get("totalCount")
        if type(total) is not int or total < 0:
            raise ValueError("totalCount must be a non-negative integer")
        counts.append(total)
        blocks = page.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError("blocks must be a list")
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("trip must be an object")
            trip_id = block.get("tripId")
            if not isinstance(trip_id, str) or not trip_id:
                raise ValueError("tripId must be a non-empty string")
            if trip_id in seen:
                continue
            seen.add(trip_id)
            detail = response_data(details[trip_id]) if trip_id in details else {}
            # The details map must be keyed by the ID used in its request.
            if "tripId" in detail and detail["tripId"] != trip_id:
                raise ValueError("detail tripId does not match list tripId")
            start = timestamp(block.get("tripStartTime"))
            end = timestamp(block.get("tripEndTime"))
            start_address = detail.get("startTripAddress")
            end_address = detail.get("endTripAddress")
            trips.append({
                "trip_id": trip_id,
                "start_time": start,
                "end_time": end,
                "crosses_date": start[:10] != end[:10] if start and end else None,
                "distance_km": number(block.get("tripMileage")),
                "duration_minutes": number(block.get("tripDuration")),
                "start_address": start_address if isinstance(start_address, str) and start_address.strip() else None,
                "end_address": end_address if isinstance(end_address, str) and end_address.strip() else None,
                "start_coordinates": coordinates(detail, "tripStart"),
                "end_coordinates": coordinates(detail, "tripEnd"),
                "detail_loaded": trip_id in details,
                "trip_type_raw": block.get("tripType"),
            })
    total = counts[0] if counts else None
    counts_consistent = len(set(counts)) <= 1
    complete = total is not None and counts_consistent and len(trips) == total
    return {
        "date": day,
        "status": status,
        "service_status_raw": data.get("serviceStatus"),
        "distance_km": number(data.get("dayMileage")),
        "duration_minutes": number(data.get("dailyMileageDuration")),
        "average_speed_kmh": number(data.get("averageVelocity")),
        "available_dates_raw": data.get("hasTripDates"),
        "total_count": total,
        "parsed_count": len(trips),
        "list_complete": complete,
        "page_counts_consistent": counts_consistent,
        "addresses_complete": complete and all(t["start_address"] and t["end_address"] for t in trips),
        "trips": trips,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    bundle = json.loads(args.input.read_text(encoding="utf-8"))
    result = parse_day(bundle["date"], bundle["summary"], bundle["pages"], bundle.get("details"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
