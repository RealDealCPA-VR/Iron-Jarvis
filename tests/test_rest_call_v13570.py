"""v1.357.0 (G84) — a REST hookup Jarvis can call.

THE GAP (product-readiness audit, section C): a REST hookup the user saved
(Settings › Connections › More ways to connect: a name, a web address, a key)
could be TESTED, but no tool could call it — "Custom API/tool: a dead end".

ACCEPTANCE: a user adds a hookup to their CRM's API and chat can fetch a
record from it after an approval card.

Every pin drives the REAL code: the real app factory and its routes (the
hookup is added through ``POST /integrations``, exactly what the Connections
card posts), the real integration registry and vault, the REAL stream chat
lane with only the model scripted, the REAL ``registry.invoke`` permission
gate (the v1.270.1 lesson), and a fake CRM that is a real HTTP server on this
machine (the hookup is told it may reach this network through the real
configure route — ``allow_internal``, the same switch the Test button reads).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.core.config import Config
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord, ToolInvocation
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon.app import create_app
from iron_jarvis.integrations import builtin as builtin_mod
from iron_jarvis.integrations.builtin import PathRefused, build_call_url
from iron_jarvis.integrations.hookups import hookups_named, named_hookup_ask_tools
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

KEY = "crm-live-key-7c1e0a9b44d2NEVERSHOWN"
NAME = "Acme CRM"
IID = "acme_crm"
ASK = "Get contact 42 from Acme CRM"


# --------------------------------------------------------------------------- #
# The fake CRM: a real HTTP server on this machine.
# --------------------------------------------------------------------------- #
class _Server:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str, bytes]] = []
        self.threads: set[int] = set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.seen.append((self.command, self.path, self.headers.get("Authorization") or "", body))
                return body

            def _send(self, code: int, payload: Any, ctype: str = "application/json", extra=()) -> None:
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                self._record()
                if self.path == "/api/contacts/42":
                    self._send(200, {"id": 42, "name": "Ada Lovelace", "email": "ada@example.com"})
                elif self.path == "/api/contacts/7":
                    # A record that carries someone else's secret-shaped value.
                    self._send(200, {"id": 7, "note": "their key is sk-ant-api03-" + "Q" * 40})
                elif self.path == "/api/echo":
                    # A service that echoes the request's own header back.
                    self._send(200, f"auth was: {self.headers.get('Authorization')}".encode(), "text/plain")
                elif self.path == "/api/big":
                    self._send(200, ("x" * 200_000).encode(), "text/plain")
                elif self.path == "/api/redirect":
                    self._send(302, b"", "text/plain", [("Location", outer.steal_url)])
                elif self.path == "/api/slow":
                    time.sleep(2.0)
                    self._send(200, {"late": True})
                else:
                    self._send(404, {"error": "no such record"})

            def _write(self) -> None:
                body = self._record()
                self._send(201, {"created": json.loads(body or b"{}")})

            do_POST = do_PUT = do_PATCH = do_DELETE = _write  # noqa: N815

            def log_message(self, *a: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/api"
        self.steal_url = ""
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def crm():
    srv = _Server()
    other = _Server()  # where a redirect would send the key
    srv.steal_url = other.base + "/steal"
    srv.other = other
    yield srv
    srv.close()
    other.close()


async def _add_hookup(app, base: str, *, allow_internal: bool = True) -> None:
    """Exactly the Connections card's Add (POST /integrations), then the
    configure route a user saves ``allow_internal`` through."""
    st, raw = await _asgi_post(app, "/integrations", {"name": NAME, "base_url": base, "auth_token": KEY})
    assert st == 200, raw
    if allow_internal:
        cfg = app.state.platform.integrations.stored_config(IID)
        cfg["allow_internal"] = True
        st, raw = await _asgi_post(app, f"/integrations/{IID}/configure", {"config": cfg})
        assert st == 200, raw


def _scripted(platform, monkeypatch, calls: list[list[tuple[str, dict]]], final: str = "Done."):
    """ONE fake model for both lanes: round n makes the calls in ``calls[n]``,
    then answers ``final``. Records each round's tools and messages."""
    seen: dict[str, Any] = {"tools": [], "messages": [], "n": 0}

    def _names(tools) -> list[str]:
        return [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []]

    def _next() -> LLMResponse:
        n = seen["n"]
        seen["n"] += 1
        if n < len(calls):
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id=f"c{n}_{i}", name=nm, arguments=a) for i, (nm, a) in enumerate(calls[n])],
            )
        return LLMResponse(text=final, tool_calls=[])

    async def fake_complete(*, system, messages, tools, **kw):
        seen["tools"].append(_names(tools))
        seen["messages"].append(json.dumps(messages, default=str))
        return RouteResult(_next(), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        seen["tools"].append(_names(tools))
        seen["messages"].append(json.dumps(messages, default=str))
        resp = _next()
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return seen


def _finished(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]


def _no_key_anywhere(platform, frames) -> None:
    """The key never reaches the browser, the ledger or an event."""
    assert KEY not in json.dumps(frames)
    with session_scope(platform.engine) as db:
        rows = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "rest_call")).all()
        assert rows, "the call is in the ledger"
        for r in rows:
            assert KEY not in r.args_json and KEY not in (r.output or "")
        for ev in db.exec(select(EventRecord)).all():
            assert KEY not in (ev.payload_json or ""), ev.type


