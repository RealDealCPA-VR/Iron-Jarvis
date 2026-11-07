"""A2 review of v1.448.0 (Wave A item 4, ``settings/records_tools``): the
independent reviewer's cases — each was RED on the builder's commit
(b776aa54) and is green with the review's fixes.

What the review found and fixed, each pinned here:

* ``?key=…`` in a server's address reached the approval card, the ledger and
  would have been stored plain: ``mask`` knows ``api_key=`` but not ``key=``.
  A secret-NAMED query parameter is now refused like any passed credential,
  and ``masked_config`` masks it wherever it still appears.
* A credential under an INNOCENT env key or header name (``FOLDER: ghp_…``,
  ``X-Trace: Bearer …``) was accepted and stored plain in ``config.toml`` —
  the pack door vaults by NAME only. A secret-SHAPED value under any env /
  header key is refused; ``_looks_like_a_secret`` also knows a bare
  ``Bearer <token>`` (``mask`` reads that shape only behind ``Authorization``),
  which also closes ``values: {folder: "Bearer …"}``.
* Every ``${…}`` placeholder config was broken at the door: ``Plan.
  filled_spec`` left ``spec.fills`` in place, and ``routes/agents.
  _body_from_spec`` re-runs ``fill_placeholders`` for a spec with ``asks`` —
  which DROPPED a card-saved env value and refused a filled argument ("type a
  value for folder first"), so the prebuilt catalog's ``filesystem`` with
  ``values`` was never added. ``filled_spec`` clears ``fills``.
* ``--token ${input:token}`` was refused as a PASSED value (``split_args``
  reads the argument after a secret flag as its value); a placeholder there
  is a secure-card slot now (vaulted as ``mcp.<pack>.arg.<i>``, the name the
  door launches with).
* ``_JSON_RX`` (``\\{.*\\}``) was quadratic on a text with braces and no
  closing one — 60,000 braces took 1.2 s ON THE LOOP (``redact_args``); it is
  ``find``/``rfind`` now.
* A card saved after a restart (or after the card expired / was withdrawn)
  left the value as an ORPHAN vault row (``mcp.<pack>.<KEY>``) nobody could
  see; it is deleted and the words say the value was not kept. An EXISTING
  pack's key through the bare ``tool_server.<pack>`` name got the "no longer
  waiting … ask again" words; it gets the plain "saved" words.
* A second ``mcp_server_add`` under a name already WAITING for its card
  silently replaced the first plan (the first card's save would then have
  added the second server); it is refused in words.
* The dashboard's secure card drew "saved securely" for a tool server's key
  and never its "added · N tools" answer (``dashboard/__tests__/
  tool-server-card-v1448.test.tsx``).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import pack_enabled, pack_grants_auto
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_tools as RT
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_records_connections_v14380 import _card, _finished, _text, _undo
from tests.test_records_tools_v14480 import (
    FIXTURE,
    WRITERS,
    _app,
    _approve_all,
    _drive_no_card,
    _everything,
    _stdio_config,
    d,  # noqa: F401 — the arming fixture
    lanes,
)
from tests.test_settings_chat_tools_v1321 import _body, _done

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]

#: The reviewer's OWN sentinel shapes — each built from pieces (the repo is
#: public; tests/test_no_secret_shaped_values_v14340 scans tracked files).
S = {
    "bearer": "brv" + "A2rev" + "QqZz19",
    "token_arg": "tok" + "A2rev" + "HhJj28",
    "url_key": "urk" + "A2rev" + "KkLl37",
    "userpass": "usp" + "A2rev" + "MmNn46",
    "env_named": "env" + "A2rev" + "PpRr55",
    "placeholder": "plh" + "A2rev" + "SsTt64",
    "auth_value": "aut" + "A2rev" + "VvWw73",
    "innocent": "ghp_" + "A2rev" + "x" * 31,
}


@pytest.fixture(autouse=True)
def _fresh_pending():
    with RT._PENDING_LOCK:
        RT._PENDING.clear()
    RT.tools_tools_for.cache_clear()
    yield
    with RT._PENDING_LOCK:
        RT._PENDING.clear()


def _everything_plus(app, frames, caplog, seen) -> str:
    """The builder's sweep (frames, ledger, journal, log, config.toml) PLUS
    every approval frame the lane showed and every persisted event payload."""
    p = app.state.platform
    parts = [_everything(app, frames, caplog), _text([data for _tool, data in seen])]
    with session_scope(p.engine) as db:
        from sqlmodel import select

        parts += [str(r.payload_json or "") for r in db.exec(select(EventRecord))]
    return "\n".join(parts)


def _http_config(name: str, url: str, headers: dict | None = None, auth: dict | None = None) -> str:
    entry: dict = {"url": url}
    if headers is not None:
        entry["headers"] = headers
    if auth is not None:
        entry["auth"] = auth
    return json.dumps({"mcpServers": {name: entry}})


# =========================================================================== #
# The sentinel sweep: eight shapes of the reviewer's own, through the REAL
# stream lane — refused with no card, and the value reaches NOTHING.
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "which, args, words",
    [
        # A Bearer value under an INNOCENT header name (mask knows only Authorization).
        ("bearer", {"config": _http_config("hb", "https://mcp.example.com/mcp", headers={"X-Trace": "Bearer " + S["bearer"]})}, "never passed in the chat"),
        # --token X in the command line.
        ("token_arg", {"config": "npx -y @acme/server --token " + S["token_arg"], "name": "ta"}, "never passed in the chat"),
        # ?key= in the address (mask knows api_key=, not key=).
        ("url_key", {"config": "https://mcp.example.com/sse?key=" + S["url_key"], "name": "uk"}, "never passed in the chat"),
        # user:pass@host.
        ("userpass", {"config": "https://me:" + S["userpass"] + "@mcp.example.com/mcp", "name": "up"}, "never passed in the chat"),
        # An env value under a secret-named key (DB_CONNECTION).
        ("env_named", {"config": _stdio_config("en", env={"DB_CONNECTION": S["env_named"]})}, "never passed in the chat"),
        # The JSON auth.value of an http entry.
        ("auth_value", {"config": _http_config("av", "https://mcp.example.com/mcp", auth={"value": S["auth_value"]})}, "never passed in the chat"),
        # A token-shaped value under an INNOCENT env key.
        ("innocent", {"config": _stdio_config("ie", env={"FOLDER": S["innocent"]})}, "never passed in the chat"),
        # A Bearer-shaped value in `values` under an innocent key.
        ("bearer", {"config": _stdio_config("vf", extra_args=["${input:folder}"]), "values": {"folder": "Bearer " + S["bearer"]}}, "looks like a key or token"),
        # A token-shaped value in `values` under an innocent key.
        ("innocent", {"config": _stdio_config("vg", extra_args=["${input:folder}"]), "values": {"folder": S["innocent"]}}, "looks like a key or token"),
    ],
)
async def test_a_credential_of_any_shape_is_refused_before_any_card_and_reaches_nothing(tmp_path, caplog, which, args, words):
    caplog.set_level(logging.DEBUG)
    app = _app(tmp_path, [("mcp_server_add", args)])
    p = app.state.platform
    seen: list = []
    frames = await _drive_no_card(app, _body("add this MCP server please"))
    out = _text(_finished(frames, "mcp_server_add"))
    assert words in out, out
    assert list(p.config.mcp_servers or []) == [], "nothing was added"
    assert S[which] not in _everything_plus(app, frames, caplog, seen), which
    # The ledger row's args (redact_args) never carry it either way.
    tool = p.registry.get("mcp_server_add")
    assert S[which] not in _text(tool.redact_args(args)), which


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, words",
    [
        ({"name": "Pricing", "address": "https://api.example.com/v1?key=" + S["url_key"]}, "without anything after a ?"),
        ({"name": "Pricing", "address": "https://api.example.com/v1", "api_token": S["token_arg"]}, "never passed in the chat"),
        ({"name": "Pricing " + "Bearer " + S["bearer"], "address": "https://api.example.com/v1"}, "looks like a key or token"),
    ],
)
async def test_a_web_service_credential_reaches_nothing_either(tmp_path, caplog, args, words):
    caplog.set_level(logging.DEBUG)
    app = _app(tmp_path, [("web_service_add", args)])
    p = app.state.platform
    seen: list = []
    if words:
        frames = await _drive_no_card(app, _body("hook up our pricing API please"))
        assert words in _text(_finished(frames, "web_service_add"))
        assert p.config.custom_integrations == []
    else:
        frames = await _drive_stream(app, _body("hook up our pricing API please"), await _approve_all(app, seen))
    for value in (S["url_key"], S["token_arg"], S["bearer"]):
        assert value not in _everything_plus(app, frames, caplog, seen), value
        assert value not in _text(p.registry.get("web_service_add").redact_args(args)), value


def test_everyday_words_are_never_read_as_a_credential(tmp_path):
    """The Bearer/Basic/Token shape needs a token-like part: a folder called
    "Basic accounting" and a description "basic bookkeeping API" pass."""
    for words in ("Basic accounting", "Token Ring Project", "bearer of the ring", "C:\\Clients\\Basic Returns"):
        assert RT._looks_like_a_secret(words) is False, words
        assert RT._mask_text(words) == words, words
    assert RT._looks_like_a_secret("Bearer " + S["bearer"]) is True
    assert RT._looks_like_a_secret("Basic dXNlcjpwYXNz" + "d29yZA==") is True
    app = create_app(str(tmp_path / "ij"))
    plan = RT.Plan(app.state.platform, {"config": _stdio_config("ba", extra_args=["${input:folder}"]), "values": {"folder": "Basic accounting"}})
    assert plan.problem == "", plan.problem
    tool = RT.WebServiceAddTool(app.state.platform)
    iid, _shown, _address, problem = tool._check({"name": "Fees", "address": "https://api.example.com/v1", "description": "basic bookkeeping API"})
    assert iid == "fees" and problem == "", problem


def test_masked_config_hides_the_shapes_mask_does_not_know():
    assert S["url_key"] not in RT.masked_config("https://mcp.example.com/sse?key=" + S["url_key"] + "&v=1")
    assert "v=1" in RT.masked_config("https://mcp.example.com/sse?key=" + S["url_key"] + "&v=1")
    assert S["bearer"] not in RT.masked_config(_http_config("hb", "https://mcp.example.com/mcp", headers={"X-Trace": "Bearer " + S["bearer"]}))
    assert S["innocent"] not in RT.masked_config(_stdio_config("ie", env={"FOLDER": S["innocent"]}))
    # Unparseable text: the same shapes are masked in the raw words.
    assert S["url_key"] not in RT.masked_config("see https://x.example.com/a?token=" + S["url_key"] + " there")
    assert S["bearer"] not in RT.masked_config("header is Bearer " + S["bearer"])


# =========================================================================== #
# Placeholders: a ${…} config works through the REAL door (the fills fix).
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_placeholder_env_token_is_asked_on_the_card_and_the_server_starts_with_it(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = _stdio_config("vsc", env={"GITHUB_TOKEN": "${input:token}", "MODE": "fast"})
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add"]
    secure = [c for c in _done(frames)["config_cards"] if c.get("kind") == "secret"]
    assert [c["name"] for c in secure] == ["tool_server.vsc__env__GITHUB_TOKEN"]
    status, raw = await _asgi_post(app, "/config/secret", {"name": secure[0]["name"], "value": S["placeholder"]})
    assert status == 200, raw
    answer = json.loads(raw)
    assert answer["tools_loaded"] == 1 and answer["app"] == "vsc", answer
    row = R.snap_app(p, "vsc")
    # The env value the card saved REACHED the server (the old door dropped it).
    assert row["env_secrets"] == {"GITHUB_TOKEN": "mcp.vsc.GITHUB_TOKEN"} and row["env"] == {"MODE": "fast"}
    assert p.secrets.get("mcp.vsc.GITHUB_TOKEN") == S["placeholder"]
    assert S["placeholder"] not in _everything_plus(app, frames, caplog, seen)
    # The Wave-6 rule: a chat-added pack never opens run-without-asking.
    assert pack_grants_auto(row) is False and pack_enabled(row) is True


@pytest.mark.asyncio
async def test_a_placeholder_after_a_secret_flag_is_a_card_slot_vaulted_where_the_door_launches_it(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = _stdio_config("argtok", extra_args=["--token", "${input:token}"])
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add"], "a placeholder is asked, never refused as a value"
    assert "the key its command line takes" in _text(seen[0][1])
    secure = [c for c in _done(frames)["config_cards"] if c.get("kind") == "secret"]
    assert [c["name"] for c in secure] == ["tool_server.argtok__arg__2"]
    status, raw = await _asgi_post(app, "/config/secret", {"name": secure[0]["name"], "value": S["token_arg"]})
    assert status == 200 and json.loads(raw)["tools_loaded"] == 1, raw
    row = R.snap_app(p, "argtok")
    assert row["arg_secrets"] == {"2": "mcp.argtok.arg.2"}, row
    assert row["args"][2] == "***" and p.secrets.get("mcp.argtok.arg.2") == S["token_arg"]
    assert S["token_arg"] not in _everything_plus(app, frames, caplog, seen)
    # The predicted row IS the stored row, so the Undo's check is honest …
    card = _card(frames, "Tool server waiting for its key")
    look = json.loads(await _lookup(app, card))
    assert look["after"] == RT.comparable(row)
    # … and the Undo removes the server and its key.
    status, text = _undo(app, card)
    assert status == 200 and "removed the tool server 'argtok' and its 1 saved key" in text, text
    assert R.snap_app(p, "argtok") is None and p.secrets.get("mcp.argtok.arg.2") is None


async def _lookup(app, card) -> str:
    from sqlmodel import select

    from iron_jarvis.core.models import UndoJournal

    p = app.state.platform
    with session_scope(p.engine) as db:
        for r in db.exec(select(UndoJournal)):
            meta = json.loads(r.pre_inline or "{}")
            if meta.get("change_id") == card["change_id"]:
                return r.pre_inline
    raise AssertionError("no journal row for the card")


@pytest.mark.asyncio
async def test_a_non_secret_placeholder_filled_from_values_is_added_through_the_real_door(tmp_path):
    """The catalog's ``filesystem`` shape: ``${input:folder}`` as an argument,
    filled from ``values``. The old door refused it: "type a value for folder
    first" (``_body_from_spec`` re-ran ``fill_placeholders``)."""
    cfg = _stdio_config("fsx", extra_args=["${input:folder}"])
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg, "values": {"folder": r"C:\Clients\Acme"}})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add"]
    assert json.dumps(r"C:\Clients\Acme")[1:-1] in _text(seen[0][1]), "the card shows the filled command line"
    out = _text(_finished(frames, "mcp_server_add"))
    assert "Added the tool server 'fsx'" in out, out
    row = R.snap_app(p, "fsx")
    assert row is not None and row["args"][-1] == r"C:\Clients\Acme", row


