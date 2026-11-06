"""v1.442.0 (chat-first plan, Wave 7 group B): THE 30-REQUEST WALKTHROUGH,
automated.

The chat-first review (``docs/CHAT-FIRST-PLAN-2026-11-04.md``) traced 30
everyday setup requests through the code at v1.432.0: 2 could be done in chat,
10 partly, 18 not at all. Waves 1-6 added the tools, the doors and the plain
"not offered" lines. This file drives EVERY one of the 30 through the REAL chat
lane and pins its verdict (Part B, "What done means"):

* **done** — every Iron Jarvis-side step finishes in chat. The stream lane
  (``POST /chat/stream``, what the chat page uses) OFFERS the tool(s) the
  step needs; a SCRIPTED model that may only call a tool it was offered in
  that turn makes the ideal call(s); every approval card is answered "Allow
  once" through ``POST /chat/approvals/{id}``; a secure card's value is posted
  to ``POST /config/secret`` the way the dashboard does; and the change LANDS
  in the scratch home (a setting saved, a project row, a night task, a
  schedule, a preference, an agent, a destination…). The cards shown are
  exactly the planned calls in ``CARDED`` (written out, never read from the
  permission table), and a credential the row hands over is in the encrypted
  vault only: no byte of the scratch home, nothing shown to the model, no
  frame and no ledger line holds it in the clear. A vendor's own step that
  truly needs its page (signing in through the vendor's login, a Google or
  Slack developer app) may remain as a door in the same reply, and every door's
  href is a real dashboard place today.
* **door** — an Iron Jarvis step truly needs a page: the reply's doors hold
  that exact page and it exists.
* **honest** — the app does not offer it: the turn's system prompt carries the
  ``daemon/page_doors`` "not offered" line and no writer that would pretend is
  armed.

Each row is checked twice: through the arming functions both lanes call
(``chat_turn._resolve_armed_tools`` + ``select_ask_tools`` +
``settings_ask_tools``, as ``tests/test_chat_arming_table_v14340.py`` does) and
END TO END through the stream lane on a scratch home (``create_app(tmp)``).
Nothing here leaves this machine (the ``no_network`` guard), Iron-Proxy is an
in-memory stand-in, Notion is never called, an MCP app is a local child, and
another program's config is a fixture under a scratch APPDATA, and a row
that starts any program but git or this Python (a real CLI) fails.

SCORE (target after Wave 7): 23 done, 1 door, 6 honest.
On this branch (before Wave 7 groups A and C merge): 16 done, 1 door, 2
honest pinned and passing; 11 rows wait (``xfail(strict=True)``): 1, 2, 3, 8,
12, 17, 18, 19, 23, 24, 26 — see each row's ``waits``. A row that starts
passing goes red until its mark is taken off.

Request 10's pull request is made in a LATER turn with the GitHub app's own
tools; this row pins the connect (the secure card) only.

Stand-ins the rows need, said once: request 6's ``D:\\Clients\\Johnson`` is a
scratch folder (a project refuses a folder that does not exist here); requests
9, 24 and 29 name no folder ("this repo", "my coding sessions"), so the chat
carries a focused project whose folder is a scratch git repository, as the
chat page sends ``project_id``; request 15's ideal setting is Auto (the only
approval level under which a test run needs no card, ``core/auto_approve``).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import page_doors as P
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.autoselect import select_ask_tools
from tests.test_build_git_v1375 import make_repo
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_page_doors_v14360 import _resolves as _page_resolves

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]
DEVICE = "dev_walkthrough01"

#: Every writer a request could arm (the arming table's families, the Wave 3-4
#: record tools). An honest row may arm only the ones it names as real
#: neighbours of what was asked.
WRITERS = frozenset({
    "config_set", "config_change", "config_change_protected", "config_secret",
    "schedule_create", "schedule_update", "schedule_delete", "workflow_schedule",
    "workflow_create", "workflow_update", "workflow_delete", "workflow_run",
    "channel_toggle", "channel_connect", "channel_remove", "channel_alerts",
    "app_connect", "app_switch", "app_disconnect", "app_import",
    "webhook_add", "webhook_update", "webhook_delete",
    "sentinel_add", "sentinel_update", "sentinel_delete",
    "goal_add", "goal_update", "goal_delete",
    "reflex_create", "reflex_update", "reflex_delete",
    "project_create", "project_update", "project_archive", "project_focus",
    "agent_create", "agent_update", "agent_pause", "agent_delete",
    "night_task_add", "night_task_cancel", "night_task_move",
    "build_defaults_set", "build_defaults_clear", "build_team_set", "build_team_clear",
    "model_server_add", "model_server_default", "model_server_remove", "account_add", "memory_base_add",
    "chat_local_only", "change_undo", "remember_preference",
})
SETTINGS = frozenset({"config_set", "config_change", "config_change_protected", "config_secret"})

#: The planned calls that must stop on an approval card in the default
#: posture ("Ask first"); every other planned call must run with NO card.
#: Written out here on purpose, never read from the product's permission table:
#: a tool that silently became allow-tier (or a lane that stopped carding an
#: ask-tier tool, the v1.270.1 shape) must turn a row red, not move with it.
CARDED = frozenset({
    "config_change", "config_change_protected",
    "project_create", "agent_create", "app_connect", "app_import",
    "night_task_add", "build_team_set", "build_defaults_set",
    "account_add", "memory_base_add", "model_server_add", "model_server_default",
    "channel_connect", "reflex_create", "schedule_create", "change_undo",
})


# =========================================================================== #
# The rows
# =========================================================================== #
@dataclass
class Call:
    tool: str
    #: The ideal arguments (a callable reads the row's scratch facts).
    args: "dict | Callable[[SimpleNamespace], dict]"
    #: False for a call the tool must refuse in words (the reply goes on).
    ok: bool = True


@dataclass
class Row:
    n: int
    text: str
    verdict: str  # done | door | honest | door_or_honest
    plan: list[Call] = field(default_factory=list)
    #: done rows: secure cards (vault name → value) posted after the reply.
    secrets: dict[str, str] = field(default_factory=dict)
    #: Doors the reply must carry (exact entries).
    doors: list[dict] = field(default_factory=list)
    #: Credentials besides ``secrets`` that the row hands Iron Jarvis another
    #: way (an imported config's token): never in the clear anywhere either.
    never_seen: tuple = ()
    #: honest rows: the not-offered id ("" = any line the request matches).
    honest_id: str = ""
    #: honest rows: writers that are real neighbours and may be armed.
    may_arm: frozenset = frozenset()
    setup: "Callable[..., dict] | None" = None
    lands: "Callable[..., None] | None" = None
    #: "" = pinned today; else what this row waits for (strict xfail).
    waits: str = ""
    #: False when the arming functions already offer the row's tools today
    #: (only the end-to-end run waits).
    arming_waits: bool = True


def _step(step_id: str) -> dict:
    return next(s for s in P.STEPS if s.id == step_id).door()


# ------------------------------------------------------------------ helpers
def _channels(p) -> dict:
    return dict((p.config.comm or {}).get("channels") or {})


def _setup_thread(p, tid: str = "chat_walk05") -> str:
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(p.engine) as db:
        db.add(ChatThreadRecord(id=tid, messages_json="[]", setup_json="{}"))
        db.commit()
    return tid


def _thread_setup(p, tid: str) -> dict:
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(p.engine) as db:
        return json.loads(db.get(ChatThreadRecord, tid).setup_json or "{}")


def _project_with_repo(app, tmp: Path, name: str = "Shop") -> tuple[str, Path]:
    repo = make_repo(tmp / "shop")
    pid = TestClient(app).post("/projects", json={"name": name, "root": str(repo)}).json()["id"]
    return pid, repo


def _kept_preferences(app) -> list[str]:
    kept = TestClient(app).get("/memory/preferences").json()["kept"]
    return [str(k.get("text") or "") for k in kept]


def _device(p, key: str):
    from iron_jarvis.daemon.routes.settings import config_writer

    return config_writer(SimpleNamespace(platform=p)).current(key, device_id=DEVICE)


def _swap_to_local_child(monkeypatch, cid: str):
    """An MCP app's Directory card launched by a REAL local child (no npx, no
    network); only the launch target changes."""
    from tests.test_connect_in_chat_v13510 import FIXTURE, _swap_connector

    return _swap_connector(monkeypatch, cid, command=sys.executable, args=[FIXTURE])


# ------------------------------------------------------------- row setups
def _s_gmail_phone(app, tmp, monkeypatch) -> dict:
    _swap_to_local_child(monkeypatch, "gmail")
    return {}


def _l_gmail_phone(app, ctx, done):
    from iron_jarvis.settings import records as R

    p = app.state.platform
    rec = p.scheduler.get("morning-mail")
    assert rec is not None and rec.cron == "0 8 * * *"
    assert _channels(p)["phone"]["type"] == "telegram"
    assert p.secrets.get("channel_phone_token") == "telegram-bot-v14420-walkthrough"
    assert R.snap_app(p, "gmail") is not None


def _l_slack(app, ctx, done):
    p = app.state.platform
    assert _channels(p)["slack"]["type"] == "slack"
    assert p.secrets.get("channel_slack_token") == "slack-bot-v14420-walkthrough"


def _s_ollama(app, tmp, monkeypatch) -> dict:
    from tests.test_records_connections_v14380 import _ModelServer

    server = _ModelServer(models=("llama3.2",))
    ctx = {"url": server.url, "_close": server.close}
    return ctx


def _l_ollama(app, ctx, done):
    p = app.state.platform
    assert p.config.default_provider.startswith("fleet-"), p.config.default_provider
    assert p.config.default_model == "llama3.2"
    node = p.fleet.get(p.config.default_provider.removeprefix("fleet-"))
    assert node is not None and node.base_url == ctx.url


def _s_account(app, tmp, monkeypatch) -> dict:
    from tests.test_records_connections_v14380 import _Proxy, _with_proxy

    proxy = _Proxy()
    panes = _with_proxy(monkeypatch, app.state.platform, proxy)
    return {"proxy": proxy, "panes": panes}


def _l_account(app, ctx, done):
    assert ctx.proxy.created == [("anthropic", "Second", "cli")]
    assert ctx.proxy.signins and ctx.panes and ctx.panes[0].written, "its own sign-in terminal ran the login"


def _s_local_only(app, tmp, monkeypatch) -> dict:
    return {"body": {"thread_id": _setup_thread(app.state.platform)}}


def _l_local_only(app, ctx, done):
    assert _thread_setup(app.state.platform, ctx.body["thread_id"]).get("local_only") is True


def _s_johnson(app, tmp, monkeypatch) -> dict:
    # A scratch folder stands in for D:\Clients\Johnson (the project refuses a
    # folder that does not exist on this machine).
    folder = tmp / "Clients" / "Johnson"
    folder.mkdir(parents=True)
    return {"folder": str(folder)}


def _l_johnson(app, ctx, done):
    from tests.test_records_projects_v14350 import _project

    row = _project(app.state.platform, "Johnson audit")
    assert row is not None and row["root"] == ctx.folder
    assert any("workpapers in Excel" in k for k in _kept_preferences(app)), _kept_preferences(app)


def _l_reviewer(app, ctx, done):
    from tests.test_records_projects_v14350 import _agent_row

    row = _agent_row(app.state.platform, "reviewer")
    assert row is not None
    assert (row["provider"], row["base_type"]) == ("openai", "reviewer")


def _s_onedrive(app, tmp, monkeypatch) -> dict:
    folder = tmp / "OneDrive" / "Iron Jarvis backups"
    folder.mkdir(parents=True)
    return {"folder": str(folder)}


def _l_backup_folder(app, ctx, done):
    assert app.state.platform.config.backup_mirror_dir == ctx.folder


def _s_night(app, tmp, monkeypatch) -> dict:
    pid, repo = _project_with_repo(app, tmp)
    return {"body": {"project_id": pid}, "repo": str(repo)}


def _l_night(app, ctx, done):
    from iron_jarvis.harness import night as N

    tasks = N.rows_in(app.state.platform.engine, N.QUEUED)
    assert [(t["words"], t["folder"]) for t in tasks] == [("update the README", ctx.repo)]


def _s_github(app, tmp, monkeypatch) -> dict:
    _swap_to_local_child(monkeypatch, "github")
    return {}


def _l_github(app, ctx, done):
    from iron_jarvis.settings import records as R

    p = app.state.platform
    assert R.snap_app(p, "github") is not None
    assert p.registry.mcp_names("github") == ["mcp__github__argv"], "its tools loaded from the card's save"


THEME_COLORS = {"accent": "#2e8b57", "surface": "cool", "mode": "dark", "name": "Calm green"}


def _l_theme(app, ctx, done):
    assert _device(app.state.platform, "device.theme_colors") == THEME_COLORS


def _s_quickbooks(app, tmp, monkeypatch) -> dict:
    user, appdata = tmp / "user", tmp / "appdata"
    user.mkdir()
    appdata.mkdir()
    for var, val in (("USERPROFILE", user), ("HOME", user), ("APPDATA", appdata)):
        monkeypatch.setenv(var, str(val))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    cfg = appdata / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir(parents=True)
    qb = {"command": "ij-no-such-app-launcher-v14420", "args": ["qb-server"],
          "env": {"QB_TOKEN": "qb-token-v14420-walkthrough", "QB_COMPANY": "C:/Books/acme.qbw"}}
    cfg.write_text(json.dumps({"mcpServers": {"quickbooks": qb}}), encoding="utf-8")
    return {"cfg": cfg, "before": cfg.read_bytes()}


def _l_quickbooks(app, ctx, done):
    from iron_jarvis.settings import records as R

    p = app.state.platform
    row = R.snap_app(p, "quickbooks")
    assert row is not None and row["env_secrets"] == {"QB_TOKEN": "mcp.quickbooks.QB_TOKEN"}
    assert p.secrets.get("mcp.quickbooks.QB_TOKEN") == "qb-token-v14420-walkthrough"
    assert ctx.cfg.read_bytes() == ctx.before, "Claude Desktop's own file is read, never written"


def _l_auto(app, ctx, done):
    assert _device(app.state.platform, "device.approval_mode") == "auto"


def _l_start_awake(app, ctx, done):
    cfg = app.state.platform.config
    assert cfg.start_with_windows is True and cfg.keep_awake is True


def _l_client_replies(app, ctx, done):
    from iron_jarvis.workflows.store import WorkflowStore

    p = app.state.platform
    assert WorkflowStore(p.engine).get("client-reply-drafts") is not None
    rules = [r for r in p.reflex.list() if getattr(r, "target", "") == "client-reply-drafts"]
    assert rules and rules[0].source == "email" and rules[0].action == "workflow"
    assert _channels(p)["mail"]["type"] == "email"
    assert p.secrets.get("channel_mail_token") == "mail-app-password-v14420"


def _l_short(app, ctx, done):
    assert any("short answers" in k for k in _kept_preferences(app)), _kept_preferences(app)


def _l_notion(app, ctx, done):
    from iron_jarvis.ltm.sources import CustomSourceStore

    p = app.state.platform
    rec = CustomSourceStore(p.engine).get("work_notes")
    assert rec is not None and rec.kind == "notion"
    assert p.config.brain_profile, "it is the memory profile Jarvis uses"
    assert p.ltm.get("work_notes").token_resolver() == "notion-token-v14420-walkthrough"


def _s_codex_folder(app, tmp, monkeypatch) -> dict:
    pid, repo = _project_with_repo(app, tmp)
    return {"body": {"project_id": pid}, "repo": str(repo)}


def _l_codex_folder(app, ctx, done):
    from iron_jarvis.harness import folder_defaults as FD
    from iron_jarvis.settings import records_build as RB

    saved = FD.read(app.state.platform.engine, ctx.repo)
    assert saved.get("tool", {}).get("harness") == "codex", saved
    assert saved["tool"].get("mode") != "full", "Full access is never a folder default"
    outs = [d for ev, d in done["_frames"] if ev == "tool_call" and d.get("status") == "finished"
            and d.get("name") == "build_defaults_set"]
    refused = outs[0].get("error") or outs[0].get("output") or ""
    assert "Full access" in refused, refused
    assert RB.ACCOUNT_NOT_KEPT.strip() in (outs[1].get("output") or ""), "the account is said not to be kept"


def _s_share(app, tmp, monkeypatch) -> dict:
    from iron_jarvis.profile import share

    home = tmp / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    # The user's own home too (the v1.306.0 rule): nothing this row turns on
    # may resolve to the real ~/.claude, now or after the test.
    user = tmp / "user"
    user.mkdir()
    for var in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(user))
    monkeypatch.setattr(share, "_cli_found", lambda spec: True)
    return {}


def _l_share(app, ctx, done):
    assert app.state.platform.config.profile_share_claude_code is True


def _s_drive(app, tmp, monkeypatch) -> dict:
    folder = tmp / "E-drive" / "Jarvis backups"
    folder.mkdir(parents=True)
    return {"folder": str(folder)}


def _s_changes(app, tmp, monkeypatch) -> dict:
    return {}


async def _before_changes(app, ctx):
    """An earlier chat turn edited a file (the change the request undoes)."""
    p = app.state.platform
    uploads = Path(p.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "notes.txt").write_text("first draft\n", encoding="utf-8")
    p.router.stream = _Scripted([Call("write_file", {"path": "notes.txt", "content": "second draft\n"})])
    await _drive_stream(app, {**_body("rewrite notes.txt"), "tools": ["write_file"]}, _answer_once(app, []))
    assert (uploads / "notes.txt").read_text(encoding="utf-8") == "second draft\n"


def _l_changes(app, ctx, done):
    notes = Path(app.state.platform.config.home) / "uploads" / "notes.txt"
    assert notes.read_text(encoding="utf-8") == "first draft\n", "the last file edit was undone"
    listed = [d for ev, d in done["_frames"] if ev == "tool_call" and d.get("status") == "finished"
              and d.get("name") == "changes_list"]
    assert "notes.txt" in (listed[0].get("output") or "")


def _s_team(app, tmp, monkeypatch) -> dict:
    pid, repo = _project_with_repo(app, tmp)
    reg = app.state.platform.agents_registry
    reg.register("codex-writer", "You write code.", [], base_type="builder", provider="codex-cli",
                 model="gpt-5", roles=["code", "read"])
    reg.register("claude-reviewer", "You review.", [], base_type="reviewer", provider="claude-cli",
                 model="subscription", roles=["review"])
    return {"body": {"project_id": pid}, "repo": str(repo)}


def _l_team(app, ctx, done):
    from iron_jarvis.harness import team as HT

    assert HT.folder_team(app.state.platform.engine, ctx.repo) == {
        "workers": ["codex-writer"], "reviewer": "claude-reviewer",
    }


def _l_stable(app, ctx, done):
    assert app.state.platform.config.update_channel == "stable"


ROWS: list[Row] = [
    Row(1, "Connect my Gmail and summarize my latest emails every morning at 8 and send it to my phone.", "done",
        plan=[
            Call("app_connect", {"app": "gmail"}),
            Call("channel_connect", {"name": "phone", "type": "telegram", "chat_id": "123456789"}),
            Call("schedule_create", {"name": "morning-mail", "cron": "0 8 * * *", "kind": "workflow", "payload": {
                "name": "morning-mail",
                "steps": [{"name": "summarize", "agent": "builder", "task": "Summarize my latest emails."}],
            }}),
        ],
        secrets={"channel.phone": "telegram-bot-v14420-walkthrough"},
        doors=[_step("google_setup")],
        setup=_s_gmail_phone, lands=_l_gmail_phone,
        waits="Wave 7 group A: a destination for 'my phone' (channel_connect) and the Google-setup door"),
    Row(2, "Set up Slack so you can message me when a long job finishes.", "done",
        plan=[Call("channel_connect", {"name": "slack", "type": "slack", "channel": "#jarvis"})],
        secrets={"channel.slack": "slack-bot-v14420-walkthrough"},
        doors=[_step("slack_setup")],
        lands=_l_slack,
        waits="Wave 7 group A: the Slack-app door (/channels) in the same reply", arming_waits=False),
    Row(3, "Add my local Ollama model and make it my default.", "done",
        plan=[
            Call("model_server_add", lambda c: {"address": c.url, "name": "Ollama"}),
            Call("model_server_default", {"server": "Ollama", "model": "llama3.2"}),
        ],
        setup=_s_ollama, lands=_l_ollama,
        waits="Wave 7 group A: model_server_add offered for 'add my local Ollama model'"),
    Row(4, "Add a second Claude account and use it when the first hits its limit.", "done",
        plan=[Call("account_add", {"vendor": "claude", "name": "Second"})],
        doors=[{"href": "/terminals?focus=term_v14380", "label": "Sign in Second in its terminal"}],
        setup=_s_account, lands=_l_account),
    Row(5, "Keep this chat on my PC only.", "done",
        plan=[Call("chat_local_only", {"on": True})],
        setup=_s_local_only, lands=_l_local_only),
    Row(6, "Make a project for the Johnson audit in D:\\Clients\\Johnson and remember that I want workpapers "
           "in Excel.", "done",
        plan=[
            Call("project_create", lambda c: {"name": "Johnson audit", "folder": c.folder}),
            Call("remember_preference", {"text": "I want workpapers in Excel."}),
        ],
        setup=_s_johnson, lands=_l_johnson),
    Row(7, "Create an agent named reviewer that only reads files and uses GPT.", "done",
        plan=[Call("agent_create", {
            "name": "reviewer", "instructions": "Read the files you are given and report problems.",
            "works_like": "reviewer", "provider": "openai", "model": "gpt-6", "roles": ["review"],
        })],
        lands=_l_reviewer),
    Row(8, "Schedule a weekly backup of my settings to my OneDrive folder.", "done",
        plan=[Call("config_change", lambda c: {"key": "backup_mirror_dir", "value": c.folder})],
        setup=_s_onedrive, lands=_l_backup_folder,
        waits="Wave 7 group A: the backup copy folder setting offered for a backup to OneDrive"),
    Row(9, 'Turn on the night shift and queue "update the README" for tonight.', "done",
        plan=[Call("night_task_add", {"words": "update the README"})],
        setup=_s_night, lands=_l_night),
    Row(10, "Connect GitHub and open a pull request for my branch.", "done",
        plan=[Call("app_connect", {"app": "github"})],
        secrets={"app.github": "github-token-v14420-walkthrough"},
        setup=_s_github, lands=_l_github),
    Row(11, "Pair my Edge browser so you can fill forms.", "door", doors=[_step("browser_addon")]),
    Row(12, "Change the theme to something green and calm.", "done",
        plan=[Call("config_set", {"key": "device.theme_colors", "value": THEME_COLORS})],
        lands=_l_theme),
    Row(13, "Let me use Jarvis from my laptop on the same network.", "honest",
        honest_id="another_computer", may_arm=SETTINGS),
    Row(14, "Add the QuickBooks MCP server I use in Claude Desktop.", "done",
        plan=[Call("app_import", {"name": "quickbooks", "source": "claude-desktop"})],
        never_seen=("qb-token-v14420-walkthrough",),
        setup=_s_quickbooks, lands=_l_quickbooks),
    Row(15, "Stop asking me before you run tests.", "done",
        plan=[Call("config_change_protected", {"key": "device.approval_mode", "value": "auto"})],
        lands=_l_auto),
    Row(16, "Make the app start with Windows and keep my PC awake while you work.", "done",
        plan=[
            Call("config_change_protected", {"key": "start_with_windows", "value": True}),
            Call("config_set", {"key": "keep_awake", "value": True}),
        ],
        lands=_l_start_awake),
    Row(17, "Watch my Downloads folder and file new PDFs into the right client folder.", "honest",
        may_arm=frozenset({"sentinel_add", "sentinel_update", "sentinel_delete"}),
        waits="Wave 7 group A: the line that a watcher only suggests (it never files the PDFs itself)"),
    Row(18, "Create a workflow that drafts a reply to every new email from a client.", "done",
        plan=[
            Call("workflow_create", {"name": "client-reply-drafts", "steps": [
                {"name": "draft", "agent": "builder", "task": "Draft a reply to this client email: {body}"},
            ]}),
            Call("reflex_create", {"name": "Client emails", "source": "email", "match": "",
                                   "action": "workflow", "target": "client-reply-drafts"}),
            Call("channel_connect", {"name": "mail", "type": "email", "host": "smtp.example.com",
                                     "from_addr": "me@example.com", "to_addr": "me@example.com"}),
        ],
        secrets={"channel.mail": "mail-app-password-v14420"},
        lands=_l_client_replies,
        waits="Wave 7 group A: workflow_create, reflex_create and an email destination offered"),
    Row(19, "Set a monthly spending limit for my builder agent.", "honest",
        may_arm=frozenset({"agent_create", "agent_update", "agent_pause", "agent_delete"}),
        waits="Wave 7 group A: the line that a built-in agent has no monthly limit (a custom one can)"),
    Row(20, "Remember that I prefer short answers.", "done",
        plan=[Call("remember_preference", {"text": "I prefer short answers."})],
        lands=_l_short),
    Row(21, "Use Notion as your long-term memory.", "done",
        plan=[Call("memory_base_add", {
            "kind": "notion", "name": "Work notes",
            "database": "https://www.notion.so/team/0123456789abcdef0123456789abcdef?v=1", "use_as_memory": True,
        })],
        secrets={"memory.work_notes": "notion-token-v14420-walkthrough"},
        lands=_l_notion),
    Row(22, "Read my screen and click through this installer.", "honest", honest_id="desktop_control"),
    Row(23, "Turn on voice so I can talk to you.", "door_or_honest", may_arm=SETTINGS,
        waits="Wave 7 group A: talking is the composer's microphone (a door or a plain line)"),
    Row(24, "Make Build › Code use Codex on my work account in Full access for this repo.", "done",
        plan=[
            Call("build_defaults_set", {"view": "build_code", "tool": "codex", "mode": "full", "account": "work"},
                 ok=False),
            Call("build_defaults_set", {"view": "build_code", "tool": "codex", "account": "work"}),
        ],
        setup=_s_codex_folder, lands=_l_codex_folder,
        waits="Wave 7 group A: the folder tools offered for 'Make Build › Code use Codex … for this repo'"),
    Row(25, "Share my profile with Claude Code in Build.", "done",
        plan=[Call("config_change", {"key": "profile_share_claude_code", "value": True})],
        setup=_s_share, lands=_l_share),
    Row(26, "Set up a webhook so Zapier can start a task.", "honest",
        may_arm=frozenset({"webhook_add", "webhook_update", "webhook_delete", "reflex_create"}),
        waits="Wave 7 group A: the line that Zapier on the internet cannot reach this PC"),
    Row(27, "Back up everything to my external drive automatically.", "done",
        plan=[Call("config_change", lambda c: {"key": "backup_mirror_dir", "value": c.folder})],
        setup=_s_drive, lands=_l_backup_folder),
    Row(28, "Show me what you changed today and undo the last file edit.", "done",
        plan=[Call("changes_list", {}), Call("change_undo", {"kind": "file"})],
        setup=_s_changes, lands=_l_changes),
    Row(29, "Make my coding sessions use a team: Codex writes, Claude reviews.", "done",
        plan=[Call("build_team_set", {"workers": ["codex-writer"], "reviewer": "claude-reviewer"})],
        setup=_s_team, lands=_l_team),
    Row(30, "Switch to the stable update channel.", "done",
        plan=[Call("config_set", {"key": "update_channel", "value": "stable"})],
        lands=_l_stable),
]

BEFORE: dict[int, Callable] = {28: _before_changes}
GROUPS = {"done": "done", "door": "door", "honest": "honest", "door_or_honest": "honest"}


def _marks(row: Row, *, arming: bool = False):
    if row.waits and (row.arming_waits or not arming):
        return [pytest.mark.xfail(strict=True, reason=f"waits for {row.waits}")]
    return []


def _params(rows, *, arming: bool = False):
    return [pytest.param(r, id=f"r{r.n:02d}", marks=_marks(r, arming=arming)) for r in rows]


# =========================================================================== #
# The scripted model: it calls only what the real lane OFFERED
# =========================================================================== #
def _names(tools) -> list[str]:
    return [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []]


class _Scripted:
    """The stream lane's model. Each round it records the tools it was offered
    and the system prompt; it makes the next planned call ONLY when that tool
    was offered (else it stops and records what was missing); after the plan
    it answers in words."""

    def __init__(self, plan: list[Call], ctx: SimpleNamespace | None = None) -> None:
        self.plan = list(plan)
        self.ctx = ctx or SimpleNamespace()
        self.offered: list[list[str]] = []
        self.systems: list[str] = []
        self.missing: list[str] = []
        self.called: list[str] = []
        #: Everything the lane showed the model, serialised (system + messages).
        self.shown: list[str] = []

    async def __call__(self, *, provider=None, model=None, system="", messages=None, tools=None,
                       session_id=None, task_class=None, **kw):
        offered = _names(tools)
        self.offered.append(offered)
        self.systems.append(str(system or ""))
        self.shown.append(str(system or "") + json.dumps(messages, default=str))
        i = len(self.called)
        if i < len(self.plan) and not self.missing:
            call = self.plan[i]
            if call.tool not in offered:
                self.missing.append(call.tool)
            else:
                args = call.args(self.ctx) if callable(call.args) else dict(call.args)
                self.called.append(call.tool)
                yield {
                    "type": "final",
                    "response": LLMResponse(text="", tool_calls=[ToolCall(id=f"w{i}", name=call.tool, arguments=args)]),
                    "provider": "mock", "model": "mock",
                }
                return
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}


def _body(text: str, **extra: Any) -> dict:
    """A stream body as the chat page sends it."""
    return {"messages": [{"role": "user", "content": text}], "tools": [], "auto_tools": True,
            "approval_mode": "approve_for_me", "device_id": DEVICE, **extra}


def _answer_once(app, seen: list):
    async def answer(data):
        seen.append(data)
        status, raw = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200, raw

    return answer


def _done_frame(frames) -> dict:
    return [d for ev, d in frames if ev == "done"][-1]


def _resolves(href: str) -> bool:
    """A real dashboard place: the page-doors resolver, plus a Build terminal
    opened by its id (``/terminals?focus=<id>``, what account_add's door is)."""
    from urllib.parse import parse_qs, urlparse

    u = urlparse(href)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if u.path == "/terminals" and set(q) == {"focus"}:
        return True
    if u.path == "/memory" and set(q) == {"scope"}:
        # The Memory page's own tab switch: "?scope= picks the tab (working
        # default | lessons | longterm)".
        page = (ROOT / "dashboard" / "app" / "memory" / "page.tsx").read_text(encoding="utf-8")
        return "`?scope=` picks the tab" in page and q["scope"] in ("working", "lessons", "longterm")
    return _page_resolves(href)


@pytest.fixture(autouse=True)
def _fresh_pack_records():
    """MCP load records are process-wide (the v1.325.2 lesson)."""
    from iron_jarvis.settings import records_apps as RA

    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    RA.apps_tools_for.cache_clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()


#: The only programs a walkthrough row may start: git (the scratch repositories
#: and Iron Jarvis's own reads of them) and this interpreter (an MCP app's local
#: child). Anything else is a REAL program on this PC (found live: building the
#: model menu for ``build_team_set`` ran the owner's own ``grok models``).
_ALLOWED_PROGRAMS = {"git", Path(sys.executable).stem.lower()}


@pytest.fixture(autouse=True)
def _no_real_programs(monkeypatch):
    """No real CLI on this PC is ever started by a row: the Grok CLI is not
    found (the catalog would run ``grok models`` otherwise), and every child
    process is checked at its start — one that is not git or this Python fails
    the test, naming it."""
    import subprocess

    from iron_jarvis.providers import cli_detect

    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: None)
    started: list[str] = []
    real_init = subprocess.Popen.__init__

    def init(self, args, *a, **kw):
        argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        name = Path(os.fsdecode(argv[0])).stem.lower() if argv else ""
        if name not in _ALLOWED_PROGRAMS:
            started.append(os.fsdecode(argv[0]))
        return real_init(self, args, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", init)
    yield
    assert started == [], f"a row started a real program on this PC: {started}"


@pytest.fixture(autouse=True)
def _no_lingering_profile_share():
    """``profile.share`` keeps ONE process-wide service (the newest app's). Row
    25 turns sharing with Claude Code ON in its scratch home; left current, a
    later test on this worker that saves a preference without building an app
    (``notify_changed``) would re-render it AFTER the scratch environment was
    put back, i.e. into the real ``~/.claude/CLAUDE.md``. Every app this file
    builds has its share closed and the previous one put back."""
    from iron_jarvis.profile import share

    before = share._current
    yield
    made = share._current
    if made is not None and made is not before:
        made.close()
    share._current = before


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("walk-arming") / "home"))
    return SimpleNamespace(platform=app.state.platform)


