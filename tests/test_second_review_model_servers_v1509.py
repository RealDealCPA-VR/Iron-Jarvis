"""What Iron Jarvis sends to model servers (second review, group 9: F7, F11, F12).

F7. A model server's key used to be sent over plain http to ANY address, and
an address carrying a name and password (``http://user:pass@host``), a query
string or a fragment was saved as typed. Now:

* an address with a name or password, a ``?`` part or a ``#`` part is refused
  in one plain sentence, before anything is asked of it;
* a key saved for a server whose address is plain http to a host OUTSIDE this
  network is refused (the key would travel unencrypted) — on add, on the
  probe form and when a key is added to a saved server;
* https to any host, and http to a host on this network, stay accepted;
* a server saved before this change is not touched, and ``GET /fleet`` never
  shows the password in its address.

F11. The sampler probed every "child" a proxy reported (its ``api_base``), so
an address a proxy wrote made this PC call it. A child is now probed only when
its address is on this network; any other child reads "runs at a remote
provider", like a child with no address, and is never called.

F12. ``parse_model_info`` keeps at most ``MAX_ENTRIES`` names. A name past the
cut was judged by the listed entries only, so with 500 local names listed a
hidden cloud name read as LOCAL. A cut table now answers cloud for any name it
does not list.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet import probes
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.fleet.probes import probe_node
from iron_jarvis.fleet.upstreams import (
    MAX_ENTRIES,
    PLACE_CLOUD,
    PLACE_LOCAL,
    RouteTable,
    parse_model_info,
)

# --------------------------------------------------------------------------- #
# F7 — addresses and keys
# --------------------------------------------------------------------------- #

PASSWORD_WORD = "pass-word-value"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The real app; detection and the first probe are recorded instead of
    sent (the routes import them from ``fleet.probes`` at call time)."""
    asked: list[str] = []

    def fake_detect(base_url, **kw):
        asked.append(base_url)
        return "unknown", ""

    def fake_probe(node, **kw):
        asked.append(node.base_url)
        return probes._unreadable(node, "timed out", time.perf_counter()), []

    monkeypatch.setattr(probes, "detect_kind", fake_detect)
    monkeypatch.setattr(probes, "probe_node", fake_probe)
    app = create_app(str(tmp_path))
    c = TestClient(app)
    c.app = app  # type: ignore[attr-defined]
    c.asked = asked  # type: ignore[attr-defined]
    return c


def _fleet(client):
    return client.app.state.platform.fleet  # type: ignore[attr-defined]


def _ids(client) -> list[str]:
    return [n.id for n in _fleet(client).nodes()]


@pytest.mark.parametrize(
    "address, words",
    [
        (f"http://someone:{PASSWORD_WORD}@10.0.0.5:8000", "no name or password"),
        ("http://someone@10.0.0.5:8000", "no name or password"),
        ("http://10.0.0.5:8000/v1?key=abc", "nothing after it"),
        ("http://10.0.0.5:8000/v1#part", "nothing after it"),
        ("http://10.0.0.5:8000\\@example.com", "not a model server's address"),
    ],
)
def test_an_address_with_a_password_query_or_fragment_is_refused_before_anything_is_asked(
    client, address, words
):
    before = _ids(client)
    res = client.post("/fleet/nodes", json={"base_url": address, "label": "box"})
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert words in detail, detail
    assert PASSWORD_WORD not in detail
    assert client.asked == [], "nothing is asked of a refused address"
    assert _ids(client) == before, "nothing is saved"


def test_a_key_over_plain_http_to_a_computer_outside_this_network_is_refused(client):
    res = client.post(
        "/fleet/nodes",
        json={"base_url": "http://203.0.113.7:8000/v1", "api_key_name": "endpoint_box_key"},
    )
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "unencrypted" in detail and "https://" in detail, detail
    assert client.asked == [], "the key was never sent"


@pytest.mark.parametrize(
    "address, key",
    [
        ("https://models.example.com/v1", "endpoint_box_key"),  # https to anyone
        ("http://192.168.1.20:11434", "endpoint_box_key"),  # http on this network
        ("http://spark-a:8000/v1", "endpoint_box_key"),  # a LAN name
        ("http://100.66.161.52:4000", "endpoint_box_key"),  # a tailnet address
        ("http://203.0.113.7:8000/v1", ""),  # plain http, no key: as before
        ("10.0.0.5:8000", "endpoint_box_key"),  # typed without a scheme, on the LAN
    ],
)
def test_what_was_accepted_before_is_still_accepted(client, address, key):
    res = client.post("/fleet/nodes", json={"base_url": address, "api_key_name": key})
    assert res.status_code == 200, res.text
    assert res.json()["node"]["base_url"] == address


