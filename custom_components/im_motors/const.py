"""Integration constants; legacy accounts remain read only by default."""
DOMAIN = "im_motors"
INTEGRATION_VERSION = "0.8.0b6"
CONF_ACCESS_MODE = "access_mode"
CONF_CONTROL_RISK = "accept_control_risk"
MODE_READ_ONLY = "read_only"
MODE_FULL = "full"


def control_enabled(entry):
    """The initial entry data fixes the mode; options cannot enable controls."""
    return (entry.data.get(CONF_ACCESS_MODE, MODE_READ_ONLY) == MODE_FULL
            and entry.data.get(CONF_CONTROL_RISK, False) is True)
CONF_TELEMETRY_INTERVAL = "telemetry_interval_minutes"
DEFAULT_TELEMETRY_INTERVAL = 60
MIN_TELEMETRY_INTERVAL = 5
CONF_DATA_DIR = "data_dir"
CONF_KEY_FILE = "key_file"
CONF_RESUME = "resume_requests"
PLATFORMS = ["sensor", "binary_sensor", "button", "device_tracker", "number"]
PENDING_FIELDS = (
    "SOC", "续航", "总里程", "充电状态", "充电功率", "充电剩余时间",
    "门窗", "车锁", "胎压", "车辆在线状态", "位置",
)

CONF_ACCOUNT_TYPE = "account_type"
ACCOUNT_PRIMARY = "primary"
ACCOUNT_AUTHORIZED = "authorized"


def account_type(entry):
    """Legacy entries keep authorized-account recording behavior."""
    return entry.options.get(CONF_ACCOUNT_TYPE,
        getattr(entry, "data", {}).get(CONF_ACCOUNT_TYPE, ACCOUNT_AUTHORIZED))
