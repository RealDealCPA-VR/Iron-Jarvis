"""The one record of what Iron Jarvis checked (``checkrecord``), and the
line a person reads from it.

One row per check: when, what it belongs to (a saved chat, a chat turn, an
agent run's session, a coding session, a workflow run and its step: whichever
apply), the check's code, the file's NAME where it is about a file, the
result (``passed`` | ``failed`` | ``look`` | ``not_checked``), a detail in
plain words ("C7 on “Summary” holds #DIV/0!") and who checked (``rule`` for
Iron Jarvis's own check, said "by Iron Jarvis"; or the checking model's
name).

FOUR OUTCOMES. Every phrase a person reads must be literally true by
construction, the good ones too:

* ``passed``: Iron Jarvis did this, and it is so. The words say what Iron
  Jarvis did ("opens": it opened the file with its own reader), never what
  another program will show.
* ``failed`` ("Problem") is an ALLOWLIST of facts (:data:`PROBLEM_CHECKS`),
  not a default: a file of a kind Iron Jarvis really opens that its own
  reader could not open (and that the disk did hand over); a cell whose
  STORED type is an error; a test command that ran to its end and reported
  failure; a required file that is not on disk.
* ``look`` ("Worth a look"): everything Iron Jarvis inferred, read out of
  prose or was told by a model, said with no verdict: a typed total that
  differs from the list above it, a formula it works out to an error, a
  document in which it found no text or that is empty, a claim of the reply
  the check-again loop found no step for, a judging model's or a reviewer's
  failed check (never its words).
* ``not_checked``: it could not be checked, with the reason. Where Iron
  Jarvis cannot tell at all, there is no row.

A row whose check is not on the allowlist can never be stored, read, shown
or answered as ``failed``, whoever wrote it: the record turns it into
``look`` at the write (:func:`_clean`) and at every read (:func:`_result`),
and a payload's item carries its check's code so the same holds for a
payload that is saved or decoded (:func:`clean_checked`, the page's
``decodeChecked``).

NAMES A MODEL CHOSE (a file's, a folder's, a step's) are shown inside curly
quotes and cut to a sane length (:func:`quoted`), so a name can never read
as part of Iron Jarvis's own sentence.

WHAT A ROW MAY HOLD. Names, counts, sizes and plain words (a made file by
its NAME; a required file by the path the task asked for it at). Never a
file's content beyond the one cell or figure a finding names, and the
detail is secret-masked before it is stored and never cut inside a finding
(:func:`fit`). A made file's row also holds the file's size and
last-written time when it was checked (``stamp``): a reader that knows
where the file is shows the row only while the file still has them. Rows
stay on this PC: nothing here publishes an event, and nothing that sends to
another computer reads them.

WHOSE ROWS. A reply's rows are known by their OWN ids (``write(written=)``,
:func:`by_ids`, :func:`home_ids`): a turn's id is the caller's choice, and
two chats (or one chat, twice) may send one.

BOUNDED. A write keeps at most :data:`MAX_ROWS_PER_WRITE` rows; a row older
than :data:`KEEP_DAYS` is removed by the daily tidy-up
(``daemon/app._event_prune_loop``) and by the first write of a day
(:func:`prune`); a deleted chat's rows go with it (:func:`drop_thread`).

THE LINE (:func:`line_for`, :func:`payload`) is built here, by the daemon,
from rows alone: never from a model's words, and never by a page.

BLOCKING throughout (callers hop with ``asyncio.to_thread``; :func:`note` is
the hop). Nothing here raises: a check's record is never worth a failed reply.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete
from sqlalchemy import literal_column
from sqlalchemy import update as sa_update
from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope, storable_text
from ..core.ids import utcnow

log = logging.getLogger(__name__)

PASSED = "passed"
FAILED = "failed"
LOOK = "look"
NOT_CHECKED = "not_checked"
RESULTS = (PASSED, FAILED, LOOK, NOT_CHECKED)

#: ``by`` for a check Iron Jarvis made itself, by rule (no model). It is
#: SAID "by Iron Jarvis" (:func:`who_words`): "by rule" read like the
#: product's own Rules page.
BY_RULE = "rule"
BY_IRON_JARVIS = "by Iron Jarvis"

# The checks, by code. The first five are made by :mod:`.content` on a file a
# reply or a run made; the rest are the checks the app already made, which
# now also write here (:mod:`.existing`).
FILE_OPENS = "file_opens"
SHEET_ERRORS = "sheet_no_error_cells"
SHEET_TOTALS = "sheet_totals"
DOC_TEXT = "document_not_empty"
PDF_PAGES = "pdf_pages"
CLAIMS = "claims_match_record"
REQUIRED_FILE = "required_file"
TESTS = "tests"
REVIEW = "review"
STEP_CHECK = "step_check"
WORKFLOW_STEP = "workflow_step"

#: The checks of what a made file says. The line under a reply is drawn only
#: when the reply's record holds at least one of these.
CONTENT_CHECKS = frozenset({FILE_OPENS, SHEET_ERRORS, SHEET_TOTALS, DOC_TEXT, PDF_PAGES})
CHECKS = CONTENT_CHECKS | {CLAIMS, REQUIRED_FILE, TESTS, REVIEW, STEP_CHECK, WORKFLOW_STEP}
#: THE ALLOWLIST. Only a row of one of these checks may be ``failed``
#: ("Problem"): each holds a fact, never an inference, a reading of prose or
#: a model's opinion.
#:
#: * ``file_opens``: a file of a kind Iron Jarvis really opens that the
#:   reader the app's own tools use could not open ("Iron Jarvis could not
#:   open “letter.docx”");
#: * ``sheet_no_error_cells``: a cell whose TYPE is an error;
#: * ``tests``: a test command that ran to its end and reported failure;
#: * ``required_file``: a required file that is not on disk.
#:
#: A ``failed`` row of ANY other check is said, stored and answered as
#: ``look`` ("Worth a look"). The writers of the four keep to the facts
#: above (:mod:`.content`, :mod:`.existing`).
PROBLEM_CHECKS = frozenset({FILE_OPENS, SHEET_ERRORS, TESTS, REQUIRED_FILE})

#: The words that open the line. They are the words a coding reply's line
#: already uses (``hosted/proof.LINE_HEAD`` / ``NOT_CHECKED``); a test holds
#: the two equal.
LINE_HEAD = "Checked by Iron Jarvis:"
LINE_NOT_CHECKED = "Not checked:"
#: The words before what Iron Jarvis worked out itself and found different.
LINE_LOOK = "Worth a look:"

#: A row older than this is removed.
KEEP_DAYS = 90
#: At most this many rows from one write (one reply, one run's finalize).
MAX_ROWS_PER_WRITE = 60
DETAIL_MAX = 300
NAME_MAX = 200
#: At most this many checks in the list a press opens; the rest are counted.
MAX_LISTED = 30
#: The line names at most this many files, problems and unchecked things.
MAX_LINE_FILES = 3
MAX_LINE_PROBLEMS = 3
#: The line is at most this long: the number the page shows
#: (``dashboard/lib/checked.ts``; a test holds the two equal).
LINE_MAX = 1_200
#: How many rows one read returns at most.
MAX_READ = 200


def new_check_id() -> str:
    return f"chk_{uuid.uuid4().hex[:16]}"


class CheckRecord(SQLModel, table=True):
    """One check Iron Jarvis made, and what it found."""

    __tablename__ = "checkrecord"

    id: str = Field(default_factory=new_check_id, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    #: The saved chat ("" = none, or not yet: :func:`home_turn`).
    thread_id: str = Field(default="", index=True)
    #: The chat turn (a reply). "" for a run's rows.
    turn_id: str = Field(default="", index=True)
    #: An agent run's session, or a coding session. Never the shared "chat".
    session_id: str = Field(default="", index=True)
    #: An agent run, or a workflow run.
    run_id: str = Field(default="", index=True)
    #: A plan step's number or a workflow step's name ("" = not about a step).
    step: str = ""
    check: str = ""
    #: The file's NAME (never its folder) where the check is about a file.
    file: str = ""
    #: Only for two made files of ONE name in two folders: the folder's own
    #: name (one name, never a path), so the two are told apart. Kept apart
    #: from ``file``: a file really called "Budget (in USD).xlsx" stays so.
    folder: str = ""
    result: str = NOT_CHECKED
    detail: str = ""
    #: ``rule``, or the checking model's name.
    by: str = BY_RULE
    #: A made file's own key (a digest of its whole path, :func:`path_key`;
    #: never the path): a run's card matches a check to the file it lists by
    #: this, never by a name two files may share. "" = not about a made file.
    path_key: str = ""
    #: The run these rows belong to keeps its words on this PC (a Local only
    #: run): a chat that is not Local only never reads them.
    kept_home: bool = False
    #: A made file as it was when it was checked (:func:`file_stamp`: its
    #: size and when it was last written; never its path or its content). A
    #: reader that knows where the file is shows the row only while the file
    #: still has this stamp. "" = not about a made file, or it was not read.
    stamp: str = ""


@dataclass(frozen=True)
class Check:
    """One check's answer, before it is written."""

    check: str
    result: str
    detail: str = ""
    file: str = ""
    by: str = BY_RULE
    step: str = ""
    run_id: str = ""
    folder: str = ""
    #: Where the file is, as a digest (:func:`path_key`). Not part of what a
    #: check SAYS: two checks that say the same are equal whatever it is.
    path_key: str = field(default="", compare=False)
    #: The file as it was when it was checked (:func:`file_stamp`). Not part
    #: of what a check says either.
    stamp: str = field(default="", compare=False)