def test_a_placeholder_after_a_secret_flag_is_a_slot_whatever_its_own_name(tmp_path):
    """``--api-key ${input:value}``: the placeholder's NAME says nothing, the
    FLAG before it does — a card slot, never a `values` blank."""
    app = create_app(str(tmp_path / "ij"))
    plan = RT.Plan(app.state.platform, {"config": _stdio_config("af", extra_args=["--api-key", "${input:value}"])})
    assert plan.problem == "", plan.problem
    assert plan.slots == ["arg__2"] and plan.vaulted_args == [2] and plan.fill_slots == {"value": "arg__2"}


def test_filled_spec_leaves_no_fills_for_the_door_to_redo(tmp_path):
    app = create_app(str(tmp_path / "ij"))
    plan = RT.Plan(app.state.platform, {"config": _stdio_config("f", env={"TOKEN": "${input:t}"}, extra_args=["${input:folder}"]), "values": {"folder": "X"}})
    assert plan.problem == "", plan.problem
    assert plan.filled_spec({"env__TOKEN": "v"}).asks == []
    assert plan.filled_spec().asks == []


# =========================================================================== #
# The card dance: a restart, an existing pack, a name already waiting, two at once.
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_card_saved_after_a_restart_keeps_no_orphan_row_and_says_so(tmp_path):
    cfg = _stdio_config("rst", env={"API_KEY": ""})
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    secure = [c for c in _done(frames)["config_cards"] if c.get("kind") == "secret"]
    with RT._PENDING_LOCK:
        RT._PENDING.clear()  # the restart forgot the plan
    status, raw = await _asgi_post(app, "/config/secret", {"name": secure[0]["name"], "value": "late-" + "A2rev"})
    assert status == 200, raw
    answer = json.loads(raw)
    assert "no longer waiting" in answer["load_reason"] and "was not kept" in answer["load_reason"], answer
    assert p.secrets.get("mcp.rst.API_KEY") is None, "no orphan vault row"
    assert R.snap_app(p, "rst") is None


