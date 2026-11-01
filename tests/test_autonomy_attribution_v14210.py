"""v1.421.0 (Wave 1, E6) — the Autonomy card places a HELPER's card and a
coding session's own card on the coding session's task.

The live record (read-only, one week): 12 asks, all "unattributed" — 9 were
cards of a Build session's helper runs, 2 of an attempt (Try on several
models), 1 of the browser sidebar. Two causes:

* ``core.approvals.run_place`` read a helper's ``options.build_session`` only
  in the spelling ``build:<thread>``, while ``harness/helpers.HelperParent.
  options`` writes the BARE thread id ("chat_…") — so every real helper card
  was published as ``surface: "agent"`` with no thread, and a helper run is
  no task of its own (``eval/autonomy.agent_tasks`` skips it);
* an attempt's cards carry its own tool session's thread
  (``<thread>~try<n>``), which no conversation is keyed by.

Fixed both ways: ``run_place`` reads both spellings (new events carry
``build`` + the thread), and ``compute`` places the events already written —
a helper's by its run's ``options.build_session``, an attempt's by its base
thread. The browser sidebar's card keeps no conversation and stays
"unattributed" (the module's own definitions). The "one interruption per
thing the person acted on" rule holds for helpers: a helper card a GRANT
settled is not counted a second time.

The fixtures are shaped like the owner's real payloads (masked: no titles,
fake ids, fake hashes).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.approvals import flush_interrupt_notes, resolve_as, run_place
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import autonomy as A
from iron_jarvis.harness import helpers as H

NOW = datetime.now(timezone.utc).timestamp()
T0 = NOW - 2 * 86400
HASH = "a" * 64


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _build_thread(key: str = "chat_d0000000beef") -> dict:
    """A Build session with two tasks: T0 → T0+600 and T0+1000 → after."""
    msgs = [
        {"role": "user", "content": "Fix the failing test in app.py", "at": _iso(T0)},
        {"role": "assistant", "content": "Fixed.", "at": _iso(T0 + 600)},
        {"role": "user", "content": "Now add a README for the project", "at": _iso(T0 + 1000)},
        {"role": "assistant", "content": "Added.", "at": _iso(T0 + 1600)},
    ]
    return {"id": key, "surface": "build", "messages_json": json.dumps(msgs), "created_at": _iso(T0)}


def _helper(sid: str, at: float, build: str = "chat_d0000000beef", origin: str = H.HELPER_ORIGIN) -> dict:
    # The real row: origin job:build-helper, options {"build_session": "<bare thread>"}.
    return {"id": sid, "origin": origin, "task": "a piece", "outcome": "completed_with_failures",
            "created_at": _iso(at), "options_json": json.dumps({H.BUILD_SESSION_OPTION: build})}


def _ev(type_: str, payload: dict) -> dict:
    return {"type": type_, "payload": payload, "created_at": None}


def _helper_card(ask: str, sid: str, at: float, *, decision: str = "conversation",
                 actor: str = "app", tool: str = "shell") -> list[dict]:
    # Exactly the shape the runtime published before this release:
    # surface "agent", the HELPER's session id, no thread, no turn.
    base = {"kind": "card", "tool": tool, "surface": "agent", "ask_id": ask,
            "session_id": sid, "args_hash": HASH}
    return [
        _ev("interrupt.asked", {**base, "at": at}),
        _ev("interrupt.answered", {**base, "decision": decision, "actor": actor,
                                   "wait_ms": 4000, "at": at + 4}),
    ]


def _attempt_card(ask: str, at: float, thread: str = "chat_d0000000beef") -> list[dict]:
    base = {"kind": "card", "tool": "shell", "surface": "build", "ask_id": ask,
            "thread_id": f"{thread}~try1", "turn_id": "chat-turn-0001.try1", "args_hash": HASH}
    return [
        _ev("interrupt.asked", {**base, "at": at}),
        _ev("interrupt.answered", {**base, "decision": "once", "actor": "app", "wait_ms": 2000,
                                   "at": at + 2}),
    ]


def _panel_card(ask: str, at: float) -> list[dict]:
    base = {"kind": "card", "tool": "browser_click", "surface": "chat", "ask_id": ask,
            "turn_id": "panel_00000000", "once_only": True, "reason": "risky_action"}
    return [
        _ev("interrupt.asked", {**base, "at": at}),
        _ev("interrupt.answered", {**base, "decision": "once", "actor": "app", "wait_ms": 3000,
                                   "at": at + 3}),
    ]


def _compute(threads, sessions, events) -> dict:
    return A.compute(threads=threads, sessions=sessions, events=events, days=30, now=NOW)


# --------------------------------------------------------------------------- #
# 1. the events already written (the owner's week)
# --------------------------------------------------------------------------- #


def test_a_helpers_card_and_an_attempts_card_land_on_the_coding_sessions_task():
    sessions = [_helper("session_h1", T0 + 10), _helper("session_h2", T0 + 1100)]
    events = [
        *_helper_card("apr_h1", "session_h1", T0 + 20),
        *_helper_card("apr_h2", "session_h2", T0 + 1120),
        *_attempt_card("apr_t1", T0 + 300),
        *_panel_card("apr_p1", T0 + 400),
    ]
    got = _compute([_build_thread()], sessions, events)
    i = got["interruptions"]
    # helper ×2 + attempt ×1 are placed; the sidebar's card keeps no
    # conversation and stays unattributed (the module's own definition).
    assert i["total"] == 3, i
    assert i["unattributed"] == 1, i
    build = got["by_surface"]["build"]
    assert (build["tasks"], build["interruptions"]) == (2, 3), build
    assert got["by_surface"]["agent"]["interruptions"] == 0
    assert got["by_surface"]["agent"]["tasks"] == 0  # a helper is never a task of its own


def test_each_card_lands_on_the_task_running_when_it_was_asked():
    sessions = [_helper("session_h1", T0 + 10), _helper("session_h2", T0 + 1100)]
    events = [*_helper_card("apr_h1", "session_h1", T0 + 20),
              *_helper_card("apr_h2", "session_h2", T0 + 1120)]
    # Each task saw ONE card the person answered, so neither is hands-off
    # (both cards on the first task would leave the second hands-off).
    got = _compute([_build_thread()], sessions, events)
    assert got["by_surface"]["build"]["interruptions"] == 2
    assert got["by_surface"]["build"]["hands_off"] == 0, (
        "both tasks had a card the person answered: neither is hands-off")
    # Without the helpers' cards both build tasks are hands-off (control).
    clean = _compute([_build_thread()], sessions, [])
    assert clean["by_surface"]["build"]["hands_off"] == 2


def test_a_helper_card_a_grant_settled_is_not_counted_a_second_time():
    """ONE interruption per thing the person acted on: the person answered
    the first helper's card "for this session"; a second helper's card that
    the grant woke (``session_grants.release_helpers``, actor ``grant``) is
    not another interruption."""
    sessions = [_helper("session_h1", T0 + 10), _helper("session_h2", T0 + 11)]
    events = [*_helper_card("apr_h1", "session_h1", T0 + 20),
              *_helper_card("apr_h2", "session_h2", T0 + 21, actor="grant")]
    got = _compute([_build_thread()], sessions, events)
    assert got["interruptions"]["total"] == 1, got["interruptions"]
    assert got["interruptions"]["unattributed"] == 0


def test_a_later_helper_card_for_a_tool_granted_for_the_session_is_avoidable():
    """The avoidable rule now sees helper cards in their session: a card the
    person approved for a tool they had granted "for this session" earlier in
    the same session (the second helper asked before the grant reached it)."""
    sessions = [_helper("session_h1", T0 + 10), _helper("session_h2", T0 + 30)]
    events = [*_helper_card("apr_h1", "session_h1", T0 + 20),
              *_helper_card("apr_h2", "session_h2", T0 + 40, decision="once")]
    got = _compute([_build_thread()], sessions, events)
    assert got["interruptions"]["total"] == 2
    assert got["interruptions"]["avoidable"] == 1


def test_a_benchmark_helpers_card_is_never_counted():
    """The benchmark's own helper (``build_session`` "bench-…") was ``agent``
    too before ``run_place`` read the bare option: it is the bench's, never
    an interruption and never "unattributed"."""
    sessions = [_helper("session_b1", T0 + 10, build="bench-1f2e3d")]
    events = _helper_card("apr_b1", "session_b1", T0 + 20)
    got = _compute([_build_thread()], sessions, events)
    assert got["interruptions"]["total"] == 0
    assert got["interruptions"]["unattributed"] == 0


def test_a_helper_of_a_session_the_read_did_not_hold_stays_unattributed():
    sessions = [_helper("session_h1", T0 + 10, build="chat_not_read")]
    events = _helper_card("apr_h1", "session_h1", T0 + 20)
    got = _compute([_build_thread()], sessions, events)
    assert (got["interruptions"]["total"], got["interruptions"]["unattributed"]) == (0, 1)


def test_the_new_shape_is_placed_too_and_a_build_prefix_reads_as_the_thread():
    sessions = [_helper("session_h3", T0 + 10)]
    base = {"kind": "card", "tool": "shell", "surface": "build", "session_id": "session_h3",
            "args_hash": HASH}
    events = [
        _ev("interrupt.asked", {**base, "ask_id": "apr_n1", "thread_id": "chat_d0000000beef", "at": T0 + 20}),
        _ev("interrupt.asked", {**base, "ask_id": "apr_n2", "thread_id": "build:chat_d0000000beef",
                                "at": T0 + 25}),
    ]
    got = _compute([_build_thread()], sessions, events)
    assert (got["interruptions"]["total"], got["interruptions"]["unattributed"]) == (2, 0)


def test_the_bare_thread_rule():
    assert A._bare_thread("chat_x~try2") == "chat_x"
    assert A._bare_thread("build:chat_x~try1") == "chat_x"
    assert A._bare_thread("build:chat_x") == "chat_x"
    assert A._bare_thread("chat_x") == "chat_x"
    assert A._bare_thread("chat~trying") == "chat~trying"  # not an attempt's suffix
    assert A._bare_thread(None) == ""


# --------------------------------------------------------------------------- #
# 2. new events: where a helper's ask is published
# --------------------------------------------------------------------------- #


def test_run_place_reads_the_bare_build_session_option_the_helpers_write():
    # The REAL option (harness/helpers.HelperParent.options writes the bare thread).
    parent = H.HelperParent(session_id="build:chat_d0000000beef", folder="", provider="p", model="m")
    opts = parent.options()
    assert opts == {H.BUILD_SESSION_OPTION: "chat_d0000000beef"}
    row = SimpleNamespace(options_json=json.dumps(opts))
    assert run_place(row) == ("build", "chat_d0000000beef")
    # The prefixed spelling still reads the same; the bench's own stays bench.
    assert run_place(SimpleNamespace(options_json='{"build_session": "build:t1"}')) == ("build", "t1")
    assert run_place(SimpleNamespace(options_json='{"build_session": "bench-1f"}')) == ("bench", "bench-1f")
    assert run_place(SimpleNamespace(options_json='{"build_session": "build:bench-1f"}')) == (
        "bench", "bench-1f")
    assert run_place(row, night=object()) == ("night", "chat_d0000000beef")
    assert run_place(SimpleNamespace(options_json='{"chat_thread_id": "c1"}')) == ("agent", "c1")
    assert run_place(SimpleNamespace()) == ("agent", "")


@pytest.fixture()
def rt(tmp_path):
    app = create_app(str(tmp_path / "rt"))
    return SimpleNamespace(runtime=AgentRuntime(app.state.platform), platform=app.state.platform)


async def _answer_first(platform, decision: str) -> None:
    for _ in range(400):
        rows = list(platform.approvals._meta.values())
        if rows:
            resolve_as(platform.approvals, rows[0]["approval_id"], decision, actor="app")
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the pause never filed its ask")


async def test_a_real_helper_pause_publishes_the_build_session(rt):
    """End to end through the runtime's real pause, with the helper row as a
    Build session writes it: the event names the session's thread, and
    ``compute`` places it on the session's task."""
    parent = H.HelperParent(session_id="build:chat_d0000000beef", folder="", provider="p", model="m")
    session = SimpleNamespace(id="session_h9", origin=H.HELPER_ORIGIN,
                              options_json=json.dumps(parent.options()))
    tc = SimpleNamespace(name="shell", arguments={"command": 'python -c "print(1)"'})
    answerer = asyncio.create_task(_answer_first(rt.platform, "once"))
    deny, _extra = await rt.runtime._pause_for_approval(
        session, tc, get_agent_definition(AgentType.BUILDER), set()
    )
    await answerer
    await flush_interrupt_notes()
    assert deny == ""
    asked = [e.payload for e in rt.platform.event_bus.history if e.type == EventType.INTERRUPT_ASKED]
    assert asked[0]["surface"] == "build" and asked[0]["thread_id"] == "chat_d0000000beef"
    assert asked[0]["session_id"] == "session_h9"
    answered = [e.payload for e in rt.platform.event_bus.history if e.type == EventType.INTERRUPT_ANSWERED]
    at = T0 + 20
    events = [_ev("interrupt.asked", {**asked[0], "at": at}),
              _ev("interrupt.answered", {**answered[0], "at": at + 1})]
    got = _compute([_build_thread()], [_helper("session_h9", T0 + 10)], events)
    assert (got["interruptions"]["total"], got["interruptions"]["unattributed"]) == (1, 0)
