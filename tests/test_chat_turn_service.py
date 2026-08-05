"""Characterization tests for the chat TURN (Pair T, v1.136.0 keystone).

Written against POST /chat BEFORE the turn was extracted into
``daemon/chat_turn.run_chat_turn`` — every test here captures CURRENT
behavior, so the whole file must pass unchanged on both sides of the move.
That is the point of characterization: the refactor is proven mechanical.

Idiom: ``platform.router.complete`` is monkeypatched to return a
``RouteResult`` per round (the same pattern tests/test_chat_armed_tools_perm.py
uses), so every test is hermetic and deterministic.

The tail of the file (post-move additions, clearly marked) covers the three
ADDITIVE route tweaks Pair T ships alongside the lift: owner/comm_channel/
comm_display on the thread GETs, and the PUT 409 guard for daemon-owned
threads.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _png(path) -> None:
    from PIL import Image

    Image.new("RGB", (2, 2), (255, 0, 0)).save(path, format="PNG")


def _text_route(text: str, usage: dict | None = None) -> RouteResult:
    resp = LLMResponse(text=text)
    if usage is not None:
        resp.usage = usage
    return RouteResult(resp, "mock", "mock")


# --- history passthrough ------------------------------------------------------


def test_history_passthrough_and_last_30_cap(tmp_path, monkeypatch):
    """The client owns the history; the turn forwards it VERBATIM to the model,
    capped at the last 30 messages, roles preserved (unknown roles -> user)."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    seen = {}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        seen["messages"] = list(messages)
        seen["task_class"] = task_class
        return _text_route("ok")

    monkeypatch.setattr(platform.router, "complete", fake_complete)

    history = []
    for i in range(34):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": f"msg-{i}"})
    history.append({"role": "user", "content": "the live question"})

    r = client.post("/chat", json={"messages": history})
    assert r.status_code == 200
    got = seen["messages"]
    assert len(got) == 30                      # last-30 cap
    assert got[0].content == "msg-5"           # 35 sent, first 5 dropped
    assert got[-1].content == "the live question"
    assert got[-1].role == "user"
    assert got[0].role == "assistant"          # msg-5 (odd index) was assistant
    assert got[1].role == "user" and got[1].content == "msg-6"
    assert seen["task_class"] == "chat"
    assert r.json()["reply"] == "ok"


# --- skill invocation ---------------------------------------------------------


def test_unknown_skill_is_a_404(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def fake_complete(**kw):  # must never be reached
        raise AssertionError("the model was called for an unknown skill")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "skill": "does-not-exist-xyz",
        },
    )
    assert r.status_code == 404
    assert "does-not-exist-xyz" in r.json()["detail"]


def test_skill_playbook_is_injected_into_the_system_prompt(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    seen = {}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        seen["system"] = system
        return _text_route("done")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "redact the attached"}],
            "skill": "pii-redaction",  # a shipped builtin skill
        },
    )
    assert r.status_code == 200
    assert "# Skill invoked by the user" in seen["system"]
    assert "FOLLOW this playbook" in seen["system"]
    assert r.json()["skill"] == "pii-redaction"


# --- armed tools: execution + round-2 feedback --------------------------------


def test_armed_tool_executes_and_result_feeds_round_2(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    img = root / "pic.png"
    _png(img)

    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Img", "root": str(root)}).json()["id"]
    platform = client.app.state.platform
    seen = {"round2": ""}
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(id="t1", name="image_info",
                                 arguments={"path": str(img)})
                    ],
                ),
                "mock", "mock",
            )
        seen["round2"] = " ".join(
            (m.content or "") for m in messages if m.role == "tool"
        )
        return _text_route("Looked at it.")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "inspect pic.png"}],
            "project_id": pid,
            "tools": ["image_info"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    # The tool RAN, and its real output reached the model in round 2.
    assert "PNG" in seen["round2"] and "2x2" in seen["round2"]
    assert "image_info" in body["tools_used"]
    assert body["reply"] == "Looked at it."


# --- the escalate exit --------------------------------------------------------


def test_escalate_exit_stops_the_turn_with_reason(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        return RouteResult(
            LLMResponse(
                text="Handing this off.",
                tool_calls=[
                    ToolCall(id="e1", name="escalate_to_agent",
                             arguments={"reason": "needs to edit several files"})
                ],
            ),
            "mock", "mock",
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "refactor the app"}]}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["escalate"] is True
    assert body["escalate_reason"] == "needs to edit several files"
    assert body["reply"] == "Handing this off."
    assert body["tools_used"] == []      # the exit never executes anything
    assert n["i"] == 1                   # the turn stopped on the exit