@pytest.mark.asyncio
async def test_an_existing_packs_key_through_the_bare_name_gets_the_plain_words(tmp_path):
    cfg = _stdio_config("live")
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg})])
    p = app.state.platform
    seen: list = []
    await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    assert R.snap_app(p, "live") is not None
    status, raw = await _asgi_post(app, "/config/secret", {"name": "tool_server.live", "value": "tok-" + "A2rev"})
    assert status == 200, raw
    answer = json.loads(raw)
    assert "load_reason" not in answer and "app" not in answer, answer  # the plain "saved" words
    assert p.secrets.get("mcp.live.TOKEN") == "tok-" + "A2rev"


@pytest.mark.asyncio
async def test_a_second_add_under_a_waiting_name_is_refused_and_the_first_card_stands(tmp_path):
    cfg1 = _stdio_config("dup", env={"API_KEY": ""})
    cfg2 = _stdio_config("dup", env={"API_KEY": ""}, extra_args=["--other"])
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg1}), ("mcp_server_add", {"config": cfg2})])
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg1), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add"], "the second call cards nothing"
    outs = _finished(frames, "mcp_server_add")
    assert len(outs) == 2 and "already waiting for its key" in _text(outs[1]), _text(outs[1])
    pending = RT.pending_for("dup")
    assert pending is not None and "--other" not in pending["plan"].spec.args, "the first plan stands"


