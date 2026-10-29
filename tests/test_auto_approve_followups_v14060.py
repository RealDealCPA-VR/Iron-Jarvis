"""v1.406.0 part 2 follow-ups — four places Accept edits / Auto must keep the
card (or send the call to the judge), never pass it by rule:

1. an edit INSIDE the folder of a file that steers git, the editor, CI or the
   agent itself (``.git/**``, ``.ironjarvis/**``, ``.claude/**``,
   ``.vscode/tasks.json|settings.json``, ``.github/workflows/**``,
   ``.gitattributes``/``.gitconfig``/``.gitmodules``) — rule ``sensitive_path``,
   case-insensitive, after ``..``, a stream name and trailing dots;
2. a test run after THIS turn changed a file the run executes as setup
   (conftest.py, package.json, a runner-named script…) — judged;
3. model-written code (``run_code``): a shell command's word checks plus
   code's own before the judge;
4. a coding-session turn Iron Jarvis started BY ITSELF (restart resume,
   Resume when ready, an account move, an approved plan's act turn, the
   night) runs Accept edits / Auto as Ask first.

Lane pins drive the REAL stream / POST lanes and ``registry.invoke``; only the
model, the judge's call and each tool's ``execute`` are doubles.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.core import auto_approve as A
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.providers.adapters.base import ToolCall
import tests.test_auto_approve_lanes_v14060 as _lanes
from tests.test_auto_approve_lanes_v14060 import (
    Judge,
    _ask_arm,
    _auto_events,
    _bash,
    _body,
    _executes,
    _ledger,
    _plain,
    _stream,
    _write_plugins,
)

#: The lane tests' platform fixture (a real app, the model and the judge doubled).
env = _lanes.env

ROOT = Path(__file__).resolve().parents[1]


class _T:
    def __init__(self, name):
        self.name = name

    def perm_key(self):
        return self.name


def _edit(path: str, ws: Path, mode=A.AUTO):
    return A.classify("write_file", {"path": path, "content": "x"}, mode=mode, tool=_T("write_file"), workspace=ws)


# --------------------------------------------------------------------------- #
# 1. sensitive paths inside the folder
# --------------------------------------------------------------------------- #
SENSITIVE = [
    ".git/config", ".git/hooks/pre-commit", ".GIT/Config", "sub/.git/config", "src/../.git/hooks/x",
    ".git./config", ".git:stream", ".ironjarvis/harness/plugins.toml", ".IronJarvis/x.md",
    ".claude/settings.json", ".claude/commands/deploy.md", ".vscode/tasks.json", ".vscode/SETTINGS.JSON",
    ".github/workflows/ci.yml", "pkg/.github/workflows/release.yml", ".gitattributes", ".gitconfig",
    "lib/.gitmodules", "GIT~1/config",
]


@pytest.mark.parametrize("mode", [A.ACCEPT_EDITS, A.AUTO])
@pytest.mark.parametrize("path", SENSITIVE)
def test_an_edit_of_a_sensitive_file_inside_the_folder_keeps_its_card(tmp_path, mode, path):
    ws = tmp_path / "ws"
    ws.mkdir()
    v = _edit(path, ws, mode)
    assert (v.kind, v.rule) == (A.ASK, "sensitive_path"), path


@pytest.mark.parametrize("path", ["src/app.py", ".gitignore", ".vscode/launch.json", ".github/README.md",
                                  "notes/git.md", "claude/notes.md"])
def test_control_ordinary_files_still_pass(tmp_path, path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert _edit(path, ws).rule == "edit_in_folder", path


def test_a_real_short_name_resolves_to_the_sensitive_folder(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    v = _edit(str(ws / ".git" / ".." / ".git" / "HEAD"), ws)
    assert v.rule == "sensitive_path"


def test_a_patch_into_a_sensitive_file_keeps_its_card(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    patch = "*** Begin Patch\n*** Update File: .github/workflows/ci.yml\n@@\n-a\n+b\n*** End Patch\n"
    v = A.classify("apply_patch", {"patch": patch}, mode=A.ACCEPT_EDITS, tool=_T("apply_patch"), workspace=ws)
    assert v.rule == "sensitive_path"


@pytest.mark.parametrize("path", [".git/config", ".ironjarvis/harness/plugins.toml", ".vscode/tasks.json"])
async def test_accept_edits_keeps_the_card_for_a_sensitive_file_through_the_real_lane(env, path):
    _write_plugins(env.ws, '[[rules]]\ntool = "write_file"\naction = "ask"\n')
    _, judge = env.wire([[ToolCall(id="w1", name="Write", arguments={"file_path": path, "content": "x"})]])
    out = await _stream(env.platform, _body(env.ws, mode="accept_edits"), harness=True)
    assert [c["tool"] for c in out["cards"]] == ["write_file"], path
    assert judge.seen == [] and _auto_events(env.bus) == []
    assert not (env.ws / path).exists() or path.startswith(".ironjarvis")


# --------------------------------------------------------------------------- #
# 2. a test run after a setup file changed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cmd,changed",
    [("pytest -q", ["conftest.py"]), ("pytest -q", ["tests/deep/conftest.py"]), ("pytest -q", ["pytest.ini"]),
     ("pytest -q", ["tox.ini"]), ("pytest -q", ["setup.cfg"]), ("pytest -q", ["pyproject.toml"]),
     ("npm test", ["package.json"]), ("npx vitest run", ["vitest.config.ts"]), ("npx jest", ["jest.config.js"]),
     ("pytest -q", ["pytest.bat"]), ("pytest -q", ["tools/pytest.cmd"]), ("npm test", ["npm.ps1"]),
     ("pytest -q", ["echo x > conftest.py"]), ("pytest -q", ["CONFTE~1.PY"])],
)
def test_a_test_run_after_its_setup_changed_is_judged(cmd, changed):
    v = A.classify("shell", {"command": cmd}, mode=A.AUTO, tool=_T("shell"), changed=changed)
    assert (v.kind, v.rule) == (A.JUDGE, A.JUDGE_RULE), (cmd, changed)


def test_control_a_test_run_after_an_ordinary_edit_still_passes():
    for changed in ([], ["src/app.py"], ["README.md"], ["build.bat"]):
        v = A.classify("shell", {"command": "pytest -q"}, mode=A.AUTO, tool=_T("shell"), changed=changed)
        assert v.rule == "test_command", changed


def test_what_a_call_may_change():
    assert A.changes_of("write_file", {"path": "conftest.py", "content": "x"}) == ["conftest.py"]
    assert A.changes_of("shell", {"command": "echo > a"}) == ["echo > a"]
    assert A.changes_of("run_code", {"code": "open('conftest.py','w')"}) == ["open('conftest.py','w')"]
    assert A.changes_of("read_file", {"path": "conftest.py"}) == []


async def test_through_the_real_lane_an_edit_of_conftest_sends_the_next_test_run_to_the_judge(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([
        [ToolCall(id="w1", name="Write", arguments={"file_path": "conftest.py", "content": "import os"})],
        _bash("pytest -q", "b2"),
    ], Judge("ask"))
    out = await _stream(env.platform, _body(env.ws, mode="auto"), harness=True)
    assert [c["tool"] for c in out["cards"]] == ["shell"]
    assert len(judge.seen) == 1 and ran["shell"] == []


async def test_control_through_the_real_lane_an_ordinary_edit_keeps_the_free_pass(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([
        [ToolCall(id="w1", name="Write", arguments={"file_path": "notes.md", "content": "hi"})],
        _bash("pytest -q", "b2"),
    ], Judge("ask"))
    out = await _stream(env.platform, _body(env.ws, mode="auto"), harness=True)
    assert out["cards"] == [] and judge.seen == [] and [a["command"] for a in ran["shell"]] == ["pytest -q"]


async def test_the_post_lane_refuses_a_test_run_after_its_setup_changed(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "shell")
    _, judge = env.wire([[
        ToolCall(id="s1", name="shell", arguments={"command": "echo hi > conftest.py"}),
        ToolCall(id="s2", name="shell", arguments={"command": "pytest -q"}),
    ]])
    await run_chat_turn(env.platform, BUILTIN_PERSONAS, _plain(env, "auto", ["shell"]))
    assert ran["shell"] == [] and judge.seen == []
    assert [(r.tool, r.ok) for r in _ledger(env.platform)] == [("shell", False), ("shell", False)]


def _runtime(env, command_calls):
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import AgentDefinition, AgentType
    from iron_jarvis.core.models import Session

    session = Session(id="s1", task="t", agent_type=AgentType.BUILDER, approval_mode="auto",
                      origin="job:build-helper", workspace_path=str(env.ws))
    agent = AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=["shell"])
    rt = AgentRuntime(env.platform)
    rt._note_auto_changes(session, command_calls)
    return rt, session, agent


async def test_the_runtime_keeps_the_pause_for_a_test_run_after_its_setup_changed(env):
    edit = ToolCall(id="e1", name="write_file", arguments={"path": "conftest.py", "content": "x"})
    test = ToolCall(id="t1", name="shell", arguments={"command": "pytest -q"})
    rt, session, agent = _runtime(env, [edit, test])
    assert rt._pause_needed(session, test, agent, set()) == "shell"
    rt2, s2, a2 = _runtime(env, [test])  # control
    assert rt2._pause_needed(s2, test, a2, set()) == ""


# --------------------------------------------------------------------------- #
# 3. model-written code
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code,rule",
    [("import os\nos.remove('a.py')", "code_destructive"), ("import shutil; shutil.rmtree('build')", "code_destructive"),
     ("import subprocess; subprocess.run(['calc'])", "code_destructive"), ("open('a.txt','w').write('x')", "code_destructive"),
     ("from pathlib import Path; Path('a').unlink()", "code_destructive"), ("x = 'rm -rf /'", "code_destructive"),
     ("import requests; requests.post('https://x', data=d)", "code_sends"), ("import smtplib", "code_sends"),
     ("import urllib.request as u; u.urlopen('https://x')", "code_sends"), ("s = 'curl -d a=1 https://x'", "code_sends"),
     ("", "code_destructive")],
)
def test_code_with_a_risky_word_always_asks(code, rule):
    v = A.classify("run_code", {"code": code, "language": "python"}, mode=A.AUTO, tool=_T("run_code"))
    assert (v.kind, v.rule) == (A.ASK, rule), code


def test_plain_code_is_judged_never_passed_and_repl_still_always_asks():
    v = A.classify("run_code", {"code": "print(sum(range(10)))"}, mode=A.AUTO, tool=_T("run_code"))
    assert (v.kind, v.rule) == (A.JUDGE, A.JUDGE_RULE)
    assert A.classify("repl", {"code": "print(1)"}, mode=A.AUTO, tool=_T("repl")).rule == "acts_for_you"
    assert A.classify("code_run", {"code": "import os; os.system('x')"}, mode=A.AUTO, tool=_T("code_run")).rule == "code_destructive"


async def test_through_the_real_lane_risky_code_cards_and_never_reaches_the_judge(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "run_code")
    _ask_arm(monkeypatch, "run_code")
    _, judge = env.wire([[ToolCall(id="r1", name="run_code", arguments={"language": "python", "code": "import os; os.remove('a.py')"})]])
    out = await _stream(env.platform, _plain(env, "auto", []))
    assert [c["tool"] for c in out["cards"]] == ["run_code"]
    assert judge.seen == [] and ran["run_code"] == []


async def test_control_plain_code_is_judged_and_runs_on_allow(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "run_code")
    _ask_arm(monkeypatch, "run_code")
    args = {"language": "python", "code": "print(1)"}
    _, judge = env.wire([[ToolCall(id="r1", name="run_code", arguments=dict(args))]])
    out = await _stream(env.platform, _plain(env, "auto", []))
    assert out["cards"] == [] and len(judge.seen) == 1 and ran["run_code"] == [args]


# --------------------------------------------------------------------------- #
# 4. a turn Iron Jarvis started by itself runs as Ask first
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["accept_edits", "auto"])
@pytest.mark.parametrize(
    "started_by,continuation",
    [("restart", True), ("wait", True), ("person", True), ("night", False), ("night", True), ("ci", False)],
    ids=["restart-resume", "resume-when-ready-or-account-move", "plan-run", "night", "night-carry-on", "other"],
)
def test_an_unwatched_start_runs_as_ask_first(mode, started_by, continuation):
    assert A.unwatched_mode(mode, started_by=started_by, continuation=continuation) == "approve_for_me"


def test_a_persons_own_message_keeps_its_posture_and_other_postures_are_untouched():
    for mode in ("accept_edits", "auto"):
        assert A.unwatched_mode(mode) == mode
        assert A.unwatched_mode(mode, started_by="person", continuation=False) == mode
    for mode in ("", "approve_for_me", "always_ask", "yolo"):
        assert A.unwatched_mode(mode, started_by="restart", continuation=True) == mode


def test_every_self_started_door_is_a_continuation_or_not_a_person():
    """The doors call start_build_turn so the mapping above applies: the
    restart resume, Resume when ready, the account move and the plan run."""
    src = (ROOT / "src/iron_jarvis/daemon/routes/build.py").read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert re.search(r"start_build_turn\(\s*d, thread_id, CONTINUE_TEXT, turn_id, continuation=True, resume_of=wait,\s*"
                     r"account_move=\{[^}]*\}, started_by=\"wait\"", src), "account move"
    assert re.search(r"start_build_turn\(d, thread_id, CONTINUE_TEXT, turn_id, continuation=True,\s*"
                     r"resume_of=wait, started_by=\"wait\"\)", src), "resume when ready"
    assert re.search(r"start_build_turn\(d, thread_id, RR\.RESUME_QUESTION, new_turn,\s*"
                     r"continuation=True, resume_turn=turn_id, started_by=\"restart\"\)", src), "restart resume"
    assert re.search(r"start_build_turn\(\s*d, thread_id, PLAN_RUN_TEXT, new_turn, continuation=True,", src), "plan run"
    night = (ROOT / "src/iron_jarvis/daemon/routes/night.py").read_bytes().decode("utf-8")
    assert "started_by=N.BY_NIGHT" in night
    # …and the turn's body takes its posture through the mapping.
    assert re.search(r"approval_mode=_auto_approve\.unwatched_mode\(\s*str\(setup\.get\(\"approval_mode\"\) or \"\"\),"
                     r"\s*started_by=started_by, continuation=continuation,", src)


@pytest.fixture()
def build_app(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    (tmp_path / "cc").mkdir()
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.chat_files_root = str(tmp_path / "chat-files")
    work = tmp_path / "work"
    work.mkdir()
    return a, work


@pytest.mark.parametrize(
    "started_by,continuation,want",
    [("person", False, "auto"), ("restart", True, "approve_for_me"), ("wait", True, "approve_for_me"),
     ("person", True, "approve_for_me")],
    ids=["a-persons-message", "restart-resume", "resume-when-ready", "plan-run"],
)
async def test_the_turn_the_lane_receives_carries_the_mapped_posture(build_app, monkeypatch, started_by,
                                                                    continuation, want):
    from iron_jarvis.daemon.routes import build as B
    from tests.test_build_sessions_v1367 import new_session, until

    app, work = build_app
    seen: list[str] = []

    async def fake_stream(platform, personas, body, **kw):
        seen.append(str(body.approval_mode or ""))

        async def gen():
            yield 'event: done\ndata: {"reply": "ok"}\n\n'

        return gen()

    monkeypatch.setattr(B, "stream_chat_turn", fake_stream)
    sid = new_session(app, work, approval_mode="auto")["id"]
    lg = await B.start_build_turn(app.state.d, sid, "go on", "t-" + started_by + str(continuation),
                                  started_by=started_by, continuation=continuation)
    await until(lambda: lg.status != "running", 30)
    assert seen == [want]
