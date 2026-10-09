"""v1.327.0 (calm chat BW2-b): two follow-ups to archiving a chat.

1. The "@ another chat" menu (``GET /chat/threads/search-refs``,
   ``chat_refs.search_thread_refs``) does not OFFER an archived chat: the user
   put it away and the chat list hides it too. The filter is in the query, so
   archived chats never take the menu's eight slots. An id the user already
   picked is still READ when the turn resolves it (both lanes), archived or
   not.
2. A message FROM the person on an archived phone (daemon-owned) chat
   (``CommThreadStore.append(..., "user", ...)``) clears ``archived_at`` in the
   SAME transaction as the append, so the chat comes back into the list and
   the message is not missed. Jarvis's own lines, and user-owned chats, leave
   the archive alone. Never raises.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_refs import (
    THREAD_REFS_HEADING,
    THREAD_REFS_SEARCH_LIMIT,
    search_thread_refs,
)
from tests.test_chat_thread_refs_v1326 import BUDGET_MSGS
from tests.test_chat_thread_refs_v1326 import lanes  # noqa: F401 — pytest fixture

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _seed(engine, title: str, *, hours: int = 0, archived: bool = False,
          owner: str = "user") -> str:
    rec = ChatThreadRecord(
        title=title,
        messages_json=json.dumps([{"role": "user", "content": "hello"}]),
        updated_at=BASE + timedelta(hours=hours),
        owner=owner,
    )
    if archived:
        rec.archived_at = BASE + timedelta(days=2)
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _row(engine, tid: str) -> ChatThreadRecord:
    with session_scope(engine) as db:
        return db.get(ChatThreadRecord, tid)


def _menu(client, **params) -> list[str]:
    r = client.get("/chat/threads/search-refs", params=params)
    assert r.status_code == 200, r.text
    return [x["title"] for x in r.json()["threads"]]


def _listed(client) -> list[str]:
    r = client.get("/chat/threads")
    assert r.status_code == 200, r.text
    return [t["id"] for t in r.json()["threads"]]


# --------------------------------------------------------------------------- #
# 1. The "@" menu leaves archived chats out
# --------------------------------------------------------------------------- #


def test_the_at_menu_does_not_offer_an_archived_chat(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    engine = app.state.platform.engine
    _seed(engine, "Budget live", hours=1)
    gone = _seed(engine, "Budget put away", hours=2)

    assert _menu(client) == ["Budget put away", "Budget live"]

    # Archived through the REAL route, as the user does it.
    assert client.post(f"/chat/threads/{gone}/archive", json={}).status_code == 200
    assert _menu(client) == ["Budget live"], "blank search offered an archived chat"
    assert _menu(client, q="budget") == ["Budget live"], "a title match offered an archived chat"
    assert _menu(client, q="put away") == [], "an exact title match offered an archived chat"

    # Unarchived, it is offered again.
    assert client.post(f"/chat/threads/{gone}/unarchive").status_code == 200
    assert _menu(client, q="put away") == ["Budget put away"]


def test_archived_chats_never_take_the_menus_slots(tmp_path):
    """The filter is in the QUERY: eight newer archived chats must not push
    the older live ones out of the eight-row menu."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    for i in range(THREAD_REFS_SEARCH_LIMIT):
        _seed(engine, f"Archived {i}", hours=100 + i, archived=True)
    _seed(engine, "Live older", hours=1)
    _seed(engine, "Live newer", hours=2)

    rows = search_thread_refs(engine)
    assert [r["title"] for r in rows] == ["Live newer", "Live older"]


