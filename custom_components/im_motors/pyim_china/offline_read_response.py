"""Parse the prepared GET responses locally; never perform I/O.

Only confirmed model fields are retained. Missing, null and zero stay distinct.
Values and server messages are excluded from repr/error text. This parser does
not validate server authenticity, vehicle ownership, no-wake behavior or units
that have not been traced. Objects still contain private data in their fields.
"""
from dataclasses import dataclass, field
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Mapping

from .offline_encryption import decrypted_response_body


class ResponseError(ValueError):
    """Malformed or unsupported response, without response content in text."""


class AuthenticationExpired(ResponseError):
    pass


class BusinessResponseError(ResponseError):
    pass


class DataUnavailable(ResponseError):
    pass


class Presence(str, Enum):
    MISSING = "missing"
    NULL = "null"
    VALUE = "value"


@dataclass(frozen=True, repr=False)
class ObservedField:
    presence: Presence
    value: object = None

    def __repr__(self):
        return f"ObservedField({self.presence.value}, <redacted>)"


@dataclass(frozen=True, repr=False)
class VehicleIdentity:
    fields: Mapping[str, ObservedField]

    def __repr__(self):
        return "VehicleIdentity(<redacted>)"


@dataclass(frozen=True, repr=False)
class VehicleList:
    presence: Presence
    vehicles: tuple[VehicleIdentity, ...] = ()

    def __repr__(self):
        return "VehicleList(<redacted>)"


@dataclass(frozen=True, repr=False)
class CategorySection:
    presence: Presence
    fields: Mapping[str, ObservedField]

    def __repr__(self):
        return f"CategorySection({self.presence.value}, <redacted>)"


@dataclass(frozen=True, repr=False)
class CategorySnapshot:
    root: Mapping[str, ObservedField]
    period_presence: Presence
    period: Mapping[str, ObservedField]
    battery_presence: Presence
    battery: Mapping[str, ObservedField]
    sections: Mapping[str, CategorySection] = field(
        default_factory=lambda: MappingProxyType({}))
    tab_metadata: Mapping[str, CategorySection] = field(
        default_factory=lambda: MappingProxyType({}))
    feature_keys: ObservedField = field(default_factory=lambda: ObservedField(Presence.MISSING))
    vin: ObservedField = field(default_factory=lambda: ObservedField(Presence.MISSING))

    def __repr__(self):
        return "CategorySnapshot(<redacted>)"

    def supports_feature(self, name: str) -> bool | None:
        """A complete returned list can establish absence; missing cannot."""
        if self.feature_keys.presence is not Presence.VALUE:
            return None
        return name in self.feature_keys.value

    @property
    def charge_target_percent(self) -> int | None:
        """imcu target is a legacy code or direct percent by support_3.0.

        APK ChargeCutOffPower maps 1..7 to 40..100. Do not copy the App's
        missing/unknown fallback of 80, or infer this from hvBattery alone.
        """
        supported = self.supports_feature("support_3.0")
        group = self.sections.get("imcuCharge")
        value = group.fields["imcuChrgTrgtSOCDspCmd"].value if group else None
        if supported is None or value is None:
            return None
        if supported:
            return value if 1 <= value <= 100 else None
        return {1: 40, 2: 50, 3: 60, 4: 70, 5: 80, 6: 90, 7: 100}.get(value)

    @property
    def remaining_charge_minutes(self) -> int | None:
        """1023 means estimating; negative values are not usable durations."""
        value = self.period["chargingRemainTime"].value
        return value if value is not None and value >= 0 and value != 1023 else None

    @property
    def charge_time_estimating(self) -> bool | None:
        value = self.period["chargingRemainTime"].value
        return None if value is None or value < 0 else value == 1023


# Wire names/types, not a normalized HA sensor schema. Unknown units (vehOdo,
# chargedPower, eventTime) remain raw; locations and unrelated fields are omitted.
_IDENTITY = {"vin": str, "vehicleName": str, "role": str}
_VEHICLE = {**_IDENTITY, "type": int, "useStatus": int}
_MANAGEMENT = {"vehicleName": str, "role": str, "vehicleVersionNumber": str,
               "isSwitch": bool, "isChargeSetting": bool, "otaSupport": bool}
_ISC_VEHICLE = {"vin": str, "vehicleName": str, "projectCode": str,
                "hasSetting": bool, "hasSupport": bool, "isSelected": bool, "themeStatus": int}
_ROOT = {"isOnLine": int, "isConnected": int, "updateTime": int,
         "isConnectedUpdateTime": int, "onLineTime": int, "offLineTime": int,
         "offLineDisconnect": int}
