"""v1.418.0 (C4.6, C4.11): Build › Code's history that stays.

* C4.11 — a live session's view is served from memory: ten views open the
  log file at most ONCE (the seed), and the memory tail is exactly what the
  file holds (in order, nothing twice), however the writer thread batched it.
* C4.6 — ``GET …/events?before=`` pages back through a long log with the
  cursor the view answered (no gap, no repeat — even across text deltas the
  memory merged and the file did not); ``events_total`` says how many are
  older; the coding tool's OWN transcript (Claude Code, Codex, Pi, OpenCode)
  fills a conversation opened from disk and merges in what was done in a
  terminal — never the turns the log already has, never a write to the
  tool's files.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import history as H
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import EventLog, make

from ._build_app_helpers_v1409 import client, stop_all



class FakeDriver:
    """A coding tool with no process: what it is sent is recorded; the test
    says when its turn ends."""

    def __init__(self, svc: S.HostedService, live: S.LiveSession) -> None:
        self.svc = svc
        self.live = live
        self.sent: list[tuple[str, list[str]]] = []
        self.session_id = ""
        self.turn_open = False
        self.commands: list[dict[str, str]] = []
        self.steers: dict[str, str] = {}
        self.steer_read = None

    async def send_user(self, text, files):
        self.sent.append((text, list(files)))
        self.turn_open = True

    def unread_steers(self):
        return []

    def on_exit(self):
        pass

    async def interrupt(self):
        self.svc._emit(self.live, make("done", stop="interrupted"))


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def fake_tool(svc: S.HostedService, monkeypatch) -> dict[str, FakeDriver]:
    drivers: dict[str, FakeDriver] = {}

    async def ensure(live, row):
        if live.driver is None:
            live.driver = drivers.setdefault(live.sid, FakeDriver(svc, live))

    monkeypatch.setattr(svc, "_ensure_running", ensure)
    return drivers


async def until(pred, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while True:
        got = pred()
        if got:
            return got
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


def end_turn(svc, sid, stop="completed"):
    live = svc.live[sid]
    svc._emit(live, make("done", stop=stop))


# --------------------------------------------------------------------------- #
# C4.11: one read per view, not the whole log
# --------------------------------------------------------------------------- #
async def test_ten_views_of_a_live_session_open_the_log_at_most_once(app, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    # What the log held before this daemon (an older Iron Jarvis: no seq).
    d = S.session_dir(svc.home, row.id)
    d.mkdir(parents=True)
    old = [make("user", text=f"old {i}") for i in range(3)]
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in old), encoding="utf-8")
    await svc.start_turn(row.id, "new one", "t1")
    await until(lambda: svc.live[row.id].driver and svc.live[row.id].driver.sent)
    svc._emit(svc.live[row.id], make("text_done", item="a1", text="answer"))
    end_turn(svc, row.id)
    await until(lambda: svc.live[row.id].turn is None)
    await asyncio.to_thread(svc.live[row.id].log.flush)

    opened: list[str] = []
    real_open = builtins.open

    def counting(file, mode="r", *a, **k):
        if str(file).endswith("events.jsonl") and "r" in mode:
            opened.append(str(file))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", counting)
    views = [await svc.view(row.id) for _ in range(10)]
    monkeypatch.setattr(builtins, "open", real_open)
    assert len(opened) <= 1, opened
    texts = [(e["type"], e.get("text")) for e in views[-1]["events"]]
    assert texts[:3] == [("user", "old 0"), ("user", "old 1"), ("user", "old 2")]
    assert ("user", "new one") in texts and ("text_done", "answer") in texts
    assert [t for t in texts].count(("user", "new one")) == 1
    assert views[-1]["events_total"] == len(views[-1]["events"])
    # The view never sends the log's own bookkeeping.
    assert all("seq" not in e for v in views for e in v["events"])
    # And it is what the file says (memory == file, in order).
    from_file = H.file_view(d / "events.jsonl", d / "meta.json", 2000)["events"]
    assert [json.dumps(e, sort_keys=True) for e in from_file] == [
        json.dumps(e, sort_keys=True) for e in views[-1]["events"]
    ]
    await stop_all(app)


async def test_a_session_this_daemon_never_drove_is_read_from_the_file(app, work):
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    d = S.session_dir(svc.home, row.id)
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps(make("user", text="hello")) + "\n", encoding="utf-8")
    view = await svc.view(row.id)
    assert [e.get("text") for e in view["events"]] == ["hello"]
    assert view["older"] is None and view["events_total"] == 1


async def test_the_live_stream_carries_no_log_bookkeeping_and_kept_texts_stay_in_the_log(app, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    turn = await svc.start_turn(row.id, "go", "t1")
    await until(lambda: svc.live[row.id].driver and svc.live[row.id].driver.sent)
    ev = make("tool", item="x1", kind="command", name="Bash", title="Run x", input="x", status="done",
              output="short")
    ev[H.FULL_KEY] = {"output": "the whole long output"}
    svc._emit(svc.live[row.id], ev)
    assert H.FULL_KEY not in ev  # off the caller's dict, in place
    assert all("seq" not in e and H.FULL_KEY not in e for e in turn.events)
    await asyncio.to_thread(svc.live[row.id].log.flush)
    lines = [json.loads(x) for x in (S.session_dir(svc.home, row.id) / "events.jsonl").read_text("utf-8").splitlines()]
    tool = next(x for x in lines if x.get("type") == "tool")
    assert tool[H.LOG_FULL_KEY] == {"output": "the whole long output"} and isinstance(tool["seq"], int)
    got = await svc.item_text(row.id, "x1")
    assert "the whole long output" in json.dumps(got)
    end_turn(svc, row.id)
    await stop_all(app)


# --------------------------------------------------------------------------- #
# C4.6: Load earlier
# --------------------------------------------------------------------------- #
async def test_a_3000_event_log_pages_back_to_its_first_message(app, work):
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    d = S.session_dir(svc.home, row.id)
    d.mkdir(parents=True)
    evs = [make("user", text="the very first message")] + [make("notice", text=f"n{i}") for i in range(2999)]
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs), encoding="utf-8")
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert len(view["events"]) == 2000 and view["events_total"] == 3000
        seen = list(view["events"])
        cursor = view["older"]
        pages = 0
        while cursor:
            r = await c.get(f"/build/app/sessions/{row.id}/events", params={"before": cursor, "limit": 500})
            assert r.status_code == 200, r.text
            page = r.json()
            seen = page["events"] + seen
            cursor = page["older"]
            pages += 1
        assert pages == 2
        assert [e["text"] for e in seen] == [e["text"] for e in evs]
        bad = await c.get(f"/build/app/sessions/{row.id}/events", params={"before": "zzz"})
        assert bad.status_code == 400 and "load the session again" in bad.json()["detail"]


async def test_paging_never_repeats_or_skips_across_deltas_the_memory_merged(app, work, monkeypatch):
    """The writer flushes between deltas (so the FILE keeps several lines of
    one answer) while the memory merges them into one: an index cursor would
    land in the wrong place; the seq cursor does not."""
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    await svc.start_turn(row.id, "go", "t1")
    live = await until(lambda: svc.live.get(row.id) if svc.live[row.id].driver else None)
    for i in range(30):
        svc._emit(live, make("text", item="a", delta=f"w{i} "))
        await asyncio.to_thread(live.log.flush)
    for i in range(10):
        svc._emit(live, make("tool", item=f"t{i}", kind="read", name="Read", title=f"Read {i}", input="", status="done"))
    await asyncio.to_thread(live.log.flush)
    view = await svc.view(row.id, limit=5)
    assert [e.get("item") for e in view["events"]] == [f"t{i}" for i in range(5, 10)]
    assert view["older"].startswith("s")
    page = await svc.events_page(row.id, view["older"], 2000)
    items = [e.get("item") for e in page["events"] if e["type"] == "tool"]
    assert items == [f"t{i}" for i in range(5)]
    deltas = "".join(e["delta"] for e in page["events"] if e["type"] == "text")
    assert deltas == "".join(f"w{i} " for i in range(30))
    assert [e["type"] for e in page["events"]][:2] == ["user", "notice"]  # the turn's question and its start
    end_turn(svc, row.id)
    await stop_all(app)


def test_an_index_cursor_moves_back_by_what_the_log_trimmed_since():
    evs = [{"type": "notice", "text": str(i)} for i in range(100)]
    assert H._cut_of(evs, "i50:0", 0) == 50
    assert H._cut_of(evs, "i50:10", 30) == 30  # 20 more lines went since
    assert H._cut_of(evs, "s1", 0) == 100  # nothing carries a seq: all older


def test_a_seq_cursor_finds_its_own_event_even_after_the_clock_went_back():
    """Review (v1.418.0): a seq is the wall clock's microseconds, so a restart
    after the PC's clock moved back writes SMALLER seqs after larger ones.
    The cursor names one event: it cuts exactly there, never at the first
    larger seq (which would be an older run's event — a page of nothing)."""
    evs = [{"type": "notice", "text": str(i), "seq": s} for i, s in enumerate([500, 600, 100, 200])]
    assert H._cut_of(evs, "s100", 0) == 2
    assert H._cut_of(evs, "s600", 0) == 1
    # A seq no event carries (trimmed, or merged into another line): the first
    # larger one, as before.
    assert H._cut_of(evs, "s550", 0) == 1


# --------------------------------------------------------------------------- #
# C4.6: the coding tool's own transcript
# --------------------------------------------------------------------------- #
def write_claude_transcript(home: Path, folder: Path, hid: str) -> Path:
    proj = home / "projects" / "C--work"
    proj.mkdir(parents=True)
    path = proj / f"{hid}.jsonl"
    recs = [
        {"type": "user", "sessionId": hid, "cwd": str(folder), "timestamp": "2026-10-30T10:00:00.000Z",
         "message": {"role": "user", "content": "fix the failing test"}},
        {"type": "assistant", "sessionId": hid, "cwd": str(folder), "timestamp": "2026-10-30T10:00:05.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Let me run the tests."},
             {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "pytest -q"}},
         ]}},
        {"type": "user", "sessionId": hid, "cwd": str(folder), "timestamp": "2026-10-30T10:00:09.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_1", "content": "1 failed", "is_error": True},
         ]}},
        {"type": "assistant", "sessionId": hid, "cwd": str(folder), "timestamp": "2026-10-30T10:00:20.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed: the import was wrong."}]}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return path


