"""The review of the default hand-over (v1.449.0, readiness Wave C item 24):
every way the first cut could point the default at the wrong thing.

* The MODEL travels with the provider: a replacement never keeps the removed
  server's model name (``promote_model``'s fallback is the CURRENT
  ``default_model`` — the removed server's — and a Fleet node with no model
  used to leave the old one in place).
* A Fleet node with no model of its own, a local slot that lists nothing, a
  node the sampler saw OFFLINE and a key the service REFUSED are not
  replacements.
* The spelling a picker uses (``fleet-custom``) and the slot's own name
  (``custom``) are one server.
* A keyless name its CLI keeps serving is re-paired with the plan's own
  default model (Codex refuses an API model id — the v1.445.0 lesson).
* The move's Undo never puts the default back on a server that is gone, and
  never restores the model alone.
* The demo model and Auto are never "affected"; a junk preview is quiet.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import default_replacement as DR
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app(str(tmp_path))
    c = TestClient(app)
    c.app = app  # type: ignore[attr-defined]
    return c


def _platform(client: TestClient):
    return client.app.state.platform  # type: ignore[attr-defined]


def _cfg(client: TestClient):
    return _platform(client).config


def _available(client: TestClient, monkeypatch, names: set[str]) -> None:
    pm = _platform(client).providers
    monkeypatch.setattr(pm, "available", lambda name: name in names)


def _add_node(client: TestClient, label: str, default_model: str | None = "qwen3", url: str = "http://10.0.0.5:8000") -> str:
    # ``routable``: a node that may answer chats (what the Connections page's
    # add writes); only a routable node is registered as ``fleet-<id>``.
    body: dict = {"base_url": url, "label": label, "routable": True}
    if default_model is not None:
        body["default_model"] = default_model
    r = client.post("/fleet/nodes", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["node"]["id"]


# --------------------------------------------------------------------------- #
# The model travels with the provider
# --------------------------------------------------------------------------- #


def test_a_replacement_outside_the_pairing_table_never_keeps_the_removed_servers_model(client, monkeypatch):
    """grok-cli is not in ``_PROMOTE_DEFAULT_MODEL``: the first cut wrote the
    REMOVED server's model ("qwen3") beside it. The model is "" — the
    adapter's own default — and never the old name."""
    node_id = _add_node(client, "Studio box")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    cfg.default_model = "qwen3"
    _available(client, monkeypatch, {"grok-cli"})
    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.status_code == 200, r.text
    moved = r.json()["default_replaced"]
    assert moved["to"] == "grok-cli"
    assert moved["model"] == ""
    assert cfg.default_provider == "grok-cli"
    assert cfg.default_model == "", "the removed server's model name stayed beside the new provider"


def test_the_demo_fallback_drops_the_removed_servers_model_too(client, monkeypatch):
    node_id = _add_node(client, "Studio box")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    cfg.default_model = "qwen3"
    _available(client, monkeypatch, set())
    client.delete(f"/fleet/nodes/{node_id}")
    assert (cfg.default_provider, cfg.default_model) == ("mock", "")


def test_a_fleet_node_replacement_carries_its_own_model(client, monkeypatch):
    a = _add_node(client, "A box", "qwen3")
    b = _add_node(client, "B box", "llama-b", url="http://10.0.0.6:8000")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{a}"
    cfg.default_model = "qwen3"
    _available(client, monkeypatch, {f"fleet-{b}"})
    client.delete(f"/fleet/nodes/{a}")
    assert (cfg.default_provider, cfg.default_model) == (f"fleet-{b}", "llama-b")


def test_a_fleet_node_with_no_model_of_its_own_and_none_seen_is_not_a_replacement(client, monkeypatch):
    """Such a node would answer the server with the literal model "default"
    (fleet/adapter: ``model or node.default_model or "default"``) — an error
    traded for the dead default. It is passed over; here nothing else is
    connected, so the demo takes the default and says so."""
    a = _add_node(client, "A box", "qwen3")
    b = _add_node(client, "B box", None, url="http://10.0.0.6:8000")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{a}"
    cfg.default_model = "qwen3"
    _available(client, monkeypatch, {f"fleet-{b}"})
    r = client.delete(f"/fleet/nodes/{a}")
    moved = r.json()["default_replaced"]
    assert moved["demo"] is True and moved["to"] == "mock"
    assert cfg.default_model == ""


