"""Diagnostics are allowlisted counts and booleans, never config/response data."""
from .const import INTEGRATION_VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    snapshot = coordinator.data if coordinator else None
    return {"integration_version": INTEGRATION_VERSION, "vehicle_telemetry_enabled": True,
            "last_update_success": bool(coordinator and coordinator.last_update_success),
            "associated_vehicle_count": len(snapshot.vehicles) if snapshot else 0,
            "vehicles_with_telemetry": len(snapshot.telemetry) if snapshot else 0,
            "poll_interval_seconds": 300,
            "individual_sample_time_verified": False,
            "pending_fields": ["里程单位", "chargedPower单位", "位置", "完整车锁枚举"]}