@pytest.mark.asyncio
async def test_two_servers_waiting_at_once_are_added_one_by_one(tmp_path):
    cfg_a = _stdio_config("aa", env={"API_KEY": ""})
    cfg_b = json.dumps({"mcpServers": {"bb": {"command": sys.executable, "args": [FIXTURE, "b"], "env": {"API_KEY": ""}}}})
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg_a}), ("mcp_server_add", {"config": cfg_b})])
    p = app.state.platform
    seen: list = []
    await _drive_stream(app, _body("add these two MCP servers: " + cfg_a + " " + cfg_b), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add", "mcp_server_add"]
    assert RT.pending_for("aa") is not None and RT.pending_for("bb") is not None
    status, raw = await _asgi_post(app, "/config/secret", {"name": "tool_server.bb__env__API_KEY", "value": "b-" + "A2rev"})
    assert status == 200 and json.loads(raw)["tools_loaded"] == 1, raw
    assert R.snap_app(p, "bb") is not None and R.snap_app(p, "aa") is None
    assert RT.pending_for("aa") is not None, "the other card still waits"
    assert p.secrets.get("mcp.aa.API_KEY") is None


# =========================================================================== #
# Linear on 128k of pathological text — in a child, with a deadline.
# =========================================================================== #
_PATHOLOGICAL = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from iron_jarvis.settings import records_tools as RT
n = 128_000
bad = [
    "{" * n,
    "{\"command\": \"" * (n // 12),
    "https://" + "a" * n + "/mcp add it",
    "add " + "the " * (n // 4) + "mcp server",
    "hook up " + "our " * (n // 4) + "api at https://x.example.com",
    "X-Trace: Bearer " + "b" * n,
    "https://x.example.com/sse?key=" + "k" * n + "&" * 2000,
    "npx -y " + "a" * n + " --token " + "t" * n,
]
t0 = time.perf_counter()
for text in bad:
    RT.tools_tools_for.cache_clear()
    RT.tools_tools_for(text)
    RT.parse_server_config(text)
    RT.masked_config(text)
    RT._looks_like_a_secret(text)
print(time.perf_counter() - t0)
"""


def test_every_matcher_and_masker_is_linear_on_128k_of_pathological_text():
    out = subprocess.run(
        [sys.executable, "-c", _PATHOLOGICAL, str(ROOT / "src")], capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 10.0


# =========================================================================== #
# Arming: the reviewer's own rows, both ways
# =========================================================================== #
REVIEW_REQUESTS = [
    ("please install the Playwright MCP server so you can browse", "mcp_server_add"),
    ("add an MCP server: uvx mcp-server-git", "mcp_server_add"),
    ("connect this tool server https://tools.example.com/mcp for me", "mcp_server_add"),
    ("set up the sequential thinking mcp server", "mcp_server_add"),
    ("hook up our firm's billing REST API at https://billing.example.com/v2", "web_service_add"),
    ("connect Jarvis to our internal CRM API https://crm.example.com so it can read client records", "web_service_add"),
    ("save a web service called Payroll at https://payroll.example.com/api", "web_service_add"),
    ("let Jarvis query our rest api at https://api.example.com/v3", "web_service_add"),
]
REVIEW_NEAR_MISSES = [
    "the server at the restaurant was rude",
    "a web service for the car wash",
    "the pricing API changed yesterday",
    "my office PC is slow",
    "two Jarvises in the film",
    "the MCP server keeps crashing",
    "install the printer server in the office",
    "connect the api to the database in my project",
    "save the web page as PDF",
    "add the server's IP to the firewall",
    "set up a REST day in my training schedule",
    "link my fitness api key",
    "what does the fetch MCP server do?",
]


@pytest.mark.parametrize("text, tool", REVIEW_REQUESTS)
def test_a_review_request_offers_its_tool_in_the_stream_lane_only(d, text, tool):  # noqa: F811
    assert tool in R.record_tools_for(text)
    armed, ask = lanes(d, text)
    assert tool in ask and tool not in armed, (text, armed, ask)


@pytest.mark.parametrize("text", REVIEW_NEAR_MISSES)
def test_a_review_near_miss_arms_neither(d, text):  # noqa: F811
    assert not set(WRITERS) & set(R.record_tools_for(text)), text
    armed, ask = lanes(d, text)
    assert not set(WRITERS) & (set(armed) | set(ask)), (text, armed, ask)