def test_adding_a_key_to_a_saved_plain_http_server_outside_this_network_is_refused(client):
    node = client.post("/fleet/nodes", json={"base_url": "http://203.0.113.7:8000/v1"}).json()["node"]
    res = client.patch(f"/fleet/nodes/{node['id']}", json={"api_key_name": "endpoint_box_key"})
    assert res.status_code == 400, res.text
    assert "unencrypted" in res.json()["detail"]
    assert _fleet(client).get(node["id"]).api_key_name == ""
    # Taking a key away, and any other edit, is never refused.
    assert client.patch(f"/fleet/nodes/{node['id']}", json={"api_key_name": ""}).status_code == 200
    assert client.patch(f"/fleet/nodes/{node['id']}", json={"label": "Box"}).status_code == 200


def test_adding_a_key_to_an_https_or_lan_server_still_works(client):
    for address in ("https://models.example.com/v1", "http://192.168.1.20:11434"):
        node = client.post("/fleet/nodes", json={"base_url": address}).json()["node"]
        res = client.patch(f"/fleet/nodes/{node['id']}", json={"api_key_name": "endpoint_box_key"})
        assert res.status_code == 200, res.text
        assert res.json()["node"]["api_key_name"] == "endpoint_box_key"


def test_the_probe_form_refuses_the_same_addresses_before_sending_anything(client):
    bad = client.post("/fleet/probe", json={"base_url": f"http://u:{PASSWORD_WORD}@10.0.0.5:8000"})
    assert bad.status_code == 400 and "no name or password" in bad.json()["detail"]
    keyed = client.post(
        "/fleet/probe", json={"base_url": "http://203.0.113.7:8000", "api_key_name": "endpoint_box_key"}
    )
    assert keyed.status_code == 400 and "unencrypted" in keyed.json()["detail"]
    assert client.asked == []
    ok = client.post("/fleet/probe", json={"base_url": "http://192.168.1.20:11434", "api_key_name": "k"})
    assert ok.status_code == 200 and client.asked, ok.text


def test_a_server_saved_before_is_untouched_and_its_password_is_never_shown(client):
    """A legacy row (saved before the refusal existed) stays exactly as saved;
    the page's view hides the name and password in its address."""
    legacy = f"http://someone:{PASSWORD_WORD}@10.0.0.5:8000/v1"
    _fleet(client).add(FleetNode(id="old", base_url=legacy, source="user"))
    res = client.get("/fleet")
    assert res.status_code == 200
    assert PASSWORD_WORD not in res.text and "someone" not in res.text
    rows = [r for r in res.json()["nodes"] if r["node"]["id"] == "old"]
    assert rows and rows[0]["node"]["base_url"] == "http://***@10.0.0.5:8000/v1"
    assert _fleet(client).get("old").base_url == legacy, "the saved address is never rewritten"


# --------------------------------------------------------------------------- #
# F11 — a proxy's children are probed only on this network
# --------------------------------------------------------------------------- #


class _Net:
    """A fake network: records every URL asked, answers what it knows."""

    def __init__(self, answers):
        self.answers = answers
        self.asked: list[str] = []

    def get(self, url, headers=None):
        self.asked.append(url)
        if url not in self.answers:
            raise ConnectionError("connection refused")

        class _R:
            status_code = 200

            def __init__(self, body):
                self._body = body
                self.text = ""

            def json(self):
                return self._body

        return _R(self.answers[url])


PROXY = "http://proxy-box:4000"
MODEL_INFO = {
    "data": [
        {"model_name": "lan", "litellm_params": {"model": "hosted_vllm/m", "api_base": "http://spark-a:8000/v1"}},
        {"model_name": "far", "litellm_params": {"model": "openai/x", "api_base": "https://api.example.com/v1"}},
        {"model_name": "raw-ip", "litellm_params": {"model": "openai/y", "api_base": "http://203.0.113.9:8000"}},
        {"model_name": "none", "litellm_params": {"model": "openrouter/z"}},
    ]
}


def _children():
    net = _Net({f"{PROXY}/model/info": MODEL_INFO, f"{PROXY}/health": {}})
    proxy = FleetNode(id="proxy", base_url=PROXY, kind="litellm")
    _snap, kids = probe_node(proxy, get=net.get)
    return {k.alias: k for k in kids}


@pytest.mark.parametrize("alias", ["far", "raw-ip"])
def test_a_child_outside_this_network_is_never_called_and_reads_remote(alias):
    child = _children()[alias]
    net = _Net({})
    snap, kids = probe_node(child, get=net.get)
    assert net.asked == [], f"a proxy-reported address outside this network was called: {net.asked}"
    assert kids == []
    assert snap.status == "not-probeable" and snap.evidence == "proxy"
    assert snap.error == ""
    assert "remote provider" in snap.metrics_reason
    assert snap.hint and "remote provider" in snap.hint["text"]