def _ctx(platform, tmp_path: Path) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


# --------------------------------------------------------------------------- #
# ACCEPTANCE: chat fetches a record from the CRM after an approval card.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
async def test_chat_fetches_a_record_from_the_crm_after_an_approval_card(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    seen = _scripted(
        p, monkeypatch,
        [[("rest_call", {"service": NAME, "path": "/contacts/42"})]],
        final="Contact 42 is Ada Lovelace (ada@example.com).",
    )
    cards: list[dict] = []

    async def approve(data):
        cards.append(data)
        assert crm.seen == [], "nothing reached the CRM before the user answered"
        st, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert st == 200

    frames = await _drive_stream(app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True}, approve)

    # Naming the hookup armed the tool (no "+" pick), and the model was told
    # which hookups exist — by name, never by address or key.
    assert "rest_call" in seen["tools"][0]
    # ONE card, and it says the method and the path before anything runs.
    assert [c["tool"] for c in cards] == ["rest_call"], cards
    assert cards[0]["args"] == {"service": NAME, "method": "GET", "path": "/contacts/42"}
    # Exactly one request, to the hookup's own address, with the saved key.
    assert crm.seen == [("GET", "/api/contacts/42", f"Bearer {KEY}", b"")]
    (fin,) = _finished(frames)
    assert fin["name"] == "rest_call" and fin["ok"] is True
    # The record reached the model's next round, and the answer the user.
    assert "Ada Lovelace" in seen["messages"][1]
    done = next(d for ev, d in frames if ev == "done")
    assert done["reply"].startswith("Contact 42 is Ada Lovelace")
    _no_key_anywhere(p, frames)


@pytest.mark.timeout(120)
async def test_deny_on_the_card_sends_nothing(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    _scripted(p, monkeypatch, [[("rest_call", {"service": NAME, "path": "/contacts/42"})]])

    async def deny(data):
        st, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert st == 200

    frames = await _drive_stream(app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True}, deny)
    (fin,) = _finished(frames)
    assert fin["ok"] is False
    assert crm.seen == [], "a declined card must not reach the service"


@pytest.mark.timeout(120)
async def test_a_call_that_changes_something_asks_every_time_even_in_yolo(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    write = ("rest_call", {"service": NAME, "path": "/contacts", "method": "post", "body": {"name": "Grace"}})
    _scripted(p, monkeypatch, [[write], [write]])
    cards: list[dict] = []

    async def wider(data):
        cards.append(data)
        # The widest answer the card offers; it must not cover the next write.
        st, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "conversation"})
        assert st == 200

    frames = await _drive_stream(
        app,
        {"messages": [{"role": "user", "content": "Add Grace to Acme CRM twice"}], "auto_tools": True,
         "approval_mode": "yolo"},
        wider,
    )
    # Two writes, two cards — yolo and "this chat" skip neither — each saying
    # the method and the path, and never offering "Always".
    assert len(cards) == 2, cards
    for c in cards:
        assert c["cost_note"] == "POST /contacts on Acme CRM: this changes something on that service."
        assert c["args"]["method"] == "POST" and c["can_always"] is False
    assert [(m, pth) for m, pth, _a, _b in crm.seen] == [("POST", "/api/contacts")] * 2
    assert json.loads(crm.seen[0][3]) == {"name": "Grace"}
    assert all(f["ok"] for f in _finished(frames))


@pytest.mark.timeout(120)
async def test_a_read_in_yolo_runs_without_a_card_through_the_real_gate(tmp_path, monkeypatch, crm):
    """The ordinary ask tier: yolo pre-approved it, so no card — and the call
    still has to RUN through the registry's own gate (the v1.270.1 rule:
    a skipped card must carry its grant)."""
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    _scripted(p, monkeypatch, [[("rest_call", {"service": IID, "path": "/contacts/42"})]])

    async def no_card(data):
        raise AssertionError(f"yolo must not card a read: {data}")

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True, "approval_mode": "yolo"}, no_card
    )
    (fin,) = _finished(frames)
    assert fin["ok"] is True
    assert [(m, pth) for m, pth, _a, _b in crm.seen] == [("GET", "/api/contacts/42")]


