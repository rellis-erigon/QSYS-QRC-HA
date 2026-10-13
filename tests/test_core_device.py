"""The Core device, and how a group hangs off it.

Every entity sits on a per-group device, and each of those groups belongs
to a Q-SYS Core. Expressing that with `via_device` was wrong twice over:
the key is deprecated and removed in Home Assistant 2027.8, and nothing in
this integration ever created the Core device it named, so the link pointed
at something that did not exist and was silently dropped.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr

from custom_components.qsys_bridge.const import CONF_ADDON_URL, DOMAIN
from custom_components.qsys_bridge.coordinator import QsysControl, QsysCoordinator
from custom_components.qsys_bridge.sensor import QsysSensor

CORES = {
    "CoreA": {
        "logged_on": True,
        "host": "192.0.2.10",
        "design": "Site Audio",
        "platform": "Core 110f",
    },
    "CoreB": {
        "logged_on": False,
        "host": "192.0.2.11",
        "design": "",
        "platform": "",
    },
}


def _control(core="CoreA", component="Mixer", group="Main Hall", **kwargs):
    return QsysControl(
        core=core,
        key=f"{component}/gain",
        component=component,
        control="gain",
        platform="sensor",
        name="Gain",
        group=group,
        **kwargs,
    )


@pytest.fixture
def hass():
    return HomeAssistant()


@pytest.fixture
def coordinator(hass):
    entry = ConfigEntry(data={CONF_ADDON_URL: "http://192.0.2.20:8099"})
    coordinator = QsysCoordinator(hass, entry)
    coordinator.cores = dict(CORES)
    return coordinator


def _registered(hass, name):
    for kwargs in dr.async_get(hass).created:
        if kwargs.get("name") == name:
            return kwargs
    return None


# -- the Core device exists ----------------------------------------------


def test_a_device_is_registered_for_each_core(hass, coordinator):
    coordinator.async_register_core_devices()

    created = dr.async_get(hass).created
    assert {kwargs["name"] for kwargs in created} == {"CoreA", "CoreB"}


def test_core_device_describes_the_hardware(hass, coordinator):
    coordinator.async_register_core_devices()

    core_a = _registered(hass, "CoreA")
    assert core_a["identifiers"] == {(DOMAIN, "core_CoreA")}
    assert core_a["manufacturer"] == "QSC"
    assert core_a["model"] == "Core 110f"
    assert core_a["configuration_url"] == "http://192.0.2.10"
    assert core_a["config_entry_id"] == coordinator.entry.entry_id


def test_core_device_falls_back_when_the_core_says_nothing(hass, coordinator):
    """CoreB has never logged on, so it reports no platform and no design."""
    coordinator.async_register_core_devices()

    core_b = _registered(hass, "CoreB")
    assert core_b["model"] == "Q-SYS Core"
    assert core_b["configuration_url"] == "http://192.0.2.11"


def test_core_without_a_host_has_no_configuration_url(hass, coordinator):
    coordinator.cores = {"CoreC": {"logged_on": False, "host": ""}}
    coordinator.async_register_core_devices()

    assert _registered(hass, "CoreC").get("configuration_url") is None


def test_registering_twice_does_not_duplicate(hass, coordinator):
    coordinator.async_register_core_devices()
    coordinator.async_register_core_devices()

    assert len(dr.async_get(hass).created) == 2


def test_core_device_ids_are_recorded_for_the_entities(coordinator):
    coordinator.async_register_core_devices()

    assert set(coordinator.core_device_ids) == {"CoreA", "CoreB"}
    assert all(coordinator.core_device_ids.values())


def test_core_device_ids_start_empty(coordinator):
    """An entity added before the first refresh must not raise."""
    assert coordinator.core_device_ids == {}


# -- the group links to it by registry id --------------------------------


def test_group_links_to_its_core_by_id(coordinator):
    coordinator.async_register_core_devices()
    entity = QsysSensor(coordinator, _control())

    assert (
        entity.device_info["via_device_id"]
        == coordinator.core_device_ids["CoreA"]
    )


def test_group_never_uses_the_deprecated_key(coordinator):
    """`via_device` is removed in Home Assistant 2027.8."""
    coordinator.async_register_core_devices()
    entity = QsysSensor(coordinator, _control())

    assert "via_device" not in entity.device_info


def test_group_of_an_unknown_core_is_left_unlinked(coordinator):
    """A control can arrive for a Core the last refresh did not list."""
    entity = QsysSensor(coordinator, _control(core="CoreZ"))

    assert "via_device_id" not in entity.device_info
    assert "via_device" not in entity.device_info


def test_group_device_is_still_keyed_per_core(coordinator):
    """Two Cores may both have a group called Main Hall."""
    coordinator.async_register_core_devices()
    a = QsysSensor(coordinator, _control(core="CoreA"))
    b = QsysSensor(coordinator, _control(core="CoreB"))

    assert a.device_info["identifiers"] != b.device_info["identifiers"]
    assert a.device_info["via_device_id"] != b.device_info["via_device_id"]


def test_core_identifier_cannot_collide_with_a_group(coordinator):
    """A Core named `CoreA_Main Hall` must not claim CoreA's Main Hall."""
    coordinator.cores = {"CoreA_Main Hall": {"logged_on": True, "host": ""}}
    coordinator.async_register_core_devices()
    group = QsysSensor(coordinator, _control(core="CoreA", group="Main Hall"))

    core_identifiers = {(DOMAIN, "core_CoreA_Main Hall")}
    assert group.device_info["identifiers"] != core_identifiers


def test_group_keeps_its_other_device_details(coordinator):
    coordinator.async_register_core_devices()
    entity = QsysSensor(coordinator, _control(area="Block A"))

    info = entity.device_info
    assert info["name"] == "Main Hall"
    assert info["manufacturer"] == "QSC"
    assert info["suggested_area"] == "Block A"
