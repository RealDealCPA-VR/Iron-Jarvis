"""v1.439.0 — the independent review of Antigravity in Build › Code: what the
first cut left unpinned or got wrong, each pin red first on the builder's code.

1. Antigravity's saved-conversations database is opened READ-ONLY: a write on
   the connection Iron Jarvis makes is refused (the builder's test compared the
   file's bytes, which a read-write connection that only SELECTs leaves as
   they were — so dropping ``mode=ro`` + ``query_only`` stayed green).
2. A note is read when ITS echo comes, never on the message's own echo:
   Antigravity echoes every prompt in order, the message's first (measured,
   ``tests/fixtures/build_app_v1439/*``); a note that starts with the message's
   words ("fix the tests" -> "fix the tests and the lint too") was marked read
   on the message's echo.
3. A model id belongs to the LONGEST base it starts with: a future
   ``gemini-3.8-flash-lite-high`` is flash-lite's, never a "lite-high" level of
   ``gemini-3.8-flash``.
4. Antigravity's own title is kept ONLY for a session with no title, checked
   and written in one transaction (the builder's test pinned only that the
   driver passed the title on; removing the "untitled" check stayed green).
5. No source file of this change carries a control character (a comment in
   ``terminals/ai_clis.py`` had ``\\a`` / ``\\b`` written as BEL / BS).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import EventLog

ROOT = Path(__file__).parents[1]


# --------------------------------------------------------------------------- #
# 1. read-only
# --------------------------------------------------------------------------- #
def test_the_summaries_database_is_opened_so_no_write_can_land(tmp_path):
    db = tmp_path / "conversation_summaries.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversation_summaries (conversation_id text, nesting_depth integer)")
    con.commit()
    con.close()
    before = db.read_bytes()
    ro = A._connect_ro(db)
    assert ro is not None
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO conversation_summaries VALUES ('x', 0)")
            ro.commit()
    finally:
        ro.close()
    assert db.read_bytes() == before


# --------------------------------------------------------------------------- #
# 2. a note's echo
# --------------------------------------------------------------------------- #
def _update(text: str) -> dict:
    return {"jsonrpc": "2.0", "method": "session/update",
            "params": {"update": {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": text}}}}


def test_the_messages_own_echo_never_reads_a_note_that_starts_with_its_words():
    read: list[str] = []
    written: list[dict] = []

    async def write(obj: dict) -> None:
        written.append(obj)

    async def go() -> None:
        d = A.AntigravityDriver(write=write, emit=lambda e: None, ask=lambda a: None,
                                on_session_id=lambda s: None, mode="own")
        d.session_id = "s1"
        d.steer_read = read.append
        await d.send_user("fix the tests", [])
        await d.steer("n1", "fix the tests and the lint too", [])
        assert [w["params"]["prompt"][0]["text"] for w in written] == [
            "fix the tests", "fix the tests and the lint too"]
        d.on_message(_update("fix the tests"))  # the MESSAGE's echo (first, measured)
        assert read == [], "the message's echo is not the note's"
        d.on_message(_update("fix the tests and the lint too"))
        assert read == ["n1"]

    asyncio.run(go())


def test_a_note_shorter_than_the_message_is_not_read_by_the_messages_echo():
    read: list[str] = []

    async def write(obj: dict) -> None:
        pass

    async def go() -> None:
        d = A.AntigravityDriver(write=write, emit=lambda e: None, ask=lambda a: None,
                                on_session_id=lambda s: None, mode="own")
        d.session_id = "s1"
        d.steer_read = read.append
        await d.send_user("do it now please", [])
        await d.steer("n1", "do it", [])
        d.on_message(_update("do it now please"))
        assert read == []
        d.on_message(_update("do it"))
        assert read == ["n1"]

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# 3. levels per model
# --------------------------------------------------------------------------- #
def test_each_level_id_belongs_to_the_longest_base_it_starts_with():
    config = [
        {"id": "model", "currentValue": "gemini-3.8-flash",
         "options": [{"value": "gemini-3.8-flash", "name": "Flash"},
                     {"value": "gemini-3.8-flash-lite", "name": "Flash Lite"}]},
        {"id": "thought_level", "currentValue": "", "options": []},
    ]
    models = {"availableModels": [{"modelId": m} for m in (
        "gemini-3.8-flash-low", "gemini-3.8-flash-high", "gemini-3.8-flash-lite-low", "gemini-3.8-flash-lite-high")]}
    rows, _cur, _lvl = A.model_rows(config, models)
    got = {r["id"]: r["efforts"] for r in rows}
    assert got == {"gemini-3.8-flash": ["low", "high"], "gemini-3.8-flash-lite": ["low", "high"]}


# --------------------------------------------------------------------------- #
# 4. the tool's title, only for an untitled session
# --------------------------------------------------------------------------- #
async def _title_after(tmp_path: Path, start_title: str, tool_title: str) -> str:
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    row = store.create(svc.engine, harness="antigravity", folder=str(work), mode="own", title=start_title)
    live = S.LiveSession(row.id, "antigravity", EventLog(tmp_path / "home", row.id))
    svc._title_if_untitled(live, tool_title)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if (store.get(svc.engine, row.id).title or "") != start_title:
            break
    return store.get(svc.engine, row.id).title or ""


async def test_antigravitys_title_names_only_a_session_with_none(tmp_path):
    assert await _title_after(tmp_path / "a", "", "Probe title") == "Probe title"
    assert await _title_after(tmp_path / "b", "My own name", "Probe title") == "My own name"


def test_the_title_is_kept_only_while_none_is_set_in_one_step(tmp_path):
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    row = store.create(svc.engine, harness="antigravity", folder=str(tmp_path), mode="own", title="")
    assert store.set_title_if_empty(svc.engine, row.id, "First") is True
    assert store.set_title_if_empty(svc.engine, row.id, "Second") is False
    assert store.get(svc.engine, row.id).title == "First"
    assert store.set_title_if_empty(svc.engine, "missing", "x") is False


# --------------------------------------------------------------------------- #
# 5. no control characters
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [
    "src/iron_jarvis/hosted/antigravity.py",
    "src/iron_jarvis/terminals/ai_clis.py",
    "src/iron_jarvis/hosted/service.py",
    "src/iron_jarvis/hosted/words.py",
    "tests/test_build_app_antigravity_v1439.py",
])
def test_no_control_characters_in_the_source(rel):
    data = (ROOT / rel).read_bytes()
    bad = sorted({c for c in data if c < 32 and c not in (9, 10)})
    assert bad == [], f"{rel} carries control characters {bad}"


def test_the_recordings_parse():
    for path in (ROOT / "tests" / "fixtures" / "build_app_v1439").glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_a_long_session_log_keeps_only_its_tail_before_a_start(tmp_path):
    # v1.439.0 integration: the log lives in the state home (backups) and grew
    # for the session's whole life. Past LOG_MAX_BYTES only the tail is kept,
    # starting at a whole line, and the newest error line is still readable.
    from iron_jarvis.hosted import antigravity as A

    log = tmp_path / A.LOG_NAME
    old = b"I1106 10:50:32.966432 201 server.go:1] filler line\n" * (A.LOG_MAX_BYTES // 40)
    last = b"E1106 10:50:33.267572 278 errorreport.go:247] agent executor error: generating and executing: Error 400, Message: API key not valid.\n"
    log.write_bytes(old + last)
    A.trim_log(str(log))
    data = log.read_bytes()
    assert len(data) <= A.LOG_TAIL_BYTES and data.endswith(last)
    assert data.startswith(b"I1106")  # cut at a whole line
    assert "API key not valid" in A.log_reason(str(log))
    small = tmp_path / "small.log"
    small.write_bytes(b"x\n" * 10)
    A.trim_log(str(small))
    assert small.read_bytes() == b"x\n" * 10
    A.trim_log(str(tmp_path / "missing.log"))  # never raises
