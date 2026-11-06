"""v1.438.0 review (chat-first plan Wave 4 group B): the independent
reviewer's pins on ``settings/records_connections``. Each pin was RED on the
builder's commit (df8e8c75) before its fix.

Same rules as the builder's file: the real stream lane, the real approval
route, the real Undo route; a model server is a stand-in on 127.0.0.1 that
records the key each request carried; Iron-Proxy is in memory; nothing else
is reached (``no_network``).
"""

from __future__ import annotations

import pytest

from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_connections as RC
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_records_connections_v14380 import (
    WRITERS,
    _app,
    _approve_all,
    _card,
    _drive_no_card,
    _finished,
    _ModelServer,
    _Proxy,
    _seed_server,
    _text,
    _undo,
    _with_proxy,
)
from tests.test_settings_chat_tools_v1321 import _body, _undo_card

pytestmark = pytest.mark.usefixtures("no_network")

OLD_KEY = "sk-OLD-v14380-key-of-a-removed-server"
NEW_KEY = "sk-NEW-v14380-key"
DB_ID = "0123456789abcdef0123456789abcdef"


# =========================================================================== #
# 1. A key saved for an earlier server is never sent to a NEW address
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_key_left_by_a_removed_server_is_never_sent_to_a_new_server_of_that_name(tmp_path):
    """The Fleet page's Remove keeps ``endpoint_<id>_key`` (an Undo of a
    removal needs it). A NEW server added from chat under the same name used
    to get that id, that key name, and so that key: sent to the new address
    by the add's own detection and probe, before the user pasted anything."""
    other = _ModelServer()
    try:
        app = _app(tmp_path, [("model_server_add", {"address": other.url, "name": "DGX", "needs_key": True})])
        p = app.state.platform
        p.secrets.set("endpoint_dgx_key", OLD_KEY, kind="token")
        seen: list = []
        frames = await _drive_stream(app, _body(f"add my vLLM server at {other.url}"), await _approve_all(app, seen))
        assert other.seen, "the new server was probed"
        assert all(OLD_KEY not in auth for _path, auth in other.seen), other.seen
        node = next(n for n in p.fleet.nodes() if n.base_url == other.url)
        assert node.api_key_name != "endpoint_dgx_key"
        secret = next(c for c in _done_cards(frames) if c["kind"] == "secret")
        assert secret["name"] == f"model_server.{node.id}"
        # …and its Undo never deletes a key this change did not make.
        await _undo_card(app, _card(frames, "Model server added"))
        assert p.secrets.get("endpoint_dgx_key") == OLD_KEY
    finally:
        other.close()


def _done_cards(frames):
    return next(d for ev, d in frames if ev == "done")["config_cards"]


# =========================================================================== #
# 2. An Undo of a memory base never deletes a token it did not make
# =========================================================================== #
@pytest.mark.asyncio
async def test_undo_of_a_notion_base_keeps_a_token_that_was_there_before(tmp_path):
    app = _app(tmp_path, [("memory_base_add", {"kind": "notion", "name": "Work notes", "database": DB_ID})])
    p = app.state.platform
    p.secrets.set("ltm_work_notes_notion", OLD_KEY, kind="token")
    seen: list = []
    frames = await _drive_stream(app, _body("Use Notion as your long-term memory"), await _approve_all(app, seen))
    await _undo_card(app, _card(frames, "Memory base added"))
    assert p.secrets.get("ltm_work_notes_notion") == OLD_KEY, "a token this change did not make is kept"


