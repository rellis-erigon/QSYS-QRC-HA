"""Tests for the control store.

The rule these all circle is the same one: discovery may refresh what the
Core says, and must never touch what the user decided.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from control_store import ControlStore, allowed_platforms  # noqa: E402


def store(tmp_path) -> ControlStore:
    return ControlStore(tmp_path / "controls.json")


def gain(s, core="Core", component="Zone 1", value=-25.0):
    return s.observe(
        core, component, "gain", type="Float", direction="Read/Write",
        value=value, string=f"{value}dB", minimum=-50.0, maximum=-25.0,
        unit="dB", suggested="number",
    )


def meter(s, core="Core"):
    return s.observe(
        core, "Meter", "meter.1", type="Float", direction="Read Only",
        value=-120.0, string="-120dB", minimum=-120.0, maximum=20.0,
        unit="dB", suggested="sensor",
    )


# -- discovery -----------------------------------------------------------

def test_observing_records_what_the_core_reports(tmp_path):
    s = store(tmp_path)
    control = gain(s)
    assert control.store_key == "Core/Zone 1/gain"
    assert control.key == "Zone 1/gain"
    assert control.unit == "dB"
    assert control.writable


def test_observing_twice_updates_rather_than_duplicates(tmp_path):
    s = store(tmp_path)
    gain(s, value=-25.0)
    control = gain(s, value=-30.0)
    assert len(s.controls) == 1
    assert control.value == -30.0
    assert control.updates == 2


def test_ranges_are_refreshed_because_gain_staging_changes(tmp_path):
    """A redeployed design can restage a fader; the old limits are wrong."""
    s = store(tmp_path)
    gain(s)
    restaged = s.observe("Core", "Zone 1", "gain", type="Float",
                         minimum=-100.0, maximum=20.0, unit="dB")
    assert restaged.minimum == -100.0 and restaged.maximum == 20.0


def test_discovery_never_overwrites_user_configuration(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", name="Bar Volume", enabled=True)
    gain(s, value=-40.0)
    control = s.controls["Core/Zone 1/gain"]
    assert control.config.name == "Bar Volume"
    assert control.config.enabled is True
    assert control.value == -40.0


# -- platform rules ------------------------------------------------------

def test_a_read_only_control_cannot_become_a_switch(tmp_path):
    s = store(tmp_path)
    meter(s)
    assert "switch" not in allowed_platforms(s.controls["Core/Meter/meter.1"])
    with pytest.raises(ValueError, match="cannot be a"):
        s.configure("Core", "Meter/meter.1", platform="switch")


def test_a_read_only_float_may_be_a_sensor(tmp_path):
    s = store(tmp_path)
    meter(s)
    control = s.configure("Core", "Meter/meter.1", platform="sensor")
    assert control.config.platform == "sensor"


def test_a_boolean_cannot_become_a_number(tmp_path):
    s = store(tmp_path)
    s.observe("Core", "Zone 1", "mute", type="Boolean", suggested="switch")
    with pytest.raises(ValueError):
        s.configure("Core", "Zone 1/mute", platform="number")


def test_an_unknown_platform_is_refused(tmp_path):
    s = store(tmp_path)
    gain(s)
    with pytest.raises(ValueError, match="unknown platform"):
        s.configure("Core", "Zone 1/gain", platform="thermostat")


def test_configuring_something_undiscovered_raises(tmp_path):
    with pytest.raises(KeyError):
        store(tmp_path).configure("Core", "Nope/gain", name="x")


def test_the_suggestion_is_used_until_a_person_overrides_it(tmp_path):
    s = store(tmp_path)
    control = gain(s)
    assert control.to_dict()["platform"] == "number"
    s.configure("Core", "Zone 1/gain", platform="sensor")
    assert s.controls["Core/Zone 1/gain"].to_dict()["platform"] == "sensor"


# -- exposure ------------------------------------------------------------

def test_only_enabled_controls_are_exposed(tmp_path):
    s = store(tmp_path)
    gain(s)
    assert s.exposed() == []
    s.configure("Core", "Zone 1/gain", enabled=True)
    assert [c.key for c in s.exposed()] == ["Zone 1/gain"]


def test_an_ignored_control_is_never_exposed_even_if_enabled(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", enabled=True, platform="ignore")
    assert s.exposed() == []


def test_watch_keys_cover_enabled_and_named_controls_only(tmp_path):
    """Subscribing to all 302 to watch 48 puts a live meter on the wire."""
    s = store(tmp_path)
    gain(s, component="Zone 1")
    gain(s, component="Zone 2")
    gain(s, component="Zone 3")
    s.configure("Core", "Zone 1/gain", enabled=True)
    s.configure("Core", "Zone 2/gain", name="Named but off")
    assert sorted(s.watch_keys("Core")) == ["Zone 1/gain", "Zone 2/gain"]


# -- persistence ---------------------------------------------------------

def test_a_saved_store_reloads_with_its_configuration(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", name="Bar", enabled=True,
                platform="number", use_position=True)
    s.save()

    fresh = ControlStore(tmp_path / "controls.json")
    assert fresh.load() == 1
    control = fresh.controls["Core/Zone 1/gain"]
    assert control.config.name == "Bar"
    assert control.config.use_position is True
    assert control.minimum == -50.0


def test_saving_is_atomic(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.save()
    assert not (tmp_path / "controls.tmp").exists()
    assert json.loads((tmp_path / "controls.json").read_text())["version"] == 1


def test_a_missing_store_loads_as_empty(tmp_path):
    assert ControlStore(tmp_path / "nothing.json").load() == 0


def test_a_corrupt_store_does_not_stop_startup(tmp_path):
    path = tmp_path / "controls.json"
    path.write_text("{ not json")
    assert ControlStore(path).load() == 0


def test_a_field_from_a_newer_version_is_ignored_not_fatal(tmp_path):
    """Forward compatibility: keep the row, drop the field we do not know."""
    path = tmp_path / "controls.json"
    path.write_text(json.dumps({"version": 2, "controls": {
        "Core/A/b": {"core": "Core", "component": "A", "control": "b",
                     "future_field": 1,
                     "config": {"name": "Kept", "future_setting": True}},
    }}))
    s = ControlStore(path)
    assert s.load() == 1
    assert s.controls["Core/A/b"].config.name == "Kept"


def test_save_if_dirty_only_writes_when_something_changed(tmp_path):
    s = store(tmp_path)
    gain(s)
    assert s.save_if_dirty() is True
    assert s.save_if_dirty() is False


# -- pruning -------------------------------------------------------------

def test_pruning_forgets_controls_a_redeploy_removed(tmp_path):
    s = store(tmp_path)
    gain(s, component="Zone 1")
    gain(s, component="Zone 2")
    assert s.prune("Core", {"Zone 1/gain"}) == 1
    assert list(s.controls) == ["Core/Zone 1/gain"]


def test_pruning_keeps_anything_the_user_configured(tmp_path):
    """Designs get redeployed in pieces; losing the setup is worse."""
    s = store(tmp_path)
    gain(s, component="Zone 1")
    gain(s, component="Zone 2")
    s.configure("Core", "Zone 2/gain", enabled=True)
    assert s.prune("Core", {"Zone 1/gain"}) == 0
    assert len(s.controls) == 2


def test_pruning_one_core_leaves_another_alone(tmp_path):
    s = store(tmp_path)
    gain(s, core="A")
    gain(s, core="B")
    s.prune("A", set())
    assert list(s.controls) == ["B/Zone 1/gain"]


def test_components_are_counted_per_core(tmp_path):
    s = store(tmp_path)
    gain(s, component="Zone 1")
    gain(s, component="Zone 2")
    meter(s)
    assert s.components("Core") == {"Zone 1": 1, "Zone 2": 1, "Meter": 1}


# -- grouping and areas --------------------------------------------------

def test_a_control_belongs_to_its_component_until_told_otherwise(tmp_path):
    s = store(tmp_path)
    control = gain(s)
    assert control.config.resolved_group("Zone 1") == "Zone 1"
    assert control.to_dict()["group"] == "Zone 1"


def test_a_group_moves_a_control_to_another_device(tmp_path):
    """One Mixer carries the outputs for every room in a building."""
    s = store(tmp_path)
    s.observe("Core", "Mixer", "output.3.gain", type="Float", suggested="number")
    s.configure("Core", "Mixer/output.3.gain", group="Function Room 3",
                area="Function Room 3")
    control = s.controls["Core/Mixer/output.3.gain"]
    assert control.to_dict()["group"] == "Function Room 3"
    assert control.config.area == "Function Room 3"


def test_clearing_a_group_falls_back_to_the_component(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", group="Somewhere")
    s.configure("Core", "Zone 1/gain", group="")
    assert s.controls["Core/Zone 1/gain"].to_dict()["group"] == "Zone 1"


def test_areas_in_use_lists_each_one_once(tmp_path):
    from control_store import areas_in_use

    s = store(tmp_path)
    gain(s, component="Zone 1")
    gain(s, component="Zone 2")
    s.configure("Core", "Zone 1/gain", area="Bar")
    s.configure("Core", "Zone 2/gain", area="Bar")
    assert areas_in_use(list(s.controls.values())) == ["Bar"]


def test_a_group_takes_its_area_from_the_first_control_that_names_one(tmp_path):
    """Assigning the area once is enough to move the whole device."""
    from control_store import groups_in_use

    s = store(tmp_path)
    s.observe("Core", "Mixer", "output.3.gain", type="Float")
    s.observe("Core", "Mixer", "output.3.mute", type="Boolean")
    s.configure("Core", "Mixer/output.3.gain", group="Room 3", area="Room 3")
    s.configure("Core", "Mixer/output.3.mute", group="Room 3", enabled=True)

    groups = groups_in_use(list(s.controls.values()))
    assert groups["Room 3"]["area"] == "Room 3"
    assert groups["Room 3"]["controls"] == 2
    assert groups["Room 3"]["exposed"] == 1


def test_entity_category_is_validated(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", entity_category="diagnostic")
    assert s.controls["Core/Zone 1/gain"].config.entity_category == "diagnostic"
    with pytest.raises(ValueError, match="unknown entity category"):
        s.configure("Core", "Zone 1/gain", entity_category="somewhere")


def test_precision_is_clamped_to_something_sensible(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", precision=99)
    assert s.controls["Core/Zone 1/gain"].config.precision == 6
    s.configure("Core", "Zone 1/gain", precision="")
    assert s.controls["Core/Zone 1/gain"].config.precision is None
    with pytest.raises(ValueError, match="whole number"):
        s.configure("Core", "Zone 1/gain", precision="loud")


def test_grouping_and_areas_survive_a_reload(tmp_path):
    s = store(tmp_path)
    gain(s)
    s.configure("Core", "Zone 1/gain", group="Bar", area="Bar",
                icon="mdi:volume-high", entity_category="config", precision=1)
    s.save()

    fresh = ControlStore(tmp_path / "controls.json")
    fresh.load()
    config = fresh.controls["Core/Zone 1/gain"].config
    assert (config.group, config.area) == ("Bar", "Bar")
    assert config.icon == "mdi:volume-high"
    assert config.entity_category == "config"
    assert config.precision == 1
