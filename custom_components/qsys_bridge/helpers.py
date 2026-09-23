"""Shared platform setup.

Every platform does the same thing: create entities for the controls of its
platform, and keep doing it, because controls are exposed from the add-on UI
while Home Assistant is running.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import QsysCoordinator


def setup_platform(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback, platform: str, factory,
) -> None:
    coordinator: QsysCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        fresh = coordinator.new_controls(platform, known)
        if not fresh:
            return
        known.update(c.store_key for c in fresh)
        async_add_entities(factory(coordinator, c) for c in fresh)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
