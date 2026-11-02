"""TEAM NOTES, Phase 2 — CONFIRMED FINDINGS BECOME WORKING HABITS THE OWNER
ACCEPTS (v1.423.0, the 2026-11-01 audit's "The owner's idea: Team notes",
Wave 3; team notes are ON by default — ``Config.team_notes``).

Phase 1b (``agents/team_findings``) turned a reviewer's ``R<n>.`` lines into
records, attributed to the worker whose piece wrote that path, and settled by
the lead's answers (``fixed`` is CONFIRMED only when a later piece changed
that file). This module turns a member's CONFIRMED findings into at most
three WORKING HABITS, each citing the findings it generalizes, that the owner
accepts or declines — and puts the accepted ones in front of that member's
next TEAM pieces.

WHEN (:func:`eligibility`): a member with at least :data:`MIN_FINDINGS`
confirmed findings across at least :data:`MIN_SESSIONS` distinct sessions
(the finding's ``scope``) — only findings the lead was ASKED to answer
(``team_findings.confirmed_findings``: ``asked`` and ``fixed``), none
already cited by a pending, accepted or recently declined
(:data:`SUPPRESS_DAYS`) proposal, none from a review whose run ended at LOW
trust — and at most one distill per member per :data:`DISTILL_EVERY_S`
(the last ATTEMPT is persisted on :class:`TeamNoteState`, before the call).

PACING (:func:`note_turn_finished` / :func:`note_turn_started` /
:func:`flush`): on the EXPERIENCE LIBRARY's idle pacing — ``harness/library``
calls these from its own (a finished Build turn is NOTED, the session is
distilled once it has been idle ``library.REFLECT_IDLE_S`` or when it is
archived, a new turn restarts the wait). The members who worked in that
session since the stretch began (``TeamRoleRun`` rows of its scope) and are
eligible are distilled, ONE call each — never while the session's account is
at or near its limit (``library.account_refusal``), never on the loop. The
test suite turns the library's pacing off for bare tests
(``library.AUTO_REFLECT``), which turns this one off too.

THE CALL (:func:`distill`): ONE ``router.complete(pin=True)`` on the
REVIEWER's own model — the reviewer member that made most of the cited
findings (its pinned model; a reviewer with no model of its own: the model
its newest cited review ran on) — HELD (``providers.hold``) and on that
member's account (``model_team.member_account``), inside
``egress.local_only_scope`` when any cited finding came from a Local only
session (a reviewer model that leaves the network then SKIPS, with a reason).
HONEST MOCK: the demo model distills nothing, before the call and after it.
Billed to the session like the library's reflection (``library._bill``).
The prompt lists the member's roles and each finding as ``F<n>`` (path, what,
what to change — stored masked; each scanned again here), asking for at most
three one-line habits, each ending with the findings it generalizes
(``[F1, F4]``), two at least, nothing when no habit is supported.

THE EVIDENCE CHECK (:func:`parse_habits` / :func:`check_habits`): a habit
citing an unknown ``F`` id, or fewer than :data:`MIN_CITED` findings, is
dropped; each habit is promptguard-scanned (flagged = dropped), MASKED
(``detections.redact.mask``) before any cut, longer than
:data:`MAX_HABIT_CHARS` dropped, and de-duplicated against the member's
accepted habits, its waiting suggestions and the answer's own earlier lines
(:func:`word_overlap` ≥ :data:`SAME_HABIT_OVERLAP` = the same habit). The
reader is LINEAR on model text (no regex over a reply's line: ``rfind`` and
``split``; every line masked and bounded first).

THE PROPOSAL: the COACH's own row (``coach.models.CoachProposalRecord``,
kind ``team_note``) on the coach's own screens — ``after`` the habit,
``rationale`` "From N confirmed findings across M sessions",
``evidence_json`` the cited findings, ``signature`` = member + sorted
finding ids — and the coach's ``coach.proposal`` event (with ``kind``, so the
bell shows a quiet row). ACCEPT (:func:`accept`) keeps it as a
:class:`TeamNote` and RENDERS the member's ``TEAM-NOTES.md`` in its agent
folder (``agents/files.AgentFiles``): atomic, a revision copy of the old text
under ``revisions/team-notes/``, and NEVER over a hand edit — the file is
written only while it is exactly what Iron Jarvis last wrote
(:attr:`TeamNoteState.written_sha`), else the user's file is kept and the
answer says so. DECLINE is the coach's (it suppresses that signature for
:data:`SUPPRESS_DAYS`). :func:`remove` takes an accepted habit back (the file
rendered again under the same rule).

INJECTION (:func:`habits_for_run`): ONLY into that member's TEAM pieces — a
coding session's helper (``harness/helpers.HELPER_ORIGIN``) and a mission's
teammate (``agents/team.MISSION_MEMBER_ORIGIN``) — never its other runs: a
``# Your working habits (from your reviewers' findings)`` section with the
``TEAM-NOTES.md`` text, scanned, at most :data:`INJECT_CHARS`, read ONCE PER
REPLY (cached by the coding session — a new turn reads afresh — or by the
mission's coordinator run). Setting off, or no habits: nothing at all — the
prompt is byte-identical to before.

ONE HABIT, TWO MEMBERS OF A ROLE (:func:`role_edit_for`): when an accepted
habit of member X matches (word overlap) an accepted habit of ANOTHER member
holding the same role, the library is asked for a ``layer_edit`` of that
role's brief (``.ironjarvis/harness/sub_agents/<role>.md``) in the newest
coding-session folder among the cited findings' sessions
(``library.propose_role_brief_edit``) — through the library's own validators
and Accept path.

Every reader and writer here is BLOCKING (SQLite, files) and never raises;
the async ones hop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# the numbers (the owner's design: 4 findings, 2 sessions, 3 habits, a day)
# --------------------------------------------------------------------------- #

#: A member needs at least this many CONFIRMED findings…
MIN_FINDINGS = 4
#: …across at least this many distinct sessions (``TeamFinding.scope``).
MIN_SESSIONS = 2
#: At most this many habits from one distill (and waiting per member).
MAX_HABITS = 3
#: A habit must cite at least this many findings.
MIN_CITED = 2
#: A habit line, at most (characters, after masking).
MAX_HABIT_CHARS = 200
#: At most one distill per member per this many seconds (the last ATTEMPT).
DISTILL_EVERY_S = 24 * 3600
#: A declined (or accepted) proposal's findings and signature rest this long.
SUPPRESS_DAYS = 14
#: The newest confirmed findings a distill reads, and shows the model.
FINDINGS_READ = 200
MAX_PROMPT_FINDINGS = 20
#: Two habits are the SAME when their word overlap is at least this.
SAME_HABIT_OVERLAP = 0.5
#: The injected section, at most (characters, heading included).
INJECT_CHARS = 2000
#: The model's answer: at most this many lines read, each this long.
ANSWER_LINES = 40
ANSWER_LINE_CHARS = 400
#: The finding text shown to the model (each part), at most.
PROMPT_PART_CHARS = 300

NOTES_FILE = "TEAM-NOTES.md"
#: Revision copies of TEAM-NOTES.md (never beside AGENTS.md's revisions —
#: a restore there must never put a habit list into an agent's instructions).
NOTES_REVISIONS = ("revisions", "team-notes")
HABITS_HEADER = "# Your working habits (from your reviewers' findings)"
NOTES_TITLE = "# Working habits"
NOTES_COMMENT = (
    "<!-- Iron Jarvis writes this file from the working habits you accepted on the Agents page. "
    "If you edit it, Iron Jarvis keeps your text and stops changing this file. -->"
)
HABIT_SOURCE = "a working habit"
FINDING_PROMPT_SOURCE = "a reviewer's finding"

REASON_OFF = "team notes are turned off"
REASON_NOT_MEMBER = "it is not on your model team"
REASON_DAILY = "its working habits were already looked at in the last 24 hours"
REASON_FEW = (
    f"it needs at least {MIN_FINDINGS} confirmed findings across at least {MIN_SESSIONS} sessions"
)
REASON_FULL = f"{MAX_HABITS} suggested habits are already waiting for you"
REASON_NO_REVIEWER = "no reviewer's model is known to distill the habits with"
REASON_MOCK = "the demo model answered — no habit is written with it"
REASON_LOCAL_ONLY = (
    "some of its findings came from a Local only session, and its reviewer's model is not on "
    "this PC or your network, so nothing was sent"
)
REASON_MODEL_FAILED = "the distilling call failed"
REASON_NOTHING = "no habit the findings support was left to suggest"
REASON_BUSY = "the session is answering"
KEPT_SENTENCE = (
    "TEAM-NOTES.md was changed by hand, so Iron Jarvis kept your file as it is. The habit is "
    "saved; edit the file yourself, or put it back as Iron Jarvis wrote it, to have it updated."
)
OFF_SENTENCE = "Team notes are turned off, so no model is shown the habit until you turn them on."

DISTILL_SYSTEM = (
    "You turn a code reviewer's CONFIRMED findings about one team member's work into WORKING "
    "HABITS for that member: short rules it follows on its next pieces of work so the same "
    "problems do not come back. Each finding below is a problem a reviewer named in the member's "
    "work that was then fixed. Write at most 3 habits. Each habit is ONE line of at most 200 "
    "characters that starts with \"- \", says what to do (not what went wrong), and ends with the "
    "findings it generalizes in square brackets, like [F1, F4]. A habit must generalize AT LEAST "
    "TWO of the findings. Never name a finding that is not listed. When no habit is supported by "
    "two or more findings, write nothing at all. Write only the habit lines."
)


# --------------------------------------------------------------------------- #
# the records (late models: ``core.db._LATE_MODEL_MODULES``)
# --------------------------------------------------------------------------- #


class TeamNote(SQLModel, table=True):
    """One working habit the owner ACCEPTED for a model team member."""

    __tablename__ = "teamnote"

    id: str = Field(default_factory=lambda: new_id("tnote"), primary_key=True)
    #: The member's name (``DynamicAgentRecord.name``, never "custom:").
    member: str = Field(default="", index=True)
    #: The habit, one line (masked and scanned when it was proposed).
    text: str = ""
    #: JSON list of the ``TeamFinding`` ids it generalizes.
    finding_ids_json: str = "[]"
    #: The coach proposal it was accepted from.
    proposal_id: str = Field(default="", index=True)
    accepted_at: datetime = Field(default_factory=utcnow)
    #: When the owner took it back ("Remove"); None while it is in force.
    removed_at: datetime | None = None


class TeamNoteState(SQLModel, table=True):
    """Per member: the last distill ATTEMPT (the daily limit) and the
    ``TEAM-NOTES.md`` text Iron Jarvis last wrote (the hand-edit rule)."""

    __tablename__ = "teamnotestate"

    member: str = Field(default="", primary_key=True)
    last_attempt_at: datetime | None = None
    #: Why the last attempt minted nothing ("" when it minted, or never ran).
    last_reason: str = ""
    #: sha256 of the TEAM-NOTES.md text Iron Jarvis last wrote ("" = never).
    written_sha: str = ""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _naive(at: "datetime | None") -> "datetime | None":
    """A stamp as SQLite keeps it (naive UTC)."""
    if at is None:
        return None
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _mask(text: Any) -> str:
    """Masked (``detections.redact.mask``) — every cut happens AFTER this."""
    from ..detections.redact import mask

    return str(mask(str(text or "")))


def _flagged(text: str, source: str) -> bool:
    """Does promptguard read ``text`` as an instruction? Unscannable counts
    as flagged (nothing reaches a model or a record on a guess)."""
    try:
        from ..core.promptguard import scan_context

        return bool(scan_context(text, source=source, cap=None).blocked)
    except Exception:  # noqa: BLE001
        return True


def _sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _on(platform: Any) -> bool:
    """Is the ``team_notes`` setting on (the default)? Read live."""
    return getattr(getattr(platform, "config", None), "team_notes", True) is not False


def _roster(member: str) -> str:
    return f"custom:{member}"


def _member_name(raw: Any) -> str:
    name = str(raw or "").strip()
    return name[len("custom:"):].strip() if name.lower().startswith("custom:") else name


#: Words a habit's sameness never turns on.
_STOP = frozenset(
    "the and for with that this from into when then than your you its it's are was were has have "
    "had not but any all each every one before after over under about only also just more most".split()
)


def _words(text: Any) -> set[str]:
    """The lower-case words of three letters or more, stop words left out.
    Linear (``split`` + per-character filtering, no regex)."""
    out: set[str] = set()
    for raw in str(text or "").lower().split():
        word = "".join(ch for ch in raw if ch.isalnum())
        if len(word) >= 3 and word not in _STOP:
            out.add(word)
    return out


def word_overlap(a: Any, b: Any) -> float:
    """How much two habits share: |A ∩ B| / |A ∪ B| over their words
    (:func:`_words`); 0.0 when either has none. At least
    :data:`SAME_HABIT_OVERLAP` = the same habit."""
    x, y = _words(a), _words(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def same_habit(a: Any, b: Any) -> bool:
    return word_overlap(a, b) >= SAME_HABIT_OVERLAP


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def rationale_for(findings: int, sessions: int) -> str:
    """The proposal's rationale, in plain words."""
    return f"From {_plural(findings, 'confirmed finding')} across {_plural(sessions, 'session')}"


