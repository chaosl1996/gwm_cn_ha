"""The GWM China integration.

基于抓包到的 accessToken 与华为云 WAF Cookie,直接查询 CN 网关
getLastStatus 接口。不做签名,代价是 token 失效(约 7 天)后
需要重新抓包更新配置。
"""
from __future__ import annotations

import logging
from datetime import timedelta

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_HW_WAF_SES_ID,
    CONF_HW_WAF_SES_TIME,
    CONF_MODEL,
    CONF_VIN,
    DOMAIN,
    SERVICE_REFRESH,
    UPDATE_INTERVAL,
    VERSION,
)
from .gwm_api import GWMChinaClient, parse_vehicle_status

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """从 config entry 装载集成。"""
    access_token = entry.data[CONF_ACCESS_TOKEN]
    hw_waf_ses_id = entry.data[CONF_HW_WAF_SES_ID]
    hw_waf_ses_time = entry.data[CONF_HW_WAF_SES_TIME]
    vin = entry.data[CONF_VIN]
    model = entry.data.get(CONF_MODEL, "GWM")

    client = GWMChinaClient(access_token, hw_waf_ses_id, hw_waf_ses_time)

    coordinator = GWMChinaUpdateCoordinator(
        hass, client=client, vin=vin, model=model, entry_id=entry.entry_id
    )
    coordinator.config_entry = entry

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 注册「手动刷新」服务(支持多实例,通过 VIN 定位)
    async def _handle_refresh(call: ServiceCall) -> None:
        target_vin = call.data.get(CONF_VIN)
        for coord in hass.data.get(DOMAIN, {}).values():
            if target_vin and coord.vin != target_vin:
                continue
            await coord.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    # 逐个 platform 加载,单个平台出错(比如 import 崩了)不拖累其它
    # (比如 sensor.py 有单位常量不存在的 bug,不至于 binary_sensor 和 tracker 全挂)
    failed: list[Platform] = []
    for platform in PLATFORMS:
        try:
            await hass.config_entries.async_forward_entry_setup(entry, platform)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "加载 platform %s 失败!请查看上方完整堆栈。该平台实体将不显示。",
                platform,
            )
            failed.append(platform)

    if len(failed) == len(PLATFORMS):
        # 全部 platform 都挂了,setup 视为失败(让用户去修,不要 entry 假上线)
        return False
    if failed:
        _LOGGER.warning(
            "以下 platform 加载失败已跳过: %s。请检查 HA 日志中的堆栈。",
            [str(p) for p in failed],
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载 config entry。"""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
    return unload_ok


class GWMChinaUpdateCoordinator(DataUpdateCoordinator):
    """管理从 GWM CN API 拉取数据的协调器。"""

    def __init__(
        self,
        hass: HomeAssistant,
        client: GWMChinaClient,
        vin: str,
        model: str,
        entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.client = client
        self.vin = vin
        self.model = model
        self.entry_id = entry_id
        self.config_entry: ConfigEntry | None = None

    async def _async_update_data(self) -> dict:
        """拉取最新车辆状态并解析。"""
        try:
            data = await self.hass.async_add_executor_job(
                self.client.get_vehicle_status, self.vin
            )
        except requests.exceptions.RequestException as exc:
            raise UpdateFailed(f"GWM CN 请求失败: {exc}") from exc

        if data is None:
            err_code = self.client.last_error_code or "unknown"
            err_desc = self.client.last_error_description or "无详细信息"
            http_status = self.client.last_http_status

            # 401/403 通常意味着 token 失效,记录更明显的日志
            if http_status in (401, 403):
                _LOGGER.warning(
                    "GWM CN 认证失败(HTTP %s),token 可能已过期,请重新抓包更新配置",
                    http_status,
                )
            raise UpdateFailed(f"获取车辆数据失败: {err_code} - {err_desc}")

        parsed = parse_vehicle_status(data)

        return {
            "raw_data": data,
            "parsed_data": parsed,
            "vin": self.vin,
            "model": self.model,
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "acquisition_time": data.get("acquisitionTime"),
            "update_time": data.get("acquisitionTime"),
        }
