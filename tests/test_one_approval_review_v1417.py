"""v1.417.0 review (C1.2) — the running reply's session grants: the lane's
own ``accept`` rule (not a stand-in lambda), Local only, and that nothing is
left registered however the reply ends."""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.harness import session_grants as SG
from iron_jarvis.providers import egress
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — `env` is a fixture
    _body,
    _shell,
    env,
)
from tests.test_one_approval_turn_v1417 import THREAD, _drive


@pytest.fixture(autouse=True)
def _clean():
    SG.forget()
    yield
    SG.forget()


class _D:
    def __init__(self, platform):
        self.platform = platform


def _attach(platform, *, mode="approve_for_me", trust=None, overrides=None):
    armed, cards = set(), set()
    turn = chat_routes._attach_session_turn(
        _D(platform), f"build:{THREAD}", armed, cards, dict(overrides or {}), mode,
        dict(trust or {"low": False}),
    )
    return turn, armed, cards


@pytest.mark.parametrize("trust,mode,overrides", [
    ({"low": True}, "approve_for_me", {}),
    ({"low": False, "tainted": True}, "approve_for_me", {}),
    ({"low": False}, "always_ask", {}),
    ({"low": False}, "approve_for_me", {"shell": "ask", "process_start": "deny"}),
])
def test_the_lanes_own_accept_rule_refuses(env, trust, mode, overrides):
    platform, _ws, _wire = env
    turn, armed, cards = _attach(platform, mode=mode, trust=trust, overrides=overrides)
    assert turn is not None
    SG.note(THREAD, {"shell"}, platform.registry)
    if overrides:
        # The folder's rules keep their two tools; the rest of the family joins.
        assert "shell" not in armed and "process_start" not in armed
        assert "process_write" in armed
    else:
        assert armed == set() and cards == set()
    SG.detach_turn(turn)


def test_an_ordinary_reply_takes_the_family(env):
    platform, _ws, _wire = env
    turn, armed, cards = _attach(platform)
    SG.note(THREAD, {"shell"}, platform.registry)
    assert {"shell", "process_start"} <= armed and {"shell", "process_start"} <= cards
    SG.detach_turn(turn)
    assert THREAD not in SG._TURNS


def test_a_local_only_reply_takes_no_session_grant_mid_turn(env):
    platform, _ws, _wire = env
    with egress.local_only_scope(True):
        turn, armed, _cards = _attach(platform)
    assert turn is None
    SG.note(THREAD, {"shell"}, platform.registry)
    assert armed == set()


def test_a_grant_never_wakes_a_card_that_was_already_answered(env):
    """``watch_ask`` then the user's own answer: a later grant's wake is a
    no-op on the registry (resolve returns False on a done future)."""
    platform, _ws, _wire = env
    approvals = chat_routes._approvals(_D(platform))
    calls: list[tuple] = []

    class Spy:
        def resolve(self, aid, decision, *, actor="app"):
            calls.append((aid, decision, actor))
            return approvals.resolve(aid, decision, actor=actor)

    turn = SG.attach_turn(THREAD, set(), set(), approvals=Spy())
    SG.watch_ask(turn, "no-such-ask", {"shell"})
    assert SG.release_helpers(approvals, platform.registry, THREAD, {"shell"}) == 0
    SG.detach_turn(turn)


async def test_nothing_stays_registered_after_a_reply_ends_or_is_stopped(env, monkeypatch):
    platform, ws, wire = env
    wire([_shell('python -c "print(1)"'), "Done."])

    def answer(frame, approvals):
        assert approvals.resolve(str(frame["id"]), "deny")

    await _drive(platform, _body(ws, text="say hello", approval_mode="approve_for_me"), answer)
    assert SG._TURNS == {}

    # A reply closed while parked on its card (the client went away).
    wire([_shell('python -c "print(2)"'), "Done."])

    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    gen = await stream_chat_turn(
        platform, BUILTIN_PERSONAS, _body(ws, text="say hello", approval_mode="approve_for_me"),
        harness="generic", session_id=f"build:{THREAD}", round_budget=30, stay_in_chat=True,
    )
    seen_turn = False
    async for chunk in gen:
        if "event: approval" in chunk:
            seen_turn = bool(SG._TURNS.get(THREAD))
            break
    await gen.aclose()
    assert seen_turn and SG._TURNS == {}


@pytest.mark.parametrize("name,command,changes", [
    ("delegate", "", True),
    ("spawn_agent", "", True),
    ("run_code", "", True),
    ("mcp__app__write", "", True),
    ("write_file", "", True),
    ("shell", "python tools/gen.py", True),
    ("shell", "pytest -q", False),
    ("shell", "ls", False),
    ("read_file", "", False),
])
def test_finish_skip_counts_a_helper_or_any_writer_as_a_change(name, command, changes):
    """C1.3c: a helper's edits land inside its hand-off call, so the hand-off
    itself is a possible change (a test that passed before it runs again)."""
    from types import SimpleNamespace

    from iron_jarvis.harness import finish as F
    from iron_jarvis.tools.base import ToolResult

    fc = F.FinishCheck.__new__(F.FinishCheck)
    fc._custom, fc.harness, fc._named, fc.checks = (), None, [], []
    call = SimpleNamespace(id="c1", name=name, arguments={"command": command} if command else {})
    res = ToolResult(ok=True, output="", data={})
    assert fc._may_change_files(name, call, res) is changes
