"""Every model call on a usage row (v1.529.0, owner decisions 3 and 4).

Two gaps the third fix plan left open, closed here:

* **A model call made INSIDE A TOOL was on no usage row** (decision 3): a
  browser screenshot's question, ``view_image``, reading text in a picture,
  a consult. The lanes count only their own completions (their
  ``UsageTally``), and the agent runtime only its steps, so the Usage page
  never showed these. ONE seam counts them: the router calls
  :func:`note_model_call` after every completion that answered
  (``ModelRouter._timed_complete``) and at every stream's terminal frame
  (``ModelRouter._stream_one``). ``tools.registry.invoke`` opens a
  :func:`tool_scope` around each tool's ``execute``; a call made inside it
  gets the tool call's OWN usage row at once (an :class:`AgentRun`, like a
  conversation summary's row: plain words in ``result`` saying what it was
  for), on the turn's chat. One row per tool call: the first call inserts
  it, each later call of the same tool call brings it up to date.

  What already counts its own calls opens :func:`counted_scope` and so hides
  any tool scope around it: an agent run (its row is saved after each step;
  a ``delegate`` tool runs one), and an app's (MCP pack's) tool, whose only
  model call is the sampling request ``daemon/mcp_turn`` bills itself.

* **A hard kill mid-reply lost the usage of the calls already made**
  (decision 4): a chat reply's row is written once, at its end. The two
  chat lanes count with a :class:`JournaledTally`: each call it counts is
  also written as an :class:`~iron_jarvis.core.models.OpenModelCall`. The
  lane's end row (``chat_turn._write_chat_usage``) deletes that reply's open
  calls IN THE SAME transaction that inserts the row
  (:meth:`TurnLedger.close_in`), so a crash between the two can never count
  twice; at the next start :func:`settle_open_calls` folds whatever an
  earlier daemon left, one row per reply (FAILED, with :data:`CUT_OFF_WORDS`),
  and deletes them, one transaction per reply. An agent run needs none of
  this (its row already carries every step). A call with no owner writes no
  open record (as before).

Nothing here runs on the event loop: every write goes to ONE worker thread
(:data:`_POOL`, in order, so a row's update never overtakes its insert),
bounded by :data:`MAX_QUEUED`. Nothing here ever raises into a reply.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .pricing import UsageTally

log = logging.getLogger(__name__)

#: This daemon. An open record carries it, and the start-up step settles only
#: records another (earlier) daemon wrote — never a reply running now.
PROCESS_ID = uuid.uuid4().hex

#: The words on a row the start-up step writes for a reply cut off by a stop
#: (VOCABULARY.md: plain words, no engine words).
CUT_OFF_WORDS = (
    "This reply was cut off when Iron Jarvis stopped (a restart or a crash); "
    "the model calls it had already made are counted here"
)
#: Fourth plan PG4: the words on that row for a reply that ENDED but whose
#: own usage row could not be saved then (:meth:`TurnLedger.mark_ended`).
ENDED_UNSAVED_WORDS = (
    "This reply ended, but its usage could not be saved then; "
    "the model calls it made are counted here"
)
#: The words on a tool call's own row, by tool (the row's ``result``).
TOOL_WORDS: dict[str, str] = {
    "view_image": "A picture was looked at for a step",
    "browser_screenshot": "A screenshot was looked at for a step",
}
#: Every other tool's row.
TOOL_WORDS_DEFAULT = "Model calls made by a step"
#: The session of a tool call's row outside chat (an agent run's tool): never
#: the agent's own session id — its runs are read one by one there (its
#: newest run, its delegation tree) and a usage row must not join them.
STEP_SESSION = "tool-use"
#: The most writes waiting for the worker; past it a write is dropped and
#: logged (a database that stopped answering must not grow memory).
MAX_QUEUED = 2_000
#: The most open records the start-up step folds in one start.
SETTLE_LIMIT = 20_000


# --------------------------------------------------------------------------- #
# The one worker thread
# --------------------------------------------------------------------------- #
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage-calls")
_PENDING: "set[concurrent.futures.Future[Any]]" = set()
_PENDING_LOCK = threading.Lock()
_dropped_logged = False


def _submit(fn: Any, *args: Any) -> None:
    """Queue *fn* on the worker; never raises, never blocks."""
    global _dropped_logged
    try:
        with _PENDING_LOCK:
            if len(_PENDING) >= MAX_QUEUED:
                if not _dropped_logged:
                    _dropped_logged = True
                    log.warning("usage of model calls is not being written fast enough; "
                                "some calls are missing from the usage totals")
                return
            fut = _POOL.submit(fn, *args)
            _PENDING.add(fut)
        fut.add_done_callback(_forget)
    except Exception:  # noqa: BLE001 — accounting never breaks a reply
        log.debug("could not queue a usage write", exc_info=True)


def _forget(fut: "concurrent.futures.Future[Any]") -> None:
    with _PENDING_LOCK:
        _PENDING.discard(fut)


def drain(timeout: float = 10.0) -> bool:
    """Wait (BLOCKING) for the writes queued so far. True when all ended.
    For tests and the daemon's own shutdown; never raises."""
    with _PENDING_LOCK:
        waiting = list(_PENDING)
    if not waiting:
        return True
    try:
        _done, left = concurrent.futures.wait(waiting, timeout=timeout)
    except Exception:  # noqa: BLE001
        return False
    return not left


