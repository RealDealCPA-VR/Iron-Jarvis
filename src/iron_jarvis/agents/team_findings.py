"""TEAM NOTES, Phase 1b — A REVIEWER'S FINDINGS BECOME RECORDS (v1.423.0, the
2026-11-01 audit's "The owner's idea: Team notes", Wave 3; the owner: "This
will be the specifics I can feed back to the agent working on the app").

Wave 1 (v1.421.0, A4) asked the team's reviewer to list each problem on ONE
numbered line above its verdict (``harness/finish.REVIEW_FINDINGS_ASK``)::

    R1. app.py:12 — what is wrong — what to change
    R2. (general) — what is wrong — what to change

and the lead to answer each by its number. This module turns those lines into
RECORDS, with no extra model call — plain code reads text the models already
wrote:

* :func:`parse_findings` reads a reviewer's report into :class:`Finding` s
  (the ``FINDING_RX`` lines, never the last line — the verdict);
* :func:`record_findings` stores them (``TeamFinding``, a late model in
  ``core.db._LATE_MODEL_MODULES``) when ``model_team.note_piece`` records a
  ``review`` piece — under the verdict's own conditions: the run FINISHED,
  the report is one the reviewer WROTE (never the runtime's no-summary
  result), and the run did not end at LOW trust. Each finding's text is
  masked (``detections.redact.mask``) BEFORE any cut, and a finding that
  promptguard flags as an instruction is DROPPED, never stored. Each is
  ATTRIBUTED to the worker whose piece WROTE that path: the newest model-team
  piece of the same work (``scope``) and reply (``turn``) before the review,
  not itself a review, whose files (``team_record.files_of``) hold the path
  (:func:`same_path`); none → no member;
* :func:`parse_answers` reads the LEAD's answers (``R1 fixed`` / ``R2 does
  not apply: why``), and :func:`settle` applies them when the reply ends
  (``harness/finish.FinishCheck.record``): ``fixed`` is CONFIRMED only when a
  LATER piece of the same work and reply wrote that path (``fixed_by``); a
  finding no member made (the lead's own change, a reply with no team) may be
  confirmed by the session's own journaled writes after the review; else the
  lead's word stands as ``said_fixed``. A finding never answered stays
  ``open``;
* :func:`findings_for` / :func:`member_findings` are the member card's line
  (``GET /team`` → Settings › Model team): confirmed, did not apply, said
  fixed, open — and FIRST-PASS APPROVAL (the reviewer's verdict on the
  member's EARLIEST judged piece of each reply), over a window (30 days by
  default, or an explicit ``since`` / ``until`` pair: a later phase splits a
  member's record at the moment a note was accepted).

Every reader and writer here is BLOCKING (SQLite) and NEVER raises: the doors
and the finish check call it off the loop (``asyncio.to_thread``), and a
record that cannot be written or read never breaks a reply.

v1.423.0 REVIEW (each pinned in ``tests/test_team_findings_review_v14230.py``):

* ``model_team.note_piece`` reads nothing — no verdict, no finding — from a
  report that reads AS A WHOLE like an instruction (the finish check's own
  ``detect_injection``, by which it withholds that report from the lead);
* a path names ONE file of the reply or no one (:func:`resolve_path`): an
  exact name always wins, a suffix ("app.py" for "src/app.py") counts only
  when no other file of the reply shares it — for the attribution and for a
  "fixed" confirmation alike;
* only a finding the lead was ASKED to answer (``TeamFinding.asked``, set by
  :func:`mark_asked` when the finish check's note shows its report) is
  counted (:func:`findings_for`): a review a lead hands out itself, or a
  mission's, is kept but never asked;
* the readers are LINEAR on model text (``combine()`` reads the lead's answer
  lines on the event loop): atomic groups, runs read once, and a line no
  longer than :data:`FINDING_LINE_CHARS` / :data:`ANSWER_LINE_CHARS` (masked
  before it is cut), scanned over exactly the length it is parsed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)

#: A finding's states. Only :data:`FIXED` counts as CONFIRMED: the lead said
#: fixed AND a later piece (or, for a finding no member made, the session's
#: own journaled write) changed that file.
OPEN = "open"
FIXED = "fixed"
SAID_FIXED = "said_fixed"
DID_NOT_APPLY = "did_not_apply"
STATUSES = (OPEN, FIXED, SAID_FIXED, DID_NOT_APPLY)

#: What the lead's answer line says (:func:`parse_answers`).
ANSWER_FIXED = "fixed"
ANSWER_DOES_NOT_APPLY = "did_not_apply"

#: The stored text's caps (each masked BEFORE it is cut).
MAX_PATH_CHARS = 300
MAX_WHAT_CHARS = 600
MAX_CHANGE_CHARS = 600
MAX_ANSWER_CHARS = 400
MAX_NAME_CHARS = 120
#: The member card's window (``GET /team``).
WINDOW_DAYS = 30
#: The source label a flagged finding would be named by (promptguard).
FINDING_SOURCE = "a reviewer's finding"
#: How many pieces before a review the attribution reads (newest first).
_ATTRIBUTION_ROWS = 400
#: v1.423.0 review: the longest line either reader reads — a model's text is
#: read on the EVENT LOOP (``harness/finish.FinishCheck.combine``), and a
#: worker thread running ``re`` still holds the GIL. A longer line is MASKED,
#: then cut (a cut through a key leaves a piece the mask no longer sees). A
#: finding line is scanned (promptguard) over the same length it is parsed.
FINDING_LINE_CHARS = 4 * (MAX_WHAT_CHARS + MAX_CHANGE_CHARS)
ANSWER_LINE_CHARS = 2000
#: A finding's place longer than this is no path (prose: its words go to
#: ``what``) — the location patterns never read a long run.
_MAX_LOC_CHARS = 2 * MAX_PATH_CHARS


class TeamFinding(SQLModel, table=True):
    """One problem a model-team reviewer named, and what became of it."""

    __tablename__ = "teamfinding"

    id: str = Field(default_factory=lambda: new_id("tfd"), primary_key=True)
    #: The work the review belongs to (``team_record.TeamRoleRun.scope``:
    #: ``build:<id>`` for a coding session, the coordinator's session for a
    #: mission) and its reply (``turn``; "" outside a coding session).
    scope: str = Field(default="", index=True)
    turn: str = Field(default="", index=True)
    #: The review piece's ``TeamRoleRun`` id ("" when none) and its session.
    review_row: str = Field(default="", index=True)
    review_session: str = Field(default="", index=True)
    #: The reviewer member's name ("" when unknown).
    reviewer: str = ""
    #: The finding's own number (``R<n>``).
    number: int = 0
    #: The place it names: a path (masked, "" for "(general)") and a line.
    path: str = ""
    line: int | None = None
    what: str = ""
    change: str = ""
    #: The worker it is ATTRIBUTED to — whose piece wrote that path ("" when
    #: no piece did) — and that piece's session.
    member: str = Field(default="", index=True)
    worker_session: str = ""
    #: :data:`STATUSES`.
    status: str = Field(default=OPEN, index=True)
    #: The lead's own words after its answer (its "why"; masked, capped).
    answer: str = ""
    #: The session whose write CONFIRMED a ``fixed`` ("" otherwise).
    fixed_by: str = ""
    #: v1.423.0 review: was the lead ASKED to answer it — the finish check's
    #: note showed this review's report (:func:`mark_asked`)? A review a lead
    #: hands out itself, or a mission's, is never asked: its findings are kept
    #: but never counted (:func:`findings_for`), or they would sit "not
    #: answered" forever. Every reader of the numbers reads asked rows only.
    asked: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    settled_at: datetime | None = None


@dataclass(frozen=True)
class Finding:
    """One finding line of a reviewer's report, read (not yet masked)."""

    number: int
    path: str
    line: "int | None"
    what: str
    change: str
    #: The whole line (what promptguard reads).
    text: str


