"""THE REPLY BUDGET for a local model endpoint (v1.387.0).

WHY THIS EXISTS. Every OpenAI-compatible local endpoint (the ``custom`` slot,
``ollama``, every ``fleet-*`` node) was built with the adapter's default
``max_tokens=4096``. The user's daily model is a local GLM behind a LiteLLM
proxy, and on the benchmark it read the files, wrote ~4,000 tokens of a
``write_file`` call (a whole module, plus its thinking), and was CUT OFF at
4,096 — the server's tool parser never emitted the half call, the round came
back with no text and no call, and the lane asked for a no-tools final answer,
so the model pasted the code into chat and the task failed (three runs, two
versions, the same way). A reply budget that fits the work is the first half
of the fix; the lanes' cut-off recovery (``daemon/chat_turn``) is the second.

THE RULE, per local endpoint and model (one place, both adapters read it):

* **The cap** — the most one reply may ask for: the model's own STATED output
  limit when its server states one (LiteLLM ``/model/info``
  ``model_info.max_output_tokens``, else ``max_tokens``, read by the fleet
  route reader with the node's saved key — :mod:`iron_jarvis.fleet.upstreams`),
  bounded by :data:`REPLY_CEILING`; otherwise :data:`DEFAULT_REPLY_TOKENS`.
  A limit the SERVER named in a refusal ("supports at most N completion
  tokens") lowers it for the process.
* **The fit** — never ask for more than fits: the window the chat lanes
  already plan against (``chat_turn._context_window``: pin → measured envelope
  → the proxy's stated ``max_input_tokens`` → the fleet probe's
  ``context_length`` → unknown, planned as ``DEFAULT_WINDOW``) minus the
  request's own estimated size and a slack for the estimate. A window the
  SERVER named in a refusal (vLLM: "This model's maximum context length is
  …") narrows it for the process.
* **The floor** — when the window is UNKNOWN the ask never drops below the
  old 4,096 (:data:`REPLY_FLOOR`): an unknown window is usually a large one,
  and asking less than before would make an unknown model worse. When the
  window is KNOWN the fit is honoured down to :data:`MIN_ASK`, so a long
  conversation on a small known window does not ask for an answer that
  cannot fit.
* **The planner agrees** — the history planner holds back
  :func:`reserve_for` for the answer (the same cap, a share of the window),
  so a planned transcript always leaves the adapter room to ask at least that
  much, and the planner never holds back more than the adapter could use.

WHY 32,768 BY DEFAULT. One tool call that writes a source file carries the
whole file as one JSON string: a 300-line module is ~3-4k tokens, a
1,000-line one ~12-15k, and a reasoning model (GLM, Qwen3, DeepSeek-R1 under
vLLM's reasoning parser) spends its thinking from the SAME ``max_tokens``
before it writes a byte of the call. 16k is tight for "think, then write a
big file"; 32k covers it with room, and it is what Claude Code itself asks
for by default. It is a CEILING, not a target — a model stops when its answer
is done; only a runaway generation uses it all, and the fit still applies.

Hosted providers (OpenAI, xAI, OpenRouter, Anthropic, Google) are NOT budgeted
here: their requests stay byte-for-byte what they were. The CLI providers
(claude-cli, codex-cli, grok-cli, opencode-cli) keep their own limits.

Pure arithmetic plus a small process memory of what servers told us; never a
network call, never raises from the public helpers.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable

from ..context.budget import (
    DEFAULT_WINDOW,
    OUTPUT_RESERVE_MIN,
    OUTPUT_RESERVE_RATIO,
    estimate_tokens,
)

#: The reply budget of a local endpoint whose server states no output limit.
DEFAULT_REPLY_TOKENS = 32_768
#: The most a STATED output limit is taken at face value. A model whose server
#: states 128k can still only usefully write so much in one call, and a
#: runaway generation at 55 tokens/s would run for the better part of an hour.
REPLY_CEILING = 65_536
#: What every local endpoint asked for before v1.387.0 — the floor when the
#: window is unknown (never ask for less than before on a guess).
REPLY_FLOOR = 4_096
#: The smallest ask on a KNOWN window: below this the prompt itself does not
#: fit, and the lanes' too-long refit (v1.335.0) is the right answer.
MIN_ASK = 1_024
#: Slack on the prompt estimate (``estimate_tokens`` is a character ratio, a
#: server counts with its own tokenizer and chat template).
PROMPT_SLACK_RATIO = 0.10
PROMPT_SLACK_MIN = 256
#: A retry with the budget the server says fits is made only when that budget
#: is at least this big; below it, the PROMPT is the problem and the error is
#: raised unchanged (so the lanes shrink the history, as before).
RETRY_MIN = MIN_ASK
#: Kept off the server's own exact fit (chat-template rounding).
SERVER_FIT_MARGIN = 16

#: ``(provider, model) -> {"window": int|None, "max_output": int|None,
#: "ratio": float|None}`` — what the platform knows about a model: the window
#: the lanes plan against, the output limit its server states, and the
#: measured chars-per-token ratio. Injected by the platform
#: (``ProviderManager.reply_facts``); absent = nothing known.
FactsFn = Callable[[str, str], "dict[str, Any] | None"]


def _positive(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def cap_for(stated: int | None = None, learned: int | None = None) -> int:
    """The most one reply may ask for (see the module docstring)."""
    stated = _positive(stated)
    cap = min(stated, REPLY_CEILING) if stated else DEFAULT_REPLY_TOKENS
    learned = _positive(learned)
    if learned:
        cap = min(cap, learned)
    return max(1, cap)


def reserve_for(window: int | None, cap: int) -> int:
    """The room the history PLANNER holds back for the answer on a budgeted
    endpoint: a quarter of the window (the planner's long-standing share),
    never more than the adapter's cap (holding back room no request can use
    starves the history for nothing), never less than the planner's minimum.
    For the hosted providers the planner keeps ``context.budget.output_reserve``
    — this function is never consulted for them."""
    win = _positive(window) or DEFAULT_WINDOW
    cap = _positive(cap) or REPLY_FLOOR
    return int(max(OUTPUT_RESERVE_MIN, min(cap, int(win * OUTPUT_RESERVE_RATIO))))


def prompt_slack(prompt_tokens: int) -> int:
    return max(PROMPT_SLACK_MIN, int(max(0, prompt_tokens) * PROMPT_SLACK_RATIO))


def ask_for(cap: int, window: int | None, prompt_tokens: int) -> int:
    """The ``max_tokens`` one request asks for: the cap, fitted to the room the
    window leaves after the prompt (and its slack). ``window`` ``None`` = not
    known (planned as ``DEFAULT_WINDOW``, floored at :data:`REPLY_FLOOR`).

    On a KNOWN window the floor is :data:`MIN_ASK` but never more than the
    planner's own reserve (:func:`reserve_for`): a transcript the planner
    fitted leaves at least that much room, so even the floor fits it — the
    property ``tests/test_reply_budget_v1387.py`` sweeps."""
    cap = _positive(cap) or REPLY_FLOOR
    known = _positive(window)
    win = known or DEFAULT_WINDOW
    room = win - max(0, int(prompt_tokens)) - prompt_slack(prompt_tokens)
    if known:
        floor = min(cap, MIN_ASK, reserve_for(win, cap))
    else:
        floor = min(cap, REPLY_FLOOR)
    return int(max(floor, min(cap, room)))


# --------------------------------------------------------------------------- #
# What a server says when the budget does not fit (the retry's input).
# --------------------------------------------------------------------------- #
_NUM = r"([\d][\d,]*)"
#: The model's window, as the server words it.
_CONTEXT_RES = (
    re.compile(r"maximum context length (?:is|of)\s*" + _NUM, re.IGNORECASE),  # vLLM, SGLang, OpenAI
    re.compile(r"max_model_len\D{0,12}" + _NUM, re.IGNORECASE),
    re.compile(r"`?max_new_tokens`?\s+must be\s*<=\s*" + _NUM, re.IGNORECASE),  # TGI
)
#: The prompt's size, in the server's own count.
_INPUT_RES = (
    re.compile(_NUM + r"\s+in the messages", re.IGNORECASE),  # vLLM (older)
    re.compile(r"request has\s+" + _NUM + r"\s+input tokens", re.IGNORECASE),  # vLLM (newer)
    re.compile(_NUM + r"\s+tokens from the input", re.IGNORECASE),  # SGLang
    re.compile(r"Given:\s*" + _NUM + r"\s*`?inputs`?\s+tokens", re.IGNORECASE),  # TGI
    re.compile(r"messages resulted in\s+" + _NUM + r"\s+tokens", re.IGNORECASE),  # OpenAI (older)
)
#: The model's own output limit, when the server names it.
_OUTPUT_CAP_RES = (
    re.compile(r"supports at most\s+" + _NUM + r"\s+completion tokens", re.IGNORECASE),
    re.compile(r"max_(?:completion_)?tokens\W{0,4}(?:must be|should be)\s*(?:<=|less than or equal to|at most)\s*" + _NUM, re.IGNORECASE),
)
#: Words that say the refusal is about the OUTPUT budget (not a bad model,
#: not a bad key): only then is the request retried with a smaller budget.
_ABOUT_OUTPUT_RE = re.compile(
    r"max_tokens|max_completion_tokens|max_new_tokens|in the completion|for the completion"
    r"|completion tokens|n_predict|num_predict",
    re.IGNORECASE,
)


def _num(rx: "re.Pattern[str]", text: str) -> int | None:
    m = rx.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _first(res: tuple, text: str) -> int | None:
    for rx in res:
        n = _num(rx, text)
        if n:
            return n
    return None


def server_limits(detail: str) -> dict[str, int]:
    """What a refusal says: ``{"context", "input", "output_cap"}`` (each only
    when stated). Never raises."""
    try:
        text = str(detail or "")
        out: dict[str, int] = {}
        for key, res in (("context", _CONTEXT_RES), ("input", _INPUT_RES), ("output_cap", _OUTPUT_CAP_RES)):
            n = _first(res, text)
            if n and 0 < n <= 50_000_000:
                out[key] = n
        return out
    except Exception:  # noqa: BLE001
        return {}


def about_output(detail: str) -> bool:
    """True when a 400's words name the OUTPUT budget (the only refusal the
    retry answers)."""
    try:
        return bool(_ABOUT_OUTPUT_RE.search(str(detail or "")))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# What servers taught us, for the process life (adapters are rebuilt per call).
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
#: ``(endpoint, model) -> {"context": int, "output_cap": int, "ratio": float}``
_LEARNED: dict[tuple[str, str], dict[str, Any]] = {}
#: ``(provider, model) -> window`` — the same windows, by the names the history
#: planner resolves (``chat_turn.learned_window`` reads it), so a window a
#: server named in a refusal the adapter's retry answered reaches the PLANNER
#: too, not only the next ask.
_CONTEXT_BY_PROVIDER: dict[tuple[str, str], int] = {}


def learned(endpoint: str, model: str) -> dict[str, Any]:
    with _LOCK:
        return dict(_LEARNED.get((str(endpoint or ""), str(model or "")), {}))


def learned_context(provider: str, model: str) -> int | None:
    """The smallest window a server named for ``(provider, model)`` this
    process (a refusal the adapter retried), or ``None``. Never raises."""
    with _LOCK:
        return _CONTEXT_BY_PROVIDER.get((str(provider or ""), str(model or "")))


def _learn(endpoint: str, model: str, **facts: Any) -> None:
    key = (str(endpoint or ""), str(model or ""))
    with _LOCK:
        cur = _LEARNED.setdefault(key, {})
        for name, value in facts.items():
            if value is None:
                continue
            if name in ("context", "output_cap"):
                old = cur.get(name)
                cur[name] = min(old, value) if old else value
            elif name == "ratio":
                cur[name] = max(float(cur.get(name) or 1.0), float(value))


def _learn_context_for(provider: str, model: str, window: int) -> None:
    key = (str(provider or ""), str(model or ""))
    with _LOCK:
        old = _CONTEXT_BY_PROVIDER.get(key)
        _CONTEXT_BY_PROVIDER[key] = min(old, window) if old else window


def forget_learned() -> None:
    """Test seam: forget every server-taught limit."""
    with _LOCK:
        _LEARNED.clear()
        _CONTEXT_BY_PROVIDER.clear()


# --------------------------------------------------------------------------- #
# The per-adapter budget.
# --------------------------------------------------------------------------- #
def request_tokens(system: str, wire: Any, tools: Any, ratio: float | None = None) -> int:
    """A request's size in tokens, estimated from the WIRE shape the adapter
    sends (JSON escapes and role keys included — a slight overestimate, which
    is the safe direction). Never raises."""
    try:
        blob = json.dumps(wire, ensure_ascii=False, default=str)
        if tools:
            blob += json.dumps(tools, ensure_ascii=False, default=str)
        return estimate_tokens(system or "", ratio) + estimate_tokens(blob, ratio)
    except Exception:  # noqa: BLE001 — a guess, never a crash
        return estimate_tokens(str(system or "") + str(wire) + str(tools or ""), ratio)


class ReplyBudget:
    """One local endpoint adapter's reply budget.

    ``provider`` / ``endpoint`` name the adapter (learned limits are kept per
    endpoint and model); ``facts`` is the platform's reader (absent on a bare
    manager: nothing known, so the default cap fitted to ``DEFAULT_WINDOW``)."""

    def __init__(self, provider: str, endpoint: str, facts: FactsFn | None = None) -> None:
        self.provider = str(provider or "")
        self.endpoint = str(endpoint or "")
        self._facts = facts

    # -- facts ------------------------------------------------------------
    def facts(self, model: str) -> dict[str, Any]:
        if self._facts is None:
            return {}
        try:
            got = self._facts(self.provider, str(model or ""))
        except Exception:  # noqa: BLE001 — nothing known, never a crash
            return {}
        return got if isinstance(got, dict) else {}

    def cap(self, model: str, facts: "dict[str, Any] | None" = None) -> int:
        facts = self.facts(model) if facts is None else facts
        return cap_for(facts.get("max_output"), learned(self.endpoint, model).get("output_cap"))

    def window(self, model: str, facts: "dict[str, Any] | None" = None) -> int | None:
        """The window to fit against: the lanes' ladder, narrowed by what the
        server itself said (``None`` = unknown)."""
        facts = self.facts(model) if facts is None else facts
        win = _positive(facts.get("window"))
        said = _positive(learned(self.endpoint, model).get("context"))
        if said and (not win or said < win):
            return said
        return win

    # -- the ask ------------------------------------------------------------
    def raw_prompt_tokens(self, model: str, system: str, wire: Any, tools: Any,
                          facts: "dict[str, Any] | None" = None) -> int:
        """The request's size by OUR estimate (the planner's ratio)."""
        facts = self.facts(model) if facts is None else facts
        ratio = facts.get("ratio")
        return request_tokens(
            system, wire, tools, ratio if isinstance(ratio, (int, float)) else None
        )

    def scaled(self, model: str, raw: int) -> int:
        """``raw`` corrected by how much more this server counted for the same
        request the last time it said (never less than ``raw``)."""
        scale = learned(self.endpoint, model).get("ratio")
        if isinstance(scale, (int, float)) and scale > 1.0:
            return int(raw * float(scale)) + 1
        return raw

    def plan(self, model: str, *, system: str, wire: Any, tools: Any) -> tuple[int, int]:
        """``(max_tokens, raw_prompt_estimate)`` for one request — the ask,
        and the estimate a refusal's retry is measured against. Never raises."""
        try:
            facts = self.facts(model)
            raw = self.raw_prompt_tokens(model, system, wire, tools, facts)
            return (
                ask_for(self.cap(model, facts), self.window(model, facts), self.scaled(model, raw)),
                raw,
            )
        except Exception:  # noqa: BLE001 — the old budget, never a crash
            return REPLY_FLOOR, 0

    def ask(self, model: str, *, system: str, wire: Any, tools: Any) -> int:
        """The ``max_tokens`` this request asks for. Never raises."""
        return self.plan(model, system=system, wire=wire, tools=tools)[0]

    def retry_ask(self, model: str, detail: str, asked: int, raw_prompt: int) -> int | None:
        """The SMALLER budget to retry once with after the server refused
        ``asked`` for not fitting — or ``None`` when the refusal is not about
        the output budget, or the room it leaves is too small to be worth an
        answer (the prompt is the problem; the caller raises as before, and
        the lanes' too-long refit takes over). ``raw_prompt`` is OUR estimate
        of the request (:meth:`plan`). Learns what the server said for the
        rest of the process: its window, its output limit, and how much more
        it counts than our estimate. Never raises."""
        try:
            if not about_output(detail):
                return None
            said = server_limits(detail)
            ctx, inp, cap = said.get("context"), said.get("input"), said.get("output_cap")
            if inp and raw_prompt > 0 and inp > raw_prompt:
                _learn(self.endpoint, model, ratio=min(3.0, inp / raw_prompt))
            if ctx:
                _learn(self.endpoint, model, context=ctx)
                _learn_context_for(self.provider, model, ctx)
            if cap:
                _learn(self.endpoint, model, output_cap=cap)
            fits: int | None = None
            if ctx:
                used = inp if inp else self.scaled(model, raw_prompt) + prompt_slack(raw_prompt)
                fits = ctx - used - SERVER_FIT_MARGIN
            if cap:
                fits = min(fits, cap) if fits is not None else cap
            if fits is None or fits >= asked or fits < RETRY_MIN:
                return None
            return int(fits)
        except Exception:  # noqa: BLE001
            return None


