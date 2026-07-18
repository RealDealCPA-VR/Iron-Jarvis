"""Fleet sampler — hybrid-cadence polling + honest rate derivation.

Prometheus/Ollama expose COUNTERS and GAUGES; a dashboard wants *rates* ("142
tok/s"). Turning one into the other is where telemetry code usually starts
lying, so the two rules here are absolute:

* **A rate we cannot compute is ``None``, never ``0``.** No previous sample, a
  zero-length window, a server restart, a counter that vanished — every one of
  those is *unknown*, and unknown renders as "—" not as "0 tok/s". A fabricated
  zero during a vLLM restart looks exactly like an idle GPU, which is the class
  of lie CLAUDE.md forbids ("honest errors ALWAYS beat fabricated output").
* **Counters are never clamped.** No ``abs()``, no ``max(0, …)``. If a counter
  went backwards the server restarted; we say so (``counter_reset``) and emit
  ``None`` rather than a 0 or an enormous spike.

:func:`derive` is a pure module-level function (trivially testable with no
sampler at all). :class:`FleetSampler` is the loop around it:

* **Hybrid cadence** — a background loop samples every ``interval_idle`` (30s).
  ``touch()`` (called by ``GET /fleet``) leases ``interval_active`` (2s) for
  ``lease`` seconds, so the fleet page is live while someone is watching it and
  cheap when nobody is.
* **Per-node backoff** — after 3 consecutive failures a node backs off
  30s → 2min → 10min until one success resets it, and failures log at DEBUG.
  The user is frequently OFF the fleet's Tailscale network; that must cost one
  quiet line, not an error-spam loop. Backoff is per-node, so a dead node never
  delays a healthy one.
* **Bounded history** — per-node ring of ``(t, NodeMetrics)`` trimmed to 30
  minutes, IN MEMORY ONLY, never persisted. ``core/config.py`` (the
  ``event_retention_days`` note) records that unbounded EventRecord growth is
  what forced retention limits on this project; 2s telemetry is that same
  failure re-run 15× faster, so it is capped by time *and* by point count.
* **Never on the event loop** — probes run in threads with a per-node deadline,
  concurrently, so one hung node cannot stall the cycle or the daemon.

``clock`` and ``probe`` are injectable so the tests are deterministic: no
sleeps, no network.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Deque

from ..core.logging import get_logger
from .models import FleetNode, NodeMetrics, NodeRates, NodeSnapshot
from .probes import probe_node

log = get_logger("fleet.sampler")

#: One point of per-node history: (logical clock seconds, metrics as read).
MetricPoint = tuple[float, NodeMetrics]

#: The monotonic counters we difference. Everything else on NodeMetrics is a
#: GAUGE (requests_running/waiting, kv_cache_usage) and is read straight off the
#: current sample — differencing a gauge is meaningless.
_COUNTERS = (
    "generation_tokens_total",
    "prompt_tokens_total",
    "prefix_cache_queries_total",
    "prefix_cache_hits_total",
)

#: History retention: 30 minutes of wall time (not a point count — the cadence
#: switches between 2s and 30s, so a fixed count would mean anywhere from 30
#: minutes to 7 hours of history).
_HISTORY_SECONDS = 30 * 60.0

#: Hard backstop on points per node. Time-trimming alone is unbounded if a
#: caller hammers sample_once() faster than the clock advances; 2s telemetry for
#: 30 minutes is ~900 points, so this only ever fires on abuse.
_MAX_HISTORY_POINTS = 2000

#: Consecutive failures before a node starts backing off.
_FAIL_THRESHOLD = 3

#: Backoff ladder applied at failure 3, 4, 5+ (seconds). The last step repeats.
_BACKOFF_STEPS = (30.0, 120.0, 600.0)

#: Per-node probe deadline (seconds). ``probes._get`` bounds each REQUEST at
#: 3.0s but a probe makes several; this bounds the whole node so a cycle cannot
#: outlive it.
_NODE_TIMEOUT = 10.0

#: Granularity of the loop's cadence sleep. Small enough that a touch() during
#: a 30s idle sleep switches to the 2s cadence promptly.
_SLEEP_SLICE = 0.25


def derive(prev: MetricPoint | None, cur: MetricPoint | None) -> NodeRates:
    """Derive per-second rates from two metric samples.

    ``prev``/``cur`` are ``(t_monotonic, NodeMetrics)`` or None. Returns rates
    that are **None wherever the truth is unknown**:

    * no ``prev`` (first sample ever) or ``dt <= 0`` → everything None, and
      never a ZeroDivisionError;
    * any counter lower than last time → the server restarted:
      ``counter_reset=True`` and ALL counter-derived rates None. Not clamped to
      0, not ``abs()``-ed, not spiked;
    * a counter present before and absent now → None (*unknown*, NOT a reset —
      a scrape that dropped a series says nothing about a restart);
    * ``prefix_cache_hit_rate`` only when the query delta is > 0, since a window
      with no queries has an UNKNOWN hit rate, not a 0% one.

    ``window_seconds`` is set on every computable result so the client reads the
    real window instead of assuming the cadence (which switches 2s/30s).
    """
    if cur is None or prev is None:
        return NodeRates()
    t_prev, m_prev = prev
    t_cur, m_cur = cur
    dt = t_cur - t_prev
    if dt <= 0:
        # Same instant (or a clock that went backwards): no window, no rates.
        return NodeRates()

    # Delta per counter: None means "unknown for this window" (missing on
    # either side); a reset on ANY counter invalidates them all.
    deltas: dict[str, float | None] = {}
    reset = False
    for name in _COUNTERS:
        before = getattr(m_prev, name, None)
        after = getattr(m_cur, name, None)
        if before is None or after is None:
            deltas[name] = None
            continue
        if after < before:
            reset = True
        deltas[name] = after - before

    if reset:
        # The window spans a restart, so every counter-derived number would be
        # a fiction. Report the window and the reset; report no rates.
        return NodeRates(window_seconds=dt, counter_reset=True)

    gen = deltas["generation_tokens_total"]
    prompt = deltas["prompt_tokens_total"]
    queries = deltas["prefix_cache_queries_total"]
    hits = deltas["prefix_cache_hits_total"]

    hit_rate: float | None = None
    if queries is not None and hits is not None and queries > 0:
        hit_rate = hits / queries

    return NodeRates(
        window_seconds=dt,
        generation_tps=None if gen is None else gen / dt,
        prompt_tps=None if prompt is None else prompt / dt,
        prefix_cache_hit_rate=hit_rate,
        counter_reset=False,
    )


@dataclass
class _Failure:
    """Per-node consecutive-failure state driving the backoff ladder."""

    count: int = 0
    #: Logical clock time before which the node is not probed again.
    next_at: float = 0.0


@dataclass
class _NodeState:
    """Everything the sampler holds per node (all in memory, never persisted)."""

    snapshot: NodeSnapshot | None = None
    history: Deque[MetricPoint] = field(
        default_factory=lambda: deque(maxlen=_MAX_HISTORY_POINTS)
    )
    failure: _Failure = field(default_factory=_Failure)


class FleetSampler:
    """Hybrid-cadence fleet poller with per-node backoff and bounded history."""

    def __init__(
        self,
        registry: Any,
        *,
        interval_idle: float = 30.0,
        interval_active: float = 2.0,
        lease: float = 45.0,
        clock: Callable[[], float] = time.monotonic,
        probe: Callable[..., tuple[NodeSnapshot, list[FleetNode]]] = probe_node,
        node_timeout: float = _NODE_TIMEOUT,
    ) -> None:
        self.registry = registry
        self.interval_idle = float(interval_idle)
        self.interval_active = float(interval_active)
        self.lease = float(lease)
        self.node_timeout = float(node_timeout)
        #: LOGICAL time source (rates, lease, retention, backoff). Real waits
        #: use time.monotonic() directly — an injected test clock does not tick.
        self._clock = clock
        self._probe = probe
        self._state: dict[str, _NodeState] = {}
        #: sample_once() runs in FastAPI's threadpool while _loop() runs on the
        #: event loop, so both can record into _state at once. Without this the
        #: two could interleave history appends out of order, which derive()
        #: would (honestly) report as an unknown window — correct, but avoidable.
        #: Only ever held around non-awaiting bookkeeping.
        self._lock = threading.RLock()
        #: Logical time of the last touch(); None = never viewed.
        self._touched_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # -- cadence / lease ---------------------------------------------------
    def touch(self) -> None:
        """Mark the fleet as being watched — leases the active (2s) cadence."""
        self._touched_at = self._clock()

    def _active(self) -> bool:
        if self._touched_at is None:
            return False
        return (self._clock() - self._touched_at) < self.lease

    def _current_interval(self) -> float:
        return self.interval_active if self._active() else self.interval_idle

    def status(self) -> dict[str, Any]:
        """Cadence state for the route: active, current interval, lease left."""
        active = self._active()
        expires_in = 0.0
        if active and self._touched_at is not None:
            expires_in = max(0.0, self._touched_at + self.lease - self._clock())
        return {
            "active": active,
            "interval": self.interval_active if active else self.interval_idle,
            "lease_expires_in": expires_in,
        }

    # -- reads -------------------------------------------------------------
    def latest(self, node_id: str) -> NodeSnapshot | None:
        """The most recent snapshot for a node, or None if never sampled.

        None means "we have not looked yet" — the caller must render that as
        unknown, never as an idle/zeroed node.
        """
        with self._lock:
            st = self._state.get(node_id)
            return st.snapshot if st else None

    def snapshots(self) -> list[NodeSnapshot]:
        """Latest snapshot per node, in registry order. Unsampled nodes are
        omitted rather than invented."""
        out: list[NodeSnapshot] = []
        for node in self._nodes():
            snap = self.latest(node.id)
            if snap is not None:
                out.append(snap)
        return out

    def series(self, node_id: str, limit: int | None = None) -> list[MetricPoint]:
        """Bounded metric history for a node, oldest → newest (a copy)."""
        with self._lock:
            st = self._state.get(node_id)
            if st is None:
                return []
            points = list(st.history)
        if limit is not None and limit >= 0:
            points = points[-limit:] if limit else []
        return points

    # -- sampling ----------------------------------------------------------
    def sample_once(self, *, force: bool = False) -> list[NodeSnapshot]:
        """Probe every due node once, synchronously. The route refresh path.

        Probes run concurrently in a thread pool with a shared deadline, and the
        pool is abandoned (``wait=False``) rather than joined, so a hung node
        delays nothing. Returns the resulting snapshots.

        ``force=True`` ignores the backoff ladder — use it for an explicit user
        refresh. Someone who just fixed a node's bind address and pressed
        Refresh must not be told nothing happened because the node is serving a
        10-minute penalty; the button has to mean what it says.
        """
        due = self._nodes() if force else self._due_nodes()
        if not due:
            return self.snapshots()

        pool = ThreadPoolExecutor(
            max_workers=min(8, len(due)), thread_name_prefix="fleet-probe"
        )
        try:
            futures = [(node, pool.submit(self._probe_one, node)) for node in due]
            # Real-time deadline: the injected logical clock does not advance on
            # its own, so it cannot bound a real wait.
            deadline = time.monotonic() + self.node_timeout
            for node, fut in futures:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    snapshot, children = fut.result(timeout=remaining)
                except FutureTimeout:
                    self._record_failure(node, "probe timed out")
                except Exception as exc:  # noqa: BLE001 — one node never breaks the cycle
                    self._record_failure(node, str(exc))
                else:
                    self._record_result(node, snapshot, children)
        finally:
            # Never join: a wedged probe thread must not hold up the caller. It
            # dies on its own (probes carry their own request timeouts).
            pool.shutdown(wait=False)
        return self.snapshots()

    async def _sample_all_async(self) -> None:
        """One cycle on the loop: probe all due nodes concurrently, off-loop."""
        due = self._due_nodes()
        if not due:
            return
        results = await asyncio.gather(
            *(self._probe_async(node) for node in due), return_exceptions=True
        )
        for node, result in zip(due, results):
            if isinstance(result, BaseException):
                # gather(return_exceptions=True) also captures CancelledError —
                # re-raise so stop() cancels the loop cleanly.
                if isinstance(result, asyncio.CancelledError):
                    raise result
                self._record_failure(node, str(result))
                continue
            snapshot, children = result
            self._record_result(node, snapshot, children)

    async def _probe_async(
        self, node: FleetNode
    ) -> tuple[NodeSnapshot, list[FleetNode]]:
        """Probe one node in a worker thread under a per-node deadline."""
        return await asyncio.wait_for(
            asyncio.to_thread(self._probe_one, node), timeout=self.node_timeout
        )

    def _probe_one(self, node: FleetNode) -> tuple[NodeSnapshot, list[FleetNode]]:
        """Call the injected probe. Runs in a worker thread — never on the loop."""
        return self._probe(node)

    # -- bookkeeping -------------------------------------------------------
    def _nodes(self) -> list[FleetNode]:
        """Enabled nodes from the registry; a registry blip yields none, not a
        crashed cycle."""
        try:
            return [n for n in self.registry.nodes() if getattr(n, "enabled", True)]
        except Exception:  # noqa: BLE001 — a bad registry read never kills the loop
            log.debug("fleet sampler: registry read failed", exc_info=True)
            return []

    def _due_nodes(self) -> list[FleetNode]:
        """Enabled nodes not currently serving a backoff penalty."""
        now = self._clock()
        due: list[FleetNode] = []
        for node in self._nodes():
            with self._lock:
                st = self._state.get(node.id)
                backing_off = st is not None and now < st.failure.next_at
            if backing_off:
                continue  # skipping it delays no one else
            due.append(node)
        return due

    def _state_for(self, node_id: str) -> _NodeState:
        st = self._state.get(node_id)
        if st is None:
            st = _NodeState()
            self._state[node_id] = st
        return st

    def _record_failure(self, node: FleetNode, reason: str) -> None:
        """Count a failure, arm the backoff ladder, and DEMOTE the snapshot.

        DEBUG only: the user is regularly off the fleet's network and that is
        not an error condition.

        The demotion is the honesty-critical half. This path runs when the probe
        RAISED (timeout / transport blow-up) rather than returning an offline
        snapshot, and simply keeping the previous one meant a node that died
        mid-session went on rendering ``online`` with its last-good tokens/sec
        forever — a live number that is not live, which is the exact lie this
        feature exists to avoid. So the retained snapshot keeps what is still
        TRUE (which models the node had) and drops what is now UNKNOWN (metrics,
        rates), flips to ``offline``, and carries the reason. ``sampled_at`` is
        deliberately NOT bumped: the UI shows the reading's real age.
        """
        now = self._clock()
        with self._lock:
            st = self._state_for(node.id)
            st.failure.count += 1
            if st.failure.count >= _FAIL_THRESHOLD:
                step = min(st.failure.count - _FAIL_THRESHOLD, len(_BACKOFF_STEPS) - 1)
                st.failure.next_at = now + _BACKOFF_STEPS[step]
            count = st.failure.count
            prev = st.snapshot
            if prev is not None:
                st.snapshot = prev.model_copy(
                    update={
                        "status": "not-probeable" if node.parent_id else "offline",
                        "metrics": None,
                        "rates": None,
                        "error": reason or "unreachable",
                    }
                )
        log.debug(
            "fleet sampler: node %s failed (%d consecutive): %s", node.id, count, reason
        )
        # The router reads this cache on the hot path — a node we just failed to
        # reach must stop reporting itself available (previously only the
        # success path updated it, so a dead node stayed "available" forever).
        setter = getattr(self.registry, "set_reachable", None)
        if callable(setter):
            try:
                setter(node.id, False)
            except Exception:  # noqa: BLE001 — a cache write never breaks a cycle
                log.debug("fleet sampler: set_reachable failed", exc_info=True)

    def _record_result(
        self, node: FleetNode, snapshot: NodeSnapshot, children: list[FleetNode]
    ) -> None:
        """Store a snapshot, derive its rates, and update the backoff ladder.

        ``probe_node`` does NOT raise for an unreachable host — it returns an
        ``offline`` (direct node) or ``not-probeable`` (topology child)
        snapshot. So backoff keys off the STATUS, not off an exception: any
        status other than ``online`` means we got no readable data and paid a
        full connection attempt for it, which is exactly what the ladder exists
        to stop repeating. The snapshot is stored either way — an offline node
        must still show its error and bind hint, just not be re-hammered.
        """
        now = self._clock()
        readable = getattr(snapshot, "status", "") == "online"
        if not readable:
            self._record_failure(node, snapshot.error or snapshot.status or "unreadable")

        with self._lock:
            st = self._state_for(node.id)
            if readable:
                st.failure.count = 0
                st.failure.next_at = 0.0

            # Rates are derived against the previous point BEFORE appending, and
            # only when this sample actually carried metrics. No metrics = no
            # rates (Ollama has no /metrics at all — that is None, not zero).
            metrics = getattr(snapshot, "metrics", None)
            if metrics is not None:
                prev = st.history[-1] if st.history else None
                point: MetricPoint = (now, metrics)
                snapshot.rates = derive(prev, point)
                st.history.append(point)
                self._trim(st.history, now)

            snapshot.sampled_at = now
            st.snapshot = snapshot

        # Registry calls stay OUTSIDE the lock: they can persist to disk, and
        # holding a lock across that would stall every reader of the fleet page.
        if children:
            try:
                self.registry.absorb_children(node.id, children)
            except Exception:  # noqa: BLE001 — topology discovery is best-effort
                log.debug("fleet sampler: absorb_children failed", exc_info=True)

        # Cache reachability for the router (reachable() must never do network).
        setter = getattr(self.registry, "set_reachable", None)
        if callable(setter):
            try:
                setter(node.id, readable)
            except Exception:  # noqa: BLE001 — a cache write never breaks a cycle
                log.debug("fleet sampler: set_reachable failed", exc_info=True)

    @staticmethod
    def _trim(history: Deque[MetricPoint], now: float) -> None:
        """Drop points older than the 30-minute window (the deque's maxlen is
        only the abuse backstop)."""
        cutoff = now - _HISTORY_SECONDS
        while history and history[0][0] < cutoff:
            history.popleft()

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        """Start the background loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="fleet-sampler")

    async def stop(self) -> None:
        """Stop the loop and await its exit. Idempotent, leaves no pending task."""
        task, self._task = self._task, None
        self._stop.set()
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - expected on cancel
            pass
        except Exception:  # noqa: BLE001 — shutdown never raises
            log.debug("fleet sampler: loop exited with an error", exc_info=True)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._sample_all_async()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a cycle must never kill the daemon
                log.debug("fleet sampler: cycle failed", exc_info=True)
            await self._sleep_cadence()

    async def _sleep_cadence(self) -> None:
        """Sleep the current interval, re-reading it so a touch() mid-idle-sleep
        switches to the active cadence without waiting out the full 30s."""
        started = time.monotonic()
        while not self._stop.is_set():
            if (time.monotonic() - started) >= self._current_interval():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_SLEEP_SLICE)
                return  # stop requested
            except (asyncio.TimeoutError, TimeoutError):
                continue