def path_key(path: Any) -> str:
    """A made file's key in the record: a digest of its whole path as the
    file system spells it (so two files of one name in two folders have two
    keys). Never the path itself; "" for no path."""
    text = str(path or "").strip()
    if not text:
        return ""
    spelled = os.path.normcase(os.path.normpath(os.path.abspath(text)))
    return hashlib.sha256(spelled.encode("utf-8", "replace")).hexdigest()[:24]


def file_stamp(path: Any) -> str:
    """A file as it is on disk NOW, as one short text: its size and when it
    was last written. Two stamps that differ mean the file changed (or is
    gone: "" for a file that cannot be asked). Never raises."""
    try:
        st = os.stat(str(path))
        return f"{int(st.st_size)}:{int(st.st_mtime_ns)}"
    except (OSError, ValueError):
        return ""


def file_name(path: Any) -> str:
    """The NAME of a file (its folder is never kept)."""
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.rpartition("/")[2][:NAME_MAX]


def asked_path(path: Any) -> str:
    """A REQUIRED file as the task asked for it ("OUTPUT/summary.xlsx"): the
    task's own relative path, with forward slashes. Two required files of
    one name in two folders are two rows, and "not on disk" is said of the
    place that was asked for (a file of that name one folder up is another
    file)."""
    text = "/".join(part for part in str(path or "").replace("\\", "/").split("/") if part.strip())
    return text[-NAME_MAX:]