# --------------------------------------------------------------------------- #
# A REPLY CUT OFF AT THE OUTPUT LIMIT IS RECOVERED, NOT ENDED.
#
# The benchmark's glm runs: the model read the files, then spent its whole
# 4,096-token budget on a `write_file` whose arguments never closed; the round
# came back with no text and no complete call, the lane read it as "stopped
# without writing a reply" and asked for a NO-TOOLS final answer — so the model
# pasted the code into chat and the task failed. Now such a round is answered
# with ONE plain note (nothing was run; continue with the tools, write a large
# file in parts) and the turn goes on, at most CUT_OFF_NOTES_MAX times; the next
# cut ends the turn with a sentence that says what happened. A cut reply with
# TEXT and no call is a long answer and keeps v1.323.0's `truncated` + Continue.
# Read by BOTH chat lanes (daemon/chat_turn.py, daemon/routes/chat.py —
# lock-step) and the agent runtime (agents/runtime.py).
# --------------------------------------------------------------------------- #
#: The most cut-off notes one turn (or one agent run) is given.
CUT_OFF_NOTES_MAX = 2
#: The tools a note offers for writing the rest of a file, in the order named
#: (the real names and the Claude dialect's; only the ARMED ones are named).
_PART_TOOLS = ("edit_file", "apply_patch", "multi_edit", "Edit", "MultiEdit")
#: A turn that can write a file at all (only then does the note say how to
#: write a large one in parts).
_WRITE_TOOLS = ("write_file", "Write", *_PART_TOOLS)


