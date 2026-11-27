"""The seams: where a settled reply, and a finished run, write to the record
and get their line back.

BOTH CHAT LANES make the same call at the same place (MIRROR NOTE:
``daemon/chat_turn.run_chat_turn`` and ``daemon/routes/chat.py``'s stream
lane): :func:`checked_for_turn`, once the reply and its notes have settled.
It writes the reply's existing checks (a claim the check-again loop left
open, the required files), checks what the files the reply's own tools wrote
say, writes those too, then READS THE RECORD BACK and builds the line from
the rows THAT WRITE made (by their own ids, ``record.by_ids``: never by the
turn's id, which is the caller's choice and which two chats, or one chat
twice, may send). A reply of a chat that is Local only when it is written
marks its rows ``kept_home`` (:func:`reply_is_local_only`): after the chat
is switched off Local only, no reader for it hands them to a model.
The answer carries the line as ``checked`` only when a file was made or
changed; a reply that made none shows no line and writes no content row.

A REPLY NEVER WAITS LONG ON THE RECORD. The whole of a file reply's work (the
files, the write, the read back) is inside ONE cap (:data:`TURN_CAP_S`), of
which the record's own share is :data:`RECORD_WAIT_S`; a reply that made no
file waits :data:`RECORD_WAIT_S` at most on its row. Past the cap the reply
goes out with NO line (never a line the record does not hold); a write that
was already on its way still lands, on its own thread, bounded by the
database's own wait. Nothing here keeps a set of background tasks.

THE CHAT a reply's rows belong to: the one the request names. A new chat's
first reply names none: its rows are written under the turn, and AFTER the
write the reply looks for its chat (:func:`_chat_of`: the one the page
linked the turn to, ``providers/egress.linked_thread``, as the script worker
resolves it; else the daemon-owned turn's live chat) and homes the rows
itself. ONE place, after the write, so a link that landed before the reply
ended and one that landed while the rows were being written are the same
case. A link that lands later homes them from the route
(``routes/chat.link_chat_turn`` -> ``record.home_turn``, which that route
calls AFTER it has made the link readable here).

AN AGENT RUN's files are checked when the run is settled
(``Orchestrator._settle_finished_run``, already off the event loop):
:func:`settle_run`. Each settle says what the run's files hold AFRESH: the
checks of its files from an earlier settle are replaced, so a finding about
a file the run has since put right, removed or dropped does not stay
behind. Its result's receipt reads the line from the record
(:func:`run_payload`), for the files the card lists and that are still on
disk.

Nothing here sends the model back, calls a model, publishes an event or
raises.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import time
import uuid
from typing import Any, Callable, Iterable

from . import content, existing, record

log = logging.getLogger(__name__)

#: One file reply's whole check takes this long at most: its files, the
#: write to the record and the read back of its line.
TURN_CAP_S = 3.0
#: The record's share of that (the write, the read back). A reply that made
#: no file waits this long at most on its one row.
RECORD_WAIT_S = 1.0


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
    cap_s: "float | None" = None,
    record_wait_s: "float | None" = None,
    wait: "bool | None" = None,
    spawn: "Callable[[Callable[[], Any]], Any] | None" = None,
) -> "dict[str, Any] | None":
    """One chat reply's checks, written, and its line read back from the
    record (``record.payload``: ``{line, tone, checks, more, more_words}``),
    or None when the reply made no file, or when the work did not fit its
    cap (*cap_s*, default :data:`TURN_CAP_S`; *record_wait_s*, default
    :data:`RECORD_WAIT_S`).

    *made_docs* is the lane's own record of the files its tools wrote or
    changed (absolute paths, from the tools' results); *reply* the model's
    own words, read only for a page count it states of a PDF it made. A
    coding session (*coding*) has its own line (the finish check), and a
    reply that hands off to an agent run (*escalate*) leaves the checking to
    that run: neither gets a line here, and a hand-off writes no row.

    *wait* False: the caller draws no line. The reply does not wait for its
    checks AT ALL: the same rows are written on a worker thread
    (:func:`_checked_later`; *spawn* starts it, for a test) and None comes
    back at once. "What did you check?" still finds them. Left out (None),
    the lanes tell by the one marker they already set: a reply that answers
    a PHONE channel (:func:`draws_no_line`) does not wait; every other reply
    does."""
    try:
        engine = getattr(platform, "engine", None)
        if engine is None:
            return None
        if wait is False or (wait is None and draws_no_line()):
            turn = str(turn_id or "").strip() or f"reply-{uuid.uuid4().hex[:16]}"
            docs = [] if coding or escalate else [str(p) for p in (made_docs or ()) if p]
            work = functools.partial(_checked_later, engine, turn, str(thread_id or "").strip(), docs, until,
                                     deliverables, bool(escalate), clock)
            if spawn is not None:
                spawn(work)
            else:
                # The caller's context goes with it (the turn's own chat is read from it).
                asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, work)
            return None
        # A reply with no id of its own (the phone, an API caller) still
        # needs one for its rows to be read back as ITS rows.
        turn = str(turn_id or "").strip() or f"reply-{uuid.uuid4().hex[:16]}"
        docs = [str(p) for p in (made_docs or ()) if p]
        made = bool(docs) and not coding and not escalate
        record_wait = max(0.0, RECORD_WAIT_S if record_wait_s is None else float(record_wait_s))
        cap = max(0.0, TURN_CAP_S if cap_s is None else float(cap_s)) if made else record_wait
        files_wait = max(0.0, min(content.HARD_WAIT_S, cap - record_wait))
        try:
            return await asyncio.wait_for(
                _checked(engine, turn, str(thread_id or "").strip(), docs if made else [], str(reply or ""),
                         until, deliverables, escalate, clock, files_wait),
                cap,
            )
        except asyncio.TimeoutError:
            # The reply goes out now, with no line. (A write already on its
            # way finishes on its own thread; the record may hold the rows.)
            log.info("a reply's checks did not fit their time: the reply goes out with no line")
            return None
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a line is never worth a failed reply
        log.debug("a reply's checks could not be recorded", exc_info=True)
        return None


async def _checked(engine: Any, turn: str, thread_id: str, docs: "list[str]", reply: str, until: Any,
                   deliverables: Any, escalate: bool, clock: Callable[[], float],
                   files_wait: float) -> "dict[str, Any] | None":
    """The work :func:`checked_for_turn` bounds. *docs* is empty for a reply
    that gets no line (it made no file, a coding session, a hand-off)."""
    # The checks of the made files come first in the record (and so in the
    # list a press opens); the reply's older checks follow them.
    rows: list[record.Check] = []
    if docs:
        rows += await content.check_made_files(docs, reply, clock=clock, hard_wait_s=files_wait)
    if not escalate:
        rows += existing.required_file_checks(getattr(deliverables, "states", None) or ())
        claims = existing.claims_check(until)
        if claims is not None:
            rows.append(claims)
    if not rows:
        return None
    # The rows hold file names, sheet names and figures: a reply of a chat
    # that is Local only NOW keeps them home, whatever the chat is later.
    home = await asyncio.to_thread(reply_is_local_only, engine, thread_id)
    # THIS write's rows, by their own ids: the line is read back by them and
    # a new chat's rows are homed by them. Never by the turn's id (the
    # caller's own choice: two new chats, or one chat twice, may send one).
    mine: list[str] = []
    await record.note(engine, rows, thread_id=thread_id, turn_id=turn, kept_home=home, written=mine)
    if not thread_id:
        # A NEW chat's first reply: the page names its chat with a link, at
        # any moment (before this reply ends, while its rows are being
        # written, or later). The chat is looked for HERE, after the write,
        # whichever of the first two it was; a later link homes the rows
        # itself (``routes/chat.link_chat_turn``).
        chat = _chat_of(turn)
        if chat:
            await asyncio.to_thread(record.home_ids, engine, mine, chat)
    if not docs:
        return None
    kept = await asyncio.to_thread(record.by_ids, engine, mine)
    return record.payload(kept) if record.has_content(kept) else None


def reply_is_local_only(engine: Any, thread_id: str) -> bool:
    """BLOCKING. Is the reply in flight a Local only one: the turn's own
    Local only scope is on (the request said so, or its saved chat does:
    ``providers/egress.current_scope``), or its saved chat *thread_id* is
    Local only NOW? Its rows are then marked ``kept_home``. True when it
    cannot be told (in doubt, the words stay home)."""
    try:
        from ..providers import egress

        if bool(egress.current_scope().on):
            return True
        return bool(thread_id) and bool(egress.thread_local_only(engine, thread_id))
    except Exception:  # noqa: BLE001
        log.debug("a reply's Local only setting could not be read", exc_info=True)
        return True


def draws_no_line() -> bool:
    """Is the reply in flight for a caller that never draws the line: one
    that answers a phone channel (``providers/failure_words.phone_surface``,
    armed by ``comm/inbound`` and by the desktop's reply into a phone
    thread)? False in any doubt: the reply then waits, as the page's does."""
    try:
        from ..providers.failure_words import SURFACE_PHONE, current_surface

        return current_surface() == SURFACE_PHONE
    except Exception:  # noqa: BLE001
        return False


def _checked_later(engine: Any, turn: str, thread_id: str, docs: "list[str]", until: Any, deliverables: Any,
                   escalate: bool, clock: Callable[[], float]) -> int:
    """BLOCKING. A reply's checks for a caller that draws no line: the same
    rows :func:`_checked` writes, with nobody waiting on them (the files get
    a finished run's longer look). Returns the rows written; never raises."""
    try:
        rows: list[record.Check] = []
        if docs:
            rows += content.check_files(docs, clock=clock, deadline_s=content.RUN_DEADLINE_S)
        if not escalate:
            rows += existing.required_file_checks(getattr(deliverables, "states", None) or ())
            claims = existing.claims_check(until)
            if claims is not None:
                rows.append(claims)
        if not rows:
            return 0
        mine: list[str] = []
        written = record.write(engine, rows, thread_id=thread_id, turn_id=turn,
                               kept_home=reply_is_local_only(engine, thread_id), written=mine)
        if not thread_id:
            chat = _chat_of(turn)
            if chat:
                record.home_ids(engine, mine, chat)     # these rows, not every row of this turn id
        return written
    except Exception:  # noqa: BLE001 — a record is never worth anything of the reply's
        log.debug("a reply's checks could not be recorded", exc_info=True)
        return 0


def _chat_of(turn_id: str) -> str:
    """The saved chat a reply with no chat in its request belongs to: the
    one the page linked this turn to (either lane; the POST lane has no
    daemon-owned turn to ask), else the daemon-owned turn's live chat. ""
    = not named yet. Never raises."""
    try:
        from ..providers.egress import linked_thread

        linked = str(linked_thread(turn_id) or "")
        if linked:
            return linked
    except Exception:  # noqa: BLE001
        log.debug("a turn's linked chat could not be read", exc_info=True)
    return _live_thread()


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
                      session_id: str = "", ended: str = "") -> None:
    """A coding reply's finish check (its tests, its review), written to the
    record. MIRROR NOTE: both chat lanes, where they add ``finish``. *ended*
    is the finish check's own answer to how a failed test run ended
    (``FinishCheck.tests_ended``): only a command that ran to its end and
    reported failure is a problem. The reply waits :data:`RECORD_WAIT_S` at
    most on it."""
    try:
        rows = existing.finish_checks(finish, str(ended or ""))
        if rows and (turn_id or session_id):
            engine = getattr(platform, "engine", None)

            async def write() -> None:
                home = await asyncio.to_thread(reply_is_local_only, engine, str(thread_id or ""))
                await record.note(engine, rows, thread_id=str(thread_id or ""), turn_id=str(turn_id or ""),
                                  session_id=str(session_id or ""), kept_home=home)

            await asyncio.wait_for(write(), RECORD_WAIT_S)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a timeout included: the reply goes on
        log.debug("a coding reply's finish check could not be recorded", exc_info=True)


