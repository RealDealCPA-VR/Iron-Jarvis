"""v1.441.0 (lane g4): Grok's and Antigravity's OWN saved conversations in the
Code view's history, and Pi's typed commands in its imported transcript.

The v1.418.0 (C4.6) rules hold for the two new readers: read-only, masked,
bounded, cached by the file's signature, and only for a session whose log is
empty or that lived outside the view (``history.went_outside``). Grok keeps a
conversation in ``<GROK_HOME>/sessions/<folder>/<id>/chat_history.jsonl``
with no times (they come from the same folder's ``events.jsonl``, which also
names every MCP server WITH its address — never read past the turn and tool
times). Antigravity keeps one in ``brain/<id>/.system_generated/logs/
transcript.jsonl``. Fixtures: ``tests/fixtures/build_app_v14410`` (shapes
measured on grok 1.0.70 and agy 1.5.2; words and paths made up).
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import history as H

from ._build_app_helpers_v1409 import client, stop_all

FIX = Path(__file__).parent / "fixtures" / "build_app_v14410"


def _grok_home(tmp_path: Path, work: Path, hid: str) -> tuple[Path, Path]:
    home = tmp_path / "grok-home"
    sess = home / "sessions" / "C%3A%5Cwork" / hid
    sess.mkdir(parents=True)
    shutil.copy(FIX / "grok_chat_history.jsonl", sess / "chat_history.jsonl")
    shutil.copy(FIX / "grok_events.jsonl", sess / "events.jsonl")
    (sess / "summary.json").write_text(json.dumps({"info": {"id": hid, "cwd": str(work)}, "num_messages": 4}),
                                       encoding="utf-8")
    return home, sess / "chat_history.jsonl"


def _agy_home(tmp_path: Path, hid: str) -> tuple[Path, Path]:
    home = tmp_path / "agy-home"
    logs = home / "brain" / hid / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    shutil.copy(FIX / "agy_transcript.jsonl", logs / "transcript.jsonl")
    return home, logs / "transcript.jsonl"


@pytest.fixture(autouse=True)
def _fresh():
    H.forget_transcripts()
    yield
    H.forget_transcripts()


# --------------------------------------------------------------------------- #
# Grok
# --------------------------------------------------------------------------- #
def test_grok_chat_history_reads_as_view_events(tmp_path):
    hid = str(uuid.uuid4())
    home, path = _grok_home(tmp_path, tmp_path, hid)
    evs = H.transcript_events("grok", str(home), "", hid)
    got = [(e["type"], e.get("text") or e.get("title")) for e in evs]
    assert got == [
        ("user", "Read hello.txt and run the tests"),
        ("text_done", "I will read it first."),
        ("tool", "Read hello.txt"),
        ("tool", "Run pytest -q"),
        ("user", "Also end with BANANA."),
        ("text_done", "One test fails.\nBANANA"),
        ("user", "What word is in the picture?"),
        ("text_done", "ZEBRA"),
    ], "Grok's own prompt, reminders, workspace note and encrypted reasoning are never drawn"
    read, run = evs[2], evs[3]
    assert read["kind"] == "read" and read["status"] == "done" and "apple pie" in read["output"]
    assert run["kind"] == "command" and run["status"] == "failed" and run["output"].endswith("1 failed")
    assert all(e.get("imported") for e in evs)
    # Times come from Grok's own events.jsonl: each prompt's start, then each
    # tool's end — in order, so a merge with the log places them right.
    assert evs[0]["at"] == "2026-10-31T19:18:54.468000Z"
    assert evs[4]["at"] == "2026-10-31T19:19:00.200000Z"
    assert evs[6]["at"] == "2026-10-31T19:20:10Z"
    ats = [H._parse_at(e["at"]) for e in evs]
    assert ats == sorted(ats)


def test_grok_import_never_carries_the_mcp_address_its_events_file_names(tmp_path):
    hid = str(uuid.uuid4())
    home, _ = _grok_home(tmp_path, tmp_path, hid)
    evs = H.transcript_events("grok", str(home), "", hid)
    blob = json.dumps(evs)
    assert "FAKE-KEY-IN-GROKS-OWN-LOG" not in blob and "mcp.example.com" not in blob
    assert "ENCRYPTED-NOT-READ" not in blob


def test_grok_times_fall_back_to_rewind_points_and_a_session_is_found_by_its_folder_name(tmp_path):
    hid = str(uuid.uuid4())
    home, path = _grok_home(tmp_path, tmp_path, hid)
    (path.parent / "events.jsonl").unlink()
    (path.parent / "rewind_points.jsonl").write_text(
        json.dumps({"prompt_index": 0, "created_at": "2026-10-31T19:18:54.468671Z"}) + "\n", encoding="utf-8")
    evs = H.transcript_events("grok", str(home), "", hid)
    assert evs[0]["at"] == "2026-10-31T19:18:54.468671Z"
    # An id that is not a session folder's name finds nothing (never a path
    # joined from the id).
    assert H.transcript_events("grok", str(home), "", "../" + hid) == []
    assert H.transcript_events("grok", str(home), "", str(uuid.uuid4())) == []


def test_grok_import_is_read_only_and_cached_by_the_files_signature(tmp_path, monkeypatch):
    hid = str(uuid.uuid4())
    home, path = _grok_home(tmp_path, tmp_path, hid)
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    calls = []
    real = H._grok_events
    monkeypatch.setattr(H, "_grok_events", lambda p: calls.append(p) or real(p))
    for _ in range(3):
        assert len(H.transcript_events("grok", str(home), "", hid)) == 8
    assert len(calls) == 1, "parsed once; later views only stat the file"
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


# --------------------------------------------------------------------------- #
# Antigravity
# --------------------------------------------------------------------------- #
def test_antigravity_transcript_reads_as_view_events(tmp_path):
    hid = str(uuid.uuid4())
    home, path = _agy_home(tmp_path, hid)
    evs = H.transcript_events("antigravity", str(home), "", hid)
    got = [(e["type"], e.get("text") or e.get("title")) for e in evs]
    assert got == [
        ("user", "run the echo and write a note"),
        ("tool", "Run echo hello-from-agy"),
        ("tool", "Write note.txt"),
        ("text_done", "done"),
        ("user", "say pong"),
        ("text_done", "pong"),
    ], "its thinking, checkpoints, system and error messages are never drawn"
    run, write = evs[1], evs[2]
    assert run["status"] == "done" and run["output"] == "hello-from-agy"
    assert write["status"] == "failed" and write["path"] == "C:/work/note.txt"
    assert "Created At" not in run["output"]
    assert "Let me check the folder" not in json.dumps(evs)
    assert evs[0]["at"] == "2026-11-06T15:53:32Z"
    assert H.transcript_events("antigravity", str(home), "", str(uuid.uuid4())) == []


# --------------------------------------------------------------------------- #
# Pi: a typed ``!`` / ``!!`` command in its own session file
# --------------------------------------------------------------------------- #
def test_pis_bash_execution_records_read_as_command_steps(tmp_path, monkeypatch):
    from iron_jarvis.hosted import pi as P

    messages = json.loads((FIX / "pi_user_bash.json").read_text(encoding="utf-8"))["frames"][-1]["data"]["messages"]
    hid = str(uuid.uuid4())
    sessions = tmp_path / "pi" / "sessions" / "--work--"
    sessions.mkdir(parents=True)
    path = sessions / f"2026-11-06T00-00-00-000Z_{hid}.jsonl"
    rows = [{"type": "session", "id": hid, "cwd": "C:/work"}]
    rows += [{"type": "message", "timestamp": "2026-11-06T15:00:00Z", "message": m} for m in messages]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(P, "find_session_file", lambda where, h: path if h == hid else None)
    evs = H.transcript_events("pi", str(tmp_path / "pi"), "", hid)
    assert [(e["type"], e["title"], e["status"]) for e in evs] == [
        ("tool", "Run echo hello-bash; echo two", "done"),
        ("tool", "Run echo secret-out", "done"),
    ]
    assert "kept_out" not in evs[0] and evs[1]["kept_out"] is True


# --------------------------------------------------------------------------- #
# Through the service: a session opened from disk shows them
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("harness", ["grok", "antigravity"])
async def test_a_conversation_opened_from_disk_shows_the_tools_own_history(tmp_path, monkeypatch, harness):
    work = tmp_path / "work"
    work.mkdir()
    hid = str(uuid.uuid4())
    if harness == "grok":
        home, path = _grok_home(tmp_path, work, hid)
        monkeypatch.setenv("IRONJARVIS_GROK_HOME", str(home))
        first = "Read hello.txt and run the tests"
    else:
        from .test_build_app_antigravity_v1439 import _db

        home, path = _agy_home(tmp_path, hid)
        _db(home, [(hid, "run the echo", work, 0, "2026-11-06 15:53:32")])
        monkeypatch.setenv("IRONJARVIS_ANTIGRAVITY_HOME", str(home))
        first = "run the echo and write a note"
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    app = create_app(str(tmp_path / "ij-home"))
    try:
        async with client(app) as c:
            r = await c.post("/build/app/sessions", json={"harness": harness, "folder": str(work),
                                                             "harness_session_id": hid})
            assert r.status_code == 201, r.text
            view = (await c.get(f"/build/app/sessions/{r.json()['id']}")).json()
    finally:
        await stop_all(app)
    assert view["events"][0]["type"] == "user" and view["events"][0]["text"] == first
    assert all(e.get("imported") for e in view["events"])
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before, "the tool's file is never written"
