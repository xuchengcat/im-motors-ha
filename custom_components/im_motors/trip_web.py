"""Authenticated archive queries and a local HA panel; keys stay on server."""
from functools import partial
from pathlib import Path

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .const import DOMAIN
from .trip_api import TripError
from .trip_store import validate_range
from .trips import ERRORS


def require_admin(connection):
    if connection.user is None or not connection.user.is_admin:
        raise Unauthorized


def manager_for(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
        raise TripError("entry_unavailable")
    manager = getattr(entry.runtime_data, "trips", None)
    if manager is None:
        raise TripError("entry_unavailable")
    return manager


@websocket_api.websocket_command({vol.Required("type"): "im_motors/trips/entries"})
@websocket_api.async_response
async def ws_entries(hass, connection, msg):
    require_admin(connection)
    result = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        try:
            manager = manager_for(hass, entry.entry_id)
        except TripError:
            continue
        result.append({"entry_id": entry.entry_id, "title": entry.title, "enabled": manager.enabled,
                       "amap_key_configured": bool(manager.key),
                       "source": "ha_telemetry", "interval_minutes": manager.coordinator.telemetry_interval_minutes,
                       "vehicles": [{"id": v.identifier, "name": v.name} for v in entry.runtime_data.data.vehicles]})
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "im_motors/trips/query",
    vol.Required("entry_id"): str, vol.Required("vehicle_id"): str,
    vol.Required("start_date"): str, vol.Required("end_date"): str,
    vol.Optional("include_trips", default=True): bool})
@websocket_api.async_response
async def ws_query(hass, connection, msg):
    require_admin(connection)
    try:
        manager = manager_for(hass, msg["entry_id"])
        if msg["vehicle_id"] not in {v.identifier for v in manager.coordinator.data.vehicles}:
            raise TripError("vehicle_unavailable")
        validate_range(msg["start_date"], msg["end_date"])
        args = (msg["vehicle_id"], msg["start_date"], msg["end_date"])
        days = await hass.async_add_executor_job(manager.store.days, *args)
        trips = await hass.async_add_executor_job(manager.store.trips, *args) if msg["include_trips"] else []
        stats = await hass.async_add_executor_job(manager.store.stats, msg["vehicle_id"])
        status = dict(manager.status)
        recording = manager.recording.get(msg["vehicle_id"], {})
        point = recording.get("previous") or {}
        active = recording.get("active") or {}
        status["recording"] = {"observation": recording.get("observation", "waiting"),
            "active": bool(active), "last_sample": point.get("time"),
            "active_since": (active.get("start") or {}).get("time")}
        status["message"] = ERRORS.get(status.get("error"), "")
        status["address_message"] = ERRORS.get(status.get("address_error"), "")
        connection.send_result(msg["id"], {"days": days, "trips": trips, "stats": stats, "status": status})
    except TripError as error:
        connection.send_error(msg["id"], error.code, ERRORS.get(error.code, "行程查询不可用。"))


@websocket_api.websocket_command({vol.Required("type"): "im_motors/trips/sync",
    vol.Required("entry_id"): str,
    vol.Optional("geocode_only", default=False): bool})
@websocket_api.async_response
async def ws_sync(hass, connection, msg):
    require_admin(connection)
    try:
        manager = manager_for(hass, msg["entry_id"])
        started = manager.start(geocode_only=msg["geocode_only"])
        connection.send_result(msg["id"], {"started": started})
    except TripError as error:
        connection.send_error(msg["id"], error.code, ERRORS.get(error.code, "请先在集成配置中启用历史行程。"))


@websocket_api.websocket_command({vol.Required("type"): "im_motors/trips/import",
    vol.Required("entry_id"): str, vol.Required("vehicle_id"): str,
    vol.Required("content"): str, vol.Optional("format", default="json"): vol.In(("json", "csv"))})
