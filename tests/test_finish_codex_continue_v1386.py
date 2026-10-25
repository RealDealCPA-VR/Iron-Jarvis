"""IJ harness part 3, Phase 2 — REVIEW: the finish note on codex-cli.

Measured with the fake app-server on the doer's commit (e6dcf046): the
model ENDS its reply (Codex's turn completes, the session is torn down), so
the finish note's call was a NEW app-server with the whole conversation
replayed as labelled text — 10,354 characters for a reply of one 4,000-char
question and one 6,000-char tool result, all fresh, uncached input, plus a
process start — once per Build reply that changed files. Now the lane opens a
TURN SCOPE (``providers/turn_scope``) while its finish check can still send
the note, the adapter keeps a session whose turn ended inside it, and the
note continues the SAME thread (``turn/start``). Outside a scope (plain chat,
an agent, a schedule) nothing changes, pinned by the control.

Driven through the REAL adapter against the fake app-server
(``fixtures/fake_codex_app_server_v1365.py``, now able to play one script
per turn); no test runs the real ``codex``.
"""

from __future__ import annotations

import time

import pytest

from iron_jarvis.harness import helpers as H
from iron_jarvis.providers import turn_scope
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.codex_native import protocol as P
from iron_jarvis.providers.adapters.codex_native import session as S
from tests.test_codex_native_v1365 import TOOLS, Fake, _after, _clean, _user  # noqa: F401 — the fixture
from tests.test_finish_lanes_v1386 import _body, _lane, env  # noqa: F401 — the fixture

PROCS = [
    {"turns": [
        [{"call": {"tool": "lookup_fake", "args": {"q": "x"}}}, {"text": "Done."},
         {"usage": {"inputTokens": 1000, "outputTokens": 10}}],
        [{"text": "1. met: checked."}, {"usage": {"inputTokens": 1500, "outputTokens": 10}}],
    ]},
    {"steps": [{"text": "1. met: from a new session."}, {"usage": {"inputTokens": 900, "outputTokens": 10}}]},
]


async def _final(adapter, messages, tools=TOOLS):
    resp = None
    async for frame in adapter.stream(system="sys", messages=messages, tools=tools):
        if frame.get("type") == "final":
            resp = frame["response"]
    return resp


async def _reply(adapter):
    """A reply that ran one tool and ended, then the lane's finish note."""
    msgs = [_user("set X to 2 " + "y" * 4000)]
    r1 = await _final(adapter, msgs)
    msgs = _after(msgs, r1, {"lookup_fake": "result " + "z" * 6000})
    r2 = await _final(adapter, msgs)
    assert r2.text == "Done."
    return msgs + [LLMMessage(role="assistant", content=r2.text),
                   LLMMessage(role="user", content="FINISH CHECK. note")]


def _turns(fake):
    return [(t["msg"]["params"].get("threadId"), t["msg"]["params"]["input"],
             any(P.REPLAY_HEADER in i.get("text", "") for i in t["msg"]["params"]["input"]))
            for t in fake.received("turn/start")]


def _gone(proc, within=10.0) -> bool:
    deadline = time.monotonic() + within
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    return proc.poll() is not None


async def test_outside_a_turn_scope_the_note_is_exactly_as_before(tmp_path, monkeypatch):
    """The control: a second app-server, the conversation replayed."""
    fake = Fake(tmp_path, monkeypatch, PROCS)
    adapter = fake.adapter()
    r3 = await _final(adapter, await _reply(adapter))
    turns = _turns(fake)
    assert len(fake.starts()) == 2 and turns[-1][2] is True
    assert r3.text == "1. met: from a new session."
    assert [s for s in S.POOL.live() if s.state == "done"] == [], "nothing is kept outside a scope"


async def test_in_a_turn_scope_the_note_continues_the_same_thread(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, PROCS)
    adapter = fake.adapter()
    scope, token = turn_scope.open_scope()
    try:
        r3 = await _final(adapter, await _reply(adapter))
        turns = _turns(fake)
        assert len(fake.starts()) == 1, "the note started a second Codex process"
        assert len(turns) == 2 and turns[0][0] == turns[1][0] and turns[1][2] is False
        assert turns[1][1] == [{"type": "text", "text": "FINISH CHECK. note"}], "nothing replayed"
        assert r3.text == "1. met: checked." and r3.usage["input_tokens"] == 1500
        kept = [s for s in S.POOL.live() if s.state == "done"]
        assert len(kept) == 1, "the continued turn ended too: kept until the scope closes"
        proc = kept[0].proc
    finally:
        await turn_scope.close(scope)
        turn_scope.reset(token)
    assert _gone(proc), "closing the scope stops the kept session"
    assert S.POOL.live() == []