# --------------------------------------------------------------------------- #
# the reviewer's report
# --------------------------------------------------------------------------- #

#: A ``path:line`` location — ``app.py:12``, ``src/a.py:12-15``,
#: ``app.py:12:5``, ``C:\x\y.py:12`` (the non-greedy path walks past the
#: drive's colon: ``\x`` is no line number), ``app.py#L12``.
_LOC_RX = re.compile(
    r"^(?P<path>.+?)(?::L?(?P<line>\d{1,9})(?:\s*[-–:,]\s*L?\d{1,9})*|#L(?P<line2>\d{1,9})(?:-L?\d{1,9})?)$"
)
#: A ``path:line`` token somewhere inside a location written as prose
#: ("in app.py:12", "app.py line 12", "app.py: line 12").
#: (v1.423.0 review: it reads at most ``_MAX_LOC_CHARS`` — :func:`_location`.)
_LOC_TOKEN_RX = re.compile(
    r"((?:[A-Za-z]:)?[\w./\\-]*\w\.\w{1,10})(?::L?|:?\s*,?\s*(?:line|ln\.?)\s*)(\d{1,9})", re.IGNORECASE
)
#: Separators of a finding's three parts: spaced dashes first, then a bare
#: em/en dash, then " - ".
#: v1.423.0 review: each starts only at the FIRST space of a run (``(?<!\s)``
#: — the same leftmost matches) so a long run of spaces is read once, not
#: once per space (quadratic: a 60,000-space line took seconds).
_SPACED_DASH = re.compile(r"(?<!\s)\s+[—–]\s+")
_BARE_DASH = re.compile(r"(?<!\s)\s*[—–]\s*")
_HYPHEN = re.compile(r"(?<!\s)\s+-\s+")
_GENERAL = {"(general)", "general", "[general]", "(general):"}


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _looks_like_path(text: str) -> bool:
    t = text.strip()
    if not t or re.search(r"\s", t) and not re.match(r"^[A-Za-z]:[\\/]", t):
        return False
    return bool(re.search(r"[\\/]", t) or re.search(r"\w\.\w{1,10}$", t))


