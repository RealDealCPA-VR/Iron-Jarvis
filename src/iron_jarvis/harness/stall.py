"""NEVER STALLS SILENTLY (v1.392.0, the long-jobs plan's Phase 3).

A coding session's reply runs for hours with no step limit (v1.385.0). What
bounded it before this was per call only — about 3 minutes of silence for
Claude Code, 5 for Codex, 60 s per HTTP phase, 10 minutes per tool — and
nothing watched the reply AS A WHOLE: a step that hung inside those bounds, or
a tool that ignored them (a thread nobody can kill), left the user watching a
spinner with no word about what it was waiting on.

THE WATCH (:class:`StallWatch`, :func:`watched`). ``routes/build.
recorded_turn`` passes the turn's frames through :func:`watched`, which
drives the lane from ONE pump task (the v1.287.0 rule: a task-affine timeout
or cancel scope the lane holds across a ``yield`` keeps working) and wakes
every :data:`TICK_S` to ask the watch. PROGRESS is any frame of the turn (a
token, a step starting or ending, a helper's row, an app's progress) AND any
output a running tool produces — a long command that keeps printing is not a
stall: the command runners (``core/procs.run`` / ``run_to_files``, the
process tools) call :func:`touch` through a ContextVar the pump task carries
into every tool task and worker thread (:func:`progress_scope`). A reply
PARKED ON THE USER (an approval card, its own question, an app's question, a
helper's card) is never a stall: the card is the notice.

ONE NOTICE PER EPISODE. With no progress for :data:`STALL_AFTER_S` the watch
opens an episode: a ``stall`` frame on the live line ("No progress for 10
minutes — waiting on <the step, in the run log's words>") and ONE
``build.turn_stalled`` event for the bell (``routes/build``). The next progress
ends the episode (``stall`` with ``over: true``); a later silence is a new
episode with its own notice.

STOP THE STEP (:func:`run_step`, :func:`stop_step`). Each tool call of a
coding session's turn runs in its own task, registered by (turn, call id).
``POST /build/sessions/{id}/stop-step`` cancels EXACTLY that task: a command's
process tree is killed by the shell tool's own cancel path, the registry
writes the call's failed row with the words :data:`STOPPED_LEDGER_NOTE`
("stopped by you"), and the lane hands the model :func:`stopped_result` — the
reply carries on without the step. Every other call, and the reply, go on.

THE PHONE (decision 3): :data:`PHONE_AFTER_S` — a coding session's card that
stays unanswered in the app this long is published as ``approval.requested``
(the agent runtime's own event), so ``comm/prompts`` registers the answerable
phone prompt and the notifier sends the one phone line; the answer resolves the
card through the one registry. The lane decides which cards may go
(:func:`phone_may_ask` — never a safety card).

Every time this module measures is read through :data:`CLOCK` (a test swaps
it for a clock it moves itself — never a bound on a sleep).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
import weakref
from typing import Any, AsyncIterator, Awaitable, Callable

#: No progress for this long is a stall (10 minutes).
STALL_AFTER_S = 600.0
#: A coding session's card unanswered in the app this long goes to the phone
#: (decision 3: 5 minutes).
PHONE_AFTER_S = 300.0
#: How often the watch looks (the notice lands within this of the window).
TICK_S = 5.0
#: v1.392.0 review: how long past its own DEADLINE (the registry's
#: ``config.tool_call_timeout_s``, 600 s by default — the window itself) a
#: step may still run before it is a stall: the deadline's cancel, the
#: command tree's kill and the ledger row take a moment. A hang detector,
#: never a speed bar.
DEADLINE_GRACE_S = 60.0
#: The clock every interval here is measured on (monotonic seconds). Tests
#: replace it with a clock they move by hand.
CLOCK: Callable[[], float] = time.monotonic

#: The live line's frame (and its end: ``{"over": true}``).
STALL_FRAME = "stall"
#: What the registry writes on the failed row of a step the user stopped.
STOPPED_LEDGER_NOTE = (
    "stopped by you while it was running (it had shown no progress) — "
    "anything it did before it was stopped may have landed"
)

#: A once-only card's reasons that are SAFETY cards (``lib/onceReason``): a
#: project's or a plugin's command line, a commit / push on a session's
#: branch, a page click that pays or sends. Never sent to the phone.
SAFETY_REASONS = frozenset({"command", "git", "page"})


def now() -> float:
    """The watch's clock, now."""
    return float(CLOCK())


