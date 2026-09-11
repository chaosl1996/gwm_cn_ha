"""Sensor platform for GWM China.

命名策略:全部实体统一采用 `_attr_has_entity_name = False` + 写死 `_attr_name` 中文,
彻底绕开 HA 的 device_class 默认翻译(DOOR→「门」,HEAT→「过热」等)导致的
「所有同类实体名字都一样、找不到哪个是哪个」的问题。

胎压/胎温/门窗 这些多实例实体尤为重要。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
# ⚠️ 不要 import 任何 UnitOf* 枚举!
# HA 不同版本的枚举成员名频繁变化,比如:
#   "kPa" (我们写的) 根本不存在 → 应该是 KILOPASCAL 单数
#   "km" → 可能是 KILOMETER 单数(不同版本不同)
#   UnitOfVolumeFlowRate.LITERS_PER_KILOMETER → 维度不对,枚举根本没有
#   homeassistant.const.PERCENTAGE → 2024.x 后删除
# 全改用字符串字面量单位("kPa"/"km"/"L"/"°C"/"%"/"L/100km"),
# HA 对字符串单位完全兼容,也不校验是不是枚举成员,0 兼容性问题。
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
    """装载 sensor 平台。"""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[SensorEntity] = [
        # 油电(写死中文名,直接出现在「传感器」卡片头)
        GWMFuelVolumeSensor(coordinator, config_entry),
        GWMFuelRangeSensor(coordinator, config_entry),
        GWMFuelGaugeSensor(coordinator, config_entry),
        GWMHevBatteryPercentSensor(coordinator, config_entry),
        GWMEvRangeSensor(coordinator, config_entry),
        GWMMileageSensor(coordinator, config_entry),
        GWMAvgFuelConsumptionSensor(coordinator, config_entry),
        # 温度
        GWMCabinTempSensor(coordinator, config_entry),
        # 引擎
        GWMEngineStateSensor(coordinator, config_entry),
        GWMGearSensor(coordinator, config_entry),
        # 电源(power:熄火="0",上电后 1/2/3)
        GWMPowerStateSensor(coordinator, config_entry),
        # 能耗/电池
        GWMAvgEnergyConsumptionSensor(coordinator, config_entry),
        GWMBatteryVoltageSensor(coordinator, config_entry),
        # 胎压(写死:左前/右前/左后/右后 + 胎压)
        GWMTirePressureSensor(coordinator, config_entry, "fl", "左前胎压"),
        GWMTirePressureSensor(coordinator, config_entry, "fr", "右前胎压"),
        GWMTirePressureSensor(coordinator, config_entry, "rl", "左后胎压"),
        GWMTirePressureSensor(coordinator, config_entry, "rr", "右后胎压"),
        # 胎温
        GWMTireTempSensor(coordinator, config_entry, "fl", "左前胎温"),
        GWMTireTempSensor(coordinator, config_entry, "fr", "右前胎温"),
        GWMTireTempSensor(coordinator, config_entry, "rl", "左后胎温"),
        GWMTireTempSensor(coordinator, config_entry, "rr", "右后胎温"),
        # 诊断
        GWMLastUpdateSensor(coordinator, config_entry),
        GWMGPSAuthorizedSensor(coordinator, config_entry),
    ]

    async_add_entities(entities)


class GWMSensorBase(CoordinatorEntity, SensorEntity):
    """所有 sensor 基类:has_entity_name=False,写死 name。"""

    _attr_has_entity_name = False

    def __init__(
        self, coordinator, config_entry: ConfigEntry, name: str, uid: str
    ) -> None:
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


# ===== 油电 =====
class GWMFuelVolumeSensor(GWMSensorBase):
    """剩余油量 L。remainOil。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "剩余油量", "fuel_volume")
        self._attr_device_class = SensorDeviceClass.VOLUME
        self._attr_native_unit_of_measurement = "L"
        self._attr_icon = "mdi:gas-station"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("fuel_volume")