_PERIOD = {
    "originalBmsPackSOCDsp": float, "bmsPackSOCDsp": float,
    "fastCharge": str, "icbSetVehRngAlg": int, "icbVehElecRng": int,
    "icbVehFuelRng": int, "imcuVehElecRng": float, "cltcVehElecRng": int,
    "power": float, "electricPilePower": float, "chargedPower": float,
    "chargingRemainTime": int, "bmsPackCrnt": float, "vehOdo": int,
    "eventTime": float,
    "acInCarTemperature": float, "outsideCarTemperature": float,
    "frontLeftTirePressure": int, "frontRightTirePressure": int,
    "rearLeftTirePressure": int, "rearRightTirePressure": int,
    "flTireTem": float, "frTireTem": float, "rlTireTem": float, "rrTireTem": float,
    "frontLeftWindowPosition": float, "frontRightWindowPosition": float,
    "rearLeftWindowPosition": float, "rearRightWindowPosition": float,
    "chrgngSpdngTime": int, "elecConsumePer100Km": float,
    "imcuVehActuElecRng": int, "remainDrvDistance": int, "vehSpeed": float,
}
_BATTERY = {
    "bmsChargeStatus": int, "bmsBasicState": int, "bmsChargeControl": int,
    "bmsChrgSpRsn": int, "bmsDsChargeTargetSOC": int, "bmsFastPlugOn": int,
    "bmsOnboardChargeTargetSOC": int, "bmsReservationControl": int,
    "bmsSlowPlugOn": int, "rmtBattWarmAbotRsn": int, "rmtBattWarmSts": int,
    "eventTime": int,
}
# Group names and types come from CarFullData and the respective APK models.
# These are raw observations, not UI defaults or normalized state enums.
_SECTIONS = {
    "basic": {"eventTime": int, "shifterPosition": int, "sysPwrMd": int, "usgMd": int},
    "powerTrain": {"eptReadyStatus": int, "remoteVehStartStatus": int},
    "seat": {prefix + suffix: int for prefix in ("fl", "fr", "sl", "sm", "sr", "tl", "tm", "tr")
             for suffix in ("SeatHeatLvl", "SeatVentLvl")},
    "steeringWheel": {"steeringWheelHeatingStatus": int},
    "imcuCharge": {name: int for name in (
        "imcuChrgTrgtSOCDspCmd", "imcuChrgTrgtSOCResp", "imcuReserChrgCtrlResp",
        "imcuReserCtrlDspCmd", "imcuReserSpHourDspCmd", "imcuReserSpMinuteDspCmd",
        "imcuReserStHourDspCmd", "imcuReserStMinuteDspCmd",
        "wrlsChrgrAutocWrlsChrgngSetngResp", "wrlsChrgrAutocWrlsChrgngSetngSts")},
    "door": {"frontLeftDoorStatus": int, "frontRightDoorStatus": int,
             "rearLeftDoorOpenStatus": int, "rearRightDoorOpenStatus": int,
             "bonnetOpenStatus": int, "chargeCapOpenStatus": int,
             "trunkOpenStatus": int, "pwrLftgtManuClsReq": int, "eventTime": float},
    "window": {"flWndOpenSts": int, "frWndOpenSts": int,
               "rlWndOpenSts": int, "rrWndOpenSts": int},
    "lock": {"vehLockingState": int, "eventTime": float},
    "ac": {"acAutoStatus": int, "acFilterLife": int, "acFrontBlowerSpeed": int,
           "acOnOffDspCmd": int, "acRearBlowerSwitchStatus": int, "acStatus": int,
           "aclTemDspCmd": float, "acrTemDspCmd": float, "eventTime": int,
           "extremeTempControl": int, "htdRrWndAbotRsn": int, "htdRrWndState": int,
           "rmtACAbortRsn": int, "temperature": int},
    "warning": {"airbagDeployedStatus": int, "bmsWarningStatus": int,
                "brakeFluidLevelLowStatus": int, "eventTime": int,
                "forwardWarningStatus": int, "frontLeftTireStatus": int,
                "frontRightTireStatus": int, "leftWarningStatus": int,
                "lowWiperFluidStatus": int, "rearLeftTireStatus": int,
                "rearRightTireStatus": int, "rightWarningStatus": int,
                "samStaWarningStatus": int, "securityAlarmTriggeredStatus": int},
}
_TAB_SECTIONS = {
    "vehicleFunction": {"projectCode": str, "vehicleSeries": str},
    "weatherInfo": {"temperature": str},
    "displayConfig": {"fuelValueN": float},
}
_OPERATIONS = frozenset(("vehicles", "last_used", "management", "isc_vehicles", "category", "vehicle_tab", "vehicle_refresh"))
_DECIMAL_TIME_FIELDS = frozenset(("updateTime", "isConnectedUpdateTime", "onLineTime", "offLineTime"))


def _object(value):
    if not isinstance(value, dict):
        raise ResponseError("Expected response object")
    return value


def _field(source, name, expected):
    if name not in source:
        return ObservedField(Presence.MISSING)
    value = source[name]
    if value is None:
        return ObservedField(Presence.NULL)
    # These four Java Long fields were observed as ASCII decimal strings in
    # the authorized v6 tab response. Do not coerce other numeric signals.
    if expected is int and name in _DECIMAL_TIME_FIELDS:
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
            value = int(value)
        if type(value) is not int or not 0 <= value <= 9223372036854775807:
            raise ResponseError("Invalid vehicle time field: " + name)
    valid = type(value) is expected
    if expected is float:
        valid = type(value) in (int, float)
    if valid and expected in (int, float):
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
    if not valid:
        raise ResponseError("Invalid model field type")
    return ObservedField(Presence.VALUE, value)


