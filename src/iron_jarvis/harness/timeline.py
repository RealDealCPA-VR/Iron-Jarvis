"""THE TIMELINE (v1.390.0, the long-jobs plan's Phase 1): WHEN each piece of a
coding session's reply ran, on one time axis, with what each model call used.

The user's request: "when multiple LLMs are used in a coding task in our native
harness there could be a waterfall component … to the right" — the Timeline
tab beside Plan. It answers "where did the 117 minutes and $18 go". Before
this, a reply line kept each step's LENGTH only (``routes/build._ReplyWatch``
dropped the start it measured) and the turn's usage as ONE total.

THE FRAMES (a harness turn only — a coding session's reply and the benchmark's
Jarvis runner; every other turn's frames are byte-identical):

* ``model_call`` (NEW) — sent by the stream lane (``routes/chat``) right after
  each BILLED model call is added to the turn's tally, at the three places it
  adds one (a model call of the tool loop, the final-answer nudge, a language
  rewrite): ``{ms, provider, model, usage, kind?}``. ``usage`` has the
  RECEIPT's shape (:func:`call_usage` — ``eval.pricing.UsageTally`` over that
  one call), so the calls' costs add up to the reply's receipt by
  construction. ``kind`` is "final" (the final-answer nudge) or "rewrite" (a
  language rewrite), absent for a call of the tool loop.
* ``tool_call`` FINISHED frames gain ``ms``: the call's OWN run time, measured
  around its invoke. Reads that ran TOGETHER (``chat_turn._run_tool_round``)
  are announced together and finished together, so the frames alone would
  give each the batch's length; this keeps each its own.

THE CLOCK (:class:`ReplyClock`, Build only): the reply writer
(``routes/build.recorded_turn``) passes every frame through it and adds the
offset — ms from the reply's start, the same origin as the reply line's
``timing.startedAt`` — to the frames it stamps: ``start`` on a ``tool_call``
(started and finished; a finished one's ``ms`` is settled here), ``start`` on a
``model_call`` (its end is when it passed, minus its ``ms``), ``start`` on a
``round`` (the call it opens works from there), ``start`` / ``end`` on a
``helper`` frame (its first frame / the one that says it ended) and ``start``
/ ``ms`` on the helper's own step. The page draws the running reply from
those, and the saved line keeps the same numbers — so a reload, a rejoin and
the live view draw the same picture. ONE precise clock (``perf_counter``)
measures every offset and every length, and the reply's own length on it is
the line's ``timing.ms`` (the Timeline's axis), so every bar lies inside the
reply. The line's ``startedAt`` / ``firstTokenAt`` / ``endedAt`` stay WALL-clock
epoch stamps (review): the reply's Changes row asks the journal for the rows
between them, and those rows are stamped by the wall clock.

THE CHECKS (review): a step Iron Jarvis ran itself — the finish check's test
run (``harness/finish.CALL_ID_PREFIX``) or a project check
(``harness/checkers.CALL_ID_PREFIX``) — carries ``check`` ("finish" /
"project", :func:`check_kind`) on its frames and its saved step, so the
Timeline tells it apart from the model's own work.

The words a person reads are the run log's ("Read app.py", the model's name,
helpers); never the engine room's.
"""

from __future__ import annotations

import time
from typing import Any

from ..eval.pricing import UsageTally

#: The new frame's event name.
MODEL_CALL_EVENT = "model_call"
#: The kinds of a call outside the tool loop (absent = a call of the loop).
#: v1.392.0: "summary" — the reply's own model writing the summary of its
#: older rounds (``harness/reply_summary``); its frame also says how many
#: earlier steps that summary covers (``summarized``).
CALL_KINDS = ("final", "rewrite", "summary")
#: Model calls a reply line keeps, at most (the step cap: two a round at the
#: safety stop, ``harness.mode.BUILD_SAFETY_ROUNDS``).
MAX_CALLS = 2000
#: A call's cost is kept to this many decimals: 2,000 calls then add up to the
#: receipt's (6 decimals, ``UsageTally.as_dict``) within its own rounding.
COST_DIGITS = 9
#: What an unfinished call is called on the line (the reply ended while the
#: model was working: a Stop, a shutdown, or the call itself failed).
OPEN_STATES = ("stopped", "failed")


