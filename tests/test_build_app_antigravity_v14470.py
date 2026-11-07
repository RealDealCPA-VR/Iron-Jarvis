"""v1.447.0 — Antigravity's full capabilities in Build › Code, each one MEASURED
on Google's agy 1.5.3 first (scratch ``USERPROFILE``/``HOME``, every model
call to a LOCAL stand-in Gemini API, no sign-in used; recordings in
``tests/fixtures/build_app_v14470``):

* the user's own agents (``agy agents``) are ``agent:<name>`` modes, started
  with ``--agent``, chosen once (a conversation keeps the agent it was opened
  with — measured) and refused in words after that;
* Iron Jarvis's own tools: started with ``--process-per-session`` when a box
  is ticked (only then does agy say ``mcpCapabilities.http``), so the chip is
  offered again; only such a start may say "takes none";
* its own MCP servers (``agy mcp list``: names and on/off, never the
  COMMAND/URL column) feed the Session tab;
* "Share my profile with Antigravity" writes ``~/.gemini/config/AGENTS.md``
  (agy reads it with the other three ~/.gemini rule files), by the v1.306.0
  rules.

The tests drive the real service with the fake harness process; nothing here
starts the real agy.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import antigravity_agents as AGA
from iron_jarvis.hosted import jarvis_tools as JT
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import tool_setup as TS
from iron_jarvis.profile import share
from iron_jarvis.settings import schema as SCH
from iron_jarvis.settings import writer as WR
from tests._build_app_helpers_v1409 import client, new_session, starts, stop_all, until
from tests.test_build_app_antigravity_v1439 import use_fake

FIX = Path(__file__).parent / "fixtures" / "build_app_v14470"


@pytest.fixture
def work(tmp_path) -> Path:
    w = tmp_path / "work"
    w.mkdir()
    return w


@pytest.fixture
def takes_reset():
    saved = dict(JT._TAKES)
    yield
    JT._TAKES.clear()
    JT._TAKES.update(saved)


# --------------------------------------------------------------------------- #
# The recordings
# --------------------------------------------------------------------------- #
def test_the_recordings_say_what_the_code_relies_on():
    tools = json.loads((FIX / "a_jarvis_tools.json").read_text(encoding="utf-8"))
    assert tools["agy"] == "1.5.3"
    assert "mcpCapabilities" not in tools["initialize_plain"]["agentCapabilities"]
    assert tools["initialize_process_per_session"]["agentCapabilities"]["mcpCapabilities"] == {"http": True}
    seen = [r["jsonrpc_method"] for r in tools["mcp_server_saw_process_per_session"]]
    assert "initialize" in seen and "tools/list" in seen and "tools/call" in seen
    assert all(r["bearer"] for r in tools["mcp_server_saw_process_per_session"] if r["method_http"] == "POST")
    assert tools["model_request_declares_tool"] is True and tools["tool_result_reached_model"] is True
    raw = (FIX / "a_jarvis_tools.json").read_text(encoding="utf-8")
    assert "SECRETPROBE" not in raw and "C:\\\\Users\\\\VR" not in raw, "the recording is masked"
    files = json.loads((FIX / "a_profile_files.json").read_text(encoding="utf-8"))
    assert "~/.gemini/config/AGENTS.md" in files["read"]


# --------------------------------------------------------------------------- #
# The user's own agents
# --------------------------------------------------------------------------- #
def test_agy_agents_output_is_read_as_names_only():
    rec = json.loads((FIX / "a_agents.json").read_text(encoding="utf-8"))
    assert AGA.parse_agents(rec["stdout"]) == ["hidden-one", "plain-one", "tax-reviewer"]
    # Anything that is not a plain name is never passed to --agent.
    assert AGA.parse_agents("ok-one\n--dangerously-skip-permissions\nhas space\n\nok-one\nx:y\n") == ["ok-one", "x:y"]


def test_agent_modes_are_listed_before_full_access_and_only_known_names_pass():
    AGA.LAST_AGENTS[:] = ["tax-reviewer"]
    rows = S.mode_rows("antigravity")
    ids = [r["id"] for r in rows]
    assert ids.index("agent:tax-reviewer") == ids.index("full") - 1
    row = next(r for r in rows if r["id"] == "agent:tax-reviewer")
    assert row["label"] == "tax-reviewer" and row["dangerous"] is False
    assert "keeps it for that conversation" in row["about"]
    assert S.mode_label("antigravity", "agent:tax-reviewer") == "tax-reviewer"
    assert S.check_mode("antigravity", "agent:tax-reviewer", False) == "agent:tax-reviewer"
    with pytest.raises(S.Refused) as err:
        S.check_mode("antigravity", "agent:no-such", False)
    assert "did not list an agent" in str(err.value)
    # OpenCode's agents never leak into Antigravity's menu (and back).
    assert not [r for r in S.mode_rows("opencode") if r["id"] == "agent:tax-reviewer"]


def test_the_driver_starts_an_agent_and_the_jarvis_tools_flag_only_when_handed_them():
    d = A.AntigravityDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="agent:tax-reviewer", cwd=".")
    argv = d.argv(session_id="", resume=False)
    assert argv[argv.index("--agent") + 1] == "tax-reviewer"
    assert A.PER_SESSION_FLAG not in argv
    d.mode = "own"
    assert "--agent" not in d.argv(session_id="", resume=False)
    d.mcp_servers = [{"type": "http", "name": JT.SERVER_NAME, "url": "http://x", "headers": []}]
    assert A.PER_SESSION_FLAG in d.argv(session_id="", resume=False)


async def test_an_agent_session_starts_with_its_agent_and_cannot_change_it_once_opened(tmp_path, work,
                                                                                       monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "a_reply", log)
    AGA.LAST_AGENTS[:] = ["tax-reviewer", "plain-one"]
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            sid = (await new_session(c, work, harness="antigravity", mode="agent:tax-reviewer"))["id"]
            # Before the conversation is opened the agent may still change.
            r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": "agent:plain-one"})
            assert r.status_code == 200, r.text
            r = await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn",
                                              json={"text": "hi", "turn_id": "t1"}), 30)
            assert r.status_code == 200, r.text
            argv = starts(log)[0]["argv"]
            assert argv[argv.index("--agent") + 1] == "plain-one"
            assert store.get(app.state.platform.engine, sid).harness_session_id
            for wanted in ("agent:tax-reviewer", "own", "edits"):
                r = await c.patch(f"/build/app/sessions/{sid}", json={"mode": wanted})
                assert r.status_code == 409, (wanted, r.text)
                assert "keeps the agent a conversation started with" in r.json()["detail"]
            assert store.get(app.state.platform.engine, sid).mode == "agent:plain-one"
            assert svc.live[sid].restart is False
    finally:
        await stop_all(app)


def _summaries_db(folder: Path, cid: str, agent: str, work: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / "conversation_summaries.db")
    con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, preview TEXT, "
                "step_count INT, last_modified_time TEXT, workspace_uris TEXT, nesting_depth INT, "
                "last_user_input_time TEXT, agent_name TEXT)")
    uris = json.dumps(["file:///" + str(work).replace("\\", "/")])
    con.execute("INSERT INTO conversation_summaries VALUES (?, 't', 'p', 2, '2026-11-07T00:00:00Z', ?, 0, '', ?)",
                (cid, uris, agent))
    con.commit()
    con.close()


async def test_a_saved_conversation_opens_only_under_the_agent_it_keeps(tmp_path, work, monkeypatch):
    agy_home = tmp_path / "agyhome"
    cid = "3b5fb863-b741-4be5-a844-2fd03e9cc5f2"
    _summaries_db(agy_home, cid, "tax-reviewer", work)
    monkeypatch.setenv(A.HOME_ENV, str(agy_home))
    assert A.conversation_agent(agy_home, cid) == "tax-reviewer"
    assert A.conversation_agent(agy_home, "not-a-uuid") is None
    use_fake(monkeypatch, "a_reply", tmp_path / "fake.log")
    AGA.LAST_AGENTS[:] = ["tax-reviewer", "plain-one"]
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(work),
                                                          "harness_session_id": cid, "mode": "agent:plain-one"})
            assert r.status_code == 400, r.text
            assert "\u201ctax-reviewer\u201d" in r.json()["detail"]
            r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(work),
                                                          "harness_session_id": cid, "mode": "agent:tax-reviewer"})
            assert r.status_code == 201, r.text
    finally:
        await stop_all(app)


def test_the_agents_read_runs_in_the_background_and_is_kept(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout=b"hidden-one\nplain-one\ntax-reviewer\n", stderr=b"")

    from iron_jarvis.core import procs

    monkeypatch.setattr(procs, "run", fake_run)
    monkeypatch.setattr(AGA, "AUTO_PROBE", True)
    path = tmp_path / "home" / "hosted" / "antigravity_agents.json"
    assert AGA.refresh_soon(path, tmp_path / "home", ["agy"], tmp_path / "gem") is True

    async def wait():
        await until(lambda: not AGA.reading(path), 10)

    asyncio.run(wait())
    assert calls and calls[0][-1] == "agents" and "--log-file" in calls[0]
    assert AGA.known_agents() == ["hidden-one", "plain-one", "tax-reviewer"]
    AGA.LAST_AGENTS.clear()
    AGA.load(path)
    assert AGA.known_agents() == ["hidden-one", "plain-one", "tax-reviewer"], "the file keeps them"
    assert AGA.refresh_soon(path, tmp_path / "home", ["agy"], tmp_path / "gem") is False, "fresh: not read again"
    monkeypatch.setattr(AGA, "AUTO_PROBE", False)
    assert AGA.refresh_soon(path.with_name("other.json"), tmp_path / "home", ["agy"], tmp_path / "gem") is False


# --------------------------------------------------------------------------- #
# Iron Jarvis's own tools
# --------------------------------------------------------------------------- #
async def test_a_ticked_box_restarts_antigravity_with_its_tools_and_the_flag(tmp_path, work, monkeypatch,
                                                                             takes_reset):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "a_reply", log)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            plain = (await new_session(c, work, harness="antigravity"))["id"]
            view = (await c.get(f"/build/app/sessions/{plain}")).json()["session"]
            assert "jarvis_tools" not in view, "the chip is offered for Antigravity again"
            r = await asyncio.wait_for(c.post(f"/build/app/sessions/{plain}/turn",
                                              json={"text": "hi", "turn_id": "t1"}), 30)
            assert r.status_code == 200, r.text
            assert A.PER_SESSION_FLAG not in starts(log)[0]["argv"], "no box ticked: no flag"
            assert JT.takes("antigravity") is True, "a plain start's silence says nothing"
            # A running session: ticking the first box needs a new process.
            r = await c.patch(f"/build/app/sessions/{plain}", json={"capabilities": {"memory": True}})
            assert r.status_code == 200, r.text
            assert r.json()["applied"]["capabilities"] == "next"
            assert svc.live[plain].restart is True
            # A session ticked before its first start starts WITH the flag.
            ticked = (await new_session(c, work, harness="antigravity"))["id"]
            r = await c.patch(f"/build/app/sessions/{ticked}", json={"capabilities": {"memory": True}})
            assert r.status_code == 200, r.text
            r = await asyncio.wait_for(c.post(f"/build/app/sessions/{ticked}/turn",
                                              json={"text": "hi", "turn_id": "t2"}), 30)
            assert r.status_code == 200, r.text
            argv = starts(log)[-1]["argv"]
            assert A.PER_SESSION_FLAG in argv
            # The recording's agy says no mcpCapabilities: a per-session start
            # that says so notes "takes none" (an older agy), and the chip hides.
            assert JT.takes("antigravity") is False
            view = (await c.get(f"/build/app/sessions/{ticked}")).json()["session"]
            assert view["jarvis_tools"] is False
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# Its own MCP servers
# --------------------------------------------------------------------------- #
def test_agy_mcp_list_is_read_as_names_and_on_off_only():
    rows = AGA.parse_mcp_list((FIX / "agy_mcp_list.txt").read_text(encoding="utf-8"))
    assert rows == [{"name": "api", "state": "on"}, {"name": "fs", "state": "on"}, {"name": "gone", "state": "off"}]
    assert AGA.parse_mcp_list((FIX / "agy_mcp_list_none.txt").read_text(encoding="utf-8")) == []
    assert AGA.parse_mcp_list("") is None
    assert AGA.parse_mcp_list("something else entirely\n") is None
    secret = ("NAME     TYPE   STATUS    COMMAND/URL\n"
              "a        http   enabled   https://h/mcp?key=SECRET1\n"
              "b        stdio  disabled  python --token SECRET2 enabled\n")
    got = AGA.parse_mcp_list(secret)
    assert got == [{"name": "a", "state": "on"}, {"name": "b", "state": "off"}]
    assert "SECRET" not in json.dumps(got)


def test_the_session_tab_says_antigravity_servers_are_turned_on_not_connected():
    events = [TS.setup_event([{"name": "api", "state": "on"}, {"name": "gone", "state": "off"}])]
    view = TS.view("antigravity", events)
    assert view["known"] is True
    words = {s["name"]: (s["state"], s["words"]) for s in view["servers"]}
    assert words["api"][0] == "on" and "not reported" in words["api"][1]
    assert words["gone"] == ("off", "Turned off")
    before = TS.view("antigravity", [])
    assert before["known"] is False and "next start" in before["note"]


def _wire_driver(read_mcp):
    out: list[dict] = []
    events: list[dict] = []

    async def write(obj):
        out.append(obj)
        method = obj.get("method")
        answers = {"initialize": {"agentCapabilities": {}},
                   "session/new": {"sessionId": "11111111-1111-1111-1111-111111111111", "configOptions": []}}
        if method in answers and "id" in obj:
            asyncio.get_running_loop().call_soon(
                d.on_message, {"jsonrpc": "2.0", "id": obj["id"], "result": answers[method]})

    d = A.AntigravityDriver(write=write, emit=events.append, ask=lambda a: None, on_session_id=lambda s: None,
                            mode="own", cwd=".")
    d.read_mcp = read_mcp
    return d, events


def test_the_driver_reads_its_own_mcp_servers_once_the_session_is_open():
    async def go():
        calls = []

        def read():
            calls.append(1)
            return [{"name": "api", "state": "on"}]

        d, events = _wire_driver(read)
        await asyncio.wait_for(d.open(session_id="", resume=False), 5)
        await until(lambda: [e for e in events if e.get("type") == "setup"], 5)
        setup = next(e for e in events if e.get("type") == "setup")
        assert setup["mcp_servers"] == [{"name": "api", "state": "on"}]
        assert calls == [1] and d.reported_mcp == [{"name": "api", "state": "on"}]
        d2, events2 = _wire_driver(lambda: None)  # not known: nothing said
        await asyncio.wait_for(d2.open(session_id="", resume=False), 5)
        await asyncio.sleep(0.2)
        assert not [e for e in events2 if e.get("type") == "setup"]

    asyncio.run(go())


def test_the_mcp_list_is_never_read_while_the_suite_turns_it_off(monkeypatch):
    from iron_jarvis.core import procs

    calls = []
    table = (FIX / "agy_mcp_list.txt").read_bytes()

    def spy(argv, **kw):
        calls.append((list(argv), kw))
        return SimpleNamespace(returncode=0, stdout=table, stderr=b"")

    monkeypatch.setattr(procs, "run", spy)
    assert AGA.AUTO_PROBE is False
    assert AGA.read_mcp_list(["agy"], {}, ".") is None and calls == []
    monkeypatch.setattr(AGA, "AUTO_PROBE", True)
    assert AGA.read_mcp_list(["agy"], {"X": "1"}, "C:/work") == [
        {"name": "api", "state": "on"}, {"name": "fs", "state": "on"}, {"name": "gone", "state": "off"}]
    argv, kw = calls[0]
    assert argv == ["agy", "mcp", "list"] and kw["cwd"] == "C:/work"
    assert kw["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true", "its updater never runs inside the Job"


# --------------------------------------------------------------------------- #
# Share my profile with Antigravity
# --------------------------------------------------------------------------- #
_REAL = Path(os.path.expanduser("~")) / ".gemini" / "config" / "AGENTS.md"


def _stat(p: Path):
    try:
        st = p.stat()
        return (st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return None


@pytest.fixture
def share_user(tmp_path, monkeypatch):
    before = _stat(_REAL)
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.setattr(share, "_cli_found", lambda spec: True)
    monkeypatch.setattr(share, "DEBOUNCE_S", 0.05)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield user
    assert _stat(_REAL) == before, "a test touched the user's real Antigravity file"


def test_antigravity_is_listed_off_by_default_with_its_setting_row():
    spec = share.CLIS["antigravity"]
    assert spec.files == ("AGENTS.md",) and spec.proxy_provider == "" and spec.binary == "agy"
    row = next(r for r in SCH.SETTINGS if r.key == "profile_share_antigravity")
    assert row.put is False and row.tier == "ask"
    assert WR._SHARE_KEYS["profile_share_antigravity"] == "antigravity"
    assert "profile_share_antigravity" in WR._HOOK_KEYS
    assert set(WR._SHARE_KEYS.values()) == set(share.CLIS)


def _outside(data: bytes) -> bytes:
    s = data.index(share.START)
    e = data.index(share.END) + len(share.END)
    e += 2 if data[e:e + 2] == b"\r\n" else 1 if data[e:e + 1] == b"\n" else 0
    return data[:s] + data[e:]


def test_antigravity_writes_its_config_agents_md_and_keeps_the_rest_byte_identical(share_user, tmp_path):
    with TestClient(create_app(str(tmp_path / "proj"))) as c:
        assert c.put("/profile", json={"values": {"about": "Dana Reyes, CPA."}}).status_code == 200
        home = share_user / ".gemini" / "config"
        home.mkdir(parents=True)
        mine = b"# My rules\r\nNo emoji.\r\n"
        (home / "AGENTS.md").write_bytes(mine)
        r = c.put("/profile/share", json={"cli": "antigravity", "on": True})
        assert r.status_code == 200, r.text
        row = next(x for x in r.json()["clis"] if x["cli"] == "antigravity")
        assert row["label"] == "Antigravity" and row["vendor"] == "Google"
        data = (home / "AGENTS.md").read_bytes()
        assert b"Dana Reyes, CPA." in data and _outside(data) == mine
        assert row["files"][0]["path"].endswith(os.path.join(".gemini", "config", "AGENTS.md"))
        assert not (share_user / ".gemini" / "GEMINI.md").exists(), "Gemini CLI's own file is never written"
        assert c.app.state.platform.config.profile_share_antigravity is True
        r = c.put("/profile/share", json={"cli": "antigravity", "on": False})
        assert r.status_code == 200
        assert (home / "AGENTS.md").read_bytes() == mine


def test_a_new_antigravity_file_goes_when_turned_off(share_user, tmp_path):
    with TestClient(create_app(str(tmp_path / "proj"))) as c:
        assert c.put("/profile", json={"values": {"about": "Dana Reyes."}}).status_code == 200
        assert c.put("/profile/share", json={"cli": "antigravity", "on": True}).status_code == 200
        f = share_user / ".gemini" / "config" / "AGENTS.md"
        assert b"Dana Reyes." in f.read_bytes()
        assert c.put("/profile/share", json={"cli": "antigravity", "on": False}).status_code == 200
        assert not f.exists(), "a file Iron Jarvis created and left empty is removed"
