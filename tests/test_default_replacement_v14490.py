"""The default model never points at nothing (v1.449.0, readiness Wave C item
24 — ease-of-use review item 47).

Removing a model's server (a Fleet node, the ``custom`` / ``ollama`` slot) or
disconnecting an account used to leave ``config.default_provider`` naming the
thing just removed, and every chat after that failed with "isn't connected"
until the user found Settings › Models. Both doors now hand the default on
BEFORE the removal (``daemon/default_replacement``): to another connected
model under the Connections page's own pairing rule (``promote_model``), or
to the demo model with a plain warning when nothing else is connected. The
preview route answers the same decision as words, so the dashboard's confirm
can say it before the press.

Every read is cached (``cli_auth.never_probe``): a removal starts no CLI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import default_replacement as DR
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import cli_auth


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
    """Every provider in *names* reads as connected; nothing else does."""
    pm = _platform(client).providers
    monkeypatch.setattr(pm, "available", lambda name: name in names)


def _add_node(client: TestClient, label: str = "Studio box") -> str:
    node = client.post(
        "/fleet/nodes",
        json={"base_url": "http://10.0.0.5:8000", "label": label, "default_model": "qwen3"},
    ).json()["node"]
    return node["id"]


# --------------------------------------------------------------------------- #
# A Fleet node that holds the default
# --------------------------------------------------------------------------- #


def test_removing_the_default_nodes_server_moves_the_default_to_another_connected_model(client, monkeypatch):
    node_id = _add_node(client)
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    cfg.default_model = "qwen3"
    _available(client, monkeypatch, {"google"})

    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.status_code == 200, r.text
    moved = r.json()["default_replaced"]
    assert moved is not None
    assert moved["from"] == f"fleet-{node_id}"
    assert moved["from_label"] == "Studio box"
    assert moved["to"] == "google" and moved["demo"] is False
    # The Connections page's own pairing for a key (promote_model).
    assert moved["model"]
    assert "Jarvis now answers with" in moved["words"]
    # THE GUARANTEE: the default never names the removed server.
    assert cfg.default_provider == "google"
    assert cfg.default_model == moved["model"]


def test_with_nothing_else_connected_the_default_falls_back_to_the_demo_with_a_warning(client, monkeypatch):
    node_id = _add_node(client)
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, set())

    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.status_code == 200, r.text
    moved = r.json()["default_replaced"]
    assert moved["demo"] is True and moved["to"] == "mock"
    assert "demo model" in moved["words"] and "scripted" in moved["words"]
    assert cfg.default_provider == "mock"


def test_removing_a_server_that_is_not_the_default_moves_nothing(client, monkeypatch):
    node_id = _add_node(client)
    cfg = _cfg(client)
    cfg.default_provider = "google"
    cfg.default_model = "gemini-x"
    _available(client, monkeypatch, {"google", "openai"})

    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.status_code == 200
    assert r.json().get("default_replaced") is None  # additive: absent when nothing moved
    assert (cfg.default_provider, cfg.default_model) == ("google", "gemini-x")


def test_the_replacement_is_written_through_the_settings_writer_so_undo_exists(client, monkeypatch):
    node_id = _add_node(client)
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, {"google"})
    before = len(client.get("/config/ledger").json().get("changes", []))
    client.delete(f"/fleet/nodes/{node_id}")
    changes = client.get("/config/ledger").json().get("changes", [])
    assert len(changes) == before + 1
    newest = changes[0]
    # The settings writer's own ledger row (`update_settings`, with an Undo).
    assert newest.get("tool") == "update_settings"
    assert "default_provider" in str(newest.get("summary"))


def test_the_slot_that_holds_the_default_moves_it_and_clears_its_settings(client, monkeypatch):
    cfg = _cfg(client)
    cfg.custom_base_url = "http://127.0.0.1:8000"
    cfg.default_provider = "custom"
    cfg.default_model = "local-x"
    _available(client, monkeypatch, {"claude-cli"})
    r = client.delete("/fleet/nodes/custom")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "custom_base_url" in body["cleared_settings"]
    assert body["default_replaced"]["to"] == "claude-cli"
    assert body["default_replaced"]["model"] == "subscription"
    assert cfg.default_provider == "claude-cli"


# --------------------------------------------------------------------------- #
# Disconnecting an account
# --------------------------------------------------------------------------- #


def test_disconnecting_the_default_account_moves_the_default_first(client, monkeypatch):
    cfg = _cfg(client)
    _platform(client).secrets.set("google_api_key", "AIza-test-not-real")
    cfg.default_provider = "google"
    _available(client, monkeypatch, {"google", "openrouter"})
    r = client.delete("/connections/google")
    assert r.status_code == 200, r.text
    moved = r.json()["default_replaced"]
    assert moved["from"] == "google" and moved["to"] == "openrouter"
    assert cfg.default_provider == "openrouter"


def test_a_keyless_api_name_its_signed_in_cli_still_serves_keeps_the_default(client, monkeypatch):
    """Dropping Anthropic's KEY while Claude Code is signed in: ``anthropic``
    keeps answering through the sign-in, so the default stays put."""
    cfg = _cfg(client)
    cfg.default_provider = "anthropic"
    pm = _platform(client).providers
    monkeypatch.setattr(pm, "_inherit_cli", True, raising=False)
    _available(client, monkeypatch, {"claude-cli", "anthropic", "google"})
    r = client.delete("/connections/anthropic")
    assert r.status_code == 200
    assert cfg.default_provider == "anthropic"
    # v1.449.0 review: the NAME stays, and the model it answered under
    # (``claude-opus-4-8``, an API id) is re-paired with the plan's own default
    # the sign-in accepts (``kept: true``); a default already on it says nothing
    # (test_default_replacement_review_v14490).
    kept = r.json()["default_replaced"]
    assert kept["kept"] is True and kept["to"] == "anthropic"
    assert cfg.default_model == "subscription"


def test_the_same_disconnect_with_no_sign_in_behind_it_moves_the_default(client, monkeypatch):
    cfg = _cfg(client)
    cfg.default_provider = "anthropic"
    _available(client, monkeypatch, {"google"})
    r = client.delete("/connections/anthropic")
    assert r.status_code == 200
    assert r.json()["default_replaced"]["to"] == "google"
    assert cfg.default_provider == "google"


# --------------------------------------------------------------------------- #
# The preview the dashboard shows before the press
# --------------------------------------------------------------------------- #


def test_the_preview_names_the_default_and_its_replacement_and_changes_nothing(client, monkeypatch):
    node_id = _add_node(client, "DGX Spark")
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, {"google"})
    r = client.get(f"/fleet/default-after-removal?leaving={node_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["affected"] is True
    assert body["current"] == {"provider": f"fleet-{node_id}", "label": "DGX Spark"}
    assert body["replacement"]["provider"] == "google"
    assert body["words"].startswith("DGX Spark is what answers your chats right now.")
    assert "Jarvis will use" in body["words"]
    # A preview is words only.
    assert cfg.default_provider == f"fleet-{node_id}"
    # The same answer for the provider name a picker uses.
    assert client.get(f"/fleet/default-after-removal?leaving=fleet-{node_id}").json()["words"] == body["words"]


def test_the_preview_for_a_demo_fallback_warns_in_words(client, monkeypatch):
    cfg = _cfg(client)
    _platform(client).secrets.set("google_api_key", "AIza-test-not-real")
    cfg.default_provider = "google"
    _available(client, monkeypatch, {"google"})
    body = client.get("/fleet/default-after-removal?leaving=google").json()
    assert body["affected"] is True and body["demo"] is True
    assert "demo model" in body["words"]


def test_the_preview_for_something_that_is_not_the_default_is_quiet(client, monkeypatch):
    cfg = _cfg(client)
    cfg.default_provider = "google"
    _available(client, monkeypatch, {"google"})
    body = client.get("/fleet/default-after-removal?leaving=openai").json()
    assert body == {"affected": False, "current": None, "replacement": None, "demo": False, "words": ""}


def test_a_removal_never_starts_a_sign_in_probe(client, monkeypatch):
    """The replacement is picked among CACHED answers: every ``available``
    read happens under ``cli_auth.never_probe``."""
    node_id = _add_node(client)
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    seen: list[bool] = []
    pm = _platform(client).providers

    def available(name: str) -> bool:
        seen.append(cli_auth._NO_PROBE.get())  # noqa: SLF001
        return name == "google"

    monkeypatch.setattr(pm, "available", available)
    client.delete(f"/fleet/nodes/{node_id}")
    assert seen and all(seen), "available() was read outside never_probe()"


def test_a_local_slot_with_no_model_on_it_is_skipped_for_one_that_has_one(client, monkeypatch):
    """Trading a dead default for a server that lists nothing would turn the
    next chat into an error: such a slot is passed over."""
    node_id = _add_node(client)
    cfg = _cfg(client)
    cfg.default_provider = f"fleet-{node_id}"
    cfg.ollama_base_url = "http://127.0.0.1:11434"
    _available(client, monkeypatch, {"ollama", "google"})
    monkeypatch.setattr("iron_jarvis.daemon.routes.settings._first_local_model", lambda d, name: "")
    r = client.delete(f"/fleet/nodes/{node_id}")
    assert r.json()["default_replaced"]["to"] == "google"


@pytest.mark.asyncio
async def test_the_chat_tool_says_where_the_default_landed_in_the_routes_words(client, monkeypatch):
    """``model_server_remove`` (chat) goes through the same route body and
    relays the route's own words — never the old "choose another default"."""
    from pathlib import Path

    from iron_jarvis.daemon.routes.settings import config_writer
    from iron_jarvis.settings.records_connections import ModelServerRemoveTool
    from iron_jarvis.tools.base import ToolContext

    node_id = _add_node(client, "DGX Spark")
    platform = _platform(client)
    cfg = platform.config
    cfg.default_provider = f"fleet-{node_id}"
    _available(client, monkeypatch, {"google"})
    config_writer(client.app.state.d)  # type: ignore[attr-defined]  — the tool reads d through the writer
    tool = ModelServerRemoveTool(platform)
    ctx = ToolContext(
        workspace=Path(cfg.home), session_id="chat", agent_run_id="chat",
        config=cfg, event_bus=platform.event_bus, engine=platform.engine,
    )
    out = await tool.execute({"server": "DGX Spark"}, ctx)
    assert out.ok, out.error
    assert "Jarvis now answers with Google" in out.output
    assert "choose another default" not in out.output
    assert cfg.default_provider == "google"


def test_the_preferred_order_puts_subscriptions_before_keys():
    assert DR.PREFERRED_ORDER[:2] == ("claude-cli", "codex-cli")
    assert DR.PREFERRED_ORDER.index("anthropic") > DR.PREFERRED_ORDER.index("ollama")
