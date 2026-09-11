"""Device tracker platform for GWM China."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_MODEL,
    ATTR_UPDATE_TIME,
    ATTR_VEHICLE_NUMBER,
    ATTR_VIN,
    DOMAIN,
    ENTITY_PICTURE_URL,
    VERSION,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """装载 device_tracker 平台。"""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([GWMCarTracker(coordinator, config_entry)])


class GWMCarTracker(CoordinatorEntity, TrackerEntity):
    """车辆位置追踪。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_unique_id = f"{coordinator.vin}_location"
        self._attr_translation_key = "vehicle_location"
        self._attr_icon = "mdi:car"
        # 用集成 logo 作为实体图片(由 __init__.py 注册的 /gwm_cn/icon.png 提供)
        self._attr_entity_picture = ENTITY_PICTURE_URL

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
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("latitude")

    @property
    def longitude(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("longitude")

    @property
    def location_accuracy(self) -> int:
        """GPS 精度(m),CN API 没有精度字段,fallback 50m。"""
        return 50

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}

        data = self.coordinator.data
        parsed = data.get("parsed_data", {}) or {}
        ts = data.get("update_time")

        attrs = {
            ATTR_VIN: data.get("vin"),
            ATTR_MODEL: data.get("model"),
            ATTR_LATITUDE: data.get("latitude"),
            ATTR_LONGITUDE: data.get("longitude"),
            ATTR_UPDATE_TIME: _format_ts(ts),
            ATTR_VEHICLE_NUMBER: data.get("vehicle_number"),
        }

        if parsed:
            attrs.update({
                "mileage": parsed.get("mileage"),
                "fuel_volume": parsed.get("fuel_volume"),
                "fuel_range": parsed.get("fuel_range"),
                "hev_battery_percent": parsed.get("battery_percent"),
                "ev_range": parsed.get("ev_range"),
                "engine_state": _engine_state_text(parsed.get("engine_state")),
                "doors_locked": (
                    "locked" if parsed.get("doors_locked") is True
                    else ("unlocked" if parsed.get("doors_locked") is False
                          else "unknown")
                ),
            })

        return attrs

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.latitude is not None
            and self.longitude is not None
        )


def _format_ts(ts: int | None) -> str | None:
    """把毫秒时间戳格式化为 ISO8601 字符串。"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _engine_state_text(state) -> str:
    if state is None:
        return "unknown"
    if state in (0, "0"):
        return "off"
    if state in (1, "1"):
        return "starting"
    if state in (2, "2"):
        return "running"
    return f"unknown_{state}"