async def test_a_conversation_opened_from_disk_shows_claude_codes_own_history(app, work, tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    hid = str(uuid.uuid4())
    path = write_claude_transcript(home, work, hid)
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    H.forget_transcripts()
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work),
                                                         "harness_session_id": hid})
        assert r.status_code == 201, r.text
        view = (await c.get(f"/build/app/sessions/{r.json()['id']}")).json()
    kinds = [(e["type"], e.get("text") or e.get("title")) for e in view["events"]]
    assert kinds == [
        ("user", "fix the failing test"),
        ("text_done", "Let me run the tests."),
        ("tool", "Run pytest -q"),
        ("text_done", "Fixed: the import was wrong."),
    ]
    tool = view["events"][2]
    assert tool["status"] == "failed" and tool["output"] == "1 failed" and tool["kind"] == "command"
    assert view["events_total"] == 4
    # Read-only: the tool's file is untouched.
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before
    await stop_all(app)


async def test_work_done_in_a_terminal_is_merged_by_time_and_the_logs_own_turns_are_not_drawn_twice(
    app, work, tmp_path, monkeypatch,
):
    home = tmp_path / "claude-home"
    hid = str(uuid.uuid4())
    path = write_claude_transcript(home, work, hid)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    H.forget_transcripts()
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), harness_session_id=hid)
    d = S.session_dir(svc.home, row.id)
    d.mkdir(parents=True)
    # The log's own turn covers the first exchange (10:00:00 .. 10:00:10) …
    log_events = [
        {"type": "user", "at": "2026-10-30T10:00:00.100000Z", "text": "fix the failing test", "turn": "t1"},
        {"type": "text_done", "at": "2026-10-30T10:00:05.100000Z", "item": "m1", "text": "Let me run the tests.", "turn": "t1"},
        {"type": "done", "at": "2026-10-30T10:00:10.000000Z", "stop": "completed", "turn": "t1"},
    ]
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log_events), encoding="utf-8")
    H.mark_outside(svc.home, row.id, "terminal")
    view = await svc.view(row.id)
    got = [(e["type"], e.get("text"), bool(e.get("imported"))) for e in view["events"]]
    # … and the answer written later, in the terminal, joins after it.
    assert got == [
        ("user", "fix the failing test", False),
        ("text_done", "Let me run the tests.", False),
        ("done", None, False),
        ("text_done", "Fixed: the import was wrong.", True),
    ]
    # A session that never went outside does not read the transcript at all.
    (d / "outside.json").unlink()
    view = await svc.view(row.id)
    assert [e["type"] for e in view["events"]] == ["user", "text_done", "done"]
    assert path.exists()
    await stop_all(app)


