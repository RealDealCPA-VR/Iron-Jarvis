"""AI CLI detection for the terminal 'Launch' dropdown."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import ai_clis


def test_catalog_has_the_headline_clis():
    ids = {c["id"] for c in ai_clis.AI_CLIS}
    assert {"claude", "codex", "grok", "opencode"} <= ids


def test_detect_returns_installed_flag(monkeypatch):
    # Pretend only `claude` resolves.
    monkeypatch.setattr(ai_clis, "_find", lambda cmd: "/x/claude" if cmd.strip().startswith("claude") else None)
    got = {c["id"]: c["installed"] for c in ai_clis.detect_ai_clis()}
    assert got["claude"] is True
    assert got["codex"] is False


def test_endpoint_shape(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/terminals/ai-clis")
    assert r.status_code == 200
    clis = r.json()["clis"]
    assert clis and all({"id", "label", "command", "installed"} <= set(c) for c in clis)


def test_find_uses_which_first(monkeypatch):
    import iron_jarvis.terminals.ai_clis as m

    monkeypatch.setattr(m.shutil, "which", lambda exe: "/usr/bin/" + exe)
    assert m._find("claude") == "/usr/bin/claude"