def test_the_current_chat_is_still_left_out_beside_archived_ones(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    here = _seed(engine, "Here", hours=3)
    _seed(engine, "Away", hours=2, archived=True)
    _seed(engine, "There", hours=1)
    assert [r["title"] for r in search_thread_refs(engine, exclude=here)] == ["There"]


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_a_picked_archived_chat_is_still_read(lanes, lane):  # noqa: F811
    """Search does not offer it, but an id the user already picked (sent on
    ``thread_refs``) is resolved and injected like any other."""
    tid = lanes.seed("Bakery budget", BUDGET_MSGS)
    with session_scope(lanes.engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        rec.archived_at = utcnow()
        db.add(rec)
        db.commit()
    assert search_thread_refs(lanes.engine) == []

    system, receipt = await getattr(lanes, lane)([tid])
    assert THREAD_REFS_HEADING in system, f"{lane}: an archived pick was not read"
    assert BUDGET_MSGS[1]["content"] in system
    (row,) = receipt["thread_refs"]
    assert row["id"] == tid and row["ok"] is True and row["note"] == ""


# --------------------------------------------------------------------------- #
# 2. A phone message brings an archived chat back
# --------------------------------------------------------------------------- #


def test_a_phone_message_brings_an_archived_chat_back_into_the_list(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    engine = app.state.platform.engine
    store = CommThreadStore(engine)
    thread = store.resolve("telegram", "4242", "Val")

    assert client.post(f"/chat/threads/{thread.id}/archive", json={}).status_code == 200
    assert thread.id not in _listed(client)

    count = store.append(thread.id, "user", "are you there?")

    assert count == 1
    row = _row(engine, thread.id)
    assert row.archived_at is None, "the phone message left the chat archived"
    assert json.loads(row.messages_json)[-1]["content"] == "are you there?"
    assert thread.id in _listed(client), "the chat did not come back into the list"
    flags = {t["id"]: t["archived"] for t in client.get("/chat/threads").json()["threads"]}
    assert flags[thread.id] is False


def test_jarvis_own_line_leaves_an_archived_phone_chat_archived(tmp_path):
    """A reply, a job summary or a remote agent's line landing after the user
    archived the chat must not undo the archive."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    store = CommThreadStore(engine)
    tid = _seed(engine, "Telegram · Val", owner="daemon", archived=True)
    stamp = _row(engine, tid).archived_at

    store.append(tid, "assistant", "here is the summary")
    store.append(tid, "assistant", "builder: done", extra={"panelWho": "remote:builder"})

    row = _row(engine, tid)
    assert row.archived_at == stamp
    assert [m["content"] for m in json.loads(row.messages_json)][-2:] == [
        "here is the summary", "builder: done",
    ]


def test_a_user_owned_chat_is_not_unarchived_by_the_store(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = _seed(engine, "desktop chat", owner="user", archived=True)
    CommThreadStore(engine).append(tid, "user", "hi")
    assert _row(engine, tid).archived_at is not None


def test_a_live_phone_chat_is_left_as_it_is(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    store = CommThreadStore(engine)
    thread = store.resolve("telegram", "7", "Ana")
    store.append(thread.id, "user", "hi")
    assert _row(engine, thread.id).archived_at is None


def test_the_unarchive_commits_with_the_append_or_not_at_all(tmp_path, monkeypatch):
    """Same transaction: when the append's commit does not happen, neither
    the message nor the unarchive lands."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    store = CommThreadStore(engine)
    tid = _seed(engine, "Telegram · Val", owner="daemon", archived=True)
    before = _row(engine, tid)

    def boom(self, db, record, msgs):
        # Inside the append's session, after the unarchive was set and
        # before the commit: the unarchive must already be on THIS record.
        assert record.archived_at is None, "the unarchive was not part of the append"
        raise RuntimeError("commit never reached")

    monkeypatch.setattr(CommThreadStore, "_index_thread", boom)
    with pytest.raises(RuntimeError):
        store.append(tid, "user", "lost")

    after = _row(engine, tid)
    assert after.archived_at == before.archived_at, "unarchived without its message"
    assert after.messages_json == before.messages_json


def test_reviving_never_raises():
    class Odd:
        id = "x"
        owner = "daemon"

        @property
        def archived_at(self):
            raise RuntimeError("unreadable")

    CommThreadStore._revive_if_archived(Odd(), "user")  # must not raise
