"""v1.435.0 review (chat-first plan, Wave 3 group H): apps and notification
destinations from chat — the independent review's pins.

Every pin drives the REAL stream lane (``routes/chat.stream_chat_turn``), the
real ``registry.invoke`` and the real Undo route (``POST /undo/{id}``).
Planted secrets are distinct sentinels; every output a person, a model, a log
or the database can see is read for them.

Other programs' settings are read under SCRATCH homes only (``USERPROFILE`` /
``HOME`` / ``APPDATA`` / ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` and the
plugin cache all point inside ``tmp_path``). No real app server starts: every
saved app's program is a launcher that cannot exist.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord, ToolInvocation, UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_apps as RA

NO_SUCH = "ij-no-such-app-launcher-v14350r"

ENV_S = "env_SENTINEL_v14350r_aaaa1111"
HDR_S = "hdr_SENTINEL_v14350r_bbbb2222"
AUTH_S = "auth_SENTINEL_v14350r_cccc3333"
ARG_S = "arg_SENTINEL_v14350r_dddd4444"
URLQ_S = "urlq_SENTINEL_v14350r_eeee5555"
HOOK_S = "https://discord.com/api/webhooks/9876/HOOK_SENTINEL_v14350r"
SLACK_HOOK_S = "https://hooks.slack.com/services/T0/B0/SLACKHOOK_SENTINEL_v14350r"
MAILPW_S = "mailpw_SENTINEL_v14350r_ffff6666"
TG_S = "555:TG_SENTINEL_v14350r_gggg7777"


@pytest.fixture(autouse=True)
def _fresh_load_records():
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()
    RA.apps_tools_for.cache_clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()


@pytest.fixture
def homes(tmp_path, monkeypatch):
    user = tmp_path / "user"
    appdata = tmp_path / "appdata"
    user.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(user / ".codex"))
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(user / ".claude" / "plugins"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return {"user": user, "appdata": appdata}


@pytest.fixture
def app(tmp_path, homes, monkeypatch):
    a = create_app(str(tmp_path / "ij"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return a


class _Grab(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.text: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            self.text.append(record.getMessage())
            if record.exc_info:
                self.text.append(logging.Formatter().formatException(record.exc_info))
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def logs():
    grab = _Grab()
    loggers = [logging.getLogger(n) for n in ("ironjarvis", "iron_jarvis", "")]
    olds = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(grab)
        lg.setLevel(logging.DEBUG)
    yield grab
    for lg, lvl in zip(loggers, olds):
        lg.removeHandler(grab)
        lg.setLevel(lvl)


def _script(app, calls: list[tuple[str, dict]]):
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        n = state["n"]
        state["n"] += 1
        if n < len(calls):
            name, a = calls[n]
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=[ToolCall(id=f"c{n}", name=name, arguments=a)]),
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = fake_stream


def _frames(chunk: Any) -> list[tuple[str, dict]]:
    text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
    out = []
    for block in text.split("\n\n"):
        ev, data = "", None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except ValueError:
                    data = {"raw": line[6:]}
        if ev and isinstance(data, dict):
            out.append((ev, data))
    return out


def _stream(app, text: str, *, answer: str = "once", mode: str = "approve_for_me", raw: list | None = None, **kw):
    platform = app.state.platform
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True, approval_mode=mode)

    async def go():
        gen = await chat_routes.stream_chat_turn(platform, {}, body, **kw)
        seen = []
        async for chunk in gen:
            if raw is not None:
                raw.append(chunk.decode() if isinstance(chunk, bytes) else str(chunk))
            for ev, data in _frames(chunk):
                seen.append((ev, data))
                if ev == "approval":
                    approvals.resolve(data["id"], answer)
        return seen

    return asyncio.run(asyncio.wait_for(go(), 60))


def _done(frames) -> dict:
    return [d for ev, d in frames if ev == "done"][-1]


def _cards(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "approval"]


def _finished(frames, tool) -> list[dict]:
    return [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool
    ]


def _card(frames, title):
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == title]
    assert len(cards) == 1, _done(frames)["config_cards"]
    return cards[0]


def _db_text(p) -> str:
    with session_scope(p.engine) as db:
        rows = [f"{r.args_json}\n{r.output}\n{r.error if hasattr(r, 'error') else ''}" for r in db.exec(select(ToolInvocation))]
        rows += [f"{r.pre_inline or ''}\n{r.pre_ref or ''}" for r in db.exec(select(UndoJournal))]
        rows += [str(r.payload_json or "") for r in db.exec(select(EventRecord))]
    return "\n".join(rows)


def _undo(app, card) -> Any:
    client = TestClient(app)
    look = client.get(f"/config/changes/{card['change_id']}")
    assert look.status_code == 200, look.text
    return client.post(f"/undo/{look.json()['action_id']}")


def _vault_backups(p) -> list[str]:
    return sorted(str(r.get("name")) for r in p.secrets.list() if str(r.get("name") or "").startswith("__undo__"))


def _add_pack(app, body: dict) -> dict:
    client = TestClient(app)
    r = client.post("/mcp/servers", json=body)
    assert r.status_code in (200, 201), r.text
    return R.snap_app(app.state.platform, body["name"])


# =========================================================================== #
# 1. Credentials: a sentinel in every place a secret can come from, read for
#    in every output, through the real lane, the real invoke and real Undo.
# =========================================================================== #
ALL_S = (ENV_S, HDR_S, AUTH_S, ARG_S, URLQ_S)


def _plant_packs(app) -> tuple[dict, dict]:
    stdio = _add_pack(app, {
        "name": "acme",
        "command": NO_SUCH,
        "args": ["serve", "--api-key", ARG_S],
        "env": {"ACME_TOKEN": ENV_S, "PLAIN": "kept"},
    })
    http = _add_pack(app, {
        "name": "remote",
        "transport": "http",
        "url": f"https://127.0.0.1:9/mcp?api_key={URLQ_S}",
        "headers": {"X-Api-Key": HDR_S, "X-Plain": "fine"},
        "auth": {"header": "Authorization", "format": "Bearer {value}", "value": AUTH_S},
    })
    return stdio, http


def test_a_disconnect_and_its_undo_never_show_a_key_anywhere(app, logs):
    p = app.state.platform
    stdio, http = _plant_packs(app)
    toml_before = (p.config.home / "config.toml").read_text(encoding="utf-8")
    assert not any(s in toml_before for s in ALL_S), "the add door keeps every value in the vault"
    raw: list[str] = []
    _script(app, [("app_disconnect", {"app": "acme"}), ("app_disconnect", {"app": "remote"})])
    frames = _stream(app, "disconnect the acme server and remove the remote server", raw=raw)
    assert [c["tool"] for c in _cards(frames)] == ["app_disconnect", "app_disconnect"]
    assert R.snap_app(p, "acme") is None and R.snap_app(p, "remote") is None
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == "App disconnected"]
    assert len(cards) == 2
    client = TestClient(app)
    seen = "\n".join(raw) + _db_text(p) + "\n".join(logs.text)
    seen += json.dumps(p.secrets.list(), default=str)
    seen += client.get("/config/ledger").text
    for c in cards:
        seen += client.get(f"/config/changes/{c['change_id']}").text
        r = _undo(app, c)
        assert r.status_code == 200, r.text
        seen += r.text
    seen += _db_text(p) + "\n".join(logs.text) + (p.config.home / "config.toml").read_text(encoding="utf-8")
    leaked = [s for s in ALL_S if s in seen]
    assert leaked == [], leaked
    assert R.snap_app(p, "acme") == stdio and R.snap_app(p, "remote") == http
    for row in (stdio, http):
        from iron_jarvis.mcp.pack_door import vault_refs

        for ref in vault_refs(row):
            assert p.secrets.get(ref), ref
    assert p.secrets.get(stdio["env_secrets"]["ACME_TOKEN"]) == ENV_S
    assert _vault_backups(p) == [], "every backup is gone once the Undo used it"


def _page_channel(app, name: str, ctype: str, config: dict) -> dict:
    r = TestClient(app).post("/comm/channels", json={"name": name, "type": ctype, "config": config})
    assert r.status_code == 200, r.text
    return R.snap_channel(app.state.platform, name)


DEST_S = (HOOK_S, SLACK_HOOK_S, MAILPW_S, TG_S)


def test_destinations_removed_and_undone_never_show_a_key_anywhere(app, logs):
    p = app.state.platform
    disc = _page_channel(app, "alerts", "discord", {"webhook_url": HOOK_S})
    slk = _page_channel(app, "work", "slack", {"webhook_url": SLACK_HOOK_S})
    mail = _page_channel(app, "mail", "email", {
        "host": "smtp.example.com", "port": "587", "username": "me@example.com",
        "password": MAILPW_S, "from_addr": "me@example.com", "to_addr": "me@example.com",
    })
    tg = _page_channel(app, "phone", "telegram", {"token": TG_S, "chat_id": "42"})
    kinds_before = {r["name"]: (r["kind"], r["description"]) for r in p.secrets.list()}
    raw: list[str] = []
    _script(app, [
        ("channel_remove", {"name": "alerts"}), ("channel_remove", {"name": "work"}),
        ("channel_remove", {"name": "mail"}), ("channel_remove", {"name": "phone"}),
        ("channel_alerts", {"name": "nope", "all": True}),
    ])
    frames = _stream(app, "remove the Discord channel and stop sending to Slack, email or Telegram", raw=raw)
    assert sorted(c["tool"] for c in _cards(frames)) == ["channel_remove"] * 4
    assert set((p.config.comm or {}).get("channels") or {}) == set()
    seen = "\n".join(raw) + _db_text(p) + "\n".join(logs.text) + json.dumps(p.secrets.list(), default=str)
    seen += (p.config.home / "config.toml").read_text(encoding="utf-8")
    for c in [c for c in _done(frames)["config_cards"] if c.get("title") == "Destination removed"]:
        r = _undo(app, c)
        assert r.status_code == 200, r.text
        seen += r.text
    seen += _db_text(p) + "\n".join(logs.text)
    assert [s for s in DEST_S if s in seen.replace((p.config.home / "config.toml").read_text(encoding="utf-8"), "")] == []
    chans = p.config.comm["channels"]
    assert chans["alerts"] == disc and chans["work"] == slk and chans["mail"] == mail and chans["phone"] == tg
    assert p.secrets.get(mail["password_secret"]) == MAILPW_S and p.secrets.get(tg["token_secret"]) == TG_S
    assert _vault_backups(p) == []
    kinds_after = {r["name"]: (r["kind"], r["description"]) for r in p.secrets.list()}
    assert kinds_after == kinds_before, "an Undo puts each key back as it was, its kind included"


def test_a_two_way_switch_never_copies_a_webhook_address_into_the_undo_journal(app):
    """Item 7: ``channel_toggle``'s snapshot (``snap_channel``) copied a
    Discord or Slack webhook address — a credential — into the journal."""
    p = app.state.platform
    before = _page_channel(app, "alerts", "discord", {"webhook_url": HOOK_S})
    before = {**before, "allowed_senders": ["42"]}
    R.apply_channel(p, "alerts", before)
    _script(app, [("channel_toggle", {"name": "alerts", "mode": "two_way"})])
    frames = _stream(app, "turn on two-way on the alerts channel")
    assert [c["tool"] for c in _cards(frames)] == ["channel_toggle"]
    assert p.config.comm["channels"]["alerts"]["inbound_enabled"] is True
    assert HOOK_S not in _db_text(p)
    card = _card(frames, "Channel changed")
    r = _undo(app, card)
    assert r.status_code == 200, r.text
    assert p.config.comm["channels"]["alerts"] == before, "Undo puts the destination back exactly"


# =========================================================================== #
# 3. Undo never destroys newer work, never resurrects what was removed since.
# =========================================================================== #
def _seed(p, name, **extra):
    from iron_jarvis.mcp.pack_door import install_config

    install_config(p, {"name": name, "command": NO_SUCH, "args": ["serve"], **extra}, load=False)
    return R.snap_app(p, name)


def test_undo_of_a_disconnect_never_writes_an_old_key_over_one_set_since(app):
    """Two apps read ONE vault row (a shared, older-style name). The user
    disconnects one (its keys go), sets the shared key again for the other,
    then presses the old Undo: the newer key must stay."""
    p = app.state.platform
    _seed(p, "gh-a", env_secrets={"GITHUB_TOKEN": "shared_gh_token"})
    _seed(p, "gh-b", env_secrets={"GITHUB_TOKEN": "shared_gh_token"})
    p.secrets.set("shared_gh_token", "OLD_v14350r", kind="api_key")
    _script(app, [("app_disconnect", {"app": "gh-a"})])
    frames = _stream(app, "disconnect the gh-a app")
    card = _card(frames, "App disconnected")
    p.secrets.set("shared_gh_token", "NEW_v14350r", kind="api_key")
    r = _undo(app, card)
    assert p.secrets.get("shared_gh_token") == "NEW_v14350r", r.text


def test_undo_of_an_app_switch_never_brings_back_an_app_disconnected_since(app):
    p = app.state.platform
    _seed(p, "notion", env_secrets={"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"})
    p.secrets.set("mcp.notion.NOTION_TOKEN", ENV_S, kind="api_key")
    _script(app, [("app_switch", {"app": "notion", "on": False})])
    frames = _stream(app, "turn off the Notion app")
    card = _card(frames, "App switched off")
    from iron_jarvis.connectors.service import _disconnect_mcp_server  # the Directory's Disconnect

    _disconnect_mcp_server(p, "notion")
    assert R.snap_app(p, "notion") is None
    r = _undo(app, card)
    assert R.snap_app(p, "notion") is None, "a switch's Undo never re-adds an app removed since (without its keys)"
    assert r.status_code == 409, r.text


def test_undo_of_an_app_switch_never_overwrites_an_edit_made_since(app):
    p = app.state.platform
    _seed(p, "notion")
    _script(app, [("app_switch", {"app": "notion", "on": False})])
    frames = _stream(app, "turn off the Notion app")
    card = _card(frames, "App switched off")
    edited = {**R.snap_app(p, "notion"), "args": ["serve", "--new-folder"]}
    from iron_jarvis.mcp.pack_door import install_config

    install_config(p, edited, load=False)
    r = _undo(app, card)
    assert R.snap_app(p, "notion")["args"] == ["serve", "--new-folder"], r.text
    assert r.status_code == 409, r.text


def test_undo_of_an_alert_choice_never_overwrites_alerts_chosen_since(app):
    p = app.state.platform
    R.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42"})
    _script(app, [("channel_alerts", {"name": "phone", "alerts": ["provider.failed"]})])
    frames = _stream(app, "only send me failures on telegram")
    card = _card(frames, "Alerts changed")
    cfg = R.snap_channel(p, "phone")
    cfg["events"] = ["goal.satisfied"]
    R.apply_channel(p, "phone", cfg)
    r = _undo(app, card)
    assert p.config.comm["channels"]["phone"]["events"] == ["goal.satisfied"], r.text
    assert r.status_code == 409, r.text


def test_undo_of_a_two_way_switch_never_overwrites_a_switch_made_since(app):
    p = app.state.platform
    R.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42", "allowed_senders": ["42"]})
    _script(app, [("channel_toggle", {"name": "phone", "mode": "two_way"})])
    frames = _stream(app, "turn on two-way on the phone channel")
    card = _card(frames, "Channel changed")
    cfg = R.snap_channel(p, "phone")
    cfg.update({"inbound_enabled": True, "chat_enabled": True})
    R.apply_channel(p, "phone", cfg)
    r = _undo(app, card)
    assert r.status_code == 409 and "changed since" in r.text, r.text
    assert p.config.comm["channels"]["phone"]["chat_enabled"] is True


def test_undo_of_a_two_way_switch_never_brings_back_a_destination_removed_since(app):
    p = app.state.platform
    R.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42", "allowed_senders": ["42"]})
    _script(app, [("channel_toggle", {"name": "phone", "mode": "two_way"})])
    frames = _stream(app, "turn on two-way on the phone channel")
    card = _card(frames, "Channel changed")
    RA.remove_destination(p, "phone")
    r = _undo(app, card)
    assert "phone" not in ((p.config.comm or {}).get("channels") or {}), r.text
    assert r.status_code == 409, r.text


# =========================================================================== #
# 5. Safety: the POST lane, low trust, "Ask before every action", Local only.
# =========================================================================== #
class _PostRouter:
    default_provider = "mock"

    def __init__(self, calls):
        self.n = 0
        self.calls = calls

    async def complete(self, **kw):
        from iron_jarvis.providers.router import RouteResult

        self.n += 1
        if self.n == 1:
            return RouteResult(LLMResponse(text="", tool_calls=self.calls, usage={}), "mock", "mock")
        return RouteResult(LLMResponse(text="Done.", tool_calls=[], usage={}), "mock", "mock")


def test_the_post_lane_never_arms_a_removal_from_the_words(app):
    """The POST lane cannot show a card, so the words never arm these tools
    there. (A tool the CALLER arms by name in ``tools`` is granted in both
    lanes — "chat approves by arming", older than these tools and the same
    for every record tool; see the review's open points.)"""
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    p = app.state.platform
    _seed(p, "notion")
    R.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42"})
    p.router = _PostRouter([
        ToolCall(id="c1", name="app_disconnect", arguments={"app": "notion"}),
        ToolCall(id="c2", name="channel_remove", arguments={"name": "phone"}),
        ToolCall(id="c3", name="app_switch", arguments={"app": "notion", "on": False}),
        ToolCall(id="c4", name="channel_alerts", arguments={"name": "phone", "all": False, "alerts": ["provider.failed"]}),
    ])
    body = ChatBody(messages=[{"role": "user", "content": "disconnect Notion and remove the Telegram destination"}],
                    auto_tools=True)
    asyncio.run(run_chat_turn(p, {}, body))
    assert R.snap_app(p, "notion") is not None and "enabled" not in R.snap_app(p, "notion")
    assert "phone" in p.config.comm["channels"] and "events" not in p.config.comm["channels"]["phone"]


@pytest.mark.parametrize("posture", ["low_trust", "always_ask", "local_only"])
def test_a_removal_never_runs_without_a_card_under_any_posture(app, posture):
    p = app.state.platform
    _seed(p, "notion")
    _script(app, [("app_disconnect", {"app": "notion"})])
    kw: dict[str, Any] = {}
    mode = "approve_for_me"
    if posture == "low_trust":
        kw = {"trust": "low", "trust_reason": "started from a channel"}
    elif posture == "always_ask":
        mode = "always_ask"
    platform_body: dict[str, Any] = {}
    seen: list[dict] = []
    approvals = chat_routes._approvals(SimpleNamespace(platform=p))
    body = ChatBody(messages=[{"role": "user", "content": "disconnect Notion"}], auto_tools=True,
                    approval_mode=mode, local_only=(posture == "local_only"), **platform_body)

    async def go():
        gen = await chat_routes.stream_chat_turn(p, {}, body, **kw)
        frames = []
        async for chunk in gen:
            for ev, data in _frames(chunk):
                frames.append((ev, data))
                if ev == "approval":
                    seen.append(data)
                    # Low trust must show NO card: an Allow there would run it.
                    approvals.resolve(data["id"], "once" if posture == "low_trust" else "deny")
        return frames

    frames = asyncio.run(asyncio.wait_for(go(), 60))
    assert R.snap_app(p, "notion") is not None, posture
    if posture == "low_trust":
        assert seen == [], "a low-trust turn never offers it"
        assert all(f.get("ok") is False for f in _finished(frames, "app_disconnect"))
    else:
        assert [c["tool"] for c in seen] == ["app_disconnect"], posture


# =========================================================================== #
# 6. A kept key expires with the change that could use it.
# =========================================================================== #
def _age_journal(p, action_ids: set[str], days: int) -> None:
    from datetime import timedelta

    from iron_jarvis.core.ids import utcnow

    with session_scope(p.engine) as db:
        for row in db.exec(select(UndoJournal)):
            if row.action_id in action_ids:
                row.created_at = utcnow() - timedelta(days=days)
                db.add(row)
        db.commit()


def test_a_kept_key_is_deleted_when_its_undo_leaves_the_history(app):
    from iron_jarvis.core.db import prune_events

    p = app.state.platform
    _seed(p, "old-app", env_secrets={"TOKEN": "mcp.old-app.TOKEN"})
    _seed(p, "new-app", env_secrets={"TOKEN": "mcp.new-app.TOKEN"})
    p.secrets.set("mcp.old-app.TOKEN", ENV_S, kind="api_key")
    p.secrets.set("mcp.new-app.TOKEN", AUTH_S, kind="api_key")
    p.secrets.set("__undo__unrelated_keep", "x", kind="password")
    _script(app, [("app_disconnect", {"app": "old-app"})])
    old = _card(_stream(app, "disconnect the old-app app"), "App disconnected")
    old_backups = set(_vault_backups(p)) - {"__undo__unrelated_keep"}
    assert old_backups
    _script(app, [("app_disconnect", {"app": "new-app"})])
    new = _card(_stream(app, "disconnect the new-app app"), "App disconnected")
    new_backups = set(_vault_backups(p)) - old_backups - {"__undo__unrelated_keep"}
    assert new_backups
    client = TestClient(app)
    old_action = client.get(f"/config/changes/{old['change_id']}").json()["action_id"]
    _age_journal(p, {old_action}, days=200)
    prune_events(p.engine, 90)
    left = set(_vault_backups(p))
    assert not (left & old_backups), "the old change left the history, so its kept keys go too"
    assert new_backups <= left, "a change still in the history keeps its Undo"
    assert "__undo__unrelated_keep" in left, "only a row a pruned change named is deleted"
    r = _undo(app, new)
    assert r.status_code == 200, r.text
    assert p.secrets.get("mcp.new-app.TOKEN") == AUTH_S


def test_a_chat_credentials_kept_key_expires_with_its_change_too(app):
    from iron_jarvis.core.db import prune_events

    p = app.state.platform
    p.secrets.set("channel_phone_token", TG_S, kind="token")
    R.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token"})
    r = TestClient(app).post("/config/secret", json={"name": "channel.phone", "value": "999:NEW_v14350r"})
    assert r.status_code == 200, r.text
    backups = set(_vault_backups(p))
    assert len(backups) == 1
    _age_journal(p, {r.json()["action_id"]}, days=200)
    prune_events(p.engine, 90)
    assert not (set(_vault_backups(p)) & backups)
    assert p.secrets.get("channel_phone_token") == "999:NEW_v14350r"


# =========================================================================== #
# 7. Words that are true.
# =========================================================================== #
def test_narrowing_alerts_says_what_still_goes_there(app):
    p = app.state.platform
    R.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42"})
    _script(app, [("channel_alerts", {"name": "phone", "alerts": ["provider.failed"]})])
    frames = _stream(app, "only send me failures on telegram")
    out = json.dumps(_finished(frames, "channel_alerts"))
    assert RA.STILL_SENT in out
    assert RA.STILL_SENT in p.registry.get("channel_alerts").description


def test_a_removal_says_a_copy_of_its_keys_is_kept_only_when_one_is(app):
    p = app.state.platform
    _seed(p, "keyed", env_secrets={"TOKEN": "mcp.keyed.TOKEN"})
    p.secrets.set("mcp.keyed.TOKEN", ENV_S, kind="api_key")
    _seed(p, "keyless")
    _script(app, [("app_disconnect", {"app": "keyed"}), ("app_disconnect", {"app": "keyless"})])
    frames = _stream(app, "disconnect the keyed app and the keyless app")
    outs = [json.dumps(f) for f in _finished(frames, "app_disconnect")]
    assert RA.KEPT_FOR_UNDO in outs[0] and RA.KEPT_FOR_UNDO not in outs[1]


def test_the_review_words_never_name_the_engine():
    import re

    for words in (RA.STILL_SENT, RA.KEPT_FOR_UNDO, RA.NEWER_KEY_WORDS, RA.LOST_KEY_WORDS):
        assert not re.search(r"\b(?:daemon|pack|mcp|endpoint|vault|journal)s?\b", words, re.I), words


def test_two_apps_spelled_alike_are_never_picked_silently(app):
    p = app.state.platform
    _seed(p, "My-App")
    _seed(p, "my_app")
    _script(app, [("app_disconnect", {"app": "myapp"})])
    frames = _stream(app, "disconnect the myapp app")
    assert _cards(frames) == []
    out = json.dumps(_finished(frames, "app_disconnect"))
    assert "name one exactly" in out
    assert R.snap_app(p, "My-App") is not None and R.snap_app(p, "my_app") is not None


# =========================================================================== #
# 8. Arming: 24 requests and 42 everyday sentences the builder's table does
#    not hold.
# =========================================================================== #
WRITERS = ("app_switch", "app_disconnect", "app_import", "channel_remove", "channel_alerts")
REVIEW_REQUESTS: list[tuple[str, set[str]]] = [
    ("turn the Notion app off", {"app_switch"}),
    ("can you disable my GitHub connector please", {"app_switch"}),
    ("deactivate the QuickBooks app for a while", {"app_switch"}),
    ("switch the Stripe app back on", {"app_switch"}),
    ("re-enable the Sentry connector", {"app_switch"}),
    ("Turn off the GitHub connector, I don't need it today.", {"app_switch"}),
    ("unhook the Dropbox app", {"app_disconnect"}),
    ("please uninstall the Notion connector", {"app_disconnect"}),
    ("get rid of the GitHub integration", {"app_disconnect"}),
    ("remove the QuickBooks MCP server", {"app_disconnect"}),
    ("forget the Postgres app", {"app_disconnect"}),
    ("bring over my Cursor servers", {"app_import"}),
    ("import the GitHub server from Claude Code", {"app_import"}),
    ("reuse the servers from my Claude Desktop", {"app_import"}),
    ("copy my Windsurf tools over", {"app_import"}),
    ("pull in the Notion server I set up in VS Code", {"app_import"}),
    ("remove the Telegram destination", {"channel_remove"}),
    ("stop sending alerts to Discord", {"channel_remove", "channel_alerts"}),
    ("delete my Slack notification channel", {"channel_remove"}),
    ("stop texting me on Telegram", {"channel_remove", "channel_alerts"}),
    ("only ping me about errors on Slack", {"channel_alerts"}),
    ("don't notify me when a workflow finishes", {"channel_alerts"}),
    ("limit the alerts my Telegram channel gets", {"channel_alerts"}),
    ("mute the goal notifications on Discord", {"channel_alerts"}),
]
REVIEW_NEAR_MISSES: list[str] = [
    "turn off dark mode in the Notion app",
    "disable the GitHub notifications for this repo",
    "remove the GitHub app's access to my private repos",
    "delete the Notion page about taxes",
    "uninstall Slack from my laptop",
    "disconnect the VPN",
    "pause Spotify",
    "import the bank transactions from QuickBooks",
    "copy this code into Cursor",
    "move the file I edited in VS Code",
    "get the latest version from Cursor's website",
    "add a comment in Claude Code",
    "only send me the summary",
    "send me only the invoice",
    "stop sending me spam",
    "remove the phone number from the contact",
    "delete the channel from YouTube",
    "drop the Telegram group",
    "stop messaging me on WhatsApp",
    "change the alert sound on my phone",
    "set a calendar alert for my email",
    "import the server logs from Codex",
    "copy the tool output from Claude Code",
    "get the app version from Cursor",
    "add the Slack app to my phone",
    "I disconnected the Notion app yesterday",
    "why did you disable the Sentry connector?",
    "turn on notifications for my bank app",
    "remove the email signature",
    "stop sending the report to my email",
    "turn off Outlook notifications",
    "enable two-factor on GitHub",
    "turn on the Gmail filter",
    "remove the Discord bot from my server",
    "disconnect my email account from Outlook",
    "set up a reminder alert for the Slack standup",
    "change the notification sound in Windows for Slack",
    "I sent the file to the client by email",
    "copy the API docs from VS Code",
    "move my notes from Claude Desktop to OneNote",
    "only email me the final PDF",
    "send me only failures-free reports",
    "adjust the notifications volume on my phone",
    "change the alerts tone for Slack messages",
]


def _armed(text: str) -> list[str]:
    RA.apps_tools_for.cache_clear()
    return [n for n in R.record_tools_for(text) if n in WRITERS]


@pytest.mark.parametrize("text,tools", REVIEW_REQUESTS)
def test_a_review_request_offers_its_tool(text, tools):
    assert set(_armed(text)) & tools, _armed(text)


@pytest.mark.parametrize("text", REVIEW_NEAR_MISSES)
def test_a_review_near_miss_arms_none_of_these_writers(text):
    assert _armed(text) == []


def test_the_review_tables_are_big_enough():
    assert len(REVIEW_REQUESTS) >= 20 and len(REVIEW_NEAR_MISSES) >= 20
    assert {t for _r, ts in REVIEW_REQUESTS for t in ts} >= set(WRITERS)


_PATHOLOGICAL = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from iron_jarvis.settings.records_apps import apps_tools_for
bad = [
    "copy " + "the server " * 1800 + "from my claude desktop",
    "import " + ("tools " * 3000) + "logs from codex",
    "change " + "my " * 6000 + "alerts sound",
    "mute " + "a " * 9000 + "notifications on slack",
    "only send me failures" + "-" * 19000,
    "add " + ("x" * 79 + " ") * 200 + "in cursor",
    "get " + "app tool server " * 1200 + "from vs code",
]
t0 = time.perf_counter()
for text in bad:
    apps_tools_for.cache_clear()
    apps_tools_for(text)
print(time.perf_counter() - t0)
"""


def test_the_reviewed_matchers_stay_linear():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", _PATHOLOGICAL, str(root / "src")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 3.0


# =========================================================================== #
# 9. Reading other programs' settings never touches a network share.
# =========================================================================== #
SHARE = "\\\\ij-nohost-v14350r\\share"


def test_a_plugin_on_a_share_or_a_manifest_naming_one_is_never_touched(app, homes, monkeypatch):
    """A Claude Code plugin's manifest is written by the plugin's AUTHOR; a
    stat or resolve of a ``\\\\host\\share`` path is an SMB connection that
    sends the user's Windows sign-in there (the v1.379.0 lesson). Chat's
    ``app_import`` starts this scan with no card."""
    import os
    import os.path

    claude = homes["user"] / ".claude"
    plugins = claude / "plugins"
    local = homes["user"] / "plugin-b"
    (local / ".claude-plugin").mkdir(parents=True)
    (local / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"mcpServers": SHARE + "\\evil.json"}), encoding="utf-8"
    )
    (local / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"localone": {"command": NO_SUCH}}}), encoding="utf-8"
    )
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "a@m": [{"installPath": SHARE + "\\plugin-a"}],
        "b@m": [{"installPath": str(local)}],
    }}), encoding="utf-8")
    touched: list[str] = []
    real_stat, real_realpath = os.stat, os.path.realpath

    def _guard(fn):
        def wrapped(p, *a, **kw):
            text = os.fspath(p) if not isinstance(p, int) else ""
            if isinstance(text, bytes):
                text = text.decode(errors="replace")
            if str(text).replace("/", "\\").startswith("\\\\ij-nohost"):
                touched.append(str(text))
                raise FileNotFoundError(text)
            return fn(p, *a, **kw)
        return wrapped

    monkeypatch.setattr(os, "stat", _guard(real_stat))
    monkeypatch.setattr(os.path, "realpath", _guard(real_realpath))
    _script(app, [("app_import", {})])
    frames = _stream(app, "import my servers from Claude Code")
    out = json.dumps(_finished(frames, "app_import"))
    assert "localone" in out, out
    assert touched == [], touched


def test_the_arming_cache_holds_only_the_cut_text():
    RA.apps_tools_for.cache_clear()
    head = "remove the Telegram destination. " + "x" * RA._MAX_TEXT
    RA.apps_tools_for(head + "TAIL_ONE")
    RA.apps_tools_for(head + "TAIL_TWO_" + TG_S)
    info = RA.apps_tools_for.cache_info()
    assert info.hits == 1 and info.currsize == 1, info


# =========================================================================== #
# 10. app_import's card says what it STARTS (the plan's "preview card").
# =========================================================================== #
def _desktop_config(homes, servers: dict) -> None:
    path = homes["appdata"] / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_the_import_card_shows_what_it_starts_and_never_a_key(app, homes):
    _desktop_config(homes, {"quickbooks": {
        "command": NO_SUCH, "args": ["qb-server", "--api-key", ARG_S],
        "env": {"QB_TOKEN": ENV_S, "QB_COMPANY": "acme"},
    }})
    _script(app, [("app_import", {"name": "quickbooks", "source": "claude-desktop"})])
    raw: list[str] = []
    frames = _stream(app, "add the QuickBooks server I use in Claude Desktop", raw=raw)
    cards = _cards(frames)
    assert [c["tool"] for c in cards] == ["app_import"]
    words = str(cards[0].get("cost_note") or "")
    assert NO_SUCH in words and "qb-server" in words and "Claude Desktop" in words, cards[0]
    assert cards[0].get("once_only") is True, "one Allow starts one program, never more"
    seen = "\n".join(raw) + _db_text(app.state.platform)
    assert ARG_S not in seen and ENV_S not in seen


def test_an_import_armed_by_name_still_shows_its_card(app, homes):
    """A tool the page arms by name is granted by arming — but an import
    STARTS a program from another app's settings, so its card always shows
    what it starts (never under "Don't ask", which the user chose)."""
    _desktop_config(homes, {"quickbooks": {"command": NO_SUCH, "args": ["qb-server"]}})
    p = app.state.platform
    _script(app, [("app_import", {"name": "quickbooks"})])
    approvals = chat_routes._approvals(SimpleNamespace(platform=p))
    body = ChatBody(messages=[{"role": "user", "content": "hello"}], tools=["app_import"], approval_mode="approve_for_me")
    seen: list[dict] = []

    async def go():
        gen = await chat_routes.stream_chat_turn(p, {}, body)
        async for chunk in gen:
            for ev, data in _frames(chunk):
                if ev == "approval":
                    seen.append(data)
                    approvals.resolve(data["id"], "deny")

    asyncio.run(asyncio.wait_for(go(), 60))
    assert [c["tool"] for c in seen] == ["app_import"]
    assert R.snap_app(p, "quickbooks") is None


def test_the_post_lane_refuses_an_import_armed_by_name(app, homes):
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    _desktop_config(homes, {"quickbooks": {"command": NO_SUCH, "args": ["qb-server"]}})
    p = app.state.platform
    p.router = _PostRouter([ToolCall(id="c1", name="app_import", arguments={"name": "quickbooks"})])
    body = ChatBody(messages=[{"role": "user", "content": "hello"}], tools=["app_import"])
    asyncio.run(run_chat_turn(p, {}, body))
    assert R.snap_app(p, "quickbooks") is None
    with session_scope(p.engine) as db:
        row = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "app_import")).first()
        said = f"{row.output} {getattr(row, 'error', '') or ''}"
    assert "cannot ask for approval" in said and "for free" not in said, said
