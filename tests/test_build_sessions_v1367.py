"""v1.367.0 (IJ harness Phase 4): the SESSION STORE behind Build's IJ harness.

An IJ harness session is a chat thread with ``surface = "build"``. These pins
drive the REAL app factory (real routes, real store, real search index):

* a Build session NEVER reaches a chat list — the list, the "@" menu and its
  resolver, history search and memory recall (the index), share, the Guide's
  search, the Status map — and a chat thread can never become a session or
  back (the chat PUT refuses a session; nothing writes ``surface`` but the
  create route);
* sessions are grouped by folder, newest first, archived hidden unless asked;
* a folder is refused IN WORDS when ``fs_policy.root_problem`` refuses it;
* ONE display-name store, and a project root's name IS the project's name
  (renaming the group renames the project); the folder on disk is never
  renamed;
* the post-write Python check stops at 256 KB, with no line and no compile.

Helpers here are imported by the other v1367 files.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import BuildFolderRecord, ChatThreadRecord, Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness import verify
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from tests.test_chat_turn_stop_v1241 import _asgi_post, _scope

CLAUDE = ("anthropic", "claude-sonnet-4-5")


# --------------------------------------------------------------------------- #
# shared helpers (imported by test_build_turns_v1367 / test_build_plan_ask_v1367)
# --------------------------------------------------------------------------- #


def frames_of(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        ev, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        out.append((ev, json.loads(data or "{}")))
    return out


class Script:
    """A scripted model on the router's ``stream()`` / ``complete()``. Each
    move is a list of ToolCalls, a reply text, or an awaitable factory
    ``async (script) -> move`` (to hold a turn mid-way). Past the script —
    and on a tool-less call — it answers "Done." Every call is recorded."""

    def __init__(self, moves: list, who: tuple[str, str] = CLAUDE) -> None:
        self.provider, self.model = who
        self.moves = list(moves)
        self.calls: list[dict[str, Any]] = []

    async def _next(self, system, messages, tools):
        self.calls.append({
            "system": system, "tools": [t["name"] for t in tools or []],
            "specs": list(tools or []), "messages": list(messages),
        })
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if callable(move):
            move = await move(self)
        return move

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None,
                     task_class=None, **kw):
        move = await self._next(system, messages, tools)
        if isinstance(move, tuple):  # (text, calls): words, then tool calls
            text, calls = move
            yield {"type": "text", "text": text}
            resp = LLMResponse(text=text, tool_calls=list(calls), usage={"input_tokens": 3, "output_tokens": 1})
        elif isinstance(move, str):
            yield {"type": "text", "text": move}
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None,
                       task_class=None, **kw):
        move = await self._next(system, messages, tools)
        if isinstance(move, tuple):
            return RouteResult(LLMResponse(text=move[0], tool_calls=list(move[1]),
                                           usage={"input_tokens": 3, "output_tokens": 1}),
                               self.provider, self.model)
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1}),
                               self.provider, self.model)
        return RouteResult(LLMResponse(text="", tool_calls=list(move),
                                       usage={"input_tokens": 3, "output_tokens": 1}),
                           self.provider, self.model)


def wire(app, script: Script) -> Script:
    app.state.platform.router.stream = script.stream
    app.state.platform.router.complete = script.complete
    return script


async def asgi_get(app, path: str) -> tuple[int, Any]:
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    status, chunks = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    p, _, q = path.partition("?")
    scope = {**_scope(p, b""), "method": "GET", "query_string": q.encode()}
    await asyncio.wait_for(app(scope, receive, send), timeout=30)
    raw = b"".join(chunks).decode("utf-8", "replace")
    try:
        return status, json.loads(raw or "{}")
    except ValueError:
        return status, raw


async def asgi(app, method: str, path: str, payload: dict | None = None) -> tuple[int, Any]:
    if method == "GET":
        return await asgi_get(app, path)
    if method == "POST":
        return await _asgi_post(app, path, payload)
    body = json.dumps(payload or {}).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status, chunks = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await app({**_scope(path, body), "method": method}, receive, send)
    raw = b"".join(chunks)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, raw.decode("utf-8", "replace")


async def open_turn(app, sid: str, body: dict, *, drop: asyncio.Event | None = None,
                    stop_after: str | None = None, timeout: float = 30.0) -> tuple[int, str]:
    """POST /build/sessions/{sid}/turn in-process. The connection drops when
    ``drop`` is set (or when ``stop_after`` appears in what was received)."""
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        if drop is not None:
            await drop.wait()
        else:
            await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    status, got = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            got.append(msg.get("body", b"").decode("utf-8", "replace"))
            if stop_after and stop_after in "".join(got) and drop is not None:
                drop.set()

    task = asyncio.create_task(app(_scope(f"/build/sessions/{sid}/turn", raw), receive, send))
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    return status, "".join(got)


async def until(fn, seconds: float = 15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.02)
    return fn()


def turn_log(app, turn_id: str):
    logs = getattr(app.state.platform, "turn_logs", None)
    return logs.get(turn_id) if logs is not None else None


def turn_frames(app, turn_id: str) -> list[tuple[str, dict]]:
    lg = turn_log(app, turn_id)
    if lg is None:
        return []
    return [(e, json.loads(d or "{}")) for e, d in lg.frames]


def thread_row(app, tid: str) -> ChatThreadRecord | None:
    with session_scope(app.state.platform.engine) as db:
        r = db.get(ChatThreadRecord, tid)
        if r is not None:
            db.expunge(r)
        return r


def messages_of(app, tid: str) -> list[dict]:
    r = thread_row(app, tid)
    return json.loads(r.messages_json or "[]") if r is not None else []


def setup_of(app, tid: str) -> dict:
    r = thread_row(app, tid)
    return json.loads(r.setup_json or "{}") if r is not None and r.setup_json else {}


@pytest.fixture()
def home(tmp_path) -> str:
    return str(tmp_path / "home")


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def new_session(client_or_app, folder: Path, **extra) -> dict:
    body = {"folder": str(folder), "provider": CLAUDE[0], "model": CLAUDE[1], **extra}
    if isinstance(client_or_app, TestClient):
        r = client_or_app.post("/build/sessions", json=body)
        assert r.status_code == 201, r.text
        return r.json()
    row = S.create(client_or_app.state.platform.engine, str(folder), extra.pop("title", ""),
                   {k: v for k, v in body.items() if k != "folder"})
    return {"id": row.id}


# --------------------------------------------------------------------------- #
# never in Chat
# --------------------------------------------------------------------------- #


def test_a_build_session_never_reaches_any_chat_list(home, work):
    client = TestClient(create_app(home))
    chat = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "hello chat"}],
                                                  "title": "the chat"}).json()["id"]
    sess = new_session(client, work, title="the build session")
    sid = sess["id"]
    assert sess["folder"] == str(work) and sess["mode"] == "act" and sess["session_id"] == f"build:{sid}"

    listed = [t["id"] for t in client.get("/chat/threads").json()["threads"]]
    assert listed == [chat], "a Build session leaked into the chat list"
    for which in ("only", "all"):
        assert sid not in [t["id"] for t in client.get("/chat/threads", params={"archived": which}).json()["threads"]]
    # The "@" menu and its resolver.
    refs = client.get("/chat/threads/search-refs", params={"q": "build"}).json()["threads"]
    assert refs == []
    from iron_jarvis.daemon.chat_refs import _load

    assert sid not in _load(client.app.state.platform.engine, [sid, chat]) and chat in _load(
        client.app.state.platform.engine, [sid, chat]
    )
    # Share answers as if it did not exist.
    assert client.post(f"/chat/threads/{sid}/share", json={"mode": "full"}).status_code == 404
    # Opening it by id says what it is.
    assert client.get(f"/chat/threads/{sid}").json()["surface"] == "build"
    assert client.get(f"/chat/threads/{chat}").json()["surface"] == "chat"
    # The Status map's Chats star counts chats only.
    smap = client.get("/ui/system-map").json()
    star = next(n for a in smap["areas"] for n in a["nodes"] if n["key"] == "chats")
    assert star["setup"] == 1 and star["used"] == 1


def test_the_guides_search_lists_chats_not_build_sessions(home, work):
    client = TestClient(create_app(home))
    client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "x"}], "title": "zebra chat"})
    new_session(client, work, title="zebra session")
    from iron_jarvis.guide.tools import AppSearchTool

    names = [row[2] for row in AppSearchTool(client.app.state.platform)._rows("thread")]
    assert "zebra chat" in names and "zebra session" not in names


def test_a_thread_saved_before_the_field_existed_is_a_chat(home, work):
    """``surface`` NULL (every thread before v1.367.0) must stay in the chat
    list: ``!= 'build'`` alone would drop it (SQL NULL compares unknown)."""
    client = TestClient(create_app(home))
    chat = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "old"}]}).json()["id"]
    from sqlalchemy import text

    with session_scope(client.app.state.platform.engine) as db:
        db.exec(text("UPDATE chatthreadrecord SET surface = NULL"))
        db.commit()
    assert thread_row(client.app, chat).surface is None
    assert [t["id"] for t in client.get("/chat/threads").json()["threads"]] == [chat]


def test_a_chat_can_never_become_a_session_or_back(home, work):
    client = TestClient(create_app(home))
    sid = new_session(client, work)["id"]
    # The chat page saving over a session is refused — one writer.
    r = client.put(f"/chat/threads/{sid}", json={"messages": [{"role": "user", "content": "hijack"}]})
    assert r.status_code == 409 and "Build session" in r.json()["detail"]
    r = client.put(f"/chat/threads/{sid}", json={"title": "x", "setup": {}})
    assert r.status_code == 409
    assert thread_row(client.app, sid).surface == "build" and messages_of(client.app, sid) == []
    # A new chat that asks to be one is still a chat.
    chat = client.put("/chat/threads/new", json={"messages": [], "surface": "build"}).json()["id"]
    assert thread_row(client.app, chat).surface == "chat"
    # The build routes never take a chat for a session.
    assert client.get(f"/build/sessions/{chat}").status_code == 404
    assert client.patch(f"/build/sessions/{chat}", json={"title": "y"}).status_code == 404


def test_a_session_is_never_indexed_for_history_search_or_recall(home, work):
    """History search and memory recall of chats read the search index — a
    session's lines never land there, live or by a (forced) backfill."""
    from iron_jarvis.core.db import search_index
    from iron_jarvis.daemon.chat_append import append_to_chat_thread

    client = TestClient(create_app(home))
    engine = client.app.state.platform.engine
    sid = new_session(client, work)["id"]
    S.append_lines(engine, sid, [
        {"role": "user", "content": "zanzibar marmalade"}, {"role": "assistant", "content": "ok"},
    ])
    # Even the chat's own daemon writer (an agent's line) does not index it.
    append_to_chat_thread(engine, sid, [{"role": "assistant", "content": "zanzibar again"}])
    # The control: the same words in a chat ARE found.
    chat = client.put("/chat/threads/new", json={
        "messages": [{"role": "user", "content": "zanzibar marmalade in a chat"}],
    }).json()["id"]
    index = search_index(engine)
    assert index is not None
    hit_threads = {h.thread_id for h in index.search("zanzibar")}
    assert chat in hit_threads and sid not in hit_threads
    # A forced reindex (the backfill) skips it too.
    cursor = None
    for _ in range(20):
        out = index.backfill(cursor=cursor, force=True)
        cursor = out.get("cursor")
        if out.get("done"):
            break
    hit_threads = {h.thread_id for h in index.search("zanzibar")}
    assert chat in hit_threads and sid not in hit_threads


