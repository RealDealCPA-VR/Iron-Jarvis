"""v1.422.0 (Wave 2 "Never lose work", B1): a coding reply WAITS OUT a passing
provider failure.

A round whose model call fails with a TRANSIENT error (overloaded, busy, a
5xx, a timeout, a dropped connection) is asked again — the same call, so the
same provider, model and account — after 5, 15, 30 and 60 seconds, saying so
on the live line (a ``provider_wait`` frame). Words a failed try had streamed
are taken back (``reset`` with ``drop``: only that try's characters). Nothing
runs twice: a round's tools run only after its model call returns. A
non-transient failure (a 400, a refusal, a sign-in or key problem, the
account's own limit, a too-long refusal) is never waited out, and PLAIN CHAT
(not a harness turn) is unchanged.

Driven through the REAL stream lane (``stream_chat_turn`` with ``harness=``)
and the real POST lane on a real platform; only the model (the router) is a
double. Every wait goes through ``harness.patience.SLEEP``, which these tests
replace with one that records the wait and returns at once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import ChatTurnFailed, run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import patience as P
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import (
    LLMResponse,
    ProviderError,
    ProviderRefusal,
    ToolCall,
)
from iron_jarvis.providers.router import RouteResult

CLAUDE = ("anthropic", "claude-sonnet-4-5")


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


class Flaky:
    """A scripted model whose moves may FAIL. A move is: a list of ToolCalls,
    a reply text, an exception (raised before any frame), or ``("partial",
    words, exc)`` (the words stream, then the exception). Every call records
    the provider and model it was ASKED for."""

    def __init__(self, moves: list, who=CLAUDE) -> None:
        self.provider, self.model = who
        self.moves = list(moves)
        self.calls: list[dict] = []

    def _move(self, provider, model, messages, tools):
        self.calls.append({"provider": provider, "model": model, "messages": list(messages),
                           "tools": [t["name"] for t in tools or []]})
        return self.moves.pop(0) if self.moves else "Done."

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None,
                     task_class=None, **kw):
        move = self._move(provider, model, messages, tools)
        if isinstance(move, BaseException):
            raise move
        if isinstance(move, tuple) and move and move[0] == "partial":
            yield {"type": "text", "text": move[1]}
            raise move[2]
        if isinstance(move, tuple) and move and move[0] == "after_final":
            # The whole answer (with a tool call) arrived, THEN the stream
            # broke: the round must never be asked again (its tools would run
            # twice).
            resp = LLMResponse(text="", tool_calls=list(move[1]), usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}
            raise move[2]
        if isinstance(move, str):
            yield {"type": "text", "text": move}
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None,
                       task_class=None, **kw):
        move = self._move(provider, model, messages, tools)
        if isinstance(move, BaseException):
            raise move
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1}),
                               self.provider, self.model)
        return RouteResult(LLMResponse(text="", tool_calls=list(move),
                                       usage={"input_tokens": 3, "output_tokens": 1}),
                           self.provider, self.model)


def _overloaded() -> ProviderError:
    return ProviderError("anthropic API error 529: overloaded_error", status_code=529)


def _busy(retry_after: float | None = None) -> ProviderError:
    return ProviderError("anthropic API error 429: too many requests", status_code=429,
                         retry_after=retry_after)


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    """Every wait the patience asks for, in order; none really sleeps. The
    patience is ON here (the suite turns it off for bare tests: conftest)."""
    seen: list[float] = []

    async def fake_sleep(seconds):
        seen.append(float(seconds))

    monkeypatch.setattr(P, "WAITS_S", P.DEFAULT_WAITS_S)
    monkeypatch.setattr(P, "SLEEP", fake_sleep)
    return seen


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()

    def wire(moves, who=CLAUDE):
        model = Flaky(moves, who)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


def _body(ws: Path, text: str = "make one.txt") -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider=CLAUDE[0], model=CLAUDE[1], workspace_dir=str(ws), approval_mode="yolo",
    )


async def _stream(platform, body, **kw) -> list[tuple[str, dict]]:
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    return _frames([c async for c in gen])


def _writes(platform) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == "write_file"]
        for r in rows:
            db.expunge(r)
        return rows


def _write(name: str, content: str) -> list[ToolCall]:
    return [ToolCall(id="w1", name="Write", arguments={"file_path": name, "content": content})]


# --------------------------------------------------------------------------- #
# the stream lane (a coding session's turn)
# --------------------------------------------------------------------------- #


async def test_a_harness_round_that_fails_twice_is_tried_again_and_the_reply_finishes(env, waits):
    platform, ws, wire = env
    model = wire([_overloaded(), _busy(), _write("one.txt", "1"), "Made one.txt."])
    frames = await _stream(platform, _body(ws), harness=True)
    done = dict(frames).get("done")
    assert done is not None, frames[-3:]
    assert done["reply"] == "Made one.txt."
    assert not [d for e, d in frames if e == "error"]
    # The file was written ONCE: the failed tries ran no tool.
    assert (ws / "one.txt").read_text(encoding="utf-8") == "1"
    assert len(_writes(platform)) == 1
    # The live line said so, twice, and each wait ended with a "trying" frame.
    pw = [d for e, d in frames if e == P.FRAME]
    waiting = [d for d in pw if d.get("state") == "waiting"]
    assert [d["attempt"] for d in waiting] == [2, 3] and all(d["of"] == 5 for d in waiting)
    assert waiting[0]["words"].endswith("Trying again in 5 s, attempt 2 of 5.")
    assert "overloaded" in waiting[0]["words"] and "too many requests" in waiting[1]["words"]
    assert [d.get("state") for d in pw] == ["waiting", "trying", "waiting", "trying"]
    assert waits == [5.0, 15.0]
    # The SAME provider and model, every try (never another provider).
    asked = {(c["provider"], c["model"]) for c in model.calls}
    assert asked == {CLAUDE}
    assert len(model.calls) == 4


async def test_words_a_failed_try_streamed_are_taken_back_and_only_those(env, waits):
    platform, ws, wire = env
    wire([
        ("partial", "Half an answ", ProviderError("anthropic API error 503: unavailable", status_code=503)),
        "The whole answer.",
    ])
    frames = await _stream(platform, _body(ws, "explain it"), harness=True)
    resets = [d for e, d in frames if e == "reset"]
    assert resets == [{"reason": P.RESET_REASON, "drop": len("Half an answ")}]
    assert dict(frames)["done"]["reply"] == "The whole answer."
    # The reset comes before the wait line, which comes before the new words.
    order = [e for e, _ in frames if e in ("token", "reset", P.FRAME)]
    assert order.index("reset") < order.index(P.FRAME)


async def test_a_failure_after_the_final_frame_is_never_tried_again(env, waits):
    platform, ws, wire = env
    model = wire([
        ("after_final", _write("one.txt", "1"), ProviderError("x API error 503: gone", status_code=503)),
        "should not be asked",
    ])
    frames = await _stream(platform, _body(ws), harness=True)
    assert [e for e, _ in frames if e == "error"] == ["error"]
    assert waits == [] and len(model.calls) == 1
    assert not [d for e, d in frames if e == P.FRAME]


@pytest.mark.parametrize("exc", [
    ProviderError("anthropic API error 400: bad request", status_code=400),
    ProviderError("anthropic API error 401: invalid x-api-key", status_code=401),
    ProviderRefusal("declined", category="cyber"),
    ProviderError("anthropic API error 429: usage limit", status_code=429, transient=False),
    ProviderError("prompt is too long: 250000 tokens > 200000 maximum", status_code=400),
])
async def test_a_failure_that_is_not_passing_is_never_waited_out(env, waits, exc):
    platform, ws, wire = env
    # (A too-long refusal is refit ONCE by its own path, which never waits:
    # the second move fails that refit too.)
    model = wire([exc, exc, "never asked"])
    frames = await _stream(platform, _body(ws), harness=True)
    assert [e for e, _ in frames if e == "error"] == ["error"]
    assert not [d for e, d in frames if e == P.FRAME]
    assert waits == []
    # Asked once (a too-long refusal may be refit ONCE by its own path, which
    # never waits).
    assert len(model.calls) <= 2


async def test_an_account_sentence_marked_no_failover_is_never_waited_out(env, waits):
    platform, ws, wire = env
    err = ProviderError("Every Claude account is resting until 3:40 PM.", status_code=429)
    err.no_failover = True  # Iron-Proxy's account sentences
    wire([err])
    frames = await _stream(platform, _body(ws), harness=True)
    assert [e for e, _ in frames if e == "error"] == ["error"] and waits == []


async def test_out_of_tries_the_reply_fails_and_says_how_long_it_tried(env, waits):
    platform, ws, wire = env
    model = wire([_overloaded() for _ in range(6)])
    frames = await _stream(platform, _body(ws), harness=True)
    err = [d for e, d in frames if e == "error"]
    assert err, frames[-3:]
    assert waits == [5.0, 15.0, 30.0, 60.0]
    assert len(model.calls) == 5
    assert "Iron Jarvis tried 5 times over 1 min 50 s." in err[0]["fix"]


async def test_plain_chat_is_unchanged_a_passing_failure_ends_the_turn(env, waits):
    platform, ws, wire = env
    model = wire([_overloaded(), "never asked"])
    frames = await _stream(platform, _body(ws, "hello"))
    err = [d for e, d in frames if e == "error"]
    assert err and "tried" not in err[0].get("fix", "")
    assert not [d for e, d in frames if e == P.FRAME]
    assert waits == [] and len(model.calls) == 1


# --------------------------------------------------------------------------- #
# the POST lane (lock-step)
# --------------------------------------------------------------------------- #


async def test_the_post_lane_waits_out_a_passing_failure_in_a_harness_turn(env, waits):
    platform, ws, wire = env
    model = wire([_overloaded(), _write("two.txt", "2"), "Made two.txt."])
    out = await run_chat_turn(platform, BUILTIN_PERSONAS, _body(ws), harness=True)
    assert out["reply"] == "Made two.txt."
    assert waits == [5.0]
    assert len(_writes(platform)) == 1
    assert {(c["provider"], c["model"]) for c in model.calls} == {CLAUDE}


async def test_the_post_lane_in_plain_chat_is_unchanged(env, waits):
    platform, ws, wire = env
    wire([_overloaded(), "never asked"])
    with pytest.raises(ChatTurnFailed):
        await run_chat_turn(platform, BUILTIN_PERSONAS, _body(ws, "hello"))
    assert waits == []


# --------------------------------------------------------------------------- #
# the rules themselves
# --------------------------------------------------------------------------- #


def test_which_failures_are_waited_out():
    assert P.why_waited(_overloaded()) == "overloaded"
    assert P.why_waited(_busy()) == "busy"
    assert P.why_waited(ProviderError("x", status_code=502)) == "server"
    assert P.why_waited(TimeoutError("read timed out")) == "timeout"
    assert P.why_waited(ConnectionResetError("reset by peer")) in ("dropped", "unreachable")
    assert P.why_waited(ProviderError("x", status_code=400)) == ""
    assert P.why_waited(ProviderError("x", status_code=403)) == ""
    assert P.why_waited(ProviderError("x", status_code=429, transient=False)) == ""
    assert P.why_waited(ProviderError("x", status_code=429, retry_after=3600)) == ""
    assert P.why_waited(ValueError("a bug")) == ""


def test_a_retry_after_is_honoured_up_to_its_cap_and_the_round_has_a_budget():
    pat = P.RoundPatience()
    w = pat.next_wait(_busy(retry_after=90))
    assert w is not None and w.seconds == 90 and w.attempt == 2
    w = pat.next_wait(_busy(retry_after=200))
    # capped at 120, and 90 + 120 is inside the 240 s budget
    assert w is not None and w.seconds == P.RETRY_AFTER_CAP_S
    # 90 + 120 + 30 = 240: still inside; the next would pass the budget
    assert pat.next_wait(_overloaded()) is not None
    assert pat.next_wait(_overloaded()) is None


def test_the_words_name_the_provider_and_the_try():
    words = P.waiting_words("overloaded", 30, 2, 5, provider="claude-cli")
    assert words == "Claude Code is busy (overloaded). Trying again in 30 s, attempt 2 of 5."
    assert P.waiting_words("dropped", 60, 4, 5, provider="").startswith("The connection to the model dropped")
