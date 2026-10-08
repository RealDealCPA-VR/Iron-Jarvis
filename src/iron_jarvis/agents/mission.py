"""The MISSION view (v1.307.0): one objective, one team, read off the record.

The Agents page's mission layout shows the deliverable first and the
workforce underneath it: a coordinator session (the Team — a SUPERVISOR run)
and every teammate it handed work to. This module composes that view from
data the app already keeps — nothing here is new state:

* MEMBERS come from the delegation tree (``AgentRun.parent_id`` links, the
  same walk ``GET /sessions/{id}/team`` does, depth-capped at the delegation
  limit), plus remote teammates, which open no session and are known only
  from their ``delegation.*`` events.
* PROGRESS is honest or absent. ``done`` is 100%, a run that has not started
  is 0%, and a running member shows a percentage ONLY when there is a count
  behind it: its plan's completed steps (``plan.*`` events of the decomposed
  lane). Otherwise ``pct`` is ``None`` and the label says what IS known
  ("working · step 3"). A bar that moved because a clock ticked would be a
  claim the record cannot support — the one thing this app refuses.
* ACTIVITY is a human-readable log built from the ledger
  (``ToolInvocation`` — whose args are stored REDACTED) and the persisted
  events (delegations, plans, asks, completions), newest last. One renderer,
  server side, so the page never guesses what a tool name means.
* The DELIVERABLE is the coordinator's own result once it has one, plus the
  files the whole team created or changed (journaled mutations, never the
  prose — the exact ``outcome.session_result`` documents semantics, computed
  team-wide in one read since v1.309.0: see ``_team_documents``).

THE POLL IS CHEAP (v1.309.0, mission-poll-n-plus-one / -recomputes-whole-
ledger). The page asks for this view every 2 s while a mission runs. It used
to cost a ``db.get`` per member plus a full ``session_result`` per member —
each of which read every ``ToolInvocation`` row of that member WITH its
``output`` (up to 4,000 chars a row) — so a team of eight with a few hundred
tool calls was ~60 statements and megabytes of text per tick, for a view
that shows none of those outputs. Now the statement count does not grow with
the team (one batched member read, one team-wide journal join, explicit
columns everywhere, ``output`` never selected) and the route answers an ETag,
so an unchanged tick is a bodiless 304. Pinned by statement count, never by
a clock (``tests/test_wave1_mission_view_v1309.py``).

BLOCKING (SQLite reads): the route calls it from FastAPI's threadpool. Never
raises past a missing session (``None``); every sub-read degrades to less.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import literal_column
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.models import (
    AgentRun,
    AgentState,
    EventRecord,
    Session,
    SessionStatus,
    ToolInvocation,
    UndoJournal,
)

#: Delegation depth cap (``delegate_tool._MAX_DELEGATION_DEPTH``) — the walk
#: stops there, which also makes a corrupt parent_id cycle harmless.
_MAX_DEPTH = 3
_MAX_MEMBERS = 24
#: Rows of activity the view carries (newest kept).
_ACTIVITY_MAX = 80
#: How many recent ledger/event rows are read to build it.
_SCAN_ROWS = 240
_TASK_CHARS = 140
_RESULT_CHARS = 600

COORDINATOR = "Jarvis"

#: The origin the mission door stamps on its coordinator. ONE definition:
#: ``agents/team.py`` owns it (and the teammates' ``job:mission-member``).
from .team import MISSION_ORIGIN  # noqa: E402

#: Route changes the view reads for its ``route_note`` (v1.309.0,
#: mission-no-route-receipt) — a separate, small read, so a busy mission's
#: 240-row event window can never push the one failover out of sight.
_ROUTE_EVENT_TYPES = (EventType.PROVIDER_FAILOVER, EventType.PROVIDER_DOWNGRADED)
_ROUTE_ROWS = 20
#: How many candidate rows the ``continued_as`` lookup verifies.
_CONTINUATION_ROWS = 10

#: WHO REFUSED A CALL (v1.309.0 review). A refused call is ledgered
#: ``verdict=ask, ok=False`` whoever refused it: ``registry.invoke`` builds
#: ``PermissionDecision(False, ASK, deny_reason)`` for ANY caller-supplied
#: refusal, so the runtime's repeated-failure breaker, the roster gate and
#: the low-trust refusal share the verdict with a real ask. Only the
#: ``tool.denied`` event's ``kind`` (the registry's ``deny_label``) says which
#: it was. These two are the approval pause: the user said no / nothing here
#: could ask ("permission denied" — an engine ASK refusal or the pause's
#: decline) and the pause timed out ("paused").
_ASK_DENIAL_KINDS = frozenset({"permission denied", "paused"})
#: The other refusals, in plain words — nobody was asked, so none of them may
#: read "needed your OK" (a breaker-refused ``read_file`` is an allow-tier
#: tool). ``{t}`` is the tool name with spaces.
_REFUSAL_WORDS: dict[str, str] = {
    "refused": "was stopped from repeating a {t} call that kept failing",
    "not armed": "tried {t}, a tool it was not given — nothing ran",
    "low trust": "was kept from using {t} (low trust)",
}

_EVENT_TYPES = (
    EventType.DELEGATION_STARTED,
    EventType.DELEGATION_COMPLETED,
    EventType.PLAN_CREATED,
    EventType.PLAN_STEP_STARTED,
    EventType.PLAN_STEP_COMPLETED,
    EventType.APPROVAL_REQUESTED,
    EventType.APPROVAL_RESOLVED,
    EventType.SESSION_COMPLETED,
)


def display_name(roster_name: str) -> str:
    """``custom:tax-reader`` → ``tax-reader``; ``researcher`` → ``Researcher``;
    ``remote:hermes`` → ``hermes``. A user's own name is never re-cased."""
    text = " ".join(str(roster_name or "").split())
    if not text:
        return "Agent"
    head, sep, tail = text.partition(":")
    if sep and head.lower() in ("custom", "remote", "builtin", "dynamic"):
        return tail.strip() or text
    return text[:1].upper() + text[1:]


