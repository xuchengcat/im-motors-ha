"""Shared trip state, address completion and encrypted settings."""
import asyncio
from .trip_api import TripError, AmapGeocoder
from .trip_store import TripStore
from .pyim_china.credential_store import key_context, vault_for_path

ERRORS = {
    "not_vehicle_owner": "智己返回非车主，请核对是否使用车辆主账号。",
    "protocol_rejected": "智己拒绝日历协议校验，请更新集成或检查服务端变化。",
    "authentication_failed": "行程认证已失效，请重新登录。",
    "authentication_refresh_required": "请等待账号令牌刷新后再同步。",
    "account_paused": "账号请求已暂停，请排查后恢复。",
    "account_unavailable": "账号暂不可用，请检查认证和加密存储。",
    "vehicle_mismatch": "行程车辆与关联车辆不一致，已停止保存。",
    "vehicle_unavailable": "当前车辆不可用。",
    "multiple_vehicles": "官方日历当前仅支持关联一辆车的账号，避免混淆车辆历史。",
    "cloud_history_disabled": "请在主账号选项中启用官方历史行程查询。",
    "cloud_date_range": "官方行程每次最多同步31天，结束日期不能晚于今天。",
    "partial_archive": "部分日期未能完整归档，其余日期已保存；失败日期保留原数据，下次自动任务会再次检查。",
    "incomplete_day": "行程分页不完整，已保留原有历史，请稍后手动重试。",
    "invalid_response": "行程响应无效，已保留原有历史。",
    "business_error": "智己拒绝行程请求，请检查后手动重试。",
    "http_error": "行程接口暂不可用，请稍后手动重试。",

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


class BaseTripManager:
    """Shared state, task cleanup and address completion for both sources."""

    def __init__(self, hass, entry, coordinator, *, database="trips.sqlite3"):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = TripStore(coordinator.client.data_dir / database)
        self.recorder = None
        self.task = None
        self.status = {"state": "idle"}
        self.recording = {}
        self.key = ""

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
