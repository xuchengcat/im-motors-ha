"""Local trip recording from existing telemetry; encrypted AMap settings."""
import asyncio
from datetime import datetime, timezone
from .trip_api import TripError, AmapGeocoder
from .trip_store import TripStore
from .trip_recorder import TripRecorder, sample
from .pyim_china.credential_store import key_context, vault_for_path

ERRORS = {
    "amap_rejected": "高德拒绝查询，请检查 Web 服务 Key、配额和 IP 限制。",
    "amap_no_address": "高德未返回有效地址，已保留坐标。",
    "amap_invalid_response": "高德响应无效，已保留坐标。",
    "network_error": "高德网络请求失败，已保留坐标，可稍后补全地址。",
    "interrupted": "地址补全被 HA 重启中断，可重新补全，历史和进行中的行程均保留。",
    "recording_failed": "本地记录失败，请检查存储空间和目录权限。",
    "history_disabled": "请先在集成配置中启用本地行程记录。",
}


def settings(client, value=None):
    with key_context(client.key_file):
        vault = vault_for_path(client.data_dir / "trip-settings.imvault")
        if value is not None:
            vault._save_payload(value)
            return value
        return vault._load_payload() if vault.path.exists() else {}


class TripManager:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = TripStore(coordinator.client.data_dir / "trips.sqlite3")
        self.recorder = TripRecorder(self.store, time_zone=hass.config.time_zone)
        self.task = None
        self.status = {"state": "idle"}
        self.recording = {}
        self.key = ""

    @property
    def enabled(self):
        return self.entry.options.get("trip_history_enabled", False)

    async def setup(self):
        await self.hass.async_add_executor_job(self.store.initialize)
        saved = await self.hass.async_add_executor_job(settings, self.coordinator.client)
        self.key = saved.get("amap_api_key", "")
        self.status = await self.hass.async_add_executor_job(self.store.state, "sync") or {"state": "idle"}
        if self.status.get("state") == "running":
            await self.set_status(state="error", error="interrupted")
        for vehicle in self.coordinator.data.vehicles:
            await self.hass.async_add_executor_job(self.recorder.rebuild_if_needed,
                vehicle.identifier, self.coordinator.telemetry_interval_minutes)
            self.recording[vehicle.identifier] = await self.hass.async_add_executor_job(self.store.state, "recorder:" + vehicle.identifier)
        if self.enabled:
            await self.observe(self.coordinator.data)
            if self.key and not self.task:
                self.start(geocode_only=True)

    async def observe(self, snapshot):
        if not self.enabled:
            return
        for vehicle, telemetry in snapshot.telemetry.items():
            try:
                point, reason = sample(telemetry, datetime.now(timezone.utc), self.coordinator.telemetry_interval_minutes)
                if point is None:
                    self.recording[vehicle] = dict(self.recording.get(vehicle, {}), observation=reason)
                    continue
                previous_id = self.recording.get(vehicle, {}).get("last_trip_id")
                previous_endpoint = self.recording.get(vehicle, {}).get("endpoint_revision", 0)
                result = await self.hass.async_add_executor_job(self.recorder.observe, vehicle, point, self.coordinator.telemetry_interval_minutes)
                self.recording[vehicle] = result
                if (result.get("last_trip_id") != previous_id or result.get("endpoint_revision", 0) != previous_endpoint) and self.key and not self.task:
                    self.start(geocode_only=True)
            except Exception:
                self.recording[vehicle] = dict(self.recording.get(vehicle, {}), observation="recording_failed")

    def start(self, *, geocode_only=False):
        if not self.enabled:
            raise TripError("history_disabled")
        if self.task is not None:
            return False
        self.task = self.hass.async_create_task(self._run(geocode_only=geocode_only), "im_motors trip addresses")
        return True

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def set_status(self, **values):
        self.status = dict(self.status, **values)
        await self.hass.async_add_executor_job(self.store.state, "sync", self.status)

    async def _run(self, *, geocode_only=False):
        try:
            await self.set_status(state="running", error=None, address_error=None)
            if not geocode_only:
                await self.coordinator.async_query_vehicle()
            for vehicle in self.coordinator.data.vehicles:
                await self.geocode(vehicle.identifier)
            await self.set_status(state="complete", error=None, finished_at=datetime.now(timezone.utc).isoformat())
        except asyncio.CancelledError:
            await self.set_status(state="error", error="interrupted")
            raise
        except Exception as error:
            await self.set_status(state="error", error=error.code if isinstance(error, TripError) else "recording_failed")
        finally:
            self.task = None

    async def geocode(self, vehicle):
        if not self.key:
            await self.set_status(address_state="key_required")
            return
        await self.set_status(address_state="running")
        geocoder = AmapGeocoder(self.key)
        stats = await self.hass.async_add_executor_job(self.store.stats, vehicle)
        if not stats["first_day"]:
            await self.set_status(address_state="complete")
            return
        rows = await self.hass.async_add_executor_job(self.store.trips, vehicle, stats["first_day"], stats["last_day"])
        try:
            for trip in rows:
                changed = False
                for prefix in ("start", "end"):
                    point = trip.get(prefix + "_coordinates")
                    if not point or trip.get(prefix + "_address"):
                        continue
                    cached = await self.hass.async_add_executor_job(self.store.address, point)
                    if not cached:
                        await asyncio.sleep(0.6)
                        cached = await self.hass.async_add_executor_job(geocoder.reverse, point["latitude"], point["longitude"])
                        await self.hass.async_add_executor_job(self.store.address, point, cached)
                    trip[prefix + "_address"] = cached["address"]
                    trip[prefix + "_poi"] = cached.get("poi")
                    trip[prefix + "_address_source"] = "amap_web_service"
                    changed = True
                if changed:
                    await self.hass.async_add_executor_job(self.store.update_trip, vehicle, trip)
            await self.set_status(address_state="complete", address_error=None)
        except TripError as error:
            await self.set_status(address_state="error", address_error=error.code)