def settle_run(engine: Any, session_id: str, documents: "Iterable[Any] | None", *, thread_id: str = "",
               reply: str = "", clock: Callable[[], float] = time.monotonic, kept_home: "bool | None" = None,
               chat_local_only: "Callable[[Any, str], bool] | None" = None) -> int:
    """BLOCKING. A finished run's made files (read from its undo journal by
    ``outcome.session_result``: ``documents``), checked by rule and written
    under the run's session, IN PLACE OF the checks of its files an earlier
    settle wrote (``record.write(replace_content=True)``): a run that
    carried on and put a file right, removed it or made another says what
    its files hold NOW, with nothing left over.

    *kept_home*: the run's words stay on this PC (a Local only run, or one
    whose report keeps its words home). Its rows hold file names, sheet
    names and figures, so they are marked, and they are written under the
    chat (*thread_id*) ONLY when that chat is Local only itself, asked NOW
    (*chat_local_only*, default ``providers/egress.thread_local_only``; a
    read that fails is "no": the rows get no chat). Left out (None), it is
    read from the run's own row (:func:`_keeps_home`), so a caller that
    forgets to say cannot put such a run's rows under a chat. Returns the
    rows written; never raises."""
    try:
        docs = [str(p) for p in (documents or ()) if p]
        if not str(session_id or ""):
            return 0
        rows = content.check_files(docs, str(reply or ""), clock=clock,
                                   deadline_s=content.RUN_DEADLINE_S) if docs else []
        chat = str(thread_id or "")
        if kept_home is None:
            kept_home = _keeps_home(engine, str(session_id))
        if kept_home and chat and not _is_local_only(engine, chat, chat_local_only):
            chat = ""       # a chat that is not Local only never owns these rows
        return record.write(engine, rows, session_id=str(session_id), thread_id=chat,
                            replace_content=True, kept_home=bool(kept_home))
    except Exception:  # noqa: BLE001 — a record is never worth a failed finalize
        log.debug("a run's made files could not be checked", exc_info=True)
        return 0


