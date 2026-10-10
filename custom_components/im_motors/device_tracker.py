"""Vehicle location from the same encrypted, VIN-checked cloud snapshot."""
from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.core import callback

from .entity import ImMotorsEntity
from .location import coordinates, coordinate_format, location_status
from .telemetry import raw, timestamp

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_vehicles():
        entities = []
        for vehicle in coordinator.data.vehicles:
            if vehicle.identifier not in known:
                known.add(vehicle.identifier)
                entities.append(ImMotorsTracker(coordinator, vehicle.identifier))
        if entities:
            async_add_entities(entities)

    add_vehicles()
    entry.async_on_unload(coordinator.async_add_listener(add_vehicles))


class ImMotorsTracker(ImMotorsEntity, TrackerEntity):
    _attr_name = "车辆位置"
    _attr_icon = "mdi:car-connected"

    def __init__(self, coordinator, vehicle_id):
        super().__init__(coordinator, vehicle_id, "location")

    @property
    def snapshot(self):
        return self.coordinator.data.telemetry.get(self.vehicle_id)

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def available(self):
        return super().available and coordinates(self.snapshot) is not None

    @property
    def latitude(self):
        point = coordinates(self.snapshot)
        return point[0] if point else None

    @property
    def longitude(self):
        point = coordinates(self.snapshot)
        return point[1] if point else None

    @property
    def extra_state_attributes(self):
        updated = timestamp(raw(self.snapshot, "root", "updateTime"))
        retrieved = timestamp(self.coordinator.data.telemetry_time_ms)
        return {"location_status": location_status(self.snapshot),
                "source_field": "period.latitude / period.longitude",
                "source_coordinate_format": coordinate_format(self.snapshot),
                "source_coordinate_system": "GCJ-02" if coordinate_format(self.snapshot) in (0, 1) else "unknown",
                "coordinate_system": "WGS-84",
                "coordinate_system_basis": "app_3.2.4_autonavi_call_chain_observed_formats_0_1",
                "cloud_updated": updated.isoformat() if updated else None,
                "retrieved_at": retrieved.isoformat() if retrieved else None,
                "individual_sample_time_verified": False}
