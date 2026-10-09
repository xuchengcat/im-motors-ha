"""Constants for the read-only integration."""
DOMAIN = "im_motors"
INTEGRATION_VERSION = "0.3.0"
CONF_DATA_DIR = "data_dir"
CONF_KEY_FILE = "key_file"
CONF_RESUME = "resume_requests"
PLATFORMS = ["sensor", "binary_sensor"]
PENDING_FIELDS = (
    "SOC", "续航", "总里程", "充电状态", "充电功率", "充电剩余时间",
    "门窗", "车锁", "胎压", "车辆在线状态", "位置",
)