def test_a_fleet_node_with_no_default_takes_the_first_model_the_sampler_saw(client, monkeypatch):
    from iron_jarvis.fleet.models import ModelEntry, NodeSnapshot

    a = _add_node(client, "A box", "qwen3")
    b = _add_node(client, "B box", None, url="http://10.0.0.6:8000")
    d = client.app.state.d  # type: ignore[attr-defined]
    seen = NodeSnapshot(node=d.fleet.get(b), status="online", models=[ModelEntry(id="seen-model"), ModelEntry(id="other")])
    monkeypatch.setattr(_platform(client).fleet_sampler, "snapshots", lambda: [seen])
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{a}"
    _available(client, monkeypatch, {f"fleet-{b}"})
    client.delete(f"/fleet/nodes/{a}")
    assert (cfg.default_provider, cfg.default_model) == (f"fleet-{b}", "seen-model")


# --------------------------------------------------------------------------- #
# Not connected means not a replacement
# --------------------------------------------------------------------------- #


def test_a_node_the_sampler_saw_offline_is_passed_over_for_one_it_saw_online(client, monkeypatch):
    """Through the REAL availability oracle for the fleet names (the
    registry's cached reachability), every other provider off."""
    a = _add_node(client, "A box", "qwen3")
    off = _add_node(client, "Off box", "m-off", url="http://10.0.0.6:8000")
    on = _add_node(client, "On box", "m-on", url="http://10.0.0.7:8000")
    d = client.app.state.d  # type: ignore[attr-defined]
    d.fleet.set_reachable(off, False)
    d.fleet.set_reachable(on, True)
    pm = _platform(client).providers
    real = pm.available
    monkeypatch.setattr(pm, "available", lambda name: real(name) if name.startswith("fleet-") else False)
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{a}"
    client.delete(f"/fleet/nodes/{a}")
    assert (cfg.default_provider, cfg.default_model) == (f"fleet-{on}", "m-on")


def test_a_key_the_service_refused_is_not_a_replacement(client, monkeypatch):
    """A present key reads "available" (presence-only), but a key the
    Directory's save or a Test press saw REFUSED is remembered: it is passed
    over for a provider whose key was not refused."""
    from iron_jarvis.connectors.service import note_key_check

    platform = _platform(client)
    platform.secrets.set("openrouter_api_key", "sk-or-refused-not-real")
    platform.secrets.set("google_api_key", "AIza-fine-not-real")
    note_key_check(platform, "openrouter", {"ok": False, "detail": "probe returned HTTP 401"})
    node_id = _add_node(client, "A box")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, {"openrouter", "google"})
    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.json()["default_replaced"]["to"] == "google"
    assert cfg.default_provider == "google"


def test_a_key_that_passed_its_check_stays_a_replacement(client, monkeypatch):
    from iron_jarvis.connectors.service import note_key_check

    platform = _platform(client)
    platform.secrets.set("openrouter_api_key", "sk-or-fine-not-real")
    note_key_check(platform, "openrouter", {"ok": True, "detail": "ok"})
    node_id = _add_node(client, "A box")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, {"openrouter", "google"})
    client.delete(f"/fleet/nodes/{node_id}")
    assert cfg.default_provider == "openrouter"


# --------------------------------------------------------------------------- #
# Spellings
# --------------------------------------------------------------------------- #


