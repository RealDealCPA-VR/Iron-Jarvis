"""Artifacts reach the project Media view (v1.200.0, CONNECT-AUDIT item 1).

The consumer chain was already complete (ArtifactRecord.project_id →
creative.service list_media filter → GET /creative/items?project_id →
ProjectSurfaces Media view) but every chat/Studio producer wrote NULL:
chat runs as session_id="chat" — not a Session row — so the store's
session inheritance could never scope those artifacts. This file pins the
producer side:

1. ArtifactStore.save precedence — explicit ``project_id`` beats session
   inheritance, and "chat" (no Session row) yields NULL unless explicit.
2. The platform's wired ``_creative_sink`` threads ``project_id`` through to
   the ArtifactRecord row.
3. The pixio delivery path passes ``ctx.project_id`` to the sink as a
   KEYWORD, only when present — so pre-v1.200.0 five-arg sink doubles keep
   working (the sink call is exercised offline via the injected fake HTTP
   transport, the same harness test_creative.py uses).
4. /creative/ingest and /creative/upload accept an optional ``project_id``
   and the saved row carries it (and shows up in the filtered items list).
5. Chat lane end-to-end: a project-grounded POST /chat turn hands the tool a
   ToolContext whose ``project_id`` is the resolved project — proven with a
   fake tool registered over an allow-tier name, per the house harness.

Fully offline; router/transport monkeypatched per the house idioms.
"""

from __future__ import annotations

import asyncio
import base64

from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.artifacts.models  # noqa: F401  (register table before init_db)
import iron_jarvis.core.models  # noqa: F401  (register Session before init_db)
from iron_jarvis.artifacts.models import ArtifactRecord
from iron_jarvis.artifacts.store import ArtifactStore
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult
from iron_jarvis.tools.pixio import PixioStatusTool

_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepixels" * 20


def _row(engine, name: str) -> ArtifactRecord:
    with session_scope(engine) as db:
        rows = db.exec(select(ArtifactRecord).where(ArtifactRecord.name == name)).all()
        assert rows, f"no ArtifactRecord for {name!r}"
        return rows[-1]


# --------------------------------------------------------------------------- #
# 1 — ArtifactStore.save precedence
# --------------------------------------------------------------------------- #


def _store_with_session(tmp_path):
    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    with session_scope(engine) as db:
        db.add(Session(id="sess_1", project_id="proj_inherited"))
        db.commit()
    return ArtifactStore(tmp_path / "artifacts", engine=engine), engine


def test_explicit_project_id_beats_session_inheritance(tmp_path):
    store, engine = _store_with_session(tmp_path)
    store.save("a", b"x", session_id="sess_1", project_id="proj_explicit")
    assert _row(engine, "a").project_id == "proj_explicit"


def test_inheritance_still_covers_agent_session_producers(tmp_path):
    store, engine = _store_with_session(tmp_path)
    store.save("b", b"x", session_id="sess_1")
    assert _row(engine, "b").project_id == "proj_inherited"


def test_chat_is_not_a_session_row_so_only_explicit_scopes_it(tmp_path):
    """The audit finding, pinned: "chat" has no Session row, so inheritance
    finds nothing — NULL without an explicit id, scoped with one."""
    store, engine = _store_with_session(tmp_path)
    store.save("c", b"x", session_id="chat")
    assert _row(engine, "c").project_id is None
    store.save("d", b"x", session_id="chat", project_id="proj_chat")
    assert _row(engine, "d").project_id == "proj_chat"


# --------------------------------------------------------------------------- #
# 2 — the platform's wired _creative_sink threads project_id to the row
# --------------------------------------------------------------------------- #


def _wired_sink(platform):
    tool = platform.registry.get("pixio_status")
    assert tool is not None, "pixio_status not registered"
    sink = tool._artifact_sink
    assert sink is not None, "platform did not wire an artifact sink"
    return sink


def test_wired_creative_sink_forwards_project_id(platform):
    sink = _wired_sink(platform)
    sink("creative-sink-proj", _PNG, "out.png", "image", "chat", project_id="proj_9")
    assert _row(platform.engine, "creative-sink-proj").project_id == "proj_9"


