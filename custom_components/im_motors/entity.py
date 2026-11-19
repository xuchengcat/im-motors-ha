"""Vehicle identifiers use hashes; VIN and user-assigned names never enter HA."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .telemetry import normalized, observed, raw, timestamp
from datetime import datetime, timezone
from .pyim_china.debug_fields import DEBUG_FIELDS


def async_label_other_entity(hass, entity_id):
    """Use HA's Other label without assigning a physical area to a signal."""
    from homeassistant.helpers import entity_registry as er, label_registry as lr
    labels = lr.async_get(hass)
    label = next((item for item in labels.labels.values() if item.name == "其他"), None)
    if label is None:
        label = labels.async_create("其他", icon="mdi:code-json", description="IM Motors raw fields for diagnosis and development")
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry and label.label_id not in entry.labels:
        registry.async_update_entity(entity_id, labels={*entry.labels, label.label_id})


class ImMotorsEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, vehicle_id, key):
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{vehicle_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, vehicle_id)},
                                            manufacturer="IM Motors", name="智己汽车")

    @property
    def vehicle(self):
        return next((v for v in self.coordinator.data.vehicles if v.identifier == self.vehicle_id), None)

    @property
    def available(self):
        return super().available and self.vehicle is not None


class ImMotorsTelemetryEntity(ImMotorsEntity):
    """Normalized observations and allowlisted raw developer signals."""

    def __init__(self, coordinator, vehicle_id, key, spec):
        super().__init__(coordinator, vehicle_id, key)
        self.spec = spec
        self._attr_name = spec.name
        self._attr_device_class = spec.device_class
        self._attr_entity_registry_enabled_default = spec.enabled_by_default and (
            not spec.diagnostic or key in ("vehicle_updated", "vehicle_series", "tab_project_code")
            or spec.debug or coordinator.control_enabled)
        if spec.diagnostic:
            from homeassistant.const import EntityCategory
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self.spec.debug:
            async_label_other_entity(self.hass, self.entity_id)

    @property
    def snapshot(self):
        return self.coordinator.data.telemetry.get(self.vehicle_id)

    @property
    def mapped_value(self):
        return normalized(self.snapshot, self.spec)

    @property
    def available(self):
        return super().available and self.snapshot is not None

    @property
    def extra_state_attributes(self):
        item = observed(self.snapshot, self.spec.group, self.spec.field)
        updated = timestamp(raw(self.snapshot, "root", "updateTime"))
        retrieved = timestamp(self.coordinator.data.telemetry_time_ms)
        attributes = {"source_field": self.spec.field if self.spec.debug else f"{self.spec.group}.{self.spec.field}",
                "source_presence": item.presence.value if item else "missing",
                "cloud_updated": updated.isoformat() if updated else None,
                "retrieved_at": retrieved.isoformat() if retrieved else None,
                "cloud_snapshot_age_seconds": round((datetime.now(timezone.utc) - updated).total_seconds())
                    if updated and updated <= datetime.now(timezone.utc) else None,
                "individual_sample_time_verified": False}
        if self.spec.debug:
            definition = DEBUG_FIELDS[self.spec.field]
            attributes.update(category="其他", group=definition.group,
                meaning_zh=definition.meaning_zh, meaning_status=definition.meaning_status,
                unit_status=definition.unit_status, enum_status=definition.enum_status,
                evidence=definition.evidence, source_container=definition.source,
                raw_type=item.raw_type if item else None,
                validation=item.validation if item else "missing")
            for name in ("unit_hint", "scale_hint", "known_codes", "evidence_note"):
                value = getattr(definition, name)
                if value is not None:
                    attributes[name] = value
        return attributes
