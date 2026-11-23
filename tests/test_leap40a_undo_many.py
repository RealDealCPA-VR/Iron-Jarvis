"""Next leaps 40-a (R2.18): "Undo these" puts many changes back with one press.

Twelve file writes from one run (three of them to the same file) are put
back by ONE call, NEWEST FIRST, exactly as the run revert does: a file
written three times returns to how it was before the first write. A file
changed since is skipped BY NAME and the rest still revert. Driven through
the real write tool (``registry.invoke``) and the real ``perform_undo``
(``POST /changes/undo``, the page's press).
"""

from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.routes import undo as undo_routes
from iron_jarvis.settings import records_day as RD
from tests.test_leap40a_day_changes import _app, _local_utc, _run, _stamp, _write


async def _twelve(tmp_path):
    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ledger.txt").write_text("v0\n", encoding="utf-8")
    for i in range(1, 10):
        (ws / f"f{i}.txt").write_text(f"old {i}\n", encoding="utf-8")
    run = _run(p, "Tidy the ledgers", at=_local_utc(RD.today_local(), 15, 0), ws=ws)
    ids: list[str] = []
    ids.append(await _write(p, ws, run, "ledger.txt", "v1\n"))
    for i in range(1, 10):
        ids.append(await _write(p, ws, run, f"f{i}.txt", f"new {i}\n"))
        if i in (3, 6):
            ids.append(await _write(p, ws, run, "ledger.txt", f"v{2 if i == 3 else 3}\n"))
    assert len(ids) == 12
    _stamp(p, ids, _local_utc(RD.today_local(), 15, 1))
    return app, p, ws, run, ids


@pytest.mark.asyncio
async def test_twelve_writes_go_back_with_one_press_newest_first_and_the_one_changed_since_is_named(tmp_path):
    app, p, ws, run, ids = await _twelve(tmp_path)
    (ws / "f5.txt").write_text("the user's own edit\n", encoding="utf-8")  # changed since
    shuffled = ids[:]
    random.Random(4).shuffle(shuffled)  # the press's order never decides the Undo's
    r = TestClient(app).post("/changes/undo", json={"action_ids": shuffled})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["reverted"]) == 11 and len(body["skipped"]) == 1, body
    (skip,) = body["skipped"]
    assert skip["target"] == "f5.txt" and "changed again since" in skip["reason"], skip
    assert (ws / "f5.txt").read_text(encoding="utf-8") == "the user's own edit\n"
    # Newest first: the file written three times is back to before the first.
    assert (ws / "ledger.txt").read_text(encoding="utf-8") == "v0\n"
    for i in (1, 2, 3, 4, 6, 7, 8, 9):
        assert (ws / f"f{i}.txt").read_text(encoding="utf-8") == f"old {i}\n"
    assert [x["id"] for x in body["reverted"]] == [a for a in reversed(ids) if a != skip["id"]]
    assert body["words"].startswith("11 files put back.") and "f5.txt" in body["words"]
    with session_scope(p.engine) as db:
        undone = {i.id for i in db.exec(select(ToolInvocation).where(ToolInvocation.undone_at != None))}  # noqa: E711
        undo_rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.undo_of != None)))  # noqa: E711
    assert undone == set(ids) - {skip["id"]} and len(undo_rows) == 11, "each Undo is its own ledger row"


@pytest.mark.asyncio
async def test_what_is_not_a_change_is_skipped_by_name_and_the_rest_still_go_back(tmp_path):
    app, p, ws, run, ids = await _twelve(tmp_path)
    r = TestClient(app).post("/changes/undo", json={"action_ids": [ids[1], "tool_nope"]})
    body = r.json()
    assert [x["id"] for x in body["reverted"]] == [ids[1]]
    assert body["skipped"] == [{"id": "tool_nope", "target": "", "what": "", "kind": "", "reason": "there is no such change"}]
    again = TestClient(app).post("/changes/undo", json={"action_ids": [ids[1]]}).json()
    assert again["reverted"] == [] and again["skipped"][0]["reason"] == "it was already undone"


def test_more_than_a_hundred_at_once_is_refused_in_words(tmp_path):
    app, _p = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/changes/undo", json={"action_ids": [f"tool_{i}" for i in range(101)]})
    assert r.status_code == 422 and "100" in r.json()["detail"]
    assert client.post("/changes/undo", json={"action_ids": []}).status_code == 422
    assert client.post("/changes/undo", json={"action_ids": "tool_1"}).status_code == 422


@pytest.mark.asyncio
async def test_the_shared_undo_is_the_run_reverts_own_order(tmp_path):
    """``undo_many`` sorts by when each change was made, newest first, and
    calls the one Undo (``perform_undo``) per change — never a second copy."""
    app, p, ws, run, ids = await _twelve(tmp_path)
    called: list[str] = []
    real = undo_routes.perform_undo

    async def spy(d, action_id, *, pressed=False):
        called.append(str(action_id))
        assert pressed is True, "the page's press is the consent (PG4)"
        return await real(d, action_id, pressed=pressed)

    undo_routes.perform_undo = spy
    try:
        out = await undo_routes.undo_many(app.state.d, ids[1:4], pressed=True)
    finally:
        undo_routes.perform_undo = real
    assert called == list(reversed(ids[1:4])) and len(out["reverted"]) == 3


@pytest.mark.asyncio
async def test_the_pages_press_is_the_consent_a_tool_set_to_ask_needs(tmp_path):
    """PG4: putting a file back means letting Iron Jarvis save a file. With
    saving set to ask, the page's press answers that ask (a model's undo
    would be refused)."""
    from iron_jarvis.tools.permissions import PermissionEngine

    app, p, ws, run, ids = await _twelve(tmp_path)
    p.permissions = PermissionEngine({**p.config.permissions, "write_file": "ask"})
    body = TestClient(app).post("/changes/undo", json={"action_ids": [ids[1]]}).json()
    assert [x["id"] for x in body["reverted"]] == [ids[1]], body
    assert (ws / "f1.txt").read_text(encoding="utf-8") == "old 1\n"
