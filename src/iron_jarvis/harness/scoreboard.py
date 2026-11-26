"""The record for this folder, in the model menu (next leaps 28-a, R2.17).

Which model is best on MY code? The model menu of a coding session says, under
each tool and model, what became of its work in this folder: "In this folder:
kept 7 of 9, passed its checks 8 of 9". Every figure carries its count; with
fewer than :data:`MIN_SAMPLES` decided pieces the line says "(too few to
judge)"; with nothing on record it says nothing. The record CHOOSES nothing
(D-28.1): it only shows the numbers, and the user picks the model.

THE UNIT is one piece of work: a REPLY that changed files (``unit`` reply, or
night for a night task's session) or one ATTEMPT of "try on several models"
(``unit`` attempt). Each is one :class:`CodingOutcome` row, ``ref`` unique
(``<session>:<turn>``, ``<session>:<turn>:<attempt>``), so a seam that fires
twice (a second press, a retried write) never counts a piece twice.

WHAT "KEPT" MEANS: the piece's changes reached the user's branch by the
user's OWN press. The seams that already write each fact settle the rows:

* Chat › Code (``routes/build_git``) and Build › Code
  (``routes/build_app_git``): Commit, Open a pull request and Merge settle
  every UNDECIDED reply row of that session as kept (``committed``,
  ``pull_request``, ``merged``); Discard settles them as thrown
  (``discarded``). Iron Jarvis's own night commit is not the user's press and
  settles nothing.
* Keep on several attempts (``attempts.keep``): the kept attempt is kept
  (``attempt_kept``), every other one that changed files thrown
  (``attempt_dropped``); Discard all (``attempts.discard``) throws them.
* A rewind that puts the files back (Chat › Code always; Build › Code with
  "and the files", or its files-only rewind) throws the rewound replies' rows
  (``rewound``). Build › Code's Undo rewind puts that rewind's rows back to
  not decided (``rewind_at`` names the rewind: :func:`unrewound`).
* Deleting a session on its own branch moves its copy of the folder to the
  trash (the Discard path, both views): its undecided replies are thrown
  (``discarded``).

A reply still sitting uncommitted is "not decided": it is counted apart
("3 not decided yet") and NEVER in an "of N".

``checked`` is Iron Jarvis's OWN verdict on the reply, never the model's
words (CLAUDE.md, "Outcomes come from the ledger"): the finish check's
``tests`` on a Chat › Code reply line, the folder's tests Iron Jarvis ran
after a Build › Code reply (``hosted/proof``, its ``after_reply`` listener).

EVERY WRITE IS BLOCKING (SQLite) and never raises: a seam calls it off the
event loop (its route already runs in a worker thread, or through
``asyncio.to_thread``), and a write that fails is logged and never fails the
press it follows.

THE BACKFILL (:func:`backfill`, once per database, off the loop at start):
the facts that already exist become rows marked ``source="backfill"`` — each
Chat › Code reply line with a finish record, each decided attempts card, each
Build › Code reply whose own file steps changed files in its folder — settled
by what the session's own facts say (``merged_at``, ``pr_url``,
``removed_at``). A Build › Code reply keeps the tool its message went to (the
user event's ``tool``), with the model that tool had last in the session. So
someone who changes nothing sees a record the first day.

``source="replay"`` is reserved for 28-c's nightly test: every reader here
leaves it out (never mixed into the real-work figures).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, col, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)

#: Fewer decided pieces than this: the line says "(too few to judge)".
MIN_SAMPLES = 5

UNIT_REPLY = "reply"
UNIT_ATTEMPT = "attempt"
UNIT_NIGHT = "night"
UNITS = (UNIT_REPLY, UNIT_ATTEMPT, UNIT_NIGHT)

#: Chat › Code (Iron Jarvis's own coding sessions) and Build › Code (the
#: user's real coding tools).
SURFACE_CODE = "code"
SURFACE_TOOL = "tool"
#: Chat › Code's coding tool is Iron Jarvis itself.
TOOL_JARVIS = "jarvis"

KEPT = "kept"
THROWN = "thrown"

HOW_MERGED = "merged"
HOW_COMMITTED = "committed"
HOW_PULL_REQUEST = "pull_request"
HOW_ATTEMPT_KEPT = "attempt_kept"
HOW_ATTEMPT_DROPPED = "attempt_dropped"
HOW_DISCARDED = "discarded"
HOW_REWOUND = "rewound"
KEPT_HOWS = (HOW_MERGED, HOW_COMMITTED, HOW_PULL_REQUEST, HOW_ATTEMPT_KEPT)
THROWN_HOWS = (HOW_ATTEMPT_DROPPED, HOW_DISCARDED, HOW_REWOUND)

CHECK_PASSED = "passed"
CHECK_FAILED = "failed"

SOURCE_WORK = "work"
SOURCE_BACKFILL = "backfill"
#: 28-c's nightly test. Shown apart and read by nothing else.
SOURCE_REPLAY = "replay"

#: The words (VOCABULARY.md).
LEAD = "In this folder:"
TOO_FEW = "too few to judge"

#: ``_ironjarvis_meta`` key: the backfill ran on this database.
BACKFILL_KEY = "coding_record_backfill"
#: Bounds of the one-time backfill (newest first).
BACKFILL_SESSIONS_MAX = 400
BACKFILL_EVENT_LINES = 5000

_LOCK = threading.Lock()


class CodingOutcome(SQLModel, table=True):
    """One piece of coding work in a folder and what became of it (28-a)."""

    __tablename__ = "codingoutcome"

    id: str = Field(default_factory=lambda: new_id("co"), primary_key=True)
    #: The folder the user PICKED (``harness/sessions.folder_key``): a branch
    #: session's copy of the folder never has its own.
    folder_key: str = Field(default="", index=True)
    #: That folder as it was spelled. The key folds case on every OS; the
    #: client wall does not on Linux, so chat asks the wall about every
    #: spelling on record (``settings/records_scoreboard``).
    folder: str = ""
    #: The Chat › Code thread id or the Build › Code session id.
    session_id: str = Field(default="", index=True)
    unit: str = UNIT_REPLY
    #: ``<session>:<turn>`` for a reply, ``<session>:<turn>:<attempt>`` for an
    #: attempt. Unique: one piece is one row.
    ref: str = Field(default="", unique=True)
    surface: str = SURFACE_CODE
    tool: str = ""
    provider: str = ""
    model: str = ""
    #: Iron Jarvis's own check: passed | failed | "" (none ran).
    checked: str = ""
    #: kept | thrown | "" (not decided yet).
    verdict: str = ""
    how: str = ""
    source: str = SOURCE_WORK
    local_only: bool = False
    app_version: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
    #: A Build › Code rewind that put the files back threw this piece away:
    #: its before-rewind checkpoint's stamp (``checkpoints.restore``'s
    #: ``before``), so an Undo of THAT rewind puts it back (:func:`unrewound`).
    rewind_at: str = ""


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #


def folder_key(folder: Any) -> str:
    """The record's key for a folder (the Build list's and the folder
    defaults' own key)."""
    from .sessions import folder_key as _fk

    return _fk(str(folder or ""))