# =========================================================================== #
# 3. An account Undo re-checks the sign-in before it removes anything
# =========================================================================== #
@pytest.mark.asyncio
async def test_account_undo_asks_iron_proxy_again_before_removing_a_login(tmp_path, monkeypatch):
    """Iron-Proxy's state can still read "unauthenticated" after the login
    landed (v1.303.3: it is TOLD to re-check). The Undo re-checks first: a
    login that is really signed in is never deleted."""
    app = _app(tmp_path, [("account_add", {"vendor": "claude", "name": "Work"})])
    p = app.state.platform
    proxy = _Proxy()
    _with_proxy(monkeypatch, p, proxy)
    signed_in: set[str] = set()
    refreshed: list[str] = []

    def refresh(self, pid=None, timeout=None):
        refreshed.append(pid)
        if pid in signed_in:
            self.o.states[pid]["status"] = "ready"
        return [dict(self.o.states[pid])] if pid in self.o.states else []

    from tests import test_records_connections_v14380 as base

    monkeypatch.setattr(base._ProxyClient, "refresh", refresh, raising=False)
    seen: list = []
    frames = await _drive_stream(app, _body("Add a second Claude account called Work"), await _approve_all(app, seen))
    pid = proxy.profiles[0]["id"]
    signed_in.add(pid)  # the login landed; the cached state still says unauthenticated
    assert proxy.states[pid]["status"] == "unauthenticated"
    status, text = _undo(app, _card(frames, "Account added"))
    assert refreshed == [pid], "the sign-in was checked again"
    assert status == 409 and "sign that login out" in text, text
    assert proxy.deleted == []


@pytest.mark.asyncio
async def test_account_undo_keeps_the_account_when_the_recheck_fails(tmp_path, monkeypatch):
    from iron_jarvis.iron_proxy.client import IronProxyError

    app = _app(tmp_path, [("account_add", {"vendor": "codex", "name": "Personal"})])
    p = app.state.platform
    proxy = _Proxy()
    _with_proxy(monkeypatch, p, proxy)

    def refresh(self, pid=None, timeout=None):
        raise IronProxyError("Iron-Proxy did not answer.", code="UNREACHABLE")

    from tests import test_records_connections_v14380 as base

    monkeypatch.setattr(base._ProxyClient, "refresh", refresh, raising=False)
    seen: list = []
    frames = await _drive_stream(app, _body("add another Codex account"), await _approve_all(app, seen))
    status, text = _undo(app, _card(frames, "Account added"))
    assert status == 409, text
    assert proxy.deleted == []


# =========================================================================== #
# 4. Local only: what leaves this network asks in every posture
# =========================================================================== #
@pytest.mark.asyncio
async def test_in_a_local_only_chat_an_internet_server_asks_even_under_dont_ask(tmp_path):
    app = _app(tmp_path, [("model_server_add", {"address": "https://gateway.example.com/v1", "name": "Cloud"})])
    seen: list = []
    from tests.test_records_connections_v14380 import _deny_all

    await _drive_stream(
        app, _body("add my LiteLLM gateway at https://gateway.example.com/v1", mode="yolo", local_only=True),
        await _deny_all(app, seen),
    )
    assert [t for t, _ in seen] == ["model_server_add"], "a Local only chat asks before reaching the internet"
    assert "cloud" not in [n.id for n in app.state.platform.fleet.nodes()]


@pytest.mark.asyncio
async def test_in_a_local_only_chat_a_notion_base_asks_even_under_dont_ask(tmp_path):
    app = _app(tmp_path, [("memory_base_add", {"kind": "notion", "name": "N", "database": DB_ID})])
    seen: list = []
    from tests.test_records_connections_v14380 import _deny_all

    await _drive_stream(
        app, _body("connect Notion as your memory", mode="yolo", local_only=True), await _deny_all(app, seen)
    )
    assert [t for t, _ in seen] == ["memory_base_add"]


@pytest.mark.asyncio
async def test_a_local_server_in_a_local_only_chat_keeps_dont_ask(tmp_path):
    srv = _ModelServer()
    try:
        app = _app(tmp_path, [("model_server_add", {"address": srv.url, "name": "LM"})])
        frames = await _drive_no_card(app, _body(f"add my LM Studio server at {srv.url}", mode="yolo", local_only=True))
        assert _finished(frames, "model_server_add")
        assert app.state.platform.fleet.get("lm") is not None
    finally:
        srv.close()


