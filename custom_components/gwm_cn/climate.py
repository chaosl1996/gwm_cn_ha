"""Climate platform for GWM China(远程空调)。

beantech 平台:AIR_CONDITIONER_START {allowStartEng:1, operationTime, temperature},
17-31℃ 可设定温度,allowStartEng=1 表示允许远程启动发动机。

gtsp 平台(2026 款坦克300 实测):AutoAI 通道无空调专用指令,
开/关映射为 远程启动/远程熄火(cmdCode 15/16),车辆按车内上次空调
设定着车出风;温度滑条仅本地记录,不下发。

当前温度来自车况的「车厢温度」,运行状态来自 airConditionSts。
目标温度本地保存(RestoreEntity 重启恢复)。
"""
from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AC_DEFAULT_DURATION_SECONDS,
    AC_DEFAULT_TEMP,
    AC_MAX_TEMP,
    AC_MIN_TEMP,
    CMD_AIR_CONDITIONER_START,
    CMD_AIR_CONDITIONER_STOP,
    CMD_ENGINE_START,
    CMD_ENGINE_STOP,
    DOMAIN,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """装载 climate 平台。"""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    platform = ""
    if coordinator.supports_remote:
        try:
            platform = await hass.async_add_executor_job(
                coordinator.client.get_platform, coordinator.vin
            )
        except Exception:  # noqa: BLE001 - 探测失败按通用处理
            platform = ""

    async_add_entities([GWMRemoteClimate(coordinator, config_entry, platform)])


class GWMRemoteClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """远程空调(温度设定 + 开关,燃油车允许远程启动发动机)。"""

    _attr_has_entity_name = False
    _attr_name = "远程空调"
    _attr_icon = "mdi:car-air-conditioner"
    # 按项目惯例用字符串单位,不 import UnitOf* 枚举(v0.1.4 兼容性教训)
    _attr_temperature_unit = "°C"
    _attr_min_temp = AC_MIN_TEMP
    _attr_max_temp = AC_MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        platform: str = "",
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_unique_id = f"{coordinator.vin}_remote_ac"
        self._attr_target_temperature = AC_DEFAULT_TEMP
        # 平台路由:gtsp 走「远程启动/熄火」映射,其余走空调专用指令
        self._platform = (platform or "").lower()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.vin)},
            name=f"GWM {self.coordinator.model}",
            manufacturer="GWM",
            model=self.coordinator.model,
            sw_version=VERSION,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def current_temperature(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("cabin_temp")

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.coordinator.data:
            return HVACMode.OFF
        ac_on = self.coordinator.data.get("parsed_data", {}).get("air_conditioner")
        return HVACMode.HEAT_COOL if ac_on else HVACMode.OFF

    async def async_added_to_hass(self) -> None:
        """恢复重启前的目标温度。"""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            temp = last.attributes.get("temperature")
            if isinstance(temp, (int, float)) and AC_MIN_TEMP <= temp <= AC_MAX_TEMP:
                self._attr_target_temperature = temp

    async def async_set_temperature(self, **kwargs) -> None:
        """设定目标温度;空调运行中则用新温度重新下发。"""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = int(temp)
        if not AC_MIN_TEMP <= temp <= AC_MAX_TEMP:
            raise HomeAssistantError(f"空调温度仅支持 {AC_MIN_TEMP}-{AC_MAX_TEMP}℃")
        self._attr_target_temperature = temp
        self.async_write_ha_state()
        if self.hvac_mode != HVACMode.OFF:
            await self._send_start()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """开/关空调。"""
        if hvac_mode == HVACMode.OFF:
            await self._send_stop()
        elif hvac_mode in (HVACMode.HEAT_COOL, HVACMode.AUTO, HVACMode.COOL, HVACMode.HEAT):
            await self._send_start()
        else:
            raise HomeAssistantError(f"不支持的空调模式: {hvac_mode}")

    async def _send_start(self) -> None:
        if self._platform == "gtsp":
            # gtsp(AutoAI 通道)无空调专用指令:开 = 远程启动(cmdCode 15),
            # 车辆按车内上次空调设定着车出风;温度无下发通道,仅本地记录
            control_type, cmd_body = CMD_ENGINE_START, {
                "operationTime": AC_DEFAULT_DURATION_SECONDS,
            }
        else:
            control_type, cmd_body = CMD_AIR_CONDITIONER_START, {
                "allowStartEng": 1,
                "operationTime": AC_DEFAULT_DURATION_SECONDS,
                "temperature": self._attr_target_temperature,
            }
        try:
            await self.coordinator.async_send_remote_command(control_type, cmd_body)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()

    async def _send_stop(self) -> None:
        if self._platform == "gtsp":
            control_type = CMD_ENGINE_STOP
        else:
            control_type = CMD_AIR_CONDITIONER_STOP
        try:
            await self.coordinator.async_send_remote_command(control_type)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {"平台": self._platform or "未知"}
        if self._platform == "gtsp":
            attrs["说明"] = (
                "gtsp 平台:开/关映射为 远程启动/远程熄火(15分钟),"
                "空调温度以车内上次设定为准,此处温度仅本地记录"
            )
        return attrs
