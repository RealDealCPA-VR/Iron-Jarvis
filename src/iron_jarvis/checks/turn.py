"""The seams: where a settled reply, and a finished run, write to the record
and get their line back.

BOTH CHAT LANES make the same call at the same place (MIRROR NOTE:
``daemon/chat_turn.run_chat_turn`` and ``daemon/routes/chat.py``'s stream
lane): :func:`checked_for_turn`, once the reply and its notes have settled.
It writes the reply's existing checks (the check-again verdict, the required
files), checks what the files the reply's own tools wrote say, writes those
too, then READS THE RECORD BACK and builds the line from the rows it holds.
The answer carries the line as ``checked`` only when a file was made or
changed; a reply that made none shows no line and writes no content row.

AN AGENT RUN's files are checked when the run is settled
(``Orchestrator._settle_finished_run``, already off the event loop):
:func:`settle_run`. Its result's receipt reads the line from the record
(:func:`run_payload`).

Nothing here sends the model back, calls a model, publishes an event or
raises.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Iterable

from . import content, existing, record

log = logging.getLogger(__name__)


async def checked_for_turn(
    platform: Any,
    *,
    turn_id: str = "",
    thread_id: str = "",
    made_docs: "Iterable[Any] | None" = None,
    reply: str = "",
    until: Any = None,
    deliverables: Any = None,
    coding: bool = False,
    escalate: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> "dict[str, Any] | None":
    """One chat reply's checks, written, and its line read back from the
    record (``record.payload``: ``{line, tone, checks, more, more_words}``),
    or None when the reply made no file.

    *made_docs* is the lane's own record of the files its tools wrote or
    changed (absolute paths, from the tools' results); *reply* the model's
    own words, read only for a page count it states of a PDF it made. A
    coding session (*coding*) has its own line (the finish check), and a
    reply that hands off to an agent run (*escalate*) leaves the checking to
    that run: neither gets a line here."""
    try:
        engine = getattr(platform, "engine", None)
        if engine is None:
            return None
        # A reply with no id of its own (the phone, an API caller) still
        # needs one for its rows to be read back as ITS rows.
        turn = str(turn_id or "").strip() or f"reply-{uuid.uuid4().hex[:16]}"
        docs = [str(p) for p in (made_docs or ()) if p]
        made = bool(docs) and not coding and not escalate
        # The checks of the made files come first in the record (and so in
        # the list a press opens); the reply's older checks follow them.
        rows: list[record.Check] = []
        if made:
            rows += await content.check_made_files(docs, str(reply or ""), clock=clock)
        if not escalate:
            rows += existing.required_file_checks(getattr(deliverables, "states", None) or ())
            claims = existing.claims_check(until)
            if claims is not None:
                rows.append(claims)
        if not rows:
            return None
        thread = str(thread_id or "").strip() or _live_thread()
        await record.note(engine, rows, thread_id=thread, turn_id=turn)
        if not thread:
            # A NEW chat's first reply: the page names its chat with a link
            # that may land while this reply is still ending. The link homes
            # the rows written before it (``routes/chat.link_chat_turn``);
            # one that landed while these were being written is read here.
            late = _live_thread()
            if late:
                await asyncio.to_thread(record.home_turn, engine, turn, late)
        if not made:
            return None
        kept = await asyncio.to_thread(record.for_turn, engine, turn)
        return record.payload(kept) if record.has_content(kept) else None
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a line is never worth a failed reply
        log.debug("a reply's checks could not be recorded", exc_info=True)
        return None


def _live_thread() -> str:
    """The saved chat of the daemon-owned reply running now ("" = none, or
    not named yet): a new chat's first reply learns it from the page's link.
    Never raises."""
    try:
        from ..daemon.durable_turns import current_turn

        _turn, thread = current_turn()
        return str(thread or "")
    except Exception:  # noqa: BLE001
        return ""


async def note_finish(platform: Any, finish: Any, *, turn_id: str = "", thread_id: str = "",
                      session_id: str = "") -> None:
    """A coding reply's finish check (its tests, its review), written to the
    record. MIRROR NOTE: both chat lanes, where they add ``finish``."""
    try:
        rows = existing.finish_checks(finish)
        if rows and (turn_id or session_id):
            await record.note(getattr(platform, "engine", None), rows, thread_id=str(thread_id or ""),
                              turn_id=str(turn_id or ""), session_id=str(session_id or ""))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.debug("a coding reply's finish check could not be recorded", exc_info=True)


def settle_run(engine: Any, session_id: str, documents: "Iterable[Any] | None", *, thread_id: str = "",
               reply: str = "", clock: Callable[[], float] = time.monotonic) -> int:
    """BLOCKING. A finished run's made files (read from its undo journal by
    ``outcome.session_result``: ``documents``), checked by rule and written
    under the run's session. Returns the rows written; never raises."""
    try:
        docs = [str(p) for p in (documents or ()) if p]
        if not docs or not str(session_id or ""):
            return 0
        rows = content.check_files(docs, str(reply or ""), clock=clock)
        return record.write(engine, rows, session_id=str(session_id), thread_id=str(thread_id or ""))
    except Exception:  # noqa: BLE001 — a record is never worth a failed finalize
        log.debug("a run's made files could not be checked", exc_info=True)
        return 0


def run_payload(engine: Any, session_id: str) -> "dict[str, Any] | None":
    """BLOCKING. The line of a run's result, from the record; None when the
    record holds no check of a file the run made."""
    try:
        rows = record.for_session(engine, session_id)
        return record.payload(rows) if record.has_content(rows) else None
    except Exception:  # noqa: BLE001
        log.debug("a run's checks could not be read", exc_info=True)
        return None


__all__ = ["checked_for_turn", "note_finish", "run_payload", "settle_run"]