def signature_for(member: str, finding_ids: Iterable[str]) -> str:
    """The proposal's dedupe key: the member + its sorted finding ids."""
    ids = sorted({str(i) for i in finding_ids if str(i or "")})
    return hashlib.sha256(f"team_note|{member}|{','.join(ids)}".encode("utf-8")).hexdigest()


def finding_place(f: Any) -> str:
    """``app.py:12`` / ``app.py`` / ``(general)`` — a finding's place."""
    path = _one_line(getattr(f, "path", "") if not isinstance(f, dict) else f.get("path"))
    line = getattr(f, "line", None) if not isinstance(f, dict) else f.get("line")
    if not path:
        return "(general)"
    return f"{path}:{line}" if isinstance(line, int) and line > 0 else path


def finding_view(f: Any) -> dict[str, Any]:
    """A cited finding as a proposal's evidence keeps it (already masked)."""
    created = getattr(f, "created_at", None)
    return {
        "id": str(getattr(f, "id", "") or ""),
        "path": str(getattr(f, "path", "") or ""),
        "line": getattr(f, "line", None),
        "what": str(getattr(f, "what", "") or ""),
        "change": str(getattr(f, "change", "") or ""),
        "scope": str(getattr(f, "scope", "") or ""),
        "reviewer": str(getattr(f, "reviewer", "") or ""),
        "created_at": created.isoformat() if isinstance(created, datetime) else None,
    }


# --------------------------------------------------------------------------- #
# eligibility
# --------------------------------------------------------------------------- #


def _team_note_proposals(engine: Any, member: str) -> list[Any]:
    """Every ``team_note`` coach proposal for the member (detached)."""
    from sqlmodel import select

    from ..coach.models import KIND_TEAM_NOTE, CoachProposalRecord
    from ..core.db import session_scope

    with session_scope(engine) as db:
        rows = list(db.exec(
            select(CoachProposalRecord)
            .where(CoachProposalRecord.agent == _roster(member))
            .where(CoachProposalRecord.kind == KIND_TEAM_NOTE)
        ))
        for row in rows:
            db.expunge(row)
    return rows


def _cited_ids(row: Any) -> list[str]:
    try:
        data = json.loads(getattr(row, "evidence_json", "") or "[]")
    except (TypeError, ValueError):
        return []
    out: list[str] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and str(item.get("id") or ""):
            out.append(str(item["id"]))
    return out


def _resting(row: Any, now: datetime) -> bool:
    """Does this proposal still hold its findings (and its signature)?
    Pending always; accepted, declined or stale within :data:`SUPPRESS_DAYS`
    of the decision. (An ACCEPTED habit's findings never come back: it is in
    force — :func:`blocked_ids` keeps them while it is.)"""
    from ..coach.models import PENDING

    if row.status == PENDING:
        return True
    decided = _naive(getattr(row, "decided_at", None))
    return decided is not None and decided >= _naive(now - timedelta(days=SUPPRESS_DAYS))


