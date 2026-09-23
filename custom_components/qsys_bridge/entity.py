"""Base entity for the Q-SYS Bridge integration."""
from __future__ import annotations

from homeassistant.const import EntityCategory
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
        if control.icon:
            self._attr_icon = control.icon
        if control.entity_category:
            self._attr_entity_category = EntityCategory(control.entity_category)

        # A device per group rather than per Core: a design has dozens of
        # components, and one device holding three hundred entities is not
        # something anyone can navigate. The group defaults to the component
        # but does not have to be it — one Mixer carries the outputs for
        # every room in a building, and those belong in the rooms.
        group = control.group or control.component
        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{control.core}_{group}")},
            name=group,
            manufacturer="QSC",
            model="Q-SYS",
            via_device=(DOMAIN, control.core),
        )
        if control.area:
            # A suggestion rather than an assignment: Home Assistant creates
            # the area if it is new, and never overrides a device someone
            # has already placed by hand.
            device_info["suggested_area"] = control.area
        self._attr_device_info = device_info

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
