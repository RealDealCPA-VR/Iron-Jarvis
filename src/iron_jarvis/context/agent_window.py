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
    CHARS_PER_TOKEN,
    DEFAULT_WINDOW,
    MIN_LAST_MESSAGE_CHARS,
    RECAP_RESERVE,
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
    #: Blocks dropped entirely (one assistant turn + its tool results, or one
    #: plain message).
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
    raw_tokens: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.dropped_blocks or self.tools_trimmed or self.clipped_task)


def blocks_of(messages: list[Any]) -> list[list[Any]]:
    """Group a transcript into indivisible units — see the module docstring."""
    out: list[list[Any]] = []
    for m in messages:
        if getattr(m, "role", "") == "tool" and out:
            prev = out[-1]
            if getattr(prev[0], "role", "") == "assistant" and getattr(
                prev[0], "tool_calls", None
            ):
                prev.append(m)
                continue
        out.append([m])
    return out


def _block_tokens(block: list[Any]) -> int:
    return sum(estimate_tokens(getattr(m, "content", "") or "") + 4 for m in block)


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
) -> TranscriptPlan:
    """Fit the transcript to *window*, sacrificing the cheapest things first.

    1. **stale tool output** (older than :data:`STALE_TOOL_BLOCKS`) → a marker.
       Already acted on; the structure survives intact.
    2. **whole blocks**, oldest first — never splitting a tool pair.
    3. **the task**, clipped, only if it alone will not fit.

    Returns the ORIGINAL list untouched when everything already fits, so a run
    that never approaches the window behaves exactly as it did before.
    """
    win = int(window or DEFAULT_WINDOW)
    if win <= 0:
        win = DEFAULT_WINDOW
    plan = TranscriptPlan(window=win)
    if not messages:
        return plan

    system_tokens = estimate_tokens(system_text)
    budget = win - system_tokens - output_reserve(win)

    blocks = blocks_of(list(messages))
    total = sum(_block_tokens(b) for b in blocks)
    plan.raw_tokens = system_tokens + total
    if total <= budget:
        plan.messages = list(messages)
        plan.used_tokens = system_tokens + total
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
    task_tokens = _block_tokens(task_block)

    # (3) Degenerate: not even the task fits. Keep as much of it as possible and
    # let the caller report that the model is too small for the job.
    if task_tokens > budget:
        first = copy.copy(task_block[0])
        room = max(MIN_LAST_MESSAGE_CHARS, int(max(0, budget) * CHARS_PER_TOKEN))
        original = getattr(first, "content", "") or ""
        first.content = original[:room]
        plan.clipped_task = len(original) > len(first.content)
        plan.messages = [first]
        plan.dropped_blocks = len(rest)
        plan.recap = recap_of(rest)
        plan.used_tokens = system_tokens + estimate_tokens(first.content)
        return plan

    # (2) Keep the newest blocks that fit in what the task leaves.
    kept: list[list[Any]] = []
    used = task_tokens
    for block in reversed(rest):
        cost = _block_tokens(block)
        if used + cost > budget:
            break
        kept.append(block)
        used += cost
    kept.reverse()

    dropped = rest[: len(rest) - len(kept)]
    plan.dropped_blocks = len(dropped)
    plan.recap = recap_of(dropped)
    plan.messages = [m for block in [task_block, *kept] for m in block]
    plan.used_tokens = system_tokens + used + estimate_tokens(plan.recap)
    return plan