def _fields(source, schema):
    return MappingProxyType({name: _field(source, name, kind)
                             for name, kind in schema.items()})


def _section(source, name, schema):
    if name not in source:
        return Presence.MISSING, _fields({}, schema)
    if source[name] is None:
        return Presence.NULL, _fields({}, schema)
    return Presence.VALUE, _fields(_object(source[name]), schema)


def _features(source):
    container = source.get("vehicleFunction")
    if container is None or "features" not in container:
        return ObservedField(Presence.MISSING)
    items = container["features"]
    if items is None:
        return ObservedField(Presence.NULL)
    if not isinstance(items, list):
        raise ResponseError("Invalid feature list")
    keys = []
    for item in items:
        key = _object(item).get("featureKey")
        if not isinstance(key, str) or not key:
            raise ResponseError("Invalid feature key")
        keys.append(key)
    return ObservedField(Presence.VALUE, tuple(keys))


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ResponseError("Duplicate JSON field")
        result[name] = value
    return result


def _reject_constant(_value):
    raise ResponseError("Non-finite JSON value")


def _load(body):
    try:
        return _object(json.loads(body, object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant))
    except (ValueError, TypeError, RecursionError):
        raise ResponseError("Invalid response JSON") from None


def parse_read_response(operation: str, wire_body: str, *,
                        response_headers: Mapping[str, str] | None = None,
                        encrypt_key: str | None = None):
    """Parse body/encryption metadata, with no HTTP-status or retry policy.

    The transport must check HTTP status separately. Success is business code
    "200" (BaseResponse.isSuccess), not inferred from HTTP 200. Legacy branches
    are rejected; encrypted envelopes are checked before codec invocation.
    """
    if operation not in _OPERATIONS:
        raise ResponseError("Unsupported read operation")
    if not isinstance(wire_body, str):
        raise ResponseError("Response body must be text")
    headers = {}
    for name, value in (response_headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ResponseError("Invalid response header")
        lower = name.lower()
        if lower in headers:
            raise ResponseError("Duplicate response header")
        headers[lower] = value
    if headers.get("encrypt", "") not in ("",):
        raise ResponseError("Unsupported legacy encryption")
    mode = headers.get("x-encrypt", "")
    if mode not in ("", "false", "true"):
        raise ResponseError("Unsupported response encryption")
    if mode == "true":
        if not isinstance(encrypt_key, str) or not encrypt_key:
            raise ResponseError("Encryption key required")
        _load(wire_body)  # Reject duplicate envelope keys before decrypting.
        try:
            wire_body = decrypted_response_body(wire_body, encrypt_key)
        except (ValueError, TypeError, UnicodeError):
            raise ResponseError("Invalid encrypted response") from None
    body = _load(wire_body)
    code = body.get("resultCode")
    if code in ("22061", "invalid_token"):
        raise AuthenticationExpired("Authentication expired")
    if not isinstance(code, str) or not code:
        raise ResponseError("Missing or invalid business result code")
    if code != "200":
        raise BusinessResponseError("Read operation failed")
    if body.get("data") is None:
        raise DataUnavailable("Response data unavailable")
    if operation == "isc_vehicles":
        items = body["data"]
        if not isinstance(items, list):
            raise ResponseError("Invalid scene vehicle list")
        return VehicleList(Presence.VALUE, tuple(
            VehicleIdentity(_fields(_object(item), _ISC_VEHICLE)) for item in items))
    data = _object(body["data"])
    tab_metadata = MappingProxyType({})
    feature_keys = ObservedField(Presence.MISSING)
    if operation in ("vehicle_tab", "vehicle_refresh"):
        if data.get("category") is None:
            raise DataUnavailable("Vehicle tab category unavailable")
        tab_metadata = MappingProxyType({
            name: CategorySection(*_section(data, name, schema))
            for name, schema in _TAB_SECTIONS.items()})
        feature_keys = _features(data)
        data = _object(data["category"])
    if operation == "last_used":
        return VehicleIdentity(_fields(data, _IDENTITY))
    if operation == "management":
        return VehicleIdentity(_fields(data, _MANAGEMENT))
    if operation == "vehicles":
        if "vehicleInfos" not in data:
            return VehicleList(Presence.MISSING)
        if data["vehicleInfos"] is None:
            return VehicleList(Presence.NULL)
        items = data["vehicleInfos"]
        if not isinstance(items, list):
            raise ResponseError("Invalid vehicle list")
        return VehicleList(Presence.VALUE, tuple(
            VehicleIdentity(_fields(_object(item), _VEHICLE)) for item in items))
    period_presence, period = _section(data, "period", _PERIOD)
    battery_presence, battery = _section(data, "hvBattery", _BATTERY)
    sections = MappingProxyType({
        name: CategorySection(*_section(data, name, schema))
        for name, schema in _SECTIONS.items()})
    return CategorySnapshot(_fields(data, _ROOT), period_presence, period,
                            battery_presence, battery, sections, tab_metadata, feature_keys,
                            _field(data, "vin", str))
