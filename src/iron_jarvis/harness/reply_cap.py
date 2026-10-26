"""AN OPTIONAL CAP ON A CODING SESSION'S REPLY (v1.392.0, the long-jobs
plan's Phase 3 — decision 8: "no time or cost cap on your own coding replies
by default; an optional per-session cap is there if you want one").

THE SETTING. A session's ⋯ menu sets it; it is kept on the session's setup as
``cap`` — ``{"minutes": n}`` and/or ``{"usd": x}`` (``routes/chat.
_clean_setup`` keeps it by :func:`clean`'s whitelist; anything else is
dropped). No ``cap`` (the default) is byte-identical to before.

THE ENDING. At the top of every round the lane asks :meth:`ReplyCap.met`:
the reply's time since its ORIGINAL question (a reply carried on after a
restart, v1.391.0, counts from the question it carries on — its earlier
part's cost too), or its list-price cost (metered money plus a subscription's
list-price equivalent, the receipt's ``cost_usd``) at or past the cap. Then no
tool runs again: the reply ends EXACTLY the step-limit way — one tool-less
answer told :func:`instruction` (``OUT_OF_STEPS_BUILD_INSTRUCTION``'s idea, a
progress report the user can carry on from), the done frame's ``cap`` (which
cap, and its limit), the reply line's ``capMet``, and the page's Keep going (a
new turn: its own clock and its own cost).

Every time here is read through :data:`WALL` (a test moves it by hand).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable

#: The wall clock a cap's time is measured on (epoch seconds; tests swap it).
WALL: Callable[[], float] = time.time
#: The setup key.
SETUP_KEY = "cap"
#: The bounds a cap may take (a week of minutes; ten thousand dollars).
MAX_MINUTES = 10_080
MAX_USD = 10_000.0
MIN_USD = 0.01
#: The two caps (the done frame's ``cap.kind``).
KINDS = ("time", "cost")


def clean(raw: Any) -> "dict[str, Any] | None":
    """``setup.cap`` by whitelist: whole ``minutes`` 1..10,080 and/or ``usd``
    0.01..10,000 (rounded to cents). None when neither is valid."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    minutes = raw.get("minutes")
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and math.isfinite(minutes):
        m = int(round(minutes))
        if 1 <= m <= MAX_MINUTES:
            out["minutes"] = m
    usd = raw.get("usd")
    if isinstance(usd, (int, float)) and not isinstance(usd, bool) and math.isfinite(usd):
        u = round(float(usd), 2)
        if MIN_USD <= u <= MAX_USD:
            out["usd"] = u
    return out or None


def strict(raw: Any) -> "dict[str, Any] | None":
    """The cap a PATCH may SAVE (v1.392.0 review): :func:`clean`'s reading,
    but only when it keeps EVERYTHING the user sent exactly — no unknown key,
    no value out of range or of the wrong type, a whole number of minutes.
    None otherwise (the route's 400), so a cap is never half-kept in silence
    (``{"minutes": 30, "usd": 0}`` used to save the minutes and drop the
    dollars the user typed). The stored setup keeps :func:`clean`'s lenient
    reading — this is the door, not the whitelist."""
    if not isinstance(raw, dict) or not raw or set(raw) - {"minutes", "usd"}:
        return None
    minutes = raw.get("minutes")
    if "minutes" in raw and not (
        isinstance(minutes, (int, float)) and not isinstance(minutes, bool)
        and math.isfinite(minutes) and float(minutes).is_integer()
    ):
        return None
    out = clean(raw)
    if out is None or set(out) != set(raw):
        return None
    return out


def usd_words(usd: float) -> str:
    return f"${float(usd):,.2f}"


def minutes_words(minutes: float) -> str:
    m = int(minutes)
    if m >= 60 and m % 60 == 0:
        h = m // 60
        return f"{h} hour" + ("" if h == 1 else "s")
    return f"{m} minute" + ("" if m == 1 else "s")


def limit_words(met: dict[str, Any]) -> str:
    """"30 minutes" / "$5.00" — the cap that was met."""
    if met.get("kind") == "cost":
        return f"{usd_words(float(met.get('limit') or 0))} of list-price cost"
    return minutes_words(float(met.get("limit") or 0))


@dataclass
class ReplyCap:
    """One reply's cap: the limits, when its original question was asked
    (epoch seconds) and what its earlier part already cost (a reply carried
    on after a restart)."""

    minutes: "int | None" = None
    usd: "float | None" = None
    started_at: float = 0.0
    spent_usd: float = 0.0

    def met(self, cost_so_far: float) -> "dict[str, Any] | None":
        """``{"kind": "time"|"cost", "limit": n}`` once a cap is met, else
        None. Time is checked first."""
        if self.minutes and WALL() - float(self.started_at) >= float(self.minutes) * 60.0:
            return {"kind": "time", "limit": int(self.minutes)}
        if self.usd is not None and float(self.spent_usd) + float(cost_so_far or 0.0) >= float(self.usd):
            return {"kind": "cost", "limit": float(self.usd)}
        return None


def from_setup(setup: Any, *, started_at: float, spent_usd: float = 0.0) -> "ReplyCap | None":
    """The session's cap for a reply that began at ``started_at`` (None =
    no cap: the default)."""
    rec = clean(setup.get(SETUP_KEY)) if isinstance(setup, dict) else None
    if rec is None:
        return None
    return ReplyCap(
        minutes=rec.get("minutes"), usd=rec.get("usd"),
        started_at=float(started_at), spent_usd=max(0.0, float(spent_usd or 0.0)),
    )


def instruction(met: dict[str, Any]) -> str:
    """The tool-less answer's instruction at a cap (the step limit's idea)."""
    which = (
        f"this session's cost limit ({limit_words(met)})" if met.get("kind") == "cost"
        else f"this session's time limit ({limit_words(met)})"
    )
    return (
        f"You have reached {which}, so no more tools can run in this reply. The user can press "
        "Keep going to let you carry on from exactly here. Write a short progress report now, "
        "from the results above: start with one sentence saying you reached the session's limit; "
        "then what you have done so far (the files you changed and where, the commands you ran and "
        "what they showed, what you found out); then the next steps you will take when the user "
        "says keep going. Never say that nothing was done: if you only read and searched, say what "
        "you read and what you learned from it."
    )


def receipt(met: "dict[str, Any] | None") -> "dict[str, Any] | None":
    """The done frame's ``cap`` (None when no cap was met)."""
    if not isinstance(met, dict) or met.get("kind") not in KINDS:
        return None
    limit = met.get("limit")
    if met["kind"] == "time":
        return {"kind": "time", "limit": int(limit or 0)}
    return {"kind": "cost", "limit": round(float(limit or 0), 2)}


__all__ = [
    "KINDS", "MAX_MINUTES", "MAX_USD", "MIN_USD", "ReplyCap", "SETUP_KEY", "WALL", "clean", "from_setup",
    "instruction", "limit_words", "receipt", "strict",
]
