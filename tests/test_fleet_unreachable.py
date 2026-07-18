"""The honesty core: what we report about a node we could NOT read.

The user's LiteLLM proxy fronts vLLM servers bound to localhost on other
machines. The proxy reaches them; this machine cannot. That is topology, not
breakage — so those nodes must come back as ``not-probeable`` with ``None``
metrics and an actionable hint, and must NEVER come back as a healthy-looking
server sitting at zero.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

from iron_jarvis.fleet import probes
from iron_jarvis.fleet.models import FleetNode


FIXTURES = Path(__file__).parent / "fixtures" / "fleet"
PROXY = "http://100.66.161.52:4000"

#: Exactly what the two dark Spark servers do (see the errors LiteLLM itself
#: recorded in litellm_health.json).
REFUSED = ConnectionRefusedError("[Errno 111] Connection refused")


def _json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, status_code: int = 200, text: str = "", payload=None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


def _world(hosts: dict):
    """A fake network: ``{netloc: routes-dict | Exception}``. An unknown host
    refuses the connection, like a machine that is simply not listening."""

    def get(url: str, headers=None):
        parts = urlsplit(url)
        entry = hosts.get(parts.netloc)
        if entry is None or isinstance(entry, BaseException):
            raise entry or REFUSED
        path = parts.path.rstrip("/") or "/"
        hit = entry.get(path)
        if hit is None:
            return _Resp(404, "not found")
        if isinstance(hit, BaseException):
            raise hit
        return hit
    return get


def _fleet_world():
    """The user's REAL topology: proxy up, spark-049d:8888 (the fleet alias)
    directly reachable, spark-049d:8000 + spark-76a1:8001 dark."""
    return _world(
        {
            "100.66.161.52:4000": {
                "/model/info": _Resp(payload=_json("litellm_model_info.json")),
                "/health": _Resp(payload=_json("litellm_health.json")),
                "/metrics": _Resp(404, "Not Found"),
            },
            "spark-049d:8888": {
                "/v1/models": _Resp(payload=_json("vllm_models.json")),
            },
            # spark-049d:8000 and spark-76a1:8001 are absent → refused.
        }
    )


def _proxy_node() -> FleetNode:
    return FleetNode(id="proxy", label="proxy", base_url=PROXY, kind="litellm")


def _children() -> dict[str, FleetNode]:
    _, kids = probes.probe_node(_proxy_node(), get=_fleet_world())
    return {c.alias: c for c in kids}


# --------------------------------------------------------------------------- #
# topology expansion
# --------------------------------------------------------------------------- #
def test_the_proxy_expands_to_exactly_four_children():
    snap, kids = probes.probe_node(_proxy_node(), get=_fleet_world())
    assert [c.alias for c in kids] == ["brain", "coder", "fleet", "frontier"]
    assert [c.id for c in kids] == [
        "proxy-brain",
        "proxy-coder",
        "proxy-fleet",
        "proxy-frontier",
    ]
    assert snap.children == [c.id for c in kids]


def test_every_child_is_topology_sourced_parented_and_never_routable():
    for child in _children().values():
        assert child.source == "topology"
        assert child.parent_id == "proxy"
        # Routing must go THROUGH the proxy, never around it — a child we
        # cannot even reach would be a guaranteed-failing route target.
        assert child.routable is False


def test_children_carry_their_upstream_url_and_model():
    kids = _children()
    assert kids["brain"].base_url == "http://spark-049d:8000/v1"
    assert kids["brain"].default_model == "hosted_vllm/openai/gpt-oss-120b"
    assert kids["coder"].base_url == "http://spark-76a1:8001/v1"
    assert kids["fleet"].base_url == "http://spark-049d:8888/v1"
    # The frontier alias is served by a remote provider — there is no api_base.
    assert kids["frontier"].base_url == ""


# --------------------------------------------------------------------------- #
# the dark children
# --------------------------------------------------------------------------- #
def test_unreachable_children_are_not_probeable_with_null_metrics_and_a_hint():
    kids = _children()
    for alias, host in (("brain", "spark-049d"), ("coder", "spark-76a1")):
        snap, discovered = probes.probe_node(kids[alias], get=_fleet_world())
        assert discovered == []
        assert snap.status == "not-probeable"  # NOT "offline" — the proxy sees it
        assert snap.evidence == "proxy"  # we know it exists secondhand
        assert snap.error == "connection refused"
        assert snap.metrics is None, "a node we could not read has no metrics"
        assert snap.rates is None
        assert snap.metrics_supported is False
        hint = snap.hint
        assert hint and hint["action"] == "bind-to-tailscale"
        assert host in hint["text"]
        assert "localhost/LAN only" in hint["text"]
        assert hint["commands"], "a hint with no command is not actionable"


def test_the_bind_hint_quotes_a_runnable_command_for_the_right_host_and_port():
    snap, _ = probes.probe_node(_children()["coder"], get=_fleet_world())
    commands = snap.hint["commands"]
    assert commands[0] == "# on spark-76a1:"
    # The litellm routing prefix is stripped — the server knows the bare id.
    assert (
        commands[1]
        == "vllm serve nvidia/Qwen3.6-27B-NVFP4 --host 0.0.0.0 --port 8001"
    )


def test_a_remote_provider_child_is_explained_not_blamed():
    """``frontier`` routes to OpenRouter through the proxy. Nothing local exists
    to probe, so calling it an error would be a lie."""
    snap, _ = probes.probe_node(_children()["frontier"], get=_fleet_world())
    assert snap.status == "not-probeable"
    assert snap.error == ""  # not a failure
    assert snap.hint["action"] == "none"
    assert "nothing local to probe" in snap.hint["text"]
    assert snap.metrics is None and snap.rates is None


def test_a_reachable_child_is_still_read_directly():
    """Being a topology child does not mean unreachable — ``fleet`` answers."""
    snap, _ = probes.probe_node(_children()["fleet"], get=_fleet_world())
    assert snap.status == "online"
    assert snap.evidence == "direct"
    assert [m.id for m in snap.models] == ["deepseek-v4-flash-dspark"]


# --------------------------------------------------------------------------- #
# standalone nodes + error classification
# --------------------------------------------------------------------------- #
def test_a_node_we_own_directly_goes_offline_not_not_probeable():
    node = FleetNode(id="vllm", base_url="http://100.66.161.52:8888", kind="vllm")
    snap, _ = probes.probe_node(node, get=_world({}))
    assert snap.status == "offline"
    assert snap.evidence == "none"
    assert snap.metrics is None and snap.rates is None
    assert snap.hint is None  # a public/Tailscale host has no bind advice


def test_transport_failures_become_plain_human_phrases():
    cases = {
        REFUSED: "connection refused",
        TimeoutError("The read operation timed out"): "timed out",
        socket.gaierror("[Errno -2] Name or service not known"): "dns failure",
    }
    for exc, phrase in cases.items():
        node = FleetNode(id="n", base_url="http://h:8000", kind="vllm")
        snap, _ = probes.probe_node(node, get=_world({"h:8000": exc}))
        assert snap.error == phrase


def test_an_auth_rejection_is_reported_as_an_http_status():
    node = FleetNode(id="n", base_url="http://h:8000", kind="vllm")
    get = _world({"h:8000": {"/metrics": _Resp(401, "Unauthorized")}})
    snap, _ = probes.probe_node(node, get=get)
    assert snap.error == "http 401"
    assert snap.status == "offline"
    assert snap.metrics is None


# --------------------------------------------------------------------------- #
# anti-fabrication tripwire
# --------------------------------------------------------------------------- #
def test_no_nodemetrics_object_is_ever_fabricated_for_an_unreached_node(monkeypatch):
    """The rule, enforced structurally: if the read failed, no ``NodeMetrics``
    is CONSTRUCTED at all. A zeroed metrics object renders as a healthy idle
    server, which is the single most damaging thing this feature could do.
    """
    built: list[dict] = []

    class _Tripwire(probes.NodeMetrics):
        def __init__(self, **kwargs):
            built.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(probes, "NodeMetrics", _Tripwire)

    kids = _children()
    unreached = [
        kids["brain"],  # connection refused
        kids["coder"],  # connection refused
        kids["frontier"],  # nothing local to probe
        FleetNode(id="down", base_url="http://gone:8000", kind="vllm"),
        FleetNode(id="dns", base_url="http://nope.invalid:8000", kind="vllm"),
        FleetNode(id="blank", base_url="", kind="vllm"),
    ]
    snaps = [probes.probe_node(n, get=_fleet_world())[0] for n in unreached]

    assert built == [], f"metrics fabricated for an unreached node: {built}"
    for snap in snaps:
        assert snap.metrics is None
        assert snap.rates is None
        assert snap.status in ("offline", "not-probeable")
