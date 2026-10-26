"""v1.392.0 (the long-jobs plan's Phase 3): A LONG CODING REPLY KEEPS ITS MEMORY.

When a coding reply's RAW demand passes ~90% of its model's window
(``context_compaction.auto_at``), its own model writes a summary of its older
rounds through the AGENT RUNTIME's machinery (``AgentRuntime._maybe_compact``:
the same coverage, prompt, ledger check and futility guard — reused, not
forked). The summary rides the system prompt for the rest of the reply; the
rounds it covers leave what is SENT as whole units; a path the ledger (the
session's own ``build:<id>`` calls since the reply began) does not support is
cut. The reply line and the Timeline say it happened ("Summarized N earlier
steps to fit the model"). The demo model writes none; a failed or empty
summary keeps today's trimming.

Driven through the REAL app factory; only the model is a scripted double, and
the window is a pin small enough that 300 rounds overflow it many times.
"""

from __future__ import annotations

import re

import pytest

from iron_jarvis.context import compaction as C
from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import reply_summary as RS
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    messages_of,
    new_session,
    open_turn,
    turn_frames,
)

ROUNDS = 300
GHOST = "ghost_never_written.py"


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


class LongReply:
    """A scripted model: ``rounds`` rounds, each writing ONE new file
    (``f<i>.py``) with a line of words, then "All written.". Its summarizer
    (a tool-less ``complete`` with the compaction prompt) names every path it
    is shown — and one more, :data:`GHOST`, which no record supports."""

    def __init__(self, app, rounds: int = ROUNDS, *, who=CLAUDE, summary: str | None = None,
                 summary_by: tuple[str, str] | None = None) -> None:
        self.app = app
        self.provider, self.model = who
        self.rounds = rounds
        self.i = 0
        self.calls: list[dict] = []
        self.summaries: list[dict] = []
        self.summary = summary
        #: Who the router says answered the summarizer (default: the reply's
        #: own model) — ("mock", "mock") is the demo model standing in.
        self.summary_by = summary_by
        self._pinned = False

    def _pin(self, system: str) -> None:
        if self._pinned:
            return
        self._pinned = True
        # Room for the harness prompt and ~60 rounds: 300 rounds overflow it
        # several times over.
        window = estimate_tokens(system) + 3000
        self.app.state.platform.config.model_context_windows = {
            f"{self.provider}::{self.model}": window,
        }

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None,
                     task_class=None, **kw):
        self._pin(system)
        self.calls.append({"system": system, "messages": list(messages)})
        if tools and self.i < self.rounds:
            n = self.i
            self.i += 1
            text = f"Round {n}: writing the next module now."
            resp = LLMResponse(
                text=text,
                tool_calls=[ToolCall(id=f"w{n}", name="Write",
                                     arguments={"file_path": f"f{n}.py", "content": f"x = {n}\n"})],
                usage={"input_tokens": 3, "output_tokens": 1},
            )
            yield {"type": "text", "text": text}
        else:
            resp = LLMResponse(text="All written.", usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "text", "text": "All written."}
        yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None,
                       task_class=None, **kw):
        user = messages[-1].content if messages else ""
        if system.startswith(C.COMPACT_PROMPT[:40]):
            seen = sorted(set(re.findall(r"\bf\d+\.py\b", user)), key=lambda p: int(p[1:-3]))
            if self.summary is not None:
                text = self.summary
            else:
                text = "GOAL: write the modules.\nDONE:\n" + "\n".join(f"- Wrote {p}" for p in seen)
                text += f"\n- Wrote {GHOST}"
            self.summaries.append({"paths": seen, "text": text, "prior": "EARLIER SUMMARY" in user,
                                   "at_call": len(self.calls)})
            by = self.summary_by or (self.provider, self.model)
            return RouteResult(LLMResponse(text=text, usage={"input_tokens": 50, "output_tokens": 20}), *by)
        return RouteResult(LLMResponse(text="Done.", usage={"input_tokens": 3, "output_tokens": 1}),
                           self.provider, self.model)


def _wire(app, script: LongReply) -> LongReply:
    app.state.platform.router.stream = script.stream
    app.state.platform.router.complete = script.complete
    return script


