"""The kept-process keeper is wired where runs and the daemon END (v1.333.0).

``core/procs`` keeps the hidden programs a SUCCESSFUL shell / run_code /
custom-tool call left running (a dev server the next step uses) under the
calling session, and stops them through ``end_session`` / ``end_all``. Those
two functions only matter if something calls them: an agent run ends its own
session's kept programs, and the daemon's shutdown ends everyone's. Chat's
session id "chat" spans every turn, so a run never ends chat's.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core import procs
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon import app as app_mod


@pytest.mark.asyncio
async def test_a_finished_agent_run_ends_the_programs_its_session_kept(platform, monkeypatch):
    ended: list[str] = []
    monkeypatch.setattr(procs, "end_session", lambda sid: ended.append(sid) or [])
    orch = Orchestrator(platform)
    session = await orch.create_session("say hello")
    await orch.runtime.run(session, get_agent_definition(AgentType.BUILDER))
    assert ended == [session.id]
    assert "chat" not in ended


def test_the_daemon_ends_every_kept_program_at_shutdown(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(procs, "end_all", lambda: calls.append("end_all") or [])
    app = app_mod.create_app(str(tmp_path))
    with TestClient(app):
        assert calls == []
    assert calls == ["end_all"]
