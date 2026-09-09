"""Binary sensor platform for GWM China.

命名修正(核心问题修复):
  之前用 `_attr_has_entity_name = True` + translation_key 模式,
  HA 的 DOOR/WINDOW/HEAT/RUNNING/PROBLEM 这些 device_class 会
  回退到内置通用翻译(全部变成「门/窗户/过热/运行/问题」),用户分不清哪个是哪个。

  现在所有**多实例**的 binary sensor(门×7/窗×5/座椅×7/报警×6 等)
  都关闭 has_entity_name,改为**写死中文 name**,直接在 HA 里显示
  「左前门」「左前车窗」「主驾座椅加热」这种清晰名字。
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
        # 门锁(单实例)
        GWMDoorsUnlockedSensor(coordinator, config_entry),
        # 车门(6 个,多实例→写死中文名)
        GWMDoorSensor(coordinator, config_entry, "front_left", "左前门"),
        GWMDoorSensor(coordinator, config_entry, "front_right", "右前门"),
        GWMDoorSensor(coordinator, config_entry, "rear_left", "左后门"),
        GWMDoorSensor(coordinator, config_entry, "rear_right", "右后门"),
        GWMDoorSensor(coordinator, config_entry, "trunk", "后备箱门"),
        GWMHoodSensor(coordinator, config_entry),  # 引擎盖,单实例但 device_class 会冲突
        # 车窗(4 个 + 天窗)
        GWMWindowSensor(coordinator, config_entry, "front_left", "左前车窗"),
        GWMWindowSensor(coordinator, config_entry, "front_right", "右前车窗"),
        GWMWindowSensor(coordinator, config_entry, "rear_left", "左后车窗"),
        GWMWindowSensor(coordinator, config_entry, "rear_right", "右后车窗"),
        GWMSunroofSensor(coordinator, config_entry),
        # 空调相关
        GWMAirConditionerSensor(coordinator, config_entry),
        GWMAcAutoModeSensor(coordinator, config_entry),
        GWMFrontDefrosterSensor(coordinator, config_entry),
        GWMRearDefrosterSensor(coordinator, config_entry),
        GWMWindshieldHeatSensor(coordinator, config_entry),
        # 灯光(熄火时 "--"→未知,启动后才有 0/1)
        GWMLowBeamSensor(coordinator, config_entry),
        GWMHighBeamSensor(coordinator, config_entry),
        # 座椅/方向盘(写死中文名,避免全部变成「过热」「运行」)
        GWMSteerWheelHeatSensor(coordinator, config_entry),
        GWMSeatHeatSensor(coordinator, config_entry, "driver", "主驾座椅加热"),
        GWMSeatVentSensor(coordinator, config_entry, "driver", "主驾座椅通风"),
        GWMSeatHeatSensor(coordinator, config_entry, "passenger", "副驾座椅加热"),
        GWMSeatVentSensor(coordinator, config_entry, "passenger", "副驾座椅通风"),
        GWMSeatHeatSensor(coordinator, config_entry, "rear_left", "左后座椅加热"),
        GWMSeatHeatSensor(coordinator, config_entry, "rear_right", "右后座椅加热"),
        # 胎压报警(4 个 PROBLEM,写死名字避免全是「问题」)
        GWMTirePressureAlarmSensor(coordinator, config_entry, "fl", "左前胎压报警"),
        GWMTirePressureAlarmSensor(coordinator, config_entry, "fr", "右前胎压报警"),
        GWMTirePressureAlarmSensor(coordinator, config_entry, "rl", "左后胎压报警"),
        GWMTirePressureAlarmSensor(coordinator, config_entry, "rr", "右后胎压报警"),
        # 安全(防盗/油量报警,单实例但 device_class 回退,同样写死名)
        GWMAntitheftSensor(coordinator, config_entry),
        GWMOilAlarmSensor(coordinator, config_entry),
    ]

    async_add_entities(entities)


class GWMBinarySensorSingleBase(CoordinatorEntity, BinarySensorEntity):
    """单实例基类:保留 has_entity_name + translation_key 机制。"""

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


class GWMBinarySensorMultiBase(CoordinatorEntity, BinarySensorEntity):
    """多实例基类:关闭 has_entity_name,写死 name,避免 device_class 回退通用翻译。"""

    _attr_has_entity_name = False

    def __init__(self, coordinator, config_entry: ConfigEntry, name: str, uid: str) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.vin}_{uid}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.vin)},
            name=f"GWM {self.coordinator.model}",
            manufacturer="GWM",
            model=self.coordinator.model,
            sw_version=VERSION,
        )


# ===== 门锁 =====
class GWMDoorsUnlockedSensor(GWMBinarySensorMultiBase):
    """中央门锁(LOCK device_class: on=未锁,off=已锁)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "车门锁", "doors_unlocked")
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
        return not locked