# =========================================================================== #
# 5. The default-model card says where that model answers
# =========================================================================== #
@pytest.mark.asyncio
async def test_the_default_model_card_says_where_that_model_answers(tmp_path):
    srv = _ModelServer()
    try:
        app = _app(tmp_path, [("model_server_default", {"server": "dgx", "model": "glm-5"})])
        p = app.state.platform
        _seed_server(p, srv)
        seen: list = []
        await _drive_stream(app, _body("use glm-5 from my DGX server by default"), await _approve_all(app, seen))
        assert [t for t, _ in seen] == ["model_server_default"]
        assert "on this PC or your own network" in _text(seen[0][1]), _text(seen[0][1])
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_a_model_a_lan_proxy_sends_to_the_cloud_says_so_on_the_default_card(tmp_path):
    srv = _ModelServer(models=("glm-5", "frontier"))
    try:
        app = _app(tmp_path, [("model_server_default", {"server": "dgx", "model": "frontier"})])
        p = app.state.platform
        _seed_server(p, srv)
        p.providers._upstream_place = lambda name, model: "cloud" if model == "frontier" else "local"
        seen: list = []
        await _drive_stream(app, _body("use frontier from my DGX server by default"), await _approve_all(app, seen))
        words = _text(seen[0][1])
        assert "leaves this PC" in words and "own network" not in words, words
    finally:
        srv.close()


