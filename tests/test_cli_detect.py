"""Offline tests for CLI-provider detection (no network, no real ~/.grok)."""

from __future__ import annotations

import json
from pathlib import Path

from iron_jarvis.providers import cli_detect
from iron_jarvis.terminals import ai_clis


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fake_grok_home(tmp_path: Path, monkeypatch, *, auth=True, cache=True,
                    version="0.2.82", bad_json=False) -> Path:
    """Build a fake ~/.grok, point GROK_HOME + the binary lookup at it."""
    home = tmp_path / ".grok"
    (home / "bin").mkdir(parents=True)
    # a fake binary so detection considers Grok "installed"
    binpath = home / "bin" / ("grok.exe")
    binpath.write_text("stub")

    monkeypatch.setenv("GROK_HOME", str(home))
    # ensure PATH lookup misses so the tool-home path is exercised
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: None)

    if version:
        (home / "version.json").write_text(json.dumps({"version": version}))
    if auth:
        content = (
            "{bad" if bad_json
            else json.dumps(
                {
                    "https://auth.x.ai::client": {
                        "key": "tok-123",
                        "expires_at": "2999-01-01T00:00:00.000Z",
                        "email": "u@example.com",
                    }
                }
            )
        )
        (home / "auth.json").write_text(content)
    if cache:
        cache_content = (
            "{bad" if bad_json
            else json.dumps(
                {
                    "grok_version": version,
                    "models": {
                        "grok-build": {
                            "info": {
                                "id": "grok-build",
                                "name": "Grok Build",
                                "base_url": "https://cli-chat-proxy.grok.com/v1",
                                "context_window": 512000,
                            }
                        },
                        "grok-composer-2.5-fast": {
                            "info": {"id": "grok-composer-2.5-fast",
                                     "name": "Composer 2.5"}
                        },
                    },
                }
            )
        )
        (home / "models_cache.json").write_text(cache_content)
    return home


# --------------------------------------------------------------------------- #
# grok detection
# --------------------------------------------------------------------------- #
def test_grok_cache_enumerated_and_available(tmp_path, monkeypatch):
    _fake_grok_home(tmp_path, monkeypatch)
    models = cli_detect.detect_grok()
    ids = {m.model for m in models}
    assert ids == {"grok-build", "grok-composer-2.5-fast"}
    build = next(m for m in models if m.model == "grok-build")
    assert build.provider == "grok-cli"
    assert build.name == "Grok Build"
    assert build.available is True
    assert build.source == "cli"
    assert build.base_url == "https://cli-chat-proxy.grok.com/v1"
    assert build.context_window == 512000
    assert build.exec_path and build.exec_path.endswith("grok.exe")


def test_grok_unavailable_when_auth_missing(tmp_path, monkeypatch):
    # binary + cache present, but no auth.json -> models listed, unavailable
    _fake_grok_home(tmp_path, monkeypatch, auth=False)
    models = cli_detect.detect_grok()
    assert models  # still enumerated from the cache
    assert all(m.available is False for m in models)
    assert all("grok login" in m.detail for m in models)


def test_grok_unavailable_when_key_empty(tmp_path, monkeypatch):
    home = _fake_grok_home(tmp_path, monkeypatch)
    (home / "auth.json").write_text(json.dumps({"iss::c": {"key": ""}}))
    assert cli_detect.grok_session() is None
    assert all(m.available is False for m in cli_detect.detect_grok())


def test_grok_malformed_json_does_not_raise(tmp_path, monkeypatch):
    _fake_grok_home(tmp_path, monkeypatch, bad_json=True)
    # malformed cache + auth -> no models, no session, no exception
    assert cli_detect.detect_grok() == []
    assert cli_detect.grok_session() is None


def test_grok_skipped_when_binary_missing(tmp_path, monkeypatch):
    home = tmp_path / ".grok"
    home.mkdir()
    monkeypatch.setenv("GROK_HOME", str(home))
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: None)
    assert cli_detect.detect_grok() == []


