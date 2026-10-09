"""Parse the prepared GET responses locally; never perform I/O.

Only confirmed model fields are retained. Missing, null and zero stay distinct.
Values and server messages are excluded from repr/error text. This parser does
not validate server authenticity, vehicle ownership, no-wake behavior or units
that have not been traced. Objects still contain private data in their fields.
"""
from dataclasses import dataclass
from enum import Enum
import json
import math
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
class CategorySnapshot:
    root: Mapping[str, ObservedField]
    period_presence: Presence
    period: Mapping[str, ObservedField]
    battery_presence: Presence
    battery: Mapping[str, ObservedField]

    def __repr__(self):
        return "CategorySnapshot(<redacted>)"

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
         "isConnectedUpdateTime": int, "onLineTime": int, "offLineTime": int}
_PERIOD = {
    "originalBmsPackSOCDsp": float, "bmsPackSOCDsp": float,
    "fastCharge": str, "icbSetVehRngAlg": int, "icbVehElecRng": int,
    "icbVehFuelRng": int, "imcuVehElecRng": float, "cltcVehElecRng": int,
    "power": float, "electricPilePower": float, "chargedPower": float,
    "chargingRemainTime": int, "bmsPackCrnt": float, "vehOdo": int,
    "eventTime": float,
}
_BATTERY = {"bmsChargeStatus": int}
_OPERATIONS = frozenset(("vehicles", "last_used", "management", "isc_vehicles", "category"))


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
    return CategorySnapshot(_fields(data, _ROOT), period_presence, period,
                            battery_presence, battery)