def reply_ref(session_id: str, turn_id: str) -> str:
    return f"{session_id}:{turn_id}"


def attempt_ref(session_id: str, turn_id: str, attempt_id: str) -> str:
    return f"{session_id}:{turn_id}:{attempt_id}"


def _version() -> str:
    try:
        from .. import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return ""


def _checked(value: Any) -> str:
    return value if value in (CHECK_PASSED, CHECK_FAILED) else ""


# --------------------------------------------------------------------------- #
# writes (BLOCKING, never raise)
# --------------------------------------------------------------------------- #


def note(engine: Any, *, session_id: str, ref: str, folder: str, surface: str, tool: str,
         unit: str = UNIT_REPLY, provider: str = "", model: str = "", checked: str = "",
         verdict: str = "", how: str = "", source: str = SOURCE_WORK,
         local_only: bool = False) -> bool:
    """One piece of work, ONCE per ``ref``. A row that exists only takes a
    ``checked`` it did not have yet (a check that finished after the reply
    was noted); its verdict is never changed here. True when a row was
    written. BLOCKING; never raises."""
    key = folder_key(folder)
    if engine is None or not key or not ref or not session_id:
        return False
    try:
        with _LOCK, session_scope(engine) as db:
            row = db.exec(select(CodingOutcome).where(CodingOutcome.ref == ref)).first()
            if row is not None:
                got = _checked(checked)
                if got and not row.checked:
                    row.checked = got
                    db.add(row)
                    db.commit()
                return False
            decided = verdict in (KEPT, THROWN)
            db.add(CodingOutcome(
                folder_key=key, folder=str(folder or "").strip()[:4096],
                session_id=str(session_id)[:200], unit=unit if unit in UNITS else UNIT_REPLY,
                ref=str(ref)[:400], surface=surface if surface in (SURFACE_CODE, SURFACE_TOOL) else SURFACE_CODE,
                tool=str(tool or "")[:60], provider=str(provider or "")[:120], model=str(model or "")[:200],
                checked=_checked(checked), verdict=verdict if decided else "", how=str(how or "")[:40] if decided else "",
                source=source if source in (SOURCE_WORK, SOURCE_BACKFILL, SOURCE_REPLAY) else SOURCE_WORK,
                local_only=bool(local_only), app_version=_version(),
                decided_at=utcnow() if decided else None,
            ))
            db.commit()
            return True
    except IntegrityError:
        return False  # written meanwhile (the unique ref): one piece, one row
    except Exception:  # noqa: BLE001 — a record never breaks the work
        log.warning("the coding record could not note %s", ref, exc_info=True)
        return False


