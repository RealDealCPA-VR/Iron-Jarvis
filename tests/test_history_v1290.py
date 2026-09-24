"""v1.290.0 Part B: the read-only Claude Code / Codex history reader + its routes.

Every test points ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` at a temp copy of the
SYNTHETIC fixtures under ``tests/fixtures/history/`` (hand-written records, no
real user content) — no test ever reads the user's real ``~/.claude`` or
``~/.codex``. Routes are driven through the REAL app factory with its auth
middleware.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.history as history
from iron_jarvis.daemon.app import create_app
from iron_jarvis.history import (
    SessionNotFound,
    list_sessions,
    load_session,
    session_events,
)
from iron_jarvis.daemon.routes import history as hroutes
from iron_jarvis.history import index as hindex

FIXTURES = Path(__file__).parent / "fixtures" / "history"
CL_ID = "11111111-2222-4333-8444-555555555555"
CX_ID = "0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
#: A second Claude session whose only credential read happens in a subagent.
CL2_ID = "22222222-3333-4444-8555-666666666666"


@pytest.fixture(autouse=True)
def _isolated_homes(tmp_path, monkeypatch):
    """Point both harness homes at EMPTY temp dirs and clear the summary cache."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    _clear()
    yield
    _clear()


def _clear():
    hindex._cache.clear()
    hindex._count_cache.clear()
    hindex._listing.clear()
    hroutes._findings_cache.clear()


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """A temp copy of the synthetic fixtures, wired in through the env overrides."""
    root = tmp_path / "fx"
    shutil.copytree(FIXTURES, root)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(root / "codex"))
    return root


def _claude_file(root: Path) -> Path:
    return root / "claude" / "projects" / "C--work-demo" / f"{CL_ID}.jsonl"


def _own(events: list[dict]) -> list[dict]:
    """The session file's own events (subagent events carry ``subagent``)."""
    return [e for e in events if "subagent" not in e]


def _by_tool(events: list[dict]) -> dict[str, dict]:
    return {e["tool"]: e for e in _own(events) if e.get("tool")}


# --------------------------------------------------------------------------- listing


def test_lists_both_harnesses_from_the_env_overrides(homes):
    sessions = list_sessions()
    # newest first
    assert [s["id"] for s in sessions] == [CX_ID, CL_ID, CL2_ID]
    cx, cl, _ = sessions
    assert cl["id"] == CL_ID
    assert cl["project"] == "C:\\work\\demo"
    assert cl["title"] == "Please tidy the demo build script and check the cloud config"
    assert cl["started"] == "2026-01-02T03:04:01.000+00:00"
    assert cl["ended"] == "2026-01-02T03:04:12.000+00:00"
    assert cl["tools"] == 9 and cl["events"] == 17 and cl["truncated"] is False
    # two subagent transcripts (one under subagents/workflows/), one call each
    assert cl["subagents"] == 2 and cl["subagent_tools"] == 2
    assert cx["subagents"] == 0 and cx["subagent_tools"] == 0
    assert Path(cl["file"]).is_absolute() and Path(cl["file"]) == _claude_file(homes)
    assert cx["id"] == CX_ID
    assert cx["project"] == "C:\\work\\codexdemo"
    # the injected <environment_context> "user" message is not the title
    assert cx["title"] == "Rename the helper module and show the key file"
    assert cx["tools"] == 6
    assert cx["started"] == "2026-02-03T04:05:00.000+00:00"
    assert cx["ended"] == "2026-02-03T04:05:22.000+00:00"


def test_subagent_transcripts_are_not_sessions(homes):
    ids = {s["id"] for s in list_sessions(harness="claude-code")}
    assert ids == {CL_ID, CL2_ID}


def test_harness_filter_limit_and_unknown_harness(homes):
    assert [s["harness"] for s in list_sessions(harness="codex")] == ["codex"]
    assert len(list_sessions(limit=1)) == 1
    with pytest.raises(ValueError):
        list_sessions(harness="cursor")


def test_missing_directories_list_nothing():
    # the autouse fixture points both homes at directories that do not exist
    assert list_sessions() == []
    with pytest.raises(SessionNotFound):
        session_events("claude-code", CL_ID)


def test_default_roots_are_the_home_dirs(monkeypatch, tmp_path):
    from iron_jarvis.history import claude_code, codex

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert claude_code.root() == tmp_path / ".claude"
    assert codex.root() == tmp_path / ".codex"


