"""Thread save versioning (CL3, audit Wave 6, v1.232.0).

Converted from ``tests/_audit_20260904/test_chat_persistence_audit.py``, which
asserted the bug: two windows on one thread were last-writer-wins, GET
returned no version stamp, and PUT compared nothing. Now:

* ``GET /chat/threads/{id}`` returns ``updated_at``;
* ``PUT`` accepts an optional ``if_updated_at`` and answers 409 — writing
  NOTHING — when the row is newer than the stamp;
* a matching stamp saves and returns the new ``updated_at``;
* no stamp keeps the old unconditional contract (older clients);
* a stamp that is not a timestamp is a 400, not a silent unconditional write.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _msgs(n: int) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"u{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def test_stale_window_is_refused_and_writes_nothing(tmp_path):
    """Tab A and tab B both opened the thread at 2 exchanges. A adds a turn.
    B (still holding the 2-exchange copy) autosaves with the stamp it read
    -> 409, and A's turn is still on disk."""
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        r = c.put("/chat/threads/new", json={"messages": _msgs(2)})
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        assert r.json()["updated_at"], "PUT returns the stamp to hand back"
        stale = c.get(f"/chat/threads/{tid}").json()
        assert stale["updated_at"] == r.json()["updated_at"]

        # Tab A: a new turn lands (4 messages -> 6).
        r = c.put(
            f"/chat/threads/{tid}",
            json={"messages": _msgs(3), "if_updated_at": stale["updated_at"]},
        )
        assert r.status_code == 200, r.text
        newer = r.json()["updated_at"]
        assert newer > stale["updated_at"]

        # Tab B: autosave from its stale copy, carrying the stamp it read.
        r = c.put(
            f"/chat/threads/{tid}",
            json={"messages": stale["messages"], "if_updated_at": stale["updated_at"]},
        )
        assert r.status_code == 409, r.text
        assert "another window" in r.json()["detail"]
        now = c.get(f"/chat/threads/{tid}").json()
        assert len(now["messages"]) == 6, "tab A's turn survived"
        assert any(m["content"] == "a2" for m in now["messages"])
        assert now["updated_at"] == newer, "the refused write touched nothing"

        # Tab B refetches, appends its own bubble onto the server array, and
        # saves under the fresh stamp — the client-side merge, end to end.
        merged = now["messages"] + [{"role": "user", "content": "from B"}]
        r = c.put(
            f"/chat/threads/{tid}",
            json={"messages": merged, "if_updated_at": now["updated_at"]},
        )
        assert r.status_code == 200, r.text
        final = c.get(f"/chat/threads/{tid}").json()["messages"]
        assert [m["content"] for m in final][-3:] == ["u2", "a2", "from B"]


def test_no_stamp_keeps_the_unconditional_contract(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        tid = c.put("/chat/threads/new", json={"messages": _msgs(2)}).json()["id"]
        c.put(f"/chat/threads/{tid}", json={"messages": _msgs(3)})
        # An older client sends no stamp: the write goes through as before.
        r = c.put(f"/chat/threads/{tid}", json={"messages": _msgs(1)})
        assert r.status_code == 200, r.text
        assert len(c.get(f"/chat/threads/{tid}").json()["messages"]) == 2
        # An empty stamp is "no stamp", not a refusal.
        r = c.put(f"/chat/threads/{tid}", json={"messages": _msgs(2), "if_updated_at": ""})
        assert r.status_code == 200, r.text


def test_a_stamp_that_is_not_a_timestamp_is_a_400(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        tid = c.put("/chat/threads/new", json={"messages": _msgs(1)}).json()["id"]
        r = c.put(
            f"/chat/threads/{tid}",
            json={"messages": _msgs(1), "if_updated_at": "yesterday-ish"},
        )
        assert r.status_code == 400, r.text
        assert "if_updated_at" in r.json()["detail"]


def test_a_timezone_aware_stamp_compares_like_the_naive_one(tmp_path):
    """Stored rows are naive UTC; a client that echoes the stamp with a +00:00
    suffix must not be refused for it."""
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        r = c.put("/chat/threads/new", json={"messages": _msgs(1)})
        tid, stamp = r.json()["id"], r.json()["updated_at"]
        r = c.put(
            f"/chat/threads/{tid}",
            json={"messages": _msgs(2), "if_updated_at": stamp + "+00:00"},
        )
        assert r.status_code == 200, r.text
