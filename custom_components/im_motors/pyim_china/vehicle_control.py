"""Allowlisted App 3.2.4 controls. Static evidence is not a road test.

Never infer a protocol from model names or fall back after a failed POST.
HTTP acceptance is not evidence that a vehicle performed the action.
"""
from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from urllib.parse import urlsplit

from .offline_signature import signature
from .offline_encryption import decrypted_response_body
from .offline_read_response import _load, AuthenticationExpired, ResponseError, BusinessResponseError

LEGACY_PATH = "/app/vus/v3/vehicle/control"
V3_PATH = "/app/vus/v4/vehicle/control3"
SENTRY_PATH = "/app/vus/v4/vehicle/sentry/model"
CONTROL_PATHS = frozenset((LEGACY_PATH, V3_PATH, SENTRY_PATH))


class ControlError(ValueError):
    """Fixed local message only; no account or server values."""


# action: Chinese label, parameter names. Fixed actions also become HA buttons.
ACTIONS = {
    "lock": ("锁车", ()), "unlock": ("解锁车辆", ()),
    "open_trunk": ("打开后备箱", ()), "close_trunk": ("关闭后备箱", ()),
    "unlock_trunk": ("后备箱解锁（旧协议）", ()),
    "open_windows": ("打开车窗", ()), "close_windows": ("关闭车窗", ()),
    "vent_windows": ("车窗通风", ()),
    "find_car": ("鸣笛闪灯寻车", ()), "stop_find_car": ("停止寻车（新协议）", ()),
    "open_bonnet": ("打开前舱盖（新协议）", ()),
    "close_bonnet": ("关闭前舱盖（新协议）", ()),
    "open_charge_port": ("打开充电口（新协议）", ()),
    "start_charge": ("开始充电", ()), "stop_charge": ("停止充电", ()),
    "battery_warm_on": ("开启电池预热（新协议）", ()),
    "battery_warm_off": ("关闭电池预热（新协议）", ()),
    "ac_on": ("开启空调", ("duration",)), "ac_off": ("关闭空调", ()),
    "ac_temperature": ("设置空调温度", ("temperature", "duration")),
    "seat_heat": ("座椅加热", ("position", "level")),
    "seat_vent": ("座椅通风", ("position", "level")),
    "steering_heat": ("方向盘加热", ("level",)),
    "charge_target": ("设置充电目标", ("percent",)),
    "charge_current": ("设置充电电流（新协议）", ("current",)),
    "schedule_charge": ("预约充电时间段", ("start_hour", "start_minute", "end_hour", "end_minute")),
    "cancel_schedule_charge": ("关闭预约充电（新协议）", ()),
    "sentry_on": ("开启哨兵模式", ()), "sentry_off": ("关闭哨兵模式", ()),
}
V3_ONLY = frozenset(("stop_find_car", "open_bonnet", "close_bonnet", "open_charge_port",
    "battery_warm_on", "battery_warm_off", "charge_current", "cancel_schedule_charge"))
LEGACY_ONLY = frozenset(("unlock_trunk",))
POSITIONS = {"front_left": (1, 1), "front_right": (2, 2), "rear_left": (4, 3),
             "rear_right": (8, 4), "rear_middle": (16, 5), "third_left": (32, 6), "third_right": (64, 7)}


def supported_actions(v3):
    if type(v3) is not bool:
        return ()
    return tuple(name for name in ACTIONS if name not in (LEGACY_ONLY if v3 else V3_ONLY))


def _integer(params, name, low, high, default=None):
    value = params.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise ControlError("Invalid control parameter: " + name)
    return value