# --------------------------------------------------------------------------- mapping


def test_claude_code_tool_names_map_to_actions(homes):
    events = session_events("claude-code", CL_ID)
    assert all(
        e["source"] == "claude-code" and e["session_id"] == CL_ID for e in events
    )
    tools = _by_tool(events)
    assert tools["Bash"]["action"] == "command.executed"
    assert tools["Bash"]["command"] == "cat ~/.aws/credentials"
    assert tools["PowerShell"]["action"] == "command.executed"
    assert tools["Read"]["action"] == "file.read"
    assert tools["Read"]["path"] == "C:\\work\\demo\\build.ps1"
    for name in ("Write", "Edit", "MultiEdit"):
        assert tools[name]["action"] == "file.write", name
    assert tools["WebFetch"]["action"] == "network.request"
    assert tools["WebFetch"]["url"] == "https://example.invalid/docs"
    assert tools["WebSearch"]["action"] == "network.request"
    # a search READS what it matches: the searched folder is the path
    assert tools["Grep"]["action"] == "file.read"
    assert tools["Grep"]["path"] == "C:\\work\\demo"


def test_claude_code_results_pair_by_tool_use_id(homes):
    events = session_events("claude-code", CL_ID)
    tools = _by_tool(events)
    assert tools["Bash"]["text"].startswith("[default]") and tools["Bash"]["ok"] is True
    assert tools["Read"]["text"] == "Write-Host 'hello'"  # list-of-blocks content
    assert (
        tools["Write"]["ok"] is False and tools["Write"]["text"] == "permission denied"
    )
    orphans = [e for e in events if e["action"] == "tool.called" and not e.get("tool")]
    assert [o["text"] for o in orphans] == ["orphan output"]
    own = str(_claude_file(homes)) + ":"
    assert all(e["ref"].startswith(own) for e in _own(events))


def test_claude_code_prompts_and_responses(homes):
    events = _own(session_events("claude-code", CL_ID))
    prompts = [e["text"] for e in events if e["action"] == "prompt.submitted"]
    # the /clear echo and the isMeta record are bookkeeping, not prompts
    assert prompts == ["Please tidy the demo build script and check the cloud config"]
    responses = [e["text"] for e in events if e["action"] == "response.completed"]
    assert responses == ["Looking at it now.", "Done tidying."]
    actions = [e["action"] for e in events]
    # the words said before the Bash call come before the call
    assert actions.index("response.completed") < actions.index("command.executed")


def test_codex_tool_names_map_to_actions(homes):
    events = session_events("codex", CX_ID)
    assert all(e["source"] == "codex" and e["session_id"] == CX_ID for e in events)
    shell = [e for e in events if e.get("tool") == "shell"][0]
    assert shell["action"] == "command.executed"
    assert (
        shell["command"]
        == "powershell -Command Get-Content $env:USERPROFILE\\.ssh\\id_rsa"
    )
    assert shell["text"] == "-----BEGIN FAKE KEY-----" and shell["ok"] is True
    git = [e for e in events if e.get("tool") == "shell_command"][0]
    assert git["action"] == "command.executed" and git["ok"] is False
    patch = [e for e in events if e.get("tool") == "apply_patch"]
    assert [(e["action"], e["path"]) for e in patch] == [
        ("file.write", "src/helper.py"),
        ("file.delete", "src/old_helper.py"),
    ]
    assert all(e["text"].startswith("Success") for e in patch)
    image = [e for e in events if e.get("tool") == "view_image"][0]
    assert image["action"] == "file.read" and image["text"] == "image attached"
    plan = [e for e in events if e.get("tool") == "update_plan"][0]
    assert plan["action"] == "tool.called"
    local = [e for e in events if e.get("tool") == "local_shell"][0]
    assert local["action"] == "command.executed" and local["command"] == "bash -lc ls"
    orphans = [e for e in events if e["action"] == "tool.called" and not e.get("tool")]
    assert [o["text"] for o in orphans] == ["orphan output"]


def test_codex_prompts_skip_injected_context(homes):
    events = session_events("codex", CX_ID)
    prompts = [e["text"] for e in events if e["action"] == "prompt.submitted"]
    # not the <environment_context> message, not the developer message, and the
    # event_msg copy of the prompt is not a second prompt
    assert prompts == ["Rename the helper module and show the key file"]
    assert [e["text"] for e in events if e["action"] == "response.completed"] == [
        "Renamed the helper."
    ]


