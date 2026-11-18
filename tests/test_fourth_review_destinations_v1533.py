"""Fourth fix plan, group 25 (PG1, decision D3 option B, C-PG-3): every place
that sets WHERE Iron Jarvis's data goes asks every time it is pointed
somewhere new.

THE DEFECT. "Ask every time when this changes where data goes" lived inside
each tool's own check, written one tool at a time. ``webhook_update`` had none,
and neither did ``config_change`` for the address settings
(``ollama_base_url``, ``custom_base_url``, ``voice_transcribe_base_url``) or
the backup copy folder (``backup_mirror_dir``, which copies the whole Iron
Jarvis folder, database included). So "Allow for this conversation" on a pause,
or a tool set to "Run without asking", let a chat or a scheduled workflow send
Iron Jarvis's events, chats or whole folder to any place, with no question.

THE RULE. One helper, ``webhooks.tools.outbound_address_check``, is the check
every such tool reads (Allow once only, "Don't ask" kept as the user's own
posture, refused with nobody to ask, reason "address", the host only). A new
address on THIS PC (loopback) does not ask: nothing leaves the PC. The
settings tools ask only when the call would really make the change
(``config_set`` with an ask-tier key is refused anyway, so it shows no card).
``tools/destinations.py`` is the table of every tool and setting that sets a
place; the scan below keeps it whole.

Proved through the layers that decide: the real stream lane (``/chat/stream``)
and its approval registry, the real workflow engine and registry, the real
tools. Only the model and the outbound store's network (its HTTP post; its DNS
check is skipped with ``allow_internal`` so a red run really stores the row)
are stand-ins.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from iron_jarvis.tools import destinations
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.webhooks.models import WebhookRecord
from iron_jarvis.workflows.engine import OWN_CHECK_LABEL, Step, WorkflowDef, WorkflowEngine
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

OLD = "https://old.example.test/hooks/one"
NEW_HOST = "hooks.example.test"
NEW = f"https://{NEW_HOST}/private/path-part"
MODEL_SERVER = "https://gpu.example.test/v1"


def _app(tmp_path, specs):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream(specs)
    p.outbound_webhooks.allow_internal = True  # no DNS: a red run really stores the row
    p.outbound_webhooks.http_post = lambda *a, **k: None
    return app


def _seed_webhook(p, slug="deploys"):
    p.outbound_webhooks.register(slug, OLD, ["session.completed"])


def _target(p, slug="deploys") -> str:
    with session_scope(p.engine) as db:
        rec = db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first()
        return rec.target_url if rec is not None else ""


def _granted(text: str, tool: str) -> dict:
    """A chat turn whose page sends "Allow for this conversation" for *tool*."""
    return _body(text, tools=[tool], granted_tools=[tool])


async def _answer(app, seen: list[dict], decision: str):
    async def decide(data):
        seen.append(dict(data))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
        assert status == 200

    return decide


def _finished(frames, tool):
    return [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool
    ]


def _ctx(tmp_path, platform=None) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="scan", agent_run_id="scan",
        config=getattr(platform, "config", None), event_bus=getattr(platform, "event_bus", None),
        engine=getattr(platform, "engine", None),
    )


def _run_without_asking(p, *tools: str) -> None:
    """The user's own "Run without asking", through the settings writer."""
    p.config_writer.apply({f"permissions.{t}": "allow" for t in tools}, actor="test", journal=False)


# ------------------------------------------------------------------ (a)


@pytest.mark.asyncio
async def test_a_a_conversation_grant_still_cards_a_new_webhook_address_and_deny_keeps_it(tmp_path):
    app = _app(tmp_path, [("webhook_update", {"slug": "deploys", "target_url": NEW})])
    p = app.state.platform
    _seed_webhook(p)
    seen: list[dict] = []
    await _drive_stream(app, _granted("send the deploys webhook somewhere else", "webhook_update"),
                        await _answer(app, seen, "deny"))
    assert [s.get("tool") for s in seen] == ["webhook_update"], (
        "a conversation grant let a webhook be pointed at a new address with no card"
    )
    card = seen[0]
    assert card.get("once_only") is True, card
    assert card.get("once_reason") == "address", card
    why = str(card.get("why") or "")
    assert NEW_HOST in why and "old.example.test" in why, why
    shown = json.dumps(card)
    assert "/private/path-part" not in shown and "/hooks/one" not in shown, shown
    assert _target(p) == OLD, "Deny changed the address"