# =========================================================================== #
# 6. account_groups: a group request never drops the user's other groups
# =========================================================================== #
@pytest.mark.asyncio
async def test_naming_one_group_keeps_the_others_and_moves_the_named_accounts(tmp_path, monkeypatch):
    app = _app(tmp_path, [("config_change_protected", {"key": "account_groups", "value": {"Firm": ["TSI"]}})])
    p = app.state.platform
    proxy = _Proxy(accounts=[("anthropic", "TSI", "ready"), ("anthropic", "VR2", "ready"), ("openai", "Home", "ready")])
    _with_proxy(monkeypatch, p, proxy)
    p.config.account_groups = {"Work": ["prof_0001", "prof_0002"], "Personal": ["prof_0003"]}
    seen: list = []
    await _drive_stream(app, _body("put TSI in a group called Firm"), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["config_change_protected"]
    card = _text(seen[0][1])
    assert "Personal" in card and "Work" in card, f"the card shows the whole result: {card}"
    assert p.config.account_groups == {"Work": ["prof_0002"], "Personal": ["prof_0003"], "Firm": ["prof_0001"]}


def test_an_empty_list_removes_a_group_and_an_empty_value_clears_them_all():
    cur = {"Work": ["a", "b"], "Home": ["c"]}
    assert RC.merged_account_groups(cur, {"Home": []}) == {"Work": ["a", "b"]}
    assert RC.merged_account_groups(cur, {}) == {}
    assert RC.merged_account_groups(cur, {"Work": ["a", "b"], "Home": ["c"]}) == cur
    assert RC.merged_account_groups({}, {"New": ["x"]}) == {"New": ["x"]}
    # a group that loses its last account to a move is said on the card, and dropped
    assert RC.merged_account_groups({"Solo": ["a"]}, {"Firm": ["a"]}) == {"Firm": ["a"]}


# =========================================================================== #
# 7. Arming: fresh everyday sentences that must arm none of these writers
# =========================================================================== #
@pytest.fixture(autouse=True)
def _fresh_matcher():
    RC.connections_tools_for.cache_clear()
    yield
    RC.connections_tools_for.cache_clear()


REVIEW_NEAR_MISSES = [
    "set up an OpenAI account for my daughter's school project",
    "put my Claude notes in a group with the other research",
    "add the Claude account details to the onboarding doc",
    "draft an email to set up a Codex account trial for the team",
    "set up the Plex server at 192.168.1.10:32400",
    "save everything about the Smith audit in Notion",
    "add a ChatGPT plan comparison to the slide",
    "create a Claude Pro plan summary for the partners",
    "add the AI server costs to the budget",
    "connect the GPU box to the monitor",
    "connect the vLLM box to the network switch",
    "remove the old Ollama server from the inventory list",
    "connect the server at 10.0.0.5 to the printer",
    "put the Claude subscription costs in a group in the budget",
    "add a Claude account manager role in HR",
    "make an OpenAI account for each student in my class",
    "add the Ollama server specs to the quote",
    "add the web server at 10.0.0.5:80 to the DNS",
    "write a guide on how to add an Ollama server",
    "save everything from the meeting in Notion",
    "remove the Ollama server logs",
    "add up the AI server costs for last year",
]
REVIEW_REQUESTS = [
    ("switch my default to llama3 on the Ollama server", "model_server_default"),
    ("add my LM Studio on the office PC at 192.168.0.44:1234", "model_server_add"),
    ("hook up the Ollama box in the back room, it's at 10.1.1.7:11434", "model_server_add"),
    ("get rid of the old Ollama server", "model_server_remove"),
    ("set up a second Codex account called Personal", "account_add"),
    ("link my other Claude Max subscription", "account_add"),
    ("put my two Claude accounts in a group called Firm", "config_change_protected"),
    ("keep your memories in Obsidian", "memory_base_add"),
    ("connect the vLLM server at http://gpu01:8000 and call it GPU01", "model_server_add"),
    ("add another Claude account for the firm", "account_add"),
    ("remove the DGX Spark server", "model_server_remove"),
    ("add my Ollama box to your list of model servers", "model_server_add"),
    ("connect the vLLM server to Iron Jarvis at 10.0.0.8:8000", "model_server_add"),
    ("plug in my llama.cpp server at localhost:8081", "model_server_add"),
    ("set up a backup Codex login", "account_add"),
]


@pytest.mark.parametrize("text", REVIEW_NEAR_MISSES)
def test_review_near_misses_arm_none_of_these_writers(text):
    got = [n for n in R.record_tools_for(text) if n in (*WRITERS, "config_change_protected")]
    assert got == [], got


@pytest.mark.parametrize("text,tool", REVIEW_REQUESTS)
def test_review_requests_offer_their_tool(text, tool):
    assert tool in R.record_tools_for(text), R.record_tools_for(text)


@pytest.mark.parametrize("text", [
    "put the Work files in a group",
    "move the personal photos into a group album",
    "keep the TSI invoices in a separate group",
])
def test_an_account_name_that_is_an_everyday_word_arms_no_group(text, monkeypatch):
    monkeypatch.setattr(RC, "_account_names", lambda: ("Personal", "TSI", "VR2", "Work"))
    got = [n for n in R.record_tools_for(text) if n == "config_change_protected"]
    assert got == [], got


@pytest.mark.parametrize("text", ["put TSI and VR2 in a group called Firm", "move Personal into its own group"])
def test_an_account_named_in_a_group_request_still_arms_it(text, monkeypatch):
    monkeypatch.setattr(RC, "_account_names", lambda: ("Personal", "TSI", "VR2", "Work"))
    assert "config_change_protected" in R.record_tools_for(text)


# =========================================================================== #
# 8. change_claims: an asset list is the user's content
# =========================================================================== #
@pytest.mark.parametrize("reply", [
    "I added the GPU box to the asset inventory.",
    "I added the inference server to the asset list in your spreadsheet.",
])
def test_a_server_written_into_the_users_content_is_no_claim(reply):
    from iron_jarvis.daemon.change_claims import claimed_change_note

    assert not claimed_change_note(reply, [], [])


# =========================================================================== #
# 9. A credential in a REFUSED call never reaches the ledger or the stream
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"address": "http://me:hunter2-v14380@127.0.0.1:9/v1", "name": "x"},
    {"address": "http://127.0.0.1:9/v1?api_key=hunter2-v14380", "name": "x"},
    {"address": "http://127.0.0.1:9/v1", "name": "x", "access_token": "hunter2-v14380"},
    {"address": "http://127.0.0.1:9/v1", "name": "x", "apiKey": "hunter2-v14380"},
])
async def test_a_password_in_a_refused_call_is_never_logged(tmp_path, args):
    import json as _json

    from tests.test_records_connections_v14380 import _db_text

    app = _app(tmp_path, [("model_server_add", args)])
    p = app.state.platform
    frames = await _drive_no_card(app, _body("add my vLLM server"))
    assert _finished(frames, "model_server_add")
    assert "x" not in [n.id for n in p.fleet.nodes()], "a call carrying a credential is refused"
    assert "hunter2-v14380" not in _db_text(p), "never in the ledger"
    assert "hunter2-v14380" not in _json.dumps(frames, default=str), "never in the stream"