def minutes_words(seconds: float) -> str:
    """``10 minutes`` / ``1 minute`` / ``30 seconds`` — how long the window
    is, in words."""
    s = max(0, int(round(float(seconds))))
    if s >= 60 and s % 60 == 0:
        m = s // 60
        return f"{m} minute" + ("" if m == 1 else "s")
    if s >= 60:
        return f"{s // 60} min {s % 60} s"
    return f"{s} second" + ("" if s == 1 else "s")


# --------------------------------------------------------------------------- #
# progress from inside a running tool
# --------------------------------------------------------------------------- #

def progress_scope(fn: "Callable[[], None] | None"):
    """Arm ``fn`` as this context's progress callback (``core/progress``: the
    pump task that drives a coding session's lane carries it into every tool
    task and worker thread)."""
    from ..core.progress import scope

    return scope(fn)


def touch() -> None:
    """A running tool produced something (``core/progress.touch``)."""
    from ..core.progress import touch as _touch

    _touch()


def listening() -> bool:
    """Does anything read :func:`touch` here?"""
    from ..core.progress import current

    return current() is not None


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #


def step_words(name: str, args: Any) -> str:
    """A running step in the run log's words ("running a command (pytest)",
    "reading app.py"). Never raises."""
    try:
        from ..agents.mission import doing_phrase, tool_line
        from .step_target import step_target

        safe = args if isinstance(args, dict) else {}
        line = tool_line(str(name or ""), safe, True)
        doing = doing_phrase(line) or line
        target = step_target(str(name or ""), safe) or ""
        if target and target.lower() not in doing.lower():
            doing = f"{doing} ({target})"
        return doing.strip() or "a step"
    except Exception:  # noqa: BLE001 — wording only
        return "a step"


def model_words(provider: str, manager: Any = None) -> str:
    """What a reply waits on when no tool is running: the model's answer,
    named the failure words' way ("Claude Code's answer"; a fleet node by
    its label when the provider manager is given)."""
    try:
        from ..providers.failure_words import plain_name

        name = plain_name(str(provider or ""), manager).strip()
    except Exception:  # noqa: BLE001
        name = ""
    if not name or name.startswith("fleet-") or name == str(provider or ""):
        return "the model's answer"
    return f"{name}'s answer"


def notice_words(after_s: float, waiting_on: str) -> str:
    """The notice: "No progress for 10 minutes — waiting on <the step>"."""
    return f"No progress for {minutes_words(after_s)} — waiting on {waiting_on}"


# --------------------------------------------------------------------------- #
# the watch
# --------------------------------------------------------------------------- #


def _frames(chunk: str) -> list[tuple[str, str]]:
    from ..daemon.durable_turns import parse_chunk

    return parse_chunk(chunk)


