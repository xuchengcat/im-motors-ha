"""Primary-account calendar archive, archived daily from official read APIs."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change

from .trip_api import TripReader
from ..const import account_type, ACCOUNT_PRIMARY
from ..trip_api import TripError
from ..trip_parser import parse_day, detail_query, coordinates, number
from ..trip_store import TripStore, validate_range
from ..trips import BaseTripManager, settings


REMOTE_HISTORY_DAYS = 60
AUTO_SYNC_HOUR = 3


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


class CloudTripManager(BaseTripManager):
    source = "official_cloud"

    def __init__(self, hass, entry, coordinator):
        super().__init__(hass, entry, coordinator, database="cloud-trips.sqlite3")
        # Switching roles never overwrites locally recorded or imported trips.
        self.recorder = None
        self.reader = TripReader(coordinator.client)
        self._remove_daily = None
        self._remove_started = None
        self._auto_check_task = None
        self._daily_lock = asyncio.Lock()
        self._stopping = False
        self._auto_pending = False
        self._downloaded_details = 0

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
        if self.enabled:
            self._remove_daily = async_track_time_change(
                self.hass, self._scheduled, hour=AUTO_SYNC_HOUR, minute=0, second=0)
            if self.hass.is_running:
                self._request_auto_check()
            else:
                @callback
                def started(_event):
                    self._remove_started = None
                    self._request_auto_check()
                self._remove_started = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started)

    def now(self):
        return datetime.now(ZoneInfo(self.hass.config.time_zone))

    def _request_auto_check(self):
        if self._stopping or not self.enabled:
            return
        if self._auto_check_task and not self._auto_check_task.done():
            return
        self._auto_check_task = self.hass.async_create_background_task(
            self.async_daily_sync(), "im_motors daily archive check", eager_start=False)

    async def _scheduled(self, _now):
        self._request_auto_check()

    async def async_daily_sync(self):
        async with self._daily_lock:
            if self._stopping or not self.enabled:
                return False
            if self.task is not None:
                self._auto_pending = True
                return False
            now = self.now()
            day = now.date().isoformat()
            claimed = await self.hass.async_add_executor_job(
                self.store.claim_daily_sync, day, now.isoformat())
            if not claimed:
                return False
            if self._stopping:
                await self.hass.async_add_executor_job(
                    self.store.finish_daily_sync, day, "interrupted", self.now().isoformat(), "interrupted")
                return False
            first = now.date() - timedelta(days=REMOTE_HISTORY_DAYS - 1)
            self.task = self.hass.async_create_background_task(
                self._run_cloud(first, now.date(), incremental=True, automatic_day=day),
                "im_motors daily official archive", eager_start=False)
            return True

    async def stop(self):
        self._stopping = True
        for remove in (self._remove_daily, self._remove_started):
            if remove:
                remove()
        self._remove_daily = self._remove_started = None
        if self._auto_check_task and not self._auto_check_task.done():
            self._auto_check_task.cancel()
            try:
                await self._auto_check_task
            except asyncio.CancelledError:
                pass
        await super().stop()

    async def observe(self, snapshot):
        """Primary accounts never collect local telemetry samples."""

    def start(self, *, geocode_only=False, start_date=None, end_date=None):
        if not self.enabled:
            raise TripError("cloud_history_disabled")
        if self.task is not None:
            return False
        today = self.now().date()
        first, last = validate_range(start_date or today.isoformat(), end_date or today.isoformat())
        if (last - first).days > 30 or last > today:
            raise TripError("cloud_date_range")
        self.task = self.hass.async_create_task(
            self._run_cloud(first, last, geocode_only=geocode_only), "im_motors official calendar")
        return True

    async def _account_job(self, function, *args):
        job = self.hass.async_add_executor_job(function, *args)
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            # Do not release the shared account lock while HTTPS is still live.
            try:
                await job
            except Exception:
                pass
            raise

    async def read(self, operation, query, vehicle):
        await asyncio.sleep(0.15)
        async with self.coordinator._request_lock:
            try:
                return await self._account_job(self.reader.read, operation, query, vehicle)
            except TripError as error:
                # This error is raised before the GET is sent. Refresh through
                # the existing durable client, then prepare the read once.
                if error.code != "authentication_refresh_required":
                    raise
            await self._account_job(self.coordinator.client.update)
            return await self._account_job(self.reader.read, operation, query, vehicle)

    @staticmethod
    def check_vehicle(data, vehicle):
        vin = data.get("vin")
        if vin is not None and (not isinstance(vin, str) or sha256(vin.encode()).hexdigest() != vehicle):
            raise TripError("vehicle_mismatch")

    async def fetch_day(self, day, vehicle, summary=None, *, incremental=False):
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
            cached = (await self.hass.async_add_executor_job(self.store.cloud_payloads, vehicle, day)) if incremental else {}
            retained = {}
            fields = ("start_time", "end_time", "distance_km", "duration_minutes", "trip_type_raw")
            for trip in parsed["trips"]:
                previous = cached.get(trip["trip_id"], {})
                if (previous.get("source") == self.source and previous.get("detail_loaded") is True
                        and isinstance(previous.get("route_points"), list)
                        and (previous["route_points"] or previous.get("driven_track_switch_raw") in ("0", 0, False))
                        and all(previous.get(key) == trip.get(key) for key in fields)):
                    retained[trip["trip_id"]] = previous
                    continue
                try:
                    query = detail_query(trip)
                except (ValueError, TypeError):
                    raise TripError("invalid_response") from None
                detail = await self.read("detail", query, vehicle)
                self.check_vehicle(detail, vehicle)
                details[trip["trip_id"]] = envelope(detail)
                self._downloaded_details += 1
            parsed = parse_day(day, envelope(summary), pages, details)
            for trip in parsed["trips"]:
                if trip["trip_id"] in retained:
                    trip.update(retained[trip["trip_id"]])
                    continue
                detail = details[trip["trip_id"]]["data"]
                trip["route_points"] = route_points(detail)
                trip["driven_track_switch_raw"] = detail.get("drivenTrackSwitch")
                for prefix in ("start", "end"):
                    if trip.get(prefix + "_address"):
                        trip[prefix + "_address_source"] = "official_cloud"
        parsed["addresses_complete"] = parsed["list_complete"] and all(
            trip.get("start_address") and trip.get("end_address") for trip in parsed["trips"])
        parsed["source"] = self.source
        for trip in parsed["trips"]:
            trip["source"] = self.source
        return parsed

    async def _run_cloud(self, first, last, *, geocode_only=False, incremental=False, automatic_day=None):
        outcome, failure = "error", "invalid_response"
        self._downloaded_details = 0
        try:
            await self.set_status(state="running", error=None, address_error=None, processed=0,
                                  automatic=automatic_day is not None, downloaded_details=0, failed_dates=[])
            vehicles = self.coordinator.data.vehicles
            if len(vehicles) != 1:
                raise TripError("multiple_vehicles")
            vehicle = vehicles[0].identifier
            if not geocode_only:
                # Use established token refresh and account backoff before cloud reads.
                async with self.coordinator._request_lock:
                    snapshot = await self._account_job(self.coordinator.client.update)
                if [v.identifier for v in snapshot.vehicles] != [vehicle]:
                    raise TripError("vehicle_mismatch")
                summary = await self.read("summary", {"tripDate": last.isoformat()}, vehicle)
                self.check_vehicle(summary, vehicle)
                available = calendar_dates(summary.get("hasTripDates"))
                # Retain the calendar before fetching details; failed pages cannot
                # turn an available date into a false no-trip day.
                await self.hass.async_add_executor_job(self.store.state, "calendar_remote", {
                    "available_dates": available, "source": self.source,
                    "queried_date": last.isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()})
                if incremental:
                    # Check all remote trip IDs in the retention window, but
                    # fetch details only for missing or changed local records.
                    days = set(day for day in available if first.isoformat() <= day <= last.isoformat())
                    days.update((last - timedelta(days=offset)).isoformat() for offset in range(3))
                    planned = sorted(day for day in days if first.isoformat() <= day <= last.isoformat())
                else:
                    planned = [(last - timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]
                await self.set_status(planned_dates=len(planned))
                for day in planned:
                    await self.set_status(current_date=day)
                    try:
                        result = await self.fetch_day(day, vehicle, summary if day == last.isoformat() else None, incremental=incremental)
                    except (TripError, ValueError) as error:
                        code = error.code if isinstance(error, TripError) else "invalid_response"
                        if not incremental or code not in ("incomplete_day", "invalid_response", "business_error"):
                            raise
                        await self.set_status(failed_dates=[*self.status["failed_dates"], day])
                        continue
                    await self.hass.async_add_executor_job(self.store.save_day, vehicle, result)
                    await self.set_status(processed=self.status["processed"] + 1, downloaded_details=self._downloaded_details)
            if self.status.get("failed_dates"):
                raise TripError("partial_archive")
            await self.geocode(vehicle)
            await self.set_status(state="complete", error=None, current_date=None,
                finished_at=self.now().isoformat())
            outcome, failure = "complete", None
        except asyncio.CancelledError:
            outcome, failure = "interrupted", "interrupted"
            await self.set_status(state="error", error="interrupted")
            raise
        except Exception as error:
            failure = error.code if isinstance(error, TripError) else "invalid_response"
            await self.set_status(state="error", error=failure)
        finally:
            try:
                if automatic_day:
                    await self.hass.async_add_executor_job(
                        self.store.finish_daily_sync, automatic_day, outcome, self.now().isoformat(),
                        failure, self.status.get("processed", 0), self._downloaded_details)
            finally:
                self.task = None
                if self._auto_pending and not self._stopping:
                    self._auto_pending = False
                    self._request_auto_check()
