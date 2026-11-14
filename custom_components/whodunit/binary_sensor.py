"""Fight detected binary sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import WhodunitEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([FightSensor(entry.runtime_data)])


class FightSensor(WhodunitEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:boxing-glove"

    def __init__(self, wd) -> None:
        super().__init__(wd, "fight")

    @property
    def is_on(self) -> bool:
        return bool(self.wd.engine.active_fights)

    @property
    def extra_state_attributes(self):
        fights = list(self.wd.engine.active_fights.values())
        return {
            "fights": [
                {
                    "entity_id": f.entity_id,
                    "kind": f.kind,
                    "flips": f.flips,
                    "actors": [a["name"] for a in f.as_dict()["actors"]],
                }
                for f in fights
            ],
            "summary": "; ".join(f.summary() for f in fights) or None,
        }