class StallWatch:
    """One reply's stall watch. ``parked`` answers whether the reply waits on
    the user right now (the reply writer's open asks)."""

    def __init__(self, *, parked: "Callable[[], bool] | None" = None,
                 after_s: "float | None" = None, provider: str = "",
                 manager: Any = None,
                 step_deadline: "Callable[[str], float | None] | None" = None) -> None:
        self.after_s = float(STALL_AFTER_S if after_s is None else after_s)
        self._parked = parked or (lambda: False)
        self._last = now()
        #: call id -> (tool name, its redacted args, when it started) of a
        #: step that started and has not finished, in the order they started.
        self._running: dict[str, tuple[str, Any, float]] = {}
        #: v1.392.0 review: the deadline the registry puts on a step by its
        #: tool name (None = none: no deadline, or a tool exempt from it). A
        #: step its own deadline ends AT the window is not a stall — the
        #: notice would land a moment before the deadline ended it, with a
        #: "Stop the step" that could only answer "already finished".
        self._step_deadline = step_deadline
        self._helper_calls: set[str] = set()
        #: v1.392.0 review: WHO the reply waits on when no step runs — the
        #: turn's held model (``routes/build.recorded_turn`` passes it), then
        #: whoever answered its latest model call (the Timeline's
        #: ``model_call`` frame). The doer read a ``meta`` frame no code
        #: emits, so every notice said "the model's answer".
        self.provider = str(provider or "")
        self.manager = manager
        #: The open episode's notice (None = none).
        self.episode: "dict[str, Any] | None" = None
        #: How many episodes the reply had (for tests and the line).
        self.episodes = 0
        self._lock = threading.Lock()

    # -- progress ----------------------------------------------------------- #

    def progress(self) -> None:
        """Something moved. Thread-safe (a float assignment)."""
        self._last = now()

    def saw(self, chunk: str) -> bool:
        """Fold one chunk of the turn's frames: every frame is progress; the
        steps that run and the model that answers are kept for the words.
        False for a chunk with no frame (a keep-alive comment)."""
        parsed = _frames(chunk)
        if not parsed:
            return False
        self.progress()
        for event, data in parsed:
            if event not in ("tool_call", "meta", "helper", "model_call"):
                continue
            try:
                frame = json.loads(data or "{}")
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            if event in ("meta", "model_call"):
                self.provider = str(frame.get("provider") or self.provider)
            elif event == "helper":
                hid = str(frame.get("id") or "")
                if hid:
                    self._helper_calls.add(hid)
            else:
                cid = str(frame.get("id") or "")
                if frame.get("status") == "started":
                    self._running[cid] = (str(frame.get("name") or ""), frame.get("args"), now())
                elif frame.get("status") == "finished":
                    self._running.pop(cid, None)
        return True

    # -- the check ---------------------------------------------------------- #

    def waiting_on(self) -> tuple[str, "dict[str, Any] | None"]:
        """``(words, step)`` — what the reply waits on now; ``step`` is
        ``{id, name}`` of the oldest running tool call (None when the model
        is answering, or the step is a helper's own call)."""
        for cid, (name, args, _started) in self._running.items():
            if cid in self._helper_calls:
                return f"the helper it started ({step_words(name, args)})", None
            return f"this step: {step_words(name, args)}", {"id": cid, "name": name}
        return model_words(self.provider, self.manager), None

    def _held_by_deadline(self) -> bool:
        """Is the step the reply waits on one its OWN deadline is ending at
        (or before) the window? Then the deadline is the notice: it ends the
        step with a failed result the model reads, and the reply goes on.
        Only a step that OUTLIVES its deadline by :data:`DEADLINE_GRACE_S`
        (something the deadline could not stop) is a stall. A deadline
        longer than the window does not hold the notice. Never raises."""
        if self._step_deadline is None:
            return False
        for cid, (name, _args, started) in self._running.items():
            if cid in self._helper_calls:
                return False
            try:
                deadline = self._step_deadline(name)
            except Exception:  # noqa: BLE001 — a notice is never lost to a lookup
                return False
            if not deadline or deadline <= 0:
                return False
            ends = float(started) + float(deadline)
            return (
                ends <= self._last + self.after_s + DEADLINE_GRACE_S
                and now() < ends + DEADLINE_GRACE_S
            )
        return False

    def check(self) -> "dict[str, Any] | None":
        """The frame data to send now, or None: a NEW episode's notice once
        the reply has made no progress for :attr:`after_s` (never while it
        waits on the user), or ``{"over": True}`` when progress ended an open
        episode."""
        idle = now() - self._last
        if self.episode is not None:
            if idle < self.after_s:
                self.episode = None
                return {"over": True}
            return None
        if self._parked():
            # Waiting on the user is not a stall; the clock starts again from
            # the answer (its frame is progress).
            return None
        if idle < self.after_s:
            return None
        if self._held_by_deadline():
            return None
        words, step = self.waiting_on()
        notice: dict[str, Any] = {
            "words": notice_words(self.after_s, words),
            "minutes": round(self.after_s / 60, 2),
        }
        if step is not None:
            notice["step"] = step
        self.episode = notice
        self.episodes += 1
        return notice

    def ended(self) -> "dict[str, Any] | None":
        """Called on a frame: an open episode ends with it."""
        if self.episode is None:
            return None
        self.episode = None
        return {"over": True}


def _sse(event: str, data: Any) -> str:
    from ..daemon.durable_turns import sse

    return sse(event, data)


async def watched(frames: Any, watch: StallWatch, *,
                  announce: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
                  ) -> AsyncIterator[str]:
    """``frames`` (the lane's SSE iterator), passed through, with the stall
    notice added (see the module doc). The lane is driven by ONE pump task,
    in lock-step: it is asked for its next chunk only after the caller took
    the previous one, exactly as when the caller iterated it itself — so
    what the reply writer does with a chunk (the end written before it is
    said) keeps its order. ``announce(notice)`` is awaited once per episode
    (the bell's event). A failure of the lane is raised here unchanged."""
    loop = asyncio.get_running_loop()
    box: asyncio.Queue = asyncio.Queue(maxsize=1)
    taken = asyncio.Event()
    end = object()

    async def pump() -> None:
        try:
            async with contextlib.aclosing(frames):
                async for chunk in frames:
                    taken.clear()
                    await box.put(("chunk", chunk))
                    await taken.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — handed to the caller
            await box.put(("raise", exc))
            return
        await box.put(("end", end))

    with progress_scope(watch.progress):
        task = loop.create_task(pump())
    try:
        while True:
            try:
                kind, item = await asyncio.wait_for(box.get(), timeout=TICK_S)
            except TimeoutError:
                notice = watch.check()
                if notice is not None:
                    if not notice.get("over") and announce is not None:
                        with contextlib.suppress(Exception):
                            await announce(notice)
                    yield _sse(STALL_FRAME, notice)
                continue
            if kind == "end":
                return
            if kind == "raise":
                raise item
            over = watch.ended() if watch.saw(item) else None
            if over is not None:
                yield _sse(STALL_FRAME, over)
            yield item
            taken.set()
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await _settle(task)


