"""A project's CODING SESSIONS on its page and its world (v1.414.0).

The context spine's gap: a coding session never carried a project — Chat ›
Code rows are chat threads with ``surface="build"`` and ``project_id=None``,
Build › Code rows (``hosted``) have no project field — so a project's page and
its world's Board / Waiting on you / Completed never listed the coding work
done in its own folder. ``projects/coding_sessions`` resolves each session's
FOLDER at read time with the one rule (``locate.best_project``: the most
specific ACTIVE root), from both views, read-only and bounded.

Every test drives the REAL app (``create_app``) and fails on the old code.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.hosted import store as hosted_store
from iron_jarvis.projects import coding_sessions as CS
from iron_jarvis.projects import locate


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


def _platform(client):
    return client.app.state.platform


def _folder(tmp_path: Path, *parts: str) -> Path:
    p = tmp_path.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _project(client, name: str, root: Path) -> str:
    r = client.post("/projects", json={"name": name, "root": str(root)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _chat_code(client, folder: Path, title: str, lines: list[dict] | None = None, **setup) -> str:
    """A Chat › Code session (a chat thread with surface="build")."""
    engine = _platform(client).engine
    row = S.create(engine, str(folder), title, {"provider": "mock", "model": "mock", **setup})
    if lines is not None:
        with session_scope(engine) as db:
            rec = db.get(ChatThreadRecord, row.id)
            rec.messages_json = json.dumps(lines)
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()
    return row.id


def _build_code(client, folder: Path, title: str, **fields) -> str:
    """A Build › Code session (a hosted row, written through its own store)."""
    row = hosted_store.create(_platform(client).engine, harness="claude-code", folder=str(folder),
                              title=title, **fields)
    return row.id


ANSWERED = [{"role": "user", "content": "add a test"}, {"role": "assistant", "content": "Added."}]


# --------------------------------------------------------------------------- #
# the project page
# --------------------------------------------------------------------------- #


def test_the_project_page_lists_its_coding_sessions_from_both_views(client, tmp_path):
    root = _folder(tmp_path, "acme")
    pid = _project(client, "Acme", root)
    chat_sid = _chat_code(client, _folder(tmp_path, "acme", "api"), "Fix the API", ANSWERED)
    app_sid = _build_code(client, root, "Refactor with Claude Code", harness_session_id="hs-1")
    _chat_code(client, _folder(tmp_path, "elsewhere"), "Not this project", ANSWERED)

    r = client.get(f"/projects/{pid}")
    assert r.status_code == 200, r.text
    rows = r.json()["coding_sessions"]
    by_id = {c["id"]: c for c in rows}
    assert set(by_id) == {chat_sid, app_sid}, rows
    assert by_id[chat_sid]["view"] == "chat_code" and by_id[chat_sid]["kind"] == "coding_session"
    assert by_id[chat_sid]["link"] == f"/chat?view=code&session={chat_sid}"
    assert by_id[chat_sid]["state"] == "finished" and by_id[chat_sid]["outcome"] == "completed"
    assert by_id[app_sid]["view"] == "build_code" and by_id[app_sid]["tool"] == "Claude Code"
    assert by_id[app_sid]["link"] == f"/terminals?view=code&code={app_sid}"
    # No private key reaches the answer.
    assert not any(k.startswith("_") for c in rows for k in c)


def test_the_most_specific_active_project_wins_and_an_archived_session_is_left_out(client, tmp_path):
    outer = _project(client, "Outer", _folder(tmp_path, "work"))
    inner = _project(client, "Inner", _folder(tmp_path, "work", "inner"))
    deep = _chat_code(client, _folder(tmp_path, "work", "inner", "src"), "Deep work", ANSWERED)
    top = _chat_code(client, _folder(tmp_path, "work", "other"), "Top work", ANSWERED)
    gone = _chat_code(client, _folder(tmp_path, "work", "other"), "Archived work", ANSWERED)
    with session_scope(_platform(client).engine) as db:
        rec = db.get(ChatThreadRecord, gone)
        rec.archived_at = utcnow()
        db.add(rec)
        db.commit()
    archived_app = _build_code(client, _folder(tmp_path, "work"), "Old Codex")
    hosted_store.update(_platform(client).engine, archived_app, archived_at=utcnow())

    ids_outer = [c["id"] for c in client.get(f"/projects/{outer}").json()["coding_sessions"]]
    ids_inner = [c["id"] for c in client.get(f"/projects/{inner}").json()["coding_sessions"]]
    assert ids_outer == [top] and ids_inner == [deep]
    # The rule is the ONE locate rule: the same answer as project_for_path.
    engine = _platform(client).engine
    assert locate.project_for_path(engine, str(tmp_path / "work" / "inner" / "src")).id == inner


def test_a_session_with_no_folder_chosen_belongs_to_no_project(client, tmp_path):
    root = _folder(tmp_path, "acme")
    pid = _project(client, "Acme", root)
    sid = _chat_code(client, root, "Folderless", ANSWERED)
    # Its own folder happens to be under the project — still no project: it
    # was started with no folder (v1.379.0), its group is "No folder".
    S.update(_platform(client).engine, sid, setup={S.NO_FOLDER_KEY: True})
    assert client.get(f"/projects/{pid}").json()["coding_sessions"] == []


def test_a_project_with_no_coding_sessions_answers_an_empty_list(client, tmp_path):
    pid = _project(client, "Acme", _folder(tmp_path, "acme"))
    assert client.get(f"/projects/{pid}").json()["coding_sessions"] == []


def test_reading_never_creates_the_build_code_table(tmp_path):
    """Read-only: an install that never opened Build › Code has no hosted
    table, and listing a project must not make one (the store's own
    list_rows would — a write)."""
    home = tmp_path / "home"
    client = TestClient(create_app(str(home)))
    engine = _platform(client).engine
    with engine.begin() as con:
        con.exec_driver_sql("DROP TABLE IF EXISTS hostedsessionrecord")
    pid = _project(client, "Acme", _folder(tmp_path, "acme"))
    sid = _chat_code(client, tmp_path / "acme", "Chat code only", ANSWERED)
    assert [c["id"] for c in client.get(f"/projects/{pid}").json()["coding_sessions"]] == [sid]
    with engine.connect() as con:
        names = {r[0] for r in con.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hostedsessionrecord" not in names


def test_the_read_is_bounded_and_reads_no_transcript(client, tmp_path, monkeypatch):
    """The newest SCAN_MAX of each view; the transcript column is never
    selected (one JSON read of the last line's role in SQL)."""
    pid = _project(client, "Acme", _folder(tmp_path, "acme"))
    for i in range(5):
        _chat_code(client, tmp_path / "acme", f"s{i}", ANSWERED)
    monkeypatch.setattr(CS, "SCAN_MAX", 3)
    seen: list[str] = []
    engine = _platform(client).engine
    from sqlalchemy import event

    def before(conn, cursor, statement, params, context, executemany):
        if "chatthreadrecord" in statement.lower():
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        rows = client.get(f"/projects/{pid}").json()["coding_sessions"]
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert len(rows) == 3
    coding = [s for s in seen if "json_extract" in s]
    assert coding, seen
    # Only the last line is read: messages_json appears only inside json_*()
    # — never selected whole.
    import re

    for stmt in coding:
        rest = re.sub(r"json_\w+\(\s*chatthreadrecord\.messages_json", "", stmt)
        assert "messages_json" not in rest, stmt


# --------------------------------------------------------------------------- #
# the world: Board, Waiting on you, Completed
# --------------------------------------------------------------------------- #


def test_the_world_lists_waiting_finished_and_working_coding_sessions(client, tmp_path, monkeypatch):
    root = _folder(tmp_path, "acme")
    pid = _project(client, "Acme", root)
    platform = _platform(client)
    waiting_chat = _chat_code(client, root, "Parked on a card", [ANSWERED[0]])
    done_chat = _chat_code(client, root, "Finished reply", ANSWERED)
    failed_chat = _chat_code(client, root, "Failed reply",
                             [ANSWERED[0], {"role": "assistant", "content": "", "errorTitle": "It broke"}])
    idle_chat = _chat_code(client, root, "No reply yet", [ANSWERED[0]])
    waiting_app = _build_code(client, root, "Codex asks", harness_session_id="hs-a")
    working_app = _build_code(client, root, "Codex works", harness_session_id="hs-b")
    done_app = _build_code(client, root, "Codex done", harness_session_id="hs-c")
    fresh_app = _build_code(client, root, "Codex never spoke")

    from iron_jarvis.core.turns import TURNS

    entry = TURNS.new_thread_turn(waiting_chat)
    TURNS.enter_thread(entry)
    entry.park()
    prev = getattr(platform, "hosted", None)
    platform.hosted = SimpleNamespace(live={
        waiting_app: SimpleNamespace(turn=SimpleNamespace(running=True), asks={"r1": object()}),
        working_app: SimpleNamespace(turn=SimpleNamespace(running=True), asks={}),
    })
    try:
        world = client.get(f"/projects/{pid}/world").json()
    finally:
        TURNS.leave_thread(entry)
        platform.hosted = prev

    states = {c["id"]: c["state"] for c in world["coding_sessions"]}
    assert states == {
        waiting_chat: "waiting", done_chat: "finished", failed_chat: "finished",
        idle_chat: "idle", waiting_app: "waiting", working_app: "working",
        done_app: "finished", fresh_app: "idle",
    }
    waiting = {w["id"]: w for w in world["waiting"] if w["kind"] == "coding_session"}
    assert set(waiting) == {waiting_chat, waiting_app}
    assert waiting[waiting_chat]["link"] == f"/chat?view=code&session={waiting_chat}"
    assert waiting[waiting_app]["link"] == f"/terminals?view=code&code={waiting_app}"
    completed = {c["id"]: c for c in world["completed"] if c.get("kind") == "coding_session"}
    assert set(completed) == {done_chat, failed_chat, done_app}
    assert completed[failed_chat]["outcome"] == "failed"
    assert completed[done_chat]["outcome"] == "completed"
    assert all(isinstance(c["finished_at"], str) for c in completed.values())


def test_the_world_read_runs_off_the_event_loop(client, tmp_path, monkeypatch):
    pid = _project(client, "Acme", _folder(tmp_path, "acme"))
    seen = {}
    real = CS.read_rows

    def spy(engine, **kw):
        try:
            asyncio.get_running_loop()
            seen["loop"] = True
        except RuntimeError:
            seen["loop"] = False
        return real(engine, **kw)

    monkeypatch.setattr(CS, "read_rows", spy)
    assert client.get(f"/projects/{pid}/world").status_code == 200
    assert seen == {"loop": False}


def test_links_are_the_dashboards_own_addresses():
    """Pinned to the exact strings lib/buildLinks.buildSessionHref and
    lib/buildApp.buildCodeHref produce (the dashboard test pins the same)."""
    assert CS.link_for(CS.VIEW_CHAT_CODE, "chat_1") == "/chat?view=code&session=chat_1"
    assert CS.link_for(CS.VIEW_BUILD_CODE, "app_1") == "/terminals?view=code&code=app_1"
    assert CS.link_for(CS.VIEW_CHAT_CODE, "a b/c") == "/chat?view=code&session=a%20b%2Fc"


def test_an_old_database_without_the_build_code_table_still_answers_the_world(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    engine = _platform(client).engine
    with engine.begin() as con:
        con.exec_driver_sql("DROP TABLE IF EXISTS hostedsessionrecord")
    pid = _project(client, "Acme", _folder(tmp_path, "acme"))
    r = client.get(f"/projects/{pid}/world")
    assert r.status_code == 200 and r.json()["coding_sessions"] == []


def test_a_store_that_cannot_answer_lists_none_and_never_fails_the_page(client, tmp_path, monkeypatch):
    pid = _project(client, "Acme", _folder(tmp_path, "acme"))

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(CS, "read_rows", boom)
    assert client.get(f"/projects/{pid}").json()["coding_sessions"] == []
    assert client.get(f"/projects/{pid}/world").status_code == 200


def test_build_code_live_state_is_only_read_never_created(client):
    """No hosted service → nothing live, and none is made by asking."""
    platform = _platform(client)
    assert getattr(platform, "hosted", None) is None
    assert CS.build_code_states(platform) == {}
    assert getattr(platform, "hosted", None) is None