def fit(detail: str, cap: int = 0) -> str:
    """A check's words at the length the record keeps (:data:`DETAIL_MAX`),
    never cut inside a finding: findings are parted by "; ", whole ones are
    dropped from the end and "and more" says so. One finding that is too
    long by itself is cut at a word, and the cut is marked ("…")."""
    cap = cap or DETAIL_MAX
    if len(detail) <= cap:
        return detail
    parts = detail.split("; ")
    tail = "; and more"
    for n in range(len(parts) - 1, 0, -1):
        said = "; ".join(parts[:n]) + tail
        if len(said) <= cap:
            return said
    return detail[:cap - 2].rsplit(" ", 1)[0].rstrip(" ,;:(") + " …"


FOLDER_MAX = 60
#: A name is shown with at most this many characters (the rest is "…").
SHOWN_NAME_MAX = 80
QUOTE_OPEN, QUOTE_CLOSE = "“", "”"
_QUOTE_RX = re.compile("[“”„‟\"«»]")


def quoted(name: Any, cap: int = SHOWN_NAME_MAX) -> str:
    """A name somebody else chose (a model named the file, the sheet, the
    step), as Iron Jarvis shows it: inside curly quotes, cut to *cap*
    characters, with any quote mark of its own turned into an apostrophe. A
    file called "all totals verified and correct.md" then reads as a NAME,
    never as part of Iron Jarvis's sentence. "" stays ""."""
    text = _QUOTE_RX.sub("'", " ".join(seen_text(name).split()))
    if not text:
        return ""
    if len(text) > cap:
        text = text[:max(1, cap - 1)].rstrip() + "…"
    return f"{QUOTE_OPEN}{text}{QUOTE_CLOSE}"


def apart(name: Any, folder: Any) -> str:
    """Two made files with the same name in two folders, as they are SHOWN:
    the folder's OWN name beside the file's (“summary.xlsx” (in “2023”)),
    each a quoted name (:func:`quoted`). One folder name, never a path. The
    record keeps the two in two fields (``file``, ``folder``): this is only
    how a row is said."""
    base, where = quoted(file_name(name)), quoted(file_name(folder)[:FOLDER_MAX])
    return f"{base} (in {where})" if where else base


def shown_name(row: Any) -> str:
    """A row's file as the line and the list name it."""
    return apart(row.get("file"), row.get("folder"))


#: Characters that never belong in a line a person reads: the control
#: characters (NUL, ESC ...) and the ones that turn text round (the bidi
#: marks, embeddings, overrides and isolates). The page's decoder strips the
#: same set (``dashboard/lib/checked.ts``; a shared fixture holds the two).
_UNSEEN_RX = re.compile("[\\u0000-\\u001f\\u007f-\\u009f\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")


def seen_text(value: Any) -> str:
    """*value* without the characters nobody sees (:data:`_UNSEEN_RX`); a
    tab or a line break becomes a space."""
    return _UNSEEN_RX.sub("", re.sub(r"[\t\r\n]+", " ", str(value or "")))


_ENGINE_TAIL_RX = re.compile(r"\s*\([A-Z][A-Za-z0-9_.]*(?::[^)]*)?\)?\s*$")


def plain_words(text: Any) -> str:
    """A reason an older check built, without an exception's name at its
    end ("it cannot be checked (OSError)" reads "it cannot be checked")."""
    return _ENGINE_TAIL_RX.sub("", " ".join(str(text or "").split()))


_READY: set[int] = set()
_PRUNED: dict[int, float] = {}
_DAY_S = 24 * 3600.0


def ensure_table(engine: Any) -> bool:
    """Create the table if it is missing (once per engine). Never raises."""
    key = id(engine)
    if key in _READY:
        return True
    try:
        CheckRecord.__table__.create(bind=engine, checkfirst=True)
        _READY.add(key)
        return True
    except Exception:  # noqa: BLE001 — a record is never worth a failed reply
        log.warning("the record of checks is unavailable", exc_info=True)
        return False


