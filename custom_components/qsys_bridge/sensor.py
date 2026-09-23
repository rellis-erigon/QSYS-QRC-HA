"""Read-only numeric and string controls."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import QsysEntity
from .helpers import setup_platform


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    setup_platform(hass, entry, async_add_entities, "sensor", QsysSensor)


class QsysSensor(QsysEntity, SensorEntity):
    @property
    def native_value(self):
        control = self.control
        if control is None:
            return None
        if isinstance(control.value, (int, float)) and not isinstance(
            control.value, bool
        ):
            return round(float(control.value), 3)
        # A string control can carry more than a state will accept.
        text = control.string or ("" if control.value is None else str(control.value))
        return text[:255]

    @property
    def native_unit_of_measurement(self) -> str | None:
        control = self.control
        if control is None or not isinstance(control.value, (int, float)):
            return None
        return control.unit or None

    @property
    def device_class(self):
        control = self.control
        return (control.device_class or None) if control else None

    @property
    def state_class(self):
        control = self.control
        if control is None or not isinstance(control.value, (int, float)):
            # Asking the recorder to keep statistics for a text control fills
            # the database with nothing.
            return None
        return SensorStateClass.MEASUREMENT
