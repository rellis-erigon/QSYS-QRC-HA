"""Keeps Home Assistant in step with the Q-SYS add-on.

Two channels. A Server-Sent Events stream carries control changes as they
happen, because a fader that catches up a poll interval later is a fader
nobody will trust. A slow reconcile poll picks up controls newly exposed or
renamed in the add-on UI, and covers anything the stream missed while
reconnecting.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDON_URL, RECONCILE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QsysControl:
    """One exposed control, as the add-on describes it."""

    core: str
    key: str
    component: str
    control: str
    platform: str
    name: str
    value: Any = None
    string: str = ""
    position: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    device_class: str = ""
    use_position: bool = False
    writable: bool = True
    available: bool = True

    @property
    def store_key(self) -> str:
        return f"{self.core}/{self.key}"

    @classmethod
    def from_json(cls, data: dict) -> "QsysControl":
        return cls(
            core=data["core"],
            key=data["key"],
            component=data.get("component", ""),
            control=data.get("control", ""),
            platform=data.get("platform") or "sensor",
            name=data.get("name") or data["key"],
            value=data.get("value"),
            string=str(data.get("string") or ""),
            position=data.get("position"),
            minimum=data.get("min"),
            maximum=data.get("max"),
            unit=data.get("unit") or "",
            device_class=data.get("device_class") or "",
            use_position=bool(data.get("use_position")),
            writable=bool(data.get("writable", True)),
            available=bool(data.get("available", True)),
        )


class QsysCoordinator(DataUpdateCoordinator[dict[str, QsysControl]]):
    """Holds the exposed controls and their current values."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name="Q-SYS Bridge",
            update_interval=timedelta(seconds=RECONCILE_INTERVAL),
        )
        self.entry = entry
        self.url = entry.data[CONF_ADDON_URL].rstrip("/")
        self.cores: dict[str, dict] = {}
        self._session = async_get_clientsession(hass)
        self._stream_task: asyncio.Task | None = None

    async def _async_update_data(self) -> dict[str, QsysControl]:
        try:
            async with self._session.get(
                f"{self.url}/api/integration/controls",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Could not reach the add-on: {err}") from err

        self.cores = payload.get("cores", {})
        controls = {}
        for entry in payload.get("controls", []):
            try:
                control = QsysControl.from_json(entry)
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Skipping malformed control %s", entry)
                continue
            controls[control.store_key] = control

        self._ensure_stream()
        return controls

    # -- live stream -----------------------------------------------------

    def _ensure_stream(self) -> None:
        if self._stream_task is None or self._stream_task.done():
            self._stream_task = self.entry.async_create_background_task(
                self.hass, self._run_stream(), "qsys_bridge_events",
            )

    async def _run_stream(self) -> None:
        backoff = 1
        while True:
            try:
                await self._consume_stream()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - the stream must not die
                _LOGGER.debug("Event stream dropped (%s); retry in %ss", err, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _consume_stream(self) -> None:
        # No total timeout: the stream stays open, and the add-on sends a
        # keepalive comment every 20 seconds.
        timeout = aiohttp.ClientTimeout(total=None, sock_read=90)
        async with self._session.get(f"{self.url}/api/events", timeout=timeout) as response:
            response.raise_for_status()
            async for raw in response.content:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    self._apply_event(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue

    def _apply_event(self, event: dict) -> None:
        if event.get("type") != "control" or not self.data:
            return
        store_key = f"{event.get('core')}/{event.get('key')}"
        current = self.data.get(store_key)
        if current is None:
            return
        if (current.value == event.get("value")
                and current.string == (event.get("string") or "")):
            return
        updated = dict(self.data)
        updated[store_key] = replace(
            current,
            value=event.get("value"),
            string=str(event.get("string") or ""),
            position=event.get("position"),
        )
        self.async_set_updated_data(updated)

    # -- dynamic entities ------------------------------------------------

    def new_controls(self, platform: str, existing: set[str]) -> list[QsysControl]:
        if not self.data:
            return []
        return [
            c for key, c in self.data.items()
            if c.platform == platform and key not in existing
        ]

    # -- writing ---------------------------------------------------------

    async def async_set(self, control: QsysControl, **payload: Any) -> None:
        body = {"core": control.core, "key": control.key, **payload}
        try:
            async with self._session.post(
                f"{self.url}/api/controls/set", json=body,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status >= 400:
                    raise UpdateFailed(
                        f"{control.key}: {response.reason or response.status}"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Could not set {control.key}: {err}") from err