def _keeps_home(engine: Any, session_id: str) -> bool:
    """BLOCKING. Does the run *session_id* keep its words on this PC, read
    from its own row (``agents/chat_delivery.keeps_words_home``)? False for
    a run with no row; True when the row cannot be read (in doubt, home)."""
    try:
        from ..agents import chat_delivery
        from ..core.db import session_scope
        from ..core.models import Session

        with session_scope(engine) as db:
            row = db.get(Session, session_id)
            return bool(row is not None and chat_delivery.keeps_words_home(row))
    except Exception:  # noqa: BLE001
        log.debug("a run's own row could not be read", exc_info=True)
        return True


def _is_local_only(engine: Any, thread_id: str, ask: "Callable[[Any, str], bool] | None" = None) -> bool:
    """Is this saved chat Local only NOW? False in any doubt (the words then
    stay home)."""
    try:
        if ask is None:
            from ..providers.egress import thread_local_only as ask
        return bool(ask(engine, thread_id))
    except Exception:  # noqa: BLE001
        log.debug("a chat's Local only setting could not be read", exc_info=True)
        return False


def run_owner(engine: Any, session: Any) -> "dict[str, Any]":
    """BLOCKING. Under which chat a RUN's rows are written, and whether they
    are marked as kept home: ``{thread_id, kept_home}``, for EVERY writer of
    a run's rows (its files' checks when it is settled, each step's check,
    the check-again verdict). A run whose words stay on this PC
    (``agents/chat_delivery.keeps_words_home``: a Local only run, or one
    whose report keeps its words home) writes under its chat ONLY when that
    chat is Local only itself, asked now. In any doubt: no chat, kept home."""
    try:
        from ..agents import chat_delivery

        chat = str(chat_delivery.chat_thread_of(session) or "")
        home = bool(chat_delivery.keeps_words_home(session))
    except Exception:  # noqa: BLE001
        log.debug("a run's chat could not be read", exc_info=True)
        return {"thread_id": "", "kept_home": True}
    if home and chat and not _is_local_only(engine, chat):
        chat = ""
    return {"thread_id": chat, "kept_home": home}