def test_round_budget_exhaustion_forces_escalate_with_stopped_note(
    tmp_path, monkeypatch
):
    """A model that keeps asking for tools runs out of road: the LAST round is
    completion-only, its unexecuted calls are skipped with an honest note, and
    the turn escalates."""
    from iron_jarvis.daemon.routes.chat import _MAX_TOOL_ROUNDS

    client = _client(tmp_path)
    platform = client.app.state.platform
    completions = {"n": 0}
    invoked = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        completions["n"] += 1
        return RouteResult(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id=str(completions["n"]), name="list_folder",
                             arguments={"path": "."})
                ],
                usage={"input_tokens": 10, "output_tokens": 5},
            ),
            "mock", "mock",
        )

    real_invoke = platform.registry.invoke

    async def spy_invoke(*a, **kw):
        invoked["n"] += 1
        return await real_invoke(*a, **kw)

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", spy_invoke)

    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "explore everything"}],
            "tools": ["list_folder"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["escalate"] is True
    assert body["escalate_reason"]       # forced escalation carries a reason
    assert completions["n"] == _MAX_TOOL_ROUNDS       # budget fully used…
    assert invoked["n"] == _MAX_TOOL_ROUNDS - 1       # …last round never runs tools
    assert f"stopped after {_MAX_TOOL_ROUNDS - 1} tool rounds" in body["reply"]
    assert "not executed" in body["reply"]


# --- the workflow-draft exit --------------------------------------------------


def test_workflow_draft_exit_returns_the_sanitized_draft(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        return RouteResult(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="w1", name="workflow_draft",
                        arguments={
                            "name": "Weekly Report!!",
                            "description": "compile the weekly report",
                            "steps": [
                                {"name": "Gather", "agent": "researcher",
                                 "task": "collect the numbers"},
                                {"name": "Write", "agent": "nonsense-agent",
                                 "task": "write it up"},
                            ],
                        },
                    )
                ],
            ),
            "mock", "mock",
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "every friday compile…"}]},
    )
    assert r.status_code == 200
    body = r.json()
    draft = body["workflow_draft"]
    assert draft is not None
    assert draft["name"] == "Weekly-Report"          # slugged to a safe charset
    assert draft["description"] == "compile the weekly report"
    assert [s["name"] for s in draft["steps"]] == ["Gather", "Write"]
    assert draft["steps"][0]["agent"] == "researcher"
    assert draft["steps"][1]["agent"] == "builder"   # unknown agent coerced
    assert all(s["tool"] is None for s in draft["steps"])
    assert body["escalate"] is False
    # A draft exit is a SUCCESS: no "(no reply)" placeholder for the card.
    assert body["reply"] == ""


# --- denied tools are reported honestly ---------------------------------------


def test_denied_armed_tool_gets_a_note_and_stays_out_of_tools_used(
    tmp_path, monkeypatch
):
    from iron_jarvis.tools.base import ToolResult

    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(id="d1", name="image_info",
                                 arguments={"path": "x.png"})
                    ],
                ),
                "mock", "mock",
            )
        return _text_route("Could not inspect it.")

    async def deny_invoke(*a, **kw):
        return ToolResult(ok=False, error="permission denied: images")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", deny_invoke)

    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "inspect x.png"}],
            "tools": ["image_info"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tools_used"] == []      # a denied call is not honestly "used"
    assert "image_info could not run (permission denied)" in body["reply"]


# --- response contract --------------------------------------------------------


def test_response_dict_keys_exactly(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        return _text_route("hello")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    body = r.json()
    # v1.139.0 (capability roster): the response dict grew EXACTLY ONE key —
    # "escalate_agent" (the validated roster target; None = caller default).
    # This is the one deliberate, pinned contract change of that arc.
    assert set(body.keys()) == {
        "reply", "provider", "model", "attached", "images", "skill",
        "tools_used", "documents", "auto_armed", "escalate",
        "escalate_reason", "escalate_agent", "workflow_draft",
    }
    assert body["reply"] == "hello"
    assert body["provider"] == "mock" and body["model"] == "mock"
    assert body["attached"] == 0 and body["images"] == 0
    assert body["skill"] is None
    assert body["tools_used"] == [] and body["documents"] == []
    assert body["auto_armed"] == []
    assert body["escalate"] is False and body["escalate_reason"] == ""
    assert body["escalate_agent"] is None
    assert body["workflow_draft"] is None


def test_empty_messages_is_a_400(tmp_path):
    client = _client(tmp_path)
    assert client.post("/chat", json={"messages": []}).status_code == 400


# --- usage ledger -------------------------------------------------------------


def test_usage_agentrun_row_written_for_the_whole_turn(tmp_path, monkeypatch):
    """A multi-round tool turn persists ONE AgentRun(session_id="chat") row
    carrying the summed token usage across every completed round."""
    root = tmp_path / "w"
    root.mkdir()
    img = root / "p.png"
    _png(img)

    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "U", "root": str(root)}).json()["id"]
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(id="u1", name="image_info",
                                 arguments={"path": str(img)})
                    ],
                    usage={"input_tokens": 10, "output_tokens": 5},
                ),
                "mock", "mock",
            )
        return _text_route("done", usage={"input_tokens": 10, "output_tokens": 5})

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "inspect p.png"}],
            "project_id": pid,
            "tools": ["image_info"],
        },
    )
    assert r.status_code == 200

    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentRun

    with session_scope(platform.engine) as db:
        rows = [x for x in db.exec(select(AgentRun)) if x.session_id == "chat"]
    assert len(rows) == 1
    run = rows[0]
    assert run.state.value == "completed"
    assert run.steps == 2                      # two billed completions
    assert run.input_tokens == 20 and run.output_tokens == 10
    assert run.provider == "mock" and run.model == "mock"


