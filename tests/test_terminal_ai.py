"""Per-terminal AI assist — output tail, command extraction, endpoint (offline)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import _first_code_block, create_app
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.session import TAIL_MAX_BYTES, TerminalSession


# --- output tail -------------------------------------------------------------


def test_session_retains_ansi_stripped_tail():
    s = TerminalSession(shell="fake", argv=["fake"], backend=FakeBackend())
    s.start()
    s.write("ls\n")  # FakeBackend echoes completed lines
    assert s.read() == b"ls\n"  # normal read path still returns the bytes
    s.write("\x1b[31mred error\x1b[0m done\n")
    s.read()
    tail = s.output_tail()
    assert "ls" in tail
    assert "red error" in tail and "done" in tail
    assert "\x1b" not in tail  # ANSI color/cursor noise stripped for the model


def test_tail_is_bounded():
    s = TerminalSession(shell="fake", argv=["fake"], backend=FakeBackend())
    s.start()
    s.write("x" * (TAIL_MAX_BYTES * 2) + "\n")
    s.read()
    assert len(s.output_tail()) <= TAIL_MAX_BYTES + 1


# --- suggested-command extraction ---------------------------------------------


def test_first_code_block_extraction():
    text = "Run this:\n```powershell\nGet-ChildItem | Sort Length\n```\nthen check."
    assert _first_code_block(text) == "Get-ChildItem | Sort Length"
    assert _first_code_block("no command here") == ""
    assert _first_code_block("```\nplain\n```") == "plain"


# --- endpoint ------------------------------------------------------------------


def _fake_terminal_app(tmp_path, monkeypatch):
    # Terminals in the test app run on FakeBackend — no real shells spawned.
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: FakeBackend()
    )
    return TestClient(create_app(str(tmp_path)))


def test_terminal_ai_answers_with_default_model(tmp_path, monkeypatch):
    client = _fake_terminal_app(tmp_path, monkeypatch)
    term = client.post("/terminals", json={}).json()

    r = client.post(f"/terminals/{term['id']}/ai", json={"prompt": "what happened?"})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"]  # the offline mock model answered
    assert data["provider"] == "mock"  # fell back to the app default
    assert "command" in data  # extraction always present ("" when no block)


def test_terminal_ai_404_on_unknown_terminal(tmp_path, monkeypatch):
    client = _fake_terminal_app(tmp_path, monkeypatch)
    r = client.post("/terminals/term_nope/ai", json={"prompt": "hi"})
    assert r.status_code == 404


def test_terminal_ai_400_on_unknown_provider(tmp_path, monkeypatch):
    client = _fake_terminal_app(tmp_path, monkeypatch)
    term = client.post("/terminals", json={}).json()
    r = client.post(
        f"/terminals/{term['id']}/ai",
        json={"prompt": "hi", "provider": "not-a-provider", "model": "x"},
    )
    assert r.status_code == 400