def cut_off_round(response: Any, *, has_tools: bool) -> "list[str] | None":
    """The names of the calls a round's reply was CUT OFF in (``""`` for a
    call whose name never arrived; ``[]`` when it wrote no text and no
    complete call at all), or ``None`` when the round was not such a cut:
    it did not stop at the output limit, the turn has no tools, or it is a
    long TEXT answer (the ``truncated`` + Continue case). Never raises."""
    try:
        if response is None or not has_tools:
            return None
        if getattr(response, "finish_reason", "") != "max_tokens":
            return None
        cut = [str(n or "") for n in (getattr(response, "cut_calls", None) or [])]
        if cut:
            return cut
        if getattr(response, "tool_calls", None):
            return None
        if (getattr(response, "text", "") or "").strip():
            return None
        return []
    except Exception:  # noqa: BLE001 — not a cut, the old path
        return None


def _cut_what(cut: "list[str]") -> str:
    names = [n for n in cut if n]
    if not cut:
        return "before it wrote any text or a complete tool call"
    if names:
        listed = ", ".join(dict.fromkeys(names))
        return f"before its {listed} call was complete"
    return "before its tool call was complete"


def cut_off_note(cut: "list[str]", tool_names: Any, *, limit: "int | None" = None,
                 ran_others: bool = False) -> str:
    """The ONE user-role note a cut-off round is answered with. ``tool_names``
    = what this turn armed (only those are named); ``limit`` = the output
    budget the reply had, when the adapter reported it; ``ran_others`` = the
    reply's OTHER, complete calls ran (their results are above)."""
    armed = set(tool_names or ())
    parts = [t for t in _PART_TOOLS if t in armed][:2]
    how = " or ".join(parts) if parts else "your file-editing tool"
    lim = (
        f" ({int(limit):,} tokens)"
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else ""
    )
    ran = (
        "so that call was not run (the other calls in it ran; their results are above)"
        if ran_others else "so nothing was run"
    )
    head = f"Your last reply was cut off at the output limit{lim} {_cut_what(cut)}, {ran}."
    if not any(t in armed for t in _WRITE_TOOLS):
        return f"{head} Continue the task with your tools, keeping each reply well under the limit."
    return (
        f"{head} Continue the task with your tools. Write a large file in parts: "
        f"create it with the first part, then add the rest with {how}, keeping "
        "each call well under the limit."
    )


def cut_off_end_text(cut: "list[str]", times: int) -> str:
    """The reply when a turn's replies were cut off more times than it is
    given notes (or with no round left): what happened, in plain words, and
    what the user can do. Never the no-tools final answer (which made the
    model paste its code into chat)."""
    n = max(1, int(times))
    when = "once" if n == 1 else ("twice" if n == 2 else f"{n} times")
    in_a_row = "" if n == 1 else " in a row"
    return (
        f"The model's reply was cut off at its output limit {when}{in_a_row} "
        f"{_cut_what(cut)}, so nothing from that reply was run. Ask for the work "
        "in smaller pieces, or choose a model with a larger output limit."
    )


def cut_off_receipt(times: int, ended: bool) -> dict:
    """``{"cut_off": {"times", "recovered"}}`` for the route object (LAST,
    only when a reply was cut off this turn), else ``{}``. ``recovered`` =
    the turn went on past the last cut instead of ending on it."""
    if not times:
        return {}
    return {"cut_off": {"times": int(times), "recovered": not ended}}
