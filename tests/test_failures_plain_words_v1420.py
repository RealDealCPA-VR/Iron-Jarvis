"""Failures in plain words where they still read raw (v1.420.0, C6.5).

* A helper that RAISED told its parent "delegated agent 'x' crashed:
  ProviderError: …" — the class name reached the mission log and Build's
  helper rows. Both doors (``delegate``, ``spawn_agent``) now say the run's one
  sentence (``failure_words.run_failure_words``); the failure's own text rides
  ``data.technical`` only.
* Chat hid Iron-Proxy's exact account sentence behind "Press Retry":
  ``friendly_error`` now makes a refusal already worded for a person
  (``worded_message``) the headline, and an account's fix names where the
  accounts are.
* The phone's crash lines carried class names; they say the run's sentence.

Every case drives the REAL classifier — nothing here re-derives the words.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents.agent_tools import SpawnAgentTool
from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType
from iron_jarvis.iron_proxy.accounts import PROVIDERS, _refusal
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.failure_words import (
    ACCOUNTS_FIX,
    GENERIC_FIX,
    friendly_error,
    run_failure_words,
)
from iron_jarvis.tools.base import ToolContext
from tests.test_comm_full_chat import ChatMockChannel, CHAT_CFG, _chat_poller, _msg

ACCOUNT_WORDS = (
    'The Claude account "Work" in Iron-Proxy needs to sign in again — sign it in on '
    "the Connections page."
)


# --------------------------------------------------------------------------- #
# friendly_error: the account sentence IS the headline
# --------------------------------------------------------------------------- #
def test_an_iron_proxy_account_refusal_is_the_headline_and_the_fix_names_connections():
    exc = _refusal(PROVIDERS["claude-cli"], ACCOUNT_WORDS)
    words = friendly_error(exc, provider="claude-cli")
    assert words["title"] == ACCOUNT_WORDS.rstrip(".")
    assert not words["title"].startswith("claude-cli")
    assert words["fix"] == ACCOUNTS_FIX
    assert "Settings › Connections" in words["fix"]
    assert "Press Retry" not in words["fix"]


def test_a_worded_refusal_about_no_account_keeps_the_chats_fix():
    exc = ProviderError("claude-cli: Nothing was sent to the other model.", transient=False)
    exc.no_failover = True  # type: ignore[attr-defined]
    words = friendly_error(exc, provider="claude-cli")
    assert words["title"] == "Nothing was sent to the other model"
    assert words["fix"] == GENERIC_FIX


def test_control_an_unworded_failure_still_gets_the_generic_words():
    words = friendly_error(RuntimeError("boom"), provider="claude-cli")
    assert words["title"] == "Iron Jarvis couldn't finish this answer"
    assert words["fix"] == GENERIC_FIX


# --------------------------------------------------------------------------- #
# a helper that raised tells its parent in plain words
# --------------------------------------------------------------------------- #
@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path / "home"))


def _ctx(p, workspace, sid: str) -> ToolContext:
    return ToolContext(
        workspace=workspace, session_id=sid, agent_run_id="parent-run",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


def _crash(monkeypatch, exc: BaseException) -> None:
    async def boom(self, session, agent_def, parent_id=None):
        raise exc

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", boom)


async def test_a_crashed_delegate_child_reports_the_runs_sentence(platform, tmp_path, monkeypatch):
    _crash(monkeypatch, RuntimeError("socket closed: secret detail"))
    parent = await Orchestrator(platform).create_session("parent", AgentType.SUPERVISOR)
    res = await DelegateTool(platform).execute(
        {"agent_type": "builder", "task": "x"}, _ctx(platform, tmp_path, parent.id)
    )
    assert res.ok is False
    reason, technical = run_failure_words(RuntimeError("socket closed: secret detail"))
    assert res.error == f"delegated agent 'builder' could not finish: {reason}"
    assert "RuntimeError" not in res.error and "crashed" not in res.error
    assert "socket closed" in res.data["technical"]
    events = [e for e in platform.event_bus.history if e.type == "delegation.completed"]
    assert events and "RuntimeError" not in (events[-1].payload.get("result") or "")


async def test_a_crashed_spawned_child_reports_the_runs_sentence(platform, tmp_path, monkeypatch):
    # A provider's own failure is named by the provider ("Codex couldn't …").
    _crash(monkeypatch, ProviderError("codex-cli: CLI exited 1: weird", transient=False))
    parent = await Orchestrator(platform).create_session("parent", AgentType.AUTOMATION)
    res = await SpawnAgentTool(platform, platform.agents_registry).execute(
        {"agent": "builder", "task": "make it"}, _ctx(platform, tmp_path, parent.id)
    )
    assert res.ok is False
    assert res.error.startswith("spawned agent 'builder' could not finish: ")
    assert "ProviderError" not in res.error and "CLI exited" not in res.error
    assert "Codex couldn't finish this task" in res.error
    assert "CLI exited 1" in res.data["technical"]


# --------------------------------------------------------------------------- #
# the phone's crash line
# --------------------------------------------------------------------------- #
async def test_a_phone_chat_turn_crash_is_said_in_plain_words(platform):
    async def failing(platform_, personas, body):
        raise RuntimeError("KeyError deep inside: token=abc")

    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, failing)
    res = await poller._handle("tg", ch, _msg("hello"))
    assert res["status"] == "chat_error"
    line = store.history_body(res["thread_id"])[-1]["content"]
    assert line.startswith("I hit a problem: ")
    assert "RuntimeError" not in line and "KeyError deep inside" not in line
    assert "RuntimeError" not in ch.sent[-1]
