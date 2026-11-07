"""v1.447.1: Build › Code catches up again — each fact MEASURED on the real
CLI (scratch homes, no model call; recordings in
``tests/fixtures/build_app_v14471/``), each pin driving the REAL driver,
service and routes with scripted peers or the fake harness process — never a
real Claude Code, Codex or Grok.

* Claude Code 2.1.319: ``/model sonnet[1m]`` keeps the ``[1m]``; Sign in… for
  an HTTP MCP server through its own ``mcp_authenticate``; its new
  ``session_writes_lost`` said in words; the folder's ``/`` list read in the
  background (``hosted.claude_catalog``) when none is known.
* Codex 0.157.0: Reload MCP servers (``config/mcpServer/reload`` then
  ``mcpServerStatus/list``), and a server its config turns off listed as off.
* Grok 1.0.70: servers its config turns off listed as off (``_x.ai/mcp/list``),
  names only.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import claude_catalog as CCAT
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import session_controls as SC
from iron_jarvis.hosted import tool_setup as TS
from iron_jarvis.hosted import typed as T
from iron_jarvis.hosted.driver import ControlRefused
from iron_jarvis.hosted.grok import GrokDriver
from iron_jarvis.providers import claude_models as CM
from tests._build_app_helpers_v1409 import client, frames, new_session, stop_all, use_fake
from tests.test_build_app_daemon_parity_v14410 import LIVE_PICKER  # noqa: F401 — the 2.1.316 picker
from tests.test_build_app_daemon_parity_v14410 import live_catalog  # noqa: F401 — a fixture
from tests.test_session_controls_v14410 import Peer, codex_peer

FIX = Path(__file__).parent / "fixtures" / "build_app_v14471"
AUTH = json.loads((FIX / "claude_mcp_authenticate.json").read_text(encoding="utf-8"))
RELOAD = json.loads((FIX / "codex_mcp_reload.json").read_text(encoding="utf-8"))
GLIST = json.loads((FIX / "grok_mcp_list.json").read_text(encoding="utf-8"))


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


@pytest.fixture
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


def _rows(cat) -> list[dict]:
    return [{"id": "", "label": "Claude Code's default"}] + [{"id": m["id"], "label": m["label"]} for m in cat["models"]]


# --------------------------------------------------------------------------- #
# 3. /model sonnet[1m] keeps the [1m]
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("typed, want", [
    ("sonnet[1m]", "claude-sonnet-5-5[1m]"),
    ("Sonnet[1M]", "claude-sonnet-5-5[1m]"),
    ("opus[1m]", "claude-opus-5-5[1m]"),
    ("sonnet", "claude-sonnet-5-5"),
    # Haiku 4.5's window is 200K: not given one; the words go to Claude Code.
    ("haiku[1m]", None),
])
def test_a_1m_model_keeps_its_1m(live_catalog, typed, want):  # noqa: F811
    assert T.resolve_claude_model(typed, _rows(live_catalog)) == want


async def test_typed_model_sonnet_1m_reaches_the_chip_and_the_next_start(app, work, tmp_path, monkeypatch,
                                                                        live_catalog):  # noqa: F811
    monkeypatch.setattr(CM, "model_efforts", lambda model: None)
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        # (the recording goes on after a mode change, as the v14410 pin drives it)
        await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "edits"})
        r = await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                          json={"text": "/model sonnet[1m]", "turn_id": "t2"}), 15)
        assert r.status_code == 200, r.text
        assert frames(r.text)[-1]["stop"] == "completed"
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
        assert row["model"] == "claude-sonnet-5-5[1m]"
    await stop_all(app)
    d = C.ClaudeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                       mode="ask", model="claude-sonnet-5-5[1m]")
    argv = d.argv(session_id="s", resume=False)
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5[1m]"


# --------------------------------------------------------------------------- #
# 2. Claude Code's own mcp_authenticate
# --------------------------------------------------------------------------- #
def _resp(frame: dict) -> dict:
    return frame["response"].get("response") or {}


def claude_auth_peer(statuses: list[str], auth: dict | None = None, error: str = ""):
    """``mcp_authenticate`` answers the recorded shape; each ``mcp_status``
    answers the next of ``statuses`` (the server's state as Claude Code says)."""
    left = list(statuses)

    def answer(msg: dict[str, Any]) -> Any:
        if msg.get("type") != "control_request":
            return None
        rid, req = msg["request_id"], msg["request"]
        if req["subtype"] == "mcp_authenticate":
            if error:
                return {"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": error}}
            body = auth if auth is not None else _resp(AUTH["mcp_authenticate"])
        elif req["subtype"] == "mcp_status":
            st = left.pop(0) if len(left) > 1 else left[0]
            body = {"mcpServers": [{"name": "oauthy", "status": st, "source": "dynamic"}]}
        else:
            body = {}
        return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": body}}
    return answer