def _clean(check: Check) -> "Check | None":
    """A check as it is stored: a known result, a masked and clipped detail,
    a file NAME. None for one that says nothing."""
    code = " ".join(str(check.check or "").split())[:64]
    if not code:
        return None
    result = check.result if check.result in RESULTS else NOT_CHECKED
    if result == FAILED and code not in PROBLEM_CHECKS:
        # "Problem" is an allowlist of facts (PROBLEM_CHECKS). Anything else
        # that did not pass is "Worth a look", whoever built the row.
        result = LOOK
    try:
        from ..detections.redact import mask

        detail = mask(" ".join(seen_text(check.detail).split()))
    except Exception:  # noqa: BLE001 — unmaskable words are not stored
        detail = ""
    # A made file is kept by its NAME. A required file is kept as the task
    # asked for it (its own relative path): "not on disk" is said of a place.
    named = asked_path(check.file) if code == REQUIRED_FILE else file_name(check.file)
    return Check(
        check=code, result=result, detail=fit(str(storable_text(detail))),
        file=str(storable_text(seen_text(named))),
        by=(" ".join(seen_text(check.by).split()) or BY_RULE)[:120],
        step=" ".join(seen_text(check.step).split())[:120], run_id=str(check.run_id or "")[:120],
        folder=str(storable_text(seen_text(file_name(check.folder))))[:FOLDER_MAX],
        path_key=re.sub(r"[^0-9a-f]", "", str(check.path_key or ""))[:24],
        stamp=re.sub(r"[^0-9:]", "", str(check.stamp or ""))[:48],
    )


def write(
    engine: Any,
    checks: Iterable[Check],
    *,
    thread_id: str = "",
    turn_id: str = "",
    session_id: str = "",
    run_id: str = "",
    clock: Any = time.time,
    replace_content: bool = False,
    kept_home: bool = False,
    written: "list[str] | None" = None,
) -> int:
    """Write *checks* for one owner (a chat turn, or a session). A row this
    owner already holds for the same check, file and step is replaced, so a
    file checked again (a run that carried on) is said once. A REPLY's owner
    is its turn AND its chat: a turn's id is the caller's own choice, and
    two chats that send one id never replace each other's rows. A reply
    with NO chat yet (a new chat's first reply) replaces nothing at all:
    whose a row of that turn id is cannot be told.

    *written*: a list the ids of the rows THIS call wrote are added to. A
    reply's line is read back by these (:func:`by_ids`), never by its turn
    id, which another reply may share.

    *replace_content* (a run that is settled again): EVERY check of a made
    file this owner holds is removed first, in the same transaction, even
    when *checks* is empty. What the run's files say is said afresh each
    time: a finding about a file that was put right, removed, or is no
    longer one of the run's files does not stay behind.

    *kept_home*: the rows belong to a run whose words stay on this PC (a
    Local only run), or to a reply of a chat that was Local only when it
    was written. They are marked, and every reader for a chat that is not
    Local only leaves them out.

    Returns how many rows were written; never raises."""
    try:
        rows = [c for c in (_clean(c) for c in checks) if c is not None][:MAX_ROWS_PER_WRITE]
        sid = "" if str(session_id or "") == "chat" else str(session_id or "")
        tid, turn = str(thread_id or ""), str(turn_id or "")
        rid = str(run_id or "")
        owned = bool(turn or sid or rid)
        # A replace removes the made-file checks of ONE owner (a run id alone
        # owns its own rows, and only those). With no owner there is nothing
        # it may remove: it would take the rows of every run-owned check.
        replace = bool(replace_content and owned)
        if not (rows or replace) or not ensure_table(engine):
            return 0
        if not (owned or all(c.run_id for c in rows)):
            return 0
        _prune_daily(engine, clock)
        # A reply's rows are its turn's AND its chat's. (A run's rows are its
        # session's whatever chat they sit under: a run that is settled again
        # may have another answer to "is its chat Local only now?".)
        mine = [CheckRecord.thread_id == tid] if turn else []
        # A turn with no session and no chat yet: its id alone does not say
        # whose rows these are (two new chats may send one id). Nothing of
        # that id is removed for it.
        own = bool(sid or tid or not turn)
        ids: list[str] = []
        with session_scope(engine) as db:
            if replace and own:
                db.exec(sa_delete(CheckRecord).where(
                    CheckRecord.turn_id == turn, CheckRecord.session_id == sid, *mine,
                    CheckRecord.check.in_(sorted(CONTENT_CHECKS)),  # type: ignore[attr-defined]
                    *([CheckRecord.run_id == rid] if rid else []),
                ))
            for key in {(c.check, c.file, c.folder, c.step, c.run_id or rid) for c in rows} if own else ():
                db.exec(sa_delete(CheckRecord).where(
                    CheckRecord.turn_id == turn, CheckRecord.session_id == sid, *mine,
                    CheckRecord.check == key[0], CheckRecord.file == key[1], CheckRecord.folder == key[2],
                    CheckRecord.step == key[3], CheckRecord.run_id == key[4],
                ))
            # One write's rows keep the order they were handed in (a clock
            # that ticks slower than the loop would tie them).
            now = utcnow()
            for i, c in enumerate(rows):
                ids.append(new_check_id())
                db.add(CheckRecord(
                    id=ids[-1], created_at=now + timedelta(microseconds=i),
                    thread_id=tid, turn_id=turn, session_id=sid, run_id=c.run_id or rid,
                    step=c.step, check=c.check, file=c.file, folder=c.folder, result=c.result, detail=c.detail,
                    by=c.by, path_key=c.path_key, kept_home=bool(kept_home), stamp=c.stamp,
                ))
            db.commit()
        if written is not None:
            written.extend(ids)
        return len(rows)
    except Exception:  # noqa: BLE001 — a record is never worth a failed reply
        log.warning("a check could not be written to the record", exc_info=True)
        return 0


