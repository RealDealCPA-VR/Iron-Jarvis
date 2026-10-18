"""v1.351.0 lane 1B, G18: each agent run's receipt carries model, agent,
place, recovery and actions.

``GET /sessions/{id}/result`` (agents/outcome.session_result with
``receipt=True``) names the provider and model the run was SERVED by, the
agent that ran it (a custom agent's own name, not its base type), the route
note when its model failed over (the mission screen's words, per run), whether
the model stays on this network, and up to 20 plain action lines (the mission
screen's activity words, lifted into ``mission.activity_lines``).
``GET /sessions`` rows carry ``agent_name`` and ``interrupted_at``.

Driven through the REAL app factory over a seeded ledger.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from iron_jarvis.agents import mission
from iron_jarvis.agents.outcome import RECEIPT_ACTIVITY_MAX, session_result
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    EventRecord,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app

_BASE = datetime(2026, 10, 18, 7, 0, 0)


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _call(db, iid, sid, tool, minutes, *, ok=True, args=None, verdict=PermissionMode.ALLOW):
    db.add(
        ToolInvocation(
            id=iid,
            session_id=sid,
            agent_run_id=f"run_{sid}",
            tool=tool,
            args_json=json.dumps(args or {}),
            verdict=verdict,
            ok=ok,
            reversibility="readonly" if tool.startswith("read") else "reversible",
            created_at=_BASE + timedelta(seconds=minutes),
        )
    )


def _seed_delegated_run(engine, sid="sess_tax", provider="anthropic", model="claude-sonnet-4-6"):
    """A finished run of the custom agent ``custom:tax-reader`` (its base type
    builder), that read a PDF, failed a write, was refused an ask, ran a quiet
    bookkeeping tool, and whose model failed over once."""
    with session_scope(engine) as db:
        db.add(
            Session(
                id=sid,
                task="Read the K-1 and summarise it",
                agent_name="custom:tax-reader",
                provider=provider,
                model=model,
                status=SessionStatus.COMPLETED,
                origin="job:agents",
                created_at=_BASE,
                finished_at=_BASE + timedelta(minutes=3),
            )
        )
        db.add(AgentRun(id=f"run_{sid}", session_id=sid, created_at=_BASE))
        _call(db, "i1", sid, "read_file", 1, args={"path": "C:/clients/acme/k1-2025.pdf"})
        _call(db, "i2", sid, "worklist_next", 2)  # bookkeeping: never a line
        _call(db, "i3", sid, "write_file", 3, ok=False, args={"path": "C:/clients/acme/summary.md"})
        _call(db, "i4", sid, "shell", 4, ok=False, verdict=PermissionMode.ASK)
        db.add(
            EventRecord(
                id="ev_denied",
                type="tool.denied",
                session_id=sid,
                payload_json=json.dumps({"invocation_id": "i4", "kind": "permission denied", "tool": "shell"}),
                created_at=_BASE + timedelta(seconds=4),
            )
        )
        db.add(
            EventRecord(
                id="ev_failover",
                type="provider.failover",
                session_id=sid,
                payload_json=json.dumps(
                    {"from": "ollama", "to": "anthropic", "reason": "unreachable"}
                ),
                created_at=_BASE + timedelta(seconds=5),
            )
        )
        db.commit()


def test_the_result_names_provider_model_agent_place_recovery_and_actions(tmp_path):
    client = _client(tmp_path)
    _seed_delegated_run(client.app.state.platform.engine)
    r = client.get("/sessions/sess_tax/result")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-sonnet-4-6"
    # The custom agent, not the builder it is based on.
    assert body["agent_name"] == "custom:tax-reader"
    assert body["agent_type"] == "builder"
    # A cloud API never stays on this network.
    assert body["on_this_network"] is False
    # Recovery: the failover, in the mission screen's words, for this run.
    assert body["route_note"] == (
        "tax-reader's model ollama failed (unreachable); anthropic answered instead."
    )
    # Actions: plain words, oldest first, bookkeeping left out, a refused ask
    # worded as a refusal, never as the agent failing.
    assert [a["text"] for a in body["activity"]] == [
        "tax-reader read k1-2025.pdf",
        "tax-reader could not write summary.md",
        "tax-reader needed your OK to use shell",
    ]
    assert [a["tone"] for a in body["activity"]] == ["ok", "warn", "ask"]
    assert all(a["session_id"] == "sess_tax" for a in body["activity"])
    # Existing keys are untouched.
    assert body["found"] is True and body["status"] == "completed"
    assert {t["tool"] for t in body["tools_used"]} >= {"read_file", "write_file"}


def test_no_failover_means_no_route_note_and_a_local_model_stays_on_this_network(tmp_path):
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        db.add(
            Session(
                id="sess_mock",
                task="t",
                provider="mock",
                model="mock-1",
                status=SessionStatus.COMPLETED,
                created_at=_BASE,
            )
        )
        db.commit()
    body = client.get("/sessions/sess_mock/result").json()
    assert body["route_note"] == ""
    assert body["on_this_network"] is True  # the offline demo model runs in-process
    assert body["agent_name"] is None
    assert body["activity"] == []


def test_activity_keeps_the_newest_twenty_lines(tmp_path):
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        db.add(Session(id="sess_busy", task="t", status=SessionStatus.COMPLETED, created_at=_BASE))
        for i in range(25):
            _call(db, f"b{i:02d}", "sess_busy", "read_file", i, args={"path": f"C:/f/{i:02d}.txt"})
        db.commit()
    body = client.get("/sessions/sess_busy/result").json()
    texts = [a["text"] for a in body["activity"]]
    assert len(texts) == RECEIPT_ACTIVITY_MAX == 20
    assert texts[0].endswith("read 05.txt")
    assert texts[-1].endswith("read 24.txt")


def test_two_route_changes_are_counted_for_the_run(tmp_path):
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    _seed_delegated_run(engine)
    with session_scope(engine) as db:
        db.add(
            EventRecord(
                id="ev_down",
                type="provider.downgraded",
                session_id="sess_tax",
                payload_json=json.dumps({"used": "none", "label": "Office box", "reason": "unreachable"}),
                created_at=_BASE + timedelta(seconds=9),
            )
        )
        db.commit()
    note = client.get("/sessions/sess_tax/result").json()["route_note"]
    assert note.startswith("tax-reader could not use Office box (unreachable).")
    assert note.endswith("There were 2 route changes in this run.")


def test_the_receipt_reads_stay_off_every_other_caller(tmp_path):
    """Cost guard: the finalize / coach / compaction callers get who-and-what
    (row fields) but not the receipt's extra reads."""
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    _seed_delegated_run(engine)
    plain = session_result(engine, "sess_tax")
    assert plain["provider"] == "anthropic" and plain["agent_name"] == "custom:tax-reader"
    for key in ("route_note", "on_this_network", "activity"):
        assert key not in plain
    full = session_result(engine, "sess_tax", receipt=True)
    assert len(full["activity"]) == 3