@pytest.mark.timeout(120)
async def test_the_post_lane_never_arms_it_by_name(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    seen = _scripted(p, monkeypatch, [])
    st, raw = await _asgi_post(app, "/chat", {"messages": [{"role": "user", "content": ASK}], "auto_tools": True})
    assert st == 200, raw
    assert all("rest_call" not in t for t in seen["tools"])
    assert crm.seen == []


@pytest.mark.timeout(120)
async def test_a_message_that_names_no_hookup_and_a_local_only_turn_arm_nothing(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)

    async def no_card(data):
        raise AssertionError(data)

    seen = _scripted(p, monkeypatch, [])
    await _drive_stream(app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True}, no_card)
    assert "rest_call" in seen["tools"][0], "control: the same app arms it when the hookup is named"
    seen = _scripted(p, monkeypatch, [])
    await _drive_stream(app, {"messages": [{"role": "user", "content": "get contact 42"}], "auto_tools": True}, no_card)
    assert "rest_call" not in seen["tools"][0]
    seen = _scripted(p, monkeypatch, [])
    await _drive_stream(
        app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True, "local_only": True}, no_card
    )
    assert "rest_call" not in seen["tools"][0]


def test_the_name_rule_mirrors_the_apps_rule(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(create_app(str(tmp_path / "home")))
    p = client.app.state.platform
    assert client.post("/integrations", json={"name": NAME, "base_url": "https://crm.example.com/api"}).status_code == 200
    assert hookups_named(p.integrations, "Get contact 42 from Acme CRM") == [IID]
    assert hookups_named(p.integrations, "check acme_crm for deals") == [IID]
    assert hookups_named(p.integrations, "an acme of crm-like tools") == []
    kw = {"low_trust": False, "local_only": False}
    assert named_hookup_ask_tools(p, ASK, [], [], **kw) == ["rest_call"]
    assert named_hookup_ask_tools(p, "get contact 42", [], [], **kw) == []
    assert named_hookup_ask_tools(p, ASK, [], ["rest_call"], **kw) == [], "never armed twice"
    for flags in ({"low_trust": True, "local_only": False}, {"low_trust": False, "local_only": True}):
        assert named_hookup_ask_tools(p, ASK, [], [], **flags) == [], flags


# --------------------------------------------------------------------------- #
# The real invoke and the call itself.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
async def test_the_real_gate_refuses_without_a_human_and_runs_with_one(tmp_path, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    args = {"service": NAME, "path": "/contacts/42"}
    refused = await p.registry.invoke("rest_call", args, _ctx(p, tmp_path), p.permissions)
    assert refused.ok is False and crm.seen == [], "an ask-tier call with nobody to ask never runs"
    ran = await p.registry.invoke(
        "rest_call", args, _ctx(p, tmp_path), p.permissions, session_allow={"rest_call"}
    )
    assert ran.ok is True and "Ada Lovelace" in ran.output
    assert ran.data["status"] == 200 and ran.data["method"] == "GET"


@pytest.mark.timeout(120)
async def test_the_call_runs_off_the_event_loop(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    loop_thread = threading.get_ident()
    where: list[int] = []
    real = builtin_mod.RestApiIntegration.call

    def spy(self, *a, **kw):
        where.append(threading.get_ident())
        return real(self, *a, **kw)

    monkeypatch.setattr(builtin_mod.RestApiIntegration, "call", spy)
    res = await p.registry.get("rest_call").execute({"service": NAME, "path": "/contacts/42"}, _ctx(p, tmp_path))
    assert res.ok is True
    assert where and where[0] != loop_thread


@pytest.mark.timeout(120)
async def test_only_a_path_under_the_hookups_address_is_ever_reached(tmp_path, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    tool = p.registry.get("rest_call")
    for bad in (
        crm.other.base + "/steal",
        "//127.0.0.1/steal",
        "../admin",
        "/contacts/../../admin",
        "%2e%2e/admin",
        "%252e%252e/admin",
        "/contacts/4 2",
        "\\\\host\\share",
    ):
        res = await tool.execute({"service": NAME, "path": bad}, _ctx(p, tmp_path))
        assert res.ok is False, bad
    assert crm.seen == [] and crm.other.seen == []
    # The base's own path is kept, and a query rides along.
    assert build_call_url("https://crm.example.com/api/v2", "/deals?stage=won") == (
        "https://crm.example.com/api/v2/deals?stage=won"
    )
    assert build_call_url("https://crm.example.com/api?k=1", "x#frag") == "https://crm.example.com/api/x?k=1"
    with pytest.raises(PathRefused):
        build_call_url("https://crm.example.com/api", "https://evil.example/x")


@pytest.mark.timeout(120)
async def test_a_redirect_is_not_followed_so_the_key_goes_nowhere_else(tmp_path, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    res = await p.registry.get("rest_call").execute({"service": NAME, "path": "/redirect"}, _ctx(p, tmp_path))
    assert res.ok is False and "redirect" in res.error
    assert crm.other.seen == [], "the other address was never asked"


@pytest.mark.timeout(120)
async def test_an_answer_that_echoes_the_key_never_shows_it_and_a_long_one_is_cut(tmp_path, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    tool = p.registry.get("rest_call")
    echo = await tool.execute({"service": NAME, "path": "/echo"}, _ctx(p, tmp_path))
    assert echo.ok is True and KEY not in echo.output and "[hidden]" in echo.output
    # The answer is masked like every other secret-bearing text (redact.mask).
    rec = await tool.execute({"service": NAME, "path": "/contacts/7"}, _ctx(p, tmp_path))
    assert rec.ok is True and "Q" * 40 not in rec.output and "sk-ant-" in rec.output
    big = await tool.execute({"service": NAME, "path": "/big"}, _ctx(p, tmp_path))
    from iron_jarvis.integrations.tools import REST_RESULT_CHARS

    assert big.ok is True and big.data["truncated"] is True
    assert len(big.output) < REST_RESULT_CHARS + 300
    small = await tool.execute({"service": NAME, "path": "/contacts/42"}, _ctx(p, tmp_path))
    assert small.data["truncated"] is False and "longer" not in small.output


@pytest.mark.timeout(120)
async def test_a_slow_service_is_bounded_and_says_so(tmp_path, monkeypatch, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, crm.base)
    monkeypatch.setattr(builtin_mod, "CALL_READ_S", 0.3)
    started = time.monotonic()
    res = await p.registry.get("rest_call").execute({"service": NAME, "path": "/slow"}, _ctx(p, tmp_path))
    assert res.ok is False and "did not answer" in res.error
    assert time.monotonic() - started < 1.9, "the read bound ended it, not the slow answer"


@pytest.mark.timeout(120)
async def test_turned_off_unknown_and_inside_the_network_are_refused_in_words(tmp_path, crm):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    # Saved WITHOUT allow_internal: the Test button's guard refuses this PC.
    await _add_hookup(app, crm.base, allow_internal=False)
    tool = p.registry.get("rest_call")
    res = await tool.execute({"service": NAME, "path": "/contacts/42"}, _ctx(p, tmp_path))
    assert res.ok is False and "inside this network" in res.error
    res = await tool.execute({"service": "Initech", "path": "/x"}, _ctx(p, tmp_path))
    assert res.ok is False and "No saved web service is called 'Initech'" in res.error and "Acme CRM (acme_crm)" in res.error
    st, _ = await _asgi_post(app, f"/integrations/{IID}/enable", {"enabled": False})
    assert st == 200
    res = await tool.execute({"service": NAME, "path": "/contacts/42"}, _ctx(p, tmp_path))
    assert res.ok is False and "turned off" in res.error
    res = await tool.execute({"service": NAME, "path": "/x", "body": {"a": 1}}, _ctx(p, tmp_path))
    assert res.ok is False and "GET sends no body" in res.error
    assert crm.seen == []


# --------------------------------------------------------------------------- #
# Where it sits: schema, permission row, low trust, local only.
# --------------------------------------------------------------------------- #
def test_it_declares_its_schema_and_is_carded_kept_away_and_reaches_outside(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(create_app(str(tmp_path / "home")))
    p = client.app.state.platform
    client.post("/integrations", json={"name": NAME, "base_url": "https://crm.example.com/api", "auth_token": KEY})
    tool = p.registry.get("rest_call")
    spec = tool.spec()
    assert spec["input_schema"]["type"] == "object"
    assert spec["input_schema"]["required"] == ["service", "path"]
    # The user's hookups are NAMED for the model — never an address or a key;
    # the built-in example is not offered.
    assert "Acme CRM (acme_crm)" in spec["description"]
    assert "crm.example.com" not in spec["description"] and KEY not in spec["description"]
    assert "Generic REST API" not in spec["description"]
    # The default permission table (a new install's), and the running app's.
    assert Config.model_fields["permissions"].default_factory()["rest_call"] == "ask"
    assert p.config.permissions["rest_call"] == "ask"
    assert "rest_call" in LOW_TRUST_DENY
    assert egress.reaches_outside(tool, p.registry) is True
    assert "rest_call" not in egress.INTERNET_TOOLS, "a local-only chat cards it, never drops it"
    assert egress.ask_first_tools(p.registry, ["rest_call"]) == frozenset({"rest_call"})
    # A read has no "every time" opinion; a write does, with its words.
    ctx = _ctx(p, tmp_path)
    assert asyncio.run(tool.spend_check({"service": NAME, "path": "/a"}, ctx)) is None
    check = asyncio.run(tool.spend_check({"service": NAME, "path": "/a", "method": "DELETE"}, ctx))
    assert check.ask is True and check.words.startswith("DELETE /a on Acme CRM")
