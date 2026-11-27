"""THE THIRD TIME (next leaps 19-a, build-out plan R3.23, idea 19): the same
kind of task asked for in chat on three different days becomes ONE skill
draft, written in the night review and waiting in Improvements.

Three steps, each its own function:

* :func:`units` (BLOCKING, bounded) turns the owner's chats into UNITS of
  work: each task of ``eval.autonomy.split_conversation`` that ended well,
  with the owner's own request words, the tools Iron Jarvis used for it in
  order (from the LEDGER: the chat's run rows found by ``thread_id``, and the
  ``"chat"`` tool rows inside their windows, each row given to the ONE turn
  whose window holds it, ``detections/ledger``'s rule), the kinds of files
  read and written (extensions only, never names), the model that answered
  (the newest run row) and whether the chat was Local only. At most
  :data:`THREADS_MAX` chats touched in the last :data:`WINDOW_DAYS` days,
  newest first; the run and tool rows are read once, bounded.
* :func:`groups` (PURE) groups units: two match when their request words
  meet the templates' rule (``templates.similarity`` >= ``SAME_TASK``) AND
  they used at least two of the same tools in the same order, or read and
  wrote the same kinds of files. A group needs :data:`MIN_UNITS` units on
  :data:`MIN_DAYS` different days within :data:`WINDOW_DAYS` days (D-19.1).
  Left out before grouping: a task whose reply ran at LOW trust (it read a
  web page or a message from outside: what it read is data, never a skill),
  a chat another computer or a messaging channel writes (``owner ==
  "daemon"``), and a task whose client owner cannot be worked out. Units are
  grouped only with units of the SAME owner (no client, or one client): a
  draft never mixes two clients' work.
* :func:`detect` (BLOCKING) leaves out a group already covered (a skill that
  does it, ``SkillLearningEngine._matches_existing_skill``; a saved template;
  a schedule with the same words), one whose tasks an earlier candidate
  already holds, and one like a chat draft the owner rejected; every other
  group becomes ONE ``SkillCandidateRecord`` with ``source="chat"`` and its
  evidence. No model. The night review then writes the draft
  (``improve/night_review``, the ``repeat`` row) on the newest unit's model,
  through ``improve/budget.may_spend``, inside the Local only scope when any
  of the chats was Local only; ``skills/learning`` re-reads the chats, sends
  them masked, scanned and fenced, and HOLDS a draft that names a client.

AGENT RUNS are not units in 19-a: one run is still enough for the run lane
(``skills/learning``), and the three-times bar reaches agent runs in the
intelligence plan's release 10. A run a chat handed its task to is part of
that chat's task here (its reply is the chat's).

Nothing here raises into the night review: :func:`detect` answers 0 when a
store cannot be read (fail closed: no candidate on a guess).
"""

from __future__ import annotations

import json
import logging
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

#: D-19.1: three times, on three different days, within five weeks.
MIN_UNITS = 3
MIN_DAYS = 3
WINDOW_DAYS = 35
#: Chats read at most (the newest touched in the window).
THREADS_MAX = 400
#: Chat run rows and chat tool rows read at most (the newest in the window).
RUNS_MAX = 20_000
TOOL_ROWS_MAX = 20_000
#: Two lists of tools match when they share this many, in the same order.
SHARED_TOOLS = 2
#: Candidates one :func:`detect` call adds at most.
MINT_MAX = 5
#: A tool row is written a moment before its turn's run row (the ledger's
#: own slack, ``detections/ledger._TURN_SLACK``).
SLACK_S = 2.0
#: The longest file extension counted as a kind.
_EXT_MAX = 8

CHAT = "chat"
#: The owner key of a unit whose client owner could not be worked out.
UNKNOWN_OWNER = "?"


