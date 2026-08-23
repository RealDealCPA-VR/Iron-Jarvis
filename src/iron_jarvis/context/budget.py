"""Context-window protection (v1.146.0) — pure, offline, no model calls.

The defect this replaces was one line: ``for m in body.messages[-30:]``. Thirty
messages is not a size, it is a count. Thirty long turns plus a system prompt
carrying the profile, the memory index, project knowledge, and an attachment
overflows an 8k local window; thirty short ones waste a 200k cloud one. Either
way nothing in the app knew how close it was, so the failure surfaced as the
model erroring, truncating, or — on some local servers — appearing to hang.

:func:`plan_history` decides what actually goes into a turn, against the window
of the model that will answer it. It is deterministic and offline BY DESIGN:
the alternative, asking a model to summarize the conversation before answering,
doubles the latency and the spend of every long turn and can hallucinate the
recap it hands to the next call. A deterministic recap is smaller, free,
instant, and cannot invent a fact — and it is honest about being a recap.

THE LADDER, in the order the brief asked for it:

1. **Reserve room** for the system prompt and the reply itself.
2. **Trim stale tool output** — results from older rounds are the largest and
   least useful thing in a transcript, so they shrink to a marker first.
3. **Keep the newest turns** that fit, walking backwards.
4. **Summarize what fell off** into a compact recap line so the model knows the
   conversation did not begin where its context does.
5. **Clip the final message** only as a last resort, saying so out loud.
6. Report ``suggest_larger`` so the caller can offer a bigger-window model.

What it deliberately does NOT do: switch models on its own. A silent swap
breaks ``strict_model_pin``, changes the price of a turn, and is exactly the
kind of "helpful" surprise this app avoids. The caller gets the signal and
tells the user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: Characters per token for ordinary prose in a Latin script. Real tokenizers
#: land near 4; code and JSON land nearer 3. We deliberately assume the DENSER
#: figure, because being wrong in that direction wastes a little window, and
#: being wrong in the other direction is the overflow this module exists to
#: prevent.
CHARS_PER_TOKEN = 3.6

#: CJK text is roughly one token per character — a 4x error if estimated as
#: Latin prose, which on a 8k window is the difference between fitting and not.
CJK_CHARS_PER_TOKEN = 1.1

#: Defensive bounds on a MEASURED chars-per-token ratio (v1.203.0, IronCore
#: Wave C5). The TOKEN-RATIO probe clamps to a sane range before persisting,
#: but the value crosses a JSON store on disk between the probe and this
#: divisor: a corrupt or hand-edited 0.0 must not zero every history budget
#: (dividing by ~0 makes any transcript "cost" millions of tokens), and an
#: absurd 1e9 must not make a 200k-char transcript "fit" an 8k window — the
#: exact overflow this module exists to prevent.
MEASURED_RATIO_MIN = 1.0
MEASURED_RATIO_MAX = 8.0

#: Share of the window kept free for the REPLY. A turn that fills the window
#: with input has nowhere to put an answer; local servers differ on whether
#: that is an error or a silent truncation, and neither is acceptable.
OUTPUT_RESERVE_RATIO = 0.25
OUTPUT_RESERVE_MIN = 512
OUTPUT_RESERVE_MAX = 4096

#: Assumed window when nothing is known (no pin, no probe). The old blind
#: slice behaved roughly like this, so an unknown model keeps today's shape.
DEFAULT_WINDOW = 32_000

#: Never trim below this many characters of the newest user message: an answer
#: to a mangled question is worse than an honest "this does not fit".
MIN_LAST_MESSAGE_CHARS = 400

#: Per-message hard cap, unchanged from the pre-v1.146.0 slice.
MAX_MESSAGE_CHARS = 12_000

#: Tool results from rounds older than this many messages back are replaced by
#: a marker. Two rounds is what a follow-up question realistically refers to.
STALE_TOOL_AFTER = 4

#: Room held back for the recap when the history will NOT fit.
#:
#: The recap is appended to the SYSTEM prompt after the history is chosen, so
#: without this reservation it lands OUTSIDE the budget it was created by — the
#: plan then overflows by exactly the size of the summary written to prevent
#: overflow. (Caught by
#: ``test_context_budget_v1146.py::test_the_plan_actually_fits_the_window``,
#: which is why that test asserts the arithmetic rather than the behaviour.)
#: Sized to ``build_recap``'s own cap plus its header, so it is a ceiling and
#: not an estimate. Charged ONLY when something is actually dropped.
RECAP_RESERVE = 300

_TOOL_TRIMMED = "[earlier tool output trimmed to fit the context window]"

_CJK_RANGES = (
    (0x3040, 0x30FF),   # kana
    (0x3400, 0x4DBF),   # CJK ext A
    (0x4E00, 0x9FFF),   # CJK unified
    (0xAC00, 0xD7AF),   # hangul
    (0xF900, 0xFAFF),   # compatibility
)


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def effective_chars_per_token(chars_per_token: float | None) -> float:
    """The divisor :func:`estimate_tokens` actually uses for NON-CJK text.

    ``None`` → :data:`CHARS_PER_TOKEN` EXACTLY — the pinned pre-v1.203.0
    constant, so every unmeasured route estimates byte-identically to before.
    A measured value is clamped to
    [:data:`MEASURED_RATIO_MIN`, :data:`MEASURED_RATIO_MAX`]; anything that is
    not a finite number (a corrupt store row, a stub handing back a string)
    falls back to the default rather than poisoning a budget.
    """
    if chars_per_token is None:
        return CHARS_PER_TOKEN
    try:
        ratio = float(chars_per_token)
    except (TypeError, ValueError):
        return CHARS_PER_TOKEN
    if not math.isfinite(ratio):
        return CHARS_PER_TOKEN
    return max(MEASURED_RATIO_MIN, min(MEASURED_RATIO_MAX, ratio))


def estimate_tokens(text: str, chars_per_token: float | None = None) -> int:
    """A deliberately CONSERVATIVE token estimate for *text*.

    Not a tokenizer: shipping one would mean a per-provider vocabulary
    download in a frozen desktop app, for a number whose only job is to decide
    what to leave out. Overestimating slightly is the safe direction — see
    :data:`CHARS_PER_TOKEN`.

    ``chars_per_token`` (v1.203.0) is the answering model's MEASURED ratio —
    the capability envelope's TOKEN-RATIO probe → ``profile.chars_per_token``,
    provenance-gated by ``field_measured("chars_per_token")`` at the call
    site. It replaces ONLY the Latin-ish divisor: the probe measures
    latin-filler documents against server-reported prompt tokens, so it says
    nothing about CJK density — the CJK constant keeps that 4x error covered
    exactly as before. ``None`` (every unmeasured route) is byte-identical to
    the pre-v1.203.0 estimate.
    """
    if not text:
        return 0
    ratio = effective_chars_per_token(chars_per_token)
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return int(other / ratio + cjk / CJK_CHARS_PER_TOKEN) + 1


def output_reserve(window: int) -> int:
    """Tokens held back for the reply."""
    return int(
        max(OUTPUT_RESERVE_MIN, min(OUTPUT_RESERVE_MAX, window * OUTPUT_RESERVE_RATIO))
    )


@dataclass
class HistoryPlan:
    """What to send, and an honest account of what was left out."""

    #: ``[{role, content}]`` — the messages to send, oldest first.
    messages: list[dict[str, str]] = field(default_factory=list)
    #: Messages dropped entirely (they live on in ``recap``).
    dropped: int = 0
    #: Older tool results replaced by a marker.
    tools_trimmed: int = 0
    #: The deterministic summary of what fell off ("" when nothing did).
    recap: str = ""
    #: The newest message had to be clipped — the honest last resort.
    clipped_last: bool = False
    #: Estimated tokens of everything going to the model (system included).
    used_tokens: int = 0
    #: The window this was planned against.
    window: int = 0
    #: Tokens available for history after the system prompt + output reserve.
    history_budget: int = 0
    #: True when even the newest turn had to be cut — the caller should offer a
    #: larger-context model.
    suggest_larger: bool = False
    #: Tokens the UNTRIMMED conversation would need (system included).
    #:
    #: The number the v1.153.0 compaction thresholds key off, and it exists
    #: because ``used_tokens`` cannot answer the question: a planned transcript
    #: is <= the window BY CONSTRUCTION, so ``used_tokens/window`` saturates at
    #: 1.0 and can never report the 70%-full signal — still less the 130% that
    #: says a conversation has outgrown its model.
    raw_tokens: int = 0

    @property
    def headroom(self) -> int:
        return max(0, self.window - self.used_tokens)

    def as_dict(self) -> dict[str, Any]:
        """The wire shape (`context` on the chat response + the SSE done frame)."""
        return {
            "window": self.window,
            "used": self.used_tokens,
            "headroom": self.headroom,
            "dropped": self.dropped,
            "tools_trimmed": self.tools_trimmed,
            "clipped": self.clipped_last,
            "recap": bool(self.recap),
            "suggest_larger": self.suggest_larger,
        }

    def note(self) -> str:
        """A one-line honest note for the reply, or "" when nothing was cut.

        Silence here would be the real failure: a user whose earlier turns
        quietly stopped being visible should be told, not left wondering why
        the assistant forgot.
        """
        parts: list[str] = []
        if self.dropped:
            parts.append(
                f"{self.dropped} earlier message{'s' if self.dropped != 1 else ''} "
                f"summarized to fit this model's context window"
            )
        if self.tools_trimmed:
            parts.append(f"{self.tools_trimmed} older tool result(s) trimmed")
        if self.clipped_last:
            parts.append("your latest message was too long for this model and was clipped")
        if not parts:
            return ""
        tail = (
            " — a model with a larger context window would fit it all"
            if self.suggest_larger
            else ""
        )
        return "; ".join(parts) + tail


def _role_of(m: Any) -> str:
    role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "")
    return str(role or "user")


def _content_of(m: Any) -> str:
    c = getattr(m, "content", None)
    if c is None and isinstance(m, dict):
        c = m.get("content")
    return str(c or "")


def build_recap(dropped: list[Any], *, max_chars: int = 900) -> str:
    """A deterministic summary of the messages that did not fit.

    One line per dropped turn, oldest first, clipped hard. It cannot invent
    anything because it only ever quotes the opening of a message the user
    actually sent — which is also why it is labelled as a partial record rather
    than presented as the conversation itself.
    """
    if not dropped:
        return ""
    lines: list[str] = []
    for m in dropped:
        role = "You" if _role_of(m) == "user" else "Iron Jarvis"
        text = " ".join(_content_of(m).split())
        if not text:
            continue
        lines.append(f"- {role}: {text[:160]}{'…' if len(text) > 160 else ''}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars].rsplit("\n", 1)[0]
    return (
        "# Earlier in this conversation (condensed — the full text no longer "
        "fits this model's context window)\n" + body
    )


#: Upper bound on messages considered, whatever the window says.
#:
#: This used to be 30 and it used to be the ONLY limit — a count standing in for
#: a size. Now that the real constraint is measured in tokens, the count's only
#: remaining job is to stop a pathological replay (a client sending back a
#: thousand turns) from costing a thousand token estimates. 60 is deliberately
#: above what any window under ~32k will actually admit, so on a small model the
#: BUDGET decides — while a large-window model now keeps twice the conversation
#: it used to, which is the whole point of measuring instead of guessing.
MAX_MESSAGES = 60


def plan_history(
    messages: list[Any],
    *,
    window: int | None,
    system_text: str = "",
    max_messages: int = MAX_MESSAGES,
    chars_per_token: float | None = None,
) -> HistoryPlan:
    """Choose the messages for one turn against a real token budget.

    ``messages`` are the conversation so far (objects or dicts with role +
    content), oldest first. ``window`` is the model's context window, or None
    when unknown (:data:`DEFAULT_WINDOW` is assumed). ``chars_per_token`` is
    the answering model's MEASURED ratio or None — see
    :func:`estimate_tokens`; it feeds EVERY estimate in the plan (and the
    token→char back-conversion of the clip path), because a ratio applied to
    some counters and not others makes the plan disagree with itself about
    what fits.
    """
    win = int(window or DEFAULT_WINDOW)
    if win <= 0:
        win = DEFAULT_WINDOW
    cpt = chars_per_token

    def _est(text: str) -> int:
        return estimate_tokens(text, cpt)

    system_tokens = _est(system_text)
    reserve = output_reserve(win)
    budget = win - system_tokens - reserve

    plan = HistoryPlan(window=win, history_budget=max(0, budget))
    # Raw demand, measured BEFORE any cap or trim: what this conversation would
    # cost if nothing were given up.
    plan.raw_tokens = system_tokens + sum(
        _est(_content_of(m)) + 4 for m in messages
    )
    if not messages:
        plan.used_tokens = system_tokens
        return plan

    # The count cap still applies as a cheap upper bound — a 200k window does
    # not make a 400-message replay a good idea.
    recent = list(messages)[-max_messages:]
    older = list(messages)[: max(0, len(messages) - len(recent))]

    # Step 2: stale tool output goes first — it is the biggest, least-referenced
    # thing in a transcript, and losing it costs less than losing a user turn.
    prepared: list[dict[str, str]] = []
    tools_trimmed = 0
    for i, m in enumerate(recent):
        role = _role_of(m)
        content = _content_of(m)[:MAX_MESSAGE_CHARS]
        if role == "tool" and (len(recent) - i) > STALE_TOOL_AFTER:
            content = _TOOL_TRIMMED
            tools_trimmed += 1
        prepared.append(
            {"role": role if role in ("user", "assistant", "tool") else "user",
             "content": content}
        )
    plan.tools_trimmed = tools_trimmed

    if budget <= 0:
        # The system prompt alone has eaten the window. Nothing sane fits; keep
        # the newest message (clipped) so the turn is still about something,
        # and tell the caller the model is too small for this conversation.
        last = prepared[-1] if prepared else {"role": "user", "content": ""}
        keep = max(MIN_LAST_MESSAGE_CHARS, 0)
        plan.messages = [{"role": last["role"], "content": last["content"][:keep]}]
        plan.clipped_last = len(last["content"]) > keep
        plan.dropped = len(messages) - 1
        plan.recap = build_recap(list(older) + list(recent[:-1]))
        plan.suggest_larger = True
        plan.used_tokens = system_tokens + _est(plan.messages[0]["content"])
        return plan

    # Does it all fit as-is? Answer this FIRST: it is the overwhelmingly common
    # case, it keeps the untouched path exactly untouched, and it decides
    # whether the recap reserve below has to be charged at all.
    full_cost = sum(_est(m["content"]) + 4 for m in prepared)
    fits_whole = full_cost <= budget and not older
    if not fits_whole:
        budget -= RECAP_RESERVE  # the recap rides in the system prompt

    # Step 3: walk BACKWARDS, keeping what fits.
    kept: list[dict[str, str]] = []
    used = 0
    for m in reversed(prepared):
        cost = _est(m["content"]) + 4  # role/format overhead
        if used + cost > budget and kept:
            break
        if used + cost > budget and not kept:
            # Step 5: the newest message alone overflows — clip it rather than
            # send an empty turn, and be loud about it. The token→char
            # conversion uses the SAME ratio as the estimates: clipping to
            # 3.6 chars/token while counting at a measured 2.0 would produce
            # a clip that still overflows the budget it was cut to fit.
            room_chars = max(
                MIN_LAST_MESSAGE_CHARS,
                int(budget * effective_chars_per_token(cpt)),
            )
            clipped = m["content"][:room_chars]
            kept.append({"role": m["role"], "content": clipped})
            plan.clipped_last = len(m["content"]) > len(clipped)
            plan.suggest_larger = True
            used += _est(clipped) + 4
            break
        kept.append(m)
        used += cost
    kept.reverse()

    dropped_msgs = list(older) + list(recent[: len(prepared) - len(kept)])
    plan.messages = kept
    plan.dropped = len(dropped_msgs)
    plan.recap = build_recap(dropped_msgs)
    plan.used_tokens = system_tokens + used + _est(plan.recap)
    # A conversation that had to shed turns fits better on a bigger model — say
    # so once, not on every trim of a stale tool result.
    if plan.dropped:
        plan.suggest_larger = True
    return plan
