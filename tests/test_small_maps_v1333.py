"""Small maps that grew for the daemon's whole life are bounded (v1.333.0, D9).

Each gained one entry per session, sender, rule or turn and nothing ever took
it out: the approvals' waited-seconds map, the phone poller's per-sender locks
(minted even for a sender the allowlist refuses), the safety-check bell's and
the prompt guard's published-key sets, the detection rules' line-split cache
(keyed on the UNCAPPED command), a named chat turn whose stream was never
started, and a git session whose review failed to build. (The runtime's
in-run grants and taint flags: tests/test_repl_reclaim_v1333.py.)
"""

from __future__ import annotations

import asyncio
import gc
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import orchestrator as orch_mod
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm import Notifier
from iron_jarvis.core import approvals as approvals_mod
from iron_jarvis.core import promptguard
from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.turns import TURNS
from iron_jarvis.detections import bell, rules


@pytest.mark.asyncio
async def test_the_waited_seconds_map_keeps_only_the_newest_sessions(monkeypatch):
    monkeypatch.setattr(approvals_mod, "WAITED_SESSIONS_MAX", 3)
    approvals = ChatApprovals()
    for n in range(5):
        approval_id, _ = approvals.request("shell", {}, session_id=f"s{n}")
        approvals.pop(approval_id)
    assert list(approvals._waited) == ["s2", "s3", "s4"]
    approval_id, _ = approvals.request("shell", {}, session_id="s2")  # asks again
    approvals.pop(approval_id)
    assert list(approvals._waited) == ["s3", "s4", "s2"]


class _Channel:
    """A channel that refuses everyone (the allowlist is the point)."""

    def is_authorized(self, sender_id) -> bool:
        return False


@pytest.mark.asyncio
async def test_a_refused_sender_never_mints_a_lock(platform):
    poller = InboundPoller(Notifier(), Orchestrator(platform), platform.engine)
    for n in range(20):
        out = await poller._handle("tg", _Channel(), InboundMessage(sender_id=str(n), text="hi"))
        assert out["status"] == "unauthorized"
    assert poller._identity_locks == {}


def test_the_split_cache_holds_short_commands_only_and_caps_first():
    rules._cached_command_lines.cache_clear()
    rules.command_lines("ls -la\npwd")
    assert rules._cached_command_lines.cache_info().currsize == 1
    huge = "echo x\n" * 20_000  # 140,000 characters
    lines = rules.command_lines(huge)
    assert rules._cached_command_lines.cache_info().currsize == 1  # not remembered
    assert lines == rules._split_command_lines(huge[: rules.COMMAND_CAP])


def test_the_prompt_guards_published_keys_are_bounded(monkeypatch):
    monkeypatch.setattr(promptguard, "PUBLISHED_MAX", 3)
    promptguard.reset_published()
    bus = SimpleNamespace(publish=lambda *a, **kw: None)  # a sync double
    result = promptguard.ScanResult(text="", blocked=[{"category": "x"}], source="notes")
    for n in range(5):
        assert promptguard.publish_blocked(bus, f"s{n}", result) is True
    assert list(promptguard._PUBLISHED) == [("s2", "notes"), ("s3", "notes"), ("s4", "notes")]
    promptguard.reset_published()


def test_the_safety_bells_published_keys_are_bounded(monkeypatch):
    monkeypatch.setattr(bell, "PUBLISHED_MAX", 3)
    monkeypatch.setattr(bell, "_PUBLISHED", bell.OrderedDict())
    finding = SimpleNamespace(
        rule_id="r1", severity=sorted(bell.BELL_SEVERITIES)[0],
        to_dict=lambda text=False: {"events": []},
    )
    monkeypatch.setattr(bell, "events_for_session", lambda engine, scope: [])
    monkeypatch.setattr(bell, "scan", lambda events: [finding])

    async def publish(*a, **kw):
        return None

    bus = SimpleNamespace(publish=publish)
    for n in range(5):
        assert len(bell.scan_and_publish(bus, None, f"s{n}")) == 1
    assert list(bell._PUBLISHED) == [("r1", "s2"), ("r1", "s3"), ("r1", "s4")]


@pytest.mark.asyncio
async def test_a_named_turn_whose_stream_never_starts_is_released(platform):
    from iron_jarvis.daemon.routes.chat import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody

    body = ChatBody(messages=[{"role": "user", "content": "hi"}], tools=[], turn_id="never-run")
    stream = await stream_chat_turn(platform, {}, body)
    assert TURNS.is_running("never-run")
    del stream  # the client went away before asking for a single frame
    gc.collect()
    for _ in range(20):  # the release is scheduled on this loop
        if not TURNS.is_running("never-run"):
            break
        await asyncio.sleep(0)
    assert not TURNS.is_running("never-run")


@pytest.mark.asyncio
async def test_a_git_session_whose_review_fails_is_not_held_forever(platform, monkeypatch):
    orch = Orchestrator(platform)
    session = await orch.create_session("say hello")
    orch._git_sessions[session.id] = SimpleNamespace(worktree=None)

    def boom(*a, **kw):
        raise RuntimeError("git is not answering")

    monkeypatch.setattr(orch_mod, "build_review", boom)
    await orch.run_session(session.id)
    assert session.id not in orch._git_sessions
    assert session.id not in orch._reviews