@dataclass(frozen=True)
class Unit:
    """One task the owner asked for and Iron Jarvis finished well."""

    source: str                 # "chat"
    ref: str                    # the chat's id
    start: float                # epoch seconds (the request line's ``at``)
    end: float = math.inf       # the next task's start, or open
    words: str = ""             # the owner's request, as sent
    tools: tuple[str, ...] = ()  # the tools used, in order of first use
    kinds_in: frozenset[str] = frozenset()
    kinds_out: frozenset[str] = frozenset()
    provider: str = ""
    model: str = ""
    #: The Iron-Proxy account the task ran on ("" = none chosen: the chat ran
    #: on Iron-Proxy's own pick, unpinned). See :func:`_account_of`.
    account: str = ""
    local_only: bool = False
    low_trust: bool = False
    remote: bool = False
    #: "" = no client's work; "client:<id>[,…]" = whose; "?" = unknown.
    owner: str = ""


@dataclass
class Group:
    """Units that are the same kind of task, newest first."""

    units: list[Unit]
    tools: list[str] = field(default_factory=list)   # used by every unit, newest's order
    kinds_in: list[str] = field(default_factory=list)
    kinds_out: list[str] = field(default_factory=list)
    days: list[str] = field(default_factory=list)    # ISO local days, oldest first

    @property
    def newest(self) -> Unit:
        return self.units[0]


# --------------------------------------------------------------------------- #
# pure pieces
# --------------------------------------------------------------------------- #
def _ordered_shared(a: Sequence[str], b: Sequence[str]) -> int:
    """How many tools two lists share in the same order (longest common
    subsequence). Pure."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def matches(a: Unit, b: Unit) -> bool:
    """Are ``a`` and ``b`` the same kind of task? The words rule AND (two
    shared tools in order, or the same file kinds in and out, not none)."""
    from ..templates import SAME_TASK, similarity, task_tokens

    if similarity(task_tokens(a.words), task_tokens(b.words)) < SAME_TASK:
        return False
    if _ordered_shared(a.tools, b.tools) >= SHARED_TOOLS:
        return True
    same_kinds = a.kinds_in == b.kinds_in and a.kinds_out == b.kinds_out
    return same_kinds and bool(a.kinds_in or a.kinds_out)


def day_of(start: float, tz: "tzinfo | None" = None) -> date:
    """The day a unit was asked for, in ``tz`` (None = this PC's own)."""
    moment = datetime.fromtimestamp(start, timezone.utc)
    return moment.astimezone(tz).date() if tz is not None else moment.astimezone().date()


def eligible(u: Unit) -> bool:
    """May this unit be grouped at all? Not a low-trust reply, not a chat
    another computer or channel writes, an owner that is known, and a
    request with enough words for the rule."""
    from ..templates import MIN_TOKENS, task_tokens

    if u.low_trust or u.remote or u.owner == UNKNOWN_OWNER:
        return False
    return len(task_tokens(u.words)) >= MIN_TOKENS


def groups(units: Iterable[Unit], *, tz: "tzinfo | None" = None) -> list[Group]:
    """The repeats among ``units`` (see the module docstring). PURE: reads
    only its input. Each unit joins at most one group; a group is built
    around its newest unit, so the draft is written on the newest model."""
    pool = sorted((u for u in units if eligible(u)), key=lambda u: (-u.start, u.ref))
    taken: set[int] = set()
    out: list[Group] = []
    for i, seed in enumerate(pool):
        if i in taken:
            continue
        members = [(i, seed)]
        for j in range(i + 1, len(pool)):
            other = pool[j]
            if j in taken or other.owner != seed.owner:
                continue
            if seed.start - other.start > WINDOW_DAYS * 86400:
                break  # oldest last: none further back can be in the window
            if matches(seed, other):
                members.append((j, other))
        days = {day_of(u.start, tz) for _, u in members}
        if len(members) < MIN_UNITS or len(days) < MIN_DAYS:
            continue
        taken.update(k for k, _ in members)
        us = [u for _, u in members]
        shared = [t for t in seed.tools if all(t in u.tools for u in us)]
        out.append(Group(
            units=us, tools=shared,
            kinds_in=sorted(seed.kinds_in) if all(u.kinds_in == seed.kinds_in for u in us) else [],
            kinds_out=sorted(seed.kinds_out) if all(u.kinds_out == seed.kinds_out for u in us) else [],
            days=sorted(d.isoformat() for d in days),
        ))
    return out


def _iso(epoch: float) -> "str | None":
    if not math.isfinite(epoch):
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None).isoformat()