def _lanes(d, text: str) -> set[str]:
    """What the stream lane offers by the arming functions both lanes call
    (the POST lane's whole arming plus the stream lane's ask tier)."""
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    ask = [t for t in select_ask_tools(text) if t not in armed]
    ask += CT.settings_ask_tools(d, text, armed, ask)
    return set(armed) | set(ask)


# =========================================================================== #
# 1. Offered by the arming functions (the cheap check, every done row)
# =========================================================================== #
@pytest.mark.parametrize("row", _params([r for r in ROWS if r.verdict == "done"], arming=True))
def test_a_done_rows_tools_are_offered_by_the_arming_functions(d, row):
    offered = _lanes(d, row.text)
    missing = sorted({c.tool for c in row.plan} - offered)
    assert missing == [], f"request {row.n} needs {missing}; offered {sorted(offered)}"


# =========================================================================== #
# 2. End to end through the real stream lane
# =========================================================================== #
async def _run(row: Row, tmp_path: Path, monkeypatch) -> tuple[Any, SimpleNamespace, dict, _Scripted]:
    app = create_app(str(tmp_path / "home"))
    raw = row.setup(app, tmp_path, monkeypatch) if row.setup else {}
    ctx = SimpleNamespace(**{k: v for k, v in raw.items()})
    if row.n in BEFORE:
        await BEFORE[row.n](app, ctx)
    model = _Scripted(row.plan, ctx)
    app.state.platform.router.stream = model
    seen: list = []
    try:
        frames = await _drive_stream(app, _body(row.text, **getattr(ctx, "body", {})), _answer_once(app, seen))
    finally:
        close = getattr(ctx, "_close", None)
        if close:
            close()
    done = dict(_done_frame(frames))
    done["_frames"] = frames
    done["_cards"] = seen
    return app, ctx, done, model


