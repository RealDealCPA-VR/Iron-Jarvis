"""POST /chat/changes (v1.328.0, calm chat B5): what a chat turn changed.

The answer comes from the UNDO JOURNAL only — the before-bytes the registry
captured, the workspace it stamped, the post-write hash — plus the file on
disk now. Driven end to end through the real ``build_platform`` + the real
registry ``invoke`` for the write tools, and once through ``create_app`` so the
route is proven REACHABLE in the product, not only on a bare app.

Pinned here:

* modified / created / deleted are told apart, with exact +/- counts and the
  exact unified diff lines;
* two writes in one turn diff the FIRST before against the file now;
* a later edit says ``changed_since`` (and a CRLF re-save of the same text
  does not);
* the window and the ``paths`` filter decide what is reported, and a client
  path the journal never recorded is NEVER read;
* failed calls, other sessions' rows and pre-stamp rows are left out;
* binary, too large, truncated, a consumed pre-image and a protected path
  each answer honestly;
* the work runs through ``asyncio.to_thread``.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import PermissionMode, ToolInvocation, UndoJournal
from iron_jarvis.daemon.routes import chat_changes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext


def _client(platform) -> TestClient:
    app = FastAPI()
    chat_changes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _ctx(platform, workspace: Path, session_id: str = "chat") -> ToolContext:
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id=session_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _invoke(platform, ctx, name, args):
    res = asyncio.run(
        platform.registry.invoke(name, args, ctx, platform.permissions, session_allow=[name])
    )
    assert res.ok, res.error
    return res


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _since() -> str:
    """A turn start a moment ago, as a browser would send it (``...+00:00``)."""
    return _iso(utcnow() - timedelta(seconds=1))


def _post(client: TestClient, **body) -> dict:
    r = client.post("/chat/changes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _journal(
    platform,
    *,
    workspace: Path | None,
    path: str,
    kind: str = "file_restore",
    prior: bytes | None = None,
    pre_ref: str | None = None,
    post_sha: str | None = None,
    mode: str = "text",
    ok: bool = True,
    session_id: str = "chat",
    created_at: datetime | None = None,
) -> str:
    """Write one ToolInvocation + its UndoJournal row by hand (for the shapes
    the real tools cannot be made to produce on demand)."""
    meta: dict = {"path": path, "mode": mode, "data": None}
    if prior is not None:
        meta["data"] = base64.b64encode(prior).decode("ascii")
    if workspace is not None:
        meta["workspace"] = str(workspace.resolve())
    inv_id = new_id("inv")
    with session_scope(platform.engine) as db:
        db.add(
            ToolInvocation(
                id=inv_id,
                session_id=session_id,
                agent_run_id=session_id,
                tool="write_file",
                args_json="{}",
                verdict=PermissionMode.ALLOW,
                ok=ok,
                output="",
                reversibility="reversible",
                **({"created_at": created_at} if created_at else {}),
            )
        )
        db.add(
            UndoJournal(
                action_id=inv_id,
                session_id=session_id,
                agent_run_id=session_id,
                tool="write_file",
                kind=kind,
                reversible=True,
                pre_ref=pre_ref,
                pre_inline=json.dumps(meta),
                post_sha256=post_sha,
            )
        )
        db.commit()
    return inv_id


# --------------------------------------------------------------- the shapes --


def test_a_modified_file_reports_exact_counts_and_diff_lines(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "memo.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    since = _since()

    _invoke(platform, ctx, "write_file", {"path": "memo.txt", "content": "alpha\nBETA\ngamma\ndelta\n"})

    out = _post(_client(platform), since=since)
    assert out["files"] == 1 and out["added"] == 2 and out["removed"] == 1
    assert out["truncated_files"] is False
    (c,) = out["changes"]
    assert c["status"] == "modified"
    assert c["rel"] == "memo.txt" and c["name"] == "memo.txt"
    assert Path(c["path"]) == (ws / "memo.txt").resolve()
    assert (c["added"], c["removed"]) == (2, 1)
    lines = c["diff"].splitlines()
    assert lines[0] == "--- a/memo.txt" and lines[1] == "+++ b/memo.txt"
    assert "-beta" in lines and "+BETA" in lines and "+delta" in lines
    assert " alpha" in lines and " gamma" in lines
    assert c["changed_since"] is False
    assert c["binary"] is False and c["truncated"] is False and c["undone"] is False


def test_a_created_file_is_created_with_every_line_added(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()

    _invoke(platform, ctx, "write_file", {"path": "out/new.md", "content": "one\ntwo\nthree\n"})

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["status"] == "created"
    assert c["rel"] == "out/new.md"
    assert (c["added"], c["removed"]) == (3, 0)
    assert [ln for ln in c["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++")] == [
        "+one", "+two", "+three",
    ]
    assert c["changed_since"] is False


def test_two_writes_in_one_turn_diff_the_first_before_against_now(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "a.txt").write_text("v1\n", encoding="utf-8")
    since = _since()

    _invoke(platform, ctx, "write_file", {"path": "a.txt", "content": "v2\n"})
    _invoke(platform, ctx, "write_file", {"path": "a.txt", "content": "v3\n"})

    out = _post(_client(platform), since=since)
    assert out["files"] == 1  # one row per FILE, not per write
    (c,) = out["changes"]
    assert "-v1" in c["diff"].splitlines() and "+v3" in c["diff"].splitlines()
    assert "v2" not in c["diff"]  # the in-between version is not the "before"
    assert c["changed_since"] is False  # the LAST write's hash matches the disk


def test_a_later_edit_is_said_honestly_but_a_crlf_resave_is_not(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    target = ws / "n.txt"
    target.write_text("x\n", encoding="utf-8")
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "n.txt", "content": "y\nz\n"})
    client = _client(platform)

    # The same text saved with CRLF line ends is NOT a later change.
    target.write_bytes(b"y\r\nz\r\n")
    (c,) = _post(client, since=since)["changes"]
    assert c["changed_since"] is False

    # A real later edit is, and the diff shows the file as it is NOW.
    target.write_bytes(b"y\nz\nlater\n")
    (c,) = _post(client, since=since)["changes"]
    assert c["changed_since"] is True
    assert "+later" in c["diff"].splitlines()


def test_a_file_removed_after_the_turn_reads_deleted(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    target = ws / "gone.txt"
    target.write_text("keep\nme\n", encoding="utf-8")
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "gone.txt", "content": "new\n"})
    target.unlink()

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["status"] == "deleted"
    assert c["changed_since"] is True
    assert (c["added"], c["removed"]) == (0, 2)


def test_a_created_file_that_is_gone_says_so_and_reads_nothing(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "tmp.txt", "content": "a\n"})
    (ws / "tmp.txt").unlink()

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["status"] == "created"
    assert c["changed_since"] is True
    assert c["note"] == "The file is no longer there."
    assert c["diff"] == "" and c["added"] is None


# ---------------------------------------------------------- what is counted --


def test_the_window_decides_and_an_older_write_is_left_out(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    (ws).mkdir()
    (ws / "old.txt").write_text("now\n", encoding="utf-8")
    _journal(
        platform, workspace=ws, path="old.txt", prior=b"then\n",
        created_at=utcnow() - timedelta(hours=1),
    )
    client = _client(platform)

    assert _post(client, since=_since())["changes"] == []
    # Control: a window that holds the row reports it.
    out = _post(client, since=_iso(utcnow() - timedelta(hours=2)))
    assert [c["rel"] for c in out["changes"]] == ["old.txt"]
    # `until` closes the window too.
    out = _post(
        client,
        since=_iso(utcnow() - timedelta(hours=2)),
        until=_iso(utcnow() - timedelta(hours=1, minutes=30)),
    )
    assert out["changes"] == []


def test_paths_narrow_the_answer_and_an_unrecorded_path_is_never_read(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "one.txt", "content": "1\n"})
    _invoke(platform, ctx, "write_file", {"path": "two.txt", "content": "2\n"})
    secret = tmp_path / "elsewhere" / "notes.txt"
    secret.parent.mkdir()
    secret.write_text("private\n", encoding="utf-8")
    client = _client(platform)

    out = _post(client, since=since, paths=[str((ws / "two.txt").resolve())])
    assert [c["rel"] for c in out["changes"]] == ["two.txt"]

    # A path the journal never recorded: nothing reported, and the file is
    # never opened (every read goes through _read_capped).
    opened: list[Path] = []
    real = chat_changes._read_capped
    monkeypatch.setattr(
        chat_changes, "_read_capped", lambda p: (opened.append(Path(p)), real(p))[1]
    )
    out = _post(client, since=since, paths=[str(secret)])
    assert out["changes"] == [] and out["files"] == 0
    assert opened == []


def test_failed_calls_other_sessions_and_unstamped_rows_are_left_out(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ws.mkdir()
    for name in ("failed.txt", "agent.txt", "unstamped.txt", "kept.txt"):
        (ws / name).write_text("after\n", encoding="utf-8")
    since = _since()
    _journal(platform, workspace=ws, path="failed.txt", prior=b"b\n", ok=False)
    _journal(platform, workspace=ws, path="agent.txt", prior=b"b\n", session_id="sess_1")
    _journal(platform, workspace=None, path="unstamped.txt", prior=b"b\n")
    _journal(platform, workspace=ws, path="kept.txt", prior=b"b\n")

    out = _post(_client(platform), since=since)
    assert [c["rel"] for c in out["changes"]] == ["kept.txt"]


def test_a_journal_path_that_escapes_its_workspace_is_never_read(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ws.mkdir()
    (tmp_path / "outside.txt").write_text("secret\n", encoding="utf-8")
    since = _since()
    _journal(platform, workspace=ws, path="../outside.txt", prior=b"x\n")

    assert _post(_client(platform), since=since)["changes"] == []


# ------------------------------------------------------------ honest limits --


def test_a_binary_file_says_binary_file(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "pic.bin").write_bytes(b"\x89PNG\x00\x01after")
    since = _since()
    _journal(platform, workspace=ws, path="pic.bin", prior=b"\x89PNG\x00\x01before", mode="raw")

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["binary"] is True
    assert c["diff"] == "binary file"
    assert c["added"] is None and c["removed"] is None


def test_a_long_diff_is_cut_but_the_counts_cover_all_of_it(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    body = "".join(f"line {i}\n" for i in range(400))
    _invoke(platform, ctx, "write_file", {"path": "big.txt", "content": body})
    monkeypatch.setattr(chat_changes, "DIFF_CAP", 300)

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["truncated"] is True
    assert len(c["diff"]) <= 300
    assert c["added"] == 400 and c["removed"] == 0
    assert not c["diff"].endswith("\n")  # cut on a line end, never mid-line
    assert all(ln.startswith(("+", "-", " ", "@")) for ln in c["diff"].splitlines())


def test_a_file_past_the_read_cap_is_not_compared(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "huge.txt", "content": "x" * 500})
    monkeypatch.setattr(chat_changes, "READ_CAP", 100)

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["note"] == "Too large to compare here."
    assert c["truncated"] is True and c["diff"] == ""


def test_a_consumed_preimage_says_there_is_nothing_to_compare(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "r.txt").write_text("restored\n", encoding="utf-8")
    since = _since()
    _journal(platform, workspace=ws, path="r.txt", pre_ref="undo_gone-0000.bin")

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["status"] == "modified"
    assert c["note"] == "The earlier version is no longer kept, so there is nothing to compare."
    assert c["diff"] == "" and c["added"] is None


def test_an_undone_write_is_flagged(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "u.txt").write_text("before\n", encoding="utf-8")
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "u.txt", "content": "after\n"})
    with session_scope(platform.engine) as db:
        inv = db.exec(
            select(ToolInvocation).where(ToolInvocation.tool == "write_file")
        ).one()
        inv.undone_at = utcnow()
        db.add(inv)
        db.commit()

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["undone"] is True


def test_a_protected_target_is_named_and_not_read(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "k.txt", "content": "k\n"})
    monkeypatch.setattr(chat_changes, "fs_read_ok", lambda p: (False, "path is protected"))
    opened: list[Path] = []
    monkeypatch.setattr(chat_changes, "_read_capped", lambda p: (opened.append(p), (None, False))[1])

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["note"] == "Not shown: path is protected."
    assert c["diff"] == "" and opened == []


def test_until_before_since_is_a_422(tmp_path):
    platform = build_platform(str(tmp_path))
    now = utcnow()
    r = _client(platform).post(
        "/chat/changes",
        json={"since": _iso(now), "until": _iso(now - timedelta(minutes=5))},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "until is before since"


def test_the_work_runs_off_the_event_loop(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    hops: list[str] = []
    real = chat_changes.asyncio.to_thread

    async def spy(fn, *a, **kw):
        hops.append(getattr(fn, "__name__", ""))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(chat_changes.asyncio, "to_thread", spy)
    _post(_client(platform), since=_since())
    assert hops == ["collect_changes"]


# ------------------------------------------------------- the real app factory --


@pytest.mark.asyncio
async def test_the_route_is_reachable_in_the_real_app(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    app = create_app(str(tmp_path))
    status, out = await _asgi_post(app, "/chat/changes", {"since": _since()})
    assert status == 200, out
    assert out == {"changes": [], "files": 0, "added": 0, "removed": 0, "truncated_files": False}
