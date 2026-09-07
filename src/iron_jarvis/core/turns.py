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
another. Stop therefore sets a plain flag — no future, no ``set_result``, no
wake-up needed — and the runner reads it at its own next checkpoint. A flag
crosses loops and threads safely where a future does not; the price is that
stop is COOPERATIVE, which is honest about what it can actually interrupt.

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

import threading


class TurnHandle:
    """One running turn's stop flag. Read by the runner, set by the route."""

    __slots__ = ("turn_id", "_stopped")

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._stopped = False

    @property
    def stopped(self) -> bool:
        """True once somebody asked this turn to stop.

        A bool read/write is atomic under the GIL, so this needs no lock and
        no loop marshalling — which is exactly why a flag was chosen over a
        future (see the module docstring)."""
        return self._stopped

    def stop(self) -> None:
        self._stopped = True


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


#: THE process-local registry. One per daemon process, like the approval
#: registry it mirrors — every surface that can start an addressable turn and
#: every surface that can stop one must reach the same instance or a Stop
#: press answers a copy nobody is running.
TURNS = TurnRegistry()
