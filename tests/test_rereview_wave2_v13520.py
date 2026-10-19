"""v1.352.0 re-review — the model menu says where an endpoint REALLY runs.

G21 rewrote the model menu's badges ("local" / "cloud · on your plan" /
"cloud · pay per use", each with a cost title) from ``GET /models``' ``kind``.
That ``kind`` is the ROUTING class (``providers.local.is_local_provider``):
every fleet node and the custom URL are "local" there, even an endpoint the
user pointed at a cloud server (an Anthropic-compatible API, a hosted
Ollama). So the menu said "local · No charge per message" for a model the
reply's own receipt calls "cloud" (``egress.stays_on_this_network``).

The rows now carry ``on_this_network`` from that SAME predicate (pure, no
DNS, no probe), and the dashboard's badge reads it
(``dashboard/__tests__/agent-runs-in-chat-v13520.test.tsx``, "re-review").
``kind`` is unchanged (routing and /health still read it), and
``reasoning_default`` stays LAST on every row (the v1.330.0 pin).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import connections as C
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.providers import discovery
from iron_jarvis.providers import egress


def _node(d, node_id: str, base_url: str, label: str, model: str) -> None:
    d.fleet.add(
        FleetNode(
            id=node_id,
            label=label,
            base_url=base_url,
            routable=True,
            default_model=model,
        )
    )


def test_a_cloud_endpoint_you_added_is_not_on_your_network(tmp_path, monkeypatch):
    # Offline: no endpoint is asked for its model list.
    monkeypatch.setattr(discovery, "discover_models", lambda *a, **k: [])
    client = TestClient(create_app(str(tmp_path)))
    d = client.app.state.d
    _node(d, "spark", "http://192.168.1.50:8000/v1", "DGX Spark", "glm-4.6")
    _node(d, "deep", "https://api.deepseek-example.com/anthropic", "DeepSeek API", "deepseek-chat")
    d.fleet.register_providers(d.platform.providers)
    C.invalidate_model_catalog()

    rows = C.build_selectable_models(d)
    by = {r["provider"]: r for r in rows}
    spark, deep = by["fleet-spark"], by["fleet-deep"]
    # The routing class is unchanged: both are "local" fleet nodes...
    assert spark["kind"] == "local" and deep["kind"] == "local"
    # ...but only the one on the user's own network says so.
    assert spark["on_this_network"] is True
    assert deep["on_this_network"] is False
    # Every row carries a real boolean, from the receipt's own predicate.
    for r in rows:
        assert isinstance(r["on_this_network"], bool), r
        assert r["on_this_network"] == egress.stays_on_this_network(
            r["provider"], d.platform.providers, d.platform.config
        ), r
        # v1.330.0: reasoning_default is still LAST on every row.
        assert list(r)[-1] == "reasoning_default", list(r)

    # And through the real route the model menu reads.
    served = {r["provider"]: r for r in client.get("/models").json()["models"]}
    assert served["fleet-spark"]["on_this_network"] is True
    assert served["fleet-deep"]["on_this_network"] is False