def settle_session(engine: Any, session_id: str, verdict: str, how: str) -> int:
    """A session-level press (Commit, Open a pull request, Merge, Discard):
    every UNDECIDED reply row of that session takes ``verdict``. A row
    already decided keeps its verdict (a second press counts nothing twice).
    Attempt rows are decided by Keep and Discard all only. Returns how many
    were settled. BLOCKING; never raises."""
    if engine is None or not session_id or verdict not in (KEPT, THROWN):
        return 0
    try:
        with _LOCK, session_scope(engine) as db:
            rows = db.exec(select(CodingOutcome).where(
                CodingOutcome.session_id == str(session_id),
                CodingOutcome.verdict == "",
                col(CodingOutcome.unit).in_((UNIT_REPLY, UNIT_NIGHT)),
                CodingOutcome.source != SOURCE_REPLAY,
            )).all()
            now = utcnow()
            for row in rows:
                row.verdict, row.how, row.decided_at = verdict, str(how or "")[:40], now
                db.add(row)
            db.commit()
            return len(rows)
    except Exception:  # noqa: BLE001 — a record never breaks the press
        log.warning("the coding record could not settle session %s", session_id, exc_info=True)
        return 0


def rewound(engine: Any, session_id: str, turn_ids: Iterable[str], *, undo_key: str = "") -> int:
    """A rewind put these replies' files back: each one's UNDECIDED row is
    thrown (``rewound``). ``undo_key``: the Build › Code rewind's own
    before-rewind stamp, kept on each row it threw so :func:`unrewound` can
    take exactly that rewind back. BLOCKING; never raises."""
    refs = [reply_ref(str(session_id), str(t)) for t in turn_ids or () if str(t or "").strip()]
    if engine is None or not session_id or not refs:
        return 0
    try:
        with _LOCK, session_scope(engine) as db:
            rows = db.exec(select(CodingOutcome).where(
                col(CodingOutcome.ref).in_(refs), CodingOutcome.verdict == "",
            )).all()
            now = utcnow()
            for row in rows:
                row.verdict, row.how, row.decided_at = THROWN, HOW_REWOUND, now
                row.rewind_at = str(undo_key or "")[:80]
                db.add(row)
            db.commit()
            return len(rows)
    except Exception:  # noqa: BLE001 — a record never breaks the rewind
        log.warning("the coding record could not mark a rewind of %s", session_id, exc_info=True)
        return 0