# --------------------------------------------------------------------------- #
# grouping, folders, names
# --------------------------------------------------------------------------- #


def test_sessions_are_grouped_by_folder_newest_first(home, tmp_path):
    client = TestClient(create_app(home))
    a, b = tmp_path / "alpha", tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    s1 = new_session(client, a, title="one")["id"]
    s2 = new_session(client, b, title="two")["id"]
    s3 = new_session(client, a, title="three")["id"]
    # v1.372.0: Windows' clock ticks every 15.6 ms, so on a fast PC s3's
    # creation and s1's append landed on the SAME stamp and the order was a
    # coin toss (it failed 5 runs in 6 on an idle machine, at v1.371.0 too).
    # The append is meant to be LATER than s3's creation: make it so.
    time.sleep(0.05)
    S.append_lines(client.app.state.platform.engine, s1, [{"role": "user", "content": "newest"}])
    out = client.get("/build/sessions").json()["folders"]
    assert [f["name"] for f in out] == ["alpha", "beta"]
    assert [s["id"] for s in out[0]["sessions"]] == [s1, s3]
    assert out[0]["path"] == str(a) and out[0]["is_project"] is False and out[0]["project_id"] is None
    assert set(out[0]["sessions"][0]) == {"id", "title", "updated_at", "running", "waiting", "archived"}
    # Archived: hidden unless asked.
    assert client.post(f"/build/sessions/{s2}/archive", json={}).status_code == 200
    assert [f["name"] for f in client.get("/build/sessions").json()["folders"]] == ["alpha"]
    full = client.get("/build/sessions", params={"archived": "1"}).json()["folders"]
    beta = next(f for f in full if f["name"] == "beta")
    assert beta["sessions"][0]["archived"] is True
    assert client.post(f"/build/sessions/{s2}/unarchive").json()["archived"] is False
    # A chat never shows up as a group.
    client.put("/chat/threads/new", json={"messages": [], "setup": {"workspace_dir": str(a)}})
    assert sum(len(f["sessions"]) for f in client.get("/build/sessions").json()["folders"]) == 3


