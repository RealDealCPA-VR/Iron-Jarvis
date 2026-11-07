"""v1.446.0 (ease-of-use review item 10) — old chats never disappear.

Before: ``GET /chat/threads`` answered the newest 100 chats and nothing
else, and "Search chats" filtered those 100 TITLES on the page. A chat past
the 100th vanished from the rail, the drawer and the Archived view, and the
words said inside a chat were never found.

What is pinned, each with the silent failure it guards:

1. the BARE list is byte-identical (other callers and the v1.311.0 pins
   read it): no ``next`` key, the newest 100;
2. PAGING walks EVERY chat exactly once, even when most of them share one
   ``updated_at`` (Windows' clock ticks every 15.6 ms, so ties are routine):
   a cursor that broke ties by nothing would drop or repeat rows at every
   page boundary;
3. the FIRST older page after the bare list (whose tie order is not a
   contract) uses a bare-stamp cursor that brings the tied rows again, so a
   chat tied with the 100th is never lost;
4. ``next`` is null at the end; ``limit`` is clamped; a bad cursor is a 400;
   archived / project scoping page too; a Build session is never a row;
5. SEARCH finds titles AND message text in every chat (past the 100th
   too), never a Build session, archived only in the Archived view, a Local
   only chat too (the user's own screen), with the matched words MASKED
   before they are cut; a short query asks nothing; a broken index still
   answers the titles.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _seed(platform, n: int, *, stamps: int = 5, archived_every: int = 0,
          project_every: int = 0, build_every: int = 0) -> list[str]:
    """``n`` chats sharing only ``stamps`` distinct ``updated_at`` values
    (heavy ties). Returns the chat-surface ids."""
    base = datetime(2026, 11, 1, 12, 0, 0)
    ids: list[str] = []
    with session_scope(platform.engine) as db:
        for i in range(n):
            tid = f"chat_{i:04d}"
            build = bool(build_every) and i % build_every == 0
            db.add(ChatThreadRecord(
                id=tid,
                title=f"Chat {i}",
                messages_json="[]",
                updated_at=base + timedelta(seconds=i % stamps),
                archived_at=(base if archived_every and i % archived_every == 0 else None),
                project_id=("proj-a" if project_every and i % project_every == 0 else None),
                surface=("build" if build else None),
            ))
            if not build:
                ids.append(tid)
        db.commit()
    return ids


def _walk(client, query: str, limit: int) -> list[str]:
    got: list[str] = []
    cursor = None
    for _ in range(1000):
        sep = "&" if query else ""
        url = f"/chat/threads?{query}{sep}limit={limit}"
        if cursor:
            from urllib.parse import quote

            url += f"&before={quote(cursor)}"
        page = client.get(url).json()
        assert set(page) == {"threads", "next"}
        got.extend(t["id"] for t in page["threads"])
        cursor = page["next"]
        if cursor is None:
            return got
    raise AssertionError("paging never ended")


# 1 ---------------------------------------------------------------------------
def test_the_bare_list_keeps_its_exact_shape(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform, 130)
    body = client.get("/chat/threads").json()
    assert set(body) == {"threads"}  # no `next`: older callers read this
    assert len(body["threads"]) == 100


# 2 ---------------------------------------------------------------------------
def test_paging_walks_every_chat_once_through_heavy_ties(tmp_path):
    client = _client(tmp_path)
    ids = _seed(client.app.state.platform, 237, stamps=3)
    got = _walk(client, "", 40)
    assert len(got) == len(set(got)), "a page boundary repeated a chat"
    assert sorted(got) == sorted(ids), "a page boundary dropped a chat"
    # The paged order is (updated_at DESC, id DESC): deterministic.
    with session_scope(client.app.state.platform.engine) as db:
        from sqlmodel import select

        rows = {r.id: r.updated_at for r in db.exec(select(ChatThreadRecord))}
    want = sorted(ids, key=lambda i: (rows[i], i), reverse=True)
    assert got == want


# 3 ---------------------------------------------------------------------------
def test_the_first_older_page_after_the_bare_list_loses_no_tied_chat(tmp_path):
    client = _client(tmp_path)
    ids = _seed(client.app.state.platform, 150, stamps=2)
    first = [t for t in client.get("/chat/threads").json()["threads"]]
    have = {t["id"] for t in first}
    cursor = first[-1]["updated_at"]  # bare stamp: every row at or before it
    from urllib.parse import quote

    while True:
        page = client.get(f"/chat/threads?limit=100&before={quote(cursor)}").json()
        have |= {t["id"] for t in page["threads"]}
        cursor = page["next"]
        if cursor is None:
            break
    assert have == set(ids)


# 4 ---------------------------------------------------------------------------
def test_next_limit_cursor_scope_and_build_sessions(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    ids = _seed(platform, 30, archived_every=4, project_every=3, build_every=7)
    # The end of the list says so.
    body = client.get("/chat/threads?limit=200").json()
    assert body["next"] is None
    assert all(not t["id"] in {f"chat_{i:04d}" for i in range(0, 30, 7)}
               for t in body["threads"]), "a Build session reached the chat list"
    # Clamped, never a 422 for an out-of-range number.
    assert len(client.get("/chat/threads?limit=0").json()["threads"]) == 1
    assert client.get("/chat/threads?limit=9999").status_code == 200
    # A cursor this route never wrote is a 400 in words.
    bad = client.get("/chat/threads?before=yesterday")
    assert bad.status_code == 400 and "cursor" in bad.json()["detail"]
    # Archived and project scoping page too.
    arch = _walk(client, "archived=only", 2)
    assert arch and all(int(i[-4:]) % 4 == 0 for i in arch)
    proj = _walk(client, "project_id=proj-a", 2)
    assert proj and all(int(i[-4:]) % 3 == 0 for i in proj)
    everyone = _walk(client, "archived=all", 7)
    assert sorted(everyone) == sorted(ids)


# 5 ---------------------------------------------------------------------------
def _put(client, messages, **extra):
    r = client.put("/chat/threads/new", json={"messages": messages, **extra})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_search_finds_titles_and_words_inside_every_chat(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    old = _put(client, [
        {"role": "user", "content": "what about the depreciation on the van"},
        {"role": "assistant", "content": "The zanzibar method applies here."},
    ])
    # Push it far past the newest 100.
    _seed(platform, 120, stamps=120)
    with session_scope(platform.engine) as db:
        r = db.get(ChatThreadRecord, old)
        r.updated_at = datetime(2020, 1, 1)
        db.add(r)
        db.commit()
    assert old not in {t["id"] for t in client.get("/chat/threads").json()["threads"]}

    hit = client.get("/chat/threads/search?q=zanzibar").json()
    assert hit["text"] is True
    rows = {t["id"]: t for t in hit["threads"]}
    assert old in rows, "words said inside an old chat were not found"
    assert rows[old]["match"] == "text"
    assert "zanzibar" in rows[old]["snippet"].lower()
    # The row carries the list's own keys (the rail draws it unchanged).
    listed_keys = set(client.get("/chat/threads").json()["threads"][0])
    assert listed_keys <= set(rows[old])

    titled = client.get("/chat/threads/search?q=Chat 11").json()["threads"]
    assert titled and all(t["match"] == "title" for t in titled)
    assert all("chat 11" in t["title"].lower() for t in titled)


def test_search_respects_archive_build_and_includes_local_only(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    archived = _put(client, [{"role": "user", "content": "quokka archived note"}])
    local = _put(client, [{"role": "user", "content": "quokka local note"}],
                 setup={"local_only": True})
    build = _put(client, [{"role": "user", "content": "quokka build note"}])
    with session_scope(platform.engine) as db:
        a = db.get(ChatThreadRecord, archived)
        a.archived_at = datetime(2026, 11, 2)
        b = db.get(ChatThreadRecord, build)
        b.surface = "build"
        db.add(a)
        db.add(b)
        db.commit()
    plain = {t["id"] for t in client.get("/chat/threads/search?q=quokka").json()["threads"]}
    assert local in plain, "a Local only chat is the user's own: it must be found"
    assert archived not in plain and build not in plain
    only = {t["id"] for t in client.get(
        "/chat/threads/search?q=quokka&archived=only").json()["threads"]}
    assert only == {archived}
    assert client.get("/chat/threads/search?q=quokka&archived=x").status_code == 400


def test_search_masks_the_matched_words_and_bounds_the_answer(tmp_path):
    client = _client(tmp_path)
    secret = "api" + "_key=" + "Zx9" * 8
    tid = _put(client, [{"role": "user", "content": f"walrus setup uses {secret} in prod"}])
    rows = client.get("/chat/threads/search?q=walrus").json()["threads"]
    row = next(t for t in rows if t["id"] == tid)
    assert "Zx9Zx9Zx9" not in row["snippet"], row["snippet"]
    assert len(row["snippet"]) <= chat_routes.CHAT_SEARCH_SNIPPET_CHARS
    # A one-character box asks nothing.
    assert client.get("/chat/threads/search?q=w").json()["threads"] == []
    # At most CHAT_SEARCH_MAX rows, however many match.
    _seed(client.app.state.platform, 80, stamps=80)
    many = client.get("/chat/threads/search?q=Chat&limit=500").json()["threads"]
    assert len(many) == chat_routes.CHAT_SEARCH_MAX


def test_a_broken_index_still_answers_the_titles(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform, 3)

    class _Broken:
        mode = "basic"

        def search(self, *a, **k):
            raise RuntimeError("index down")

    out = chat_routes.search_chat_threads(
        client.app.state.platform.engine, _Broken(), "Chat 1")
    assert [t["id"] for t in out["threads"]] == ["chat_0001"]
    none = chat_routes.search_chat_threads(
        client.app.state.platform.engine, None, "Chat 1")
    assert none["text"] is False and none["threads"]
