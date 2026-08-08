"""Execution truth (v1.149.0) — what the agent DID, not what it said it would.

The report: "agents must stop only describing what they intend to do." Iron
Jarvis was never guessing — every tool call and every file mutation has been in
``ToolInvocation`` + ``UndoJournal`` since TX-01. Nothing summarised it back, so
the only thing a user saw was the model's own closing paragraph, which is
exactly the sentence that can claim work that never happened.

The load-bearing property here is that the outcome is derived from the LEDGER.
:func:`test_a_reply_that_claims_a_file_it_never_wrote_is_contradicted` is the
one that matters: a session whose summary says it wrote a file, with no write
journaled, must report no files. If that ever passes by reading the summary,
the whole wave is decorative.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.agents.outcome import did_nothing, session_result
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    Session,
    SessionStatus,
    ToolInvocation,
    UndoJournal,
)
from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed(
    engine,
    *,
    status=SessionStatus.COMPLETED,
    summary="",
    task="do the thing",
    tools=(),
    journals=(),
):
    """Write a session + its ledger rows directly — these tests are about how the
    LEDGER is read, so the rows are the fixture."""
    sid = "session_seed"
    with session_scope(engine) as db:
        db.add(
            Session(
                id=sid, task=task, status=status, summary=summary,
                workspace_path="/ws",
            )
        )
        db.add(AgentRun(id="run_seed", session_id=sid, steps=3))
        for i, spec in enumerate(tools):
            db.add(
                ToolInvocation(
                    id=f"tool_{i}",
                    session_id=sid,
                    agent_run_id="run_seed",
                    tool=spec.get("tool", "write_file"),
                    ok=spec.get("ok", True),
                    output=spec.get("output", ""),
                    reversibility=spec.get("reversibility", "reversible"),
                    undone_at=spec.get("undone_at"),
                    undo_of=spec.get("undo_of"),
                    **({"created_at": spec["created_at"]} if "created_at" in spec else {}),
                )
            )
        for j in journals:
            # session_id / agent_run_id / tool are how the real registry writes
            # these rows — a fixture that omits them is a state the app cannot
            # be in, and the NOT NULL constraint says so.
            db.add(
                UndoJournal(
                    action_id=j["action_id"],
                    session_id=sid,
                    agent_run_id="run_seed",
                    tool=j.get("tool", "write_file"),
                    kind=j["kind"],
                    reversible=j.get("reversible", True),
                    pre_inline=j.get("pre_inline", "{}"),
                )
            )
        db.commit()
    return sid


# --------------------------------------------------------------------------- #
# (1) THE HEADLINE: the ledger contradicts a reply that overclaims.
# --------------------------------------------------------------------------- #
def test_a_reply_that_claims_a_file_it_never_wrote_is_contradicted(tmp_path):
    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        summary="Done! I've saved the summary to notes.md for you.",
        tools=(),  # ...and not one tool ran
    )
    result = session_result(client.app.state.platform.engine, sid)
    assert result["files_created"] == []
    assert result["files_changed"] == []
    assert result["tools_used"] == []
    assert did_nothing(result) is True, (
        "a completed session with zero tool calls is an agent that DESCRIBED "
        "the work — the card has to be able to say so"
    )


def test_files_come_from_journaled_mutations(tmp_path):
    import json

    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=({"tool": "write_file"}, {"tool": "write_file"}),
        journals=(
            {
                "action_id": "tool_0",
                "kind": "file_delete",  # undo DELETES it => the tool CREATED it
                "pre_inline": json.dumps({"path": "/ws/report.md"}),
            },
            {
                "action_id": "tool_1",
                "kind": "file_restore",  # undo RESTORES it => the tool CHANGED it
                "pre_inline": json.dumps({"path": "/ws/ledger.csv"}),
            },
        ),
    )
    r = session_result(client.app.state.platform.engine, sid)
    assert r["files_created"] == ["report.md"]   # workspace-relative
    assert r["files_changed"] == ["ledger.csv"]


def test_a_file_created_then_edited_is_reported_once_as_created(tmp_path):
    import json

    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=({"tool": "write_file"}, {"tool": "write_file"}),
        journals=(
            {"action_id": "tool_0", "kind": "file_delete",
             "pre_inline": json.dumps({"path": "/ws/a.md"})},
            {"action_id": "tool_1", "kind": "file_restore",
             "pre_inline": json.dumps({"path": "/ws/a.md"})},
        ),
    )
    r = session_result(client.app.state.platform.engine, sid)
    assert r["files_created"] == ["a.md"]
    assert r["files_changed"] == [], "listing it twice reads as two things happening"


# --------------------------------------------------------------------------- #
# (2) Failures are reported with their own words.
# --------------------------------------------------------------------------- #
def test_failed_tools_and_their_errors_are_surfaced(tmp_path):
    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        status=SessionStatus.FAILED,
        tools=(
            {"tool": "read_file", "ok": True},
            {"tool": "shell", "ok": False, "output": "permission denied: shell"},
        ),
    )
    r = session_result(client.app.state.platform.engine, sid)
    assert r["status"] == "failed"
    assert {t["tool"] for t in r["tools_used"]} == {"read_file", "shell"}
    assert r["tools_failed"] == [{"tool": "shell", "count": 1}]
    assert r["errors"] == [{"tool": "shell", "error": "permission denied: shell"}]


def test_an_undo_row_is_not_counted_as_work_the_agent_did(tmp_path):
    """An undo is itself a ledger row; counting it would double-report the
    action it reversed."""
    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=(
            {"tool": "write_file"},
            {"tool": "write_file", "undo_of": "tool_0"},
        ),
    )
    r = session_result(client.app.state.platform.engine, sid)
    assert r["tools_used"] == [{"tool": "write_file", "count": 1}]


def test_already_reverted_actions_are_counted_separately(tmp_path):
    from datetime import datetime

    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=({"tool": "write_file", "undone_at": datetime(2026, 1, 1)},),
    )
    r = session_result(client.app.state.platform.engine, sid)
    assert r["reverted"] == 1
    assert r["revertable"] == 0, "an undone action must not be offered again"


def test_an_irreversible_action_is_never_offered_as_revertable(tmp_path):
    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=({"tool": "shell", "reversibility": "irreversible"},),
        journals=({"action_id": "tool_0", "kind": "file_restore"},),
    )
    assert session_result(client.app.state.platform.engine, sid)["revertable"] == 0


def test_an_unknown_session_is_not_found_rather_than_empty(tmp_path):
    r = session_result(_client(tmp_path).app.state.platform.engine, "nope")
    assert r["found"] is False


def test_a_poisoned_ledger_degrades_instead_of_raising(tmp_path):
    """This feeds a card and an SSE frame — it must never break a turn."""
    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=({"tool": "write_file"},),
        journals=({"action_id": "tool_0", "kind": "file_delete",
                   "pre_inline": "{not json"},),
    )
    r = session_result(client.app.state.platform.engine, sid)
    assert r["found"] is True and r["files_created"] == []


# --------------------------------------------------------------------------- #
# (3) End to end through a REAL session.
# --------------------------------------------------------------------------- #
def test_a_real_session_reports_the_file_it_really_wrote(tmp_path):
    client = _client(tmp_path)
    r = client.post("/sessions", json={"task": "write a report", "wait": True})
    assert r.status_code == 200
    sid = r.json()["id"]
    result = client.get(f"/sessions/{sid}/result").json()
    assert result["found"] is True
    assert result["status"] == "completed"
    assert result["files_created"], "the builder writes RESULT.md — it must appear"
    assert any(t["tool"] == "write_file" for t in result["tools_used"])
    assert result["revertable"] >= 1
    assert result["duration_s"] is not None


def test_the_result_endpoint_404s_for_an_unknown_session(tmp_path):
    assert _client(tmp_path).get("/sessions/nope/result").status_code == 404


# --------------------------------------------------------------------------- #
# (4) Revert the whole task.
# --------------------------------------------------------------------------- #
def test_reverting_a_session_undoes_what_it_wrote(tmp_path):
    from pathlib import Path

    client = _client(tmp_path)
    sid = client.post(
        "/sessions", json={"task": "write a report", "wait": True}
    ).json()["id"]
    before = client.get(f"/sessions/{sid}/result").json()
    written = Path(
        client.app.state.platform.engine.url.database  # type: ignore[arg-type]
    ).parent / "workspaces"
    assert before["revertable"] >= 1

    r = client.post(f"/sessions/{sid}/revert")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["considered"] >= 1
    assert len(body["reverted"]) >= 1
    assert body["skipped"] == []

    after = client.get(f"/sessions/{sid}/result").json()
    assert after["revertable"] == 0
    assert after["reverted"] >= 1
    # The file is genuinely gone from disk.
    assert not any(
        (p / "RESULT.md").exists() for p in written.glob("session_*")
    ), "revert must actually remove the created file"


def test_revert_is_newest_first(tmp_path):
    """Three edits to ONE file only replay back to the original in reverse
    order — oldest-first would restore the first pre-image and then immediately
    re-apply the second edit's, leaving the file wrong and the user unaware.

    Distinct timestamps make the order observable: whichever way each action
    resolves, the SEQUENCE the route worked through must be newest -> oldest.
    """
    from datetime import datetime

    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=(
            {"tool": "write_file", "created_at": datetime(2026, 1, 1, 10, 0)},
            {"tool": "write_file", "created_at": datetime(2026, 1, 1, 11, 0)},
            {"tool": "write_file", "created_at": datetime(2026, 1, 1, 12, 0)},
        ),
        journals=(
            {"action_id": "tool_0", "kind": "file_restore"},
            {"action_id": "tool_1", "kind": "file_restore"},
            {"action_id": "tool_2", "kind": "file_restore"},
        ),
    )
    body = client.post(f"/sessions/{sid}/revert").json()
    # Every action is attempted; each lands in exactly one bucket, and the
    # concatenated sequence preserves the order the route walked.
    seen = [x["action_id"] for x in body["reverted"]] + [
        x["action_id"] for x in body["skipped"]
    ]
    assert sorted(seen) == ["tool_0", "tool_1", "tool_2"]
    assert body["considered"] == 3
    assert seen == ["tool_2", "tool_1", "tool_0"], f"newest-first violated: {seen}"


def test_reverting_an_unknown_session_is_a_404(tmp_path):
    assert _client(tmp_path).post("/sessions/nope/revert").status_code == 404


def test_a_session_with_nothing_reversible_reverts_cleanly(tmp_path):
    client = _client(tmp_path)
    sid = _seed(
        client.app.state.platform.engine,
        tools=({"tool": "read_file", "reversibility": "readonly"},),
    )
    body = client.post(f"/sessions/{sid}/revert").json()
    assert body["considered"] == 0
    assert body["reverted"] == [] and body["skipped"] == []


# --------------------------------------------------------------------------- #
# (5) Phases: the run says where it is.
# --------------------------------------------------------------------------- #
def test_the_sink_emits_phase_frames():
    from iron_jarvis.core.streams import StreamHub

    hub = StreamHub()
    q = hub.subscribe("s1")
    sink = hub.sink("s1", "r1")
    sink.phase("planning", "working out the steps")
    frame = q.get_nowait()
    assert frame["event"] == "phase"
    assert frame["data"] == {"phase": "planning", "detail": "working out the steps"}


def test_an_empty_phase_name_emits_nothing():
    from iron_jarvis.core.streams import StreamHub

    hub = StreamHub()
    q = hub.subscribe("s1")
    hub.sink("s1", "r1").phase("")
    assert q.empty()


def test_a_real_run_announces_a_phase(tmp_path):
    """The flat loop has no planning stage and must still say what it is doing —
    a phase-less spinner is what made a working run look like a stuck one."""
    from iron_jarvis.core.streams import StreamHub

    client = _client(tmp_path)
    platform = client.app.state.platform
    seen: list[dict] = []
    real_publish = StreamHub.publish

    def spy(self, session_id, frame):
        if frame.get("event") == "phase":
            seen.append(frame["data"])
        return real_publish(self, session_id, frame)

    StreamHub.publish = spy  # type: ignore[method-assign]
    try:
        # A subscriber must exist or the hub has nobody to publish to.
        platform.streams.subscribe("probe")
        client.post("/sessions", json={"task": "write a report", "wait": True})
    finally:
        StreamHub.publish = real_publish  # type: ignore[method-assign]
    assert any(f["phase"] == "running" for f in seen), seen