def blocked_ids(engine: Any, member: str, *, now: "datetime | None" = None) -> set[str]:
    """The finding ids a pending, accepted (while its habit is in force) or
    recently declined proposal already cites. BLOCKING; never raises (an
    unreadable list blocks nothing — the signature rule still holds)."""
    from ..coach.models import ACCEPTED

    now = now or utcnow()
    out: set[str] = set()
    try:
        rows = _team_note_proposals(engine, member)
        live = {n.proposal_id for n in active_notes(engine, member)}
        for row in rows:
            if _resting(row, now) or (row.status == ACCEPTED and row.id in live):
                out.update(_cited_ids(row))
    except Exception:  # noqa: BLE001
        log.debug("team note proposals unreadable", exc_info=True)
    return out


def _review_low_trust(engine: Any, sessions: Iterable[str]) -> set[str]:
    """The review sessions (of the findings) that ended at LOW trust — read
    once each (``model_team._low_trust``). Nothing is ever read from those."""
    from .model_team import _low_trust

    out: set[str] = set()
    for sid in {str(s) for s in sessions if str(s or "")}:
        if _low_trust(engine, sid):
            out.add(sid)
    return out


@dataclass
class Eligibility:
    """What :func:`eligibility` found for one member."""

    findings: list[Any] = field(default_factory=list)
    sessions: int = 0
    ok: bool = False
    reason: str = ""


def eligibility(engine: Any, member: str, *, now: "datetime | None" = None) -> Eligibility:
    """The member's findings a distill may use, newest first: CONFIRMED and
    ASKED (``team_findings.confirmed_findings``), not cited by a resting
    proposal (:func:`blocked_ids`), not from a review run that ended at low
    trust. ``ok`` when at least :data:`MIN_FINDINGS` across at least
    :data:`MIN_SESSIONS` distinct sessions. BLOCKING; never raises."""
    from . import team_findings as TF

    out = Eligibility()
    try:
        rows = TF.confirmed_findings(engine, member, limit=FINDINGS_READ)
        if not rows:
            out.reason = REASON_FEW
            return out
        blocked = blocked_ids(engine, member, now=now)
        low = _review_low_trust(engine, (r.review_session for r in rows))
        kept = [r for r in rows if r.id not in blocked and r.review_session not in low]
        sessions = {str(r.scope or "") for r in kept if str(r.scope or "")}
        out.findings, out.sessions = kept, len(sessions)
        out.ok = len(kept) >= MIN_FINDINGS and len(sessions) >= MIN_SESSIONS
        out.reason = "" if out.ok else REASON_FEW
    except Exception:  # noqa: BLE001
        log.debug("team notes eligibility unreadable", exc_info=True)
        out.reason = REASON_FEW
    return out


# --------------------------------------------------------------------------- #
# the state row (the daily limit, the file's last text)
# --------------------------------------------------------------------------- #


def _state(engine: Any, member: str) -> TeamNoteState:
    from ..core.db import session_scope

    try:
        with session_scope(engine) as db:
            row = db.get(TeamNoteState, member)
            if row is not None:
                db.expunge(row)
                return row
    except Exception:  # noqa: BLE001
        log.debug("team note state unreadable", exc_info=True)
    return TeamNoteState(member=member)


def _save_state(engine: Any, member: str, **changes: Any) -> None:
    from ..core.db import session_scope

    try:
        with session_scope(engine) as db:
            row = db.get(TeamNoteState, member) or TeamNoteState(member=member)
            for key, value in changes.items():
                setattr(row, key, value)
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001 — a state row never breaks a distill
        log.warning("team note state could not be saved", exc_info=True)


def attempted_recently(engine: Any, member: str, *, now: "datetime | None" = None) -> bool:
    """Was a distill ATTEMPTED for the member within :data:`DISTILL_EVERY_S`?"""
    last = _naive(_state(engine, member).last_attempt_at)
    if last is None:
        return False
    return last > _naive((now or utcnow()) - timedelta(seconds=DISTILL_EVERY_S))


# --------------------------------------------------------------------------- #
# the accepted habits
# --------------------------------------------------------------------------- #


def active_notes(engine: Any, member: "str | None" = None) -> list[TeamNote]:
    """The habits in force (accepted, not removed), oldest first — one
    member's, or every member's when ``member`` is None. Detached.
    BLOCKING; never raises (``[]``)."""
    from sqlalchemy import literal_column
    from sqlmodel import select

    from ..core.db import session_scope

    try:
        stmt = select(TeamNote).where(TeamNote.removed_at.is_(None))  # type: ignore[union-attr]
        if member is not None:
            stmt = stmt.where(TeamNote.member == member)
        stmt = stmt.order_by(literal_column("teamnote.rowid"))  # type: ignore[arg-type]
        with session_scope(engine) as db:
            rows = list(db.exec(stmt))
            for row in rows:
                db.expunge(row)
        return rows
    except Exception:  # noqa: BLE001
        log.debug("team notes unreadable", exc_info=True)
        return []


def note_view(note: TeamNote) -> dict[str, Any]:
    try:
        ids = json.loads(note.finding_ids_json or "[]")
    except (TypeError, ValueError):
        ids = []
    return {
        "id": note.id,
        "member": note.member,
        "text": note.text,
        "findings": len(ids) if isinstance(ids, list) else 0,
        "accepted_at": note.accepted_at.isoformat() if note.accepted_at else None,
        "removed_at": note.removed_at.isoformat() if note.removed_at else None,
    }


# --------------------------------------------------------------------------- #
# the model's answer: parse + the evidence check
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Habit:
    """One habit read from the model's answer, checked."""

    text: str
    finding_ids: tuple[str, ...]


def _bounded(line: str) -> str:
    """A line as the reader reads it: MASKED, then cut (a cut through a key
    leaves a piece the mask no longer sees) — never a long run through any
    reader."""
    if len(line) <= ANSWER_LINE_CHARS:
        return _mask(line)
    return _mask(line)[:ANSWER_LINE_CHARS]


def _cited(tail: str) -> "list[int] | None":
    """``F1, F4`` → ``[1, 4]``; None when anything in the brackets is not an
    ``F<number>`` (the whole citation is unreadable). Plain string work."""
    nums: list[int] = []
    for part in tail.split(","):
        token = part.strip()
        if len(token) < 2 or token[0] not in "Ff" or not token[1:].isdigit() or len(token) > 4:
            return None
        nums.append(int(token[1:]))
    return nums or None


def parse_habits(text: Any) -> list[tuple[str, list[int]]]:
    """``[(habit, [n, …])]`` from the model's answer: each non-empty line (at
    most :data:`ANSWER_LINES`, each masked and bounded first), a leading list
    mark dropped, ENDING with ``[F1, F4]`` (read with ``rfind``/``split`` —
    linear, no regex over model text). A line with no readable citation is
    not a habit. Not checked against the findings here (:func:`check_habits`).
    Pure; never raises."""
    out: list[tuple[str, list[int]]] = []
    try:
        lines = str(text or "").splitlines()[:ANSWER_LINES]
        for raw in lines:
            line = _bounded(raw).strip()
            while line[:1] in ("-", "*", "•", "+"):
                line = line[1:].strip()
            # "1. habit …" / "2) habit …": a number before a dot or bracket
            # is a list mark too (read by hand, not by a pattern).
            digits = 0
            while digits < len(line) and digits < 3 and line[digits].isdigit():
                digits += 1
            if digits and line[digits:digits + 1] in (".", ")"):
                line = line[digits + 1:].strip()
            if line.endswith("]"):
                at = line.rfind("[")
                if at > 0:
                    nums = _cited(line[at + 1:-1])
                    habit = line[:at].strip().rstrip(" -—–:;,").strip()
                    if nums is not None and habit:
                        out.append((habit, nums))
    except Exception:  # noqa: BLE001
        log.debug("a distill answer could not be read", exc_info=True)
    return out