def may_read_home(engine: Any, thread_id: str) -> bool:
    """BLOCKING. May the reply in flight be handed ``kept_home`` rows: is it
    Local only itself, NOW (the turn's own Local only scope is on, or its
    saved chat *thread_id* is Local only)? The READER's side of
    :func:`reply_is_local_only`, and the other way round in doubt: False
    when it cannot be told (the words then stay home)."""
    try:
        from ..providers import egress

        if bool(egress.current_scope().on):
            return True
    except Exception:  # noqa: BLE001
        log.debug("a reply's Local only scope could not be read", exc_info=True)
        return False
    return bool(thread_id) and _is_local_only(engine, thread_id)


def home_rows(rows: "list[dict[str, Any]]", local_only: bool) -> "list[dict[str, Any]]":
    """*rows* as a chat may read them: the rows of a run that keeps its
    words on this PC are left out unless the chat is Local only itself."""
    return rows if local_only else [r for r in rows if not r.get("kept_home")]


def _row_key(row: "dict[str, Any]") -> "tuple[str, str]":
    """One made file in the record: by its path's key where the row has one
    (every row a content check writes), else by its name and its folder's."""
    key = str(row.get("path_key") or "")
    if key:
        return key, ""
    return str(row.get("file") or "").lower(), str(row.get("folder") or "").lower()


