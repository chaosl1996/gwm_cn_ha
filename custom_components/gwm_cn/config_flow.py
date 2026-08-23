"""Config flow for GWM China integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_HW_WAF_SES_ID,
    CONF_HW_WAF_SES_TIME,
    CONF_MODEL,
    CONF_VIN,
    DEFAULT_MODEL,
    DOMAIN,
)
from .gwm_api import GWMChinaClient

_LOGGER = logging.getLogger(__name__)

# 配置表单 schema
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): str,
        vol.Required(CONF_HW_WAF_SES_ID): str,
        vol.Required(CONF_HW_WAF_SES_TIME): str,
        vol.Required(CONF_VIN): str,
        vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): str,
    }
)


def _is_valid_jwt(token: str) -> bool:
    """简单校验 JWT 格式(三段 base64,以 . 分隔)。"""
    if not token or not isinstance(token, str):
        return False
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def _is_valid_vin(vin: str) -> bool:
    """简单校验 VIN:17 位字母数字。"""
    return bool(re.match(r"^[A-HJ-NPR-Z0-9]{17}$", vin or ""))


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """校验用户输入。

    用提供的 token + WAF cookie 调一次 getLastStatus,确认能拿到数据。
    """
    access_token = data[CONF_ACCESS_TOKEN].strip()
    hw_waf_ses_id = data[CONF_HW_WAF_SES_ID].strip()
    hw_waf_ses_time = data[CONF_HW_WAF_SES_TIME].strip()
    vin = data[CONF_VIN].strip().upper()
    model = (data.get(CONF_MODEL) or DEFAULT_MODEL).strip()

    if not _is_valid_jwt(access_token):
        raise InvalidAuth("accessToken 格式不正确,应为 JWT 三段式")
    if not hw_waf_ses_id:
        raise InvalidAuth("HWWAFSESID 不能为空")
    if not hw_waf_ses_time:
        raise InvalidAuth("HWWAFSESTIME 不能为空")
    if not _is_valid_vin(vin):
        raise InvalidAuth("VIN 必须是 17 位字母数字(不含 I/O/Q)")

    client = GWMChinaClient(access_token, hw_waf_ses_id, hw_waf_ses_time)

    try:
        vehicle_data = await hass.async_add_executor_job(
            client.get_vehicle_status, vin
        )
    except Exception as exc:
        _LOGGER.exception("GWM CN 连接异常: %s", exc)
        raise CannotConnect(f"连接 GWM 服务器失败: {exc}") from exc

    if vehicle_data is None:
        err_code = client.last_error_code or "unknown"
        err_desc = client.last_error_description or "无详细信息"
        if err_code == "http_error" and client.last_http_status in (401, 403):
            raise InvalidAuth(f"认证失败 ({err_desc})")
        raise CannotConnect(f"获取车辆数据失败: {err_code} - {err_desc}")

    return {
        CONF_VIN: vin,
        CONF_MODEL: model,
        "title": f"GWM {model}",
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """GWM China 集成的配置流程。"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """第一步:让用户填写 token / cookie / VIN。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("GWM CN 配置流程未知异常")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info[CONF_VIN])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN].strip(),
                        CONF_HW_WAF_SES_ID: user_input[CONF_HW_WAF_SES_ID].strip(),
                        CONF_HW_WAF_SES_TIME: user_input[CONF_HW_WAF_SES_TIME].strip(),
                        CONF_VIN: info[CONF_VIN],
                        CONF_MODEL: info[CONF_MODEL],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "vin_example": "LGWFF7A56TJ049057",
            },
        )


class CannotConnect(HomeAssistantError):
    """无法连接 GWM 服务器。"""


class InvalidAuth(HomeAssistantError):
    """认证失败(token/cookie 无效)。"""