class GWMFuelRangeSensor(GWMSensorBase):
    """综合续航 km(油+电),与 APP「续航里程」一致。preMileage。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "综合续航", "fuel_range")
        self._attr_device_class = SensorDeviceClass.DISTANCE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "km"
        self._attr_icon = "mdi:map-marker-distance"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("fuel_range")


class GWMFuelGaugeSensor(GWMSensorBase):
    """油表格数 0-8。oilQty,与 APP 8 格蓝条对应。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "油表格数", "fuel_gauge")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("fuel_gauge")


class GWMHevBatteryPercentSensor(GWMSensorBase):
    """混动(PHEV/HEV)高压动力电池电量 %。remainElectricPercent。

    纯燃油车(Tank 300 燃油版)此字段恒为 null,默认禁用避免一直显示未知。
    """

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "混动动力电池", "hev_battery_percent")
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        # 用字符串 "%" 替代 homeassistant.const.PERCENTAGE(2024.x 后已移除,避免崩)
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:car-electric"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("battery_percent")


class GWMEvRangeSensor(GWMSensorBase):
    """纯电续航 km(仅 PHEV 有值,HEV 返回 null)。charge.evContnsDistance。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "纯电续航", "ev_range")
        self._attr_device_class = SensorDeviceClass.DISTANCE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "km"
        self._attr_icon = "mdi:ev-station"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("ev_range")


class GWMMileageSensor(GWMSensorBase):
    """行驶总里程 km,与 APP「行驶总里程」对应。mileage。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "行驶总里程", "mileage")
        self._attr_device_class = SensorDeviceClass.DISTANCE
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = "km"
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("mileage")


class GWMAvgFuelConsumptionSensor(GWMSensorBase):
    """平均油耗 L/100km。avgFuelConse(当前 GWM v2.0 API 经常返回 null)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "平均油耗", "avg_fuel_consumption")
        # ⚠️ 不使用 VOLUME_FLOW_RATE device_class:
        # HA 的 UnitOfVolumeFlowRate 是流量(体积/时间),枚举里根本没有
        # LITERS_PER_KILOMETER(油耗:体积/距离),引用会 AttributeError → 整个 sensor 平台崩
        # 改为不声明 device_class,直接用字符串 "L/100km" 当单位,HA 不校验字符串
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "L/100km"
        self._attr_icon = "mdi:fuel"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("parsed_data", {}).get("avg_fuel_consumption")
        if val is None:
            return None
        try:
            # API 返回的就是 L/100km 数字(比如 8.7 表示 8.7L/100km)
            # 不做单位换算(上面的 unit 已经是 "L/100km" 字符串)
            return float(val)
        except (ValueError, TypeError):
            return None


# ===== 温度 =====
class GWMCabinTempSensor(GWMSensorBase):
    """车厢温度 ℃。cbnTemp。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "车厢温度", "cabin_temp")
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "°C"
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("cabin_temp")


