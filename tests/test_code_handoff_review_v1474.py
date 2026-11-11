"""v1.474.0 review — Hand off (``hosted/handoff``): defects an independent
review found, each pinned red first.

Driven through the real service and store with the recorded stand-in coding
tool (``tests/fixtures/fake_harness_v1409.py``); never a real CLI.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.hosted import handoff as HO
from tests._build_app_helpers_v1409 import stop_all
from tests.test_code_handoff_v1474 import _ctx, _setup, _tool


@pytest.mark.asyncio
async def test_starts_at_the_same_moment_never_pass_the_limit_together(tmp_path, monkeypatch):
    """A coding tool can send several tool calls at once. Each start counted
    the working helpers before any of them had begun, so five starts at once
    all saw none working and five ran."""
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        ctx = _ctx(app, parent, work)
        # A helper counts as working once its piece of work has started.
        monkeypatch.setattr(HO, "_running", lambda s, sid: sid in s.handoff_turns)
        got = await asyncio.gather(*(
            _tool(app, HO.TOOL_START).execute({"task": f"piece {i}"}, ctx) for i in range(5)))
        assert sum(1 for r in got if r.ok) == HO.MAX_RUNNING
        assert all("already working" in r.error for r in got if not r.ok)
    finally:
        monkeypatch.undo()
        await stop_all(app)


@pytest.mark.asyncio
async def test_more_work_for_an_idle_helper_still_counts_against_the_limit(tmp_path, monkeypatch):
    app, svc, parent, work = await _setup(tmp_path, monkeypatch)
    try:
        ctx = _ctx(app, parent, work)
        monkeypatch.setattr(HO, "_running", lambda s, sid: False)

        class _T:
            def __init__(self, tid):
                self.id = tid

        async def started(sid, text, turn_id, *a, **k):  # no real tool needed here
            return _T(turn_id)

        monkeypatch.setattr(svc, "start_turn", started)
        made = []
        for i in range(HO.MAX_RUNNING + 1):
            r = await _tool(app, HO.TOOL_START).execute({"task": f"piece {i}"}, ctx)
            assert r.ok, r.error
            made.append(r.data["helper"])
        idle, busy = made[0], set(made[1:])
        monkeypatch.setattr(HO, "_running", lambda s, sid: sid in busy)
        more = await _tool(app, HO.TOOL_START).execute({"task": "more", "helper": idle}, ctx)
        assert not more.ok and "already working" in more.error
    finally:
        monkeypatch.undo()
        await stop_all(app)
