"""v1.400.0 review of TEAM-FIRST: the words reach the model TONIGHT's planner
runs on, through the adapters that carry them — offline, no real CLI.

The planner tonight is Claude Code (claude-cli / sonnet). A coding session's
system prompt and tool list do not go to it as an API request: the claude-cli
adapter writes the prompt to ``--system-prompt-file``, the tools to the inert
MCP server's manifest (``tools.json``, listed to Claude Code by
``tools/list``) and to ``CLAUDE_CODE_EXTRA_BODY`` in ``--settings``. A fake
``claude`` (``fixtures/fake_claude_reads_v1400.py``) reads each of those the
way Claude Code does — it STARTS the inert server and asks it — so these pins
prove the team-first instruction and the helper tool's leading sentence
arrive where the model reads them, through a real Build session's turn.

The same for Codex (codex-cli, the app-server): the prompt rides
``baseInstructions`` and the tools ``dynamicTools`` of ``thread/start``,
recorded by the fake app-server (``fixtures/fake_codex_app_server_v1365.py``).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from iron_jarvis.agents import model_team as MT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.adapters.codex_native import adapter as A
from tests.test_build_sessions_v1367 import frames_of, open_turn
from tests.test_codex_native_v1365 import Fake, _clean  # noqa: F401 — the fixture (Codex sessions, sign-in probe)

FAKE_CLAUDE = Path(__file__).parent / "fixtures" / "fake_claude_reads_v1400.py"

#: The instruction for the team below (glm: read + code; codex: review).
WORDS = MT.team_first_words(["read", "code", "review"])
CLAUDE_SENTENCE = ("Your model team is listed under 'Your model team': for a piece that fits a role, "
                   "give subagent_type role:read, role:code or role:review.")
CODEX_SENTENCE = ("Your model team is listed under 'Your model team': for a piece that fits a role, "
                  "give agent_type role:read, role:code or role:review.")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    from iron_jarvis.providers import cli_auth

    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)


def _claude_shim(tmp_path: Path) -> str:
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE_CLAUDE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


def _app(tmp_path: Path, monkeypatch, provider: str, factory, *, team_first: bool):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.providers.register(provider, factory)
    real_available = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: name == provider or real_available(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    reg = p.agents_registry
    reg.register("glm", "You are glm.", [], description="reads and drafts code", provider="mock",
                 model="glm-5.3-flash", roles=["read", "code"])
    reg.register("codex", "You are codex.", [], description="reviews a change", provider="codex-cli",
                 model="gpt-6-astra", roles=["review"])
    p.config.team_first = team_first
    return app


async def _build_turn(app, tmp_path: Path, provider: str, model: str) -> list:
    from iron_jarvis.harness import sessions as S

    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / "app.py").write_text("x = 1\n", encoding="utf-8")
    row = S.create(app.state.platform.engine, str(work), "", {"provider": provider, "model": model})
    status, raw = await open_turn(app, row.id, {"text": "fix the bug in app.py", "turn_id": "tf-1"}, timeout=120)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames[-1][0] == "done", frames[-3:]
    return frames


@pytest.mark.parametrize("team_first", [True, False])
async def test_claude_code_reads_the_instruction_and_the_agent_tools_first_sentence(tmp_path, monkeypatch,
                                                                                   team_first):
    shim = _claude_shim(tmp_path)
    log = tmp_path / "reads.jsonl"
    monkeypatch.setenv("FAKE_READS_LOG", str(log))
    app = _app(tmp_path, monkeypatch, "claude-cli",
               lambda model=None: sc.make_claude_cli(which=lambda _b: shim, model=model or "claude-sonnet-4-5"),
               team_first=team_first)
    await _build_turn(app, tmp_path, "claude-cli", "claude-sonnet-4-5")
    (read,) = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # The system prompt Claude Code is started with (--system-prompt-file).
    assert "# Your model team" in read["system"]
    assert (read["system"].count(WORDS) == 1) is team_first
    # The tools the inert server LISTS to Claude Code, and the ones the API
    # request carries (CLAUDE_CODE_EXTRA_BODY): the same descriptions.
    listed = {t["name"]: t["description"] for t in read["listed"]}
    body = {t["name"]: t["description"] for t in read["body_tools"]}
    assert body == {f"mcp__ij__{name}": text for name, text in listed.items()}
    led = sorted(name for name, text in listed.items() if text.startswith("Your model team is listed under"))
    if team_first:
        assert led == ["Agent"]
        assert listed["Agent"].startswith(CLAUDE_SENTENCE + " Starts a helper agent")
    else:
        assert led == []
        assert listed["Agent"].startswith("Starts a helper agent")


@pytest.mark.parametrize("team_first", [True, False])
async def test_codex_reads_the_instruction_and_its_spawn_agents_first_sentence(tmp_path, monkeypatch, team_first):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "Done."},
                                                   {"usage": {"inputTokens": 10, "outputTokens": 1}}]}])
    app = _app(tmp_path, monkeypatch, "codex-cli",
               lambda model=None: A.CodexNativeAdapter(which=lambda _b: fake.shim, model=model or "gpt-6-astra"),
               team_first=team_first)
    await _build_turn(app, tmp_path, "codex-cli", "gpt-6-astra")
    (start,) = fake.received("thread/start")
    params = start["msg"]["params"]
    assert "# Your model team" in params["baseInstructions"]
    assert (params["baseInstructions"].count(WORDS) == 1) is team_first
    tools = {t["name"]: t["description"] for t in params["dynamicTools"]}
    led = sorted(name for name, text in tools.items() if text.startswith("Your model team is listed under"))
    # Codex's own spawn_agent is a name Codex keeps for itself: Iron Jarvis's
    # rides as ij_spawn_agent, with the sentence first.
    if team_first:
        assert led == ["ij_spawn_agent"]
        assert tools["ij_spawn_agent"].startswith(CODEX_SENTENCE + " Spawn a sub-agent")
    else:
        assert led == []
        assert tools["ij_spawn_agent"].startswith("Spawn a sub-agent")
