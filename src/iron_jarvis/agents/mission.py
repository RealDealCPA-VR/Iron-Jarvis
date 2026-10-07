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
  files the whole team created or changed (``outcome.session_result`` per
  member — journaled mutations, never the prose).

BLOCKING (SQLite reads): the route calls it from FastAPI's threadpool. Never
raises past a missing session (``None``); every sub-read degrades to less.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.models import AgentRun, AgentState, EventRecord, Session, SessionStatus, ToolInvocation

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


def _walk(db, root_id: str) -> tuple[list[Session], dict[str, list[AgentRun]]]:
    """``(member sessions in discovery order, runs by session id)`` — the
    delegation tree under ``root_id`` (root included in ``runs``)."""
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
        next_ids: list[str] = []
        for r in linked:
            sid = r.session_id
            if not sid or sid in seen:
                continue
            seen.add(sid)
            row = db.get(Session, sid)
            if row is None:
                continue
            members.append(Session(**row.model_dump()))
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
                .order_by(EventRecord.created_at.desc())  # type: ignore[attr-defined]
                .limit(_SCAN_ROWS)
            )
        )
        events.reverse()
        invocations = list(
            db.exec(
                select(ToolInvocation)
                .where(ToolInvocation.session_id.in_(team_ids))  # type: ignore[attr-defined]
                .order_by(ToolInvocation.created_at.desc())  # type: ignore[attr-defined]
                .limit(_SCAN_ROWS)
            )
        )
        invocations.reverse()

    from .roster import session_roster_name

    names: dict[str, str] = {session_id: COORDINATOR}
    roster_names: dict[str, str] = {}
    for m in members:
        rn = session_roster_name(m) or "builder"
        roster_names[m.id] = rn
        names[m.id] = display_name(rn)

    # The task each member was HANDED (the delegation event), else its row.
    handed: dict[str, str] = {}
    remote_members: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev.type not in (EventType.DELEGATION_STARTED, EventType.DELEGATION_COMPLETED):
            continue
        data = _payload(ev)
        child = str(data.get("child_session_id") or "")
        agent = str(data.get("agent") or "")
        if ev.type == EventType.DELEGATION_STARTED and child:
            handed.setdefault(child, str(data.get("task") or ""))
        if agent.lower().startswith("remote:") and not child:
            key = agent
            slot = remote_members.setdefault(
                key, {"agent": agent, "task": str(data.get("task") or ""), "status": "working"}
            )
            if ev.type == EventType.DELEGATION_COMPLETED:
                slot["status"] = "done" if data.get("ok") else "failed"
                slot["result"] = _short(data.get("result") or "", _RESULT_CHARS)

    last_line: dict[str, str] = {}
    lines: list[dict[str, Any]] = []
    for inv in invocations:
        if inv.undo_of or inv.tool in _QUIET_TOOLS:
            continue
        who = names.get(inv.session_id, "Agent")
        text = tool_line(inv.tool or "", _args(inv), bool(inv.ok))
        last_line[inv.session_id] = text
        lines.append({
            "at": _iso(inv.created_at), "who": who, "session_id": inv.session_id,
            "text": f"{who} {text}", "tone": "ok" if inv.ok else "warn",
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

    from .outcome import session_result

    out_members: list[dict[str, Any]] = []
    documents: list[str] = []
    for m in members:
        runs = runs_by.get(m.id, [])
        waiting = _waiting(m.id)
        status = _status(m, runs, waiting is not None)
        steps = sum(int(r.steps or 0) for r in runs)
        plan = _plan_counts(events, m.id)
        files: list[str] = []
        try:
            res = session_result(engine, m.id)
            files = [str(p) for p in (res.get("documents") or [])]
        except Exception:  # noqa: BLE001 — files are a bonus on a card
            files = []
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
            "started_at": _iso(m.created_at),
            "finished_at": _iso(m.finished_at),
        })
    for rec in remote_members.values():
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
            "started_at": None,
            "finished_at": None,
        })

    root_runs = runs_by.get(session_id, [])
    root_waiting = _waiting(session_id)
    root_status = _status(root, root_runs, root_waiting is not None)
    try:
        root_res = session_result(engine, session_id)
        for f in root_res.get("documents") or []:
            if str(f) not in documents:
                documents.insert(0, str(f))
    except Exception:  # noqa: BLE001
        pass

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
            "status": root.status.value,
            "outcome": getattr(root, "outcome", None) or None,
            "agent_type": root.agent_type.value,
            "project_id": root.project_id,
            "provider": root.provider,
            "model": root.model,
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
