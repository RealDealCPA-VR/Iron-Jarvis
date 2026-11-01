"""v1.421.0 (Wave 1, part C review) — the agent transcript planner keeps a
NOTE with the reply it is about, and prices the tool definitions against a
KNOWN window.

THE NOTES. ``blocks_of`` (``context/agent_window``) grouped an assistant turn
with its ``role="tool"`` results, but a plain answer followed by a role=user
NOTE — the closing-offer nudge (v1.402.0), the text-tool-call note (v1.421.0),
the step-limit carry-on, a cut-off note, a resume note; in a chat turn's tool
rounds a steer — made two blocks. Dropping old blocks could then keep
``[task, note]``: two user turns in a row (strict chat templates — Mistral,
Gemma under vLLM — refuse it) and a note about a reply that is gone. Now every
user message after the first joins the block before it, so a note travels with
its reply or not at all. The fix only bites now that a local model's REAL
window is known (D3: Ollama 4,096; mid-size servers 44k-70k).

THE TOOLS. Every agent step sends ``tool_specs`` (~15k tokens for a builder's
67 tools) beside the system prompt and the messages, and the planner priced
only the latter two. ``fixed_tokens`` prices them; 0 is byte-identical, and
they never count toward compaction pressure (``raw_tokens``) — a summary
cannot shrink a tool list.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.agents.runtime import _effective
from iron_jarvis.context import compaction as C
from iron_jarvis.context.agent_window import blocks_of, plan_agent_transcript
from iron_jarvis.harness.closing_offer import CLOSING_OFFER_NUDGE
from iron_jarvis.harness.mode import TEXT_TOOL_CALL_NUDGE
from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall

CUT_NOTE = "Your last reply was cut off at the output limit; nothing from it was run."
RESUME_NOTE = "You were stopped by a restart. Carry on from where you were."


def _task(text: str = "do the thing") -> LLMMessage:
    return LLMMessage(role="user", content=text)


def _tool_step(i: int, *, say_chars: int, out_chars: int) -> list[LLMMessage]:
    call = ToolCall(id=f"call_{i}", name="read_file", arguments={"path": f"f{i}"})
    return [
        LLMMessage(role="assistant", content=("reading file %d " % i) * (say_chars // 16 + 1),
                   tool_calls=[call]),
        LLMMessage(role="tool", tool_call_id=f"call_{i}", name="read_file", content="r" * out_chars),
    ]


def _noted_transcript(rounds: int = 16) -> list[LLMMessage]:
    """A run in which every kind of note follows the reply it is about:
    a plain answer + the closing-offer nudge; a tool step + a cut-off note; a
    call written as text + the text-tool-call note; a plain step; a tool step
    + a resume note. Assistant turns carry real reasoning — with a 6-token
    "working" turn a boundary never lands between a reply and its note."""
    msgs = [_task()]
    for i in range(rounds):
        kind = i % 5
        if kind == 0:
            msgs.append(LLMMessage(role="assistant", content=("Here is the answer %d. " % i) * 60))
            msgs.append(LLMMessage(role="user", content=CLOSING_OFFER_NUDGE))
        elif kind == 1:
            msgs.extend(_tool_step(i, say_chars=700, out_chars=900))
            msgs.append(LLMMessage(role="user", content=CUT_NOTE))
        elif kind == 2:
            msgs.append(LLMMessage(
                role="assistant",
                content="<tool_call>read_file\n<arg_key>path</arg_key>\n<arg_value>x</arg_value>\n</tool_call> " * 20,
            ))
            msgs.append(LLMMessage(role="user", content=TEXT_TOOL_CALL_NUDGE))
        elif kind == 3:
            msgs.extend(_tool_step(i, say_chars=900, out_chars=1200))
        else:
            msgs.extend(_tool_step(i, say_chars=600, out_chars=800))
            msgs.append(LLMMessage(role="user", content=RESUME_NOTE))
    return msgs


def _key(m) -> object:
    """A message's identity across the planner's copies: a trimmed tool
    result is a COPY (the loop owns the real list), so it is matched by the
    call it answers; everything else by object identity."""
    if getattr(m, "role", "") == "tool":
        return ("tool", m.tool_call_id)
    return id(m)


# --------------------------------------------------------------------------- #
# (1) a note never leaves the reply it is about
# --------------------------------------------------------------------------- #


def test_a_note_joins_the_block_of_the_reply_before_it():
    task = _task()
    plain = LLMMessage(role="assistant", content="the answer")
    offer = LLMMessage(role="user", content=CLOSING_OFFER_NUDGE)
    call = ToolCall(id="c1", name="read_file", arguments={})
    asked = LLMMessage(role="assistant", content="reading", tool_calls=[call])
    result = LLMMessage(role="tool", tool_call_id="c1", name="read_file", content="r")
    cut = LLMMessage(role="user", content=CUT_NOTE)
    blocks = blocks_of([task, plain, offer, asked, result, cut])
    assert [[id(m) for m in b] for b in blocks] == [
        [id(task)], [id(plain), id(offer)], [id(asked), id(result), id(cut)],
    ]
    # Every block after the task begins with the assistant turn it belongs to.
    assert all(b[0].role == "assistant" for b in blocks[1:])


def test_the_first_message_still_starts_its_own_block():
    """A lone user message opens the transcript's first block (the task in an
    agent run; the first covered message in a compaction's coverage)."""
    only = _task()
    assert blocks_of([only]) == [[only]]
    # A stray tool message with nothing before it is still its own block.
    stray = LLMMessage(role="tool", tool_call_id="x", content="stray")
    assert [len(b) for b in blocks_of([stray, _task()])] == [2]