def _never_in_the_clear(app, model: "_Scripted", done: dict, values) -> None:
    """A credential the row handed Iron Jarvis (a secure card's value, an
    imported config's token) is in the encrypted vault ONLY: never in a byte of
    the scratch home in the clear (the database and its WAL with every ledger
    and event row, config.toml, any other file), never in what the lane showed
    the model, never in a frame of the turn, never in the change ledger."""
    home = Path(app.state.platform.config.home)
    files = [f for f in home.rglob("*") if f.is_file()]
    assert any(f.name.startswith("ironjarvis.db") for f in files), "the scan reads the database"
    ledger = TestClient(app).get("/config/ledger").text
    shown = " ".join(model.shown)
    frames = json.dumps(done["_frames"], default=str)
    for value in values:
        raw = value.encode("utf-8")
        for f in files:
            assert raw not in f.read_bytes(), f"{value!r} is in the clear in {f.relative_to(home)}"
        assert value not in shown, f"{value!r} was shown to the model"
        assert value not in frames, f"{value!r} is in the turn's frames"
        assert value not in ledger, f"{value!r} is in the change ledger"


def _doors_are_real(done: dict) -> None:
    for door in done.get("doors") or []:
        assert _resolves(door["href"]), f"a door to a place that does not exist: {door}"