async def test_claude_sign_in_hands_its_own_page_to_the_browser_and_watches_the_list(monkeypatch):
    monkeypatch.setattr(C, "SIGN_IN_WATCH_S", 0.01)
    p = Peer(C.ClaudeDriver, claude_auth_peer(["needs-auth", "needs-auth", "connected"]))
    TS.note_servers(p.driver, [{"name": "oauthy", "state": TS.NEEDS_SIGN_IN}])
    out = await p.driver.mcp_action("oauthy", "sign_in")
    assert out == {"url": _resp(AUTH["mcp_authenticate"])["authUrl"]}
    assert p.requests()[0] == {"subtype": "mcp_authenticate", "serverName": "oauthy"}
    for _ in range(300):
        if p.driver.mcp_servers and p.driver.mcp_servers[0]["state"] == TS.CONNECTED:
            break
        await asyncio.sleep(0.01)
    assert p.driver.mcp_servers == [{"name": "oauthy", "state": TS.CONNECTED, "source": "dynamic"}]
    # Claude Code says nothing when the browser comes back (measured): the list
    # is read again until the server is no longer waiting, then no more.
    n = len([r for r in p.requests() if r["subtype"] == "mcp_status"])
    assert n == 3
    await asyncio.sleep(0.1)
    assert len([r for r in p.requests() if r["subtype"] == "mcp_status"]) == n


async def test_claude_sign_in_that_needs_nothing_says_so_and_an_unknown_server_is_refused(monkeypatch):
    monkeypatch.setattr(C, "SIGN_IN_WATCH_S", 0.01)
    p = Peer(C.ClaudeDriver, claude_auth_peer(["connected"], auth={"requiresUserAction": False,
                                                                    "callbackExpected": False}))
    out = await p.driver.mcp_action("oauthy", "sign_in")
    assert out == {"words": "Claude Code says oauthy needs no sign-in now."}
    p2 = Peer(C.ClaudeDriver, claude_auth_peer(["needs-auth"], error="Server not found: nope"))
    with pytest.raises(ControlRefused, match="could not start signing in to nope: Server not found: nope"):
        await p2.driver.mcp_action("nope", "sign_in")


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///C:/x", "http://u:p@host/a", ""])
async def test_claude_sign_in_never_hands_over_an_unsafe_address(bad):
    p = Peer(C.ClaudeDriver, claude_auth_peer(["needs-auth"], auth={"authUrl": bad, "requiresUserAction": True}))
    with pytest.raises(ControlRefused, match="sign-in page"):
        await p.driver.mcp_action("oauthy", "sign_in")