def test_an_unknown_session_still_404s(tmp_path):
    client = _client(tmp_path)
    assert client.get("/sessions/nope/result").status_code == 404


def test_session_rows_carry_agent_name_and_interrupted_at(tmp_path):
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    cut = _BASE + timedelta(hours=1)
    with session_scope(engine) as db:
        db.add(
            Session(
                id="sess_cut",
                task="Rename the files",
                agent_name="custom:renamer",
                status=SessionStatus.FAILED,
                interrupted_at=cut,
                created_at=_BASE,
            )
        )
        db.add(Session(id="sess_plain", task="t", status=SessionStatus.COMPLETED, created_at=_BASE))
        db.commit()
    rows = {s["id"]: s for s in client.get("/sessions").json()["sessions"]}
    assert rows["sess_cut"]["agent_name"] == "custom:renamer"
    assert rows["sess_cut"]["interrupted_at"] == cut.isoformat()
    assert rows["sess_plain"]["agent_name"] is None
    assert rows["sess_plain"]["interrupted_at"] is None
    # The one renderer: the single-session read carries them too.
    one = client.get("/sessions/sess_cut").json()["session"]
    assert one["agent_name"] == "custom:renamer"
    assert one["interrupted_at"] == cut.isoformat()


def test_the_mission_screen_and_the_receipt_share_one_wording(tmp_path):
    """The lift: ``activity_lines`` IS the loop mission_view uses — same rows
    in, same lines out (mission_view's own pins cover its whole view)."""
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    _seed_delegated_run(engine)
    with session_scope(engine) as db:
        lines = mission.activity_lines(db, ["sess_tax"], {"sess_tax": "Jarvis"})
        invs, denied = mission._read_tool_rows(db, ["sess_tax"])
    again, last = mission.tool_activity(invs, denied, {"sess_tax": "Jarvis"})
    assert lines == again
    assert [ln["text"] for ln in lines][0] == "Jarvis read k1-2025.pdf"
    assert last["sess_tax"] == "needed your OK to use shell"