def call_usage(provider: str, model: str, usage: Any) -> dict[str, Any]:
    """ONE billed call's usage in the receipt's shape: ``input_tokens`` and
    ``output_tokens`` always; ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` only when the call reported them;
    ``cost_usd`` only when KNOWN (a native figure or a price row — never a
    made-up 0); ``list_price_equivalent: true`` only when a subscription
    served it. The same rules as the reply's receipt (``UsageTally``), applied
    to one call. Never raises."""
    tally = UsageTally()
    try:
        cost = tally.add(provider, model, usage if isinstance(usage, dict) else {})
    except Exception:  # noqa: BLE001 — accounting never breaks a turn
        return {"input_tokens": 0, "output_tokens": 0}
    out = tally.as_dict()
    if tally.cost_known:
        # Unrounded past the receipt's 6 decimals, so a reply's calls add up
        # to its receipt (the receipt rounds the SUM once).
        out["cost_usd"] = round(float(cost), COST_DIGITS)
    return out


def model_call_frame(provider: str, model: str, usage: Any, ms: float, kind: str = "") -> dict[str, Any]:
    """The ``model_call`` frame's data for one billed call (see the module
    doc). ``ms`` is the call's length, measured by the lane."""
    out: dict[str, Any] = {
        "ms": max(0, int(ms)),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "usage": call_usage(str(provider or ""), str(model or ""), usage),
    }
    if kind in CALL_KINDS:
        out["kind"] = kind
    return out


#: What ``check`` says on a step Iron Jarvis ran itself (absent on the model's).
CHECK_KINDS = ("finish", "project")


def check_kind(call_id: Any) -> str:
    """"finish" for the finish check's own test run, "project" for a project
    check, "" for a step the model chose. Read off the call id the check
    gave its synthetic call (the one fact both carry). Never raises."""
    from .checkers import CALL_ID_PREFIX as _PROJECT
    from .finish import CALL_ID_PREFIX as _FINISH

    cid = str(call_id or "")
    if cid.startswith(_FINISH):
        return "finish"
    if cid.startswith(_PROJECT):
        return "project"
    return ""


def _int(v: Any) -> "int | None":
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