def test_a_folder_that_cannot_be_used_is_refused_in_words(home, tmp_path):
    client = TestClient(create_app(home))
    for folder, words in (
        ("relative/folder", "absolute"),
        (str(tmp_path / "missing"), "does not exist"),
    ):
        r = client.post("/build/sessions", json={"folder": folder})
        assert r.status_code == 400, r.text
        assert r.json()["detail"].startswith("This folder cannot be used:") and words in r.json()["detail"]
    assert client.get("/build/sessions").json()["folders"] == []
    assert client.post("/build/sessions", json={"folder": str(tmp_path), "mode": "sideways"}).status_code == 400


def test_one_display_name_store_and_a_project_root_is_the_projects_name(home, tmp_path):
    client = TestClient(create_app(home))
    plain, root, inner = tmp_path / "plain", tmp_path / "proj", tmp_path / "proj" / "sub"
    for f in (plain, root, inner):
        f.mkdir(parents=True)
    pid = client.post("/projects", json={"name": "Acme Site", "root": str(root)}).json()["id"]
    new_session(client, plain)
    new_session(client, root)
    new_session(client, inner)
    names = {f["path"]: f for f in client.get("/build/sessions").json()["folders"]}
    assert names[str(root)]["name"] == "Acme Site" and names[str(root)]["is_project"] is True
    assert names[str(root)]["project_id"] == pid
    # A folder INSIDE a project is not its root: it keeps its own name.
    assert names[str(inner)]["name"] == "sub" and names[str(inner)]["is_project"] is False

    # A plain folder's display name lives in the ONE store; the disk is untouched.
    r = client.post("/build/folders/rename", json={"path": str(plain), "name": "  My   Plain  "})
    assert r.json() == {"path": str(plain), "name": "My Plain", "is_project": False, "project_id": None}
    assert plain.is_dir() and plain.name == "plain"
    with session_scope(client.app.state.platform.engine) as db:
        rows = list(db.exec(select(BuildFolderRecord)))
    assert [(r.key, r.name) for r in rows] == [(S.folder_key(str(plain)), "My Plain")]
    assert {f["path"]: f["name"] for f in client.get("/build/sessions").json()["folders"]}[str(plain)] == "My Plain"

    # Renaming a project root renames the PROJECT — never a second name.
    r = client.post("/build/folders/rename", json={"path": str(root), "name": "Acme Website"})
    assert r.json()["is_project"] is True
    assert client.get(f"/projects/{pid}").json()["project"]["name"] == "Acme Website"
    assert root.name == "proj" and root.is_dir()
    with session_scope(client.app.state.platform.engine) as db:
        keys = [r.key for r in db.exec(select(BuildFolderRecord))]
    assert S.folder_key(str(root)) not in keys
    assert {f["path"]: f["name"] for f in client.get("/build/sessions").json()["folders"]}[str(root)] == "Acme Website"
    # And GET of a session reads the same one name.
    sid = client.get("/build/sessions").json()["folders"][0]["sessions"][0]["id"]
    view = client.get(f"/build/sessions/{sid}").json()
    assert view["folder_name"] in ("Acme Website", "My Plain", "sub")
    assert client.post("/build/folders/rename", json={"path": str(plain), "name": "   "}).status_code == 400