def member_kind(roster_name: str) -> str:
    low = str(roster_name or "").lower()
    if low.startswith("custom:"):
        return "custom"
    if low.startswith("remote:"):
        return "remote"
    return "builtin"


def _short(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _iso(moment: Any) -> str | None:
    try:
        return moment.isoformat() if moment is not None else None
    except Exception:  # noqa: BLE001
        return None


def _payload(row: EventRecord) -> dict[str, Any]:
    try:
        data = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _args(inv: ToolInvocation) -> dict[str, Any]:
    try:
        data = json.loads(inv.args_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _base(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return Path(text).name or text
    except Exception:  # noqa: BLE001
        return text


#: tool name -> (verb when it worked, verb when it failed). ``{x}`` is filled
#: from the call's (redacted) arguments when one is available.
_TOOL_WORDS: dict[str, tuple[str, str]] = {
    "read_file": ("read {x}", "could not read {x}"),
    "read_document": ("read {x}", "could not read {x}"),
    "extract_pdf": ("read the PDF {x}", "could not read the PDF {x}"),
    "view_image": ("looked at {x}", "could not open the image {x}"),
    "list_files": ("looked through a folder", "could not list a folder"),
    "list_folder": ("looked through a folder", "could not list a folder"),
    "grep": ("searched the files for “{q}”", "searched the files and found nothing usable"),
    "file_search": ("searched your drives for “{q}”", "searched your drives without a result"),
    "write_file": ("wrote {x}", "could not write {x}"),
    "write_document": ("created {x}", "could not create {x}"),
    "edit_file": ("edited {x}", "could not edit {x}"),
    "rename_file": ("renamed {x}", "could not rename {x}"),
    "docx_edit": ("edited {x}", "could not edit {x}"),
    "excel_edit": ("updated the workbook {x}", "could not update the workbook {x}"),
    "excel_read": ("read the workbook {x}", "could not read the workbook {x}"),
    "web_search": ("searched the web for “{q}”", "searched the web without a result"),
    "browse": ("opened a web page", "could not open a web page"),
    "web_extract": ("read a web page", "could not read a web page"),
    "recall": ("checked memory for “{q}”", "checked memory without a result"),
    "ltm_search": ("searched the knowledge base for “{q}”", "searched the knowledge base without a result"),
    "memory_search": ("searched notes for “{q}”", "searched notes without a result"),
    "shell": ("ran a command", "ran a command that failed"),
    "consult": ("asked {a} for advice", "could not reach {a} for advice"),
    "blackboard_post": ("posted a note for the team", "could not post a note"),
    "message_agent": ("messaged a teammate", "could not message a teammate"),
    "worklist_add": ("listed the items to work through", "could not list the items"),
    "worklist_done": ("finished an item", "could not record an item"),
    "remember_preference": ("noted a preference", "could not note a preference"),
    "notebook": ("updated its notebook", "could not update its notebook"),
}

#: Tools whose lines are bookkeeping, not work — kept out of the log.
_QUIET_TOOLS = frozenset(
    {"delegate", "spawn_agent", "worklist_next", "worklist_status", "blackboard_read",
     "recall_lessons", "skill_search", "skill_load", "tool_list", "list_agents"}
)


def tool_line(tool: str, args: dict[str, Any], ok: bool) -> str:
    """One plain-words line for a tool call. Never quotes a whole argument
    blob — a basename, a short query, or nothing."""
    words = _TOOL_WORDS.get(tool)
    x = _base(args.get("path") or args.get("file") or args.get("source") or args.get("dst") or "")
    q = _short(args.get("query") or args.get("pattern") or args.get("q") or "", 60)
    a = _short(args.get("agent") or "a teammate", 40)
    if words is None:
        label = tool.replace("_", " ")
        return f"used {label}" if ok else f"tried {label} and it failed"
    text = words[0] if ok else words[1]
    if "{x}" in text and not x:
        text = text.replace(" {x}", " a file").replace("{x}", "a file")
    if "{q}" in text and not q:
        text = text.replace(" for “{q}”", "")
    return text.format(x=x, q=q, a=a)


def session_options(session: Any) -> dict[str, Any]:
    """The decoded run options of a session row — ``{}`` for a legacy row or
    junk in the column. Never raises."""
    raw = getattr(session, "options_json", None)
    if not raw:
        return {}
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def display_objective(session: Any) -> str:
    """What the user ASKED for, for display (v1.309.0, contract 5/6).

    A continuation's ``Session.task`` is the model-facing recap ("<message>
    [Continuing an earlier session. Original task: …]"); shown as the
    mission's objective it filled the Recent list with recap-wrapped text and
    listed every follow-up as a new, oddly-worded mission. The continue door
    stores the user's own words in ``options["objective"]`` (Track A writes
    it in ``continue_session``; the route also stamps it), and that wins;
    every other mission's objective IS its task."""
    objective = session_options(session).get("objective")
    if isinstance(objective, str) and objective.strip():
        return objective.strip()
    return str(getattr(session, "task", "") or "")


def continued_as(db, session_id: str, since: Any = None) -> str | None:
    """The id of the NEWEST session continuing ``session_id``, or None
    (v1.309.0, interrupted-mission-no-continue). No column links a
    continuation to its parent, so the continue and retry-failed doors stamp
    ``options["continued_from"]`` on the new row (always overwritten — run
    options carry over through ``_stored_options``). The LIKE only narrows the
    candidates; each one is decoded and checked, so a formatting difference
    or an id that is a prefix of another can never produce a wrong link.

    BOUNDED (v1.309.0 review): the view runs this every 2 s, and a bare LIKE
    is an unindexed scan of the WHOLE session table per tick — a cost the
    statement-count pin cannot see. Two facts true of every continuation
    narrow it first: it is a mission (only a mission's continuation is
    stamped) and it was created no earlier than the mission it continues
    (``since`` = that row's ``created_at``)."""
    if not session_id:
        return None
    stmt = select(Session.id, Session.options_json).where(
        Session.origin == MISSION_ORIGIN,
        Session.options_json.like(f"%{session_id}%"),  # type: ignore[union-attr]
    )
    if since is not None:
        stmt = stmt.where(Session.created_at >= since)  # type: ignore[operator]
    rows = db.exec(
        stmt
        .order_by(
            Session.created_at.desc(),  # type: ignore[attr-defined]
            literal_column("session.rowid").desc(),
        )
        .limit(_CONTINUATION_ROWS)
    )
    for sid, raw in rows:
        try:
            opts = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(opts, dict) and opts.get("continued_from") == session_id and sid != session_id:
            return str(sid)
    return None


def _walk(db, root_id: str) -> tuple[list[Session], dict[str, list[AgentRun]]]:
    """``(member sessions in discovery order, runs by session id)`` — the
    delegation tree under ``root_id`` (root included in ``runs``).

    v1.309.0: the member rows of one depth are read in ONE statement (it was
    a ``db.get`` per member), so the walk costs a fixed number of statements
    per DEPTH — capped at ``_MAX_DEPTH`` — however large the team."""
    runs_by: dict[str, list[AgentRun]] = {}
    members: list[Session] = []
    seen = {root_id}
    frontier = list(db.exec(select(AgentRun).where(AgentRun.session_id == root_id)))
    runs_by[root_id] = list(frontier)
    for _depth in range(_MAX_DEPTH):
        parent_ids = [r.id for r in frontier]
        if not parent_ids:
            break
        linked = list(
            db.exec(
                select(AgentRun)
                .where(AgentRun.parent_id.in_(parent_ids))  # type: ignore[attr-defined, union-attr]
                .order_by(AgentRun.created_at)  # type: ignore[arg-type]
            )
        )
        candidates: list[str] = []
        for r in linked:
            sid = r.session_id
            if not sid or sid in seen:
                continue
            seen.add(sid)
            candidates.append(sid)
        rows: dict[str, Session] = {}
        if candidates:
            rows = {
                row.id: Session(**row.model_dump())
                for row in db.exec(
                    select(Session).where(Session.id.in_(candidates))  # type: ignore[attr-defined]
                )
            }
        next_ids: list[str] = []
        for sid in candidates:
            row = rows.get(sid)
            if row is None:
                continue
            members.append(row)
            next_ids.append(sid)
            if len(members) >= _MAX_MEMBERS:
                break
        frontier = []
        if next_ids:
            frontier = list(
                db.exec(
                    select(AgentRun).where(AgentRun.session_id.in_(next_ids))  # type: ignore[attr-defined]
                )
            )
            for r in frontier:
                runs_by.setdefault(r.session_id, []).append(r)
        if len(members) >= _MAX_MEMBERS:
            break
    return members, runs_by


def _status(session: Session, runs: list[AgentRun], waiting: bool) -> str:
    """``waiting_you`` | ``queued`` | ``working`` | ``done`` | ``failed`` |
    ``cancelled`` — the card's one word, from the row and its runs."""
    st = session.status
    if st is SessionStatus.COMPLETED:
        return "done"
    if st is SessionStatus.FAILED:
        return "failed"
    if st is SessionStatus.CANCELLED:
        return "cancelled"
    if waiting or any(r.state is AgentState.WAITING for r in runs):
        return "waiting_you"
    if st is SessionStatus.QUEUED or not runs:
        return "queued"
    return "working"


def _plan_counts(events: list[EventRecord], session_id: str) -> tuple[int, int, str]:
    """``(steps planned, steps finished, current step goal)`` from the
    decomposed lane's persisted ``plan.*`` events for one session."""
    total = finished = 0
    current = ""
    for ev in events:
        if ev.session_id != session_id:
            continue
        data = _payload(ev)
        if ev.type == EventType.PLAN_CREATED:
            steps = data.get("steps")
            total = len(steps) if isinstance(steps, list) else total
            finished, current = 0, ""
        elif ev.type == EventType.PLAN_STEP_STARTED:
            current = _short(data.get("goal") or "", 90)
        elif ev.type == EventType.PLAN_STEP_COMPLETED:
            finished += 1
    return total, min(finished, total) if total else finished, current


def progress_for(status: str, steps: int, plan: tuple[int, int, str]) -> dict[str, Any]:
    """The card's bar. ``pct`` is None unless something COUNTS it."""
    total, finished, current = plan
    if status == "done":
        return {"pct": 100, "label": "Done", "basis": "done"}
    if status == "queued":
        return {"pct": 0, "label": "Waiting to start", "basis": "queued"}
    if status in ("failed", "cancelled"):
        return {"pct": None, "label": "Failed" if status == "failed" else "Stopped", "basis": status}
    if total > 0:
        pct = int(finished * 100 / total)
        label = f"Step {min(finished + 1, total)} of {total}"
        if current:
            label += f": {current}"
        return {"pct": pct, "label": label, "basis": "plan"}
    if status == "waiting_you":
        return {"pct": None, "label": "Waiting for your OK", "basis": "waiting"}
    label = f"Working · step {steps}" if steps else "Working"
    return {"pct": None, "label": label, "basis": "steps"}


def _team_documents(db, sessions: dict[str, Session]) -> dict[str, list[str]]:
    """``{session id: documents}`` for the whole team in ONE statement
    (v1.309.0) — the exact ``outcome.session_result`` ``documents`` list for
    every session in ``sessions``, without reading a single tool output.

    ``session_result`` walks every invocation of a session and looks each one
    up in the undo journal; only the rows that HAVE a file journal can name a
    document, so this joins the two and selects just those rows' id, session,
    journal kind and envelope. Same rules, same order: undo rows skipped;
    ``file_delete`` = created, ``file_restore`` = changed, ``files_delete``
    expands its envelope into created; a path both created and changed is a
    creation; workspace-relative de-duplication, then resolved under the
    session's workspace (no workspace = no documents), capped at the same
    list cap. Ties on ``created_at`` break on rowid, which is the order
    ``session_result``'s single-session read returns them in.
    ``test_team_documents_match_the_ledger_after_the_rewrite`` is the golden
    pin that keeps this and ``session_result`` agreeing.
    """
    from .outcome import (
        _CHANGED_KIND,
        _CREATED_KIND,
        _CREATED_MANY_KIND,
        _LIST_CAP,
        _envelope_path,
        _envelope_paths,
        _rel,
    )

    out: dict[str, list[str]] = {sid: [] for sid in sessions}
    if not sessions:
        return out
    rows = db.exec(
        select(ToolInvocation.session_id, UndoJournal.kind, UndoJournal.pre_inline)
        .join(UndoJournal, UndoJournal.action_id == ToolInvocation.id)  # type: ignore[arg-type]
        .where(
            ToolInvocation.session_id.in_(list(sessions)),  # type: ignore[attr-defined]
            ToolInvocation.undo_of.is_(None),  # type: ignore[union-attr]
            UndoJournal.kind.in_((_CREATED_KIND, _CHANGED_KIND, _CREATED_MANY_KIND)),  # type: ignore[attr-defined]
        )
        .order_by(
            ToolInvocation.created_at,  # type: ignore[arg-type]
            literal_column("toolinvocation.rowid"),
        )
    )
    created: dict[str, list[str]] = {sid: [] for sid in sessions}
    changed: dict[str, list[str]] = {sid: [] for sid in sessions}
    for sid, kind, pre_inline in rows:
        workspace = (sessions[sid].workspace_path or "") if sid in sessions else ""
        journal = UndoJournal(action_id="", kind=kind or "", pre_inline=pre_inline)
        made, edited = created.setdefault(sid, []), changed.setdefault(sid, [])
        if kind == _CREATED_MANY_KIND:
            for p in _envelope_paths(journal):
                rp = _rel(p, workspace)
                if rp and rp not in made:
                    made.append(rp)
            continue
        path = _rel(_envelope_path(journal), workspace)
        if not path:
            continue
        if kind == _CREATED_KIND and path not in made:
            made.append(path)
        elif kind == _CHANGED_KIND and path not in edited:
            edited.append(path)
    for sid, row in sessions.items():
        workspace = row.workspace_path or ""
        if not workspace:
            continue
        made = created.get(sid, [])
        edited = [p for p in changed.get(sid, []) if p not in made]
        try:
            out[sid] = [str((Path(workspace) / p).resolve()) for p in (made + edited)[:_LIST_CAP]]
        except (OSError, ValueError):  # a path the OS refuses costs that card its files
            out[sid] = []
    return out


def _route_note(route_events: list[tuple[str, str | None, str]], names: dict[str, str]) -> str:
    """ONE plain sentence when a failover or a downgrade touched this mission,
    else ``""`` (v1.309.0, mission-no-route-receipt). ``route_events`` are
    ``(type, session_id, payload_json)``, newest first. The newest change is
    named; more than one is counted. Provider names and the router's own
    DERIVED reason (``failure_reason``: "unreachable", "http 500", …) only —
    never an error blob."""
    notes: list[str] = []
    for etype, sid, raw in route_events:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        who = names.get(sid or "", "A teammate")
        why = _short(data.get("reason") or "", 80)
        if etype == EventType.PROVIDER_FAILOVER:
            src = _short(data.get("from") or "its model", 40)
            dst = _short(data.get("to") or "another model", 40)
            text = f"{who}'s model {src} failed" + (f" ({why})" if why else "")
            notes.append(f"{text}; {dst} answered instead.")
        elif etype == EventType.PROVIDER_DOWNGRADED:
            wanted = _short(data.get("requested") or "the chosen model", 40)
            used = str(data.get("used") or "")
            if used == "mock":
                notes.append(f"{who} ran on the offline mock model — no real model answered.")
            else:
                notes.append(f"{who} could not use {wanted}" + (f" ({why})." if why else "."))
    if not notes:
        return ""
    note = notes[0]
    if len(notes) > 1:
        note = f"{note[:-1]} — {len(notes)} route changes in this mission."
    return " ".join(note.split())


def _handoff_key(data: dict[str, Any]) -> str:
    """A per-handoff id when the delegation event carries one (Track A may add
    ``handoff_id``; a tool call id is the same idea), else ``""``."""
    for key in ("handoff_id", "call_id", "tool_call_id"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def mission_view(platform: Any, session_id: str) -> dict[str, Any] | None:
    """The whole mission under coordinator ``session_id``, or None when the
    session does not exist. BLOCKING."""
    engine = platform.engine
    approvals = getattr(platform, "approvals", None)

    def _waiting(sid: str) -> dict | None:
        pending = getattr(approvals, "pending_for", None)
        if pending is None:
            return None
        try:
            rows = list(pending(sid) or [])
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        first = rows[0]
        return {"approval_id": first.get("approval_id") or first.get("id"), "tool": first.get("tool")}

    with session_scope(engine) as db:
        root = db.get(Session, session_id)
        if root is None:
            return None
        root = Session(**root.model_dump())
        members, runs_by = _walk(db, session_id)
        team_ids = [session_id, *[m.id for m in members]]
        events = list(
            db.exec(
                select(EventRecord)
                .where(
                    EventRecord.session_id.in_(team_ids),  # type: ignore[attr-defined, union-attr]
                    EventRecord.type.in_(_EVENT_TYPES),  # type: ignore[attr-defined]
                )
                # Ties on created_at break on rowid (insertion order): a
                # remote's started + completed events land in one 15.6 ms
                # Windows tick, and read completion-first they made TWO
                # cards (v1.317.0; the v1.286.0 same-tick rule).
                .order_by(
                    EventRecord.created_at.desc(),  # type: ignore[attr-defined]
                    literal_column("eventrecord.rowid").desc(),
                )
                .limit(_SCAN_ROWS)
            )
        )
        events.reverse()
        # EXPLICIT COLUMNS (v1.309.0): the log needs who/what/ok/when and the
        # redacted args — never ``output``, which is up to 4,000 chars a row
        # and was read 240 rows at a time, every 2 s, to be thrown away.
        invocations = list(
            db.exec(
                select(
                    ToolInvocation.id,
                    ToolInvocation.session_id,
                    ToolInvocation.tool,
                    ToolInvocation.args_json,
                    ToolInvocation.ok,
                    ToolInvocation.verdict,
                    ToolInvocation.undo_of,
                    ToolInvocation.created_at,
                )
                .where(ToolInvocation.session_id.in_(team_ids))  # type: ignore[attr-defined]
                .order_by(
                    ToolInvocation.created_at.desc(),  # type: ignore[attr-defined]
                    literal_column("toolinvocation.rowid").desc(),
                )
                .limit(_SCAN_ROWS)
            )
        )
        invocations.reverse()
        # WHO REFUSED each refused call (v1.309.0 review): the registry's
        # ``tool.denied`` event names it (``kind``) and points at its ledger
        # row (``invocation_id``). One bounded statement for the whole team,
        # explicit column — a refusal is never more frequent than the calls
        # it refuses, so the same window covers the invocations above.
        denied_kind: dict[str, str] = {}
        for raw in db.exec(
            select(EventRecord.payload_json)
            .where(
                EventRecord.session_id.in_(team_ids),  # type: ignore[attr-defined, union-attr]
                EventRecord.type == EventType.TOOL_DENIED,
            )
            .order_by(EventRecord.created_at.desc())  # type: ignore[attr-defined]
            .limit(_SCAN_ROWS)
        ):
            try:
                data = json.loads(raw or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("invocation_id"):
                denied_kind[str(data["invocation_id"])] = str(data.get("kind") or "")
        route_events = [
            (str(t), s, str(p or "{}"))
            for t, s, p in db.exec(
                select(EventRecord.type, EventRecord.session_id, EventRecord.payload_json)
                .where(
                    EventRecord.session_id.in_(team_ids),  # type: ignore[attr-defined, union-attr]
                    EventRecord.type.in_(_ROUTE_EVENT_TYPES),  # type: ignore[attr-defined]
                )
                .order_by(EventRecord.created_at.desc())  # type: ignore[attr-defined]
                .limit(_ROUTE_ROWS)
            )
        ]
        try:
            team_docs = _team_documents(db, {root.id: root, **{m.id: m for m in members}})
        except Exception:  # noqa: BLE001 — files are a bonus on a card
            team_docs = {}
        try:
            next_id = continued_as(db, session_id, root.created_at)
        except Exception:  # noqa: BLE001 — a missing link is "not continued"
            next_id = None

    from .roster import session_roster_name

    names: dict[str, str] = {session_id: COORDINATOR}
    roster_names: dict[str, str] = {}
    for m in members:
        rn = session_roster_name(m) or "builder"
        roster_names[m.id] = rn
        names[m.id] = display_name(rn)

    # The task each member was HANDED (the delegation event), else its row.
    # REMOTE teammates open no session; they are known only from their
    # delegation events, and since v1.309.0 (remote-teammates-invisible) each
    # HANDOFF is its own card: two jobs given to one remote are two cards,
    # each with its own task and outcome. A per-handoff key is used when the
    # event carries one; otherwise a completion settles the OLDEST open
    # handoff to that agent (the events are read oldest first).
    handed: dict[str, str] = {}
    remote_members: list[dict[str, Any]] = []
    open_remote: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev.type not in (EventType.DELEGATION_STARTED, EventType.DELEGATION_COMPLETED):
            continue
        data = _payload(ev)
        child = str(data.get("child_session_id") or "")
        agent = str(data.get("agent") or "")
        if ev.type == EventType.DELEGATION_STARTED and child:
            handed.setdefault(child, str(data.get("task") or ""))
        if not (agent.lower().startswith("remote:") and not child):
            continue
        key = _handoff_key(data)
        queue = open_remote.setdefault(key or agent, [])
        if ev.type == EventType.DELEGATION_STARTED:
            slot = {
                "agent": agent, "task": str(data.get("task") or ""), "status": "working",
                "started_at": _iso(ev.created_at), "finished_at": None,
            }
            remote_members.append(slot)
            queue.append(slot)
            continue
        if queue:
            slot = queue.pop(0)
        else:  # a completion whose start fell out of the scan window
            slot = {"agent": agent, "task": "", "started_at": None}
            remote_members.append(slot)
        slot["status"] = "done" if data.get("ok") else "failed"
        slot["result"] = _short(data.get("result") or "", _RESULT_CHARS)
        slot["finished_at"] = _iso(ev.created_at)

    last_line: dict[str, str] = {}
    lines: list[dict[str, Any]] = []
    for inv_id, inv_session, inv_tool, inv_args, inv_ok, inv_verdict, inv_undo, inv_at in invocations:
        tool = inv_tool or ""
        if inv_undo or tool in _QUIET_TOOLS:
            continue
        who = names.get(inv_session, "Agent")
        verdict = str(getattr(inv_verdict, "value", inv_verdict) or "").lower()
        kind = denied_kind.get(str(inv_id or "")) if verdict == "ask" and not inv_ok else None
        if kind in _ASK_DENIAL_KINDS:
            # A DENIED ASK IS NOT A FAILURE (v1.309.0, contract 5): the call
            # needed the user's OK and did not get it (nobody could ask, the
            # user said no, or the pause timed out). "Builder could not write
            # report.md" read as the agent failing at the work; it was
            # waiting on a person.
            text, tone = f"needed your OK to use {tool.replace('_', ' ')}", "ask"
        elif kind is not None:
            # …and a refusal NOBODY was asked about is never worded as one
            # (v1.309.0 review, honest copy): the breaker, the roster gate
            # and low trust share the ``ask`` verdict but asked no one.
            words = _REFUSAL_WORDS.get(kind, "was not allowed to use {t}")
            text, tone = words.format(t=tool.replace("_", " ")), "warn"
        else:
            # No ``tool.denied`` event = not a refusal: an ``ask`` call the
            # user APPROVED whose tool then failed reads as the failure it is
            # (as does a refusal whose event was pruned — never "needed your
            # OK" on a guess).
            try:
                args = json.loads(inv_args or "{}")
            except (TypeError, ValueError):
                args = {}
            text = tool_line(tool, args if isinstance(args, dict) else {}, bool(inv_ok))
            tone = "ok" if inv_ok else "warn"
        last_line[inv_session] = text
        lines.append({
            "at": _iso(inv_at), "who": who, "session_id": inv_session,
            "text": f"{who} {text}", "tone": tone,
        })
    for ev in events:
        data = _payload(ev)
        who = names.get(ev.session_id or "", "Agent")
        text = ""
        tone = "info"
        if ev.type == EventType.DELEGATION_STARTED:
            agent = display_name(str(data.get("agent") or ""))
            # "gave X a task: …" reads right whatever the task's first word is
            # ("asked X to Industry research…" did not).
            text = f"{who} gave {agent} a task: {_short(data.get('task') or 'help', 120)}"
        elif ev.type == EventType.DELEGATION_COMPLETED:
            agent = display_name(str(data.get("agent") or ""))
            if data.get("ok"):
                text, tone = f"{agent} finished and reported back to {who}", "ok"
            else:
                text, tone = f"{agent} could not finish: {_short(data.get('result') or 'no reason given', 140)}", "warn"
        elif ev.type == EventType.PLAN_CREATED:
            steps = data.get("steps")
            n = len(steps) if isinstance(steps, list) else 0
            text = f"{who} broke the work into {n} step{'s' if n != 1 else ''}"
        elif ev.type == EventType.PLAN_STEP_STARTED:
            text = f"{who} is on step {int(data.get('index') or 0) + 1}: {_short(data.get('goal') or '', 100)}"
        elif ev.type == EventType.APPROVAL_REQUESTED:
            tool = str(data.get("tool") or "a tool")
            text, tone = f"{who} is waiting for your OK to use {tool.replace('_', ' ')}", "ask"
        elif ev.type == EventType.APPROVAL_RESOLVED:
            decision = str(data.get("decision") or "")
            if decision == "deny":
                text, tone = f"You declined {who}'s request", "warn"
            elif decision == "timeout":
                text, tone = f"{who}'s request went unanswered", "warn"
            else:
                text = f"You approved {who}'s request"
        elif ev.type == EventType.SESSION_COMPLETED and ev.session_id == session_id:
            status = str(data.get("status") or "")
            if status == "completed":
                text, tone = f"{COORDINATOR} finished the objective", "ok"
            elif status == "cancelled":
                text, tone = "The objective was stopped", "warn"
            else:
                text, tone = f"{COORDINATOR} could not finish the objective", "warn"
        if text:
            lines.append({
                "at": _iso(ev.created_at), "who": who, "session_id": ev.session_id,
                "text": text, "tone": tone,
            })
    lines.sort(key=lambda r: r.get("at") or "")
    lines = lines[-_ACTIVITY_MAX:]

    out_members: list[dict[str, Any]] = []
    documents: list[str] = []
    for m in members:
        runs = runs_by.get(m.id, [])
        waiting = _waiting(m.id)
        status = _status(m, runs, waiting is not None)
        steps = sum(int(r.steps or 0) for r in runs)
        plan = _plan_counts(events, m.id)
        files = list(team_docs.get(m.id) or [])
        for f in files:
            if f not in documents:
                documents.append(f)
        rn = roster_names.get(m.id, "builder")
        out_members.append({
            "session_id": m.id,
            "agent": rn,
            "name": names.get(m.id, "Agent"),
            "kind": member_kind(rn),
            "task": _short(handed.get(m.id) or m.task, _TASK_CHARS),
            "status": status,
            "progress": progress_for(status, steps, plan),
            "activity": last_line.get(m.id, ""),
            "waiting_on": waiting,
            "steps": steps,
            "result": _short(m.summary, _RESULT_CHARS) if status in ("done", "failed", "cancelled") else "",
            "files": files[:20],
            # WHICH MODEL DID THIS PART (v1.309.0, mission-no-model-disclosure):
            # the member's own row, as the coordinator's block already says.
            "provider": m.provider,
            "model": m.model,
            "started_at": _iso(m.created_at),
            "finished_at": _iso(m.finished_at),
        })
    for rec in remote_members:
        st = rec["status"]
        out_members.append({
            "session_id": None,
            "agent": rec["agent"],
            "name": display_name(rec["agent"]),
            "kind": "remote",
            "task": _short(rec.get("task") or "", _TASK_CHARS),
            "status": st,
            "progress": progress_for(st, 0, (0, 0, "")),
            "activity": "",
            "waiting_on": None,
            "steps": 0,
            "result": rec.get("result", ""),
            "files": [],
            # A remote teammate runs on ITS machine's model — this app cannot
            # name it, so it says nothing rather than guess.
            "provider": None,
            "model": None,
            "started_at": rec.get("started_at"),
            "finished_at": rec.get("finished_at"),
        })

    root_runs = runs_by.get(session_id, [])
    root_waiting = _waiting(session_id)
    root_status = _status(root, root_runs, root_waiting is not None)
    for f in team_docs.get(session_id) or []:
        if str(f) not in documents:
            documents.insert(0, str(f))

    worklist = None
    store = getattr(platform, "worklist", None)
    if store is not None:
        try:
            summary = store.summary(store.root_session_for(session_id))
            if int(summary.get("total") or 0) > 0:
                worklist = summary
        except Exception:  # noqa: BLE001
            worklist = None

    finished = sum(1 for m in out_members if m["status"] == "done")
    return {
        "found": True,
        "session": {
            "id": root.id,
            "task": root.task,
            # The user's own words (v1.309.0): a continuation's ``task`` is
            # the model-facing recap, which stays untouched above.
            "objective": display_objective(root),
            "status": root.status.value,
            "outcome": getattr(root, "outcome", None) or None,
            "agent_type": root.agent_type.value,
            "project_id": root.project_id,
            "provider": root.provider,
            "model": root.model,
            # A RESTART CUT IT OFF (v1.309.0): the boot reconcile's tag, so
            # the screen can offer Continue instead of a bare "failed".
            "interrupted": getattr(root, "interrupted_at", None) is not None,
            # …and once continued, where the work went on (the newest
            # continuation), so the old screen links forward, not back.
            "continued_as": next_id,
            "route_note": _route_note(route_events, names),
            "created_at": _iso(root.created_at),
            "finished_at": _iso(root.finished_at),
        },
        "coordinator": {
            "name": COORDINATOR,
            "status": root_status,
            "waiting_on": root_waiting,
            "steps": sum(int(r.steps or 0) for r in root_runs),
        },
        "members": out_members,
        "progress": {"done": finished, "total": len(out_members)},
        "activity": lines,
        "deliverable": {
            # The coordinator's own answer, once it HAS one. A running
            # session's summary is only the create-time folder note, if any.
            "text": (
                root.summary
                if root.status
                in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
                else ""
            ),
            "documents": documents[:30],
            "worklist": worklist,
        },
    }


def missions_waiting(platform: Any, db, root_ids: list[str]) -> set[str]:
    """The mission roots (of ``root_ids``) parked on an ask — their own, or
    any delegated member's (v1.309.0, mission-list-says-working-while-blocked,
    contract 6). The Recent list said "working" while the whole team sat on a
    question for the user.

    Bounded for a list: nothing at all is read when no ask is pending
    anywhere, and otherwise one statement per delegation DEPTH (``_MAX_DEPTH``)
    for every row together — never the full ``_walk`` per row. Asks are
    filed under the asking run's OWN session id (a member's under the
    child's), so each level's session ids are checked against the one
    in-memory approvals registry."""
    approvals = getattr(platform, "approvals", None)
    pending_for = getattr(approvals, "pending_for", None)
    if pending_for is None or not root_ids:
        return set()
    try:
        if int(approvals.pending_count()) == 0:
            return set()
    except Exception:  # noqa: BLE001 — no count = look anyway
        pass

    def _asks(sid: str) -> bool:
        try:
            return bool(pending_for(sid))
        except Exception:  # noqa: BLE001
            return False

    waiting = {rid for rid in root_ids if _asks(rid)}
    # session id -> its mission root, level by level down the run links.
    root_of: dict[str, str] = {rid: rid for rid in root_ids}
    frontier = list(
        db.exec(
            select(AgentRun.id, AgentRun.session_id).where(
                AgentRun.session_id.in_([r for r in root_ids if r not in waiting])  # type: ignore[attr-defined]
            )
        )
    )
    for _depth in range(_MAX_DEPTH):
        run_root = {run_id: root_of.get(sid, "") for run_id, sid in frontier}
        if not run_root:
            break
        children = list(
            db.exec(
                select(AgentRun.id, AgentRun.session_id, AgentRun.parent_id).where(
                    AgentRun.parent_id.in_(list(run_root))  # type: ignore[attr-defined, union-attr]
                )
            )
        )
        frontier = []
        for run_id, sid, parent in children:
            rid = run_root.get(parent or "", "")
            if not rid or rid in waiting or not sid:
                continue
            if sid not in root_of:  # a session with several runs is asked once
                root_of[sid] = rid
                if _asks(sid):
                    waiting.add(rid)
                    continue
            frontier.append((run_id, sid))
    return waiting
