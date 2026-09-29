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
    """Group exposed controls by device, keeping the level and the mute."""
    zones: dict[str, dict[str, Any]] = {}
    for control in controls:
        if not control.config.enabled:
            continue
        group = control.config.resolved_group(control.component)
        entry = zones.setdefault(group, {})
        if _LEVEL.match(control.control) and "level" not in entry:
            entry["level"] = control
        elif _MUTE.match(control.control) and "mute" not in entry:
            entry["mute"] = control
    # A zone with no level is not a zone; a mute on its own has nothing to
    # show, and a strip drawn for it would be a row of dashes.
    return {name: e for name, e in zones.items() if "level" in e}


def zone_card(controls: Iterable[Any], groups: list[str] | None = None) -> dict | None:
    """The card for a set of zones, or None if there are none to draw."""
    zones = _role_controls(controls)
    if groups:
        chosen = [g for g in groups if g in zones]
    else:
        chosen = sorted(zones, key=str.casefold)
    chosen = chosen[:MAX_ZONES]
    if not chosen:
        return None

    entities: dict[str, str] = {}
    labels: dict[str, str] = {}
    for index, group in enumerate(chosen, start=1):
        entry = zones[group]
        entities[f"zone{index}_volume"] = entry["level"].key
        if "mute" in entry:
            entities[f"zone{index}_mute"] = entry["mute"].key
        labels[f"zone{index}"] = group

    return {
        "type": CARD_TYPE,
        "faceplate": FACEPLATE,
        "options": {"zones": len(chosen)},
        "labels": labels,
        "entities": entities,
    }


def zone_groups(controls: Iterable[Any]) -> list[str]:
    """The devices that could appear on a zone card, for the picker."""
    return sorted(_role_controls(controls), key=str.casefold)
