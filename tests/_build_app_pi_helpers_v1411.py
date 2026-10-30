"""Shared helpers for the Pi Code-view tests (v1.411.0). Every test replays a
RECORDED Pi 1.3.0 session (``tests/fixtures/build_app_v1411/*.json``, masked
live runs) through ``tests/fixtures/fake_harness_v1409.py`` — the real
process plumbing, parser, routes and store; never a real Pi."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, stop_all, until

FIX_PI = Path(__file__).parent / "fixtures" / "build_app_v1411"

__all__ = ["FIX_PI", "use_fake_pi", "run_pi", "client", "frames", "new_session", "stop_all", "until", "canonical"]


def use_fake_pi(monkeypatch, scenario: str, log: Path) -> None:
    from iron_jarvis.hosted import service as S

    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX_PI / f"{scenario}.json"))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def canonical(events: list[dict], work: Path) -> list[dict]:
    """``at`` dropped, Iron Jarvis's own ids replaced by stable names, this
    run's folder written as the recordings' masked one."""
    names: dict[str, str] = {}
    sessions: dict[str, str] = {}
    out = []
    for ev in events:
        ev = json.loads(json.dumps(ev).replace(json.dumps(str(work))[1:-1], r"C:\\work\\demo"))
        ev = {k: v for k, v in ev.items() if k != "at"}
        rid = ev.get("request_id")
        if rid:
            names.setdefault(rid, f"ask-{len(names) + 1}")
            ev["request_id"] = names[rid]
        if "turn" in ev:
            ev["turn"] = "turn-1"
        hid = ev.get("harness_session_id")
        if hid:
            sessions.setdefault(hid, f"harness-session-{len(sessions) + 1}")
            ev["harness_session_id"] = sessions[hid]
        out.append(ev)
    return out


async def run_pi(tmp_path: Path, monkeypatch, fixture: str, actions: list, *, text: str = "do it",
                 name: str = "") -> tuple[list[dict], Any, str, Path]:
    """One turn of ``fixture`` with the user's ``actions`` (``("answer",
    decision, answers)`` / ``("interrupt",)``). Returns the turn's events,
    the app, the session id and the work folder (the app is shut down)."""
    base = tmp_path / (name or fixture)
    work = base / "work"
    work.mkdir(parents=True)
    use_fake_pi(monkeypatch, fixture, base / "fake.log")
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import service as S

    app = create_app(str(base / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="pi"))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": "t"}))
        done_asks: set[str] = set()
        for act in actions:
            if act[0] == "interrupt":
                await until(lambda: svc.turn("t") and any(e["type"] == "text" for e in svc.turn("t").events))
                r = await c.post(f"/build/app/sessions/{sid}/interrupt")
                assert r.status_code == 200, r.text
                continue
            _, decision, answers = act
            ask = await until(lambda: next((a for a in (svc.live.get(sid).asks if svc.live.get(sid) else {})
                                            if a not in done_asks), None))
            done_asks.add(ask)
            r = await c.post(f"/build/app/sessions/{sid}/answer",
                             json={"request_id": ask, "decision": decision, "answers": answers})
            assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    return evs, app, sid, work