@websocket_api.async_response
async def ws_import(hass, connection, msg):
    require_admin(connection)
    try:
        from .trip_import import parse_import
        manager = manager_for(hass, msg["entry_id"])
        if not manager.enabled:
            raise TripError("history_disabled")
        if manager.task is not None:
            raise TripError("sync_running")
        if msg["vehicle_id"] not in {v.identifier for v in manager.coordinator.data.vehicles}:
            raise TripError("vehicle_unavailable")
        days, trips = await hass.async_add_executor_job(parse_import, msg["content"], msg["format"])
        result = await hass.async_add_executor_job(manager.store.import_rows, msg["vehicle_id"], days, trips)
        connection.send_result(msg["id"], result)
        if manager.key:
            manager.start(geocode_only=True)
    except TripError as error:
        messages = {"invalid_import": "文件格式不正确，请使用文档中的 JSON/CSV 模板，保留完整起止日期。",
                    "import_too_large": "每次导入最多2MB、366天、10000段行程。",
                    "history_disabled": "请先在集成配置中开启历史行程归档。",
                    "sync_running": "正在同步，请完成后导入。"}
        connection.send_error(msg["id"], error.code, messages.get(error.code, "行程导入失败。"))


async def async_setup_services(hass):
    async def handle(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        try:
            manager = manager_for(hass, call.data["entry_id"])
            if call.service == "sync_trip_history":
                manager.start(geocode_only=call.data.get("geocode_only", False))
                return None
            vehicle = call.data["vehicle_id"]
            if vehicle not in {v.identifier for v in manager.coordinator.data.vehicles}:
                raise TripError("vehicle_unavailable")
            if call.service == "import_trip_history":
                from .trip_import import parse_import
                if not manager.enabled or manager.task:
                    raise TripError("history_disabled_or_busy")
                days, trips = await hass.async_add_executor_job(parse_import, call.data["content"], call.data.get("format", "json"))
                result = await hass.async_add_executor_job(manager.store.import_rows, vehicle, days, trips)
                if manager.key:
                    manager.start(geocode_only=True)
                return result
            args = (vehicle, call.data["start_date"], call.data["end_date"])
            validate_range(*args[1:])
            return {"days": await hass.async_add_executor_job(manager.store.days, *args),
                    "trips": await hass.async_add_executor_job(manager.store.trips, *args)}
        except TripError as error:
            raise HomeAssistantError(ERRORS.get(error.code, "行程操作失败：" + error.code)) from None

    entry = {vol.Required("entry_id"): str}
    vehicle = {**entry, vol.Required("vehicle_id"): str}
    hass.services.async_register(DOMAIN, "sync_trip_history", handle,
        schema=vol.Schema({**entry,
                           vol.Optional("geocode_only", default=False): bool}))
    hass.services.async_register(DOMAIN, "query_trip_history", handle,
        schema=vol.Schema({**vehicle, vol.Required("start_date"): str, vol.Required("end_date"): str}),
        supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "import_trip_history", handle,
        schema=vol.Schema({**vehicle, vol.Required("content"): str,
                           vol.Optional("format", default="json"): vol.In(("json", "csv"))}),
        supports_response=SupportsResponse.OPTIONAL)


async def async_setup_web(hass):
    key = DOMAIN + "_trip_web"
    if hass.data.get(key):
        return
    websocket_api.async_register_command(hass, ws_entries)
    websocket_api.async_register_command(hass, ws_query)
    websocket_api.async_register_command(hass, ws_sync)
    websocket_api.async_register_command(hass, ws_import)
    await async_setup_services(hass)
    # HA test instances without HTTP may still exercise the query commands.
    if "frontend" in hass.config.components and "http" in hass.config.components:
        from homeassistant.components import panel_custom
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([StaticPathConfig(
            "/im_motors/trips-panel.js", str(Path(__file__).with_name("frontend") / "trips-panel.js"), False)])
        await panel_custom.async_register_panel(hass, frontend_url_path="im-motors-trips",
            webcomponent_name="im-motors-trips-panel", sidebar_title="智己行程", sidebar_icon="mdi:calendar-clock",
            module_url="/im_motors/trips-panel.js?v=0.5.0", require_admin=True)
    hass.data[key] = True
