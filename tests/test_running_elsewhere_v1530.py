"""v1.530.0: the coding tools running elsewhere, watched from Chat › Code; and
Iron Jarvis's running work, seen from them.

1. A tool's saved conversation written to in the last ten minutes may still be
   running in that tool's own app or a terminal Iron Jarvis does not know, so
   Build › Code does not continue it unless the owner says it is closed there
   (two programs writing one conversation garble it). Driven through the real
   route with the REAL wall clock (``tests/conftest.py`` runs it an hour ahead
   for every other test).
2. Each Claude Code / Codex session in the list says whether it is working,
   how long it has been quiet and its newest step; the watch route gives its
   newest steps, found by id among the listed sessions.
3. ``work_search`` starts with what is running in Iron Jarvis, behind the
   client wall, and answers with only that when called with nothing.
4. Claude Code is a third app to connect, changed ONLY through its own
   ``claude mcp`` command (a stand-in here: a test never runs the real one).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import outside, reported, running
from tests._build_app_helpers_v1409 import client
from tests.test_build_app_review_v1409 import _write_claude, app, work  # noqa: F401 — fixtures by name

SID = "0e0e0e0e-1111-4222-8333-444444444444"
X1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INSTALL = "install-bearer-for-the-whole-app"


def _aged(path: Path, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


# --------------------------------------------------------------------------- #
# 1. never two writers on one conversation
# --------------------------------------------------------------------------- #
@pytest.fixture()
def real_clock(monkeypatch):
    monkeypatch.setattr(S, "wall_clock", time.time)


@pytest.mark.parametrize("ago,flag,status", [
    (30, None, 409),           # written 30 s ago: may still be open in Claude Code
    (30, "yes", 422),          # only a real true says it is closed there
    (30, True, 201),           # the owner said it is closed there
    (9 * 60, None, 409),       # still inside ten minutes
    (11 * 60, None, 201),      # quiet for longer: continued as before
])
async def test_a_conversation_written_a_moment_ago_is_not_continued_here_unasked(
        app, work, tmp_path, monkeypatch, real_clock, ago, flag, status):
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    _aged(_write_claude(home, work, SID), ago)
    body = {"harness": "claude-code", "folder": str(work), "harness_session_id": SID}
    if flag is not None:
        body["elsewhere_closed"] = flag
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json=body)
    assert r.status_code == status, r.json().get("detail")
    rows = [x for x in store.list_rows(app.state.platform.engine) if x.harness_session_id == SID]
    if status == 422:
        assert rows == []
    elif status == 409:
        detail = r.json()["detail"]
        assert "Claude Code wrote to this conversation" in detail and "may still be running there" in detail
        assert "It's closed there" in detail
        assert rows == []
    else:
        assert len(rows) == 1


def test_the_quiet_time_is_read_from_the_files_own_write_time():
    now = 1_800_000_000.0
    assert S.live_elsewhere_quiet({"mtime": now - 30}, now) == 30
    assert S.live_elsewhere_quiet({"mtime": now - 601}, now) is None
    assert S.live_elsewhere_quiet({}, now) is None
    assert S.live_elsewhere_quiet({"mtime": "x"}, now) is None
    assert S.ago_words(1) == "1 second" and S.ago_words(59) == "59 seconds"
    assert S.ago_words(60) == "1 minute" and S.ago_words(150) == "2 minutes"


# --------------------------------------------------------------------------- #
# 2. working, quiet, the newest step, the watch view
# --------------------------------------------------------------------------- #
@pytest.fixture()
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    monkeypatch.setattr(apps, "TEST_ROOT", str(tmp_path / "other-apps"))
    claude = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(outside, "READS_DISK", True)
    monkeypatch.setattr(outside, "wall_clock", time.time)
    temp = tmp_path / "pc-temp"
    temp.mkdir()
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(outside, "SCRATCH_MARKERS", ())
    outside.forget_cache()
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "Parser"
    folder.mkdir()

    class Scene:
        pass

    s = Scene()
    s.app, s.platform = app, app.state.platform
    s.client = TestClient(app, headers={"Authorization": f"Bearer {INSTALL}"})
    s.engine, s.tmp, s.folder, s.claude = s.platform.engine, tmp_path, folder, claude
    yield s
    outside.forget_cache()


def _claude_session(scene, sid: str, *, ago: float) -> Path:
    folder = str(scene.folder)
    lines = [
        {"type": "user", "sessionId": sid, "cwd": folder, "entrypoint": "cli", "timestamp": "2026-11-17T10:00:00Z",
         "message": {"role": "user", "content": "Fix the date column"}},
        {"type": "assistant", "sessionId": sid, "cwd": folder, "timestamp": "2026-11-17T10:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Running the tests first."},
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q tests/test_dates.py"}}]}},
        {"type": "user", "sessionId": sid, "cwd": folder, "timestamp": "2026-11-17T10:00:09Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "3 passed"}]}},
    ]
    path = scene.claude / "projects" / "slug" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    _aged(path, ago)
    return path


def test_each_session_says_whether_it_is_working_and_its_newest_step(scene):
    _claude_session(scene, SID, ago=5)
    _claude_session(scene, X1, ago=900)
    view = scene.client.get("/work-record/other-tools").json()
    chats = {c["id"]: c for t in view["tools"] if t["tool"] == "claude-code" for f in t["folders"] for c in f["chats"]}
    assert chats[SID]["working"] is True and chats[SID]["quiet_s"] < 60
    assert chats[SID]["doing"] == "Ran: pytest -q tests/test_dates.py"
    assert chats[X1]["working"] is False and chats[X1]["quiet_s"] >= 899


def test_the_watch_view_gives_the_newest_steps_in_plain_words(scene):
    _claude_session(scene, SID, ago=5)
    r = scene.client.get(f"/work-record/other-tools/claude-code/{SID}/steps")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["working"] is True and got["title"] == "Fix the date column"
    assert [s["words"] for s in got["steps"]] == [
        "You: Fix the date column",
        "Claude Code: Running the tests first.",
        "Ran: pytest -q tests/test_dates.py",
    ]


@pytest.mark.parametrize("path", [
    f"/work-record/other-tools/claude-code/{X1}/steps",            # not listed
    f"/work-record/other-tools/claude-chat/{SID}/steps",           # not a coding tool
    "/work-record/other-tools/claude-code/..%2F..%2Fsecrets/steps",  # never a path
])
def test_the_watch_view_finds_only_a_listed_session_by_its_id(scene, path):
    _claude_session(scene, SID, ago=5)
    assert scene.client.get(path).status_code == 404


def test_a_program_started_session_is_not_watched_either(scene):
    path = _claude_session(scene, SID, ago=5)
    text = path.read_text(encoding="utf-8").replace('"entrypoint": "cli"', '"entrypoint": "sdk-cli"')
    path.write_text(text, encoding="utf-8")
    _aged(path, 5)
    view = scene.client.get("/work-record/other-tools").json()
    assert not any(t["tool"] == "claude-code" for t in view["tools"])


# --------------------------------------------------------------------------- #
# 3. what runs in Iron Jarvis, seen from another app
# --------------------------------------------------------------------------- #
def test_running_now_tells_coding_work_and_keeps_a_clients_behind_the_wall(scene, monkeypatch):
    from iron_jarvis.workmap.clients import store as clients

    hartleys = scene.tmp / "Clients" / "Hartleys"
    hartleys.mkdir(parents=True)
    clients.create(scene.platform, folder=str(hartleys), name="Hartleys")
    rows = [
        {"where": "Chat › Code", "title": "Speed up the import", "folder": str(scene.folder), "waiting": False},
        {"where": "Build › Code (Codex)", "title": "Hartleys reconciliation", "folder": str(hartleys), "waiting": True},
        {"where": "Chat › Code", "title": "Tidy the tests", "folder": str(scene.folder), "waiting": True},
    ]
    monkeypatch.setattr(running, "_rows", lambda platform: rows)
    text = running.running_block(scene.platform)
    assert text.splitlines() == [
        running.HEAD,
        f"- Chat › Code: Speed up the import, in {scene.folder}",
        f"- Chat › Code: Tidy the tests, in {scene.folder} (waiting for the user)",
    ]
    monkeypatch.setattr(running, "_rows", lambda platform: [])
    assert running.running_block(scene.platform) == running.NOTHING


def test_a_build_code_session_running_now_is_told_with_its_tool(scene, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.hosted import news

    live = SimpleNamespace(folder=str(scene.folder))
    scene.platform.hosted = SimpleNamespace(live={"hs_1": live}, clock=time.monotonic)
    monkeypatch.setattr(news, "running_rows", lambda platform: [
        {"thread_id": "hs_1", "harness": "codex", "title": "Fix the parser", "waiting": False}])
    text = running.running_block(scene.platform)
    assert f"- Build › Code (Codex): Fix the parser, in {scene.folder}" in text


def test_work_search_with_nothing_answers_what_runs(scene, monkeypatch):
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.workmap.connector_tools import WorkSearchTool

    monkeypatch.setattr(running, "_rows", lambda platform: [
        {"where": "Chat › Code", "title": "Speed up the import", "folder": str(scene.folder), "waiting": False}])
    ctx = ToolContext(workspace=scene.tmp, session_id="mcp:app:codex", agent_run_id="",
                      config=scene.platform.config, event_bus=scene.platform.event_bus, engine=scene.engine)
    got = asyncio.run(WorkSearchTool(scene.platform).execute({}, ctx))
    assert got.ok is True
    assert running.HEAD in got.output and "Speed up the import" in got.output
    assert "nothing on record that matches" not in got.output


# --------------------------------------------------------------------------- #
# 4. Claude Code as a third app, through its own command
# --------------------------------------------------------------------------- #
class FakeClaude:
    """``claude mcp add/remove`` as Claude Code does it: into its own file."""

    def __init__(self, path: Path, fail: str = "") -> None:
        self.path, self.fail, self.calls = path, fail, []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.fail:
            return 1, self.fail
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        servers = data.setdefault("mcpServers", {})
        if argv[1:3] == ["mcp", "add"]:
            env = dict(a.removeprefix("--env=").split("=", 1) for a in argv if a.startswith("--env="))
            dash = argv.index("--")
            servers[argv[dash - 1]] = {"type": "stdio", "command": argv[dash + 1], "args": argv[dash + 2:], "env": env}
        elif argv[1:3] == ["mcp", "remove"]:
            servers.pop(argv[-1], None)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return 0, "done"


@pytest.fixture()
def cc(scene, monkeypatch):
    monkeypatch.setattr(apps, "claude_cli", lambda: ["claude"])
    apps.claude_code_config_path().parent.mkdir(parents=True, exist_ok=True)
    fake = FakeClaude(apps.claude_code_config_path())
    monkeypatch.setattr(apps, "run_cli", fake)
    apps._cc_cache.clear()
    return fake


def test_connect_claude_code_goes_through_its_own_command_and_never_passes_the_key(scene, cc):
    apps.claude_code_config_path().write_text(json.dumps({"projects": {"x": {}}, "numStartups": 3}), encoding="utf-8")
    r = scene.client.post("/work-record/other-apps/claude-code/connect")
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True
    key = scene.platform.app_connections.key_path("claude-code")
    token = key.read_text(encoding="utf-8")
    (add,) = cc.calls
    assert add[:5] == ["claude", "mcp", "add", "--scope", "user"]
    assert f"--env=IRONJARVIS_MCP_TOKEN_FILE={key}" in add and "iron-jarvis" in add
    assert not any(token in a for a in add)
    data = json.loads(apps.claude_code_config_path().read_text(encoding="utf-8"))
    assert data["numStartups"] == 3  # its own file, changed only by its own command
    got = {a["app"]: a for a in scene.client.get("/work-record/other-tools").json()["apps"]}
    assert got["claude-code"]["installed"] is True and got["claude-code"]["connected"] is True
    r = scene.client.post("/work-record/other-apps/claude-code/disconnect")
    assert r.status_code == 200 and cc.calls[-1] == ["claude", "mcp", "remove", "--scope", "user", "iron-jarvis"]
    assert not key.exists()


def test_claude_codes_own_iron_jarvis_entry_is_left_alone(scene, cc):
    apps.claude_code_config_path().write_text(
        json.dumps({"mcpServers": {"iron-jarvis": {"command": "theirs"}}}), encoding="utf-8")
    r = scene.client.post("/work-record/other-apps/claude-code/connect")
    assert r.status_code == 409 and "did not add" in r.json()["detail"]
    assert cc.calls == []
    assert not scene.platform.app_connections.key_path("claude-code").exists()


def test_a_command_claude_code_refuses_leaves_no_key(scene, monkeypatch):
    monkeypatch.setattr(apps, "claude_cli", lambda: ["claude"])
    monkeypatch.setattr(apps, "run_cli", FakeClaude(apps.claude_code_config_path(), fail="Error: invalid scope"))
    r = scene.client.post("/work-record/other-apps/claude-code/connect")
    assert r.status_code == 409 and "Error: invalid scope" in r.json()["detail"]
    assert not scene.platform.app_connections.key_path("claude-code").exists()


def test_claude_code_not_installed_is_not_offered(scene, monkeypatch):
    monkeypatch.setattr(apps, "claude_cli", lambda: None)
    got = {a["app"]: a for a in scene.client.get("/work-record/other-tools").json()["apps"]}
    assert got["claude-code"]["installed"] is False
    assert scene.client.post("/work-record/other-apps/claude-code/connect").status_code == 409


def test_a_test_never_runs_the_real_claude_program():
    assert apps.TEST_ROOT
    with pytest.raises(apps.AppConnectError):
        apps.run_cli(["claude", "mcp", "list"])


def test_a_claude_code_report_adds_its_summary_to_the_session_it_belongs_to(scene):
    from datetime import datetime, timezone

    _claude_session(scene, SID, ago=60)
    when = datetime(2026, 11, 17, 10, 5)  # during the session (its records' own times)
    reported.record(scene.engine, "claude-code", {"title": "Date fix", "folders": [str(scene.folder)],
                                                  "summary": "Fixed the date column."}, now=when)
    view = scene.client.get("/work-record/other-tools").json()
    (chat,) = [c for t in view["tools"] if t["tool"] == "claude-code" for f in t["folders"] for c in f["chats"]]
    assert chat["summary"] == "Fixed the date column."
    assert not any(t["tool"] == "claude-chat" for t in view["tools"])


@pytest.mark.parametrize("name,words", [
    ("mcp__Claude_Browser__browser_batch", "browser batch (Claude Browser)"),
    ("mcp__iron-jarvis__work_search", "work search (iron-jarvis)"),
    ("TodoWrite", "TodoWrite"),
    ("mcp__", "mcp__"),
])
def test_a_tools_name_is_said_as_a_person_reads_it(name, words):
    assert outside.tool_words(name) == words
