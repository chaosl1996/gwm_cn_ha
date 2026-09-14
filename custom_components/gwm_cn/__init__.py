"""The GWM China integration.

v0.2.0 起支持两种接入方式:
  1. 短信登录(推荐):手机号+验证码,token 到期自动刷新,无需再抓包;
  2. 旧版 token:直接使用抓包的 accessToken + WAF Cookie(兼容老条目,
     token 约 7 天失效,建议重新添加走短信登录)。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_AUTH_STATE,
    CONF_ENABLE_REMOTE,
    CONF_HW_WAF_SES_ID,
    CONF_HW_WAF_SES_TIME,
    CONF_MODEL,
    CONF_PHONE,
    CONF_POLL_INTERVAL,
    CONF_VIN,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    ENTITY_PICTURE_URL,
    SERVICE_REFRESH,
    VERSION,
)
from .gwm_api import GWMChinaClient, parse_vehicle_status
from .gwm_cn_auth import (
    GWMCNAuthError,
    GWMCNConnectionError,
    GWMCNRiskControlError,
    GWMCNSchemaError,
    GWMChinaAuthClient,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.LOCK,
    Platform.BUTTON,
    Platform.CLIMATE,
]

# 集成目录下的 logo(供 entity_picture 使用)
_ICON_PATH = Path(__file__).parent / "icon.png"


class GWMIconView(HomeAssistantView):
    """/gwm_cn/icon.png 只读视图(无需登录,仅暴露 logo 文件)。

    注意:不能把整个集成目录注册为静态目录 —— 目录里有含协议密钥的源码。
    """

    url = ENTITY_PICTURE_URL
    name = "api:gwm_cn:icon"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        if not _ICON_PATH.exists():
            return web.Response(status=404)
        return web.FileResponse(
            _ICON_PATH, headers={"Cache-Control": "max-age=3600"}
        )


def _ensure_icon_view(hass: HomeAssistant) -> None:
    """注册 logo 视图(整个 HA 生命周期只注册一次)。"""
    if hass.data.get(DOMAIN, {}).get("_icon_view_registered"):
        return
    try:
        hass.http.register_view(GWMIconView())
        hass.data.setdefault(DOMAIN, {})["_icon_view_registered"] = True
    except Exception:  # noqa: BLE001
        _LOGGER.exception("注册 GWM logo 视图失败(entity_picture 将不可用)")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """从 config entry 装载集成。"""
    vin = entry.data[CONF_VIN]
    model = entry.data.get(CONF_MODEL, "GWM")

    # ======================
    # 防止 HA 自动重试造成的 "has already been setup" ValueError
    # ======================
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

    # logo 视图(device_tracker 的 entity_picture 用),只注册一次
    _ensure_icon_view(hass)

    # ---- 按条目类型选择客户端 ----
    auth_state = entry.data.get(CONF_AUTH_STATE)
    if auth_state:
        # v0.2.0+:短信登录,token 自动刷新
        client = GWMChinaAuthClient(
            entry.data.get(CONF_PHONE, ""), state=auth_state
        )
    else:
        # 旧版:抓包 token + WAF cookie
        client = GWMChinaClient(
            entry.data[CONF_ACCESS_TOKEN],
            entry.data[CONF_HW_WAF_SES_ID],
            entry.data[CONF_HW_WAF_SES_TIME],
        )

    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    coordinator = GWMChinaUpdateCoordinator(
        hass, client=client, vin=vin, model=model, entry_id=entry.entry_id,
        update_interval=poll_interval,
    )
    coordinator.config_entry = entry

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed:
        # 即使第一次 refresh 失败(网络问题/token 失效),也不要直接 return False。
        _LOGGER.warning(
            "GWM CN 第一次拉车辆数据失败(网络/token 失效?);集成仍会加载,"
            "后续 coordinator 会按间隔自动重试。"
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 注册「手动刷新」服务(支持多实例,通过 VIN 定位)
    async def _handle_refresh(call: ServiceCall) -> None:
        target_vin = call.data.get(CONF_VIN)
        for coord in hass.data.get(DOMAIN, {}).values():
            # 跳过 _icon_view_registered 等非 coordinator 的内部标志位
            if not isinstance(coord, GWMChinaUpdateCoordinator):
                continue
            if target_vin and coord.vin != target_vin:
                continue
            await coord.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh)

    # 部分平台失败不 return False(避免 SetupFailed 重试撞 already been setup)
    try:
        setup_result = await hass.config_entries.async_forward_entry_setups(
            entry, PLATFORMS
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "调用 async_forward_entry_setups(%s) 时抛了未预期异常!",
            [str(p) for p in PLATFORMS],
        )
        setup_result = False

    if not setup_result:
        _LOGGER.error(
            "以下 platform 中至少有一个加载失败: %s。集成仍以成功的 platform 继续运行。",
            [str(p) for p in PLATFORMS],
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """配置条目版本迁移。"""
    if entry.version == 1:
        # v1(token 抓包)→ v2(短信登录):保留旧字段原样运行,
        # 用户可在方便时删除重加。min 只升版本号。
        hass.config_entries.async_update_entry(entry, version=2)
        _LOGGER.info("GWM CN 条目 %s 已迁移到 v2(token 模式,建议重新添加走短信登录)", entry.entry_id)
    return True


class GWMChinaUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """管理从 GWM CN API 拉取数据的协调器(支持 token 自动刷新)。"""

    def __init__(
        self,
        hass: HomeAssistant,
        client: GWMChinaAuthClient | GWMChinaClient,
        vin: str,
        model: str,
        entry_id: str,
        update_interval: int = 60,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        self.vin = vin
        self.model = model
        self.entry_id = entry_id
        self.config_entry: ConfigEntry | None = None

    # ---------- 远控(供 lock/button 平台调用) ----------
    @property
    def remote_enabled(self) -> bool:
        """是否启用远控(选项流开关,默认关)。"""
        if self.config_entry is None:
            return False
        return bool(self.config_entry.options.get(CONF_ENABLE_REMOTE, False))

    @property
    def supports_remote(self) -> bool:
        """是否具备远控能力(仅短信登录的新客户端)。"""
        return isinstance(self.client, GWMChinaAuthClient)

    async def async_send_remote_command(
        self, control_type: str, cmd_body: dict | None = None
    ) -> str | None:
        """发送远控命令,返回 seqNo(旧客户端不支持时返回 None)。"""
        if not self.supports_remote:
            raise UpdateFailed("当前条目是旧版 token 模式,不支持远控;请重新添加集成")
        if not self.remote_enabled:
            raise UpdateFailed("远控未启用:集成配置 → 选项里打开「启用远程控制」")
        try:
            return await self.hass.async_add_executor_job(
                self.client.send_command, self.vin, control_type, cmd_body
            )
        except GWMCNRiskControlError as exc:
            raise UpdateFailed(str(exc)) from exc
        except GWMCNAuthError as exc:
            raise UpdateFailed(f"远控发送失败: {exc}") from exc
        except (GWMCNConnectionError, GWMCNSchemaError, OSError) as exc:
            raise UpdateFailed(f"远控网络失败: {exc}") from exc

    # ---------- 数据轮询 ----------
    async def _async_update_data(self) -> dict[str, Any]:
        """拉取最新车辆状态并解析(新客户端带 token 自动刷新)。"""
        if isinstance(self.client, GWMChinaAuthClient):
            return await self._async_update_auth_client()
        return await self._async_update_legacy_client()

    async def _async_update_auth_client(self) -> dict[str, Any]:
        """短信登录客户端:查询失败时自动刷新 token 重试一次。"""
        client = self.client
        assert isinstance(client, GWMChinaAuthClient)
        data = None
        try:
            data = await self.hass.async_add_executor_job(client.get_status, self.vin)
        except GWMCNRiskControlError as exc:
            raise UpdateFailed(str(exc)) from exc
        except GWMCNAuthError as exc:
            # token 失效 → 自动恢复(先重初始化短命会话,再 refreshToken)后重试一次
            _LOGGER.info("GWM CN 会话失效(%s),尝试自动恢复", exc)
            try:
                await self.hass.async_add_executor_job(client.recover_session)
                # 恢复成功,把新状态回写到 config entry 持久化
                self._persist_auth_state(client)
            except (GWMCNAuthError, GWMCNConnectionError, GWMCNSchemaError, OSError) as refresh_exc:
                # refreshToken 也死了 → 触发 reauth 流程(集成卡片出现「需要重新配置」,
                # 用户点进去重新走一遍短信即可,不用删除集成)
                if self.config_entry is not None:
                    self.config_entry.async_start_reauth(self.hass)
                raise UpdateFailed(
                    f"登录已彻底失效({refresh_exc}),请到集成卡片点「重新配置」重新短信验证"
                ) from refresh_exc
            try:
                data = await self.hass.async_add_executor_job(client.get_status, self.vin)
            except (GWMCNAuthError, GWMCNConnectionError, GWMCNSchemaError, OSError) as exc:
                raise UpdateFailed(f"恢复后查询仍失败: {exc}") from exc
        except (GWMCNConnectionError, GWMCNSchemaError, OSError) as exc:
            raise UpdateFailed(f"GWM CN 请求失败: {exc}") from exc

        if not data:
            raise UpdateFailed("车况响应为空")

        return self._build_result(data)

    async def _async_update_legacy_client(self) -> dict[str, Any]:
        """旧版 token 客户端(v2.0 网关,无签名)。"""
        client = self.client
        assert isinstance(client, GWMChinaClient)
        try:
            data = await self.hass.async_add_executor_job(
                client.get_vehicle_status, self.vin
            )
        except requests.exceptions.RequestException as exc:
            raise UpdateFailed(f"GWM CN 请求失败: {exc}") from exc

        if data is None:
            err_code = client.last_error_code or "unknown"
            err_desc = client.last_error_description or "无详细信息"
            http_status = client.last_http_status
            if http_status in (401, 403):
                _LOGGER.warning(
                    "GWM CN 认证失败(HTTP %s),token 可能已过期;建议重新添加集成走短信登录",
                    http_status,
                )
            raise UpdateFailed(f"获取车辆数据失败: {err_code} - {err_desc}")

        return self._build_result(data)

    def _build_result(self, data: dict[str, Any]) -> dict[str, Any]:
        """统一组装 coordinator 数据。"""
        parsed = parse_vehicle_status(data)
        return {
            "raw_data": data,
            "parsed_data": parsed,
            "vin": self.vin,
            "model": self.model,
            "latitude": data.get("latitude") or data.get("lat"),
            "longitude": data.get("longitude") or data.get("lon") or data.get("lng"),
            "acquisition_time": data.get("acquisitionTime") or data.get("acquisitiontime"),
            "update_time": data.get("acquisitionTime") or data.get("acquisitiontime"),
        }

    def _persist_auth_state(self, client: GWMChinaAuthClient) -> None:
        """把刷新后的 token 状态回写 config entry(重启不丢)。"""
        if self.config_entry is None:
            return
        try:
            new_data = dict(self.config_entry.data)
            new_data[CONF_AUTH_STATE] = client.state_dict()
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            _LOGGER.info("GWM CN token 已自动刷新并持久化")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("持久化刷新后的 token 状态失败(不影响本次运行)")