@pytest.mark.asyncio
@pytest.mark.parametrize("row", _params([r for r in ROWS if r.verdict == "done"]))
async def test_a_done_row_finishes_in_chat_and_the_change_lands(row, tmp_path, monkeypatch):
    app, ctx, done, model = await _run(row, tmp_path, monkeypatch)
    try:
        assert model.missing == [], (
            f"request {row.n}: the lane did not offer {model.missing}; it offered {sorted(set(sum(model.offered, [])))}"
        )
        assert model.called == [c.tool for c in row.plan], "every planned call was made"
        finished = [x for ev, x in done["_frames"] if ev == "tool_call" and x.get("status") == "finished"]
        for call, fin in zip(row.plan, finished, strict=True):
            assert fin.get("name", fin.get("tool")) == call.tool
            assert bool(fin.get("ok")) is call.ok, (call.tool, fin.get("error") or fin.get("output"))
        # Secure cards: the value goes to /config/secret, never through chat.
        cards = {c["name"] for c in done.get("config_cards") or [] if c.get("kind") == "secret"}
        assert set(row.secrets) <= cards, f"secure cards shown: {sorted(cards)}"
        client = TestClient(app)
        for name, value in row.secrets.items():
            r = client.post("/config/secret", json={"name": name, "value": value})
            assert r.status_code == 200, r.text
            assert value not in r.text
        _never_in_the_clear(app, model, done, [*row.secrets.values(), *row.never_seen])
        if not row.waits:
            # Gated on the row being pinned: a strict xfail that also failed
            # here would stay quietly red after its builder lands; once the mark
            # is taken off, this check applies.
            want = [c.tool for c in row.plan if c.tool in CARDED]
            got = [c.get("tool") for c in done["_cards"]]
            assert got == want, f"request {row.n}: approval cards {got}, expected {want}"
        _doors_are_real(done)
        for want in row.doors:
            assert want in (done.get("doors") or []), f"request {row.n}: missing door {want}; got {done.get('doors')}"
        if row.lands:
            row.lands(app, ctx, done)
    finally:
        from iron_jarvis.mcp.tools import unload_pack_tools

        for pack in ("github", "gmail", "quickbooks"):
            unload_pack_tools(app.state.platform.registry, pack)


