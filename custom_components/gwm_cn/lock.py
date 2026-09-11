"""Lock platform for GWM China.

中央门锁实体:显示锁车状态(来自车况轮询),
锁车/解锁走 BeanTech T5 远控(需要在选项里启用「启用远程控制」)。

注意:远控命令经过 GWM 云端转发,执行比本地实体慢(数秒),
且仅短信登录(v0.2.0+)的条目支持;旧 token 条目只能看状态不能控。
"""
from __future__ import annotations

import logging

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CMD_VEHICLE_LOCK, CMD_VEHICLE_UNLOCK, DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """装载 lock 平台。"""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([GWMDoorLock(coordinator, config_entry)])


class GWMDoorLock(CoordinatorEntity, LockEntity):
    """中央门锁(on=已解锁,off=已锁)。"""

    _attr_has_entity_name = False
    _attr_icon = "mdi:car-key"

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        # 与二元传感器「车门锁」区分:这是可控的锁实体
        self._attr_name = "车门锁控制"
        self._attr_unique_id = f"{coordinator.vin}_door_lock"

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
    def is_locked(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("doors_locked")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {}
        if self.coordinator.supports_remote:
            attrs["远控"] = "已启用" if self.coordinator.remote_enabled else "未启用(选项里打开)"
        else:
            attrs["远控"] = "不支持(旧 token 条目,请重新添加)"
        return attrs

    async def async_lock(self, **kwargs) -> None:
        """锁车(远控)。"""
        await self._send_command(CMD_VEHICLE_LOCK)

    async def async_unlock(self, **kwargs) -> None:
        """解锁(远控)。"""
        await self._send_command(CMD_VEHICLE_UNLOCK)

    async def _send_command(self, control_type: str) -> None:
        try:
            seq_no = await self.coordinator.async_send_remote_command(control_type)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc
        _LOGGER.info("门锁远控已发送(%s),seqNo=%s", control_type, seq_no)
        # 远控执行有延迟,稍后主动刷新一次状态
        await self.coordinator.async_request_refresh()
