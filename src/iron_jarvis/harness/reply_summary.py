"""A LONG CODING REPLY KEEPS ITS MEMORY (v1.392.0, the long-jobs plan's
Phase 3 — "never stalls silently").

THE GAP. A coding session's reply has no step limit (v1.385.0), so it can run
hundreds of rounds. Past the model's window, the lane only DROPPED its oldest
rounds (``chat_turn._fit_turn_transcript``) with a typed recap of at most
900 characters — the model forgot what it had read, which files it had
changed and why, and read them again. Agent runs had solved this since
v1.153.0: at the ceiling they summarize their own older steps.

THE SAME MACHINERY, NOT A COPY. Every round, before the transcript is fitted,
:class:`ReplySummary` hands the reply's own part of the conversation — its
question first, then every round — to ``AgentRuntime._maybe_compact``: the
same RAW-demand threshold (``context_compaction.auto_at``, about 90% of the
model's window), the same coverage from the beginning in whole BLOCKS (an
assistant turn and its tool results are one unit; the newest
``KEEP_RECENT_BLOCKS`` are kept as they are; the question is never covered),
the same model-written record checked line by line against the LEDGER — here
the session's own ``build:<id>`` calls made since the reply began
(``harness/compact.ledger_facts``), so a file path the record does not show
is cut — and the same futility guard. A re-summary covers from the beginning
again with the earlier summary fed back in as ``prior``.

THE REPLY'S OWN MODEL, BILLED. The summary is written by the reply's model
through the router with ``pin=True`` (the v1.372.0 hold binds it; Local only
holds), and its usage joins the reply's receipt — its ``model_call`` frame
(``kind: "summary"``) is drawn on the Timeline. HONEST MOCK: the demo model
writes none (before the call: the reply runs on it; after: the router served
it). A summary that fails, comes back empty or is cut to nothing changes
nothing: the reply keeps today's trimming; a failed try is not repeated for
:data:`RETRY_ROUNDS` rounds (each try is a model call on the user's plan).

WHAT IT CHANGES. Only what the MODEL is sent: from then on the summary rides
the system prompt and the rounds it covers leave the transcript (as whole
units). The reply's own record (``reply_record``), its steps and its line are
untouched. The line and the Timeline say it happened: "Summarized N earlier
steps to fit the model".
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable

log = logging.getLogger(__name__)

#: The frame a coding reply sends when it wrote a summary of its own older
#: rounds (``{steps, covers, stripped, times}``).
SUMMARY_FRAME = "summarized"
#: The ``model_call`` kind of the summary's own call (the Timeline's words).
CALL_KIND = "summary"
#: A failed try waits this many rounds before the next.
RETRY_ROUNDS = 3
#: The summary call is bounded (a CLI can take a while; the reply waits).
SUMMARY_TIMEOUT_S = 240.0


def summarized_words(steps: int) -> str:
    """"Summarized 12 earlier steps to fit the model"."""
    n = max(0, int(steps or 0))
    return f"Summarized {n} earlier step{'' if n == 1 else 's'} to fit the model"


def _is_mock(provider: Any, reason: Any = "") -> bool:
    return str(provider or "").strip().lower() == "mock" or str(reason or "").strip().lower() == "mock"


def _steps_in(messages: list[Any]) -> int:
    """How many tool calls (the run log's steps) these messages hold."""
    n = 0
    for m in messages:
        if getattr(m, "role", "") == "assistant":
            n += len(getattr(m, "tool_calls", None) or [])
    return n


class ReplySummary:
    """One coding reply's summary of its own older rounds (see the module
    doc). ``since`` (naive UTC) is when the reply began — the original
    question of a reply carried on after a restart — the ledger window that
    may back the summary."""

    def __init__(self, platform: Any, thread_id: str, *, since: "datetime | None" = None) -> None:
        self.platform = platform
        self.thread_id = str(thread_id or "")
        self.since = since
        self.summary = ""
        self.covers = 0
        self.futile = False
        #: Summaries written in this reply, and the steps the newest covers.
        self.times = 0
        self.steps = 0
        self._wait_until = -1

    # -- the transcript the model is sent ----------------------------------- #

    @staticmethod
    def _question(msgs: list[Any], head: int) -> int:
        q = min(head, len(msgs)) - 1
        while q >= 0 and getattr(msgs[q], "role", "") != "user":
            q -= 1
        return q

    def apply(self, msgs: list[Any], system: str, head: int) -> tuple[list[Any], str]:
        """``(messages, system)`` with the summary standing in for the rounds
        it covers — the copy the round fits and sends; ``msgs`` is never
        edited (the loop keeps appending to it, the record keeps it whole)."""
        if not self.summary or self.covers <= 0 or len(msgs) <= head:
            return msgs, system
        return [*msgs[:head], *msgs[head + self.covers:]], f"{system}\n\n{self.summary}"

    # -- the step ------------------------------------------------------------ #

    def _ledger(self) -> Callable[[], tuple[set[str], set[str]]]:
        engine = getattr(self.platform, "engine", None)
        since = self.since

        def _read() -> tuple[set[str], set[str]]:
            from .compact import ledger_facts

            windows = [(since, datetime(9999, 1, 1))] if since is not None else None
            return ledger_facts(engine, self.thread_id, windows)

        return _read

    async def step(
        self, d: Any, msgs: list[Any], system: str, head: int, *, provider: str, model: str,
        round_index: int, on_call: "Callable[[Any, float], None] | None" = None,
    ) -> "dict[str, Any] | None":
        """Maybe write (or rewrite) the summary before this round's call.
        The frame data when a NEW summary was written, else None. Never
        raises: a failure leaves the reply exactly as it was."""
        try:
            return await self._step(d, msgs, system, head, provider=provider, model=model,
                                    round_index=round_index, on_call=on_call)
        except Exception:  # noqa: BLE001 — an optimisation never breaks a reply
            log.warning("a coding reply's summary failed; keeping the transcript as it was", exc_info=True)
            return None

    async def _step(self, d: Any, msgs: list[Any], system: str, head: int, *, provider: str, model: str,
                    round_index: int, on_call: "Callable[[Any, float], None] | None") -> "dict[str, Any] | None":
        if self.futile or round_index < self._wait_until or _is_mock(provider):
            return None
        from ..daemon.chat_turn import _context_window

        window = _context_window(d, provider, model)
        if not window or len(msgs) <= head:
            return None
        q = self._question(msgs, head)
        if q < 0:
            return None
        # The agent runtime's shape: the task (here: the question) first, then
        # the reply's own rounds; the history before the question is a fixed
        # cost, counted with the system prompt (`_fit_turn_transcript`'s rule).
        fixed = "\n".join(getattr(m, "content", "") or "" for m in msgs[:q])
        fixed += "\n".join(getattr(m, "content", "") or "" for m in msgs[q + 1:head])
        agent_msgs = [msgs[q], *msgs[head:]]
        called: list[Any] = []
        complete = self._complete(provider, model, called, on_call)
        from ..agents.runtime import AgentRuntime
        from .sessions import session_id_for

        report: dict[str, Any] = {}
        summary, covers, futile = await AgentRuntime(self.platform)._maybe_compact(
            SimpleNamespace(id=session_id_for(self.thread_id)),
            messages=agent_msgs,
            system_prompt=system + "\n" + fixed,
            summary=self.summary,
            covers=self.covers,
            futile=self.futile,
            window=window,
            sink=None,
            complete=complete,
            ledger=self._ledger(),
            report=report,
        )
        self.futile = futile
        if covers <= self.covers or not summary.strip():
            if called:
                # A try that wrote nothing (failed, empty, cut to nothing):
                # today's trimming carries on, and the next try waits.
                self._wait_until = round_index + RETRY_ROUNDS
            return None
        self.summary, self.covers = summary, covers
        self.times += 1
        self.steps = _steps_in(msgs[head:head + covers])
        return {
            "steps": self.steps,
            "covers": covers,
            "stripped": int(report.get("stripped") or 0),
            "times": self.times,
            "words": summarized_words(self.steps),
        }

    def _complete(self, provider: str, model: str, called: list[Any],
                  on_call: "Callable[[Any, float], None] | None"):
        """The summarizer: ONE pinned call on the reply's own model through
        the router (the hold binds it, Local only refuses a model off this
        network), bounded, billed through ``on_call(route, ms)``; the demo
        model's text is never a summary."""
        platform = self.platform

        async def _call(system_text: str, user: str) -> tuple[str, str, str]:
            import time

            from ..providers.adapters.base import LLMMessage

            called.append(True)
            t0 = time.perf_counter()
            route = await asyncio.wait_for(platform.router.complete(
                provider=provider or None, model=model or None,
                system=system_text, messages=[LLMMessage(role="user", content=user)], tools=[],
                session_id=None, pin=True,
            ), timeout=SUMMARY_TIMEOUT_S)
            if _is_mock(getattr(route, "provider", ""), getattr(route, "reason", "")):
                return "", "mock", ""
            if on_call is not None:
                try:
                    on_call(route, (time.perf_counter() - t0) * 1000)
                except Exception:  # noqa: BLE001 — billing never fails the summary
                    pass
            resp = getattr(route, "response", None)
            return (str(getattr(resp, "text", "") or ""), str(getattr(route, "provider", "") or ""),
                    str(getattr(route, "model", "") or ""))

        return _call


__all__ = ["CALL_KIND", "RETRY_ROUNDS", "ReplySummary", "SUMMARY_FRAME", "summarized_words"]