async def note(engine: Any, checks: Iterable[Check], **owner: Any) -> int:
    """:func:`write`, off the event loop. Never raises (a cancel still does)."""
    rows = list(checks or ())
    if not rows or engine is None:
        return 0
    try:
        return await asyncio.to_thread(write, engine, rows, **owner)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.debug("a check's record was not written", exc_info=True)
        return 0


def as_dict(row: CheckRecord) -> dict[str, Any]:
    return {
        "id": row.id, "at": row.created_at.isoformat() if row.created_at else "",
        "thread_id": row.thread_id, "turn_id": row.turn_id, "session_id": row.session_id,
        "run_id": row.run_id, "step": row.step, "check": row.check, "file": row.file, "folder": row.folder,
        "result": row.result, "detail": row.detail, "by": row.by,
        "path_key": str(row.path_key or ""), "kept_home": bool(row.kept_home), "stamp": str(row.stamp or ""),
    }


def _read(engine: Any, *where: Any, limit: int = MAX_READ, newest: bool = False) -> list[dict[str, Any]]:
    try:
        if not ensure_table(engine):
            return []
        # Two writes in one clock tick (a Windows clock is coarse) tie on
        # created_at; the id is random, so the tie is broken by the order
        # the rows were WRITTEN (SQLite's rowid), the same way round.
        written = literal_column("rowid")
        order = (CheckRecord.created_at.desc(), written.desc()) if newest else (  # type: ignore[attr-defined]
            CheckRecord.created_at, written)
        with session_scope(engine) as db:
            got = db.exec(
                select(CheckRecord).where(*where).order_by(*order).limit(max(1, int(limit)))
            ).all()
            return [as_dict(r) for r in got]
    except Exception:  # noqa: BLE001
        log.debug("the record of checks could not be read", exc_info=True)
        return []


def for_turn(engine: Any, turn_id: str) -> list[dict[str, Any]]:
    """One reply's checks, oldest first."""
    turn = str(turn_id or "")
    return _read(engine, CheckRecord.turn_id == turn) if turn else []


def by_ids(engine: Any, ids: "Iterable[str]") -> list[dict[str, Any]]:
    """The rows ONE write wrote (``write(written=...)``), oldest first, read
    back from the record: a reply's own rows, whoever else used its turn
    id."""
    wanted = [str(i) for i in (ids or ()) if i][:MAX_ROWS_PER_WRITE]
    return _read(engine, CheckRecord.id.in_(wanted)) if wanted else []  # type: ignore[attr-defined]


def home_ids(engine: Any, ids: "Iterable[str]", thread_id: str) -> int:
    """Give the rows ONE write wrote (before its chat had an id) their
    chat: these rows and no other row of their turn id. Never raises."""
    try:
        wanted = [str(i) for i in (ids or ()) if i][:MAX_ROWS_PER_WRITE]
        if not wanted or not thread_id or not ensure_table(engine):
            return 0
        with engine.begin() as conn:
            res = conn.execute(
                sa_update(CheckRecord)
                .where(CheckRecord.id.in_(wanted), CheckRecord.thread_id == "")  # type: ignore[attr-defined]
                .values(thread_id=str(thread_id))
            )
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001
        log.debug("a reply's checks were not given their chat", exc_info=True)
        return 0


def for_session(engine: Any, session_id: str, limit: int = MAX_READ) -> list[dict[str, Any]]:
    """One run's (or coding session's) checks, oldest first. A long run
    holds more rows than one read returns: the NEWEST *limit* are read (a
    file checked last is never the one that falls out)."""
    sid = str(session_id or "")
    if not sid or sid == "chat":
        return []
    return list(reversed(_read(engine, CheckRecord.session_id == sid, limit=limit, newest=True)))


def for_thread(engine: Any, thread_id: str, limit: int = MAX_READ) -> list[dict[str, Any]]:
    """A saved chat's checks, NEWEST first."""
    tid = str(thread_id or "")
    return _read(engine, CheckRecord.thread_id == tid, limit=limit, newest=True) if tid else []


def home_turn(engine: Any, turn_id: str, thread_id: str) -> int:
    """Give a new chat's first reply's rows (written before the chat had an
    id) their chat. Never raises."""
    try:
        if not turn_id or not thread_id or not ensure_table(engine):
            return 0
        with engine.begin() as conn:
            res = conn.execute(
                sa_update(CheckRecord)
                .where(CheckRecord.turn_id == str(turn_id), CheckRecord.thread_id == "")
                .values(thread_id=str(thread_id))
            )
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001
        log.debug("a reply's checks were not given their chat", exc_info=True)
        return 0


def drop_thread(db: Any, thread_id: str) -> None:
    """Remove a deleted chat's rows, inside the caller's transaction."""
    if str(thread_id or ""):
        db.exec(sa_delete(CheckRecord).where(CheckRecord.thread_id == str(thread_id)))