def test_event_text_is_clipped(homes):
    path = _claude_file(homes)
    big = "y" * 9000
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": CL_ID,
                    "message": {"role": "user", "content": big},
                }
            )
            + "\n"
        )
    prompts = [
        e
        for e in session_events("claude-code", CL_ID)
        if e["action"] == "prompt.submitted"
    ]
    assert len(prompts[-1]["text"]) == 4000
    assert list_sessions(harness="claude-code")[0]["title"].startswith("Please tidy")


# --------------------------------------------------------------------------- tolerance


def test_malformed_unknown_and_empty_files_never_raise(homes):
    day = homes / "codex" / "sessions" / "2026" / "03" / "04"
    day.mkdir(parents=True)
    empty = (
        day / "rollout-2026-03-04T00-00-00-00000000-0000-0000-0000-000000000001.jsonl"
    )
    empty.write_bytes(b"")
    junk = (
        day / "rollout-2026-03-04T00-00-01-00000000-0000-0000-0000-000000000002.jsonl"
    )
    junk.write_bytes(b'{nope\n\x00\xff\xfe garbage\n[]\n42\n{"type": "mystery"}\n')
    by_id = {s["id"]: s for s in list_sessions(harness="codex")}
    # an id is recovered from the file name when no session_meta is readable
    assert "00000000-0000-0000-0000-000000000001" in by_id
    assert by_id["00000000-0000-0000-0000-000000000001"]["events"] == 0
    assert by_id["00000000-0000-0000-0000-000000000002"]["events"] == 5
    assert session_events("codex", "00000000-0000-0000-0000-000000000001") == []
    assert session_events("codex", "00000000-0000-0000-0000-000000000002") == []


def test_title_falls_back_to_the_recorded_title(homes, tmp_path):
    slug = homes / "claude" / "projects" / "C--other"
    slug.mkdir()
    sid = "99999999-2222-4333-8444-555555555555"
    (slug / f"{sid}.jsonl").write_text(
        json.dumps(
            {"type": "ai-title", "aiTitle": "Named by the harness", "sessionId": sid}
        )
        + "\n",
        encoding="utf-8",
    )
    s = [x for x in list_sessions(harness="claude-code") if x["id"] == sid][0]
    assert s["title"] == "Named by the harness"
    assert s["project"] == "C--other"  # no cwd recorded: the slug


def test_loading_stops_at_the_byte_cap_and_says_so(homes, monkeypatch):
    """Few LONG lines: a line cap would never trigger; the byte cap does."""
    slug = homes / "claude" / "projects" / "C--big"
    slug.mkdir()
    sid = "33333333-2222-4333-8444-555555555555"
    path = slug / f"{sid}.jsonl"

    def line(i: int) -> str:
        rec = {
            "type": "user",
            "sessionId": sid,
            "timestamp": f"2026-01-05T00:00:{i:02d}Z",
            "message": {"role": "user", "content": f"prompt {i} " + "z" * 900},
        }
        return json.dumps(rec) + "\n"

    lines = [line(i) for i in range(6)]
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    sizes = [len(x.encode()) for x in lines]
    # the cap lands INSIDE the fourth line: three are read whole, no more
    cap = sum(sizes[:3]) + sizes[3] // 2
    monkeypatch.setattr(hindex, "MAX_READ_BYTES", cap)
    listed = [s for s in list_sessions(harness="claude-code") if s["id"] == sid][0]
    assert listed["truncated"] is True  # predicted from the size
    summary, events = load_session("claude-code", sid)
    assert summary["truncated"] is True  # and what was really skipped
    assert [e["text"].split(" ")[1] for e in events] == ["0", "1", "2"]

    # a file that fits the cap exactly is read whole and NOT truncated
    monkeypatch.setattr(hindex, "MAX_READ_BYTES", sum(sizes))
    summary, events = load_session("claude-code", sid)
    assert summary["truncated"] is False and len(events) == 6