def _location(raw: str) -> "tuple[str, int | None, str]":
    """``(path, line, leftover)`` of a finding's first part. A part that is
    no path (prose) keeps a ``path:line`` token found inside it; else it is
    general and its words go back to the ``what`` (``leftover``)."""
    loc = str(raw or "").replace("`", "").replace("*", "").strip().strip("'\"").strip()
    loc = loc.rstrip(":,;").strip()
    if loc.lower() in _GENERAL or not loc:
        return "", None, ""
    if len(loc) > _MAX_LOC_CHARS:
        return "", None, loc  # no path is this long: prose (v1.423.0 review)
    m = _LOC_RX.match(loc)
    if m and _looks_like_path(m.group("path")):
        line = m.group("line") or m.group("line2")
        return m.group("path").strip(), int(line), ""
    if _looks_like_path(loc):
        return loc, None, ""
    tok = _LOC_TOKEN_RX.search(loc)
    if tok:
        return tok.group(1), int(tok.group(2)), ""
    return "", None, loc


def _split_parts(body: str) -> list[str]:
    for rx in (_SPACED_DASH, _BARE_DASH, _HYPHEN):
        parts = [p.strip() for p in rx.split(body)]
        if len(parts) > 1:
            return parts
    return [body.strip()]


def _parse_line(number: int, line: str) -> "Finding | None":
    from ..harness.finish import FINDING_RX

    m = FINDING_RX.match(line)
    if m is None:
        return None
    body = line[m.end():].strip()
    parts = _split_parts(body)
    if len(parts) == 1:
        # No separator: the first word is the place when it reads as one.
        first, _, rest = parts[0].partition(" ")
        path, ln, leftover = _location(first)
        if path or first.replace("`", "").strip().lower() in _GENERAL:
            what, change = rest.strip(), ""
        else:
            path, ln, what, change = "", None, parts[0], ""
            leftover = ""
    else:
        path, ln, leftover = _location(parts[0])
        if len(parts) == 2:
            what, change = parts[1], ""
        else:
            sep = " — "
            what, change = sep.join(parts[1:-1]), parts[-1]
    if leftover:
        what = f"{leftover} — {what}" if what else leftover
    what, change = _one_line(what), _one_line(change)
    if not what:
        return None  # a bare place says no problem
    return Finding(number=number, path=_one_line(path), line=ln, what=what, change=change,
                   text=_one_line(line))


def _bounded(line: str, cap: int) -> str:
    """A line as the readers read it (v1.423.0 review): whole when it fits
    ``cap``; else MASKED (``detections.redact.mask``), then cut — never a
    long run through a pattern on the loop, and never half a key kept."""
    if len(line) <= cap:
        return line
    from ..detections.redact import mask

    return str(mask(line))[:cap]


