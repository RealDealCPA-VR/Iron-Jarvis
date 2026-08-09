"""A file an AGENT made is reachable, not just described (v1.155.0).

Reported after a working redaction: the run said it created the file, the tool
strip listed it — and there was nothing to click. Files only ever reached the
right-rail preview from the CHAT lane (``made_docs`` in ``chat_turn``), and the
work that actually produces files happens in an escalated agent session.

``session_result`` already knew which files a run created, from the undo
journal rather than the model's prose. It reported them workspace-RELATIVE,
which reads well in the result card and cannot be opened: an agent session's
workspace is a folder no user would guess. The ``documents`` key adds the same
files as absolute paths — the same rule the tools themselves adopted in
v1.153.2, and the same key the chat preview already consumes, so the client
handles both lanes with one code path.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _run(client: TestClient, task: str = "write a report") -> str:
    r = client.post("/sessions", json={"task": task, "wait": True})
    assert r.status_code == 200
    return r.json()["id"]


def test_the_result_reports_created_files_as_absolute_paths(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    sid = _run(client)
    result = client.get(f"/sessions/{sid}/result").json()

    assert result["files_created"], "the mock run wrote nothing to report"
    assert result["documents"], "no absolute paths for a run that created files"
    for path in result["documents"]:
        assert Path(path).is_absolute(), f"{path} is not absolute"
        assert Path(path).is_file(), f"{path} does not exist on disk"


def test_the_absolute_paths_match_the_relative_ones(tmp_path):
    """Two views of the SAME files — not a second, differently-derived list."""
    client = TestClient(create_app(str(tmp_path)))
    sid = _run(client)
    result = client.get(f"/sessions/{sid}/result").json()

    rel_names = {Path(p).name for p in result["files_created"] + result["files_changed"]}
    abs_names = {Path(p).name for p in result["documents"]}
    assert abs_names == rel_names


def test_a_run_that_created_nothing_reports_no_documents(tmp_path):
    """The key is a signal. A run with no files must not imply one."""
    from sqlmodel import Session, select

    from iron_jarvis.core.models import UndoJournal

    client = TestClient(create_app(str(tmp_path)))
    sid = _run(client)
    # Strip the journal so the session provably created nothing.
    with Session(client.app.state.platform.engine) as db:
        for row in db.exec(select(UndoJournal)).all():
            db.delete(row)
        db.commit()

    result = client.get(f"/sessions/{sid}/result").json()
    assert result["files_created"] == []
    assert result["documents"] == []


def test_the_relative_lists_are_unchanged(tmp_path):
    """The result card renders the relative names; adding absolutes must not
    have quietly changed what it shows."""
    client = TestClient(create_app(str(tmp_path)))
    sid = _run(client)
    result = client.get(f"/sessions/{sid}/result").json()
    for path in result["files_created"]:
        assert not Path(path).is_absolute(), "the readable list went absolute"


def test_an_unknown_session_still_404s(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/sessions/session_nope/result").status_code == 404


def test_the_chat_page_opens_the_preview_for_an_agent_run():
    """Pins the WIRING. The daemon can hand over perfect paths and the user
    still has nothing to click if the client ignores them — which is exactly
    the state this release found."""
    src = (
        Path(__file__).resolve().parents[1]
        / "dashboard" / "app" / "chat" / "page.tsx"
    ).read_text(encoding="utf-8")
    assert "runResult?.documents?.length" in src, (
        "the chat page never reads the agent run's documents"
    )
    assert "showDocPreview(runResult.documents)" in src, (
        "the documents are read but never previewed"
    )


def test_the_client_type_declares_the_field():
    src = (
        Path(__file__).resolve().parents[1]
        / "dashboard" / "components" / "chat" / "RunResultCard.tsx"
    ).read_text(encoding="utf-8")
    assert "documents?: string[]" in src


def test_documents_survive_a_json_round_trip(tmp_path):
    """Windows paths carry backslashes; a client reads this over JSON."""
    client = TestClient(create_app(str(tmp_path)))
    sid = _run(client)
    raw = client.get(f"/sessions/{sid}/result").text
    parsed = json.loads(raw)
    for path in parsed["documents"]:
        assert Path(path).is_file()