@pytest.mark.asyncio
async def test_a_allow_once_changes_it_and_the_change_card_names_hosts_only(tmp_path):
    app = _app(tmp_path, [("webhook_update", {"slug": "deploys", "target_url": NEW})])
    p = app.state.platform
    _seed_webhook(p)
    seen: list[dict] = []
    frames = await _drive_stream(app, _granted("send the deploys webhook somewhere else", "webhook_update"),
                                 await _answer(app, seen, "once"))
    assert len(seen) == 1
    assert _target(p) == NEW
    done = next(d for ev, d in frames if ev == "done")
    cards = [c for c in done.get("config_cards") or [] if c.get("title") == "Webhook changed"]
    assert len(cards) == 1, done.get("config_cards")
    assert cards[0]["old"] == "sends session.completed to old.example.test", cards[0]
    assert cards[0]["new"] == f"sends session.completed to {NEW_HOST}", cards[0]
    assert "/private/path-part" not in json.dumps(frames)


# ------------------------------------------------------------------ (b)


@pytest.mark.asyncio
async def test_b_a_workflow_tool_step_set_to_allow_is_refused_in_words_with_a_ledger_row(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    _seed_webhook(p)
    _run_without_asking(p, "webhook_update")
    wf = WorkflowDef(
        name="move the webhook",
        steps=[Step(name="Move it", kind="tool", tool="webhook_update",
                    args={"slug": "deploys", "target_url": NEW})],
    )
    rec = await WorkflowEngine(p).run(wf)
    assert _target(p) == OLD, "a workflow re-pointed a webhook with nobody asked"
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Move it"]["summary"]
    assert NEW_HOST in summary and "/private/path-part" not in summary, summary
    assert "every time" in summary and "from chat" in summary, summary
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(
            ToolInvocation.session_id == rec.id, ToolInvocation.tool == "webhook_update")))
        assert len(rows) == 1 and rows[0].ok is False, rows
        assert "every time" in rows[0].output
        assert "/private/path-part" not in rows[0].args_json, rows[0].args_json
    assert OWN_CHECK_LABEL == "not run"


# ------------------------------------------------------------------ (c)


@pytest.mark.asyncio
async def test_c_config_change_of_a_model_server_address_cards_under_a_grant(tmp_path):
    app = _app(tmp_path, [("config_change", {"key": "custom_base_url", "value": MODEL_SERVER})])
    p = app.state.platform
    seen: list[dict] = []
    await _drive_stream(app, _granted("use my model server at gpu", "config_change"),
                        await _answer(app, seen, "deny"))
    assert [s.get("tool") for s in seen] == ["config_change"], "a new model server address ran with no card"
    card = seen[0]
    assert card.get("once_only") is True and card.get("once_reason") == "address", card
    why = str(card.get("why") or "")
    assert "gpu.example.test" in why and "/v1" not in why, why
    assert "your model server" in why, why
    assert str(getattr(p.config, "custom_base_url", "") or "") != MODEL_SERVER


@pytest.mark.asyncio
async def test_c_config_change_of_the_backup_copy_folder_cards_under_a_grant(tmp_path):
    folder = str(tmp_path / "copies")
    app = _app(tmp_path, [("config_change", {"key": "backup_mirror_dir", "value": folder})])
    p = app.state.platform
    seen: list[dict] = []
    await _drive_stream(app, _granted("copy my backups somewhere else", "config_change"),
                        await _answer(app, seen, "deny"))
    assert [s.get("tool") for s in seen] == ["config_change"], "a new backup copy folder ran with no card"
    card = seen[0]
    assert card.get("once_only") is True and card.get("once_reason") == "address", card
    why = str(card.get("why") or "")
    assert why.startswith("Copy Iron Jarvis's whole folder, database included, to "), why
    assert str(getattr(p.config, "backup_mirror_dir", "") or "") != folder


@pytest.mark.asyncio
async def test_c_config_set_with_an_ask_tier_address_shows_no_card(tmp_path):
    """The tool that would NOT make the change never asks (its Allow would
    then be refused: "an Allow that cannot run is a lie")."""
    app = _app(tmp_path, [("config_set", {"key": "custom_base_url", "value": MODEL_SERVER})])
    p = app.state.platform
    seen: list[dict] = []
    frames = await _drive_stream(app, _granted("use my model server at gpu", "config_set"),
                                 await _answer(app, seen, "once"))
    assert seen == [], seen
    fin = _finished(frames, "config_set")
    assert fin and "config_change" in json.dumps(fin[-1]), fin
    assert str(getattr(p.config, "custom_base_url", "") or "") != MODEL_SERVER


