"""Tests for the add-on's HTTP API, driven through a real aiohttp client."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from control_store import ControlStore  # noqa: E402
from qrc_client import Control  # noqa: E402
from server import Hub, build_app  # noqa: E402


class FakeCore:
    """Stands in for QrcConnection; records what would go on the wire."""

    def __init__(self, host="192.0.2.10", name="Core") -> None:
        self.host, self.name, self.port = host, name, 1710
        self.connected = self.logged_on = self.discovery_complete = True
        self.connected_since = 1_700_000_000.0
        self.last_error = ""
        self.components = {"Zone 1": "gain", "Meter": "meter2"}
        self.controls: dict = {}
        self.engine = {"DesignName": "Design", "Platform": "Core 110f",
                       "State": "Active", "Status": {"String": "OK"}}
        self.writes: list[tuple] = []
        self.subscribed: list[str] = []

    def snapshot(self) -> dict:
        return {
            "name": self.name, "host": self.host, "port": self.port,
            "connected": self.connected, "logged_on": self.logged_on,
            "discovery_complete": True, "connected_since": self.connected_since,
            "last_error": "", "component_count": len(self.components),
            "control_count": len(self.controls),
            "design": "Design", "platform": "Core 110f",
            "state": "Active", "status": "OK",
        }

    async def set_control(self, component, name, value):
        self.writes.append(("value", component, name, value))

    async def set_position(self, component, name, position):
        self.writes.append(("position", component, name, position))

    async def subscribe(self, keys):
        self.subscribed = list(keys)

    async def close(self):
        self.logged_on = False


def _control(component, name, type="Float", direction="Read/Write",
             value=-25.0, minimum=-50.0, maximum=-25.0):
    return Control(
        component=component, name=name, type=type, direction=direction,
        value=value, string=f"{value}dB", value_min=minimum,
        value_max=maximum, string_min="-50.0dB", string_max="-25.0dB",
    )


@pytest.fixture
def hub(tmp_path):
    store = ControlStore(tmp_path / "controls.json")
    hub = Hub(store)
    hub.cores["Core"] = FakeCore()
    hub._on_change("Core", _control("Zone 1", "gain"))
    hub._on_change("Core", _control("Zone 1", "mute", type="Boolean",
                                    value=True, minimum=None, maximum=None))
    hub._on_change("Core", _control("Meter", "meter.1",
                                    direction="Read Only", value=-120.0))
    return hub


@pytest.fixture
async def client(hub, aiohttp_client):
    return await aiohttp_client(build_app(hub))


# -- reading -------------------------------------------------------------

async def test_status_summarises_each_core(client):
    body = await (await client.get("/api/status")).json()
    assert body["total_controls"] == 3
    assert body["exposed_controls"] == 0
    core = body["cores"][0]
    assert core["name"] == "Core" and core["stored_controls"] == 3


async def test_controls_carry_their_suggestion_and_allowed_platforms(client):
    body = await (await client.get("/api/controls")).json()
    by_key = {c["key"]: c for c in body["controls"]}
    assert by_key["Zone 1/gain"]["platform"] == "number"
    assert by_key["Zone 1/mute"]["platform"] == "switch"
    # A read-only meter must not be offered as something writable.
    assert "number" not in by_key["Meter/meter.1"]["allowed"]


async def test_controls_can_be_narrowed(client):
    one = await (await client.get("/api/controls?component=Meter")).json()
    assert [c["key"] for c in one["controls"]] == ["Meter/meter.1"]

    writable = await (await client.get("/api/controls?only=writable")).json()
    assert "Meter/meter.1" not in [c["key"] for c in writable["controls"]]

    found = await (await client.get("/api/controls?search=mute")).json()
    assert [c["key"] for c in found["controls"]] == ["Zone 1/mute"]


async def test_components_report_how_many_are_exposed(client, hub):
    hub.store.configure("Core", "Zone 1/gain", enabled=True)
    body = await (await client.get("/api/components?core=Core")).json()
    zone = next(c for c in body["components"] if c["name"] == "Zone 1")
    assert zone["controls"] == 2 and zone["exposed"] == 1
    assert zone["type"] == "gain"


async def test_platforms_are_constrained_by_type(client):
    body = await (await client.get("/api/platforms")).json()
    assert "switch" in body["by_type"]["Boolean"]
    assert "switch" not in body["by_type"]["Float"]


# -- configuring ---------------------------------------------------------

async def test_configuring_persists_and_resubscribes(client, hub):
    response = await client.post("/api/controls/configure", json={
        "core": "Core", "key": "Zone 1/gain",
        "name": "Bar Volume", "enabled": True, "use_position": True,
    })
    assert response.status == 200
    body = await response.json()
    assert body["control"]["config"]["name"] == "Bar Volume"

    saved = json.loads(hub.store.path.read_text())
    assert saved["controls"]["Core/Zone 1/gain"]["config"]["name"] == "Bar Volume"
    # Enabling something changes what is worth watching.
    assert hub.cores["Core"].subscribed == ["Zone 1/gain"]


async def test_a_platform_the_control_cannot_be_is_refused(client):
    response = await client.post("/api/controls/configure", json={
        "core": "Core", "key": "Meter/meter.1", "platform": "switch",
    })
    assert response.status == 400


async def test_configuring_an_unknown_control_is_404(client):
    response = await client.post("/api/controls/configure", json={
        "core": "Core", "key": "Nope/gain", "name": "x",
    })
    assert response.status == 404


# -- bulk ----------------------------------------------------------------

async def test_bulk_applies_one_change_to_many(client, hub):
    response = await client.post("/api/controls/bulk", json={
        "core": "Core", "keys": ["Zone 1/gain", "Zone 1/mute"],
        "enabled": True,
    })
    body = await response.json()
    assert body["applied"] == 2
    assert len(hub.store.exposed()) == 2


async def test_bulk_can_apply_each_control_s_own_suggestion(client, hub):
    """One shared platform makes no sense across mixed types."""
    response = await client.post("/api/controls/bulk", json={
        "core": "Core", "keys": ["Zone 1/gain", "Zone 1/mute"],
        "use_suggested": True, "enabled": True,
    })
    assert (await response.json())["applied"] == 2
    assert hub.store.controls["Core/Zone 1/gain"].config.platform == "number"
    assert hub.store.controls["Core/Zone 1/mute"].config.platform == "switch"


async def test_bulk_reports_what_it_could_not_do_rather_than_failing(client):
    response = await client.post("/api/controls/bulk", json={
        "core": "Core", "keys": ["Zone 1/gain", "Nope/x"], "enabled": True,
    })
    body = await response.json()
    assert body["applied"] == 1
    assert body["skipped"][0]["reason"] == "unknown"


async def test_bulk_with_nothing_to_change_is_rejected(client):
    response = await client.post("/api/controls/bulk", json={
        "core": "Core", "keys": ["Zone 1/gain"],
    })
    assert response.status == 400


async def test_bulk_refuses_an_unreasonable_number(client):
    response = await client.post("/api/controls/bulk", json={
        "core": "Core", "keys": ["x"] * 2001, "enabled": True,
    })
    assert response.status == 400


# -- writing -------------------------------------------------------------

async def test_setting_a_value_reaches_the_core(client, hub):
    assert (await client.post("/api/controls/set", json={
        "core": "Core", "key": "Zone 1/mute", "value": True})).status == 200
    assert hub.cores["Core"].writes == [("value", "Zone 1", "mute", True)]


async def test_setting_by_position_is_sent_as_position(client, hub):
    await client.post("/api/controls/set", json={
        "core": "Core", "key": "Zone 1/gain", "position": 0.25})
    assert hub.cores["Core"].writes == [("position", "Zone 1", "gain", 0.25)]


async def test_a_read_only_control_cannot_be_written(client):
    response = await client.post("/api/controls/set", json={
        "core": "Core", "key": "Meter/meter.1", "value": 0})
    assert response.status == 400


async def test_writing_to_a_disconnected_core_is_unavailable(client, hub):
    hub.cores["Core"].logged_on = False
    response = await client.post("/api/controls/set", json={
        "core": "Core", "key": "Zone 1/mute", "value": True})
    assert response.status == 503


async def test_a_malformed_key_is_rejected(client):
    response = await client.post("/api/controls/set", json={
        "core": "Core", "key": "nokey", "value": 1})
    assert response.status == 400


# -- integration feed ----------------------------------------------------

async def test_the_feed_carries_only_exposed_controls(client, hub):
    hub.store.configure("Core", "Zone 1/gain", enabled=True, name="Bar",
                        platform="number", use_position=True)
    body = await (await client.get("/api/integration/controls")).json()
    assert len(body["controls"]) == 1
    entry = body["controls"][0]
    assert entry["name"] == "Bar"
    assert entry["platform"] == "number"
    assert entry["min"] == -50.0 and entry["max"] == -25.0
    assert entry["use_position"] is True
    assert entry["available"] is True


async def test_the_feed_names_anything_left_unnamed(client, hub):
    hub.store.configure("Core", "Zone 1/mute", enabled=True)
    body = await (await client.get("/api/integration/controls")).json()
    assert body["controls"][0]["name"] == "Zone 1 mute"


async def test_the_feed_reports_a_core_that_dropped(client, hub):
    hub.store.configure("Core", "Zone 1/gain", enabled=True)
    hub.cores["Core"].logged_on = False
    body = await (await client.get("/api/integration/controls")).json()
    assert body["controls"][0]["available"] is False


# -- stream and page -----------------------------------------------------

async def test_changes_arrive_on_the_event_stream(client, hub):
    response = await client.get("/api/events")
    assert response.headers["Content-Type"].startswith("text/event-stream")
    await asyncio.sleep(0.05)
    hub._on_change("Core", _control("Zone 2", "gain", value=-30.0))

    line = await asyncio.wait_for(response.content.readline(), timeout=5)
    while line.startswith(b":") or line == b"\n":
        line = await asyncio.wait_for(response.content.readline(), timeout=5)
    event = json.loads(line.decode().removeprefix("data: "))
    assert event["key"] == "Zone 2/gain" and event["value"] == -30.0
    response.close()


async def test_a_stalled_subscriber_is_dropped_not_blocking(hub):
    queue = hub.subscribe()
    for index in range(600):
        hub._on_change("Core", _control("Zone 1", f"c{index}"))
    assert queue not in hub._subscribers


async def test_any_non_api_path_serves_the_panel(client):
    """Ingress has been seen to forward a stray double slash."""
    for path in ("/", "//", "/anything"):
        assert (await client.get(path)).status in (200, 404)
    assert (await client.get("/api/nope")).status == 404


# -- areas and grouping --------------------------------------------------

async def test_areas_combine_home_assistant_s_with_those_in_use(client, hub, monkeypatch, tmp_path):
    import server

    registry = tmp_path / "core.area_registry"
    registry.write_text(json.dumps(
        {"data": {"areas": [{"name": "Lobby"}, {"name": "Bar"}]}}))
    monkeypatch.setattr(server, "AREA_REGISTRY", registry)

    hub.store.configure("Core", "Zone 1/gain", area="Function Room 3")
    body = await (await client.get("/api/areas")).json()
    assert body["areas"] == ["Bar", "Function Room 3", "Lobby"]
    assert body["in_use"] == ["Function Room 3"]


async def test_a_missing_area_registry_is_not_fatal(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "AREA_REGISTRY", Path("/nonexistent"))
    body = await (await client.get("/api/areas")).json()
    assert body["from_home_assistant"] == []


async def test_groups_show_the_devices_that_will_appear(client, hub):
    hub.store.configure("Core", "Zone 1/gain", group="Bar", area="Bar",
                        enabled=True)
    hub.store.configure("Core", "Zone 1/mute", group="Bar")
    body = await (await client.get("/api/groups?core=Core")).json()
    bar = next(g for g in body["groups"] if g["name"] == "Bar")
    assert bar["controls"] == 2 and bar["exposed"] == 1 and bar["area"] == "Bar"
    # The meter was never regrouped, so it stays under its component.
    assert any(g["name"] == "Meter" for g in body["groups"])


async def test_bulk_can_assign_an_area_to_a_selection(client, hub):
    response = await client.post("/api/controls/bulk", json={
        "core": "Core", "keys": ["Zone 1/gain", "Zone 1/mute"],
        "area": "Function Room 3", "group": "Function Room 3",
    })
    assert (await response.json())["applied"] == 2
    assert hub.store.controls["Core/Zone 1/mute"].config.area == "Function Room 3"


async def test_the_feed_carries_group_area_and_presentation(client, hub):
    hub.store.configure("Core", "Zone 1/gain", enabled=True, group="Bar",
                        area="Bar", icon="mdi:volume-high",
                        entity_category="config", precision=1)
    body = await (await client.get("/api/integration/controls")).json()
    entry = body["controls"][0]
    assert entry["group"] == "Bar" and entry["area"] == "Bar"
    assert entry["icon"] == "mdi:volume-high"
    assert entry["entity_category"] == "config"
    assert entry["precision"] == 1


async def test_a_control_with_no_group_reports_its_component(client, hub):
    hub.store.configure("Core", "Zone 1/gain", enabled=True)
    body = await (await client.get("/api/integration/controls")).json()
    assert body["controls"][0]["group"] == "Zone 1"


async def test_the_payload_carries_every_configured_field(client, hub):
    """Guards against the config block drifting from the dataclass again.

    A hand-written list of fields stopped including area and device when
    those were added, so the UI showed them blank however they were set.
    """
    from dataclasses import fields

    from control_store import ControlConfig

    body = await (await client.get("/api/controls")).json()
    carried = set(body["controls"][0]["config"])
    assert carried == {f.name for f in fields(ControlConfig)}


async def test_area_and_device_survive_the_round_trip(client, hub):
    await client.post("/api/controls/configure", json={
        "core": "Core", "key": "Zone 1/gain",
        "area": "Function Room 3", "group": "Function Room 3",
    })
    body = await (await client.get("/api/controls?component=Zone 1")).json()
    row = next(c for c in body["controls"] if c["key"] == "Zone 1/gain")
    assert row["config"]["area"] == "Function Room 3"
    assert row["config"]["group"] == "Function Room 3"
    assert row["group"] == "Function Room 3"


# -- forced rescan -------------------------------------------------------

async def test_rescan_rereads_the_core(client, hub, monkeypatch):
    called = []

    async def fake(name, conn):
        called.append(name)
        hub._on_change(name, _control("Zone 9", "gain"))

    monkeypatch.setattr(hub, "_rediscover", fake)
    body = await (await client.post("/api/rediscover", json={})).json()
    assert called == ["Core"]
    assert body["cores"]["Core"]["ok"] is True
    assert body["cores"]["Core"]["added"] == 1


async def test_rescan_reports_a_core_that_is_not_connected(client, hub):
    hub.cores["Core"].logged_on = False
    body = await (await client.post("/api/rediscover", json={})).json()
    assert body["cores"]["Core"]["ok"] is False


async def test_rescan_of_an_unknown_core_is_404(client):
    response = await client.post("/api/rediscover", json={"core": "nope"})
    assert response.status == 404


async def test_a_failing_rescan_is_reported_not_raised(client, hub, monkeypatch):
    async def boom(name, conn):
        raise OSError("core went away")

    monkeypatch.setattr(hub, "_rediscover", boom)
    body = await (await client.post("/api/rediscover", json={})).json()
    assert "went away" in body["cores"]["Core"]["error"]
