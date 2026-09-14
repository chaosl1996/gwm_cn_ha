"""Button platform for GWM China.

BeanTech 平台(Tank 300 Hi4-T)已验证的远控命令按钮:
  - 鸣笛 / 闪灯 / 鸣笛+闪灯(找车)
  - 关闭全车窗 / 关天窗(忘关窗补救)
  - 远程启动 / 远程熄火(默认 15 分钟)
  - 前后除霜开/关(冬季预热,开=15 分钟)
  - 方向盘加热开/关(开=10 分钟)
  - 主/副驾座椅加热开/关、主/副驾座椅通风开/关(开=10 分钟)

远程空调(带温度设定)在 climate 平台,不在这里重复。
远控需在 集成 → 配置 里打开「启用远程控制」,且仅短信登录条目支持。
远程启动/空调会按需启动发动机,请确保车辆停在通风安全处。
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CMD_DEFROST_BACK_START,
    CMD_DEFROST_BACK_STOP,
    CMD_DEFROST_FRONT_START,
    CMD_DEFROST_FRONT_STOP,
    CMD_ENGINE_START,
    CMD_ENGINE_STOP,
    CMD_FLASH,
    CMD_SEAT_HEATING_START,
    CMD_SEAT_HEATING_STOP,
    CMD_SEAT_VENTILATION_START,
    CMD_SEAT_VENTILATION_STOP,
    CMD_SKYLIGHT_CLOSE,
    CMD_STEERING_WHEEL_HEATING,
    CMD_STEERING_WHEEL_HEATLESS,
    CMD_WHISTLE,
    CMD_WHISTLE_FLASH,
    CMD_WINDOW_CLOSE,
    DOMAIN,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

# 官方 APP 默认时长:远程启动/除霜 15 分钟,座椅/方向盘加热通风 10 分钟
_ENGINE_DEFROST_SECONDS = 15 * 60
_COMFORT_SECONDS = 10 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """装载 button 平台。gtsp 平台跳过其不支持的舒适类按钮。"""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    all_buttons = [
        # ---- 找车(gtsp ✓) ----
        GWMRemoteButton(coordinator, config_entry, "鸣笛找车", "horn", CMD_WHISTLE, "mdi:bullhorn", gtsp_supported=True),
        GWMRemoteButton(coordinator, config_entry, "闪灯找车", "flash", CMD_FLASH, "mdi:lightbulb-flash", gtsp_supported=True),
        GWMRemoteButton(coordinator, config_entry, "鸣笛+闪灯", "horn_flash", CMD_WHISTLE_FLASH, "mdi:alarm-light-outline", gtsp_supported=True),
        # ---- 门窗补救(gtsp ✓) ----
        GWMRemoteButton(
            coordinator, config_entry, "关闭全车窗", "close_windows", CMD_WINDOW_CLOSE,
            "mdi:car-door-lock", cmd_body={"leftFront": 0, "leftBack": 0, "rightFront": 0, "rightBack": 0},
            gtsp_supported=True,
        ),
        GWMRemoteButton(
            coordinator, config_entry, "关闭天窗", "close_sunroof", CMD_SKYLIGHT_CLOSE,
            "mdi:car-roof", cmd_body={"skyLight": 0}, gtsp_supported=True,
        ),
        # ---- 远程启动(燃油车按需启动发动机,gtsp ✓) ----
        GWMRemoteButton(
            coordinator, config_entry, "远程启动(15分钟)", "engine_start", CMD_ENGINE_START,
            "mdi:engine-outline", cmd_body={"operationTime": _ENGINE_DEFROST_SECONDS},
            gtsp_supported=True,
        ),
        GWMRemoteButton(
            coordinator, config_entry, "远程熄火", "engine_stop", CMD_ENGINE_STOP,
            "mdi:engine-off-outline", gtsp_supported=True,
        ),
        # ---- 除霜(冬季,开=15 分钟;gtsp ✗ 无对应指令) ----
        GWMRemoteButton(
            coordinator, config_entry, "前除霜开(15分钟)", "defrost_front_on", CMD_DEFROST_FRONT_START,
            "mdi:car-defrost-front", cmd_body={"operationTime": _ENGINE_DEFROST_SECONDS},
        ),
        GWMRemoteButton(coordinator, config_entry, "前除霜关", "defrost_front_off", CMD_DEFROST_FRONT_STOP, "mdi:car-defrost-front"),
        GWMRemoteButton(
            coordinator, config_entry, "后除霜开(15分钟)", "defrost_back_on", CMD_DEFROST_BACK_START,
            "mdi:car-defrost-rear", cmd_body={"operationTime": _ENGINE_DEFROST_SECONDS},
        ),
        GWMRemoteButton(coordinator, config_entry, "后除霜关", "defrost_back_off", CMD_DEFROST_BACK_STOP, "mdi:car-defrost-rear"),
        # ---- 方向盘加热(开=10 分钟;gtsp ✗) ----
        GWMRemoteButton(
            coordinator, config_entry, "方向盘加热开(10分钟)", "steer_heat_on", CMD_STEERING_WHEEL_HEATING,
            "mdi:steering", cmd_body={"operationTime": _COMFORT_SECONDS},
        ),
        GWMRemoteButton(coordinator, config_entry, "方向盘加热关", "steer_heat_off", CMD_STEERING_WHEEL_HEATLESS, "mdi:steering"),
        # ---- 座椅加热(开=10 分钟;gtsp ✗) ----
        GWMRemoteButton(
            coordinator, config_entry, "主驾座椅加热开(10分钟)", "seat_heat_driver_on", CMD_SEAT_HEATING_START,
            "mdi:car-seat-heater", cmd_body={"leftFront": 3, "operationTime": _COMFORT_SECONDS},
        ),
        GWMRemoteButton(
            coordinator, config_entry, "主驾座椅加热关", "seat_heat_driver_off", CMD_SEAT_HEATING_STOP,
            "mdi:car-seat-heater", cmd_body={"leftFront": 0, "operationMode": 1},
        ),
        GWMRemoteButton(
            coordinator, config_entry, "副驾座椅加热开(10分钟)", "seat_heat_passenger_on", CMD_SEAT_HEATING_START,
            "mdi:car-seat-heater", cmd_body={"rightFront": 3, "operationTime": _COMFORT_SECONDS},
        ),
        GWMRemoteButton(
            coordinator, config_entry, "副驾座椅加热关", "seat_heat_passenger_off", CMD_SEAT_HEATING_STOP,
            "mdi:car-seat-heater", cmd_body={"rightFront": 0, "operationMode": 1},
        ),
        # ---- 座椅通风(gtsp ✗) ----
        GWMRemoteButton(
            coordinator, config_entry, "主驾座椅通风开(10分钟)", "seat_vent_driver_on", CMD_SEAT_VENTILATION_START,
            "mdi:car-seat-cooler", cmd_body={"leftFront": 3, "operationTime": _COMFORT_SECONDS},
        ),
        GWMRemoteButton(
            coordinator, config_entry, "主驾座椅通风关", "seat_vent_driver_off", CMD_SEAT_VENTILATION_STOP,
            "mdi:car-seat-cooler", cmd_body={"leftFront": 0, "operationMode": 2},
        ),
        GWMRemoteButton(
            coordinator, config_entry, "副驾座椅通风开(10分钟)", "seat_vent_passenger_on", CMD_SEAT_VENTILATION_START,
            "mdi:car-seat-cooler", cmd_body={"rightFront": 3, "operationTime": _COMFORT_SECONDS},
        ),
        GWMRemoteButton(
            coordinator, config_entry, "副驾座椅通风关", "seat_vent_passenger_off", CMD_SEAT_VENTILATION_STOP,
            "mdi:car-seat-cooler", cmd_body={"rightFront": 0, "operationMode": 2},
        ),
    ]

    # gtsp 平台(2026 款坦克300 实测)AutoAI 通道只有核心数字指令,
    # 舒适类按钮按下去必然报错,直接不创建
    platform = ""
    if coordinator.supports_remote:
        try:
            platform = await hass.async_add_executor_job(
                coordinator.client.get_platform, coordinator.vin
            )
        except Exception:  # noqa: BLE001 - 探测失败按全量创建兜底
            platform = ""
    if platform == "gtsp":
        entities = [b for b in all_buttons if b.gtsp_supported]
        _LOGGER.info(
            "检测到 gtsp 平台,跳过 %d 个其不支持的舒适类按钮(除霜/方向盘/座椅)",
            len(all_buttons) - len(entities),
        )
    else:
        entities = all_buttons

    async_add_entities(entities)


class GWMRemoteButton(CoordinatorEntity, ButtonEntity):
    """远控按钮(鸣笛/闪灯/关窗/启动/除霜/座椅等)。"""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        config_entry: ConfigEntry,
        name: str,
        uid: str,
        control_type: str,
        icon: str,
        cmd_body: dict | None = None,
        gtsp_supported: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._control_type = control_type
        self._cmd_body = cmd_body
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.vin}_{uid}"
        self._attr_icon = icon
        # gtsp 平台(AutoAI 通道)只有数字指令:核心 7 个支持,舒适类不支持
        self.gtsp_supported = gtsp_supported
        self._attr_entity_registry_enabled_default = control_type in {
            CMD_WHISTLE, CMD_FLASH, CMD_WHISTLE_FLASH,
            CMD_WINDOW_CLOSE, CMD_SKYLIGHT_CLOSE,
            CMD_ENGINE_START, CMD_ENGINE_STOP,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.vin)},
            name=f"GWM {self.coordinator.model}",
            manufacturer="GWM",
            model=self.coordinator.model,
            sw_version=VERSION,
        )

    async def async_press(self) -> None:
        """按下按钮 → 发送远控命令。"""
        try:
            seq_no = await self.coordinator.async_send_remote_command(
                self._control_type, self._cmd_body
            )
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(str(exc)) from exc
        _LOGGER.info("远控 %s 已发送,seqNo=%s", self._control_type, seq_no)
