"""Binary sensor platform for GWM China.

精简原则:
  1. 仅保留 HAR 响应里实际出现过非 null/非 '--' 值的字段。
  2. 4 个胎压报警 + 防盗 + 油量报警放诊断类(很少变)。
  3. 新增 ac_auto_mode(空调自动模式)、windshield_heat(前风挡加热)。
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """装载 binary_sensor 平台。"""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[BinarySensorEntity] = [
        # 门锁
        GWMDoorsUnlockedSensor(coordinator, config_entry),
        # 车门(5 门 + 引擎盖)
        GWMDoorSensor(coordinator, config_entry, "front_left"),
        GWMDoorSensor(coordinator, config_entry, "front_right"),
        GWMDoorSensor(coordinator, config_entry, "rear_left"),
        GWMDoorSensor(coordinator, config_entry, "rear_right"),
        GWMDoorSensor(coordinator, config_entry, "trunk"),
        GWMHoodSensor(coordinator, config_entry),
        # 车窗(4 窗 + 天窗)
        GWMWindowSensor(coordinator, config_entry, "front_left"),
        GWMWindowSensor(coordinator, config_entry, "front_right"),
        GWMWindowSensor(coordinator, config_entry, "rear_left"),
        GWMWindowSensor(coordinator, config_entry, "rear_right"),
        GWMSunroofSensor(coordinator, config_entry),
        # 空调 / 除霜 / 风挡加热
        GWMAirConditionerSensor(coordinator, config_entry),
        GWMAcAutoModeSensor(coordinator, config_entry),
        GWMFrontDefrosterSensor(coordinator, config_entry),
        GWMRearDefrosterSensor(coordinator, config_entry),
        GWMWindshieldHeatSensor(coordinator, config_entry),
        # 座椅 / 方向盘
        GWMSteerWheelHeatSensor(coordinator, config_entry),
        GWMSeatHeatSensor(coordinator, config_entry, "driver"),
        GWMSeatVentSensor(coordinator, config_entry, "driver"),
        GWMSeatHeatSensor(coordinator, config_entry, "passenger"),
        GWMSeatVentSensor(coordinator, config_entry, "passenger"),
        GWMSeatHeatSensor(coordinator, config_entry, "rear_left"),
        GWMSeatHeatSensor(coordinator, config_entry, "rear_right"),
        # 胎压报警(诊断类,默认启用但不显眼)
        GWMTirePressureAlarmSensor(coordinator, config_entry, "fl"),
        GWMTirePressureAlarmSensor(coordinator, config_entry, "fr"),
        GWMTirePressureAlarmSensor(coordinator, config_entry, "rl"),
        GWMTirePressureAlarmSensor(coordinator, config_entry, "rr"),
        # 安全
        GWMAntitheftSensor(coordinator, config_entry),
        GWMOilAlarmSensor(coordinator, config_entry),
    ]

    async_add_entities(entities)


class GWMBinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """GWM binary sensor 基类。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry

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
    def extra_state_attributes(self) -> dict[str, Any]:
        return {}


# ===== 门锁 =====
class GWMDoorsUnlockedSensor(GWMBinarySensorBase):
    """车门未锁(True=未锁,符合 device_class=LOCK 语义)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_doors_unlocked"
        self._attr_translation_key = "doors_unlocked"
        self._attr_device_class = BinarySensorDeviceClass.LOCK
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:car-door-lock"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        locked = self.coordinator.data.get("parsed_data", {}).get("doors_locked")
        if locked is None:
            return None
        return not locked  # 解析层 locked=True 表示已锁

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:car-door-lock"
        locked = self.coordinator.data.get("parsed_data", {}).get("doors_locked")
        if locked is None:
            return "mdi:help-circle-outline"
        return "mdi:car-door-lock" if locked else "mdi:car-door"


# ===== 车门 =====
class GWMDoorSensor(GWMBinarySensorBase):
    """车门打开(True=打开)。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, door_type: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.door_type = door_type
        self._attr_unique_id = f"{coordinator.vin}_door_{door_type}"
        self._attr_translation_key = f"door_{door_type}"
        self._attr_device_class = BinarySensorDeviceClass.DOOR
        self._attr_icon = (
            "mdi:car-back" if door_type == "trunk" else "mdi:car-door"
        )

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"door_{self.door_type}")
        )