def check_habits(
    parsed: list[tuple[str, list[int]]], index: dict[int, str], *, accepted: Iterable[str] = (),
    waiting: Iterable[str] = (),
) -> tuple[list[Habit], list[str]]:
    """The EVIDENCE CHECK: ``(kept, why_dropped)``. A habit citing an id not
    in ``index`` (``{n: finding id}``) or fewer than :data:`MIN_CITED`
    distinct findings is dropped; one longer than :data:`MAX_HABIT_CHARS`
    (masked already) is dropped; one promptguard flags is dropped; one that
    is the same habit (:func:`same_habit`) as an accepted habit, a waiting
    suggestion or an earlier line of this answer is dropped. At most
    :data:`MAX_HABITS`. Pure (the scan aside); never raises."""
    kept: list[Habit] = []
    dropped: list[str] = []
    seen = [*accepted, *waiting]
    for habit, nums in parsed:
        try:
            if any(n not in index for n in nums):
                dropped.append("cites a finding that was not listed")
                continue
            ids = tuple(dict.fromkeys(index[n] for n in nums))
            if len(ids) < MIN_CITED:
                dropped.append(f"cites fewer than {MIN_CITED} findings")
                continue
            text = _one_line(_mask(habit))
            if not text or len(text) > MAX_HABIT_CHARS:
                dropped.append(f"is longer than {MAX_HABIT_CHARS} characters")
                continue
            if _flagged(text, HABIT_SOURCE):
                dropped.append("reads like an instruction to the model")
                continue
            if any(same_habit(text, other) for other in seen):
                dropped.append("is the same as a habit already there")
                continue
            kept.append(Habit(text=text, finding_ids=ids))
            seen.append(text)
            if len(kept) >= MAX_HABITS:
                break
        except Exception:  # noqa: BLE001
            dropped.append("could not be checked")
    return kept, dropped


# --------------------------------------------------------------------------- #
# the prompt
# --------------------------------------------------------------------------- #


def _finding_line(n: int, f: Any) -> str:
    what = _one_line(getattr(f, "what", ""))[:PROMPT_PART_CHARS]
    change = _one_line(getattr(f, "change", ""))[:PROMPT_PART_CHARS]
    line = f"F{n}. {finding_place(f)[:PROMPT_PART_CHARS]} — {what}"
    return line + (f" — {change}" if change else "")


def distill_prompt(member: str, roles: Iterable[str], findings: list[Any]) -> tuple[str, dict[int, str]]:
    """The user prompt and its ``{n: finding id}`` index. Each finding line
    is scanned again here (promptguard; a flagged one is left out — its text
    was a model's)."""
    from .model_team import ROLE_LABELS

    index: dict[int, str] = {}
    lines: list[str] = []
    for f in findings[:MAX_PROMPT_FINDINGS]:
        n = len(index) + 1
        line = _finding_line(n, f)
        if _flagged(line, FINDING_PROMPT_SOURCE):
            continue
        index[n] = str(f.id)
        lines.append(line)
    role_words = ", ".join(ROLE_LABELS.get(r, r) for r in roles) or "none listed"
    prompt = "\n".join([
        f"THE MEMBER: {member}",
        f"ITS ROLES: {role_words}",
        "ITS CONFIRMED FINDINGS (each a problem a reviewer named in its work, then fixed):",
        *lines,
        "",
        "Write at most 3 working habits for this member, one per line, each ending with the "
        "findings it generalizes, like [F1, F4] — at least two findings each. Write nothing when "
        "no habit is supported.",
    ])
    return prompt, index


# --------------------------------------------------------------------------- #
# the reviewer's model, its account, Local only
# --------------------------------------------------------------------------- #


def reviewer_of(findings: list[Any]) -> list[str]:
    """The reviewers of ``findings``, the one that made MOST of them first
    (a tie: the one on the newest finding)."""
    counts: dict[str, int] = {}
    newest: dict[str, int] = {}
    for i, f in enumerate(findings):  # newest first
        name = str(getattr(f, "reviewer", "") or "")
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        newest.setdefault(name, i)
    return sorted(counts, key=lambda n: (-counts[n], newest[n]))


@dataclass
class ReviewerModel:
    name: str = ""
    provider: str = ""
    model: str = ""
    record: Any = None
    #: True: the reviewer's OWN pinned model (its account: ``member_account``).
    own: bool = False


def reviewer_model(platform: Any, findings: list[Any]) -> ReviewerModel:
    """The model the distill runs on: the reviewer that made most of the
    findings — its OWN pinned model, else the model its newest cited review
    RAN on (``TeamRoleRun.provider/model``). An empty answer when none is
    known. BLOCKING; never raises."""
    from . import model_team as MT

    registry = getattr(platform, "agents_registry", None)
    for name in reviewer_of(findings):
        try:
            record = registry.get(name) if registry is not None else None
        except Exception:  # noqa: BLE001
            record = None
        if record is not None and MT.runs_elsewhere(record):
            model = MT.pinned_model(platform, record)
            return ReviewerModel(name=name, provider=str(record.provider).strip(), model=model,
                                 record=record, own=True)
        rows = [str(f.review_row) for f in findings if getattr(f, "reviewer", "") == name and f.review_row]
        try:
            from sqlmodel import select

            from ..core.db import session_scope
            from .team_record import TeamRoleRun

            with session_scope(platform.engine) as db:
                runs = list(db.exec(select(TeamRoleRun).where(TeamRoleRun.id.in_(rows))))  # type: ignore[attr-defined]
            runs = sorted((r for r in runs if str(getattr(r, "provider", "") or "")),
                          key=lambda r: _naive(r.created_at) or datetime.min, reverse=True)
        except Exception:  # noqa: BLE001
            runs = []
        if runs:
            return ReviewerModel(name=name, provider=str(runs[0].provider), model=str(runs[0].model or ""),
                                 record=record, own=False)
    return ReviewerModel()


def _thread_of_scope(scope: str) -> str:
    from ..harness.sessions import thread_of

    return thread_of(scope)


def any_local_only(engine: Any, findings: list[Any]) -> bool:
    """Did any of ``findings`` come from a Local only session — its review's
    run carried Local only (a helper of such a session does, ``helpers.
    HelperParent.options``), or its coding session is set Local only?
    BLOCKING; True when unsure (the stricter answer)."""
    try:
        from ..core.db import session_scope
        from ..core.models import Session
        from ..harness.sessions import _setup, get_row
        from ..scheduling.knobs import session_options

        reviews = {str(f.review_session) for f in findings if str(getattr(f, "review_session", "") or "")}
        with session_scope(engine) as db:
            for sid in reviews:
                row = db.get(Session, sid)
                if row is not None and session_options(row).get("local_only") is True:
                    return True
        for scope in {str(f.scope or "") for f in findings}:
            thread = _thread_of_scope(scope)
            if thread:
                row = get_row(engine, thread)
                if row is not None and _setup(row).get("local_only") is True:
                    return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _is_mock_provider(platform: Any, provider: str, model: str = "") -> bool:
    """The demo model, by name or by the adapter it resolves to."""
    if str(provider or "").strip().lower() == "mock":
        return True
    try:
        from ..providers.adapters.mock import MockLLMAdapter

        adapter = platform.providers.get(provider, model or None)
        return isinstance(adapter, MockLLMAdapter)
    except Exception:  # noqa: BLE001 — an adapter that cannot be built is the router's to refuse
        return False


# --------------------------------------------------------------------------- #
# minting
# --------------------------------------------------------------------------- #


def _suppressed(engine: Any, member: str, sig: str, *, now: "datetime | None" = None) -> bool:
    now = now or utcnow()
    try:
        return any(r.signature == sig and _resting(r, now) for r in _team_note_proposals(engine, member))
    except Exception:  # noqa: BLE001
        return True


def _waiting_texts(engine: Any, member: str) -> list[str]:
    from ..coach.models import PENDING

    try:
        return [str(r.after or "") for r in _team_note_proposals(engine, member) if r.status == PENDING]
    except Exception:  # noqa: BLE001
        return []


