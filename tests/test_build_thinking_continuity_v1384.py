"""v1.384.0 — does each round of a Claude Code Build reply carry THIS turn's
earlier reasoning?

The user's 117-minute reply took 2-6 minutes a round and ~10,000 output
tokens a round, mostly thinking. If a round were sent the earlier rounds as
REBUILT assistant turns (text + tool_use, no thinking), the model would work
its reasoning out again every round. This drives a real Build turn — the real
app, the real stream lane in harness mode (the claude dialect: ``Read``), the
REAL claude-cli adapter — against a fake ``claude`` that answers three rounds
and records the frames each round was sent (``fixtures/
fake_claude_rounds_v1384.py``).

THE EVIDENCE: round n is sent every earlier round's assistant message
VERBATIM from its ``claude_cli_native`` carrier — its signed thinking block
included — because the lane appends the model's own calls (``_model_calls``,
the dialect's names, never the translated ones) and the adapter's own
``raw_blocks``, so the carrier's projection still matches.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.cli_auth import CliAuthProbe
from tests.test_build_sessions_v1367 import frames_of, messages_of, open_turn

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_rounds_v1384.py"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)


def _shim(tmp_path: Path) -> str:
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


def _thinking_signatures(frame: dict) -> list[str]:
    msg = frame.get("message") or {}
    return [b.get("signature") for b in msg.get("content") or []
            if isinstance(b, dict) and b.get("type") == "thinking"]


async def test_every_round_is_sent_this_turns_earlier_signed_thinking(tmp_path, monkeypatch):
    shim = _shim(tmp_path)
    log = tmp_path / "rounds.jsonl"
    monkeypatch.setenv("FAKE_ROUNDS", "3")
    monkeypatch.setenv("FAKE_ROUNDS_LOG", str(log))
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.providers.register(
        "claude-cli", lambda model=None: sc.make_claude_cli(which=lambda _b: shim, model=model or "claude-sonnet-4-5"),
    )
    real_available = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: name == "claude-cli" or real_available(name))
    work = tmp_path / "work"
    work.mkdir()
    (work / "app.py").write_text("x = 1\n", encoding="utf-8")
    from iron_jarvis.harness import sessions as S

    row = S.create(p.engine, str(work), "", {"provider": "claude-cli", "model": "claude-sonnet-4-5"})
    status, raw = await open_turn(app, row.id, {"text": "read app.py twice", "turn_id": "th-1"}, timeout=120)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames[-1][0] == "done", frames[-3:]
    assert [d.get("name") for e, d in frames if e == "tool_call" and d.get("status") == "finished"] == ["read_file", "read_file"]
    rounds = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["round"] for r in rounds] == [0, 1, 2]
    for r in rounds:
        sent = [f for f in r["frames"] if f.get("type") == "assistant"]
        # Every earlier round, in order, WITH its own signed thinking.
        assert [_thinking_signatures(f) for f in sent] == [[f"sig-round-{k}"] for k in range(r["round"])], r["round"]
        # ... and the tool_use ids are the model's own (a verbatim replay,
        # not a rebuilt turn with minted ids).
        ids = [b["id"] for f in sent for b in f["message"]["content"] if b.get("type") == "tool_use"]
        assert ids == [f"toolu_r{k}" for k in range(r["round"])]
    # The reply line keeps role + content (+ the display thinking); no
    # native carrier crosses into the NEXT turn (v1.368.0 — accounts).
    line = messages_of(app, row.id)[-1]
    assert line["content"] == "All read." and "raw_blocks" not in line
