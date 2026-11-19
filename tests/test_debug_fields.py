"""All raw developer fields stay bounded, labelled and distinct from controls."""
import hashlib
import inspect
import json
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er, label_registry as lr

from conftest import VIN, response
from test_ha import create_entry
from custom_components.im_motors.pyim_china.debug_fields import DEBUG_FIELDS, bounded_debug_value
from custom_components.im_motors.pyim_china.offline_read_response import (
    parse_read_response, Presence, _ROOT, _PERIOD, _BATTERY, _SECTIONS, _TAB_SECTIONS)
from custom_components.im_motors.telemetry import SENSORS, normalized


def parsed(category, **tab):
    return parse_read_response("vehicle_tab", json.dumps({"resultCode": "200", "data": {
        "category": {"vin": VIN, **category}, **tab}}))


def test_entire_catalog_roundtrips_without_scaling_or_automatic_enums():
    assert len(DEBUG_FIELDS) == 172
    specs = {s.field: s for s in SENSORS.values() if s.debug}
    assert set(specs) == set(DEBUG_FIELDS)
    assert all(s.diagnostic and s.unit is None and s.device_class is None for s in specs.values())
    assert all(s.name == "Other / " + path for path, s in specs.items())
    assert all(d.meaning_zh and d.meaning_status in ("partially_confirmed", "inferred_from_name", "unknown")
               for d in DEBUG_FIELDS.values())
    assert not any(word in DEBUG_FIELDS for word in ("root.vin", "period.latitude", "period.longitude", "root.token"))
    schemas = {"root": _ROOT, "period": _PERIOD, "hvBattery": _BATTERY, **_SECTIONS, **_TAB_SECTIONS}
    category, tab, expected = {}, {}, {}
    for path, definition in DEBUG_FIELDS.items():
        source = tab if definition.source == "tab" else category
        group = source if definition.group == "root" else source.setdefault(definition.group, {})
        kind = schemas.get(definition.group, {}).get(definition.field, float)
        value = "1" if kind is str else 1.25 if kind is float else 1
        group[definition.field] = value
        expected[path] = value
    snapshot = parsed(category, **tab)
    for path, value in expected.items():
        assert snapshot.debug_fields[path].presence is Presence.VALUE
        assert normalized(snapshot, specs[path]) == value, path
    # Existing raw entity keys retain their stable unique-ID suffixes.
    assert SENSORS["charged_power_raw"].field == "period.chargedPower"
    assert SENSORS["soc_secondary"].field == "period.bmsPackSOCDsp"
    assert SENSORS["fl_SeatHeatLvl"].field == "seat.flSeatHeatLvl"


def test_missing_null_and_invalid_optional_fields_do_not_break_telemetry():
    snapshot = parsed({"period": {"originalBmsPackSOCDsp": 82,
        "bmsPackVol": None, "battVoltage": {"nested": "PRIVATE"},
        "bmsAvlblEnrg": "x" * 300, "arbitrarySecret": "PRIVATE"},
        "sentryMode": {"sentryModeSetSts": False},
        "bookTrip": {"tripBookWeekValue": [1, 3, 5]},
        "untrustedGroup": {"token": "PRIVATE"}})
    assert normalized(snapshot, SENSORS["soc"]) == 82
    assert snapshot.debug_fields["period.bmsPackVol"].presence is Presence.NULL
    assert snapshot.debug_fields["period.battVoltage"].validation == "unsupported_type"
    assert snapshot.debug_fields["period.bmsAvlblEnrg"].validation == "too_long"
    assert snapshot.debug_fields["period.vehSpeed"].presence is Presence.MISSING
    assert snapshot.debug_fields["sentryMode.sentryModeSetSts"].value == "false"
    assert snapshot.debug_fields["bookTrip.tripBookWeekValue"].value == "[1,3,5]"
    assert "PRIVATE" not in repr(snapshot.debug_fields)
    assert not any("arbitrarySecret" in path or "token" in path for path in snapshot.debug_fields)


@pytest.mark.parametrize("value,validation", [
    (float("inf"), "non_finite"), (float("nan"), "non_finite"),
    ({"private": 1}, "unsupported_type"), ([[1]], "unsupported_type"),
    ("x" * 256, "too_long"), ("bad\nvalue", "control_characters"),
    (["x" * 256], "too_long"), (list(range(65)), "unsupported_type"),
])
def test_unsupported_raw_values_are_unknown_instead_of_oversized_ha_states(value, validation):
    assert bounded_debug_value(value) == (None, validation)


@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_all_other_entities_enabled_labelled_defined_and_no_extra_http(hass, account, mode):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {
        "vin": VIN, "period": {"originalBmsPackSOCDsp": 82, "bmsPackVol": 392.25},
        "sentryMode": {"sentryModeSetSts": 2}}})]
    entry = await create_entry(hass, account, access_mode=mode)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    debug_keys = {key for key, spec in SENSORS.items() if spec.debug}
    vehicle_id = hashlib.sha256(VIN.encode()).hexdigest()
    unique_ids = {f"{entry.entry_id}_{vehicle_id}_{key}" for key in debug_keys}
    entries = [e for e in er.async_get(hass).entities.values() if e.unique_id in unique_ids]
    assert len(entries) == 172
    other_label = next(label.label_id for label in lr.async_get(hass).labels.values() if label.name == "其他")
    for entity in entries:
        assert entity.disabled_by is None
        assert other_label in entity.labels
        state = hass.states.get(entity.entity_id)
        assert state.attributes["category"] == "其他"
        assert state.attributes["meaning_zh"]
        assert "Other / " in state.attributes["friendly_name"]
        assert "unit_of_measurement" not in state.attributes
        assert "state_class" not in state.attributes
        assert "device_class" not in state.attributes
        assert VIN not in repr(state.as_dict())
    voltage = next(hass.states.get(e.entity_id) for e in entries
                   if hass.states.get(e.entity_id).attributes["source_field"] == "period.bmsPackVol")
    assert voltage.state == "392.25"
    assert voltage.attributes["raw_type"] == "float"
    assert voltage.attributes["meaning_status"] == "inferred_from_name"
    assert account["transport"].send.call_count == 2
    assert all(call.args[0].method == "GET" for call in account["transport"].send.call_args_list)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_upgrade_enables_integration_disabled_raw_entities_without_changing_ids(hass, account):
    account["transport"].send.side_effect = [response([{"vin": VIN}]), response({"category": {"vin": VIN}})]
    entry = await create_entry(hass, account)
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        await hass.config_entries.async_add(entry)
    registry = er.async_get(hass)
    vehicle_id = hashlib.sha256(VIN.encode()).hexdigest()
    registry_parameters = inspect.unwrap(registry.async_get_or_create).__code__.co_varnames
    registry_options = ({"config_entry": entry} if "config_entry" in registry_parameters
                        else {"config_entry_id": entry.entry_id})
    old = registry.async_get_or_create("sensor", "im_motors",
        f"{entry.entry_id}_{vehicle_id}_charged_power_raw", **registry_options,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    manual = registry.async_get_or_create("sensor", "im_motors",
        f"{entry.entry_id}_{vehicle_id}_soc_secondary", **registry_options,
        disabled_by=er.RegistryEntryDisabler.USER)
    with patch("custom_components.im_motors.pyim_china.AccountClient", return_value=account["client"]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert registry.async_get(old.entity_id).disabled_by is None
    assert hass.states.get(old.entity_id) is not None
    assert registry.async_get(manual.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(old.entity_id).unique_id == old.unique_id
    assert await hass.config_entries.async_unload(entry.entry_id)
