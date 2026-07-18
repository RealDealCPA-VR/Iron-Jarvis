"""Offline tests for fleet rate derivation + the hybrid-cadence sampler.

Fully deterministic: the clock is injected (nothing sleeps), the probe is a stub
(nothing touches the network), and history lives in memory. The theme of the
file is the project's first rule — **a number we could not compute is None,
never 0** — so several assertions are deliberately over-specific (``is None``
*and* ``!= 0`` *and* not a spike), because a regression here would render an
invented "0 tok/s" on a GPU that is actually mid-restart.
"""

from __future__ import annotations

import asyncio
import threading

from iron_jarvis.fleet.models import FleetNode, NodeMetrics, NodeSnapshot
from iron_jarvis.fleet.sampler import FleetSampler, derive


# --------------------------------------------------------------------------- #
# Fakes.
# --------------------------------------------------------------------------- #
class _Clock:
    """Manually advanced logical clock (drop-in for time.monotonic)."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _Registry:
    """Minimal stand-in for FleetRegistry (only what the sampler calls)."""

    def __init__(self, nodes: list[FleetNode]) -> None:
        self._nodes = list(nodes)
        self.absorbed: list[tuple[str, list[FleetNode]]] = []
        self.reachable: list[tuple[str, bool]] = []

    def nodes(self) -> list[FleetNode]:
        return list(self._nodes)

    def absorb_children(self, parent_id: str, children: list[FleetNode]) -> None:
        self.absorbed.append((parent_id, children))

    def set_reachable(self, node_id: str, ok: bool) -> None:
        self.reachable.append((node_id, ok))


def _node(node_id: str, kind: str = "vllm") -> FleetNode:
    return FleetNode(id=node_id, label=node_id, base_url=f"http://{node_id}:8888", kind=kind)


def _metrics(**kw) -> NodeMetrics:
    return NodeMetrics(**kw)


def _snapshot(node: FleetNode, *, status: str = "online", metrics=None) -> NodeSnapshot:
    return NodeSnapshot(node=node, status=status, evidence="direct", metrics=metrics)


class _Probe:
    """Scripted probe: per-node queue of snapshots, or an exception to raise."""

    def __init__(self) -> None:
        self.script: dict[str, list] = {}
        self.calls: list[str] = []

    def feed(self, node_id: str, *results) -> None:
        self.script.setdefault(node_id, []).extend(results)

    def __call__(self, node: FleetNode):
        self.calls.append(node.id)
        queue = self.script.get(node.id) or []
        result = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else None)
        if result is None:
            raise RuntimeError("connection refused")
        if isinstance(result, BaseException):
            raise result
        return result, []


# --------------------------------------------------------------------------- #
# derive() — the honesty rules.
# --------------------------------------------------------------------------- #
def test_generation_rate_over_a_ten_second_window():
    """1000 -> 2500 tokens across 10s is 150 tok/s, and the window is reported."""
    prev = (100.0, _metrics(generation_tokens_total=1000.0))
    cur = (110.0, _metrics(generation_tokens_total=2500.0))

    rates = derive(prev, cur)

    assert rates.generation_tps == 150.0
    assert rates.window_seconds == 10.0
    assert rates.counter_reset is False


def test_counter_reset_reports_unknown_and_never_a_fabricated_zero():
    """A vLLM restart (2500 -> 40) must yield counter_reset + None rates.

    This is the load-bearing test of the whole feature. A restart is exactly
    when a naive implementation emits "0 tok/s" (clamped) or "-246 tok/s"
    (raw) or a giant spike (abs) — all three are lies about a GPU we simply
    have no window for. The only honest answer is "unknown".
    """
    prev = (100.0, _metrics(generation_tokens_total=2500.0, prompt_tokens_total=900.0))
    cur = (110.0, _metrics(generation_tokens_total=40.0, prompt_tokens_total=12.0))

    rates = derive(prev, cur)

    assert rates.counter_reset is True
    assert rates.generation_tps is None  # unknown...
    assert rates.generation_tps != 0  # ...not a fabricated idle GPU
    assert rates.prompt_tps is None
    # And explicitly: never clamped to zero, never negative, never a spike.
    for value in (rates.generation_tps, rates.prompt_tps, rates.prefix_cache_hit_rate):
        assert value is None or value >= 0
    # The window itself IS known, so it is still reported.
    assert rates.window_seconds == 10.0


def test_reset_on_any_counter_invalidates_every_rate():
    """One counter going backwards means the process restarted — all rates go."""
    prev = (0.0, _metrics(generation_tokens_total=10.0, prefix_cache_queries_total=100.0,
                          prefix_cache_hits_total=50.0))
    cur = (10.0, _metrics(generation_tokens_total=999.0, prefix_cache_queries_total=5.0,
                          prefix_cache_hits_total=2.0))

    rates = derive(prev, cur)

    assert rates.counter_reset is True
    assert rates.generation_tps is None  # even though it rose, its window spans a restart
    assert rates.prefix_cache_hit_rate is None


def test_no_previous_sample_gives_all_none():
    """The first sample of a node's life has no window and therefore no rates."""
    rates = derive(None, (100.0, _metrics(generation_tokens_total=1000.0)))

    assert rates.generation_tps is None
    assert rates.prompt_tps is None
    assert rates.prefix_cache_hit_rate is None
    assert rates.window_seconds is None
    assert rates.counter_reset is False


