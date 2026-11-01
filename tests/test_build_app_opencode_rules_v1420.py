"""v1.420.0 (C1.10): OpenCode in Build's Code view never changes a rule of the
user's own, grants its helpers like Iron Jarvis's own, and keeps an "Allow for
this session" across the new process a mode change starts.

The verdicts below are OpenCode 1.18.18's own evaluation (the simulator of
``test_build_app_opencode_review_v1413``, read from the installed program:
config rules merged in load order, ``OPENCODE_PERMISSION`` merged last, a key
the user already has keeps its PLACE, the last matching rule wins, names and
patterns matched as wildcards without regard to case). The permission
requests are the recorded ACP shapes in ``tests/fixtures/build_app_v1413``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask
from tests.test_build_app_opencode_review_v1413 import (
    PATTERNS,
    RANK,
    _wild,
    config_permission,
    env_for,
    verdict,
)

FIX_OC = Path(__file__).parent / "fixtures" / "build_app_v1413"

#: A user's own rules, each one a case the old code changed.
USER_CONFIGS: list[dict[str, Any]] = [
    {},
    {"bash": "allow"},
    {"edit": "allow"},
    {"bash": "allow", "edit": "allow"},
    {"bash": {"git *": "allow", "*": "ask"}},
    {"*": "deny"},
    {"*": "allow"},
    {"web*": "deny"},
    {"edit": "allow", "*": "allow"},
    {"task": "deny"},
    {"bash": "deny"},
]
TOOLS = ["bash", "edit", "webfetch", "websearch", "read", "github_create_issue", "task", "glob"]


def _covered(user: dict[str, Any], tool: str, pattern: str) -> bool:
    return any(_wild(tool, k) and (isinstance(v, str) or any(_wild(pattern, p) for p in v))
               for k, v in user.items())


# --------------------------------------------------------------------------- #
# 1. A rule of the user's own is never CHANGED — tighter or looser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["ask", "edits", "plan", "full"])
@pytest.mark.parametrize("user", USER_CONFIGS)
def test_no_rule_of_the_users_is_changed_in_any_mode(tmp_path, monkeypatch, mode, user):
    env = env_for(tmp_path, monkeypatch, mode, user)
    with_env = config_permission(user, None, env)
    without = config_permission(user, None, None)
    for agent in ("build", "plan"):
        for tool in TOOLS:
            for pattern in PATTERNS:
                if not _covered(user, tool, pattern):
                    continue
                if mode == "plan" and tool == "edit":
                    continue  # Plan refuses every edit, on purpose
                a = verdict(agent, with_env, tool, pattern)
                b = verdict(agent, without, tool, pattern)
                assert a == b, (mode, user, agent, tool, pattern, env)


@pytest.mark.parametrize("mode", ["ask", "edits"])
def test_a_users_bash_allow_is_kept(tmp_path, monkeypatch, mode):
    """The merge-order case the plan names: a user's ``"bash": "allow"`` used to
    meet Iron Jarvis's ``"*": "ask"`` AFTER it, and every allowed command became
    a card."""
    env = env_for(tmp_path, monkeypatch, mode, {"bash": "allow"})
    assert env is not None and "*" not in env and "bash" not in env
    perm = config_permission({"bash": "allow"}, None, env)
    assert verdict("build", perm, "bash", "npm test") == "allow"
    # The tools the user left unset still follow the mode.
    assert verdict("build", perm, "webfetch", "https://example.com") == "ask"


def test_the_global_and_project_config_files_count(tmp_path, monkeypatch):
    """Every config file OpenCode reads: the global folder, the project's own
    ``opencode.json`` and ``.opencode/opencode.json``, the managed folder."""
    conf = tmp_path / "global"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    (conf / "opencode.json").write_text('{"permission": {"edit": "allow"}}', encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / ".opencode").mkdir(parents=True)
    (proj / ".opencode" / "opencode.json").write_text('{"permission": {"bash": "allow"}}', encoding="utf-8")
    env, note = O.child_env({}, "ask", str(proj))
    perm = json.loads(env["OPENCODE_PERMISSION"])
    assert "*" not in perm and "bash" not in perm and "edit" not in perm, perm
    assert note == W.OPENCODE_RULES_KEPT.format(tools="bash, edit")


def test_the_managed_folder_counts_too(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "opencode.json").write_text('{"permission": {"bash": "allow"}}', encoding="utf-8")
    monkeypatch.setenv("OPENCODE_TEST_MANAGED_CONFIG_DIR", str(managed))
    monkeypatch.delenv("IRONJARVIS_OPENCODE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    work = tmp_path / "w"
    work.mkdir()
    env, _note = O.child_env({}, "ask", str(work))
    assert "bash" not in json.loads(env["OPENCODE_PERMISSION"])


def test_star_is_still_used_when_every_rule_of_the_users_says_the_same():
    """A user rule that already says what ``"*"`` would is not changed by it, so
    ``"*"`` still covers the tools of their own servers and plugins (no notice)."""
    perm, kept = O.permission_env("ask", {"bash": "ask", "webfetch": {"*": "ask"}})
    assert perm["*"] == "ask" and kept == []
    perm, kept = O.permission_env("plan", {"edit": "ask"})
    assert perm["*"] == "ask" and perm["EDIT"] == "deny" and kept == []


def test_a_wildcard_rule_of_the_users_names_the_tools_it_matches():
    """``"web*": "deny"`` names webfetch and websearch: naming them ``ask``
    after it turned the refusal into a card."""
    perm, kept = O.permission_env("ask", {"web*": "deny"})
    assert "webfetch" not in perm and "websearch" not in perm and kept == ["web*"]


def test_a_users_own_star_decides_for_every_tool(tmp_path, monkeypatch):
    """A ``"*"`` rule of the user's names every tool. ``"*": "deny"`` used to get
    the built-in tools named ``ask`` after it (a refusal turned into cards)."""
    perm, kept = O.permission_env("ask", {"*": "deny"})
    assert perm == {} and kept == ["*"]
    conf = tmp_path / "c"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    (conf / "opencode.json").write_text('{"permission": {"*": "deny"}}', encoding="utf-8")
    env, note = O.child_env({}, "ask", str(tmp_path))
    assert "OPENCODE_PERMISSION" not in env and note == W.OPENCODE_RULES_DECIDE
    env, note = O.child_env({}, "plan", str(tmp_path))
    assert json.loads(env["OPENCODE_PERMISSION"]) == {"edit": "deny"} and note == W.OPENCODE_RULES_DECIDE_PLAN


# --------------------------------------------------------------------------- #
# 2. Plan's refusal is the LAST rule for edit
# --------------------------------------------------------------------------- #
def test_plan_still_refuses_an_edit_when_the_user_allows_edits_and_everything(tmp_path, monkeypatch):
    """A user's ``"edit": "allow"`` followed by ``"*": "allow"``: Plan's deny
    used to replace ``edit`` IN PLACE, and the user's ``"*"`` after it allowed
    the edit. The deny now goes under a key the user does not have."""
    user = {"edit": "allow", "*": "allow"}
    env = env_for(tmp_path, monkeypatch, "plan", user)
    assert env == {"EDIT": "deny"}
    perm = config_permission(user, None, env)
    for pattern in ("src/app.py", "notes.txt"):
        assert verdict("plan", perm, "edit", pattern) == "deny"
    assert O._fresh_key("edit", {"edit", "EDIT", "Edit"}) not in {"edit", "EDIT", "Edit"}


# --------------------------------------------------------------------------- #
# 3. OpenCode's helpers start without a card; their steps still ask
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["ask", "edits"])
def test_a_helper_starts_without_a_card_and_its_steps_still_ask(tmp_path, monkeypatch, mode):
    env = env_for(tmp_path, monkeypatch, mode, {})
    perm = config_permission({}, None, env)
    assert verdict("build", perm, "task", "general") == "allow"
    assert verdict("build", perm, "bash", "rm -rf build") == "ask"
    assert verdict("build", perm, "webfetch", "https://example.com") == "ask"


def test_plan_keeps_opencodes_own_rule_for_helpers(tmp_path, monkeypatch):
    env = env_for(tmp_path, monkeypatch, "plan", {})
    perm = config_permission({}, None, env)
    assert "task" not in env
    assert RANK[verdict("plan", perm, "task", "general")] >= RANK["ask"]


def test_a_users_own_task_rule_is_kept():
    perm, kept = O.permission_env("ask", {"task": "deny"})
    assert "task" not in perm and kept == ["task"]


# --------------------------------------------------------------------------- #
# 4. "Allow for this session" survives the new process of a mode change
# --------------------------------------------------------------------------- #
def _recorded_request(scenario: str) -> dict[str, Any]:
    steps = json.loads((FIX_OC / f"{scenario}.json").read_text(encoding="utf-8"))
    steps = steps if isinstance(steps, list) else steps.get("steps") or []
    return next(s["out"] for s in steps if isinstance(s, dict) and isinstance(s.get("out"), dict)
                and s["out"].get("method") == "session/request_permission")


def _driver(session_id: str) -> tuple[O.OpenCodeDriver, list, list, list]:
    wrote: list = []
    asks: list = []
    events: list = []

    async def write(obj):
        wrote.append(obj)

    drv = O.OpenCodeDriver(write=write, emit=events.append, ask=asks.append,
                           on_session_id=lambda s: None, mode="ask")
    drv.session_id = session_id
    return drv, wrote, asks, events


def _deliver(drv: O.OpenCodeDriver, request: dict[str, Any], first_name: str) -> None:
    """The request after its tool's FIRST record (which names the tool)."""
    tc = request["params"]["toolCall"]
    drv._tools[tc["toolCallId"]] = (first_name, tc.get("kind") or "", {})

    async def go():
        drv.on_message(request)
        await asyncio.sleep(0)

    asyncio.run(go())


