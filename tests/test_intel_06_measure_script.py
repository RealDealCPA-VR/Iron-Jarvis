"""The intelligence plan, release 6: ``scripts/measure_tool_definitions.py``
prints, from the app's own records, what each reply used.

For the last week (``--days``): tokens sent per reply by part, and the share
the model reused, by REPLY NUMBER in a chat (reply 1, 2, 3 ...
averaged over chats), with plain chat, coding sessions and agent runs apart,
and the time to the first word where one was recorded.

The script is read-only on the database it is pointed at. Every test here
fills a database of its own (never the owner's) through the real schema, and
passes the time in (``--now``): nothing sleeps and nothing reads the clock.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.core.models import AgentRun, AgentState
from iron_jarvis.daemon.chat_turn import SUMMARY_USAGE_LABEL
from iron_jarvis.daemon.routes.followups import FOLLOWUPS_USAGE_LABEL
from iron_jarvis.harness.library import REFLECT_USAGE_LABEL

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_tool_definitions.py"
NOW = datetime(2026, 11, 26, 12, 0, 0)
NOW_ARG = "2026-11-26T12:00:00"


def _script():
    spec = importlib.util.spec_from_file_location("measure_tool_definitions_r6", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _script()


def _record(messages: int, *, files: int = 0, memory: int = 100, system: int = 2_000, tools: int = 1_500,
            kind: str = "", cache_input: int | None = None, left_out: int = 0, sent: dict | None = None) -> dict:
    parts = {"messages": messages, "memory": memory, "system": system}
    if files:
        parts["attachments"] = files
    rec = {"v": 1, "window": 100_000, "window_known": True, "used": sum(parts.values()),
           "percent": 5, "parts": parts, "tools": {"builtin": [4, tools], "extensions": {}}}
    if kind:
        rec["kind"] = kind
    if cache_input is not None:
        rec["cache_input"] = cache_input
    if left_out:
        rec["left_out"] = left_out
    if sent:
        rec["sent"] = sent
    return rec


def _row(db, *, thread: str | None, hours_ago: float, record: dict | None, input_tokens: int = 6_000,
         cache_read: int | None = None, model_ms: int | None = 3_000, first_ms: int | None = None,
         calls: int | None = 1, session: str = "chat", label: str = "") -> None:
    db.add(AgentRun(
        session_id=session, thread_id=thread, state=AgentState.COMPLETED, input_tokens=input_tokens,
        output_tokens=20, steps=calls or 1, result=label, created_at=NOW - timedelta(hours=hours_ago),
        parts_json=json.dumps(record) if record is not None else None,
        cache_read_input_tokens=cache_read, model_ms=model_ms, model_calls=calls, first_word_ms=first_ms,
    ))


@pytest.fixture()
def filled(tmp_path) -> Path:
    """Two saved chats, a coding session and two agent runs, on the real schema."""
    path = tmp_path / "home" / "ironjarvis.db"
    path.parent.mkdir()
    engine = open_db(str(path))
    with session_scope(engine) as db:
        # Chat A: three replies; the messages grow, the model reuses more each time.
        _row(db, thread="chat_a", hours_ago=30, record=_record(100, cache_input=6_000), cache_read=0,
             first_ms=1_000)
        _row(db, thread="chat_a", hours_ago=29, record=_record(900, files=4_000, cache_input=8_000),
             input_tokens=8_000, cache_read=4_000, first_ms=2_000)
        _row(db, thread="chat_a", hours_ago=28, record=_record(1_700), input_tokens=9_000, cache_read=None)
        # A summary's row and a step's own row in chat A are not replies
        # (each has a label), and an agent's step rows are in a session of
        # their own.
        _row(db, thread="chat_a", hours_ago=28.5, record=None, label="A summary of the conversation")
        _row(db, thread="chat_a", hours_ago=29.5, record=None, label="A picture was looked at for a step")
        _row(db, thread=None, hours_ago=28.4, record=None, session="tool-use", label="Model calls made by a step")
        # Chat B: two replies.
        _row(db, thread="chat_b", hours_ago=10, record=_record(300, cache_input=4_000), input_tokens=4_000,
             cache_read=1_000, first_ms=3_000)
        # (One of its two calls reported no reuse figure: 9,000 sent, 6,000 of it reported.)
        _row(db, thread="chat_b", hours_ago=9, record=_record(1_100, cache_input=6_000), cache_read=5_000,
             input_tokens=9_000, model_ms=5_000, calls=2)
        # Chat C: its first reply is older than the week; its second is inside it.
        _row(db, thread="chat_c", hours_ago=24 * 20, record=_record(50), cache_read=0)
        _row(db, thread="chat_c", hours_ago=2, record=_record(500, left_out=4, cache_input=6_000), cache_read=3_000)
        # A reply from before this release (no record), and one of a chat never saved.
        _row(db, thread="chat_old", hours_ago=5, record=None)
        _row(db, thread=None, hours_ago=4, record=_record(70))
        # A coding session's reply.
        _row(db, thread="code_a", hours_ago=3, record=_record(2_500, kind="code", system=9_000, cache_input=6_000),
             cache_read=1_500)
        # Two agent runs (each its own session).
        _row(db, thread=None, session="session_1", hours_ago=6, input_tokens=20_000, cache_read=10_000,
             calls=4, model_ms=12_000, first_ms=1_500,
             record=_record(40, system=3_000, cache_input=20_000,
                            sent={"system": 12_000, "messages": 2_000, "tools": 6_000, "steps": 4}))
        _row(db, thread=None, session="session_2", hours_ago=5, input_tokens=10_000, cache_read=None,
             calls=2, model_ms=4_000,
             record=_record(60, system=3_000, sent={"system": 6_000, "messages": 400, "tools": 3_000, "steps": 2}))
        db.commit()
    engine.dispose()
    return path


def _run(capsys, *argv: str) -> tuple[int, str]:
    code = M.main(list(argv))
    return code, capsys.readouterr().out


def _table(out: str, title: str) -> tuple[list[str], dict[str, list[str]]]:
    """``(header, {row label: cells})`` of the table under *title*."""
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith(title + ":"))
    head = lines[at + 1].replace("first word s", "first_word_s").replace("model s", "model_s").split()
    rows: dict[str, list[str]] = {}
    for line in lines[at + 2:]:
        cells = line.replace(M.NOT_REPORTED, "not_reported").split()
        if not line.startswith("  ") or not cells or not cells[0].rstrip("+").isdigit():
            break
        assert len(cells) == len(head), (line, head)
        rows[cells[0]] = cells
    return head, rows


def _cell(head: list[str], row: list[str], col: str) -> str:
    return row[head.index(col)]


# --------------------------------------------------------------------------- #
# A filled database
# --------------------------------------------------------------------------- #
def test_it_prints_tokens_by_part_and_the_share_reused_by_reply_number(filled, capsys):
    code, out = _run(capsys, "--db", str(filled), "--now", NOW_ARG)
    assert code == 0
    assert out.splitlines()[0] == "What each reply used (Iron Jarvis's own records, last 7 days)"
    assert "Plain chat: 6 replies in 3 chats" in out
    head, rows = _table(out, "Plain chat")
    assert head[:4] == ["reply", "n", "sent", "calls"]
    assert head[4:] == ["messages", "files", "remembered", "folder", "rules", "skills", "summary",
                        "instructions", "tools", "reused", "model_s", "first_word_s"]
    assert set(rows) == {"1", "2", "3"}

    # Reply 1: chats A and B (chat C's first reply is older than the week).
    r1 = rows["1"]
    assert _cell(head, r1, "n") == "2"
    assert _cell(head, r1, "sent") == "5,000"              # (6,000 + 4,000) / 2: the provider's count
    assert _cell(head, r1, "messages") == "200"            # (100 + 300) / 2, an estimate
    assert _cell(head, r1, "files") == "0"                 # measured: no file was attached
    assert _cell(head, r1, "instructions") == "2,000"
    assert _cell(head, r1, "tools") == "1,500"
    assert _cell(head, r1, "reused") == "10.0%"            # (0 + 1,000) / (6,000 + 4,000)
    assert _cell(head, r1, "first_word_s") == "2.0"        # (1.0 + 3.0) / 2

    # Reply 2: chats A, B and C; the model reuses more of a longer chat.
    r2 = rows["2"]
    assert _cell(head, r2, "n") == "3"
    assert _cell(head, r2, "messages") == "833"            # (900 + 1,100 + 500) / 3
    assert _cell(head, r2, "files") == "1,333"             # 4,000 in one of three
    assert _cell(head, r2, "sent") == "7,667"              # (8,000 + 9,000 + 6,000) / 3
    # Read against the input of the calls that REPORTED a figure: 12,000 / 20,000.
    assert _cell(head, r2, "reused") == "60.0%"
    assert _cell(head, r2, "calls") == "1"                 # (1 + 2 + 1) / 3 = 1.33
    assert _cell(head, r2, "model_s") == "3.7"             # (3 + 5 + 3) / 3
    assert _cell(head, r2, "first_word_s") == "2.0"        # one reply recorded one

    # Reply 3: only chat A, whose provider reported no reuse figure: said so, never 0%.
    r3 = rows["3"]
    assert _cell(head, r3, "n") == "1" and _cell(head, r3, "reused") == "not_reported"
    assert _cell(head, r3, "first_word_s") == "-"          # not recorded is "-", never 0
    assert "0.0%" not in " ".join(r3)

    # One reply left earlier messages out: said under the table.
    assert "In 1 of these, earlier messages were left out so the reply fits" in out
    assert "1 reply of chats that were never saved is left out" in out


def test_coding_sessions_and_agent_runs_are_read_apart(filled, capsys):
    _, out = _run(capsys, "--db", str(filled), "--now", NOW_ARG)
    assert "Coding sessions: 1 reply in 1 chat" in out
    head, rows = _table(out, "Coding sessions")
    assert _cell(head, rows["1"], "instructions") == "9,000" and _cell(head, rows["1"], "reused") == "25.0%"

    assert "Agent runs: 2 runs in 2 sessions" in out, "an agent's step rows are not runs"
    head, rows = _table(out, "Agent runs")
    assert head[0] == "run" and set(rows) == {"1"}
    r = rows["1"]
    assert _cell(head, r, "n") == "2" and _cell(head, r, "sent") == "15,000"
    assert _cell(head, r, "calls") == "3"                  # (4 + 2) / 2
    assert _cell(head, r, "reused") == "50.0%"             # only the run that reported: 10,000 / 20,000
    assert _cell(head, r, "model_s") == "8.0" and _cell(head, r, "first_word_s") == "1.5"
    assert "Per step, over 6 steps (estimates): instructions 3,000, messages 400, tools 1,500" in out
    # A plain chat's table holds no run and no coding reply.
    head, rows = _table(out, "Plain chat")
    assert sum(int(_cell(head, r, "n")) for r in rows.values()) == 6


def test_days_widens_the_window_and_long_chats_fold_into_one_row(filled, capsys):
    _, out = _run(capsys, "--db", str(filled), "--now", NOW_ARG, "--days", "30", "--max-replies", "2")
    assert out.splitlines()[0].endswith("last 30 days)")
    assert "Plain chat: 7 replies in 3 chats" in out       # chat C's old first reply is inside now
    head, rows = _table(out, "Plain chat")
    assert set(rows) == {"1", "2", "3+"}
    assert _cell(head, rows["1"], "n") == "3" and _cell(head, rows["3+"], "n") == "1"


def test_the_older_form_still_prints_what_definitions_cost(filled, tmp_path, capsys):
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"tools": [
        {"name": "read_file", "description": "Read a file."},
        {"name": "mcp__ledger__post", "description": "Post an entry. " * 20},
        {"name": "mcp__mail__send", "description": "Send a message."},
        {"name": "mcp__notes__find", "description": "Find a note."},
    ]}), encoding="utf-8")
    code, out = _run(capsys, str(tools), str(filled), "--now", NOW_ARG)
    assert code == 0
    assert out.startswith("built-in tools: 1, all together")
    assert "each extension:" in out and "ledger" in out
    assert "What each reply used (Iron Jarvis's own records, last 7 days)" in out
    assert "Plain chat: 6 replies in 3 chats" in out


# --------------------------------------------------------------------------- #
# A reply number counts replies only
# --------------------------------------------------------------------------- #
def _db(tmp_path, name: str = "ironjarvis.db") -> Path:
    path = tmp_path / name
    open_db(str(path)).dispose()
    return path


def _fill(path: Path, rows) -> None:
    engine = open_db(str(path))
    with session_scope(engine) as db:
        rows(db)
        db.commit()
    engine.dispose()


def test_a_reply_number_skips_the_rows_a_chat_writes_that_are_not_replies(tmp_path, capsys):
    """Follow-up suggestions and a coding session's look back for a lesson
    write a usage row in the chat (it is in the chat's totals). With their
    label they are not replies: three replies are numbered 1, 2, 3."""
    assert FOLLOWUPS_USAGE_LABEL and REFLECT_USAGE_LABEL and SUMMARY_USAGE_LABEL
    path = _db(tmp_path)

    def rows(db):
        _row(db, thread="chat_n", hours_ago=10, record=_record(100))
        _row(db, thread="chat_n", hours_ago=9.9, record=None, label=FOLLOWUPS_USAGE_LABEL)
        _row(db, thread="chat_n", hours_ago=9, record=_record(200))
        _row(db, thread="chat_n", hours_ago=8.9, record=None, label=FOLLOWUPS_USAGE_LABEL)
        _row(db, thread="chat_n", hours_ago=8.8, record=None, label=REFLECT_USAGE_LABEL)
        _row(db, thread="chat_n", hours_ago=8.7, record=None, label=SUMMARY_USAGE_LABEL)
        _row(db, thread="chat_n", hours_ago=8, record=_record(300))

    _fill(path, rows)
    code, out = _run(capsys, "--db", str(path), "--now", NOW_ARG)
    assert code == 0 and "Plain chat: 3 replies in 1 chat" in out
    head, table = _table(out, "Plain chat")
    assert sorted(table) == ["1", "2", "3"]
    assert [_cell(head, table[n], "messages") for n in ("1", "2", "3")] == ["100", "200", "300"]
    assert all(_cell(head, table[n], "n") == "1" for n in table)
    # Said in one line: rows of those two kinds from before this version have no label.
    assert out.count(M.OLDER_LABELS) == 1
    assert "written before this version have no label and still count as replies" in M.OLDER_LABELS


def test_an_unlabelled_row_of_that_kind_from_an_older_version_still_counts_and_the_output_says_so(tmp_path,
                                                                                                capsys):
    path = _db(tmp_path)

    def rows(db):
        _row(db, thread="chat_o", hours_ago=10, record=_record(100))
        _row(db, thread="chat_o", hours_ago=9.9, record=None)      # an older follow-ups row: no label
        _row(db, thread="chat_o", hours_ago=9, record=_record(200))

    _fill(path, rows)
    _, out = _run(capsys, "--db", str(path), "--now", NOW_ARG)
    head, table = _table(out, "Plain chat")
    assert sorted(table) == ["1", "3"], "the unlabelled row took number 2"
    assert M.OLDER_LABELS in out


def test_a_record_of_a_shape_it_does_not_know_is_left_out_and_counted_never_read_as_zeros(tmp_path, capsys):
    path = _db(tmp_path)

    def rows(db):
        _row(db, thread="chat_v", hours_ago=10, record=_record(100))
        _row(db, thread="chat_v", hours_ago=9, record={**_record(999_000), "v": 2})    # a newer version's
        _row(db, thread="chat_v", hours_ago=8, record=_record(300))
        _row(db, thread="chat_w", hours_ago=7, record={"parts": {"messages": 5}})      # no version at all

    _fill(path, rows)
    code, out = _run(capsys, "--db", str(path), "--now", NOW_ARG)
    assert code == 0 and "Plain chat: 2 replies in 1 chat" in out
    head, table = _table(out, "Plain chat")
    # The reply is still a reply of its chat (the third one is number 3); its record is not read.
    assert sorted(table) == ["1", "3"]
    assert "999,000" not in out
    assert "2 records of a shape this script does not know (a newer version's, or damaged) are left out." in out
    assert M.RECORD_VERSION == 1


def test_only_records_it_does_not_know_says_nothing_yet_and_how_many_were_left_out(tmp_path, capsys):
    path = _db(tmp_path)
    _fill(path, lambda db: _row(db, thread="chat_v", hours_ago=3, record={**_record(100), "v": 7}))
    code, out = _run(capsys, "--db", str(path), "--now", NOW_ARG)
    assert code == 0 and M.NOTHING_YET.format(days=7) in out
    assert "1 record of a shape this script does not know (a newer version's, or damaged) is left out." in out


# --------------------------------------------------------------------------- #
# Nothing yet
# --------------------------------------------------------------------------- #
def test_an_empty_database_says_there_is_nothing_yet_and_exits_0(tmp_path, capsys):
    path = tmp_path / "home" / "ironjarvis.db"
    path.parent.mkdir()
    open_db(str(path)).dispose()
    code, out = _run(capsys, "--root", str(path.parent), "--now", NOW_ARG)
    assert code == 0
    assert out.splitlines()[1] == M.NOTHING_YET.format(days=7)
    assert "reply  " not in out, "no table is printed over nothing"


def test_replies_from_before_this_release_are_not_counted_as_zeros(tmp_path, capsys):
    path = tmp_path / "ironjarvis.db"
    engine = open_db(str(path))
    with session_scope(engine) as db:
        _row(db, thread="chat_old", hours_ago=3, record=None, cache_read=None, model_ms=None, calls=None)
        db.commit()
    engine.dispose()
    code, out = _run(capsys, "--db", str(path), "--now", NOW_ARG)
    assert code == 0 and M.NOTHING_YET.format(days=7) in out


def test_a_database_from_an_older_version_and_a_missing_one_say_so_and_exit_0(tmp_path, capsys):
    old = tmp_path / "old.db"
    con = sqlite3.connect(str(old))
    con.execute("create table agentrun (id text primary key, session_id text, input_tokens integer)")
    con.commit()
    con.close()
    code, out = _run(capsys, "--db", str(old))
    assert code == 0 and M.OLD_DATABASE in out
    code, out = _run(capsys, "--db", str(tmp_path / "nowhere" / "ironjarvis.db"))
    assert code == 0 and "there is no database at" in out and "Nothing yet." in out
    assert not (tmp_path / "nowhere").exists(), "reading must not create a database"


def test_the_default_database_is_the_dev_home_never_the_installed_apps():
    home = os.path.join(os.path.expanduser("~"), ".ironjarvis")
    assert M.default_db(None) == os.path.join(home, "ironjarvis.db")
    assert M.default_db(os.path.join("somewhere", "else")) == os.path.join("somewhere", "else", "ironjarvis.db")
    assert "Iron Jarvis" not in M.default_db(None), "the packaged app's data is never the default"


# --------------------------------------------------------------------------- #
# Read-only
# --------------------------------------------------------------------------- #
def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_it_never_writes_to_the_database(filled, capsys):
    before = _digest(filled)
    siblings = sorted(p.name for p in filled.parent.iterdir())
    _run(capsys, "--db", str(filled), "--now", NOW_ARG, "--days", "30")
    assert _digest(filled) == before
    assert sorted(p.name for p in filled.parent.iterdir()) == siblings, "no journal or side file was left"
    con = M.open_read_only(str(filled))
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("update agentrun set input_tokens = 0")
    finally:
        con.close()
    assert _digest(filled) == before


def test_a_database_the_app_has_open_is_read_with_its_newest_rows(tmp_path, capsys):
    """The app's database keeps its newest rows in a write-ahead file: they
    are read too, and no new file appears beside it."""
    path = tmp_path / "ironjarvis.db"
    engine = open_db(str(path))
    try:
        with session_scope(engine) as db:
            _row(db, thread="chat_live", hours_ago=1, record=_record(400, cache_input=6_000), cache_read=3_000)
            db.commit()
        siblings = sorted(p.name for p in tmp_path.iterdir())
        assert f"{path.name}-wal" in siblings, "the row is still in the write-ahead file"
        code, out = _run(capsys, "--db", str(path), "--now", NOW_ARG)
        assert code == 0 and "Plain chat: 1 reply in 1 chat" in out
        assert sorted(p.name for p in tmp_path.iterdir()) == siblings
    finally:
        engine.dispose()


def test_a_database_whose_app_died_gets_no_side_file_and_the_output_says_what_is_not_counted(tmp_path, capsys):
    """A non-empty write-ahead file with NO ``-shm`` beside it: the app
    stopped before folding its newest changes in. A plain read-only open
    would create a ``-shm`` file there. Nothing is created, nothing is
    copied, and one line says the newest changes are not counted."""
    live = tmp_path / "live" / "ironjarvis.db"
    live.parent.mkdir()
    open_db(str(live)).dispose()                      # the tables are in the main file
    engine = open_db(str(live))
    try:
        with session_scope(engine) as db:
            _row(db, thread="chat_old", hours_ago=30, record=_record(100))
            db.commit()
        engine.dispose()                              # folded into the main file
        engine = open_db(str(live))
        with session_scope(engine) as db:
            _row(db, thread="chat_new", hours_ago=1, record=_record(700))
            db.commit()
        assert (live.parent / "ironjarvis.db-wal").stat().st_size > 0, "the newest row is in the write-ahead file"
        # What a crash leaves: the main file and its write-ahead file, no -shm.
        dead = tmp_path / "dead"
        dead.mkdir()
        shutil.copyfile(live, dead / "ironjarvis.db")
        shutil.copyfile(str(live) + "-wal", dead / "ironjarvis.db-wal")
    finally:
        engine.dispose()
    path = dead / "ironjarvis.db"
    assert M.wal_state(str(path)) == "left_behind"
    before = {p.name: _digest(p) for p in dead.iterdir()}
    assert sorted(before) == ["ironjarvis.db", "ironjarvis.db-wal"]

    code, out = _run(capsys, "--db", str(path), "--now", NOW_ARG, "--days", "30")
    assert code == 0
    assert {p.name: _digest(p) for p in dead.iterdir()} == before, "a file was created or changed beside it"
    assert not list(dead.glob("*-shm")), "a side file was left beside the database"
    assert out.splitlines()[0] == M.LEFT_BEHIND
    assert "not counted" in M.LEFT_BEHIND
    # The main file's reply is read; the one still in the write-ahead file is not.
    assert "Plain chat: 1 reply in 1 chat" in out
    head, table = _table(out, "Plain chat")
    assert _cell(head, table["1"], "messages") == "100"


def test_the_three_states_of_the_write_ahead_file(tmp_path):
    path = tmp_path / "x.db"
    path.write_bytes(b"")
    assert M.wal_state(str(path)) == "closed"
    (tmp_path / "x.db-wal").write_bytes(b"")
    assert M.wal_state(str(path)) == "closed", "an empty write-ahead file holds nothing"
    (tmp_path / "x.db-wal").write_bytes(b"frames")
    assert M.wal_state(str(path)) == "left_behind"
    (tmp_path / "x.db-shm").write_bytes(b"index")
    assert M.wal_state(str(path)) == "in_use"


def test_a_file_that_is_not_a_database_ends_in_one_sentence_and_a_non_zero_exit(tmp_path, capsys):
    junk = tmp_path / "notes.db"
    junk.write_text("this is not a database, it is a note " * 40, encoding="utf-8")
    code = M.main(["--db", str(junk), "--now", NOW_ARG])
    got = capsys.readouterr()
    assert code != 0
    assert got.err.strip() == M.NOT_A_DATABASE.format(path=str(junk))
    assert "Traceback" not in got.err + got.out and got.out == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.db"]
    # A real database that is not Iron Jarvis's (no table of replies and runs) says the same.
    other = tmp_path / "other.db"
    con = sqlite3.connect(str(other))
    con.execute("create table notes (id integer primary key, body text)")
    con.commit()
    con.close()
    code = M.main(["--db", str(other)])
    got = capsys.readouterr()
    assert code != 0 and got.err.strip() == M.NOT_A_DATABASE.format(path=str(other))


def test_a_bad_now_ends_in_one_sentence_and_a_non_zero_exit(filled, capsys):
    before = _digest(filled)
    code = M.main(["--db", str(filled), "--now", "next tuesday"])
    got = capsys.readouterr()
    assert code != 0
    assert got.err.strip() == M.BAD_NOW.format(value="next tuesday")
    assert "Traceback" not in got.err and got.out == ""
    assert _digest(filled) == before


def test_run_as_a_program_a_bad_file_and_a_bad_time_leave_no_traceback(filled, tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"\x00\x01 not a database " * 100)
    for argv in (["--db", str(junk)], ["--db", str(filled), "--now", "soon"]):
        done = subprocess.run([sys.executable, "-I", str(SCRIPT), *argv], capture_output=True, text=True,
                              timeout=60, check=False)
        assert done.returncode != 0, argv
        assert "Traceback" not in done.stderr and len(done.stderr.strip().splitlines()) == 1, done.stderr


def test_it_runs_alone_with_no_package_on_the_path(filled):
    """``python -I``: the script imports nothing of Iron Jarvis."""
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--db", str(filled), "--now", NOW_ARG],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "Plain chat: 6 replies in 3 chats" in done.stdout
    assert "iron_jarvis" not in SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n").split('"""', 2)[2]