@pytest.mark.asyncio
async def test_c_an_address_on_this_pc_does_not_ask(tmp_path):
    local = "http://127.0.0.1:1234/v1"
    app = _app(tmp_path, [("config_change", {"key": "custom_base_url", "value": local})])
    p = app.state.platform
    seen: list[dict] = []
    await _drive_stream(app, _granted("use my LM Studio on this PC", "config_change"),
                        await _answer(app, seen, "deny"))
    assert seen == [], "an address on this PC asked although nothing leaves the PC"
    assert str(getattr(p.config, "custom_base_url", "") or "") == local


# ------------------------------------------------------------------ (d)


@pytest.mark.asyncio
async def test_d_pausing_a_webhook_under_the_grant_still_runs_with_no_card(tmp_path):
    app = _app(tmp_path, [("webhook_update", {"slug": "deploys", "enabled": False})])
    p = app.state.platform
    _seed_webhook(p)
    seen: list[dict] = []
    await _drive_stream(app, _granted("pause the deploys webhook", "webhook_update"),
                        await _answer(app, seen, "deny"))
    assert seen == [], seen
    with session_scope(p.engine) as db:
        rec = db.exec(select(WebhookRecord).where(WebhookRecord.slug == "deploys")).first()
        assert rec.enabled is False and rec.target_url == OLD


# ------------------------------------------------------------------ (e)


#: The call each "asks" row is proved with (a public test address, never
#: shaped like a credential). A row proved by its own older test names it.
_SAMPLES = {
    "webhook_add": {"slug": "x", "direction": "outbound", "target_url": NEW},
    "webhook_update": {"slug": "deploys", "target_url": NEW},
    "config_change": {"key": "custom_base_url", "value": MODEL_SERVER},
    "model_server_add": {"address": "https://gpu.example.test:8000"},
}
_PROVED_ELSEWHERE = {
    # Each needs a world of its own (a service's own check of its address, a
    # server already added, another app's servers, a notes folder); its own
    # test drives the card through the stream lane.
    "web_service_add": ("test_records_tools_v14480.py",
                        "test_adding_a_web_service_asks_says_where_it_points_and_undo_removes_it"),
    "mcp_server_add": ("test_records_tools_v14480.py",
                       "test_adding_a_server_asks_once_only_shows_the_command_adds_it_and_undo_removes_it"),
    "memory_base_add": ("test_records_connections_v14380.py",
                        "test_a_notes_folder_becomes_a_live_memory_base_and_undo_removes_it"),
    "app_import": ("test_apps_destinations_v14350.py",
                   "test_app_import_asks_adds_with_the_key_from_disk_and_undo_removes_it"),
    "model_server_default": ("test_records_connections_v14380.py",
                             "test_default_undo_puts_the_old_default_back"),
}


def test_e_every_tool_with_an_address_argument_is_in_the_table(tmp_path):
    app = create_app(str(tmp_path))
    reg = app.state.platform.registry
    missing = []
    for name in reg.names():
        props = (getattr(reg.get(name), "input_schema", None) or {}).get("properties") or {}
        for arg in sorted(props):
            if destinations.address_shaped(arg) and name not in destinations.DESTINATION_TOOLS:
                missing.append(
                    f"`{name}` takes `{arg}`, which can name a place outside this PC: give it an own "
                    "check that asks every time it sets a new place (webhooks.tools."
                    "outbound_address_check), or add it to tools/destinations.py with the reason."
                )
    assert not missing, "\n".join(missing)
    for row in destinations.DESTINATION_TOOLS.values():
        assert row.kind in destinations.KINDS and row.why.strip(), row


