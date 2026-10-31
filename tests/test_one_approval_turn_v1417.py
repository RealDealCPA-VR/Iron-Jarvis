"""v1.417.0 (C1.2) — ONE approval per coding session reaches the RUNNING reply.

Two residuals of the v1.404.1 fix, driven through the REAL stream lane (the
registry, the permission engine, the approvals registry and the ledger are
real; only the model, and the execute of a background program, are doubles):

* a "for this session" answer on the reply's own ``shell`` card widened the
  card set (``card_grants``) to the command family, but the posture reads
  ``armed_grant`` — so ``process_start`` carded AGAIN in the same reply;
* a helper's "for this session" grant never reached the reply that was
  RUNNING: its card (the finish check's test run here) stayed parked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.harness import session_grants as SG
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.base import ToolResult
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — `env` is a fixture
    TEST_CMD,
    _body,
    _finish_card,
    _frames,
    _shell,
    _write,
    env,
)

THREAD = "c12-thread"
SESSION = f"build:{THREAD}"


@pytest.fixture(autouse=True)
def _clean():
    SG.forget()
    yield
    SG.forget()


async def _drive(platform, body, on_card):
    """The stream lane as a coding session's reply; ``on_card(frame,
    approvals)`` answers (or not) each card as it comes."""
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(
        platform, BUILTIN_PERSONAS, body, harness="generic", session_id=SESSION,
        round_budget=30, stay_in_chat=True,
    )
    frames: list[tuple[str, dict]] = []
    async for chunk in gen:
        for event, data in _frames([chunk]):
            frames.append((event, data))
            if event == "approval":
                on_card(data, approvals)
    done = [d for e, d in frames if e == "done"]
    return {"done": done[0] if done else None, "frames": frames,
            "cards": [d for e, d in frames if e == "approval"]}


def _stub_process_start(platform, monkeypatch) -> list[dict]:
    ran: list[dict] = []
    tool = platform.registry.get("process_start")
    assert tool is not None

    async def _fake(args, ctx):
        ran.append(dict(args))
        return ToolResult(ok=True, output="started", data={"id": "p1", "running": False, "exit_code": 0})

    monkeypatch.setattr(tool, "execute", _fake)
    return ran


async def test_a_session_answer_on_shell_covers_process_start_in_the_same_reply(env, monkeypatch):
    platform, ws, wire = env
    ran = _stub_process_start(platform, monkeypatch)
    wire([
        _shell('python -c "print(1)"'),
        [ToolCall(id="p1", name="process_start", arguments={"command": "npm run two", "until_exit": True})],
        "Done.",
    ])

    def answer(frame, approvals):
        assert approvals.resolve(str(frame["id"]), "conversation")

    out = await _drive(platform, _body(ws, text="say hello twice", approval_mode="approve_for_me"), answer)
    # ONE card — the shell's; the background program ran on that answer.
    assert [c["tool"] for c in out["cards"]] == ["shell"], out["cards"]
    assert [a["command"] for a in ran] == ["npm run two"]


async def test_a_session_answer_on_shell_still_leaves_a_folder_rule_on_process_start(env, monkeypatch):
    """A folder rule that asks about the background program keeps its card."""
    platform, ws, wire = env
    _stub_process_start(platform, monkeypatch)
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text('[[rules]]\ntool = "process_start"\naction = "ask"\n', encoding="utf-8")
    wire([
        _shell('python -c "print(1)"'),
        [ToolCall(id="p1", name="process_start", arguments={"command": "npm run two", "until_exit": True})],
        "Done.",
    ])

    def answer(frame, approvals):
        approvals.resolve(str(frame["id"]), "conversation" if frame["tool"] == "shell" else "once")

    out = await _drive(platform, _body(ws, text="say hello twice", approval_mode="approve_for_me"), answer)
    assert [c["tool"] for c in out["cards"]] == ["shell", "process_start"]


async def test_a_helpers_session_grant_releases_the_replys_waiting_finish_card(env, monkeypatch):
    platform, ws, wire = env
    # A bound, so a card the grant failed to reach ends as "timeout" here
    # instead of hanging the test.
    monkeypatch.setattr(chat_routes, "CHAT_ASK_TIMEOUT_S", 4.0)
    # The test ran BEFORE the last edit, so the check runs it again — on a card.
    wire([_shell(TEST_CMD), _write("app.py", "X = 2\n"), "Set X to 2.", "1. met.\nNothing needed changing."])
    woken: list[int] = []

    def answer(frame, approvals):
        if _finish_card(frame):
            # A helper of this session answers ITS card "for this session"
            # (the runtime's own path: note, then release).
            SG.note(THREAD, {"shell"}, platform.registry)
            woken.append(SG.release_helpers(approvals, platform.registry, THREAD, {"shell"}))
            return
        assert approvals.resolve(str(frame["id"]), "once")

    out = await _drive(platform, _body(ws, approval_mode="approve_for_me"), answer)
    assert woken == [1]
    resolved = [d for e, d in out["frames"] if e == "approval_resolved"]
    finish_resolved = [r for r in resolved if str(r["call_id"]).startswith("ij-finish-")]
    assert [r["decision"] for r in finish_resolved] == ["conversation"]
    assert out["done"]["finish"]["tests"] == "passed"
    # Settled by the GRANT: not listed as a card the user allowed.
    approved = out["done"].get("approved") or []
    assert len(approved) == 1, approved


async def test_a_grant_reaches_a_later_card_of_the_running_reply_with_no_card(env, monkeypatch):
    """A helper's grant made BEFORE the reply's next shell call: no card at all."""
    platform, ws, wire = env
    noted: list[bool] = []

    def grant_then_call(model):
        SG.note(THREAD, {"shell"}, platform.registry)
        noted.append(True)
        return _shell('python -c "print(3)"')

    wire([grant_then_call, "Done."])

    def answer(frame, approvals):
        raise AssertionError(f"a card the session grant covers: {frame}")

    out = await _drive(platform, _body(ws, text="say hello", approval_mode="approve_for_me"), answer)
    assert noted and out["cards"] == []


async def test_low_trust_never_takes_a_session_grant(env, monkeypatch):
    platform, ws, wire = env
    turn_sets: list[set] = []
    grants: set = set()
    t = SG.attach_turn(THREAD, grants, set(), accept=lambda n: False)
    SG.note(THREAD, {"shell"}, platform.registry)
    turn_sets.append(set(grants))
    SG.detach_turn(t)
    assert turn_sets == [set()]