def test_a_subagents_oversize_file_marks_the_session_truncated(homes, monkeypatch):
    sub = homes / "claude" / "projects" / "C--work-notes" / CL2_ID / "subagents"
    parent = homes / "claude" / "projects" / "C--work-notes" / f"{CL2_ID}.jsonl"
    rec = {"type": "user", "sessionId": CL2_ID, "message": {"content": "q" * 8000}}
    with open(sub / "agent-cccc.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec) + "\n")
    # the parent fits; the subagent's appended line does not
    monkeypatch.setattr(hindex, "MAX_READ_BYTES", parent.stat().st_size + 100)
    listed = [s for s in list_sessions(harness="claude-code") if s["id"] == CL2_ID][0]
    assert listed["truncated"] is True
    summary, events = load_session("claude-code", CL2_ID)
    assert summary["truncated"] is True
    assert not any("q" * 100 in e.get("text", "") for e in events)


def test_summary_parses_only_the_head_and_the_tail(homes, monkeypatch):
    path = _claude_file(homes)
    lines = path.read_text(encoding="utf-8").splitlines()
    filler = json.dumps({"type": "progress", "sessionId": CL_ID, "pad": "p" * 200})
    last = json.dumps(
        {"type": "system", "sessionId": CL_ID, "timestamp": "2026-01-09T00:00:00Z"}
    )
    path.write_text(
        "\n".join(lines + [filler] * 2000 + [last]) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(hindex, "HEAD_BYTES", 8 * 1024)
    monkeypatch.setattr(hindex, "TAIL_BYTES", 2 * 1024)
    calls = []
    real = hindex.parse_line
    monkeypatch.setattr(hindex, "parse_line", lambda raw: calls.append(1) or real(raw))
    s = list_sessions(harness="claude-code")[0]
    assert s["title"].startswith("Please tidy")  # from the head
    assert s["ended"] == "2026-01-09T00:00:00.000+00:00"  # from the tail
    assert s["events"] == len(lines) + 2001  # counted, not parsed
    assert len(calls) < 100, f"parsed {len(calls)} lines for a 2000+ line file"


# --------------------------------------------------------------------------- cache


def test_summary_cache_hits_until_the_file_changes(homes, monkeypatch):
    reads = []
    real = hindex._head_tail
    monkeypatch.setattr(
        hindex, "_head_tail", lambda p, n: reads.append(p) or real(p, n)
    )
    list_sessions()
    list_sessions()
    assert len(reads) == 3  # one per file, the second listing was all cache hits
    path = _claude_file(homes)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"type": "system", "sessionId": CL_ID}) + "\n")
    s = list_sessions(harness="claude-code")[0]
    assert len(reads) == 4 and s["events"] == 18


def test_a_new_subagent_transcript_refreshes_the_summary(homes):
    before = list_sessions(harness="claude-code")[0]
    assert before["subagents"] == 2
    sub = homes / "claude" / "projects" / "C--work-demo" / CL_ID / "subagents"
    rec = {
        "type": "assistant",
        "sessionId": CL_ID,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t9",
                    "name": "Bash",
                    "input": {"command": "x"},
                }
            ]
        },
    }
    (sub / "agent-dddd.jsonl").write_text(json.dumps(rec, separators=(",", ":")) + "\n")
    after = list_sessions(harness="claude-code")[0]
    assert after["subagents"] == 3 and after["subagent_tools"] == 3


def test_cache_entries_for_deleted_files_are_evicted(homes):
    list_sessions()
    path = _claude_file(homes)
    assert str(path) in hindex._cache
    sub = path.parent / CL_ID / "subagents" / "agent-aaaa.jsonl"
    assert str(sub) in hindex._count_cache
    shutil.rmtree(path.parent / CL_ID)
    path.unlink()
    list_sessions()
    assert str(path) not in hindex._cache
    assert str(sub) not in hindex._count_cache


def test_same_size_rewrite_with_a_new_mtime_is_reread(homes):
    path = _claude_file(homes)
    before = list_sessions(harness="claude-code")[0]
    data = path.read_bytes().replace(b"Please tidy", b"Please TIDY")
    assert len(data) == before["size"]
    path.write_bytes(data)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert list_sessions(harness="claude-code")[0]["title"].startswith("Please TIDY")


def test_size_change_with_the_same_mtime_is_reread(homes):
    path = _claude_file(homes)
    list_sessions(harness="claude-code")
    st = os.stat(path)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"type": "system", "sessionId": CL_ID}) + "\n")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime put back exactly
    assert os.stat(path).st_mtime_ns == st.st_mtime_ns
    assert list_sessions(harness="claude-code")[0]["events"] == 18