@pytest.fixture(autouse=True)
def _fresh_always(monkeypatch):
    monkeypatch.setattr(O, "SESSION_ALWAYS", {}, raising=False)


SID = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"


def test_a_commands_allow_for_the_session_is_answered_again_after_a_new_process():
    request = _recorded_request("oc_bash_session")
    old, *_ = _driver(SID)
    old.answered(Ask("ask_1", "approval", "bash", 0, {}, {"command": "echo one"}, "", True), "allow_session")
    new, wrote, asks, events = _driver(SID)
    _deliver(new, request, "bash")
    assert asks == [], "no card: the user already answered this one for the session"
    assert wrote == [{"jsonrpc": "2.0", "id": request["id"],
                      "result": {"outcome": {"outcome": "selected", "optionId": "always"}}}]
    assert [e["text"] for e in events if e.get("type") == "notice"] == [W.OPENCODE_ALWAYS_CARRIED]
    # Said once a process.
    _deliver(new, {**request, "id": 9}, "bash")
    assert [e["text"] for e in events if e.get("type") == "notice"] == [W.OPENCODE_ALWAYS_CARRIED]


def test_only_the_same_command_in_the_same_conversation():
    request = _recorded_request("oc_bash_session")
    old, *_ = _driver(SID)
    old.answered(Ask("ask_1", "approval", "bash", 0, {}, {"command": "echo two"}, "", True), "allow_session")
    other_cmd, wrote, asks, _ = _driver(SID)
    _deliver(other_cmd, request, "bash")  # asks for "echo one"
    assert len(asks) == 1 and wrote == []
    old.answered(Ask("ask_2", "approval", "bash", 0, {}, {"command": "echo one"}, "", True), "allow_session")
    other_conv, wrote, asks, _ = _driver("ses_bbbbbbbbbbbbbbbbbbbbbbbb")
    _deliver(other_conv, request, "bash")
    assert len(asks) == 1 and wrote == []


