"""Q-SYS Remote Control (QRC) client.

QRC is JSON-RPC 2.0 over TCP 1710, with each message terminated by a null
byte instead of a newline. The Core also pushes frames nobody asked for —
change-group polls, status updates — so a reply has to be matched by id
rather than simply read next.

Unlike most control protocols this one properly introspects: the Core will
list every component and every control on it, with type, direction, range
and the formatted string it displays. There is no guessing here, which is
why this client does none.

The one thing it cannot see is a component whose Script Access is left at
None or Script in Q-SYS Designer. Those are invisible over QRC, and a design
where nobody set it answers cheerfully with almost nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("qsys-qrc.client")

DEFAULT_PORT = 1710

# The Core drops a connection that says nothing. Its own documentation
# suggests a keepalive well inside 60s.
KEEPALIVE_INTERVAL = 20.0

CHANGE_GROUP_ID = "qsys_bridge"

# Controls that configure the DSP rather than operate it. Surfacing these
# puts "invert polarity" next to the volume slider on someone's dashboard.
SETUP_CONTROLS = frozenset({
    "invert", "bypass", "hold.time", "infinite.hold", "pre.post",
})


class QrcError(Exception):
    """The Core answered, and the answer was no."""


class LogonError(QrcError):
    """Credentials were rejected."""


@dataclass
class Control:
    """One control on one component, as the Core describes it."""

    component: str
    name: str
    type: str = ""
    direction: str = "Read/Write"
    value: Any = None
    string: str = ""
    position: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    string_min: str = ""
    string_max: str = ""
    updates: int = 0
    last_seen: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.component}/{self.name}"

    @property
    def writable(self) -> bool:
        return self.direction != "Read Only"

    @property
    def unit(self) -> str:
        """The unit Q-SYS already displays, rather than one we invented."""
        for text in (self.string_max, self.string_min, self.string):
            unit = _unit_from(text)
            if unit:
                return unit
        return ""

    @classmethod
    def from_json(cls, component: str, data: dict) -> "Control":
        return cls(
            component=component,
            name=data.get("Name", ""),
            type=data.get("Type", ""),
            direction=data.get("Direction", "Read/Write"),
            value=data.get("Value"),
            string=str(data.get("String", "") or ""),
            position=data.get("Position"),
            value_min=data.get("ValueMin"),
            value_max=data.get("ValueMax"),
            string_min=str(data.get("StringMin", "") or ""),
            string_max=str(data.get("StringMax", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "component": self.component, "name": self.name, "key": self.key,
            "type": self.type, "direction": self.direction,
            "writable": self.writable, "value": self.value,
            "string": self.string, "position": self.position,
            "min": self.value_min, "max": self.value_max,
            "unit": self.unit, "updates": self.updates,
            "last_seen": self.last_seen,
        }


# "-50.0dB" -> "dB", "1.00s" -> "s", "muted" -> nothing. A unit only counts
# when it trails an actual number.
_UNIT_RE = re.compile(r"^[+-]?[\d.]+\s*([^\d\s].*)$")


def _unit_from(text: str) -> str:
    """Pull a unit off a value Q-SYS has already formatted."""
    match = _UNIT_RE.match((text or "").strip())
    return match.group(1).strip() if match else ""


def suggest_platform(control: Control) -> str | None:
    """Which Home Assistant platform a control obviously belongs on.

    Conservative on purpose: a control becomes an entity only where its type
    and direction make the intent unambiguous. Anything else is left for a
    person to decide in the UI.
    """
    if control.name in SETUP_CONTROLS or control.name.endswith(".label"):
        return None
    if control.type == "Boolean":
        return "switch" if control.writable else "binary_sensor"
    if control.type in ("Float", "Integer"):
        return "number" if control.writable else "sensor"
    if control.type == "String":
        return "text" if control.writable else "sensor"
    return None


class QrcConnection:
    """One long-lived connection to a Core."""

    def __init__(
        self, host: str, port: int = DEFAULT_PORT, name: str = "",
        username: str = "", password: str = "", poll_rate: float = 0.5,
    ) -> None:
        self.host = host
        self.port = port
        self.name = name or host
        self.username = username
        self.password = password
        self.poll_rate = poll_rate

        self.connected = False
        self.logged_on = False
        self.discovery_complete = False
        self.last_error = ""
        self.connected_since: float | None = None
        self.engine: dict = {}
        self.components: dict[str, str] = {}
        self.controls: dict[str, Control] = {}

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buf = b""
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._on_change: Callable[[Control], None] | None = None

    # -- transport -------------------------------------------------------

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        self.connected = True
        self.connected_since = time.time()
        self.last_error = ""
        logger.info("Connected to %s:%d", self.host, self.port)

    async def close(self) -> None:
        self.connected = self.logged_on = self.discovery_complete = False
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass
        self._reader = self._writer = None
        self._buf = b""

    async def _send(self, payload: dict) -> None:
        if self._writer is None:
            raise ConnectionError("not connected")
        self._writer.write(json.dumps(payload).encode() + b"\x00")
        await self._writer.drain()

    async def call(
        self, method: str, params: Any = None, timeout: float = 30.0,
    ) -> Any:
        """Send a request and wait for the reply with the matching id."""
        self._id += 1
        request_id = self._id
        payload: dict[str, Any] = {
            "jsonrpc": "2.0", "method": method, "id": request_id,
        }
        if params is not None:
            payload["params"] = params

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(payload)
            reply = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

        if isinstance(reply, dict) and "error" in reply:
            error = reply["error"]
            message = error.get("message") if isinstance(error, dict) else error
            raise QrcError(f"{method}: {message}")
        return reply.get("result") if isinstance(reply, dict) else reply

    async def notify(self, method: str, params: Any = None) -> None:
        """Fire and forget — used for the keepalive."""
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    # -- framing ---------------------------------------------------------

    def feed(self, chunk: bytes) -> list[dict]:
        """Split a raw read into whole messages. Exposed for testing."""
        self._buf += chunk
        messages = []
        while b"\x00" in self._buf:
            raw, self._buf = self._buf.split(b"\x00", 1)
            if not raw.strip():
                continue
            try:
                messages.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.debug("Ignoring unparseable frame from %s", self.host)
        return messages

    def dispatch(self, message: dict) -> None:
        """Route one message: a reply to its waiter, or a change to the hook."""
        message_id = message.get("id")
        if message_id is not None and message_id in self._pending:
            future = self._pending[message_id]
            if not future.done():
                future.set_result(message)
            return
        if message.get("method") == "ChangeGroup.Poll":
            for change in (message.get("params") or {}).get("Changes", []) or []:
                self._apply_change(change)

    def _apply_change(self, change: dict) -> None:
        component = change.get("Component") or ""
        name = change.get("Name") or ""
        if not name:
            return
        key = f"{component}/{name}"
        control = self.controls.get(key)
        if control is None:
            # A control that appears mid-session, usually because the design
            # was redeployed. Record it rather than dropping the update.
            control = Control(component=component, name=name)
            self.controls[key] = control
        control.value = change.get("Value", control.value)
        control.string = str(change.get("String", control.string) or "")
        if change.get("Position") is not None:
            control.position = change["Position"]
        control.updates += 1
        control.last_seen = time.time()
        if self._on_change is not None:
            self._on_change(control)

    # -- session ---------------------------------------------------------

    async def logon(self) -> None:
        if not self.username:
            self.logged_on = True
            return
        try:
            await self.call("Logon", {
                "User": self.username, "Password": self.password,
            })
        except QrcError as err:
            raise LogonError(str(err)) from err
        self.logged_on = True

    async def discover(self) -> int:
        """Enumerate every visible component and its controls."""
        status = await self.call("StatusGet", 0)
        self.engine = status if isinstance(status, dict) else {}

        components = await self.call("Component.GetComponents") or []
        self.components = {
            c.get("Name", ""): c.get("Type", "") for c in components
            if c.get("Name")
        }

        found = 0
        for name in self.components:
            result = await self.call("Component.GetControls", {"Name": name})
            for data in (result or {}).get("Controls", []) or []:
                control = Control.from_json(name, data)
                existing = self.controls.get(control.key)
                if existing is not None:
                    control.updates = existing.updates
                self.controls[control.key] = control
                found += 1

        self.discovery_complete = True
        logger.info(
            "Discovered %d controls across %d components on %s",
            found, len(self.components), self.name,
        )
        if not self.components:
            logger.warning(
                "%s reported no components. In Q-SYS Designer every "
                "component has a Script Access property, and QRC only sees "
                "External or All.", self.name,
            )
        return found

    async def subscribe(self, keys: list[str] | None = None) -> None:
        """Watch controls, so changes arrive instead of being polled for."""
        wanted = keys if keys is not None else list(self.controls)
        by_component: dict[str, list[str]] = {}
        for key in wanted:
            control = self.controls.get(key)
            if control is None:
                continue
            by_component.setdefault(control.component, []).append(control.name)

        await self.call("ChangeGroup.Destroy", {"Id": CHANGE_GROUP_ID})
        for component, names in by_component.items():
            await self.call("ChangeGroup.AddComponentControl", {
                "Id": CHANGE_GROUP_ID,
                "Component": {
                    "Name": component,
                    "Controls": [{"Name": n} for n in names],
                },
            })
        if by_component:
            await self.call("ChangeGroup.AutoPoll", {
                "Id": CHANGE_GROUP_ID, "Rate": self.poll_rate,
            })
            logger.info(
                "Watching %d controls on %s",
                sum(len(v) for v in by_component.values()), self.name,
            )

    async def set_control(self, component: str, name: str, value: Any) -> None:
        await self.call("Component.Set", {
            "Name": component, "Controls": [{"Name": name, "Value": value}],
        })

    async def set_position(
        self, component: str, name: str, position: float,
    ) -> None:
        """Set by normalised 0-1 position rather than by value.

        A fader's dB scale is not linear, so a UI slider that moves in
        position feels right where one moving in dB does not.
        """
        await self.call("Component.Set", {
            "Name": component,
            "Controls": [{"Name": name, "Position": position}],
        })

    def snapshot(self) -> dict:
        return {
            "name": self.name, "host": self.host, "port": self.port,
            "connected": self.connected, "logged_on": self.logged_on,
            "discovery_complete": self.discovery_complete,
            "connected_since": self.connected_since,
            "last_error": self.last_error,
            "component_count": len(self.components),
            "control_count": len(self.controls),
            "design": self.engine.get("DesignName", ""),
            "platform": self.engine.get("Platform", ""),
            "state": self.engine.get("State", ""),
            "status": (self.engine.get("Status") or {}).get("String", ""),
        }
