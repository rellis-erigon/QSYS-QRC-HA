"""HTTP API and ingress UI for the Q-SYS bridge.

Everything runs on one asyncio loop: the QRC connections stay open while the
web server serves from the same process, so the panel reflects a fader
moving as it moves.

The scale is what shapes this. A modest design offers three hundred
controls, so the API is built around narrowing and acting in bulk — per
component, per suggestion — rather than around clicking three hundred times.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from control_store import (
    PLATFORMS_FOR_TYPE, VALID_CATEGORIES, VALID_PLATFORMS, ControlStore,
    allowed_platforms, areas_in_use, groups_in_use,
)
from qrc_client import QrcConnection, QrcError, suggest_platform

logger = logging.getLogger("qsys-qrc.server")

_PACKAGED = Path("/app/static")
STATIC_DIR = _PACKAGED if _PACKAGED.is_dir() else Path(__file__).parent.parent / "static"


class Hub:
    """Owns the Core connections and the control store."""

    def __init__(self, store: ControlStore) -> None:
        self.store = store
        self.cores: dict[str, QrcConnection] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []

    def add_core(
        self, name: str, host: str, port: int = 1710,
        username: str = "", password: str = "", poll_rate: float = 0.5,
    ) -> QrcConnection:
        conn = QrcConnection(
            host, port, name=name, username=username, password=password,
            poll_rate=poll_rate,
        )
        conn._on_change = lambda control, n=name: self._on_change(n, control)
        self.cores[name] = conn
        return conn

    def _on_change(self, core: str, control) -> None:
        stored = self.store.observe(
            core, control.component, control.name,
            type=control.type, direction=control.direction,
            value=control.value, string=control.string,
            position=control.position, minimum=control.value_min,
            maximum=control.value_max, unit=control.unit,
            suggested=suggest_platform(control),
        )
        self._publish({
            "type": "control",
            "core": core,
            "key": stored.key,
            "component": stored.component,
            "control": stored.control,
            "value": stored.value,
            "string": stored.string,
            "position": stored.position,
            "updates": stored.updates,
            "at": time.time(),
        })

    # -- live stream -----------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A browser that stopped reading must not stall the hub. An
                # audio meter alone can outrun a slow client.
                self._subscribers.discard(queue)

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        for name in self.cores:
            self._tasks.append(asyncio.create_task(self._run_core(name)))
        self._tasks.append(asyncio.create_task(self._autosave()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for conn in self.cores.values():
            await conn.close()
        self.store.save_if_dirty()

    async def _run_core(self, name: str, base_delay: float = 5.0) -> None:
        """Keep one Core connected, rediscovering after every reconnect."""
        conn = self.cores[name]
        delay = base_delay
        while True:
            reader_task: asyncio.Task | None = None
            try:
                await conn.connect()
                reader_task = asyncio.create_task(self._pump(conn))
                await conn.logon()
                await self._rediscover(name, conn)
                delay = base_delay
                await self._keepalive(conn)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a Core must never stop us
                conn.last_error = str(err)
                logger.warning("%s: %s — reconnecting in %.0fs", name, err, delay)
            finally:
                if reader_task is not None:
                    reader_task.cancel()
                await conn.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120.0)

    async def _pump(self, conn: QrcConnection) -> None:
        """Read frames and route them until the Core goes away."""
        while True:
            assert conn._reader is not None
            chunk = await conn._reader.read(1 << 20)
            if not chunk:
                raise ConnectionError(f"{conn.name} closed the connection")
            for message in conn.feed(chunk):
                conn.dispatch(message)

    async def _rediscover(self, name: str, conn: QrcConnection) -> None:
        await conn.discover()
        seen = set()
        for control in conn.controls.values():
            self.store.observe(
                name, control.component, control.name,
                type=control.type, direction=control.direction,
                value=control.value, string=control.string,
                position=control.position, minimum=control.value_min,
                maximum=control.value_max, unit=control.unit,
                suggested=suggest_platform(control),
            )
            seen.add(control.key)
        self.store.prune(name, seen)
        self.store.save()

        watch = self.store.watch_keys(name)
        # Before anything is configured there is nothing worth watching, and
        # subscribing to everything would put a live audio meter on the wire
        # for no reason.
        if watch:
            await conn.subscribe(watch)

    async def _keepalive(self, conn: QrcConnection, interval: float = 20.0) -> None:
        """A Core drops a connection that says nothing."""
        while True:
            await asyncio.sleep(interval)
            await conn.notify("NoOp")

    async def resubscribe(self, core: str) -> None:
        """Re-watch after the exposed set changes."""
        conn = self.cores.get(core)
        if conn is None or not conn.logged_on:
            return
        try:
            await conn.subscribe(self.store.watch_keys(core))
        except (QrcError, OSError) as err:
            logger.debug("Could not resubscribe on %s: %s", core, err)

    async def _autosave(self, interval: float = 30.0) -> None:
        """Persist periodically rather than on every change.

        A watched fader can move many times a second; writing the store each
        time would spend the add-on's life in the filesystem.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                if self.store.save_if_dirty():
                    logger.debug("Saved %d controls", len(self.store.controls))
            except OSError as err:
                logger.warning("Could not save the control store: %s", err)


