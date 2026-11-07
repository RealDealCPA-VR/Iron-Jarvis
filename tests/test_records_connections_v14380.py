"""v1.438.0 (chat-first plan, Wave 4 group B): model servers, Iron-Proxy
accounts and memory bases from chat — ``settings/records_connections``.

Every change test drives the REAL stream lane (``/chat/stream``), the real
permission gate (an ask-tier call pauses on the approval card, answered
"Allow once" through ``/chat/approvals``), the real ``registry.invoke`` and
the real Undo route (``POST /undo/{id}``, found through the card's
``change_id``).

No test reaches the network: a model server is a stand-in on 127.0.0.1 (it
records every request and the key it was sent with), Iron-Proxy is an
in-memory stand-in (the user's real ``~/.iron-proxy`` is never read — the
service is replaced on the platform), and a terminal is a stand-in (no shell
is started). ``no_network`` refuses everything else.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_connections as RC
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]
WRITERS = ("model_server_add", "model_server_default", "model_server_remove", "account_add", "memory_base_add")
KEY = "sk-SECRET-v14380-model-server-key"
NOTION_TOKEN = "ntn_SECRET_v14380_notion_token"
DB_ID = "0123456789abcdef0123456789abcdef"


# =========================================================================== #
# Stand-ins
# =========================================================================== #
class _ModelServer:
    """An OpenAI-compatible model server on 127.0.0.1: ``/v1/models`` lists
    ``models``; with ``key`` set it answers 401 unless that exact key comes."""

    def __init__(self, models=("qwen3", "glm-5"), key: str = "", status: int = 200) -> None:
        self.models = list(models)
        self.key = key
        self.status = status
        self.seen: list[tuple[str, str]] = []  # (path, Authorization)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 — quiet
                pass

            def do_GET(self):  # noqa: N802
                outer.seen.append((self.path, self.headers.get("Authorization") or ""))
                if outer.key and self.headers.get("Authorization") != f"Bearer {outer.key}":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path.rstrip("/") in ("/v1/models", "/models") and outer.status == 200:
                    body = json.dumps({"object": "list", "data": [{"id": m} for m in outer.models]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(outer.status if outer.status != 200 else 404)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    s = _ModelServer()
    yield s
    s.close()


class _ProxyClient:
    def __init__(self, owner) -> None:
        self.o = owner

    def profiles(self):
        return [dict(p) for p in self.o.profiles]

    def states(self):
        return {k: dict(v) for k, v in self.o.states.items()}

    def create_profile(self, provider, title, lane="cli"):
        self.o.n += 1
        pid = f"prof_{self.o.n:04d}"
        prof = {"id": pid, "provider": provider, "title": title, "lane": lane, "order": self.o.n, "enabled": True}
        self.o.profiles.append(prof)
        self.o.states[pid] = {"profileId": pid, "status": "unauthenticated"}
        self.o.created.append((provider, title, lane))
        return dict(prof)

    def login_command(self, pid):
        return {"binary": "claude", "args": ["/login"], "env": {"CLAUDE_CONFIG_DIR": f"C:\\homes\\{pid}"}}

    def refresh(self, pid=None, timeout=None):
        # POST /iron/refresh: the re-checked states (the stand-in's own).
        return [dict(self.o.states[pid])] if pid in self.o.states else []

    def delete_profile(self, pid):
        self.o.profiles = [p for p in self.o.profiles if p["id"] != pid]
        self.o.states.pop(pid, None)
        self.o.deleted.append(pid)


class _Proxy:
    """Iron-Proxy, in memory: what the service hands the routes and tools."""

    def __init__(self, *, enabled: bool = True, accounts=()) -> None:
        self.enabled = enabled
        self.profiles: list[dict] = []
        self.states: dict[str, dict] = {}
        self.n = 0
        self.created: list = []
        self.deleted: list = []
        self.signins: list = []
        for provider, title, status in accounts:
            self.n += 1
            pid = f"prof_{self.n:04d}"
            self.profiles.append({"id": pid, "provider": provider, "title": title, "lane": "cli",
                                  "order": self.n, "enabled": True})
            self.states[pid] = {"profileId": pid, "status": status}
        self.config = SimpleNamespace(account_groups={})

    def client(self):
        return _ProxyClient(self) if self.enabled else None

    def cached_accounts(self):
        return ([dict(p) for p in self.profiles], {k: dict(v) for k, v in self.states.items()})

    def refresh_accounts(self):
        pass

    def note_signin_started(self, pid):
        self.signins.append(pid)

    def status(self):
        return {"enabled": self.enabled, "running": self.enabled}


class _Pane:
    def __init__(self, name):
        self.id = "term_v14380"
        self.pane_name = name
        self.shell = "pwsh"
        self.written: list[str] = []

    def start_autodrain(self):
        pass

    def write(self, text):
        self.written.append(text)


def _with_proxy(monkeypatch, p, proxy: _Proxy) -> list[_Pane]:
    from iron_jarvis.iron_proxy import service as svc_mod

    monkeypatch.setattr(p, "iron_proxy", proxy, raising=False)
    monkeypatch.setattr(svc_mod, "_CURRENT", proxy, raising=False)
    panes: list[_Pane] = []

    def create(*_a, env=None, name="", **_k):
        pane = _Pane(name)
        panes.append(pane)
        return pane

    monkeypatch.setattr(p.terminals, "create", create)
    return panes


# =========================================================================== #
# Harness
# =========================================================================== #
def _app(tmp_path, specs):
    app = create_app(str(tmp_path / "ij"))
    app.state.platform.router.stream = _calls_stream(specs)
    return app


async def _approve_all(app, seen):
    async def approve(data):
        seen.append((data["tool"], data))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


async def _deny_all(app, seen):
    async def deny(data):
        seen.append((data["tool"], data))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    return deny


async def _drive_no_card(app, body):
    """Drive a turn whose calls must show NO card. A card that does show is
    DECLINED (so a regression fails at once, never hangs the stream) and
    fails the test."""
    seen: list = []
    frames = await _drive_stream(app, body, await _deny_all(app, seen))
    assert seen == [], f"these calls must not card: {[t for t, _ in seen]}"
    return frames


def _card(frames, title):
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == title]
    assert len(cards) == 1, _done(frames)["config_cards"]
    return cards[0]


def _finished(frames, tool):
    return [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool
    ]


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _db_text(p) -> str:
    from sqlmodel import select

    from iron_jarvis.core.models import ToolInvocation, UndoJournal

    with session_scope(p.engine) as db:
        rows = [f"{r.args_json}\n{r.output}" for r in db.exec(select(ToolInvocation))]
        rows += [str(r.pre_inline or "") for r in db.exec(select(UndoJournal))]
    return "\n".join(rows)


def _undo(app, card) -> "tuple[int, str]":
    client = TestClient(app)
    look = client.get(f"/config/changes/{card['change_id']}")
    assert look.status_code == 200, look.text
    r = client.post(f"/undo/{look.json()['action_id']}")
    return r.status_code, r.text


def _node_ids(p):
    return [n.id for n in p.fleet.nodes() if not n.parent_id]


# =========================================================================== #
# model_server_add
# =========================================================================== #
@pytest.mark.asyncio
async def test_adding_a_server_asks_says_where_it_is_and_sets_the_default_from_its_own_list(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {
        "address": server.url, "name": "LM Studio", "make_default": True, "model": "glm-5",
    })])
    p = app.state.platform
    before = (p.config.default_provider, p.config.default_model)
    seen: list = []
    frames = await _drive_stream(
        app, _body(f"Add my LM Studio server at {server.url} and make it default"), await _approve_all(app, seen)
    )
    assert [t for t, _ in seen] == ["model_server_add"], "adding a server must ask first"
    assert "on this PC or your own network" in _text(seen[0][1]), "the card says WHERE it is"
    node = p.fleet.get("lm-studio")
    assert node is not None and node.routable and node.base_url == server.url
    assert (p.config.default_provider, p.config.default_model) == ("fleet-lm-studio", "glm-5")
    assert any(path.rstrip("/").endswith("/models") for path, _ in server.seen), "its OWN list was read"
    card = _card(frames, "Model server added")
    assert card["key"] == "model_default.lm-studio"
    await _undo_card(app, card)
    assert p.fleet.get("lm-studio") is None
    assert (p.config.default_provider, p.config.default_model) == before, "Undo puts the old default back"


@pytest.mark.asyncio
async def test_a_model_not_in_the_servers_list_is_never_made_the_default(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {
        "address": server.url, "name": "LM Studio", "make_default": True, "model": "gpt-guessed",
    })])
    p = app.state.platform
    before = p.config.default_model
    seen: list = []
    frames = await _drive_stream(app, _body(f"add my LM Studio server at {server.url}"), await _approve_all(app, seen))
    out = _text(_finished(frames, "model_server_add"))
    assert "not in its list" in out and "qwen3" in out and "glm-5" in out
    assert p.config.default_model == before
    assert _card(frames, "Model server added")["key"] == "model_server.lm-studio"


@pytest.mark.asyncio
async def test_several_models_and_no_name_asks_which_one(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"address": server.url, "name": "Box", "make_default": True})])
    p = app.state.platform
    before = p.config.default_model
    seen: list = []
    frames = await _drive_stream(app, _body(f"add my Ollama box at {server.url}"), await _approve_all(app, seen))
    assert "Which of its models should be the default?" in _text(_finished(frames, "model_server_add"))
    assert p.config.default_model == before


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_is_said_and_no_default_is_set(tmp_path):
    broken = _ModelServer(status=500)
    try:
        app = _app(tmp_path, [("model_server_add", {"address": broken.url, "name": "vLLM", "make_default": True})])
        p = app.state.platform
        before = p.config.default_model
        seen: list = []
        frames = await _drive_stream(app, _body(f"add my vLLM server at {broken.url}"), await _approve_all(app, seen))
        out = _text(_finished(frames, "model_server_add"))
        assert "could not be read" in out and "no default model was set" in out
        assert p.config.default_model == before
        assert p.fleet.get("vllm") is not None, "the server is still added"
    finally:
        broken.close()


@pytest.mark.asyncio
async def test_a_key_is_never_a_tool_argument_and_never_logged(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"address": server.url, "name": "GW", "api_key": KEY})])
    p = app.state.platform
    frames = await _drive_no_card(app, _body(f"add my LiteLLM gateway at {server.url}"))
    assert RC.KEY_NOT_IN_CHAT in _text(_finished(frames, "model_server_add"))
    assert p.fleet.get("gw") is None
    assert KEY not in _db_text(p), "a key handed to the tool is never written to the ledger"


@pytest.mark.asyncio
async def test_a_password_in_the_address_is_refused(tmp_path):
    app = _app(tmp_path, [("model_server_add", {"address": "http://me:hunter2@127.0.0.1:9/v1", "name": "x"})])
    frames = await _drive_no_card(app, _body("add my vLLM server at http://127.0.0.1:9/v1"))
    assert RC.ADDRESS_WITH_PASSWORD in _text(_finished(frames, "model_server_add"))
    assert "x" not in _node_ids(app.state.platform)


@pytest.mark.asyncio
async def test_a_server_that_needs_a_key_takes_it_on_the_secure_card_sent_only_to_itself(tmp_path):
    keyed = _ModelServer(key=KEY)
    try:
        app = _app(tmp_path, [
            ("model_server_add", {"address": keyed.url, "name": "Gateway", "needs_key": True}),
        ])
        p = app.state.platform
        seen: list = []
        frames = await _drive_stream(app, _body(f"add my LiteLLM gateway at {keyed.url}"), await _approve_all(app, seen))
        node = p.fleet.get("gateway")
        assert node.api_key_name == "endpoint_gateway_key"
        secret = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
        assert secret["name"] == "model_server.gateway"
        status, raw = await _asgi_post(app, "/config/secret", {"name": "model_server.gateway", "value": KEY})
        assert status == 200 and KEY.encode() not in raw
        assert p.secrets.get("endpoint_gateway_key") == KEY
        # The default is now picked from its list, read WITH its own key.
        p.router.stream = _calls_stream([("model_server_default", {"server": "Gateway", "model": "qwen3"})])
        keyed.seen.clear()
        frames2 = await _drive_stream(app, _body("use qwen3 from my gateway server by default"), await _approve_all(app, seen))
        assert (p.config.default_provider, p.config.default_model) == ("fleet-gateway", "qwen3")
        assert keyed.seen and all(auth == f"Bearer {KEY}" for _p, auth in keyed.seen)
        assert KEY not in _db_text(p) + json.dumps(frames + frames2, default=str)
        # Undo of the add takes the key away with the server.
        await _undo_card(app, _card(frames, "Model server added"))
        assert p.fleet.get("gateway") is None and p.secrets.get("endpoint_gateway_key") is None
    finally:
        keyed.close()


@pytest.mark.asyncio
async def test_an_internet_server_says_so_on_its_card(tmp_path):
    """Declined: nothing is added and nothing is reached (no_network)."""
    app = _app(tmp_path, [("model_server_add", {"address": "https://gateway.example.com/v1", "name": "Cloud"})])
    seen: list = []
    await _drive_stream(app, _body("add my LiteLLM gateway at https://gateway.example.com/v1"), await _deny_all(app, seen))
    assert [t for t, _ in seen] == ["model_server_add"]
    assert "on the internet" in _text(seen[0][1]) and "leaves this PC" in _text(seen[0][1])
    assert "cloud" not in _node_ids(app.state.platform)


@pytest.mark.asyncio
async def test_the_same_server_twice_is_refused_without_a_card(tmp_path, server):
    app = _app(tmp_path, [
        ("model_server_add", {"address": server.url, "name": "One"}),
        ("model_server_add", {"address": server.url.replace("/v1", ""), "name": "Two"}),
    ])
    seen: list = []
    frames = await _drive_stream(app, _body(f"add my LM Studio server at {server.url}"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["model_server_add"], "the second call shows no card"
    assert "already added" in _text(_finished(frames, "model_server_add")[1])
    assert _node_ids(app.state.platform).count("two") == 0


@pytest.mark.asyncio
async def test_undo_of_an_add_refuses_when_the_server_was_changed_since(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {"address": server.url, "name": "LM Studio"})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body(f"add my LM Studio server at {server.url}"), await _approve_all(app, seen))
    p.fleet.update("lm-studio", label="Renamed by me")
    status, text = _undo(app, _card(frames, "Model server added"))
    assert status == 409 and "changed since" in text, text
    assert p.fleet.get("lm-studio") is not None, "the user's newer work is kept"


@pytest.mark.asyncio
async def test_undo_of_an_add_keeps_a_default_the_user_chose_since(tmp_path, server):
    app = _app(tmp_path, [("model_server_add", {
        "address": server.url, "name": "LM Studio", "make_default": True, "model": "qwen3",
    })])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body(f"add my LM Studio server at {server.url} and make it default"), await _approve_all(app, seen))
    p.config_writer.apply({"default_model": "chosen-since"}, actor="test", journal=False)
    status, text = _undo(app, _card(frames, "Model server added"))
    assert status == 200 and "was kept" in text, text
    assert p.config.default_model == "chosen-since"


# =========================================================================== #
# model_server_default / model_server_remove
# =========================================================================== #
def _seed_server(p, server, node_id="dgx", label="DGX"):
    from iron_jarvis.fleet.models import FleetNode

    p.fleet.add(FleetNode(id=node_id, label=label, base_url=server.url, routable=True))
    p.fleet.register_providers(p.providers)


@pytest.mark.asyncio
async def test_default_with_no_model_lists_the_servers_own_models_with_no_card(tmp_path, server):
    app = _app(tmp_path, [("model_server_default", {"server": "DGX"})])
    p = app.state.platform
    _seed_server(p, server)
    frames = await _drive_no_card(app, _body("make a model on the DGX server my default"))
    out = _text(_finished(frames, "model_server_default"))
    assert "qwen3" in out and "glm-5" in out
    assert _done(frames)["config_cards"] == []


@pytest.mark.asyncio
async def test_default_undo_refuses_after_the_default_changed_since(tmp_path, server):
    app = _app(tmp_path, [("model_server_default", {"server": "dgx", "model": "glm-5"})])
    p = app.state.platform
    _seed_server(p, server)
    seen: list = []
    frames = await _drive_stream(app, _body("use glm-5 from my DGX server by default"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["model_server_default"]
    assert (p.config.default_provider, p.config.default_model) == ("fleet-dgx", "glm-5")
    card = _card(frames, "Default model changed")
    p.config_writer.apply({"default_model": "chosen-since"}, actor="test", journal=False)
    status, text = _undo(app, card)
    assert status == 409 and "changed since" in text, text
    assert p.config.default_model == "chosen-since"


@pytest.mark.asyncio
async def test_default_undo_puts_the_old_default_back(tmp_path, server):
    app = _app(tmp_path, [("model_server_default", {"server": "dgx", "model": "glm-5"})])
    p = app.state.platform
    _seed_server(p, server)
    before = (p.config.default_provider, p.config.default_model)
    seen: list = []
    frames = await _drive_stream(app, _body("use glm-5 from my DGX server by default"), await _approve_all(app, seen))
    await _undo_card(app, _card(frames, "Default model changed"))
    assert (p.config.default_provider, p.config.default_model) == before


@pytest.mark.asyncio
async def test_remove_asks_and_undo_brings_it_back_then_refuses_a_second_time(tmp_path, server):
    app = _app(tmp_path, [("model_server_remove", {"server": "the DGX server"})])
    p = app.state.platform
    _seed_server(p, server)
    seen: list = []
    frames = await _drive_stream(app, _body("remove the DGX server"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["model_server_remove"]
    assert p.fleet.get("dgx") is None
    card = _card(frames, "Model server removed")
    await _undo_card(app, card)
    node = p.fleet.get("dgx")
    assert node is not None and node.label == "DGX" and node.base_url == server.url
    status, text = _undo(app, card)
    assert status in (409, 410), text


@pytest.mark.asyncio
async def test_undo_of_a_removal_never_writes_over_a_server_added_again(tmp_path, server):
    app = _app(tmp_path, [("model_server_remove", {"server": "dgx"})])
    p = app.state.platform
    _seed_server(p, server)
    seen: list = []
    frames = await _drive_stream(app, _body("remove the DGX server"), await _approve_all(app, seen))
    _seed_server(p, server, label="Newer")
    status, text = _undo(app, _card(frames, "Model server removed"))
    assert status == 409 and "added again" in text
    assert p.fleet.get("dgx").label == "Newer"


@pytest.mark.asyncio
async def test_removing_an_unknown_server_shows_no_card(tmp_path):
    app = _app(tmp_path, [("model_server_remove", {"server": "nowhere"})])
    frames = await _drive_no_card(app, _body("remove the DGX server"))
    assert "There is no model server called" in _text(_finished(frames, "model_server_remove"))


# =========================================================================== #
# account_add
# =========================================================================== #
@pytest.mark.asyncio
async def test_account_add_asks_opens_its_sign_in_and_undo_removes_it_while_unsigned(tmp_path, monkeypatch):
    app = _app(tmp_path, [("account_add", {"vendor": "claude", "name": "Work"})])
    p = app.state.platform
    proxy = _Proxy()
    panes = _with_proxy(monkeypatch, p, proxy)
    seen: list = []
    frames = await _drive_stream(app, _body("Add a second Claude account called Work"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["account_add"]
    assert proxy.created == [("anthropic", "Work", "cli")]
    pid = proxy.profiles[0]["id"]
    assert proxy.signins == [pid] and panes and panes[0].written, "its OWN sign-in terminal ran the login"
    doors = _done(frames).get("doors") or []
    assert {"href": "/terminals?focus=term_v14380", "label": "Sign in Work in its terminal"} in doors
    assert "never sees the login" in _text(_finished(frames, "account_add"))
    await _undo_card(app, _card(frames, "Account added"))
    assert proxy.deleted == [pid]


@pytest.mark.asyncio
async def test_undo_never_removes_an_account_that_signed_in_since(tmp_path, monkeypatch):
    app = _app(tmp_path, [("account_add", {"vendor": "codex", "name": "Personal"})])
    p = app.state.platform
    proxy = _Proxy()
    _with_proxy(monkeypatch, p, proxy)
    seen: list = []
    frames = await _drive_stream(app, _body("add another Codex account for personal use"), await _approve_all(app, seen))
    pid = proxy.profiles[0]["id"]
    assert proxy.profiles[0]["provider"] == "openai"
    proxy.states[pid]["status"] = "ready"  # the user signed in
    status, text = _undo(app, _card(frames, "Account added"))
    assert status == 409 and "signed in now" in text and "sign that login out" in text, text
    assert proxy.deleted == []


@pytest.mark.asyncio
async def test_undo_refuses_an_account_renamed_since(tmp_path, monkeypatch):
    app = _app(tmp_path, [("account_add", {"vendor": "grok", "name": "Spare"})])
    p = app.state.platform
    proxy = _Proxy()
    _with_proxy(monkeypatch, p, proxy)
    seen: list = []
    frames = await _drive_stream(app, _body("set up a Grok account"), await _approve_all(app, seen))
    proxy.profiles[0]["title"] = "Renamed"
    status, text = _undo(app, _card(frames, "Account added"))
    assert status == 409 and "renamed since" in text
    assert proxy.deleted == []


@pytest.mark.asyncio
async def test_account_add_with_several_accounts_off_is_refused_in_words_with_no_card(tmp_path, monkeypatch):
    app = _app(tmp_path, [("account_add", {"vendor": "claude", "name": "Work"})])
    p = app.state.platform
    proxy = _Proxy(enabled=False)
    _with_proxy(monkeypatch, p, proxy)
    frames = await _drive_no_card(app, _body("add a second Claude account"))
    out = _text(_finished(frames, "account_add"))
    assert "Several accounts is off" in out and "Settings › Connections" in out
    assert proxy.created == []


@pytest.mark.asyncio
async def test_a_second_account_of_the_same_name_or_a_password_is_refused_with_no_card(tmp_path, monkeypatch):
    app = _app(tmp_path, [
        ("account_add", {"vendor": "claude", "name": "work"}),
        ("account_add", {"vendor": "claude", "name": "Other", "password": "hunter2-v14380"}),
    ])
    p = app.state.platform
    proxy = _Proxy(accounts=[("anthropic", "Work", "ready")])
    _with_proxy(monkeypatch, p, proxy)
    frames = await _drive_no_card(app, _body("add another Claude account"))
    results = _finished(frames, "account_add")
    assert "already a Claude Code account called" in _text(results[0])
    assert "never passed in the chat" in _text(results[1])
    assert len(proxy.created) == 0
    assert "hunter2-v14380" not in _db_text(p)


@pytest.mark.asyncio
async def test_account_groups_name_the_accounts_by_their_names(tmp_path, monkeypatch):
    app = _app(tmp_path, [("config_change_protected", {"key": "account_groups", "value": {"Work": ["TSI", "vr2"]}})])
    p = app.state.platform
    proxy = _Proxy(accounts=[("anthropic", "TSI", "ready"), ("anthropic", "VR2", "ready"), ("openai", "Home", "ready")])
    _with_proxy(monkeypatch, p, proxy)
    seen: list = []
    await _drive_stream(app, _body("put TSI and VR2 in a group called Work"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["config_change_protected"], "an account group always asks"
    assert p.config.account_groups == {"Work": ["prof_0001", "prof_0002"]}


@pytest.mark.asyncio
async def test_two_accounts_with_one_name_are_refused_for_a_group(tmp_path, monkeypatch):
    app = _app(tmp_path, [("config_change_protected", {"key": "account_groups", "value": {"Work": ["Main"]}})])
    p = app.state.platform
    proxy = _Proxy(accounts=[("anthropic", "Main", "ready"), ("openai", "Main", "ready")])
    _with_proxy(monkeypatch, p, proxy)
    seen: list = []
    frames = await _drive_stream(app, _body("put my Main accounts in a group called Work"), await _approve_all(app, seen))
    assert "two accounts are called" in _text(_finished(frames, "config_change_protected"))
    assert not p.config.account_groups


# =========================================================================== #
# memory_base_add
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_notes_folder_becomes_a_live_memory_base_and_undo_removes_it(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# Clients\nJohnson audit is due in March.\n", encoding="utf-8")
    app = _app(tmp_path, [("memory_base_add", {"kind": "folder", "name": "Vault", "folder": str(vault)})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body(f"add my Obsidian vault at {vault} as memory"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["memory_base_add"]
    assert "vault" in p.ltm.sources(), "registered LIVE, no restart"
    hits = p.ltm.get("vault").search("Johnson", k=3)
    assert hits, "it is searched at once"
    await _undo_card(app, _card(frames, "Memory base added"))
    assert "vault" not in p.ltm.sources()
    from iron_jarvis.ltm.sources import CustomSourceStore

    assert CustomSourceStore(p.engine).get("vault") is None


@pytest.mark.asyncio
async def test_notion_as_memory_takes_its_token_on_the_card_works_at_once_and_undo_takes_it_all_back(tmp_path):
    app = _app(tmp_path, [("memory_base_add", {
        "kind": "notion", "name": "Work notes", "database": f"https://www.notion.so/team/{DB_ID}?v=1",
        "use_as_memory": True,
    })])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("Use Notion as your long-term memory"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["memory_base_add"]
    card_words = _text(seen[0][1])
    assert "Notion is a cloud service" in card_words and "where Jarvis saves what it learns" in card_words
    from iron_jarvis.brain.profiles import BrainProfileStore
    from iron_jarvis.ltm.sources import CustomSourceStore

    rec = CustomSourceStore(p.engine).get("work_notes")
    assert rec.kind == "notion" and rec.database_id == DB_ID and rec.token_secret == "ltm_work_notes_notion"
    profiles = BrainProfileStore(p).list()
    assert [x["primary"] for x in profiles] == ["work_notes"]
    assert p.config.brain_profile == profiles[0]["id"]
    secret = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
    assert secret["name"] == "memory.work_notes"
    status, raw = await _asgi_post(app, "/config/secret", {"name": "memory.work_notes", "value": NOTION_TOKEN})
    assert status == 200 and NOTION_TOKEN.encode() not in raw
    conn = p.ltm.get("work_notes")
    assert conn.token_resolver() == NOTION_TOKEN, "the live base reads the token the moment it is saved"
    assert NOTION_TOKEN not in _db_text(p) + json.dumps(frames, default=str)
    card = _card(frames, "Memory base added")
    assert card["key"] == "memory_profile.work_notes"
    await _undo_card(app, card)
    assert CustomSourceStore(p.engine).get("work_notes") is None and "work_notes" not in p.ltm.sources()
    assert BrainProfileStore(p).list() == [] and p.config.brain_profile == ""
    assert p.secrets.get("ltm_work_notes_notion") is None


@pytest.mark.asyncio
async def test_undo_never_removes_a_base_another_profile_relies_on(tmp_path):
    vault = tmp_path / "notes"
    vault.mkdir()
    app = _app(tmp_path, [("memory_base_add", {"kind": "folder", "name": "Notes", "folder": str(vault)})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body(f"add {vault} as a memory base"), await _approve_all(app, seen))
    from iron_jarvis.brain.profiles import BrainProfileStore

    BrainProfileStore(p).create({"name": "Mine", "primary": "notes"})
    status, text = _undo(app, _card(frames, "Memory base added"))
    assert status == 409 and "Mine" in text, text
    assert "notes" in p.ltm.sources()


@pytest.mark.asyncio
async def test_a_notion_base_with_no_database_a_network_folder_and_a_token_are_refused_with_no_card(tmp_path):
    app = _app(tmp_path, [
        ("memory_base_add", {"kind": "notion", "name": "N"}),
        ("memory_base_add", {"kind": "folder", "name": "Share", "folder": "\\\\server\\notes"}),
        ("memory_base_add", {"kind": "notion", "name": "T", "database": DB_ID, "token": NOTION_TOKEN}),
    ])
    p = app.state.platform
    frames = await _drive_no_card(app, _body("add Notion as a memory base"))
    results = [_text(r) for r in _finished(frames, "memory_base_add")]
    assert "needs the Notion database" in results[0]
    assert "network share" in results[1]
    assert "never passed in the chat" in results[2]
    assert NOTION_TOKEN not in _db_text(p)


@pytest.mark.asyncio
async def test_the_memory_profile_is_chosen_by_its_name(tmp_path):
    app = _app(tmp_path, [("config_change", {"key": "brain_profile", "value": "Work"})])
    p = app.state.platform
    from iron_jarvis.brain.profiles import BrainProfileStore

    work = BrainProfileStore(p).create({"name": "Work", "primary": "brain"})
    seen: list = []
    await _drive_stream(app, _body("use my memory profile Work"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["config_change"]
    assert p.config.brain_profile == work["id"]


@pytest.mark.asyncio
async def test_an_unknown_memory_profile_name_is_said(tmp_path):
    app = _app(tmp_path, [("config_change", {"key": "brain_profile", "value": "Nope"})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("use my memory profile Nope"), await _approve_all(app, seen))
    assert "no memory profile called" in _text(_finished(frames, "config_change"))
    assert p.config.brain_profile == ""


# =========================================================================== #
# Registration, doors, claims
# =========================================================================== #
def test_every_tool_is_registered_ask_tier_low_trust_denied_and_worded(tmp_path):
    from iron_jarvis.agents import mission
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    for name in WRITERS:
        assert p.registry.get(name) is not None, name
        assert R.RECORD_TOOL_TIERS[name] == RC.CONNECTIONS_TOOL_TIERS[name] == "ask"
        assert p.permissions.authorize(name, {}).allowed is False, f"{name} must ask"
        assert name in LOW_TRUST_DENY and name in R.RECORD_CARD_TOOLS
        assert name in mission.ASK_WORDS and name in mission._RUN_TOOL_WORDS
    words = (ROOT / "dashboard" / "lib" / "toolWords.ts").read_text(encoding="utf-8")
    run = (ROOT / "dashboard" / "lib" / "runWords.ts").read_text(encoding="utf-8")
    for name in WRITERS:
        assert f'  {name}: "{mission.ASK_WORDS[name]}",' in words
        did = mission._RUN_TOOL_WORDS[name][0]
        assert f'{name}: {{ did: "{did[0].upper()}{did[1:]}"' in run


def test_the_secure_cards_name_the_vault_rows_the_pages_read():
    from iron_jarvis.settings.credentials import resolve_secret

    assert resolve_secret("model_server.dgx")[2] == "endpoint_dgx_key"
    assert resolve_secret("memory.work_notes")[2] == "ltm_work_notes_notion"


def test_the_account_door_is_only_ever_its_terminal_or_the_accounts_card():
    from iron_jarvis.daemon.doors import door_for

    def res(href):
        return SimpleNamespace(ok=True, data={"door": {"href": href, "label": "Sign in Work in its terminal"}})

    assert door_for("account_add", res("/terminals?focus=term_1"))["href"] == "/terminals?focus=term_1"
    assert door_for("account_add", res("/connections"))["href"] == "/connections"
    for bad in ("https://evil.example.com", "/terminals?focus=x&y=1", "javascript:alert(1)", "/settings"):
        assert door_for("account_add", res(bad)) is None, bad


@pytest.mark.parametrize("reply,tools,cards,noted", [
    ("I added your LM Studio server.", [], [], True),
    ("I added your LM Studio server.", ["model_server_add"], [], False),
    ("I've added a second Claude account called Work.", [], [], True),
    ("I've added a second Claude account called Work.", ["account_add"], [], False),
    ("I added Notion as your memory base.", [], [], True),
    ("I added Notion as your memory base.", ["memory_base_add"], [], False),
    ("I removed the vLLM server.", [], [], True),
    ("I removed the vLLM server.", ["model_server_remove"], [], False),
    ("I made qwen3 your default model.", ["model_server_add"], [], True),
    ("I made qwen3 your default model.", ["model_server_default"],
     [{"kind": "change", "title": "Default model changed", "key": "model_default.dgx", "change_id": "rec_1"}], False),
    # never a claim: someone else's server or account, an offer
    ("I added a server to your Discord.", [], [], False),
    ("I updated your bank account details in the spreadsheet.", [], [], False),
    ("I can add your LM Studio server if you like.", [], [], False),
    ("I added the mail server settings to config.py.", [], [], False),
])
def test_a_claimed_connection_change_is_checked_against_this_turn(reply, tools, cards, noted):
    from iron_jarvis.daemon.change_claims import claimed_change_note

    assert bool(claimed_change_note(reply, tools, cards)) is noted


def test_the_accounts_page_step_says_adding_is_in_chat_and_signing_in_is_the_users():
    from iron_jarvis.daemon import page_doors as P

    line = next(s.line for s in P.STEPS if s.id == "accounts")
    assert "account_add" in line and "Add account" in line and f'"{P.ACCOUNTS_CARD}"' in line
    assert "cannot do it for the user" in line


# =========================================================================== #
# Arming, both ways
# =========================================================================== #
REQUESTS: list[tuple[str, str]] = [
    ("Add my LM Studio server at 192.168.1.20:1234 and make it default", "model_server_add"),
    ("add my Ollama box at http://dgx:11434", "model_server_add"),
    ("connect my vLLM server at http://10.0.0.5:8000/v1", "model_server_add"),
    ("set up my local model server on 192.168.1.50:8080", "model_server_add"),
    ("add an OpenAI-compatible endpoint at https://gateway.example.com/v1", "model_server_add"),
    ("hook up the llama.cpp server running on localhost:8080", "model_server_add"),
    ("register my LiteLLM gateway at http://spark:4000", "model_server_add"),
    ("please add my LM Studio server", "model_server_add"),
    ("can you add my vLLM server at http://10.0.0.5:8000/v1?", "model_server_add"),
    ("add the inference server at 10.1.2.3:9000", "model_server_add"),
    ("Add my LM Studio server at 192.168.1.20:1234 and make it default", "model_server_default"),
    ("use qwen3 from my DGX server by default", "model_server_default"),
    ("make glm-5 on the spark server my default model", "model_server_default"),
    ("set my default to the llama model on my Ollama box", "model_server_default"),
    ("remove the DGX server", "model_server_remove"),
    ("delete my LM Studio server", "model_server_remove"),
    ("forget the vLLM server at 10.0.0.5:8000", "model_server_remove"),
    ("stop using the Ollama box", "model_server_remove"),
    ("Add a second Claude account", "account_add"),
    ("add another Codex account", "account_add"),
    ("add my work ChatGPT account", "account_add"),
    ("set up a Grok account for personal use", "account_add"),
    ("add a Claude Max account for work", "account_add"),
    ("connect one more Claude Code login", "account_add"),
    ("put my work accounts in a group called Work", "config_change_protected"),
    ("make an account group for my personal Claude accounts", "config_change_protected"),
    ("put my Claude and Codex accounts into separate groups", "config_change_protected"),
    ("Use Notion as your long-term memory", "memory_base_add"),
    ("add my Obsidian vault at D:\\vault as memory", "memory_base_add"),
    ("add D:\\Notes as a memory base", "memory_base_add"),
    ("connect my Notion as your brain", "memory_base_add"),
    ("remember things in Notion from now on", "memory_base_add"),
    ("set up a memory base in my notes folder", "memory_base_add"),
    ("save what you learn to my Obsidian vault", "memory_base_add"),
    # v1.448.0 (Wave A "chat configures everything", A1): a program said to be
    # RUNNING here with a use/connect cue; the memory pointed somewhere.
    ("I'm running Ollama with llama3 on this PC, use it", "model_server_add"),
    ("LM Studio is running on port 1234, connect to it", "model_server_add"),
    ("I installed vLLM on this machine, point Jarvis at it", "model_server_add"),
    ("Ollama is up on this PC, make it my default", "model_server_default"),
    ("Point your long-term memory at my Obsidian vault in D:\\Notes", "memory_base_add"),
    ("keep your memory in Notion", "memory_base_add"),
    ("move your brain to the Notes folder", "memory_base_add"),
]
NEAR_MISSES: list[str] = [
    # v1.448.0 (Wave A, A1): a program running with no use asked, the
    # opposite request, the memory words in their everyday senses.
    "the Ollama logo is a llama",
    "Ollama is running slow on my PC",
    "I'm running Ollama on my laptop for fun",
    "LM Studio is running on port 1234 but I don't want you to use it",
    "use Ollama to summarize this file",
    "my memory of the vault is hazy",
    "put the memory card in the folder",
    "use your memory to find the folder",
    "my LM Studio keeps crashing",
    "add a server to my Discord",
    "my second account at the bank",
    "Notion is down",
    "my memory is bad",
    "group chat with work",
    "add a Minecraft server",
    "use Ollama to summarize this file",
    "what is LM Studio?",
    "is my Ollama box online?",
    "add an endpoint to my FastAPI app",
    "remove the mail server from the config",
    "delete the server-side validation",
    "add my bank account to QuickBooks",
    "add my ChatGPT account to Zapier",
    "I created a Claude account yesterday",
    "add Sarah to the group",
    "put me in a group chat with work",
    "put the files in a group called Q3",
    "add this to my Notion",
    "use Notion to write a page about the audit",
    "I use Obsidian for notes",
    "remember to email Bob",
    "save this note in Notion",
    "my Obsidian vault is slow",
    "add a new memory of our trip to my journal",
    "the web server is down",
    "how many accounts do I have?",
    "set up a test server for the website",
    "make the default font bigger in Word",
    "use the default settings for the printer",
    "add 192.168.1.1 to my router's blocklist",
    "connect to the VPN at 10.0.0.1",
    "add a column for account numbers",
]


@pytest.fixture(autouse=True)
def _fresh_matcher():
    RC.connections_tools_for.cache_clear()
    yield
    RC.connections_tools_for.cache_clear()


@pytest.mark.parametrize("text,tool", REQUESTS)
def test_a_request_offers_its_tool(text, tool):
    assert tool in R.record_tools_for(text), R.record_tools_for(text)


@pytest.mark.parametrize("text", NEAR_MISSES)
def test_an_everyday_sentence_arms_none_of_these_writers(text):
    got = [n for n in R.record_tools_for(text) if n in (*WRITERS, "config_change_protected")]
    assert got == [], got


def test_an_account_named_in_a_group_request_arms_the_group_setting(monkeypatch):
    from iron_jarvis.iron_proxy import service as svc_mod

    proxy = _Proxy(accounts=[("anthropic", "TSI", "ready"), ("anthropic", "VR2", "ready")])
    monkeypatch.setattr(svc_mod, "_CURRENT", proxy, raising=False)
    assert "config_change_protected" in R.record_tools_for("put TSI and VR2 in a group called Work")
    assert "config_change_protected" not in R.record_tools_for("put Sarah and Tom in a group called Family")


@pytest.mark.parametrize("text", [
    "can I add my LM Studio server?", "what models does my Ollama box have?",
    "should I add a second Claude account?", "how do I use Notion as your long-term memory?",
])
def test_a_question_arms_none_of_these_writers(text):
    got = [n for n in R.record_tools_for(text) if n in WRITERS]
    assert got == [], got


def test_the_table_is_big_enough_both_ways():
    assert len(REQUESTS) >= 30 and len(NEAR_MISSES) >= 30
    assert {t for _r, t in REQUESTS} == set(WRITERS) | {"config_change_protected"}


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    return SimpleNamespace(platform=create_app(str(tmp_path_factory.mktemp("arm") / "home")).state.platform)


@pytest.mark.parametrize("text,tool", [r for r in REQUESTS if r[1] in WRITERS][::3])
def test_the_stream_lane_shows_them_and_the_post_lane_never_arms_them(d, text, tool):
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    assert not set(armed) & set(WRITERS), armed
    assert tool in CT.settings_ask_tools(d, text, armed, [])


_PATHOLOGICAL = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from iron_jarvis.settings import records_connections as RC
bad = [
    "add " + "my " * 6000 + "server",
    "add " + "x" * 19000 + ":1234",
    "remove " + "the " * 5000 + "box",
    "put " + "a " * 9000 + "in a group",
    "use " + "notion " * 2800 + "as memory",
    "add " + "a-" * 9000 + " claude account",
    "http://" + "a" * 19990,
    "make " + "x " * 9000 + "default",
    "remember " + "things " * 2800 + "in notion",
    "1." * 9000 + "1:80",
    "ollama " * 2800 + "is running, use it",
    "i'm running ollama " + "x " * 9000 + " use it",
    "running " * 2800 + "ollama on this pc, use it",
    "point your memory " + "x " * 9000 + " at my vault",
    "point your your your " * 2800 + "memory at my vault",
    "keep your memory in " + "the x " * 4000 + " folder",
]
t0 = time.perf_counter()
for text in bad:
    RC.connections_tools_for.cache_clear()
    RC.connections_tools_for(text)
    RC.without_addresses(text)
print(time.perf_counter() - t0)
"""


def test_the_matchers_are_linear_on_pathological_input():
    """A child process with a deadline (a hang detector, never a speed bar)."""
    out = subprocess.run(
        [sys.executable, "-c", _PATHOLOGICAL, str(ROOT / "src")], capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 5.0
