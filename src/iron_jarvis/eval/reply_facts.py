"""What one reply used, kept while it runs (the intelligence plan, release 6).

A chat reply (both lanes) and an agent run each open ONE :class:`ReplyFacts`
when they start. While the reply runs it collects, as numbers and names only:

* ``parts``: the size of each part of what the reply was sent when it began
  (``context.breakdown.sent_record``: your messages, attached files, what was
  remembered, project and folder knowledge, instructions, skills, a summary of
  earlier messages) with the room the model has. Every size is an ESTIMATE
  (``context.budget.estimate_tokens``), never a provider's count;
* ``model_ms`` / ``model_calls``: how long the model took, summed over the
  reply's own model calls that answered, and how many there were. The router
  notes each one at the place it already times a call
  (``ModelRouter._timed_complete`` and the three stream ends), through its own
  injectable clock;
* ``first_word_ms``: from the reply's start to the first word a lane showed
  (the stream lane and an agent run; the POST lane shows no word until the
  end, so it stays None).

NEVER the text of any part, a file's content or a tool's arguments. Nothing
here is sent anywhere: the lane writes these on the reply's own usage row
(``chat_turn._sent_fields``; ``agents.runtime`` on the run's row).

The current reply's facts ride a ``ContextVar`` so the router's one seam
reaches them without every call site passing them along. A model call made
INSIDE A TOOL is that tool call's (it has its own usage row,
``eval.call_ledger``): its time is not this reply's. Nothing here raises into
a reply.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

#: The clock every duration here is read from (seconds, monotonic). A test
#: replaces it; nothing in this module sleeps.
clock = time.monotonic


def now() -> float:
    return float(clock())


def _call_scope() -> Any:
    """The usage scope a model call is made in (``call_ledger``): None for a
    reply's own call, a tool call's scope inside a tool."""
    try:
        from . import call_ledger

        return call_ledger._SCOPE.get()
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        return None


class ReplyFacts:
    """One reply's (or one agent run's) facts. Numbers and names only."""

    def __init__(self) -> None:
        self.started = now()
        #: ``context.breakdown.sent_record``'s dict, or None = not measured.
        self.parts: "dict[str, Any] | None" = None
        self.model_ms = 0.0
        self.model_calls = 0
        self.first_word_ms: "int | None" = None
        #: An agent run only (``agents.run_facts``): the prompt's named part
        #: strings, held in MEMORY to be measured once (saved nowhere), and
        #: the run's running sums.
        self.pieces: "dict[str, list[str]] | None" = None
        self.run_state: "dict[str, Any] | None" = None
        self.run_id: "str | None" = None
        #: True for the facts :func:`scope` opens (an agent run's): a run's
        #: steps are never noted on a chat reply's facts.
        self.for_run = False
        #: The ``input_tokens`` of the run's calls that reported a cache figure.
        self.cache_input = 0
        #: The intelligence plan, release 9: one of this reply's own model
        #: calls was asked in the more guided form because the model had been
        #: stumbling (``router._guide_if_stumbling``). The lanes add the quiet
        #: line from it; nothing else.
        self.guided = False
        #: The usage scope this reply's own model calls are made in: a call
        #: made in any other scope (a tool's) is not this reply's.
        self._home = _call_scope()
        self._lock = threading.Lock()

    # -- writers (never raise) --------------------------------------------
    def note_parts(self, parts: Any) -> None:
        """The reply's sizes by part, once: the first note stands (the reply
        as it BEGAN; a later call of the same reply does not replace it)."""
        if isinstance(parts, dict) and parts and self.parts is None:
            self.parts = parts

    def note_model_time(self, seconds: Any) -> None:
        try:
            ms = max(0.0, float(seconds)) * 1000.0
        except (TypeError, ValueError):
            return
        with self._lock:
            self.model_ms += ms
            self.model_calls += 1

    def first_word(self) -> None:
        """The first word of this reply is being shown now (kept once)."""
        if self.first_word_ms is None:
            try:
                self.first_word_ms = max(0, int(round((now() - self.started) * 1000)))
            except Exception:  # noqa: BLE001
                pass

    # -- the row's columns -------------------------------------------------
    def fields(self) -> "dict[str, Any]":
        """ONLY what is known: a reply whose model calls were never timed has
        no ``model_ms`` (None on the row, never a made-up 0)."""
        out: dict[str, Any] = {}
        with self._lock:
            calls, ms = self.model_calls, self.model_ms
        if calls > 0:
            out["model_calls"] = int(calls)
            out["model_ms"] = max(0, int(round(ms)))
        if self.first_word_ms is not None:
            out["first_word_ms"] = int(self.first_word_ms)
        return out


_FACTS: "ContextVar[ReplyFacts | None]" = ContextVar("iron_jarvis_reply_facts", default=None)


def begin() -> ReplyFacts:
    """A chat reply starts (both lanes, at the top, before any model call of
    the reply): its facts become the current ones for everything the reply
    awaits or starts. Not reset at the end: the next reply begins its own,
    and the row is a snapshot read when the reply ends."""
    facts = ReplyFacts()
    _FACTS.set(facts)
    return facts


@contextlib.contextmanager
def scope() -> Iterator[ReplyFacts]:
    """Around one agent run (beside ``call_ledger.counted_scope``): the run's
    own facts, and the facts of whatever started it (a chat reply whose tool
    delegated) are back when it ends."""
    facts = ReplyFacts()
    facts.for_run = True
    token = _FACTS.set(facts)
    try:
        yield facts
    finally:
        try:
            _FACTS.reset(token)
        except (ValueError, LookupError, RuntimeError):  # another context's token
            _FACTS.set(None)


def current() -> "ReplyFacts | None":
    try:
        return _FACTS.get()
    except Exception:  # noqa: BLE001
        return None


def note_guided() -> None:
    """THE ROUTER'S SEAM (release 9): this reply's own model call is being
    guided step by step. Kept only for a call made in the scope the reply
    began in (a tool's own model call is not this reply's). Never raises."""
    try:
        facts = _FACTS.get()
        if facts is None or _call_scope() is not facts._home:
            return
        facts.guided = True
    except Exception:  # noqa: BLE001 — a record never breaks a request
        pass


def note_model_time(seconds: Any) -> None:
    """THE ROUTER'S SEAM: one model call answered after *seconds*. Counted on
    the current reply only when the call is the reply's own (made in the
    scope the reply began in). Cheap, sync, never raises."""
    try:
        facts = _FACTS.get()
        if facts is None or _call_scope() is not facts._home:
            return
        facts.note_model_time(seconds)
    except Exception:  # noqa: BLE001 — a record never breaks a request
        pass


__all__ = [
    "ReplyFacts", "begin", "clock", "current", "note_guided", "note_model_time", "now", "scope",
]
