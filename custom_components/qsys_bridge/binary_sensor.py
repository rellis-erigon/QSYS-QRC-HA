"""Boolean controls that are read only."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import QsysEntity
from .helpers import setup_platform


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    setup_platform(hass, entry, async_add_entities, "binary_sensor",
                   QsysBinarySensor)


class QsysBinarySensor(QsysEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        control = self.control
        return None if control is None else bool(control.value)

    @property
    def device_class(self):
        control = self.control
        return (control.device_class or None) if control else None