async def _settle(task: "asyncio.Task[Any]") -> None:
    """Wait for a cancelled pump to unwind (the lane's own ``finally`` runs
    there). A second cancel landing here still lets it finish."""
    try:
        await asyncio.wait({task})
    except asyncio.CancelledError:
        await asyncio.wait({task})
        raise


# --------------------------------------------------------------------------- #
# Stop the step
# --------------------------------------------------------------------------- #

_STEPS_LOCK = threading.Lock()
#: turn id -> call id -> (task, tool name)
_STEPS: dict[str, dict[str, tuple["asyncio.Task[Any]", str]]] = {}
#: The step tasks the USER stopped (the registry and :func:`run_step` read it).
_STOPPED: "weakref.WeakSet[asyncio.Task[Any]]" = weakref.WeakSet()


def stopped_result(name: str = "", after_s: "float | None" = None) -> str:
    """What the model is told about a step the user stopped (plain words)."""
    window = minutes_words(STALL_AFTER_S if after_s is None else after_s)
    what = f"The {name} step" if name else "This step"
    return (
        f"{what} was stopped by the user: it had shown no progress for {window}. It did not finish, "
        "and anything it did before it was stopped may have taken effect. Carry on without it; do "
        "not start it again unless the user asks."
    )


def stopped_by_user(task: "asyncio.Task[Any] | None" = None) -> bool:
    """Was this task (default: the current one) stopped with Stop the step?"""
    if task is None:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            return False
    return task is not None and task in _STOPPED


async def run_step(turn_id: str, call_id: str, name: str,
                   make: "Callable[[], Awaitable[Any]]",
                   stopped: "Callable[[], Any]") -> Any:
    """Run one tool call of a coding session's turn as its OWN task,
    registered so :func:`stop_step` can cancel exactly it. A cancel the USER
    asked for returns ``stopped()`` (the call's result: the model is told);
    a cancel of the turn itself (Stop, a shutdown) cancels the call and goes
    on unwinding, as before."""
    task: "asyncio.Task[Any]" = asyncio.ensure_future(make())
    key, cid = str(turn_id or ""), str(call_id or "")
    with _STEPS_LOCK:
        _STEPS.setdefault(key, {})[cid] = (task, str(name or ""))
    try:
        return await task
    except asyncio.CancelledError:
        cur = asyncio.current_task()
        mine = cur is not None and cur.cancelling() > 0
        if task in _STOPPED and task.done() and not mine:
            return stopped()
        if not task.done():
            task.cancel()
        raise
    finally:
        with _STEPS_LOCK:
            steps = _STEPS.get(key)
            if steps is not None and steps.get(cid, (None,))[0] is task:
                steps.pop(cid, None)
                if not steps:
                    _STEPS.pop(key, None)


def running_steps(turn_id: str) -> list[str]:
    """The call ids of ``turn_id``'s steps that are running now."""
    with _STEPS_LOCK:
        return list((_STEPS.get(str(turn_id or "")) or {}).keys())


def stop_step(turn_id: str, call_id: str) -> bool:
    """Cancel ONE running step of a turn (the user's Stop the step). False
    when no such step runs (it finished, or the id is not this turn's)."""
    with _STEPS_LOCK:
        entry = (_STEPS.get(str(turn_id or "")) or {}).get(str(call_id or ""))
    if entry is None:
        return False
    task = entry[0]
    if task.done():
        return False
    _STOPPED.add(task)
    try:
        home = task.get_loop()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if home is running:
            task.cancel()
        else:
            home.call_soon_threadsafe(task.cancel)
    except RuntimeError:
        return False
    return True


# --------------------------------------------------------------------------- #
# the phone (decision 3)
# --------------------------------------------------------------------------- #


def phone_may_ask(*, once_only: bool, local_ask: bool, priced: bool) -> bool:
    """May a coding session's card go to the phone? Never a SAFETY card: a
    once-only card (the plan door, a project's or plugin's command line, a
    commit / push on the session's branch, a page click that pays or sends),
    a Local only "send to the cloud" card, or a priced spend card — the whole
    card must be read where it is answered."""
    return not (once_only or local_ask or priced)


