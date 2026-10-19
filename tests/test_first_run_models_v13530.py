"""v1.353.0 (wave 3, lane 3A) -- a clean first five minutes, backend half.

G34 "Ollama: one answer, one press". A running Ollama was listed as pickable
in the composer (``GET /models`` read ``cli_detect.detect_ollama``) while the
router refused it -- ``ProviderManager.available('ollama')`` is False until
``ollama_base_url`` is saved -- and /health, the connect doors and the one
press never named it. Now:

* ``providers/ollama_seen`` is ONE cached "Ollama answers on this PC with a
  model" answer that never blocks its reader;
* /health's ollama row carries ``detected`` from it;
* ``GET /models`` lists a detected Ollama's models as available ONLY when the
  router would take them;
* ``POST /onboarding/use-model {"provider": "ollama"}`` -- the user's explicit
  press -- saves this PC's address when none is saved and Ollama answers, and
  promotes it; with no Ollama answering it refuses in plain words and saves
  nothing.

G36: the checklist gains the OPTIONAL "Connect the apps you use" step (the
set_up_voice pattern), done once any Directory connector is connected.

Every route test drives the REAL app factory on an isolated tmp root. Only the
network edges are scripted: the Ollama ``/api/tags`` probe and the model
listing (``discovery``); nothing listens on any port.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.onboarding import getting_started, readiness
from iron_jarvis.providers import cli_detect, discovery, ollama_seen

LOCAL_URL = "http://localhost:11434"
TAGS = {"models": [{"name": "llama3.2:latest"}, {"name": "qwen3:8b"}]}


@pytest.fixture(autouse=True)
def _clean_ollama_answer(monkeypatch):
    """Each test starts with no remembered answer, refreshes inline (no
    background thread can land after the test), and no OLLAMA_HOST."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(ollama_seen, "_thread", lambda fn: fn())
    ollama_seen.forget()
    yield
    ollama_seen.forget()


def _ollama_on_this_pc(monkeypatch, *, running: bool) -> list[str]:
    """Script THIS PC's Ollama at its two edges: the binary is installed, and
    ``GET /api/tags`` answers (``running``) or not. Returns the probe log."""
    calls: list[str] = []

    def _tags(api: str):
        calls.append(api)
        return TAGS if running else None

    monkeypatch.setattr(cli_detect, "_ollama_binary", lambda: "C:/fake/ollama.exe")
    monkeypatch.setattr(cli_detect, "_probe_ollama_tags", _tags)
    return calls


def _script_discovery(monkeypatch, models: list[str]) -> None:
    """Every local model listing answers *models* (no network)."""
    monkeypatch.setattr(discovery, "_ollama_models", lambda *a, **k: list(models))
    monkeypatch.setattr(discovery, "_openai_compatible_models", lambda *a, **k: list(models))
    discovery.clear_cache()


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _rows(client) -> dict[str, dict]:
    return {p["provider"]: p for p in client.get("/health").json()["providers"]}


# === providers/ollama_seen: one cached answer, never blocking ================


def test_seen_never_probes_inline_and_refreshes_once(monkeypatch):
    """The reader gets the LAST answer at once; a stale answer starts ONE
    refresh behind it (single-flight), never a probe on the caller."""
    started: list = []

    def _boom(*_a, **_k):
        raise AssertionError("seen() probed on the caller's thread")

    monkeypatch.setattr(ollama_seen, "_detect", _boom)
    # A spawn that does not run the refresh: the reader must not wait for it.
    assert ollama_seen.seen(spawn=started.append) is False
    assert ollama_seen.seen(spawn=started.append) is False
    assert len(started) == 1, "a refresh already running is not started twice"


def test_seen_serves_the_probe_answer_after_the_refresh(monkeypatch):
    _ollama_on_this_pc(monkeypatch, running=True)
    assert ollama_seen.seen() is False  # first read: nothing known yet (refresh ran inline)
    assert ollama_seen.seen() is True
    ollama_seen.forget()
    _ollama_on_this_pc(monkeypatch, running=False)
    ollama_seen.seen()
    assert ollama_seen.seen() is False


def test_running_but_no_model_is_not_seen(monkeypatch):
    """An Ollama with nothing downloaded has nothing to answer with, so the
    one press would only 409 -- it is not offered."""
    monkeypatch.setattr(cli_detect, "_ollama_binary", lambda: "C:/fake/ollama.exe")
    monkeypatch.setattr(cli_detect, "_probe_ollama_tags", lambda api: {"models": []})
    assert ollama_seen.probe_now() is False


# === /health: the ollama row says "detected" ==================================


def test_health_ollama_row_carries_detected(tmp_path, monkeypatch):
    client = _client(tmp_path)
    _ollama_on_this_pc(monkeypatch, running=True)
    ollama_seen.probe_now()
    row = _rows(client)["ollama"]
    assert row["detected"] is True
    # Detected is not connected: the address is still unset.
    assert row["available"] is False

    ollama_seen.forget()
    _ollama_on_this_pc(monkeypatch, running=False)
    ollama_seen.probe_now()
    assert _rows(client)["ollama"]["detected"] is False
    # Only the ollama row carries the flag.
    assert "detected" not in _rows(client)["claude-cli"]


def test_redetect_records_the_ollama_answer(tmp_path, monkeypatch):
    """Re-detect's own scan IS the probe /health reads: the press turns on at
    once, without waiting for the cache to age out."""
    client = _client(tmp_path)
    ollama_seen.record(False)
    _ollama_on_this_pc(monkeypatch, running=True)
    r = client.post("/providers/rescan")
    assert r.status_code == 200, r.text
    assert _rows(client)["ollama"]["detected"] is True


# === GET /models: never lists an Ollama model the router would refuse ========