@pytest.mark.asyncio
@pytest.mark.parametrize("row", _params([r for r in ROWS if r.verdict == "door"]))
async def test_a_door_row_carries_the_exact_page(row, tmp_path, monkeypatch):
    _app, _ctx, done, model = await _run(row, tmp_path, monkeypatch)
    assert model.called == []
    for want in row.doors:
        assert want in (done.get("doors") or []), done.get("doors")
        assert _resolves(want["href"])
    _doors_are_real(done)


def _honest_line(row: Row, system: str) -> list[str]:
    items = [n for n in P.not_offered(row.text) if not row.honest_id or n.id == row.honest_id]
    assert items, f"request {row.n}: no 'not offered' line matches it"
    for item in items:
        assert item.line in system, f"request {row.n}: the line {item.id!r} is not in the system prompt"
    return [i.id for i in items]


@pytest.mark.asyncio
@pytest.mark.parametrize("row", _params([r for r in ROWS if GROUPS[r.verdict] == "honest"]))
async def test_an_honest_row_says_so_plainly_and_arms_no_writer_that_would_pretend(row, tmp_path, monkeypatch):
    _app, _ctx, done, model = await _run(row, tmp_path, monkeypatch)
    assert model.called == []
    system = model.systems[0]
    armed = set(sum(model.offered, []))
    stray = sorted((armed & WRITERS) - set(row.may_arm))
    assert stray == [], f"request {row.n}: writers that would pretend: {stray}"
    if row.verdict == "door_or_honest" and done.get("doors"):
        _doors_are_real(done)
        return
    _honest_line(row, system)