_KEEP: set["asyncio.Task[Any]"] = set()


def keep(task: "asyncio.Task[Any]") -> "asyncio.Task[Any]":
    """Hold a fire-and-forget task until it ends (the loop keeps only a weak
    reference)."""
    _KEEP.add(task)
    task.add_done_callback(_KEEP.discard)
    return task


#: The ``surface`` a coding session's card carries on its ``approval.
#: requested`` (the pending listing leaves such a card to the session's own
#: waiting row; the phone words name the session, not "an agent").
PHONE_SURFACE = "build"


class PhoneAsk:
    """One coding session's card, on its way to the phone once nobody has
    answered it in the app for :data:`PHONE_AFTER_S` (decision 3).

    :meth:`tick` is called on every slice of the card's wait: the FIRST tick
    past the window publishes ``approval.requested`` — the agent runtime's
    own event, so ``comm/prompts`` registers the answerable prompt and the
    notifier sends the one phone line (never "This PC": the bell and the
    session's own row say it there) — off the turn's way, in a task of its
    own. :meth:`close` (the card's wait is over: answered anywhere, or the
    reply stopped) publishes ``approval.resolved`` AFTER it, so the phone's
    prompt expires with the card. A card answered within the window sends
    nothing at all. Names only on the event — never the call's arguments."""

    def __init__(self, platform: Any, approval_id: str, tool: str, *, thread_id: str = "",
                 count: int = 1) -> None:
        self.platform = platform
        self.approval_id = str(approval_id or "")
        self.tool = str(tool or "")
        self.thread_id = str(thread_id or "")
        self.count = int(count or 1)
        self.asked_at = now()
        self.sent: "asyncio.Task[Any] | None" = None

    def due(self) -> bool:
        return self.sent is None and now() - self.asked_at >= PHONE_AFTER_S

    def tick(self) -> None:
        if not self.due():
            return
        try:
            self.sent = keep(asyncio.ensure_future(self._publish()))
        except RuntimeError:  # no loop (a test double): nothing to send
            self.sent = None

    async def _title(self) -> str:
        if not self.thread_id:
            return ""
        try:
            from .sessions import get_row

            row = await asyncio.to_thread(get_row, self.platform.engine, self.thread_id)
            return str(getattr(row, "title", "") or "") if row is not None else ""
        except Exception:  # noqa: BLE001 — the line without a title
            return ""

    async def _publish(self) -> None:
        bus = getattr(self.platform, "event_bus", None)
        if bus is None:
            return
        try:
            from ..core.events import EventType

            payload: dict[str, Any] = {
                "approval_id": self.approval_id,
                "tool": self.tool,
                # 0 = no expiry: the reply waits for an answer (v1.247.0).
                "timeout_s": 0,
                "surface": PHONE_SURFACE,
                "thread_id": self.thread_id,
                "title": await self._title(),
            }
            if self.count > 1:
                payload["count"] = self.count
            await bus.publish(EventType.APPROVAL_REQUESTED, payload)
        except Exception:  # noqa: BLE001 — the card in the app still waits
            import logging

            logging.getLogger(__name__).warning("sending a Build card to the phone failed", exc_info=True)

    def close(self, decision: str) -> None:
        """The card's wait is over. Only a card that went to the phone says
        so (``approval.resolved``), after its request was published."""
        if self.sent is None:
            return
        sent = self.sent
        bus = getattr(self.platform, "event_bus", None)
        if bus is None:
            return

        async def _resolved() -> None:
            with contextlib.suppress(BaseException):
                await sent
            try:
                from ..core.events import EventType

                await bus.publish(EventType.APPROVAL_RESOLVED, {
                    "approval_id": self.approval_id, "tool": self.tool,
                    "decision": str(decision or ""), "surface": PHONE_SURFACE,
                })
            except Exception:  # noqa: BLE001
                pass

        with contextlib.suppress(RuntimeError):
            keep(asyncio.ensure_future(_resolved()))


__all__ = [
    "CLOCK", "DEADLINE_GRACE_S", "PHONE_AFTER_S", "SAFETY_REASONS", "STALL_AFTER_S", "STALL_FRAME", "STOPPED_LEDGER_NOTE",
    "StallWatch", "TICK_S", "keep", "listening", "minutes_words", "model_words", "notice_words", "now",
    "phone_may_ask", "progress_scope", "run_step", "running_steps", "step_words", "stop_step",
    "stopped_by_user", "stopped_result", "touch", "watched",
]