def _masked(text: str) -> str:
    """The owner's words as kept: secret-shaped values masked
    (``detections/redact.mask``), the way every record a model may read is."""
    from ..detections.redact import mask

    return str(mask(str(text or "")) or "")


def evidence_of(group: Group) -> dict[str, Any]:
    """What a chat candidate keeps (``evidence_json``): enough to find each
    task again, never a file's name. Pure."""
    return {
        "units": [
            {"thread": u.ref, "start": u.start, "end": u.end if math.isfinite(u.end) else None,
             "at": _iso(u.start), "words": _masked(u.words)[:2000], "provider": u.provider, "model": u.model,
             "account": u.account, "local_only": bool(u.local_only)}
            for u in group.units
        ],
        "tools": list(group.tools),
        "kinds_in": list(group.kinds_in),
        "kinds_out": list(group.kinds_out),
        "days": list(group.days),
    }


def load_evidence(text: Any) -> dict[str, Any]:
    """A candidate's or a draft's evidence, or {} when it holds none."""
    try:
        got = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def days_words(days: Iterable[str]) -> str:
    """"3, 10 and 17 November"; "28 October, 4 and 11 November". Each day
    once, oldest first. "" when none can be read. Pure."""
    seen: list[date] = []
    for d in days or ():
        try:
            day = date.fromisoformat(str(d))
        except ValueError:
            continue
        if day not in seen:
            seen.append(day)
    seen.sort()
    if not seen:
        return ""
    runs: list[list[date]] = []
    for day in seen:
        if runs and (runs[-1][0].year, runs[-1][0].month) == (day.year, day.month):
            runs[-1].append(day)
        else:
            runs.append([day])
    pieces = [f"{_and([str(d.day) for d in run])} {_MONTHS[run[0].month - 1]}" for run in runs]
    if len(pieces) == 1:
        return pieces[0]
    return ", ".join(pieces[:-1]) + (", " if " and " in pieces[-1] else " and ") + pieces[-1]


def times_line(evidence: dict[str, Any]) -> str:
    """"You asked for this 3 times, on 3, 10 and 17 November." Counted from
    the evidence's own tasks: every one is a request the owner sent. ""
    when the evidence holds none."""
    units = evidence.get("units") if isinstance(evidence, dict) else None
    n = len(units) if isinstance(units, list) else 0
    if n <= 0:
        return ""
    when = days_words(evidence.get("days") or [])
    return f"You asked for this {n} times, on {when}." if when else f"You asked for this {n} times."


def tools_line(evidence: dict[str, Any]) -> str:
    """"Each time Iron Jarvis used: read file, write document." The tools
    the ledger shows in EVERY task of the group, by their own names. ""
    when there are none."""
    tools = evidence.get("tools") if isinstance(evidence, dict) else None
    if not isinstance(tools, list) or not tools:
        return ""
    try:
        from ..agents.mission import tool_own_name
    except Exception:  # noqa: BLE001 — wording only
        def tool_own_name(t: str) -> str:  # type: ignore[misc]
            return " ".join(str(t).replace("_", " ").split())
    names = list(dict.fromkeys(tool_own_name(str(t)) for t in tools if str(t or "").strip()))
    return f"Each time Iron Jarvis used: {', '.join(names)}." if names else ""


# --------------------------------------------------------------------------- #
# reading the chats (BLOCKING)
# --------------------------------------------------------------------------- #
def _naive(at: datetime) -> datetime:
    return at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo is not None else at


def _epoch_of(at: datetime) -> float:
    return (at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)).timestamp()


def _kind(path: Any) -> str:
    name = str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1].lower()
    return ext if ext.isalnum() and len(ext) <= _EXT_MAX else ""


def _line_local(m: Any) -> bool:
    if not isinstance(m, dict):
        return False
    if m.get("localOnly") is True:
        return True
    route = m.get("route")
    if isinstance(route, dict) and route.get("local_only") is True:
        return True
    return str(m.get("errorKind") or "") == "local_only"