# --------------------------------------------------------------------------- traversal


def test_session_id_is_matched_never_joined(homes):
    # a well-formed transcript OUTSIDE the listed set, reachable by a path join
    secret_records = [
        {
            "type": "user",
            "sessionId": "secret",
            "message": {"role": "user", "content": "SECRET"},
        }
    ]
    body = "\n".join(json.dumps(r) for r in secret_records) + "\n"
    (homes / "claude" / "secret.jsonl").write_text(body, encoding="utf-8")
    (homes / "claude" / "projects" / "secret.jsonl").write_text(body, encoding="utf-8")
    sub = f"C--work-demo/{CL_ID}/subagents/agent-aaaa"
    for bad in (
        "../../secret",
        "..\\..\\secret",
        "../secret",
        "..\\secret",
        str(homes / "claude" / "secret"),
        sub,
        sub.replace("/", "\\"),
        f"C--work-demo/{CL_ID}",
        "",
        "x" * 1000,
    ):
        with pytest.raises(SessionNotFound):
            session_events("claude-code", bad)
    with pytest.raises(SessionNotFound):
        session_events("codex", "../../../claude/secret")


def test_nothing_under_the_homes_is_written(homes):
    def snapshot():
        return {
            str(p): (p.stat().st_mtime_ns, p.read_bytes())
            for p in homes.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    list_sessions()
    load_session("claude-code", CL_ID)
    load_session("codex", CX_ID)
    assert snapshot() == before


# --------------------------------------------------------------------------- routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "hist-token")
    return TestClient(create_app(str(tmp_path / "home")))


AUTH = {"Authorization": "Bearer hist-token"}


def _fake_detections(monkeypatch, found: list):
    """Install a stand-in ``iron_jarvis.detections`` whose scan records its input."""

    class _F:
        def __init__(self, n):
            self.n = n

        def to_dict(self):
            return {"rule_id": "fake", "n": self.n}

    mod = types.ModuleType("iron_jarvis.detections")

    def scan(events, rules=None):
        found.append(list(events))
        return [_F(len(found[-1]))]

    mod.scan = scan
    monkeypatch.setitem(sys.modules, "iron_jarvis.detections", mod)


def test_routes_are_behind_the_apps_auth(homes, client):
    assert client.get("/history/sessions").status_code == 401
    assert client.get(f"/history/sessions/claude-code/{CL_ID}").status_code == 401


def test_list_route_through_the_real_app(homes, client):
    r = client.get("/history/sessions", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert {s["id"] for s in body["sessions"]} == {CL_ID, CL2_ID, CX_ID}
    r = client.get("/history/sessions?harness=codex&limit=5", headers=AUTH)
    assert [s["id"] for s in r.json()["sessions"]] == [CX_ID]
    assert client.get("/history/sessions?harness=nope", headers=AUTH).status_code == 400


def test_session_route_returns_events_and_findings(homes, client, monkeypatch):
    seen: list = []
    _fake_detections(monkeypatch, seen)
    r = client.get(f"/history/sessions/claude-code/{CL_ID}", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session"]["id"] == CL_ID
    assert body["events"] == session_events("claude-code", CL_ID)
    assert seen and seen[0] == body["events"]  # the scan saw exactly these events
    assert body["findings"] == [{"rule_id": "fake", "n": len(body["events"])}]
    assert "findings_error" not in body


def test_session_route_refuses_unknown_and_traversal(homes, client):
    (homes / "claude" / "secret.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": "s", "message": {"content": "SECRET"}})
        + "\n",
        encoding="utf-8",
    )
    assert (
        client.get("/history/sessions/claude-code/nope", headers=AUTH).status_code
        == 404
    )
    assert (
        client.get(f"/history/sessions/cursor/{CL_ID}", headers=AUTH).status_code == 400
    )
    for bad in ("..%2F..%2Fsecret", "..%5C..%5Csecret", "%2E%2E%2F%2E%2E%2Fsecret"):
        r = client.get(f"/history/sessions/claude-code/{bad}", headers=AUTH)
        assert r.status_code == 404, (bad, r.status_code)
        assert "SECRET" not in r.text


def test_session_route_survives_a_missing_engine(homes, client, monkeypatch):
    monkeypatch.setitem(sys.modules, "iron_jarvis.detections", None)  # import fails
    r = client.get(f"/history/sessions/codex/{CX_ID}", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["findings"] == [] and "unavailable" in r.json()["findings_error"]


def test_file_reading_runs_off_the_event_loop(homes, client, monkeypatch):
    loops = []
    real_list, real_load = history.list_sessions, history.load_session

    def _on_loop():
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def spy_list(*a, **k):
        loops.append(("list", _on_loop()))
        return real_list(*a, **k)

    def spy_load(*a, **k):
        loops.append(("load", _on_loop()))
        return real_load(*a, **k)

    monkeypatch.setattr(history, "list_sessions", spy_list)
    monkeypatch.setattr(history, "load_session", spy_load)
    assert client.get("/history/sessions", headers=AUTH).status_code == 200
    assert (
        client.get(f"/history/sessions/codex/{CX_ID}", headers=AUTH).status_code == 200
    )
    assert loops == [("list", False), ("load", False)]


def test_session_route_with_the_real_detections_engine(homes, client):
    """No stand-in: the packaged rules see the synthetic credential reads."""
    r = client.get(f"/history/sessions/claude-code/{CL_ID}", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "findings_error" not in body, body.get("findings_error")
    assert body["findings"], "cat ~/.aws/credentials should be a finding"
    assert all(f["session_id"] == CL_ID for f in body["findings"])
    r = client.get(f"/history/sessions/codex/{CX_ID}", headers=AUTH)
    body = r.json()
    assert "findings_error" not in body, body.get("findings_error")
    assert body["findings"], "Get-Content ...\\.ssh\\id_rsa should be a finding"


def test_a_credential_read_only_in_a_subagent_is_found(homes, client):
    """The real engine, the real route: the ONLY credential read in this
    session is a subagent's, and it is found and attributed to that subagent."""
    from iron_jarvis.detections import scan

    events = session_events("claude-code", CL2_ID)
    # without the subagent the session is clean — the control
    assert not [f for f in scan(_own(events)) if f.rule_id == "credential-file-read"]
    r = client.get(f"/history/sessions/claude-code/{CL2_ID}", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "findings_error" not in body, body.get("findings_error")
    creds = [f for f in body["findings"] if f["rule_id"] == "credential-file-read"]
    assert creds, body["findings"]
    evidence = [e for f in creds for e in f["events"]]
    assert any(
        e.get("subagent") == "agent-cccc" and ".kube/config" in e.get("command", "")
        for e in evidence
    ), evidence


# --------------------------------------------------------------------------- subagents


def test_subagent_events_are_folded_in_and_tagged(homes):
    events = session_events("claude-code", CL_ID)
    subs = [e for e in events if "subagent" in e]
    assert {e["subagent"] for e in subs} == {"agent-aaaa", "agent-bbbb"}
    bash = [e for e in subs if e.get("tool") == "Bash"]
    assert len(bash) == 1
    assert bash[0]["action"] == "command.executed"
    assert bash[0]["command"] == "ls C:/work/demo" and bash[0]["text"] == "build.ps1"
    assert bash[0]["session_id"] == CL_ID and bash[0]["source"] == "claude-code"
    assert (
        bash[0]["ref"].split("subagents")[1].startswith(("/agent-aaaa", "\\agent-aaaa"))
    )
    wf = [e for e in subs if e["subagent"] == "agent-bbbb"]
    assert [(e["action"], e["path"]) for e in wf] == [
        ("file.read", "C:/work/demo/notes.md")
    ]


def test_subagent_events_merge_by_timestamp(homes):
    events = session_events("claude-code", CL_ID)
    stamped = [e["ts"] for e in events if e.get("ts")]
    assert stamped == sorted(stamped)
    order = [(e.get("subagent"), e["action"]) for e in events]
    # agent-aaaa ran at 03:04:05.5, between the parent's Read (03:04:06) and the
    # parent's Bash (03:04:04)
    i_parent_bash = order.index((None, "command.executed"))
    i_sub_bash = order.index(("agent-aaaa", "command.executed"))
    i_parent_read = order.index((None, "file.read"))
    assert i_parent_bash < i_sub_bash < i_parent_read


def test_events_without_timestamps_keep_parent_first_then_name_order(homes, tmp_path):
    slug = homes / "claude" / "projects" / "C--nots"
    sid = "44444444-2222-4333-8444-555555555555"
    subdir = slug / sid / "subagents"
    subdir.mkdir(parents=True)

    def call(i: int, cmd: str) -> str:
        block = {
            "type": "tool_use",
            "id": f"t{i}",
            "name": "Bash",
            "input": {"command": cmd},
        }
        rec = {"type": "assistant", "sessionId": sid, "message": {"content": [block]}}
        return json.dumps(rec) + "\n"

    (slug / f"{sid}.jsonl").write_text(call(1, "parent"), encoding="utf-8")
    (subdir / "agent-b.jsonl").write_text(call(2, "b"), encoding="utf-8")
    (subdir / "agent-a.jsonl").write_text(call(3, "a"), encoding="utf-8")
    cmds = [e["command"] for e in session_events("claude-code", sid)]
    assert cmds == ["parent", "a", "b"]


# --------------------------------------------------------------------------- review minors


def test_find_session_refuses_all_and_none(homes):
    for bad in ("all", None, ""):
        with pytest.raises(ValueError):
            hindex.find_session(bad, CL_ID)


def test_session_route_answers_400_for_the_all_harness(homes, client):
    r = client.get(f"/history/sessions/all/{CL_ID}", headers=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"] == "harness must be one of claude-code, codex"


def test_glob_reads_the_folder_its_pattern_names(homes):
    from iron_jarvis.history import claude_code

    records = [
        (
            1,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "g1",
                            "name": "Glob",
                            "input": {
                                "pattern": ".ssh/id_*",
                                "path": "C:\\Users\\demo",
                            },
                        },
                        {
                            "type": "tool_use",
                            "id": "g2",
                            "name": "Glob",
                            "input": {"pattern": "~/.aws/*"},
                        },
                        {
                            "type": "tool_use",
                            "id": "g3",
                            "name": "Glob",
                            "input": {"pattern": "**/*.py", "path": "/repo"},
                        },
                        {
                            "type": "tool_use",
                            "id": "g4",
                            "name": "Grep",
                            "input": {"pattern": "key/value", "path": "/home/me/.ssh"},
                        },
                    ]
                },
            },
        )
    ]
    evs = claude_code.map_events(records, Path("x.jsonl"), "s")
    assert [(e["action"], e["path"]) for e in evs] == [
        ("file.read", "C:\\Users\\demo\\.ssh"),
        ("file.read", "~/.aws"),
        ("file.read", "/repo"),
        ("file.read", "/home/me/.ssh"),
    ]


def test_codex_js_is_a_command_with_its_code(homes):
    from iron_jarvis.history import codex

    args = json.dumps(
        {"code": "require('fs').readFileSync('/etc/passwd')", "title": "t"}
    )
    records = [
        (
            1,
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "js",
                    "arguments": args,
                    "call_id": "j1",
                },
            },
        ),
        (
            2,
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "j1",
                    "output": "root:x",
                },
            },
        ),
    ]
    (ev,) = codex.map_events(records, Path("r.jsonl"), "s")
    assert ev["action"] == "command.executed"
    assert ev["command"] == "require('fs').readFileSync('/etc/passwd')"
    assert ev["text"] == "root:x"