# =========================================================================== #
# 3. The table is the walkthrough, and the score is said
# =========================================================================== #
def test_the_table_is_the_thirty_requests_with_their_target_verdicts():
    assert [r.n for r in ROWS] == list(range(1, 31))
    counts = {"done": 0, "door": 0, "honest": 0}
    for r in ROWS:
        counts[GROUPS[r.verdict]] += 1
        if r.verdict == "done":
            assert r.plan, r.n
        if r.verdict == "door":
            assert r.doors, r.n
    assert counts == {"done": 23, "door": 1, "honest": 6}
    doc = __doc__ or ""
    assert "SCORE (target after Wave 7): 23 done, 1 door, 6 honest." in doc
    waiting = sorted(r.n for r in ROWS if r.waits)
    assert waiting == [1, 2, 3, 8, 17, 18, 19, 23, 24, 26]
    assert ", ".join(str(n) for n in waiting) in " ".join(doc.split())


def test_the_score_table(capsys):
    """Prints the walkthrough: each request, its target verdict, and whether
    it is pinned today or waits (and for what)."""
    lines = [f"{'#':>2}  {'verdict':<14} {'state':<9} request"]
    for r in ROWS:
        state = "waits" if r.waits else "pinned"
        lines.append(f"{r.n:>2}  {r.verdict:<14} {state:<9} {r.text}")
        if r.waits:
            lines.append(f"{'':>2}  {'':<14} {'':<9} -> {r.waits}")
    pinned = [r for r in ROWS if not r.waits]
    lines.append(
        f"pinned today: {sum(1 for r in pinned if r.verdict == 'done')} done, "
        f"{sum(1 for r in pinned if r.verdict == 'door')} door, "
        f"{sum(1 for r in pinned if GROUPS[r.verdict] == 'honest')} honest; {len(ROWS) - len(pinned)} wait"
    )
    print("\n".join(lines))
    out = capsys.readouterr().out
    with capsys.disabled():  # shown with `pytest -s`
        print("\n" + out)
    assert "pinned today: 16 done, 1 door, 2 honest; 11 wait" in out
