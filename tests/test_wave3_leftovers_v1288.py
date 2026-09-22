"""v1.288.0 review leftovers (agents-04 / agents-05, second pass).

Three gaps the reviewers found after the wave's own doer/reviewer round:

* the phone's DYNAMIC-agent lane (``InboundPoller._run_dynamic_session``) ran
  the runtime with no finalizer, so a provider crash left the session ACTIVE
  and its run row RUNNING until the next boot — every other lane ends in
  ``Orchestrator._finalize_failed``; now this one does too;
* the boot reconcile only looked at ACTIVE/QUEUED sessions, so run rows the
  pre-v1.288.0 failure finalizer left RUNNING under an already-FAILED session
  stayed that way forever — the one-off repair the finding asked for;
* ``POST /agents/remote`` accepted a NEGATIVE ``timeout_s`` (PATCH clamped);
  with the value now a total ``asyncio.timeout`` a stored negative expired at
  once.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.comm import Notifier
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

_ERR = "fleet down at 3am"


class _Boom(MockLLMAdapter):
    async def complete(self, **kw):
        raise RuntimeError(_ERR)

    async def stream(self, **kw):
        raise RuntimeError(_ERR)
        yield  # pragma: no cover


def _runs(p, session_id: str) -> list[tuple[AgentState, object]]:
    with session_scope(p.engine) as db:
        return [
            (r.state, r.finished_at)
            for r in db.exec(select(AgentRun).where(AgentRun.session_id == session_id))
        ]


def test_the_phones_dynamic_agent_lane_settles_a_crash(tmp_path):
    p = build_platform(str(tmp_path))
    p.providers.register("mock", lambda model=None: _Boom())
    orch = Orchestrator(p)
    poller = InboundPoller(Notifier(), orch, p.engine, platform=p)

    async def go():
        s = await orch.create_session(
            "escalated from the phone", provider="mock", model="mock-1",
            origin="comm:telegram",
        )
        with pytest.raises(RuntimeError):
            await poller._run_dynamic_session(s, get_agent_definition(AgentType.BUILDER))
        return s.id

    sid = asyncio.run(go())
    refreshed = orch.get_session(sid)
    assert refreshed.status is SessionStatus.FAILED, refreshed.status
    assert _ERR in (refreshed.summary or ""), refreshed.summary
    rows = _runs(p, sid)
    assert rows and all(st is AgentState.FAILED and fin is not None for st, fin in rows), rows


def test_boot_reconcile_repairs_a_ghost_row_under_an_already_failed_session(tmp_path):
    p = build_platform(str(tmp_path))
    orch = Orchestrator(p)

    async def make():
        s = await orch.create_session("old job", provider="mock", model="mock-1")
        return s.id

    sid = asyncio.run(make())
    # What the pre-v1.288.0 failure finalizer left behind: a FAILED session
    # with its run row still RUNNING.
    with session_scope(p.engine) as db:
        s = db.get(Session, sid)
        s.status = SessionStatus.FAILED
        db.add(s)
        db.add(AgentRun(session_id=sid, state=AgentState.RUNNING))
        db.add(AgentRun(session_id=sid, state=AgentState.COMPLETED, result="done"))
        db.commit()

    Orchestrator(p).reconcile_interrupted_sessions()

    with session_scope(p.engine) as db:
        rows = {
            r.state: r
            for r in db.exec(select(AgentRun).where(AgentRun.session_id == sid))
        }
        s = db.get(Session, sid)
    assert AgentState.RUNNING not in rows, list(rows)
    assert AgentState.FAILED in rows and rows[AgentState.FAILED].finished_at is not None
    assert rows[AgentState.FAILED].result  # says why in words
    assert rows[AgentState.COMPLETED].result == "done"  # a settled row is untouched
    # The session itself was already terminal: not relabelled "interrupted".
    assert s.status is SessionStatus.FAILED and s.interrupted_at is None


def test_creating_a_remote_with_a_negative_timeout_clamps_like_patch(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/agents/remote",
        json={"name": "slow", "base_url": "http://192.168.1.50:8080", "timeout_s": -5},
    )
    assert r.status_code == 200, r.text
    assert r.json()["timeout_s"] == 1, r.json()