class ReplyClock:
    """The reply's own clock: offsets from its start, and the model calls it
    has seen. One per reply (``routes/build._ReplyWatch.clock``). Every
    ``stamp_*`` returns the frame's data with its offsets added (a NEW dict;
    the caller re-sends it) and records what the reply line keeps."""

    def __init__(self, started_at: float, now: Any = None) -> None:
        self.started_at = float(started_at)
        # ONE PRECISE CLOCK for every offset: on Windows (Python 3.12)
        # `time.time()` and `time.monotonic()` tick every 15.6 ms, so a start
        # measured on one and a length on the other put a model call 11 ms
        # BEFORE the `round` frame that opened it (seen in this phase's own
        # pins). The lane measures its lengths on `perf_counter` too.
        self._now = now or time.perf_counter
        self._t0 = self._now()
        #: call id -> start offset of a step that started and has not finished.
        self._steps: dict[str, int] = {}
        #: (helper id, step id) -> start offset of a helper's own step.
        self._helper_steps: dict[tuple[str, str], int] = {}
        #: helper id -> the offset its row's first frame passed at.
        self._helpers: dict[str, int] = {}
        #: The model calls seen, in order (what the reply line keeps).
        self.calls: list[dict[str, Any]] = []
        #: The model call in flight: the offset its ``round`` frame passed at,
        #: and the provider/model a ``meta`` frame named — None when none is.
        self._open: "dict[str, Any] | None" = None

    # -- the clock -------------------------------------------------------- #

    def offset(self) -> int:
        """ms from the reply's start, now (never negative)."""
        return max(0, int((self._now() - self._t0) * 1000))

    # -- steps -------------------------------------------------------------- #

    def step_started(self, cid: str) -> int:
        start = self.offset()
        self._steps[cid] = start
        return start

    def step_finished(self, cid: str, lane_ms: Any = None) -> tuple[int, int]:
        """``(start, ms)`` of a finished step: its start as it was stamped
        (now, for a step whose start was never seen) and its length — the
        lane's own measure when it sent one (never past now), else the time
        since its start."""
        now = self.offset()
        start = self._steps.pop(cid, now)
        ms = _int(lane_ms)
        span = now - start
        if ms is None or ms < 0 or ms > span:
            ms = span
        return start, ms

    # -- model calls -------------------------------------------------------- #

    def round_started(self) -> int:
        """A ``round`` frame: the tool loop is about to ask the model. Answers
        the call's start (the page draws it working from there)."""
        if self._open is None:
            self._open = {"start": self.offset()}
        return int(self._open["start"])

    def meta(self, provider: Any, model: Any) -> None:
        if self._open is not None:
            if isinstance(provider, str) and provider:
                self._open["provider"] = provider
            if isinstance(model, str) and model:
                self._open["model"] = model

    def call_seen(self, data: dict[str, Any]) -> dict[str, Any]:
        """A ``model_call`` frame: record the call, and return its entry (the
        frame's data with ``start`` added). It ended NOW; it started its
        ``ms`` before."""
        self._open = None
        now = self.offset()
        ms = _int(data.get("ms"))
        ms = now if ms is None or ms < 0 or ms > now else ms
        entry: dict[str, Any] = {"start": now - ms, "ms": ms}
        for key in ("provider", "model"):
            if isinstance(data.get(key), str):
                entry[key] = data[key]
        if isinstance(data.get("usage"), dict):
            entry["usage"] = data["usage"]
        if data.get("kind") in CALL_KINDS:
            entry["kind"] = data["kind"]
        summarized = _int(data.get("summarized"))
        if entry.get("kind") == "summary" and summarized is not None and summarized >= 0:
            entry["summarized"] = summarized
        if len(self.calls) < MAX_CALLS:
            self.calls.append(entry)
        return {**data, "start": entry["start"]}

    def open_call(self, state: str) -> "dict[str, Any] | None":
        """The call that was in flight when the reply ended (a Stop, a
        shutdown, or the call failed) as the line keeps it: from its ``round``
        frame to now, ``state`` "stopped" or "failed", no usage (an unfinished
        call is never billed — the receipt does not count it either). None
        when no call was in flight."""
        if self._open is None:
            return None
        start = int(self._open.get("start") or 0)
        entry: dict[str, Any] = {"start": start, "ms": max(0, self.offset() - start),
                                 "state": state if state in OPEN_STATES else "stopped"}
        for key in ("provider", "model"):
            if self._open.get(key):
                entry[key] = self._open[key]
        return entry

    # -- helpers ------------------------------------------------------------ #

    def helper(self, data: dict[str, Any]) -> dict[str, Any]:
        """A ``helper`` frame with its row's ``start`` (its first frame — the
        call that started it began then) and, once it ended, ``end``, on the
        reply's clock; and its step's ``start`` (and ``ms`` once finished).
        The frame's own ``since`` / ``ended`` (epoch ms) are kept as they
        were — they tick on the coarse wall clock."""
        out = dict(data)
        hid = str(data.get("id") or "")
        now = self.offset()
        start = self._helpers.setdefault(hid, now)
        out["start"] = start
        if data.get("ended") is not None:
            out["end"] = max(start, now)
        step = data.get("step")
        if isinstance(step, dict):
            sid = str(step.get("id") or "")
            stamped = dict(step)
            if step.get("status") == "finished":
                s = self._helper_steps.pop((hid, sid), now)
                stamped["start"] = s
                stamped["ms"] = max(0, now - s)
            else:
                self._helper_steps[(hid, sid)] = now
                stamped["start"] = now
            out["step"] = stamped
        return out


__all__ = [
    "CALL_KINDS", "CHECK_KINDS", "COST_DIGITS", "MAX_CALLS", "MODEL_CALL_EVENT", "OPEN_STATES", "ReplyClock",
    "call_usage", "check_kind", "model_call_frame",
]
