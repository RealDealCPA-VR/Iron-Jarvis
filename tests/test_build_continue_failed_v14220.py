"""v1.422.0 (Wave 2 "Never lose work", B1): a coding reply that FAILED keeps
its progress, and "Continue from where it stopped" carries it on.

Before: a provider error at round 35 of a 40-minute reply ended it, its kept
conversation (``harness/reply_record``, v1.391.0) was DELETED, and the only
way on was Retry — the question again from the beginning, every file read
again and every command run again.

Now a reply that ended on an error keeps its rounds under a key of its own
(``build-failed:<turn>``), its line says ``canContinue``, and ``POST
/build/sessions/{id}/turn`` with ``continue_turn`` carries it on from them —
the restart resume's own path (its rounds, then the failed-reply note; the
question is NOT asked fresh). The boot's resume pass never sees such a
record (it is only ever carried on by the user's press); a newer message
makes it moot, and a refusal or an account's limit card never keeps one.

Driven through the REAL app factory, the real stream lane and the real tool
registry; only the model (the router) is a scripted double, and the
patience's waits (``harness.patience.SLEEP``) return at once.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import RunTranscript, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import patience as P
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.providers.adapters.base import ProviderError, ProviderRefusal, ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    messages_of,
    new_session,
    open_turn,
    until,
    wire,
)


def _write(cid: str, name: str, content: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="Write", arguments={"file_path": name, "content": content})]


def _overloaded():
    async def fail(script):
        raise ProviderError("anthropic API error 529: overloaded_error", status_code=529)

    return fail


def _raises(exc):
    async def fail(script):
        raise exc

    return fail


def _rows(app) -> dict[str, RunTranscript]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(RunTranscript)).all())
        for r in rows:
            db.expunge(r)
        return {r.run_id: r for r in rows}


def _writes(app) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == "write_file"]
        for r in rows:
            db.expunge(r)
        return rows


def _reply(app, sid: str, turn_id: str) -> dict:
    return next(m for m in messages_of(app, sid) if m["role"] == "assistant" and m.get("turnId") == turn_id)


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    """The patience ON (the suite turns it off for bare tests: conftest),
    its waits instant."""
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(P, "WAITS_S", P.DEFAULT_WAITS_S)
    monkeypatch.setattr(P, "SLEEP", fake_sleep)


@pytest.fixture()
def home(tmp_path) -> str:
    return str(tmp_path / "home")


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    harness = folder / ".ironjarvis" / "harness"
    harness.mkdir(parents=True)
    (harness / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


async def _fail_mid_reply(app, sid: str, turn_id: str = "bt-f") -> Script:
    """A reply that wrote one.txt and two.txt and then failed: its third
    model call was overloaded five times (the patience ran out)."""
    script = wire(app, Script([
        _write("c1", "one.txt", "1"), _write("c2", "two.txt", "2"),
        *[_overloaded() for _ in range(5)],
    ]))
    status, raw = await open_turn(app, sid, {"text": "make three files", "turn_id": turn_id}, timeout=60)
    assert status == 200 and "event: error" in raw, raw[-400:]
    return script


async def test_a_failed_reply_keeps_its_rounds_and_its_line_offers_continue(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    rows = _rows(app)
    assert RR.failed_run_id_for("bt-f") in rows, rows.keys()
    assert RR.run_id_for("bt-f") not in rows, "kept under the FAILED key, never the one the boot reads"
    assert not rows[RR.failed_run_id_for("bt-f")].waiting
    line = _reply(app, sid, "bt-f")
    assert line.get("canContinue") is True and line.get("error")
    # How long it kept trying is said on the line.
    assert "Iron Jarvis tried 5 times" in line["error"]


async def test_the_boot_resume_pass_never_carries_a_failed_reply_on(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    second = create_app(home)
    assert await build_routes.resume_after_restart(second.state.d) == []
    # ...and it did not throw the kept rounds away either.
    assert RR.has_failed(second.state.platform.engine, "bt-f")
    assert RR.kept(second.state.platform.engine) == []


async def test_continue_carries_it_on_from_its_kept_rounds(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    assert len(_writes(app)) == 2
    script = wire(app, Script([_write("c3", "three.txt", "3"), "All three made."]))
    status, raw = await open_turn(
        app, sid, {"text": "", "continue_turn": "bt-f", "turn_id": "bt-c"}, timeout=60,
    )
    assert status == 200 and "event: done" in raw, raw[-400:]
    first = script.calls[0]["messages"]
    # The earlier rounds' tool results are in the request (never run again)...
    tool_ids = [getattr(m, "tool_call_id", None) for m in first if getattr(m, "role", "") == "tool"]
    assert tool_ids[:2] == ["c1", "c2"]
    # ...the question is there ONCE (not asked fresh), and the note says why.
    asked = [m for m in first if getattr(m, "role", "") == "user" and m.content == "make three files"]
    assert len(asked) == 1
    assert first[-1].role == "user" and first[-1].content == RR.FAILED_RESUME_NOTE
    # one.txt and two.txt were not written again: only three.txt is new.
    assert len(_writes(app)) == 3
    msgs = messages_of(app, sid)
    question = next(m for m in msgs if m["role"] == "user" and m.get("turnId") == "bt-c")
    assert question.get("continuation") is True and question.get("continuedFrom") == "bt-f"
    assert "resumedFrom" not in question
    assert _reply(app, sid, "bt-c").get("continuedFrom") == "bt-f"
    assert _reply(app, sid, "bt-c")["content"] == "All three made."
    # The kept rounds were TAKEN by the continue and the finished reply keeps
    # nothing.
    assert await until(lambda: not _rows(app), 10), _rows(app).keys()


async def test_a_newer_message_makes_continue_moot(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    wire(app, Script(["Something else."]))
    status, raw = await open_turn(app, sid, {"text": "never mind, something else", "turn_id": "bt-n"})
    assert status == 200 and "event: done" in raw
    assert not RR.has_failed(app.state.platform.engine, "bt-f")
    status, raw = await open_turn(app, sid, {"text": "", "continue_turn": "bt-f", "turn_id": "bt-c2"})
    assert status == 409 and "can no longer be continued" in raw


async def test_a_refusal_keeps_nothing_and_offers_no_continue(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script([
        _write("c1", "one.txt", "1"),
        _raises(ProviderRefusal("declined", category="cyber")),
    ]))
    status, raw = await open_turn(app, sid, {"text": "make it", "turn_id": "bt-r"}, timeout=60)
    assert status == 200 and "event: error" in raw
    assert not _rows(app)
    assert "canContinue" not in _reply(app, sid, "bt-r")


async def test_a_failure_before_any_round_offers_no_continue(home, work):
    """Nothing past the question was kept: Continue would be Retry."""
    app = create_app(home)
    sid = new_session(app, work)["id"]
    wire(app, Script([_overloaded() for _ in range(5)]))
    status, raw = await open_turn(app, sid, {"text": "make it", "turn_id": "bt-0"}, timeout=60)
    assert status == 200 and "event: error" in raw
    assert not _rows(app)
    assert "canContinue" not in _reply(app, sid, "bt-0")


async def test_a_failed_record_expires_like_the_others(home, work, monkeypatch):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    # v1.441.0 (integration): prune's cutoff is strict (``updated_at <
    # now - days``); with days=0 a record written in the SAME 15.6 ms tick as
    # the prune was kept (red now and then on a loaded run). The prune comes
    # later than the write, as a real one does: step its clock.
    later = RR.utcnow() + timedelta(seconds=1)
    monkeypatch.setattr(RR, "utcnow", lambda: later)
    assert RR.prune(app.state.platform.engine, days=0) >= 1
    assert not RR.has_failed(app.state.platform.engine, "bt-f")