# --- the 502 contract: usage persisted BEFORE the failure surfaces ------------


def test_router_failure_is_502_and_persists_the_partial_usage(tmp_path, monkeypatch):
    """A round-2 failure returns 502 with the raw error as detail, and the
    round that DID complete is still billed to the ledger (state=failed) —
    the persist-then-raise order from before the extraction."""
    root = tmp_path / "w2"
    root.mkdir()
    img = root / "q.png"
    _png(img)

    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "F", "root": str(root)}).json()["id"]
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(id="f1", name="image_info",
                                 arguments={"path": str(img)})
                    ],
                    usage={"input_tokens": 11, "output_tokens": 7},
                ),
                "mock", "mock",
            )
        raise RuntimeError("provider melted mid-turn")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "inspect q.png"}],
            "project_id": pid,
            "tools": ["image_info"],
        },
    )
    assert r.status_code == 502
    assert "provider melted mid-turn" in r.json()["detail"]

    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentRun

    with session_scope(platform.engine) as db:
        rows = [x for x in db.exec(select(AgentRun)) if x.session_id == "chat"]
    assert len(rows) == 1                    # round 1 was billed despite the 502
    run = rows[0]
    assert run.state.value == "failed"
    assert run.steps == 1
    assert run.input_tokens == 11 and run.output_tokens == 7


# --- the unarmed-loop break (the ctx/tool_ws reachability guarantee) ----------


def test_hallucinated_tool_call_with_nothing_armed_ends_the_turn_cleanly(
    tmp_path, monkeypatch
):
    """No tools armed: ``ctx``/``tool_ws`` are never defined, and the loop is
    guaranteed to break on ``not armed`` BEFORE any invoke — so a model that
    hallucinates a tool call anyway must yield a clean 200 (its text as the
    reply, nothing 'used'), never a NameError-turned-502. Pins the exact
    ordering gotcha called out in the extraction spec."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        n["i"] += 1
        return RouteResult(
            LLMResponse(
                text="I would list the files.",
                tool_calls=[
                    ToolCall(id="h1", name="list_folder", arguments={"path": "."})
                ],
            ),
            "mock", "mock",
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "list my files"}]}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "I would list the files."
    assert body["tools_used"] == []
    assert body["escalate"] is False
    assert n["i"] == 1                       # the break fired on round 1


# --- project spine ------------------------------------------------------------


def test_project_spine_block_injected_when_project_id_set(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    client = _client(tmp_path)
    pid = client.post(
        "/projects", json={"name": "Tax Season", "root": str(root)}
    ).json()["id"]
    pr = client.patch(
        f"/projects/{pid}", json={"instructions": "Always cite the tax code section."}
    )
    assert pr.status_code == 200
    platform = client.app.state.platform
    seen = {}

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class):
        seen["system"] = system
        return _text_route("ok")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "what about form 8829?"}],
            "project_id": pid,
        },
    )
    assert r.status_code == 200
    assert "# Project: Tax Season" in seen["system"]
    assert "Always cite the tax code section." in seen["system"]

    # And WITHOUT a project_id the block stays out (main chat is agnostic).
    r2 = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hello"}]}
    )
    assert r2.status_code == 200
    assert "# Project:" not in seen["system"]


# =============================================================================
# POST-MOVE ADDITIONS (not characterization): the three additive route tweaks
# Pair T ships in routes/chat.py for the comm-thread arc — owner/comm_channel/
# comm_display on the thread GETs, and the PUT 409 guard for daemon-owned
# threads. Assertions are getattr-tolerant of the additive columns.
# =============================================================================


def _mk_daemon_thread(platform) -> str:
    """Insert a daemon-owned thread directly (the comm layer's job); returns
    its id. Uses model kwargs when the additive columns exist."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    kwargs = {}
    if "owner" in ChatThreadRecord.model_fields:
        kwargs = {"owner": "daemon", "comm_channel": "telegram",
                  "comm_display": "Val"}
    rec = ChatThreadRecord(
        title="Telegram · Val",
        messages_json='[{"role": "user", "content": "hi from the phone"}]',
        **kwargs,
    )
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def test_thread_routes_carry_additive_comm_fields(tmp_path):
    client = _client(tmp_path)
    # A normal user-created thread defaults to owner "user", no comm binding.
    tid = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "hello"}]},
    ).json()["id"]

    rows = client.get("/chat/threads").json()["threads"]
    row = next(t for t in rows if t["id"] == tid)
    assert row["owner"] == "user"
    assert row["comm_channel"] == "" and row["comm_display"] == ""

    detail = client.get(f"/chat/threads/{tid}").json()
    assert detail["owner"] == "user"
    assert detail["comm_channel"] == "" and detail["comm_display"] == ""


