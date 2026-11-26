"""Next leaps 28-a (R2.17): the one-time backfill, so someone who changes
nothing sees a record on the first day (CLAUDE.md, "Shipping the mechanism is
not shipping the feature").

A scratch database holds what an older app left: a Chat › Code session that
was MERGED (two replies with a finish record, one reply that changed
nothing), one whose copy was DISCARDED, one still open, an attempts card whose
Keep was pressed, and a Build › Code session whose log has a reply with the
tool's own file step and a finished check, beside a reply that only read.

Pinned: each fact becomes ONE row marked ``backfill``, settled by the
session's own facts (merged = kept, discarded = thrown, open = not decided);
the attempts card gives its kept and dropped attempts; the Build › Code reply
gets its check; a reply that changed nothing is no row; the backfill runs ONCE
per database (a second start writes nothing, and a ref is never counted
twice even when it runs again after a cut-off); and it runs off the loop at
the daemon's start, through the real lifespan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlmodel import create_engine, select

from iron_jarvis.core.db import init_db, session_scope
from iron_jarvis.core.models import BUILD_SURFACE, ChatThreadRecord
from iron_jarvis.harness import scoreboard as SB
from iron_jarvis.harness.attempts import LINE_KEY
from iron_jarvis.hosted.events import session_dir
from iron_jarvis.hosted.models import HostedSessionRecord

REPO = "C:/code/shop"
#: A file outside the folder, absolute on this OS (a coding tool's own plan file).
OUTSIDE = "C:/Users/me/.codex/plan.md" if os.name == "nt" else "/home/me/.codex/plan.md"
FIN = {"tests": "passed", "command": "pytest", "removed": 0, "kept": 0, "points": 0, "unmet": 0, "after": ""}


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'ij.db').as_posix()}")
    init_db(eng)
    return eng


def _chat_session(engine, tid: str, *, git: "dict | None", messages: list[dict]) -> None:
    setup = {"workspace_dir": f"{REPO}.ij-worktrees/{tid}", "provider": "anthropic", "model": "claude-x"}
    if git is not None:
        setup["git"] = {"branch": f"ij/{tid}", "base": "main", "base_commit": "abc123", "repo": REPO,
                        "checkout": REPO, "worktree": f"{REPO}.ij-worktrees/{tid}", **git}
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(id=tid, title=tid, surface=BUILD_SURFACE, messages_json=json.dumps(messages),
                                setup_json=json.dumps(setup)))
        db.commit()


def _reply(turn: str, *, finish: "dict | None" = FIN, **extra) -> dict:
    out = {"role": "assistant", "turnId": turn, "content": "done",
           "route": {"provider": "anthropic", "model": "claude-x"}, **extra}
    if finish is not None:
        out["finish"] = finish
    return out


def _old_world(engine, home: Path) -> None:
    _chat_session(engine, "merged", git={"merged_at": "2026-11-01T10:00:00"}, messages=[
        {"role": "user", "content": "a"}, _reply("m1"), {"role": "user", "content": "b"},
        _reply("m2", finish={**FIN, "tests": "failed"}), _reply("m3", finish=None),
    ])
    _chat_session(engine, "gone", git={"removed_at": "2026-11-02T10:00:00"}, messages=[_reply("g1")])
    _chat_session(engine, "open", git={}, messages=[_reply("o1", finish={"off": "folder"})])
    card = {"items": [
        {"id": "try1", "label": "A", "provider": "openai", "model": "model-a", "state": "done", "files": ["x.py"],
         "kept": True, "finish": FIN},
        {"id": "try2", "label": "B", "provider": "openai", "model": "model-b", "state": "done", "files": ["x.py"],
         "trashed": True},
        {"id": "try3", "label": "C", "provider": "openai", "model": "model-c", "state": "failed"},
    ], "kept": "try1", "kept_at": "2026-11-03T10:00:00", "task": "t", "base": "ij/tries"}
    _chat_session(engine, "tries", git={}, messages=[{"role": "assistant", "turnId": "a1", "content": "x",
                                                      LINE_KEY: card}])
    # Build › Code: a folder session with one reply that wrote a file (and was
    # checked) and one that only read.
    with session_scope(engine) as db:
        db.add(HostedSessionRecord(id="app_1", harness="codex", folder=REPO, model="gpt-x"))
        db.commit()
    log = Path(session_dir(home, "app_1"))
    log.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "user", "turn": "u1", "text": "add it"},
        {"type": "tool", "turn": "u1", "kind": "write", "status": "done", "path": f"{REPO}/a.py"},
        {"type": "done", "turn": "u1", "stop": "completed"},
        {"type": "proof", "for_turn": "u1", "state": "done", "tests": "passed"},
        {"type": "user", "turn": "u2", "text": "what is it?"},
        {"type": "tool", "turn": "u2", "kind": "read", "status": "done", "path": f"{REPO}/a.py"},
        {"type": "done", "turn": "u2", "stop": "completed"},
    ]
    (log / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _rows(engine) -> dict[str, tuple]:
    with session_scope(engine) as db:
        got = db.exec(select(SB.CodingOutcome)).all()
        return {r.ref: (r.unit, r.tool, r.model, r.checked, r.verdict, r.how, r.source, r.folder_key) for r in got}


def test_the_old_facts_become_one_row_each_settled_by_the_sessions_own_facts(engine, tmp_path):
    home = tmp_path / "home"
    _old_world(engine, home)
    n = SB.backfill(engine, home)
    key = SB.folder_key(REPO)
    B = SB.SOURCE_BACKFILL
    assert _rows(engine) == {
        "merged:m1": ("reply", "jarvis", "claude-x", "passed", "kept", "merged", B, key),
        "merged:m2": ("reply", "jarvis", "claude-x", "failed", "kept", "merged", B, key),
        "gone:g1": ("reply", "jarvis", "claude-x", "passed", "thrown", "discarded", B, key),
        "open:o1": ("reply", "jarvis", "claude-x", "", "", "", B, key),
        "tries:a1:try1": ("attempt", "jarvis", "model-a", "passed", "kept", "attempt_kept", B, key),
        "tries:a1:try2": ("attempt", "jarvis", "model-b", "", "thrown", "attempt_dropped", B, key),
        "app_1:u1": ("reply", "codex", "gpt-x", "passed", "", "", B, key),
    }
    assert n == 7
    # The menu reads it at once.
    jarvis = next(r for r in SB.summary(engine, REPO)["rows"] if r["tool"] == "jarvis" and r["model"] == "claude-x")
    assert jarvis["words"] == "In this folder: kept 2 of 3 (too few to judge), passed its checks 2 of 3; 1 not decided yet"


def test_the_backfill_runs_once_per_database_and_never_counts_a_piece_twice(engine, tmp_path):
    home = tmp_path / "home"
    _old_world(engine, home)
    first = SB.backfill(engine, home)
    assert first == 7
    assert SB.backfill(engine, home) == -1, "the second start writes nothing"
    # A run cut short before it marked itself done, run again: still one row
    # per piece.
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("DELETE FROM _ironjarvis_meta WHERE key=:k"), {"k": SB.BACKFILL_KEY})
    assert SB.backfill(engine, home) == 0
    assert len(_rows(engine)) == 7


def test_a_build_code_reply_keeps_the_tool_its_message_went_to_and_only_its_folders_writes_count(engine, tmp_path):
    """Review: a session that switched tools keeps each old reply with the
    tool that wrote it (its user event's ``tool``, v1.545.0), with the model
    that tool had last in the session (its parked record); a tool with no
    model on record is not counted rather than credited to a guess; a reply
    whose only write lies outside the folder is no piece."""
    from iron_jarvis.hosted import tool_switch as TS

    home = tmp_path / "home"
    with session_scope(engine) as db:
        db.add(HostedSessionRecord(id="app_2", harness="codex", folder=REPO, model="gpt-x"))
        db.commit()
    log = Path(session_dir(home, "app_2"))
    log.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "user", "turn": "u1", "text": "add it", "tool": "claude-code"},
        {"type": "tool", "turn": "u1", "kind": "edit", "status": "done", "path": f"{REPO}/a.py"},
        {"type": "user", "turn": "u2", "text": "and this", "tool": "codex"},
        {"type": "tool", "turn": "u2", "kind": "write", "status": "done", "path": f"{REPO}/b.py"},
        {"type": "user", "turn": "u3", "text": "a plan", "tool": "codex"},
        {"type": "tool", "turn": "u3", "kind": "write", "status": "done", "path": OUTSIDE},
        {"type": "user", "turn": "u4", "text": "try pi", "tool": "pi"},
        {"type": "tool", "turn": "u4", "kind": "write", "status": "done", "path": f"{REPO}/c.py"},
        {"type": "user", "turn": "u5", "text": "older, no tool named"},
        {"type": "tool", "turn": "u5", "kind": "write", "status": "done", "path": f"{REPO}/d.py"},
    ]
    (log / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (log / TS.FILE).write_text(json.dumps({"parked": {
        "claude-code": {"hid": "h-1", "model": "claude-opus-5-5"},
    }}), encoding="utf-8")
    assert SB.backfill(engine, home) == 3
    got = {ref: row[1:3] for ref, row in _rows(engine).items()}
    assert got == {
        "app_2:u1": ("claude-code", "claude-opus-5-5"),
        "app_2:u2": ("codex", "gpt-x"),
        "app_2:u5": ("codex", "gpt-x"),
    }


def test_the_backfill_runs_at_the_daemons_start_off_the_loop(tmp_path, monkeypatch):
    import asyncio

    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    seen: list[bool] = []
    real = SB.backfill

    def watched(engine, home=None):
        # On the event loop's own thread a loop is running; in the worker
        # thread ``asyncio.to_thread`` uses, none is.
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return real(engine, home)

    monkeypatch.setattr(SB, "backfill", watched)
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    _chat_session(engine, "merged", git={"merged_at": "2026-11-01T10:00:00"}, messages=[_reply("m1")])
    with TestClient(app):
        import time

        deadline = time.monotonic() + 60
        while not _rows(engine) and time.monotonic() < deadline:
            time.sleep(0.05)
    assert seen == [False]
    assert _rows(engine)["merged:m1"][4:6] == ("kept", "merged")