def _mint(engine: Any, member: str, habit: Habit, findings: dict[str, Any]) -> Any:
    """One pending ``team_note`` coach proposal (or None: suppressed)."""
    from ..coach.models import KIND_TEAM_NOTE, PENDING, CoachProposalRecord
    from ..core.db import session_scope

    cited = [findings[i] for i in habit.finding_ids if i in findings]
    sig = signature_for(member, habit.finding_ids)
    if _suppressed(engine, member, sig):
        return None
    sessions = {str(getattr(f, "scope", "") or "") for f in cited} - {""}
    record = CoachProposalRecord(
        agent=_roster(member), kind=KIND_TEAM_NOTE, target=member, before="", after=habit.text,
        rationale=rationale_for(len(cited), len(sessions)),
        evidence_json=json.dumps([finding_view(f) for f in cited]), signature=sig, status=PENDING,
    )
    with session_scope(engine) as db:
        db.add(record)
        db.commit()
        db.refresh(record)
        db.expunge(record)
    return record


async def _publish(platform: Any, row: Any) -> None:
    """The coach's own ``coach.proposal`` event, with ``kind`` (the bell's
    quiet row). A bus hiccup never loses the row."""
    bus = getattr(platform, "event_bus", None)
    if bus is None:
        return
    from ..core.events import EventType

    try:
        await bus.publish(EventType.COACH_PROPOSAL, {
            "id": row.id, "agent": row.agent, "kind": row.kind, "categories": [row.kind],
            "rationale": row.rationale,
        })
    except Exception:  # noqa: BLE001
        log.debug("team note proposal publish failed", exc_info=True)


# --------------------------------------------------------------------------- #
# the distill
# --------------------------------------------------------------------------- #


@dataclass
class Distilled:
    member: str = ""
    minted: list[Any] = field(default_factory=list)
    reason: str = ""
    dropped: list[str] = field(default_factory=list)


async def distill(platform: Any, member: str, *, thread_id: str = "",
                  setup: "dict[str, Any] | None" = None) -> Distilled:
    """Distill ONE member's working habits (see the module docstring): at
    most :data:`MAX_HABITS` pending proposals, or none with ``reason``.
    Never raises."""
    out = Distilled(member=str(member or ""))
    try:
        return await _distill(platform, out, thread_id=str(thread_id or ""), setup=setup)
    except Exception:  # noqa: BLE001 — a distill must never take anything down
        log.warning("team notes distill failed", exc_info=True)
        out.reason = REASON_MODEL_FAILED
        return out


async def _distill(platform: Any, out: Distilled, *, thread_id: str,
                   setup: "dict[str, Any] | None") -> Distilled:
    from . import model_team as MT

    member = out.member
    engine = platform.engine
    if not _on(platform):
        out.reason = REASON_OFF
        return out
    registry = getattr(platform, "agents_registry", None)
    record = await asyncio.to_thread(registry.get, member) if registry is not None else None
    if record is None or not MT.is_member(record):
        out.reason = REASON_NOT_MEMBER
        return out
    if await asyncio.to_thread(attempted_recently, engine, member):
        out.reason = REASON_DAILY
        return out
    if len(await asyncio.to_thread(_waiting_texts, engine, member)) >= MAX_HABITS:
        out.reason = REASON_FULL
        return out
    found = await asyncio.to_thread(eligibility, engine, member)
    if not found.ok:
        out.reason = found.reason or REASON_FEW
        return out
    findings = found.findings
    rev = await asyncio.to_thread(reviewer_model, platform, findings)
    if not rev.provider:
        out.reason = REASON_NO_REVIEWER
        return out
    # HONEST MOCK, before the call (the adapter is looked up off the loop).
    if await asyncio.to_thread(_is_mock_provider, platform, rev.provider, rev.model):
        out.reason = REASON_MOCK
        return out
    local = await asyncio.to_thread(any_local_only, engine, findings)
    if local:
        from ..providers import egress

        if not egress.stays_on_this_network(
            rev.provider, getattr(platform, "providers", None), getattr(platform, "config", None), rev.model,
        ):
            out.reason = REASON_LOCAL_ONLY
            return out
    # The account the call leases: the reviewer's own (its member account),
    # else the coding session's (the library's way); never one at its limit.
    from ..harness import library as _library

    pin = None
    if rev.own and rev.record is not None:
        pin, why = await asyncio.to_thread(MT.member_account, platform, rev.record, None)
        if why:
            out.reason = why
            return out
    elif thread_id and (setup or {}).get("account"):
        try:
            from ..daemon.routes.build import turn_account

            pin, _prov = await asyncio.to_thread(turn_account, platform, thread_id, dict(setup or {}))
        except Exception:  # noqa: BLE001 — an unreachable account: no call on another
            out.reason = _library.REASON_ACCOUNT
            return out
    if pin is not None:
        refusal = await asyncio.to_thread(
            _library.account_refusal, platform, {"account": str(getattr(pin, "profile_id", "") or "")}
        )
        if refusal:
            out.reason = refusal
            return out
    # (Each finding is scanned again as the prompt is built: CPU work, so off
    # the loop — the v1.153.1 rule.)
    prompt, index = await asyncio.to_thread(distill_prompt, member, MT.roles_of(record), findings)
    shown ={str(f.scope or "") for f in findings if str(f.id) in set(index.values())} - {""}
    if len(index) < MIN_FINDINGS or len(shown) < MIN_SESSIONS:
        # (A finding the scan left out of the prompt no longer counts.)
        out.reason = REASON_FEW
        return out
    # THE DAILY LIMIT: the ATTEMPT is kept before the call (a crash or a
    # failed call still counts — the plan's limits are the user's).
    await asyncio.to_thread(_save_state, engine, member, last_attempt_at=utcnow(), last_reason="")
    route = await _complete(platform, rev, prompt, pin=pin, local_only=local)
    if route is None:
        out.reason = REASON_MODEL_FAILED
        await asyncio.to_thread(_save_state, engine, member, last_reason=out.reason)
        return out
    await asyncio.to_thread(_library._bill, platform, route, thread_id)
    # HONEST MOCK, after the call (a fallback that landed on the demo model).
    if _library._is_mock(getattr(route, "provider", ""), getattr(route, "reason", "")):
        out.reason = REASON_MOCK
        await asyncio.to_thread(_save_state, engine, member, last_reason=out.reason)
        return out
    text = str(getattr(getattr(route, "response", None), "text", "") or "")
    accepted = [n.text for n in await asyncio.to_thread(active_notes, engine, member)]
    waiting = await asyncio.to_thread(_waiting_texts, engine, member)
    # The answer is read (masked, bounded) and each habit scanned off the loop.
    habits, out.dropped = await asyncio.to_thread(
        lambda: check_habits(parse_habits(text), index, accepted=accepted, waiting=waiting)
    )
    by_id = {str(f.id): f for f in findings}
    room = MAX_HABITS - len(waiting)
    for habit in habits[: max(0, room)]:
        row = await asyncio.to_thread(_mint, engine, member, habit, by_id)
        if row is not None:
            out.minted.append(row)
    if not out.minted:
        out.reason = REASON_NOTHING
    await asyncio.to_thread(_save_state, engine, member, last_reason=out.reason)
    for row in out.minted:
        await _publish(platform, row)
    return out


async def _complete(platform: Any, rev: ReviewerModel, prompt: str, *, pin: Any,
                    local_only: bool) -> Any:
    """ONE pinned completion on the reviewer's model, HELD to it, on its
    account, under Local only when asked, within the library's bound. None
    on any failure."""
    from ..harness.library import REFLECT_TIMEOUT_S
    from ..iron_proxy.pin import reset_pin, set_pin
    from ..providers import egress
    from ..providers.adapters.base import LLMMessage
    from ..providers.hold import reset_hold, set_hold

    router = getattr(platform, "router", None)
    if router is None:
        return None
    hold = set_hold(rev.provider, rev.model, f"member:{rev.name}" if rev.own else "")
    token = set_pin(pin)
    try:
        with egress.local_only_scope(bool(local_only)):
            async with asyncio.timeout(REFLECT_TIMEOUT_S):
                return await router.complete(
                    provider=rev.provider, model=rev.model or None, system=DISTILL_SYSTEM,
                    messages=[LLMMessage(role="user", content=prompt)], tools=[],
                    session_id=None, pin=True,
                )
    except Exception:  # noqa: BLE001 — a failed call is a reason, never a crash
        log.info("team notes distill call failed", exc_info=True)
        return None
    finally:
        reset_pin(token)
        reset_hold(hold)


# --------------------------------------------------------------------------- #
# the pacing (the library's idle pacing; ``harness/library`` calls these)
# --------------------------------------------------------------------------- #