async def test_a_helpers_turn_is_never_kept(tmp_path, monkeypatch):
    """A helper agent's run inherits its parent's scope; its own turn is its
    own and must never linger as a kept process for the parent's turn."""
    fake = Fake(tmp_path, monkeypatch, PROCS)
    adapter = fake.adapter()
    scope, token = turn_scope.open_scope()
    htoken = H.HELPER_PARENT.set(object())
    try:
        await _reply(adapter)
        assert [s for s in S.POOL.live() if s.state == "done"] == []
    finally:
        H.HELPER_PARENT.reset(htoken)
        await turn_scope.close(scope)
        turn_scope.reset(token)


async def test_another_conversation_or_other_tools_never_continue_a_kept_thread(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, PROCS)
    adapter = fake.adapter()
    scope, token = turn_scope.open_scope()
    try:
        msgs = await _reply(adapter)
        other = [LLMMessage(role="user", content="something else")] + msgs[1:]
        await _final(adapter, other)
        assert len(fake.starts()) == 2, "another conversation continued a kept thread"
        await _final(adapter, msgs, tools=TOOLS[:1])
        assert len(fake.starts()) == 3, "a call with other tools continued a kept thread"
    finally:
        await turn_scope.close(scope)
        turn_scope.reset(token)


async def test_a_closed_scope_keeps_nothing(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, PROCS)
    adapter = fake.adapter()
    scope, token = turn_scope.open_scope()
    await turn_scope.close(scope)
    try:
        await _reply(adapter)
        assert [s for s in S.POOL.live() if s.state == "done"] == []
    finally:
        turn_scope.reset(token)


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_build_replys_finish_round_is_one_process_through_the_lane(env, tmp_path, monkeypatch, lane):  # noqa: F811 — the fixture
    """Through the REAL lanes (MIRROR NOTE: both open the scope): the reply's
    write, its answer, the note and the answer to it run on ONE app-server and
    ONE thread; the lane's end stops it."""
    platform, ws, _wire = env
    fake = Fake(tmp_path, monkeypatch, [{"turns": [
        [{"call": {"tool": "write_file", "args": {"path": "app.py", "content": "X = 2\n"}}}, {"text": "Done."}],
        [{"text": "1. met: app.py says X = 2.\nNothing needed changing."}],
    ]}])
    adapter = fake.adapter()

    async def stream(*, provider=None, model=None, system="", messages=(), tools=None, **kw):
        async for frame in adapter.stream(system=system, messages=list(messages), tools=tools):
            if frame.get("type") == "final":
                yield {"type": "final", "response": frame["response"], "provider": "codex-cli",
                       "model": "gpt-fake-1"}
            else:
                yield frame

    async def complete(*, provider=None, model=None, system="", messages=(), tools=None, **kw):
        from iron_jarvis.providers.router import RouteResult

        resp = None
        async for frame in stream(system=system, messages=messages, tools=tools):
            if frame.get("type") == "final":
                resp = frame["response"]
        return RouteResult(resp, "codex-cli", "gpt-fake-1")

    monkeypatch.setattr(platform.router, "stream", stream)
    monkeypatch.setattr(platform.router, "complete", complete)
    done, _frames = await _lane(lane, platform, _body(ws, text="set X to 2 in app.py"), monkeypatch)
    assert (ws / "app.py").read_bytes().replace(b"\r\n", b"\n") == b"X = 2\n"
    assert done["finish"]["tests"] == "none"
    assert done["reply"] == "Done.\n\nNothing needed changing."
    turns = _turns(fake)
    assert len(fake.starts()) == 1, "the finish round started a second Codex process"
    assert len(turns) == 2 and turns[0][0] == turns[1][0] and turns[1][2] is False
    assert turns[1][1][0]["text"].startswith("FINISH CHECK.")
    deadline = time.monotonic() + 10
    while S.POOL.live() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert S.POOL.live() == [], "the lane's end stopped the kept session"


@pytest.mark.parametrize("messages, expect", [
    ([LLMMessage(role="user", content="a"), LLMMessage(role="assistant", content="b"),
      LLMMessage(role="user", content="c")], True),
    ([LLMMessage(role="user", content="a")], False),
    ([LLMMessage(role="user", content="a"), LLMMessage(role="assistant", content="b")], False),
])
def test_the_continuation_shape(messages, expect):
    assert (P.continuation_of(messages) is not None) is expect