def test_codex_rollout_reads_as_view_events(tmp_path):
    hid = "0198c2a1-1111-7222-8333-444455556666"
    day = tmp_path / "sessions" / "2026" / "10" / "30"
    day.mkdir(parents=True)
    path = day / f"rollout-2026-10-30T10-00-00-{hid}.jsonl"
    recs = [
        {"timestamp": "2026-10-30T10:00:00Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list the files"}]}},
        {"timestamp": "2026-10-30T10:00:02Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                     "arguments": json.dumps({"command": ["ls"]})}},
        {"timestamp": "2026-10-30T10:00:03Z", "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "c1", "output": "a.py\nb.py"}},
        {"timestamp": "2026-10-30T10:00:04Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Two files."}]}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    H.forget_transcripts()
    evs = H.transcript_events("codex", str(tmp_path), "", hid)
    assert [e["type"] for e in evs] == ["user", "tool", "text_done"]
    assert evs[0]["text"] == "list the files" and evs[1]["kind"] == "command" and "a.py" in evs[1]["output"]
    assert evs[2]["text"] == "Two files."


def test_pi_session_file_reads_as_view_events(tmp_path):
    hid = "019a0000-aaaa-7bbb-8ccc-dddddddddddd"
    root = tmp_path / "sessions" / "--C--work--"
    root.mkdir(parents=True)
    path = root / f"2026-10-30T10-00-00-000Z_{hid}.jsonl"
    rows = [
        {"type": "session", "id": hid, "cwd": "C:/work", "timestamp": "2026-10-30T10:00:00Z", "version": 3},
        {"type": "message", "id": "m1", "timestamp": "2026-10-30T10:00:01Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "read the readme"}], "timestamp": 1793354401000}},
        {"type": "message", "id": "m2", "timestamp": "2026-10-30T10:00:02Z",
         "message": {"role": "assistant", "timestamp": 1793354402000, "content": [
             {"type": "toolCall", "id": "call_1", "name": "read", "arguments": {"path": "README.md"}}]}},
        {"type": "message", "id": "m3", "timestamp": "2026-10-30T10:00:03Z",
         "message": {"role": "toolResult", "toolCallId": "call_1", "toolName": "read", "isError": False,
                     "timestamp": 1793354403000, "content": [{"type": "text", "text": "# Hello"}]}},
        {"type": "message", "id": "m4", "timestamp": "2026-10-30T10:00:04Z",
         "message": {"role": "assistant", "timestamp": 1793354404000, "content": [{"type": "text", "text": "It says hello."}]}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    H.forget_transcripts()
    evs = H.transcript_events("pi", str(tmp_path), "", hid)
    assert [e["type"] for e in evs] == ["user", "tool", "text_done"]
    assert evs[1]["title"] == "Read README.md" and evs[1]["output"] == "# Hello" and evs[1]["status"] == "done"


def test_opencode_database_is_read_read_only(tmp_path):
    hid = "ses_" + "a" * 26
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE message (id text primary key, session_id text, time_created integer, "
                "time_updated integer, data text)")
    con.execute("CREATE TABLE part (id text primary key, message_id text, session_id text, time_created integer, "
                "time_updated integer, data text)")
    con.execute("INSERT INTO message VALUES ('m1', ?, 1793354401000, 1793354401000, ?)",
                (hid, json.dumps({"role": "user"})))
    con.execute("INSERT INTO message VALUES ('m2', ?, 1793354402000, 1793354402000, ?)",
                (hid, json.dumps({"role": "assistant"})))
    con.execute("INSERT INTO part VALUES ('p1', 'm1', ?, 1793354401000, 0, ?)",
                (hid, json.dumps({"type": "text", "text": "run the tests"})))
    con.execute("INSERT INTO part VALUES ('p2', 'm2', ?, 1793354402000, 0, ?)",
                (hid, json.dumps({"type": "tool", "tool": "bash", "callID": "c",
                                  "state": {"status": "completed", "input": {"command": "pytest"}, "output": "ok"}})))
    con.execute("INSERT INTO part VALUES ('p3', 'm2', ?, 1793354403000, 0, ?)",
                (hid, json.dumps({"type": "text", "text": "All green."})))
    con.commit()
    con.close()
    before = db.read_bytes()
    H.forget_transcripts()
    evs = H.transcript_events("opencode", str(tmp_path), "", hid)
    assert [e["type"] for e in evs] == ["user", "tool", "text_done"]
    assert evs[1]["kind"] == "command" and evs[1]["output"] == "ok"
    assert db.read_bytes() == before


def test_merge_keeps_the_logs_turns_and_drops_older_entries_when_the_view_is_cut():
    log_events = [
        {"type": "user", "at": "2026-10-30T12:00:00Z", "text": "b"},
        {"type": "done", "at": "2026-10-30T12:01:00Z", "stop": "completed"},
    ]
    tr = [
        {"type": "user", "at": "2026-10-30T11:00:00Z", "text": "a", "imported": True},
        {"type": "user", "at": "2026-10-30T12:00:30Z", "text": "b dup", "imported": True},
        {"type": "user", "at": "2026-10-30T13:00:00Z", "text": "c", "imported": True},
    ]
    assert [e["text"] for e in H.merge_transcript(log_events, tr) if e["type"] == "user"] == ["a", "b", "c"]
    assert [e["text"] for e in H.merge_transcript(log_events, tr, cut=True) if e["type"] == "user"] == ["b", "c"]
    assert H.merge_transcript([], tr) == tr


async def test_commands_carry_their_argument_hint(app):
    from iron_jarvis.hosted.claude import LAST_COMMANDS

    svc = S.service_for(app.state.platform)
    saved = dict(LAST_COMMANDS)
    try:
        LAST_COMMANDS["claude-code"] = [
            {"name": "review", "description": "Review a PR", "argument_hint": "<pr number>"},
            {"name": "plain", "description": "No hint"},
        ]
        rows = svc.commands_for("claude-code")
    finally:
        LAST_COMMANDS.clear()
        LAST_COMMANDS.update(saved)
    assert rows[0] == {"name": "review", "description": "Review a PR", "argument_hint": "<pr number>"}
    assert rows[1] == {"name": "plain", "description": "No hint"}


def test_a_plain_event_log_is_untouched_by_the_tail():
    assert issubclass(H.TailLog, EventLog)


@pytest.mark.parametrize("flushed", [False, True])
def test_the_seed_keeps_an_earlier_runs_events_even_when_the_clock_went_back(tmp_path, monkeypatch, flushed):
    """Review (v1.418.0): the seed tells this run's events from an earlier
    run's by the FIRST seq this run wrote, exactly — not "every seq at or
    above it", which after the PC's clock moved back hid the earlier run's
    newest events from the view (they were neither in memory nor seeded)."""
    home = tmp_path / "home"
    earlier = H.TailLog(home, "sess1")
    for i in range(3):
        earlier.append(make("notice", text=f"before {i}"))
    earlier.close()
    lines = [json.loads(x) for x in earlier.path.read_text("utf-8").splitlines()]
    assert all(isinstance(ev.get("seq"), int) for ev in lines)
    # The next run's clock reads EARLIER than every seq above.
    monkeypatch.setattr(H, "_LAST_SEQ", 0)
    monkeypatch.setattr(H.time, "time_ns", lambda: 1_000_000)  # 1 ms after 1970
    log_ = H.TailLog(home, "sess1")
    log_.append(make("notice", text="after"))
    if flushed:
        log_.flush()
    log_.seed()
    got = log_.served(50)
    assert got is not None
    assert [e["text"] for e in got["events"]] == ["before 0", "before 1", "before 2", "after"]
    log_.close()


def test_a_projects_pi_setting_naming_a_share_is_never_listed(tmp_path, monkeypatch):
    """Review (v1.418.0; the v1.379.0 rule): a repository's own
    ``.pi/settings.json`` can name a network sessionDir (two backslashes and
    a host). Listing it opens an SMB connection (a sign-in hash to that host)
    on every view of the session, so a network spelling is refused by its
    TEXT, before any stat, unless the session's own folder is on a share."""
    from iron_jarvis.hosted import pi as P

    agent = tmp_path / "agent"
    (agent / "sessions").mkdir(parents=True)
    folder = tmp_path / "repo"
    (folder / ".pi").mkdir(parents=True)
    share = "\\\\evil-host\\share\\pi"
    (folder / ".pi" / "settings.json").write_text(json.dumps({"sessionDir": share}), encoding="utf-8")
    monkeypatch.setattr(P, "agent_dir", lambda: agent)
    listed: list[str] = []
    real = P.conversation_files

    def spy(root):
        listed.append(str(root))
        return real(root)

    monkeypatch.setattr(P, "conversation_files", spy)
    H.forget_transcripts()
    hid = "019a0000-aaaa-7bbb-8ccc-dddddddddddd"
    assert H.transcript_events("pi", str(agent), str(folder), hid) == []
    assert not [r for r in listed if r.replace("/", "\\").startswith("\\\\")], listed


def test_repeated_views_list_the_tools_folders_once(tmp_path, monkeypatch):
    """Review (v1.418.0, C4.11): a session that lived outside the view reads
    the tool's transcript on every view; the FILE is cached by its signature,
    and finding it (a listing of every project folder) is done once too —
    later views only check the found file is still there."""
    from pathlib import Path as _P

    home = tmp_path / "claude-home"
    hid = str(uuid.uuid4())
    write_claude_transcript(home, tmp_path, hid)
    H.forget_transcripts()
    globs: list[str] = []
    real = _P.glob

    def spy(self, pattern, *a, **kw):
        globs.append(pattern)
        return real(self, pattern, *a, **kw)

    monkeypatch.setattr(_P, "glob", spy)
    for _ in range(10):
        assert len(H.transcript_events("claude-code", str(home), str(tmp_path), hid)) == 4
    assert len(globs) <= 1, globs
