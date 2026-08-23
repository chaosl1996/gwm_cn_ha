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

    # ======================
    # 防止 HA 自动重试造成的 "has already been setup" ValueError
    # ======================
    # 场景:上个 setup 调用部分 platform 成功了,但最后我们 return False(之前版本错误的做法),
    # 触发 HA 自动重试 config entry,HA 内部 entity_component 还保留上次成功的 platform,
    # 再次调用 async_forward_entry_setups 就会抛:
    #   ValueError: Config entry xxx for gwm_cn.sensor has already been setup!
    # 所以:如果这个 entry_id 已经在 hass.data[DOMAIN] 里(说明是重试、残留未清),
    # 先显式 unload 所有 platforms + 移除服务,给本次 setup 留一个干净的状态。
    hass.data.setdefault(DOMAIN, {})
    if entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.warning(
            "检测到 config entry %s 残留的 setup 状态(通常是上次 setup 失败 HA 自动重试)。"
            "先清理所有 platforms 避免 ValueError: has already been setup。",
            entry.entry_id,
        )
        try:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("清理残留 platforms 时忽略异常,继续 setup")
        # 只清掉 service 相关(最后一个 entry unload 时整体 remove,这里保守不 remove,
        # 直接 async_register 也不会重复抛错,HA 内部去重了)

    client = GWMChinaClient(access_token, hw_waf_ses_id, hw_waf_ses_time)

    coordinator = GWMChinaUpdateCoordinator(
        hass, client=client, vin=vin, model=model, entry_id=entry.entry_id
    )
    coordinator.config_entry = entry

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed:
        # 即使第一次 refresh 失败(网络问题/token 失效),也不要直接 return False。
        # 不然会导致:① HA 把 entry 标记为 SetupFailed 并持续重试;② 如果之前有残留,
        # 下次重试会撞 "has already been setup"。
        # 改为:打 WARNING 继续 setup(后续 coordinator 会按 interval 轮询重试)。
        _LOGGER.warning(
            "GWM CN 第一次拉车辆数据失败(网络/token 失效?);集成仍会加载,"
            "后续 coordinator 每 5 分钟会自动重试。请检查 token 是否过期。"
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 注册「手动刷新」服务(支持多实例,通过 VIN 定位)
    async def _handle_refresh(call: ServiceCall) -> None:
        target_vin = call.data.get(CONF_VIN)
        for coord in hass.data.get(DOMAIN, {}).values():
            if target_vin and coord.vin != target_vin:
                continue
            await coord.async_request_refresh()

    # HA 的 async_register 不会因为重复调而报错,会静默覆盖/去重
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    # ============================================================
    # 关键:部分 platform 失败 不要 return False!
    # ============================================================
    # async_forward_entry_setups 返回 False 的含义是「至少 1 个 platform setup 失败」。
    # 之前版本我写成:失败就 return False → HA 把整个集成标记 SetupFailed 并重试,
    # 但上一轮成功 setup 的 platform 没有被自动 unload,下一轮 HA 调 forward_entry_setups
    # 时直接抛 ValueError: "has already been setup" (用户当前遇到的报错)。
    #
    # 官方集成的正确做法:部分 platform 失败 → 只打日志,继续 return True,
    # 让已经成功的 platform(比如 binary_sensor/device_tracker)继续工作,
    # 用户至少能看到部分实体。日志里也会有 "Setup failed for xxx" 的完整堆栈。
    try:
        setup_result = await hass.config_entries.async_forward_entry_setups(
            entry, PLATFORMS
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "调用 async_forward_entry_setups(%s) 时抛了未预期异常!"
            "请查看上方完整堆栈(通常是某个 platform 的 import/单位枚举崩溃)。"
            "集成仍会以部分功能继续加载。",
            [str(p) for p in PLATFORMS],
        )
        setup_result = False

    if not setup_result:
        _LOGGER.error(
            "以下 platform 中至少有一个加载失败: %s。"
            "请在 HA 日志里搜索关键词 'Setup failed for' 定位到具体 platform 和堆栈,"
            "把那几行贴出来就能秒修。集成仍以成功的 platform 继续运行(不会触发 HA 自动重试冲突)。",
            [str(p) for p in PLATFORMS],
        )

    # 无论 setup_result 是 True 还是 False,都 return True。
    # 好处:① 不再触发 HA 自动重试 → 不会撞 "has already been setup"
    #      ② 成功的 platform 正常出实体,用户能用一部分 > 0
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
