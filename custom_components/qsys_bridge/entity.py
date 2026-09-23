"""Base entity for the Q-SYS Bridge integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import QsysControl, QsysCoordinator


class QsysEntity(CoordinatorEntity[QsysCoordinator]):
    """One exposed control."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: QsysCoordinator, control: QsysControl) -> None:
        super().__init__(coordinator)
        self._store_key = control.store_key
        # Keyed on core, component and control rather than the config entry,
        # so removing and re-adding the integration keeps the same entities.
        self._attr_unique_id = f"{DOMAIN}_{control.core}_{control.key}"
        self._attr_name = control.name
        # A device per component rather than per Core: a design has dozens of
        # components, and one device holding three hundred entities is not
        # something anyone can navigate.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{control.core}_{control.component}")},
            name=f"{control.core} {control.component}",
            manufacturer="QSC",
            model="Q-SYS component",
            via_device=(DOMAIN, control.core),
        )

    @property
    def control(self) -> QsysControl | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._store_key)

    @property
    def available(self) -> bool:
        control = self.control
        return bool(
            self.coordinator.last_update_success and control and control.available
        )

    @property
    def extra_state_attributes(self) -> dict:
        control = self.control
        if control is None:
            return {}
        return {
            "core": control.core,
            "component": control.component,
            "control": control.control,
            "qsys_string": control.string,
        }
