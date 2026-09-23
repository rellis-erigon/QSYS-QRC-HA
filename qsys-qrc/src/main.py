"""Q-SYS QRC add-on entry point."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from control_store import STORE_FILE, ControlStore
from server import Hub, build_app

OPTIONS_PATH = Path("/data/options.json")
MANIFEST_PATH = Path("/app/config.yaml")

logger = logging.getLogger("qsys-qrc")


def _version() -> str:
    """The add-on's version, from the build arg or the manifest it shipped with.

    Reading one line does not justify a YAML dependency, and BUILD_VERSION
    has been seen not to survive a Supervisor build.
    """
    from_build = os.environ.get("QSYS_QRC_VERSION")
    if from_build:
        return from_build
    try:
        for line in MANIFEST_PATH.read_text().splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return "unknown"


VERSION = _version()


def load_options() -> dict:
    try:
        return json.loads(OPTIONS_PATH.read_text())
    except FileNotFoundError:
        logger.error("No options.json at %s", OPTIONS_PATH)
        sys.exit(1)
    except json.JSONDecodeError as err:
        logger.error("options.json is not valid JSON: %s", err)
        sys.exit(1)


def setup_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def run() -> None:
    options = load_options()
    setup_logging(options.get("log_level", "info"))
    logger.info("Q-SYS QRC bridge v%s starting", VERSION)

    store = ControlStore(STORE_FILE)
    store.load()
    hub = Hub(store)

    cores = options.get("cores") or []
    if not cores:
        logger.warning(
            "No Cores configured. Add one with its host, and the username "
            "and password if the Core requires a logon."
        )
    for entry in cores:
        host = (entry.get("host") or "").strip()
        if not host:
            continue
        name = (entry.get("name") or host).strip()
        hub.add_core(
            name, host,
            port=int(entry.get("port", 1710)),
            username=(entry.get("username") or "").strip(),
            password=entry.get("password") or "",
            poll_rate=float(options.get("poll_rate", 0.5)),
        )
        logger.info("Core %s at %s:%s", name, host, entry.get("port", 1710))

    await hub.start()

    port = int(os.environ.get("INGRESS_PORT", "8099"))
    runner = web.AppRunner(build_app(hub))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Web UI on port %d", port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    logger.info("Shutting down")
    await hub.stop()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