def test_zero_and_negative_window_give_all_none_without_dividing():
    """dt <= 0 must be None-everything, never a ZeroDivisionError."""
    m_prev = _metrics(generation_tokens_total=1000.0)
    m_cur = _metrics(generation_tokens_total=2500.0)

    same_instant = derive((100.0, m_prev), (100.0, m_cur))
    backwards = derive((100.0, m_prev), (90.0, m_cur))

    for rates in (same_instant, backwards):
        assert rates.generation_tps is None
        assert rates.window_seconds is None


def test_counter_that_vanished_is_unknown_not_a_reset():
    """A series missing from this scrape says nothing about a restart."""
    prev = (0.0, _metrics(generation_tokens_total=100.0, prompt_tokens_total=50.0))
    cur = (10.0, _metrics(generation_tokens_total=None, prompt_tokens_total=150.0))

    rates = derive(prev, cur)

    assert rates.counter_reset is False  # NOT a reset
    assert rates.generation_tps is None  # just unknown
    assert rates.prompt_tps == 10.0  # the counter we DID read still works


def test_a_genuinely_idle_node_reports_a_measured_zero():
    """The complement of the honesty rule, so nobody over-corrects to None.

    A counter that provably did not move across a known window IS 0 tok/s —
    that is measured, not fabricated. Only an UNKNOWN window yields None.
    """
    rates = derive(
        (0.0, _metrics(generation_tokens_total=1000.0)),
        (10.0, _metrics(generation_tokens_total=1000.0)),
    )

    assert rates.generation_tps == 0.0
    assert rates.generation_tps is not None
    assert rates.counter_reset is False
    assert rates.window_seconds == 10.0


def test_prefix_cache_hit_rate_needs_queries():
    """A window with no queries has an UNKNOWN hit rate, not a 0% one."""
    idle = derive(
        (0.0, _metrics(prefix_cache_queries_total=500.0, prefix_cache_hits_total=400.0)),
        (10.0, _metrics(prefix_cache_queries_total=500.0, prefix_cache_hits_total=400.0)),
    )
    assert idle.prefix_cache_hit_rate is None
    assert idle.prefix_cache_hit_rate != 0

    busy = derive(
        (0.0, _metrics(prefix_cache_queries_total=500.0, prefix_cache_hits_total=400.0)),
        (10.0, _metrics(prefix_cache_queries_total=700.0, prefix_cache_hits_total=550.0)),
    )
    assert busy.prefix_cache_hit_rate == 0.75  # 150 hits / 200 queries


# --------------------------------------------------------------------------- #
# FleetSampler — gauges, history, backoff, cadence, lifecycle.
# --------------------------------------------------------------------------- #
def test_gauges_pass_through_undifferenced():
    """requests_running / kv_cache_usage are GAUGES: reported as read."""
    clock, probe = _Clock(), _Probe()
    node = _node("vllm")
    probe.feed(
        "vllm",
        _snapshot(node, metrics=_metrics(requests_running=3.0, kv_cache_usage=0.10,
                                         generation_tokens_total=1000.0)),
        _snapshot(node, metrics=_metrics(requests_running=5.0, kv_cache_usage=0.42,
                                         generation_tokens_total=2500.0)),
    )
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)

    sampler.sample_once()
    clock.advance(10.0)
    sampler.sample_once()

    snap = sampler.latest("vllm")
    assert snap.metrics.requests_running == 5.0  # the reading, not 5-3
    assert snap.metrics.kv_cache_usage == 0.42
    assert snap.rates.generation_tps == 150.0  # counters ARE differenced


def test_first_sample_has_no_rates():
    """One point is not a window — the first snapshot must not invent rates."""
    clock, probe = _Clock(), _Probe()
    node = _node("vllm")
    probe.feed("vllm", _snapshot(node, metrics=_metrics(generation_tokens_total=1000.0)))
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)

    sampler.sample_once()

    assert sampler.latest("vllm").rates.generation_tps is None


