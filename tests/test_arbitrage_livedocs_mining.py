"""#1 subscription CLIs, #4 living docs, #5 watch-me-work mining."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.subprocess_cli import (
    make_claude_cli, make_codex_cli,
)


@pytest.mark.asyncio
async def test_claude_cli_adapter_flattens_and_parses_json():
    calls = {}

    def runner(argv):
        calls["argv"] = argv
        return 0, '{"type":"result","result":"flat-rate answer"}', ""

    a = make_claude_cli(runner=runner, which=lambda b: f"/x/{b}")
    r = await a.complete(system="be brief", messages=[LLMMessage(role="user", content="hi")], tools=[])
    assert r.text == "flat-rate answer"
    assert calls["argv"][0] == "/x/claude" and "-p" in calls["argv"]
    assert "--output-format" in calls["argv"]
    assert "be brief" in calls["argv"][calls["argv"].index("-p") + 1]


@pytest.mark.asyncio
async def test_codex_cli_adapter_and_errors():
    a = make_codex_cli(
        runner=lambda argv: (0, "OpenAI Codex v9\n\nbanner\n\nthe real answer", ""),
        which=lambda b: f"/x/{b}",
    )
    r = await a.complete(system="", messages=[LLMMessage(role="user", content="q")], tools=[])
    assert r.text == "the real answer"
    # Missing binary + nonzero exit are honest errors.
    b = make_claude_cli(which=lambda _: None)
    with pytest.raises(RuntimeError, match="not installed"):
        await b.complete(system="", messages=[], tools=[])
    c = make_claude_cli(runner=lambda a_: (1, "", "login required"), which=lambda b_: "/x/claude")
    with pytest.raises(RuntimeError, match="login required"):
        await c.complete(system="", messages=[], tools=[])


def test_cli_providers_registered_and_in_models(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    models = client.get("/models").json()["models"]
    provs = {m["provider"] for m in models}
    assert {"claude-cli", "codex-cli"} <= provs  # pickable (available flag varies by machine)


def test_livedoc_lifecycle(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)

        async def c(*, system, messages, tools):
            from iron_jarvis.providers.adapters.base import LLMResponse
            return LLMResponse(text="# Weekly Status\n\nAll green.", tool_calls=[], usage={})

        a.complete = c
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    r = client.post("/documents/live", json={
        "name": "Weekly Status", "prompt": "status report", "format": "md",
        "cron": "0 7 * * 1",
    }).json()
    assert r["ok"] and r["path"].endswith("weekly-status.md")
    from pathlib import Path
    assert "All green" in Path(r["path"]).read_text(encoding="utf-8")
    docs = client.get("/documents/live").json()["docs"]
    assert docs[0]["schedule_name"].startswith("livedoc_")
    # Schedule row exists; regenerate works; delete keeps the FILE.
    scheds = client.get("/schedules").json()["schedules"]
    assert any(s["name"] == docs[0]["schedule_name"] for s in scheds)
    rid = docs[0]["id"]
    assert client.post(f"/documents/live/{rid}/regenerate").json()["ok"]
    d = client.delete(f"/documents/live/{rid}").json()
    assert d["files_touched"] == 0
    assert Path(r["path"]).exists()  # generated file untouched on disk
    assert client.get("/documents/live").json()["docs"] == []


def test_livedoc_validation(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/documents/live", json={"name": "", "prompt": "x"}).status_code == 400
    assert client.post("/documents/live", json={"name": "a", "prompt": "x", "format": "exe"}).status_code == 400


def test_template_suggestions_mining(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    for i in range(3):
        client.post("/sessions", json={"task": f"draft follow up email to client number {i}", "wait": True})
    client.post("/sessions", json={"task": "completely unrelated one-off", "wait": True})
    sugg = client.get("/templates/suggestions").json()["suggestions"]
    assert any("follow" in s["task"] for s in sugg), sugg
    hit = next(s for s in sugg if "follow" in s["task"])
    assert hit["count"] >= 3
