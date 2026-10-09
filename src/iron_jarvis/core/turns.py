"""Addressable chat turns — the registry that makes Stop reach a running turn.

THE SILENT FAILURE THIS PREVENTS: a Stop button that does nothing, and says
nothing.

Until v1.241.0 a streamed chat turn had no name. ``POST /chat/stream``
cancellation was CONNECTION-BOUND: the only cooperative check was
``request.is_disconnected()`` once per tool round, and mid-generation stop
worked solely because Starlette cancels the response generator when the HTTP
client goes away. That is enough for the dashboard, which holds the stream in
the browser tab that owns the Stop button — and it is *nothing at all* for a
caller that has no HTTP connection to drop. A browser side panel asks the
daemon to run a turn on its behalf (docs/BROWSER-SIDEBAR-PLAN.md §4, F3); the
daemon owns that turn's lifetime, so "close the connection" is not a gesture
anyone can make. Without this registry, the panel's Stop would render, click,
and change nothing, while the turn kept generating and kept billing.

WHY IN-MEMORY AND PROCESS-LOCAL (the ``core/approvals.py`` argument, and it is
the same argument): a running turn is a live object on one event loop in one
process. It cannot outlive the process that is running it, so persistence
would only manufacture orphans — a row promising that something is stoppable
when its runner is already gone. A daemon restart ends every turn and every
right to stop one, together, which is the correct outcome for both.

LOOP-AWARE, for the same reason approvals are: the turn may be driven on one
loop (an SSE response on the daemon's main loop, or a panel turn on whatever
loop the socket lives on) while ``POST /chat/turns/{id}/stop`` answers on
another. Stop therefore sets a plain flag — no future, no ``set_result`` —
and the runner reads it at its own next checkpoint. A flag crosses loops and
threads safely where a future does not; the price is that stop is
COOPERATIVE, which is honest about what it can actually interrupt.

THE ONE WAKE-UP (v1.287.0): a checkpoint that only comes round when the model
sends a frame is no checkpoint while the model has not sent its FIRST one —
a subscription CLI answers in one chunk after the whole reply, a cold local
model loads for seconds — so the side panel's Stop and close did nothing for
exactly that stretch. The runner therefore parks on :meth:`wait_stopped`
while it waits for the model, and :meth:`stop` wakes it through
``loop.call_soon_threadsafe`` on the RUNNER's loop (recorded by the runner
itself), which is the one call that is safe from any thread or loop.

WHAT STOP DOES NOT DO: it does not kill a tool that is already executing. A
tool call runs on a worker thread (v1.228.0); that thread finishes and its
write lands. Stop prevents the NEXT round and ends the answer being generated
— it is not an abort of work already in flight, and no surface may imply that
it is.

THE RUNNER OWNS THE POP, not a sweeper: the generator releases its id in a
``finally``, so an id can never stop a turn that has moved on. :meth:`stop` on
an unknown id is a clean ``False`` (the route's 404) rather than an error,
because a double-click races the release and the second click must read as
"already finished", not as a failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any, Callable, Iterator

#: How much of a thread id is kept (the ``ChatBody.thread_id`` validator cuts
#: at the same length, so a key here always matches a saved row's id).
THREAD_ID_CHARS = 80


class ThreadTurn:
    """One chat turn in flight FOR A SAVED CHAT (v1.327.0).

    WHY: the chat list could not tell a chat that is still answering, or one
    parked on a question for the user, from one that is idle. A thread's row
    now carries ``running`` and ``waiting``, read from this live state — no
    database read, nothing persisted (a turn cannot outlive its process, the
    same argument as the rest of this module).

    ``waiting`` is TRUE while the turn is parked on the user: an approval card
    (``park``/``unpark``, counted, so a batch's asks nest) or an app's question
    or model request (``watch`` — a probe the lane points at its own live ask
    registry, read only when somebody lists the chats). A probe that raises
    reads as "not waiting"; listing chats must never fail because of it.
    """

    __slots__ = ("thread_id", "_parked", "_probe", "_lock")

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self._parked = 0
        self._probe: Callable[[], Any] | None = None
        self._lock = threading.Lock()

    def park(self) -> None:
        with self._lock:
            self._parked += 1

    def unpark(self) -> None:
        # Never below zero: an unbalanced release must not hide a real ask.
        with self._lock:
            self._parked = max(0, self._parked - 1)

    def watch(self, probe: Callable[[], Any] | None) -> None:
        """Point ``waiting`` at a live check (e.g. "has an app asked?")."""
        self._probe = probe

    @property
    def waiting(self) -> bool:
        if self._parked > 0:
            return True
        probe = self._probe
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 — a broken probe never breaks a listing
            return False


def thread_key(thread_id: Any) -> str:
    """The registry key for ``thread_id`` ("" = no saved chat: counts for
    nothing)."""
    return str(thread_id or "").strip()[:THREAD_ID_CHARS]


class TurnHandle:
    """One running turn's stop flag. Read by the runner, set by the route."""

    __slots__ = (
        "turn_id", "_stopped", "_steers", "_steer_lock", "_steers_closed",
        "_wake", "_loop",
    )

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._stopped = False
        # v1.287.0: the runner's wake-up, bound by the runner on its own loop
        # the first time it parks (see :meth:`wait_stopped`). None until then.
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # v1.278.0: notes queued for this turn from another connection, taken
        # by the runner at its next round boundary (the sidebar's steer
        # contract, now reachable by any caller that named its turn).
        self._steers: list[str] = []
        self._steer_lock = threading.Lock()
        # v1.287.0: set once the runner has read its LAST note (see
        # :meth:`close_steers`); a note arriving after that is refused.
        self._steers_closed = False

    @property
    def stopped(self) -> bool:
        """True once somebody asked this turn to stop.

        A bool read/write is atomic under the GIL, so this needs no lock and
        no loop marshalling — which is exactly why a flag was chosen over a
        future (see the module docstring)."""
        return self._stopped

    def stop(self) -> None:
        self._stopped = True
        # Wake a runner parked on the model (v1.287.0). The flag is written
        # FIRST and the runner binds its event BEFORE it reads the flag, so
        # either this sees the event or the runner sees the flag — a stop can
        # never fall between the two.
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # the runner's loop is closed: nothing to wake
                pass

    async def wait_stopped(self) -> None:
        """Return once somebody asks this turn to stop (v1.287.0).

        Called by the runner, on the runner's loop, to race the model's next
        frame against Stop. Binds the wake-up to THIS loop on first use,
        then reads the flag, so a stop that landed earlier returns at once.
        """
        if self._wake is None:
            self._wake = asyncio.Event()
            self._loop = asyncio.get_running_loop()
        if self._stopped:
            return
        await self._wake.wait()

    def queue_steer(self, text: str) -> bool:
        """Queue one note for the runner. Empty text is not a note, and a turn
        that has closed its queue takes none (v1.287.0) — False either way."""
        note = str(text or "").strip()
        if not note:
            return False
        with self._steer_lock:
            if self._steers_closed:
                return False
            self._steers.append(note)
        return True

    def take_steers(self) -> list[str]:
        """Every queued note, in order, once. The runner calls this at the
        round boundary; the list is empty afterwards."""
        with self._steer_lock:
            taken = self._steers[:]
            self._steers.clear()
        return taken

    def close_steers(self) -> list[str]:
        """The notes still queued, once, and NO MORE after this (v1.287.0).

        THE SILENT FAILURE THIS PREVENTS: a note typed while the model writes
        its final answer. There is no round boundary left to read it, the
        route had already answered "queued", and the handle's release threw
        it away — the user's words vanished. The runner calls this once its
        loop is over, hands what it gets back to the caller as unread, and
        from then on :meth:`queue_steer` refuses, so the route answers 404
        instead of accepting a note that nothing will ever read or return.
        """
        with self._steer_lock:
            self._steers_closed = True
            left = self._steers[:]
            self._steers.clear()
        return left


class TurnRegistry:
    """Running turns, keyed by the caller-supplied ``turn_id``.

    Purely additive: a turn that supplies no id never registers here, and
    behaves exactly as turns did before v1.241.0. Nothing mints an id
    server-side — an unnamed turn is an unaddressable turn, and saying so is
    better than handing back a name whose only user would be a client that did
    not ask for one.
    """

    def __init__(self) -> None:
        self._turns: dict[str, TurnHandle] = {}
        # The dict is touched from the runner's loop AND from the stop route's
        # loop/thread; a plain lock is enough because every critical section
        # is a single dict operation.
        self._lock = threading.Lock()
        #: v1.327.0: saved-chat id -> the turns in flight for it, NAMED OR
        #: NOT (a turn with no ``turn_id`` still runs for its chat). Separate
        #: from ``_turns`` because a thread can run without a turn name and
        #: two windows can run two turns on one chat at once.
        self._threads: dict[str, list[ThreadTurn]] = {}

    def register(self, turn_id: str) -> TurnHandle:
        """Claim ``turn_id`` for a turn that is about to run.

        A repeated id REPLACES the previous handle. The alternative — refusing
        — would strand the new turn permanently unstoppable, which is the
        failure this module exists to prevent; the displaced runner still ends
        normally, it merely stops being addressable, and its own ``finally``
        no longer removes an entry it does not own (see :meth:`release`).
        """
        handle = TurnHandle(str(turn_id))
        with self._lock:
            self._turns[str(turn_id)] = handle
        return handle

    def stop(self, turn_id: str) -> bool:
        """Ask a running turn to stop. False = unknown or already finished."""
        with self._lock:
            handle = self._turns.get(str(turn_id))
        if handle is None:
            return False
        handle.stop()
        return True

    def steer(self, turn_id: str, text: str) -> bool:
        """Queue a note for a running turn (v1.278.0). False = unknown or
        finished (nothing will ever read it), or an empty note."""
        note = str(text or "").strip()
        if not note:
            return False
        with self._lock:
            handle = self._turns.get(str(turn_id))
        if handle is None:
            return False
        # v1.287.0: False too once the runner has read its last note.
        return handle.queue_steer(note)

    def take_steers(self, turn_id: str) -> list[str]:
        """The notes queued for ``turn_id`` since the last take — [] for an
        unknown id, so a runner that outlived its registration reads nothing."""
        with self._lock:
            handle = self._turns.get(str(turn_id))
        return handle.take_steers() if handle is not None else []

    def release(self, turn_id: str, handle: TurnHandle | None = None) -> None:
        """The runner is done with this id. After this, :meth:`stop` reports it
        unknown, so a late click reads as "already finished".

        ``handle`` scopes the removal to the entry this runner actually
        registered: a turn whose id was later re-registered by somebody else
        must not delete the newcomer's entry on its way out.
        """
        key = str(turn_id)
        with self._lock:
            current = self._turns.get(key)
            if current is None:
                return
            if handle is not None and current is not handle:
                return
            self._turns.pop(key, None)

    def is_running(self, turn_id: str) -> bool:
        with self._lock:
            return str(turn_id) in self._turns

    def running_ids(self) -> list[str]:
        """Snapshot of the addressable turns (read-only)."""
        with self._lock:
            return list(self._turns)

    # -- per-chat state (v1.327.0) ---------------------------------------- #

    @staticmethod
    def new_thread_turn(thread_id: Any) -> ThreadTurn | None:
        """A NOT-YET-ENTERED entry for ``thread_id``; None for a turn with no
        saved chat (it counts for nothing). Made before the turn starts so
        the lane can hold it; entered by :meth:`enter_thread` when it runs."""
        key = thread_key(thread_id)
        return ThreadTurn(key) if key else None

    def enter_thread(self, entry: ThreadTurn | None) -> None:
        if entry is None:
            return
        with self._lock:
            self._threads.setdefault(entry.thread_id, []).append(entry)

    def leave_thread(self, entry: ThreadTurn | None) -> None:
        """Remove exactly ``entry`` (by identity); the chat stops reading as
        running once its last turn has left."""
        if entry is None:
            return
        with self._lock:
            rows = self._threads.get(entry.thread_id)
            if not rows:
                return
            self._threads[entry.thread_id] = [e for e in rows if e is not entry]
            if not self._threads[entry.thread_id]:
                self._threads.pop(entry.thread_id, None)

    @contextlib.contextmanager
    def thread_turn(self, thread_id: Any) -> Iterator[ThreadTurn | None]:
        """``with TURNS.thread_turn(body.thread_id):`` — the chat reads as
        running for the block's lifetime, however it ends."""
        entry = self.new_thread_turn(thread_id)
        self.enter_thread(entry)
        try:
            yield entry
        finally:
            self.leave_thread(entry)

    def thread_states(self) -> dict[str, dict[str, bool]]:
        """``{thread id: {"running": True, "waiting": bool}}`` for every chat
        with a turn in flight — ONE snapshot for a whole listing. The probes
        run OUTSIDE the lock (they read other registries)."""
        with self._lock:
            snap = {tid: list(rows) for tid, rows in self._threads.items() if rows}
        return {
            tid: {"running": True, "waiting": any(e.waiting for e in rows)}
            for tid, rows in snap.items()
        }


#: THE process-local registry. One per daemon process, like the approval
#: registry it mirrors — every surface that can start an addressable turn and
#: every surface that can stop one must reach the same instance or a Stop
#: press answers a copy nobody is running.
TURNS = TurnRegistry()


class _Tracked:
    """The context manager :meth:`InflightCounter.track` returns."""

    __slots__ = ("_counter",)

    def __init__(self, counter: "InflightCounter") -> None:
        self._counter = counter

    def __enter__(self) -> "InflightCounter":
        self._counter.enter()
        return self._counter

    def __exit__(self, *_exc: object) -> bool:
        self._counter.exit()
        return False


class InflightCounter:
    """How many chat replies are being generated RIGHT NOW (v1.249.0, R-02).

    THE SILENT FAILURE THIS CLOSES: "Restart to update" force-killed the
    daemon mid-reply with no warning, because ``/system/activity`` counted
    Session rows and workflow runs — and a chat turn has neither (it runs as
    session id ``"chat"``). The reply was cut off mid-sentence and nothing had
    said it would be.

    IN-MEMORY AND PROCESS-LOCAL for the same reason :class:`TurnRegistry` is:
    a reply in flight is a live object on one loop in one process, and a
    restart ends every one of them. Counted, never named — this answers "is
    anything being written?", and no surface may read more into it than that.
    """

    __slots__ = ("_n", "_lock")

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self._n += 1

    def exit(self) -> None:
        # Never below zero: an unbalanced release must not make the daemon
        # report "nothing is running" while a reply is still being written.
        with self._lock:
            self._n = max(0, self._n - 1)

    def count(self) -> int:
        with self._lock:
            return self._n

    def track(self) -> _Tracked:
        """``with CHAT_INFLIGHT.track():`` — counted for the block's lifetime."""
        return _Tracked(self)


#: THE chat-reply counter (one per daemon process, like TURNS above).
CHAT_INFLIGHT = InflightCounter()