def test_node_without_metrics_gets_no_rates_and_no_history():
    """Ollama has no /metrics at all: metrics None, rates None, nothing stored."""
    clock, probe = _Clock(), _Probe()
    node = _node("tower", kind="ollama")
    probe.feed("tower", _snapshot(node, metrics=None))
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)

    sampler.sample_once()

    snap = sampler.latest("tower")
    assert snap.metrics is None
    assert snap.rates is None
    assert sampler.series("tower") == []


def test_history_ring_evicts_points_older_than_thirty_minutes():
    """History is bounded by WALL TIME (30 min), never persisted, never unbounded."""
    clock, probe = _Clock(), _Probe()
    node = _node("vllm")
    probe.feed("vllm", _snapshot(node, metrics=_metrics(generation_tokens_total=1.0)))
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)

    sampler.sample_once()  # t = 1000
    clock.advance(60.0)
    sampler.sample_once()  # t = 1060
    assert [t for t, _ in sampler.series("vllm")] == [1000.0, 1060.0]

    clock.advance(30 * 60.0)  # t = 2860
    sampler.sample_once()
    # t=1000 is 31 minutes old and evicted; t=1060 sits exactly on the 30-minute
    # edge and is still inside the window, so it stays.
    assert [t for t, _ in sampler.series("vllm")] == [1060.0, 2860.0]

    clock.advance(60.0)  # t = 2920 — the edge point is now past it too
    sampler.sample_once()
    points = sampler.series("vllm")
    assert [t for t, _ in points] == [2860.0, 2920.0]
    assert all(clock.t - t <= 30 * 60.0 for t, _ in points)


def test_series_limit_returns_the_newest_points():
    clock, probe = _Clock(), _Probe()
    node = _node("vllm")
    probe.feed("vllm", _snapshot(node, metrics=_metrics(generation_tokens_total=1.0)))
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)

    for _ in range(4):
        sampler.sample_once()
        clock.advance(2.0)

    assert len(sampler.series("vllm", limit=2)) == 2
    assert sampler.series("vllm", limit=2) == sampler.series("vllm")[-2:]


def test_backoff_starts_after_three_failures_and_resets_on_success():
    """3 strikes -> 30s, then 2min, then 10min; one success clears the ladder."""
    clock, probe = _Clock(), _Probe()
    node = _node("dead")
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)  # no script = raises

    for _ in range(3):
        sampler.sample_once()
    assert probe.calls.count("dead") == 3

    sampler.sample_once()  # inside the 30s penalty — not probed
    assert probe.calls.count("dead") == 3

    clock.advance(30.0)
    sampler.sample_once()  # 4th failure -> 2 min
    assert probe.calls.count("dead") == 4
    clock.advance(60.0)
    sampler.sample_once()  # still inside 2 min
    assert probe.calls.count("dead") == 4

    clock.advance(60.0)  # 120s elapsed -> 5th failure -> 10 min
    sampler.sample_once()
    assert probe.calls.count("dead") == 5
    clock.advance(120.0)
    sampler.sample_once()  # still inside 10 min
    assert probe.calls.count("dead") == 5

    # A success clears everything: the very next cycle probes again.
    clock.advance(600.0)
    probe.feed("dead", _snapshot(node, metrics=_metrics(requests_running=0.0)))
    sampler.sample_once()
    assert probe.calls.count("dead") == 6
    sampler.sample_once()
    assert probe.calls.count("dead") == 7


def test_unreadable_statuses_drive_backoff_even_though_the_probe_never_raises():
    """probe_node returns offline/not-probeable rather than raising.

    Backoff therefore has to key off the STATUS — otherwise it would never
    engage at all for the common case. Both kinds still keep their snapshot
    (error + bind hint) and both keep metrics None; neither is ever zeroed.
    """
    clock, probe = _Clock(), _Probe()
    offline, spark = _node("offline"), _node("spark-049d")
    probe.feed("offline", _snapshot(offline, status="offline"))
    probe.feed("spark-049d", _snapshot(spark, status="not-probeable", metrics=None))
    sampler = FleetSampler(_Registry([offline, spark]), clock=clock, probe=probe)

    for _ in range(5):
        sampler.sample_once()

    assert probe.calls.count("offline") == 3  # backed off after 3 strikes
    assert probe.calls.count("spark-049d") == 3  # a child costs a connect too
    for node_id in ("offline", "spark-049d"):
        snap = sampler.latest(node_id)
        assert snap is not None  # the snapshot is still shown...
        assert snap.metrics is None  # ...with unknown metrics, never zeros
        assert snap.rates is None


def test_forced_refresh_ignores_backoff():
    """An explicit user Refresh must actually probe, penalty or not.

    Someone who just fixed a node's bind address and pressed Refresh cannot be
    told "nothing changed" because the node is serving a 10-minute penalty.
    """
    clock, probe = _Clock(), _Probe()
    node = _node("dead")
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe)

    for _ in range(4):
        sampler.sample_once()
    assert probe.calls.count("dead") == 3  # backed off

    sampler.sample_once(force=True)
    assert probe.calls.count("dead") == 4