def parse_findings(report: Any) -> list[Finding]:
    """Every finding line of a reviewer's report (``harness/finish.
    FINDING_RX`` — the ONE regex the lead's note keeps whole, so the records
    are exactly the lines the lead was shown), in order. Never the LAST
    non-empty line (the verdict's — ``team_record.verdict_of``); a number
    seen twice keeps its FIRST line; at most ``finish.MAX_REVIEW_FINDINGS``;
    a line that names a place and no problem is not a finding. The three
    parts split at "—", "–" or " - "; the place is ``path:line`` (a Windows
    drive letter's colon is not a line number), a path alone, or
    ``(general)``. Not masked here (:func:`record_findings` masks before it
    cuts). Pure; never raises."""
    try:
        from ..harness.finish import FINDING_RX, MAX_REVIEW_FINDINGS

        lines = str(report or "").splitlines()
        last = len(lines) - 1
        while last >= 0 and not lines[last].strip():
            last -= 1
        out: list[Finding] = []
        seen: set[int] = set()
        for i, line in enumerate(lines):
            if i == last:
                continue
            m = FINDING_RX.match(line)
            if m is None:
                continue
            line = _bounded(line, FINDING_LINE_CHARS)
            number = int(m.group(1))
            if number in seen:
                continue
            found = _parse_line(number, line)
            if found is None:
                continue
            seen.add(number)
            out.append(found)
            if len(out) >= MAX_REVIEW_FINDINGS:
                break
        return out
    except Exception:  # noqa: BLE001 — no record beats a broken reply
        log.debug("team findings: a report could not be read", exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# the lead's answers
# --------------------------------------------------------------------------- #

#: One answer: its R numbers (``R1``, ``R1 and R3``, ``R1, R3``, ``R1 & R3``),
#: a separator, a few plain fillers ("is", "was", "now"…) and the answer
#: itself AS THE FIRST WORD — so "R3 not fixed" is no "fixed", and a finding
#: line the lead quotes ("R1. app.py:3 — …") is no answer.
#: v1.423.0 review: every run of spaces and marks is taken ONCE (atomic
#: groups): the builder's ``,\s*(?:and\s+)?`` beside another ``\s*``, and five
#: optional ``\s*`` in a row, backtracked EXPONENTIALLY — "R1, R2, … R22
#: maybe" took 5 s, each more number doubled it, and ``combine()`` reads these
#: lines on the event loop.
_ANSWER_BODY = (
    r"(?P<nums>R\d{1,3}(?>\s*(?:,(?:\s*and\b)?|&|/|and\b)\s*R?\d{1,3})*)"
    r"(?>[\s*_:.)\-–—]*)"
    r"(?:(?:is|was|are|were|has\s+been|have\s+been|now|all|both)\s+)*"
    r"(?P<status>fixed|does\s+not\s+apply|did\s+not\s+apply|do\s+not\s+apply|"
    r"doesn['’]t\s+apply|didn['’]t\s+apply|don['’]t\s+apply|not\s+applicable)"
    r"(?![\w'’-])"
)
_ANSWER_HEAD_RX = re.compile(r"(?>\s*(?:[-*+•]\s+|\d{1,3}[.)]\s+)?(?:\*\*|__)?\s*)" + _ANSWER_BODY,
                             re.IGNORECASE)
_ANSWER_AT_RX = re.compile(_ANSWER_BODY, re.IGNORECASE)
#: Where a second answer may begin on the same line ("R1 fixed; R2 does not
#: apply: …", "R1 fixed, R2 …", "R1 fixed and R2 …").
_NEXT_ANSWER_RX = re.compile(r"(?:[;,.]|(?<!\s)\s+and)\s+(?=R\d)", re.IGNORECASE)
_FENCE_RX = re.compile(r"^\s*(```|~~~)")
_WHY_LEAD_RX = re.compile(r"^[\s:;,.\-–—)]*(?:because\s+)?", re.IGNORECASE)


def _quoted_finding(line: str) -> bool:
    """A finding line the lead QUOTES (``R1. path — what — change``: the
    finding's own two separators) — never an answer."""
    from ..harness.finish import FINDING_RX

    if not FINDING_RX.match(line):
        return False
    return len(_SPACED_DASH.findall(line)) + len(_HYPHEN.findall(line)) >= 2


def _answers_in(line: str) -> list[tuple[list[int], str, str]]:
    """``[(numbers, status, why)]`` the line answers, left to right."""
    out: list[tuple[list[int], str, str]] = []
    m = _ANSWER_HEAD_RX.match(line)
    while m is not None:
        nums = [int(n) for n in re.findall(r"\d{1,3}", m.group("nums"))]
        word = " ".join(m.group("status").lower().split())
        status = ANSWER_FIXED if word == "fixed" else ANSWER_DOES_NOT_APPLY
        nxt = None
        for sep in _NEXT_ANSWER_RX.finditer(line, m.end()):
            cand = _ANSWER_AT_RX.match(line, sep.end())
            if cand is not None:
                nxt = (sep.start(), cand)
                break
        why = line[m.end(): nxt[0] if nxt else len(line)]
        why = _WHY_LEAD_RX.sub("", why, count=1).strip().strip("*_").strip()
        out.append((nums, status, why))
        m = nxt[1] if nxt else None
    return out


def _answer_line_texts(text: Any) -> list[str]:
    """The lines of ``text`` an answer may be read from: outside fenced code,
    not quoted (``>``), not a finding line the lead quotes."""
    out: list[str] = []
    fenced = False
    for line in str(text or "").splitlines():
        line = _bounded(line, ANSWER_LINE_CHARS)
        if _FENCE_RX.match(line):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith(">") or _quoted_finding(line):
            continue
        out.append(line)
    return out


def answer_lines(text: Any) -> list[str]:
    """The lines of ``text`` (stripped, in order, once each) that answer a
    finding (:func:`parse_answers` reads each of them) — what the user keeps
    in the saved reply. Pure; never raises."""
    out: list[str] = []
    try:
        for line in _answer_line_texts(text):
            if _answers_in(line):
                kept = line.strip()
                if kept and kept not in out:
                    out.append(kept)
    except Exception:  # noqa: BLE001
        return out
    return out


def parse_answers(text: Any) -> dict[int, tuple[str, str]]:
    """``{number: (status, why)}`` from the lead's own words after a review:
    lines that BEGIN with ``R1 fixed``, ``R1: fixed``, ``R2 does not apply:
    <why>``, ``R2 did not apply — <why>``, ``R1 and R3 fixed``, ``R1, R3
    fixed`` (case-insensitive, a list marker or bold allowed), and further
    answers on the same line after ";", "," or "and". ``status`` is
    :data:`ANSWER_FIXED` or :data:`ANSWER_DOES_NOT_APPLY`; ``why`` is the
    rest of the answer (unmasked here). "R3 not fixed" answers nothing; a
    number answered BOTH ways is left out (open); quoted text (``>``) and
    fenced code are not answers, nor a finding line the lead quotes. Pure;
    never raises."""
    out: dict[int, tuple[str, str]] = {}
    both: set[int] = set()
    try:
        for line in _answer_line_texts(text):
            for nums, status, why in _answers_in(line):
                for n in nums:
                    if n in both:
                        continue
                    if n in out and out[n][0] != status:
                        both.add(n)
                        out.pop(n, None)
                        continue
                    out.setdefault(n, (status, why))
    except Exception:  # noqa: BLE001
        log.debug("team findings: an answer could not be read", exc_info=True)
        return {}
    return out


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #


def _norm_path(path: Any, fold: "bool | None" = None) -> str:
    s = str(path or "").strip().strip("`'\"").strip().replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    while s.startswith("./"):
        s = s[2:]
    s = s.rstrip("/")
    if fold if fold is not None else os.name == "nt":
        s = s.casefold()
    return s


def same_path(a: Any, b: Any, *, fold: "bool | None" = None) -> bool:
    """Do two names of a file name the SAME file, as far as text can tell?
    Slashes either way, a leading "./" dropped, case folded on Windows
    (``fold``: the platform's rule unless given), and one may be a suffix of
    the other at a "/" boundary (``src/app.py`` and ``app.py``; never
    ``data.py`` and ``a.py``). Pure."""
    x, y = _norm_path(a, fold), _norm_path(b, fold)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    return long.endswith("/" + short)


def resolve_path(path: Any, candidates: Iterable[Any], *, fold: "bool | None" = None) -> str:
    """The ONE file among ``candidates`` (the files a reply's pieces wrote)
    that ``path`` names — normalised (:func:`same_path`'s rule) — or "" when
    it names none or is AMBIGUOUS (v1.423.0 review). An exact name always
    wins; a suffix match ("app.py" for "src/app.py") counts only when it is
    the ONLY candidate that matches, so a fix to ``tests/app.py`` never
    confirms a finding about ``app.py``, and "app.py" names no one when a
    reply wrote both ``src/app.py`` and ``tests/app.py``. Pure."""
    p = _norm_path(path, fold)
    if not p:
        return ""
    norms = {_norm_path(c, fold) for c in candidates} - {""}
    if p in norms:
        return p
    hits = {c for c in norms if same_path(p, c, fold=fold)}
    return next(iter(hits)) if len(hits) == 1 else ""


def _holds(files: Iterable[Any], target: str, fold: "bool | None" = None) -> bool:
    """Does a piece's file list hold exactly the (normalised) ``target``?"""
    return bool(target) and any(_norm_path(f, fold) == target for f in files)


# --------------------------------------------------------------------------- #
# storing a review's findings
# --------------------------------------------------------------------------- #


def _masked(text: Any, cap: int) -> str:
    """Masked (``detections.redact.mask``) BEFORE it is cut (the v1.349.0
    rule: a cut through a key leaves a piece the mask no longer sees)."""
    from ..detections.redact import mask

    return str(mask(_one_line(text)))[:cap]


def _flagged(text: str) -> bool:
    """Does promptguard read ``text`` as an instruction? Unscannable counts
    as flagged (nothing is stored on a guess)."""
    try:
        from ..core.promptguard import scan_context

        return bool(scan_context(text, source=FINDING_SOURCE, cap=None).blocked)
    except Exception:  # noqa: BLE001
        return True


def _files(raw: Any) -> list[str]:
    """A ``files_json`` value as a list (``team_record.files_of``'s rule)."""
    from types import SimpleNamespace

    from .team_record import files_of

    return files_of(SimpleNamespace(files_json=raw))


def record_findings(engine: Any, *, review_row: str, report: Any) -> int:
    """Store the findings of the review piece ``review_row`` (its
    ``TeamRoleRun`` id) from its report. The CALLER decides whether the
    report may be read at all (``model_team.note_piece``: finished, written
    by the reviewer, not at low trust); here each finding is masked before it
    is cut, a finding promptguard flags is DROPPED, and each is attributed to
    the newest piece of the same scope and reply before the review — not a
    review itself — whose files hold the ONE file that path names among them
    (:func:`resolve_path`; ambiguous = no one). A review already stored is
    not stored twice; its rows start not ``asked`` (:func:`mark_asked`).
    Returns how many were stored. BLOCKING; never raises."""
    if engine is None or not review_row:
        return 0
    found = parse_findings(report)
    if not found:
        return 0
    try:
        from sqlalchemy import text
        from sqlmodel import select

        from ..core.db import session_scope

        with session_scope(engine) as db:
            me = db.exec(text(  # type: ignore[call-overload]
                "SELECT rowid, scope, turn, member, session_id FROM teamrolerun WHERE id = :id"
            ).bindparams(id=review_row)).first()
            if me is None:
                return 0
            mine, scope, turn = int(me[0]), str(me[1] or ""), str(me[2] or "")
            reviewer, review_session = str(me[3] or ""), str(me[4] or "")
            if db.exec(select(TeamFinding.id).where(TeamFinding.review_row == review_row)).first():
                return 0
            pieces = db.exec(text(  # type: ignore[call-overload]
                "SELECT member, session_id, files_json FROM teamrolerun WHERE scope = :scope "
                "AND COALESCE(turn, '') = :turn AND role != 'review' AND rowid < :mine "
                "ORDER BY rowid DESC LIMIT :n"
            ).bindparams(scope=scope, turn=turn, mine=mine, n=_ATTRIBUTION_ROWS)).all()
            written = [(str(m or ""), str(s or ""), _files(f)) for m, s, f in pieces]
            stored = 0
            dropped = 0
            # v1.423.0 review: a path names ONE file the reviewed change
            # holds, or no one (:func:`resolve_path` over every file the
            # pieces before the review wrote — an exact name wins, a suffix
            # counts only when no other file shares it).
            reviewed = [w for _name, _sid, files in written for w in files]
            for f in found:
                path = _masked(f.path, MAX_PATH_CHARS)
                what = _masked(f.what, MAX_WHAT_CHARS)
                change = _masked(f.change, MAX_CHANGE_CHARS)
                # v1.423.0 review: the WHOLE line as parsed is scanned — a
                # line is read no longer than it is scanned
                # (``FINDING_LINE_CHARS``): the builder's cut parsed every
                # character but scanned only the head, and stored an
                # instruction from a long line's tail as its ``change``.
                if _flagged(_masked(f.text, FINDING_LINE_CHARS)):
                    dropped += 1
                    continue
                member = worker = ""
                target = resolve_path(f.path, reviewed) if f.path else ""
                if target:
                    for name, sid, files in written:  # newest first
                        if _holds(files, target):
                            member, worker = name, sid
                            break
                db.add(TeamFinding(
                    scope=scope, turn=turn, review_row=str(review_row), review_session=review_session,
                    reviewer=reviewer[:MAX_NAME_CHARS], number=int(f.number),
                    path=path, line=f.line, what=what, change=change,
                    member=member[:MAX_NAME_CHARS], worker_session=worker,
                ))
                stored += 1
            db.commit()
        if dropped:
            log.info("team findings: %d finding(s) of a review read like an instruction and were not kept",
                     dropped)
        return stored
    except Exception:  # noqa: BLE001 — a record never breaks a delegation
        log.warning("team findings could not be stored", exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# settling the lead's answers
# --------------------------------------------------------------------------- #


def settle(
    engine: Any,
    *,
    review_session: str,
    answers: "dict[int, tuple[str, str]]",
    own_writes: "Callable[[], Iterable[str]] | Iterable[str] | None" = None,
    own_session: str = "",
) -> int:
    """Apply the lead's ``answers`` (:func:`parse_answers`) to the still-OPEN
    findings of the review run ``review_session`` (its numbers are the
    review's own: a second review's R1 is another finding). ``did not
    apply`` → :data:`DID_NOT_APPLY` with the lead's why; ``fixed`` →
    :data:`FIXED` (CONFIRMED) when a LATER piece of the same scope and reply
    (a row after the review's, not a review) wrote that path — ``fixed_by``
    its session — or, for a finding NO member made, when ``own_writes`` (the
    session's journaled writes after the review, a callable read only when
    needed) holds it — ``fixed_by`` = ``own_session``; else
    :data:`SAID_FIXED`. "That path" is the ONE file it names among every file
    the reply's pieces (and, for the own-writes check, the session) wrote
    (:func:`resolve_path`): a write of another file of the same name, or an
    ambiguous name, confirms nothing. A finding with no answer stays open. Returns how
    many it settled. BLOCKING; never raises."""
    if engine is None or not review_session or not answers:
        return 0
    try:
        from sqlalchemy import text
        from sqlmodel import select

        from ..core.db import session_scope

        own: "list[str] | None" = None

        def _own() -> list[str]:
            nonlocal own
            if own is None:
                try:
                    got = own_writes() if callable(own_writes) else (own_writes or [])
                    own = [str(p) for p in got if str(p or "")]
                except Exception:  # noqa: BLE001 — unreadable: nothing confirmed by it
                    own = []
            return own

        n = 0
        now = utcnow()
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(TeamFinding)
                .where(TeamFinding.review_session == review_session)
                .where(TeamFinding.status == OPEN)
            ))
            # Per review row: the pieces of its reply AFTER it (each
            # ``(session, files)``) and every file the reply's pieces wrote,
            # before or after it — the candidates a finding's path must name
            # unambiguously (v1.423.0 review: :func:`resolve_path`).
            later: dict[str, list[tuple[str, list[str]]]] = {}
            reply_files: dict[str, list[str]] = {}
            for row in rows:
                said = answers.get(int(row.number or 0))
                if said is None:
                    continue
                status, why = said
                row.answer = _masked(why, MAX_ANSWER_CHARS)
                row.settled_at = now
                if status == ANSWER_DOES_NOT_APPLY:
                    row.status = DID_NOT_APPLY
                else:
                    row.status = SAID_FIXED
                    if row.path:
                        if row.review_row not in later:
                            me = db.exec(text(  # type: ignore[call-overload]
                                "SELECT rowid FROM teamrolerun WHERE id = :id"
                            ).bindparams(id=row.review_row)).first() if row.review_row else None
                            if me is None:
                                later[row.review_row] = []
                                reply_files[row.review_row] = []
                            else:
                                got = db.exec(text(  # type: ignore[call-overload]
                                    "SELECT session_id, files_json FROM teamrolerun WHERE scope = :scope "
                                    "AND COALESCE(turn, '') = :turn AND role != 'review' AND rowid > :mine "
                                    "ORDER BY rowid LIMIT :n"
                                ).bindparams(scope=row.scope, turn=row.turn or "", mine=int(me[0]),
                                             n=_ATTRIBUTION_ROWS)).all()
                                before = db.exec(text(  # type: ignore[call-overload]
                                    "SELECT files_json FROM teamrolerun WHERE scope = :scope "
                                    "AND COALESCE(turn, '') = :turn AND role != 'review' AND rowid < :mine "
                                    "ORDER BY rowid DESC LIMIT :n"
                                ).bindparams(scope=row.scope, turn=row.turn or "", mine=int(me[0]),
                                             n=_ATTRIBUTION_ROWS)).all()
                                later[row.review_row] = [(str(s or ""), _files(f)) for s, f in got]
                                reply_files[row.review_row] = [
                                    *(w for (f,) in before for w in _files(f)),
                                    *(w for _s, files in later[row.review_row] for w in files),
                                ]
                        target = resolve_path(row.path, reply_files[row.review_row])
                        for sid, files in later[row.review_row]:
                            if _holds(files, target):
                                row.status, row.fixed_by = FIXED, sid
                                break
                        if row.status == SAID_FIXED and not row.member and own_writes is not None:
                            mine_too = _own()
                            target = resolve_path(row.path, [*reply_files[row.review_row], *mine_too])
                            if _holds(mine_too, target):
                                row.status, row.fixed_by = FIXED, str(own_session or "")
                db.add(row)
                n += 1
            db.commit()
        return n
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        log.warning("team findings could not be settled", exc_info=True)
        return 0