# --------------------------------------------------------------------------- paging


def _all_events(client, harness, sid, page=7):
    got, offset = [], 0
    while True:
        r = client.get(
            f"/history/sessions/{harness}/{sid}?offset={offset}&limit={page}",
            headers=AUTH,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        got += body["events"]
        offset += page
        if offset >= body["events_total"]:
            return got, body


def test_session_route_pages_events(homes, client):
    full = session_events("claude-code", CL_ID)
    assert len(full) > 10
    r = client.get(
        f"/history/sessions/claude-code/{CL_ID}?offset=3&limit=4", headers=AUTH
    )
    body = r.json()
    assert set(body) == {
        "session",
        "events",
        "events_total",
        "offset",
        "limit",
        "findings",
    }
    assert body["events"] == full[3:7]
    assert (body["events_total"], body["offset"], body["limit"]) == (len(full), 3, 4)
    default = client.get(f"/history/sessions/claude-code/{CL_ID}", headers=AUTH).json()
    assert (default["offset"], default["limit"]) == (0, 500)
    assert default["events"] == full
    paged, _ = _all_events(client, "claude-code", CL_ID)
    assert paged == full


def test_offset_past_the_end_is_an_empty_page(homes, client):
    r = client.get(
        f"/history/sessions/codex/{CX_ID}?offset=100000&limit=10", headers=AUTH
    )
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == [] and body["events_total"] > 0


def test_bad_paging_values_are_422(homes, client):
    base = f"/history/sessions/codex/{CX_ID}"
    for q in ("limit=0", "limit=2001", "offset=-1", "limit=x"):
        assert client.get(f"{base}?{q}", headers=AUTH).status_code == 422, q
    assert client.get(f"{base}?limit=2000", headers=AUTH).status_code == 200


def test_findings_cover_all_events_and_are_scanned_once(homes, client, monkeypatch):
    seen: list = []
    _fake_detections(monkeypatch, seen)
    total = len(session_events("claude-code", CL_ID))
    first = client.get(
        f"/history/sessions/claude-code/{CL_ID}?offset=0&limit=2", headers=AUTH
    ).json()
    second = client.get(
        f"/history/sessions/claude-code/{CL_ID}?offset=2&limit=2", headers=AUTH
    ).json()
    assert first["events"] != second["events"]
    assert first["findings"] == second["findings"] == [{"rule_id": "fake", "n": total}]
    assert len(seen) == 1, "two pages of an unchanged session must scan once"
    assert len(seen[0]) == total  # the scan saw ALL events, not the page
    # a changed session is scanned again
    with open(_claude_file(homes), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"type": "system", "sessionId": CL_ID}) + "\n")
    client.get(f"/history/sessions/claude-code/{CL_ID}?limit=2", headers=AUTH)
    assert len(seen) == 2