# -- API -----------------------------------------------------------------

def _payload(control) -> dict:
    data = control.to_dict()
    data["config"] = {
        "name": control.config.name,
        "platform": control.config.platform,
        "enabled": control.config.enabled,
        "unit": control.config.unit,
        "device_class": control.config.device_class,
        "use_position": control.config.use_position,
        "notes": control.config.notes,
    }
    data["allowed"] = list(allowed_platforms(control))
    return data


async def status(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    return web.json_response({
        "cores": [
            {**conn.snapshot(), "stored_controls": len(hub.store.for_core(name))}
            for name, conn in hub.cores.items()
        ],
        "total_controls": len(hub.store.controls),
        "exposed_controls": len(hub.store.exposed()),
    })


async def components(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    core = request.query.get("core", "")
    conn = hub.cores.get(core)
    counts = hub.store.components(core)
    exposed: dict[str, int] = {}
    for control in hub.store.for_core(core):
        if control.config.enabled:
            exposed[control.component] = exposed.get(control.component, 0) + 1
    return web.json_response({
        "components": [
            {
                "name": name,
                "type": (conn.components.get(name, "") if conn else ""),
                "controls": count,
                "exposed": exposed.get(name, 0),
            }
            for name, count in sorted(counts.items())
        ],
    })


async def list_controls(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    core = request.query.get("core", "")
    component = request.query.get("component", "")
    search = request.query.get("search", "").lower()
    only = request.query.get("only", "")
    limit = min(int(request.query.get("limit", 500)), 2000)

    controls = (hub.store.for_core(core) if core
                else sorted(hub.store.controls.values(),
                            key=lambda c: (c.core, c.component, c.control)))
    if component:
        controls = [c for c in controls if c.component == component]
    if only == "enabled":
        controls = [c for c in controls if c.config.enabled]
    elif only == "named":
        controls = [c for c in controls if c.config.name]
    elif only == "suggested":
        controls = [c for c in controls if c.suggested]
    elif only == "writable":
        controls = [c for c in controls if c.writable]
    if search:
        controls = [
            c for c in controls
            if search in c.key.lower()
            or search in c.config.name.lower()
            or search in str(c.string).lower()
        ]

    return web.json_response({
        "controls": [_payload(c) for c in controls[:limit]],
        "total": len(controls),
        "truncated": max(0, len(controls) - limit),
    })


_CONFIG_FIELDS = (
    "name", "platform", "enabled", "unit", "device_class", "use_position",
    "group", "area", "icon", "entity_category", "precision", "notes",
)

# Home Assistant's own area list, read straight from its registry. Only used
# to offer existing names for reuse — a new name is passed to Home Assistant
# as a suggestion and the area gets created, so this is convenience rather
# than a constraint.
AREA_REGISTRY = Path("/config/.storage/core.area_registry")


def _known_areas() -> list[str]:
    try:
        data = json.loads(AREA_REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    entries = (data.get("data") or {}).get("areas") or []
    return sorted(
        {a.get("name", "") for a in entries if a.get("name")},
        key=str.casefold,
    )


async def configure_control(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    body = await request.json()
    core = (body.get("core") or "").strip()
    key = (body.get("key") or "").strip()
    if not core or not key:
        raise web.HTTPBadRequest(reason="core and key are required")

    changes = {k: body[k] for k in _CONFIG_FIELDS if k in body}
    try:
        control = hub.store.configure(core, key, **changes)
    except KeyError:
        raise web.HTTPNotFound(reason=f"unknown control {core}/{key}")
    except ValueError as err:
        raise web.HTTPBadRequest(reason=str(err))
    hub.store.save()
    if "enabled" in changes:
        await hub.resubscribe(core)
    return web.json_response({"ok": True, "control": _payload(control)})


async def bulk_configure(request: web.Request) -> web.Response:
    """Apply one change to many controls.

    Three hundred controls is too many to click through, and a design is
    regular: every gain component has the same four controls. So the useful
    unit of work is "expose the gain and mute on every one of these".
    """
    hub: Hub = request.app["hub"]
    body = await request.json()
    core = (body.get("core") or "").strip()
    keys = body.get("keys") or []
    if not core or not isinstance(keys, list):
        raise web.HTTPBadRequest(reason="core and keys are required")
    if len(keys) > 2000:
        raise web.HTTPBadRequest(reason="too many controls in one request")

    changes = {k: body[k] for k in _CONFIG_FIELDS if k in body}
    # "Apply the suggestion" cannot be one shared value, so it is a mode.
    use_suggested = bool(body.get("use_suggested"))
    if not changes and not use_suggested:
        raise web.HTTPBadRequest(reason="nothing to change")

    applied, skipped = 0, []
    for key in keys:
        control = hub.store.controls.get(f"{core}/{key}")
        if control is None:
            skipped.append({"key": key, "reason": "unknown"})
            continue
        this = dict(changes)
        if use_suggested:
            if not control.suggested:
                skipped.append({"key": key, "reason": "nothing suggested"})
                continue
            this["platform"] = control.suggested
        try:
            hub.store.configure(core, key, **this)
        except (KeyError, ValueError) as err:
            skipped.append({"key": key, "reason": str(err)})
            continue
        applied += 1

    hub.store.save()
    await hub.resubscribe(core)
    return web.json_response({
        "ok": True, "applied": applied,
        "skipped": skipped[:50], "skipped_total": len(skipped),
    })


async def set_control(request: web.Request) -> web.Response:
    """Write a value to a Core."""
    hub: Hub = request.app["hub"]
    body = await request.json()
    core = (body.get("core") or "").strip()
    key = (body.get("key") or "").strip()
    conn = hub.cores.get(core)
    if conn is None:
        raise web.HTTPNotFound(reason=f"unknown core {core}")
    if not conn.logged_on:
        raise web.HTTPServiceUnavailable(reason=f"{core} is not connected")
    if "/" not in key:
        raise web.HTTPBadRequest(reason=f"malformed control key {key!r}")

    component, _, control = key.partition("/")
    stored = hub.store.controls.get(f"{core}/{key}")
    if stored is not None and not stored.writable:
        raise web.HTTPBadRequest(reason=f"{key} is read only")

    try:
        if "position" in body:
            await conn.set_position(component, control, float(body["position"]))
        else:
            await conn.set_control(component, control, body.get("value"))
    except (QrcError, OSError, ConnectionError, asyncio.TimeoutError) as err:
        raise web.HTTPServiceUnavailable(reason=str(err))
    return web.json_response({"ok": True})


async def events(request: web.Request) -> web.StreamResponse:
    """Server-sent events, one per control change."""
    hub: Hub = request.app["hub"]
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    queue = hub.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            await response.write(f"data: {json.dumps(event)}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.unsubscribe(queue)
    return response


async def integration_controls(request: web.Request) -> web.Response:
    """What the Home Assistant integration consumes: exposed controls only."""
    hub: Hub = request.app["hub"]
    return web.json_response({
        "controls": [
            {
                "core": c.core,
                "key": c.key,
                "component": c.component,
                "control": c.control,
                "group": c.config.resolved_group(c.component),
                "area": c.config.area,
                "icon": c.config.icon,
                "entity_category": c.config.entity_category,
                "precision": c.config.precision,
                "platform": c.config.resolved_platform(c.suggested),
                "name": c.config.name or f"{c.component} {c.control}",
                "value": c.value,
                "string": c.string,
                "position": c.position,
                "min": c.minimum,
                "max": c.maximum,
                "unit": c.config.unit or c.unit,
                "device_class": c.config.device_class,
                "use_position": c.config.use_position,
                "writable": c.writable,
                "available": bool(
                    hub.cores.get(c.core) and hub.cores[c.core].logged_on
                ),
            }
            for c in hub.store.exposed()
        ],
        "cores": {
            name: {
                "logged_on": conn.logged_on, "host": conn.host,
                "design": conn.engine.get("DesignName", ""),
                "platform": conn.engine.get("Platform", ""),
            }
            for name, conn in hub.cores.items()
        },
    })


async def areas(request: web.Request) -> web.Response:
    """Areas to choose from: Home Assistant's, plus any already assigned."""
    hub: Hub = request.app["hub"]
    assigned = areas_in_use(list(hub.store.controls.values()))
    known = _known_areas()
    return web.json_response({
        "areas": sorted(set(known) | set(assigned), key=str.casefold),
        "from_home_assistant": known,
        "in_use": assigned,
        "categories": list(VALID_CATEGORIES),
    })


async def groups(request: web.Request) -> web.Response:
    """Devices as they will appear in Home Assistant."""
    hub: Hub = request.app["hub"]
    core = request.query.get("core", "")
    controls = hub.store.for_core(core) if core else list(hub.store.controls.values())
    return web.json_response({
        "groups": sorted(
            groups_in_use(controls).values(), key=lambda g: g["name"].casefold()
        ),
    })


async def platforms(request: web.Request) -> web.Response:
    return web.json_response({
        "platforms": list(VALID_PLATFORMS),
        "by_type": {k: list(v) for k, v in PLATFORMS_FOR_TYPE.items()},
    })


async def index(request: web.Request) -> web.Response:
    """Serve the panel for any path that is not an API call.

    Ingress composes the URL it forwards and does not always compose what
    you expect; a stray double slash once made a whole panel 404.
    """
    if request.path.startswith("/api/"):
        raise web.HTTPNotFound()
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return web.Response(text="UI not installed", status=404)
    return web.FileResponse(page)


def build_app(hub: Hub) -> web.Application:
    app = web.Application()
    app["hub"] = hub
    app.add_routes([
        web.get("/api/status", status),
        web.get("/api/platforms", platforms),
        web.get("/api/components", components),
        web.get("/api/areas", areas),
        web.get("/api/groups", groups),
        web.get("/api/controls", list_controls),
        web.post("/api/controls/configure", configure_control),
        web.post("/api/controls/bulk", bulk_configure),
        web.post("/api/controls/set", set_control),
        web.get("/api/events", events),
        web.get("/api/integration/controls", integration_controls),
    ])
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR)
    app.router.add_get("/{tail:.*}", index)
    return app