@pytest.mark.asyncio
async def test_a_token_under_another_name_is_refused_for_a_memory_base(tmp_path):
    from tests.test_records_connections_v14380 import _db_text

    app = _app(tmp_path, [("memory_base_add", {
        "kind": "notion", "name": "N", "database": DB_ID, "notion_token": "ntn_hunter2_v14380",
    })])
    p = app.state.platform
    frames = await _drive_no_card(app, _body("connect Notion as your memory"))
    assert "never passed in the chat" in _text(_finished(frames, "memory_base_add"))
    assert "ntn_hunter2_v14380" not in _db_text(p)


# =========================================================================== #
# 10. A server set up in Settings and renamed comes back whole
# =========================================================================== #
@pytest.mark.asyncio
async def test_removing_a_renamed_settings_server_and_undo_bring_back_its_address_name_and_key(tmp_path):
    srv = _ModelServer()
    try:
        app = _app(tmp_path, [("model_server_remove", {"server": "DGX Spark"})])
        p = app.state.platform
        p.config_writer.apply({"custom_base_url": srv.url, "custom_model": "qwen3"}, actor="test", journal=False)
        p.secrets.set("custom_api_key", OLD_KEY, kind="token")
        p.fleet.update("custom", label="DGX Spark")  # promoted: a stored row with source "config"
        seen: list = []
        frames = await _drive_stream(app, _body("remove the DGX Spark server"), await _approve_all(app, seen))
        assert [t for t, _ in seen] == ["model_server_remove"]
        assert p.fleet.get("custom") is None and not p.config.custom_base_url
        await _undo_card(app, _card(frames, "Model server removed"))
        node = p.fleet.get("custom")
        assert node is not None and node.label == "DGX Spark" and node.base_url == srv.url
        assert p.config.custom_model == "qwen3"
        assert p.secrets.get("custom_api_key") == OLD_KEY, "the slot's key was never touched"
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_an_add_undone_twice_is_refused_the_second_time(tmp_path):
    srv = _ModelServer()
    try:
        app = _app(tmp_path, [("model_server_add", {"address": srv.url, "name": "LM"})])
        seen: list = []
        frames = await _drive_stream(app, _body(f"add my LM Studio server at {srv.url}"), await _approve_all(app, seen))
        card = _card(frames, "Model server added")
        await _undo_card(app, card)
        status, text = _undo(app, card)
        assert status in (409, 410), text
    finally:
        srv.close()


# =========================================================================== #
# 11. The accounts line reaches every surface, so it never promises a tool
# =========================================================================== #
def test_the_accounts_line_offers_account_add_only_when_it_is_offered():
    from iron_jarvis.daemon import page_doors as P

    line = next(s.line for s in P.STEPS if s.id == "accounts")
    assert "account_add when that tool is offered" in line, line


@pytest.mark.parametrize("reply,noted", [
    ("I connected your Notion.", False),
    ("I connected your Gmail.", True),
    ("I connected Slack for you.", True),
])
def test_a_memory_base_backs_a_notion_claim_and_no_other_app(reply, noted):
    from iron_jarvis.daemon.change_claims import claimed_change_note

    assert bool(claimed_change_note(reply, ["memory_base_add"], [])) is noted


_REVIEW_PATHOLOGICAL = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from iron_jarvis.settings import records_connections as RC
bad = [
    ("use c:\\" + "a" * 118 + " ") * 150,
    ("add the server to the " + "big-x " * 30) * 100,
    ("draft " + "x " * 20 + " ") * 400,
    ("add a claude account for my " + "x " * 5) * 400,
    ("the " + "aa bb cc " * 3) * 500 + " server",
    ("connect the ollama box to the " + "a-b " * 10) * 300,
]
t0 = time.perf_counter()
for text in bad:
    RC.connections_tools_for.cache_clear()
    RC.connections_tools_for(text[:20000])
print(time.perf_counter() - t0)
"""


def test_the_review_matchers_are_linear_on_pathological_input():
    """A child process with a deadline (a hang detector, never a speed bar)."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", _REVIEW_PATHOLOGICAL, str(root / "src")], capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 5.0