def unrewound(engine: Any, session_id: str, undo_key: str) -> int:
    """Build › Code's Undo rewind put the files of the rewind ``undo_key``
    back: the rows THAT rewind threw away are not decided again (verdict,
    how and decided_at cleared). A row decided any other way, or thrown by
    another rewind, is never touched. BLOCKING; never raises."""
    key = str(undo_key or "")
    if engine is None or not session_id or not key:
        return 0
    try:
        with _LOCK, session_scope(engine) as db:
            rows = db.exec(select(CodingOutcome).where(
                CodingOutcome.session_id == str(session_id), CodingOutcome.how == HOW_REWOUND,
                CodingOutcome.rewind_at == key,
            )).all()
            for row in rows:
                row.verdict, row.how, row.decided_at, row.rewind_at = "", "", None, ""
                db.add(row)
            db.commit()
            return len(rows)
    except Exception:  # noqa: BLE001 — a record never breaks the undo
        log.warning("the coding record could not take back a rewind of %s", session_id, exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# Chat › Code seams
# --------------------------------------------------------------------------- #


def _chat_row(engine: Any, thread_id: str) -> Any:
    from . import sessions as S

    return S.get_row(engine, thread_id)


def _chat_facts(row: Any) -> "tuple[str, dict[str, Any], bool]":
    """``(picked folder, setup, local_only)`` of a Chat › Code session."""
    from . import sessions as S

    setup = S._setup(row)  # noqa: SLF001
    return S.checkout_of(row), setup, bool(setup.get("local_only"))


def _night_thread(engine: Any, thread_id: str) -> bool:
    try:
        from .night import NightTaskRecord

        with session_scope(engine) as db:
            return db.exec(select(NightTaskRecord.id).where(NightTaskRecord.thread_id == str(thread_id))).first() \
                is not None
    except Exception:  # noqa: BLE001 — no night table yet: not a night task
        return False


def _served(line: dict[str, Any], setup: dict[str, Any]) -> tuple[str, str]:
    """The provider and model that SERVED a reply (its route), else the
    session's own pick."""
    route = line.get("route") if isinstance(line.get("route"), dict) else {}
    provider = str(route.get("provider") or setup.get("provider") or "")
    model = str(route.get("model") or setup.get("model") or "")
    return provider, model


def reply_changed(line: dict[str, Any]) -> bool:
    """A Chat › Code reply CHANGED FILES when its line keeps a finish record
    (the finish check runs only after a reply that changed files, through
    the journal or the folder's own snapshot; a folder that turned the check
    off keeps ``{"off": "folder"}``). An attempts turn's own line is not a
    piece: its attempts are. Pure."""
    from .attempts import LINE_KEY
    from .finish import saved_record

    if not isinstance(line, dict) or isinstance(line.get(LINE_KEY), dict):
        return False
    return saved_record(line.get("finish")) is not None


def _check_of(line: dict[str, Any]) -> str:
    from .finish import saved_record

    fin = saved_record(line.get("finish")) or {}
    return _checked(fin.get("tests"))


def note_chat_reply(engine: Any, thread_id: str, turn_id: str, line: dict[str, Any], *,
                    source: str = SOURCE_WORK, verdict: str = "", how: str = "") -> bool:
    """A Chat › Code reply's line was saved (``routes/build._record_turn_end``):
    a reply that changed files is one piece. BLOCKING; never raises."""
    try:
        if not reply_changed(line):
            return False
        row = _chat_row(engine, thread_id)
        if row is None:
            return False
        folder, setup, local_only = _chat_facts(row)
        provider, model = _served(line, setup)
        return note(
            engine, session_id=thread_id, ref=reply_ref(thread_id, turn_id), folder=folder,
            surface=SURFACE_CODE, tool=TOOL_JARVIS,
            unit=UNIT_NIGHT if _night_thread(engine, thread_id) else UNIT_REPLY,
            provider=provider, model=model, checked=_check_of(line), local_only=local_only,
            source=source, verdict=verdict, how=how,
        )
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        log.warning("the coding record could not read reply %s of %s", turn_id, thread_id, exc_info=True)
        return False


def _counted_items(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The attempts of a card that are pieces of work: each that changed
    files (an attempt that changed nothing cannot be kept)."""
    return [it for it in card.get("items") or [] if isinstance(it, dict) and it.get("id") and it.get("files")]


def note_attempts(engine: Any, thread_id: str, turn_id: str, card: dict[str, Any], *,
                  kept_id: str = "", dropped: "Iterable[str] | None" = None,
                  source: str = SOURCE_WORK) -> int:
    """Keep (``kept_id``) or Discard all (``dropped``) on an attempts card:
    the kept attempt is kept, every other one that changed files and was
    let go is thrown. A second press finds the rows written. BLOCKING; never
    raises."""
    try:
        row = _chat_row(engine, thread_id)
        if row is None or not isinstance(card, dict) or card.get("from_session"):
            return 0
        folder, setup, local_only = _chat_facts(row)
        gone = {str(i) for i in dropped or ()}
        n = 0
        for it in _counted_items(card):
            aid = str(it.get("id"))
            if kept_id and aid == kept_id:
                verdict, how = KEPT, HOW_ATTEMPT_KEPT
            elif (kept_id and aid != kept_id) or aid in gone:
                verdict, how = THROWN, HOW_ATTEMPT_DROPPED
            else:
                continue
            n += int(note(
                engine, session_id=thread_id, ref=attempt_ref(thread_id, turn_id, aid), folder=folder,
                surface=SURFACE_CODE, tool=TOOL_JARVIS, unit=UNIT_ATTEMPT,
                provider=str(it.get("served_provider") or it.get("provider") or setup.get("provider") or ""),
                model=str(it.get("served_model") or it.get("model") or ""),
                checked=_checked(it.get("tests")), verdict=verdict, how=how, local_only=local_only, source=source,
            ))
        return n
    except Exception:  # noqa: BLE001 — a record never breaks Keep
        log.warning("the coding record could not note the attempts of %s", thread_id, exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# Build › Code seams
# --------------------------------------------------------------------------- #


def _hosted_row(engine: Any, sid: str) -> Any:
    from ..hosted import store

    return store.get(engine, sid)


def hosted_changed(events: list[dict[str, Any]], folder: str) -> bool:
    """A Build › Code reply changed files IN THE SESSION'S FOLDER when the
    tool's OWN finished edit/write steps of that message say so
    (``panes.tool_paths``; pure, so a reply's end pays no git read). A step
    whose file lies outside the folder (a scratch file in the temp folder,
    the tool's own plan or memory file in its home) changes nothing the
    session's Commit, Merge or Discard decide: not counted. ``folder`` is
    where the session works (a branch session's own copy). Pure."""
    import os

    from ..hosted import panes as PN

    where = str(folder or "").strip()
    if not where:
        return False
    try:
        root = os.path.normcase(os.path.normpath(where))
        inside = root.rstrip("\\/") + os.sep
        return any(k.startswith(inside) for k in PN.tool_paths(list(events or []), where))
    except Exception:  # noqa: BLE001
        return False


def note_hosted_reply(engine: Any, sid: str, turn_id: str, *, checked: str = "",
                      source: str = SOURCE_WORK, verdict: str = "", how: str = "",
                      tool: "str | None" = None, model: "str | None" = None) -> bool:
    """A Build › Code reply that changed files is one piece (its tool and the
    model the session chose: "" is the tool's own default). ``tool`` /
    ``model``: the backfill's own reading of an older message (the tool its
    user event names); else the session's, which at a reply's end is the
    tool and model that wrote it. BLOCKING; never raises."""
    try:
        row = _hosted_row(engine, sid)
        if row is None or not turn_id or getattr(row, "handed_from", None):
            return False  # (a helper's work is its lead session's, decided there)
        from ..hosted import session_git as SG

        return note(
            engine, session_id=sid, ref=reply_ref(sid, turn_id), folder=SG.checkout_of(row),
            surface=SURFACE_TOOL, tool=str(getattr(row, "harness", "") or "") if tool is None else str(tool),
            model=str(getattr(row, "model", "") or "") if model is None else str(model),
            checked=checked, source=source, verdict=verdict, how=how,
        )
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        log.warning("the coding record could not read Build › Code reply %s", turn_id, exc_info=True)
        return False


def note_hosted_turn(engine: Any, sid: str, turn_id: str, events: list[dict[str, Any]]) -> bool:
    """A Build › Code reply ENDED: noted when the tool's own steps changed
    files in the session's folder. BLOCKING; never raises."""
    try:
        row = _hosted_row(engine, sid)
        if row is None or not hosted_changed(events, str(getattr(row, "folder", "") or "")):
            return False
        return note_hosted_reply(engine, sid, turn_id)
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        log.warning("the coding record could not read Build › Code reply %s", turn_id, exc_info=True)
        return False


def after_hosted_turn(svc: Any, live: Any, turn: Any, done: dict[str, Any]) -> None:
    """``hosted/service._end_turn``'s hook, right after the ``done`` is out: a
    reply with a finished file step is looked at OFF the loop (a task the
    loop keeps; the row is read there). A reply with none costs nothing.
    Never raises; never waits."""
    try:
        events = [e for e in list(getattr(turn, "events", []) or []) if isinstance(e, dict)]
        if not _has_file_steps(events):
            return
        import asyncio

        from .stall import keep

        engine = getattr(svc, "engine", None)
        sid, tid = str(getattr(live, "sid", "") or ""), str(getattr(turn, "id", "") or "")
        if not sid or not tid:
            return
        keep(asyncio.ensure_future(asyncio.to_thread(note_hosted_turn, engine, sid, tid, events)))
    except Exception:  # noqa: BLE001 — a record never breaks a reply's end
        log.debug("the coding record could not read a Build › Code reply's end", exc_info=True)


def _has_file_steps(events: list[dict[str, Any]]) -> bool:
    """A finished edit or write step (what ``panes.tool_paths`` counts).
    Pure."""
    return any(
        ev.get("type") == "tool" and ev.get("kind") in ("edit", "write") and ev.get("status") == "done"
        and str(ev.get("path") or "").strip()
        for ev in events
    )


def on_proof(engine: Any, sid: str, turn_id: str, record: dict[str, Any]) -> bool:
    """``hosted/proof.after_reply``'s listener: the folder's tests Iron Jarvis
    ran after a Build › Code reply give that reply's ``checked`` (a check
    runs only after a reply that changed files, read from git, so a reply the
    tool's own steps missed is noted here). BLOCKING; never raises."""
    got = _checked((record or {}).get("tests"))
    if not got:
        return False
    return note_hosted_reply(engine, sid, turn_id, checked=got)


_ENGINE_OF: "Callable[[], Any] | None" = None
_LISTENING = False


def install(engine_of: Callable[[], Any]) -> Callable[[], None]:
    """Listen to Build › Code checks for this daemon (``app`` lifespan). One
    listener per process, whose engine is the newest daemon's; the returned
    function takes it back (only its own)."""
    global _ENGINE_OF, _LISTENING
    from ..hosted import proof as PROOF

    _ENGINE_OF = engine_of
    if not _LISTENING:
        _LISTENING = True
        PROOF.after_reply(_listener)

    def remove() -> None:
        global _ENGINE_OF
        if _ENGINE_OF is engine_of:
            _ENGINE_OF = None

    return remove


def _listener(sid: str, turn_id: str, record: dict[str, Any], folder: str) -> None:
    get = _ENGINE_OF
    if get is None:
        return
    on_proof(get(), sid, turn_id, record)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def words(kept: int, thrown: int, undecided: int, passed: int, failed: int) -> str:
    """The muted line under a model row. Every figure carries its count;
    "(too few to judge)" under :data:`MIN_SAMPLES` decided pieces; work not
    decided is said apart and never counted in an "of N"; nothing on record
    says nothing. Pure."""
    decided = int(kept) + int(thrown)
    checks = int(passed) + int(failed)
    parts: list[str] = []
    if decided > 0:
        part = f"kept {int(kept)} of {decided}"
        if decided < MIN_SAMPLES:
            part += f" ({TOO_FEW})"
        parts.append(part)
    if checks > 0:
        parts.append(f"passed its checks {int(passed)} of {checks}")
    if not parts and int(undecided) <= 0:
        return ""
    out = LEAD + (" " + ", ".join(parts) if parts else "")
    if int(undecided) > 0:
        out += ("; " if parts else " ") + f"{int(undecided)} not decided yet"
    return out


def _real(q: Any) -> Any:
    """Every reader's filter: never the nightly test's rows (28-c)."""
    return q.where(CodingOutcome.source != SOURCE_REPLAY)


def summary(engine: Any, folder: str) -> dict[str, Any]:
    """``GET /build/scoreboard?folder=``: per tool and model, ``{kept,
    thrown, undecided, passed, failed, samples, words}``; ``samples`` is the
    decided count; rows by sample count, then by all their pieces. No
    percentage and no "best" anywhere (D-28.1). BLOCKING."""
    key = folder_key(folder)
    out: dict[str, Any] = {"folder": key, "min_samples": MIN_SAMPLES, "rows": []}
    if engine is None or not key:
        return out
    with session_scope(engine) as db:
        rows = db.exec(_real(select(CodingOutcome).where(CodingOutcome.folder_key == key))).all()
    groups: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for r in rows:
        g = groups.setdefault((r.surface, r.tool, r.provider, r.model),
                              {"kept": 0, "thrown": 0, "undecided": 0, "passed": 0, "failed": 0})
        if r.verdict == KEPT:
            g["kept"] += 1
        elif r.verdict == THROWN:
            g["thrown"] += 1
        else:
            g["undecided"] += 1
        if r.checked == CHECK_PASSED:
            g["passed"] += 1
        elif r.checked == CHECK_FAILED:
            g["failed"] += 1
    listed = []
    for (surface, tool, provider, model), g in groups.items():
        samples = g["kept"] + g["thrown"]
        listed.append({
            "surface": surface, "tool": tool, "provider": provider, "model": model, **g,
            "samples": samples, "words": words(g["kept"], g["thrown"], g["undecided"], g["passed"], g["failed"]),
        })
    listed.sort(key=lambda x: (-x["samples"], -(x["samples"] + x["undecided"]), x["tool"], x["provider"], x["model"]))
    out["rows"] = listed
    return out


def folders(engine: Any, limit: int = 12) -> list[str]:
    """The folders with a record, the most pieces first. BLOCKING."""
    if engine is None:
        return []
    with session_scope(engine) as db:
        keys = db.exec(_real(select(CodingOutcome.folder_key))).all()
    counts: dict[str, int] = {}
    for k in keys:
        if k:
            counts[k] = counts.get(k, 0) + 1
    return [k for k, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:max(1, int(limit))]


#: Spellings of one folder read for the client wall (a bound, never a cut
#: that lets a folder through: the reader holds a folder it could not check).
SPELLINGS_MAX = 50


def spellings(engine: Any, key: str) -> "list[str] | None":
    """Every spelling on record of the folder ``key`` (the wall compares
    folders by their own key, which keeps case on Linux). None when there
    are more than :data:`SPELLINGS_MAX` (the caller then tells nothing of
    it). BLOCKING."""
    if engine is None or not key:
        return []
    with session_scope(engine) as db:
        got = db.exec(_real(select(CodingOutcome.folder).where(CodingOutcome.folder_key == key)).distinct()
                      .limit(SPELLINGS_MAX + 1)).all()
    out = sorted({str(f) for f in got if str(f or "").strip()})
    return None if len(got) > SPELLINGS_MAX else out


# --------------------------------------------------------------------------- #
# the one-time backfill
# --------------------------------------------------------------------------- #


def _meta_get(engine: Any, key: str) -> str:
    from ..core.db import _ensure_meta

    _ensure_meta(engine)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM _ironjarvis_meta WHERE key=:k"), {"k": key}).first()
    return str(row[0]) if row else ""


def _meta_set(engine: Any, key: str, value: str) -> None:
    from ..core.db import _ensure_meta

    _ensure_meta(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO _ironjarvis_meta(key,value) VALUES(:k,:v) ON CONFLICT(key) DO UPDATE SET value=:v"
        ), {"k": key, "v": value})


def _session_verdict(facts: "dict[str, Any] | None") -> tuple[str, str]:
    """What a session's OWN facts say became of its work: merged, a pull
    request, or its copy discarded. ``("", "")`` when none."""
    f = facts or {}
    if f.get("merged_at"):
        return KEPT, HOW_MERGED
    if f.get("pr_url"):
        return KEPT, HOW_PULL_REQUEST
    if f.get("removed_at"):
        return THROWN, HOW_DISCARDED
    return "", ""


def _backfill_chat(engine: Any) -> int:
    from ..core.models import BUILD_SURFACE, ChatThreadRecord
    from . import sessions as S
    from .attempts import LINE_KEY, saved_card

    with session_scope(engine) as db:
        rows = db.exec(
            select(ChatThreadRecord).where(ChatThreadRecord.surface == BUILD_SURFACE)
            .order_by(col(ChatThreadRecord.updated_at).desc()).limit(BACKFILL_SESSIONS_MAX)
        ).all()
        for r in rows:
            db.expunge(r)
    n = 0
    for row in rows:
        try:
            verdict, how = _session_verdict(S.git_of(row))
            for m in S._messages(row):  # noqa: SLF001
                if m.get("role") != "assistant" or not m.get("turnId"):
                    continue
                tid = str(m["turnId"])
                card = saved_card(m.get(LINE_KEY))
                if card is not None:
                    kept = str(card.get("kept") or "")
                    dropped = [str(i.get("id")) for i in card["items"] if i.get("trashed")] \
                        if card.get("discarded_at") and not kept else []
                    if kept or dropped:
                        n += note_attempts(engine, row.id, tid, card, kept_id=kept, dropped=dropped,
                                           source=SOURCE_BACKFILL)
                    continue
                n += int(note_chat_reply(engine, row.id, tid, m, source=SOURCE_BACKFILL, verdict=verdict, how=how))
        except Exception:  # noqa: BLE001 — one session never stops the rest
            log.warning("the coding record could not read session %s", getattr(row, "id", "?"), exc_info=True)
    return n


def _log_tail(home: Any, sid: str) -> list[dict[str, Any]]:
    from ..hosted.events import session_dir

    path = Path(session_dir(home, sid)) / "events.jsonl"
    try:
        with open(path, "rb") as fh:
            lines = fh.read().splitlines()[-BACKFILL_EVENT_LINES:]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in lines:
        try:
            ev = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _parked_tools(home: Any, sid: str) -> dict[str, Any]:
    """The tools a Build › Code session switched away from, each with what it
    had last (``tool_switch``'s own record). ``{}`` when none or unreadable."""
    try:
        from ..hosted import tool_switch as TS

        got = TS.read(home, sid).get("parked")
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _backfill_hosted(engine: Any, home: Any) -> int:
    from ..hosted import session_git as SG
    from ..hosted import store

    try:
        rows = store.list_rows(engine, archived=True)[:BACKFILL_SESSIONS_MAX]
    except Exception:  # noqa: BLE001 — no Build › Code table yet
        return 0
    n = 0
    for row in rows:
        try:
            folder = str(getattr(row, "folder", "") or "")
            if not folder or home is None:
                continue
            raw = getattr(row, "git_json", "") or ""
            try:
                facts = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                facts = {}
            verdict, how = _session_verdict(facts if isinstance(facts, dict) else {})
            if SG.facts_of(row) is None and how in (HOW_MERGED, HOW_DISCARDED):
                verdict, how = "", ""  # a folder session has no branch to merge or discard
            events = _log_tail(home, row.id)
            by_turn: dict[str, list[dict[str, Any]]] = {}
            checks: dict[str, str] = {}
            #: The tool each message went to (its user event, v1.545.0): a
            #: session that switched tools keeps each reply with its writer.
            tool_of: dict[str, str] = {}
            for ev in events:
                if ev.get("type") == "proof" and ev.get("state") == "done" and ev.get("for_turn"):
                    checks[str(ev["for_turn"])] = _checked(ev.get("tests"))
                tid = str(ev.get("turn") or "")
                if tid:
                    by_turn.setdefault(tid, []).append(ev)
                    if ev.get("type") == "user" and str(ev.get("tool") or "").strip():
                        tool_of.setdefault(tid, str(ev["tool"]).strip())
            current = str(getattr(row, "harness", "") or "")
            parked: "dict[str, Any] | None" = None
            for tid, evs in by_turn.items():
                if not hosted_changed(evs, folder):
                    continue
                tool = tool_of.get(tid) or current
                if tool == current:
                    model = str(getattr(row, "model", "") or "")
                else:
                    # Another tool wrote it: the model that tool had last in
                    # this session (its parked record). None on record: not
                    # counted (never credited to a model it may not have used).
                    if parked is None:
                        parked = _parked_tools(home, row.id)
                    if tool not in parked:
                        continue
                    model = str(parked[tool].get("model") or "")
                n += int(note_hosted_reply(engine, row.id, tid, checked=checks.get(tid, ""),
                                           source=SOURCE_BACKFILL, verdict=verdict, how=how,
                                           tool=tool, model=model))
        except Exception:  # noqa: BLE001 — one session never stops the rest
            log.warning("the coding record could not read Build › Code session %s", getattr(row, "id", "?"),
                        exc_info=True)
    return n


def backfill(engine: Any, home: Any = None) -> int:
    """ONCE per database: the facts that already exist become rows marked
    ``backfill`` (each ``ref`` once, so a run cut short and run again counts
    nothing twice). Returns how many rows it wrote (-1 when it had run).
    BLOCKING; never raises."""
    if engine is None:
        return 0
    try:
        if _meta_get(engine, BACKFILL_KEY):
            return -1
        CodingOutcome.__table__.create(bind=engine, checkfirst=True)
        n = _backfill_chat(engine) + _backfill_hosted(engine, home)
        _meta_set(engine, BACKFILL_KEY, utcnow().isoformat())
        log.info("the coding record was filled from %d earlier pieces of work", n)
        return n
    except Exception:  # noqa: BLE001 — tried again at the next start
        log.warning("the coding record's backfill failed; it is tried again at the next start", exc_info=True)
        return 0


async def backfill_at_start(platform: Any) -> int:
    """The daemon's start: :func:`backfill` off the loop. Boot never waits."""
    import asyncio

    return await asyncio.to_thread(
        backfill, getattr(platform, "engine", None), getattr(getattr(platform, "config", None), "home", None),
    )


__all__ = [
    "CHECK_FAILED", "CHECK_PASSED", "CodingOutcome", "HOW_ATTEMPT_DROPPED", "HOW_ATTEMPT_KEPT",
    "HOW_COMMITTED", "HOW_DISCARDED", "HOW_MERGED", "HOW_PULL_REQUEST", "HOW_REWOUND", "KEPT",
    "MIN_SAMPLES", "SOURCE_BACKFILL", "SOURCE_REPLAY", "SOURCE_WORK", "SURFACE_CODE", "SURFACE_TOOL",
    "THROWN", "TOOL_JARVIS", "TOO_FEW", "after_hosted_turn", "backfill", "backfill_at_start", "folder_key",
    "folders", "install", "note", "note_attempts", "note_chat_reply", "note_hosted_reply", "note_hosted_turn",
    "on_proof",
    "reply_changed", "rewound", "settle_session", "spellings", "summary", "unrewound", "words",
]
