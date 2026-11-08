"""v1.453.0 — archiving a Build › Code session writes a short Markdown summary.

The summary is built from the session's OWN record (its events log and git
in its folder), never a model call: these tests drive a real git repository
and the real routes (a recorded harness replays through the fake process of
``tests/fixtures/fake_harness_v1409.py``), and check that every fact it states
comes from that record — and that it never claims what the record cannot
show (a test run "passed").
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import archive_summary as AS
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, new_session, stop_all, until, use_fake

SECRET = "sk-ant-api03-" + "Q" * 40


def _git(cwd: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+0000")
        env.update({"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    got = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, env=env,
                         capture_output=True, text=True, check=True)
    return got.stdout.strip()


@pytest.fixture()
def repo(tmp_path) -> tuple[Path, datetime]:
    """A repository with one commit an hour BEFORE the session started, one
    commit after it, and one uncommitted file."""
    folder = tmp_path / "proj"
    folder.mkdir()
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    _git(folder, "init", "-q", "-b", "main")
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "Initial ledger import", when=start - timedelta(hours=1))
    (folder / "app.py").write_text("x = 2\n", encoding="utf-8")
    (folder / "test_app.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "Fix the rounding in totals", when=start + timedelta(minutes=10))
    (folder / "notes.txt").write_text("left over\n", encoding="utf-8")
    return folder, start


def _events() -> list[dict]:
    return [
        {"type": "user", "text": f"Fix the totals. My key is {SECRET}", "turn": "t1"},
        {"type": "tool", "kind": "edit", "status": "done", "path": "app.py", "title": "Edit app.py", "turn": "t1"},
        {"type": "tool", "kind": "command", "status": "failed", "subtitle": "pytest -q", "title": "Run tests",
         "turn": "t1"},
        {"type": "tool", "kind": "command", "status": "done", "subtitle": "pytest -q", "title": "Run tests",
         "turn": "t1"},
        {"type": "text_done", "item": "a", "text": "A helper said hi", "parent": "task1", "turn": "t1"},
        {"type": "done", "stop": "completed", "turn": "t1"},
        {"type": "user", "text": "Now push it", "turn": "t2"},
        {"type": "tool", "kind": "command", "status": "done", "subtitle": "git push origin main", "turn": "t2"},
        {"type": "plan", "steps": [{"text": "Fix totals", "status": "completed"},
                                   {"text": "Write the release notes", "status": "pending"}], "turn": "t2"},
        {"type": "text_done", "item": "b", "text": "Done the fix.\n\nStill left: the release notes.", "turn": "t2"},
        {"type": "done", "stop": "interrupted", "turn": "t2"},
        # A background helper's report, written after the tool's last reply.
        {"type": "text_done", "item": "c", "text": "Helper report: all tests green", "parent": "task2"},
    ]


def _row(folder: Path, start: datetime, **kw) -> SimpleNamespace:
    base = {"id": "app_test", "harness": "claude-code", "folder": str(folder), "title": "Fix the totals",
            "model": "opus", "account": "", "account_json": "", "created_at": start}
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_summary_says_before_what_was_done_and_after_from_git_and_the_log(repo):
    folder, start = repo
    text = AS.build_summary(_row(folder, start), _events())
    before = text.split("## Before")[1].split("## What was done")[0]
    done = text.split("## What was done")[1].split("## After")[0]
    after = text.split("## After")[1]
    # Who and where.
    assert str(folder) in text and "Claude Code" in text and "this PC's own sign-in" in text
    # Before: the commit HEAD was on when the session started, and the first ask.
    assert "Initial ledger import" in before and "Fix the rounding" not in before
    assert "Fix the totals" in before
    # The secret in the ask is masked, everywhere.
    assert SECRET not in text
    # What was done: the later ask, the commit after the start, the files git sees, the checks.
    assert "Now push it" in done
    assert "Fix the rounding in totals" in done
    assert "test_app.py" in done and "notes.txt" in done
    assert "test run `pytest -q`: ended with an error" in done
    assert "test run `pytest -q`: finished without an error" in done
    assert "push `git push origin main`: finished without an error" in done
    assert "1 stopped" in done
    # Never a claim the record cannot prove.
    assert "passed" not in text.lower()
    # After: the branch, the uncommitted file, the open plan step, the last reply quoted.
    assert "`main`" in after and "Left uncommitted: 1 file" in after
    assert "Write the release notes" in after and "Fix totals" not in after
    assert "> Still left: the release notes." in after
    # A helper's own text is never taken for the tool's last reply.
    assert "A helper said hi" not in text and "Helper report" not in text
    assert len(text) <= AS.MAX_CHARS


def test_a_folder_that_is_not_a_repository_says_so_and_lists_the_tools_own_edits(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    text = AS.build_summary(_row(folder, datetime.now(timezone.utc)), _events())
    assert "not a git repository" in text
    assert "Files the tool's own edit steps changed: 1" in text and "app.py" in text
    assert "Initial" not in text and "commit" not in text.split("## Before")[1].split("## What")[0].lower()


def test_a_dropped_log_and_an_empty_log_are_said_never_guessed(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    text = AS.build_summary(_row(folder, datetime.now(timezone.utc)), _events(), dropped=40)
    assert "The first message in the log asked" in text and "dropped 40 older events" in text
    empty = AS.build_summary(_row(folder, datetime.now(timezone.utc)), [])
    assert "No messages from this view are in the session's log." in empty


# --------------------------------------------------------------------------- #
# the routes, end to end
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def test_archive_writes_the_summary_and_it_stays_after_bringing_back(app, repo, tmp_path, monkeypatch):
    folder, _start = repo
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, folder)
        sid = s["id"]
        assert (await c.get(f"/build/app/sessions/{sid}/summary")).status_code == 404
        await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "ready to look?", "turn_id": "t1"})
        await until(lambda: not (svc.live[sid].turn and svc.live[sid].turn.running))
        r = await c.post(f"/build/app/sessions/{sid}/archive")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["archived"] is True and "archive_summary_error" not in body
        path = Path(body["archive_summary"]["path"])
        assert path.is_file() and path.name == "summary.md" and path.parent.name == sid
        got = (await c.get(f"/build/app/sessions/{sid}/summary")).json()
        assert got["path"] == str(path)
        assert "ready to look?" in got["text"] and "## Before" in got["text"] and "## After" in got["text"]
        # The opened archived session says it has one.
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert view["archive_summary"]["path"] == str(path)
        # Brought back: the summary stays.
        assert (await c.post(f"/build/app/sessions/{sid}/unarchive")).json()["archived"] is False
        assert (await c.get(f"/build/app/sessions/{sid}/summary")).status_code == 200
        assert "archive_summary" in (await c.get(f"/build/app/sessions/{sid}")).json()
    await stop_all(app)


async def test_archive_is_refused_while_the_tool_works_in_the_background(app, repo, tmp_path, monkeypatch):
    folder, _start = repo
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, folder)
        sid = s["id"]
        await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "ready?", "turn_id": "t1"})
        await until(lambda: not (svc.live[sid].turn and svc.live[sid].turn.running))
        live = svc.live[sid]
        proc = live.proc
        assert proc is not None and proc.alive
        monkeypatch.setattr(S.LiveSession, "keeps_working", lambda self, now: True)
        r = await c.post(f"/build/app/sessions/{sid}/archive")
        assert r.status_code == 409
        assert "still working in the background" in r.json()["detail"]
        assert proc.alive, "a refused archive must not stop the tool"
        assert (await c.get(f"/build/app/sessions/{sid}")).json()["session"]["archived"] is False
        assert (await c.get(f"/build/app/sessions/{sid}/summary")).status_code == 404
    await stop_all(app)


async def test_a_summary_that_cannot_be_written_never_stops_the_archive(app, repo, tmp_path, monkeypatch):
    folder, _start = repo

    def boom(*a, **k):
        raise AS.SummaryNotWritten("The archive summary could not be saved: disk full")

    monkeypatch.setattr(AS, "write_summary", boom)
    async with client(app) as c:
        s = await new_session(c, folder)
        body = (await c.post(f"/build/app/sessions/{s['id']}/archive")).json()
        assert body["archived"] is True
        assert body["archive_summary_error"] == "The archive summary could not be saved: disk full"
        assert "archive_summary" not in body
    await stop_all(app)


async def test_open_the_summary_file_opens_iron_jarvis_own_path(app, repo, monkeypatch):
    from iron_jarvis.daemon.routes import documents as docroutes

    folder, _start = repo
    launched: list[str] = []
    monkeypatch.setattr(docroutes, "_open_native", lambda p: launched.append(p))
    async with client(app) as c:
        s = await new_session(c, folder)
        assert (await c.post(f"/build/app/sessions/{s['id']}/summary/open")).status_code == 404
        path = (await c.post(f"/build/app/sessions/{s['id']}/archive")).json()["archive_summary"]["path"]
        r = await c.post(f"/build/app/sessions/{s['id']}/summary/open")
        assert r.status_code == 200 and launched == [path]
    await stop_all(app)
