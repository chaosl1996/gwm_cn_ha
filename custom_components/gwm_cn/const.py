"""Constants for the GWM China integration."""
from __future__ import annotations

# 集成 domain 与版本
DOMAIN = "gwm_cn"
VERSION = "0.1.0"

# 数据更新间隔(秒)。CN 网关有华为云 WAF,过于频繁可能被拦,先取 60s
UPDATE_INTERVAL = 60

# CN 网关与 API 路径
BASE_URL = "https://apgdm.gwmcloudcn.com"
GET_STATUS_PATH = "/mabdm/app-bff-vehicle/app-api/api/v2.0/vehicle/getLastStatus"

# 默认车型
DEFAULT_MODEL = "Tank 300"

# 配置流程字段
CONF_ACCESS_TOKEN = "access_token"
CONF_HW_WAF_SES_ID = "hw_waf_ses_id"
CONF_HW_WAF_SES_TIME = "hw_waf_ses_time"
CONF_VIN = "vin"
CONF_MODEL = "model"

# 服务
SERVICE_REFRESH = "refresh"

# 设备属性键
ATTR_VIN = "vin"
ATTR_MODEL = "model"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_UPDATE_TIME = "update_time"
ATTR_VEHICLE_NUMBER = "vehicle_number"