def _ollama_models(client) -> list[dict]:
    return [m for m in client.get("/models").json()["models"] if m["provider"] == "ollama"]


def test_models_lists_a_detected_ollama_as_unavailable_until_connected(tmp_path, monkeypatch):
    _ollama_on_this_pc(monkeypatch, running=True)
    _script_discovery(monkeypatch, ["llama3.2:latest", "qwen3:8b"])
    client = _client(tmp_path)

    rows = _ollama_models(client)
    assert {m["model"] for m in rows} >= {"llama3.2:latest", "qwen3:8b"}, rows
    # The router refuses ollama while its address is unset...
    assert client.app.state.d.platform.providers.available("ollama") is False
    # ...so no picker may offer one as pickable.
    assert all(m["available"] is False for m in rows), rows

    r = client.put("/settings", json={"values": {"ollama_base_url": LOCAL_URL}})
    assert r.status_code == 200, r.text
    rows = _ollama_models(client)
    assert rows and all(m["available"] is True for m in rows), rows


# === POST /onboarding/use-model: one press connects AND chooses ==============


def test_use_model_ollama_saves_this_pcs_address_and_promotes(tmp_path, monkeypatch):
    _ollama_on_this_pc(monkeypatch, running=True)
    _script_discovery(monkeypatch, ["llama3.2:latest"])
    client = _client(tmp_path)
    cfg = client.app.state.d.platform.config
    assert not cfg.ollama_base_url

    r = client.post("/onboarding/use-model", json={"provider": "ollama"})
    assert r.status_code == 200, r.text
    assert r.json() == {"promoted": {"provider": "ollama", "model": "llama3.2:latest"}, "reason": ""}
    # Connected through the one settings writer (live provider re-pointed)...
    assert cfg.ollama_base_url == LOCAL_URL
    assert client.app.state.d.platform.providers.available("ollama") is True
    health = client.get("/health").json()
    assert (health["default_provider"], health["default_model"]) == ("ollama", "llama3.2:latest")
    # ...journaled with Undo like any setting...
    ledger = client.get("/config/ledger").json()
    assert any("ollama" in str(row).lower() for row in ledger.get("changes", [])), ledger
    # ...and persisted: a reborn daemon answers from Ollama too.
    reborn = _client(tmp_path).get("/health").json()
    assert reborn["default_provider"] == "ollama"


def test_use_model_ollama_not_running_refuses_and_saves_nothing(tmp_path, monkeypatch):
    probes = _ollama_on_this_pc(monkeypatch, running=False)
    client = _client(tmp_path)

    r = client.post("/onboarding/use-model", json={"provider": "ollama"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "isn't running on this PC" in detail, detail
    assert "Connections page first" not in detail  # the old wrong step
    assert probes, "the press asked whether Ollama answers before refusing"
    assert not client.app.state.d.platform.config.ollama_base_url
    assert client.get("/health").json()["default_provider"] == "mock"


def test_use_model_never_overwrites_the_users_own_ollama_address(tmp_path, monkeypatch):
    probes = _ollama_on_this_pc(monkeypatch, running=True)
    _script_discovery(monkeypatch, ["llama3.2:latest"])
    client = _client(tmp_path)
    own = "http://192.168.1.50:11434"
    assert client.put("/settings", json={"values": {"ollama_base_url": own}}).status_code == 200
    probes.clear()

    r = client.post("/onboarding/use-model", json={"provider": "ollama"})
    assert r.status_code == 200, r.text
    assert client.app.state.d.platform.config.ollama_base_url == own
    assert probes == [], "a saved address is the user's: no probe, no save"


def test_use_model_for_another_provider_never_touches_ollama(tmp_path, monkeypatch):
    """The save is part of the OLLAMA press only -- pressing anything else
    (here an unknown name) never connects Ollama behind the user's back."""
    probes = _ollama_on_this_pc(monkeypatch, running=True)
    client = _client(tmp_path)
    r = client.post("/onboarding/use-model", json={"provider": "custom"})
    assert r.status_code == 409
    assert probes == []
    assert not client.app.state.d.platform.config.ollama_base_url


# === G36: the optional "Connect the apps you use" step ========================


@pytest.fixture
def _no_env_apps(monkeypatch):
    """A Pixio key in the developer's environment counts as a connected app
    (connections/registry honours PIXIO_API_KEY) -- a fresh install has none."""
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)


def test_connect_apps_step_is_optional_and_never_next(platform, _no_env_apps):
    steps = {s["key"]: s for s in getting_started(platform)}
    step = steps["connect_apps"]
    assert step["title"] == "Connect the apps you use (optional)"
    assert step["optional"] is True
    assert step["done"] is False
    assert step["href"] == "/marketplace"
    report = readiness(platform)
    assert report["next_step"]["key"] != "connect_apps"


def test_connect_apps_step_done_once_a_directory_app_is_connected(platform, _no_env_apps):
    from iron_jarvis.connectors.service import list_connectors

    before = next(s for s in getting_started(platform) if s["key"] == "connect_apps")
    assert before["done"] is False
    platform.connections.set_api_key("pixio", "pk-test-0000")
    # Precondition: the Directory itself shows it connected (one truth).
    assert any(c["id"] == "pixio" and c["connected"] for c in list_connectors(platform))
    step = next(s for s in getting_started(platform) if s["key"] == "connect_apps")
    assert step["done"] is True


def test_onboarding_route_carries_the_apps_step(tmp_path, _no_env_apps):
    client = _client(tmp_path)
    body = client.get("/onboarding").json()
    keys = [s["key"] for s in body["checklist"]]
    assert keys[-1] == "connect_apps"
    assert body["next_step"]["key"] == "connect_ai"