def _match(rows: "list[dict[str, Any]]", documents: "Iterable[Any] | None") -> "tuple[dict[tuple[str, str], str], list[str]]":
    """Which checked file is which listed document. ``({the key of a checked
    file: its document's path}, the documents no check covers)``. A document
    is matched BY ITS PATH (``record.path_key``: a digest of the whole path,
    the same one the check wrote): never by a name two files may share, and
    never by whichever of two same-named files the card lists first. A
    worker's ``summary.xlsx`` is not covered by the check of the lead's, and
    the check of a file that is gone never shows off another file. (A row
    with no path key, from a writer that had none, matches by the file's
    name and its folder's name, one document a row.)"""
    keys = list(dict.fromkeys(_row_key(r) for r in rows
                              if r.get("check") in record.CONTENT_CHECKS and r.get("file")))
    free = set(keys)
    took: dict[tuple[str, str], str] = {}
    missing: list[str] = []
    for doc in dict.fromkeys(str(d) for d in (documents or ()) if d):
        name = record.file_name(doc).lower()
        parent = record.file_name(doc.replace("\\", "/").rstrip("/").rpartition("/")[0]).lower()
        pick = next((k for k in ((record.path_key(doc), ""), (name, parent), (name, "")) if k in free), None)
        if pick is None:
            missing.append(doc)
            continue
        free.discard(pick)
        took[pick] = doc
    return took, missing


def _on_disk(path: str) -> bool:
    try:
        return os.path.exists(path)
    except OSError:
        return False


def _as_checked(rows: "list[dict[str, Any]]", key: "tuple[str, str]", path: str) -> bool:
    """Is the file at *path* still the file these rows were written about:
    on disk, with the size and the last-written time the check stored
    (``record.file_stamp``)? A row says what a file held WHEN IT WAS
    CHECKED; once the file has changed (the person's own edit, a later
    reply's) its row is not shown. A row from a writer that stored no stamp
    is held only to "the file is on disk"."""
    now = record.file_stamp(path)
    if not now:
        return False
    stamps = {str(r.get("stamp") or "") for r in rows
              if r.get("check") in record.CONTENT_CHECKS and r.get("file") and _row_key(r) == key}
    stamps.discard("")
    return not stamps or now in stamps


def not_covered(rows: "list[dict[str, Any]]", documents: "Iterable[Any] | None") -> "dict[str, Any] | None":
    """A result card may list files this run's own checks never looked at
    (a team lead's card lists its workers' documents; a run that made more
    than :data:`content.MAX_FILES`). ONE ``not_checked`` row that says so,
    by name, so the line and its list never read as if they covered them;
    None when every listed file has a check. Worked out at the read, from
    the record's rows and the card's own list (:func:`_match`: by the
    document's path, never by a bare name alone): it is not stored."""
    _took, missing = _match(rows, documents)
    # Only the kinds Iron Jarvis opens: a picture or a slide deck on the card
    # is checked by nothing, and nothing is said about it.
    names = list(dict.fromkeys(record.file_name(d) for d in missing if content.opened_kind(d)))
    if not names:
        return None
    n = len(names)
    shown = ", ".join(record.quoted(x) for x in names[:record.MAX_LINE_FILES])
    rest = n - min(n, record.MAX_LINE_FILES)
    return {
        "check": record.FILE_OPENS, "file": "", "folder": "", "result": record.NOT_CHECKED, "by": record.BY_RULE,
        "detail": (f"{n:,} other file{'' if n == 1 else 's'} on this card {'was' if n == 1 else 'were'} not checked "
                   f"here: {shown}" + (f" and {rest:,} more" if rest > 0 else "")),
    }


def _is_leftover(row: "dict[str, Any]") -> bool:
    """The row a check of more files than its cap writes ("2 more files were
    not checked: only the first 6 are"): about no file by name."""
    return (row.get("check") == record.FILE_OPENS and not row.get("file")
            and row.get("result") == record.NOT_CHECKED)