async def _run(app, sid: str, turn: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": "write the modules", "turn_id": turn}, timeout=600)
    assert status == 200, raw
    frames = turn_frames(app, turn)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


@pytest.mark.timeout(900)
async def test_a_300_round_reply_keeps_every_path_it_touched_and_cuts_one_no_record_supports(app, work):
    script = _wire(app, LongReply(app))
    sid = new_session(app, work, approval_mode="yolo")["id"]
    frames = await _run(app, sid, "bt-long")
    done = dict(frames)["done"]
    assert done["reply"] == "All written."
    assert len([p for p in work.iterdir() if re.fullmatch(r"f\d+\.py", p.name)]) == ROUNDS
    # The reply summarized itself — more than once over 300 rounds — each
    # time from the beginning, with the earlier summary as `prior`.
    assert len(script.summaries) >= 2, "a 300-round reply never summarized itself"
    assert any(s["prior"] for s in script.summaries[1:])
    said = [d for e, d in frames if e == RS.SUMMARY_FRAME]
    assert len(said) == len(script.summaries)
    # What the model was sent at its LAST call: the summary in the system
    # prompt, the newest rounds whole after the question.
    last = script.calls[-1]
    assert "# Earlier in this conversation (compacted" in last["system"]
    sent_text = "\n".join(str(getattr(m, "content", "") or "") for m in last["messages"])
    for n in range(ROUNDS):
        path = f"f{n}.py"
        assert path in last["system"] or path in sent_text, f"{path} fell out of the reply's memory"
    # The path no record supports was cut from what the model reads.
    assert GHOST not in last["system"]
    # The covered rounds left as whole units: every tool result sent still
    # follows the assistant turn that asked for it.
    msgs = last["messages"]
    for i, m in enumerate(msgs):
        if getattr(m, "role", "") == "tool":
            asked = [p for p in msgs[:i] if getattr(p, "role", "") == "assistant"
                     and any(getattr(c, "id", "") == m.tool_call_id for c in (getattr(p, "tool_calls", None) or []))]
            assert asked, "a tool result was sent without the call it answers"
    # The reply line says it happened; the Timeline drew the summary's call.
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    assert line["summarized"]["steps"] == said[-1]["steps"] and line["summarized"]["times"] == len(said)
    assert said[-1]["words"] == f"Summarized {said[-1]['steps']} earlier steps to fit the model"
    summary_calls = [c for c in line["calls"] if c.get("kind") == "summary"]
    assert len(summary_calls) == len(script.summaries)
    assert summary_calls[-1]["summarized"] == said[-1]["steps"]
    # The summary's own use is on the reply's receipt — exactly: every call
    # of the reply (3 tokens in, 1 out) and every summary (50 in, 20 out).
    assert done["usage"]["input_tokens"] == 3 * len(script.calls) + 50 * len(script.summaries)
    assert done["usage"]["output_tokens"] == len(script.calls) + 20 * len(script.summaries)
    # ...and its cost: the Timeline's calls (the summaries' among them) add up
    # to the receipt's cost.
    assert done["usage"]["cost_usd"] > 0
    assert sum(c["usage"]["cost_usd"] for c in line["calls"]) == pytest.approx(done["usage"]["cost_usd"], abs=2e-6)
    # Nothing it summarized was lost from the reply's own record: the line's
    # steps are every round's.
    assert len([s for s in line["steps"] if s["name"] == "write_file"]) == ROUNDS


async def test_the_summary_is_checked_against_this_replys_own_ledger(app, work, monkeypatch):
    """The record that may back the summary is the SESSION's ledger
    (``build:<id>`` — a coding session has no Session row for the agent
    runtime's own reader) limited to what THIS reply did: a file an earlier
    reply wrote is not this reply's evidence."""
    seen: list[set[str]] = []
    real = C.compact_messages

    async def spy(covered, **kw):
        seen.append(set(kw.get("ledger_paths") or set()))
        return await real(covered, **kw)

    monkeypatch.setattr(C, "compact_messages", spy)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    # An earlier reply of the same session wrote old.py.
    app.state.platform.router.stream = _OneWrite("old.py").stream
    status, raw = await open_turn(app, sid, {"text": "write old.py", "turn_id": "bt-old"}, timeout=120)
    assert status == 200 and (work / "old.py").exists()
    _wire(app, LongReply(app, rounds=150))
    await _run(app, sid, "bt-ledger")
    assert seen, "the reply never summarized itself"
    last = seen[-1]
    assert any(p.replace("\\", "/").endswith("/f0.py") or p == "f0.py" for p in last), sorted(last)[:5]
    assert not any(p.replace("\\", "/").endswith("old.py") for p in last), "an earlier reply's file backed this summary"


class _OneWrite:
    def __init__(self, name: str) -> None:
        self.name = name
        self.done = False

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None, **kw):
        if tools and not self.done:
            self.done = True
            resp = LLMResponse(text="", tool_calls=[ToolCall(id="o1", name="Write",
                                                            arguments={"file_path": self.name, "content": "1\n"})],
                               usage={"input_tokens": 3, "output_tokens": 1})
        else:
            resp = LLMResponse(text="Written.", usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "text", "text": "Written."}
        yield {"type": "final", "response": resp, "provider": CLAUDE[0], "model": CLAUDE[1]}


async def test_the_demo_model_writes_no_summary(app, work):
    script = _wire(app, LongReply(app, rounds=120, who=("mock", "mock")))
    sid = new_session(app, work, approval_mode="yolo", provider="mock", model="mock")["id"]
    frames = await _run(app, sid, "bt-demo")
    assert script.summaries == []
    assert [e for e, _ in frames if e == RS.SUMMARY_FRAME] == []
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    assert "summarized" not in line


async def test_an_empty_summary_keeps_todays_trimming_and_waits_before_trying_again(app, work):
    script = _wire(app, LongReply(app, rounds=120, summary=""))
    sid = new_session(app, work, approval_mode="yolo")["id"]
    frames = await _run(app, sid, "bt-empty")
    assert dict(frames)["done"]["reply"] == "All written."
    assert [e for e, _ in frames if e == RS.SUMMARY_FRAME] == []
    assert "# Earlier in this conversation (compacted" not in script.calls[-1]["system"]
    # A failed try is not repeated round after round (each is a model call
    # on the user's plan): the next waits RETRY_ROUNDS rounds.
    tries = [s["at_call"] for s in script.summaries]
    assert len(tries) >= 2, f"the reply never tried again: {tries}"
    gaps = [b - a for a, b in zip(tries, tries[1:])]
    assert min(gaps) >= RS.RETRY_ROUNDS, gaps


async def test_a_summary_the_demo_model_answered_is_never_kept(app, work):
    """The reply runs on a real model, but the router served the summarizer
    with the demo model: its text is not a summary (the honest-mock rule,
    after the call)."""
    script = _wire(app, LongReply(app, rounds=120, summary_by=("mock", "mock")))
    sid = new_session(app, work, approval_mode="yolo")["id"]
    frames = await _run(app, sid, "bt-demo-route")
    assert script.summaries, "the summarizer was never asked"
    assert [e for e, _ in frames if e == RS.SUMMARY_FRAME] == []
    assert "# Earlier in this conversation (compacted" not in script.calls[-1]["system"]