async def test_the_overview_offers_sign_in_only_to_a_claude_server_waiting_for_one():
    d = C.ClaudeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.harness = "claude-code"
    live = SimpleNamespace(running=True, driver=d)
    view = {"mcp": {"known": True, "servers": [{"name": "oauthy", "state": TS.NEEDS_SIGN_IN},
                                               {"name": "echo", "state": TS.CONNECTED}]}}
    out = await SC.decorate_overview(view, live)
    acts = {r["name"]: r.get("actions") for r in out["mcp"]["servers"]}
    assert acts == {"oauthy": ["off", "sign_in"], "echo": ["reconnect", "off"]}
    assert "reload" not in out["mcp"], "Claude Code has no whole-list reload"


async def test_sign_in_words_the_tool_said_replace_the_waiting_words(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_full_mode_edit_v1418", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        live = svc.live[s["id"]]
        TS.note_servers(live.driver, [{"name": "oauthy", "state": TS.NEEDS_SIGN_IN}])

        async def signed(server, action):
            return {"words": "Claude Code says oauthy needs no sign-in now."}

        monkeypatch.setattr(live.driver, "mcp_action", signed)
        r = await c.post(f"/build/app/sessions/{s['id']}/mcp/oauthy", json={"action": "sign_in"})
        assert r.status_code == 200, r.text
        assert r.json()["words"] == "Claude Code says oauthy needs no sign-in now."
        assert "url" not in r.json()
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 1. new in claude 2.1.319: session_writes_lost
# --------------------------------------------------------------------------- #
def test_claude_says_when_the_disk_was_full():
    ev = AUTH["new_in_2_1_319"]["session_writes_lost"]
    assert C.system_notice(ev) == ("Claude Code could not save part of this conversation because the disk is "
                                   "full. Free up space before the next message.")
    quota = dict(ev, error_code="EDQUOT", lost=["global_config"])
    assert "its settings because your disk quota is used up" in C.system_notice(quota)


# --------------------------------------------------------------------------- #
# 4. the folder's `/` list read in the background
# --------------------------------------------------------------------------- #
FAKE_INIT = r'''
import json, os, sys
line = sys.stdin.readline()
with open(os.environ["IJ_FAKE_INIT_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "in": json.loads(line),
                         "traffic": os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")}) + "\n")
req = json.loads(line)
cmds = [{"name": "compact", "description": "Free up context", "argumentHint": "<x>", "builtin": True},
        {"name": "projcmd", "description": "a project command (project)", "argumentHint": ""}]
print(json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": req["request_id"],
                  "response": {"commands": cmds}}}), flush=True)
'''


@pytest.fixture
def fake_init(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude_init.py"
    script.write_text(FAKE_INIT, encoding="utf-8")
    log = tmp_path / "init.log"
    monkeypatch.setenv("IJ_FAKE_INIT_LOG", str(log))
    monkeypatch.setattr(CCAT, "AUTO_PROBE", True)
    monkeypatch.setattr(CCAT, "_FAILED_AT", {})
    monkeypatch.setattr(C, "LAST_COMMANDS", {})
    return [sys.executable, str(script)], log


def _wait(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_a_folders_list_is_read_once_in_the_background_with_no_hooks_and_no_mcp(tmp_path, fake_init, work):
    argv, log = fake_init
    assert CCAT.refresh_soon(tmp_path / "home", lambda: argv, str(work)) is True
    assert _wait(lambda: C.LAST_COMMANDS.get(C.commands_key(C.HARNESS, str(work))))
    assert [r["name"] for r in C.LAST_COMMANDS[C.commands_key(C.HARNESS, str(work))]] == ["compact", "projcmd"]
    assert [r["name"] for r in C.LAST_COMMANDS[C.HARNESS]] == ["compact"], "the bare key keeps the built-ins"
    (row,) = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert row["in"]["request"] == {"subtype": "initialize"}
    assert "--strict-mcp-config" in row["argv"] and "-p" in row["argv"]
    settings = Path(row["argv"][row["argv"].index("--settings") + 1])
    assert json.loads(settings.read_text(encoding="utf-8")) == {"disableAllHooks": True}
    assert Path(row["cwd"]).resolve() == work.resolve()
    assert row["traffic"] == "1"
    assert _wait(lambda: not CCAT.reading(str(work)))
    # Known now: no second read.
    assert CCAT.refresh_soon(tmp_path / "home", lambda: argv, str(work)) is False


def test_a_sessions_own_list_wins_over_the_background_read(tmp_path, fake_init, work):
    argv, _log = fake_init
    key = C.commands_key(C.HARNESS, str(work))
    got = CCAT.probe(argv, CCAT.probe_env(), work, CCAT.settings_file(tmp_path / "home"))
    C.LAST_COMMANDS[key] = [{"name": "mine", "description": "", "argument_hint": ""}]
    C.keep_commands(str(work), got, only_if_unknown=True)
    assert [r["name"] for r in C.LAST_COMMANDS[key]] == ["mine"]


def test_a_read_claude_code_did_not_answer_waits_before_the_next(tmp_path, fake_init, work, monkeypatch):
    argv, _log = fake_init
    assert CCAT.refresh_soon(tmp_path / "home", lambda: argv + ["--never-mind"], str(work / "gone")) is True
    assert _wait(lambda: not CCAT.reading(str(work / "gone")))
    assert CCAT.refresh_soon(tmp_path / "home", lambda: argv, str(work / "gone")) is False


def test_the_off_switch_reads_nothing(tmp_path, fake_init, work, monkeypatch):
    argv, log = fake_init
    monkeypatch.setattr(CCAT, "AUTO_PROBE", False)
    assert CCAT.refresh_soon(tmp_path / "home", lambda: argv, str(work)) is False
    time.sleep(0.2)
    assert not log.exists()


async def test_the_service_starts_the_read_for_a_claude_session_folder(app, work, tmp_path, fake_init, monkeypatch):
    argv, _log = fake_init
    monkeypatch.setattr(S, "binary_for", lambda harness: argv)
    svc = S.service_for(app.state.platform)
    assert svc.commands_for(S.CLAUDE, None, folder=str(work)) == []
    assert await asyncio.to_thread(_wait, lambda: C.LAST_COMMANDS.get(C.commands_key(C.HARNESS, str(work))))
    rows = svc.commands_for(S.CLAUDE, None, folder=str(work))
    assert "projcmd" in [r["name"] for r in rows]
    # Another tool never starts it.
    before = dict(C.LAST_COMMANDS)
    svc.commands_for(S.CODEX, None, folder=str(tmp_path))
    assert not CCAT.reading(str(tmp_path)) and C.LAST_COMMANDS == before


# --------------------------------------------------------------------------- #
# 5. Codex: Reload MCP servers, and its turned-off servers listed
# --------------------------------------------------------------------------- #
def test_codex_status_list_folds_names_and_states_only():
    rows = [{"name": "tiny", "state": TS.CONNECTED}, {"name": "tiny2", "state": TS.FAILED, "error": "x"}]
    after = TS.codex_listed(rows, RELOAD["status_list_after_taking_out"]["result"], replace=True)
    assert after == [{"name": "tiny", "state": TS.CONNECTED}], "a server taken out of the config is gone"
    added = TS.codex_listed(rows, RELOAD["status_list_after_adding"]["result"], replace=False)
    assert added == rows + [{"name": "tinyoff", "state": TS.OFF}], "only the unknown, turned-off one is added"
    full = TS.codex_listed(rows, RELOAD["status_list_after_adding"]["result"], replace=True)
    assert full == [{"name": "tiny", "state": TS.CONNECTED}, {"name": "tiny2", "state": TS.CONNECTED},
                    {"name": "tinyoff", "state": TS.OFF}]
    assert TS.codex_listed(rows, {"nope": 1}, replace=True) == rows
    signin = {"data": [{"name": "a", "runtimeStatus": "failed", "authStatus": "notLoggedIn"},
                       {"name": "b", "runtimeStatus": "authenticationRequired"}]}
    assert [r["state"] for r in TS.codex_listed(None, signin, replace=True)] == [TS.NEEDS_SIGN_IN, TS.NEEDS_SIGN_IN]


async def test_codex_reload_sends_null_params_then_reads_the_whole_list():
    p = Peer(X.CodexDriver, codex_peer({
        "config/mcpServer/reload": {},
        "mcpServerStatus/list": RELOAD["status_list_after_adding"]["result"],
    }))
    p.driver.session_id = "thr1"
    TS.note_servers(p.driver, [{"name": "tiny", "state": TS.CONNECTED}, {"name": "gone", "state": TS.CONNECTED}])
    await p.driver.reload_mcp()
    raw = [m for m in p.sent if m.get("method") == "config/mcpServer/reload"]
    assert raw and raw[0]["params"] is None, "the schema's params is null"
    lst = [m for m in p.sent if m.get("method") == "mcpServerStatus/list"]
    assert lst and lst[0]["params"] == {"threadId": "thr1", "detail": "toolsAndAuthOnly"}
    assert p.driver.mcp_servers == [{"name": "tiny", "state": TS.CONNECTED}, {"name": "tiny2", "state": TS.CONNECTED},
                                    {"name": "tinyoff", "state": TS.OFF}]
    # Never while a turn runs (the newest request must stay the turn's).
    p.driver.turn_open = True
    n = len(p.sent)
    with pytest.raises(ControlRefused, match="Wait for Codex's reply"):
        await p.driver.reload_mcp()
    assert len(p.sent) == n


async def test_codex_lists_a_turned_off_server_at_start_without_touching_the_rest():
    p = Peer(X.CodexDriver, codex_peer({"mcpServerStatus/list": RELOAD["status_list_after_adding"]["result"]}))
    p.driver.session_id = "thr1"
    TS.note_servers(p.driver, [{"name": "tiny", "state": TS.STARTING}])
    await p.driver._read_mcp_list()
    for _ in range(200):
        if any(r["name"] == "tinyoff" for r in p.driver.mcp_servers or []):
            break
        await asyncio.sleep(0.01)
    names = {r["name"]: r["state"] for r in p.driver.mcp_servers}
    assert names["tinyoff"] == TS.OFF and names["tiny"] == TS.STARTING, "a known server keeps its own state"


class _Svc:
    """Just what ``session_controls.mcp_reload`` reads of the service."""

    def __init__(self, harness: str, driver: Any) -> None:
        self.row = SimpleNamespace(id="s1", harness=harness)
        self.live = {"s1": SimpleNamespace(running=True, driver=driver, turn=None)}
        self.said: list[dict] = []

    async def _row(self, sid):
        return self.row

    def _emit(self, live, ev):
        self.said.append(ev)


async def test_the_reload_is_said_and_offered_only_by_a_tool_that_has_it():
    p = Peer(X.CodexDriver, codex_peer({"config/mcpServer/reload": {},
                                        "mcpServerStatus/list": {"data": []}}))
    p.driver.session_id = "thr1"
    svc = _Svc("codex", p.driver)
    out = await SC.mcp_reload(svc, "s1")
    assert out["reloaded"] is True and "read its MCP settings again" in out["words"]
    assert svc.said[-1]["text"] == out["words"]
    view = await SC.decorate_overview({"mcp": {"known": True, "servers": []}}, svc.live["s1"])
    assert view["mcp"]["reload"] is True
    d = C.ClaudeDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    with pytest.raises(S.Refused) as r:
        await SC.mcp_reload(_Svc("claude-code", d), "s1")
    assert r.value.status == 400
    p.driver.turn_open = True
    with pytest.raises(S.Refused) as r:
        await SC.mcp_reload(svc, "s1")
    assert r.value.status == 409


async def test_the_reload_route_through_a_running_codex(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "codex_signin", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="codex")
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        live = svc.live[s["id"]]

        async def reload_mcp():
            TS.note_servers(live.driver, [{"name": "tiny", "state": TS.CONNECTED}])

        monkeypatch.setattr(live.driver, "reload_mcp", reload_mcp)
        TS.note_servers(live.driver, [{"name": "gone", "state": TS.CONNECTED}])
        over = (await c.get(f"/build/app/sessions/{s['id']}/overview")).json()
        assert over["mcp"].get("reload") is True
        r = await c.post(f"/build/app/sessions/{s['id']}/mcp-reload")
        assert r.status_code == 200, r.text
        over = (await c.get(f"/build/app/sessions/{s['id']}/overview")).json()
        assert [x["name"] for x in over["mcp"]["servers"]] == ["tiny"]
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 6. Grok: servers its config turns off
# --------------------------------------------------------------------------- #
SECRETS = ("FAKE-KEY-9", "ARG-SECRET-1", "127.0.0.1:9", "tinymcp", "python.exe")


def test_grok_list_reads_names_only_and_adds_the_ones_it_did_not_push():
    names = TS.grok_listed(GLIST["answer"]["result"])
    assert names == ["tinyon", "listoff", "webby", "tinyoff"]
    pushed = TS.grok_servers(None, GLIST["servers_updated_same_start"]["params"])
    rows = TS.grok_with_off(pushed, names)
    assert rows == [{"name": "tinyon", "state": TS.STARTING}, {"name": "webby", "state": TS.STARTING},
                    {"name": "listoff", "state": TS.OFF}, {"name": "tinyoff", "state": TS.OFF}]
    assert not any(s in json.dumps(rows) for s in SECRETS)
    assert TS.grok_listed({"result": "x"}) is None
    assert TS.grok_with_off(None, names) is None, "nothing pushed yet: not known"


async def test_grok_driver_asks_its_own_list_once_and_keeps_the_off_ones_after_later_pushes():
    def answer(msg: dict[str, Any]) -> Any:
        if msg.get("method") == "_x.ai/mcp/list":
            return {"jsonrpc": "2.0", "id": msg["id"], "result": GLIST["answer"]["result"]}
        return None

    p = Peer(GrokDriver, answer)
    p.driver.session_id = "SID"
    # Grok's first push of its list is what asks for the whole list.
    p.driver.on_extension("_x.ai/mcp/servers_updated", GLIST["servers_updated_same_start"]["params"])
    for _ in range(200):
        if p.driver.reported_mcp and len(p.driver.reported_mcp) == 4:
            break
        await asyncio.sleep(0.01)
    assert {r["name"]: r["state"] for r in p.driver.reported_mcp} == {
        "tinyon": TS.STARTING, "webby": TS.STARTING, "listoff": TS.OFF, "tinyoff": TS.OFF}
    reqs = [m for m in p.sent if m.get("method") == "_x.ai/mcp/list"]
    assert len(reqs) == 1 and reqs[0]["params"] == {"sessionId": "SID"}
    # A later push (Grok restating its turned-on servers) keeps the off ones.
    p.driver.on_extension("_x.ai/mcp/servers_updated", GLIST["servers_updated_same_start"]["params"])
    p.driver.on_extension("_x.ai/mcp/server_status", {"sessionId": "SID", "name": "tinyon", "status": "ready"})
    assert {r["name"]: r["state"] for r in p.driver.reported_mcp}["listoff"] == TS.OFF
    assert {r["name"]: r["state"] for r in p.driver.reported_mcp}["tinyon"] == TS.CONNECTED
    await asyncio.sleep(0.05)
    assert len([m for m in p.sent if m.get("method") == "_x.ai/mcp/list"]) == 1, "asked once per process"
    setups = [e for e in p.events if e.get("type") == "setup"]
    assert setups and not any(s in json.dumps(setups) for s in SECRETS)
