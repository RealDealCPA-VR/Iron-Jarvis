"""The one record of what Iron Jarvis checked (``checkrecord``), and the
line a person reads from it.

One row per check: when, what it belongs to (a saved chat, a chat turn, an
agent run's session, a coding session, a workflow run and its step: whichever
apply), the check's code, the file's NAME where it is about a file, the
result (``passed`` | ``failed`` | ``not_checked``), a detail in plain words
("Summary!C7 holds #DIV/0!") and who checked (``rule``, or the checking
model's name).

WHAT A ROW MAY HOLD. Names, counts, sizes and plain words. Never a file's
content beyond the one cell or figure a finding names, and the detail is
secret-masked before it is stored. Rows stay on this PC: nothing here
publishes an event, and nothing that sends to another computer reads them.

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
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope, storable_text
from ..core.ids import utcnow

log = logging.getLogger(__name__)

PASSED = "passed"
FAILED = "failed"
NOT_CHECKED = "not_checked"
RESULTS = (PASSED, FAILED, NOT_CHECKED)

#: ``by`` for a check a rule made (no model).
BY_RULE = "rule"

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

#: The words that open the line. They are the words a coding reply's line
#: already uses (``hosted/proof.LINE_HEAD`` / ``NOT_CHECKED``); a test holds
#: the two equal.
LINE_HEAD = "Checked by Iron Jarvis:"
LINE_NOT_CHECKED = "Not checked:"

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
    result: str = NOT_CHECKED
    detail: str = ""
    #: ``rule``, or the checking model's name.
    by: str = BY_RULE


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


def file_name(path: Any) -> str:
    """The NAME of a file (its folder is never kept)."""
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.rpartition("/")[2][:NAME_MAX]


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
    try:
        from ..detections.redact import mask

        detail = mask(" ".join(str(check.detail or "").split()))
    except Exception:  # noqa: BLE001 — unmaskable words are not stored
        detail = ""
    return Check(
        check=code, result=result, detail=str(storable_text(detail))[:DETAIL_MAX],
        file=str(storable_text(file_name(check.file))), by=(" ".join(str(check.by or "").split()) or BY_RULE)[:120],
        step=" ".join(str(check.step or "").split())[:120], run_id=str(check.run_id or "")[:120],
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
) -> int:
    """Write *checks* for one owner (a chat turn, or a session). A row this
    owner already holds for the same check, file and step is replaced, so a
    file checked again (a run that carried on) is said once. Returns how many
    rows were written; never raises."""
    try:
        rows = [c for c in (_clean(c) for c in checks) if c is not None][:MAX_ROWS_PER_WRITE]
        sid = "" if str(session_id or "") == "chat" else str(session_id or "")
        tid, turn = str(thread_id or ""), str(turn_id or "")
        rid = str(run_id or "")
        if not rows or not (turn or sid or rid or all(c.run_id for c in rows)) or not ensure_table(engine):
            return 0
        _prune_daily(engine, clock)
        with session_scope(engine) as db:
            for key in {(c.check, c.file, c.step, c.run_id or rid) for c in rows}:
                db.exec(sa_delete(CheckRecord).where(
                    CheckRecord.turn_id == turn, CheckRecord.session_id == sid,
                    CheckRecord.check == key[0], CheckRecord.file == key[1],
                    CheckRecord.step == key[2], CheckRecord.run_id == key[3],
                ))
            # One write's rows keep the order they were handed in (a clock
            # that ticks slower than the loop would tie them).
            now = utcnow()
            for i, c in enumerate(rows):
                db.add(CheckRecord(
                    created_at=now + timedelta(microseconds=i),
                    thread_id=tid, turn_id=turn, session_id=sid, run_id=c.run_id or rid,
                    step=c.step, check=c.check, file=c.file, result=c.result, detail=c.detail, by=c.by,
                ))
            db.commit()
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
        "run_id": row.run_id, "step": row.step, "check": row.check, "file": row.file,
        "result": row.result, "detail": row.detail, "by": row.by,
    }


def _read(engine: Any, *where: Any, limit: int = MAX_READ, newest: bool = False) -> list[dict[str, Any]]:
    try:
        if not ensure_table(engine):
            return []
        order = CheckRecord.created_at.desc() if newest else CheckRecord.created_at  # type: ignore[attr-defined]
        with session_scope(engine) as db:
            got = db.exec(
                select(CheckRecord).where(*where).order_by(order, CheckRecord.id).limit(max(1, int(limit)))
            ).all()
            return [as_dict(r) for r in got]
    except Exception:  # noqa: BLE001
        log.debug("the record of checks could not be read", exc_info=True)
        return []


def for_turn(engine: Any, turn_id: str) -> list[dict[str, Any]]:
    """One reply's checks, oldest first."""
    turn = str(turn_id or "")
    return _read(engine, CheckRecord.turn_id == turn) if turn else []


def for_session(engine: Any, session_id: str) -> list[dict[str, Any]]:
    """One run's (or coding session's) checks, oldest first."""
    sid = str(session_id or "")
    return _read(engine, CheckRecord.session_id == sid) if sid and sid != "chat" else []


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
_KIND_WORDS = {
    ".xlsx": "the spreadsheet", ".xlsm": "the spreadsheet", ".xls": "the spreadsheet", ".ods": "the spreadsheet",
    ".csv": "the table", ".tsv": "the table",
    ".docx": "the document", ".doc": "the document", ".odt": "the document", ".rtf": "the document",
    ".md": "the document", ".markdown": "the document", ".txt": "the document",
    ".pdf": "the PDF", ".pptx": "the slides",
    ".png": "the picture", ".jpg": "the picture", ".jpeg": "the picture", ".gif": "the picture",
}
#: What a row that is not about a file is called on the line and in the list.
_SUBJECTS = {
    CLAIMS: "the reply", TESTS: "the tests", REVIEW: "the review",
}
_RESULT_WORDS = {PASSED: "Checked", FAILED: "Problem", NOT_CHECKED: "Not checked"}


