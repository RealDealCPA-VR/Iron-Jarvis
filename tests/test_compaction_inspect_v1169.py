"""Compaction inspect (v1.169.0): the standing summary, readable again.

The model-written compaction summary is injected into the system prompt of
every later turn and read back as authoritative — and before this wave it was
readable exactly once, in the response of the compact that created it. These
tests cover the new read path:

* ``GET /chat/threads/{id}/compaction`` — recompute what stands over a SAVED
  thread and return the summary + the claims verification removed;
* ``CompactionStore.standing`` — the longest-stored-prefix probe, pinned
  byte-for-byte against ``compaction.prefix_key`` (the anti-drift pin: the
  probe is that function unrolled, and if the keying ever changes shape this
  file goes red before the endpoint silently reports ``found: false`` forever);
* the claims TEXT now persists (``stripped_claims_json``), not just the count
  — including through ``POST /chat/compact``'s fresh AND cached responses.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.context import compaction as C
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _compaction_store


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _msgs(n: int, stem: str = "we should discuss invoice 42 (message") -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{stem} {i})"}
        for i in range(n)
    ]


def _save_thread(client: TestClient, messages: list[dict]) -> str:
    r = client.put("/chat/threads/new", json={"messages": messages})
    assert r.status_code == 200
    return r.json()["id"]


def _key(messages: list[dict], upto: int) -> str:
    """The EXACT keying the live turn uses (chat_turn._apply_compaction /
    routes.chat.chat_compact) — deliberately re-spelled here, not imported,
    so a drift in either implementation turns a test red."""
    return C.prefix_key(
        [f"{m['role']}\x1e{m['content']}" for m in messages[:upto]]
    )


def _seed(client: TestClient, messages: list[dict], upto: int, body: str, **kw) -> str:
    key = _key(messages, upto)
    rec = _compaction_store(client.app.state.platform).put(
        key, summary=C.render(f"GOAL:\n- {body}\n"), covers=upto, **kw
    )
    assert rec is not None
    return key


# --------------------------------------------------------------------------- #
# (1) THE ENDPOINT.
# --------------------------------------------------------------------------- #
def test_an_unknown_thread_is_404(tmp_path):
    r = _client(tmp_path).get("/chat/threads/no-such-thread/compaction")
    assert r.status_code == 404


def test_a_thread_with_no_standing_summary_says_found_false(tmp_path):
    """Strict shape: ``found: false`` carries NOTHING else — a summary field
    on the not-found path would invite the client to render a ghost."""
    client = _client(tmp_path)
    tid = _save_thread(client, _msgs(14))
    r = client.get(f"/chat/threads/{tid}/compaction")
    assert r.status_code == 200
    assert r.json() == {"found": False}


def test_the_standing_summary_comes_back_with_its_values(tmp_path):
    client = _client(tmp_path)
    msgs = _msgs(14)
    tid = _save_thread(client, msgs)
    _seed(
        client,
        msgs,
        8,
        "THE-STANDING-BODY",
        stripped=2,
        stripped_claims=["C:/gone/a.py", '"never said this"'],
        trigger="manual",
        provider="acme",
        model="acme-1",
    )
    got = client.get(f"/chat/threads/{tid}/compaction").json()
    assert got["found"] is True
    assert "THE-STANDING-BODY" in got["summary"]
    assert got["covers"] == 8
    assert got["stripped"] == 2
    assert got["stripped_claims"] == ["C:/gone/a.py", '"never said this"']
    assert got["trigger"] == "manual"
    assert got["provider"] == "acme"
    assert got["model"] == "acme-1"
    datetime.fromisoformat(got["created_at"])  # parseable, not a repr


def test_a_summary_still_stands_after_the_thread_grows(tmp_path):
    """The whole reason ``standing`` probes prefixes: the live turn's key is
    computed over the list it was HANDED, which grows every turn, so the
    creating key alone would go stale one exchange after every compaction."""
    client = _client(tmp_path)
    msgs = _msgs(18)
    _seed(client, msgs, 8, "SURVIVES-GROWTH")  # created 10 messages ago
    tid = _save_thread(client, msgs)  # the thread has since grown to 18
    got = client.get(f"/chat/threads/{tid}/compaction").json()
    assert got["found"] is True
    assert got["covers"] == 8
    assert "SURVIVES-GROWTH" in got["summary"]


def test_the_longest_standing_summary_wins(tmp_path):
    """A re-compaction absorbs the earlier summary as material, so the longer
    record supersedes — returning the short one would resurrect claims the
    later verification pass may have dropped."""
    client = _client(tmp_path)
    msgs = _msgs(18)
    tid = _save_thread(client, msgs)
    _seed(client, msgs, 6, "THE-OLD-SHORT-ONE")
    _seed(client, msgs, 11, "THE-NEWER-LONGER-ONE")
    got = client.get(f"/chat/threads/{tid}/compaction").json()
    assert got["covers"] == 11
    assert "THE-NEWER-LONGER-ONE" in got["summary"]
    assert "THE-OLD-SHORT-ONE" not in got["summary"]


def test_a_stripped_count_without_recorded_text_reads_back_empty_claims(tmp_path):
    """Rows from before v1.169.0 (and the agent auto-lane) persisted only the
    COUNT. The endpoint must hand both facts over unchanged — the client says
    "not recorded", and inventing an empty 'nothing was stripped' would be a
    lie about the exact thing this surface exists for."""
    client = _client(tmp_path)
    msgs = _msgs(14)
    tid = _save_thread(client, msgs)
    _seed(client, msgs, 8, "COUNT-ONLY", stripped=3)  # no stripped_claims kwarg
    got = client.get(f"/chat/threads/{tid}/compaction").json()
    assert got["stripped"] == 3
    assert got["stripped_claims"] == []


def test_a_corrupt_transcript_blob_is_not_found_not_a_500(tmp_path):
    from sqlmodel import Session

    from iron_jarvis.core.models import ChatThreadRecord

    client = _client(tmp_path)
    tid = _save_thread(client, _msgs(14))
    with Session(client.app.state.platform.engine) as db:
        r = db.get(ChatThreadRecord, tid)
        r.messages_json = "{not json"
        db.add(r)
        db.commit()
    resp = client.get(f"/chat/threads/{tid}/compaction")
    assert resp.status_code == 200
    assert resp.json() == {"found": False}


# --------------------------------------------------------------------------- #
# (2) THE PROBE — parity with prefix_key is the load-bearing fact.
# --------------------------------------------------------------------------- #
def test_the_probe_matches_prefix_key_byte_for_byte(tmp_path):
    """``standing`` unrolls ``prefix_key`` into an incremental hash. For every
    probed length, a record seeded under the REAL ``prefix_key`` must be found
    — and found under that exact id. If the keying ever changes shape, this is
    the test that goes red instead of the endpoint going silently blind."""
    store = _compaction_store(_client(tmp_path).app.state.platform)
    for k in (1, 3, 8, 14):
        msgs = _msgs(14, stem=f"variant {k} content")
        key = C.prefix_key([f"{m['role']}\x1e{m['content']}" for m in msgs[:k]])
        store.put(key, summary=C.render(f"GOAL:\n- covers {k}\n"), covers=k)
        rec = store.standing(msgs)
        assert rec is not None, f"prefix {k} never probed"
        assert rec.id == key
        assert rec.covers == k


def test_the_probe_reads_attr_style_messages_like_dicts(tmp_path):
    """The route hands the store dicts (a stored thread); the live turn keys
    off attribute-style bodies. Both must land on the same address or the two
    halves of the feature describe different conversations."""
    store = _compaction_store(_client(tmp_path).app.state.platform)
    dicts = _msgs(12)
    objs = [SimpleNamespace(role=m["role"], content=m["content"]) for m in dicts]
    _ = store.put(
        _key(dicts, 5), summary=C.render("GOAL:\n- attr parity\n"), covers=5
    )
    from_dicts = store.standing(dicts)
    from_objs = store.standing(objs)
    assert from_dicts is not None and from_objs is not None
    assert from_dicts.id == from_objs.id


def test_the_probe_degrades_to_none_never_raises(tmp_path):
    store = _compaction_store(_client(tmp_path).app.state.platform)
    assert store.standing([]) is None
    assert store.standing(None) is None
    assert store.standing([{"role": None, "content": None}]) is None


# --------------------------------------------------------------------------- #
# (3) THE CLAIMS now PERSIST — store level, then end-to-end through the route.
# --------------------------------------------------------------------------- #
def test_claims_round_trip_the_store(tmp_path):
    store = _compaction_store(_client(tmp_path).app.state.platform)
    key = "claims-round-trip"
    store.put(
        key,
        summary="GOAL:\n- x\n",
        covers=4,
        stripped=2,
        stripped_claims=["C:/a/b.py", 7, '"quoted"'],  # non-str filtered out
    )
    rec = store.get(key)
    assert rec is not None
    assert rec.claims() == ["C:/a/b.py", '"quoted"']

    store.put("no-claims", summary="GOAL:\n- y\n", covers=4, stripped=1)
    assert store.get("no-claims").claims() == []


def test_the_store_never_truncates_the_claims_it_is_handed(tmp_path):
    """The store persists the list FAITHFULLY. It used to re-cap at 20, which
    collapsed an honesty state: a summary with 25 stripped claims read back 20
    with zero indication more were removed — and the creating POST returned a
    different list than the inspect route forever after. Any bounding is the
    producer's call (``compaction.compact_messages`` caps its own output); the
    store silently shortening what a caller recorded is the mutation this test
    exists to catch."""
    store = _compaction_store(_client(tmp_path).app.state.platform)
    many = [f"C:/gone/file-{i}.py" for i in range(25)]
    store.put(
        "no-truncation",
        summary="GOAL:\n- z\n",
        covers=6,
        stripped=25,
        stripped_claims=many,
    )
    rec = store.get("no-truncation")
    assert rec is not None
    assert rec.claims() == many  # all 25, byte-identical, in order


def test_the_route_returns_every_recorded_claim(tmp_path):
    """End-to-end: what the store was handed is what the inspect route serves —
    the same list the creating response returned, however long."""
    client = _client(tmp_path)
    msgs = _msgs(14)
    tid = _save_thread(client, msgs)
    many = [f"claim number {i}" for i in range(23)]
    _seed(client, msgs, 8, "ALL-CLAIMS", stripped=23, stripped_claims=many)
    got = client.get(f"/chat/threads/{tid}/compaction").json()
    assert got["stripped"] == 23
    assert got["stripped_claims"] == many


def test_manual_compact_persists_its_claims_end_to_end(tmp_path, monkeypatch):
    """POST /chat/compact -> the store -> GET /chat/threads/{id}/compaction:
    the claim the verifier removed is readable AFTER the creating response is
    gone, and the cached second POST no longer drops it either."""
    from iron_jarvis.providers.adapters.base import LLMResponse

    client = _client(tmp_path)
    platform = client.app.state.platform

    class _FakeAdapter:  # a REAL (non-mock) provider as far as the glue cares
        model = "fake-1"

        async def complete(self, *, system, messages, tools=None):
            return LLMResponse(
                text=(
                    "GOAL:\n- discuss invoice 42\n"
                    "DONE:\n- wrote C:/nowhere/else.zzz\n"
                )
            )

    monkeypatch.setattr(platform.providers, "get", lambda p, m=None: _FakeAdapter())

    msgs = _msgs(14)  # every message mentions "invoice 42" -> GOAL corroborated
    tid = _save_thread(client, msgs)

    fresh = client.post("/chat/compact", json={"messages": msgs})
    assert fresh.status_code == 200
    body = fresh.json()
    assert body["cached"] is False
    assert body["stripped"] == 1
    assert body["stripped_claims"] == ["C:/nowhere/else.zzz"]
    assert "invoice 42" in body["summary"]

    got = client.get(f"/chat/threads/{tid}/compaction").json()
    assert got["found"] is True
    assert got["covers"] == 14 - C.KEEP_RECENT
    assert got["stripped"] == 1
    assert got["stripped_claims"] == ["C:/nowhere/else.zzz"]
    assert "invoice 42" in got["summary"]
    assert "C:/nowhere/else.zzz" not in got["summary"]  # removed means removed

    cached = client.post("/chat/compact", json={"messages": msgs}).json()
    assert cached["cached"] is True
    assert cached["stripped_claims"] == ["C:/nowhere/else.zzz"]


# --------------------------------------------------------------------------- #
# (4) THE PAGE's RESETS ORPHAN IN-FLIGHT FETCHES — source-level call-site pins.
#
# Why these live in Python: nothing in the vitest suite imports chat/page.tsx
# (a 6,000+-line Next page with the whole app behind it), so a mutation that
# reverts these lines leaves every frontend test green. Same technique as
# test_draft_spacing_v1163.py, same class of silent wiring failure.
#
# The defect: `refreshCompaction` guards its response with a generation ref —
# but a BARE `setCompaction(null)` clears the chip WITHOUT bumping that gen,
# so a GET started for the previous thread still passes the guard when it
# resolves after the reset and repaints the OLD thread's summary onto a fresh
# conversation (New Chat) or a thread whose own GET threw (openThread's error
# path never reaches the later gen bump). Both resets must clear through
# `refreshCompaction(null)`, which increments the gen before clearing.
# --------------------------------------------------------------------------- #
def test_the_page_resets_clear_compaction_through_the_gen_bump():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[1] / "dashboard" / "app" / "chat" / "page.tsx"
    ).read_text(encoding="utf-8")
    # Both resets (openThread + newChat) go through the gen-bumping clear...
    assert page.count("refreshCompaction(null)") >= 2, (
        "a reset went back to a bare setCompaction(null): an in-flight "
        "compaction GET for the previous thread would survive it and repaint "
        "the old thread's summary"
    )
    # ...and NO bare gen-unaware clear remains anywhere in the page. The only
    # legitimate setCompaction(null) sites are inside refreshCompaction itself,
    # which bumps compactionGenRef first.
    import re

    bare = [
        m.start()
        for m in re.finditer(r"setCompaction\(null\)", page)
        if "compactionGenRef.current" not in page[max(0, m.start() - 600) : m.start()]
    ]
    assert bare == [], (
        "setCompaction(null) called without a compactionGenRef bump in scope — "
        "the stale-fetch guard cannot see that reset"
    )
