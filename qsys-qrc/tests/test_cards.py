"""Building a zone-rack card from the controls a Core exposes."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cards  # noqa: E402


def control(component, name, key, enabled=True, group=None):
    stored = types.SimpleNamespace(component=component, control=name, key=key)
    stored.config = types.SimpleNamespace(
        enabled=enabled, resolved_group=lambda c, g=group or component: g)
    return stored


def test_a_zone_needs_a_level():
    """A mute on its own draws a strip of dashes, so it is not a zone."""
    controls = [control("Foyer", "mute", "k1")]
    assert cards.zone_card(controls)[0] is None


def test_level_and_mute_become_one_strip():
    controls = [control("Foyer", "gain", "k1"), control("Foyer", "mute", "k2")]
    card, omitted = cards.zone_card(controls)
    assert card["options"] == {"zones": 1}
    assert card["entities"] == {"zone1_volume": "k1", "zone1_mute": "k2"}
    assert card["labels"] == {"zone1": "Foyer"}


def test_level_alone_is_still_a_zone():
    card, omitted = cards.zone_card([control("Foyer", "level", "k1")])
    assert card["entities"] == {"zone1_volume": "k1"}


def test_unexposed_controls_are_ignored():
    controls = [control("Foyer", "gain", "k1", enabled=False)]
    assert cards.zone_card(controls)[0] is None


def test_groups_choose_the_strips_and_their_order():
    controls = [
        control("B", "gain", "kb"), control("A", "gain", "ka"),
        control("C", "gain", "kc"),
    ]
    card, omitted = cards.zone_card(controls, ["C", "A"])
    assert card["labels"] == {"zone1": "C", "zone2": "A"}
    assert card["entities"] == {"zone1_volume": "kc", "zone2_volume": "ka"}


def test_an_unknown_group_is_skipped_not_fatal():
    card, omitted = cards.zone_card([control("A", "gain", "ka")], ["A", "Nope"])
    assert card["options"] == {"zones": 1}


def test_no_groups_takes_everything_alphabetically():
    controls = [control("Zulu", "gain", "kz"), control("alpha", "gain", "ka")]
    card, omitted = cards.zone_card(controls)
    assert card["labels"] == {"zone1": "alpha", "zone2": "Zulu"}


def test_the_rack_is_capped_and_says_what_it_dropped():
    """The faceplate draws at most sixteen strips. A card that quietly
    truncated would look complete while leaving zones off the wall."""
    controls = [control(f"Z{i:02d}", "gain", f"k{i}") for i in range(20)]
    card, omitted = cards.zone_card(controls)
    assert card["options"] == {"zones": cards.MAX_ZONES}
    assert len(omitted) == 20 - cards.MAX_ZONES
    assert omitted[0] == "Z16"


def test_volume_spelling_is_matched_too():
    card, omitted = cards.zone_card([control("A", "Volume", "k1")])
    assert card["entities"]["zone1_volume"] == "k1"


def test_one_mixer_holding_many_zones_is_many_strips():
    """A Q-SYS mixer routinely carries a whole building's outputs. Keying
    strips on the Home Assistant device collapsed sixteen zones here into
    one strip showing the first fader and nothing else."""
    controls = []
    for n in range(1, 5):
        controls.append(control(f"Zone {n}", "gain", f"g{n}", group="Public-Area-Mixer"))
        controls.append(control(f"Zone {n}", "mute", f"m{n}", group="Public-Area-Mixer"))

    card, _ = cards.zone_card(controls)
    assert card["options"] == {"zones": 4}
    assert card["labels"] == {
        "zone1": "Zone 1", "zone2": "Zone 2",
        "zone3": "Zone 3", "zone4": "Zone 4",
    }
    assert card["entities"]["zone4_volume"] == "g4"
