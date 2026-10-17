"""``daemon/chat_append.append_to_chat_thread`` — the ONE server-side writer
for chat lines that arrive while nobody is looking (v1.345.0, S1).

Each rule of the module doc is pinned here on a real engine, and the one that
matters most — a page holding a STALE stamp 409s on its next PUT after an
append — is driven through the real app's PUT route, because that 409 is the
signal the page re-reads on (the v1.232.0 two-window rule).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon import chat_append
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_append import LINE_KEYS, MAX_MESSAGES, append_to_chat_thread


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


def _engine(app):
    return app.state.platform.engine


def _thread(engine, messages=None, **cols) -> str:
    rec = ChatThreadRecord(title="t", messages_json=json.dumps(messages or []), **cols)
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _row(engine, thread_id):
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, thread_id)
        assert r is not None
        return {
            "messages": json.loads(r.messages_json or "[]"),
            "updated_at": r.updated_at,
            "archived_at": getattr(r, "archived_at", None),
        }


# --------------------------------------------------------------------------- #
# Shape: whitelist, stamp, tail
# --------------------------------------------------------------------------- #
def test_a_line_is_whitelisted_and_stamped_like_the_put_route(app):
    engine = _engine(app)
    tid = _thread(engine, [{"role": "user", "content": "@hermes research this", "at": "x"}])
    out = append_to_chat_thread(
        engine,
        tid,
        [
            {
                "role": "assistant",
                "content": "here is the research",
                "panelWho": "remote:hermes",
                "panelThreadId": "athr_1",
                "panelKind": "done",
                "documents": ["C:/Users/VR/remote-inbox/hermes/brief.pdf"],
                "sessionId": "sess_1",
                "agentType": "builder",
                "receipt": {"provider": "mock"},
                "remembered": ["keep it short"],
                "at": "2001-01-01T00:00:00+00:00",  # a client stamp never wins here
                "fabricated": "dropped",
                "who": "dropped too",
            }
        ],
    )
    assert out["ok"] is True and out["why"] == "" and out["appended"] == 1, out
    msgs = _row(engine, tid)["messages"]
    assert len(msgs) == 2
    line = msgs[-1]
    assert "fabricated" not in line and "who" not in line, line
    for key in (
        "panelWho",
        "panelThreadId",
        "panelKind",
        "documents",
        "sessionId",
        "agentType",
        "receipt",
        "remembered",
    ):
        assert key in line, key
    assert line["content"] == "here is the research"
    # Stamped NOW by the server, aware ISO with +00:00 — the PUT route's format.
    assert line["at"] != "2001-01-01T00:00:00+00:00"
    assert line["at"].endswith("+00:00")
    parsed = datetime.fromisoformat(line["at"])
    assert parsed.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60
    # the earlier line is untouched (its own at kept)
    assert msgs[0]["at"] == "x"


def test_the_whitelist_is_the_pages_own_message_shape():
    """Every key the page's sendPanel / mirrorRoomInbound / finalize write
    is admitted; a key none of them writes is not."""
    for key in (
        "role", "content", "panelWho", "panelThreadId", "panelKind", "panelError",
        "panelNote", "panelAt", "documents", "fromSession", "runResult",
        "sessionId", "agentType", "receipt", "remembered",
    ):
        assert key in LINE_KEYS, key
    assert "who" not in LINE_KEYS and "at" not in LINE_KEYS


def test_a_line_without_a_role_or_a_non_dict_is_dropped(app):
    engine = _engine(app)
    tid = _thread(engine)
    before = _row(engine, tid)["updated_at"]
    out = append_to_chat_thread(engine, tid, [{"content": "no role"}, "nope", None])
    assert out["ok"] is True and out["appended"] == 0
    row = _row(engine, tid)
    assert row["messages"] == []
    assert row["updated_at"] == before, "a no-op never moves the stamp"


def test_the_tail_is_capped_like_the_put_route(app):
    engine = _engine(app)
    tid = _thread(engine, [{"role": "user", "content": f"m{i}"} for i in range(MAX_MESSAGES)])
    out = append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "last"}])
    assert out["ok"] and out["appended"] == 1
    msgs = _row(engine, tid)["messages"]
    assert len(msgs) == MAX_MESSAGES
    assert msgs[-1]["content"] == "last" and msgs[0]["content"] == "m1"


# --------------------------------------------------------------------------- #
# Idempotent by session
# --------------------------------------------------------------------------- #
def test_a_session_that_already_has_a_line_is_not_appended_twice(app):
    engine = _engine(app)
    tid = _thread(engine)
    first = append_to_chat_thread(
        engine, tid, [{"role": "assistant", "content": "done", "sessionId": "sess_9"}]
    )
    assert first["ok"] and first["appended"] == 1
    again = append_to_chat_thread(
        engine, tid, [{"role": "assistant", "content": "done again", "sessionId": "sess_9"}]
    )
    assert again["ok"] is True and again["appended"] == 0 and again["skipped"] == 1, again
    assert again["updated_at"] == first["updated_at"], "a skip never moves the stamp"
    msgs = _row(engine, tid)["messages"]
    assert [m["content"] for m in msgs] == ["done"]
    # a line with no session id is never deduped (two plain replies are two lines)
    append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "x"}])
    append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "x"}])
    assert len(_row(engine, tid)["messages"]) == 3


def test_the_pages_own_fromsession_key_counts_as_the_same_session(app):
    """A page that still composes its reply (``fromSession``) and a daemon
    that appended (``sessionId``) must not make two replies for one run."""
    engine = _engine(app)
    tid = _thread(engine, [{"role": "assistant", "content": "composed", "fromSession": "sess_2"}])
    out = append_to_chat_thread(
        engine, tid, [{"role": "assistant", "content": "appended", "sessionId": "sess_2"}]
    )
    assert out["ok"] and out["appended"] == 0 and out["skipped"] == 1
    assert [m["content"] for m in _row(engine, tid)["messages"]] == ["composed"]
    # and within ONE call two lines for one session land once
    out = append_to_chat_thread(
        engine,
        tid,
        [
            {"role": "assistant", "content": "a", "sessionId": "sess_3"},
            {"role": "assistant", "content": "b", "fromSession": "sess_3"},
        ],
    )
    assert out["appended"] == 1 and out["skipped"] == 1


# --------------------------------------------------------------------------- #
# Stamp, un-archive, the stale page's 409
# --------------------------------------------------------------------------- #
def test_updated_at_moves_even_inside_one_clock_tick(app, monkeypatch):
    engine = _engine(app)
    tid = _thread(engine)
    frozen = _row(engine, tid)["updated_at"]
    assert frozen is not None
    # The clock did not move since the row was made (Windows ticks at 15.6 ms;
    # here it is forced): the bump must still be strictly later.
    monkeypatch.setattr(
        chat_append, "utcnow", lambda: frozen.replace(tzinfo=timezone.utc)
    )
    out = append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "x"}])
    assert out["ok"]
    after = _row(engine, tid)["updated_at"]
    assert after > frozen, (after, frozen)
    assert out["updated_at"] == after.isoformat()


def test_a_reply_un_archives_the_chat(app):
    engine = _engine(app)
    tid = _thread(engine, archived_at=datetime(2026, 1, 1))
    assert _row(engine, tid)["archived_at"] is not None
    out = append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "back"}])
    assert out["ok"]
    assert _row(engine, tid)["archived_at"] is None


def test_a_stale_page_put_409s_after_an_append_and_the_new_stamp_passes(app):
    """THE signal the page re-reads on: its ``if_updated_at`` is older than the
    append's stamp → the PUT route refuses the whole-array rewrite; the stamp
    the append answered is accepted as the next ``if_updated_at``."""
    client = TestClient(app)
    made = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "find me a place to eat"}]},
    ).json()
    tid, stale = made["id"], made["updated_at"]
    out = append_to_chat_thread(
        _engine(app), tid, [{"role": "assistant", "content": "the research", "panelWho": "remote:h"}]
    )
    assert out["ok"] and out["updated_at"] and out["updated_at"] != stale
    # the page, still holding the pre-append stamp, tries to save its own copy
    r = client.put(
        f"/chat/threads/{tid}",
        json={
            "messages": [{"role": "user", "content": "find me a place to eat"}],
            "if_updated_at": stale,
        },
    )
    assert r.status_code == 409, r.text
    # nothing was overwritten
    got = client.get(f"/chat/threads/{tid}").json()
    assert [m["content"] for m in got["messages"]] == ["find me a place to eat", "the research"]
    assert got["updated_at"] == out["updated_at"], "GET and the append agree on the stamp"
    # a page that ADOPTED the append's stamp saves fine
    r = client.put(
        f"/chat/threads/{tid}",
        json={"messages": got["messages"] + [{"role": "user", "content": "thanks"}],
              "if_updated_at": out["updated_at"]},
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# The event, the misses, never raising
# --------------------------------------------------------------------------- #
def test_chat_thread_updated_is_published_from_a_worker_and_from_the_loop(app):
    engine = _engine(app)
    tid = _thread(engine)
    bus = EventBus()
    seen: list = []
    bus.add_handler(lambda e: seen.append(e))
    # off any loop (a worker thread / a sync route)
    out = append_to_chat_thread(
        engine, tid, [{"role": "assistant", "content": "a", "panelWho": "remote:hermes"}],
        event_bus=bus,
    )
    assert out["ok"]
    assert [e.type for e in seen] == [EventType.CHAT_THREAD_UPDATED]
    assert seen[0].payload == {"thread_id": tid, "who": "remote:hermes", "entries": 1}
    # on the loop: scheduled as a task, delivered once the loop turns
    seen.clear()

    async def on_loop():
        res = append_to_chat_thread(
            engine, tid, [{"role": "assistant", "content": "b"}], event_bus=bus, who="builtin:x"
        )
        await asyncio.sleep(0.05)
        return res

    res = asyncio.run(on_loop())
    assert res["ok"]
    assert len(seen) == 1 and seen[0].payload == {"thread_id": tid, "who": "builtin:x", "entries": 2}
    # without a bus: nothing published, nothing raised
    assert append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "c"}])["ok"]
    assert len(seen) == 1
    # a no-op append publishes nothing
    seen.clear()
    assert append_to_chat_thread(engine, tid, [], event_bus=bus)["ok"]
    assert seen == []


def test_a_missing_thread_is_said_not_raised(app):
    out = append_to_chat_thread(_engine(app), "chat_nope", [{"role": "assistant", "content": "x"}])
    assert out == {"ok": False, "updated_at": None, "why": "no such chat", "appended": 0, "skipped": 0}
    assert append_to_chat_thread(_engine(app), "", [{"role": "assistant", "content": "x"}])["why"] == "no such chat"


def test_a_daemon_owned_phone_thread_is_refused(app):
    """The comm store is the phone thread's one writer; this door must not
    become a second one."""
    engine = _engine(app)
    tid = _thread(engine, owner="daemon", comm_channel="telegram")
    out = append_to_chat_thread(engine, tid, [{"role": "assistant", "content": "x"}])
    assert out["ok"] is False and "messaging" in out["why"]
    assert _row(engine, tid)["messages"] == []


def test_it_never_raises_on_a_broken_engine(app):
    class _Broken:
        def connect(self, *a, **k):
            raise RuntimeError("db gone")

        def raw_connection(self, *a, **k):
            raise RuntimeError("db gone")

    out = append_to_chat_thread(_Broken(), "chat_x", [{"role": "assistant", "content": "x"}])
    assert out["ok"] is False and out["why"].startswith("could not append")