def test_an_edit_allowed_for_the_session_covers_every_edit_as_opencode_did():
    request = _recorded_request("oc_edit_allow")
    old, *_ = _driver(SID)
    old.answered(Ask("ask_1", "approval", "write", 0, {}, {"path": "C:/w/a.txt"}, "", True), "allow_session")
    new, wrote, asks, _ = _driver(SID)
    _deliver(new, request, "write")
    assert asks == [] and wrote[0]["result"]["outcome"]["optionId"] == "always"


@pytest.mark.parametrize("answer", ["allow", "deny"])
def test_only_allow_for_the_session_is_kept(answer):
    old, *_ = _driver(SID)
    old.answered(Ask("ask_1", "approval", "bash", 0, {}, {"command": "echo one"}, "", True), answer)
    assert O.SESSION_ALWAYS == {}


def test_what_opencodes_always_reach_is_not_known_for_is_never_kept():
    assert not O.remember_always(SID, "bash", "rm *.tmp"), "a wildcard would reach other commands"
    assert not O.remember_always(SID, "bash", "ls ?")
    assert not O.remember_always(SID, "github_create_issue", "")
    assert not O.remember_always(SID, "external_directory", "")
    assert not O.remember_always("", "edit", "")
    assert O.SESSION_ALWAYS == {}


def test_a_request_with_no_first_record_is_a_card():
    """Without the tool's first record the name is unknown (the request's title
    is the command): never matched."""
    request = _recorded_request("oc_bash_session")
    O.remember_always(SID, "bash", "echo one")
    new, wrote, asks, _ = _driver(SID)

    async def go():
        new.on_message(request)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert len(asks) == 1 and wrote == []