def run_payload(engine: Any, session_id: str, documents: "Iterable[Any] | None" = None) -> "dict[str, Any] | None":
    """BLOCKING. The line of a run's result, from the record; None when the
    record holds no check of a file the run made. *documents* is the list
    the result card shows:

    * a check of a file that is no longer on that list, no longer on disk,
      or CHANGED since it was checked (:func:`_as_checked`) is not shown
      (the card speaks of the files it lists, as they are now);
    * a file on it that no check of this run covers is said as not checked,
      ONCE and by name (:func:`not_covered`; the cap's own "2 more files"
      row gives way to it)."""
    try:
        if documents is not None and not [d for d in documents if d]:
            return None     # a card that lists NO file has no file's line
        rows = record.for_session(engine, session_id)
        docs = [str(d) for d in (documents or ()) if d]
        listed = bool(docs)
        if listed:
            took, _missing = _match(rows, docs)
            shown = {key for key, path in took.items() if _as_checked(rows, key, path)}
            rows = [r for r in rows if r.get("check") not in record.CONTENT_CHECKS or not r.get("file")
                    or _row_key(r) in shown]
            docs = [d for d in docs if _on_disk(d)]
        if not record.has_content([r for r in rows if r.get("file")] if listed else rows):
            return None
        extra = not_covered(rows, docs)
        if extra is not None:
            rows = [r for r in rows if not _is_leftover(r)] + [extra]
        return record.payload(rows)
    except Exception:  # noqa: BLE001
        log.debug("a run's checks could not be read", exc_info=True)
        return None


def line_stays_home(engine: Any, session_id: str) -> bool:
    """BLOCKING. Must a run's line be left off a result that is read from
    OUTSIDE the run (``GET /sessions/{id}/result``: a chat's page draws the
    run's card from it and saves it on the chat)? The rule of the delivered
    card and of the tool: a run that keeps its words on this PC
    (``agents/chat_delivery.keeps_words_home``) shows its line only in a
    chat that is Local only itself, asked NOW; with no chat, or when any of
    it cannot be read, the line stays home (True)."""
    try:
        from ..agents import chat_delivery
        from ..core.db import session_scope
        from ..core.models import Session

        with session_scope(engine) as db:
            row = db.get(Session, str(session_id or ""))
            if row is None or not chat_delivery.keeps_words_home(row):
                return False
            chat = str(chat_delivery.chat_thread_of(row) or "")
    except Exception:  # noqa: BLE001
        log.debug("a run's own row could not be read", exc_info=True)
        return True
    return not (chat and _is_local_only(engine, chat))


#: How many of a chat's rows one reopening reads at most.
REOPEN_READ = 2_000


def reopened(engine: Any, thread_id: str, messages: Any) -> Any:
    """BLOCKING. A saved chat's messages as they are SERVED when the chat
    is opened again: a reply's saved line (``checked``) says what its files
    held when the reply ended, so the checks of a file that has CHANGED
    since (or is gone) are taken off it, and the line is built again from
    the record's remaining rows of that reply (no line when no check of a
    file is left). Told by the stamp each row stored (:func:`_as_checked`)
    against the reply's own list of files (``documents``). A reply whose
    files are as they were, one with no rows left in the record, and
    anything that cannot be read come back exactly as saved. Nothing is
    written: the saved chat is not changed. Never raises."""
    try:
        marked = [m for m in messages if isinstance(m, dict) and isinstance(m.get("checked"), dict)
                  and m.get("turnId") and isinstance(m.get("documents"), list)]
        if not marked or not str(thread_id or ""):
            return messages
        by_turn: dict[str, list[dict[str, Any]]] = {}
        for r in reversed(record.for_thread(engine, str(thread_id), limit=REOPEN_READ)):
            by_turn.setdefault(str(r.get("turn_id") or ""), []).append(r)
        for m in marked:
            rows = by_turn.get(str(m.get("turnId")), [])
            if not rows:
                continue
            took, _missing = _match(rows, [d for d in m["documents"] if isinstance(d, str)])
            stale = {key for key, path in took.items() if not _as_checked(rows, key, path)}
            if not stale:
                continue
            left = [r for r in rows if not (r.get("check") in record.CONTENT_CHECKS and r.get("file")
                                            and _row_key(r) in stale)]
            fresh = record.payload(left) if record.has_content(left) else None
            if fresh is None:
                m.pop("checked", None)
            else:
                m["checked"] = fresh
    except Exception:  # noqa: BLE001 — a line is never worth a chat that does not open
        log.debug("a reopened chat's checked lines could not be looked at again", exc_info=True)
    return messages


__all__ = ["RECORD_WAIT_S", "TURN_CAP_S", "checked_for_turn", "line_stays_home", "may_read_home", "not_covered",
           "note_finish", "reopened", "reply_is_local_only", "run_payload", "settle_run"]
