"""v1.418.0 (C3.6): a coding reply's per-round writes cost what the round
added, not what the whole reply holds.

Before: every save of a coding session's kept conversation
(``harness/reply_record``) re-encoded the WHOLE list and rewrote its row (up
to 4 M chars), twice a round; the turn log (``daemon/durable_turns``) parsed
and re-serialised the merged text frame on EVERY token (O(L²) on the loop),
and dumped the whole ``frames_json`` on the loop.

Now:

* the kept list is written WHOLE once, then each save APPENDS only the
  messages added since (encoded alone, joined in SQL); anything that would
  make an append wrong — an earlier message changed in place, the row moved
  or changed under it, the cap — falls back to the whole write, so the row is
  always byte-identical to what a whole write would have stored. Both of a
  round's saves stay (the top of the round keeps the results a crash during
  the next model call must not lose; the one before the tools keeps the
  call a crash mid-tool must answer — ``tests/test_build_resume_v1391.py``
  pins both), so a 40-round reply makes ONE whole write and ≤ 2 small
  appends a round;
* the turn log keeps a growing token/thinking frame as a list of escaped
  chunks and joins them only when the frames are read (a persist, a rejoin):
  a plain token makes no ``json.loads`` at all, and no parse ever sees the
  stored text;
* the record's ``json.dumps`` runs on a worker thread, writes still in order.

Pins COUNT calls and compare bytes; none reads a clock.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import RunTranscript
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall

FIXTURES = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures"


# --------------------------------------------------------------------------- #
# the kept conversation: one whole write, then appends
# --------------------------------------------------------------------------- #


class _Count:
    def __init__(self, monkeypatch):
        self.whole = 0
        self.appends = 0
        self.encoded: list[int] = []
        real_write, real_append, real_encode = RR._write, RR._append, RR._encode_rows

        def write(*a, **kw):
            self.whole += 1
            return real_write(*a, **kw)

        def append(*a, **kw):
            self.appends += 1
            return real_append(*a, **kw)

        def encode(snaps, real):
            self.encoded.append(len(snaps))
            return real_encode(snaps, real)

        monkeypatch.setattr(RR, "_write", write)
        monkeypatch.setattr(RR, "_append", append)
        monkeypatch.setattr(RR, "_encode_rows", encode)


def _stored(engine, turn: str) -> str:
    with session_scope(engine) as db:
        row = db.get(RunTranscript, RR.run_id_for(turn))
        return row.messages_json if row is not None else ""


def _whole(msgs, head) -> str:
    """What one whole write of ``msgs`` stores (the reference)."""
    from iron_jarvis.agents import transcript

    snaps = [RR._Snap(m) for m in msgs[head - 1:]]
    return RR._fit(RR._encode_rows(snaps, {}), transcript.MAX_TRANSCRIPT_CHARS)


def _round(n: int) -> tuple[LLMMessage, LLMMessage]:
    call = ToolCall(id=f"c{n}", name="read_file", arguments={"path": f"f{n}.py"})
    return (LLMMessage(role="assistant", content=f"step {n}", tool_calls=[call]),
            LLMMessage(role="tool", tool_call_id=f"c{n}", name="read_file", content=f"body of f{n} " * 20))


async def _forty_rounds(rec, msgs, head):
    await rec.save(msgs, head)  # the top of round 1: the question
    for n in range(40):
        a, t = _round(n)
        msgs.append(a)
        await rec.save(msgs, head)  # before the tools run
        msgs.append(t)
        await rec.save(msgs, head)  # the top of the next round: results in


async def test_a_forty_round_reply_makes_one_whole_write_and_small_appends(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    count = _Count(monkeypatch)
    rec = RR.ReplyRecord(engine, "s1", "t40")
    msgs = [LLMMessage(role="user", content="earlier"), LLMMessage(role="user", content="read every file")]
    await _forty_rounds(rec, msgs, 2)
    assert count.whole == 1, f"{count.whole} whole rewrites of the kept list"
    assert count.whole + count.appends <= 2 * 40 + 1
    # Each message is encoded ONCE across the reply (the question + 80).
    assert sum(count.encoded) == 1 + 80, count.encoded
    assert _stored(engine, "t40") == _whole(msgs, 2), "an append stored other bytes than a whole write"
    assert rec.enabled


async def test_a_message_changed_in_place_is_written_whole_again(tmp_path, monkeypatch):
    """``trim_tool_images`` changes an EARLIER tool result in place (its
    picture goes, a note is added): an append would keep the old text, so
    the save writes the whole list again."""
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    count = _Count(monkeypatch)
    rec = RR.ReplyRecord(engine, "s1", "t-img")
    msgs = [LLMMessage(role="user", content="look")]
    await rec.save(msgs, 1)
    a, t = _round(1)
    t.images = [{"data_b64": "AAAA", "media_type": "image/png"}]
    msgs += [a, t]
    await rec.save(msgs, 1)
    t.images = []
    t.content = str(t.content) + " [picture removed]"
    a2, t2 = _round(2)
    msgs.append(a2)
    await rec.save(msgs, 1)
    assert count.whole == 2
    assert _stored(engine, "t-img") == _whole(msgs, 1)


async def test_a_row_changed_under_the_record_is_written_whole_again(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    count = _Count(monkeypatch)
    rec = RR.ReplyRecord(engine, "s1", "t-moved")
    msgs = [LLMMessage(role="user", content="go")]
    await rec.save(msgs, 1)
    with session_scope(engine) as db:  # someone else wrote the row meanwhile
        row = db.get(RunTranscript, RR.run_id_for("t-moved"))
        row.messages_json = '[{"role": "user", "content": "other"}]'
        db.add(row)
        db.commit()
    msgs.extend(_round(1))
    await rec.save(msgs, 1)
    assert count.appends == 1 and count.whole == 2
    assert _stored(engine, "t-moved") == _whole(msgs, 1)


async def test_an_append_past_the_cap_goes_through_the_cap_rule(tmp_path, monkeypatch):
    from iron_jarvis.agents import transcript

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    rec = RR.ReplyRecord(engine, "s1", "t-cap")
    msgs = [LLMMessage(role="user", content="go")]
    await rec.save(msgs, 1)
    for n in range(4):
        msgs.extend(_round(n))
        await rec.save(msgs, 1)
    whole = len(_stored(engine, "t-cap"))
    monkeypatch.setattr(transcript, "MAX_TRANSCRIPT_CHARS", whole + 200)
    for n in range(4, 8):
        msgs.extend(_round(n))
        await rec.save(msgs, 1)
    kept = _stored(engine, "t-cap")
    assert len(kept) <= whole + 200
    assert kept == RR._fit(RR._encode_rows([RR._Snap(m) for m in msgs], {}), whole + 200)
    assert "earliest" in json.loads(kept)[1]["content"], "the left-out note"


async def test_the_append_keeps_the_real_tool_name_beside_the_models(tmp_path):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    rec = RR.ReplyRecord(engine, "s1", "t-real")
    msgs = [LLMMessage(role="user", content="go")]
    await rec.save(msgs, 1)
    call = ToolCall(id="w1", name="Write", arguments={"file_path": "a.txt", "content": "x"})
    msgs.append(LLMMessage(role="assistant", content="", tool_calls=[call]))
    rec.note_calls([call], [ToolCall(id="w1", name="write_file", arguments={})])
    await rec.save(msgs, 1)
    kept = json.loads(_stored(engine, "t-real"))
    assert kept[1]["tool_calls"][0] == {"id": "w1", "name": "Write", "arguments": call.arguments,
                                        "tool": "write_file"}


async def test_a_real_coding_reply_writes_its_list_whole_once(tmp_path, monkeypatch):
    """Through the REAL stream lane of a coding session (only the model is
    scripted): six tool rounds, one whole write, the rest appends."""
    from tests.test_build_resume_v1391 import Hold, _kept_list, _write
    from tests.test_build_sessions_v1367 import Script, new_session, open_turn, wire

    work = tmp_path / "work"
    (work / ".ironjarvis" / "harness").mkdir(parents=True)
    (work / ".ironjarvis" / "harness" / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    sid = new_session(app, work)["id"]
    count = _Count(monkeypatch)
    hold = Hold()
    wire(app, Script([_write(f"c{n}", f"f{n}.txt", str(n)) for n in range(6)] + [hold]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "make six files", "turn_id": "rw-1"},
                                             drop=drop, timeout=60))
    await asyncio.wait_for(hold.reached.wait(), 30)
    kept = _kept_list(app, "rw-1")
    assert [r["role"] for r in kept] == ["user"] + ["assistant", "tool"] * 6
    assert count.whole == 1, count.whole
    assert count.appends <= 2 * 7
    await app.state.platform.turn_logs.shutdown()
    drop.set()
    hold.gate.set()
    await asyncio.wait_for(follower, 30)


# --------------------------------------------------------------------------- #
# the turn log: tokens are joined, never parsed again
# --------------------------------------------------------------------------- #


class _LoadsSpy:
    def __init__(self, monkeypatch):
        self.calls: list[int] = []
        real = json.loads

        def loads(s, *a, **kw):
            self.calls.append(len(s))
            return real(s, *a, **kw)

        import types

        spy = types.SimpleNamespace(**{k: getattr(json, k) for k in dir(json) if not k.startswith("__")})
        spy.loads = loads
        monkeypatch.setattr(dt, "json", spy)


def test_a_plain_token_makes_no_json_loads(monkeypatch):
    lg = dt.TurnLog("t")
    lg.add(dt.sse("round", {"round": 1}))
    spy = _LoadsSpy(monkeypatch)
    for n in range(500):
        lg.add(dt.sse("token", {"text": f"word{n} "}))
        assert lg.position() == f"1:{sum(len(f'word{k} ') for k in range(n + 1))}"
    assert spy.calls == [], f"{len(spy.calls)} json.loads on the per-token path"
    assert json.loads(lg.frames[1][1])["text"] == "".join(f"word{n} " for n in range(500))


def test_no_parse_ever_sees_the_stored_text(monkeypatch):
    """A token that needs escaping (a newline, a quote, a non-ASCII letter,
    an emoji) is parsed ALONE — never the growing frame."""
    lg = dt.TurnLog("t")
    spy = _LoadsSpy(monkeypatch)
    pieces = ["line one\n", 'a "quote" ', "café ", "😀 ", "tab\there ", "back\\slash "] * 50
    biggest = max(len(dt.sse("token", {"text": p})) for p in pieces)
    for p in pieces:
        lg.add(dt.sse("token", {"text": p}))
    assert spy.calls and max(spy.calls) <= biggest
    monkeypatch.undo()
    assert json.loads(lg.frames[0][1]) == {"text": "".join(pieces)}
    assert lg.position() == f"0:{len(''.join(pieces))}"


def test_a_reader_while_the_text_still_grows_sees_every_word():
    """Every reader of the frames (a rejoin's snapshot, a resume, the record)
    gets the growing text frame JOINED — while it is still the last frame."""
    lg = dt.TurnLog("t")
    lg.add(dt.sse("round", {"round": 1}))
    for w in ("one ", "two ", "three"):
        lg.add(dt.sse("token", {"text": w}))
    whole = json.dumps({"text": "one two three"})
    assert lg.snapshot().endswith(dt.sse("token", whole))
    assert lg.frames_after("1:4") == [("1:13", "token", json.dumps({"text": "two three"}))]
    assert lg._row_values()["frames_json"][-1] == ["token", whole]
    lg.add(dt.sse("token", {"text": "!"}))
    assert lg.frames[-1] == ["token", json.dumps({"text": "one two three!"})]


def _old_store(frames: list, chars: list, event: str, data: str) -> None:
    """The v1.417.0 ``TurnLog._store`` (merge part), the byte reference."""
    if event in dt._MERGEABLE and frames and frames[-1][0] == event:
        try:
            prev = json.loads(frames[-1][1])
            more = json.loads(data)
            if chars[0] + len(more.get("text", "")) > dt.MAX_STORED_CHARS:
                return
            prev["text"] = (prev.get("text") or "") + (more.get("text") or "")
            frames[-1][1] = json.dumps(prev)
            chars[0] += len(more.get("text") or "")
            return
        except (ValueError, AttributeError, TypeError):
            pass
    if chars[0] + len(data) > dt.MAX_STORED_CHARS and event in dt._MERGEABLE:
        return
    frames.append([event, data])
    chars[0] += len(data)


@pytest.mark.parametrize("cap", [dt.MAX_STORED_CHARS, 300])
def test_the_frames_are_byte_identical_to_the_old_store(monkeypatch, cap):
    monkeypatch.setattr(dt, "MAX_STORED_CHARS", cap)
    chunks = [
        ("phase", {"phase": "recalling"}), ("thinking", {"text": "Let me "}), ("thinking", {"text": "look."}),
        ("token", {"text": "Reading "}), ("token", {"text": "naïve \"x\"\n"}), ("token", {"text": "😀"}),
        ("token", {"text": "more", "extra": 1}), ("token", {"text": " after"}),
        ("tool_call", {"id": "a", "status": "running"}), ("token", "not json"), ("token", {"text": "z"}),
        ("token", {"text": "y" * 120}), ("token", {"text": "w" * 120}), ("thinking", {"text": "t"}),
        ("token", {"no_text": True}), ("token", {"text": "q"}), ("token", {"text": ""}), ("token", {"text": "e"}),
    ]
    lg = dt.TurnLog("t")
    ref: list = []
    chars = [0]
    for event, data in chunks:
        payload = data if isinstance(data, str) else json.dumps(data)
        lg.add(dt.sse(event, data))
        _old_store(ref, chars, event, payload)
        assert lg.frames == ref, (event, data)
        assert lg.position() == dt.position_of(ref, len(ref) - 1)
        for i in range(len(ref)):
            for c in (0, 1, 3):
                after = f"{i}:{c}"
                old = dt.TurnLog("o")
                old.frames = [list(f) for f in ref]
                assert lg.frames_after(after) == old.frames_after(after), after


@pytest.mark.parametrize("name", ["build-resume-v1384.json", "build-resume-kinds-v1384.json"])
def test_the_recorded_logs_resume_exactly_as_before(name):
    """The dashboard's recorded turns (made by the old store): fed frame by
    frame, the new log stands at every recorded position, stores the same
    frames and answers every cut the same."""
    fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    lg = dt.TurnLog("fixture")
    for pos, event, data in fx["live"]:
        lg.add(dt.sse(event, data))
        assert lg.position() == pos
    assert lg.frames == [list(f) for f in fx["stored"]]
    lg.status = "done"
    for n in range(1, len(fx["live"]) + 1):
        got = [list(t) for t in lg.frames_after(fx["live"][n - 1][0])]
        assert got == fx["resumes"][str(n)], n


# --------------------------------------------------------------------------- #
# the record is serialised off the loop, in order
# --------------------------------------------------------------------------- #


async def test_the_record_is_serialised_on_a_worker_thread(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    loop_thread = threading.get_ident()
    dumped_on: list[int] = []
    real_dumps = json.dumps

    def dumps(obj, *a, **kw):
        if isinstance(obj, list) and obj and isinstance(obj[0], list):
            dumped_on.append(threading.get_ident())
        return real_dumps(obj, *a, **kw)

    import types

    spy = types.SimpleNamespace(**{k: getattr(json, k) for k in dir(json) if not k.startswith("__")})
    spy.dumps = dumps
    monkeypatch.setattr(dt, "json", spy)
    lg = dt.TurnLog("rec")
    lg.add(dt.sse("token", {"text": "hello"}))
    lg.maybe_persist(engine)
    await lg._persisting
    lg.add(dt.sse("token", {"text": " world"}))
    await lg.persist_now(engine)
    assert dumped_on and loop_thread not in dumped_on, "frames_json was dumped on the loop"
    from iron_jarvis.core.models import ChatTurnRecord

    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, "rec")
        assert json.loads(row.frames_json) == [["token", '{"text": "hello world"}']]


async def test_a_persist_holds_the_frames_as_they_were_when_it_was_asked(tmp_path, monkeypatch):
    """The snapshot handed to the worker is taken on the loop: a token added
    while the write runs is not half in it, and lands in the next write."""
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    gate = threading.Event()
    real = dt._write_record

    def slow(*a, **kw):
        gate.wait(10)
        return real(*a, **kw)

    monkeypatch.setattr(dt, "_write_record", slow)
    lg = dt.TurnLog("snap")
    lg.add(dt.sse("token", {"text": "one"}))
    lg.maybe_persist(engine)
    lg.add(dt.sse("token", {"text": " two"}))
    assert json.loads(lg.frames[0][1])["text"] == "one two"  # a reader joins the frame meanwhile
    gate.set()
    await lg._persisting
    from iron_jarvis.core.models import ChatTurnRecord

    with session_scope(engine) as db:
        assert json.loads(db.get(ChatTurnRecord, "snap").frames_json) == [["token", '{"text": "one"}']]
    await lg.persist_now(engine)
    with session_scope(engine) as db:
        assert json.loads(db.get(ChatTurnRecord, "snap").frames_json) == [["token", '{"text": "one two"}']]


async def test_a_same_length_change_in_place_is_written_whole_again(tmp_path, monkeypatch):
    """Review (v1.418.0): ``attach_check`` rewrites an EARLIER edit's tool
    message in place (its content gains the checker's findings, then is cut
    to the harness cap) — and a message already over the cap is cut to the
    SAME length again. The fingerprint must see a changed text of the same
    length, or the append keeps the old words while a whole write would
    store the new ones."""
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    count = _Count(monkeypatch)
    rec = RR.ReplyRecord(engine, "s1", "t-same")
    msgs = [LLMMessage(role="user", content="edit it")]
    await rec.save(msgs, 1)
    a, t = _round(1)
    msgs += [a, t]
    await rec.save(msgs, 1)
    before = str(t.content)
    t.content = before[:-5] + "CHECK"  # same length, other words
    assert len(t.content) == len(before)
    a2, _t2 = _round(2)
    msgs.append(a2)
    await rec.save(msgs, 1)
    assert count.whole == 2, (count.whole, count.appends)
    assert _stored(engine, "t-same") == _whole(msgs, 1)
