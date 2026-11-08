"""Agent history lists a resumed Claude Code conversation ONCE per file (v1.449.0).

The readiness audit (docs/PRODUCT-READINESS-RESULTS.md 0h.5 item 18): Claude
Code can write a resumed or forked conversation into a NEW file whose
records still carry the earlier conversation's ``sessionId``, and
``history/claude_code.summarize`` took the id off that record — two files
listed under one id, and `session_events()` opened whichever came first.
The FILE is the session: its id is the file's own name (the same rule
``history/index.summarize`` already falls back to), so two files are two
rows and each opens its own events.
"""

from __future__ import annotations

import json
from pathlib import Path

from iron_jarvis.history import claude_code
from iron_jarvis.history.index import list_sessions, session_events

OLD = "aaaaaaaa-1111-4222-8333-444444444444"
NEW = "bbbbbbbb-1111-4222-8333-444444444444"


def _write(path: Path, session_id: str, prompt: str, stamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = [
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": "C:\\work\\demo",
            "timestamp": stamp,
            "message": {"role": "user", "content": prompt},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _two_files(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    folder = root / "projects" / "C--work-demo"
    _write(folder / f"{OLD}.jsonl", OLD, "first question", "2026-01-09T10:00:00Z")
    # The resumed conversation: a new file, records still stamped with OLD.
    _write(folder / f"{NEW}.jsonl", OLD, "second question", "2026-01-09T11:00:00Z")
    return folder


def test_two_files_carrying_one_session_id_are_two_sessions(tmp_path, monkeypatch):
    _two_files(tmp_path, monkeypatch)
    rows = list_sessions(harness="claude-code")
    ids = [r["id"] for r in rows]
    assert sorted(ids) == sorted([OLD, NEW]), ids
    assert len(set(ids)) == 2, "a resumed conversation's file was listed under the first file's id"


def test_each_file_opens_its_own_events(tmp_path, monkeypatch):
    _two_files(tmp_path, monkeypatch)
    old_prompts = [e["text"] for e in session_events("claude-code", OLD) if e["action"] == "prompt.submitted"]
    new_prompts = [e["text"] for e in session_events("claude-code", NEW) if e["action"] == "prompt.submitted"]
    assert old_prompts == ["first question"]
    assert new_prompts == ["second question"]
    assert all(e["session_id"] == NEW for e in session_events("claude-code", NEW))


def test_summarize_names_the_file_never_the_record(tmp_path):
    path = tmp_path / "projects" / "C--x" / f"{NEW}.jsonl"
    rec = {"type": "user", "sessionId": OLD, "timestamp": "2026-01-09T10:00:00Z", "message": {"content": "q"}}
    out = claude_code.summarize([(0, rec)], [], path)
    assert out["id"] == NEW
