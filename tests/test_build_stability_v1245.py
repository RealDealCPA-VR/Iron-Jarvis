"""v1.245.0 — Build quick fixes: the daily-driver stability pass.

From the 2026-09-11 Build reliability audit (live logs + measured benchmarks):

- A shell with no pane attached stalled ~3 s before its first byte: ConPTY
  asks the terminal for its device attributes (DA1) and holds all output until
  answered, and nothing but an attached xterm ever answered.
- An input hiccup (a malformed resize, a write error on a LIVE shell) closed
  the socket with 4000 "shell exited" — the one close a pane never reconnects
  from — so a working shell was marked dead for good.
- Every same-size resize made ConPTY reflow and the TUI repaint; a dragged
  edge sent dozens a second.
- The terminal snapshot was written only on graceful paths, so the
  2026-09-09 Patch-Tuesday kill left restored panes with history hours old.
- A restored pane kept `agent_cli` and its chip claimed "claude" over a bare
  shell; there was no way back into the conversation the update ended.
FakeBackend only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import anyio
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager
from iron_jarvis.terminals.session import TerminalSession

REPO = Path(__file__).resolve().parents[1]


def _session() -> TerminalSession:
    return TerminalSession(shell="fake", argv=["fake"], backend=FakeBackend()).start()


class RecordingBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.resizes: list[tuple[int, int]] = []

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))
        super().resize(cols, rows)


def _app(tmp_path, monkeypatch, backends=None) -> TestClient:
    def make():
        b = RecordingBackend()
        if backends is not None:
            backends.append(b)
        return b

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", make)
    return TestClient(create_app(str(tmp_path)))


def _recv(ws, timeout: float = 5.0) -> bytes:
    async def _get():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(_get)
    ws._raise_on_close(message)
    return message["bytes"]


def _until(ws, needle: bytes, frames: int = 100) -> bytes:
    got = b""
    for _ in range(frames):
        got += _recv(ws)
        if needle in got:
            return got
    raise AssertionError(f"{needle!r} never arrived (got {got!r})")


# --- the 3-second startup stall ------------------------------------------------


def test_a_shell_with_no_pane_attached_gets_its_da1_question_answered():
    s = _session()
    try:
        s.backend._out += b"\x1b[c"  # ConPTY's startup question
        s.read()
        assert b"\x1b[?1;2c" in bytes(s.backend._line)  # answered, so output flows
    finally:
        s.kill()


def test_an_attached_pane_answers_it_itself():
    s = _session()
    try:
        _, sub = s.subscribe()
        s.backend._out += b"\x1b[0c"
        s.read()
        assert b"\x1b[?1;2c" not in bytes(s.backend._line)  # the pane's xterm will
        assert sub.take() == b"\x1b[0c"
    finally:
        s.kill()


def test_output_seq_counts_only_reads_that_returned_output():
    s = _session()
    try:
        assert s.output_seq == 0
        s.read()
        assert s.output_seq == 0
        s.write("x\n")
        s.read()
        assert s.output_seq == 1
    finally:
        s.kill()


# --- a live shell is never marked exited -------------------------------------------


def test_a_malformed_resize_does_not_mark_a_live_shell_exited(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""  # end of replay
        ws.send_text(json.dumps({"type": "resize", "cols": "wide", "rows": 30}))
        ws.send_text("still-here\n")
        _until(ws, b"still-here")  # the socket stayed open and the shell answered
    assert {t["id"]: t for t in client.get("/terminals").json()["terminals"]}[tid]["alive"]


# --- resize storms ---------------------------------------------------------------------


def test_a_same_size_resize_is_ignored_but_the_attach_still_wiggles(tmp_path, monkeypatch):
    backends: list[RecordingBackend] = []
    client = _app(tmp_path, monkeypatch, backends)
    tid = client.post("/terminals", json={}).json()["id"]
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        for _ in range(5):  # the attach's first resize + four identical ones
            ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        ws.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 30}))
        ws.send_text("done\n")
        _until(ws, b"done")
    assert backends[0].resizes == [(100, 29), (100, 30), (120, 30)]


# --- the snapshot keeps up -------------------------------------------------------------


def test_the_snapshot_is_rewritten_only_when_a_pane_printed(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    s = m.create(cwd=str(tmp_path), backend=FakeBackend())
    m.snapshot()
    assert m.snapshot_if_changed() is False  # nothing new — nothing written
    s.write("new output\n")
    s.read()
    assert m.snapshot_if_changed() is True
    saved = json.loads((tmp_path / "terminals.json").read_text(encoding="utf-8"))
    assert saved["terminals"][0]["id"] == s.id
    assert m.snapshot_if_changed() is False


def test_the_daemon_runs_the_snapshot_and_stall_loops():
    app_src = (REPO / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(encoding="utf-8")
    assert 'bg_tasks["terminal_snapshot"] = asyncio.create_task(_terminal_snapshot_loop())' in app_src
    assert "platform.terminals.snapshot_if_changed" in app_src
    assert 'bg_tasks["loop_lag"] = asyncio.create_task(_loop_lag_loop())' in app_src


# --- resume after a restart ----------------------------------------------------------------


def _restart(tmp_path, cli: str) -> tuple[TerminalManager, str]:
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path), backend=FakeBackend(), name="work", agent_cli=cli)
    m1.snapshot()
    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate(backend=FakeBackend()) == 1
    return m2, s.id


def test_a_restored_pane_offers_resume_instead_of_claiming_the_cli(tmp_path):
    m, pid = _restart(tmp_path, "claude")
    restored = m.get(pid)
    assert restored.agent_cli is None  # the CLI died with the old daemon
    info = restored.info()
    assert info["resume_cli"] == "claude"
    assert info["resume_command"] == "claude --continue"


def test_codex_resumes_its_last_session_and_an_unknown_cli_gets_no_button(tmp_path):
    m, pid = _restart(tmp_path, "codex")
    assert m.get(pid).info()["resume_command"] == "codex resume --last"
    other = tmp_path / "grok-pane"
    other.mkdir()
    m2, pid2 = _restart(other, "grok")
    info = m2.get(pid2).info()
    assert info["resume_cli"] == "grok"
    assert info["resume_command"] == ""  # no known continue command — no guess


def test_the_offer_survives_a_second_restart_until_it_is_used(tmp_path):
    m2, pid = _restart(tmp_path, "claude")
    m2.snapshot()
    m3 = TerminalManager(state_path=tmp_path / "terminals.json")
    assert m3.rehydrate(backend=FakeBackend()) == 1
    assert m3.get(pid).resume_cli == "claude"


def test_resume_and_dismiss_go_through_patch(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    pane = client.post("/terminals", json={}).json()
    session = client.app.state.platform.terminals.get(pane["id"])
    session.resume_cli = "claude"

    # Resume: the CLI is running again, and the offer is gone.
    r = client.patch(f"/terminals/{pane['id']}", json={"agent_cli": "claude", "resume_cli": ""})
    assert r.json()["agent_cli"] == "claude"
    assert r.json()["resume_cli"] is None

    session.resume_cli = "codex"
    r = client.patch(f"/terminals/{pane['id']}", json={"resume_cli": ""})  # Dismiss
    assert r.json()["resume_cli"] is None


def test_the_launch_catalog_carries_the_resume_command(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    clis = {c["id"]: c for c in client.get("/terminals/ai-clis").json()["clis"]}
    assert clis["claude"]["resume_command"] == "claude --continue"
    assert clis["aider"]["resume_command"] == ""


# --- quieter, lighter -------------------------------------------------------------------


def test_websocket_compression_is_off_for_both_entry_points():
    cli_src = (REPO / "src" / "iron_jarvis" / "daemon" / "cli.py").read_text(encoding="utf-8")
    assert cli_src.count("ws_per_message_deflate=False") == 2


def test_the_build_poll_is_not_written_to_the_access_log():
    from iron_jarvis.core.logging import QUIET_PATHS

    assert "/terminals/activity" in QUIET_PATHS


def test_pane_activity_is_classified_once_per_output_change(monkeypatch):
    import iron_jarvis.terminals.agent_state as agent_state

    calls = {"n": 0}
    real = agent_state.classify

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(agent_state, "classify", counting)
    s = _session()
    try:
        s.write("hello\n")
        s.read()
        for _ in range(5):  # five polls, nothing printed in between
            s.activity()
        assert calls["n"] == 1
        s.write("more\n")
        s.read()
        s.activity()
        assert calls["n"] == 2
    finally:
        s.kill()