@pytest.mark.asyncio
async def test_e_each_asks_row_really_asks(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    _seed_webhook(p)
    reg = p.registry
    asks = [r.tool for r in destinations.DESTINATION_TOOLS.values() if r.kind == destinations.ASKS]
    assert set(_SAMPLES) | set(_PROVED_ELSEWHERE) >= set(asks), set(asks) - set(_SAMPLES) - set(_PROVED_ELSEWHERE)
    from pathlib import Path

    here = Path(__file__).parent
    for name in asks:
        tool = reg.get(name)
        assert tool is not None, f"{name} is in the table but not registered"
        if name not in _SAMPLES:
            file, test = _PROVED_ELSEWHERE[name]
            assert f"def {test}(" in (here / file).read_text(encoding="utf-8"), (name, file, test)
            continue
        check = await tool.spend_check(dict(_SAMPLES[name]), _ctx(tmp_path, p))
        assert check is not None and check.ask is True, (name, check)
        assert destinations.still_asks(name)


@pytest.mark.asyncio
async def test_e_every_address_setting_has_a_destination_and_asks_for_a_new_value(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    tool = p.registry.get("config_change")
    keyed = [d for d in schema.SETTINGS if not d.pattern and (
        d.key.endswith("_url") or d.key.endswith("_address") or d.key in destinations.FOLDER_SETTINGS
    )]
    assert {d.key for d in keyed} >= {"ollama_base_url", "custom_base_url", "voice_transcribe_base_url",
                                      "backup_mirror_dir"}
    for d in keyed:
        assert d.destination.strip(), f"{d.key} sets where data goes but has no `destination`"
        value = str(tmp_path / "elsewhere") if d.key in destinations.FOLDER_SETTINGS else MODEL_SERVER
        check = await tool.spend_check({"key": d.key, "value": value}, _ctx(tmp_path, p))
        assert check is not None and check.ask and check.once_only and check.reason == "address", (d.key, check)
        # Clearing it narrows: no question.
        assert await tool.spend_check({"key": d.key, "value": ""}, _ctx(tmp_path, p)) is None
    assert set(destinations.destination_settings()) == {d.key for d in schema.SETTINGS if d.destination}


def test_e_the_one_check_says_the_host_only_and_never_asks_for_this_pc():
    from iron_jarvis.webhooks.tools import outbound_address_check as check

    c = check(NEW, old_url=OLD, note="webhook “deploys”")
    assert c.words == (
        f"Send Iron Jarvis's events to {NEW_HOST} from now on, instead of old.example.test (webhook “deploys”)"
    ), c.words
    assert (c.ask, c.once_only, c.reason, c.dont_ask_skips, c.attended_only) == (True, True, "address", True, True)
    same = check("https://old.example.test/hooks/two", old_url=OLD)
    assert same.words == "Send Iron Jarvis's events to a different address on old.example.test from now on"
    for here in ("http://127.0.0.1:11434", "http://localhost:1234/v1", "http://[::1]:8000", "127.0.0.1:1234", "::1"):
        assert check(here) is None, here
    assert check("http://192.168.1.20:1234/v1").ask is True, "a computer on the network is not this PC"
    share = check("\\\\nas\\share\\copies", what="Iron Jarvis's whole folder, database included")
    assert share.words == (
        "Copy Iron Jarvis's whole folder, database included, to a folder in \\\\nas\\share from now on"
    ), share.words
    drive = check("D:\\Backups", what="Iron Jarvis's whole folder, database included")
    assert drive.words == "Copy Iron Jarvis's whole folder, database included, to D:\\Backups from now on"


# ------------------------------------------------------------------ C-PG-3


def test_c_pg_3_a_tool_set_to_run_without_asking_says_its_one_exception(tmp_path):
    from iron_jarvis.capability_search import index as _index
    from iron_jarvis.capability_search.tool import entry_line

    app = create_app(str(tmp_path))
    p = app.state.platform
    _run_without_asking(p, "webhook_update", "notify")
    line, row = entry_line(p, _index.Capability(kind="tool", id="webhook_update", title="Change a webhook",
                                                 tool="webhook_update", changes=True))
    assert row["mode"] == "allow"
    assert line.endswith(destinations.STILL_ASKS_LINE), line
    assert destinations.STILL_ASKS_LINE == "Still asks every time it would send to a new address."
    line, _ = entry_line(p, _index.Capability(kind="tool", id="notify", title="Send a notification",
                                              tool="notify", changes=True))
    assert destinations.STILL_ASKS_LINE not in line, line


@pytest.mark.asyncio
async def test_c_pg_3_config_list_names_the_exception_on_the_per_tool_row(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    tool = p.registry.get("config_list")
    out = await tool.execute({"query": "approval for one tool"}, _ctx(tmp_path, p))
    line = next(x for x in out.output.splitlines() if "permissions.{tool}" in x)
    assert destinations.STILL_ASKS_LINE.rstrip(".") in line or "still ask every time" in line, line
    assert "webhook_update" in line, line



def test_adding_a_webhook_keeps_only_its_host_in_the_record():
    """Lead's review of PG1 (D6): webhook_add's card, ledger row and chat
    history carry the address's host only, as webhook_update's do."""
    from iron_jarvis.webhooks.tools import WebhookAddTool

    tool = WebhookAddTool.__new__(WebhookAddTool)
    got = tool.redact_args({"slug": "deploys", "direction": "outbound",
                            "target_url": "https://hooks.example.test/private/path-part?key=abc"})
    assert "private" not in str(got) and "path-part" not in str(got) and "key=abc" not in str(got)
    assert "hooks.example.test" in got["target_url"]
    assert got["slug"] == "deploys"
