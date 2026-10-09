"""Diagnostics are allowlisted counts and booleans, never config/response data."""
from .const import INTEGRATION_VERSION, DEFAULT_TELEMETRY_INTERVAL
from .location import coordinates


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    snapshot = coordinator.data if coordinator else None
    return {"integration_version": INTEGRATION_VERSION, "vehicle_telemetry_enabled": True,
            "last_update_success": bool(coordinator and coordinator.last_update_success),
            "associated_vehicle_count": len(snapshot.vehicles) if snapshot else 0,
            "vehicles_with_telemetry": len(snapshot.telemetry) if snapshot else 0,
            "poll_interval_seconds": (coordinator.telemetry_interval_minutes if coordinator
                                      else DEFAULT_TELEMETRY_INTERVAL) * 60,
            "individual_sample_time_verified": False,
            "vehicle_location_enabled": True,
            "vehicles_with_location": sum(coordinates(v) is not None for v in snapshot.telemetry.values()) if snapshot else 0,
            "pending_fields": ["里程单位", "chargedPower单位", "完整车锁枚举", "定位独立采样时间", "其他坐标格式"]}
