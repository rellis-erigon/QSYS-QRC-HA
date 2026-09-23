"""String controls that can be written: labels, messages."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import QsysEntity
from .helpers import setup_platform


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    setup_platform(hass, entry, async_add_entities, "text", QsysText)


class QsysText(QsysEntity, TextEntity):
    _attr_native_min = 0
    _attr_native_max = 255

    @property
    def native_value(self) -> str | None:
        control = self.control
        if control is None:
            return None
        text = control.string or ("" if control.value is None else str(control.value))
        return text[:255]

    async def async_set_value(self, value: str) -> None:
        control = self.control
        if control is None:
            return
        await self.coordinator.async_set(control, value=value)
