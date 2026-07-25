"""Code Lab (v1.95.0): agents' scripts are kept, browsable and re-runnable.

``run_code`` writes into the SESSION workspace, which the orchestrator deletes
when the session ends — so even ``keep=true`` scripts vanished and nothing an
agent worked out in code survived. These tests pin the durable half: the store,
the HTTP surface, the persistence hook on the tool, and the honesty rules
(a failed re-run reports its real exit code; saving is not running).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.codelab.store import MAX_OUTPUT, MAX_SOURCE, CodeArtifactStore
from iron_jarvis.core.db import open_db
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def store(tmp_path) -> CodeArtifactStore:
    return CodeArtifactStore(open_db(tmp_path / "t.db"))


# --- store -----------------------------------------------------------------


def test_save_upserts_per_session_not_per_run(store):
    """An agent iterating on one script must not bury the list in near-copies:
    same (session, name) updates in place. A DIFFERENT session is a new row, so
    provenance stays honest."""
    a = store.save("fix", "python", "print(1)", session_id="s1", exit_code=0)
    b = store.save("fix", "python", "print(2)", session_id="s1", exit_code=0)
    assert a.id == b.id
    assert b.source == "print(2)"  # latest source wins
    assert b.run_count == 2  # both runs counted

    other = store.save("fix", "python", "print(3)", session_id="s2", exit_code=0)
    assert other.id != a.id
    assert len(store.list()) == 2


def test_saving_is_not_running(store):
    """A hand-saved script has not executed; claiming '1 run' would be a lie."""
    rec = store.save("manual", "python", "print(1)", origin="manual", count_run=False)
    assert rec.run_count == 0
    assert rec.last_exit_code is None
    assert rec.last_run_at is None


def test_source_and_output_are_capped(store):
    """A runaway script must not be able to grow the DB without bound."""
    rec = store.save(
        "huge", "python", "x" * (MAX_SOURCE + 5000),
        exit_code=0, output="y" * (MAX_OUTPUT + 5000),
    )
    assert len(rec.source) < MAX_SOURCE + 200
    assert "truncated" in rec.source
    assert len(rec.last_output) < MAX_OUTPUT + 200
    assert "truncated" in rec.last_output


def test_record_run_updates_outcome_without_touching_source(store):
    rec = store.save("s", "python", "print(1)", exit_code=0, output="one")
    again = store.record_run(rec.id, 3, "boom")
    assert again.source == "print(1)"  # unchanged
    assert again.last_exit_code == 3
    assert again.run_count == 2
    assert store.record_run("nope", 0, "") is None


def test_list_is_newest_first_and_project_filterable(store):
    store.save("a", "python", "1", session_id="s1", project_id="p1")
    store.save("b", "python", "2", session_id="s2", project_id="p2")
    names = [r.name for r in store.list()]
    assert set(names) == {"a", "b"}
    assert [r.name for r in store.list(project_id="p1")] == ["a"]


def test_delete(store):
    rec = store.save("gone", "python", "1")
    assert store.delete(rec.id) is True
    assert store.delete(rec.id) is False
    assert store.get(rec.id) is None


# --- HTTP surface ----------------------------------------------------------


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def test_list_omits_source_bodies(client):
    """The media artifacts page shipped every byte of every item and froze the
    browser. The list here must stay metadata-only."""
    client.post("/code-artifacts", json={"name": "s", "source": "print('x' * 100)"})
    row = client.get("/code-artifacts").json()["artifacts"][0]
    assert "source" not in row
    assert row["size"] > 0  # size is advertised instead


def test_save_get_run_delete_roundtrip(client):
    saved = client.post(
        "/code-artifacts",
        json={"name": "hello", "language": "python", "source": "print('hi there')"},
    ).json()
    assert saved["run_count"] == 0

    got = client.get(f"/code-artifacts/{saved['id']}").json()
    assert got["source"] == "print('hi there')"

    run = client.post(f"/code-artifacts/{saved['id']}/run").json()
    assert run["ok"] is True
    assert run["exit_code"] == 0
    assert "hi there" in run["output"]
    assert run["artifact"]["run_count"] == 1

    assert client.delete(f"/code-artifacts/{saved['id']}").json()["deleted"] == saved["id"]
    assert client.get(f"/code-artifacts/{saved['id']}").status_code == 404


def test_a_failing_script_reports_its_real_exit_code(client):
    """Never dress a failure up as success — the whole project rule."""
    saved = client.post(
        "/code-artifacts",
        json={"name": "boom", "language": "python", "source": "raise SystemExit(3)"},
    ).json()
    run = client.post(f"/code-artifacts/{saved['id']}/run").json()
    assert run["ok"] is False
    assert run["exit_code"] == 3
    assert client.get(f"/code-artifacts/{saved['id']}").json()["last_exit_code"] == 3


def test_run_uses_a_durable_folder_not_a_dead_workspace(client, tmp_path):
    """The session workspace is deleted with its session; a re-run must not
    depend on it. Files a script writes persist between runs."""
    saved = client.post(
        "/code-artifacts",
        json={
            "name": "writer",
            "language": "python",
            "source": "open('out.txt','a').write('run\\n')",
        },
    ).json()
    r1 = client.post(f"/code-artifacts/{saved['id']}/run").json()
    cwd = Path(r1["cwd"])
    assert cwd.is_dir()
    assert saved["id"] in str(cwd)
    client.post(f"/code-artifacts/{saved['id']}/run")
    assert (cwd / "out.txt").read_text().count("run") == 2  # survived between runs


def test_unknown_ids_are_404_and_empty_source_is_400(client):
    assert client.get("/code-artifacts/nope").status_code == 404
    assert client.post("/code-artifacts/nope/run").status_code == 404
    assert client.delete("/code-artifacts/nope").status_code == 404
    assert client.post("/code-artifacts", json={"source": "   "}).status_code == 400


# --- the run_code hook -----------------------------------------------------


def test_run_code_persists_the_script_it_just_ran(tmp_path):
    """The actual gap being closed: running code leaves something behind."""
    from iron_jarvis.tools.runcode import RunCodeTool
    from iron_jarvis.tools.base import ToolContext

    engine = open_db(tmp_path / "t.db")
    store = CodeArtifactStore(engine)
    captured: list[tuple] = []

    def sink(name, language, code, session_id, exit_code, output, purpose=""):
        captured.append((name, language, session_id, exit_code))
        store.save(name, language, code, session_id=session_id,
                   exit_code=exit_code, output=output)

    ws = tmp_path / "ws"
    ws.mkdir()
    # RunCodeTool uses only workspace + session_id (same shape the existing
    # tool tests use); the rest of the context is irrelevant here.
    ctx = ToolContext(
        workspace=ws, session_id="sess-1", agent_run_id="run-1",
        config=None, event_bus=None, engine=engine,
    )
    result = asyncio.run(
        RunCodeTool(sink=sink).execute(
            {"language": "python", "code": "print('persisted')"}, ctx
        )
    )
    assert result.ok, result.error
    assert captured and captured[0][2] == "sess-1"
    saved = store.list()
    assert len(saved) == 1
    assert "persisted" in saved[0].source
    assert saved[0].last_exit_code == 0


def test_a_failing_sink_never_breaks_the_agent_s_run(tmp_path):
    """Bookkeeping is subordinate to the task: if persistence throws, the
    script's result must still reach the agent."""
    from iron_jarvis.tools.runcode import RunCodeTool
    from iron_jarvis.tools.base import ToolContext

    def broken_sink(*a, **kw):
        raise RuntimeError("store is down")

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )
    result = asyncio.run(
        RunCodeTool(sink=broken_sink).execute(
            {"language": "python", "code": "print('still works')"}, ctx
        )
    )
    assert result.ok
    assert "still works" in result.output


# --- the regression that started this --------------------------------------


def test_binary_artifacts_are_never_returned_as_text(client, tmp_path):
    """GET /artifacts/{name} used to decode(..., 'replace') ANY file: a 65 MB
    mp4 became a 155 MB JSON body of replacement characters that froze the
    browser laying it out. Binary must come back as content:null + a reason."""
    app_client = client
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    app_client.app.state.platform.artifacts.save("shot", png, kind="image",
                                                 filename="shot.png")
    body = app_client.get("/artifacts/shot").json()
    assert body["content"] is None
    assert "binary" in body["content_note"]
    assert body["size"] == len(png)


def test_text_artifacts_still_come_back_as_text(client):
    """The guard must not break the case the endpoint exists for."""
    client.app.state.platform.artifacts.save("notes", "hello world", filename="n.md")
    body = client.get("/artifacts/notes").json()
    assert body["content"] == "hello world"
    assert body["content_note"] == ""
