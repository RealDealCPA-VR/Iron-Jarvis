"""v1.369.0 (IJ harness plan Phase 6): a Build session waiting for the user,
told by the desktop app while the window is closed to the tray.

The daemon lists a Build session parked on an approval as a chat row with
`surface: "build"` (daemon/durable_turns.waiting_unwatched). Before this
release the tray's toast read "A chat is waiting for your OK" and its click
opened /chat?thread=<id>, where a Build session never shows. These run the
REAL main.js functions under node (lifted, like the v1.351.0 chat pins).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_chat_lanes_v13510 import _CLICK_HARNESS, _HOUR, _PLAN_HARNESS
from tests.test_desktop_reliability_v1249 import _lift, _run, requires_node


def _plan(steps: list, tmp_path: Path) -> list:
    script = _PLAN_HARNESS.replace(
        "__LIFTED__", _lift("const ASK_WATCH_MS", "function planAskNotifications")
    )
    return _run(script, tmp_path, "plan1369", json.dumps({"steps": steps}))["out"]


def _click(items: list, tmp_path: Path) -> list:
    script = _CLICK_HARNESS.replace(
        "__LIFTED__", _lift("function showAskNotification", "function showAskNotification")
    )
    return _run(script, tmp_path, "click1369", json.dumps(items))["loaded"]


BUILD = {"id": "chat:bs_7", "kind": "chat", "thread_id": "bs_7", "tool": "shell", "surface": "build"}


@requires_node
def test_a_waiting_build_session_is_said_as_the_session(tmp_path):
    out = _plan([{"approvals": [BUILD], "now": 0}, {"approvals": [BUILD], "now": 25_000}], tmp_path)
    first = out[0]["notify"]
    assert len(first) == 1
    assert first[0]["title"] == "A Build session is waiting for your OK"
    assert first[0]["body"].endswith("click to open the session.")
    assert "chat" not in first[0]["title"].lower()
    assert first[0]["build"] is True and first[0]["threadId"] == "bs_7"
    assert out[1]["notify"] == [], "it re-announced the same session on the next tick"


@requires_node
def test_its_reminder_says_the_session_too(tmp_path):
    out = _plan([{"approvals": [BUILD], "now": 0}, {"approvals": [BUILD], "now": _HOUR + 1}], tmp_path)
    later = out[1]["notify"][0]
    assert later["title"] == "A Build session is still waiting for your OK"
    assert later["body"].endswith("waiting 1 hour — click to open the session.")


@requires_node
def test_an_open_window_leaves_it_to_the_bell(tmp_path):
    out = _plan([{"approvals": [BUILD], "now": 0, "windowOpen": True}], tmp_path)
    assert out[0]["notify"] == [] and out[0]["waiting"] == 1


@requires_node
def test_a_plain_chat_row_keeps_its_own_words(tmp_path):
    chat = {k: v for k, v in BUILD.items() if k != "surface"}
    out = _plan([{"approvals": [chat], "now": 0}], tmp_path)
    assert out[0]["notify"][0]["title"] == "A chat is waiting for your OK"
    assert "build" not in out[0]["notify"][0]


@requires_node
def test_the_toast_click_opens_build_on_that_session(tmp_path):
    loaded = _click(
        [
            {"title": "t", "body": "b", "chat": True, "build": True, "threadId": "bs 7", "sessionId": ""},
            {"title": "t", "body": "b", "chat": True, "build": True, "threadId": "", "sessionId": ""},
            {"title": "t", "body": "b", "chat": True, "threadId": "th1", "sessionId": ""},
        ],
        tmp_path,
    )
    assert loaded == [
        "http://127.0.0.1:8788/terminals?session=bs%207",
        "http://127.0.0.1:8788/terminals",
        "http://127.0.0.1:8788/chat?thread=th1",
    ]
