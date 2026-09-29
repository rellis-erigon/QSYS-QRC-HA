"""Build a Lovelace card from the zones a Core exposes.

The Niagara bridge generates one card per device, because a BMS device is
a folder of points that belongs together. Audio is not shaped that way: a
zone is two controls, and nobody wants sixteen cards with two rows each.
So the unit here is the *rack* — one card carrying every zone chosen.

As with Niagara, the add-on knows the control keys and not the Home
Assistant entity ids, so it emits the keys and the integration substitutes.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

CARD_TYPE = "custom:audio-zone-card"
FACEPLATE = "qsys-zone-rack"
MAX_ZONES = 16

# What a zone's two controls are called in a Q-SYS design. Gain blocks are
# conventionally "gain" and "mute", but designers rename them, so match on
# how the control reads rather than on one exact spelling.
_LEVEL = re.compile(r"^(gain|level|volume)$", re.I)
_MUTE = re.compile(r"^(mute|muted)$", re.I)


def _role_controls(controls: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """The zones, keyed by component, with their level and mute.

    Keyed on the **component**, not on the Home Assistant device group.
    One Q-SYS mixer routinely carries the outputs for a whole building —
    here a single "Public-Area-Mixer" group holds sixteen of them — so
    grouping by device collapsed sixteen zones into one strip showing the
    first zone's fader and nothing else.
    """
    zones: dict[str, dict[str, Any]] = {}
    for control in controls:
        if not control.config.enabled:
            continue
        entry = zones.setdefault(control.component, {})
        if _LEVEL.match(control.control) and "level" not in entry:
            entry["level"] = control
        elif _MUTE.match(control.control) and "mute" not in entry:
            entry["mute"] = control
    # A zone with no level is not a zone; a mute on its own has nothing to
    # show, and a strip drawn for it would be a row of dashes.
    return {name: e for name, e in zones.items() if "level" in e}


def _label_for(control: Any, component: str) -> str:
    """What to print on the strip.

    The name someone gave the control is usually "Volume", which is no
    use as a strip label when every strip has one — so the component is
    preferred unless the group is more specific than both.
    """
    return component


def zone_card(
    controls: Iterable[Any], groups: list[str] | None = None,
    title: str = "",
) -> tuple[dict, list[str]] | tuple[None, list[str]]:
    """The card for a set of zones, and the zones that would not fit.

    The faceplate draws at most MAX_ZONES strips. Truncating quietly would
    hand back a card that looks complete while leaving zones off the wall,
    so what was dropped comes back with it.
    """
    zones = _role_controls(controls)
    if groups:
        wanted = [g for g in groups if g in zones]
    else:
        wanted = sorted(zones, key=str.casefold)
    chosen, omitted = wanted[:MAX_ZONES], wanted[MAX_ZONES:]
    if not chosen:
        return None, omitted

    entities: dict[str, str] = {}
    labels: dict[str, str] = {}
    for index, component in enumerate(chosen, start=1):
        entry = zones[component]
        entities[f"zone{index}_volume"] = entry["level"].key
        if "mute" in entry:
            entities[f"zone{index}_mute"] = entry["mute"].key
        labels[f"zone{index}"] = _label_for(entry["level"], component)

    return {
        "type": CARD_TYPE,
        "faceplate": FACEPLATE,
        "title": title or "Audio Zones",
        "options": {"zones": len(chosen)},
        "labels": labels,
        "entities": entities,
    }, omitted


def zone_groups(controls: Iterable[Any]) -> list[str]:
    """The components that could appear on a zone card, for the picker."""
    return sorted(_role_controls(controls), key=str.casefold)
