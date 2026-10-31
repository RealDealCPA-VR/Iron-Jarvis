"""v1.414.0 — Build › Code's right panes: Changes, Usage and Session.

The real routes, the real store, the real events log and REAL git in a
scratch repository; the coding tool's frames are the recorded ones
(``tests/fixtures/build_app_v1409``) through the fake harness. Nothing here
starts a real coding tool or calls a model.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import seen as SEEN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import EventLog, make
from tests._build_app_helpers_v1409 import FIX, client, frames, new_session, stop_all, until, use_fake

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(cwd: Path, *args: str, when: str | None = None) -> None:
    env = dict(os.environ)
    if when:
        env["GIT_COMMITTER_DATE"] = when
        env["GIT_AUTHOR_DATE"] = when
    subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (folder / "old.txt").write_text("keep\n", encoding="utf-8")
    (folder / ".gitignore").write_text("secret.log\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first", when="2020-01-01T00:00:00+00:00")
    return folder


def _log(app, sid: str, *events: dict) -> None:
    svc = S.service_for(app.state.platform)
    log_ = EventLog(svc.home, sid)
    for ev in events:
        log_.append(ev)
    log_.close()


# --------------------------------------------------------------------------- #
# CHANGES
# --------------------------------------------------------------------------- #
async def test_changes_in_a_repository_come_from_git_since_the_session_started(app, repo):
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        # After the session started: an edit, a new file, a commit, an ignored file.
        (repo / "app.py").write_text("x = 1\ny = 3\n", encoding="utf-8")
        (repo / "new.md").write_text("hello\n", encoding="utf-8")
        (repo / "old.txt").write_text("keep\nmore\n", encoding="utf-8")
        _git(repo, "add", "old.txt")
        _git(repo, "commit", "-q", "-m", "during")
        (repo / "secret.log").write_text("token\n", encoding="utf-8")
        _log(app, sid, make("tool", item="t1", kind="edit", name="Edit", title="Edit app.py", status="done",
                            path=str(repo / "app.py")))
        body = (await c.get(f"/build/app/sessions/{sid}/changes")).json()
    await stop_all(app)
    assert body["source"] == "git" and "git" in body["words"] and "you or another program" in body["words"]
    by = {r["rel"]: r for r in body["changes"]}
    # Committed DURING the session counts; the file git ignores does not.
    assert set(by) == {"app.py", "new.md", "old.txt"}, by.keys()
    assert by["app.py"]["status"] == "modified" and "+y = 3" in by["app.py"]["diff"]
    assert by["new.md"]["status"] == "created"
    assert "+more" in by["old.txt"]["diff"]
    # Only the file the tool's own record names is marked as the tool's.
    assert by["app.py"]["by_tool"] is True and by["new.md"]["by_tool"] is False
    assert body["by_tool"] == 1 and body["files"] == 3
    assert "No Undo" in body["no_undo"] and "Claude Code" in body["no_undo"]


async def test_changes_with_no_repository_are_the_tools_own_record(app, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    async with client(app) as c:
        sid = (await new_session(c, folder))["id"]
        d1 = "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,1 +1,1 @@\n-a\n+b"
        d2 = "--- a/notes.txt\n+++ b/notes.txt\n@@ -3,1 +3,2 @@\n c\n+d"
        _log(
            app, sid,
            make("tool", item="1", kind="edit", name="Edit", title="Edit", status="done", path=str(folder / "notes.txt"), diff=d1),
            make("tool", item="2", kind="edit", name="Edit", title="Edit", status="done", path="notes.txt", diff=d2),
            make("tool", item="3", kind="edit", name="Edit", title="Edit", status="failed", path=str(folder / "no.txt"), diff=d1),
            make("tool", item="4", kind="command", name="Bash", title="Run", status="done", input="sed -i x other.txt"),
        )
        body = (await c.get(f"/build/app/sessions/{sid}/changes")).json()
    await stop_all(app)
    assert body["source"] == "tool" and "not in a git repository" in body["words"]
    assert "commands it ran are not" in body["words"]
    assert [r["rel"] for r in body["changes"]] == ["notes.txt"], "a failed edit or a command is not a change"
    diff = body["changes"][0]["diff"]
    # Both edits, ONE header: a second header inside a hunk would read as lines.
    assert diff.count("+++ ") == 1 and diff.count("--- ") == 1
    assert "+b" in diff and "+d" in diff
    assert body["changes"][0]["added"] == 2 and body["changes"][0]["removed"] == 1


async def test_a_folder_on_a_network_share_is_refused_from_its_text(app, monkeypatch):
    from iron_jarvis.git import integration

    row = store.create(app.state.platform.engine, harness="claude-code", folder=r"\\host\share\work")
    touched: list = []
    # A spy that never touches the disk itself (a real stat of \host\share
    # would be an SMB connection — the thing this pins).
    monkeypatch.setattr(PN.os.path, "isdir", lambda p: touched.append(p) or False)
    monkeypatch.setattr(PN.os.path, "isfile", lambda p: touched.append(p) or False)
    monkeypatch.setattr(integration, "run_git", lambda *a, **k: touched.append(a) or (_ for _ in ()).throw(AssertionError))
    async with client(app) as c:
        body = (await c.get(f"/build/app/sessions/{row.id}/changes")).json()
        over = (await c.get(f"/build/app/sessions/{row.id}/overview")).json()
    assert body["source"] == "none" and "network share" in body["words"]
    assert over["on_share"] is True and over["git"] == {"repo": False}
    assert not [t for t in touched if "host" in str(t)], touched


async def test_a_session_with_no_folder_yet_says_so(app):
    row = store.create(app.state.platform.engine, harness="claude-code", folder="", no_folder=True)
    async with client(app) as c:
        body = (await c.get(f"/build/app/sessions/{row.id}/changes")).json()
        missing = await c.get("/build/app/sessions/app_nope/changes")
    assert body["source"] == "none" and "first message" in body["words"]
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# USAGE
# --------------------------------------------------------------------------- #
def test_the_tools_own_windows_and_context_are_read_from_their_frames():
    edit = json.loads((FIX / "claude_edit.json").read_text(encoding="utf-8"))["steps"]
    info = next(r["out"]["rate_limit_info"] for r in edit if isinstance(r.get("out"), dict)
                and r["out"].get("type") == "rate_limit_event")
    wins = SEEN.claude_windows(info)
    assert [(w["key"], w["percent"], w["label"]) for w in wins] == [
        ("five_hour", 24, "5-hour window"), ("seven_day", 27, "7-day window")]
    assert wins[0]["resets_at"].endswith("Z")
    result = next(r["out"] for r in edit if isinstance(r.get("out"), dict) and r["out"].get("type") == "result")
    used, window = SEEN.claude_context(result)
    assert window == 1_000_000 and used > 0
    codex = json.loads((FIX / "codex_allow.json").read_text(encoding="utf-8"))["steps"]
    rl = next(r["out"]["params"]["rateLimits"] for r in codex if isinstance(r.get("out"), dict)
              and r["out"].get("method") == "account/rateLimits/updated")
    cw, plan = SEEN.codex_windows(rl)
    assert [(w["label"], w["percent"]) for w in cw] == [("5-hour window", 2), ("7-day window", 0)]
    assert plan == "plus"
    tu = next(r["out"]["params"]["tokenUsage"] for r in codex if isinstance(r.get("out"), dict)
              and r["out"].get("method") == "thread/tokenUsage/updated")
    assert SEEN.codex_context(tu) == (17421, 258400)
    # A frame with no numbers says nothing (never a 0%).
    assert SEEN.claude_windows({"status": "allowed"}) == []
    assert SEEN.claude_windows({"unifiedWindows": {"five_hour": {"resetsAt": 1}, "seven_day": {"utilization": 0.5}}}) == [
        {"key": "seven_day", "label": "7-day window", "percent": 50, "resets_at": None}]
    assert SEEN.opencode_context({"used": 0, "size": 0}) is None


async def test_usage_sums_the_replies_and_keeps_the_newest_windows_with_their_time(app, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    async with client(app) as c:
        sid = (await new_session(c, folder))["id"]
        none = (await c.get(f"/build/app/sessions/{sid}/usage")).json()
        w1 = [{"key": "five_hour", "label": "5-hour window", "percent": 10, "resets_at": None}]
        # v1.414.0 review: a reset in the FUTURE (a past one is over and left out).
        later = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
        w2 = [{"key": "five_hour", "label": "5-hour window", "percent": 31, "resets_at": later}]
        _log(
            app, sid,
            make("usage", input=100, output=10, cache_read=50, list_price=True, cost_usd=0.5, windows=w1),
            make("usage", input=200, output=20, cache_read=0, list_price=True, cost_usd=0.25, windows=w2,
                 context={"used": 50_000, "window": 200_000}),
            make("usage", input=1, output=1, cache_read=0, list_price=True),
        )
        body = (await c.get(f"/build/app/sessions/{sid}/usage")).json()
    await stop_all(app)
    # Nothing reported yet: no windows, no context — never a made-up 0%.
    assert none["seen"] is None and none["context"] is None and none["totals"]["replies"] == 0
    assert none["account"] is None
    t = body["totals"]
    assert (t["replies"], t["input"], t["output"], t["cost_usd"], t["list_price"]) == (3, 301, 31, 0.75, True)
    assert body["seen"]["windows"][0]["percent"] == 31 and body["seen"]["at"]
    assert body["context"] == {**body["context"], "used": 50_000, "window": 200_000, "percent": 25}


async def test_usage_reads_the_cached_iron_proxy_snapshot_for_the_sessions_account(app, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    calls: list = []

    class Proxy:
        enabled = True

        def cached_accounts(self):
            calls.append("cached")
            return (
                [{"id": "p1", "title": "Work", "provider": "anthropic", "lane": "cli"}],
                {"p1": {"status": "ready", "plan": "max", "windows": [
                    {"usedPercent": 63, "windowMins": 300, "resetsAt": soon},
                    {"usedPercent": 99, "windowMins": 10080, "resetsAt": past},
                ]}},
            )

    app.state.platform.iron_proxy = Proxy()
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(tmp_path), "unset": []}
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(folder), account="p1",
                       account_json=json.dumps(rec))
    async with client(app) as c:
        body = (await c.get(f"/build/app/sessions/{row.id}/usage")).json()
    acc = body["account"]
    assert acc["name"] == "Work" and acc["plan_label"]
    # A window whose reset has passed is over: left out.
    assert [(w["label"], w["percent"]) for w in acc["windows"]] == [("5-hour window", 63)]
    assert calls, "the cached snapshot was read"


async def test_the_real_parser_puts_the_tools_windows_on_the_reply(tmp_path, monkeypatch):
    """The recorded Claude Code edit through the REAL driver: the Usage tab
    reads the windows Claude Code itself reported, and how full."""
    work = tmp_path / "work"
    work.mkdir()
    use_fake(monkeypatch, "claude_edit", tmp_path / "fake.log")
    # v1.414.0 review: the recorded windows reset long ago, and a window whose
    # reset has passed is left out — judge them at the time they were recorded.
    edit = json.loads((FIX / "claude_edit.json").read_text(encoding="utf-8"))["steps"]
    info = next(r["out"]["rate_limit_info"] for r in edit if isinstance(r.get("out"), dict)
                and r["out"].get("type") == "rate_limit_event")
    first_reset = min(w["resetsAt"] for w in info["unifiedWindows"].values() if w.get("resetsAt"))
    monkeypatch.setattr(PN, "_now", lambda: datetime.fromtimestamp(first_reset - 3600, tz=timezone.utc))
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
        await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
        body = (await c.get(f"/build/app/sessions/{sid}/usage")).json()
    await stop_all(app)
    usage = [e for e in evs if e["type"] == "usage"]
    assert usage and usage[-1]["windows"][0]["key"] == "five_hour"
    assert [w["percent"] for w in body["seen"]["windows"]] == [24, 27]
    assert body["context"]["window"] == 1_000_000 and 0 < body["context"]["percent"] <= 100


# --------------------------------------------------------------------------- #
# SESSION
# --------------------------------------------------------------------------- #
async def test_overview_names_the_branch_and_the_instruction_files(app, repo):
    (repo / "CLAUDE.md").write_text("rules\n", encoding="utf-8")
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        body = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
    await stop_all(app)
    assert body["git"] == {"repo": True, "branch": "main", "detached": False}
    assert body["rules"] == ["CLAUDE.md"]
    assert body["tool_running"] is False and body["programs"] == []


def test_programs_under_lists_the_whole_tree_masked(tmp_path):
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    script = tmp_path / "p.py"
    script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)', '{key}'])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen([sys.executable, str(script)])
    try:
        deadline = time.monotonic() + 15
        got: list = []
        while time.monotonic() < deadline:
            got = PN.programs_under(parent.pid)
            if any("time.sleep(30)" in g["command"] for g in got):
                break
            time.sleep(0.1)
        cmds = [g["command"] for g in got]
        assert any("time.sleep(30)" in c for c in cmds), cmds
        assert all(g["pid"] != parent.pid for g in got)
        assert not any("api03-abcdef" in c for c in cmds), cmds
        assert PN.programs_under(None) == []
    finally:
        import psutil

        for p in psutil.Process(parent.pid).children(recursive=True):
            p.kill()
        parent.kill()
        parent.wait(10)


async def test_the_three_reads_never_start_the_coding_tool(app, repo, monkeypatch):
    # git is read (a child of its own); the CODING TOOL is never looked up
    # or started.
    spawned: list = []
    monkeypatch.setattr(S, "binary_for", lambda h: spawned.append(h) or (_ for _ in ()).throw(AssertionError))
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        for part in ("changes", "usage", "overview"):
            assert (await c.get(f"/build/app/sessions/{sid}/{part}")).status_code == 200
    await stop_all(app)
    live = S.service_for(app.state.platform).live.get(sid)
    assert spawned == [] and (live is None or not live.running)