def _account_of(lines: list[dict], setup_json: str) -> str:
    """The account a task ran on, as the chat recorded it: the newest reply
    that carries a route says it (``route.account.id``; a route with no
    account = the turn ran unpinned, ""). A reply saved before routes were
    kept falls back to the account chosen for the chat (``setup.account``,
    the one the chat lane would pin). Never a guess: "" means unpinned."""
    for m in reversed(lines):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        route = m.get("route")
        if isinstance(route, dict):
            acct = route.get("account")
            got = acct.get("id") if isinstance(acct, dict) else acct
            return " ".join(str(got or "").split())
    try:
        setup = json.loads(setup_json or "{}")
    except (TypeError, ValueError):
        return ""
    got = setup.get("account") if isinstance(setup, dict) else ""
    return " ".join(str(got or "").split()) if isinstance(got, str) else ""


def _line_low(m: Any) -> bool:
    return isinstance(m, dict) and str(m.get("trust") or "").strip().lower() == "low"


@dataclass
class _Ledger:
    """The chat run rows and chat tool rows of the window, read once."""

    #: thread id -> [(created_at epoch, finished_at epoch, provider, model, run id)], oldest first
    runs: dict[str, list[tuple[float, float, str, str, str]]] = field(default_factory=dict)
    #: run id -> [ToolInvocation-like rows], oldest first
    tools: dict[str, list[Any]] = field(default_factory=dict)


def _read_ledger(engine: Any, since: datetime, thread_ids: list[str]) -> _Ledger:
    """BLOCKING; RAISES when the tables cannot be read. Every chat turn's run
    row (all chats, so a tool row is given to the right turn when two chats
    overlap), bounded; the ``"chat"`` tool rows of the window, bounded."""
    from sqlalchemy import literal_column
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import AgentRun, ToolInvocation

    wanted = set(thread_ids)
    lo = _naive(since)
    with session_scope(engine) as db:
        run_rows = list(db.exec(
            select(AgentRun.id, AgentRun.created_at, AgentRun.finished_at, AgentRun.thread_id,
                   AgentRun.provider, AgentRun.model)
            .where(AgentRun.session_id == CHAT, AgentRun.created_at >= lo - timedelta(seconds=SLACK_S))
            .order_by(AgentRun.created_at.desc()).limit(RUNS_MAX)  # type: ignore[attr-defined]
        ))
        tool_rows = list(db.exec(
            select(ToolInvocation)
            .where(ToolInvocation.session_id == CHAT, ToolInvocation.created_at >= lo)
            .order_by(ToolInvocation.created_at.desc(), literal_column("toolinvocation.rowid").desc())
            .limit(TOOL_ROWS_MAX)
        ))
        for r in tool_rows:
            db.expunge(r)
    tool_rows.reverse()
    turns = sorted(
        ((rid, created, finished) for rid, created, finished, *_ in run_rows
         if created is not None and finished is not None),
        key=lambda t: t[1],
    )
    starts = [created - timedelta(seconds=SLACK_S) for _, created, _ in turns]
    ends = [finished + timedelta(seconds=SLACK_S) for _, _, finished in turns]
    ids = [rid for rid, _, _ in turns]
    led = _Ledger()
    for rid, created, finished, thread, provider, model in run_rows:
        if created is None or str(thread or "") not in wanted:
            continue
        led.runs.setdefault(str(thread), []).append((
            _epoch_of(created), _epoch_of(finished) if finished is not None else math.inf,
            str(provider or ""), str(model or ""), str(rid),
        ))
    for rows in led.runs.values():
        rows.sort(key=lambda t: t[0])
    for row in tool_rows:
        if getattr(row, "rehearsed", False) or row.created_at is None:
            continue  # a rehearsal's row did nothing
        k = bisect_right(starts, row.created_at) - 1
        for i in range(k, max(-1, k - 16), -1):  # the latest-starting turn that holds it
            if ends[i] >= row.created_at:
                led.tools.setdefault(ids[i], []).append(row)
                break
    return led


