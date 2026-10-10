"""A saved endpoint is listed at once, as "not checked yet" (v1.329.0, calm
chat wave 8, J3).

The closing audit: a newly added endpoint did not appear in ``GET /fleet``
until the sampler had sampled it (``FleetSampler.snapshots`` omitted every
node without a snapshot), and on a fresh install the sampler loop is only
started at boot when a node already exists, so the first endpoint was never
sampled at all. Settings > Connections reads its saved-endpoint rows from
``GET /fleet``, so right after saving it listed nothing.

Now:

* the sampler LISTS a configured node no probe has looked at as an honest
  stand-in (status ``unknown``, evidence ``none``, no metrics, no rates, no
  models, ``sampled_at`` 0) and ``GET /fleet`` marks it ``checked: false``;
* the stand-in never writes the registry's reachability cache, so the router
  still reads such a node as unknown, never as healthy;
* ``POST /fleet/nodes`` keeps its own one probe as the node's first reading
  (``sampler.record``) and re-arms the sampler loop.

Driven through the REAL app factory; only the two network calls in
``fleet.probes`` are stood in for.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet import probes as fprobes
from iron_jarvis.fleet.models import FleetNode, NodeMetrics, NodeSnapshot
from iron_jarvis.fleet.sampler import NOT_CHECKED_REASON, FleetSampler


def _app(tmp_path):
    app = create_app(str(tmp_path))
    # No `with`: the lifespan (and so the background sampler loop) never runs,
    # so nothing samples behind the test's back.
    return app, TestClient(app)


def _stub_network(monkeypatch, *, status="online", calls=None):
    """Offline detect + probe. ``calls`` counts probes (none expected on a GET)."""
    calls = calls if calls is not None else {"probe": 0}

    def _detect(base_url, **_kw):
        return ("openai-compat", "stubbed")

    def _probe(node, **_kw):
        calls["probe"] += 1
        return (
            NodeSnapshot(
                node=node,
                status=status,
                evidence="direct",
                latency_ms=12.0,
                metrics_supported=True,
                metrics=NodeMetrics(requests_running=1) if status == "online" else None,
                error="" if status == "online" else "ConnectError: refused",
            ),
            [],
        )

    monkeypatch.setattr(fprobes, "detect_kind", _detect)
    monkeypatch.setattr(fprobes, "probe_node", _probe)
    return calls


def _row(client, node_id):
    rows = client.get("/fleet").json()["nodes"]
    return next((r for r in rows if r["node"]["id"] == node_id), None)


# --- the sampler -------------------------------------------------------------


class _Reg:
    def __init__(self, *nodes):
        self._nodes = list(nodes)
        self.reach_writes: list[tuple[str, bool]] = []

    def nodes(self):
        return list(self._nodes)

    def set_reachable(self, node_id, ok):
        self.reach_writes.append((node_id, ok))

    def absorb_children(self, *_a, **_kw):
        pass


def test_sampler_lists_an_unsampled_node_as_not_checked_and_writes_nothing():
    seen = FleetNode(id="seen", base_url="http://seen:8000")
    fresh = FleetNode(id="fresh", base_url="http://fresh:8000", routable=True)
    reg = _Reg(seen, fresh)
    sampler = FleetSampler(reg, clock=lambda: 100.0)  # type: ignore[arg-type]
    sampler.record(seen, NodeSnapshot(node=seen, status="online", evidence="direct"), [])
    reg.reach_writes.clear()

    pairs = sampler.listing()
    assert [(s.node.id, checked) for s, checked in pairs] == [("seen", True), ("fresh", False)]
    stand_in = pairs[1][0]
    assert stand_in.status == "unknown", "an unchecked node must never read online"
    assert stand_in.evidence == "none"
    assert stand_in.metrics is None and stand_in.rates is None
    assert stand_in.models == []
    assert stand_in.sampled_at == 0.0
    assert stand_in.latency_ms is None
    assert stand_in.metrics_reason == NOT_CHECKED_REASON
    # Listing is observation-free: no reachability write, so routing still
    # reads the node as unknown.
    assert reg.reach_writes == []
    # snapshots() is the same list without the flags.
    assert [s.node.id for s in sampler.snapshots()] == ["seen", "fresh"]


# --- GET /fleet through the real app -----------------------------------------


def test_a_node_no_probe_has_seen_is_listed_unchecked_and_not_reachable(tmp_path, monkeypatch):
    calls = _stub_network(monkeypatch)
    app, client = _app(tmp_path)
    platform = app.state.platform
    platform.fleet.add(
        FleetNode(id="lab", label="Lab box", base_url="http://lab:8000", routable=True,
                  default_model="qwen3", protocol="anthropic")
    )

    row = _row(client, "lab")
    assert row is not None, "a saved node must be listed at once, before any probe"
    assert row["checked"] is False
    assert row["status"] == "unknown"
    assert row["metrics"] is None and row["rates"] is None
    assert row["models"] == []
    assert row["metrics_reason"] == NOT_CHECKED_REASON
    # The config the Connections rows read is all there.
    assert row["node"]["label"] == "Lab box"
    assert row["node"]["routable"] is True
    assert row["node"]["protocol"] == "anthropic"
    # Serving the list probed nothing and claimed nothing for routing.
    assert calls["probe"] == 0
    assert platform.fleet.reachable("fleet-lab") is None


def test_adding_a_node_keeps_its_probe_as_the_first_reading(tmp_path, monkeypatch):
    _stub_network(monkeypatch, status="online")
    app, client = _app(tmp_path)
    r = client.post("/fleet/nodes", json={"base_url": "http://new:8000", "label": "New", "routable": True})
    assert r.status_code == 200, r.text
    node_id = r.json()["node"]["id"]

    row = _row(client, node_id)
    assert row is not None
    assert row["checked"] is True
    assert row["status"] == "online"
    assert row["sampled_at"] > 0
    assert app.state.platform.fleet.reachable(f"fleet-{node_id}") is True


def test_an_added_node_that_did_not_answer_reads_offline_not_online(tmp_path, monkeypatch):
    _stub_network(monkeypatch, status="offline")
    app, client = _app(tmp_path)
    node_id = client.post("/fleet/nodes", json={"base_url": "http://asleep:8000"}).json()["node"]["id"]

    row = _row(client, node_id)
    assert row["checked"] is True
    assert row["status"] == "offline"
    assert row["metrics"] is None
    assert "refused" in row["error"]
    assert app.state.platform.fleet.reachable(f"fleet-{node_id}") is False


def test_adding_a_node_rearms_the_sampler_loop(tmp_path, monkeypatch):
    _stub_network(monkeypatch)
    app, client = _app(tmp_path)
    armed: list[int] = []
    app.state.d._live_rearm["fleet"] = lambda: armed.append(1)
    client.post("/fleet/nodes", json={"base_url": "http://first:8000"})
    assert armed == [1], "the first endpoint on a fresh install was never sampled again"


def test_a_rearm_or_record_fault_never_fails_the_add(tmp_path, monkeypatch):
    _stub_network(monkeypatch)
    app, client = _app(tmp_path)

    def _boom():
        raise RuntimeError("loop gone")

    app.state.d._live_rearm["fleet"] = _boom
    monkeypatch.setattr(app.state.d.fleet_sampler, "record", lambda *a, **k: _boom())
    r = client.post("/fleet/nodes", json={"base_url": "http://sturdy:8000"})
    assert r.status_code == 200
    # Not recorded, so it is listed as not checked yet rather than missing.
    row = _row(client, r.json()["node"]["id"])
    assert row is not None and row["checked"] is False
