"""Sensor platform for GWM China."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
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
    """装载 sensor 平台。

    原则:只保留 HAR 里实际返回过有效值(非 null / 非 '--')、
    并且在日常使用里有意义的字段。
    """
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[SensorEntity] = [
        # 油电(与官方 APP 车况数据页对应)
        GWMFuelVolumeSensor(coordinator, config_entry),   # 剩余油量(L)
        GWMFuelRangeSensor(coordinator, config_entry),    # 综合续航 km(APP 「续航里程」)
        GWMFuelGaugeSensor(coordinator, config_entry),    # 油表格数 0-8(APP 油表蓝条)
        GWMHevBatteryPercentSensor(coordinator, config_entry),  # PHEV 电池电量 %
        GWMEvRangeSensor(coordinator, config_entry),      # 纯电续航 km(仅 PHEV)
        GWMMileageSensor(coordinator, config_entry),      # 行驶总里程 km(APP 「行驶总里程」)
        GWMAvgFuelConsumptionSensor(coordinator, config_entry),  # 平均油耗
        # 温度
        GWMCabinTempSensor(coordinator, config_entry),     # 车厢温度 ℃
        # 引擎/档位
        GWMEngineStateSensor(coordinator, config_entry),
        GWMGearSensor(coordinator, config_entry),
        # 胎压(与 APP 卡片四角对应)
        GWMTirePressureSensor(coordinator, config_entry, "fl"),
        GWMTirePressureSensor(coordinator, config_entry, "fr"),
        GWMTirePressureSensor(coordinator, config_entry, "rl"),
        GWMTirePressureSensor(coordinator, config_entry, "rr"),
        # 胎温(与 APP 卡片四角对应)
        GWMTireTempSensor(coordinator, config_entry, "fl"),
        GWMTireTempSensor(coordinator, config_entry, "fr"),
        GWMTireTempSensor(coordinator, config_entry, "rl"),
        GWMTireTempSensor(coordinator, config_entry, "rr"),
        # 诊断/辅助
        GWMLastUpdateSensor(coordinator, config_entry),   # 采集时间(APP 底部)
        GWMGPSAuthorizedSensor(coordinator, config_entry), # GPS 开关
    ]

    async_add_entities(entities)


class GWMSensorBase(CoordinatorEntity, SensorEntity):
    """GWM sensor 基类。"""

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


# ===== 油电 =====
class GWMFuelVolumeSensor(GWMSensorBase):
    """剩余油量(L)。remainOil。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_fuel_volume"
        self._attr_translation_key = "fuel_volume"
        self._attr_device_class = SensorDeviceClass.VOLUME
        self._attr_native_unit_of_measurement = UnitOfVolume.LITERS
        self._attr_icon = "mdi:gas-station"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("fuel_volume")


class GWMFuelRangeSensor(GWMSensorBase):
    """综合续航里程(油+电)km,与 APP「续航里程」739km 对应。preMileage。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_fuel_range"
        self._attr_translation_key = "fuel_range"
        self._attr_device_class = SensorDeviceClass.DISTANCE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
        self._attr_icon = "mdi:map-marker-distance"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("fuel_range")


class GWMFuelGaugeSensor(GWMSensorBase):
    """油表格数(0-8),与 APP 底部 8 格蓝色油条对应。oilQty。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_fuel_gauge"
        self._attr_translation_key = "fuel_gauge"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("fuel_gauge")


class GWMHevBatteryPercentSensor(GWMSensorBase):
    """混动(PHEV/HEV)动力电池电量 %。remainElectricPercent。

    注:这是高压动力电池电量,不是 12V 小电池。
    """

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_hev_battery_percent"
        self._attr_translation_key = "hev_battery_percent"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:car-electric"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("battery_percent")


class GWMEvRangeSensor(GWMSensorBase):
    """纯电续航 km(只在 PHEV 有值,HEV 为 null)。charge.evContnsDistance。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_ev_range"
        self._attr_translation_key = "ev_range"
        self._attr_device_class = SensorDeviceClass.DISTANCE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
        self._attr_icon = "mdi:ev-station"
        self._attr_entity_registry_enabled_default = False  # 默认隐藏,HEV 没意义

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("ev_range")


class GWMMileageSensor(GWMSensorBase):
    """行驶总里程 km,与 APP 「行驶总里程」130km 对应。mileage。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_mileage"
        self._attr_translation_key = "mileage"
        self._attr_device_class = SensorDeviceClass.DISTANCE
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("mileage")