def _tools_of(rows: list[Any]) -> tuple[tuple[str, ...], set[str], set[str], list[str]]:
    """(tools in order of first use, kinds read, kinds written, paths) of a
    task's ledger rows. Only calls that ran and were not refused count."""
    from ..detections.ledger import to_events

    tools: list[str] = []
    kinds_in: set[str] = set()
    kinds_out: set[str] = set()
    paths: list[str] = []
    for row in rows:
        if not bool(getattr(row, "ok", False)):
            continue
        name = str(getattr(row, "tool", "") or "")
        if name and name not in tools:
            tools.append(name)
        try:
            events = to_events(row)
        except Exception:  # noqa: BLE001 — a row that cannot be read adds no kind
            continue
        for ev in events:
            path = ev.get("path")
            kind = _kind(path)
            if not kind:
                continue
            if ev.get("action") == "file.read":
                kinds_in.add(kind)
            elif ev.get("action") == "file.write":
                kinds_out.add(kind)
            else:
                continue
            paths.append(str(path))
    return tuple(tools), kinds_in, kinds_out, paths


def _timed(messages: list, fallback: "float | None") -> tuple[list[float], list[dict]]:
    """``(times, lines)`` of a chat, ONE pass, by the same ``at`` reading
    ``split_conversation`` uses (a line with no ``at`` is where the last one
    was; a line with no time at all is left out). Times never go back."""
    from ..eval.autonomy import _epoch

    times: list[float] = []
    lines: list[dict] = []
    last = fallback
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        at = _epoch(raw.get("at"))
        if at is None:
            at = last
        else:
            last = at
        if at is None:
            continue
        times.append(max(at, times[-1]) if times else at)
        lines.append(raw)
    return times, lines


def _task_lines(timed: tuple[list[float], list[dict]], start: float, end: float) -> list[dict]:
    """The lines that belong to the task [start, end) (bisect, no scan)."""
    from bisect import bisect_left

    times, lines = timed
    return lines[bisect_left(times, start):bisect_left(times, end)]


def chat_units(messages: list, *, ref: str, fallback_at: "float | None", since: float,
               setup_json: str = "", remote: bool = False, ledger: "_Ledger | None" = None,
               owner: str = "") -> list[tuple[Unit, list[str], list[dict]]]:
    """``(unit, file paths, the task's lines)`` for every task of one chat
    that ended well and began at or after ``since``. BLOCKING only through
    the already-read ``ledger`` (pure otherwise)."""
    from ..eval.autonomy import split_conversation
    from ..providers.egress import setup_says_local_only

    chat_local = bool(setup_says_local_only(setup_json))
    out: list[tuple[Unit, list[str], list[dict]]] = []
    timed: "tuple[list[float], list[dict]] | None" = None
    for task in split_conversation(messages, surface="chat", key=ref, fallback_at=fallback_at):
        if not task.success or not task.whole or task.start < since or not task.request.strip():
            continue
        if timed is None:
            timed = _timed(messages, fallback_at)
        lines = _task_lines(timed, task.start, task.end)
        runs = [r for r in (ledger.runs.get(ref, []) if ledger is not None else [])
                if task.start - SLACK_S <= r[0] < task.end - SLACK_S]
        rows: list[Any] = []
        for r in runs:
            rows += ledger.tools.get(r[4], []) if ledger is not None else []
        tools, kinds_in, kinds_out, paths = _tools_of(rows)
        for line in lines:
            if line.get("role") == "user" and not line.get("steer"):
                for name in line.get("attachmentNames") or []:
                    kind = _kind(name)
                    if kind:
                        kinds_in.add(kind)
                break  # the request line only
        newest = runs[-1] if runs else None
        out.append((Unit(
            source=CHAT, ref=ref, start=float(task.start), end=float(task.end),
            words=" ".join(task.request.split()), tools=tools,
            kinds_in=frozenset(kinds_in), kinds_out=frozenset(kinds_out),
            provider=newest[2] if newest else "", model=newest[3] if newest else "",
            account=_account_of(lines, setup_json),
            local_only=chat_local or any(_line_local(m) for m in lines),
            low_trust=any(_line_low(m) for m in lines), remote=remote, owner=owner,
        ), paths, lines))
    return out


def _owner_key(cmap: Any, folder: str, words: str, paths: list[str]) -> str:
    from ..workmap.clients.wall import owners_of

    got = owners_of(cmap, folder=folder, title=words, files=paths, waiting=True)
    if got is None:
        return UNKNOWN_OWNER
    return ",".join(sorted(got))