# ===== 引擎 =====
class GWMEngineStateSensor(GWMSensorBase):
    """引擎状态。engineSts。native_value 直接返回中文,不再绕 HA 翻译。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "引擎状态", "engine_state")
        self._attr_icon = "mdi:engine"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        state = self.coordinator.data.get("parsed_data", {}).get("engine_state")
        if state is None:
            return None
        s = str(state)
        return {"0": "熄火", "1": "启动中", "2": "运行中"}.get(s, f"未知_{s}")

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:engine-off"
        s = str(self.coordinator.data.get("parsed_data", {}).get("engine_state", "0"))
        return (
            "mdi:engine"
            if s == "2"
            else "mdi:engine-outline"
            if s == "1"
            else "mdi:engine-off"
        )


class GWMGearSensor(GWMSensorBase):
    """档位。hcuGearSts(15=P 已验证,其它档位值来自 BR 项目经验)。"""

    _GEAR_MAP = {"0": "N", "13": "R", "14": "D", "15": "P"}

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "档位", "gear")
        self._attr_icon = "mdi:car-shift-pattern"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        gear = self.coordinator.data.get("parsed_data", {}).get("gear")
        if gear is None:
            return None
        return self._GEAR_MAP.get(str(gear), f"档位_{gear}")


# ===== 电源 =====
class GWMPowerStateSensor(GWMSensorBase):
    """电源状态。power("0"=下电,上电后 1/2/3;熄火停车时恒为"0")。"""

    _POWER_MAP = {"0": "下电", "1": "ACC", "2": "ON", "3": "READY"}

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "电源状态", "power_state")
        self._attr_icon = "mdi:power-standby"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        state = self.coordinator.data.get("parsed_data", {}).get("power_state")
        if state is None:
            return None
        s = str(state)
        return self._POWER_MAP.get(s, f"上电_{s}")

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:power-standby"
        s = str(self.coordinator.data.get("parsed_data", {}).get("power_state", "0"))
        return "mdi:power" if s != "0" else "mdi:power-standby"


# ===== 能耗 / 电池 =====
class GWMAvgEnergyConsumptionSensor(GWMSensorBase):
    """平均能耗 kWh/100km。avrgEgyCns(行驶后有值,熄火停车时 null 显示未知)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "平均能耗", "avg_energy_consumption")
        self._attr_state_class = SensorStateClass.MEASUREMENT
        # 混动模式的电耗,与「平均油耗」(L/100km)互补;单位为推测,启动后以实际值为准
        self._attr_native_unit_of_measurement = "kWh/100km"
        self._attr_icon = "mdi:flash"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        # 纯燃油车恒为 null,默认禁用
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("avg_energy_consumption")


class GWMBatteryVoltageSensor(GWMSensorBase):
    """动力电池电压 V。bmsPackVolt(上电/充电后有值,熄火停车时 null 显示未知)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "动力电池电压", "battery_voltage")
        self._attr_device_class = SensorDeviceClass.VOLTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "V"
        self._attr_icon = "mdi:battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        # 高压电池电压(bmsPackVolt),纯燃油车恒为 null,默认禁用
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("battery_voltage")


# ===== 胎压胎温(多实例:写死名字,防止所有 PRESSURE/TEMPERATURE 重名) =====
class GWMTirePressureSensor(GWMSensorBase):
    """胎压 kPa。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, position: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"tire_pressure_{position}")
        self.position = position
        self._attr_device_class = SensorDeviceClass.PRESSURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "kPa"
        self._attr_icon = "mdi:car-tire-alert"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"tire_pressure_{self.position}")
        )


class GWMTireTempSensor(GWMSensorBase):
    """胎温 ℃。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, position: str, display_name: str
    ) -> None:
        super().__init__(coordinator, config_entry, display_name, f"tire_temp_{position}")
        self.position = position
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "°C"
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return (
            self.coordinator.data.get("parsed_data", {})
            .get(f"tire_temp_{self.position}")
        )


# ===== 诊断 =====
class GWMLastUpdateSensor(GWMSensorBase):
    """车辆最后一次数据采集时间(与 APP 底部「数据更新于 X」对应)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "最后更新", "last_update")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> datetime | None:
        if not self.coordinator.data:
            return None
        ts = self.coordinator.data.get("update_time")
        if ts:
            try:
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def icon(self) -> str:
        if not self.coordinator.data or not self.coordinator.last_update_success:
            return "mdi:clock-alert-outline"
        return "mdi:clock-check-outline"


class GWMGPSAuthorizedSensor(GWMSensorBase):
    """GPS 授权开关(顶级 gpsSwitchOn)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry, "GPS 开关", "gps_switch")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:map-marker-question"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        on = self.coordinator.data.get("parsed_data", {}).get("gps_switch_on")
        if on is None:
            return "未知"
        return "开" if on else "关"

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:map-marker-off"
        on = self.coordinator.data.get("parsed_data", {}).get("gps_switch_on")
        return "mdi:map-marker-check" if on else "mdi:map-marker-off"