def prune(engine: Any, *, days: int = KEEP_DAYS, now: "datetime | None" = None) -> int:
    """Remove rows older than *days*. Returns how many; never raises."""
    try:
        if not ensure_table(engine):
            return 0
        cutoff = (now or utcnow()) - timedelta(days=max(1, int(days)))
        with engine.begin() as conn:
            res = conn.execute(sa_delete(CheckRecord).where(CheckRecord.created_at < cutoff))
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001
        log.debug("old checks could not be removed", exc_info=True)
        return 0


def _prune_daily(engine: Any, clock: Any) -> None:
    """The first write of a day removes old rows too (the desktop app is
    often closed before the daily tidy-up comes round)."""
    try:
        now = float(clock())
        last = _PRUNED.get(id(engine))
        if last is not None and now - last < _DAY_S:
            return
        _PRUNED[id(engine)] = now
        prune(engine)
    except Exception:  # noqa: BLE001
        log.debug("the daily removal of old checks was skipped", exc_info=True)


# --------------------------------------------------------------------------- #
# The words: built from rows alone
# --------------------------------------------------------------------------- #
#: What ONE made file is called, for the kinds Iron Jarvis really opens (the
#: only kinds a content check writes a row for). Each is singular: the words
#: after it are "opens", "has text". Every other kind is "the file".
_KIND_WORDS = {
    ".xlsx": "the spreadsheet", ".xlsm": "the spreadsheet",
    ".csv": "the table", ".tsv": "the table",
    ".docx": "the document", ".md": "the document", ".markdown": "the document", ".txt": "the document",
    ".pdf": "the PDF",
}
#: What a row that is not about a file is called on the line and in the list.
_SUBJECTS = {
    CLAIMS: "the reply", TESTS: "the tests", REVIEW: "the review",
}
_RESULT_WORDS = {PASSED: "Checked", FAILED: "Problem", LOOK: "Worth a look", NOT_CHECKED: "Not checked"}


def kind_words(name: str) -> str:
    """"the spreadsheet" for summary.xlsx: what one made file is called (by
    the file's own name; the folder that tells two apart is another field)."""
    return _KIND_WORDS.get(os.path.splitext(str(name or ""))[1].lower(), "the file")


def _result(row: dict[str, Any]) -> str:
    """A row's result as it is SAID: only a check on the allowlist
    (:data:`PROBLEM_CHECKS`) is ever a "Problem", even from a row written
    before the rule or by another writer; any other ``failed`` is "Worth a
    look"."""
    result = row.get("result")
    return LOOK if result == FAILED and row.get("check") not in PROBLEM_CHECKS else str(result or "")


_MODEL_FAMILY = r"(opus|sonnet|haiku)"
_MODEL_VER = r"(\d{1,2})(?:[-.](\d{1,2}))?"
_MODEL_TAIL = r"(?:-\d{8}|-latest)?"
_MODEL_NEW_RX = re.compile(rf"^claude-{_MODEL_FAMILY}-{_MODEL_VER}{_MODEL_TAIL}$", re.IGNORECASE)
_MODEL_OLD_RX = re.compile(rf"^claude-{_MODEL_VER}-{_MODEL_FAMILY}{_MODEL_TAIL}$", re.IGNORECASE)
_MODEL_1M_RX = re.compile(r"\[1m\]$", re.IGNORECASE)


def friendly_model(name: Any) -> str:
    """A model id as people say it ("claude-sonnet-4-5" reads "Sonnet 4.5"):
    the daemon's twin of the page's own helper
    (``dashboard/lib/friendlyModelName.ts``; a test holds the two to the same
    cases). Only the family and the version numbers the id already spells;
    every other name comes back unchanged, so nothing is invented."""
    raw = " ".join(str(name or "").split())
    big = _MODEL_1M_RX.search(raw) is not None
    bare = _MODEL_1M_RX.sub("", raw)
    m = _MODEL_NEW_RX.match(bare)
    if m is not None:
        family, major, minor = m.groups()
    else:
        m = _MODEL_OLD_RX.match(bare)
        if m is None:
            return raw
        major, minor, family = m.groups()
    said = f"{family.capitalize()} {major}" + (f".{minor}" if minor is not None else "")
    return f"{said} 1M" if big else said


def who_words(by: Any) -> str:
    """"by Iron Jarvis" for its own check, or "by <the model's name>" (a
    model id in the words people say, :func:`friendly_model`; the row keeps
    the name it was given)."""
    name = " ".join(str(by or "").split())
    return BY_IRON_JARVIS if not name or name == BY_RULE else f"by {friendly_model(name)}"


def _subject(row: dict[str, Any], one_file: bool) -> str:
    """What a row is about, as the line names it."""
    check, name, step = str(row.get("check") or ""), str(row.get("file") or ""), str(row.get("step") or "")
    if check in CONTENT_CHECKS:
        if not name:
            return "the other files"
        return kind_words(name) if one_file else shown_name(row)
    if check == REQUIRED_FILE:
        return f"required file {quoted(name)}" if name else "a required file"
    if check in (STEP_CHECK, WORKFLOW_STEP):
        # A plan step is a number; a workflow step's name is somebody's words.
        return f"step {step if step.isdigit() else quoted(step)}" if step else "a step"
    return _SUBJECTS.get(check, "the work")


