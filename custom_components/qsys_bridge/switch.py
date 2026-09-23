"""Boolean controls that can be driven."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import QsysEntity
from .helpers import setup_platform


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    setup_platform(hass, entry, async_add_entities, "switch", QsysSwitch)


class QsysSwitch(QsysEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        control = self.control
        return None if control is None else bool(control.value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, state: bool) -> None:
        control = self.control
        if control is None:
            return
        await self.coordinator.async_set(control, value=state)
        # The Core echoes the change back on the change group, so nothing is
        # written into local state here. A control the design interlocks
        # would otherwise show something the room is not doing.
