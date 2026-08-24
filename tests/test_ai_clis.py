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


def test_catalog_has_pi():
    # v1.211.0 — the Pi coding agent joined the catalog after a live report
    # ("I don't see the Pi CLI coming up even though it's on my computer").
    by_id = {c["id"]: c for c in ai_clis.AI_CLIS}
    assert "pi" in by_id
    assert by_id["pi"]["command"] == "pi"


def test_find_resolves_pi_from_its_bundled_node_dir(tmp_path, monkeypatch):
    """Pi installs pi.cmd into %LOCALAPPDATA%\\pi-node\\current and prepends it
    to the USER PATH — a GUI-launched daemon whose environment predates the
    install misses it, so the tool-home fallback must find it (the ~/.grok/bin
    driving case). Red if the pi-node dir is dropped from _extra_bin_dirs."""
    import iron_jarvis.terminals.ai_clis as m

    if m.os.name != "nt":  # the dir is windows-only by construction
        import pytest

        pytest.skip("windows-only install layout")
    local = tmp_path / "LocalAppData"
    pi_dir = local / "pi-node" / "current"
    pi_dir.mkdir(parents=True)
    (pi_dir / "pi.cmd").write_text("stub")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    # PATH lookup must miss so the tool-home path is exercised.
    monkeypatch.setattr(m.shutil, "which", lambda exe: None)
    # Home-dir dirs (~/.grok etc.) may or may not exist on the runner — they
    # only ever ADD candidates, never shadow pi-node.
    assert m._find("pi") == str(pi_dir / "pi.cmd")
    installed = {c["id"]: c["installed"] for c in m.detect_ai_clis()}
    assert installed["pi"] is True