def test_real_findings_are_identical_across_pages(homes, client):
    pages = [
        client.get(
            f"/history/sessions/claude-code/{CL_ID}?offset={o}&limit=3", headers=AUTH
        ).json()
        for o in (0, 3, 9)
    ]
    assert pages[0]["findings"]
    assert pages[0]["findings"] == pages[1]["findings"] == pages[2]["findings"]


def test_at_most_two_session_loads_run_at_once(homes, monkeypatch):
    active = [0]
    peak = [0]
    lock = threading.Lock()
    release = threading.Event()
    real = history.load_session

    def slow_load(*a, **k):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        release.wait(5)
        with lock:
            active[0] -= 1
        return real(*a, **k)

    monkeypatch.setattr(history, "load_session", slow_load)
    threads = [
        threading.Thread(target=hroutes.session_page, args=("codex", CX_ID, 0, 10))
        for _ in range(4)
    ]
    for th in threads:
        th.start()
    deadline = time.monotonic() + 5
    while active[0] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # a third, if it could get in, has had time to
    assert active[0] == 2
    release.set()
    for th in threads:
        th.join(10)
    assert peak[0] == 2


# --------------------------------------------------------------------------- listing cost


def test_listing_limit_summarises_only_the_newest_files(homes, monkeypatch):
    slug = homes / "claude" / "projects" / "C--many"
    slug.mkdir()
    for i in range(6):
        sid = f"5555555{i}-2222-4333-8444-555555555555"
        rec = {"type": "user", "sessionId": sid, "message": {"content": f"p{i}"}}
        f = slug / f"{sid}.jsonl"
        f.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        os.utime(f, ns=(0, (2_000_000_000 + i) * 1_000_000_000))
    calls = []
    real = hindex.summarize
    monkeypatch.setattr(hindex, "summarize", lambda h, p: calls.append(p) or real(h, p))
    rows = list_sessions(limit=3)
    assert len(calls) <= 3
    assert {r["id"] for r in rows} == {
        f"5555555{i}-2222-4333-8444-555555555555" for i in (3, 4, 5)
    }


def test_find_session_reuses_a_young_listing(homes, monkeypatch):
    from iron_jarvis.history import claude_code

    walks = []
    real = claude_code.discover
    monkeypatch.setattr(claude_code, "discover", lambda: walks.append(1) or real())
    assert hindex.find_session("claude-code", CL_ID)["id"] == CL_ID
    assert hindex.find_session("claude-code", CL2_ID)["id"] == CL2_ID
    assert len(walks) == 1
    # an unknown id is still refused, after a fresh listing
    assert hindex.find_session("claude-code", "nope") is None
    assert len(walks) == 2
    # an expired listing is not reused
    monkeypatch.setattr(hindex, "LISTING_TTL_S", 0.0)
    hindex.find_session("claude-code", CL_ID)
    assert len(walks) == 3
