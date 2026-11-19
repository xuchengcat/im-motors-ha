"""Primary-account calendar archive, fetched explicitly from official read APIs."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

from .cloud_trip_api import TripReader
from .const import account_type, ACCOUNT_PRIMARY
from .trip_api import TripError
from .trip_parser import parse_day, detail_query, coordinates, number
from .trip_store import TripStore, validate_range
from .trips import TripManager, settings


def envelope(data):
    return {"resultCode": "200", "data": data}


def calendar_dates(values):
    """The H5 calendar uses YYYYMMDD; expose only canonical valid dates."""
    if not isinstance(values, list):
        raise TripError("invalid_response")
    result = set()
    for value in values:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise TripError("invalid_response")
        text = str(value)
        if len(text) == 8 and text.isascii() and text.isdecimal():
            text = text[:4] + "-" + text[4:6] + "-" + text[6:]
        try:
            if date.fromisoformat(text).isoformat() != text:
                raise ValueError()
        except ValueError:
            raise TripError("invalid_response") from None
        result.add(text)
    return sorted(result)


def route_points(detail):
    raw = detail.get("poiList")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 100000:
        raise TripError("invalid_response")
    result = []
    for point in raw:
        if not isinstance(point, dict):
            raise TripError("invalid_response")
        coord = coordinates({"pointLat": point.get("latitude"), "pointLon": point.get("longitude")}, "point")
        stamp = point.get("tboxTime")
        time = None
        if type(stamp) is int and 0 < stamp < 253402300800000:
            time = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
        result.append({"coordinates": coord, "time": time,
            "speed_kmh": number(point.get("instantaneousVelocity")), "type_raw": point.get("type")})
    return result


class CloudTripManager(TripManager):
    source = "official_cloud"

    def __init__(self, hass, entry, coordinator):
        super().__init__(hass, entry, coordinator)
        # Switching roles never overwrites locally recorded or imported trips.
        self.store = TripStore(coordinator.client.data_dir / "cloud-trips.sqlite3")
        self.recorder = None
        self.reader = TripReader(coordinator.client)

    @property
    def enabled(self):
        return (account_type(self.entry) == ACCOUNT_PRIMARY
                and self.entry.options.get("cloud_trip_history_enabled", False))

    async def setup(self):
        await self.hass.async_add_executor_job(self.store.initialize)
        saved = await self.hass.async_add_executor_job(settings, self.coordinator.client)
        self.key = saved.get("amap_api_key", "")
        self.status = await self.hass.async_add_executor_job(self.store.state, "sync") or {"state": "idle"}
        if self.status.get("state") == "running":
            await self.set_status(state="error", error="interrupted")

    async def observe(self, snapshot):
        """Primary accounts never collect local telemetry samples."""

    def start(self, *, geocode_only=False, start_date=None, end_date=None):
        if not self.enabled:
            raise TripError("cloud_history_disabled")
        if self.task is not None:
            return False
        today = datetime.now(ZoneInfo(self.hass.config.time_zone)).date()
        first, last = validate_range(start_date or today.isoformat(), end_date or today.isoformat())
        if (last - first).days > 30 or last > today:
            raise TripError("cloud_date_range")
        self.task = self.hass.async_create_task(
            self._run_cloud(first, last, geocode_only=geocode_only), "im_motors official calendar")
        return True

    async def read(self, operation, query, vehicle):
        await asyncio.sleep(0.15)
        async with self.coordinator._request_lock:
            job = self.hass.async_add_executor_job(self.reader.read, operation, query, vehicle)
            try:
                return await asyncio.shield(job)
            except asyncio.CancelledError:
                # Keep the shared account lock until its HTTPS worker has exited.
                try:
                    await job
                except Exception:
                    pass
                raise

    @staticmethod
    def check_vehicle(data, vehicle):
        vin = data.get("vin")
        if vin is not None and (not isinstance(vin, str) or sha256(vin.encode()).hexdigest() != vehicle):
            raise TripError("vehicle_mismatch")

    async def fetch_day(self, day, vehicle, summary=None):
        summary = summary if summary is not None else await self.read("summary", {"tripDate": day}, vehicle)
        self.check_vehicle(summary, vehicle)
        status = summary.get("tripStatus")
        if type(status) is not int or status not in (1, 2, 3):
            raise TripError("invalid_response")
        if status == 2:
            parsed = parse_day(day, envelope(summary), [envelope({"totalCount": 0, "blocks": []})])
        elif status == 3:
            parsed = parse_day(day, envelope(summary), [])
        else:
            pages, details, seen, expected = [], {}, set(), None
            for page in range(1, 201):
                result = await self.read("trips", {"tripDate": day, "offset": page, "limit": 5}, vehicle)
                self.check_vehicle(result, vehicle)
                count, blocks = result.get("totalCount"), result.get("blocks")
                if type(count) is not int or not 0 <= count <= 1000 or not isinstance(blocks, list) or len(blocks) > 5:
                    raise TripError("invalid_response")
                if expected is not None and count != expected:
                    raise TripError("incomplete_day")
                expected = count
                pages.append(envelope(result))
                before = len(seen)
                for block in blocks:
                    if not isinstance(block, dict) or not isinstance(block.get("tripId"), str) or not block["tripId"]:
                        raise TripError("invalid_response")
                    self.check_vehicle(block, vehicle)
                    if block["tripId"] in seen:
                        raise TripError("incomplete_day")
                    seen.add(block["tripId"])
                if len(seen) == expected:
                    break
                if len(seen) == before or len(blocks) < 5 or len(seen) > expected:
                    raise TripError("incomplete_day")
            else:
                raise TripError("incomplete_day")
            parsed = parse_day(day, envelope(summary), pages)
            for trip in parsed["trips"]:
                try:
                    query = detail_query(trip)
                except (ValueError, TypeError):
                    raise TripError("invalid_response") from None
                detail = await self.read("detail", query, vehicle)
                self.check_vehicle(detail, vehicle)
                details[trip["trip_id"]] = envelope(detail)
            parsed = parse_day(day, envelope(summary), pages, details)
            for trip in parsed["trips"]:
                detail = details[trip["trip_id"]]["data"]
                trip["route_points"] = route_points(detail)
                trip["driven_track_switch_raw"] = detail.get("drivenTrackSwitch")
                for prefix in ("start", "end"):
                    if trip.get(prefix + "_address"):
                        trip[prefix + "_address_source"] = "official_cloud"
        parsed["source"] = self.source
        for trip in parsed["trips"]:
            trip["source"] = self.source
        return parsed

    async def _run_cloud(self, first, last, *, geocode_only=False):
        try:
            await self.set_status(state="running", error=None, address_error=None, processed=0)
            vehicles = self.coordinator.data.vehicles
            if len(vehicles) != 1:
                raise TripError("multiple_vehicles")
            vehicle = vehicles[0].identifier
            if not geocode_only:
                # Use established token refresh and account backoff before cloud reads.
                async with self.coordinator._request_lock:
                    job = self.hass.async_add_executor_job(self.coordinator.client.update)
                    try:
                        snapshot = await asyncio.shield(job)
                    except asyncio.CancelledError:
                        try:
                            await job
                        except Exception:
                            pass
                        raise
                if [v.identifier for v in snapshot.vehicles] != [vehicle]:
                    raise TripError("vehicle_mismatch")
                summary = await self.read("summary", {"tripDate": last.isoformat()}, vehicle)
                self.check_vehicle(summary, vehicle)
                available = calendar_dates(summary.get("hasTripDates"))
                # Retain the calendar before fetching details; failed pages cannot
                # turn an available date into a false no-trip day.
                await self.hass.async_add_executor_job(self.store.state, "calendar", {
                    "available_dates": available, "source": self.source,
                    "queried_date": last.isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()})
                for offset in range((last - first).days + 1):
                    day = (last - timedelta(days=offset)).isoformat()
                    await self.set_status(current_date=day)
                    result = await self.fetch_day(day, vehicle, summary if day == last.isoformat() else None)
                    await self.hass.async_add_executor_job(self.store.save_day, vehicle, result)
                    await self.set_status(processed=self.status["processed"] + 1)
            await self.geocode(vehicle)
            await self.set_status(state="complete", error=None, current_date=None,
                finished_at=datetime.now(timezone.utc).isoformat())
        except asyncio.CancelledError:
            await self.set_status(state="error", error="interrupted")
            raise
        except Exception as error:
            await self.set_status(state="error", error=error.code if isinstance(error, TripError) else "invalid_response")
        finally:
            self.task = None
