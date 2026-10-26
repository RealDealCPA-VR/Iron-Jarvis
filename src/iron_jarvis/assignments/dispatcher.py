"""The assignment dispatcher — the job waits for the agent (v1.296.0).

One background loop, wired in the daemon lifespan exactly like
``_autonomy_loop``: a named task, ``_tick("dispatcher", ok, exc)`` per pass,
``loop_health["dispatcher"]``, cancelled at shutdown. Each pass
(:meth:`AssignmentDispatcher.tick`) walks the queued assignments in dispatch
order and, for each one, either HOLDS it with a plain sentence or STARTS it:

* held — the agent is paused or has used its allowance
  (``allowance.refusal_for``, the v1.295.0 door every other door asks), the
  agent already has a claimed/running assignment (one at a time per agent —
  an employee works one job at a time), or every ``max_concurrent_sessions``
  slot is busy (the orchestrator's ``_governed`` set, the same denominator
  ``spawn_managed`` parks on);
* started — ``claim_next`` (the CAS) → ``create_session`` with
  ``origin="assignment:<id>"`` and the roster name as ``agent_name`` (so the
  run is CREDITED to the agent and its allowance is charged) → ``start`` →
  ``spawn_managed`` with the dynamic definition, the way ``POST
  /agents/{name}/spawn`` with ``wait=false`` does it.

The END of the run is not this module's: ``Orchestrator._post_run_assignment``
reads ``session.origin`` at every finalize site (completed, failed,
cancelled) and calls ``store.finish``; this loop's ``sweep`` is the backstop
for a row whose session settled without the hook (a cancel stamped from a
foreign thread, a reconcile) — it reads the session row's status and
finishes the assignment to match.

Every DB call hops off the loop (``asyncio.to_thread``); the loop survives
any exception (logged + reported through ``_tick``) and keeps ticking; a
``stop`` event ends it; :meth:`wake` cuts the interval short so a job queued
by a route or the tool starts without waiting the full interval. Since
v1.333.0 a pass that found nothing queued and nothing live waits
``IDLE_INTERVAL_S`` (60 s) instead of 5 s — the wakes carry the latency.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..core.ids import utcnow
from .models import CLAIMED, RUNNING, AssignmentRecord
from .store import CUSTOM_PREFIX, AssignmentStore, decode_payload

log = logging.getLogger("iron_jarvis.assignments.dispatcher")

#: Seconds between passes when nothing wakes the loop.
DEFAULT_INTERVAL_S = 5.0
#: Seconds between passes when the last pass found NOTHING to do — no queued
#: row and no claimed/running one (v1.333.0). An empty queue cost two SELECTs
#: every 5 s all day; everything that can give this loop work wakes it
#: (a route or the tool queuing a job, an assignment's session ending), so the
#: long wait is only the backstop for a row that appeared some other way.
IDLE_INTERVAL_S = 60.0
#: A row stuck in ``claimed`` (no session row ever appeared) longer than this
#: is handed back to ``queued`` — the create→start window is milliseconds.
STALE_CLAIM_S = 300
#: The held sentences (the dashboard prints them verbatim).
HELD_BUSY = "{name} is busy with another queued task"
HELD_SLOTS = "every session slot is busy — waiting for one to free"
HELD_GONE = "{name} no longer exists — cancel this task or re-create the agent"
HELD_NO_ORCH = "the agent orchestrator is not ready yet"

TickHook = Callable[[str, bool, BaseException | None], None]


def _noop_tick(name: str, ok: bool, exc: BaseException | None = None) -> None:
    return None


class AssignmentDispatcher:
    """Turns queued assignments into governed background sessions."""

    def __init__(
        self,
        platform: Any,
        interval_s: float = DEFAULT_INTERVAL_S,
        *,
        tick: TickHook | None = None,
        idle_interval_s: float = IDLE_INTERVAL_S,
    ) -> None:
        self.p = platform
        self.interval_s = max(0.05, float(interval_s))
        self.idle_interval_s = max(self.interval_s, float(idle_interval_s))
        #: Whether the last pass found nothing queued and nothing live
        #: (v1.333.0) — the loop then waits ``idle_interval_s``. False until
        #: a pass proves it, so a failing pass keeps the short interval.
        self._idle = False
        #: Whether the last sweep saw a claimed/running row (or could not tell).
        self._saw_live = True
        #: The loop ``run_forever`` runs on (a foreign-thread ``wake`` hops to it).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tick_hook: TickHook = tick or _noop_tick
        self._wake = asyncio.Event()
        #: One pass at a time (reviewer, v1.296.0): two overlapping passes
        #: both see an agent free, and the CAS re-pick hands the LOSER the
        #: agent's NEXT row — two sessions for one agent. The lock binds to
        #: the running loop on first use, like ``_wake``.
        self._tick_lock = asyncio.Lock()
        #: Background run tasks this dispatcher started (tests ``drain()``).
        self._inflight: set[asyncio.Task] = set()

    # -- seams ------------------------------------------------------------- #

    @property
    def store(self) -> AssignmentStore | None:
        return getattr(self.p, "assignments", None)

    @property
    def orchestrator(self) -> Any:
        return getattr(self.p, "orchestrator", None)

    def wake(self) -> None:
        """Dispatch now instead of at the next interval. Thread-safe (v1.333.0):
        the ``assign`` tool can run inside a schedule-fired run, whose loop is
        on APScheduler's thread — an ``asyncio.Event`` set from there would
        not wake this loop until its (now idle-length) wait ran out, so a
        foreign caller hands the set to the dispatcher's own loop."""
        try:
            loop = self._loop
            try:
                here = asyncio.get_running_loop()
            except RuntimeError:
                here = None
            if loop is not None and loop is not here and loop.is_running():
                loop.call_soon_threadsafe(self._wake.set)
            else:
                self._wake.set()
        except Exception:  # noqa: BLE001
            pass

    async def drain(self) -> None:
        """Await every run this dispatcher started (tests). Never spins on a
        run that finished just before (core/tasks)."""
        from ..core.tasks import drain_tasks

        await drain_tasks(self._inflight)

    # -- the loop ---------------------------------------------------------- #

    async def run_forever(self, stop: asyncio.Event) -> None:
        store = self.store
        self._loop = asyncio.get_running_loop()
        if store is not None:
            store.bind_loop(self._loop)
        try:
            while not stop.is_set():
                # Cleared BEFORE the pass (v1.333.0): a job queued while the
                # pass runs keeps its wake, so the next wait returns at once
                # instead of sleeping out an idle minute on a stale "empty".
                self._wake.clear()
                try:
                    await self.tick()
                    self._tick_hook("dispatcher", True, None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — a pass must never kill the loop
                    log.exception("assignment dispatch pass failed")
                    self._tick_hook("dispatcher", False, exc)
                    self._idle = False
                await self._sleep(
                    stop, self.idle_interval_s if self._idle else self.interval_s
                )
        finally:
            self._loop = None
            if store is not None:
                store.bind_loop(None)

    async def _sleep(self, stop: asyncio.Event, timeout: float) -> None:
        """Wait up to ``timeout`` for a wake or the stop (a seam: tests drive
        the loop on a counted fake clock)."""
        waiters = [
            asyncio.create_task(self._wake.wait()),
            asyncio.create_task(stop.wait()),
        ]
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                if not w.done():
                    w.cancel()

    async def requeue_lost_at_boot(self, live_session_ids: set[str] | None = None) -> list[dict]:
        """Boot: every claimed/running row without a live session goes back
        to ``queued``; ONE ``assignment.requeued`` event when any did."""
        store = self.store
        if store is None:
            return []
        rows = await asyncio.to_thread(store.requeue_lost, set(live_session_ids or ()))
        if rows:
            from ..core.events import EventType

            await self._publish(
                EventType.ASSIGNMENT_REQUEUED,
                {"count": len(rows), "ids": [r.id for r in rows]},
            )
        return [{"id": r.id, "assignee": r.assignee, "title": r.title} for r in rows]

    # -- one pass ---------------------------------------------------------- #

    async def tick(self) -> dict[str, Any]:
        """One dispatch pass. Returns ``{"started": [...], "held": n, "swept": n}``.
        Serialized: a pass that overlaps another waits for it."""
        async with self._tick_lock:
            return await self._tick_once()

    async def _tick_once(self) -> dict[str, Any]:
        result: dict[str, Any] = {"started": [], "held": 0, "swept": 0}
        self._idle = False
        store = self.store
        if store is None:
            return result
        result["swept"] = await self.sweep()
        orch = self.orchestrator
        queued = await asyncio.to_thread(store.queued)
        if not queued:
            # Nothing waiting; and with nothing claimed/running either, no
            # session end is due to free anything — the long wait is safe.
            self._idle = not self._saw_live
            return result
        if orch is None:
            for row in queued:
                await asyncio.to_thread(store.hold, row.id, HELD_NO_ORCH)
            result["held"] = len(queued)
            return result
        #: Agents handled THIS pass — one start per agent per tick, and the
        #: rest of its queue holds behind it.
        seen: set[str] = set()
        for row in queued:
            if row.assignee in seen:
                continue
            seen.add(row.assignee)
            reason = await self._hold_reason(row)
            if reason:
                await asyncio.to_thread(store.hold, row.id, reason)
                result["held"] += 1
                continue
            started = await self._start(row.assignee)
            if started:
                result["started"].append(started)
        return result

    async def sweep(self) -> int:
        """Finish rows whose session already ended without the hook; hand a
        stale ``claimed`` row (no session ever started) back to the queue."""
        store = self.store
        orch = self.orchestrator
        if store is None:
            return 0
        swept = 0
        self._saw_live = True  # unknown until the read answers
        try:
            live = await asyncio.to_thread(store.list, None, None, (CLAIMED, RUNNING), 200)
        except Exception:  # noqa: BLE001
            log.debug("sweep read failed", exc_info=True)
            return 0
        self._saw_live = bool(live)
        if not live:
            return 0
        from ..core.models import SessionStatus

        now = utcnow()
        for row in live:
            try:
                if row.status == CLAIMED or not row.session_id:
                    stamp = row.claimed_at or row.updated_at or row.created_at
                    if stamp is not None and stamp.tzinfo is None:
                        from datetime import timezone

                        stamp = stamp.replace(tzinfo=timezone.utc)
                    if stamp is not None and (now - stamp).total_seconds() > STALE_CLAIM_S:
                        rows = await asyncio.to_thread(
                            store.requeue_lost, set(getattr(orch, "_running", {}) or {})
                        )
                        swept += len(rows)
                    continue
                if orch is None:
                    continue
                session = await asyncio.to_thread(orch.get_session, row.session_id)
                if session is None:
                    await asyncio.to_thread(
                        store.finish, row.id, False, "its session row is gone"
                    )
                    swept += 1
                    continue
                status = session.status
                if status in (SessionStatus.ACTIVE, SessionStatus.QUEUED):
                    continue
                if status is SessionStatus.CANCELLED:
                    # Stopped from outside (the sessions route, a foreign
                    # thread): cancelled, not a failure strike.
                    await asyncio.to_thread(store.cancel, row.id)
                    swept += 1
                    continue
                ok = status is SessionStatus.COMPLETED
                error = "" if ok else _first_line(getattr(session, "summary", "") or "")
                await asyncio.to_thread(store.finish, row.id, ok, error)
                swept += 1
            except Exception:  # noqa: BLE001 — one bad row must not stop the sweep
                log.debug("sweep of %s failed", row.id, exc_info=True)
        return swept

    # -- helpers ----------------------------------------------------------- #

    async def _hold_reason(self, row: AssignmentRecord) -> str:
        """``""`` when ``row`` may start now, else the sentence to hold it with."""
        # EMERGENCY STOP (v1.351.0, G13): HOLD the job with the sentence; never
        # claim it and let create_session refuse it — that path FAILS the row
        # and counts a strike (3 = blocked), so every queued job would be lost
        # to "failed" until the user pressed Retry on each one.
        stop = getattr(self.orchestrator, "emergency_stop_reason", None)
        if callable(stop):
            reason = stop(f"assignment:{row.id}")
            if reason:
                return reason
        store = self.store
        name = row.assignee
        record = None
        if name.casefold().startswith(CUSTOM_PREFIX):
            registry = getattr(self.p, "agents_registry", None)
            slug = name[len(CUSTOM_PREFIX):]
            record = (
                await asyncio.to_thread(registry.get, slug) if registry is not None else None
            )
            if record is None:
                return HELD_GONE.format(name=name)
            from ..agents import allowance

            refusal = await asyncio.to_thread(allowance.refusal_for, record, self.p.engine)
            if refusal:
                return refusal
            # A MODEL TEAM MEMBER (v1.395.0, the Phase 5 review's open item):
            # the doors' rules, here too — its account must be one the user's
            # groups allow, Local only work never starts a member whose model
            # leaves the network, and a job naming ANOTHER model than the
            # member runs on is said, never swapped. HELD with the sentence
            # (never claimed: a refused start would count a strike).
            from ..agents import model_team as _mt

            if _mt.runs_elsewhere(record):
                payload = decode_payload(row.payload_json)
                why = await asyncio.to_thread(
                    _mt.queued_refusal,
                    self.p,
                    record,
                    provider=str(payload.get("provider") or ""),
                    model=str(payload.get("model") or ""),
                    local_only=await self._local_only_of(row),
                )
                if why:
                    return why
        live = await asyncio.to_thread(store.live_for, name)
        if live:
            return HELD_BUSY.format(name=name)
        orch = self.orchestrator
        try:
            limit = int(getattr(self.p.config, "max_concurrent_sessions", 0) or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0 and len(getattr(orch, "_governed", ()) or ()) >= limit:
            return HELD_SLOTS
        return ""

    async def _local_only_of(self, row: AssignmentRecord) -> bool:
        """Was this job queued by a Local only run (``source ==
        "agent:<session>"`` and that run is Local only)? Off the loop; False
        when unknown."""
        src = str(getattr(row, "source", "") or "")
        if not src.startswith("agent:"):
            return False
        try:
            from ..agents.orchestrator import inherited_local_only

            parent = await asyncio.to_thread(self.orchestrator.get_session, src[len("agent:"):])
            return bool(inherited_local_only(parent))
        except Exception:  # noqa: BLE001
            return False

    async def _start(self, assignee: str) -> str | None:
        """Claim the agent's top job and run it. Returns the assignment id
        started, or None when the claim was lost or the row failed to start."""
        store = self.store
        orch = self.orchestrator
        row = await asyncio.to_thread(store.claim_next, assignee)
        if row is None:
            return None
        from ..core.events import EventType
        from ..core.models import AgentType
        from ..agents.types import get_agent_definition

        payload = decode_payload(row.payload_json)
        registry = getattr(self.p, "agents_registry", None)
        try:
            definition = None
            record = None
            if assignee.casefold().startswith(CUSTOM_PREFIX):
                slug = assignee[len(CUSTOM_PREFIX):]
                if registry is not None:  # both are SQLite reads — off the loop
                    definition = await asyncio.to_thread(registry.definition, slug)
                    record = await asyncio.to_thread(registry.get, slug)
                if definition is None or record is None:
                    raise RuntimeError(HELD_GONE.format(name=assignee))
                if definition.type is AgentType.SUPERVISOR:
                    raise RuntimeError(
                        f"{assignee} is based on the supervisor and cannot take a queued task"
                    )
                agent_type = definition.type
            else:
                agent_type = AgentType(assignee)
                if agent_type is AgentType.SUPERVISOR:
                    raise RuntimeError("the supervisor cannot take a queued task")
                get_agent_definition(agent_type)  # existence check
            workspace_root = str(payload.get("workspace_root") or "").strip() or None
            if workspace_root:
                from ..core.fs_policy import usable_workspace_root

                if not await asyncio.to_thread(usable_workspace_root, workspace_root):
                    raise RuntimeError(
                        "workspace_root must be an existing, absolute, non-protected "
                        f"folder this app may write in: {workspace_root}"
                    )
            provider = str(payload.get("provider") or "").strip() or (
                getattr(record, "provider", "") or None
            )
            model = str(payload.get("model") or "").strip() or (
                getattr(record, "model", "") or None
            )
            # v1.395.0: a MODEL TEAM MEMBER runs on ITS OWN model — that
            # provider's default when it names none (``model_team.
            # pinned_model``, the doors' rule; a job naming another model was
            # held by ``_hold_reason``), and its run is HELD there below.
            from ..agents import model_team as _mt

            _member = record if _mt.runs_elsewhere(record) else None
            if _member is not None:
                provider = str(_member.provider or "").strip() or None
                model = _mt.pinned_model(self.p, _member) or None
            allow_tools = payload.get("allow_tools")
            if not isinstance(allow_tools, list):
                allow_tools = None
            max_steps = payload.get("max_steps")
            if max_steps is None:
                max_steps = getattr(record, "max_steps", None)
            task = row.task
            if row.reason:
                task = f"{task}\n\nWhy this was assigned: {row.reason}"
            # TRUST (v1.298.0): the assignment row carries nothing new. A job
            # an AGENT queued (``source == "agent:<session id>"``) inherits
            # that parent's posture when the parent is low — trust flows
            # down, never up; a user-queued or retried row is full.
            _trust, _trust_reason = None, ""
            _local_only = False  # v1.332.0: a local-only parent's job stays local
            _src = str(getattr(row, "source", "") or "")
            if _src.startswith("agent:"):
                from ..agents.orchestrator import inherited_local_only, inherited_trust

                # Off the loop, like every other store/DB read in this file.
                _parent = await asyncio.to_thread(orch.get_session, _src[len("agent:"):])
                _trust, _trust_reason = inherited_trust(_parent)
                _local_only = inherited_local_only(_parent)
            session = await orch.create_session(
                task,
                agent_type,
                provider=provider or None,
                model=model or None,
                project_id=(row.project_id or None),
                allow_tools=allow_tools,
                workspace_root=workspace_root,
                origin=f"assignment:{row.id}",
                max_steps=max_steps,
                agent_name=assignee,
                approval_mode=str(getattr(record, "approval_mode", "") or "") or None,
                trust=_trust,
                trust_reason=_trust_reason,
                **({"options": {"local_only": True}} if _local_only else {}),
            )
        except Exception as exc:  # noqa: BLE001 — a job that cannot start FAILS, honestly
            log.warning("assignment %s could not start: %s", row.id, exc)
            await asyncio.to_thread(store.finish, row.id, False, f"could not start: {exc}")
            return None
        await asyncio.to_thread(store.start, row.id, session.id)
        await self._publish(
            EventType.ASSIGNMENT_STARTED,
            {
                "id": row.id,
                "assignee": assignee,
                "session_id": session.id,
                "title": row.title,
            },
            session_id=session.id,
        )
        if _member is not None:
            # v1.395.0: the member's model HELD and its account pinned for the
            # whole run — entered INSIDE the run's own task, so a run the
            # governor parks and promotes later keeps it.
            run = _mt.run_as_member(
                self.p, _member, lambda: orch.run_session(session.id, definition=definition)
            )
        else:
            run = orch.run_session(session.id, definition=definition)
        task_obj = orch.spawn_managed(session.id, run)
        if task_obj is not None:
            self._inflight.add(task_obj)
            task_obj.add_done_callback(self._inflight.discard)
        else:
            # Parked by the governor (its row says QUEUED — still ours) or
            # refused (draining / already terminal): the sweep settles the
            # latter from the session row's own status.
            refreshed = await asyncio.to_thread(orch.get_session, session.id)
            if refreshed is not None and str(getattr(refreshed.status, "value", refreshed.status)) not in (
                "active", "queued",
            ):
                await asyncio.to_thread(
                    store.finish, row.id, False, "the run was refused before it started"
                )
                return None
        return row.id

    async def _publish(self, type_: str, payload: dict, session_id: str | None = None) -> None:
        bus = getattr(self.p, "event_bus", None)
        if bus is None:
            return
        try:
            result = bus.publish(type_, payload, session_id=session_id)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                await result
        except TypeError:
            result = bus.publish(type_, payload)
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                await result
        except Exception:  # noqa: BLE001 — an event is never worth a pass
            log.debug("assignment publish failed", exc_info=True)


def _first_line(text: str, limit: int = 200) -> str:
    for line in str(text or "").splitlines():
        words = " ".join(line.split())
        if words:
            return words[:limit]
    return ""