class GWMAvgFuelConsumptionSensor(GWMSensorBase):
    """平均油耗 L/100km(如果接口返回)。avgFuelConse。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_avg_fuel_consumption"
        self._attr_translation_key = "avg_fuel_consumption"
        self._attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        # HA 没有 L/100km,用 L/km,显示层可换算
        self._attr_native_unit_of_measurement = UnitOfVolumeFlowRate.LITERS_PER_KILOMETER
        self._attr_icon = "mdi:fuel"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False  # 默认隐藏(很多时候 null)

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("parsed_data", {}).get("avg_fuel_consumption")
        if val is None:
            return None
        # API 返回 L/100km;HA 单位是 L/km,所以 /100
        try:
            return float(val) / 100.0
        except (ValueError, TypeError):
            return None


# ===== 温度 =====
class GWMCabinTempSensor(GWMSensorBase):
    """车厢温度 ℃。cbnTemp。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_cabin_temp"
        self._attr_translation_key = "cabin_temp"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("parsed_data", {}).get("cabin_temp")


# ===== 引擎 =====
class GWMEngineStateSensor(GWMSensorBase):
    """引擎状态。engineSts。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_engine_state"
        self._attr_translation_key = "engine_state"
        self._attr_icon = "mdi:engine"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        state = self.coordinator.data.get("parsed_data", {}).get("engine_state")
        if state is None:
            return None
        s = str(state)
        return {
            "0": "off",
            "1": "starting",
            "2": "running",
        }.get(s, f"unknown_{s}")

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:engine-off"
        state = str(self.coordinator.data.get("parsed_data", {}).get("engine_state", "0"))
        if state == "2":
            return "mdi:engine"
        if state == "1":
            return "mdi:engine-outline"
        return "mdi:engine-off"


class GWMGearSensor(GWMSensorBase):
    """档位。hcuGearSts。

    已知映射("15"=P 已在停车熄火状态验证过,其他值来自 BR 项目经验)。
    """

    _GEAR_MAP = {
        "0": "N",
        "13": "R",
        "14": "D",
        "15": "P",
    }

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_gear"
        self._attr_translation_key = "gear"
        self._attr_icon = "mdi:car-shift-pattern"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        gear = self.coordinator.data.get("parsed_data", {}).get("gear")
        if gear is None:
            return None
        return self._GEAR_MAP.get(str(gear), f"gear_{gear}")


# ===== 胎压胎温 =====
class GWMTirePressureSensor(GWMSensorBase):
    """胎压 kPa。"""

    def __init__(
        self, coordinator, config_entry: ConfigEntry, position: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.position = position
        self._attr_unique_id = f"{coordinator.vin}_tire_pressure_{position}"
        self._attr_translation_key = f"tire_pressure_{position}"
        self._attr_device_class = SensorDeviceClass.PRESSURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfPressure.KILOPASCALS
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
        self, coordinator, config_entry: ConfigEntry, position: str
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.position = position
        self._attr_unique_id = f"{coordinator.vin}_tire_temp_{position}"
        self._attr_translation_key = f"tire_temp_{position}"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
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
    """车辆数据最后采集时间(与 APP 底部「数据更新于 X」对应)。"""

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_last_update"
        self._attr_translation_key = "last_update"
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
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.vin}_gps_switch"
        self._attr_translation_key = "gps_switch"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:map-marker-question"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        on = self.coordinator.data.get("parsed_data", {}).get("gps_switch_on")
        if on is None:
            return "unknown"
        return "on" if on else "off"

    @property
    def icon(self) -> str:
        if not self.coordinator.data:
            return "mdi:map-marker-off"
        on = self.coordinator.data.get("parsed_data", {}).get("gps_switch_on")
        return "mdi:map-marker-check" if on else "mdi:map-marker-off"
