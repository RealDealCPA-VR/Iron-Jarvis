"""Assignment store — the queue an agent's jobs wait in (v1.296.0).

Synchronous SQLite over :class:`~.models.AssignmentRecord`, by the module-level
convention of ``core/db.py``; every caller on the daemon's loop hops off it
with ``asyncio.to_thread`` (the dispatcher, the routes, the tool).

Four properties carry the feature, each a real failure mode:

1. **Creating refuses honestly.** An unknown assignee, a coordinator (the
   supervisor, or any definition carrying ``delegate`` — ``delegate_tool``'s
   anti-fork-bomb rule), a remote (not in this wave), a blank task, a depth
   past 2 (an agent queueing for an agent queueing for an agent) and a 21st
   queued job for one agent are each ONE plain ``ValueError`` sentence — the
   routes 422 it and the tool returns it as its error. A refused assignment
   leaves no row.
2. **Creating is idempotent on a key.** A non-empty ``idempotency_key`` that
   is already LIVE (queued/claimed/running) for the same assignee folds into
   the live row (``coalesced_count`` += 1) and ``create`` says ``created=False``.
   A schedule or a webhook that fires twice must not queue the job twice.
3. **Claiming is a compare-and-swap.** :meth:`claim_next` flips ONE row
   ``queued -> claimed`` in an ``UPDATE ... WHERE status = 'queued' AND id =
   <chosen>`` that stamps a per-call token, then reads back by that token —
   the worklist's proven shape. Two dispatch passes (or a tick racing a
   route's manual start) cannot both start the same job.
4. **A lost claim comes back.** A row left ``claimed``/``running`` by a crash
   or an update has no task behind it on a fresh process;
   :meth:`requeue_lost` puts it back in ``queued`` with a held reason that
   says so, and the dispatcher starts it again when the agent is free.

The 3-strike breaker (the goal breaker's precedent, ``goals/store.py``):
``finish(ok=False)`` counts a failure; the THIRD failure in a row blocks the
job with a reason naming the last error. ``retry`` of a FAILED job carries
the count forward so a job that fails every time is blocked on its third
copy, not retried forever; ``unblock`` resets it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import Engine, text, update
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from .models import (
    BLOCKED,
    CANCELLED,
    CLAIMED,
    DONE,
    FAILED,
    LIVE_STATUSES,
    QUEUED,
    RUNNING,
    STATUSES,
    TERMINAL_STATUSES,
    AssignmentRecord,
    derive_title,
)

log = logging.getLogger("iron_jarvis.assignments.store")

#: Hard ceilings. A model that loops on ``assign_work`` must not be able to
#: fill the database, and a queue nobody can drain is a lie about capacity.
MAX_QUEUED_PER_ASSIGNEE = 20
MAX_DEPTH = 2
#: Failed runs in a row before the breaker blocks the job.
BLOCK_AFTER_FAILURES = 3
MAX_TASK_CHARS = 20_000
MAX_REASON_CHARS = 400
MAX_ERROR_CHARS = 400
#: How many finished rows ``inbox`` shows.
RECENT_LIMIT = 10
#: How many times ``claim_next`` re-picks after losing a race before giving
#: up for this call (the next tick tries again).
_CLAIM_ATTEMPTS = 5
#: The held reason stamped on a row the boot reconcile put back.
REQUEUED_REASON = "restarted after an update or crash"

CUSTOM_PREFIX = "custom:"
REMOTE_PREFIX = "remote:"


def _one_line(text_: Any, limit: int) -> str:
    return " ".join(str(text_ or "").split())[:limit]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _as_naive(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; compare in naive UTC like the allowance ledger."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def decode_payload(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def as_dict(record: AssignmentRecord) -> dict[str, Any]:
    """JSON-ready view: ISO timestamps and the decoded payload."""
    return {
        "id": record.id,
        "project_id": record.project_id or "",
        "assignee": record.assignee,
        "task": record.task,
        "title": record.title,
        "priority": int(record.priority or 0),
        "status": record.status,
        "source": record.source,
        "reason": record.reason or "",
        "payload": decode_payload(record.payload_json),
        "idempotency_key": record.idempotency_key or "",
        "coalesced_count": int(record.coalesced_count or 0),
        "attempts": int(record.attempts or 0),
        "failure_count": int(record.failure_count or 0),
        "blocked_reason": record.blocked_reason or "",
        "held_reason": record.held_reason or "",
        "depth": int(record.depth or 0),
        "session_id": record.session_id or "",
        "last_error": record.last_error or "",
        "created_at": _iso(record.created_at),
        "claimed_at": _iso(record.claimed_at),
        "started_at": _iso(record.started_at),
        "finished_at": _iso(record.finished_at),
        "updated_at": _iso(record.updated_at),
    }


class AssignmentStore:
    """Durable queue of jobs handed to agents.

    ``agents_registry`` (the platform's ``DynamicAgentRegistry``) answers
    "does custom:<slug> exist and what is it based on"; a ``validator``
    callable ``(roster_name) -> canonical name`` may replace that lookup
    wholesale (tests, or a later remote wave). ``event_bus`` is optional:
    with one, ``create`` publishes ``assignment.created`` and the breaker
    publishes ``assignment.blocked``.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        agents_registry: Any = None,
        validator: Callable[[str], str] | None = None,
        event_bus: Any = None,
    ) -> None:
        self.engine = engine
        self.agents_registry = agents_registry
        self._validator = validator
        self.event_bus = event_bus
        #: The daemon's loop, bound by the dispatcher at start so a publish
        #: from a worker thread (every store call runs under ``to_thread``)
        #: can reach the async bus. Unbound + no running loop = no event.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event_tasks: set[Any] = set()

    #: JSON view, reachable from the store too (the routes hold only the store).
    as_dict = staticmethod(as_dict)

    # -- events ------------------------------------------------------------ #

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    async def flush_events(self) -> None:
        """Await every in-flight publish this store scheduled (tests; a
        route that must answer only once the ledger has the event)."""
        pending = list(self._event_tasks)
        if not pending:
            return
        awaitables = []
        for item in pending:
            if isinstance(item, asyncio.Future):
                awaitables.append(item)
            else:  # a concurrent.futures.Future from run_coroutine_threadsafe
                awaitables.append(asyncio.wrap_future(item))
        await asyncio.gather(*awaitables, return_exceptions=True)

    def _publish(self, type_: str, payload: dict) -> None:
        """Best-effort publish from sync code. On the loop thread it schedules
        a task; from a worker thread it hops to the bound loop; with neither
        it logs and drops — bookkeeping must never fail for want of a bus."""
        bus = self.event_bus
        if bus is None:
            return
        try:
            result = bus.publish(type_, payload)
        except Exception:  # noqa: BLE001 — a sync double that raises
            log.debug("assignment publish failed", exc_info=True)
            return
        if not inspect.isawaitable(result):
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            task = running.create_task(result)
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                fut = asyncio.run_coroutine_threadsafe(result, loop)
                self._event_tasks.add(fut)
                fut.add_done_callback(self._event_tasks.discard)
                return
            except Exception:  # noqa: BLE001
                log.debug("assignment publish hand-off failed", exc_info=True)
        try:
            result.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    # -- validation -------------------------------------------------------- #

    def resolve_assignee(self, raw: Any) -> str:
        """The canonical roster name for ``raw`` or a one-sentence ValueError.

        Accepts a builtin type (``"builder"``), ``"custom:<slug>"``, or the
        bare slug of an existing custom agent (returned as ``custom:<slug>``,
        the registry's own casing). Refuses remotes, the supervisor, any
        definition carrying ``delegate`` (the planner, or a custom agent that
        can hand work out itself), and names nobody knows.
        """
        name = " ".join(str(raw or "").split())
        if not name:
            raise ValueError("an assignment needs an assignee — a builtin type or a custom agent")
        if self._validator is not None:
            return self._validator(name)
        low = name.casefold()
        if low.startswith(REMOTE_PREFIX):
            raise ValueError(
                f"{name} is a remote agent — assignments to remote agents are not supported yet"
            )
        from ..core.models import AgentType
        from ..agents.types import get_agent_definition

        slug = name[len(CUSTOM_PREFIX):] if low.startswith(CUSTOM_PREFIX) else ""
        registry = self.agents_registry
        record = None
        if registry is not None:
            try:
                record = registry.get(slug or name)
            except Exception:  # noqa: BLE001 — a broken registry is "unknown"
                record = None
        if slug and record is None:
            raise ValueError(f"no custom agent named '{slug}' exists")
        if record is not None and (slug or low not in {t.value for t in AgentType}):
            try:
                definition = registry.definition(record.name)
            except Exception:  # noqa: BLE001
                definition = None
            base = str(getattr(record, "base_type", "") or "").casefold()
            tools = list(getattr(definition, "tools", None) or [])
            if base == AgentType.SUPERVISOR.value or (
                definition is not None and definition.type is AgentType.SUPERVISOR
            ):
                raise ValueError(
                    f"custom:{record.name} is based on the supervisor — assign work to a "
                    "specialist agent instead"
                )
            if "delegate" in tools:
                raise ValueError(
                    f"custom:{record.name} can delegate work itself — assign work to a "
                    "specialist agent instead, or it could fan out without bound"
                )
            return f"{CUSTOM_PREFIX}{record.name}"
        try:
            agent_type = AgentType(low)
        except ValueError:
            raise ValueError(
                f"unknown agent '{name}' — use a builtin type like 'builder' or an "
                "existing custom agent"
            ) from None
        if agent_type is AgentType.SUPERVISOR:
            raise ValueError(
                "the supervisor cannot take an assignment — assign work to a specialist "
                "agent (builder/researcher/reviewer) instead"
            )
        if "delegate" in (get_agent_definition(agent_type).tools or []):
            raise ValueError(
                f"{agent_type.value} can delegate work itself — assign work to a specialist "
                "agent instead, or it could fan out without bound"
            )
        return agent_type.value

    # -- create ------------------------------------------------------------ #

    def create(
        self,
        assignee: str,
        task: str,
        *,
        project_id: str = "",
        source: str = "user",
        reason: str = "",
        priority: int = 0,
        payload: dict | None = None,
        idempotency_key: str = "",
        depth: int = 0,
        title: str = "",
    ) -> tuple[AssignmentRecord, bool]:
        """Queue a job; ``(record, created)``. ValueError = one plain sentence."""
        name = self.resolve_assignee(assignee)
        text_ = str(task or "").strip()
        if not text_:
            raise ValueError("an assignment needs a task — say what the agent should do")
        text_ = text_[:MAX_TASK_CHARS]
        try:
            depth_n = int(depth or 0)
        except (TypeError, ValueError):
            depth_n = 0
        if depth_n > MAX_DEPTH:
            raise ValueError(
                f"an agent's assignment may not queue another assignment this deep "
                f"(depth {depth_n} > {MAX_DEPTH}) — do the work directly instead"
            )
        try:
            prio = int(priority or 0)
        except (TypeError, ValueError):
            prio = 0
        key = _one_line(idempotency_key, 200)
        payload_obj = dict(payload) if isinstance(payload, dict) else {}
        now = utcnow()
        with session_scope(self.engine) as db:
            if key:
                live = db.exec(
                    select(AssignmentRecord).where(
                        AssignmentRecord.assignee == name,
                        AssignmentRecord.idempotency_key == key,
                        AssignmentRecord.status.in_(LIVE_STATUSES),  # type: ignore[attr-defined]
                    )
                ).first()
                if live is not None:
                    live.coalesced_count = int(live.coalesced_count or 0) + 1
                    live.updated_at = now
                    db.add(live)
                    db.commit()
                    db.refresh(live)
                    db.expunge(live)
                    return live, False
            queued = db.exec(
                select(AssignmentRecord).where(
                    AssignmentRecord.assignee == name,
                    AssignmentRecord.status == QUEUED,
                )
            ).all()
            if len(queued) >= MAX_QUEUED_PER_ASSIGNEE:
                raise ValueError(
                    f"{name} already has {len(queued)} assignments waiting — let it work "
                    "through them, or cancel some, before queueing more"
                )
            record = AssignmentRecord(
                project_id=str(project_id or ""),
                assignee=name,
                task=text_,
                title=_one_line(title, 80) or derive_title(text_),
                priority=prio,
                status=QUEUED,
                source=_one_line(source, 120) or "user",
                reason=_one_line(reason, MAX_REASON_CHARS),
                payload_json=json.dumps(payload_obj, default=str),
                idempotency_key=key,
                depth=depth_n,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            db.expunge(record)
        from ..core.events import EventType

        self._publish(
            EventType.ASSIGNMENT_CREATED,
            {
                "id": record.id,
                "assignee": record.assignee,
                "title": record.title,
                "source": record.source,
                "reason": record.reason,
                "project_id": record.project_id,
            },
        )
        return record, True

    # -- the claim --------------------------------------------------------- #

    def claim_next(
        self, assignee: str | None = None, now: datetime | None = None
    ) -> AssignmentRecord | None:
        """Atomically take the next queued job: highest priority, then oldest.

        Pick, then ``UPDATE ... WHERE status = 'queued' AND id = <pick>`` with
        a per-call token, then read back by the token. Losing the race means
        another claimer flipped that exact row first; re-pick a bounded
        number of times (the loser takes the next row, or nothing).
        """
        moment = now or utcnow()
        for _ in range(_CLAIM_ATTEMPTS):
            token = new_id("clm")
            with session_scope(self.engine) as db:
                stmt = select(AssignmentRecord).where(AssignmentRecord.status == QUEUED)
                if assignee:
                    stmt = stmt.where(AssignmentRecord.assignee == assignee)
                pick = db.exec(
                    stmt.order_by(
                        AssignmentRecord.priority.desc(),  # type: ignore[attr-defined]
                        AssignmentRecord.created_at,  # type: ignore[arg-type]
                        text("rowid"),
                    ).limit(1)
                ).first()
                if pick is None:
                    return None
                chosen_id = pick.id
                # THE COMPARE-AND-SWAP: the status predicate makes a concurrent
                # claim lose instead of double-booking; the token lets us read
                # back only the row we won.
                db.execute(
                    update(AssignmentRecord)
                    .where(
                        AssignmentRecord.id == chosen_id,
                        AssignmentRecord.status == QUEUED,
                    )
                    .values(
                        status=CLAIMED,
                        claim_token=token,
                        claimed_at=moment,
                        held_reason="",
                        updated_at=moment,
                    )
                )
                db.commit()
                won = db.exec(
                    select(AssignmentRecord).where(AssignmentRecord.claim_token == token)
                ).first()
                if won is not None:
                    db.expunge(won)
                    return won
        return None

    # -- lifecycle --------------------------------------------------------- #

    def _mutate(self, id_: str, fn: Callable[[AssignmentRecord], bool]) -> AssignmentRecord | None:
        """Apply ``fn`` (returns True when it changed the row) and persist."""
        with session_scope(self.engine) as db:
            row = db.get(AssignmentRecord, id_)
            if row is None:
                return None
            if fn(row):
                row.updated_at = utcnow()
                db.add(row)
                db.commit()
                db.refresh(row)
            db.expunge(row)
            return row

    def start(self, id_: str, session_id: str) -> AssignmentRecord | None:
        """claimed/queued → running, with the session that carries it."""
        now = utcnow()

        def fn(row: AssignmentRecord) -> bool:
            if row.status not in (CLAIMED, QUEUED):
                return False
            row.status = RUNNING
            row.session_id = str(session_id or "")
            row.started_at = now
            row.held_reason = ""
            return True

        return self._mutate(id_, fn)

    def finish(self, id_: str, ok: bool, error: str = "") -> AssignmentRecord | None:
        """running/claimed → done, or failed — or BLOCKED on the third failure
        in a row. A row already settled (cancelled while its session ran,
        say) is left alone: the first verdict stands."""
        now = utcnow()
        blocked: dict | None = None

        def fn(row: AssignmentRecord) -> bool:
            nonlocal blocked
            if row.status not in (RUNNING, CLAIMED):
                return False
            row.attempts = int(row.attempts or 0) + 1
            row.finished_at = now
            row.claim_token = ""
            row.held_reason = ""
            if ok:
                row.status = DONE
                row.failure_count = 0
                row.last_error = ""
                return True
            row.failure_count = int(row.failure_count or 0) + 1
            row.last_error = _one_line(error, MAX_ERROR_CHARS)
            if row.failure_count >= BLOCK_AFTER_FAILURES:
                row.status = BLOCKED
                tail = f" — last error: {row.last_error}" if row.last_error else ""
                row.blocked_reason = f"{row.failure_count} failed runs in a row{tail}"
                blocked = {
                    "id": row.id,
                    "assignee": row.assignee,
                    "title": row.title,
                    "blocked_reason": row.blocked_reason,
                }
            else:
                row.status = FAILED
            return True

        row = self._mutate(id_, fn)
        if blocked is not None:
            from ..core.events import EventType

            self._publish(EventType.ASSIGNMENT_BLOCKED, blocked)
        return row

    def hold(self, id_: str, reason: str) -> AssignmentRecord | None:
        """Say why a QUEUED job is not starting right now (informational)."""
        words = _one_line(reason, 300)

        def fn(row: AssignmentRecord) -> bool:
            if row.status != QUEUED or row.held_reason == words:
                return False
            row.held_reason = words
            return True

        return self._mutate(id_, fn)

    def requeue_lost(self, live_session_ids: Iterable[str]) -> list[AssignmentRecord]:
        """claimed/running rows whose session is not live → queued again."""
        live = {str(s) for s in (live_session_ids or ())}
        now = utcnow()
        out: list[AssignmentRecord] = []
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(AssignmentRecord).where(
                    AssignmentRecord.status.in_((CLAIMED, RUNNING))  # type: ignore[attr-defined]
                )
            ).all()
            for row in rows:
                if row.session_id and row.session_id in live:
                    continue
                row.status = QUEUED
                row.claim_token = ""
                row.claimed_at = None
                row.started_at = None
                row.held_reason = REQUEUED_REASON
                row.updated_at = now
                db.add(row)
                out.append(row)
            db.commit()
            for row in out:
                db.refresh(row)
                db.expunge(row)
        return out

    def cancel(self, id_: str) -> AssignmentRecord | None:
        """Any non-terminal row (or a blocked one) → cancelled. The ROUTE stops
        a running row's session through the orchestrator; this only flips."""
        now = utcnow()

        def fn(row: AssignmentRecord) -> bool:
            if row.status in (DONE, FAILED, CANCELLED):
                return False
            row.status = CANCELLED
            row.claim_token = ""
            row.held_reason = ""
            row.finished_at = now
            return True

        return self._mutate(id_, fn)

    def unblock(self, id_: str) -> AssignmentRecord | None:
        """blocked → queued with the breaker reset."""

        def fn(row: AssignmentRecord) -> bool:
            if row.status != BLOCKED:
                return False
            row.status = QUEUED
            row.failure_count = 0
            row.blocked_reason = ""
            row.held_reason = ""
            row.claim_token = ""
            return True

        return self._mutate(id_, fn)

    def retry(self, id_: str) -> AssignmentRecord | None:
        """failed|cancelled|done → a NEW queued copy (source ``retry:<old>``).

        A FAILED original hands its failure count to the copy, so the breaker
        counts the chain: the third failing copy is blocked, not retried.
        """
        old = self.get(id_)
        if old is None or old.status not in (FAILED, CANCELLED, DONE):
            return None
        record, _created = self.create(
            old.assignee,
            old.task,
            project_id=old.project_id or "",
            source=f"retry:{old.id}",
            reason=old.reason or "",
            priority=int(old.priority or 0),
            payload=decode_payload(old.payload_json),
            depth=int(old.depth or 0),
            title=old.title or "",
        )
        if old.status == FAILED and int(old.failure_count or 0) > 0:
            carried = int(old.failure_count or 0)

            def fn(row: AssignmentRecord) -> bool:
                row.failure_count = carried
                row.last_error = old.last_error or ""
                return True

            record = self._mutate(record.id, fn) or record
        return record

    # -- reads ------------------------------------------------------------- #

    def get(self, id_: str) -> AssignmentRecord | None:
        with session_scope(self.engine) as db:
            row = db.get(AssignmentRecord, id_)
            if row is not None:
                db.expunge(row)
            return row

    def list(
        self,
        assignee: str | None = None,
        project_id: str | None = None,
        statuses: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[AssignmentRecord]:
        """Newest first; ``statuses`` narrows (unknown words are ignored)."""
        wanted = [s for s in (statuses or ()) if s in STATUSES]
        try:
            cap = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            cap = 50
        with session_scope(self.engine) as db:
            stmt = select(AssignmentRecord)
            if assignee:
                stmt = stmt.where(AssignmentRecord.assignee == assignee)
            if project_id:
                stmt = stmt.where(AssignmentRecord.project_id == project_id)
            if wanted:
                stmt = stmt.where(AssignmentRecord.status.in_(wanted))  # type: ignore[attr-defined]
            rows = db.exec(
                stmt.order_by(
                    AssignmentRecord.created_at.desc(),  # type: ignore[attr-defined]
                    text("rowid DESC"),
                ).limit(cap)
            ).all()
            for row in rows:
                db.expunge(row)
            return list(rows)

    def queued(self, assignee: str | None = None, limit: int = 200) -> list[AssignmentRecord]:
        """Queued rows in DISPATCH order: priority desc, then oldest first."""
        with session_scope(self.engine) as db:
            stmt = select(AssignmentRecord).where(AssignmentRecord.status == QUEUED)
            if assignee:
                stmt = stmt.where(AssignmentRecord.assignee == assignee)
            rows = db.exec(
                stmt.order_by(
                    AssignmentRecord.priority.desc(),  # type: ignore[attr-defined]
                    AssignmentRecord.created_at,  # type: ignore[arg-type]
                    text("rowid"),
                ).limit(max(1, int(limit)))
            ).all()
            for row in rows:
                db.expunge(row)
            return list(rows)

    def live_for(self, assignee: str) -> list[AssignmentRecord]:
        """Claimed/running rows for one agent (its "busy" from the queue's view)."""
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(AssignmentRecord).where(
                    AssignmentRecord.assignee == assignee,
                    AssignmentRecord.status.in_((CLAIMED, RUNNING)),  # type: ignore[attr-defined]
                )
            ).all()
            for row in rows:
                db.expunge(row)
            return list(rows)

    def counts(self, assignee: str) -> dict[str, int]:
        """``{"queued", "running", "blocked"}`` for one agent (running = claimed+running)."""
        out = {"queued": 0, "running": 0, "blocked": 0}
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(AssignmentRecord.status).where(
                    AssignmentRecord.assignee == assignee,
                    AssignmentRecord.status.in_((QUEUED, CLAIMED, RUNNING, BLOCKED)),  # type: ignore[attr-defined]
                )
            ).all()
        for status in rows:
            if status == QUEUED:
                out["queued"] += 1
            elif status in (CLAIMED, RUNNING):
                out["running"] += 1
            elif status == BLOCKED:
                out["blocked"] += 1
        return out

    def inbox(self, assignee: str) -> dict[str, list[AssignmentRecord]]:
        """``{"queued", "claimed", "running", "blocked", "recent"}`` for one agent."""
        out: dict[str, list[AssignmentRecord]] = {
            "queued": [], "claimed": [], "running": [], "blocked": [], "recent": [],
        }
        with session_scope(self.engine) as db:
            rows = db.exec(
                select(AssignmentRecord)
                .where(AssignmentRecord.assignee == assignee)
                .order_by(
                    AssignmentRecord.priority.desc(),  # type: ignore[attr-defined]
                    AssignmentRecord.created_at,  # type: ignore[arg-type]
                    text("rowid"),
                )
            ).all()
            finished: list[AssignmentRecord] = []
            for row in rows:
                db.expunge(row)
                if row.status in out:
                    out[row.status].append(row)
                elif row.status in TERMINAL_STATUSES:
                    finished.append(row)
            finished.sort(
                key=lambda r: _as_naive(r.finished_at or r.updated_at or r.created_at)
                or datetime.min,
                reverse=True,
            )
            out["recent"] = finished[:RECENT_LIMIT]
        return out

    def latest_for(self, assignee: str) -> AssignmentRecord | None:
        """The newest row for one agent (any status), for the health card."""
        rows = self.list(assignee=assignee, limit=1)
        return rows[0] if rows else None