@pytest.mark.parametrize("window", [6_000, 8_000, *range(1_500, 14_000, 211)])
def test_at_small_windows_no_two_user_turns_and_no_orphaned_note(window):
    """The fine sweep is what makes this able to fail: a keep/drop boundary
    has to land between a reply and its note, and a handful of round numbers
    can miss every such boundary (the v1.152.0 pairing test's lesson)."""
    msgs = _noted_transcript()
    position = {_key(m): i for i, m in enumerate(msgs)}
    plan = plan_agent_transcript(msgs, window=window)
    sent = plan.messages
    roles = [m.role for m in sent]
    for a, b in zip(roles, roles[1:]):
        assert not (a == "user" and b == "user"), (
            f"two user turns in a row at window={window}: {roles}"
        )
    for k, m in enumerate(sent):
        if k == 0 or m.role != "user":
            continue
        original = position[_key(m)]
        # The note is preceded by exactly what preceded it in the run.
        assert _key(sent[k - 1]) == _key(msgs[original - 1]), (
            f"a note lost the reply it is about at window={window}"
        )
    # …and the v1.152.0 unit rule still holds.
    requested = {c.id for m in sent for c in (m.tool_calls or []) if m.role == "assistant"}
    answered = {m.tool_call_id for m in sent if m.role == "tool"}
    assert answered <= requested


def test_the_sweep_really_drops_blocks():
    """The sweep above is only meaningful if the planner is dropping blocks
    across it (else it checks a transcript nothing touched)."""
    msgs = _noted_transcript()
    assert plan_agent_transcript(msgs, window=6_000).dropped_blocks > 0
    assert plan_agent_transcript(msgs, window=8_000).dropped_blocks > 0


def test_a_transcript_that_fits_is_sent_unchanged():
    msgs = _noted_transcript(rounds=5)
    plan = plan_agent_transcript(msgs, window=200_000, system_text="sys")
    assert plan.changed is False and plan.recap == "" and plan.lost_newest is False
    assert len(plan.messages) == len(msgs)
    assert all(a is b for a, b in zip(plan.messages, msgs))


def test_compaction_coverage_never_leaves_a_note_at_the_front():
    """``agent_coverage`` cuts on ``blocks_of``'s boundaries, so what the run
    sends after a compaction is ``[task, <an assistant turn>, …]`` — never the
    task followed by a note about a covered reply."""
    msgs = _noted_transcript()
    pairs, covers = C.agent_coverage(msgs, covered=0)
    assert covers > 0
    eff, _system = _effective(msgs, "sys", "summary", covers)
    assert eff[0] is msgs[0] and eff[1].role == "assistant"


# --------------------------------------------------------------------------- #
# (2) the newest block: kept whole, or said so
# --------------------------------------------------------------------------- #


def test_a_newest_block_that_cannot_fit_is_reported_lost():
    msgs = [_task(), *_tool_step(0, say_chars=200, out_chars=200),
            *_tool_step(1, say_chars=200, out_chars=40_000)]
    plan = plan_agent_transcript(msgs, window=8_000)
    assert plan.lost_newest is True
    assert plan.changed is True


def test_a_newest_block_that_fits_is_not_reported_lost():
    msgs = [_task()]
    for i in range(12):
        msgs.extend(_tool_step(i, say_chars=2_400, out_chars=1_500))
    plan = plan_agent_transcript(msgs, window=8_000)
    assert plan.dropped_blocks > 0  # the planner worked for this
    assert plan.lost_newest is False
    assert plan.messages[-1] is msgs[-1] and plan.messages[-2] is msgs[-2]


def test_a_clipped_task_loses_the_newest_block_too():
    plan = plan_agent_transcript([_task("z" * 100_000)], window=1_200)
    assert plan.clipped_task is True and plan.lost_newest is True


