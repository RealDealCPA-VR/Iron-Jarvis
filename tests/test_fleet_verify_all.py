"""POST /fleet/verify-all — the Settings "Verify all local models" sweep.

One call probes every endpoint (tool + vision), records capabilities on the
node records, and reports per-node results independently: a dead box carries
its own error without hiding a healthy one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall


class _Probe:
    """Full-capability endpoint: answers the ping tool + sees the red square."""

    provider, model = "fleet-good", "llama3"

    async def complete(self, *, system, messages, tools):
        if tools:
            return LLMResponse(
                text="", tool_calls=[ToolCall(id="1", name="ping", arguments={})]
            )
        assert messages[0].images
        return LLMResponse(text="Red", tool_calls=[], usage={})


class _Dead:
    provider, model = "fleet-dead", "x"

    async def complete(self, *, system, messages, tools):
        raise RuntimeError("connection refused")


def test_verify_all_reports_each_endpoint_independently(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    good = client.post(
        "/fleet/nodes",
        json={"base_url": "http://127.0.0.1:9/v1", "label": "good box", "routable": True},
    ).json()["node"]
    dead = client.post(
        "/fleet/nodes",
        json={"base_url": "http://127.0.0.1:7/v1", "label": "dead box", "routable": True},
    ).json()["node"]
    watch = client.post(
        "/fleet/nodes", json={"base_url": "http://127.0.0.1:5/v1", "label": "watch-only"}
    ).json()["node"]

    platform = client.app.state.platform
    adapters = {f"fleet-{good['id']}": _Probe(), f"fleet-{dead['id']}": _Dead()}
    monkeypatch.setattr(
        platform.providers, "get", lambda p, m=None: adapters[p]
    )

    r = client.post("/fleet/verify-all")
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()["results"]}

    g = rows[good["id"]]
    assert g["tool_use"] is True and g["vision"] is True and g["error"] == ""
    d_ = rows[dead["id"]]
    assert d_["tool_use"] is None and "connection refused" in d_["error"]
    w = rows[watch["id"]]
    assert "not routable" in w["error"]

    # Capabilities persisted on the node records (routing + chips pick them up).
    node = platform.fleet.get(good["id"])
    assert node.tool_use is True and node.vision is True
    # The healthy probe also marked the endpoint reachable.
    assert platform.fleet.reachable(f"fleet-{good['id']}") is True