def kind_words(name: str) -> str:
    """"the spreadsheet" for summary.xlsx: what one made file is called."""
    return _KIND_WORDS.get(os.path.splitext(str(name or ""))[1].lower(), "the file")


def who_words(by: Any) -> str:
    """"by rule", or "by <the model's name>"."""
    name = " ".join(str(by or "").split())
    return "by rule" if not name or name == BY_RULE else f"by {name}"


def _subject(row: dict[str, Any], one_file: bool) -> str:
    """What a row is about, as the line names it."""
    check, name, step = str(row.get("check") or ""), str(row.get("file") or ""), str(row.get("step") or "")
    if check in CONTENT_CHECKS:
        if not name:
            return "the other files"
        return kind_words(name) if one_file else name
    if check == REQUIRED_FILE:
        return f"required file {name}" if name else "a required file"
    if check in (STEP_CHECK, WORKFLOW_STEP):
        return f"step {step}" if step else "a step"
    return _SUBJECTS.get(check, "the work")


def _label(row: dict[str, Any]) -> str:
    """What a row is about, as the LIST names it (a file by its name)."""
    check, name = str(row.get("check") or ""), str(row.get("file") or "")
    if check in CONTENT_CHECKS and name:
        return name
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
    the record's rows; tone ``warn`` when a check failed, else ``quiet``.
    ``("", "quiet")`` for no rows. Pure."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("result") in RESULTS]
    if not rows:
        return "", "quiet"
    files = list(dict.fromkeys(
        str(r.get("file") or "") for r in rows if r.get("check") in CONTENT_CHECKS and r.get("file")
    ))
    one_file = len(files) <= 1
    passed: list[str] = []
    for name in files:
        said = [str(r.get("detail") or "") for r in rows
                if r.get("check") in CONTENT_CHECKS and str(r.get("file") or "") == name
                and r.get("result") == PASSED and r.get("detail")]
        if said:
            passed.append(f"{kind_words(name) if one_file else name} {', '.join(said)}")
    problems = [f"{_subject(r, one_file)}: {r.get('detail') or 'it did not pass'}"
                for r in rows if r.get("result") == FAILED]
    unchecked = [f"{_subject(r, one_file)} ({r.get('detail') or 'no reason was kept'})"
                 for r in rows if r.get("result") == NOT_CHECKED]
    parts: list[str] = []
    if passed:
        parts.append(f"{LINE_HEAD} {_and_more(passed, MAX_LINE_FILES, 'file')}.")
    if problems:
        said = _and_more(problems, MAX_LINE_PROBLEMS)
        parts.append(f"Problem in {said}." if passed else f"{LINE_HEAD} a problem in {said}.")
    if unchecked:
        parts.append(f"{LINE_NOT_CHECKED} {_and_more(unchecked, MAX_LINE_PROBLEMS)}.")
    return " ".join(parts), ("warn" if problems else "quiet")


_ORDER = {FAILED: 0, NOT_CHECKED: 1, PASSED: 2}


def payload(rows: Iterable[dict[str, Any]], *, limit: int = MAX_LISTED) -> "dict[str, Any] | None":
    """What a page draws: ``{line, tone, checks: [{label, said, result,
    result_words, who}], more, more_words}``; None for no rows. The list has
    the problems first, then what was not checked, then what passed; ``more``
    counts the checks past *limit* (``more_words`` says so: "and 3 more
    checks", "" when none). Pure."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("result") in RESULTS]
    if not rows:
        return None
    line, tone = line_for(rows)
    ordered = sorted(rows, key=lambda r: _ORDER[r["result"]])
    shown = ordered[:max(1, int(limit))]
    return {
        "line": line,
        "tone": tone,
        "checks": [
            {
                "label": _label(r), "said": str(r.get("detail") or ""), "result": r["result"],
                "result_words": _RESULT_WORDS[r["result"]], "who": who_words(r.get("by")),
            }
            for r in shown
        ],
        "more": len(ordered) - len(shown),
        "more_words": _more_words(len(ordered) - len(shown)),
    }


def _more_words(rest: int) -> str:
    return f"and {rest:,} more check{'s' if rest != 1 else ''}" if rest > 0 else ""


def has_content(rows: Iterable[dict[str, Any]]) -> bool:
    """Does the record hold a check of a made file? (The line is drawn only
    then: a reply that made no file shows none.)"""
    return any(isinstance(r, dict) and r.get("check") in CONTENT_CHECKS for r in rows)


__all__ = [
    "BY_RULE", "CHECKS", "CLAIMS", "CONTENT_CHECKS", "Check", "CheckRecord", "DOC_TEXT", "FAILED", "FILE_OPENS",
    "KEEP_DAYS", "LINE_HEAD", "LINE_NOT_CHECKED", "MAX_LISTED", "MAX_ROWS_PER_WRITE", "NOT_CHECKED", "PASSED",
    "PDF_PAGES", "REQUIRED_FILE", "RESULTS", "REVIEW", "SHEET_ERRORS", "SHEET_TOTALS", "STEP_CHECK", "TESTS",
    "WORKFLOW_STEP", "as_dict", "drop_thread", "ensure_table", "file_name", "for_session", "for_thread",
    "for_turn", "has_content", "home_turn", "kind_words", "line_for", "new_check_id", "note", "payload", "prune",
    "who_words", "write",
]
