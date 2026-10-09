"""Confirmed, read-only mappings. Unknown codes/units never acquire defaults."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.binary_sensor import BinarySensorDeviceClass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    group: str
    field: str
    unit: str | None = None
    device_class: object = None
    conversion: str = "raw"
    diagnostic: bool = False


SENSORS = {
    "soc": FieldSpec("电量", "period", "originalBmsPackSOCDsp", "%", SensorDeviceClass.BATTERY, "percent"),
    "soc_secondary": FieldSpec("电量（BMS 显示字段）", "period", "bmsPackSOCDsp", "%", SensorDeviceClass.BATTERY, "percent", True),
    "cltc_range": FieldSpec("CLTC 续航", "period", "cltcVehElecRng", "km", SensorDeviceClass.DISTANCE, "nonnegative"),
    "estimated_range": FieldSpec("估算续航", "period", "imcuVehElecRng", "km", SensorDeviceClass.DISTANCE, "nonnegative"),
    "odometer": FieldSpec("总里程", "period", "vehOdo", "km", SensorDeviceClass.DISTANCE, "odometer"),
    "cabin_temperature": FieldSpec("车内温度", "period", "acInCarTemperature", "°C", SensorDeviceClass.TEMPERATURE),
    "outside_temperature": FieldSpec("车辆外温", "period", "outsideCarTemperature", "°C", SensorDeviceClass.TEMPERATURE),
    "weather_temperature": FieldSpec("天气温度", "weatherInfo", "temperature", "°C", SensorDeviceClass.TEMPERATURE, "weather"),
    "ac_left_temperature": FieldSpec("空调左侧设定温度", "ac", "aclTemDspCmd", "°C", SensorDeviceClass.TEMPERATURE, "setpoint"),
    "ac_right_temperature": FieldSpec("空调右侧设定温度", "ac", "acrTemDspCmd", "°C", SensorDeviceClass.TEMPERATURE, "setpoint"),
    "charge_status": FieldSpec("充电状态", "hvBattery", "bmsChargeStatus", conversion="charge_status"),
    "charge_target": FieldSpec("充电目标电量", "imcuCharge", "imcuChrgTrgtSOCDspCmd", "%", conversion="charge_target"),
    "charge_remaining": FieldSpec("充电剩余时间", "period", "chargingRemainTime", "min", SensorDeviceClass.DURATION, "remaining"),
    "charge_elapsed": FieldSpec("当前充电耗时", "period", "chrgngSpdngTime", "min", SensorDeviceClass.DURATION, "elapsed"),
    "charge_power": FieldSpec("车辆充电功率", "period", "power", "kW", SensorDeviceClass.POWER, "charging_power"),
    "pile_power": FieldSpec("充电桩功率", "period", "electricPilePower", "kW", SensorDeviceClass.POWER, "charging_power"),
    "reservation_start": FieldSpec("预约充电开始", "imcuCharge", "imcuReserStHourDspCmd", conversion="start"),
    "reservation_stop": FieldSpec("预约充电结束", "imcuCharge", "imcuReserSpHourDspCmd", conversion="stop"),
    "driving_state": FieldSpec("车辆运行分类", "basic", "shifterPosition", conversion="driving"),
    "vehicle_series": FieldSpec("首页车辆系列", "vehicleFunction", "vehicleSeries", diagnostic=True),
    "tab_project_code": FieldSpec("首页车型项目代码", "vehicleFunction", "projectCode", diagnostic=True),
    "vehicle_updated": FieldSpec("云端车况更新时间", "root", "updateTime", device_class=SensorDeviceClass.TIMESTAMP, conversion="timestamp", diagnostic=True),
    "odometer_raw": FieldSpec("里程原始值", "period", "vehOdo", diagnostic=True),
    "charged_power_raw": FieldSpec("chargedPower 原始值（单位待定）", "period", "chargedPower", diagnostic=True),
    "lock_raw": FieldSpec("车锁原始码", "lock", "vehLockingState", diagnostic=True),
    "charge_status_raw": FieldSpec("充电状态原始码", "hvBattery", "bmsChargeStatus", diagnostic=True),
    "charge_remaining_raw": FieldSpec("充电剩余时间原始值", "period", "chargingRemainTime", "min", diagnostic=True),
    "charge_elapsed_raw": FieldSpec("充电耗时原始值", "period", "chrgngSpdngTime", "min", diagnostic=True),
    "steering_heat_raw": FieldSpec("方向盘加热原始码", "steeringWheel", "steeringWheelHeatingStatus", diagnostic=True),
}
BINARY_SENSORS = {
    "online": FieldSpec("车辆在线", "root", "isOnLine", device_class=BinarySensorDeviceClass.CONNECTIVITY, conversion="boolean"),
    "connected": FieldSpec("车辆连接", "root", "isConnected", device_class=BinarySensorDeviceClass.CONNECTIVITY, conversion="boolean"),
    "lock_display": FieldSpec("App 云端车锁", "lock", "vehLockingState", device_class=BinarySensorDeviceClass.LOCK, conversion="lock"),
    "reservation": FieldSpec("预约充电开启", "imcuCharge", "imcuReserCtrlDspCmd", conversion="reservation"),
}
for prefix, label, pressure, temperature, window, door in (
    ("fl", "左前", "frontLeftTirePressure", "flTireTem", "frontLeftWindowPosition", "frontLeftDoorStatus"),
    ("fr", "右前", "frontRightTirePressure", "frTireTem", "frontRightWindowPosition", "frontRightDoorStatus"),
    ("rl", "左后", "rearLeftTirePressure", "rlTireTem", "rearLeftWindowPosition", "rearLeftDoorOpenStatus"),
    ("rr", "右后", "rearRightTirePressure", "rrTireTem", "rearRightWindowPosition", "rearRightDoorOpenStatus"),
):
    SENSORS[prefix + "_pressure"] = FieldSpec(label + "胎压", "period", pressure, "bar", SensorDeviceClass.PRESSURE, "pressure")
    SENSORS[prefix + "_temperature"] = FieldSpec(label + "胎温", "period", temperature, "°C", SensorDeviceClass.TEMPERATURE)
    SENSORS[prefix + "_window_position"] = FieldSpec(label + "车窗开度", "period", window, "%", conversion="percent")
    BINARY_SENSORS[prefix + "_window"] = FieldSpec(label + "车窗", "period", window, device_class=BinarySensorDeviceClass.WINDOW, conversion="window")
    BINARY_SENSORS[prefix + "_door"] = FieldSpec(label + "车门", "door", door, device_class=BinarySensorDeviceClass.DOOR, conversion="boolean")
for key, name, wire in (("bonnet", "前舱盖", "bonnetOpenStatus"), ("trunk", "尾门", "trunkOpenStatus"), ("charge_cap", "充电口盖", "chargeCapOpenStatus")):
    BINARY_SENSORS[key] = FieldSpec(name, "door", wire, device_class=BinarySensorDeviceClass.OPENING, conversion="boolean")
for prefix, label in (("fl", "左前"), ("fr", "右前"), ("sl", "二排左"), ("sm", "二排中"), ("sr", "二排右"), ("tl", "三排左"), ("tm", "三排中"), ("tr", "三排右")):
    for suffix, name in (("SeatHeatLvl", "加热"), ("SeatVentLvl", "通风")):
        SENSORS[prefix + "_" + suffix] = FieldSpec(label + "座椅" + name + "原始级别", "seat", prefix + suffix, diagnostic=True)

CHARGE_STATES = (
    "none", "on_board_charging", "charge_done", "balancing", "charge_fault",
    "connecting", "connected_not_recognized", "connected_not_charged", "charge_cease",
    "charge_reserved", "off_board_charging", "discharging_gun_connecting",
    "multiple_charging", "discharging", "cease", "discharging_done", "wireless_charging",
)
# Only explicit charging modes. Balancing/discharging/reservation are distinct.
ACTIVE_CHARGING = frozenset((1, 10, 12, 16))


def observed(snapshot, group, name):
    if snapshot is None:
        return None
    if group in ("root", "period", "hvBattery"):
        fields = getattr(snapshot, {"hvBattery": "battery"}.get(group, group))
    else:
        section = snapshot.sections.get(group) or snapshot.tab_metadata.get(group)
        fields = section.fields if section else {}
    return fields.get(name)


def raw(snapshot, group, name):
    item = observed(snapshot, group, name)
    return item.value if item else None


def timestamp(value):
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def normalized(snapshot, spec):
    value = raw(snapshot, spec.group, spec.field)
    conversion = spec.conversion
    if conversion == "charge_target":
        return snapshot.charge_target_percent if snapshot else None
    if conversion == "driving":
        ready = raw(snapshot, "powerTrain", "eptReadyStatus")
        if value == 1 and ready == 1:
            return "ready"
        if value in (0, 1) and ready == 0:
            return "parking"
        if value is not None and value >= 2 and ready == 1:
            return "driving"
        return None
    if conversion in ("start", "stop"):
        control = raw(snapshot, "imcuCharge", "imcuReserCtrlDspCmd")
        if control not in (1, 3) or (conversion == "stop" and control != 1):
            return None
        part = "St" if conversion == "start" else "Sp"
        hour = raw(snapshot, "imcuCharge", f"imcuReser{part}HourDspCmd")
        minute = raw(snapshot, "imcuCharge", f"imcuReser{part}MinuteDspCmd")
        return f"{hour:02d}:{minute:02d}" if hour is not None and minute is not None and 0 <= hour < 24 and 0 <= minute < 60 else None
    if value is None:
        return None
    if conversion == "odometer":
        # Integer kilometres; sentinel/negative values are not a meter reset.
        return value if type(value) is int and 0 <= value < 0x7FFFFFFF else None
    if conversion == "percent":
        return value if 0 <= value <= 100 else None
    if conversion == "nonnegative":
        return value if value >= 0 else None
    if conversion == "pressure":
        # APK hides status 1 and suppresses nonpositive readings.
        warning = raw(snapshot, "warning", spec.field.replace("Pressure", "Status"))
        return value / 100 if value > 0 and warning != 1 else None
    if conversion == "weather":
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError, OverflowError):
            return None
    if conversion == "setpoint":
        return value if value > 0 else None
    if conversion == "charge_status":
        return CHARGE_STATES[value] if 0 <= value < len(CHARGE_STATES) else None
    if conversion in ("remaining", "elapsed", "charging_power"):
        if raw(snapshot, "hvBattery", "bmsChargeStatus") not in ACTIVE_CHARGING:
            return None
        if conversion == "remaining":
            return snapshot.remaining_charge_minutes
        return value if value >= 0 else None
    if conversion == "boolean":
        return {0: False, 1: True}.get(value)
    if conversion == "window":
        return value > 0 if 0 <= value <= 100 else None
    if conversion == "lock":
        # Code 3 is corroborated by the phone. Other protocol codes pending.
        # HA BinarySensorDeviceClass.LOCK: on=unlocked, off=locked.
        return False if value == 3 else None
    if conversion == "reservation":
        return True if value in (1, 3) else None
    if conversion == "timestamp":
        return timestamp(value)
    return value