def test_the_pickers_spelling_of_the_slot_is_the_slot(client, monkeypatch):
    """The model menu names the custom slot ``fleet-custom``; Settings names
    it ``custom``. Removing the slot moves a default under either name."""
    cfg = _cfg(client)
    cfg.custom_base_url = "http://127.0.0.1:8000"
    cfg.default_provider = "fleet-custom"
    cfg.default_model = "local-x"
    _available(client, monkeypatch, {"google"})
    r = client.delete("/fleet/nodes/custom")
    assert r.status_code == 200, r.text
    assert r.json()["default_replaced"]["from"] == "fleet-custom"
    assert cfg.default_provider == "google"
    assert cfg.default_model != "local-x"


def test_removing_the_slot_when_the_default_is_elsewhere_changes_the_default_by_not_one_byte(client, monkeypatch):
    cfg = _cfg(client)
    cfg.custom_base_url = "http://127.0.0.1:8000"
    cfg.default_provider = "anthropic"
    cfg.default_model = "claude-opus-4-8"
    _available(client, monkeypatch, {"google", "custom"})
    r = client.delete("/fleet/nodes/custom")
    assert r.status_code == 200
    assert "default_replaced" not in r.json()
    assert (cfg.default_provider, cfg.default_model) == ("anthropic", "claude-opus-4-8")


# --------------------------------------------------------------------------- #
# A keyless name its CLI keeps serving
# --------------------------------------------------------------------------- #


def test_dropping_the_openai_key_with_codex_signed_in_repairs_the_model_to_the_plans_default(client, monkeypatch):
    """``openai`` keeps answering through Codex — but Codex refuses
    ``gpt-4o-mini`` (a model its plan does not offer), so the first chat after
    the disconnect failed. The model is re-paired, the provider kept."""
    platform = _platform(client)
    platform.secrets.set("openai_api_key", "sk-not-real")
    cfg = _cfg(client)
    cfg.default_provider = "openai"
    cfg.default_model = "gpt-4o-mini"
    monkeypatch.setattr(platform.providers, "_inherit_cli", True, raising=False)
    _available(client, monkeypatch, {"codex-cli", "openai", "google"})
    r = client.delete("/connections/openai")
    assert r.status_code == 200, r.text
    kept = r.json()["default_replaced"]
    assert kept["kept"] is True and kept["from"] == kept["to"] == "openai"
    assert kept["model"] == "subscription"
    assert "through" in kept["words"] and "plan" in kept["words"]
    assert (cfg.default_provider, cfg.default_model) == ("openai", "subscription")


def test_a_kept_name_already_on_the_plans_default_moves_nothing_and_says_nothing(client, monkeypatch):
    platform = _platform(client)
    cfg = _cfg(client)
    cfg.default_provider = "anthropic"
    cfg.default_model = "subscription"
    monkeypatch.setattr(platform.providers, "_inherit_cli", True, raising=False)
    _available(client, monkeypatch, {"claude-cli", "anthropic"})
    r = client.delete("/connections/anthropic")
    assert r.status_code == 200
    assert "default_replaced" not in r.json()
    assert (cfg.default_provider, cfg.default_model) == ("anthropic", "subscription")


def test_the_kept_repair_is_a_settings_writer_row_too(client, monkeypatch):
    platform = _platform(client)
    cfg = _cfg(client)
    cfg.default_provider = "openai"
    cfg.default_model = "gpt-4o-mini"
    monkeypatch.setattr(platform.providers, "_inherit_cli", True, raising=False)
    _available(client, monkeypatch, {"codex-cli", "openai"})
    before = len(client.get("/config/ledger").json()["changes"])
    client.delete("/connections/openai")
    changes = client.get("/config/ledger").json()["changes"]
    assert len(changes) == before + 1 and "default_model" in changes[0]["summary"]


# --------------------------------------------------------------------------- #
# Undo never points the default at a server that is gone
# --------------------------------------------------------------------------- #


