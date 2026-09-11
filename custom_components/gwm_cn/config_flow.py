"""Config flow for GWM China integration.

v0.2.0 起改为手机号 + 短信验证码登录(协议逆向自 ha-gwm-ev 项目),
不再需要手动抓包 accessToken。token 到期由集成自动刷新。
旧的 token 配置入口已移除;旧条目仍可继续运行,建议重新添加。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_AUTH_STATE,
    CONF_ENABLE_REMOTE,
    CONF_MODEL,
    CONF_PHONE,
    CONF_POLL_INTERVAL,
    CONF_VIN,
    DEFAULT_MODEL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)
from .gwm_cn_auth import (
    GWMCNAuthError,
    GWMCNConnectionError,
    GWMCNRiskControlError,
    GWMCNSchemaError,
    GWMChinaAuthClient,
)

_LOGGER = logging.getLogger(__name__)

STEP_PHONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE): str,
    }
)

STEP_SMS_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)


def _is_valid_phone(phone: str) -> bool:
    """中国大陆手机号:1 开头 11 位数字。"""
    return bool(re.match(r"^1\d{10}$", (phone or "").strip()))


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """GWM China 集成的配置流程(短信登录)。"""

    VERSION = 2

    def __init__(self) -> None:
        """初始化流程状态。"""
        self._phone: str = ""
        self._client: GWMChinaAuthClient | None = None
        self._vehicles: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """第一步:输入手机号。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            phone = (user_input.get(CONF_PHONE) or "").strip()
            if not _is_valid_phone(phone):
                errors["phone"] = "invalid_phone"
            else:
                self._phone = phone
                self._client = GWMChinaAuthClient(phone)
                try:
                    await self.hass.async_add_executor_job(
                        self._client.request_sms_code
                    )
                except GWMCNRiskControlError:
                    errors["base"] = "risk_control"
                except GWMCNAuthError:
                    errors["base"] = "invalid_auth"
                except (GWMCNConnectionError, GWMCNSchemaError, OSError):
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("请求验证码时未知异常")
                    errors["base"] = "unknown"
                else:
                    return await self.async_step_sms()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_PHONE_SCHEMA,
            errors=errors,
            description_placeholders={"phone": self._phone},
        )

    async def async_step_sms(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """第二步:输入短信验证码,完成三段式登录。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = (user_input.get("code") or "").strip()
            try:
                self._vehicles = await self.hass.async_add_executor_job(
                    self._client.login_sms, code
                )
            except GWMCNRiskControlError:
                errors["base"] = "risk_control"
            except GWMCNAuthError:
                errors["base"] = "code_rejected"
            except (GWMCNConnectionError, GWMCNSchemaError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("短信登录时未知异常")
                errors["base"] = "unknown"
            else:
                if not self._vehicles:
                    errors["base"] = "no_vehicles"
                elif len(self._vehicles) == 1:
                    return await self._finish(self._vehicles[0])
                else:
                    return await self.async_step_vehicle()

        return self.async_show_form(
            step_id="sms",
            data_schema=STEP_SMS_SCHEMA,
            errors=errors,
            description_placeholders={"phone": self._phone},
        )

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """第三步(多车时):选择车辆。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            vin = user_input.get(CONF_VIN)
            for vehicle in self._vehicles:
                if vehicle["vin"] == vin:
                    return await self._finish(vehicle)
            errors["base"] = "unknown"

        vehicle_options = {
            v["vin"]: (v.get("nickname") or v.get("series_name") or v.get("model_name") or v["vin"])
            for v in self._vehicles
        }
        return self.async_show_form(
            step_id="vehicle",
            data_schema=vol.Schema({vol.Required(CONF_VIN): vol.In(vehicle_options)}),
            errors=errors,
        )

    async def _finish(self, vehicle: dict[str, Any]) -> FlowResult:
        """登录成功,创建 config entry。"""
        assert self._client is not None
        vin = vehicle["vin"]
        model = vehicle.get("series_name") or vehicle.get("model_name") or DEFAULT_MODEL

        # 验证车况接口可用(顺手触发一次完整查询)
        # ⚠️ 必须放 executor:get_status 是阻塞 requests 调用,
        # 在事件循环里直接调会被新版 HA 检测并抛 RuntimeError
        try:
            await self.hass.async_add_executor_job(self._client.get_status, vin)
        except (GWMCNAuthError, GWMCNConnectionError, GWMCNSchemaError) as exc:
            _LOGGER.warning("登录后首次车况查询失败(不影响创建): %s", exc)

        return self.async_create_entry(
            title=f"GWM {model}",
            data={
                CONF_PHONE: self._phone,
                CONF_AUTH_STATE: self._client.state_dict(),
                CONF_VIN: vin,
                CONF_MODEL: model,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GwmCnOptionsFlowHandler:
        """返回选项流。"""
        return GwmCnOptionsFlowHandler()


class GwmCnOptionsFlowHandler(config_entries.OptionsFlow):
    """选项:轮询间隔 + 远控开关。"""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """管理集成选项。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            interval = user_input.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            if not MIN_POLL_INTERVAL <= interval <= MAX_POLL_INTERVAL:
                errors[CONF_POLL_INTERVAL] = "invalid_interval"
            else:
                return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)),
                    vol.Required(
                        CONF_ENABLE_REMOTE,
                        default=current.get(CONF_ENABLE_REMOTE, False),
                    ): bool,
                }
            ),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """无法连接 GWM 服务器。"""


class InvalidAuth(HomeAssistantError):
    """认证失败。"""