# ===== 车门(6 个:前左/前右/后左/后右/后备箱/引擎盖) =====
class GWMDoorSensor(GWMBinarySensorMultiBase):
    """车门(True=打开)。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, door_type: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"door_{door_type}")
        self.door_type = door_type
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


class GWMHoodSensor(GWMBinarySensorMultiBase):
    """引擎盖。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "引擎盖", "hood")
        self._attr_device_class = BinarySensorDeviceClass.DOOR
        self._attr_icon = "mdi:car-outline"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("hood")


# ===== 车窗(4 个 + 天窗) =====
class GWMWindowSensor(GWMBinarySensorMultiBase):
    """车窗(True=打开)。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, window_type: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"window_{window_type}")
        self.window_type = window_type
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


class GWMSunroofSensor(GWMBinarySensorMultiBase):
    """天窗(True=打开;"3"=关为锁车熄火实测,开/翘起的值待实测校准)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "天窗", "sunroof")
        self._attr_device_class = BinarySensorDeviceClass.OPENING
        self._attr_icon = "mdi:car-roof"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("sunroof")


# ===== 空调 / 除霜 / 风挡加热(写死中文名,避免 RUNNING→全叫「运行」,HEAT→全叫「过热」) =====
class GWMAirConditionerSensor(GWMBinarySensorMultiBase):
    """空调压缩机状态。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "空调", "air_conditioner")
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:air-conditioner"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("air_conditioner")


class GWMAcAutoModeSensor(GWMBinarySensorMultiBase):
    """空调 AUTO 模式。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "空调自动模式", "ac_auto_mode")
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_icon = "mdi:fan-auto"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("ac_auto_mode")


class GWMFrontDefrosterSensor(GWMBinarySensorMultiBase):
    """前除霜。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "前除霜", "front_defroster")
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-defrost-front"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("front_defroster")


class GWMRearDefrosterSensor(GWMBinarySensorMultiBase):
    """后除霜。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "后除霜", "rear_defroster")
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-defrost-rear"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("rear_defroster")


class GWMWindshieldHeatSensor(GWMBinarySensorMultiBase):
    """前挡风玻璃加热(与前除霜不同,加热丝不是吹热风)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "前风挡加热", "windshield_heat")
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:car-windshield"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("windshield_heat")


# ===== 灯光(熄火时 "--"→未知,启动后 "0"=关,"1"=开) =====
class GWMLowBeamSensor(GWMBinarySensorMultiBase):
    """近光灯。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "近光灯", "low_beam")
        self._attr_device_class = BinarySensorDeviceClass.LIGHT
        self._attr_icon = "mdi:car-light-dimmed"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("low_beam")


class GWMHighBeamSensor(GWMBinarySensorMultiBase):
    """远光灯。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "远光灯", "high_beam")
        self._attr_device_class = BinarySensorDeviceClass.LIGHT
        self._attr_icon = "mdi:car-light-high"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("high_beam")


# ===== 座椅 / 方向盘 =====
class GWMSteerWheelHeatSensor(GWMBinarySensorMultiBase):
    """方向盘加热。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "方向盘加热", "steer_wheel_heat")
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:steering"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("steer_wheel_heat")


class GWMSeatHeatSensor(GWMBinarySensorMultiBase):
    """座椅加热。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, seat: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"seat_heat_{seat}")
        self.seat = seat
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


class GWMSeatVentSensor(GWMBinarySensorMultiBase):
    """座椅通风。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, seat: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"seat_vent_{seat}")
        self.seat = seat
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


# ===== 胎压报警(4 个 PROBLEM→写死中文名,避免全是「问题」) =====
class GWMTirePressureAlarmSensor(GWMBinarySensorMultiBase):
    """胎压异常报警。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, position: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"tire_pressure_alarm_{position}")
        self.position = position
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


# ===== 安全(防盗/油量报警,虽然单实例但 PROBLEM device_class 会变成「问题」,所以写死名) =====
class GWMAntitheftSensor(GWMBinarySensorMultiBase):
    """防盗激活。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "防盗状态", "antitheft")
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_icon = "mdi:shield-lock"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("antitheft")


class GWMOilAlarmSensor(GWMBinarySensorMultiBase):
    """油量报警。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "油量低报警", "oil_alarm")
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:gas-station-outline"

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("oil_alarm")