def test_grok_session_shape(tmp_path, monkeypatch):
    _fake_grok_home(tmp_path, monkeypatch)
    sess = cli_detect.grok_session()
    assert sess is not None
    assert sess["token"] == "tok-123"
    assert sess["base_url"] == cli_detect.GROK_PROXY_BASE
    assert sess["version"] == "0.2.82"
    assert sess["email"] == "u@example.com"


def test_grok_session_expired_helper():
    assert cli_detect.grok_session_expired(None) is False
    assert cli_detect.grok_session_expired({"expires_at": None}) is False
    assert cli_detect.grok_session_expired(
        {"expires_at": "2000-01-01T00:00:00Z"}
    ) is True
    assert cli_detect.grok_session_expired(
        {"expires_at": "2999-01-01T00:00:00Z"}
    ) is False
    # unparseable -> treated as NOT expired (don't brick a live token)
    assert cli_detect.grok_session_expired({"expires_at": "not-a-date"}) is False


# --------------------------------------------------------------------------- #
# registry / top-level
# --------------------------------------------------------------------------- #
def test_detect_cli_providers_never_raises(monkeypatch):
    # a provider strategy that explodes must be swallowed, not propagated
    boom = cli_detect.CliProvider(
        id="boom", name="Boom", binaries=["boom"],
        home=lambda: Path("/nowhere"),
        detect=lambda: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )
    monkeypatch.setattr(cli_detect, "CLI_PROVIDERS", [boom])
    assert cli_detect.detect_cli_providers() == []


def test_detect_cli_providers_aggregates(tmp_path, monkeypatch):
    _fake_grok_home(tmp_path, monkeypatch)
    # neutralize ollama so the test is deterministic offline
    monkeypatch.setattr(cli_detect, "detect_ollama", lambda: [])
    grok = cli_detect.CliProvider(
        id="grok", name="Grok CLI", binaries=["grok"],
        home=cli_detect.grok_home, detect=cli_detect.detect_grok,
    )
    ollama = cli_detect.CliProvider(
        id="ollama", name="Ollama", binaries=["ollama"],
        home=lambda: tmp_path, detect=cli_detect.detect_ollama,
    )
    monkeypatch.setattr(cli_detect, "CLI_PROVIDERS", [grok, ollama])
    out = cli_detect.detect_cli_providers()
    assert {m.model for m in out} == {"grok-build", "grok-composer-2.5-fast"}
    assert all(isinstance(m, cli_detect.DetectedModel) for m in out)
    # DetectedModel is JSON-friendly for the API layer
    assert out[0].as_dict()["provider"] == "grok-cli"


def test_ollama_returns_empty_when_absent(monkeypatch):
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: None)

    class _Boom:
        def __enter__(self): raise RuntimeError("no server")
        def __exit__(self, *a): return False

    # httpx.Client(...) context manager blows up -> reachability probe fails
    import httpx
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _Boom())
    assert cli_detect.detect_ollama() == []


# --------------------------------------------------------------------------- #
# ai_clis.py: the tool-home bin dir change
# --------------------------------------------------------------------------- #
def test_grok_bin_dir_in_extra_dirs(tmp_path, monkeypatch):
    home = tmp_path
    (home / ".grok" / "bin").mkdir(parents=True)
    monkeypatch.setattr(ai_clis.Path, "home", staticmethod(lambda: home))
    dirs = ai_clis._extra_bin_dirs()
    assert (home / ".grok" / "bin") in dirs


def test_find_resolves_grok_in_home_bin(tmp_path, monkeypatch):
    home = tmp_path
    bindir = home / ".grok" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "grok.exe").write_text("stub")
    monkeypatch.setattr(ai_clis.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: None)  # force fallback
    monkeypatch.setattr(ai_clis.os, "name", "nt")
    found = ai_clis._find("grok")
    assert found is not None and found.endswith("grok.exe")
