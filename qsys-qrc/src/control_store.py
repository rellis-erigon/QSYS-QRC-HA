"""Persistence for discovered controls and what the user made of them.

Discovery is complete and self-describing on Q-SYS, which removes one
problem and leaves another: a modest design offers 302 controls, and almost
none of them belong on a dashboard. So what matters here is the decision —
which controls are worth exposing, what they should be called, and what they
should become.

Two things are kept apart:

  - what the Core reports, refreshed on every connection and redeploy
  - what the user decided, which discovery never overwrites

That separation is the whole point. A design redeploy renames nothing the
user chose; it only refreshes values and ranges.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger("qsys-qrc.store")

STORE_FILE = Path("/config/qsys-qrc/controls.json")

PLATFORM_IGNORE = "ignore"
PLATFORM_SENSOR = "sensor"
PLATFORM_BINARY_SENSOR = "binary_sensor"
PLATFORM_SWITCH = "switch"
PLATFORM_NUMBER = "number"
PLATFORM_TEXT = "text"
PLATFORM_BUTTON = "button"

VALID_PLATFORMS = (
    PLATFORM_IGNORE, PLATFORM_SENSOR, PLATFORM_BINARY_SENSOR,
    PLATFORM_SWITCH, PLATFORM_NUMBER, PLATFORM_TEXT, PLATFORM_BUTTON,
)

# What a control of each Q-SYS type can sensibly become. Direction narrows
# this further: a read-only control can never be a switch.
PLATFORMS_FOR_TYPE = {
    "Boolean": (PLATFORM_IGNORE, PLATFORM_BINARY_SENSOR, PLATFORM_SWITCH,
                PLATFORM_BUTTON),
    "Float": (PLATFORM_IGNORE, PLATFORM_SENSOR, PLATFORM_NUMBER),
    "Integer": (PLATFORM_IGNORE, PLATFORM_SENSOR, PLATFORM_NUMBER),
    "String": (PLATFORM_IGNORE, PLATFORM_SENSOR, PLATFORM_TEXT),
}

READ_ONLY_PLATFORMS = frozenset({
    PLATFORM_IGNORE, PLATFORM_SENSOR, PLATFORM_BINARY_SENSOR,
})


# Where an entity belongs in Home Assistant's own hierarchy. Diagnostic and
# config controls are hidden from a device's main view, which is exactly
# where a threshold or a setup flag should live.
VALID_CATEGORIES = ("", "config", "diagnostic")


@dataclass
class ControlConfig:
    """What the user decided about one control."""

    name: str = ""
    platform: str = ""
    enabled: bool = False
    unit: str = ""
    device_class: str = ""
    # A fader's dB scale is not linear, so driving it by normalised position
    # gives a slider that behaves the way a person expects.
    use_position: bool = False
    # Which Home Assistant device this control belongs to. Defaults to the
    # component, which is right until it is not: one Mixer component carries
    # the outputs for every room in a building, and those belong in the
    # rooms rather than in a device called "Mixer".
    group: str = ""
    # The Home Assistant area. Passed as a suggestion, so Home Assistant
    # creates the area if it does not exist yet.
    area: str = ""
    icon: str = ""
    entity_category: str = ""
    precision: int | None = None
    notes: str = ""

    def resolved_platform(self, suggested: str | None) -> str:
        return self.platform or suggested or PLATFORM_SENSOR

    def resolved_group(self, component: str) -> str:
        return self.group or component


@dataclass
class StoredControl:
    """A control as last seen, plus whatever the user configured."""

    core: str
    component: str
    control: str
    type: str = ""
    direction: str = "Read/Write"
    value: Any = None
    string: str = ""
    position: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    suggested: str | None = None
    updates: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    config: ControlConfig = field(default_factory=ControlConfig)

    @property
    def key(self) -> str:
        return f"{self.component}/{self.control}"

    @property
    def store_key(self) -> str:
        return f"{self.core}/{self.component}/{self.control}"

    @property
    def writable(self) -> bool:
        return self.direction != "Read Only"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["key"] = self.key
        data["writable"] = self.writable
        data["platform"] = self.config.resolved_platform(self.suggested)
        data["group"] = self.config.resolved_group(self.component)
        # The integration feed calls these min and max, and so does the UI.
        # Two names for the same number is how a slider ends up offering
        # "undefined to undefined".
        data["min"] = self.minimum
        data["max"] = self.maximum
        return data


def allowed_platforms(control: StoredControl) -> tuple[str, ...]:
    """What this control may become, given its type and direction."""
    options = PLATFORMS_FOR_TYPE.get(control.type, (PLATFORM_IGNORE, PLATFORM_SENSOR))
    if not control.writable:
        return tuple(p for p in options if p in READ_ONLY_PLATFORMS)
    return options


class ControlStore:
    """Discovered controls plus user configuration, persisted atomically."""

    def __init__(self, path: Path = STORE_FILE) -> None:
        self.path = path
        self.controls: dict[str, StoredControl] = {}
        self._dirty = False

    # -- persistence -----------------------------------------------------

    def load(self) -> int:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return 0
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("Could not read %s: %s", self.path, err)
            return 0

        # to_dict() emits derived fields as well as stored ones, so filter
        # to what the dataclass actually accepts. Keeping a list of keys to
        # strip in sync with to_dict() is a bug waiting to happen, and was
        # one: adding "group" silently stopped the store reloading.
        stored_fields = {f.name for f in fields(StoredControl)} - {"config"}
        config_fields = {f.name for f in fields(ControlConfig)}

        for store_key, data in (raw.get("controls") or {}).items():
            raw_config = data.get("config") or {}
            config = ControlConfig(**{
                k: v for k, v in raw_config.items() if k in config_fields
            })
            try:
                self.controls[store_key] = StoredControl(config=config, **{
                    k: v for k, v in data.items() if k in stored_fields
                })
            except TypeError as err:
                logger.debug("Skipping %s: %s", store_key, err)
        logger.info("Loaded %d controls", len(self.controls))
        return len(self.controls)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "saved": time.time(),
            "controls": {k: v.to_dict() for k, v in self.controls.items()},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        tmp.replace(self.path)
        self._dirty = False

    def save_if_dirty(self) -> bool:
        if not self._dirty:
            return False
        self.save()
        return True

    # -- discovery -------------------------------------------------------

    def observe(
        self, core: str, component: str, control: str, *,
        type: str = "", direction: str = "Read/Write", value: Any = None,
        string: str = "", position: float | None = None,
        minimum: float | None = None, maximum: float | None = None,
        unit: str = "", suggested: str | None = None,
    ) -> StoredControl:
        """Record what the Core says, leaving user configuration untouched."""
        store_key = f"{core}/{component}/{control}"
        entry = self.controls.get(store_key)
        if entry is None:
            entry = StoredControl(
                core=core, component=component, control=control,
            )
            self.controls[store_key] = entry

        entry.type = type or entry.type
        entry.direction = direction or entry.direction
        entry.value = value
        entry.string = string
        entry.position = position
        # Ranges are refreshed because gain staging genuinely changes when a
        # design is redeployed, and a slider offering the old limits is worse
        # than useless.
        entry.minimum = minimum
        entry.maximum = maximum
        entry.unit = unit or entry.unit
        entry.suggested = suggested if suggested is not None else entry.suggested
        entry.updates += 1
        entry.last_seen = time.time()
        self._dirty = True
        return entry

    # -- user intent -----------------------------------------------------

    def configure(self, core: str, key: str, **changes: Any) -> StoredControl:
        store_key = f"{core}/{key}"
        entry = self.controls.get(store_key)
        if entry is None:
            raise KeyError(store_key)

        if "platform" in changes:
            platform = changes["platform"]
            if platform not in VALID_PLATFORMS:
                raise ValueError(f"unknown platform {platform!r}")
            allowed = allowed_platforms(entry)
            if platform not in allowed:
                raise ValueError(
                    f"a {entry.type or 'unknown'} control that is "
                    f"{entry.direction} cannot be a {platform}; "
                    f"choose one of {', '.join(allowed)}"
                )
            entry.config.platform = platform

        if "entity_category" in changes:
            category = (changes["entity_category"] or "").strip()
            if category not in VALID_CATEGORIES:
                raise ValueError(
                    f"unknown entity category {category!r}; "
                    f"choose one of {', '.join(c or '(none)' for c in VALID_CATEGORIES)}"
                )
            entry.config.entity_category = category

        if "precision" in changes:
            value = changes["precision"]
            if value in (None, ""):
                entry.config.precision = None
            else:
                try:
                    entry.config.precision = max(0, min(6, int(value)))
                except (TypeError, ValueError):
                    raise ValueError(f"precision must be a whole number, got {value!r}")

        for field_name in ("name", "unit", "device_class", "group", "area",
                           "icon", "notes"):
            if field_name in changes:
                setattr(entry.config, field_name,
                        str(changes[field_name]).strip())
        for flag in ("enabled", "use_position"):
            if flag in changes:
                setattr(entry.config, flag, bool(changes[flag]))

        self._dirty = True
        return entry

    # -- queries ---------------------------------------------------------

    def for_core(self, core: str) -> list[StoredControl]:
        return sorted(
            (c for c in self.controls.values() if c.core == core),
            key=lambda c: (c.component, c.control),
        )

    def components(self, core: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for control in self.controls.values():
            if control.core == core:
                counts[control.component] = counts.get(control.component, 0) + 1
        return counts

    def exposed(self) -> list[StoredControl]:
        """Controls the user switched on, and which resolve to a platform."""
        return [
            c for c in sorted(
                self.controls.values(),
                key=lambda c: (c.core, c.component, c.control),
            )
            if c.config.enabled
            and c.config.resolved_platform(c.suggested) != PLATFORM_IGNORE
        ]

    def watch_keys(self, core: str) -> list[str]:
        """Which controls are worth subscribing to.

        Subscribing to all 302 controls of a design to watch the 48 someone
        exposed puts a continuous audio meter on the wire for no reason.
        """
        return [
            c.key for c in self.controls.values()
            if c.core == core and (c.config.enabled or c.config.name)
        ]

    def prune(self, core: str, seen: set[str]) -> int:
        """Forget controls a redeploy removed, keeping anything configured.

        A control the user named or enabled is kept even when it disappears,
        because a design is often redeployed in pieces and losing the
        configuration would be far more annoying than a stale row.
        """
        removed = 0
        for store_key, control in list(self.controls.items()):
            if control.core != core or control.key in seen:
                continue
            if control.config.enabled or control.config.name:
                continue
            del self.controls[store_key]
            removed += 1
        if removed:
            self._dirty = True
            logger.info("Forgot %d controls no longer in the design", removed)
        return removed


def areas_in_use(controls: list[StoredControl]) -> list[str]:
    """Every area already assigned, so the UI can offer them again."""
    return sorted({c.config.area for c in controls if c.config.area})


def groups_in_use(controls: list[StoredControl]) -> dict[str, dict]:
    """Devices as they will appear, with the area each one resolves to.

    A group takes its area from the first control in it that names one, so
    assigning the area once is enough to move the whole device.
    """
    groups: dict[str, dict] = {}
    for control in controls:
        name = control.config.resolved_group(control.component)
        entry = groups.setdefault(
            name, {"name": name, "area": "", "controls": 0, "exposed": 0}
        )
        entry["controls"] += 1
        if control.config.enabled:
            entry["exposed"] += 1
        if not entry["area"] and control.config.area:
            entry["area"] = control.config.area
    return groups