def test_a_child_on_this_network_is_still_read_directly():
    child = _children()["lan"]
    net = _Net({"http://spark-a:8000/v1/models": {"data": [{"id": "m", "max_model_len": 8192}]}})
    snap, _ = probe_node(child, get=net.get)
    assert net.asked == ["http://spark-a:8000/v1/models"]
    assert snap.status == "online"


def test_a_child_with_no_address_reads_as_before():
    child = _children()["none"]
    net = _Net({})
    snap, _ = probe_node(child, get=net.get)
    assert net.asked == [] and snap.status == "not-probeable"
    assert "remote provider" in snap.metrics_reason


def test_a_top_level_server_the_user_added_is_still_probed_wherever_it_is():
    """The rule is about addresses a PROXY wrote; a server the user saved
    themselves (https to anywhere) is asked exactly as before."""
    node = FleetNode(id="mine", base_url="https://models.example.com", kind="openai-compat")
    net = _Net({})
    snap, _ = probe_node(node, get=net.get)
    assert net.asked == ["https://models.example.com/v1/models"]
    assert snap.status == "offline"


# --------------------------------------------------------------------------- #
# F12 — a cut routing table never reads a cloud name as local
# --------------------------------------------------------------------------- #


def _local(i: int) -> dict:
    return {"model_name": f"local-{i}", "litellm_params": {"model": "hosted_vllm/m", "api_base": "http://spark-a:8000"}}


def _cloud(name: str) -> dict:
    return {"model_name": name, "litellm_params": {"model": "openrouter/frontier"}}


def test_a_table_cut_at_the_limit_answers_cloud_for_a_name_it_does_not_list():
    body = {"data": [_local(i) for i in range(MAX_ENTRIES)] + [_cloud("frontier")]}
    table = parse_model_info(body)
    assert table is not None and len(table.entries) == MAX_ENTRIES
    assert table.truncated is True
    assert table.place("frontier") == PLACE_CLOUD, "a cloud name past the cut read as local"
    assert table.place("hidden-alias") == PLACE_CLOUD
    assert table.place("local-3") == PLACE_LOCAL, "a listed local name keeps its verdict"


def test_a_listed_name_with_a_cloud_deployment_past_the_cut_is_cloud():
    body = {"data": [_local(i) for i in range(MAX_ENTRIES)] + [_cloud("local-3")]}
    table = parse_model_info(body)
    assert table.place("local-3") == PLACE_CLOUD
    assert len(table.entries) == MAX_ENTRIES


def test_a_wildcard_match_in_a_cut_table_is_cloud():
    body = {"data": [_local(i) for i in range(MAX_ENTRIES)] + [_cloud("other")]}
    body["data"][0] = {"model_name": "lan-*", "litellm_params": {"model": "ollama/x"}}
    table = parse_model_info(body)
    assert table.place("lan-anything") == PLACE_CLOUD


def test_a_table_within_the_limit_answers_as_before():
    body = {"data": [_local(i) for i in range(MAX_ENTRIES)]}
    table = parse_model_info(body)
    assert table.truncated is False
    assert table.place("hidden-alias") == PLACE_LOCAL
    assert table.place("local-0") == PLACE_LOCAL


def test_the_cut_is_kept_across_a_restart():
    body = {"data": [_local(i) for i in range(MAX_ENTRIES)] + [_cloud("frontier")]}
    table = parse_model_info(body)
    again = RouteTable.from_json(table.to_json())
    assert again is not None and again.truncated is True
    assert again.place("hidden-alias") == PLACE_CLOUD
    # A saved table from before this change (no flag) loads uncut.
    old = {"read_at": 1.0, "entries": [{"alias": "a", "model": "ollama/x", "place": "local"}]}
    assert RouteTable.from_json(old).truncated is False
    # A saved file holding more rows than the limit is a cut table too.
    many = {"read_at": 1.0, "entries": [{"alias": f"a{i}", "model": "", "place": "local"} for i in range(MAX_ENTRIES + 1)]}
    assert RouteTable.from_json(many).truncated is True


def test_a_cut_changes_the_tables_signature():
    """The registry moves its revision (and the model list is rebuilt) when
    only the cut changed."""
    cut = parse_model_info({"data": [_local(i) for i in range(MAX_ENTRIES)] + [_cloud("x")]})
    whole = parse_model_info({"data": [_local(i) for i in range(MAX_ENTRIES)]})
    assert cut.signature() != whole.signature()


def test_the_registry_oracle_reads_the_cut_as_cloud(tmp_path):
    from types import SimpleNamespace

    from iron_jarvis.fleet.registry import FleetRegistry

    cfg = SimpleNamespace(home=str(tmp_path), fleet_nodes=[])
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    reg.add(FleetNode(id="gw", base_url="http://proxy-box:4000", kind="litellm", routable=True))
    reg.set_routes("gw", parse_model_info({"data": [_local(i) for i in range(MAX_ENTRIES)] + [_cloud("x")]}))
    assert reg.route_place("fleet-gw", "hidden-alias") == PLACE_CLOUD