def test_a_display_name_left_from_before_a_folder_became_a_project_never_wins(home, tmp_path):
    client = TestClient(create_app(home))
    root = tmp_path / "later"
    root.mkdir()
    new_session(client, root)
    client.post("/build/folders/rename", json={"path": str(root), "name": "Old Label"})
    client.post("/projects", json={"name": "Real Project", "root": str(root)})
    assert client.get("/build/sessions").json()["folders"][0]["name"] == "Real Project"
    client.post("/build/folders/rename", json={"path": str(root), "name": "Renamed Project"})
    with session_scope(client.app.state.platform.engine) as db:
        assert list(db.exec(select(BuildFolderRecord))) == []
        assert [p.name for p in db.exec(select(Project))] == ["Renamed Project"]


def test_patch_is_partial_and_never_moves_the_folder(home, work, tmp_path):
    client = TestClient(create_app(home))
    sid = new_session(client, work, reasoning="high")["id"]
    other = tmp_path / "elsewhere"
    other.mkdir()
    r = client.patch(f"/build/sessions/{sid}", json={"title": "Renamed", "mode": "plan"})
    assert r.status_code == 200
    view = r.json()
    assert view["title"] == "Renamed" and view["mode"] == "plan"
    assert view["setup"]["provider"] == CLAUDE[0] and view["setup"]["reasoning"] == "high"
    assert view["folder"] == str(work)
    r = client.patch(f"/build/sessions/{sid}", json={"model": "", "mode": "act", "local_only": True})
    view = r.json()
    assert "model" not in view["setup"] and view["mode"] == "act" and view["setup"]["local_only"] is True
    assert setup_of(client.app, sid).get("mode") is None
    assert client.patch(f"/build/sessions/{sid}", json={"mode": "sideways"}).status_code == 400
    # The store itself never moves a session's folder, whoever asks.
    S.update(client.app.state.platform.engine, sid, setup={"workspace_dir": str(other), "reasoning": "low"})
    assert setup_of(client.app, sid)["workspace_dir"] == str(work)
    assert setup_of(client.app, sid)["reasoning"] == "low"
    assert client.patch(f"/build/sessions/{sid}", json={"folder": str(other)}).json()["folder"] == str(work)