def test_wired_creative_sink_still_accepts_the_old_five_args(platform):
    # Pre-v1.200.0 shape (positional, no project) must keep working.
    sink = _wired_sink(platform)
    sink("creative-sink-old", _PNG, "out.png", "image", None)
    assert _row(platform.engine, "creative-sink-old").project_id is None


# --------------------------------------------------------------------------- #
# 3 — pixio delivery passes ctx.project_id to the sink (keyword, only when set)
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload


def _fake_http(method, url, headers, json_body):
    if "/api/v1/generations/" in url:
        return _Resp(200, {"status": "succeeded", "outputUrl": "https://cdn.example/out.png"})
    resp = _Resp(200, {})
    resp.content = _PNG
    return resp


def _ctx(platform, tmp_path, project_id=None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    kwargs = {"project_id": project_id} if project_id else {}
    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="r1",
        config=platform.config, event_bus=platform.event_bus,
        engine=platform.engine, **kwargs,
    )


def test_pixio_delivery_passes_ctx_project_id_to_the_sink(platform, tmp_path):
    seen = {}

    def sink(name, blob, filename, kind, session_id=None, **kw):
        seen.update(name=name, kind=kind, session_id=session_id, kwargs=kw)

    tool = PixioStatusTool(key_resolver=lambda: "k", http=_fake_http, artifact_sink=sink)
    res = asyncio.run(
        tool.execute({"generation_id": "gen-p"}, _ctx(platform, tmp_path, "proj_px"))
    )
    assert res.ok and res.data.get("artifact") == "creative-gen-p"
    assert seen["kwargs"] == {"project_id": "proj_px"}
    assert seen["session_id"] == "chat"


def test_pixio_delivery_without_project_keeps_old_sink_doubles_working(
    platform, tmp_path
):
    """No project on the ctx → the kwarg is NOT sent, so a pre-v1.200.0
    five-arg sink double neither crashes nor loses the gallery copy."""
    seen = {}

    def old_sink(name, blob, filename, kind, session_id=None):  # 5 args, no **kw
        seen["name"] = name

    tool = PixioStatusTool(key_resolver=lambda: "k", http=_fake_http, artifact_sink=old_sink)
    res = asyncio.run(tool.execute({"generation_id": "gen-o"}, _ctx(platform, tmp_path)))
    assert res.ok and res.data.get("artifact") == "creative-gen-o"
    assert seen["name"] == "creative-gen-o"


def test_pixio_delivery_end_to_end_row_carries_the_project(platform, tmp_path):
    """The REAL wired sink under the REAL delivery path: generation → sink →
    ArtifactStore.save → ArtifactRecord.project_id (what Media filters on)."""
    tool = PixioStatusTool(
        key_resolver=lambda: "k", http=_fake_http, artifact_sink=_wired_sink(platform)
    )
    res = asyncio.run(
        tool.execute({"generation_id": "gen-e2e"}, _ctx(platform, tmp_path, "proj_e2e"))
    )
    assert res.ok
    assert _row(platform.engine, "creative-gen-e2e").project_id == "proj_e2e"


# --------------------------------------------------------------------------- #
# 4 — /creative/ingest and /creative/upload accept project_id
# --------------------------------------------------------------------------- #


