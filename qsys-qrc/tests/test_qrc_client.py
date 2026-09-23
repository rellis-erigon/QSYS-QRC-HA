"""Tests for the QRC client.

The control fixtures are the real shapes a Core returns — captured from a
live design — rather than what the documentation implies.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qrc_client import (  # noqa: E402
    Control, QrcConnection, QrcError, _unit_from, suggest_platform,
)

GAIN = {
    "Name": "gain", "Type": "Float", "Value": -25.0,
    "ValueMin": -50.0, "ValueMax": -25.0,
    "StringMin": "-50.0dB", "StringMax": "-25.0dB", "String": "-25.0dB",
    "Position": 1.0, "Direction": "Read/Write",
}
MUTE = {
    "Name": "mute", "Type": "Boolean", "Value": True, "String": "muted",
    "Position": 1.0, "Direction": "Read/Write",
}
METER = {
    "Name": "meter.1", "Type": "Float", "Value": -120.0,
    "ValueMin": -120.0, "ValueMax": 20.0, "StringMin": "-120dB",
    "StringMax": "20.0dB", "String": "-120dB", "Position": 0.0,
    "Direction": "Read Only",
}
LABEL = {
    "Name": "input.1.label", "Type": "String", "Value": None,
    "String": "1", "Direction": "Read/Write",
}


def conn() -> QrcConnection:
    return QrcConnection("192.0.2.10", name="Core")


# -- units ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("-50.0dB", "dB"), ("1.00s", "s"), ("10.0ms", "ms"), ("0dB", "dB"),
    ("85%", "%"), ("muted", ""), ("normal", ""), ("", ""), ("1", ""),
])
def test_units_come_from_the_string_the_core_already_formats(text, expected):
    assert _unit_from(text) == expected


def test_a_control_prefers_the_range_string_for_its_unit():
    """String can read "0dB" while the range says dB more reliably."""
    control = Control.from_json("Zone 1", GAIN)
    assert control.unit == "dB"


# -- platform suggestions ------------------------------------------------

def test_a_writable_float_is_a_number_and_a_read_only_one_is_a_sensor():
    assert suggest_platform(Control.from_json("Zone 1", GAIN)) == "number"
    assert suggest_platform(Control.from_json("Meter", METER)) == "sensor"


def test_a_writable_boolean_is_a_switch():
    assert suggest_platform(Control.from_json("Zone 1", MUTE)) == "switch"


def test_setup_controls_are_not_offered():
    """Invert polarity does not belong beside a volume slider."""
    for name in ("invert", "bypass", "pre.post"):
        control = Control("Zone 1", name, type="Boolean")
        assert suggest_platform(control) is None


def test_labels_are_skipped():
    assert suggest_platform(Control.from_json("Mixer", LABEL)) is None


def test_an_unknown_type_is_left_for_a_person():
    assert suggest_platform(Control("X", "y", type="Trigger")) is None


# -- framing -------------------------------------------------------------

def test_messages_are_split_on_null_not_newline():
    c = conn()
    frame = b'{"id":1,"result":true}\x00{"id":2,"result":false}\x00'
    assert [m["id"] for m in c.feed(frame)] == [1, 2]


def test_a_split_frame_is_held_until_it_completes():
    c = conn()
    assert c.feed(b'{"id":1,"res') == []
    assert [m["id"] for m in c.feed(b'ult":true}\x00')] == [1]


def test_an_unparseable_frame_does_not_lose_the_next_one():
    c = conn()
    messages = c.feed(b'not json\x00{"id":7,"result":1}\x00')
    assert [m["id"] for m in messages] == [7]


def test_empty_keepalive_frames_are_ignored():
    c = conn()
    assert c.feed(b'\x00\x00{"id":3,"result":1}\x00')[0]["id"] == 3


# -- dispatch ------------------------------------------------------------

async def test_a_reply_resolves_the_waiter_for_its_id():
    c = conn()
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    c._pending[5] = future
    c.dispatch({"id": 5, "result": "yes"})
    assert (await future)["result"] == "yes"


async def test_an_unsolicited_frame_is_not_mistaken_for_a_reply():
    """The Core pushes status and change frames nobody asked for."""
    c = conn()
    future = asyncio.get_running_loop().create_future()
    c._pending[5] = future
    c.dispatch({"id": 99, "result": "someone else's"})
    assert not future.done()


def test_a_change_updates_the_control_and_fires_the_hook():
    c = conn()
    c.controls["Zone 1/gain"] = Control.from_json("Zone 1", GAIN)
    seen = []
    c._on_change = seen.append

    c.dispatch({"method": "ChangeGroup.Poll", "params": {"Changes": [
        {"Component": "Zone 1", "Name": "gain", "Value": -30.0,
         "String": "-30.0dB", "Position": 0.8},
    ]}})

    control = c.controls["Zone 1/gain"]
    assert control.value == -30.0
    assert control.string == "-30.0dB"
    assert control.updates == 1
    assert seen == [control]


def test_a_control_appearing_mid_session_is_recorded_not_dropped():
    """A redeployed design can introduce controls we never discovered."""
    c = conn()
    c.dispatch({"method": "ChangeGroup.Poll", "params": {"Changes": [
        {"Component": "New", "Name": "gain", "Value": 1.0},
    ]}})
    assert c.controls["New/gain"].value == 1.0


def test_a_change_with_no_name_is_ignored():
    c = conn()
    c.dispatch({"method": "ChangeGroup.Poll",
                "params": {"Changes": [{"Component": "X"}]}})
    assert c.controls == {}


# -- control model -------------------------------------------------------

def test_read_only_controls_are_not_writable():
    assert not Control.from_json("Meter", METER).writable
    assert Control.from_json("Zone 1", GAIN).writable


def test_the_key_identifies_a_control_within_a_core():
    assert Control.from_json("Zone 1", GAIN).key == "Zone 1/gain"


def test_to_dict_carries_the_range_the_core_reported():
    data = Control.from_json("Zone 1", GAIN).to_dict()
    assert data["min"] == -50.0 and data["max"] == -25.0
    assert data["unit"] == "dB"
    assert data["writable"] is True


# -- protocol shapes -----------------------------------------------------

class _Writer:
    def __init__(self) -> None:
        self.sent = b""

    def write(self, data: bytes) -> None:
        self.sent += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


async def test_requests_are_null_terminated_json_rpc():
    c = conn()
    c._writer = _Writer()
    task = asyncio.create_task(c.call("StatusGet", 0, timeout=1))
    await asyncio.sleep(0)

    assert c._writer.sent.endswith(b"\x00")
    payload = json.loads(c._writer.sent[:-1])
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "StatusGet"
    assert payload["params"] == 0

    c.dispatch({"id": payload["id"], "result": {"Platform": "Core 110f"}})
    assert (await task)["Platform"] == "Core 110f"


async def test_an_error_reply_raises_rather_than_returning_it():
    c = conn()
    c._writer = _Writer()
    task = asyncio.create_task(c.call("Logon", {}, timeout=1))
    await asyncio.sleep(0)
    request_id = json.loads(c._writer.sent[:-1])["id"]
    c.dispatch({"id": request_id, "error": {"code": 10, "message": "Bad login"}})

    with pytest.raises(QrcError, match="Bad login"):
        await task


async def test_setting_a_control_sends_component_set():
    c = conn()
    c._writer = _Writer()
    task = asyncio.create_task(c.set_control("Zone 1", "mute", True))
    await asyncio.sleep(0)
    payload = json.loads(c._writer.sent[:-1])
    assert payload["method"] == "Component.Set"
    assert payload["params"]["Name"] == "Zone 1"
    assert payload["params"]["Controls"] == [{"Name": "mute", "Value": True}]
    c.dispatch({"id": payload["id"], "result": True})
    await task


async def test_position_is_sent_as_position_not_value():
    """A fader's dB scale is not linear; a slider must move in position."""
    c = conn()
    c._writer = _Writer()
    task = asyncio.create_task(c.set_position("Zone 1", "gain", 0.5))
    await asyncio.sleep(0)
    payload = json.loads(c._writer.sent[:-1])
    assert payload["params"]["Controls"] == [{"Name": "gain", "Position": 0.5}]
    c.dispatch({"id": payload["id"], "result": True})
    await task


async def test_close_cancels_anything_still_waiting():
    c = conn()
    c._writer = _Writer()
    future = asyncio.get_running_loop().create_future()
    c._pending[1] = future
    await c.close()
    assert future.cancelled()
    assert not c.connected
