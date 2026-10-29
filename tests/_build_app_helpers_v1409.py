"""Shared helpers for the Code-view tests (v1.409.0). Every test replays a
RECORDED harness session through ``tests/fixtures/fake_harness_v1409.py`` —
the real process plumbing (``core.procs``), the real parser, the real routes,
the real store; never a real Claude Code or Codex."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

FIX = Path(__file__).parent / "fixtures" / "build_app_v1409"
FAKE = Path(__file__).parent / "fixtures" / "fake_harness_v1409.py"


def use_fake(monkeypatch, scenario: str, log: Path) -> None:
    """Point both harnesses at the fake, replaying ``scenario``."""
    from iron_jarvis.hosted import service as S

    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / f"{scenario}.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def fake_log(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def sent(log: Path) -> list[dict[str, Any]]:
    """Every message the host sent the fake harness, in order."""
    return [r["in"] for r in fake_log(log) if "in" in r]


def starts(log: Path) -> list[dict[str, Any]]:
    """One row per process start: argv, cwd, env."""
    return [r for r in fake_log(log) if "argv" in r]


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                             timeout=60.0)


def frames(raw: str) -> list[dict[str, Any]]:
    out = []
    for block in raw.split("\n\n"):
        data = "".join(line[5:].strip() for line in block.split("\n") if line.startswith("data:"))
        if data:
            out.append(json.loads(data))
    return out


async def until(pred, timeout: float = 30.0, step: float = 0.05):
    """Wait for ``pred()`` (sync or async) to be truthy; return its value."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        got = pred()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return got
        if loop.time() > end:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(step)


async def new_session(c: httpx.AsyncClient, folder: Path, harness: str = "claude-code", **extra) -> dict:
    r = await c.post("/build/app/sessions", json={"harness": harness, "folder": str(folder), **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def pending(c: httpx.AsyncClient, sid: str) -> list[dict]:
    return (await c.get(f"/build/app/sessions/{sid}")).json()["pending"]


async def wait_pending(c: httpx.AsyncClient, sid: str, n: int = 1) -> list[dict]:
    async def got():
        p = await pending(c, sid)
        return p if len(p) >= n else None

    return await until(got)


async def stop_all(app) -> None:
    svc = getattr(app.state.platform, "hosted", None)
    if svc is not None:
        await svc.shutdown()