def units(engine: Any, since: datetime, *, cmap: Any = None) -> list[Unit]:
    """The owner's chat tasks since ``since`` as units (module docstring).
    BLOCKING; RAISES when the chats or the ledger cannot be read. ``cmap``
    the client map (``workmap/clients/store.load_map``); None = no client
    is known (tests of the rule alone)."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ChatThreadRecord as T, chat_surface_only

    lo = _naive(since)
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(T.id, T.messages_json, T.setup_json, T.owner, T.created_at)
            .where(T.updated_at >= lo, chat_surface_only())
            .order_by(T.updated_at.desc()).limit(THREADS_MAX)  # type: ignore[attr-defined]
        ))
    ids = [str(r[0]) for r in rows]
    ledger = _read_ledger(engine, since, ids)
    folders: dict[str, str] = {}
    if cmap is not None:
        from ..workmap.clients.wall import thread_owners

        got = thread_owners(engine, cmap, ids)
        if got is None:
            raise RuntimeError("the chats' folders could not be read")
        folders = got
    floor = _epoch_of(since)
    out: list[Unit] = []
    for tid, messages_json, setup_json, owner, created in rows:
        try:
            messages = json.loads(messages_json or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(messages, list):
            continue
        remote = str(owner or "user") == "daemon"
        fallback = _epoch_of(created) if isinstance(created, datetime) else None
        for unit, paths, _lines in chat_units(
            messages, ref=str(tid), fallback_at=fallback, since=floor, setup_json=setup_json or "",
            remote=remote, ledger=ledger,
        ):
            if cmap is not None:
                unit = _with_owner(unit, _owner_key(cmap, folders.get(str(tid), ""), unit.words, paths))
            out.append(unit)
    return out


def _with_owner(u: Unit, owner: str) -> Unit:
    from dataclasses import replace

    return replace(u, owner=owner)


def reread(engine: Any, evidence: dict[str, Any]) -> "list[tuple[Unit, list[dict]]] | None":
    """Each task the evidence names, read AGAIN from its chat and the ledger:
    ``(unit, its lines)``; a task whose chat is gone or no longer holds it is
    left out. None when the chats cannot be read. BLOCKING; never raises."""
    try:
        wanted = [u for u in (evidence.get("units") or []) if isinstance(u, dict)]
        tids = list(dict.fromkeys(str(u.get("thread") or "") for u in wanted if u.get("thread")))
        if not tids:
            return []
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord as T

        with session_scope(engine) as db:
            rows = {str(r[0]): r for r in db.exec(
                select(T.id, T.messages_json, T.setup_json, T.owner, T.created_at)
                .where(T.id.in_(tids))  # type: ignore[attr-defined]
            )}
        starts = [float(u.get("start") or 0.0) for u in wanted]
        oldest = min(starts) if starts else 0.0
        ledger = _read_ledger(engine, datetime.fromtimestamp(oldest, timezone.utc) - timedelta(seconds=60),
                              tids)
        out: list[tuple[Unit, list[dict]]] = []
        for want in wanted:
            row = rows.get(str(want.get("thread") or ""))
            if row is None:
                continue
            tid, messages_json, setup_json, owner, created = row
            try:
                messages = json.loads(messages_json or "[]")
            except (TypeError, ValueError):
                continue
            fallback = _epoch_of(created) if isinstance(created, datetime) else None
            start = float(want.get("start") or 0.0)
            for unit, _paths, lines in chat_units(
                messages if isinstance(messages, list) else [], ref=str(tid), fallback_at=fallback,
                since=start, setup_json=setup_json or "", remote=str(owner or "user") == "daemon",
                ledger=ledger,
            ):
                if unit.start == start:
                    out.append((unit, lines))
                    break
        return out
    except Exception:  # noqa: BLE001 — unreadable: the caller holds the draft
        log.debug("repeats: the chats of a draft could not be read again", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# detect: groups become candidates (BLOCKING; no model)
# --------------------------------------------------------------------------- #
def _covered_by_saved(engine: Any, words: str) -> bool:
    """A saved template or a schedule already says this. RAISES when they
    cannot be read."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import SavedPromptRecord
    from ..scheduling.models import ScheduledTaskRecord
    from ..templates import ALREADY_SAVED, similarity, task_tokens

    mine = task_tokens(words)
    with session_scope(engine) as db:
        saved = [str(t or "") for t in db.exec(select(SavedPromptRecord.task))]
        payloads = [str(p or "") for p in db.exec(select(ScheduledTaskRecord.payload_json))]
    for text in saved:
        if similarity(mine, task_tokens(text)) >= ALREADY_SAVED:
            return True
    for raw in payloads:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        task = payload.get("task") if isinstance(payload, dict) else None
        if isinstance(task, str) and similarity(mine, task_tokens(task)) >= ALREADY_SAVED:
            return True
    return False


