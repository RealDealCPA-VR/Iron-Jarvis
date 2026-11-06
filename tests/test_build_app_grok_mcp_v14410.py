"""v1.441.0 (lane g4): Grok's OWN MCP servers in the Session tab.

Grok 1.0.70 pushes ``_x.ai/mcp/servers_updated`` (each turned-on server's
CONFIG: its URL with any key in it, its command and arguments) and then
``_x.ai/mcp/server_status`` per server (``status`` ready | unavailable,
``reason``, and a ``detail`` that repeats the URL) — measured on a scratch
GROK_HOME (``tests/fixtures/build_app_v14410/grok_mcp_notifications.json``).
Only each server's NAME, status and reason are ever read; the URL, the key,
the command, its arguments and Grok's detail never reach an event, the log,
the Session tab or the model.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import tool_setup as TS
from iron_jarvis.hosted.grok import GrokDriver

from ._build_app_helpers_v1409 import FAKE, client, frames, new_session, stop_all

FIX = Path(__file__).parent / "fixtures"
NOTES = json.loads((FIX / "build_app_v14410" / "grok_mcp_notifications.json").read_text(encoding="utf-8"))["frames"]
SECRETS = ("FAKE-KEY-123", "FAKE-ARG-TOKEN", "127.0.0.1:9", "tinymcp", "handshake failed: error")


def _fold(frames_: list[dict]) -> list[dict] | None:
    rows = None
    for f in frames_:
        m = f["method"]
        if m.endswith("servers_updated"):
            rows = TS.grok_servers(rows, f["params"])
        elif m.endswith("server_status"):
            rows = TS.grok_status(rows, f["params"])
    return rows


def test_the_recorded_notifications_fold_to_names_and_states_only():
    rows = _fold(NOTES)
    assert rows == [
        {"name": "deadhttp", "state": "failed", "error": "it did not answer when Grok connected"},
        {"name": "okserver", "state": "connected"},
    ]
    assert not any(s in json.dumps(rows) for s in SECRETS)


def test_a_server_waiting_for_a_sign_in_and_one_grok_stops_listing():
    rows = TS.grok_servers(None, {"mcpServers": [{"name": "linear", "url": "https://x/?k=1"}, {"name": "gone"}]})
    assert [r["state"] for r in rows] == ["starting", "starting"]
    rows = TS.grok_status(rows, {"name": "linear", "status": "unavailable", "reason": "auth_required"})
    assert rows[0] == {"name": "linear", "state": "needs_sign_in"}
    # A later list keeps what is known and drops what Grok no longer lists.
    rows = TS.grok_servers(rows, {"mcpServers": [{"name": "linear"}]})
    assert rows == [{"name": "linear", "state": "needs_sign_in"}]
    # Not a list: not known (never "no servers").
    assert TS.grok_servers(None, {"mcpServers": "x"}) is None


def test_the_session_tab_lists_grok_and_says_how_to_sign_in():
    view = TS.view("grok", [TS.setup_event([{"name": "linear", "state": "needs_sign_in"}])])
    assert view["known"] is True
    assert view["servers"][0]["words"] == "Needs you to sign in"
    assert "/mcps" in view["servers"][0]["sign_in"] and "linear" in view["servers"][0]["sign_in"]
    assert TS.view("grok", [])["note"] == TS.NOT_YET.format(tool="Grok")


def test_the_driver_keeps_its_reported_list_apart_from_the_servers_iron_jarvis_hands_it():
    said: list[dict] = []

    async def _write(obj):
        return None

    d = GrokDriver(write=_write, emit=said.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.session_id = "SID"
    d.mcp_servers = [{"type": "http", "name": "iron-jarvis-code", "url": "http://127.0.0.1:8787/mcp", "headers": []}]
    for f in NOTES:
        d.on_extension(f["method"], f["params"])
    assert d.mcp_servers[0]["name"] == "iron-jarvis-code", "Iron Jarvis's own tools are left alone"
    assert d.reported_mcp == _fold(NOTES)
    setups = [e for e in said if e.get("type") == "setup"]
    assert len(setups) == 3 and setups[-1]["mcp_servers"] == _fold(NOTES)
    assert not any(s in json.dumps(said) for s in SECRETS)
    # Another session's status is not this one's.
    d.on_extension("_x.ai/mcp/server_status", {"sessionId": "OTHER", "name": "okserver", "status": "unavailable"})
    assert d.reported_mcp == _fold(NOTES)


async def test_through_a_running_grok_the_tab_shows_them_and_nothing_secret_is_kept(tmp_path, monkeypatch):
    base = json.loads((FIX / "build_app_v1416" / "g_read.json").read_text(encoding="utf-8"))
    steps = base["steps"]
    at = next(i for i, s in enumerate(steps) if s.get("reply_to") == "session/new")
    sid_value = steps[at]["out"]["result"]["sessionId"]
    notes = json.loads(json.dumps(NOTES).replace('"SID"', json.dumps(sid_value)))
    steps[at + 1:at + 1] = [{"out": f} for f in notes]
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"harness": "grok", "steps": steps}), encoding="utf-8")
    log = tmp_path / "fake.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    work = tmp_path / "work"
    work.mkdir()
    (work / "hello.txt").write_text("apple pie\n", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            sid = (await new_session(c, work, harness="grok", mode="ask", model="grok-4.5", effort="low"))["id"]
            r = await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn",
                                              json={"text": "do it", "turn_id": "t"}), 30)
            evs = frames(r.text)
            got = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
            view = (await c.get(f"/build/app/sessions/{sid}")).json()
    finally:
        await stop_all(app)
    mcp = got["mcp"]
    assert mcp["known"] is True
    assert [(s["name"], s["state"]) for s in mcp["servers"]] == [("deadhttp", "failed"), ("okserver", "connected")]
    kept = (tmp_path / "home").rglob("events.jsonl")
    blob = json.dumps(evs) + json.dumps(view) + json.dumps(got) + "".join(p.read_text(encoding="utf-8") for p in kept)
    assert not any(s in blob for s in SECRETS), "no URL, key, command or detail is kept or shown"
