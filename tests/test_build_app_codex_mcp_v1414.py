"""v1.414.0 (fix): in Build › Code, Codex ASKS before an MCP tool call — the
Memory box's ``iron-jarvis-code`` server and every server the user configured
for Codex — and that question is now an ordinary Code-view permission card.

THE SHAPE (codex 0.157.0, read from its own ``generate-json-schema`` and
``core/src/mcp_tool_call.rs``, never guessed): under ``approvalPolicy:
untrusted`` (Ask first on Windows) Codex sends ``mcpServer/elicitation/request``
with ``mode: "form"``, an EMPTY ``requestedSchema`` and ``_meta``
``{codex_approval_kind: "mcp_tool_call", persist?, tool_params?, …}``; it reads
``{"action": "accept"}`` as allow once, ``accept`` + ``_meta.persist ==
"session"`` as allow for this session, ``{"action": "decline"}`` as "user
rejected MCP tool call". Before the fix every such request was declined with
"An app asked Codex for input…", so Codex could never use Iron Jarvis memory
in Ask mode.

Every test replays the request through the real fake harness
(``fixtures/build_app_v1409/codex_mcp_approval.json``), the real driver,
service and routes, and asserts on what really reached the harness.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted.driver import Ask, AskRefused
from tests._build_app_helpers_v1409 import (
    client,
    frames,
    new_session,
    sent,
    stop_all,
    until,
    use_fake,
    wait_pending,
)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _answers_to(log: Path, rid=0) -> list[dict]:
    return [m for m in sent(log) if "method" not in m and m.get("id") == rid and "result" in m]


async def _turn_with_card(app, work, tmp_path, monkeypatch, scenario="codex_mcp_approval"):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, scenario, log)
    c = client(app)
    s = await new_session(c, work, harness="codex")
    task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}))
    return c, s, task, log


async def test_the_mcp_tool_approval_is_a_card_naming_the_server_tool_and_arguments(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch)
    try:
        card = (await wait_pending(c, s["id"]))[0]
        assert card["type"] == "approval" and card["kind"] == "tool"
        assert card["title"] == 'Search Iron Jarvis memory for "project codename"'
        assert "query: project codename" in card["detail"]
        assert card["tool"] == "iron-jarvis-code.memory_search"
        # Codex offered "session" (persist: ["session", "always"]); "always"
        # would change Codex's own config, so only session is offered.
        assert card["can_session"] is True and not card.get("once_only")
        assert not _answers_to(log), "nothing was answered for the user"
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": card["request_id"], "decision": "deny"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
        assert not any(e["type"] == "notice" and "declined" in e.get("text", "") for e in evs)
    finally:
        await c.aclose()
        await stop_all(app)


async def test_allow_once_sends_codex_a_plain_accept_and_the_tool_runs(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch)
    try:
        card = (await wait_pending(c, s["id"]))[0]
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": card["request_id"], "decision": "allow"})
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await c.aclose()
        await stop_all(app)
    assert [a["result"] for a in _answers_to(log)] == [{"action": "accept"}]
    tools = [e for e in evs if e["type"] == "tool" and e["kind"] == "mcp"]
    assert tools[-1]["status"] == "done" and "BLUEHERON" in tools[-1]["output"]
    # The step's input is JSON (the live check showed a Python repr).
    assert tools[0]["input"] == '{"query": "project codename"}'
    assert any(e["type"] == "resolved" and e["decision"] == "allow" for e in evs)


async def test_allow_for_this_session_sends_persist_session_and_never_always(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch)
    try:
        card = (await wait_pending(c, s["id"]))[0]
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": card["request_id"], "decision": "allow_session"})
        assert r.status_code == 200, r.text
        await asyncio.wait_for(task, 30)
    finally:
        await c.aclose()
        await stop_all(app)
    assert [a["result"] for a in _answers_to(log)] == [{"action": "accept", "_meta": {"persist": "session"}}]


async def test_deny_sends_decline_and_codex_reports_the_rejection(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch)
    try:
        card = (await wait_pending(c, s["id"]))[0]
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": card["request_id"], "decision": "deny"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await c.aclose()
        await stop_all(app)
    assert [a["result"] for a in _answers_to(log)] == [{"action": "decline"}]
    tool = [e for e in evs if e["type"] == "tool" and e["kind"] == "mcp"][-1]
    assert tool["status"] == "failed"
    # Codex's own words, read off its error object — never a Python dict.
    assert tool["output"] == "user rejected MCP tool call"


async def test_stop_while_the_card_waits_declines_it(app, work, tmp_path, monkeypatch):
    svc = S.service_for(app.state.platform)
    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch)
    try:
        await wait_pending(c, s["id"])
        await c.post(f"/build/app/sessions/{s['id']}/interrupt")
        await until(lambda: _answers_to(log))
        assert not svc.live[s["id"]].asks
        task.cancel()
    finally:
        await c.aclose()
        await stop_all(app)
    assert [a["result"] for a in _answers_to(log)] == [{"action": "decline"}]


async def test_an_apps_own_form_is_still_declined_with_honest_words(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch, scenario="codex_mcp_form")
    try:
        evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await c.aclose()
        await stop_all(app)
    assert [a["result"] for a in _answers_to(log)] == [{"action": "decline"}]
    assert not any(e["type"] == "approval" for e in evs)
    notes = [e["text"] for e in evs if e["type"] == "notice"]
    assert any("github" in n and "declined" in n and "form" in n for n in notes), notes


# -- the driver's own rules (no process) ---------------------------------------

def _driver():
    got: list = []

    async def write(obj):
        got.append({"wrote": obj})

    d = X.CodexDriver(write=write, emit=lambda e: got.append(e), ask=lambda a: got.append(a),
                      on_session_id=lambda s: None, mode="ask")
    return d, got


def _params(**meta):
    return {"threadId": "th", "turnId": "tu", "serverName": "iron-jarvis-code", "mode": "form",
            "message": 'Allow the iron-jarvis-code MCP server to run tool "memory_search"?',
            "requestedSchema": {"type": "object", "properties": {}},
            "_meta": {"codex_approval_kind": "mcp_tool_call", **meta}}


async def test_no_persist_offer_makes_a_once_only_card_and_refuses_a_session_allow():
    d, got = _driver()
    d._on_server_request("mcpServer/elicitation/request", 7, _params(tool_params={"query": "q"}))
    ask = next(a for a in got if isinstance(a, Ask))
    assert ask.can_session is False and ask.event["once_only"] is True
    with pytest.raises(AskRefused):
        d.prepare_answer(ask, "allow_session", "", None)
    assert d.prepare_answer(ask, "allow", "", None) == {"jsonrpc": "2.0", "id": 7, "result": {"action": "accept"}}


async def test_the_tool_name_is_read_from_codexs_own_message_when_no_item_names_it():
    d, got = _driver()
    p = _params(persist="session")
    p["serverName"] = "linear"
    p["message"] = 'Allow the linear MCP server to run tool "create_issue"?'
    d._on_server_request("mcpServer/elicitation/request", 1, p)
    ask = next(a for a in got if isinstance(a, Ask))
    assert ask.event["title"] == "Use linear › create_issue"
    assert ask.can_session is True


async def test_an_approval_kind_with_fields_to_fill_is_not_a_yes_no_card():
    d, got = _driver()
    p = _params()
    p["requestedSchema"] = {"type": "object", "properties": {"x": {"type": "string"}}}
    d._on_server_request("mcpServer/elicitation/request", 2, p)
    await asyncio.sleep(0.05)
    assert not any(isinstance(a, Ask) for a in got)
    assert {"wrote": {"jsonrpc": "2.0", "id": 2, "result": {"action": "decline"}}} in got
    assert any(isinstance(e, dict) and e.get("type") == "notice" for e in got)


async def test_the_card_may_go_to_the_phone_only_when_it_is_not_once_only():
    from iron_jarvis.hosted import news

    d, got = _driver()
    d._on_server_request("mcpServer/elicitation/request", 3, _params(persist=["session", "always"]))
    d._on_server_request("mcpServer/elicitation/request", 4, _params())
    session_ask, once_ask = [a for a in got if isinstance(a, Ask)]
    assert news.phone_may_send(session_ask, "ask") is True
    assert news.phone_may_send(once_ask, "ask") is False
    assert news.phone_may_send(session_ask, "full") is False


# -- the seam the dashboard reads ---------------------------------------------

SEAM = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "codex-mcp-card-v1414.json"


async def test_the_dashboard_fixture_is_what_the_real_daemon_says(app, work, tmp_path, monkeypatch):
    """The card and the sign-in failure as the REAL daemon sends them (the
    route's own pending row; ``words.failure_for`` on the live Claude text),
    written for ``dashboard/__tests__/codex-mcp-card-v1414.test.tsx``.
    Regenerate with ``IJ_REGEN_CODEX_MCP=1``."""
    import json
    import os

    from iron_jarvis.hosted import words as W

    c, s, task, log = await _turn_with_card(app, work, tmp_path, monkeypatch)
    try:
        card = dict((await wait_pending(c, s["id"]))[0])
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": card["request_id"], "decision": "deny"})
        await asyncio.wait_for(task, 30)
    finally:
        await c.aclose()
        await stop_all(app)
    card.pop("at", None)
    card.pop("turn", None)
    card["request_id"] = "ask-1"
    err = W.failure_for("claude-code", "Failed to authenticate: OAuth session expired and could not be refreshed")
    err.pop("at", None)
    doc = {"_about": "v1.414.0 (fix): written by tests/test_build_app_codex_mcp_v1414.py from the real daemon; "
                     "IJ_REGEN_CODEX_MCP=1 to regenerate.",
           "approval": card, "sign_in_error": err}
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if os.environ.get("IJ_REGEN_CODEX_MCP") == "1":
        SEAM.write_bytes(text.encode("utf-8"))
    assert json.loads(SEAM.read_text(encoding="utf-8")) == json.loads(text), "stale — IJ_REGEN_CODEX_MCP=1"