def mark_asked(engine: Any, *, review_session: str) -> int:
    """v1.423.0 review: the finish check's note SHOWED the review run
    ``review_session``'s report and asked the lead to answer each finding —
    its findings become ``asked`` (the only ones the member card counts).
    Returns how many rows it marked. BLOCKING; never raises."""
    if engine is None or not review_session:
        return 0
    try:
        from sqlalchemy import update

        from ..core.db import session_scope

        with session_scope(engine) as db:
            got = db.exec(  # type: ignore[call-overload]
                update(TeamFinding)
                .where(TeamFinding.review_session == review_session)  # type: ignore[arg-type]
                .where(TeamFinding.asked == False)  # noqa: E712 — SQL, not Python
                .values(asked=True)
            )
            db.commit()
            return int(getattr(got, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 — a record never breaks a reply
        log.warning("team findings could not be marked asked", exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# the member card's numbers
# --------------------------------------------------------------------------- #


def zeros() -> dict[str, Any]:
    """The numbers with nothing recorded."""
    return {"confirmed": 0, "did_not_apply": 0, "said_fixed": 0, "open": 0,
            "first_pass": {"approved": 0, "of": 0}}


def _naive_utc(at: datetime) -> datetime:
    """A stamp as the table stores it (SQLite keeps naive UTC wall time)."""
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def window(*, days: int = WINDOW_DAYS, now: "datetime | None" = None,
           since: "datetime | None" = None, until: "datetime | None" = None) -> tuple[datetime, datetime]:
    """The ``(since, until)`` pair the numbers are read over (naive UTC):
    ``until`` (else ``now``, else the clock) and ``since`` (else ``days``
    before ``until``)."""
    end = until or now or utcnow()
    start = since or (end - timedelta(days=days))
    return _naive_utc(start), _naive_utc(end)


def findings_for(engine: Any, members: Iterable[str], *, days: int = WINDOW_DAYS,
                 now: "datetime | None" = None, since: "datetime | None" = None,
                 until: "datetime | None" = None) -> dict[str, dict[str, Any]]:
    """``{member: numbers}`` for ``members`` over one window (two queries):
    how many of the findings attributed to the member are ``confirmed``,
    ``did_not_apply``, ``said_fixed`` and ``open``, and its FIRST-PASS
    APPROVAL — over the member's ``code`` / ``test`` pieces in the window that
    a review judged, grouped by (scope, reply): the verdict on its EARLIEST
    judged piece of that reply (``of`` = replies, ``approved`` = those that
    were good). Every member asked for is in the answer (zeros when it has
    nothing, or when nothing could be read). BLOCKING; never raises."""
    names = [str(m) for m in members if str(m or "").strip()]
    out = {name: zeros() for name in names}
    if engine is None or not names:
        return out
    start, end = window(days=days, now=now, since=since, until=until)
    try:
        from sqlalchemy import func, literal_column
        from sqlmodel import select

        from ..core.db import session_scope
        from .team_record import REVIEW_GOOD, TeamRoleRun

        with session_scope(engine) as db:
            counted = db.exec(
                select(TeamFinding.member, TeamFinding.status, func.count())
                .where(TeamFinding.member.in_(names))  # type: ignore[attr-defined]
                # v1.423.0 review: only findings the lead was ASKED to answer.
                .where(TeamFinding.asked == True)  # noqa: E712 — SQL, not Python
                .where(TeamFinding.created_at >= start)
                .where(TeamFinding.created_at <= end)
                .group_by(TeamFinding.member, TeamFinding.status)
            ).all()
            judged = db.exec(
                select(TeamRoleRun.member, TeamRoleRun.scope, TeamRoleRun.turn, TeamRoleRun.review)
                .where(TeamRoleRun.member.in_(names))  # type: ignore[attr-defined]
                .where(TeamRoleRun.role.in_(("code", "test")))  # type: ignore[attr-defined]
                .where(TeamRoleRun.review != "")
                .where(TeamRoleRun.created_at >= start)
                .where(TeamRoleRun.created_at <= end)
                .order_by(literal_column("teamrolerun.rowid"))  # type: ignore[arg-type]
            ).all()
    except Exception:  # noqa: BLE001
        log.debug("team findings unreadable", exc_info=True)
        return out
    key = {FIXED: "confirmed", DID_NOT_APPLY: "did_not_apply", SAID_FIXED: "said_fixed", OPEN: "open"}
    for member, status, count in counted:
        slot = key.get(str(status or ""))
        if slot and member in out:
            out[member][slot] += int(count or 0)
    first: dict[tuple[str, str, str], str] = {}
    for member, scope, turn, review in judged:
        first.setdefault((str(member), str(scope or ""), str(turn or "")), str(review or ""))
    for (member, _scope, _turn), review in first.items():
        if member in out:
            out[member]["first_pass"]["of"] += 1
            if review == REVIEW_GOOD:
                out[member]["first_pass"]["approved"] += 1
    return out


def confirmed_findings(engine: Any, member: str, *, since: "datetime | None" = None,
                       limit: int = 200) -> list[TeamFinding]:
    """v1.423.0 (Team notes, Phase 2): the member's CONFIRMED findings —
    status :data:`FIXED` (the lead said fixed AND a later piece changed that
    file) — that the lead was ASKED to answer (``asked``: the one rule every
    reader of these rows keeps, :func:`findings_for`), newest first, at most
    ``limit``, created at or after ``since`` when given. Detached rows (what
    ``agents/team_notes`` distills working habits from). BLOCKING; never
    raises (``[]``)."""
    name = str(member or "")
    if engine is None or not name.strip():
        return []
    try:
        from sqlalchemy import literal_column
        from sqlmodel import select

        from ..core.db import session_scope

        stmt = (
            select(TeamFinding)
            .where(TeamFinding.member == name)
            .where(TeamFinding.asked == True)  # noqa: E712 — SQL, not Python
            .where(TeamFinding.status == FIXED)
        )
        if since is not None:
            stmt = stmt.where(TeamFinding.created_at >= _naive_utc(since))
        stmt = stmt.order_by(literal_column("teamfinding.rowid").desc()).limit(  # type: ignore[arg-type]
            max(1, int(limit)))
        with session_scope(engine) as db:
            rows = list(db.exec(stmt))
            for row in rows:
                db.expunge(row)
        return rows
    except Exception:  # noqa: BLE001
        log.debug("confirmed team findings unreadable", exc_info=True)
        return []


def member_findings(engine: Any, member: str, *, days: int = WINDOW_DAYS,
                    now: "datetime | None" = None, since: "datetime | None" = None,
                    until: "datetime | None" = None) -> dict[str, Any]:
    """One member's numbers (:func:`findings_for`): ``{"confirmed",
    "did_not_apply", "said_fixed", "open", "first_pass": {"approved",
    "of"}}``. BLOCKING; never raises (zeros when unreadable)."""
    name = str(member or "")
    if not name.strip():
        return zeros()
    return findings_for(engine, [name], days=days, now=now, since=since, until=until).get(name) or zeros()


__all__ = [
    "ANSWER_DOES_NOT_APPLY", "ANSWER_FIXED", "DID_NOT_APPLY", "FINDING_SOURCE", "FIXED", "Finding",
    "MAX_ANSWER_CHARS", "MAX_CHANGE_CHARS", "MAX_PATH_CHARS", "MAX_WHAT_CHARS", "OPEN", "SAID_FIXED",
    "ANSWER_LINE_CHARS", "FINDING_LINE_CHARS",
    "STATUSES", "TeamFinding", "WINDOW_DAYS", "answer_lines", "confirmed_findings", "findings_for",
    "mark_asked",
    "member_findings", "parse_answers", "parse_findings", "record_findings", "resolve_path", "same_path",
    "settle", "window", "zeros",
]
