"""v1.426.0 (Wave 6 lane B, E4): the real CLI's OWN setup is visible in
Build › Code — its MCP servers (with the CLI's own way to sign in to one that
waits for it), Codex's own `/` items (its skills and its compact) and Codex's
own compact.

Every scenario replays frames in the shapes MEASURED on the real CLIs with
scratch homes (``tests/fixtures/build_app_v1426``, each file's ``source``
says how): the real fake-harness plumbing, the real driver, service and
routes. Nothing here reads the user's real ``~/.claude`` or ``~/.codex``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import tool_setup as T
from iron_jarvis.hosted.events import EventLog
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, stop_all

FIX = Path(__file__).parent / "fixtures" / "build_app_v1426"


def _use(monkeypatch, scenario: str, log: Path) -> None:
    from iron_jarvis.hosted import service as S

    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / f"{scenario}.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _turn(c, sid: str, text: str, turn_id: str) -> list[dict]:
    r = await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn",
                                      json={"text": text, "turn_id": turn_id}), 30)
    assert r.status_code == 200, r.text
    return frames(r.text)


def _setup_events(app, sid: str) -> list[dict]:
    from iron_jarvis.hosted import service as S

    svc = S.service_for(app.state.platform)
    live = svc.live.get(sid)
    events, _t, _d = (live.log if live is not None else EventLog(svc.home, sid)).read(5000)
    return [e for e in events if e.get("type") == "setup"]


# --------------------------------------------------------------------------- #
# Claude Code: its init frame's mcp_servers
# --------------------------------------------------------------------------- #


async def test_claude_codes_own_mcp_servers_show_with_their_state_and_its_way_to_sign_in(
        app, work, tmp_path, monkeypatch):
    _use(monkeypatch, "claude_mcp", tmp_path / "fake.log")
    c = client(app)
    try:
        sid = (await new_session(c, work))["id"]
        evs = await _turn(c, sid, "first", "t1")
        assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
        await _turn(c, sid, "second", "t2")
        over = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
        setups = _setup_events(app, sid)
    finally:
        await c.aclose()
        await stop_all(app)
    mcp = over["mcp"]
    assert mcp["known"] is True and mcp["at"]
    rows = {r["name"]: r for r in mcp["servers"]}
    assert [r["name"] for r in mcp["servers"]] == ["notes", "broken-tool", "tracker"]
    assert (rows["notes"]["state"], rows["notes"]["words"]) == ("connected", "Connected")
    assert (rows["broken-tool"]["state"], rows["broken-tool"]["words"]) == ("failed", "Did not start")
    assert rows["tracker"]["state"] == "needs_sign_in"
    assert rows["tracker"]["words"] == "Needs you to sign in"
    # Claude Code's OWN way: /mcp in its own screen, Authenticate.
    assert "/mcp" in rows["tracker"]["sign_in"] and "Authenticate" in rows["tracker"]["sign_in"]
    assert "tracker" in rows["tracker"]["sign_in"] and "Open in terminal" in rows["tracker"]["sign_in"]
    assert "sign_in" not in rows["notes"] and "sign_in" not in rows["broken-tool"]
    # The second turn's init said the SAME list: nothing new was written.
    assert len(setups) == 1, setups


async def test_a_session_whose_tool_never_said_says_not_yet(app, work, tmp_path, monkeypatch):
    c = client(app)
    try:
        sid = (await new_session(c, work))["id"]
        claude = (await c.get(f"/build/app/sessions/{sid}/overview")).json()["mcp"]
        pi = (await c.get(f"/build/app/sessions/{(await new_session(c, work, harness='pi'))['id']}/overview")).json()["mcp"]
    finally:
        await c.aclose()
        await stop_all(app)
    assert claude == {"known": False, "servers": [], "note": T.NOT_YET.format(tool="Claude Code")}
    # v1.441.0 (moved on purpose): Pi says their state when /mcp is typed.
    assert pi == {"known": False, "servers": [],
                  "note": "Pi does not report its own MCP servers here. " + T.NOT_REPORTED_HOW["pi"]}


def test_an_init_frame_without_the_key_is_not_known_never_no_servers():
    assert T.claude_servers(None) is None
    assert T.claude_servers("x") is None
    assert T.claude_servers([]) == []
    got = T.claude_servers([{"name": "a", "status": "pending"}, {"name": "b", "status": "disabled"},
                            {"name": "c", "status": "something-new"}, {"status": "connected"}])
    assert [(r["name"], r["state"]) for r in got] == [("a", "starting"), ("b", "off"), ("c", "failed")]


def test_note_servers_writes_only_a_change_and_never_raises():
    said: list = []

    class D:
        mcp_servers = None
        emit = staticmethod(said.append)

    d = D()
    T.note_servers(d, None)  # not known: nothing
    assert said == []
    T.note_servers(d, [{"name": "a", "state": "connected"}])
    T.note_servers(d, [{"name": "a", "state": "connected"}])
    T.note_servers(d, [{"name": "a", "state": "failed"}])
    assert [e["mcp_servers"][0]["state"] for e in said] == ["connected", "failed"]

    class Broken:
        mcp_servers = None

        def emit(self, _e):
            raise RuntimeError("boom")

    T.note_servers(Broken(), [{"name": "a", "state": "connected"}])  # never raises


def test_a_secret_in_a_servers_error_is_masked_before_the_cut():
    key = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0" * 3
    got = T.codex_update(None, {"name": "x", "status": "failed", "error": f"bad key {key} " + "z" * 900})
    assert key not in json.dumps(got)
    assert len(got[0]["error"]) <= T.ERROR_CHARS


# --------------------------------------------------------------------------- #
# Codex: startup statuses, its skills in the `/` menu, its own compact
# --------------------------------------------------------------------------- #


async def test_codex_mcp_servers_skills_and_its_own_compact(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    _use(monkeypatch, "codex_setup", log)
    c = client(app)
    try:
        sid = (await new_session(c, work, harness="codex"))["id"]
        evs = await _turn(c, sid, "/compact", "t1")
        over = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
        cmds = (await c.get(f"/build/app/sessions/{sid}/commands")).json()["commands"]
        ctx = (await c.get(f"/build/app/sessions/{sid}/context")).json()
    finally:
        await c.aclose()
        await stop_all(app)
    msgs = sent(log)
    methods = [m.get("method") for m in msgs if "method" in m]
    # "/compact" became Codex's OWN compaction, never a message to the model.
    compact = [m for m in msgs if m.get("method") == "thread/compact/start"]
    assert len(compact) == 1 and compact[0]["params"]["threadId"]
    assert "turn/start" not in methods
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
    assert any(e["type"] == "notice" and "Codex compacted the conversation" in e["text"] for e in evs)
    # The skills were asked for THIS folder, once.
    skills = [m for m in msgs if m.get("method") == "skills/list"]
    assert len(skills) == 1 and skills[0]["params"] == {"cwds": [str(work)]}
    # Its servers, in its own words.
    rows = {r["name"]: r for r in over["mcp"]["servers"]}
    assert rows["notes"]["state"] == "connected"
    assert rows["broken-tool"]["state"] == "failed" and "failed to start" in rows["broken-tool"]["error"]
    assert rows["tracker"]["state"] == "needs_sign_in"
    assert "codex mcp login tracker" in rows["tracker"]["sign_in"]
    # The `/` menu: Codex's compact, then its ENABLED skills as `$name`.
    by = {c["name"]: c for c in cmds}
    assert by["compact"].get("prefix", "/") == "/"
    assert by["release-notes"]["prefix"] == "$" and by["imagegen"]["prefix"] == "$"
    assert "old-helper" not in by
    # The Context drop-down offers Codex's own compact, as a message.
    assert ctx["compact"] == {"how": "message", "text": "/compact"}


async def test_a_skills_list_that_never_answers_never_holds_the_session(monkeypatch):
    """The write happens in open(); the answer is taken in the background
    (the fake below never answers): open returns at once, the compact row
    stays, and the waiter is dropped when its wait ends."""
    wrote: list = []

    async def write(obj):
        wrote.append(obj)

    d = X.CodexDriver(write=write, emit=lambda e: None, ask=lambda a: None,
                      on_session_id=lambda s: None, mode="ask", cwd="C:\\w")
    monkeypatch.setattr(X, "SKILLS_WAIT_S", 0.05)
    await asyncio.wait_for(d._read_skills(), 1)
    assert wrote[-1]["method"] == "skills/list"
    assert d.commands == [T.CODEX_COMPACT_ROW]
    await asyncio.sleep(0.2)
    assert not d._bg and not d._replies._waiting
    assert d.commands == [T.CODEX_COMPACT_ROW]


async def test_a_turn_started_names_a_compaction_turn_for_stop():
    got: list = []

    async def write(obj):
        got.append(obj)

    d = X.CodexDriver(write=write, emit=lambda e: None, ask=lambda a: None,
                      on_session_id=lambda s: None, mode="ask")
    d.turn_open = True
    d._on_notification("turn/started", {"threadId": "th", "turn": {"id": "turn-9"}})
    assert d.turn_id == "turn-9"


def test_codex_update_reads_a_sign_in_from_its_reason_or_its_words():
    a = T.codex_update(None, {"name": "x", "status": "failed", "error": None,
                              "failureReason": "reauthenticationRequired"})
    assert a[0]["state"] == "needs_sign_in"
    b = T.codex_update(a, {"name": "y", "status": "starting"})
    c = T.codex_update(b, {"name": "x", "status": "ready"})
    assert [(r["name"], r["state"]) for r in c] == [("x", "connected"), ("y", "starting")]
    assert T.codex_update(c, {"status": "ready"}) is c  # no name: unchanged


def test_skill_rows_keep_enabled_safe_names_once():
    rows = T.codex_skill_rows({"data": [
        {"skills": [{"name": "a", "description": "A", "enabled": True},
                    {"name": "a", "description": "again"},
                    {"name": "off", "enabled": False},
                    {"name": "bad name", "description": "spaces"},
                    {"name": "b", "description": "line one\nline two"}]},
        {"skills": [{"name": "c"}]},
    ]})
    assert [(r["name"], r["prefix"]) for r in rows] == [("a", "$"), ("b", "$"), ("c", "$")]
    assert rows[1]["description"] == "line one"
    assert T.codex_skill_rows(None) == [] and T.codex_skill_rows({"data": "x"}) == []


async def test_codex_compact_is_listed_before_its_first_start(app, work, monkeypatch):
    """Never a start just to ask: before Codex has run here, its `/` menu
    still offers its own compact (its skills come with its first start)."""
    from iron_jarvis.hosted import claude as CL

    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    c = client(app)
    try:
        sid = (await new_session(c, work, harness="codex"))["id"]
        cmds = (await c.get(f"/build/app/sessions/{sid}/commands")).json()["commands"]
    finally:
        await c.aclose()
        await stop_all(app)
    assert cmds == [T.CODEX_COMPACT_ROW]
