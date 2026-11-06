"""v1.441.0: OpenCode in Build › Code — the user's OWN agents as modes, its
helpers, and its question tool.

MEASURED on opencode 1.18.18 (``hosted.opencode`` holds the reading): a
SCRATCH home, a stand-in model server on 127.0.0.1 scripting the model's tool
calls, and a read of the program's bundled ACP source.

* ``configOptions.mode`` lists build, plan and every primary / all agent of
  the user's that is not hidden; an agent's own ``permission`` rules sit AFTER
  the config's and ``OPENCODE_PERMISSION`` (its ``"bash": "allow"`` ran with no
  card under ``{"*": "ask"}``) — ``fixtures/build_app_v14410/oc_agent_docs``.
* A helper's (``task``) own question is never sent over ACP: the helper and the
  reply waited until Stop. So outside Full access ``task`` is refused.
* OpenCode's ``question`` tool is not offered to an ACP session, and with
  ``OPENCODE_ENABLE_QUESTION_TOOL`` its question was never sent either. So
  elicitation is not advertised and the variable is dropped.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, stop_all

FIX = Path(__file__).parent / "fixtures" / "build_app_v14410"
DOCS = {"name": "docs", "description": "Writes docs only"}


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _agents(monkeypatch):
    """Each test starts with no agent listed (the map is the process's)."""
    monkeypatch.setattr(O, "LAST_AGENTS", {})


def _options(*agents: dict) -> list[dict]:
    return [{"id": "mode", "type": "select", "currentValue": "build", "options": [
        {"value": "build", "name": "build", "description": "The default agent."},
        *[{"value": a["name"], "name": a["name"], "description": a.get("description", "")} for a in agents],
        {"value": "plan", "name": "plan", "description": "Plan mode."},
    ]}]


# -- the user's own agents ------------------------------------------------------
def test_the_users_agents_are_read_from_the_mode_option_never_build_or_plan():
    got = O.agents_from_options(_options(DOCS, {"name": "allx", "description": "an all-mode agent"}))
    assert got == [DOCS, {"name": "allx", "description": "an all-mode agent"}]
    assert O.agents_from_options(_options()) == []


def test_an_agent_mode_is_ask_first_with_that_agent():
    assert O.posture_agent("agent:docs") == ("ask", "docs")
    assert O.posture_agent("agent:build") == ("ask", "build"), "a preset's agent is never an agent mode"
    assert O.posture_agent("agent:../x") == ("ask", "build")
    assert O.agent_of("ask") == "" and O.agent_of("agent:Docs") == "Docs"


def test_an_agent_modes_rules_are_ask_firsts_and_never_name_a_tool_of_the_users():
    assert O.permission_env("agent:docs", {})[0] == O.permission_env("ask", {})[0]
    perm, kept = O.permission_env("agent:docs", {"bash": "allow"})
    assert "bash" not in perm and kept == ["bash"], "a rule of the user's own is never changed"
    assert "edit" not in O.permission_env("agent:docs", {})[0], "no Plan refusal for an agent of the user's"


def test_the_mode_menu_offers_a_listed_agent_before_full_access_and_says_what_it_does():
    O.note_agents("C:/work/demo", [DOCS])
    rows = S.mode_rows("opencode")
    ids = [r["id"] for r in rows]
    assert ids == ["ask", "edits", "plan", "agent:docs", "full"]
    docs = rows[3]
    assert docs["label"] == "docs" and docs["dangerous"] is False
    assert docs["about"].startswith("Writes docs only. ") and "own permission rules" in docs["about"]
    assert S.mode_label("opencode", "agent:docs") == "docs"


def test_only_a_listed_agent_is_accepted_and_its_name_keeps_its_case():
    with pytest.raises(S.Refused) as no:
        S.check_mode("opencode", "agent:docs", False)
    assert "docs" in str(no.value)
    O.note_agents("C:/w", [{"name": "Docs", "description": ""}])
    assert S.check_mode("opencode", "agent:Docs", False) == "agent:Docs"
    with pytest.raises(S.Refused):
        S.check_mode("codex", "agent:Docs", False)


def test_the_agents_are_kept_on_disk_and_read_back(tmp_path):
    O.note_agents("C:/work/a", [DOCS])
    path = tmp_path / "agents.json"
    O.save_agents(path)
    O.LAST_AGENTS.clear()
    O.load_agents(path)
    assert O.known_agents() == [DOCS]
    assert O.known_agents("C:/work/a") == [DOCS] and O.known_agents("C:/other") == [DOCS]


async def test_a_session_on_the_users_agent_switches_opencode_to_it(app, work, tmp_path, monkeypatch):
    O.note_agents(str(work), [DOCS])
    log = tmp_path / "fake.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / "oc_agent_docs.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    async with client(app) as c:
        s = await new_session(c, work, harness="opencode", mode="agent:docs")
        assert s["mode"] == "agent:docs"
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "RUNBASH", "turn_id": "t1"})
        evs = frames(r.text)
        await stop_all(app)
    sets = [m["params"] for m in sent(log) if m.get("method") == "session/set_config_option"]
    assert sets == [{"sessionId": sets[0]["sessionId"], "configId": "mode", "value": "docs"}]
    # The measured run: the agent's own "bash": "allow" ran the command with NO card.
    assert not any(e["type"] == "approval" for e in evs)
    assert any(e["type"] == "tool" and e.get("kind") == "command" and e["status"] == "done" for e in evs)
    env = next(r for r in [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if "argv" in r)["env"]
    assert json.loads(env["OPENCODE_PERMISSION"])["task"] == "deny"


async def test_an_agent_this_folder_does_not_list_falls_back_to_build_in_words():
    got: list = []

    async def write(obj):
        got.append(obj)

    d = O.OpenCodeDriver(write=write, emit=lambda e: got.append(e), ask=lambda a: got.append(a),
                         on_session_id=lambda s: None, mode="agent:review", cwd="C:/w")
    calls: list = []

    async def request(method, params=None, timeout=None):
        calls.append((method, params))
        if method == "initialize":
            return {"agentCapabilities": {"loadSession": True}}
        return {"sessionId": "ses_abcdefghijkl", "configOptions": _options(DOCS)}

    d.request = request  # type: ignore[method-assign]
    await d.open(session_id="", resume=False)
    assert not [c for c in calls if c[0] == "session/set_config_option"], "never a mode OpenCode does not list"
    notes = [e["text"] for e in got if isinstance(e, dict) and e.get("type") == "notice"]
    assert W.OPENCODE_NO_SUCH_AGENT.format(agent="review") in notes


# -- helpers (task) -------------------------------------------------------------
@pytest.mark.parametrize("mode", ["ask", "edits", "plan", "agent:docs"])
def test_outside_full_access_opencode_starts_no_helpers(mode):
    assert O.permission_env(mode, {})[0]["task"] == "deny"
    assert "starts no helpers" in (O.MODE_ABOUT.get(mode) or O.AGENT_ABOUT)


def test_full_access_and_a_users_own_task_rule_are_left_alone():
    assert O.permission_env("full", {})[0] == {"*": "allow"}
    perm, kept = O.permission_env("ask", {"task": "allow"})
    assert "task" not in perm and kept == ["task"]


def _driver():
    got: list = []

    async def write(obj):
        got.append({"wrote": obj})

    d = O.OpenCodeDriver(write=write, emit=lambda e: got.append(e), ask=lambda a: got.append(a),
                         on_session_id=lambda s: None, mode="ask", cwd="C:/w")
    d.session_id = "ses_parent0000000"
    d.turn_open = True
    return d, got


async def test_an_update_of_another_opencode_session_is_never_drawn_as_this_ones():
    d, got = _driver()
    tool = {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash", "kind": "execute",
            "status": "pending", "rawInput": {"command": "echo hi"}}
    d.on_message({"jsonrpc": "2.0", "method": "session/update",
                  "params": {"sessionId": "ses_child00000000", "update": tool}})
    assert not any(isinstance(e, dict) and e.get("type") == "tool" for e in got)
    d.on_message({"jsonrpc": "2.0", "method": "session/update",
                  "params": {"sessionId": "ses_parent0000000", "update": tool}})
    assert any(isinstance(e, dict) and e.get("type") == "tool" for e in got)


async def test_a_permission_question_naming_another_session_is_still_a_card_answered_by_its_own_id():
    d, got = _driver()
    d.on_message({"jsonrpc": "2.0", "id": 41, "method": "session/request_permission", "params": {
        "sessionId": "ses_child00000000",
        "toolCall": {"toolCallId": "c9", "title": "bash", "kind": "execute", "rawInput": {"command": "echo hi"}},
        "options": [{"optionId": "once", "kind": "allow_once"}, {"optionId": "reject", "kind": "reject_once"}]}})
    ask = next(a for a in got if isinstance(a, Ask))
    assert d.prepare_answer(ask, "allow", "", None)["id"] == 41


# -- the question tool ------------------------------------------------------------
def test_opencode_is_never_told_iron_jarvis_answers_forms():
    d, _ = _driver()
    assert d.client_capabilities() == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}


def test_the_question_tool_switch_never_reaches_opencode(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(tmp_path / "cfg"))
    env, _ = O.child_env({"OPENCODE_ENABLE_QUESTION_TOOL": "1", "PATH": "x"}, "ask", str(tmp_path))
    assert "OPENCODE_ENABLE_QUESTION_TOOL" not in env and env["PATH"] == "x"


def test_the_measurements_are_written_into_the_module():
    doc = O.__doc__ or ""
    assert "MEASURED for v1.441.0" in doc
    for fact in ("session.tryGet", "OPENCODE_ENABLE_QUESTION_TOOL", "elicitation", "subagent", "Task cancelled"):
        assert fact in doc, fact


async def test_the_users_agents_are_noted_when_opencode_lists_them():
    d, _ = _driver()
    d._take_options(_options(DOCS))
    assert O.known_agents("C:/w") == [DOCS]
    await asyncio.sleep(0)
