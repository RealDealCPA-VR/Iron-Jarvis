"""Readiness 0j.5 item 17 (daemon part) — a run or a Build › Code reply that
did NOT finish says which files it wrote are kept.

* An agent run that FAILED keeps every file it wrote (nothing is rolled back),
  but its summary said only "The run failed: …". Now the summary ends with
  "The files it wrote are kept: …", read off the run's own ledger card
  (``agents/outcome.kept_files_note``: the undo journal's created + changed
  files; a failed tool's writes are never counted).
* A Build › Code reply that FAILED after an edit: a ``notice`` before the
  ``done`` says "The file it changed is kept: hello.txt." (``hosted/
  kept_files``, from the reply's own finished edit/write steps).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.outcome import kept_files_note
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import kept_files as K
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from tests._build_app_helpers_v1409 import FIX, client, frames, new_session, stop_all, use_fake

# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #


def test_the_kept_line_names_the_files_and_counts_the_rest():
    assert kept_files_note({"files_created": [], "files_changed": []}) == ""
    assert kept_files_note(None) == ""
    one = {"files_created": ["out/report.md"], "files_created_total": 1}
    assert kept_files_note(one) == " The file it wrote is kept: report.md."
    many = {
        "files_created": [f"f{i}.txt" for i in range(6)],
        "files_changed": ["notes.md"],
        "files_created_total": 9,
        "files_changed_total": 1,
    }
    note = kept_files_note(many)
    assert note.startswith(" The files it wrote are kept: f0.txt, f1.txt, f2.txt, f3.txt, f4.txt")
    assert note.endswith("(and 5 more).")


def test_the_build_notice_counts_only_finished_writes():
    evs = [
        {"type": "tool", "kind": "edit", "status": "done", "path": "C:\\w\\a.py"},
        {"type": "tool", "kind": "write", "status": "failed", "path": "C:\\w\\b.py"},
        {"type": "tool", "kind": "read", "status": "done", "path": "C:\\w\\c.py"},
        {"type": "tool", "kind": "write", "status": "running", "path": "C:\\w\\d.py"},
        {"type": "tool", "kind": "edit", "status": "done", "path": "C:\\w\\a.py"},
    ]
    assert K.kept_notice(evs) == "The file it changed is kept: a.py."
    assert K.kept_notice([]) == ""


# --------------------------------------------------------------------------- #
# an agent run that failed after writing a file
# --------------------------------------------------------------------------- #


async def test_a_failed_run_says_the_file_it_wrote_is_kept(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    write = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "report.md", "content": "# Q3"})],
        finish_reason="tool_use",
    )

    class _Dies(MockLLMAdapter):
        calls = 0

        async def complete(self, *a, **kw):  # type: ignore[override]
            _Dies.calls += 1
            if _Dies.calls == 1:
                return write
            raise RuntimeError("the model went away")

    p.providers.register("mock", lambda model=None: _Dies())
    orch = Orchestrator(p)
    s = await orch.create_session("write the report", AgentType.BUILDER, provider="mock")
    try:
        await asyncio.wait_for(orch.run_session(s.id), timeout=60)
    except Exception:  # noqa: BLE001 — run_session re-raises the crash
        pass
    with session_scope(p.engine) as db:
        row = Session(**db.get(Session, s.id).model_dump())
    assert row.status is SessionStatus.FAILED
    assert row.summary.startswith("The run failed: ")
    assert row.summary.rstrip().endswith("The file it wrote is kept: report.md.")


# --------------------------------------------------------------------------- #
# a Build › Code reply that failed after an edit
# --------------------------------------------------------------------------- #


def _failed_after_an_edit(tmp_path: Path) -> Path:
    failed = json.loads((FIX / "claude_failed_v1417.json").read_text(encoding="utf-8"))
    edit = json.loads((FIX / "claude_edit.json").read_text(encoding="utf-8"))
    sid = failed["steps"][3]["out"]["session_id"]
    inserted = []
    for step in (edit["steps"][35], edit["steps"][41]):
        step = json.loads(json.dumps(step))
        step["out"]["session_id"] = sid
        inserted.append(step)
    steps = failed["steps"][:5] + inserted + failed["steps"][5:]
    out = tmp_path / "claude_failed_after_edit.json"
    out.write_text(json.dumps({**failed, "steps": steps}), encoding="utf-8")
    return out


@pytest.fixture
def work(tmp_path):
    w = tmp_path / "work"
    w.mkdir()
    return w


async def test_a_failed_build_reply_says_the_file_it_changed_is_kept(tmp_path, work, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    use_fake(monkeypatch, "claude_failed_v1417", tmp_path / "fake.log")
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(_failed_after_an_edit(tmp_path)))
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        res = await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}), 30
        )
        evs = frames(res.text)
    await stop_all(app)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "failed"
    notices = [e["text"] for e in evs if e["type"] == "notice"]
    assert "The file it changed is kept: hello.txt." in notices, notices


async def test_a_failed_build_reply_with_no_edit_says_nothing_more(tmp_path, work, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    use_fake(monkeypatch, "claude_failed_v1417", tmp_path / "fake.log")
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        res = await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}), 30
        )
        evs = frames(res.text)
    await stop_all(app)
    assert not any("is kept" in str(e.get("text") or "") for e in evs)
