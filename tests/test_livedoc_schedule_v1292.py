"""v1.292.0 (platform-01): a scheduled living-document refresh must actually
regenerate the document.

EventBus._dispatch runs every sync handler via asyncio.to_thread, so the
livedoc handler NEVER sees a running loop in its own thread: the only branch
that executes in production is RuntimeError -> _live_rearm["loop"] ->
run_coroutine_threadsafe. The old handler's ``except RuntimeError: pass``
dropped every scheduled / Run-now refresh while the schedule ledger said
"done". These drive the REAL create_app lifespan, the REAL event bus, the REAL
/schedules/{name}/run route and scheduler._fire on a foreign thread (the
APScheduler shape); only the provider's complete() is stubbed.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
import warnings

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _stub_provider(platform, monkeypatch) -> None:
    real_get = platform.providers.get

    def spy(p, m=None):
        adapter = real_get(p, m)

        async def complete(*, system, messages, tools):
            from iron_jarvis.providers.adapters.base import LLMResponse

            return LLMResponse(text="# Weekly\n\nok", tool_calls=[], usage={})

        adapter.complete = complete
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy)


def _setup(monkeypatch, client):
    """A living doc with a weekly cron; returns (platform, regen calls, schedule)."""
    app = client.app
    platform = app.state.platform
    _stub_provider(platform, monkeypatch)
    calls: list[str] = []
    real = app.state.regenerate_livedoc

    async def counting(doc_id):
        calls.append(doc_id)
        return await real(doc_id)

    app.state.regenerate_livedoc = counting
    r = client.post(
        "/documents/live",
        json={"name": "Weekly", "prompt": "status", "format": "md", "cron": "0 7 * * 1"},
    ).json()
    assert r["ok"], r
    sched = client.get("/documents/live").json()["docs"][0]["schedule_name"]
    return platform, calls, sched


def _wait_for(pred, ceiling: float = 10.0) -> None:
    """Wait for the thing we assert (bounded ceiling, no wall-clock assertion)."""
    deadline = time.time() + ceiling
    while not pred() and time.time() < deadline:
        time.sleep(0.05)


def test_run_now_on_a_livedoc_schedule_regenerates(tmp_path, monkeypatch):
    """Run now on the schedule row -> the bus -> to_thread handler -> the
    lifespan loop -> regenerate_livedoc really runs."""
    with TestClient(create_app(str(tmp_path))) as client:
        platform, calls, sched = _setup(monkeypatch, client)
        resp = client.post(f"/schedules/{sched}/run")
        assert resp.status_code == 200, resp.text
        _wait_for(lambda: bool(calls))
        assert calls, "Run-now on a living-doc schedule never called regenerate_livedoc"


def test_apscheduler_fire_on_a_livedoc_schedule_regenerates(tmp_path, monkeypatch):
    """Exactly what APScheduler does: _fire on a foreign worker thread (its own
    short-lived asyncio.run loop) -> the regeneration must still land on the
    daemon loop."""
    with TestClient(create_app(str(tmp_path))) as client:
        platform, calls, sched = _setup(monkeypatch, client)
        t = threading.Thread(target=platform.scheduler._fire, args=(sched,))
        t.start()
        t.join(30)
        _wait_for(lambda: bool(calls))
        assert calls, "a scheduled fire never called regenerate_livedoc"


def test_no_daemon_loop_warns_instead_of_dropping_silently(tmp_path, caplog):
    """After the lifespan has exited (``_live_rearm`` cleared -> no daemon loop
    to hop to): the event goes through the REAL bus (to_thread dispatch), the
    handler finds no loop, and must SAY so at WARNING -- never silent, never
    an un-awaited coroutine, never a raise into the bus."""
    app = create_app(str(tmp_path))
    with TestClient(app):
        pass  # the lifespan registers the handler, then clears the loop on exit
    platform = app.state.platform
    calls: list[str] = []

    async def counting(doc_id):
        calls.append(doc_id)

    app.state.regenerate_livedoc = counting
    with caplog.at_level(logging.WARNING), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(platform.event_bus.publish("livedoc.regenerate", {"livedoc_id": "doc_x"}))
        gc.collect()
    assert calls == []
    assert not [w for w in caught if "never awaited" in str(w.message)]
    dropped = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "living-doc regeneration" in r.getMessage()
        and "doc_x" in r.getMessage()
    ]
    assert dropped, [r.getMessage() for r in caplog.records]