def compile_command(action, params, *, v3):
    """Return path, body fields for traced calls; reject extra/unknown inputs."""
    if action not in supported_actions(v3):
        raise ControlError("Control unavailable for this vehicle protocol")
    if not isinstance(params, dict) or set(params) - set(ACTIONS[action][1]):
        raise ControlError("Unexpected control parameters")
    if action in ("sentry_on", "sentry_off"):
        return SENTRY_PATH, {"sentryModeReq": 1 if action == "sentry_on" else 2,
                             "sentryModeSource": 2, "rearViewMirrorTypeReq": 0}
    duration = 0
    fixed = {
        "lock": ("DOOR_LOCK_UNLOCK", "1", "RMT_LOCK_UNLOCK", "1"),
        "unlock": ("DOOR_LOCK_UNLOCK", "2", "RMT_LOCK_UNLOCK", "2"),
        "open_trunk": ("TAILGATE_SWITCH", "5", "RMT_TAILGATE", "1:0"),
        "close_trunk": ("TAILGATE_SWITCH", "1", "RMT_TAILGATE", "2:0"),
        "unlock_trunk": ("LOCK_UNLOCK_TRUNK", "2", "", ""),
        # Issue #7 field report (2026 LS6): 1=vent, 2=close, 3=open on both protocols.
        "open_windows": ("WINDOW_SWITCH", "3", "RMT_WINDOW", "3:254"),
        "close_windows": ("WINDOW_SWITCH", "2", "RMT_WINDOW", "2:254"),
        "vent_windows": ("WINDOW_SWITCH", "1", "RMT_WINDOW", "1:254"),
        "find_car": ("LIGHT_AND_WHISTLE", "1", "RMT_LIGHT_AND_WHISTLE", "1"),
        "stop_find_car": ("", "", "RMT_LIGHT_AND_WHISTLE", "2"),
        "open_bonnet": ("", "", "RMT_BONNET", "5"),
        "close_bonnet": ("", "", "RMT_BONNET", "1"),
        "open_charge_port": ("", "", "RMT_CHARGE_DOOR", "3"),
        "start_charge": ("CHARGE_WIRED", "1", "RMT_CHARGE_START_STOP", "1"),
        "stop_charge": ("CHARGE_WIRED", "2", "RMT_CHARGE_START_STOP", "2"),
        "battery_warm_on": ("", "", "RMT_BATTERY_WARM", "2"),
        "battery_warm_off": ("", "", "RMT_BATTERY_WARM", "1"),
        "ac_off": ("AIR_SWITCH", "0", "RMT_AC", "0"),
        "cancel_schedule_charge": ("", "", "RMT_BOOKING_CHARGING", "2"),
    }
    if action in fixed:
        old_type, old_value, new_type, new_value = fixed[action]
        command, value = (new_type, new_value) if v3 else (old_type, old_value)
        if action == "find_car" and not v3:
            duration = 30
    elif action in ("ac_on", "ac_temperature"):
        minutes = _integer(params, "duration", 1, 30, 10)
        if action == "ac_on":
            command, value = ("RMT_AC", f"1:{minutes}") if v3 else ("AIR_SWITCH", f"1:{minutes}")
        else:
            temperature = params.get("temperature")
            if type(temperature) not in (int, float) or not 16 <= temperature <= 30 or temperature * 2 != int(temperature * 2):
                raise ControlError("Invalid control parameter: temperature")
            if not v3 and temperature != int(temperature):
                raise ControlError("Legacy temperature requires whole degrees")
            command, value = (("RMT_AC", f"6:{minutes}:{float(temperature)}") if v3
                              else ("AIR_TEMP", str(int(temperature))))
    elif action in ("seat_heat", "seat_vent"):
        position = params.get("position")
        if not isinstance(position, str) or position not in POSITIONS:
            raise ControlError("Invalid control parameter: position")
        level = _integer(params, "level", 0, 3)
        old, new = POSITIONS[position]
        command, value = (("RMT_SEAT", f"{1 if action == 'seat_heat' else 2}:{level}:{new}") if v3
            else ("SEAT_HEATING" if action == "seat_heat" else "SEAT_VENT", f"{level}:{old}"))
    elif action == "steering_heat":
        level = _integer(params, "level", 0, 3)
        command, value = ("RMT_SEAT", f"3:{level}:9") if v3 else ("STEERING_WHEEL_HEATING", str(level))
    elif action == "charge_target":
        percent = _integer(params, "percent", 40, 100)
        if not v3 and percent % 10:
            raise ControlError("Legacy charge target requires ten percent increments")
        command, value = (("RMT_CHARGE_CUT_OFF_POWER", str(percent)) if v3
            else ("REMOTE_CHARGING_CUTOFF_SETTING", str((percent - 30) // 10)))
    elif action == "charge_current":
        current = params.get("current")
        if type(current) is not str or current not in ("6A", "8A", "16A", "max"):
            raise ControlError("Invalid control parameter: current")
        command, value = "RMT_CHARGE_ELE_CURRENT", str(("6A", "8A", "16A", "max").index(current) + 1)
    elif action == "schedule_charge":
        values = [_integer(params, key, 0, high) for key, high in
                  (("start_hour", 23), ("start_minute", 59), ("end_hour", 23), ("end_minute", 59))]
        command = "RMT_BOOKING_CHARGING" if v3 else "BOOKING_CHARGING_SETTINGS"
        value = "1:" + ":".join(map(str, values))
    else:
        raise ControlError("Unsupported control action")
    return V3_PATH if v3 else LEGACY_PATH, {"commandType": command, "commandValue": value,
        "duration": duration, "operateDevice": "ANDROID"}


@dataclass(frozen=True, repr=False)
class PreparedControlRequest:
    method: str
    url: str
    headers: object
    body: bytes

    def __repr__(self):
        return "PreparedControlRequest(<redacted>)"


def prepare_control(config, *, vin, uid, action, parameters, v3, nonce, timestamp_ms, baseline_headers):
    origin = urlsplit(config.base_url)
    if (origin.scheme != "https" or origin.netloc not in ("mh.immotors.com", "mh.immotors.com:443")
            or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise ControlError("Unexpected control origin")
    if not isinstance(vin, str) or not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        raise ControlError("Invalid owned vehicle")
    if not isinstance(uid, str) or not uid or len(uid) > 128 or any(c.isspace() or ord(c) < 32 for c in uid):
        raise ControlError("Account UID unavailable; sign in again using SMS")
    path, payload = compile_command(action, parameters, v3=v3)
    payload["vin"] = vin
    if path != SENTRY_PATH:
        payload["userAccountId"] = uid
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if type(timestamp_ms) is not int or timestamp_ms <= 0 or not isinstance(nonce, str) or not nonce or any(ord(c) < 32 for c in nonce):
        raise ControlError("Invalid control request timestamp or nonce")
    headers = {k: v for k, v in baseline_headers.items() if k.lower() != "content-type"}
    reserved = {"x-app-key", "x-nonce", "x-timestamp", "x-signature", "encrypt", "x-encrypt"}
    if any(name.lower() in reserved for name in headers):
        raise ControlError("Unexpected baseline control header")
    signed = {"x-app-key": "android", "x-nonce": nonce, "x-timestamp": str(timestamp_ms)}
    headers.update(signed)
    headers["Content-Type"] = "application/json; charset=UTF-8"
    headers["x-signature"] = signature("POST", path, signed, config.app_secret, body=body)
    return PreparedControlRequest("POST", config.base_url.rstrip("/") + path,
                                  MappingProxyType(headers), body.encode("utf-8"))


def parse_control_response(response, config):
    """Only report accepted. Async vehicle results require the App push channel."""
    headers = {k.lower(): v for k, v in response.headers.items()}
    if headers.get("encrypt") or headers.get("x-encrypt", "") not in ("", "false", "true"):
        raise ResponseError("Unsupported control response encryption")
    wire = response.body
    _load(wire)
    if headers.get("x-encrypt") == "true":
        wire = decrypted_response_body(wire, config.encrypt_key)
    envelope = _load(wire)
    code = envelope.get("resultCode")
    if code in ("22061", "invalid_token"):
        raise AuthenticationExpired("Control authentication expired")
    if not isinstance(code, str) or not code:
        raise ResponseError("Missing control result code")
    if code != "200":
        raise BusinessResponseError("Vehicle control rejected by server")
    return "accepted"