def test_delete_removes_the_session(home, work):
    client = TestClient(create_app(home))
    sid = new_session(client, work)["id"]
    assert client.delete(f"/build/sessions/{sid}").json() == {"deleted": sid}
    assert thread_row(client.app, sid) is None
    assert client.delete(f"/build/sessions/{sid}").status_code == 404


# --------------------------------------------------------------------------- #
# the post-write Python check is capped (Phase 1 review)
# --------------------------------------------------------------------------- #


def test_a_python_file_over_256_kb_is_not_compiled_and_gets_no_line(tmp_path, monkeypatch):
    compiled: list[str] = []
    real = verify._check_python

    def spy(source, name):
        compiled.append(name)
        return real(source, name)

    monkeypatch.setattr(verify, "_check_python", spy)
    assert verify.MAX_PY_CHECK_BYTES == 256 * 1024
    big = tmp_path / "big.py"
    big.write_bytes(b"#" * (verify.MAX_PY_CHECK_BYTES + 1))
    assert verify.check_file(big) is None and compiled == []
    ok = tmp_path / "ok.py"
    ok.write_bytes(b"x = 1\n" + b"#" * (verify.MAX_PY_CHECK_BYTES - 10))
    assert verify.check_file(ok) == verify.PARSES and compiled == ["ok.py"]
    # Other types keep their own 2 MB line.
    js = tmp_path / "big.json"
    js.write_bytes(b" " * (verify.MAX_CHECK_BYTES + 1))
    assert verify.check_file(js) == "Check: skipped — the file is over 2 MB"


# --------------------------------------------------------------------------- #
# the two new tools are registered the way every built-in is
# --------------------------------------------------------------------------- #


def test_ask_user_and_exit_plan_mode_owe_every_registration(tmp_path):
    from iron_jarvis.agents import mission
    from iron_jarvis.agents.types import AgentType, get_agent_definition
    from iron_jarvis.core.config import default_permissions
    from iron_jarvis.core.trust import LOW_TRUST_DENY
    from iron_jarvis.harness import parity
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers import egress
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools
    from iron_jarvis.tools.base import Reversibility

    platform = build_platform(str(tmp_path / "home"))
    perms = default_permissions()
    assert parity.PHASE_4_TOOLS == ("ask_user", "exit_plan_mode")
    assert perms["ask_user"] == "allow" and perms["exit_plan_mode"] == "ask"
    for name in parity.PHASE_4_TOOLS:
        tool = platform.registry.get(name)
        assert tool is not None and tool.spec()["input_schema"]["type"] == "object", name
        assert tool.reversibility is Reversibility.READONLY, name
        assert egress.reaches_outside(tool, platform.registry) is False, name
        # A person must be present: never armed by a roster or a sentence.
        for agent_type in AgentType:
            assert name not in get_agent_definition(agent_type).tools, (agent_type, name)
        assert name not in AUTO_SAFE_TOOLS
        assert name in mission._RUN_TOOL_WORDS and "_" not in mission.tool_line(name, {}, True)
        # Low trust: neither writes anything a later run reads, and the door
        # is carded once-only every time — so neither is kept away.
        assert name not in LOW_TRUST_DENY
    for sentence in ("ask me which database to use", "exit plan mode", "plan it first then ask me"):
        assert not {"ask_user", "exit_plan_mode"} & set(select_auto_tools(sentence)), sentence
    assert mission.ASK_WORDS["exit_plan_mode"] == "start working on this plan"
    assert getattr(platform.registry.get("ask_user"), "deadline_exempt", False) is True
    assert getattr(platform.registry.get("exit_plan_mode"), "deadline_exempt", False) is False
