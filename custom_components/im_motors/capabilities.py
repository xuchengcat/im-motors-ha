"""Per-vehicle powertrain capabilities, independent of account role."""

HYBRID_SENSORS = frozenset(("fuel_level", "fuel_range", "electric_range"))
FUEL_FIELDS = frozenset(("fuelLvlPcnt", "icbVehFuelRng", "icbVehElecRng", "fuelValueN"))


def hybrid(coordinator, vehicle):
    snapshot = coordinator.data.telemetry.get(vehicle) if coordinator.data else None
    current = snapshot.supports_feature("HYBRID_ELECTRIC") if snapshot else None
    return current if current is not None else getattr(coordinator, "hybrid_capabilities", {}).get(vehicle)


def requires_hybrid(key, spec):
    return key in HYBRID_SENSORS or spec.field.rsplit(".", 1)[-1] in FUEL_FIELDS


def visible(coordinator, vehicle, key, spec):
    return not requires_hybrid(key, spec) or hybrid(coordinator, vehicle) is True


def registry_changes(entity, allowed):
    """Preserve user choices while hiding unsupported entities from older installs."""
    from homeassistant.helpers import entity_registry as er
    changes = {}
    if not allowed:
        if entity.disabled_by is None:
            changes['disabled_by'] = er.RegistryEntryDisabler.INTEGRATION
        if entity.hidden_by is None:
            changes['hidden_by'] = er.RegistryEntryHider.INTEGRATION
    else:
        if entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            changes['disabled_by'] = None
        if entity.hidden_by == er.RegistryEntryHider.INTEGRATION:
            changes['hidden_by'] = None
    return changes
