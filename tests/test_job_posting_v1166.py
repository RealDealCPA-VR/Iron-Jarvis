r"""Job-posting backend (v1.166.0, P3): origin provenance, spawn parity, /team.

Three seams, one feature — the Agents page "give an agent work" card:

* ``origin`` on session create: the caller asserts WHERE a session came from
  ("job:agents") so the recent-jobs list can filter honestly. Validated
  (strip, <=64 chars, ``[A-Za-z0-9:_\-. ]``) — an invalid tag is a 422, never
  silently laundered into the audit timeline.
* ``POST /agents/{name}/spawn`` gains SessionCreate parity (provider/model/
  project_id/allow_tools/origin) and — the important half — runs through
  ``orchestrator.run_session`` instead of a hand-rolled inline runner. The old
  runner set COMPLETED/FAILED only when the run RETURNED; a run that RAISED
  left the session stranded ACTIVE forever (spinner that never stops). Now a
  crash is finalized FAILED with the error named in the summary.
* ``GET /sessions/{id}/team``: the delegation tree, derived from AgentRun
  ``parent_id`` links (the honest record), recursed to depth 3 (the
  delegation cap). Frozen shape — the sessions page codes against it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    Session as SessionRow,
    SessionStatus,
)
from iron_jarvis.daemon.app import create_app


def _client(tmp_path, **kw):
    return TestClient(create_app(str(tmp_path)), **kw)


def _make_agent(client, name="scout", provider="", model=""):
    r = client.post(
        "/agents",
        json={
            "name": name,
            "system_prompt": "You are Scout, a focused helper. Be concise.",
            "tools": ["write_file"],
            "description": "test helper",
            "provider": provider,
            "model": model,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# (1) origin on POST /sessions — asserted provenance, validated.
# --------------------------------------------------------------------------- #
def test_origin_lands_on_the_session_and_in_the_list(tmp_path):
    with _client(tmp_path) as client:
        r = client.post(
            "/sessions", json={"task": "note", "wait": True, "origin": "job:agents"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["origin"] == "job:agents"
        sid = r.json()["id"]
        # The recent-jobs list filters on this value — it must survive the DB.
        rows = client.get("/sessions").json()["sessions"]
        mine = next(s for s in rows if s["id"] == sid)
        assert mine["origin"] == "job:agents"


def test_origin_is_stripped_and_blank_means_unattributed(tmp_path):
    with _client(tmp_path) as client:
        r = client.post(
            "/sessions", json={"task": "a", "wait": True, "origin": "  job:agents  "}
        )
        assert r.json()["origin"] == "job:agents"  # stripped, not stored raw
        r = client.post("/sessions", json={"task": "b", "wait": True, "origin": "   "})
        assert r.status_code == 200
        assert r.json()["origin"] is None  # blank = unattributed, same as omitted


def test_invalid_origin_characters_are_a_422(tmp_path):
    with _client(tmp_path) as client:
        for bad in ("job|agents", "job\nagents", "job;drop", "job\tagents"):
            r = client.post("/sessions", json={"task": "x", "origin": bad})
            assert r.status_code == 422, f"origin {bad!r} must be rejected"
        # Trailing whitespace is NOT invalid — strip runs first (contract).
        r = client.post(
            "/sessions", json={"task": "x", "wait": True, "origin": "job:agents\t"}
        )
        assert r.status_code == 200
        assert r.json()["origin"] == "job:agents"


def test_origin_length_boundary_is_64(tmp_path):
    with _client(tmp_path) as client:
        ok = "a" * 64
        r = client.post("/sessions", json={"task": "x", "wait": True, "origin": ok})
        assert r.status_code == 200
        assert r.json()["origin"] == ok  # exactly 64 passes and is not clipped
        r = client.post("/sessions", json={"task": "x", "origin": "a" * 65})
        assert r.status_code == 422


def test_origin_docstring_charset_matches_the_regex():
    """The docstring of ``_clean_origin`` is the documented contract; its
    charset must equal ``_ORIGIN_RE``'s byte-for-byte. It once drifted — the
    dropped backslash made ``_-.`` read as a character RANGE."""
    from iron_jarvis.daemon import schemas

    pattern = schemas._ORIGIN_RE.pattern
    charset = pattern[: pattern.index("{")]  # "[A-Za-z0-9:_\\-. ]"
    assert charset in (schemas._clean_origin.__doc__ or ""), (
        f"_clean_origin's docstring must quote the exact charset {charset!r}"
    )


# --------------------------------------------------------------------------- #
# (2) POST /agents/{name}/spawn — parity + orchestrator finalization.
# --------------------------------------------------------------------------- #
def test_spawn_happy_path_completes_with_summary_and_new_fields(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client)
        r = client.post(
            "/agents/scout/spawn",
            json={
                "task": "summarize the project",
                "wait": True,
                "origin": "job:agents",
                "project_id": "proj-1",
                "allow_tools": ["write_file"],
            },
        )
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["status"] == "completed"
        assert view["summary"], "a finished spawn must carry the run's result"
        assert view["origin"] == "job:agents"
        assert view["project_id"] == "proj-1"
        # The up-front tool grant reached the session row.
        import json as _json

        with session_scope(client.app.state.platform.engine) as db:
            row = db.get(SessionRow, view["id"])
            assert _json.loads(row.allow_tools_json) == ["write_file"]


def test_spawn_body_provider_wins_over_the_records_pin(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client, provider="anthropic", model="claude-opus-4-8")
        r = client.post(
            "/agents/scout/spawn",
            json={"task": "t", "wait": True, "provider": "mock", "model": "mock-1"},
        )
        assert r.status_code == 200, r.text
        # If the record's anthropic pin had won, the offline run could not have
        # completed on mock — the status discriminates, not just the field.
        assert r.json()["provider"] == "mock"
        assert r.json()["status"] == "completed"


def test_spawn_falls_back_to_the_records_pinned_provider(tmp_path, monkeypatch):
    with _client(tmp_path) as client:
        _make_agent(client, provider="anthropic", model="claude-opus-4-8")
        orch = client.app.state.orchestrator

        async def _stub(session_id, definition=None):
            return orch.get_session(session_id)  # don't actually call anthropic

        monkeypatch.setattr(orch, "run_session", _stub)
        r = client.post("/agents/scout/spawn", json={"task": "t", "wait": True})
        assert r.status_code == 200, r.text
        assert r.json()["provider"] == "anthropic"
        assert r.json()["model"] == "claude-opus-4-8"


def test_spawn_rejects_an_invalid_origin_too(tmp_path):
    with _client(tmp_path) as client:
        _make_agent(client)
        r = client.post(
            "/agents/scout/spawn", json={"task": "t", "origin": "job|agents"}
        )
        assert r.status_code == 422


def test_spawn_unknown_agent_is_still_a_404(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/agents/does-not-exist/spawn", json={"task": "t"})
        assert r.status_code == 404


def test_spawn_refuses_a_supervisor_typed_dynamic_record(tmp_path, monkeypatch):
    """Honest refusal, not silent substitution. run_session reroutes
    SUPERVISOR-typed sessions to the builtin run_supervised, which cannot
    honor a dynamic record's custom system prompt — spawning such a record
    would silently discard a user-authored prompt. Unreachable via POST
    /agents today (base_type is hardcoded 'builder'), but a directly
    registered record must be refused at the seam, before any session row
    is created."""
    with _client(tmp_path) as client:
        client.app.state.platform.agents_registry.register(
            name="boss",
            system_prompt="Custom boss prompt the builtin would discard.",
            tools=[],
            base_type="supervisor",
        )
        r = client.post("/agents/boss/spawn", json={"task": "boss job", "wait": True})
        assert r.status_code == 409, r.text
        assert "supervisor" in r.json()["detail"]
        assert "system prompt" in r.json()["detail"]  # names WHAT would be lost
        # Refused BEFORE create_session — no stranded session row.
        orch = client.app.state.orchestrator
        assert all(s.task != "boss job" for s in orch.list_sessions())

        # The guard DISCRIMINATES: the BUILTIN supervisor (no dynamic record,
        # nothing to discard) still spawns. run_session is stubbed so this
        # asserts the route's guard, not the supervised loop.
        async def _stub(session_id, definition=None):
            return orch.get_session(session_id)

        monkeypatch.setattr(orch, "run_session", _stub)
        r = client.post("/agents/supervisor/spawn", json={"task": "s", "wait": True})
        assert r.status_code == 200, r.text


def test_a_crashing_spawned_run_is_finalized_failed_not_stranded_active(
    tmp_path, monkeypatch
):
    """THE POINT of routing spawn through run_session. The old inline runner
    only wrote a status when the run RETURNED; a raising run left the session
    ACTIVE forever. Now the crash finalizes FAILED and names the error."""
    with _client(tmp_path, raise_server_exceptions=False) as client:
        _make_agent(client, name="crasher")
        orch = client.app.state.orchestrator

        async def _boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(orch.runtime, "run", _boom)
        r = client.post("/agents/crasher/spawn", json={"task": "explode", "wait": True})
        assert r.status_code == 500  # honest error, same as POST /sessions wait:true
        rows = [s for s in orch.list_sessions() if s.task == "explode"]
        assert rows, "the session row must exist"
        assert rows[0].status is SessionStatus.FAILED, (
            f"a crashed spawn must be FAILED, got {rows[0].status!r}"
        )
        assert "boom" in (rows[0].summary or "")
        # And the API says the same thing (nested shape on GET one).
        got = client.get(f"/sessions/{rows[0].id}").json()
        assert got["session"]["status"] == "failed"


# --------------------------------------------------------------------------- #
# (3) GET /sessions/{id}/team — the delegation tree (frozen shape).
# --------------------------------------------------------------------------- #
def _seed_tree(engine):
    """s-root -> s-c1 + s-c1b (depth 1) -> s-c2 (2) -> s-c3 (3) -> s-c4 (4).

    Links are AgentRun.parent_id run-ids, exactly how delegate/spawn record
    them. s-c4 sits past the depth-3 delegation cap and must NOT appear.
    """
    with session_scope(engine) as db:
        for sid in ("s-root", "s-c1", "s-c1b", "s-c2", "s-c3", "s-c4"):
            db.add(SessionRow(id=sid, task=f"task {sid}", status=SessionStatus.COMPLETED))
        for rid, sid, parent in (
            ("r-root", "s-root", None),
            ("r-c1", "s-c1", "r-root"),
            ("r-c1b", "s-c1b", "r-root"),
            ("r-c2", "s-c2", "r-c1"),
            ("r-c3", "s-c3", "r-c2"),
            ("r-c4", "s-c4", "r-c3"),  # depth 4 — beyond the cap
        ):
            db.add(
                AgentRun(
                    id=rid,
                    session_id=sid,
                    parent_id=parent,
                    agent_type=AgentType.BUILDER,
                    state=AgentState.COMPLETED,
                )
            )
        db.commit()


def test_team_unknown_session_is_found_false_with_empty_lists(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/sessions/nope/team")
        assert r.status_code == 200  # the polled page must not toast a 404
        assert r.json() == {
            "found": False,
            "session_id": "nope",
            "children": [],
            "runs": [],
        }


def test_team_derives_children_and_caps_at_depth_3(tmp_path):
    with _client(tmp_path) as client:
        _seed_tree(client.app.state.platform.engine)
        team = client.get("/sessions/s-root/team").json()
        assert team["found"] is True
        assert team["session_id"] == "s-root"

        by_id = {c["id"]: c for c in team["children"]}
        assert set(by_id) == {"s-c1", "s-c1b", "s-c2", "s-c3"}, (
            "depth 1-3 children exactly; s-c4 is past the delegation cap"
        )
        # Each child names the run in its PARENT that spawned it.
        assert by_id["s-c1"]["parent_run_id"] == "r-root"
        assert by_id["s-c1b"]["parent_run_id"] == "r-root"
        assert by_id["s-c2"]["parent_run_id"] == "r-c1"
        assert by_id["s-c3"]["parent_run_id"] == "r-c2"
        # Children are full _session_view rows, not bare ids.
        assert by_id["s-c1"]["task"] == "task s-c1"
        assert by_id["s-c1"]["status"] == "completed"
        assert "created_at" in by_id["s-c1"]

        # runs = the parent's AND every discovered child's runs — values, not
        # just presence, so a mutated row-builder cannot pass.
        assert {r["id"] for r in team["runs"]} == {
            "r-root",
            "r-c1",
            "r-c1b",
            "r-c2",
            "r-c3",
        }
        r_c2 = next(r for r in team["runs"] if r["id"] == "r-c2")
        assert r_c2 == {
            "id": "r-c2",
            "session_id": "s-c2",
            "parent_id": "r-c1",
            "agent_type": "builder",
            "state": "completed",
        }


def test_team_keeps_orphan_run_rows_whose_session_is_gone(tmp_path):
    """``runs`` carries EVERY discovered child's runs — including a run whose
    Session row was deleted, or whose session_id is blank. The run was found
    via parent_id (the honest record); it must not vanish without trace just
    because its session did. ``children`` still excludes them (there is no
    session to render)."""
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        with session_scope(engine) as db:
            db.add(
                SessionRow(id="s-root", task="task s-root", status=SessionStatus.COMPLETED)
            )
            for rid, sid, parent in (
                ("r-root", "s-root", None),
                ("r-ghost", "s-ghost", "r-root"),  # session row deleted
                ("r-blank", "", "r-root"),  # blank session_id
            ):
                db.add(
                    AgentRun(
                        id=rid,
                        session_id=sid,
                        parent_id=parent,
                        agent_type=AgentType.BUILDER,
                        state=AgentState.FAILED,
                    )
                )
            db.commit()
        team = client.get("/sessions/s-root/team").json()
        assert team["found"] is True
        assert team["children"] == []  # no Session rows to render
        # Full-value rows, not just presence — a mutated row-builder cannot pass.
        by_id = {r["id"]: r for r in team["runs"]}
        assert set(by_id) == {"r-root", "r-ghost", "r-blank"}
        assert by_id["r-ghost"] == {
            "id": "r-ghost",
            "session_id": "s-ghost",
            "parent_id": "r-root",
            "agent_type": "builder",
            "state": "failed",
        }
        assert by_id["r-blank"]["session_id"] == ""
        assert by_id["r-blank"]["parent_id"] == "r-root"


def test_team_orphan_does_not_stop_recursion_past_it(tmp_path):
    """Regression guard for the fix's mechanics: recording a linked run early
    must not empty the next frontier — siblings with live sessions still
    recurse to their own children."""
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        with session_scope(engine) as db:
            for sid in ("s-root", "s-c1", "s-c2"):
                db.add(SessionRow(id=sid, task=f"task {sid}", status=SessionStatus.COMPLETED))
            for rid, sid, parent in (
                ("r-root", "s-root", None),
                ("r-ghost", "s-ghost", "r-root"),  # orphan sibling at depth 1
                ("r-c1", "s-c1", "r-root"),
                ("r-c2", "s-c2", "r-c1"),  # depth 2, via the live sibling
            ):
                db.add(
                    AgentRun(
                        id=rid,
                        session_id=sid,
                        parent_id=parent,
                        agent_type=AgentType.BUILDER,
                        state=AgentState.COMPLETED,
                    )
                )
            db.commit()
        team = client.get("/sessions/s-root/team").json()
        assert {c["id"] for c in team["children"]} == {"s-c1", "s-c2"}
        assert {r["id"] for r in team["runs"]} == {"r-root", "r-ghost", "r-c1", "r-c2"}


def test_team_of_a_real_run_lists_its_own_runs_and_no_children(tmp_path):
    """End-to-end through the real runtime: a solo mock run has run rows but
    no delegation links, so the tree is just the trunk."""
    with _client(tmp_path) as client:
        r = client.post("/sessions", json={"task": "write a short note", "wait": True})
        sid = r.json()["id"]
        team = client.get(f"/sessions/{sid}/team").json()
        assert team["found"] is True
        assert team["children"] == []
        assert team["runs"], "the session's own AgentRun rows must be listed"
        assert all(row["session_id"] == sid for row in team["runs"])
        assert team["runs"][0]["state"] == "completed"
        assert team["runs"][0]["agent_type"] == "builder"
        assert team["runs"][0]["parent_id"] is None


def test_parked_spawn_response_says_queued_not_active(tmp_path, monkeypatch):
    """v1.167.0: at the concurrency limit, the wait:false response used to
    serialize the stale in-memory row — claiming "active" for a session the
    governor had just parked QUEUED. The response must match the DB."""
    import asyncio as _asyncio

    with _client(tmp_path) as client:
        platform = client.app.state.platform
        object.__setattr__(platform.config, "max_concurrent_sessions", 1)
        orch = client.app.state.orchestrator

        gate = _asyncio.Event()

        async def gated_run(session_id, definition=None):
            await gate.wait()
            return orch.get_session(session_id)

        monkeypatch.setattr(orch, "run_session", gated_run)

        first = client.post(
            "/sessions", json={"task": "holds the slot", "wait": False}
        ).json()
        assert first["status"] == "active"
        second = client.post(
            "/sessions", json={"task": "parks honestly", "wait": False}
        ).json()
        assert second["status"] == "queued", (
            f"a parked spawn reported {second['status']!r} — the stale-row lie"
        )
        gate.set()