#: thread -> when the stretch the next distill covers began (its first turn).
_SINCE: dict[str, datetime] = {}
#: threads with a finished turn noted since their last distill.
_NOTED: set[str] = set()
_TIMERS: dict[str, asyncio.Task] = {}
_RUNNING: dict[str, asyncio.Task] = {}
_PENDING: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> None:
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def _idle_s() -> float:
    from ..harness.library import REFLECT_IDLE_S

    return float(REFLECT_IDLE_S)


def _cancel_timer(thread_id: str) -> None:
    timer = _TIMERS.pop(thread_id, None)
    if timer is not None and not timer.done() and timer is not asyncio.current_task():
        timer.cancel()


def _arm(platform: Any, thread_id: str, delay: float) -> "asyncio.Task | None":
    _cancel_timer(thread_id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _wait() -> None:
        await asyncio.sleep(max(0.0, delay))
        if _TIMERS.get(thread_id) is asyncio.current_task():
            _TIMERS.pop(thread_id, None)
        flush(platform, thread_id)

    task = loop.create_task(_wait())
    _TIMERS[thread_id] = task
    _track(task)
    return task


def note_turn_finished(platform: Any, thread_id: str, turn_id: str = "") -> "asyncio.Task | None":
    """A coding session's turn FINISHED (``library.note_turn_finished``): note
    the session and (re)start its idle wait. Nothing while the setting is
    off. Must be called on the loop. Never raises."""
    try:
        if not _on(platform) or not thread_id:
            return None
        _NOTED.add(thread_id)
        _SINCE.setdefault(thread_id, utcnow() - timedelta(seconds=DISTILL_EVERY_S))
        return _arm(platform, thread_id, _idle_s())
    except Exception:  # noqa: BLE001
        log.debug("noting a Build turn for team notes failed", exc_info=True)
        return None


def note_turn_started(thread_id: str) -> None:
    """A coding session's turn STARTED (``library.note_turn_started``): not
    idle — the wait stops; the stretch's start is kept (its first turn); and
    the NEW reply reads each member's habits afresh (:func:`habits_for_run`'s
    once-per-reply cache for this session is dropped). Never raises."""
    with contextlib.suppress(Exception):
        _cancel_timer(thread_id)
        if thread_id not in _NOTED:
            _SINCE[thread_id] = utcnow()
        forget_reply(f"build:{thread_id}")


def flush(platform: Any, thread_id: str) -> "asyncio.Task | None":
    """Distill the members who worked in the session NOW (its idle wait
    ended, or it was archived — ``library.flush``): ONE task, one call per
    eligible member. Nothing when nothing is noted; a session that is
    answering, or whose distill still runs, waits one more idle period. Must
    be called on the loop. Never raises."""
    try:
        _cancel_timer(thread_id)
        if thread_id not in _NOTED:
            return None
        running = _RUNNING.get(thread_id)
        if running is not None and not running.done():
            _arm(platform, thread_id, _idle_s())
            return None
        from ..harness.library import _session_busy

        if _session_busy(platform, thread_id):
            _arm(platform, thread_id, _idle_s())
            return None
        _NOTED.discard(thread_id)
        since = _SINCE.pop(thread_id, None)
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001
        log.debug("starting a team notes distill failed", exc_info=True)
        return None

    async def _run() -> list[Distilled]:
        return await distill_session(platform, thread_id, since=since)

    task = loop.create_task(_run())
    _RUNNING[thread_id] = task
    _track(task)

    def _done(t: asyncio.Task) -> None:
        if _RUNNING.get(thread_id) is t:
            _RUNNING.pop(thread_id, None)

    task.add_done_callback(_done)
    return task


def members_who_worked(engine: Any, scope: str, since: "datetime | None") -> list[str]:
    """The members with a piece in ``scope`` at or after ``since`` (else the
    last day), by name. BLOCKING; never raises."""
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from .team_record import TeamRoleRun

        start = _naive((since or utcnow() - timedelta(seconds=DISTILL_EVERY_S)) - timedelta(seconds=5))
        with session_scope(engine) as db:
            rows = db.exec(
                select(TeamRoleRun.member)
                .where(TeamRoleRun.scope == scope)
                .where(TeamRoleRun.created_at >= start)
            ).all()
        return sorted({str(m) for m in rows if str(m or "")})
    except Exception:  # noqa: BLE001
        log.debug("team members of a session unreadable", exc_info=True)
        return []


async def distill_session(platform: Any, thread_id: str, *,
                          since: "datetime | None" = None) -> list[Distilled]:
    """Distill every eligible member who worked in the coding session
    ``thread_id`` since ``since`` — one call each, in turn — unless the
    session's account is at or near its limit (the library's rule: the
    session is noted again and waits for a later idle period). Never
    raises."""
    try:
        if not _on(platform):
            return []
        from ..harness import library as _library
        from ..harness.sessions import _setup, get_row

        engine = platform.engine
        members = await asyncio.to_thread(members_who_worked, engine, f"build:{thread_id}", since)
        if not members:
            return []
        row = await asyncio.to_thread(get_row, engine, thread_id)
        setup = _setup(row) if row is not None else {}
        refusal = await asyncio.to_thread(_library.account_refusal, platform, setup)
        if refusal:
            if _library._retry_later(refusal):
                _NOTED.add(thread_id)
                if since is not None:
                    _SINCE.setdefault(thread_id, since)
            return [Distilled(member=m, reason=refusal) for m in members]
        out: list[Distilled] = []
        for member in members:
            out.append(await distill(platform, member, thread_id=thread_id, setup=setup))
        return out
    except Exception:  # noqa: BLE001
        log.warning("team notes distill of a session failed", exc_info=True)
        return []


def shutdown() -> None:
    """At the daemon's shutdown (``library.shutdown``): every wait and
    distill stops (nothing noted is kept — nothing was spent on it)."""
    for task in list(_PENDING):
        with contextlib.suppress(Exception):
            if not task.done():
                task.cancel()
    _PENDING.clear()
    _TIMERS.clear()
    _RUNNING.clear()
    _NOTED.clear()
    _SINCE.clear()


async def drain(timeout: float = 30.0) -> None:
    """Wait for the waits and distills started so far (tests)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        me = asyncio.current_task()
        tasks = [t for t in list(_PENDING) if not t.done() and t is not me]
        left = end - loop.time()
        if not tasks or left <= 0:
            return
        await asyncio.wait(tasks, timeout=left)


# --------------------------------------------------------------------------- #
# TEAM-NOTES.md (the member's agent folder)
# --------------------------------------------------------------------------- #

_RENDER_LOCK = threading.Lock()
_ACCEPT_LOCK = threading.Lock()


def _files(platform: Any) -> Any:
    files = getattr(getattr(platform, "agents_registry", None), "files", None)
    if files is not None:
        return files
    from .files import AgentFiles

    return AgentFiles(platform.config.home)


def notes_path(platform: Any, member: str) -> Any:
    """``<home>/agents/<slug>/TEAM-NOTES.md`` (confined by ``AgentFiles``)."""
    return _files(platform).folder(member) / NOTES_FILE


def render_text(habits: Iterable[str]) -> str:
    """TEAM-NOTES.md as Iron Jarvis writes it."""
    lines = [NOTES_TITLE, "", NOTES_COMMENT, ""]
    body = [f"- {_one_line(h)}" for h in habits if _one_line(h)]
    return "\n".join(lines + body).rstrip("\n") + "\n"


def _read(path: Any) -> "str | None":
    """The file's text (CRLF read as LF, a BOM dropped), or None when there
    is none or it cannot be read as UTF-8 text."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # (Line ends never make an edit: ``AgentFiles`` writes the OS's, and an
    # editor that only re-saves with other line ends changed nothing.)
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class Rendered:
    #: ``written`` | ``unchanged`` | ``kept`` (a hand edit) | ``failed`` | ``none``.
    state: str
    path: str = ""
    sentence: str = ""


def render(platform: Any, member: str) -> Rendered:
    """Write the member's TEAM-NOTES.md from its habits in force — ONLY
    while the file is exactly what Iron Jarvis last wrote (or there is none:
    nothing to overwrite). A file Iron Jarvis did not write, or one changed
    since, is the USER's: kept as it is, and :data:`KEPT_SENTENCE` says so.
    Atomic, with a revision copy of the previous text (``revisions/
    team-notes/``). A member with no habit and no file gets no file.
    BLOCKING; never raises."""
    engine = platform.engine
    with _RENDER_LOCK:
        try:
            path = notes_path(platform, member)
        except (ValueError, OSError) as exc:
            return Rendered(state="failed", sentence=f"TEAM-NOTES.md could not be written ({exc}).")
        try:
            habits = [n.text for n in active_notes(engine, member)]
            want = render_text(habits)
            state = _state(engine, member)
            current = _read(path) if path.exists() else None
            if path.exists() and current is None:
                return Rendered(state="kept", path=str(path), sentence=KEPT_SENTENCE)
            if current is not None and current == want:
                # Already exactly what Iron Jarvis would write: nothing to do
                # (and from now on it is Iron Jarvis's text again).
                _save_state(engine, member, written_sha=_sha(want))
                return Rendered(state="unchanged", path=str(path))
            if current is None:
                if not habits:
                    return Rendered(state="none", path=str(path))
            elif not state.written_sha or _sha(current) != state.written_sha:
                return Rendered(state="kept", path=str(path), sentence=KEPT_SENTENCE)
            if current is not None:
                _revision(path, current)
            from .files import _atomic_write

            _atomic_write(path, want)
            _save_state(engine, member, written_sha=_sha(want))
            return Rendered(state="written", path=str(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("TEAM-NOTES.md could not be written", exc_info=True)
            return Rendered(state="failed", sentence=f"TEAM-NOTES.md could not be written ({exc}).")


def _revision(path: Any, previous: str) -> None:
    """Keep the previous TEAM-NOTES.md text under ``revisions/team-notes/``
    (its own folder: AGENTS.md's revision list never shows it)."""
    from .files import _atomic_write, _stamp

    rdir = path.parent.joinpath(*NOTES_REVISIONS)
    base = f"{_stamp()}-team-notes"
    rid, n = base, 2
    while (rdir / f"{rid}.md").exists():
        rid = f"{base}-{n:03d}"
        n += 1
    _atomic_write(rdir / f"{rid}.md", previous)


def hand_edited(platform: Any, member: str) -> bool:
    """Does the member's TEAM-NOTES.md hold text Iron Jarvis did not write
    (the user's own file, or one changed since Iron Jarvis wrote it)?
    BLOCKING; never raises (False)."""
    try:
        path = notes_path(platform, member)
        if not path.exists():
            return False
        current = _read(path)
        state = _state(platform.engine, member)
        return current is None or not state.written_sha or _sha(current) != state.written_sha
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# accept / remove
# --------------------------------------------------------------------------- #


@dataclass
class Accepted:
    row: Any
    note: str = ""
    rendered: "Rendered | None" = None
    #: v1.423.0: library ``layer_edit`` rows minted for a role's brief (the
    #: caller publishes them on the loop).
    library_rows: list[Any] = field(default_factory=list)


def accept(platform: Any, proposal_id: str) -> Accepted:
    """Accept a ``team_note`` proposal: a :class:`TeamNote` in force and the
    proposal ``accepted`` in ONE transaction, then TEAM-NOTES.md rendered
    (:func:`render`), then the role-brief check (:func:`role_edit_for`).
    ``KeyError`` (no proposal), ``ValueError`` (already decided, or not a
    team note), ``coach.engine.StaleProposal`` (the member is gone) — the
    coach route's 404 / 409 sentences. BLOCKING."""
    from ..coach.engine import StaleProposal
    from ..coach.models import ACCEPTED, KIND_TEAM_NOTE, PENDING, STALE, CoachProposalRecord
    from ..core.db import session_scope

    engine = platform.engine
    with _ACCEPT_LOCK:  # one decision at a time: a double press keeps ONE habit
        with session_scope(engine) as db:
            row = db.get(CoachProposalRecord, str(proposal_id or ""))
            if row is not None:
                db.expunge(row)
        if row is None:
            raise KeyError(f"no such proposal {proposal_id!r}")
        if row.kind != KIND_TEAM_NOTE:
            raise ValueError("this proposal is not a working habit")
        if row.status != PENDING:
            raise ValueError(f"this proposal was already {row.status}")
        member = str(row.target or "")
        registry = getattr(platform, "agents_registry", None)
        gone = registry is None or registry.get(member) is None
        with session_scope(engine) as db:
            live = db.get(CoachProposalRecord, row.id)
            if live is None or live.status != PENDING:
                raise ValueError(f"this proposal was already {getattr(live, 'status', 'removed')}")
            live.decided_at = utcnow()
            if gone:
                live.status = STALE
                db.add(live)
                db.commit()
                raise StaleProposal("the agent no longer exists — nothing to write")
            note = TeamNote(member=member, text=str(live.after or ""),
                            finding_ids_json=json.dumps(_cited_ids(live)), proposal_id=live.id)
            live.status = ACCEPTED
            db.add(note)
            db.add(live)
            db.commit()
            db.refresh(live)
            db.refresh(note)
            db.expunge(live)
            db.expunge(note)
            row = live
    rendered = render(platform, member)
    sentences = [rendered.sentence] if rendered.sentence else []
    if not _on(platform):
        sentences.append(OFF_SENTENCE)
    lib_rows: list[Any] = []
    try:
        minted = role_edit_for(platform, note)
        if minted is not None:
            lib_rows.append(minted)
    except Exception:  # noqa: BLE001 — a suggestion never breaks the Accept
        log.debug("role brief edit check failed", exc_info=True)
    return Accepted(row=row, note=" ".join(sentences), rendered=rendered, library_rows=lib_rows)


class NoteRefused(Exception):
    """A plain sentence; ``status`` is the HTTP status the route answers."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def remove(platform: Any, note_id: str) -> dict[str, Any]:
    """Take an accepted habit back (``removed_at``); TEAM-NOTES.md is
    rendered again under the hand-edit rule. ``NoteRefused`` 404 / 409.
    BLOCKING."""
    from ..core.db import session_scope

    with session_scope(platform.engine) as db:
        note = db.get(TeamNote, str(note_id or ""))
        if note is None:
            raise NoteRefused("No such working habit.", 404)
        if note.removed_at is not None:
            raise NoteRefused("This working habit was already removed.", 409)
        note.removed_at = utcnow()
        db.add(note)
        db.commit()
        db.refresh(note)
        db.expunge(note)
    forget_member(note.member)
    rendered = render(platform, note.member)
    return {"note": note_view(note), "file": rendered.state, "message": rendered.sentence}


# --------------------------------------------------------------------------- #
# the views (GET /team)
# --------------------------------------------------------------------------- #


def notes_for(platform: Any, members: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{member: {accepted: [note_view…], suggested: n, hand_edited}}`` for
    Settings › Model team. BLOCKING; never raises (empty lists)."""
    from ..coach.models import PENDING

    names = [str(m) for m in members if str(m or "")]
    out = {n: {"accepted": [], "suggested": 0, "hand_edited": False} for n in names}
    engine = getattr(platform, "engine", None)
    if engine is None or not names:
        return out
    try:
        for note in active_notes(engine):
            if note.member in out:
                out[note.member]["accepted"].append(note_view(note))
        for name in names:
            out[name]["suggested"] = sum(1 for r in _team_note_proposals(engine, name) if r.status == PENDING)
            if out[name]["accepted"]:
                out[name]["hand_edited"] = hand_edited(platform, name)
    except Exception:  # noqa: BLE001
        log.debug("team notes view unreadable", exc_info=True)
    return out


# --------------------------------------------------------------------------- #
# injection: only into the member's TEAM pieces, once per reply
# --------------------------------------------------------------------------- #

#: (reply key, member) -> the section ("" = none) — read once per reply.
_REPLY_CACHE: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_REPLY_CACHE_MAX = 512
_CACHE_LOCK = threading.Lock()


def forget_reply(key: str) -> None:
    """A new reply of ``key`` (``build:<thread>``) reads afresh."""
    with _CACHE_LOCK:
        for k in [k for k in _REPLY_CACHE if k[0] == key]:
            _REPLY_CACHE.pop(k, None)


def forget_member(member: str) -> None:
    """Drop every cached section of ``member`` (a habit removed)."""
    with _CACHE_LOCK:
        for k in [k for k in _REPLY_CACHE if k[1] == member]:
            _REPLY_CACHE.pop(k, None)


def notes_body(text: str) -> str:
    """The habits part of TEAM-NOTES.md: the title line and Iron Jarvis's
    one-line comment left out. Linear (lines, no regex)."""
    out: list[str] = []
    first = True
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if first and s:
            first = False
            if s.startswith("# "):
                continue
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def section_for(platform: Any, member: str, *, event_bus: Any = None,
                session_id: "str | None" = None) -> str:
    """The ``# Your working habits …`` section of a member — its
    TEAM-NOTES.md text (the user's own edits included), promptguard-scanned
    (a flagged paragraph becomes the guard's placeholder), at most
    :data:`INJECT_CHARS` in all; "" when there is no text. BLOCKING; never
    raises."""
    try:
        path = notes_path(platform, member)
        if not path.exists():
            return ""
        body = notes_body(_read(path) or "")
        if not body:
            return ""
        from ..core.promptguard import guard

        room = INJECT_CHARS - len(HABITS_HEADER) - 1
        body = guard(body, source=f"working habits of {member}", event_bus=event_bus,
                     session_id=session_id, cap=room).strip()
        if not body:
            return ""
        return (HABITS_HEADER + "\n" + body)[:INJECT_CHARS]
    except Exception:  # noqa: BLE001 — a member's prompt never breaks its start
        log.debug("working habits unreadable for %s", member, exc_info=True)
        return ""


def _reply_key(session: Any, parent_id: "str | None") -> str:
    """The reply a TEAM piece belongs to ("" = not a team piece): a coding
    session's helper → its session (``build:<thread>``; a new turn drops it,
    :func:`note_turn_started`); a mission's teammate → its coordinator's run."""
    from ..harness.helpers import BUILD_SESSION_OPTION, HELPER_ORIGIN
    from ..scheduling.knobs import session_options
    from .team import MISSION_MEMBER_ORIGIN

    origin = str(getattr(session, "origin", "") or "")
    if origin == HELPER_ORIGIN:
        thread = str(session_options(session).get(BUILD_SESSION_OPTION) or "")
        return f"build:{thread}" if thread else f"session:{getattr(session, 'id', '')}"
    if origin == MISSION_MEMBER_ORIGIN:
        return f"run:{parent_id}" if parent_id else f"session:{getattr(session, 'id', '')}"
    return ""


def habits_for_run(platform: Any, session: Any, parent_id: "str | None" = None) -> str:
    """The working-habits section a RUN's system prompt gets (the runtime
    calls it off the loop): only a model team member's TEAM piece — a coding
    session's helper or a mission's teammate — only while the setting is on,
    read ONCE per reply (:func:`_reply_key`). "" for anything else: a
    member's own runs, a non-member, no habits. BLOCKING; never raises."""
    try:
        if not _on(platform):
            return ""
        key = _reply_key(session, parent_id)
        if not key:
            return ""
        member = _member_name(getattr(session, "agent_name", ""))
        if not member:
            return ""
        from .model_team import is_member

        registry = getattr(platform, "agents_registry", None)
        record = registry.get(member) if registry is not None else None
        if record is None or not is_member(record):
            return ""
        with _CACHE_LOCK:
            if (key, member) in _REPLY_CACHE:
                _REPLY_CACHE.move_to_end((key, member))
                return _REPLY_CACHE[(key, member)]
        text = section_for(platform, member, event_bus=getattr(platform, "event_bus", None),
                           session_id=str(getattr(session, "id", "") or "") or None)
        with _CACHE_LOCK:
            text = _REPLY_CACHE.setdefault((key, member), text)
            while len(_REPLY_CACHE) > _REPLY_CACHE_MAX:
                _REPLY_CACHE.popitem(last=False)
        return text
    except Exception:  # noqa: BLE001
        log.debug("working habits for a run failed", exc_info=True)
        return ""


# --------------------------------------------------------------------------- #
# one habit, two members of a role → the role's brief (library layer_edit)
# --------------------------------------------------------------------------- #

ROLE_EDIT_WHY = (
    "{a} and {b} both hold the {role} role and were given the same working habit; keep it in "
    "the role's brief."
)


def _findings_by_id(engine: Any, ids: Iterable[str]) -> list[Any]:
    from sqlmodel import select

    from ..core.db import session_scope
    from .team_findings import TeamFinding

    want = [str(i) for i in ids if str(i or "")]
    if not want:
        return []
    with session_scope(engine) as db:
        rows = list(db.exec(select(TeamFinding).where(TeamFinding.id.in_(want))))  # type: ignore[attr-defined]
        for r in rows:
            db.expunge(r)
    return rows


def _note_ids(note: Any) -> list[str]:
    try:
        ids = json.loads(getattr(note, "finding_ids_json", "") or "[]")
    except (TypeError, ValueError):
        return []
    return [str(i) for i in ids if str(i or "")] if isinstance(ids, list) else []


def role_edit_for(platform: Any, note: TeamNote) -> Any:
    """When ``note`` (member X's accepted habit) is the SAME habit as an
    accepted habit of ANOTHER member holding one of X's roles, propose the
    library ``layer_edit`` that adds it to that role's brief
    (``library.propose_role_brief_edit``) in the NEWEST coding-session
    folder among the cited findings' sessions. Returns the minted library
    row, or None. BLOCKING; never raises."""
    from . import model_team as MT

    try:
        engine = platform.engine
        registry = platform.agents_registry
        mine = registry.get(note.member)
        roles = MT.roles_of(mine) if mine is not None else []
        if not roles:
            return None
        match = None
        role = ""
        for other in active_notes(engine):
            if other.member == note.member or other.id == note.id:
                continue
            rec = registry.get(other.member)
            shared = [r for r in roles if r in (MT.roles_of(rec) if rec is not None else [])]
            if shared and same_habit(note.text, other.text):
                match, role = other, shared[0]
                break
        if match is None:
            return None
        findings = _findings_by_id(engine, [*_note_ids(note), *_note_ids(match)])
        findings.sort(key=lambda f: _naive(f.created_at) or datetime.min, reverse=True)
        from ..harness import library as _library

        tried: set[str] = set()
        for f in findings:
            thread = _thread_of_scope(str(f.scope or ""))
            if not thread or thread in tried:
                continue
            tried.add(thread)
            row, why = _library.propose_role_brief_edit(
                platform, thread_id=thread, role=role, line=note.text,
                why=ROLE_EDIT_WHY.format(a=note.member, b=match.member, role=role),
                evidence={"team_notes": [note.id, match.id], "findings": [str(x.id) for x in findings]},
                same=lambda existing: same_habit(existing, note.text),
            )
            if row is not None:
                return row
            if why != _library.REASON_NO_FOLDER:
                # The newest folder answered (full, already there, invalid…):
                # an older folder is never edited instead.
                log.info("a role brief edit was not proposed: %s", why)
                return None
        return None
    except Exception:  # noqa: BLE001
        log.debug("role brief edit check failed", exc_info=True)
        return None


__all__ = [
    "Accepted", "DISTILL_EVERY_S", "Distilled", "Eligibility", "HABITS_HEADER", "INJECT_CHARS",
    "KEPT_SENTENCE", "MAX_HABITS", "MAX_HABIT_CHARS", "MIN_CITED", "MIN_FINDINGS", "MIN_SESSIONS",
    "NOTES_FILE", "NoteRefused", "SAME_HABIT_OVERLAP", "SUPPRESS_DAYS", "TeamNote", "TeamNoteState",
    "accept", "active_notes", "check_habits", "distill", "distill_prompt", "distill_session", "drain",
    "eligibility", "flush", "habits_for_run", "members_who_worked", "note_turn_finished",
    "note_turn_started", "notes_body", "notes_for", "parse_habits", "remove", "render", "render_text",
    "role_edit_for", "same_habit", "section_for", "shutdown", "word_overlap",
]
