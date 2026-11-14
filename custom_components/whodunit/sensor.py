"""Whodunit sensors."""

from __future__ import annotations

from homeassistant.components.sensor import RestoreSensor, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import WhodunitEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    wd = entry.runtime_data
    async_add_entities([FightsToday(wd), BusiestAutomation(wd)])


class FightsToday(WhodunitEntity, RestoreSensor):
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "fights"

    def __init__(self, wd) -> None:
        super().__init__(wd, "fights_today")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        data = await self.async_get_last_sensor_data()
        if last and data and data.native_value is not None:
            if dt_util.as_local(last.last_updated).date() == dt_util.now().date():
                self.wd.fights_today += int(float(data.native_value))

    @property
    def native_value(self) -> int:
        return self.wd.fights_today


class BusiestAutomation(WhodunitEntity, SensorEntity):
    _attr_icon = "mdi:run-fast"

    def __init__(self, wd) -> None:
        super().__init__(wd, "busiest_automation")

    @property
    def native_value(self) -> str | None:
        top = self.wd.engine.busiest(1)
        return top[0]["name"] if top else None

    @property
    def extra_state_attributes(self):
        top = self.wd.engine.busiest(10)
        return {
            "runs_last_hour": top[0]["runs_last_hour"] if top else 0,
            "automation": top[0]["entity_id"] if top else None,
            "top": top,
        }