def _client_and_project(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    root = tmp_path / "projroot"
    root.mkdir(exist_ok=True)
    pid = client.post("/projects", json={"name": "Media", "root": str(root)}).json()["id"]
    return client, pid


def test_creative_upload_with_project_id_scopes_the_row(tmp_path):
    client, pid = _client_and_project(tmp_path)
    r = client.post(
        "/creative/upload",
        json={
            "filename": "logo.png",
            "content_b64": base64.b64encode(_PNG).decode(),
            "project_id": pid,
        },
    )
    assert r.status_code == 200
    name = r.json()["name"]
    platform = client.app.state.platform
    assert _row(platform.engine, name).project_id == pid
    # ...and the CONSUMER chain sees it: the filtered items list includes it.
    items = client.get(f"/creative/items?project_id={pid}").json()["items"]
    assert any(i["name"] == name for i in items)


def test_creative_upload_without_project_id_stays_global(tmp_path):
    client, pid = _client_and_project(tmp_path)
    r = client.post(
        "/creative/upload",
        json={"filename": "solo.png", "content_b64": base64.b64encode(_PNG).decode()},
    )
    assert r.status_code == 200
    platform = client.app.state.platform
    assert _row(platform.engine, r.json()["name"]).project_id is None


def test_creative_ingest_with_project_id_scopes_the_row(tmp_path):
    client, pid = _client_and_project(tmp_path)
    src = tmp_path / "studio-out.png"
    src.write_bytes(_PNG)
    r = client.post("/creative/ingest", json={"path": str(src), "project_id": pid})
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] is True
    platform = client.app.state.platform
    assert _row(platform.engine, body["name"]).project_id == pid


# --------------------------------------------------------------------------- #
# 5 — chat lane: ToolContext.project_id reaches the tool
# --------------------------------------------------------------------------- #


class _ProbeTool(Tool):
    """Registered OVER the allow-tier image_info name so the armed-tool loop
    executes it without a permission prompt; records what ctx carried."""

    name = "image_info"
    description = "probe"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, seen: dict) -> None:
        self._seen = seen

    async def execute(self, args, ctx) -> ToolResult:
        self._seen["project_id"] = getattr(ctx, "project_id", None)
        return ToolResult(ok=True, output="probed")


def _tool_call_then_text(platform, monkeypatch):
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="", tool_calls=[ToolCall(id="t1", name="image_info", arguments={})]
                ),
                "mock", "mock",
            )
        return RouteResult(LLMResponse(text="done"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)


def test_project_grounded_chat_turn_hands_the_tool_its_project(tmp_path, monkeypatch):
    client, pid = _client_and_project(tmp_path)
    platform = client.app.state.platform
    seen: dict = {}
    platform.registry.register(_ProbeTool(seen))
    _tool_call_then_text(platform, monkeypatch)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "probe"}],
            "project_id": pid,
            "tools": ["image_info"],
        },
    )
    assert r.status_code == 200
    assert "image_info" in r.json()["tools_used"]
    assert seen["project_id"] == pid


def test_main_chat_without_project_hands_the_tool_none(tmp_path, monkeypatch):
    client, _pid = _client_and_project(tmp_path)
    platform = client.app.state.platform
    seen: dict = {}
    platform.registry.register(_ProbeTool(seen))
    _tool_call_then_text(platform, monkeypatch)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "probe"}], "tools": ["image_info"]},
    )
    assert r.status_code == 200
    assert seen["project_id"] is None


def test_run_code_sink_receives_ctx_project_id(tmp_path):
    """The code sink gets the chat lane's resolved project (v1.200.0).

    run_code's _record SWALLOWS sink errors, so this is the one place a broken
    project thread would fail silently — pin the positive path: a ctx carrying
    a project hands it to the sink as a keyword; a ctx without one calls the
    sink with NO project kwarg at all (older 7-arg sink doubles must survive).
    """
    import asyncio

    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.runcode import RunCodeTool

    calls: list[dict] = []

    def sink(name, language, code, session_id, exit_code, output, purpose="", **kw):
        calls.append(kw)

    ws = tmp_path / "ws"
    ws.mkdir()

    def ctx_with(project_id):
        return ToolContext(
            workspace=ws, session_id="chat", agent_run_id="chat",
            config=None, event_bus=None, engine=None, project_id=project_id,
        )

    asyncio.run(
        RunCodeTool(sink=sink).execute(
            {"language": "python", "code": "print(1)"}, ctx_with("proj_x")
        )
    )
    asyncio.run(
        RunCodeTool(sink=sink).execute(
            {"language": "python", "code": "print(2)"}, ctx_with(None)
        )
    )
    assert calls[0] == {"project_id": "proj_x"}
    assert calls[1] == {}  # no kwarg -> legacy sink doubles keep working