def test_undoing_the_move_refuses_to_put_the_default_back_on_the_removed_server(client, monkeypatch):
    node_id = _add_node(client, "Studio box")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    cfg.default_model = "qwen3"
    _available(client, monkeypatch, {"google"})
    client.delete(f"/fleet/nodes/{node_id}")
    assert cfg.default_provider == "google"
    google_model = cfg.default_model
    newest = client.get("/config/ledger").json()["changes"][0]
    assert newest["undoable"] is True
    r = client.post(f"/undo/{newest['action_id']}")
    assert r.status_code == 200, r.text
    body = str(r.json())
    # The provider that is gone is not put back, and the model is NOT
    # restored alone (it belongs to that provider).
    assert (cfg.default_provider, cfg.default_model) == ("google", google_model)
    assert "not restored" in body and "default_provider" in body and "default_model" in body


def test_undoing_a_move_to_a_provider_that_still_exists_is_a_real_restore(client, monkeypatch):
    """Control: the same Undo puts a default back on a provider the daemon
    still has (a disconnected account's name stays registered)."""
    platform = _platform(client)
    platform.secrets.set("google_api_key", "AIza-not-real")
    cfg = _cfg(client)
    cfg.default_provider = "google"
    cfg.default_model = "gemini-x"
    _available(client, monkeypatch, {"google", "openrouter"})
    client.delete("/connections/google")
    assert cfg.default_provider == "openrouter"
    newest = client.get("/config/ledger").json()["changes"][0]
    r = client.post(f"/undo/{newest['action_id']}")
    assert r.status_code == 200, r.text
    assert (cfg.default_provider, cfg.default_model) == ("google", "gemini-x")
    assert "not restored" not in str(r.json())


# --------------------------------------------------------------------------- #
# The writer's rule, on its own
# --------------------------------------------------------------------------- #


def test_the_settings_writer_refuses_a_default_provider_the_daemon_does_not_have(client):
    r = client.put("/settings/values", json={"values": {"default_provider": "fleet-gone-forever"}})
    assert r.status_code in (400, 409, 422), r.text
    assert "not a model server or account Iron Jarvis has" in r.text
    assert _cfg(client).default_provider == "mock"


def test_the_settings_writer_still_takes_auto_and_every_registered_provider(client):
    for name in ("auto", "google", "claude-cli"):
        r = client.put("/settings/values", json={"values": {"default_provider": name}})
        assert r.status_code == 200, (name, r.text)
        assert _cfg(client).default_provider == name


def test_a_full_settings_save_that_repeats_a_dangling_default_is_not_refused(client):
    """A name that went away since (an older install's leftover) is not
    re-checked when the save does not change it."""
    cfg = _cfg(client)
    cfg.default_provider = "fleet-left-over"
    r = client.put("/settings/values", json={"values": {"default_provider": "fleet-left-over", "default_model": "x"}})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Never "affected": the demo, Auto, junk
# --------------------------------------------------------------------------- #


def test_the_demo_and_auto_are_never_affected_and_junk_previews_are_quiet(client, monkeypatch):
    cfg = _cfg(client)
    d = client.app.state.d  # type: ignore[attr-defined]
    _available(client, monkeypatch, {"google"})
    cfg.default_provider = "mock"
    assert DR.affected(d, {"mock"}) is False
    assert client.get("/fleet/default-after-removal?leaving=mock").json()["affected"] is False
    cfg.default_provider = "auto"
    assert DR.affected(d, {"auto"}) is False
    assert client.get("/fleet/default-after-removal?leaving=auto,,%20,junk").json()["affected"] is False
    assert client.get("/fleet/default-after-removal").json()["affected"] is False
    assert cfg.default_provider == "auto"


def test_a_writer_refusal_never_blocks_the_removal_itself(client, monkeypatch):
    """The removal goes on when the default could not be moved (an honest
    "not re-pointed" beats a refusal nobody can act on)."""
    from iron_jarvis.settings import writer as W

    node_id = _add_node(client, "Studio box")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, {"google"})

    def boom(self, values, **kw):
        raise W.SettingError("refused on purpose")

    monkeypatch.setattr(W.ConfigWriter, "apply", boom)
    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.status_code == 200, r.text
    assert "default_replaced" not in r.json()
    assert client.app.state.d.fleet.get(node_id) is None  # type: ignore[attr-defined]
