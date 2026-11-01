"""Fit an AGENT's growing transcript to the answering model's window (v1.152.0).

Chat got a context budget in v1.146.0. Agent runs — where context actually gets
big — did not: the perceive→act loop appended the assistant turn and every tool
result on every step with no token accounting. The only guards were indirect (a
16k-char cap per tool result, a 12-step ceiling), which on a 32k local model is
40-60k tokens of transcript before the system prompt (profile + memory index +
roster + grounding) is even counted.

THIS IS A SEPARATE MODULE FROM ``budget.py``, not a parameter on
:func:`~iron_jarvis.context.budget.plan_history`, because an agent transcript
has a constraint a chat history does not: an assistant turn that requested tools
and the ``role="tool"`` messages answering it are ONE INDIVISIBLE THING. Keep
the results without the request — or the request without its results — and
strict providers reject the entire conversation (Anthropic requires every
``tool_use`` to have a matching ``tool_result``). A context fix that corrupts
the transcript is worse than the overflow it was preventing.

The other inversion: chat protects the NEWEST message, because that is the ask.
Here the ask is the OLDEST — ``messages[0]`` is the task. A transcript that
keeps the last three tool results and loses the goal produces confident work on
the wrong problem.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .budget import (
    DEFAULT_WINDOW,
    MIN_LAST_MESSAGE_CHARS,
    RECAP_RESERVE,
    effective_chars_per_token,
    estimate_tokens,
    output_reserve,
)

#: Tool results older than this many BLOCKS back are trimmed to a marker before
#: anything is dropped. A result the model has already acted on is the cheapest
#: thing in the transcript to give up, and unlike dropping a block it costs no
#: structure at all.
STALE_TOOL_BLOCKS = 2

_TOOL_TRIMMED = "[earlier tool output trimmed — it had already been acted on]"


@dataclass
class TranscriptPlan:
    """What to send this step, and an honest account of what had to go."""

    messages: list[Any] = field(default_factory=list)
    #: Blocks dropped entirely (one assistant turn + its tool results and any
    #: note that followed it, or one plain message).
    dropped_blocks: int = 0
    #: Tool results shrunk to a marker.
    tools_trimmed: int = 0
    #: Recap of the dropped work — for the SYSTEM prompt, never the transcript.
    recap: str = ""
    used_tokens: int = 0
    window: int = 0
    #: The task itself had to be clipped: this model is too small for this job.
    clipped_task: bool = False
    #: Tokens the UNTRIMMED transcript would need (system included) — the
    #: number compaction thresholds key off. ``used_tokens`` cannot serve: it is
    #: <= the window by construction and so saturates at 100%.
    #:
    #: v1.421.0: it does NOT include ``fixed_tokens`` (the tool definitions).
    #: Pressure is what a SUMMARY can relieve, and a summary cannot shrink a
    #: tool list: a run whose ~15k tokens of tools fill most of a known window
    #: would be "over the ceiling" before it took a step, buy a compaction
    #: that cannot bring it under, and have it judged futile at once — a model
    #: call for nothing. (The agent loop's compaction ceiling,
    #: ``AgentRuntime._maybe_compact``, computes its own transcript-only
    #: figure the same way, unchanged; the planner's deterministic recap
    #: covers what the tools crowd out.)
    raw_tokens: int = 0
    #: v1.421.0: the NEWEST block — the latest results the model is about to
    #: act on (or the task itself, on a first step) — could not be sent whole.
    #: A step without them cannot make progress: the model asks for the same
    #: results again, step after step, until its step limit. The agent loop
    #: ends such a run with one plain sentence instead, and only when the
    #: window is KNOWN (a guessed one never ends a run).
    lost_newest: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.dropped_blocks or self.tools_trimmed or self.clipped_task or self.lost_newest
        )


def blocks_of(messages: list[Any]) -> list[list[Any]]:
    """Group a transcript into indivisible units — see the module docstring.

    Two kinds of unit (v1.421.0):

    * an assistant turn that asked for tools and the ``role="tool"`` results
      answering it (the v1.152.0 rule — split them and strict providers reject
      the whole conversation);
    * a ``role="user"`` message after the first message JOINS THE BLOCK BEFORE
      IT. After the task, every user message in an agent transcript is a NOTE
      about the reply just before it — the closing-offer nudge (v1.402.0), the
      text-tool-call note (v1.421.0), the step-limit carry-on, a cut-off note,
      a resume note — and in a chat turn's tool rounds a steer typed while it
      worked. As a block of its own, dropping older blocks could keep
      ``[task, note]``: two user turns in a row (strict chat templates —
      Mistral, Gemma under vLLM — refuse that outright) and a note about a
      reply that is no longer there.

    So every block after the first begins with the assistant turn it belongs
    to (a stray tool message aside, which is still its own block)."""
    out: list[list[Any]] = []
    for m in messages:
        role = getattr(m, "role", "")
        if role == "user" and out:
            out[-1].append(m)
            continue
        if role == "tool" and out:
            prev = out[-1]
            if getattr(prev[0], "role", "") == "assistant" and getattr(
                prev[0], "tool_calls", None
            ):
                prev.append(m)
                continue
        out.append([m])
    return out


def _block_tokens(block: list[Any], chars_per_token: float | None = None) -> int:
    return sum(
        estimate_tokens(getattr(m, "content", "") or "", chars_per_token) + 4
        for m in block
    )


def recap_of(dropped: list[list[Any]], *, max_chars: int = 900) -> str:
    """A deterministic note about the steps that no longer fit.

    Quotes only what was actually said and names only tools that actually ran,
    so it cannot invent progress the agent never made — the worst failure here,
    since the agent reads this back as its own history and would build on it.
    """
    if not dropped:
        return ""
    lines: list[str] = []
    for block in dropped:
        head = block[0]
        text = " ".join((getattr(head, "content", "") or "").split())
        tools = [
            getattr(c, "name", "") for c in (getattr(head, "tool_calls", None) or [])
        ]
        bits: list[str] = []
        if text:
            bits.append(text[:120] + ("…" if len(text) > 120 else ""))
        if tools:
            named = ", ".join(t for t in tools if t)
            if named:
                bits.append("ran " + named)
        if bits:
            lines.append("- " + "; ".join(bits))
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars].rsplit("\n", 1)[0]
    return (
        "# Earlier steps in this run (condensed — they no longer fit this "
        "model's context window)\n" + body
    )


def plan_agent_transcript(
    messages: list[Any],
    *,
    window: int | None,
    system_text: str = "",
    chars_per_token: float | None = None,
    reserve: int | None = None,
    fixed_tokens: int = 0,
) -> TranscriptPlan:
    """Fit the transcript to *window*, sacrificing the cheapest things first.

    1. **stale tool output** (older than :data:`STALE_TOOL_BLOCKS`) → a marker.
       Already acted on; the structure survives intact.
    2. **whole blocks**, oldest first — never splitting a tool pair (nor a
       note from the reply it is about: see :func:`blocks_of`).
    3. **the task**, clipped, only if it alone will not fit.

    Returns the ORIGINAL messages untouched (a new list holding the same
    objects) when everything already fits, so a run that never approaches the
    window behaves exactly as it did before. ``lost_newest`` says when the
    newest block could not be sent whole.

    ``chars_per_token`` (v1.203.0) is the answering model's MEASURED ratio or
    None — the same parameter, with the same None-is-byte-identical contract,
    as ``budget.plan_history``'s: both lanes must count tokens IDENTICALLY, so
    it feeds every estimate here (and the token→char back-conversion of the
    task clip).

    ``reserve`` (v1.387.0): the room held back for the answer when the caller
    knows it (a local endpoint's reply budget — the same contract as
    ``budget.plan_history``'s); ``None`` = ``output_reserve(window)``.

    ``fixed_tokens`` (v1.421.0): tokens EVERY request carries beside the
    system prompt and the messages that no plan can shrink — the agent loop's
    tool definitions (a 67-tool builder sends ~15k tokens of them with each
    step). They are priced against the window like the system prompt; 0 (the
    default) is byte-identical to before. The caller passes them only when the
    window is KNOWN: counted against the 32k GUESS for an unknown model they
    would over-trim every such run. Not part of ``raw_tokens`` (see there).
    """
    win = int(window or DEFAULT_WINDOW)
    if win <= 0:
        win = DEFAULT_WINDOW
    plan = TranscriptPlan(window=win)
    if not messages:
        return plan

    cpt = chars_per_token
    fixed = max(0, int(fixed_tokens or 0))
    system_tokens = estimate_tokens(system_text, cpt)
    budget = (
        win - system_tokens - fixed
        - (output_reserve(win) if reserve is None else int(reserve))
    )

    blocks = blocks_of(list(messages))
    total = sum(_block_tokens(b, cpt) for b in blocks)
    plan.raw_tokens = system_tokens + total
    if total <= budget:
        plan.messages = list(messages)
        plan.used_tokens = system_tokens + fixed + total
        return plan

    budget -= RECAP_RESERVE  # the recap rides in the system prompt

    # (1) Trim stale tool output. Messages are COPIED before mutation: the loop
    # owns the real list and keeps appending to it, so editing in place would
    # silently rewrite the run's own history (and the DB transcript with it).
    working: list[list[Any]] = []
    for i, block in enumerate(blocks):
        stale = (len(blocks) - i) > STALE_TOOL_BLOCKS
        if not stale or len(block) == 1:
            working.append(block)
            continue
        new_block = [block[0]]
        for m in block[1:]:
            if getattr(m, "role", "") == "tool" and (getattr(m, "content", "") or ""):
                trimmed = copy.copy(m)
                trimmed.content = _TOOL_TRIMMED
                new_block.append(trimmed)
                plan.tools_trimmed += 1
            else:
                new_block.append(m)
        working.append(new_block)

    task_block, rest = working[0], working[1:]
    task_tokens = _block_tokens(task_block, cpt)

    # (3) Degenerate: not even the task fits. Keep as much of it as possible and
    # let the caller report that the model is too small for the job. The
    # token→char conversion uses the SAME ratio as the estimates — clipping at
    # 3.6 chars/token while counting at a measured 2.0 would keep a task that
    # still overflows the budget it was clipped to fit.
    if task_tokens > budget:
        first = copy.copy(task_block[0])
        room = max(
            MIN_LAST_MESSAGE_CHARS,
            int(max(0, budget) * effective_chars_per_token(cpt)),
        )
        original = getattr(first, "content", "") or ""
        first.content = original[:room]
        plan.clipped_task = len(original) > len(first.content)
        plan.messages = [first]
        plan.dropped_blocks = len(rest)
        plan.recap = recap_of(rest)
        plan.used_tokens = system_tokens + fixed + estimate_tokens(first.content, cpt)
        # v1.421.0: the newest block is gone when any block came after the
        # task (it is one of them), or — on a first step, where the task's
        # own block IS the newest — when the task was cut or a note in its
        # block left behind.
        plan.lost_newest = bool(rest) or plan.clipped_task or len(task_block) > 1
        return plan

    # (2) Keep the newest blocks that fit in what the task leaves.
    kept: list[list[Any]] = []
    used = task_tokens
    for block in reversed(rest):
        cost = _block_tokens(block, cpt)
        if used + cost > budget:
            break
        kept.append(block)
        used += cost
    kept.reverse()

    dropped = rest[: len(rest) - len(kept)]
    plan.dropped_blocks = len(dropped)
    plan.recap = recap_of(dropped)
    plan.messages = [m for block in [task_block, *kept] for m in block]
    plan.used_tokens = system_tokens + fixed + used + estimate_tokens(plan.recap, cpt)
    # v1.421.0: blocks are kept newest-first, so the newest did not fit
    # exactly when nothing after the task was kept.
    plan.lost_newest = bool(rest) and not kept
    return plan