class GWMHoodSensor(GWMBinarySensorBase):
    """引擎盖。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_hood"
        self._attr_translation_key = "hood"
        self._attr_device_class = BinarySensorDeviceClass.DOOR
        self._attr_icon = "mdi:car-outline"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("hood")


# ===== 车窗 / 天窗 =====
class GWMWindowSensor(GWMBinarySensorBase):
    """车窗(True=打开)。

    CN API 语义:WinPosnSts "1"=关,"3"=开。
    """

    def __init__(
        self, coordinator, config_entry: ConfigEntry, window_type: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.window_type = window_type
        self._attr_unique_id = f"{coordinator.vin}_window_{window_type}"
        self._attr_translation_key = f"window_{window_type}"
        self._attr_device_class = BinarySensorDeviceClass.WINDOW
        self._attr_icon = "mdi:car-door"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"window_{self.window_type}")
        )


class GWMSunroofSensor(GWMBinarySensorBase):
    """天窗(True=打开/半开)。

    CN API 语义:skyLightSts "3"=开/半开。
    """

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_sunroof"
        self._attr_translation_key = "sunroof"
        self._attr_device_class = BinarySensorDeviceClass.OPENING
        self._attr_icon = "mdi:car-roof"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("sunroof")


# ===== 空调 / 除霜 / 风挡加热 =====
class GWMAirConditionerSensor(GWMBinarySensorBase):
    """空调压缩机状态。airConditionSts。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_air_conditioner"
        self._attr_translation_key = "air_conditioner"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:air-conditioner"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("air_conditioner")


class GWMAcAutoModeSensor(GWMBinarySensorBase):
    """空调 AUTO 模式。airConditionAutoModEnaSts "1"=自动。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_ac_auto_mode"
        self._attr_translation_key = "ac_auto_mode"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:fan-auto"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("ac_auto_mode")


class GWMFrontDefrosterSensor(GWMBinarySensorBase):
    """前除霜。frontFrost。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_front_defroster"
        self._attr_translation_key = "front_defroster"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-defrost-front"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("front_defroster")


class GWMRearDefrosterSensor(GWMBinarySensorBase):
    """后除霜。backFrost。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_rear_defroster"
        self._attr_translation_key = "rear_defroster"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-defrost-rear"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("rear_defroster")


class GWMWindshieldHeatSensor(GWMBinarySensorBase):
    """前挡风玻璃加热。windows.fWinHeatSts。

    与前除霜(frontFrost,吹热风除霜)不同,这是风挡加热丝。
    """

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_windshield_heat"
        self._attr_translation_key = "windshield_heat"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-windshield"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("windshield_heat")


# ===== 座椅 / 方向盘 =====
class GWMSteerWheelHeatSensor(GWMBinarySensorBase):
    """方向盘加热。steerWheelHeat。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_steer_wheel_heat"
        self._attr_translation_key = "steer_wheel_heat"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:steering"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("steer_wheel_heat")


class GWMSeatHeatSensor(GWMBinarySensorBase):
    """座椅加热。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, seat: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.seat = seat
        self._attr_unique_id = f"{coordinator.vin}_seat_heat_{seat}"
        self._attr_translation_key = f"seat_heat_{seat}"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-seat-heater"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"seat_heat_{self.seat}")
        )


class GWMSeatVentSensor(GWMBinarySensorBase):
    """座椅通风。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, seat: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.seat = seat
        self._attr_unique_id = f"{coordinator.vin}_seat_vent_{seat}"
        self._attr_translation_key = f"seat_vent_{seat}"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:car-seat-cooler"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"seat_vent_{self.seat}")
        )


# ===== 胎压报警 =====
class GWMTirePressureAlarmSensor(GWMBinarySensorBase):
    """胎压异常报警。TirePressIndcrSts。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, position: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.position = position
        self._attr_unique_id = f"{coordinator.vin}_tire_pressure_alarm_{position}"
        self._attr_translation_key = f"tire_pressure_alarm_{position}"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:car-tire-alert"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"tire_pressure_alarm_{self.position}")
        )


# ===== 安全 =====
class GWMAntitheftSensor(GWMBinarySensorBase):
    """防盗激活。vehicleAntitheftStatus "1"=报警。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_antitheft"
        self._attr_translation_key = "antitheft"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_icon = "mdi:shield-lock"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("antitheft")


class GWMOilAlarmSensor(GWMBinarySensorBase):
    """油量报警。oilAlarmSts "1"=缺油。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_oil_alarm"
        self._attr_translation_key = "oil_alarm"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:gas-station-outline"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("oil_alarm")
