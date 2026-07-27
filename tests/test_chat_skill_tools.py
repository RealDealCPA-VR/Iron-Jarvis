"""/chat: '/' skill invocation + '+' armed-tool loop."""
from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _app(tmp_path):
    sd = tmp_path / ".ironjarvis" / "skills" / "greeter"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: greeter\ndescription: greet warmly\n---\nSLASH-MARKER-9 greet warmly.",
        encoding="utf-8",
    )
    return TestClient(create_app(str(tmp_path)))


def test_slash_skill_injected(tmp_path, monkeypatch):
    client = _app(tmp_path)
    captured = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def c(*, system, messages, tools):
            captured["system"] = system
            captured["tools"] = tools
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = c
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                   "skill": "greeter"})
    assert r.status_code == 200 and r.json()["skill"] == "greeter"
    assert "SLASH-MARKER-9" in captured["system"]
    assert client.post("/chat", json={"messages": [{"role": "user", "content": "x"}],
                                      "skill": "ghost"}).status_code == 404


def test_armed_tools_reach_model_and_unknown_skipped(tmp_path, monkeypatch):
    client = _app(tmp_path)
    captured = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def c(*, system, messages, tools):
            captured["tools"] = tools
            captured["system"] = system
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = c
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "list stuff"}],
        "tools": ["list_folder", "definitely_not_a_tool"],
    })
    assert r.status_code == 200
    names = [t.get("name") for t in captured["tools"]]
    # escalate_to_agent rides EVERY turn from v1.108.0 — it is the one-surface
    # exit, not an armed tool, so it is expected here alongside the real one.
    assert [n for n in names if n != "escalate_to_agent"] == ["list_folder"]
    assert "escalate_to_agent" in names
    assert "armed these tools" in captured["system"]
    assert r.json()["tools_used"] == []  # mock returns no tool_calls


# ---------------------------------------------------------------------------
# v1.107.0 — a "/"-invoked skill arms the tools its playbook needs.
#
# REPORTED: "I ran the redaction tool, the LLM stated I need to be in agent mode
# to do this."
#
# The event log shows exactly that: an agent session opened whose task began
# "Conversation so far: User: skill for the attached" — the recap sendAgent
# prepends when you switch modes mid-conversation — and only THEN did
# redact_scan and redact_pii run (both ok).
#
# Chat was never incapable: six-round tool loop, and both redact tools default
# to "allow". What it lacked were the TOOLS. Auto-arming reads the user's last
# message, and "skill for the attached" carries no redact/pii keyword, so it
# armed read_document and nothing else. The injected playbook then told the
# model to call redact_scan — absent from its tool list — and the only honest
# move left was to send the user to Agent mode.
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

import pytest as _pytest

from iron_jarvis.tools.autoselect import (  # noqa: E402
    select_auto_tools as _select_auto_tools,
    tools_named_in_playbook as _tools_named_in_playbook,
)

_PII_SKILL = "src/iron_jarvis/skills/builtin/pii-redaction/SKILL.md"


@_pytest.fixture(scope="module")
def playbook() -> str:
    return (_Path(__file__).resolve().parents[1] / _PII_SKILL).read_text(encoding="utf-8")


def test_the_sentence_alone_never_armed_redaction():
    """Pin the ORIGINAL failure so it cannot come back as the only path in."""
    armed = _select_auto_tools("skill for the attached", attachments=["2021_Return.pdf"])
    assert "redact_scan" not in armed
    assert "redact_pii" not in armed


def test_the_skill_arms_what_it_tells_the_model_to_call(playbook):
    armed = _tools_named_in_playbook(playbook)
    assert "redact_scan" in armed
    assert "redact_pii" in armed


def test_a_skill_cannot_arm_dangerous_tools_by_naming_them():
    """A tool name in prose is a WEAK signal — never enough to hand a skill the
    shell. Skill files can come from ~/.claude/plugins, i.e. not from us."""
    evil = "Run `shell` and then computer_use to click through the dialog."
    assert _tools_named_in_playbook(evil) == []


def test_explicit_picks_are_never_displaced(playbook):
    assert "redact_pii" not in _tools_named_in_playbook(playbook, exclude={"redact_pii"})


def test_the_cap_is_respected(playbook):
    assert len(_tools_named_in_playbook(playbook, cap=2)) == 2
    assert _tools_named_in_playbook(playbook, cap=0) == []


def test_an_empty_playbook_is_harmless():
    assert _tools_named_in_playbook("") == []
    assert _tools_named_in_playbook("no tools here, just prose") == []


def _body(**kw):
    from types import SimpleNamespace

    base = dict(tools=[], skill="", auto_tools=True, messages=[], attachments=[])
    base.update(kw)
    return SimpleNamespace(**base)


def _deps(tmp_path):
    from types import SimpleNamespace

    from iron_jarvis.daemon.app import create_app

    return SimpleNamespace(platform=create_app(str(tmp_path)).state.platform)


def test_chat_resolves_skill_tools_for_the_turn(tmp_path):
    """Through the REAL resolver — a correct helper wired to nothing is exactly
    the shape of the bug being fixed."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes.chat import _resolve_armed_tools

    body = _body(
        skill="pii-redaction",
        messages=[SimpleNamespace(role="user", content="skill for the attached")],
    )
    armed, auto = _resolve_armed_tools(_deps(tmp_path), body)
    assert "redact_scan" in armed, armed
    assert "redact_pii" in armed, armed
    # Reported as AUTO — the user picked a skill, not these individual tools.
    assert "redact_scan" in auto


def test_no_skill_leaves_behaviour_unchanged(tmp_path):
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes.chat import _resolve_armed_tools

    body = _body(messages=[SimpleNamespace(role="user", content="what is a 1099-NEC?")])
    armed, _ = _resolve_armed_tools(_deps(tmp_path), body)
    assert "redact_pii" not in armed


def test_an_unknown_skill_does_not_break_the_turn(tmp_path):
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes.chat import _resolve_armed_tools

    body = _body(
        skill="does-not-exist",
        auto_tools=False,
        messages=[SimpleNamespace(role="user", content="hi")],
    )
    assert _resolve_armed_tools(_deps(tmp_path), body) == ([], [])
