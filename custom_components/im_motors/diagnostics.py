"""Diagnostics are allowlisted counts and booleans, never config/response data."""
from .const import PENDING_FIELDS


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    snapshot = coordinator.data if coordinator else None
    return {"integration_version": "0.1.0", "vehicle_telemetry_enabled": False,
            "last_update_success": bool(coordinator and coordinator.last_update_success),
            "associated_vehicle_count": len(snapshot.vehicles) if snapshot else 0,
            "pending_fields": list(PENDING_FIELDS)}