def _tok(usage: Mapping, key: str) -> int:
    try:
        return max(0, int(usage.get(key) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


# --------------------------------------------------------------------------- #
# Decision 3: the scope a call is made in
# --------------------------------------------------------------------------- #
@dataclass
class _ToolUse:
    """The model use of ONE tool call (its own usage row)."""

    tool: str
    engine: Any
    session_id: str
    thread_id: str
    row_id: str
    started_at: Any
    tally: UsageTally = field(default_factory=UsageTally)
    calls: int = 0
    provider: str = ""
    model: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


class _Counted:
    """A scope that counts its own model calls (an agent run, an app's tool)."""


_COUNTED = _Counted()
_SCOPE: "ContextVar[_ToolUse | _Counted | None]" = ContextVar("iron_jarvis_call_scope", default=None)


def _row_session(session_id: str) -> str:
    sid = str(session_id or "")
    return "chat" if sid == "chat" or sid.startswith("build:") else STEP_SESSION


@contextlib.contextmanager
def tool_scope(name: str, ctx: Any) -> Iterator[None]:
    """Around one tool's ``execute`` (``tools.registry.invoke``): the model
    calls made inside it are this tool call's. An app's (MCP pack's) tool is
    a :func:`counted_scope` instead: its sampling request bills itself."""
    scope: "_ToolUse | _Counted | None"
    try:
        if str(name or "").startswith("mcp__"):
            scope = _COUNTED
        else:
            from ..core.ids import new_id, utcnow

            sid = str(getattr(ctx, "session_id", "") or "")
            scope = _ToolUse(
                tool=str(name or ""),
                engine=getattr(ctx, "engine", None),
                session_id=_row_session(sid),
                thread_id=str(getattr(ctx, "thread_id", "") or "") if _row_session(sid) == "chat" else "",
                row_id=new_id("run"),
                started_at=utcnow(),
            )
    except Exception:  # noqa: BLE001 — accounting never breaks a tool
        scope = None
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        try:
            _SCOPE.reset(token)
        except (ValueError, LookupError, RuntimeError):  # another context's token
            _SCOPE.set(None)


@contextlib.contextmanager
def counted_scope() -> Iterator[None]:
    """Around code that counts its own model calls (an agent run): a tool
    scope outside it does not count them a second time."""
    token = _SCOPE.set(_COUNTED)
    try:
        yield
    finally:
        try:
            _SCOPE.reset(token)
        except (ValueError, LookupError, RuntimeError):
            _SCOPE.set(None)


def note_model_call(provider: str, model: str, usage: Any) -> None:
    """THE ROUTER'S ONE SEAM: a model call answered with *usage*. Inside a
    tool call (and nothing inside it that counts its own), the tool call's
    usage row is written or brought up to date on the worker thread. Cheap,
    sync, never raises."""
    try:
        scope = _SCOPE.get()
        if not isinstance(scope, _ToolUse) or scope.engine is None:
            return
        if not isinstance(usage, Mapping):
            usage = {}
        thread = ""
        if scope.session_id == "chat" and not scope.thread_id:
            # A new chat's first reply learns its chat a moment later (CS6).
            from ..daemon import durable_turns as _durable

            _turn, thread = _durable.current_turn()
        else:
            _turn = ""
        with scope.lock:
            scope.tally.add(str(provider or ""), str(model or ""), usage)
            scope.calls += 1
            scope.provider, scope.model = str(provider or ""), str(model or "")
            if thread and not scope.thread_id:
                scope.thread_id = thread
            snap = {
                "row_id": scope.row_id,
                "session_id": scope.session_id,
                "thread_id": scope.thread_id,
                "provider": scope.provider,
                "model": scope.model,
                "steps": scope.calls,
                "input_tokens": scope.tally.input_tokens,
                "output_tokens": scope.tally.output_tokens,
                "cost_usd": round(max(0.0, scope.tally.cost_usd), 6),
                "result": TOOL_WORDS.get(scope.tool, TOOL_WORDS_DEFAULT),
                "started_at": scope.started_at,
                "turn": _turn,
            }
        _submit(_write_tool_row, scope.engine, snap)
    except Exception:  # noqa: BLE001 — accounting never breaks a reply
        log.debug("could not note a model call made by a step", exc_info=True)


def _write_tool_row(engine: Any, snap: dict[str, Any]) -> None:
    """Insert or bring up to date one tool call's usage row. BLOCKING (the
    worker); never raises."""
    try:
        from ..core.db import session_scope
        from ..core.ids import utcnow
        from ..core.models import AgentRun, AgentState, AgentType

        with session_scope(engine) as db:
            row = db.get(AgentRun, snap["row_id"])
            if row is None:
                row = AgentRun(
                    id=snap["row_id"], session_id=snap["session_id"],
                    agent_type=AgentType.BUILDER, created_at=snap["started_at"],
                )
            row.provider = snap["provider"]
            row.model = snap["model"]
            row.state = AgentState.COMPLETED
            row.steps = int(snap["steps"])
            row.input_tokens = int(snap["input_tokens"])
            row.output_tokens = int(snap["output_tokens"])
            row.cost_usd = float(snap["cost_usd"])
            row.result = snap["result"]
            row.finished_at = utcnow()
            if snap["thread_id"]:
                row.thread_id = snap["thread_id"][:80]
            db.add(row)
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not write the usage of a step's model call: it is missing from the "
                    "usage totals", exc_info=True)
        return
    if snap.get("turn") and not snap["thread_id"]:
        try:
            from ..daemon import durable_turns as _durable

            _durable.note_usage_row(snap["turn"], snap["row_id"])  # CS6: the link names it
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Decision 4: a reply's calls, written as they are counted
# --------------------------------------------------------------------------- #
class TurnLedger:
    """The open calls of ONE chat reply. ``lock`` + ``closed`` make the end
    row and a late open write exclusive: a write that runs after the end row
    sees ``closed`` and writes nothing; one that ran before it is deleted by
    the end row's own transaction."""

    def __init__(self, engine: Any, *, session_id: str = "chat", thread_id: str = "",
                 turn_id: str = "", started_at: Any = None) -> None:
        self.key = uuid.uuid4().hex
        self.engine = engine
        self.session_id = session_id
        self.thread_id = str(thread_id or "").strip()[:80]
        self.turn_id = str(turn_id or "").strip()[:80]
        self.started_at = started_at
        self.lock = threading.Lock()
        self.closed = False
        #: PG4: the reply's end state once its end row failed (else None).
        self.ended: str | None = None

    def note(self, provider: str, model: str, usage: Mapping, cost: float) -> None:
        """Queue one counted call (on the loop: cheap, never raises)."""
        if self.engine is None or self.closed:
            return
        thread = self.thread_id
        turn = self.turn_id
        if not thread:
            try:
                from ..daemon import durable_turns as _durable

                live_turn, thread = _durable.current_turn()
                turn = turn or live_turn
            except Exception:  # noqa: BLE001
                thread = ""
        fields = {
            "owner": self.key,
            "process": PROCESS_ID,
            "session_id": self.session_id,
            "thread_id": thread,
            "turn_id": turn,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "input_tokens": _tok(usage, "input_tokens"),
            "output_tokens": _tok(usage, "output_tokens"),
            "cache_read_input_tokens": _tok(usage, "cache_read_input_tokens"),
            "cache_creation_input_tokens": _tok(usage, "cache_creation_input_tokens"),
            "cost_usd": max(0.0, float(cost or 0.0)),
            "started_at": self.started_at,
        }
        _submit(self._insert, fields)

    def _insert(self, fields: dict[str, Any]) -> None:
        try:
            from ..core.db import session_scope
            from ..core.models import OpenModelCall

            with self.lock:
                if self.closed:
                    return
                with session_scope(self.engine) as db:
                    # PG4: a call that lands after a failed end row carries its mark.
                    db.add(OpenModelCall(**fields, ended=self.ended))
                    db.commit()
        except Exception:  # noqa: BLE001 — the end row still counts the call
            log.debug("could not keep a reply's model call", exc_info=True)

    @contextlib.contextmanager
    def closing(self) -> Iterator[None]:
        """Hold the ledger while the end row's transaction runs (BLOCKING).
        NOT reentrant: nothing called inside it takes ``self.lock`` again
        (:meth:`mark_ended` runs inside it and never does)."""
        with self.lock:
            yield

    def close_in(self, db: Any) -> None:
        """Delete this reply's open calls inside the END ROW's transaction
        (the caller commits, inside :meth:`closing`, then :meth:`mark_closed`)."""
        from sqlmodel import delete

        from ..core.models import OpenModelCall

        db.exec(delete(OpenModelCall).where(OpenModelCall.owner == self.key))

    def mark_closed(self) -> None:
        self.closed = True

    def mark_ended(self, state: Any) -> None:
        """Fourth plan PG4: the reply ENDED but its end row failed. Mark its
        open calls with the state that row would have had, so the next start
        does not call it "cut off". BLOCKING; never raises.

        The caller runs this INSIDE :meth:`closing` (the same hold as the end
        row), so it never takes ``self.lock`` itself: that lock is not
        reentrant and the end row's thread would wait on itself forever.
        ``closed`` stays False: the failed row took no call with it, so a call
        still waiting for the worker lands afterwards, carrying the mark."""
        value = str(getattr(state, "value", state) or "") or "completed"
        self.ended = value
        if self.engine is None:
            return
        try:
            from sqlmodel import update

            from ..core.db import session_scope
            from ..core.models import OpenModelCall

            with session_scope(self.engine) as db:
                db.exec(update(OpenModelCall).where(OpenModelCall.owner == self.key)
                        .values(ended=value))
                db.commit()
        except Exception:  # noqa: BLE001 — the calls kept so far stay unmarked
            log.debug("could not mark a reply's model calls as ended", exc_info=True)


class JournaledTally(UsageTally):
    """A chat reply's :class:`UsageTally` that also writes each call it
    counts as an open record (:class:`TurnLedger`). The totals are exactly
    the plain tally's."""

    def __init__(self, engine: Any = None, **ledger: Any) -> None:
        super().__init__()
        self.ledger = TurnLedger(engine, **ledger)

    def add(self, provider: str, model: str, usage: Mapping | None) -> float:
        cost = super().add(provider, model, usage)
        try:
            self.ledger.note(provider, model, usage if isinstance(usage, Mapping) else {}, cost)
        except Exception:  # noqa: BLE001 — accounting never breaks a reply
            log.debug("could not keep a reply's model call", exc_info=True)
        return cost


def turn_ledger(tally: Any) -> "TurnLedger | None":
    """The ledger of a lane's tally, or None (a plain tally)."""
    led = getattr(tally, "ledger", None)
    return led if isinstance(led, TurnLedger) else None


# --------------------------------------------------------------------------- #
# The start-up step
# --------------------------------------------------------------------------- #
def settle_open_calls(engine: Any) -> int:
    """Fold the open calls an earlier daemon left (a reply cut off by a
    restart or a crash) into ONE usage row per reply, then delete them — one
    transaction per reply, so a second start adds nothing. BLOCKING (a boot
    step runs it on a worker thread, bounded). Returns the rows written.
    Never raises but what the boot step reports."""
    from sqlmodel import delete, select

    from ..core.db import session_scope
    from ..core.models import AgentRun, AgentState, AgentType, ChatTurnRecord, OpenModelCall

    with session_scope(engine) as db:
        rows = list(db.exec(
            select(OpenModelCall)
            .where(OpenModelCall.process != PROCESS_ID)
            .order_by(OpenModelCall.created_at)  # type: ignore[arg-type]
            .limit(SETTLE_LIMIT)
        ).all())
    groups: dict[str, list[Any]] = {}
    for r in rows:
        groups.setdefault(r.owner, []).append(r)
    written = 0
    for owner, calls in groups.items():
        try:
            thread = next((c.thread_id for c in calls if c.thread_id), "")
            turn = next((c.turn_id for c in calls if c.turn_id), "")
            last = calls[-1]
            ids = [c.id for c in calls]
            # PG4: a reply that ENDED (its end row failed) keeps its own state.
            ended = next((c.ended for c in calls if getattr(c, "ended", None)), None)
            state, words = AgentState.FAILED, CUT_OFF_WORDS
            if ended:
                words = ENDED_UNSAVED_WORDS
                try:
                    state = AgentState(ended)
                except ValueError:
                    state = AgentState.COMPLETED
            with session_scope(engine) as db:
                if not thread and turn:
                    thread = str(db.exec(
                        select(ChatTurnRecord.thread_id).where(ChatTurnRecord.id == turn)
                    ).first() or "")
                started = next((c.started_at for c in calls if c.started_at is not None), None)
                db.add(AgentRun(
                    session_id=last.session_id or "chat",
                    agent_type=AgentType.BUILDER,
                    provider=last.provider,
                    model=last.model,
                    state=state,
                    steps=len(calls),
                    result=words,
                    input_tokens=sum(int(c.input_tokens or 0) for c in calls),
                    output_tokens=sum(int(c.output_tokens or 0) for c in calls),
                    cost_usd=round(sum(float(c.cost_usd or 0.0) for c in calls), 6),
                    created_at=started or calls[0].created_at,
                    finished_at=last.created_at,
                    thread_id=(thread[:80] or None),
                ))
                db.exec(delete(OpenModelCall).where(OpenModelCall.id.in_(ids)))  # type: ignore[union-attr]
                db.commit()
            written += 1
        except Exception:  # noqa: BLE001 — one reply's fault never stops the rest
            log.warning("could not count the model calls of a reply cut off by a stop "
                        "(reply %s)", owner, exc_info=True)
    if written:
        log.info("counted the model calls of %d reply(ies) cut off when Iron Jarvis stopped", written)
    return written


__all__ = [
    "CUT_OFF_WORDS",
    "ENDED_UNSAVED_WORDS",
    "JournaledTally",
    "PROCESS_ID",
    "STEP_SESSION",
    "TOOL_WORDS",
    "TOOL_WORDS_DEFAULT",
    "TurnLedger",
    "counted_scope",
    "drain",
    "note_model_call",
    "settle_open_calls",
    "tool_scope",
    "turn_ledger",
]
