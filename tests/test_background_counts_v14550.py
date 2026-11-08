"""v1.455.0 (readiness Wave F, item 11): the orb and the home line count a
tool-only workflow run and a media job as running.

``daemon/background_counts.background_running`` is the one count, read from
memory: workflow run ids (``wfrun…``) in ``orchestrator._running`` and the
media jobs this process holds. ``/health`` carries it as ``background`` (the
orb), and ``/ui/status-line``'s ``running`` adds the media jobs (a workflow
run id was already in ``_running``).
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.daemon.background_counts import background_running


class _Jobs:
    def __init__(self, ids):
        self._ids = ids

    def live_ids(self):
        return list(self._ids)


def _d(running=(), media=()):
    return SimpleNamespace(
        orchestrator=SimpleNamespace(_running={k: object() for k in running}),
        platform=SimpleNamespace(media_jobs=_Jobs(media)),
    )


def test_counts_workflow_runs_and_media_jobs_from_memory():
    d = _d(running=["wfrun_1", "session_abc", "wfrun_2", "comm_x"], media=["mj_1"])
    assert background_running(d) == {"workflows": 2, "media": 1}


def test_never_raises_and_counts_what_it_cannot_read_as_zero():
    assert background_running(SimpleNamespace()) == {"workflows": 0, "media": 0}

    class _Broken:
        def live_ids(self):
            raise RuntimeError("boom")

    d = SimpleNamespace(orchestrator=None, platform=SimpleNamespace(media_jobs=_Broken()))
    assert background_running(d) == {"workflows": 0, "media": 0}


def _app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


def test_health_carries_background_and_the_status_line_counts_media(tmp_path, monkeypatch):
    app = _app(tmp_path)
    with TestClient(app) as c:
        d = app.state.d
        h = c.get("/health").json()
        assert h["background"] == {"workflows": 0, "media": 0}
        before = c.get("/ui/status-line").json()["running"]

        monkeypatch.setattr(d.platform.media_jobs, "live_ids", lambda: ["mj_1", "mj_2"])
        d.orchestrator._running["wfrun_test"] = object()
        try:
            h = c.get("/health").json()
            assert h["background"] == {"workflows": 1, "media": 2}
            # The workflow run is already one of ``_running``; media adds two.
            assert c.get("/ui/status-line").json()["running"] == before + 1 + 2
        finally:
            d.orchestrator._running.pop("wfrun_test", None)