def _held_before(engine: Any, group: Group) -> bool:
    """Does an earlier chat candidate already hold one of these tasks, or
    did the owner reject a chat draft for the same kind of task? RAISES when
    the tables cannot be read."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..skills.learning_models import SOURCE_CHAT, SkillCandidateRecord as C, SkillProposalRecord as P
    from ..templates import SAME_TASK, similarity, task_tokens

    keys = {(u.ref, u.start) for u in group.units}
    words = [task_tokens(u.words) for u in group.units]
    with session_scope(engine) as db:
        cands = [str(e or "") for e in db.exec(select(C.evidence_json).where(C.source == SOURCE_CHAT))]
        rejected = [str(e or "") for e in db.exec(
            select(P.evidence_json).where(P.source == SOURCE_CHAT, P.status == "rejected")
        )]
    for text in cands:
        for u in load_evidence(text).get("units") or []:
            if isinstance(u, dict) and (str(u.get("thread") or ""), float(u.get("start") or 0.0)) in keys:
                return True
    for text in rejected:
        for u in load_evidence(text).get("units") or []:
            theirs = task_tokens(str((u or {}).get("words") or "")) if isinstance(u, dict) else frozenset()
            if any(similarity(theirs, w) >= SAME_TASK for w in words):
                return True
    return False


def _signature(words: str) -> str:
    import re

    normalized = re.sub(r"[^a-z0-9]+", " ", (words or "").lower()).strip()
    return f"chat::{normalized[:200]}"


def detect(platform: Any, *, now: "datetime | None" = None) -> int:
    """Find the repeats of the last :data:`WINDOW_DAYS` days and add one
    chat candidate per new group. How many were added. BLOCKING; never
    raises (0 when a store cannot be read: no candidate on a guess)."""
    learning = getattr(platform, "skill_learning", None)
    if learning is None or not learning._enabled():
        return 0
    engine = platform.engine
    try:
        from ..core.db import session_scope
        from ..skills.learning_models import SOURCE_CHAT, SkillCandidateRecord
        from ..workmap.clients import store as client_store

        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        since = moment - timedelta(days=WINDOW_DAYS)
        cmap = client_store.load_map(engine)  # RAISES when unreadable: fail closed
        found = groups(units(engine, since, cmap=cmap))
        added = 0
        for group in found:
            if added >= MINT_MAX:
                break
            words = group.newest.words
            if learning._matches_existing_skill(words) or _covered_by_saved(engine, words):
                continue
            if _held_before(engine, group):
                continue
            with session_scope(engine) as db:
                db.add(SkillCandidateRecord(
                    session_id=group.newest.ref, task=_masked(words)[:4000], kind="create",
                    signature=_signature(words), source=SOURCE_CHAT,
                    evidence_json=json.dumps(evidence_of(group)),
                ))
                db.commit()
            added += 1
        return added
    except Exception:  # noqa: BLE001 — the night review carries on without it
        log.warning("repeats: the chats could not be read for repeats", exc_info=True)
        return 0


__all__ = [
    "MIN_DAYS", "MIN_UNITS", "THREADS_MAX", "UNKNOWN_OWNER", "WINDOW_DAYS", "Group", "Unit", "chat_units",
    "day_of", "days_words", "detect", "eligible", "evidence_of", "groups", "load_evidence", "matches",
    "reread", "times_line", "tools_line", "units",
]