def test_daemon_thread_surfaces_its_comm_binding(tmp_path):
    import pytest

    from iron_jarvis.core.models import ChatThreadRecord

    if "owner" not in ChatThreadRecord.model_fields:
        pytest.skip("owner column not landed yet (Pair M)")
    client = _client(tmp_path)
    tid = _mk_daemon_thread(client.app.state.platform)

    row = next(
        t for t in client.get("/chat/threads").json()["threads"] if t["id"] == tid
    )
    assert row["owner"] == "daemon"
    assert row["comm_channel"] == "telegram" and row["comm_display"] == "Val"
    detail = client.get(f"/chat/threads/{tid}").json()
    assert detail["owner"] == "daemon"


def test_put_messages_write_on_daemon_thread_is_409(tmp_path):
    import pytest

    from iron_jarvis.core.models import ChatThreadRecord

    if "owner" not in ChatThreadRecord.model_fields:
        pytest.skip("owner column not landed yet (Pair M)")
    client = _client(tmp_path)
    platform = client.app.state.platform
    tid = _mk_daemon_thread(platform)

    r = client.put(
        f"/chat/threads/{tid}",
        json={"messages": [{"role": "user", "content": "clobber attempt"}]},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "This thread is managed by a messaging destination"
        " — reply from the thread instead."
    )
    # The daemon's copy was NOT clobbered.
    detail = client.get(f"/chat/threads/{tid}").json()
    assert detail["messages"] == [{"role": "user", "content": "hi from the phone"}]


def test_put_metadata_edits_on_daemon_thread_still_allowed(tmp_path):
    import pytest

    from iron_jarvis.core.models import ChatThreadRecord

    if "owner" not in ChatThreadRecord.model_fields:
        pytest.skip("owner column not landed yet (Pair M)")
    client = _client(tmp_path)
    tid = _mk_daemon_thread(client.app.state.platform)

    r = client.put(
        f"/chat/threads/{tid}",
        json={"title": "Val (phone)", "persona": "concise", "project_id": None},
    )
    assert r.status_code == 200
    detail = client.get(f"/chat/threads/{tid}").json()
    assert detail["title"] == "Val (phone)"
    assert detail["persona"] == "concise"
    # Messages untouched by a metadata-only edit.
    assert detail["messages"] == [{"role": "user", "content": "hi from the phone"}]


def test_put_user_thread_contract_unchanged(tmp_path):
    """User-owned threads keep the long-standing rules: messages required
    (400 without), full-array replace with it."""
    client = _client(tmp_path)
    tid = client.put(
        "/chat/threads/new",
        json={"messages": [{"role": "user", "content": "hello"}]},
    ).json()["id"]
    assert client.put(f"/chat/threads/{tid}", json={"title": "x"}).status_code == 400
    r = client.put(
        f"/chat/threads/{tid}",
        json={"messages": [{"role": "user", "content": "hello"},
                           {"role": "assistant", "content": "hi!"}]},
    )
    assert r.status_code == 200
    assert len(client.get(f"/chat/threads/{tid}").json()["messages"]) == 2


def test_delete_stays_allowed_for_daemon_threads(tmp_path):
    import pytest

    from iron_jarvis.core.models import ChatThreadRecord

    if "owner" not in ChatThreadRecord.model_fields:
        pytest.skip("owner column not landed yet (Pair M)")
    client = _client(tmp_path)
    tid = _mk_daemon_thread(client.app.state.platform)
    assert client.delete(f"/chat/threads/{tid}").status_code == 200
    assert client.get(f"/chat/threads/{tid}").status_code == 404
