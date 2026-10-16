"""Everything › Status — the system map, in ONE read (v1.334.0).

The Status tab used to be a second directory (a module grid, saved tasks,
recent sessions, a connections list) plus eight polls. It is now one picture:
every part of Iron Jarvis as a star, grouped like the Directory, each saying
whether it is SET UP, USED in the last 7 days, or IN USE right now. This
module computes that picture, plus the status line's own counts and the day's
heartbeat, so the page makes one slow poll instead of eight.

COST RULES (the user's 2026-10-11 ask: the app must not eat the PC). Every
number here is an in-memory read or one SQL aggregate over an indexed range.
Nothing spawns a process, touches the network, walks a folder or loads every
row of a table:
  * no ``/memory/overview`` (it globs note vaults) — a grouped count instead;
  * no ``/artifacts`` listing (it walks the artifact store) — a distinct count;
  * no ``/usage`` (it reads OpenCode's and Pi's own stores) — AgentRun sums;
  * no fleet ``sample_once``/``touch`` — the sampler's cached listing only;
  * providers come from ``d._visible_providers()``, the same call ``/health``
    makes every 5 s (its CLI probe is cached and rate-limited there).

Every star is computed in its own ``try``: a store that cannot be read shows
as "not set up" rather than failing the map, and the endpoint never raises.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlmodel import select

from ...core.db import session_scope
from ...core.ids import utcnow

#: The window "used" means.
USED_DAYS = 7
#: Heartbeat buckets (hours, the last one being the current hour).
HEARTBEAT_HOURS = 24

#: The constellations, in the Directory's words.
AREAS: list[tuple[str, str]] = [
    ("work", "Work"),
    ("automations", "Automations"),
    ("system", "System"),
    ("knowledge", "Knowledge"),
    ("setup", "Setup"),
]


def _node(
    key: str,
    label: str,
    href: str,
    *,
    setup: int = 0,
    used: int = 0,
    live: int = 0,
    unit: str = "",
    detail: str = "",
    attention: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "href": href,
        "setup": max(0, int(setup)),
        "used": max(0, int(used)),
        "live": max(0, int(live)),
        "unit": unit,
        "detail": detail,
        "attention": attention,
    }


def _count(db, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def _active_session_count(db, *where) -> int:
    from ...core.models import Session, SessionStatus

    stmt = select(func.count()).select_from(Session).where(
        Session.status.in_([SessionStatus.ACTIVE, SessionStatus.QUEUED])  # type: ignore[attr-defined]
    )
    for w in where:
        stmt = stmt.where(w)
    return _count(db, stmt)


def _event_count(db, types: tuple[str, ...], since) -> int:
    from ...core.models import EventRecord

    return _count(
        db,
        select(func.count())
        .select_from(EventRecord)
        .where(EventRecord.created_at >= since)
        .where(EventRecord.type.in_(types)),  # type: ignore[attr-defined]
    )


# --------------------------------------------------------------------- stars


def _work(d, db, since) -> list[dict[str, Any]]:
    from ...core.models import ChatThreadRecord, LiveDocRecord, Project, SavedPromptRecord, Session

    out: list[dict[str, Any]] = []

    def star(fn: Callable[[], dict[str, Any]], fallback: dict[str, Any]) -> None:
        try:
            out.append(fn())
        except Exception:  # noqa: BLE001 — an unreadable store is "not set up"
            out.append(fallback)

    def projects() -> dict[str, Any]:
        total = _count(db, select(func.count()).select_from(Project).where(Project.status == "active"))
        touched = set(
            db.exec(
                select(Session.project_id).where(Session.created_at >= since).where(Session.project_id.is_not(None))  # type: ignore[union-attr]
            )
        ) | set(
            db.exec(
                select(ChatThreadRecord.project_id)
                .where(ChatThreadRecord.updated_at >= since)
                .where(ChatThreadRecord.project_id.is_not(None))  # type: ignore[union-attr]
            )
        )
        live = _active_session_count(db, Session.project_id.is_not(None))  # type: ignore[union-attr]
        return _node(
            "projects", "Projects", "/projects", setup=total, used=len(touched), live=live, unit="project",
            detail="Folders Jarvis works in, each with its own chats, tasks and team.",
        )

    def chats() -> dict[str, Any]:
        from ...core.turns import CHAT_INFLIGHT

        total = _count(
            db, select(func.count()).select_from(ChatThreadRecord).where(ChatThreadRecord.archived_at.is_(None))  # type: ignore[union-attr]
        )
        used = _count(
            db,
            select(func.count())
            .select_from(ChatThreadRecord)
            .where(ChatThreadRecord.updated_at >= since)
            .where(ChatThreadRecord.archived_at.is_(None)),  # type: ignore[union-attr]
        )
        return _node(
            "chats", "Chats", "/chat", setup=total, used=used, live=CHAT_INFLIGHT.count(), unit="chat",
            detail="Your conversations with Jarvis.",
        )

    def agents() -> dict[str, Any]:
        from ...core.models import AgentType

        custom = 0
        try:
            custom = len(d.platform.agents_registry.list())
        except Exception:  # noqa: BLE001
            pass
        used = _count(
            db,
            select(func.count()).select_from(Session).where(Session.created_at >= since).where(Session.origin == "job:mission"),
        )
        live = _active_session_count(db, Session.origin == "job:mission")
        return _node(
            "agents", "Agents", "/agents", setup=len(list(AgentType)) + custom, used=used, live=live, unit="agent",
            detail="Your team. Give Jarvis one objective and the team works it.",
        )

    def panes() -> dict[str, Any]:
        rows = list(d.platform.terminals.list())
        alive = [r for r in rows if r.get("alive") is not False]
        busy = sum(1 for r in alive if r.get("state") in ("working", "blocked"))
        return _node(
            "build", "Build", "/terminals", setup=len(alive), used=busy, live=busy, unit="pane",
            detail=f"{len(alive)} open pane{'s' if len(alive) != 1 else ''}; a pane counts as in use while its program works.",
        )

    def documents() -> dict[str, Any]:
        total = _count(db, select(func.count()).select_from(LiveDocRecord))
        used = _count(db, select(func.count()).select_from(LiveDocRecord).where(LiveDocRecord.updated_at >= since))
        return _node(
            "documents", "Documents", "/documents", setup=total, used=used, unit="live document",
            detail="Documents Jarvis keeps up to date for you.",
        )

    def templates() -> dict[str, Any]:
        total = _count(db, select(func.count()).select_from(SavedPromptRecord))
        return _node(
            "templates", "Templates", "/templates", setup=total, unit="template",
            detail="Saved tasks you can start again in one press.",
        )

    def creative() -> dict[str, Any]:
        from ...artifacts.models import ArtifactRecord
        from ...creative.service import _MEDIA_KINDS

        base = select(func.count(func.distinct(ArtifactRecord.name))).where(
            ArtifactRecord.kind.in_(sorted(_MEDIA_KINDS))  # type: ignore[attr-defined]
        )
        return _node(
            "creative", "Creative", "/creative", setup=_count(db, base),
            used=_count(db, base.where(ArtifactRecord.created_at >= since)), unit="piece",
            detail="Images, video and audio made here.",
        )

    for fn, key, label, href in (
        (projects, "projects", "Projects", "/projects"),
        (chats, "chats", "Chats", "/chat"),
        (agents, "agents", "Agents", "/agents"),
        (panes, "build", "Build", "/terminals"),
        (documents, "documents", "Documents", "/documents"),
        (creative, "creative", "Creative", "/creative"),
        (templates, "templates", "Templates", "/templates"),
    ):
        star(fn, _node(key, label, href))
    return out


def _automations(d, db, since) -> list[dict[str, Any]]:
    from ...core.models import Session

    out: list[dict[str, Any]] = []

    def star(fn: Callable[[], dict[str, Any]], key: str, label: str, href: str) -> None:
        try:
            out.append(fn())
        except Exception:  # noqa: BLE001
            out.append(_node(key, label, href))

    def schedules() -> dict[str, Any]:
        from ...scheduling.models import ScheduledTaskRecord as S

        enabled = _count(db, select(func.count()).select_from(S).where(S.enabled == True))  # noqa: E712
        used = _count(db, select(func.count()).select_from(S).where(S.last_run >= since))  # type: ignore[operator]
        failing = _count(
            db,
            select(func.count()).select_from(S).where(S.enabled == True).where(S.last_status == "failed"),  # noqa: E712
        )
        live = _active_session_count(db, Session.origin.like("schedule:%"))  # type: ignore[union-attr]
        return _node(
            "schedules", "Schedules", "/schedules", setup=enabled, used=used, live=live, unit="schedule",
            detail="Work handed to an agent on a clock.",
            attention=f"{failing} schedule{'s' if failing != 1 else ''} failed on the last run" if failing else None,
        )

    def workflows() -> dict[str, Any]:
        from ...workflows.models import WorkflowRecord, WorkflowRunRecord

        total = _count(db, select(func.count()).select_from(WorkflowRecord))
        used = _count(db, select(func.count()).select_from(WorkflowRunRecord).where(WorkflowRunRecord.started_at >= since))
        live = _count(
            db,
            select(func.count())
            .select_from(WorkflowRunRecord)
            .where(WorkflowRunRecord.status.in_(["running", "waiting", "cancelling", "resuming"])),  # type: ignore[attr-defined]
        )
        return _node(
            "workflows", "Workflows", "/workflows", setup=total, used=used, live=live, unit="workflow",
            detail="Multi-step work wired from agents.",
        )

    def reflexes() -> dict[str, Any]:
        from ...reflex.models import ReflexRule

        enabled = _count(db, select(func.count()).select_from(ReflexRule).where(ReflexRule.enabled == True))  # noqa: E712
        return _node(
            "reflexes", "Reflexes", "/reflex", setup=enabled, used=_event_count(db, ("reflex.fired",), since),
            unit="reflex", detail="Something happens, Jarvis acts — on its own.",
        )

    def sentinels() -> dict[str, Any]:
        from ...motivation.models import ProposalRecord
        from ...sentinels.models import SentinelRecord

        on = bool(getattr(d.platform.config, "sentinels_enabled", True))
        enabled = (
            _count(db, select(func.count()).select_from(SentinelRecord).where(SentinelRecord.enabled == True))  # noqa: E712
            if on
            else 0
        )
        used = _count(
            db,
            select(func.count())
            .select_from(ProposalRecord)
            .where(ProposalRecord.source == "sentinel")
            .where(ProposalRecord.created_at >= since),
        )
        erroring = _count(
            db,
            select(func.count())
            .select_from(SentinelRecord)
            .where(SentinelRecord.enabled == True)  # noqa: E712
            .where(SentinelRecord.last_error.is_not(None))  # type: ignore[union-attr]
            .where(SentinelRecord.last_error != ""),
        ) if on else 0
        return _node(
            "sentinels", "Sentinels", "/sentinels", setup=enabled, used=used, unit="watcher",
            detail="Always-on watchers that suggest, never act.",
            attention=f"{erroring} watcher{'s' if erroring != 1 else ''} cannot check" if erroring else None,
        )

    def goals() -> dict[str, Any]:
        from ...goals.models import GoalContractRecord as G
        from ...motivation.models import GoalRecord

        active = _count(db, select(func.count()).select_from(G).where(G.state == "active"))
        active += _count(db, select(func.count()).select_from(GoalRecord).where(GoalRecord.status == "active"))
        used = _count(db, select(func.count()).select_from(G).where(G.last_run_at >= since))  # type: ignore[operator]
        used += _count(db, select(func.count()).select_from(GoalRecord).where(GoalRecord.last_acted_at >= since))  # type: ignore[operator]
        tripped = _count(db, select(func.count()).select_from(G).where(G.state.in_(["tripped", "failed"])))  # type: ignore[attr-defined]
        live = _active_session_count(db, Session.origin.like("goal:%")) + _active_session_count(  # type: ignore[union-attr]
            db, Session.origin == "autonomy"
        )
        return _node(
            "goals", "Autonomy", "/autonomy", setup=active, used=used, live=live, unit="active goal",
            detail="What Jarvis wants to do on its own, and the switch that stops it.",
            attention=f"{tripped} goal{'s' if tripped != 1 else ''} stopped by its safety breaker" if tripped else None,
        )

    def webhooks() -> dict[str, Any]:
        from ...webhooks.models import WebhookRecord

        enabled = _count(db, select(func.count()).select_from(WebhookRecord).where(WebhookRecord.enabled == True))  # noqa: E712
        return _node(
            "webhooks", "Webhooks", "/webhooks", setup=enabled, used=_event_count(db, ("webhook.received",), since),
            unit="webhook", detail="Other apps knocking on Jarvis's door, and Jarvis knocking on theirs.",
        )

    for fn, key, label, href in (
        (schedules, "schedules", "Schedules", "/schedules"),
        (workflows, "workflows", "Workflows", "/workflows"),
        (goals, "goals", "Autonomy", "/autonomy"),
        (reflexes, "reflexes", "Reflexes", "/reflex"),
        (sentinels, "sentinels", "Sentinels", "/sentinels"),
        (webhooks, "webhooks", "Webhooks", "/webhooks"),
    ):
        star(fn, key, label, href)
    return out


def _knowledge(d, db, since) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def star(fn: Callable[[], dict[str, Any]], key: str, label: str, href: str) -> None:
        try:
            out.append(fn())
        except Exception:  # noqa: BLE001
            out.append(_node(key, label, href))

    def memory() -> dict[str, Any]:
        from ...memory.models import MemoryRecord

        total = _count(db, select(func.count()).select_from(MemoryRecord))
        used = _count(db, select(func.count()).select_from(MemoryRecord).where(MemoryRecord.created_at >= since))
        return _node(
            "memory", "Memory", "/memory", setup=total, used=used, unit="memory",
            detail="Working notes, lessons learned and long-term facts. Used = new this week.",
        )

    def skills() -> dict[str, Any]:
        from ...skills.learning_models import SkillStatRecord as K

        total = len(d.platform.skills.list())
        used = _count(
            db,
            select(func.count()).select_from(K).where((K.last_used_at >= since) | (K.last_injected_at >= since)),  # type: ignore[operator]
        )
        return _node(
            "skills", "Skills", "/skills", setup=total, used=used, unit="skill",
            detail="Know-how your agents call on.",
        )

    def artifacts() -> dict[str, Any]:
        from ...artifacts.models import ArtifactRecord
        from ...codelab.models import CodeArtifactRecord

        total = _count(db, select(func.count(func.distinct(ArtifactRecord.name))))
        total += _count(db, select(func.count()).select_from(CodeArtifactRecord))
        used = _count(
            db, select(func.count(func.distinct(ArtifactRecord.name))).where(ArtifactRecord.created_at >= since)
        )
        used += _count(
            db, select(func.count()).select_from(CodeArtifactRecord).where(CodeArtifactRecord.last_run_at >= since)  # type: ignore[operator]
        )
        return _node(
            "artifacts", "Artifacts", "/artifacts", setup=total, used=used, unit="artifact",
            detail="Files and code your agents made.",
        )

    def you() -> dict[str, Any]:
        from ...profile.store import ProfileStore

        prof = ProfileStore(d.platform.engine).get()
        filled = any(str(getattr(prof, f, "") or "").strip() for f in ("about", "tone", "writing_style"))
        on = bool(getattr(prof, "enabled", True))
        updated = getattr(prof, "updated_at", None)
        # SQLite hands back a NAIVE datetime (stored as UTC); `since` is aware.
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        used = 1 if updated is not None and updated >= since and filled else 0
        return _node(
            "you", "You", "/you", setup=1 if (filled and on) else 0, used=used,
            detail="Who you are and how you like answers — applied to every model.",
        )

    for fn, key, label, href in (
        (memory, "memory", "Memory", "/memory"),
        (skills, "skills", "Skills", "/skills"),
        (artifacts, "artifacts", "Artifacts", "/artifacts"),
        (you, "you", "You", "/you"),
    ):
        star(fn, key, label, href)
    return out


def _system(d, db, since) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from ...core.models import AgentRun, EventRecord, Session, SessionStatus

    out: list[dict[str, Any]] = []
    vitals: dict[str, Any] = {}

    def star(fn: Callable[[], dict[str, Any]], key: str, label: str, href: str) -> None:
        try:
            out.append(fn())
        except Exception:  # noqa: BLE001
            out.append(_node(key, label, href))

    def sessions() -> dict[str, Any]:
        runs = _count(db, select(func.count()).select_from(Session).where(Session.created_at >= since))
        ok = _count(
            db,
            select(func.count())
            .select_from(Session)
            .where(Session.created_at >= since)
            .where(Session.status == SessionStatus.COMPLETED),
        )
        done = _count(
            db,
            select(func.count())
            .select_from(Session)
            .where(Session.created_at >= since)
            .where(
                Session.status.in_(  # type: ignore[attr-defined]
                    [SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED]
                )
            ),
        )
        failed = done - ok
        vitals["runs_7d"] = runs
        vitals["runs_ok_7d"] = ok
        # The share that went well is of FINISHED runs: one still going is
        # neither a success nor a failure yet.
        vitals["runs_done_7d"] = done
        return _node(
            "sessions", "Sessions", "/sessions", setup=_count(db, select(func.count()).select_from(Session)),
            used=runs, live=_active_session_count(db), unit="run",
            detail=f"Every agent run. This week: {runs} started, {ok} finished cleanly"
            + (f", {failed} did not." if failed > 0 else "."),
        )

    def activity() -> dict[str, Any]:
        events = _count(db, select(func.count()).select_from(EventRecord).where(EventRecord.created_at >= since))
        return _node(
            "activity", "Activity", "/activity", setup=1, used=events,
            detail="Every action, tool and decision, replayable.",
        )

    def usage() -> dict[str, Any]:
        row = db.exec(
            select(
                func.count(),
                func.coalesce(func.sum(AgentRun.input_tokens), 0),
                func.coalesce(func.sum(AgentRun.output_tokens), 0),
            ).where(AgentRun.created_at >= since)
        ).one()
        calls, tin, tout = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
        tokens = tin + tout
        return _node(
            "usage", "Usage", "/usage", setup=1, used=calls,
            detail=f"{tokens:,} tokens across {calls} model run{'s' if calls != 1 else ''} this week.",
        )

    def fleet() -> dict[str, Any]:
        listing = list(d.fleet_sampler.listing())
        online = 0
        busy = 0
        for snap, _checked in listing:
            if getattr(snap, "status", "") == "online":
                online += 1
                metrics = getattr(snap, "metrics", None)
                if metrics is not None and (getattr(metrics, "requests_running", 0) or 0) > 0:
                    busy += 1
        return _node(
            "fleet", "Local fleet", "/fleet", setup=len(listing), used=online, live=busy, unit="server",
            detail=f"{online} of {len(listing)} model server{'s' if len(listing) != 1 else ''} reachable.",
        )

    def self_dev() -> dict[str, Any]:
        from ...motivation.models import ProposalRecord

        pending = _count(
            db, select(func.count()).select_from(ProposalRecord).where(ProposalRecord.status == "pending")
        )
        return _node(
            "self-dev", "Self-development", "/self-dev", setup=1,
            used=_count(db, select(func.count()).select_from(ProposalRecord).where(ProposalRecord.created_at >= since)),
            detail=f"{pending} suggestion{'s' if pending != 1 else ''} waiting for your review." if pending else
            "Ideas Jarvis has for improving itself — every change review-gated.",
        )

    for fn, key, label, href in (
        (sessions, "sessions", "Sessions", "/sessions"),
        (activity, "activity", "Activity", "/activity"),
        (usage, "usage", "Usage", "/usage"),
        (fleet, "fleet", "Local fleet", "/fleet"),
        (self_dev, "self-dev", "Self-development", "/self-dev"),
    ):
        star(fn, key, label, href)
    return out, vitals


def _setup(d, db, since, failing_packs: list[dict[str, str]]) -> list[dict[str, Any]]:
    from ...core.models import ToolInvocation

    out: list[dict[str, Any]] = []

    def star(fn: Callable[[], dict[str, Any]], key: str, label: str, href: str) -> None:
        try:
            out.append(fn())
        except Exception:  # noqa: BLE001
            out.append(_node(key, label, href))

    def models() -> dict[str, Any]:
        from ...core.models import AgentRun

        rows = [r for r in d._visible_providers() if r.get("class") != "mock"]
        ready = [r for r in rows if r.get("available")]
        cfg = d.platform.config
        default = str(getattr(cfg, "default_provider", "") or "")
        calls = _count(
            db,
            select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= since).where(AgentRun.provider != "mock"),
        )
        live = _active_session_count(db)  # a run in flight is a model in use
        names = ", ".join(str(r.get("provider")) for r in ready[:4]) or "none yet"
        demo = default in ("", "mock")
        return _node(
            "models", "Models", "/connections", setup=len(ready), used=calls, live=1 if live > 0 and not demo else 0,
            unit="model",
            detail=(f"Ready: {names}. " + ("No model chosen for answers yet — replies are a demo." if demo else
                    f"Answers come from {default}.")),
        )

    def tools() -> dict[str, Any]:
        from ...mcp.tools import load_status

        packs = [s for s in (getattr(d.platform.config, "mcp_servers", None) or []) if isinstance(s, dict)]
        ready = 0
        for s in packs:
            st = load_status(str(s.get("name") or "")) or {}
            # v1.342.0: the load record writes "ok" (was "ready").
            if st.get("state") == "ok":
                ready += 1
        custom = 0
        try:
            custom = len(d.platform.tools_registry.list())
        except Exception:  # noqa: BLE001
            pass
        used = _count(
            db,
            select(func.count())
            .select_from(ToolInvocation)
            .where(ToolInvocation.created_at >= since)
            .where(ToolInvocation.tool.like("mcp\\_\\_%", escape="\\")),  # type: ignore[attr-defined]
        )
        names = ", ".join(p["name"] for p in failing_packs)
        return _node(
            "tools", "Tools", "/tools", setup=len(packs) + custom, used=used, live=0, unit="pack",
            detail=f"{ready} of {len(packs)} tool pack{'s' if len(packs) != 1 else ''} running"
            + (f", plus {custom} custom tool{'s' if custom != 1 else ''}." if custom else "."),
            attention=f"Didn't start: {names}" if failing_packs else None,
        )

    def browser() -> dict[str, Any]:
        from .system import _browser_health

        b = _browser_health(d) or {}
        used = _count(
            db,
            select(func.count())
            .select_from(ToolInvocation)
            .where(ToolInvocation.created_at >= since)
            .where(ToolInvocation.tool.like("browser\\_%", escape="\\")),  # type: ignore[attr-defined]
        )
        connected = bool(b.get("connected"))
        return _node(
            "browser", "Browser", "/computeruse", setup=1 if (b.get("paired") or connected) else 0, used=used,
            live=1 if connected else 0,
            detail="Your own Chrome, connected." if connected else
            ("Paired — Chrome is not connected right now." if b.get("paired") else "Not paired with Chrome yet."),
        )

    def channels() -> dict[str, Any]:
        configured = (getattr(d.platform.config, "comm", None) or {}).get("channels") or {}
        n = len(configured) if hasattr(configured, "__len__") else 0
        return _node(
            "channels", "Notifications", "/channels", setup=n, used=_event_count(db, ("comm.received",), since),
            unit="channel", detail="Where Jarvis reaches you, and where you can reach it.",
        )

    def connections() -> dict[str, Any]:
        rows = d.platform.connections.status()
        connected = sum(1 for r in rows if r.get("connected"))
        return _node(
            "directory", "Directory", "/marketplace", setup=connected, unit="app",
            detail="Apps you connected — Notion, GitHub, Slack and more.",
        )

    for fn, key, label, href in (
        (models, "models", "Models", "/connections"),
        (tools, "tools", "Tools", "/tools"),
        (browser, "browser", "Browser", "/computeruse"),
        (channels, "channels", "Notifications", "/channels"),
        (connections, "directory", "Directory", "/marketplace"),
    ):
        star(fn, key, label, href)
    return out


# ------------------------------------------------------------------- status


def _status(d, db) -> dict[str, Any]:
    out: dict[str, Any] = {"running": 0, "failing_loops": [], "failing_packs": [], "provider_failures_24h": 0}
    try:
        out["running"] = _active_session_count(db)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["failing_loops"] = [
            {"name": str(name), "error": str(v.get("last_error") or v.get("error") or "failed")}
            for name, v in (getattr(d, "loop_health", {}) or {}).items()
            if isinstance(v, dict) and v.get("ok") is False
        ]
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...mcp.tools import load_status

        for s in getattr(d.platform.config, "mcp_servers", None) or []:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name") or "")
            err = (load_status(name) or {}).get("last_error")
            if err:
                out["failing_packs"].append({"name": name, "error": str(err)})
    except Exception:  # noqa: BLE001
        pass
    try:
        out["provider_failures_24h"] = _event_count(
            db, ("provider.failed", "provider.failover"), utcnow() - timedelta(hours=24)
        )
    except Exception:  # noqa: BLE001
        pass
    return out


def heartbeat(db, now=None) -> list[int]:
    """Events per hour over the last 24 hours, oldest first; the last bucket
    is the current (partial) hour. One grouped count over the indexed
    ``created_at`` range."""
    from ...core.models import EventRecord

    now = now or utcnow()
    top = now.replace(minute=0, second=0, microsecond=0)
    start = top - timedelta(hours=HEARTBEAT_HOURS - 1)
    bucket = func.strftime("%Y-%m-%dT%H", EventRecord.created_at)
    rows = db.exec(
        select(bucket, func.count()).where(EventRecord.created_at >= start).group_by(bucket)
    ).all()
    counts = {str(k): int(v) for k, v in rows}
    return [
        counts.get((start + timedelta(hours=i)).strftime("%Y-%m-%dT%H"), 0) for i in range(HEARTBEAT_HOURS)
    ]


def system_map(d) -> dict[str, Any]:
    """The whole picture. Never raises."""
    since = utcnow() - timedelta(days=USED_DAYS)
    result: dict[str, Any] = {"areas": [], "heartbeat": [0] * HEARTBEAT_HOURS, "vitals": {}, "status": {}}
    try:
        with session_scope(d.platform.engine) as db:
            status = _status(d, db)
            result["status"] = status
            system_nodes, vitals = _system(d, db, since)
            built = {
                "work": _work(d, db, since),
                "automations": _automations(d, db, since),
                "system": system_nodes,
                "knowledge": _knowledge(d, db, since),
                "setup": _setup(d, db, since, status.get("failing_packs") or []),
            }
            result["areas"] = [{"key": k, "label": label, "nodes": built[k]} for k, label in AREAS]
            result["vitals"].update(vitals)
            try:
                result["heartbeat"] = heartbeat(db)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — the map is a picture; it never 500s
        pass
    try:
        usage = shutil.disk_usage(str(d.platform.config.home))
        result["vitals"]["disk_free"] = usage.free
        result["vitals"]["disk_total"] = usage.total
    except Exception:  # noqa: BLE001
        pass
    try:
        import psutil

        result["vitals"]["memory_mb"] = round(psutil.Process().memory_info().rss / 1_048_576)
    except Exception:  # noqa: BLE001
        pass
    return result
