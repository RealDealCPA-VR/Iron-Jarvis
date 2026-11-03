"""v1.427.0 (D4) review: defects an independent reviewer found in the live
``claude`` session, each pinned red first.

Same harness as ``test_claude_live_v1427.py`` (the REAL adapter, transport and
relay against ``fixtures/fake_claude_live_v1427.py`` and a loopback fake
Anthropic). Nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import json
import time

from iron_jarvis.providers import turn_scope
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.claude_native import live, transport
from tests.test_claude_live_v1427 import (  # noqa: F401 — fixtures
    DENIAL,
    THREE,
    Upstream,
    _adapter,
    _alive,
    _answer,
    _call,
    _clean,
    _gone,
    _phases,
    _reply,
    _results_in,
    _scope,
    _starts,
    _user,
    trace,
    upstream,
)


class _SideUpstream(Upstream):
    """A request whose first message says ``SIDE`` is answered in text (a judge,
    a summary, a rewrite: a one-shot call with no tools); the rest by step."""

    def answer(self, body: dict) -> bytes:
        if "SIDE" in json.dumps(body["messages"][:1]):
            steps, self.steps = self.steps, [("text", "side")]
            try:
                return super().answer(body)
            finally:
                self.steps = steps
        return super().answer(body)


# --------------------------------------------------------------------------- #
# 1. a call with no tools inside the scope (the Auto judge, the mid-reply
#    summary, the language rewrite, the final-answer nudge) is a one-shot: it
#    must not keep a `claude` process for the rest of the reply, and must not
#    push the lead's kept process out through the cap.
# --------------------------------------------------------------------------- #
async def test_a_one_shot_call_without_tools_keeps_no_process_and_never_evicts_the_lead(tmp_path, trace):
    up = _SideUpstream(THREE)
    try:
        ad = _adapter(tmp_path, up)
        async with _scope():
            msgs = [_user("fix it")]
            first = await _call(ad, msgs)
            lead = _starts()[0]["pid"]
            # As many one-shots as the cap holds, AT ONCE (the Auto judge gathers
            # a round's judgements; four judged commands in one round is
            # ordinary): all of them are still running when the cap is read.
            sides = [
                asyncio.create_task(_call(ad, [_user(f"SIDE SLOW judge {i}")], tools=[], system="judge"))
                for i in range(live.MAX_LIVE_SESSIONS)
            ]
            deadline = time.monotonic() + 30
            while len(up.bodies) < 1 + live.MAX_LIVE_SESSIONS:
                assert time.monotonic() < deadline, "the one-shots never reached the upstream"
                await asyncio.sleep(0.05)
            assert _alive(lead), "a one-shot pushed the lead's kept process out"
            up.release.set()
            for side in await asyncio.gather(*sides):
                assert side.text.startswith("side"), side.text
            assert _alive(lead), "a one-shot pushed the lead's kept process out"
            assert [s.state for s in live.live_sessions()] == ["parked_tools"], "a one-shot left a process kept"
            _, final, _ = await _reply(ad, _answer(msgs, first))
            assert final.text == "Done REAL-a|REAL-b"
            lead_starts = [s for s in _starts() if s["pid"] == lead]
            assert len(lead_starts) == 1
        await transport.drain_teardowns()
        assert [i.get("reason") for i in _phases(trace, "live_resume")] == ["tools", "tools"]
        assert _phases(trace, "live_miss") == []
    finally:
        up.stop()


# --------------------------------------------------------------------------- #
# 2. a tool result holding a lone UTF-16 surrogate (a JavaScript MCP server
#    that cut an emoji in half; a Windows file name that is not valid UTF-16)
#    is sent by today's path (the CLI escapes it); the relay's re-written
#    request must send it too, never fail the round as "an internal relay error".
# --------------------------------------------------------------------------- #
async def test_a_tool_result_with_a_lone_surrogate_still_resumes(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    half = "half an emoji \ud83d here"
    async with _scope():
        msgs = [_user("fix it")]
        first = await _call(ad, msgs)
        msgs = _answer(msgs, first, {first.tool_calls[0].id: half})
        second = await _call(ad, msgs)
        assert second.tool_calls, "the resumed step answered"
        assert len(_starts()) == 1, "it continued the kept process"
        assert _results_in(up.bodies[1]) == [half]
        _, final, _ = await _reply(ad, _answer(msgs, second))
        assert final.text == f"Done {half}|REAL-b"
        assert len(_starts()) == 1


# --------------------------------------------------------------------------- #
# 3. checked and fine (kept as pins): a picture inside a tool result rides the
#    held request; the scope closing while a round still streams lets that
#    round answer and then ends the process (never parks it).
# --------------------------------------------------------------------------- #
_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


async def test_a_picture_in_a_tool_result_rides_the_held_request(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        msgs = [_user("fix it")]
        first = await _call(ad, msgs)
        tc = first.tool_calls[0]
        msgs = msgs + [
            LLMMessage(role="assistant", content=first.text, tool_calls=first.tool_calls,
                       raw_blocks=first.raw_blocks),
            LLMMessage(role="tool", content="SHOT", tool_call_id=tc.id, name=tc.name,
                       images=[{"media_type": "image/png", "data_b64": _PNG}]),
        ]
        second = await _call(ad, msgs)
        assert second.tool_calls and len(_starts()) == 1
        [result] = [b for b in up.bodies[1]["messages"][-1]["content"] if b.get("type") == "tool_result"]
        assert result["content"][0] == {"type": "text", "text": "SHOT"}
        assert result["content"][1]["source"]["data"] == _PNG
        assert "is_error" not in result and DENIAL not in json.dumps(up.bodies[1])


async def test_the_scope_closing_while_a_round_streams_lets_it_answer_then_ends_it(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    scope, token = turn_scope.open_scope()
    try:
        task = asyncio.create_task(_call(ad, [_user("SLOW one")]))
        assert await asyncio.to_thread(up.reached.wait, 30)
        pid = _starts()[0]["pid"]
        await turn_scope.close(scope)  # the lane's tool loop ended (a helper still streams)
        assert _alive(pid), "a streaming round is not cut by the scope's close"
        up.release.set()
        resp = await task
        assert resp.tool_calls, "the round still answered"
        assert live.live_sessions() == [], "a round of a closed scope never parks"
        assert await _gone(pid)
    finally:
        turn_scope.reset(token)
