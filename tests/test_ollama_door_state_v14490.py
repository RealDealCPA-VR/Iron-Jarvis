"""v1.449.0 (readiness Wave C, item 21) — the Ollama door tells three facts.

The free-and-private door could only say "Ollama answers with a model" or
nothing, so step 2 (download a model) was live before Ollama was installed
and a running Ollama with no model was neither "answering" nor explained;
the one press then refused with a command to type (``ollama pull llama3.2``).
Now ``providers/ollama_seen`` keeps three facts — installed, running, has a
model — /health's ollama row carries them, Re-detect refreshes them, and
the refusal names the door's own Download press instead of a command.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import cli_detect, ollama_seen


@pytest.fixture(autouse=True)
def _fresh():
    ollama_seen.forget()
    yield
    ollama_seen.forget()


def _pc(monkeypatch, *, installed: bool, running: bool, models: list[str]) -> None:
    """Ollama on this PC as the probe sees it: its program, its server, its
    models. No network: the two seams cli_detect reads are stand-ins."""
    monkeypatch.setattr(cli_detect, "_ollama_binary", lambda: "C:/fake/ollama.exe" if installed else None)
    monkeypatch.setattr(
        cli_detect,
        "_probe_ollama_tags",
        lambda api: {"models": [{"name": m} for m in models]} if running else None,
    )
    # With no program and no setting the probe is never asked (v1.332.0);
    # OLLAMA_HOST makes "running, not installed" probeable like a real box.
    if running and not installed:
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    else:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)


# --------------------------------------------------------------------------- #
# providers/ollama_seen: three facts, one probe
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "installed, running, models, want",
    [
        (False, False, [], (False, False, False)),
        (True, False, [], (True, False, False)),
        (True, True, [], (True, True, False)),
        (True, True, ["llama3.2"], (True, True, True)),
        (False, True, ["llama3.2"], (False, True, True)),
    ],
)
def test_state_reads_installed_running_and_has_model(monkeypatch, installed, running, models, want):
    """MUTATION: read only detect_ollama (the old probe) -> "running with no
    model" collapses into "not seen" -> red on the third row."""
    _pc(monkeypatch, installed=installed, running=running, models=models)
    assert ollama_seen.probe_now() is want[2]  # the old bool contract
    assert tuple(ollama_seen.state()) == want
    # The old reader is the third fact, unchanged.
    assert ollama_seen.seen() is want[2]


def test_a_probe_fault_reads_as_nothing_seen(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_detect, "detect_ollama", _boom)
    assert ollama_seen.probe_now() is False
    assert ollama_seen.state() == ollama_seen.NOT_SEEN


def test_record_keeps_the_old_bool_contract():
    ollama_seen.record(True)
    assert ollama_seen.state() == ollama_seen.OllamaState(True, True, True)
    ollama_seen.record(False)
    assert ollama_seen.state() == ollama_seen.NOT_SEEN
    ollama_seen.record(ollama_seen.OllamaState(True, True, False))
    assert ollama_seen.seen() is False and ollama_seen.state().running is True


# --------------------------------------------------------------------------- #
# /health: the ollama row carries the two facts under `detected`
# --------------------------------------------------------------------------- #
def _rows(client) -> dict[str, dict]:
    return {p["provider"]: p for p in client.get("/health").json()["providers"]}


def test_health_ollama_row_carries_installed_and_running(tmp_path, monkeypatch):
    """MUTATION: drop either key from the row -> red."""
    client = TestClient(create_app(str(tmp_path)))
    _pc(monkeypatch, installed=True, running=True, models=[])
    ollama_seen.probe_now()
    row = _rows(client)["ollama"]
    assert (row["installed"], row["running"], row["detected"]) == (True, True, False)

    ollama_seen.forget()
    _pc(monkeypatch, installed=False, running=False, models=[])
    ollama_seen.probe_now()
    row = _rows(client)["ollama"]
    assert (row["installed"], row["running"], row["detected"]) == (False, False, False)
    # Only the ollama row carries them.
    assert "running" not in _rows(client)["anthropic"]


def test_redetect_refreshes_the_whole_answer(tmp_path, monkeypatch):
    """A Look again after installing Ollama ungreys the door's step 2 at
    once. MUTATION: record only the has-model bool in the rescan -> red."""
    client = TestClient(create_app(str(tmp_path)))
    ollama_seen.record(False)
    _pc(monkeypatch, installed=True, running=False, models=[])
    assert client.post("/providers/rescan").status_code == 200
    row = _rows(client)["ollama"]
    assert row["installed"] is True and row["running"] is False and row["detected"] is False


# --------------------------------------------------------------------------- #
# the one press: no command to type
# --------------------------------------------------------------------------- #
def test_a_running_ollama_with_no_model_is_refused_in_words_never_a_command(tmp_path, monkeypatch):
    """MUTATION: put `ollama pull` back into the sentence -> red."""
    from iron_jarvis.daemon.routes import settings as settings_routes

    client = TestClient(create_app(str(tmp_path)))
    d = client.app.state.d
    d.platform.config.ollama_base_url = "http://localhost:11434"
    monkeypatch.setattr(d.platform.providers, "available", lambda name: name == "ollama")
    monkeypatch.setattr(settings_routes, "_first_local_model", lambda _d, _name: "")
    r = client.post("/onboarding/use-model", json={"provider": "ollama"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "ollama pull" not in detail and "`" not in detail
    assert "has no model yet" in detail
    assert "Download a starter model" in detail
    assert "then try again" in detail