# --------------------------------------------------------------------------- #
# (3) the tool definitions are priced — and only what the caller says
# --------------------------------------------------------------------------- #


def _tool_heavy(rounds: int = 10, say_chars: int = 2_000) -> list[LLMMessage]:
    msgs = [_task()]
    for i in range(rounds):
        msgs.extend(_tool_step(i, say_chars=say_chars, out_chars=1_400))
    return msgs


def test_tool_definitions_leave_less_room_for_the_transcript():
    msgs = _tool_heavy()
    plain = plan_agent_transcript(msgs, window=12_000)
    with_tools = plan_agent_transcript(msgs, window=12_000, fixed_tokens=4_000)
    kept = lambda p: sum(1 for m in p.messages if m.role == "assistant")  # noqa: E731
    assert kept(with_tools) < kept(plain), (kept(with_tools), kept(plain))
    assert with_tools.used_tokens <= 12_000
    # Compaction pressure is the TRANSCRIPT's: a summary cannot shrink tools.
    assert with_tools.raw_tokens == plain.raw_tokens


def test_tools_alone_can_tip_a_fitting_transcript_into_trimming():
    msgs = _tool_heavy(rounds=4, say_chars=1_000)
    assert plan_agent_transcript(msgs, window=16_000).changed is False
    assert plan_agent_transcript(msgs, window=16_000, fixed_tokens=10_500).changed is True


def test_zero_fixed_tokens_is_byte_identical_to_the_old_call():
    msgs = _tool_heavy()
    for window in (3_000, 9_000, 30_000):
        old = plan_agent_transcript(msgs, window=window, system_text="s" * 900)
        new = plan_agent_transcript(msgs, window=window, system_text="s" * 900, fixed_tokens=0)
        assert [m.content for m in new.messages] == [m.content for m in old.messages]
        assert (new.dropped_blocks, new.tools_trimmed, new.recap, new.used_tokens,
                new.raw_tokens, new.clipped_task) == (
                old.dropped_blocks, old.tools_trimmed, old.recap, old.used_tokens,
                old.raw_tokens, old.clipped_task)


# --------------------------------------------------------------------------- #
# (4) the chat lanes' in-turn fitter rides the same blocks
# --------------------------------------------------------------------------- #


def _steered_turn(tmp_path, *, last_out_chars: int):
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path / "home"))
    p.config.model_context_windows = {"mock": 9_000}
    question = LLMMessage(role="user", content="summarise these pages")
    tail: list[LLMMessage] = []
    for i in range(8):
        out = last_out_chars if i == 7 else 3_000
        tail.extend(_tool_step(i, say_chars=800, out_chars=out))
    steer = LLMMessage(role="user", content="also note the dates")
    tail.append(steer)
    return SimpleNamespace(platform=p), [question, *tail], steer


def _no_two_users(sent) -> bool:
    roles = [m.role for m in sent]
    return all(not (a == "user" and b == "user") for a, b in zip(roles, roles[1:]))


def test_the_chat_fitter_keeps_a_steer_right_after_its_round(tmp_path):
    """``_fit_turn_transcript`` (both chat lanes) rides ``blocks_of``: a steer
    typed while the turn worked is part of the newest block, so a fitted
    transcript carries the round and its steer together."""
    from iron_jarvis.daemon.chat_turn import _fit_turn_transcript

    d, msgs, steer = _steered_turn(tmp_path, last_out_chars=3_000)
    sent, _system = _fit_turn_transcript(d, msgs, "sys", "mock", "", head=1)
    assert sent is not msgs, "this transcript must be fitted"
    assert _no_two_users(sent)
    assert sent[-1] is steer and sent[-2] is msgs[-2] and sent[-3] is msgs[-3]


def test_the_chat_fitter_never_sends_the_question_then_the_steer(tmp_path):
    """The newest ROUND does not fit (its results are huge), the steer alone
    would. As its own block the steer was kept and the round dropped:
    ``[question, steer]`` — two user turns in a row, and a steer about
    results the model never sees. Now the newest block is the round WITH its
    steer, it cannot arrive intact, and the lane sends what it sent before
    (the v1.287.0 rule: never a smaller-but-wrong transcript)."""
    from iron_jarvis.daemon.chat_turn import _fit_turn_transcript

    d, msgs, steer = _steered_turn(tmp_path, last_out_chars=40_000)
    sent, _system = _fit_turn_transcript(d, msgs, "sys", "mock", "", head=1)
    assert _no_two_users(sent), [m.role for m in sent]
    assert sent[-1] is steer and sent[-2] is msgs[-2]