def _label(row: dict[str, Any]) -> str:
    """What a row is about, as the LIST names it (a file by its name)."""
    check, name = str(row.get("check") or ""), str(row.get("file") or "")
    if check in CONTENT_CHECKS and name:
        return shown_name(row)
    said = _subject(row, False)
    return said[:1].upper() + said[1:]


def _and_more(items: list[str], cap: int, noun: str = "") -> str:
    shown = items[:cap]
    rest = len(items) - len(shown)
    if rest > 0:
        shown.append(f"and {rest} more{(' ' + noun + ('s' if rest != 1 else '')) if noun else ''}")
    return "; ".join(shown)


def line_for(rows: Iterable[dict[str, Any]]) -> "tuple[str, str]":
    """``(line, tone)``: the ONE line under a reply or a run's result, from
    the record's rows: what passed, then "Problem in ..." (what the file
    itself holds), then "Worth a look: ..." (what Iron Jarvis worked out
    itself and found different), then "Not checked: ...". Tone ``warn`` when
    there is a problem or something worth a look, else ``quiet``. ``("",
    "quiet")`` for no rows. Pure."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("result") in RESULTS]
    if not rows:
        return "", "quiet"
    files = list(dict.fromkeys(
        (str(r.get("file") or ""), str(r.get("folder") or ""))
        for r in rows if r.get("check") in CONTENT_CHECKS and r.get("file")
    ))
    one_file = len(files) <= 1
    passed: list[str] = []
    for name, folder in files:
        said = [str(r.get("detail") or "") for r in rows
                if r.get("check") in CONTENT_CHECKS and str(r.get("file") or "") == name
                and str(r.get("folder") or "") == folder and _result(r) == PASSED and r.get("detail")]
        if said:
            passed.append(f"{kind_words(name) if one_file else apart(name, folder)} {', '.join(said)}")
    problems = [_about(r, one_file, "it did not pass") for r in rows if _result(r) == FAILED]
    looks = [_look_words(r, one_file) for r in rows if _result(r) == LOOK]
    unchecked = [f"{_subject(r, one_file)} ({r.get('detail') or 'no reason was kept'})"
                 for r in rows if _result(r) == NOT_CHECKED]

    def build(n_files: int, n_problems: int, n_looks: int, n_unchecked: int) -> str:
        parts: list[str] = []
        if passed:
            parts.append(f"{LINE_HEAD} {_and_more(passed, n_files, 'file')}.")
        if problems:
            said = _and_more(problems, n_problems)
            parts.append(f"Problem in {said}." if passed else f"{LINE_HEAD} a problem in {said}.")
        if looks:
            parts.append(f"{LINE_LOOK} {_and_more(looks, n_looks)}.")
        if unchecked:
            parts.append(f"{LINE_NOT_CHECKED} {_and_more(unchecked, n_unchecked)}.")
        return " ".join(parts)

    # The line is at most LINE_MAX long (the page shows no more). One that
    # would be longer names fewer things and COUNTS the rest ("and 2 more"):
    # what was not checked gives way first, then the files that passed, then
    # what is worth a look, and the problems last. It is never cut in the
    # middle of a finding.
    caps = [MAX_LINE_FILES, MAX_LINE_PROBLEMS, MAX_LINE_PROBLEMS, MAX_LINE_PROBLEMS]
    line = build(*caps)
    for which in (3, 0, 2, 1, 3, 0, 2, 1):
        if len(line) <= LINE_MAX:
            break
        if caps[which] > 1:
            caps[which] -= 1
            line = build(*caps)
    if len(line) > LINE_MAX:    # one finding of each kind, and still too long
        line = line[:LINE_MAX - 2].rsplit(" ", 1)[0].rstrip(" ,;:(") + " …"
    return line, ("warn" if problems or looks else "quiet")


def _about(row: dict[str, Any], one_file: bool, default: str) -> str:
    """A problem as the line says it: what it is about, then the finding."""
    return f"{_subject(row, one_file)}: {row.get('detail') or default}"


def _look_words(row: dict[str, Any], one_file: bool) -> str:
    """One "worth a look" finding as the line says it: what was found, no
    verdict. A finding about the file as a whole ("it is empty") names the
    file; one about a cell, and one that names Iron Jarvis ("Iron Jarvis
    found no text in it"), stands as it is under the one file, and after the
    file's name among several."""
    detail = str(row.get("detail") or "it is not what was worked out")
    if row.get("check") not in CONTENT_CHECKS or not row.get("file"):
        return f"{_subject(row, one_file)}: {detail}"
    name = shown_name(row)
    if detail.startswith("it "):
        return f"{name} {detail[3:]}"
    return detail if one_file else f"{name}: {detail}"


_ORDER = {FAILED: 0, LOOK: 1, NOT_CHECKED: 2, PASSED: 3}


def payload(rows: Iterable[dict[str, Any]], *, limit: int = MAX_LISTED) -> "dict[str, Any] | None":
    """What a page draws: ``{line, tone, checks: [{check, label, said,
    result, result_words, who}], more, more_words}``; None for no rows. The
    list has the problems first, then what is worth a look, then what was
    not checked, then what passed; ``more`` counts the checks past *limit*
    (``more_words`` says so: "and 3 more checks", "" when none). Each item
    carries its check's CODE, so whoever saves or decodes the payload can
    hold "Problem" to the allowlist (:func:`clean_checked`). Pure."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("result") in RESULTS]
    if not rows:
        return None
    line, tone = line_for(rows)
    ordered = sorted(rows, key=lambda r: _ORDER[_result(r)])
    shown = ordered[:max(1, int(limit))]
    return {
        "line": line,
        "tone": tone,
        "checks": [
            {
                "check": str(r.get("check") or ""), "label": _label(r), "said": str(r.get("detail") or ""),
                "result": _result(r), "result_words": _RESULT_WORDS[_result(r)], "who": who_words(r.get("by")),
            }
            for r in shown
        ],
        "more": len(ordered) - len(shown),
        "more_words": _more_words(len(ordered) - len(shown)),
    }


def _more_words(rest: int) -> str:
    return f"and {rest:,} more check{'s' if rest != 1 else ''}" if rest > 0 else ""


def _text(value: Any, cap: int) -> str:
    return seen_text(value).strip()[:cap] if isinstance(value, str) else ""


def clean_checked(raw: Any) -> "dict[str, Any] | None":
    """A ``checked`` payload as it may be SAVED on a chat reply: by
    whitelist, the shape :func:`payload` builds and the page's decoder
    accepts (``dashboard/lib/checked.ts``, the same caps), and nothing else.
    None for one that says nothing (no line) or is not a payload at all. A
    payload this module built comes back equal. Pure; never raises."""
    try:
        if not isinstance(raw, dict):
            return None
        line = _text(raw.get("line"), LINE_MAX)
        if not line:
            return None
        checks: list[dict[str, Any]] = []
        listed = raw.get("checks")
        for item in listed if isinstance(listed, list) else []:
            if not isinstance(item, dict) or item.get("result") not in RESULTS:
                continue
            label = _text(item.get("label"), 240)
            if not label:
                continue
            # "Problem" is the allowlist's, read from the item's own check
            # code: an item with no code, a code nobody knows, or a code off
            # the list can never come out "Problem" (it is "Worth a look").
            code = item.get("check") if isinstance(item.get("check"), str) and item.get("check") in CHECKS else ""
            result = _result({"check": code, "result": item["result"]})
            # The result's WORD is this module's, read from the result: a
            # payload that says "look" under the word "Checked" is not kept
            # as it came (the page's decoder does the same).
            checks.append({
                "check": code, "label": label, "said": _text(item.get("said"), 400), "result": result,
                "result_words": _RESULT_WORDS[result], "who": _text(item.get("who"), 140),
            })
            if len(checks) >= MAX_LISTED:
                break
        more = raw.get("more")
        more = int(more) if isinstance(more, (int, float)) and not isinstance(more, bool) and 0 < more < 10**9 else 0
        # The tone is read from the ITEMS too: a payload that says "quiet"
        # over a problem or something worth a look is not kept quiet.
        warn = raw.get("tone") == "warn" or any(c["result"] in (FAILED, LOOK) for c in checks)
        return {
            "line": line, "tone": "warn" if warn else "quiet", "checks": checks,
            "more": more, "more_words": _text(raw.get("more_words"), 80) if more else "",
        }
    except Exception:  # noqa: BLE001 — a line is never worth a failed save
        log.debug("a reply's checked line could not be read", exc_info=True)
        return None


def has_content(rows: Iterable[dict[str, Any]]) -> bool:
    """Does the record hold a check of a made file? (The line is drawn only
    then: a reply that made no file shows none.)"""
    return any(isinstance(r, dict) and r.get("check") in CONTENT_CHECKS for r in rows)


__all__ = [
    "BY_RULE", "CHECKS", "CLAIMS", "CONTENT_CHECKS", "Check", "CheckRecord", "DOC_TEXT", "FAILED", "FILE_OPENS",
    "KEEP_DAYS", "LINE_HEAD", "LINE_MAX", "LINE_NOT_CHECKED", "MAX_LISTED", "MAX_READ", "MAX_ROWS_PER_WRITE",
    "NOT_CHECKED", "PASSED", "PDF_PAGES", "REQUIRED_FILE", "RESULTS", "REVIEW", "SHEET_ERRORS", "SHEET_TOTALS",
    "FOLDER_MAX", "LINE_LOOK", "LOOK", "PROBLEM_CHECKS", "path_key", "quoted", "seen_text", "shown_name",
    "STEP_CHECK", "TESTS", "WORKFLOW_STEP", "apart", "as_dict", "clean_checked", "drop_thread", "ensure_table",
    "file_name", "for_session", "for_thread", "for_turn", "friendly_model", "has_content", "home_turn",
    "kind_words", "line_for", "BY_IRON_JARVIS", "DETAIL_MAX", "asked_path", "by_ids", "file_stamp", "fit",
    "home_ids",
    "new_check_id", "note", "payload", "plain_words", "prune", "who_words", "write",
]
