"""A project's WORLD on the Agents page (v1.304.0) — its team, its round table,
what waits on the user and what got done.

The user's request: "a round table for each set of agents … grouped by the
project; when a project is selected you enter the world of that project". This
module is the daemon side of that: plain functions over the stores that
already exist, called by ``routes/projects.py`` (``/projects/{id}/team``,
``/world``, ``/world/room``) and ``routes/agents.py`` (``/agents/worlds``).

Nothing here is a new source of truth:

* the TEAM is a list of roster names on the project row (``Project.team_json``)
  — validated against ``agents.roster.build_roster`` and rendered with the same
  portrait/face fields ``GET /agents/roster`` serves;
* the ROUND TABLE is an ordinary agent thread whose ``project_id`` is set
  (``agents.threads``) — the most recently active one is the world's room;
* WAITING ON YOU is sessions of the project parked on an ask (the
  ``waiting_on`` view every listing uses — read by the ROUTE, because the live
  pause lives in the in-memory approvals registry), assignments that are
  ``blocked`` or held, and pending git reviews of project sessions;
* COMPLETED is finished sessions of the project (+ the NAMES of the files they
  created, from the undo ledger ``agents.outcome.session_result`` reads) and
  finished assignments whose session is not already listed.

Every function here is SYNCHRONOUS and BLOCKING (SQLite reads, a stat per
portrait, the roster's health fold): the routes call them through
``asyncio.to_thread`` — the v1.153.1 rule. In-memory reads (approvals,
pending reviews) are done by the route ON the loop and handed in, because the
registries they read are mutated by the loop and are not thread-safe to walk.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from sqlalchemy import func, literal_column, or_
from sqlmodel import select

from ..core.db import session_scope
from ..core.models import Project, SessionStatus, ToolInvocation, UndoJournal
from ..core.models import Session as SessionModel

#: A team is a handful of seats around one table, not the whole roster.
TEAM_MAX = 24
#: Suggestions look back this far and offer at most this many faces.
SUGGEST_DAYS = 30
SUGGEST_MAX = 6
#: The Completed tab's length and the "done this week" window.
COMPLETED_MAX = 50
DONE_WINDOW_DAYS = 7
#: File names shown per completed item (the total rides beside it).
FILES_PER_ITEM = 20
#: Title clip for a waiting/completed row — the assignment title rule.
TITLE_CHARS = 80

#: A finished run the ledger says NEEDS YOU stays under Waiting this long
#: (the verdict never clears itself; an interrupted run waits until Continue
#: or dismiss clears ``interrupted_at``). At most ATTENTION_MAX per request.
ATTENTION_DAYS = 30
ATTENTION_MAX = 200
#: A failed assignment's error on a Completed row: one sentence, this long.
ERROR_CHARS = 200


def one_sentence(text: Any, limit: int = ERROR_CHARS) -> str:
    """The first line's first sentence, collapsed, at most ``limit`` chars
    (an ellipsis says it was cut). ``""`` stays ``""``."""
    line = next((ln for ln in str(text or "").splitlines() if ln.strip()), "")
    line = " ".join(line.split())
    for stop in (". ", "! ", "? "):
        cut = line.find(stop)
        if cut != -1:
            line = line[: cut + 1]
            break
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _attention_clause():
    """SQL: a project session that WAITS on the user although its run ended —
    interrupted by a restart, or the ledger's ``needs_you`` verdict within
    :data:`ATTENTION_DAYS`. ONE definition for Waiting, ``done_7d`` and
    Completed, so a run is never counted done AND waiting."""
    from sqlalchemy import and_

    since = _now() - timedelta(days=ATTENTION_DAYS)
    # COALESCE on purpose: the clause is also used NEGATED, and SQL's
    # three-valued logic makes NOT(outcome = 'needs_you') NULL — i.e. it
    # would drop every row whose outcome is NULL (most of them).
    return or_(
        SessionModel.interrupted_at.is_not(None),  # type: ignore[union-attr]
        and_(
            func.coalesce(SessionModel.outcome, "") == "needs_you",
            func.coalesce(SessionModel.finished_at, SessionModel.created_at) >= since,
        ),
    )


#: Serialises "is there a room yet? no → make one" so two first opens of the
#: same world (two windows, a double click) cannot mint two round tables.
_ROOM_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _naive(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; compare everything in naive UTC."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _title(text: Any) -> str:
    """The first non-empty line, collapsed and clipped (``derive_title``)."""
    from ..assignments.models import derive_title

    return derive_title(str(text or "")) or "(untitled)"


def _bare(name: str) -> str:
    return name.split(":", 1)[1] if ":" in name else name


def kind_of(name: str) -> str:
    """A roster name's kind from its prefix — the roster's own vocabulary."""
    low = str(name or "").casefold()
    if low.startswith("custom:"):
        return "dynamic"
    if low.startswith("remote:"):
        return "remote"
    return "builtin"


def member_kind(name: str) -> str:
    """The ``kind`` a team/world member row carries: ``builtin`` | ``custom``
    | ``remote`` (the worlds contract's words; the roster says ``dynamic``
    for a custom agent and that word rides beside it as ``roster_kind``)."""
    kind = kind_of(name)
    return "custom" if kind == "dynamic" else kind


#: What a REMOTE seat is shown in a project room (v1.304.0): the user's own
#: lines and its own — never the local seats' project-grounded replies. The
#: team/world rows carry it as ``sees`` so the UI can say so beside the face.
#: v1.308.0: the round table is gone — a remote on the team is reached by a
#: mission's coordinator through ``delegate``, which sends it the TASK TEXT
#: and nothing else (no files, no other teammate's work).
REMOTE_SEES = "only the task Jarvis hands it"


def sees_for(name: str) -> str | None:
    """``sees`` for a member row: :data:`REMOTE_SEES` for a remote, else None
    (a local seat on the project's model sees the whole room)."""
    return REMOTE_SEES if kind_of(name) == "remote" else None


def participant_for(name: str, role: str = "participant") -> dict[str, str]:
    """A roster name as a thread participant — the ONE bridge the
    ``@mention`` lane also uses (``routes/agents._roster_to_participant``):
    ``custom:<slug>`` sits as ``dynamic``, ``remote:<name>`` as ``remote``."""
    source = kind_of(name)
    return {"source": source, "name": _bare(name), "role": role or "participant"}


#: A remote seat's role at a project table — it answers from another machine.
REMOTE_SEAT_ROLE = "advisor"


def seat_role(platform, name: str) -> str:
    """The ROSTER ROLE a team member sits with when the server seats it: a
    builtin sits as itself (``builder``, ``reviewer`` …), a custom agent as
    the builtin it is based on (its ``base_type`` — what it does), a remote
    as :data:`REMOTE_SEAT_ROLE`. The role is a word in the panelist's prompt
    ("You are taxpro, the researcher in a panel …"), so a short function word,
    never the free-text description. Never raises."""
    kind = kind_of(name)
    if kind == "remote":
        return REMOTE_SEAT_ROLE
    if kind == "dynamic":
        try:
            row = platform.agents_registry.get(_bare(name))
            base = str(getattr(row, "base_type", "") or "").strip()
            return base or "participant"
        except Exception:  # noqa: BLE001 — a registry hiccup seats a participant
            return "participant"
    return _bare(name) or "participant"


def team_seats(platform, members: list[str], entries: list | None = None) -> list[dict[str, str]]:
    """The team as round-table seats, in team order, each with its roster
    role. A member the roster no longer knows is NOT seated — its seat could
    only ever answer "no longer exists"."""
    entries = list(entries if entries is not None else _roster(platform))
    seats = []
    for name in members:
        entry = _match(entries, name)
        if entry is None:
            continue
        seats.append(participant_for(entry.name, seat_role(platform, entry.name)))
    return seats


class NoTeamError(ValueError):
    """There is no room yet and nobody to seat — the route's 409."""


def decode_team(raw: Any) -> list[str]:
    """``Project.team_json`` → roster names. NULL, "", corrupt JSON and
    non-string items all read as nothing — a bad blob is "no team", never a
    500 on the world."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def portrait_fields(home: Path | str, name: str) -> dict[str, Any]:
    """``avatar`` (the serve URL — only when a stored portrait exists) and
    ``face`` (the stored override, or None to derive) for one roster name.

    The SAME rule ``GET /agents/roster`` applies (``routes/agents._avatar_url``
    / ``_face_override``): both are keyed on the BARE name through
    ``agents.files.agent_slug``, so a team row and a roster row can never
    show two different faces for one agent. Never raises."""
    from ..agents import faces
    from ..agents.files import agent_slug

    bare = _bare(str(name or ""))
    avatar = None
    face = None
    try:
        slug = agent_slug(bare)
        if bare and (Path(home) / "avatars" / f"{slug}.png").is_file():
            avatar = f"/agents/{quote(bare, safe='')}/avatar"
        face = faces.read_face(home, slug) or None if bare else None
    except Exception:  # noqa: BLE001 — a display field never breaks a world
        pass
    return {"avatar": avatar, "face": face}


# --------------------------------------------------------------------------- #
# the team
# --------------------------------------------------------------------------- #
def _roster(platform) -> list:
    from ..agents.roster import build_roster

    return build_roster(platform, with_health=False)


def _match(entries: Iterable, raw: str):
    """The roster entry ``raw`` names: exact (case-folded) first, then a BARE
    slug for a prefixed entry (``remy`` → ``custom:remy``). None = unknown.
    Offline remotes still match — a team seat is not a health claim."""
    query = " ".join(str(raw or "").split()).casefold()
    if not query:
        return None
    entries = list(entries)
    for entry in entries:
        if str(entry.name).casefold() == query:
            return entry
    if ":" not in query:
        for entry in entries:
            _pre, sep, slug = str(entry.name).partition(":")
            if sep and slug.casefold() == query:
                return entry
    return None


def _ambiguous(entries: Iterable, raw: str) -> list[str]:
    """The roster names a BARE name could mean when it names no entry exactly
    but two or more prefixed ones (``hermes`` = ``custom:hermes`` and
    ``remote:hermes``) — ``[]`` when it is unambiguous."""
    query = " ".join(str(raw or "").split()).casefold()
    if not query or ":" in query:
        return []
    entries = list(entries)
    if any(str(e.name).casefold() == query for e in entries):
        return []
    hits = [
        str(e.name)
        for e in entries
        if str(e.name).partition(":")[1] and str(e.name).partition(":")[2].casefold() == query
    ]
    return hits if len(hits) > 1 else []


def validate_members(platform, raw: Any) -> list[str]:
    """``PUT /projects/{id}/team``'s ``members`` → canonical roster names, or
    a ``ValueError`` carrying ONE plain sentence. Remote agents and the
    supervisor are allowed (a team is who sits at the table, not who may take
    a queued job — the assignment rules still apply at queue time). Duplicates
    collapse; order is the user's."""
    if not isinstance(raw, list):
        raise ValueError("members must be a list of agent names from the roster")
    # The cap is judged on what was SENT, before any matching or de-duplication
    # — a 10,000-name body must not cost 10,000 roster matches first.
    if len(raw) > TEAM_MAX:
        raise ValueError(
            f"a team can have at most {TEAM_MAX} agents — remove some before adding more"
        )
    entries = _roster(platform)
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("each team member must be an agent's name")
        both = _ambiguous(entries, item)
        if both:
            raise ValueError(
                f"{item.strip()} could be {' or '.join(both)} — say which one by its full name"
            )
        entry = _match(entries, item)
        if entry is None:
            raise ValueError(
                f"{item.strip()} is not an agent on this machine — pick one from the roster"
            )
        if entry.name not in out:
            out.append(entry.name)
    return out


def _entry_view(platform, entry) -> dict[str, Any]:
    """One roster entry as a team/suggestion row: the ``GET /agents/roster``
    row's fields (same names) plus ``label`` (the bare name a seat shows),
    ``participant_key`` (how the round table keys the seat) and
    ``missing: False``."""
    name = str(entry.name)
    view: dict[str, Any] = {
        "name": name,
        "kind": member_kind(name),
        "roster_kind": entry.kind,
        "label": _bare(name),
        "participant_key": f"{participant_for(name)['source']}:{_bare(name)}",
        "description": entry.description,
        "delegable": bool(entry.delegable),
        "healthy": bool(entry.healthy),
        "stats": entry.stats,
        "line": entry.line(),
        "activity": getattr(entry, "activity", "unknown"),
        "paused": bool(getattr(entry, "paused", False)),
        "pause_reason": str(getattr(entry, "pause_reason", "") or ""),
        "allowance": getattr(entry, "allowance", None),
        "reports_to": str(getattr(entry, "reports_to", "") or ""),
        "health": getattr(entry, "health", None),
        "missing": False,
        "sees": sees_for(name),
    }
    view.update(portrait_fields(platform.config.home, name))
    return view


def _missing_view(platform, name: str) -> dict[str, Any]:
    """A team member the roster no longer knows (a deleted custom agent, an
    unregistered remote) — kept visible so the user can take the seat away,
    and SAID to be missing rather than silently dropped."""
    view: dict[str, Any] = {
        "name": name,
        "kind": member_kind(name),
        "roster_kind": kind_of(name),
        "label": _bare(name),
        "participant_key": f"{participant_for(name)['source']}:{_bare(name)}",
        "description": "",
        "delegable": False,
        "healthy": False,
        "stats": None,
        "line": f"{name} (no longer on this machine)",
        "activity": "unknown",
        "paused": False,
        "pause_reason": "",
        "allowance": None,
        "reports_to": "",
        "health": None,
        "missing": True,
        "sees": sees_for(name),
    }
    view.update(portrait_fields(platform.config.home, name))
    return view


def _with_health(platform, entries: list) -> list:
    """The roster's health fold for THESE entries only (a team is a few
    seats; folding the whole roster is four reads per agent for nothing)."""
    try:
        from ..agents.roster import _with_health as fold

        return fold(platform, entries)
    except Exception:  # noqa: BLE001 — a health miss leaves "unknown"
        return entries


def _load_project(platform, project_id: str) -> Project | None:
    with session_scope(platform.engine) as db:
        row = db.get(Project, project_id)
        if row is None:
            return None
        db.expunge(row)
        return row


def team_rows(platform, members: list[str], entries: list | None = None) -> list[dict]:
    """The team as rows, in the user's order, health folded in."""
    entries = list(entries if entries is not None else _roster(platform))
    matched = []
    for name in members:
        entry = _match(entries, name)
        matched.append((name, entry))
    _with_health(platform, [e for _n, e in matched if e is not None])
    return [
        _entry_view(platform, e) if e is not None else _missing_view(platform, n)
        for n, e in matched
    ]


def _work_by_agent(platform, project_id: str, since: datetime) -> dict[str, dict]:
    """``{casefolded roster name: {tasks, last}}`` — the distinct jobs each
    agent did in this project since ``since``: its sessions, plus its
    assignments that never became one of those sessions (an assignment that
    ran IS its session — counting both would double every queued job)."""
    from ..agents.roster import session_roster_name
    from ..assignments.models import AssignmentRecord

    out: dict[str, dict] = {}

    def _bump(name: str, at: datetime | None) -> None:
        key = str(name or "").strip().casefold()
        if not key:
            return
        slot = out.setdefault(key, {"tasks": 0, "last": None})
        slot["tasks"] += 1
        at = _naive(at)
        if at is not None and (slot["last"] is None or at > slot["last"]):
            slot["last"] = at

    with session_scope(platform.engine) as db:
        sessions = list(
            db.exec(
                select(SessionModel).where(
                    SessionModel.project_id == project_id,
                    SessionModel.created_at >= since,  # type: ignore[operator]
                )
            )
        )
        seen = set()
        for s in sessions:
            seen.add(s.id)
            _bump(session_roster_name(s), s.finished_at or s.created_at)
        try:
            rows = list(
                db.exec(
                    select(AssignmentRecord).where(
                        AssignmentRecord.project_id == project_id,
                        AssignmentRecord.created_at >= since,  # type: ignore[operator]
                    )
                )
            )
        except Exception:  # noqa: BLE001 — no assignments table = no assignments
            rows = []
        for a in rows:
            if a.session_id and a.session_id in seen:
                continue
            _bump(a.assignee, a.updated_at or a.created_at)
    return out


def suggestion_rows(
    platform, project_id: str, members: list[str], entries: list | None = None
) -> list[dict]:
    """Agents that already worked on this project in the last
    :data:`SUGGEST_DAYS` days and are not on the team — newest work first,
    at most :data:`SUGGEST_MAX`, each with ``why`` in plain words."""
    entries = list(entries if entries is not None else _roster(platform))
    since = _now() - timedelta(days=SUGGEST_DAYS)
    work = _work_by_agent(platform, project_id, since)
    on_team = {m.casefold() for m in members}
    picked: list[tuple[datetime, int, Any, dict]] = []
    for order, (key, slot) in enumerate(work.items()):
        entry = _match(entries, key)
        if entry is None or str(entry.name).casefold() in on_team:
            continue
        # v1.308.0: a team works a MISSION's parts, and a coordinator
        # (supervisor, planner — anything carrying `delegate`) cannot be
        # handed one; it ran here AS the coordinator, so it is not suggested.
        if not bool(getattr(entry, "delegable", True)):
            continue
        if any(str(p[2].name) == str(entry.name) for p in picked):
            continue
        picked.append((slot["last"] or datetime.min, order, entry, slot))
    # Newest work first; a same-tick tie keeps first-seen order (stable sort).
    picked.sort(key=lambda p: p[0], reverse=True)
    picked = picked[:SUGGEST_MAX]
    _with_health(platform, [p[2] for p in picked])
    out = []
    for last, _order, entry, slot in picked:
        n = int(slot["tasks"])
        row = _entry_view(platform, entry)
        row["why"] = (
            f"worked on {n} task{'s' if n != 1 else ''} here in the last {SUGGEST_DAYS} days"
        )
        row["tasks"] = n
        row["last_worked_at"] = _iso(last if last != datetime.min else None)
        out.append(row)
    return out


def team_payload(platform, project_id: str) -> dict[str, Any] | None:
    """``GET /projects/{id}/team`` — None for an unknown project."""
    project = _load_project(platform, project_id)
    if project is None:
        return None
    members = decode_team(project.team_json)
    entries = _roster(platform)
    from ..agents.threads import AgentThreads

    room = AgentThreads(platform.engine).latest_for_project(project.id)
    return {
        "project_id": project.id,
        "members": members,
        "team": team_rows(platform, members, entries),
        "suggestions": suggestion_rows(platform, project.id, members, entries),
        # The project's round table (the room the team sits at), or null.
        "thread_id": room.id if room is not None else None,
    }


def save_team(platform, project_id: str, members: list[str]) -> Project | None:
    """Write the (already validated) team. None for an unknown project."""
    with session_scope(platform.engine) as db:
        row = db.get(Project, project_id)
        if row is None:
            return None
        row.team_json = json.dumps(members)
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def sync_room(platform, project_id: str, members: list[str]) -> str | None:
    """Seat the team at the project's round table (if it has one): members
    kept keep their role/model pins, new ones join, removed ones leave the
    table (their earlier lines stay in the transcript). An EMPTY team leaves
    the room as it is — a room cannot have no seats. Returns the room id
    that was re-seated, or None."""
    if not members:
        return None
    from ..agents.threads import AgentThreads, clean_participants

    threads = AgentThreads(platform.engine)
    room = threads.latest_for_project(project_id)
    if room is None:
        return None
    try:
        current = json.loads(room.participants_json or "[]")
    except (TypeError, ValueError):
        current = []
    by_key = {str(p.get("key") or ""): p for p in current if isinstance(p, dict)}
    seats = []
    for seat in team_seats(platform, members):
        prior = by_key.get(f"{seat['source']}:{seat['name']}")
        if prior:
            # A member who STAYS keeps the seat the user set: role and pins.
            seat = {**seat, "role": prior.get("role") or seat["role"],
                    "provider": prior.get("provider") or "", "model": prior.get("model") or ""}
        seats.append(seat)
    if not seats:
        return None  # nobody the roster still knows — the room keeps its seats
    threads.update_participants(room.id, clean_participants(seats))
    return room.id


def ensure_room(
    platform, project: Project, participants: list[dict[str, str]] | None
) -> tuple[Any, bool]:
    """The project's round table — ``(record, created)``. An existing room
    is returned untouched; otherwise one is made seating ``participants``
    (already cleaned by the caller) under :data:`_ROOM_LOCK`, so two first
    opens cannot mint two rooms. Raises :class:`NoTeamError` (one sentence)
    when there is no room and nobody to seat."""
    from ..agents.threads import AgentThreads

    threads = AgentThreads(platform.engine)
    with _ROOM_LOCK:
        room = threads.latest_for_project(project.id)
        if room is not None:
            return room, False
        if not participants:
            raise NoTeamError(
                "this project has no team yet — build the team first, then open its round table"
            )
        rec = threads.create(f"{project.name} — round table", participants, project_id=project.id)
        return rec, True


# --------------------------------------------------------------------------- #
# the world: one snapshot of the DB, assembled by the route
# --------------------------------------------------------------------------- #
def _active_projects(db) -> list[Project]:
    rows = list(
        db.exec(
            select(Project).where(
                or_(Project.status == "active", Project.status.is_(None))  # type: ignore[union-attr]
            )
        )
    )
    for r in rows:
        db.expunge(r)
    return rows


def _snapshot(db, project_ids: list[str], review_ids: Iterable[str]) -> dict[str, dict]:
    """The grouped per-project facts both ``/agents/worlds`` and
    ``/projects/{id}/world`` count from — ONE definition of every count.

    Per project: ``active`` [(session id, task, agent, created_at)] (status
    ACTIVE — the ones that might be parked on an ask; the ROUTE checks the
    approvals registry), ``queued_sessions`` (governor-parked), the live and
    blocked assignment rows, ``done_7d`` (sessions completed in the window),
    ``reviews`` [session ids with a pending review], and ``last`` (newest
    activity of any kind)."""
    from ..agents.roster import session_roster_name
    from ..assignments.models import AssignmentRecord

    out: dict[str, dict] = {
        pid: {
            "active": [],
            "queued_sessions": 0,
            "assignments": [],
            "done_7d": 0,
            "reviews": [],
            "attention": [],
            "last": None,
        }
        for pid in project_ids
    }
    if not project_ids:
        return out

    def _touch(pid: str, at: datetime | None) -> None:
        at = _naive(at)
        slot = out.get(pid)
        if slot is None or at is None:
            return
        if slot["last"] is None or at > slot["last"]:
            slot["last"] = at

    # Sessions: counts + newest activity per (project, status) — one query.
    for pid, status, newest_created, newest_finished in db.exec(
        select(
            SessionModel.project_id,
            SessionModel.status,
            func.max(SessionModel.created_at),
            func.max(SessionModel.finished_at),
        )
        .where(SessionModel.project_id.in_(project_ids))  # type: ignore[union-attr]
        .group_by(SessionModel.project_id, SessionModel.status)
    ).all():
        _touch(pid, newest_created)
        _touch(pid, newest_finished)
    for pid, cnt in db.exec(
        select(SessionModel.project_id, func.count())
        .where(
            SessionModel.project_id.in_(project_ids),  # type: ignore[union-attr]
            SessionModel.status == SessionStatus.QUEUED,
        )
        .group_by(SessionModel.project_id)
    ).all():
        out[pid]["queued_sessions"] = int(cnt or 0)
    cutoff = _now() - timedelta(days=DONE_WINDOW_DAYS)
    for pid, cnt in db.exec(
        select(SessionModel.project_id, func.count())
        .where(
            SessionModel.project_id.in_(project_ids),  # type: ignore[union-attr]
            SessionModel.status == SessionStatus.COMPLETED,
            SessionModel.finished_at >= cutoff,  # type: ignore[operator]
            # A run the ledger says NEEDS YOU, or one a restart cut off, is
            # not done — it is waiting (``attention`` below).
            ~_attention_clause(),
        )
        .group_by(SessionModel.project_id)
    ).all():
        out[pid]["done_7d"] = int(cnt or 0)
    # Finished runs that still need the user: the ledger's ``needs_you``
    # verdict (an ask went unanswered) within ATTENTION_DAYS, and every run a
    # restart cut off (``interrupted_at`` — cleared by Continue/dismiss).
    for s in db.exec(
        select(SessionModel)
        .where(
            SessionModel.project_id.in_(project_ids),  # type: ignore[union-attr]
            _attention_clause(),
        )
        # NEWEST first under the cap (review fix): an interrupted run waits
        # until Continue/dismiss with no age bound, so an ascending read let
        # months-old leftovers fill the cap and hid this week's. The Waiting
        # list re-sorts oldest-first itself (``waiting_items``).
        .order_by(
            SessionModel.created_at.desc(),  # type: ignore[union-attr]
            literal_column("rowid").desc(),
        )
        .limit(ATTENTION_MAX)
    ).all():
        interrupted = s.interrupted_at is not None
        out[s.project_id]["attention"].append(
            {
                "id": s.id,
                "kind": "interrupted" if interrupted else "needs_you",
                "title": _title(s.task),
                "agent": session_roster_name(s),
                "since": _naive(s.interrupted_at if interrupted else (s.finished_at or s.created_at)),
            }
        )
    # The ACTIVE rows themselves — few by nature (the governor's slots).
    for s in db.exec(
        select(SessionModel)
        .where(
            SessionModel.project_id.in_(project_ids),  # type: ignore[union-attr]
            SessionModel.status == SessionStatus.ACTIVE,
        )
        .order_by(SessionModel.created_at, literal_column("rowid"))  # type: ignore[arg-type]
    ).all():
        out[s.project_id]["active"].append(
            {
                "id": s.id,
                "title": _title(s.task),
                "agent": session_roster_name(s),
                "created_at": _naive(s.created_at),
            }
        )
    # Reviews of project sessions (ids come from the in-memory registry).
    review_ids = [str(r) for r in review_ids or () if r]
    if review_ids:
        for s in db.exec(
            select(SessionModel).where(SessionModel.id.in_(review_ids))  # type: ignore[union-attr]
        ).all():
            if s.project_id in out:
                out[s.project_id]["reviews"].append(
                    {
                        "id": s.id,
                        "title": _title(s.task),
                        "agent": session_roster_name(s),
                        "since": _naive(s.finished_at or s.created_at),
                    }
                )
    # Assignments that are not finished (live) or are blocked — bounded by
    # the per-assignee queue cap; plus newest activity of ANY assignment.
    try:
        for pid, newest in db.exec(
            select(AssignmentRecord.project_id, func.max(AssignmentRecord.updated_at))
            .where(AssignmentRecord.project_id.in_(project_ids))  # type: ignore[attr-defined]
            .group_by(AssignmentRecord.project_id)
        ).all():
            _touch(pid, newest)
        for a in db.exec(
            select(AssignmentRecord)
            .where(
                AssignmentRecord.project_id.in_(project_ids),  # type: ignore[attr-defined]
                AssignmentRecord.status.in_(  # type: ignore[attr-defined]
                    ("queued", "claimed", "running", "blocked")
                ),
            )
            .order_by(AssignmentRecord.created_at, literal_column("rowid"))  # type: ignore[arg-type]
        ).all():
            out[a.project_id]["assignments"].append(
                {
                    "id": a.id,
                    "status": a.status,
                    "title": a.title or _title(a.task),
                    "agent": a.assignee,
                    "held_reason": a.held_reason or "",
                    "blocked_reason": a.blocked_reason or "",
                    "session_id": a.session_id or "",
                    "since": _naive(a.updated_at or a.created_at),
                }
            )
    except Exception:  # noqa: BLE001 — no assignments table = no assignments
        pass
    # Rooms: newest activity.
    try:
        from ..agents.threads import AgentThreadRecord

        for pid, newest in db.exec(
            select(AgentThreadRecord.project_id, func.max(AgentThreadRecord.updated_at))
            .where(AgentThreadRecord.project_id.in_(project_ids))  # type: ignore[attr-defined]
            .group_by(AgentThreadRecord.project_id)
        ).all():
            _touch(pid, newest)
    except Exception:  # noqa: BLE001
        pass
    return out


def waiting_items(
    project_id: str, snap: dict, asks: dict[str, dict]
) -> list[dict[str, Any]]:
    """What needs the USER in one project, oldest first (the longest wait
    leads). ``asks`` = ``{session id: {"waiting_on": <the listing view —
    numbers, never arguments>, "since": datetime|None}}`` for the ACTIVE
    sessions the route found parked on the approvals registry."""
    items: list[dict[str, Any]] = []
    for s in snap["active"]:
        ask = asks.get(s["id"])
        if not ask:
            continue
        waiting_on = ask.get("waiting_on") or {}
        tool = str(waiting_on.get("tool") or "a tool")
        count = waiting_on.get("count")
        calls = f" ({count} calls)" if isinstance(count, int) and count > 1 else ""
        items.append(
            {
                "kind": "ask",
                "id": s["id"],
                "key": f"ask:{s['id']}",
                "title": s["title"],
                "agent": s["agent"],
                "since": ask.get("since") or s["created_at"],
                "reason": f"waiting for your answer on {tool}{calls}",
                "link": f"/sessions/{quote(s['id'], safe='')}",
                "session_id": s["id"],
                "waiting_on": waiting_on,
            }
        )
    tasks_link = f"/projects/{quote(project_id, safe='')}?tab=tasks"
    for a in snap["assignments"]:
        if a["status"] == "blocked":
            kind, reason = "blocked", a["blocked_reason"] or "blocked — it needs you to unblock or retry it"
        elif a["status"] == "queued" and a["held_reason"]:
            kind, reason = "held", a["held_reason"]
        else:
            continue
        items.append(
            {
                "kind": kind,
                "id": a["id"],
                "key": f"{kind}:{a['id']}",
                "title": a["title"],
                "agent": a["agent"],
                "since": a["since"],
                "reason": reason,
                "link": tasks_link,
                "assignment_id": a["id"],
            }
        )
    for s in snap.get("attention", []):
        interrupted = s["kind"] == "interrupted"
        items.append(
            {
                "kind": s["kind"],
                "id": s["id"],
                "key": f"{s['kind']}:{s['id']}",
                "title": s["title"],
                "agent": s["agent"],
                "since": s["since"],
                "reason": (
                    "a restart cut it off — open it to continue"
                    if interrupted
                    else "it stopped because a question it asked you went unanswered"
                ),
                "link": f"/sessions/{quote(s['id'], safe='')}",
                "session_id": s["id"],
            }
        )
    for r in snap["reviews"]:
        items.append(
            {
                "kind": "review",
                "id": r["id"],
                "key": f"review:{r['id']}",
                "title": r["title"],
                "agent": r["agent"],
                "since": r["since"],
                "reason": "its change is waiting for your review",
                "link": f"/sessions/{quote(r['id'], safe='')}",
                "session_id": r["id"],
            }
        )
    items.sort(key=lambda i: _naive(i["since"]) or datetime.min)
    for i in items:
        i["since"] = _iso(_naive(i["since"]))
    return items


def counts_for(snap: dict, asks: dict[str, dict]) -> dict[str, int]:
    """``{waiting, running, queued, done_7d}`` — each piece of work in exactly
    ONE bucket: a session parked on an ask is waiting (not running); a held
    assignment is waiting (not queued); an assignment that already has its
    session is counted as that session."""
    parked = {s["id"] for s in snap["active"] if asks.get(s["id"])}
    blocked = sum(1 for a in snap["assignments"] if a["status"] == "blocked")
    held = sum(1 for a in snap["assignments"] if a["status"] == "queued" and a["held_reason"])
    queued = sum(
        1 for a in snap["assignments"] if a["status"] == "queued" and not a["held_reason"]
    )
    starting = sum(
        1
        for a in snap["assignments"]
        if a["status"] in ("claimed", "running") and not a["session_id"]
    )
    return {
        "waiting": len(parked) + blocked + held + len(snap["reviews"])
        + len(snap.get("attention", [])),
        "running": len(snap["active"]) - len(parked) + starting,
        "queued": int(snap["queued_sessions"]) + queued,
        "done_7d": int(snap["done_7d"]),
    }


def _status_index(platform, teams: list[list[str]]) -> dict[str, Any]:
    """``{member name: roster entry | None}`` for every member of every team —
    ONE roster build per request (never one per world), with the health fold
    (the roster's own ``idle`` rule) applied only to the entries that sit on
    some team. Blocking: runs inside :func:`gather_worlds`, off the loop."""
    wanted = {m for team in teams for m in team}
    if not wanted:
        return {}
    entries = _roster(platform)
    index = {m: _match(entries, m) for m in wanted}
    seen: dict[int, Any] = {}
    for entry in index.values():
        if entry is not None:
            seen[id(entry)] = entry
    _with_health(platform, list(seen.values()))
    return index


def _team_faces(
    platform, members: list[str], status: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The light team row a world CARD carries: the face, plus the three
    status fields its dot reads — ``paused``, ``activity``, ``healthy`` — with
    the SAME values ``GET /agents/roster`` serves. A member the roster no
    longer knows reads paused False, activity "unknown", healthy False."""
    home = platform.config.home
    status = status or {}
    rows = []
    for m in members:
        entry = status.get(m)
        rows.append(
            {
                "name": m,
                "kind": member_kind(m),
                "label": _bare(m),
                **portrait_fields(home, m),
                "paused": bool(getattr(entry, "paused", False)) if entry is not None else False,
                "activity": (
                    str(getattr(entry, "activity", "unknown") or "unknown")
                    if entry is not None
                    else "unknown"
                ),
                "healthy": bool(getattr(entry, "healthy", False)) if entry is not None else False,
                "missing": entry is None,
                "sees": sees_for(m),
            }
        )
    return rows


def _project_view(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status or "active",
        "brief": project.brief or "",
        "root": project.root or "",
        "created_at": _iso(project.created_at),
    }


def _room_ids(db, project_ids: list[str]) -> dict[str, str]:
    """``{project id: its round table's thread id}`` — the most recently
    active project-bound room, rowid breaking a same-tick tie."""
    from ..agents.threads import AgentThreadRecord

    out: dict[str, str] = {}
    if not project_ids:
        return out
    try:
        rows = db.exec(
            select(AgentThreadRecord.id, AgentThreadRecord.project_id)
            .where(AgentThreadRecord.project_id.in_(project_ids))  # type: ignore[attr-defined]
            .order_by(
                AgentThreadRecord.updated_at.desc(),  # type: ignore[attr-defined]
                literal_column("rowid").desc(),
            )
        ).all()
    except Exception:  # noqa: BLE001
        return out
    for tid, pid in rows:
        out.setdefault(pid, tid)
    return out


def gather_worlds(platform, review_ids: Iterable[str]) -> dict[str, Any]:
    """The DB half of ``GET /agents/worlds``: every ACTIVE project with its
    team faces, room id and the snapshot its counts come from, plus the
    General world's room count. A handful of grouped queries, whatever the
    number of projects."""
    from ..agents.threads import AgentThreadRecord

    with session_scope(platform.engine) as db:
        projects = _active_projects(db)
        pids = [p.id for p in projects]
        snaps = _snapshot(db, pids, review_ids)
        rooms = _room_ids(db, pids)
        try:
            general = int(
                db.exec(
                    select(func.count()).select_from(AgentThreadRecord).where(
                        or_(
                            AgentThreadRecord.project_id == "",
                            AgentThreadRecord.project_id.is_(None),  # type: ignore[union-attr]
                        )
                    )
                ).one()
            )
        except Exception:  # noqa: BLE001
            general = 0
    teams = {p.id: decode_team(p.team_json) for p in projects}
    # The face dots' status: ONE roster build for every world on the page.
    status = _status_index(platform, list(teams.values()))
    worlds = []
    for p in projects:
        snap = snaps[p.id]
        last = snap["last"] or _naive(p.created_at)
        worlds.append(
            {
                "project": _project_view(p),
                "team": _team_faces(platform, teams[p.id], status),
                "thread_id": rooms.get(p.id),
                "snap": snap,
                "last_activity": last,
            }
        )
    # Newest activity first; a tie keeps the newer project first.
    worlds.sort(
        key=lambda w: (w["last_activity"] or datetime.min, w["project"]["created_at"] or ""),
        reverse=True,
    )
    return {"worlds": worlds, "general": {"thread_count": general}}


def _created_names(db, session_ids: list[str]) -> dict[str, list[str]]:
    """``{session id: [file NAMES it created]}`` — the undo-ledger rule
    ``agents.outcome.session_result`` reads (a ``file_delete`` /
    ``files_delete`` journal is a creation), in two queries for the whole
    page instead of one ``session_result`` per row. NAMES only: the world
    lists what was made, never where on the user's disk it sits."""
    from ..agents.outcome import _CREATED_KIND, _CREATED_MANY_KIND, _envelope_path, _envelope_paths

    out: dict[str, list[str]] = {sid: [] for sid in session_ids}
    if not session_ids:
        return out
    invocations = list(
        db.exec(
            select(ToolInvocation)
            .where(ToolInvocation.session_id.in_(session_ids))  # type: ignore[union-attr]
            .order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
        )
    )
    live = [i for i in invocations if not i.undo_of]
    if not live:
        return out
    journals = {
        j.action_id: j
        for j in db.exec(
            select(UndoJournal).where(
                UndoJournal.action_id.in_([i.id for i in live])  # type: ignore[union-attr]
            )
        )
    }
    for inv in live:
        journal = journals.get(inv.id)
        if journal is None:
            continue
        if journal.kind == _CREATED_MANY_KIND:
            paths = _envelope_paths(journal)
        elif journal.kind == _CREATED_KIND:
            paths = [_envelope_path(journal)]
        else:
            continue
        names = out.setdefault(inv.session_id, [])
        for p in paths:
            name = Path(str(p).replace("\\", "/")).name if p else ""
            if name and name not in names:
                names.append(name)
    return out


def _completed(db, project_id: str) -> list[dict[str, Any]]:
    """Finished work in one project, newest first, at most
    :data:`COMPLETED_MAX`: completed/failed sessions (with their outcome and
    created file names) and done/failed assignments whose session is not
    already one of those rows."""
    from ..agents.roster import session_roster_name
    from ..assignments.models import AssignmentRecord

    sessions = list(
        db.exec(
            select(SessionModel)
            .where(
                SessionModel.project_id == project_id,
                SessionModel.status.in_(  # type: ignore[attr-defined]
                    (SessionStatus.COMPLETED, SessionStatus.FAILED)
                ),
                # Waiting on the user (needs_you / interrupted) is not
                # Completed — it is listed under Waiting instead.
                ~_attention_clause(),
            )
            .order_by(
                SessionModel.finished_at.desc(),  # type: ignore[union-attr]
                literal_column("rowid").desc(),
            )
            .limit(COMPLETED_MAX)
        )
    )
    names = _created_names(db, [s.id for s in sessions])
    try:
        assignments = list(
            db.exec(
                select(AssignmentRecord)
                .where(
                    AssignmentRecord.project_id == project_id,
                    AssignmentRecord.status.in_(("done", "failed")),  # type: ignore[attr-defined]
                )
                .order_by(
                    AssignmentRecord.finished_at.desc(),  # type: ignore[union-attr]
                    literal_column("rowid").desc(),
                )
                .limit(COMPLETED_MAX)
            )
        )
    except Exception:  # noqa: BLE001
        assignments = []
    by_session = {a.session_id: a for a in assignments if a.session_id}
    items: list[dict[str, Any]] = []
    listed = set()
    for s in sessions:
        listed.add(s.id)
        status = getattr(s.status, "value", str(s.status))
        files = names.get(s.id, [])
        a = by_session.get(s.id)
        item = {
            "kind": "session",
            "id": s.id,
            "key": f"session:{s.id}",
            "title": (a.title if a is not None and a.title else _title(s.task)),
            "agent": session_roster_name(s),
            "finished_at": _naive(s.finished_at or s.created_at),
            "status": status,
            "outcome": getattr(s, "outcome", None) or status,
            "files": files[:FILES_PER_ITEM],
            "files_total": len(files),
            "link": f"/sessions/{quote(s.id, safe='')}",
            "session_id": s.id,
        }
        if a is not None:
            item["assignment_id"] = a.id
        items.append(item)
    tasks_link = f"/projects/{quote(project_id, safe='')}?tab=tasks"
    # sqlmodel answers a one-column select with SCALARS, not 1-tuples.
    waiting_ids = (
        {
            str(sid)
            for sid in db.exec(
                select(SessionModel.id).where(
                    SessionModel.project_id == project_id, _attention_clause()
                )
            ).all()
        }
        if assignments
        else set()
    )
    for a in assignments:
        if a.session_id and a.session_id in listed:
            continue
        if a.session_id and a.session_id in waiting_ids:
            continue  # its run waits on the user — listed under Waiting
        outcome = "completed" if a.status == "done" else "failed"
        items.append(
            {
                "kind": "assignment",
                "id": a.id,
                "key": f"assignment:{a.id}",
                "title": a.title or _title(a.task),
                "agent": a.assignee,
                "finished_at": _naive(a.finished_at or a.updated_at or a.created_at),
                "status": a.status,
                "outcome": outcome,
                "files": [],
                "files_total": 0,
                "link": (
                    f"/sessions/{quote(a.session_id, safe='')}" if a.session_id else tasks_link
                ),
                "session_id": a.session_id or "",
                "assignment_id": a.id,
                "error": one_sentence(a.last_error or "", ERROR_CHARS),
            }
        )
    # Newest first; the sort is stable, so a same-tick tie keeps the query's
    # rowid-DESC order (sessions before the assignments merged after them).
    items.sort(key=lambda i: i["finished_at"] or datetime.min, reverse=True)
    items = items[:COMPLETED_MAX]
    for i in items:
        i["finished_at"] = _iso(i["finished_at"])
    return items


def gather_world(platform, project_id: str, review_ids: Iterable[str]) -> dict[str, Any] | None:
    """The DB half of ``GET /projects/{id}/world`` (None = unknown project):
    the project, its full team rows (health folded), its room, the snapshot
    and the completed list. The ROUTE adds the asks and assembles."""
    with session_scope(platform.engine) as db:
        project = db.get(Project, project_id)
        if project is None:
            return None
        db.expunge(project)
        snap = _snapshot(db, [project.id], review_ids)[project.id]
        room = _room_ids(db, [project.id]).get(project.id)
        completed = _completed(db, project.id)
    members = decode_team(project.team_json)
    return {
        "project": _project_view(project),
        "members": members,
        "team": team_rows(platform, members),
        "thread_id": room,
        "snap": snap,
        "completed": completed,
    }


__all__ = [
    "COMPLETED_MAX",
    "SUGGEST_DAYS",
    "SUGGEST_MAX",
    "TEAM_MAX",
    "counts_for",
    "decode_team",
    "ensure_room",
    "gather_world",
    "gather_worlds",
    "NoTeamError",
    "kind_of",
    "member_kind",
    "participant_for",
    "seat_role",
    "team_seats",
    "portrait_fields",
    "save_team",
    "suggestion_rows",
    "sync_room",
    "team_payload",
    "team_rows",
    "validate_members",
    "waiting_items",
]
