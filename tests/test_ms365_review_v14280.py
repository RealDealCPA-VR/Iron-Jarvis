"""v1.428.0 review — the holes an independent review found in the locked
Microsoft 365 card, each closed and pinned here (``tests/test_ms365_locked_
v14280.py`` pins the card itself):

* (b) the program gets its settings from Iron Jarvis ONLY: a daemon started
  with ``MS365_MCP_*`` (an OAuth token that skips the account lock, a command
  that stores the sign-in), a wider ``ENABLED_TOOLS`` or ``NODE_OPTIONS`` never
  hands them on — not to the pack, not to its Sign in terminal;
* (c) its tools answer only in a plain chat on this PC — never an agent's
  task, a coding session or the phone (each carries a tool's answer further);
* (d) the account-switching tools (logout, remove-account, select-account)
  are never offered;
* (e) it always asks before each use: "run without asking" is refused;
* (f) a chat that read it never sends a message to the cloud, even "once";
* (g) a new chat's first turn holds the chat whichever comes first: the page
  linking the turn, or the tool reading the mail;
* (h) its program is added only from its card: the add door refuses a copy,
  and a row from before is never started;
* (i) its results stay behind when a chat hands work to an agent;
* (j) a chat that read it is never written to a memory base off this PC.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from iron_jarvis.connectors import locked_pack as LP
from iron_jarvis.daemon import chat_turn
from iron_jarvis.mcp import tools as mcp_tools
from iron_jarvis.providers import egress
from iron_jarvis.providers.failure_words import phone_surface, run_surface
from tests import test_ms365_locked_v14280 as locked
from tests.test_ms365_locked_v14280 import CARD, GOOD, _connect, _ctx, _new_chat, _row, _tool, needs_node

client = locked.client
_checked_native = locked._checked_native


# --------------------------------------------------------------------------- #
# (b) nothing from the daemon's own environment
# --------------------------------------------------------------------------- #
STRAY = {
    "MS365_MCP_OAUTH_TOKEN": "stray-token",
    "MS365_MCP_AUTH_CACHE_COMMAND": "calc.exe",
    "ms365_mcp_use_keytar": "off",
    "ENABLED_TOOLS": ".*",
    "NODE_OPTIONS": "--require C:/evil.js",
}


def test_clean_env_drops_every_setting_the_program_must_only_get_from_iron_jarvis():
    out = LP.clean_env({"PATH": "C:/bin", "HOME": "C:/u", **STRAY})
    assert out == {"PATH": "C:/bin", "HOME": "C:/u"}


@needs_node
def test_the_pack_never_inherits_the_daemons_microsoft_settings(client, monkeypatch):
    assert _connect(client).status_code == 200
    for k, v in STRAY.items():
        monkeypatch.setenv(k, v)
    cfg = _row(client)
    transport = mcp_tools._build_transport(cfg, None)
    env = {str(k).upper(): v for k, v in (transport.env or {}).items()}
    for k in STRAY:
        assert k.upper() not in env or env[k.upper()] == cfg["env"].get(k.upper()), k
    assert env["ENABLED_TOOLS"] == cfg["env"]["ENABLED_TOOLS"]
    assert env.get("PATH") == os.environ.get("PATH")


def test_an_unlocked_pack_still_inherits_the_daemons_environment(monkeypatch):
    monkeypatch.setenv("ENABLED_TOOLS", ".*")
    transport = mcp_tools._build_transport({"name": "x", "command": "node", "args": ["a.js"],
                                            "env": {"A": "1"}}, None)
    assert transport.env["ENABLED_TOOLS"] == ".*" and transport.env["A"] == "1"


# --------------------------------------------------------------------------- #
# (c) only in a plain chat on this PC
# --------------------------------------------------------------------------- #
@needs_node
def test_its_tools_answer_only_in_a_plain_chat(client, tmp_path):
    assert _connect(client).status_code == 200
    tool = _tool(client)

    async def run(session_id="chat", around=None):
        with egress.local_only_scope(True):
            if around is None:
                return await tool.execute({}, _ctx_for(client, tmp_path, session_id))
            with around():
                return await tool.execute({}, _ctx_for(client, tmp_path, session_id))

    assert asyncio.run(run()).ok
    for sid, around in (("build:abc", None), ("session_1", None), ("chat", run_surface), ("chat", phone_surface)):
        out = asyncio.run(run(sid, around))
        assert not out.ok and "only in Chat on this PC" in out.error, (sid, around)


def _ctx_for(c, tmp_path, session_id):
    ctx = _ctx(c, tmp_path)
    ctx.session_id = session_id
    return ctx


# --------------------------------------------------------------------------- #
# (d) the account-switching tools are never offered
# --------------------------------------------------------------------------- #
@needs_node
def test_the_account_switching_tools_are_never_offered(client, monkeypatch):
    # The stand-in server lists the package's own account tools too.
    real = mcp_tools._connect_with_timeout

    def with_account_tools(cfg, name, resolver, timeout):
        client_, specs = real(cfg, name, resolver, timeout)
        extra = [{"name": n, "inputSchema": {"type": "object"}} for n in
                 ("login", "logout", "verify-login", "list-accounts", "select-account", "remove-account")]
        return client_, [*specs, *extra]

    monkeypatch.setattr(mcp_tools, "_connect_with_timeout", with_account_tools)
    assert _connect(client).status_code == 200
    reg = client.app.state.platform.registry
    for hidden in mcp_tools.LOCKED_HIDDEN_TOOLS:
        assert reg.get(f"mcp__{CARD}__{hidden}") is None, hidden
    for shown in ("login", "verify-login", "list-accounts"):
        assert reg.get(f"mcp__{CARD}__{shown}") is not None, shown


# --------------------------------------------------------------------------- #
# (e) it always asks
# --------------------------------------------------------------------------- #
@needs_node
def test_run_without_asking_is_refused_and_never_counts(client):
    assert _connect(client).status_code == 200
    r = client.patch(f"/mcp/servers/{CARD}", json={"auto_approve": True})
    assert r.status_code == 409 and "asks you before each use" in r.json()["detail"]
    assert client.patch(f"/mcp/servers/{CARD}", json={"auto_approve": False}).status_code == 200
    # A row edited by hand still never opens run-without-asking.
    assert mcp_tools.pack_grants_auto({**_row(client), "auto_approve": True}) is False
    assert mcp_tools.pack_grants_auto({"name": "x", "auto_approve": True}) is True


# --------------------------------------------------------------------------- #
# (f) a held chat never sends a message to the cloud, even once
# --------------------------------------------------------------------------- #
@needs_node
def test_a_held_chat_ignores_send_once(client, tmp_path):
    p = client.app.state.platform
    held = _new_chat(client)
    assert egress.hold_local_only(p.engine, held, CARD)
    plain = _new_chat(client)  # Local only, never read Microsoft 365

    def scope(tid):
        body = SimpleNamespace(local_only=True, thread_id=tid, allow_cloud_once="anthropic",
                               provider="anthropic", model="", project_id="")
        return asyncio.run(chat_turn.local_only_scope_for(p, body))

    assert scope(plain).allowed_once == "anthropic", "an ordinary local-only chat keeps its OK"
    got = scope(held)
    assert got.on and not got.allowed_once


# --------------------------------------------------------------------------- #
# (g) the hold lands whichever comes first
# --------------------------------------------------------------------------- #
@needs_node
def test_a_turn_linked_before_the_mail_is_read_still_holds_its_chat(client, tmp_path):
    assert _connect(client).status_code == 200
    tid = _new_chat(client, {})
    r = client.post("/chat/turns/turn-early-1/thread", json={"thread_id": tid})
    assert r.status_code == 200, r.text
    engine = client.app.state.platform.engine
    assert egress.thread_local_only(engine, tid) is False
    tool = _tool(client)

    async def run():
        with egress.local_only_scope(True):
            return await tool.execute({}, _ctx(client, tmp_path, turn_id="turn-early-1"))

    assert asyncio.run(run()).ok
    assert egress.thread_holds(engine, tid) == [CARD]


def test_a_hold_and_a_link_of_the_same_turn_never_miss_each_other():
    assert egress.note_pending_hold("t-1", CARD) == ""  # nothing linked yet: pending
    assert egress.link_turn_holds("t-1", "chat_1") == [CARD]  # the link takes it
    assert egress.note_pending_hold("t-1", CARD) == "chat_1"  # later: straight to the chat
    assert egress.take_pending_holds("t-1") == []


# --------------------------------------------------------------------------- #
# (h) only from its card
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", [
    {"name": "outlook", "command": "npx", "args": ["-y", "@softeria/ms-365-mcp-server"]},
    {"name": "mail", "command": "npx", "args": ["-y", "@softeria/ms-365-mcp-server@0.170.2", "--org-mode"]},
    {"name": "microsoft_365", "command": "node", "args": ["anything.js"]},
])
def test_the_add_door_refuses_an_unlocked_copy(client, body):
    r = client.post("/mcp/servers", json=body)
    assert r.status_code == 409, r.text
    assert "set up only from its card" in r.json()["detail"]
    assert not [s for s in client.app.state.platform.config.mcp_servers if s.get("name") == body["name"]]


def test_a_row_from_before_is_never_started(monkeypatch):
    started = []
    monkeypatch.setattr(mcp_tools, "_connect_with_timeout", lambda *a, **k: started.append(a) or (None, []))
    errors: dict = {}
    cfg = {"name": "microsoft_365", "command": "npx", "args": ["-y", "@softeria/ms-365-mcp-server"]}
    assert mcp_tools._load_one_server(cfg, None, 1.0, record=False, errors=errors) == []
    assert started == []
    assert "set up only from its card" in errors["microsoft_365"]["error"]
    # The locked row itself is not stopped by this rule.
    assert LP.unlocked_copy({**cfg, "locked": "ms365"}) == ""


def test_an_ordinary_pack_is_untouched():
    assert LP.unlocked_copy({"name": "github", "command": "npx", "args": ["-y", "@x/github"]}) == ""


# --------------------------------------------------------------------------- #
# (i) its results stay behind on a hand-off
# --------------------------------------------------------------------------- #
def test_its_results_stay_behind_when_a_chat_hands_work_to_an_agent():
    kept = SimpleNamespace(local_only=True)
    plain = SimpleNamespace(local_only=False)
    reg = SimpleNamespace(get=lambda n: {"mcp__microsoft_365__list-mail-messages": kept,
                                         "read_file": plain}.get(n))
    platform = SimpleNamespace(registry=reg)
    rows = [
        {"tool": "mcp__microsoft_365__list-mail-messages", "args_summary": "{}", "ok": True,
         "output_head": "Subject: Client K-1"},
        {"tool": "read_file", "args_summary": "{}", "ok": True, "output_head": "hello"},
    ]
    out = chat_turn.cap_prior_tool_results(rows, platform)
    assert [r["tool"] for r in out] == ["read_file"]
    # Without the platform (an older caller), nothing changes.
    assert len(chat_turn.cap_prior_tool_results(rows)) == 2


# --------------------------------------------------------------------------- #
# (j) never written to a memory base off this PC
# --------------------------------------------------------------------------- #
def test_a_held_chat_is_never_written_to_a_memory_base_off_this_pc(client, monkeypatch):
    p = client.app.state.platform
    tid = _new_chat(client, {})
    assert egress.hold_local_only(p.engine, tid, CARD)
    monkeypatch.setattr(egress, "ltm_connector_is_local", lambda conn: False)
    r = client.post(f"/chat/threads/{tid}/remember", json={"mode": "full"})
    assert r.status_code == 409
    assert "not on this PC" in r.json()["detail"]


def test_an_ordinary_chat_is_still_written_to_its_memory_base(client):
    tid = _new_chat(client, {})
    r = client.post(f"/chat/threads/{tid}/remember", json={"mode": "full"})
    assert r.status_code == 200, r.text
