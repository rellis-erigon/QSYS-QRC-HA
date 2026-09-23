"""Numeric controls that can be set: gain, delay, threshold."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import QsysEntity
from .helpers import setup_platform


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    setup_platform(hass, entry, async_add_entities, "number", QsysNumber)


class QsysNumber(QsysEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    @property
    def native_min_value(self) -> float:
        control = self.control
        # The Core's own staging. A fader limited to -50..-25 dB must not
        # offer 0 dB on a dashboard.
        if control is None or control.minimum is None:
            return 0.0
        return float(control.minimum)

    @property
    def native_max_value(self) -> float:
        control = self.control
        if control is None or control.maximum is None:
            return 100.0
        return float(control.maximum)

    @property
    def native_step(self) -> float:
        control = self.control
        if control is None or control.minimum is None or control.maximum is None:
            return 1.0
        span = float(control.maximum) - float(control.minimum)
        return 0.1 if span <= 200 else 1.0

    @property
    def native_value(self) -> float | None:
        control = self.control
        if control is None or control.value is None:
            return None
        try:
            return round(float(control.value), 3)
        except (TypeError, ValueError):
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        control = self.control
        return (control.unit or None) if control else None

    async def async_set_native_value(self, value: float) -> None:
        control = self.control
        if control is None:
            return
        if control.use_position and control.minimum is not None \
                and control.maximum is not None:
            # A dB scale is not linear. Driving by normalised position gives
            # a slider that behaves the way a person expects it to.
            span = float(control.maximum) - float(control.minimum)
            position = 0.0 if span == 0 else (value - float(control.minimum)) / span
            await self.coordinator.async_set(
                control, position=max(0.0, min(1.0, position))
            )
            return
        await self.coordinator.async_set(control, value=value)