def test_a_healthy_node_keeps_its_cadence_while_a_sibling_backs_off():
    """Backoff is per-node: one dead host must not slow the rest of the fleet."""
    clock, probe = _Clock(), _Probe()
    healthy, dead = _node("vllm"), _node("dead")
    probe.feed("vllm", _snapshot(healthy, metrics=_metrics(generation_tokens_total=1.0)))
    sampler = FleetSampler(_Registry([healthy, dead]), clock=clock, probe=probe)

    for _ in range(6):
        sampler.sample_once()
        clock.advance(2.0)

    assert probe.calls.count("vllm") == 6  # every cycle
    assert probe.calls.count("dead") == 3  # then penalised
    assert sampler.latest("vllm").sampled_at == clock.t - 2.0


def test_a_hung_node_does_not_stall_the_cycle():
    """A wedged probe is abandoned at the per-node deadline; siblings still land.

    The only real wait in this file (50ms) — it covers the guarantee that one
    unresponsive host cannot freeze the fleet page or the daemon.

    The hung node is deliberately FIRST in the registry: that is the harder
    ordering, because the cycle's shared deadline is already spent by the time
    the healthy sibling's result is collected. Its future is long since done, so
    a zero-length wait still returns it — an implementation that treated the
    exhausted deadline as a timeout would drop a node it had successfully read.
    """
    clock = _Clock()
    healthy, hung = _node("vllm"), _node("hung")
    release = threading.Event()

    def probe(node):
        if node.id == "hung":
            release.wait(5.0)  # a socket that never answers
            raise RuntimeError("released")
        return _snapshot(healthy, metrics=_metrics(requests_running=2.0)), []

    sampler = FleetSampler(
        _Registry([hung, healthy]), clock=clock, probe=probe, node_timeout=0.05
    )
    try:
        sampler.sample_once()

        assert sampler.latest("vllm").metrics.requests_running == 2.0
        assert sampler.latest("hung") is None  # timed out = unknown, not zeroed
    finally:
        release.set()  # let the abandoned worker thread exit


def test_touch_leases_the_active_cadence_and_expiry_flips_it_back():
    clock = _Clock()
    sampler = FleetSampler(_Registry([]), clock=clock, probe=_Probe(),
                           interval_idle=30.0, interval_active=2.0, lease=45.0)

    assert sampler.status() == {"active": False, "interval": 30.0, "lease_expires_in": 0.0}

    sampler.touch()
    status = sampler.status()
    assert status["active"] is True
    assert status["interval"] == 2.0
    assert status["lease_expires_in"] == 45.0

    clock.advance(44.0)
    assert sampler.status()["active"] is True
    assert sampler.status()["lease_expires_in"] == 1.0

    clock.advance(2.0)  # lease expired
    status = sampler.status()
    assert status["active"] is False
    assert status["interval"] == 30.0
    assert status["lease_expires_in"] == 0.0


def test_children_are_absorbed_and_reachability_is_cached():
    """LiteLLM topology discovery feeds the registry; reachability is cached so
    the router never does network to answer reachable()."""
    clock = _Clock()
    proxy = _node("proxy", kind="litellm")
    child = _node("brain")

    def probe(node):
        return _snapshot(proxy, metrics=None), [child]

    registry = _Registry([proxy])
    sampler = FleetSampler(registry, clock=clock, probe=probe)
    sampler.sample_once()

    assert registry.absorbed == [("proxy", [child])]
    assert registry.reachable == [("proxy", True)]


async def test_stop_leaves_no_pending_tasks():
    """start()/stop() are idempotent and shut down without pending-task warnings."""
    sampler = FleetSampler(_Registry([]), clock=_Clock(), probe=_Probe(),
                           interval_idle=0.01, interval_active=0.01)

    await sampler.start()
    await sampler.start()  # idempotent — still exactly one task
    await asyncio.sleep(0)

    await sampler.stop()
    await sampler.stop()  # idempotent
    await asyncio.sleep(0)

    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    assert pending == []


async def test_loop_samples_nodes_off_the_event_loop():
    """The background loop probes via threads and stores snapshots."""
    clock, probe = _Clock(), _Probe()
    node = _node("vllm")
    probe.feed("vllm", _snapshot(node, metrics=_metrics(requests_running=1.0)))
    sampler = FleetSampler(_Registry([node]), clock=clock, probe=probe,
                           interval_idle=60.0, interval_active=60.0)

    await sampler.start()
    for _ in range(50):  # yield until the first cycle lands (no wall-clock sleep)
        await asyncio.sleep(0)
        if sampler.latest("vllm") is not None:
            break
    await sampler.stop()

    assert sampler.latest("vllm").metrics.requests_running == 1.0
