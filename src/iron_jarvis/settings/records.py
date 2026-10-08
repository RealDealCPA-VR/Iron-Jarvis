"""Records changed from chat, each with a ledger row and an Undo (calm UI
redesign S5, AUDIT §6.3).

Settings are keys in the one schema; RECORDS are the user's own things —
schedules, saved workflows, notification channels, connected apps. Chat could
create a schedule or a workflow but not change or remove one, and it could not
touch a channel or an app at all. These tools close that gap the same way the
settings tools do (settings.tools):

* every tool is REVERSIBLE: ``capture_undo`` snapshots the record as it was
  into a ``record_restore`` journal row the registry writes for this very
  call, and ``POST /undo/{id}`` puts it back (``restore``) — a created record
  is removed, a changed one restored, a removed one recreated;
* the result carries a ``record_change`` the chat lanes turn into the reply's
  "Changed … [Undo]" card (``chat_turn.config_cards_from_result``), with a
  ``change_id`` minted at capture so the card can find its Undo
  (``GET /config/changes/{change_id}``);
* a credential is NEVER a tool argument: ``channel_connect`` and
  ``app_connect`` write the record with a reference to a vault name and hand
  back ``secret_requests`` the page renders as secure cards (the same door as
  ``config_secret``).

Every change that can widen what reaches the machine or start work unattended
is ASK tier (the ordinary approval card): updating or removing a schedule or a
workflow, scheduling a workflow, turning a channel's two-way listening on or
off, connecting a channel or an app. ``schedule_create`` and
``workflow_create`` keep their own (allow) tiers and only gain the Undo.

The rest of AUDIT §6.3, same shape: webhooks, sentinels, goals and reflex
rules (update / delete, ``reflex_create``) are ask tier; ``grant_revoke`` is
allow tier — taking back an "always allow" only narrows what runs unasked —
and its Undo puts the grant back. ``webhook_add`` / ``sentinel_add`` /
``goal_add`` keep their tiers and gain the Undo through
:func:`capture_create_undo`. A record whose id is minted BY the store
(a goal, a reflex rule) is captured "pending" and named after the write
(:func:`bind_created`) — the registry journals the descriptor only after a
successful execute, the same post-execute fill ``finalize_post_hash`` does.

``config_undo`` is "undo that" from chat: it reverses the newest change the
Settings ledger lists (or the one named) through THE code ``POST
/undo/{id}`` runs (``routes.undo.perform_undo``).

Snapshots and restores are BLOCKING (SQLite, config.toml, MCP spawns); the
tools run them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from contextvars import ContextVar
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult

log = logging.getLogger("ironjarvis.settings")

RECORD_RESTORE = "record_restore"

#: The record tools added here, with their default permission. Kept in one
#: table so the permission defaults, the low-trust deny set and the roster
#: pins can all read the same names.
RECORD_TOOL_TIERS: dict[str, str] = {
    "schedule_update": "ask",
    "schedule_delete": "ask",
    "workflow_update": "ask",
    "workflow_delete": "ask",
    "workflow_schedule": "ask",
    "channel_toggle": "ask",
    "channel_connect": "ask",
    "app_connect": "ask",
    # AUDIT §6.3, the rest: each starts or reshapes unattended work (a
    # trigger, a watcher, a goal the autonomy loop reads, a signal→action
    # rule) — ask on the card, with an Undo.
    "webhook_update": "ask",
    "webhook_delete": "ask",
    "sentinel_update": "ask",
    "sentinel_delete": "ask",
    "goal_update": "ask",
    "goal_delete": "ask",
    "reflex_create": "ask",
    "reflex_update": "ask",
    "reflex_delete": "ask",
    # Taking back an "always allow" only narrows what runs without a card.
    "grant_revoke": "allow",
}
#: The create tools that gained an Undo (their own tiers are unchanged).
RECORD_CREATE_TOOLS = frozenset({"schedule_create", "workflow_create", "webhook_add", "sentinel_add", "goal_add"})
#: Tools whose successful result carries a ``record_change`` (the record
#: tools plus the create tools that gained an Undo).
RECORD_CARD_TOOLS = frozenset({*RECORD_TOOL_TIERS, *RECORD_CREATE_TOOLS})

#: "undo that" from chat (:class:`ConfigUndoTool`): ask — an undo is a change.
#: Kept OUT of ``RECORD_TOOL_TIERS``: it carries no card and is not itself on
#: the configuration ledger.
CONFIG_UNDO_TIERS: dict[str, str] = {"config_undo": "ask"}

_CHANGE_ID: ContextVar[str] = ContextVar("ij_record_change_id", default="")
#: A create captured before its store minted the id (see :func:`bind_created`).
_PENDING: ContextVar[dict[str, Any] | None] = ContextVar("ij_record_pending", default=None)
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _.-]{0,79}$")
_CHANNEL_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")

_SCHEDULE_WORDS = re.compile(
    r"\b(?:schedules?|scheduled|every (?:day|morning|evening|night|week|month|hour|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)|nightly|weekly|daily|cron)\b",
    re.IGNORECASE,
)
_WORKFLOW_WORDS = re.compile(r"\bworkflows?\b", re.IGNORECASE)
_CHANNEL_WORDS = re.compile(
    r"\b(?:notification channels?|channels?|telegram|slack|discord|two.?way)\b", re.IGNORECASE
)
_APP_WORDS = re.compile(
    r"\b(?:connect (?:my|to|the)|notion|github|sentry|stripe|brave search|google maps|connectors?)\b",
    re.IGNORECASE,
)
_WEBHOOK_WORDS = re.compile(r"\bweb ?hooks?\b", re.IGNORECASE)
_SENTINEL_WORDS = re.compile(r"\b(?:sentinels?|watchers?)\b", re.IGNORECASE)
_GOAL_WORDS = re.compile(r"\bgoals?\b", re.IGNORECASE)
_REFLEX_WORDS = re.compile(
    r"\b(?:reflex(?:es)?|rules?)\b|\bwhen\b[^.?!\n]{1,80}\bhappens\b", re.IGNORECASE
)
_GRANT_WORDS = re.compile(r"\b(?:grants?|always[ -]allow(?:s|ed|ing)?)\b", re.IGNORECASE)
#: "undo that" (the brief's must-work example) and its close variants.
_UNDO_WORDS = re.compile(
    r"\bundo (?:that|it|this|the last (?:change|setting)|my last change)\b", re.IGNORECASE
)


def record_tools_for(text: str) -> list[str]:
    """The record tools a chat message plausibly asks for, by the record it
    names (chat-only arming, like ``settings.tools.wants_settings``). The
    stream lane arms them VISIBLE, never granted: an ask-tier one renders
    the card, ``grant_revoke`` (allow) and ``goal_list`` (read) run."""
    t = text or ""
    out: list[str] = []
    if _SCHEDULE_WORDS.search(t):
        out += ["schedule_update", "schedule_delete", "workflow_schedule"]
    if _WORKFLOW_WORDS.search(t):
        out += ["workflow_update", "workflow_delete", "workflow_schedule"]
    if _CHANNEL_WORDS.search(t):
        out += ["channel_toggle", "channel_connect"]
    if _APP_WORDS.search(t):
        out += ["app_connect"]
    if _WEBHOOK_WORDS.search(t):
        out += ["webhook_update", "webhook_delete"]
    if _SENTINEL_WORDS.search(t):
        out += ["sentinel_update", "sentinel_delete"]
    if _GOAL_WORDS.search(t):
        out += ["goal_list", "goal_update", "goal_delete"]
    if _REFLEX_WORDS.search(t):
        out += ["reflex_create", "reflex_update", "reflex_delete"]
    if _GRANT_WORDS.search(t):
        out += ["grant_revoke"]
    if _UNDO_WORDS.search(t):
        out += ["config_undo"]
    return list(dict.fromkeys(out))


class RecordError(ValueError):
    pass


# ------------------------------------------------------------- schedules


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def snap_schedule(platform: Any, name: str) -> dict[str, Any] | None:
    rec = platform.scheduler.get(name)
    if rec is None:
        return None
    return {
        "name": rec.name,
        "trigger_type": rec.trigger_type,
        "cron": rec.cron or "",
        "run_at": _iso(rec.run_at),
        "interval_seconds": rec.interval_seconds,
        "kind": rec.kind,
        "payload": rec.decoded_payload(),
        "enabled": bool(rec.enabled),
    }


def _trigger_kwargs(prior: dict[str, Any]) -> dict[str, Any]:
    t = prior.get("trigger_type") or "cron"
    if t == "date":
        return {"run_at": prior.get("run_at")}
    if t == "interval":
        return {"interval_seconds": int(prior.get("interval_seconds") or 0)}
    return {"cron": prior.get("cron") or ""}


def restore_schedule(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    sch = platform.scheduler
    if prior is None:
        sch.remove(name)
        return f"undo: removed the schedule '{name}'"
    trig = _trigger_kwargs(prior)
    if sch.get(name) is None:
        sch.add_task(
            name,
            trig.get("cron"),
            run_at=trig.get("run_at"),
            interval_seconds=trig.get("interval_seconds"),
            kind=prior.get("kind") or "workflow",
            payload=prior.get("payload") or {},
            enabled=bool(prior.get("enabled", True)),
        )
        return f"undo: recreated the schedule '{name}'"
    sch.update_task(
        name,
        **trig,
        kind=prior.get("kind") or "workflow",
        payload=prior.get("payload") or {},
        enabled=bool(prior.get("enabled", True)),
    )
    return f"undo: restored the schedule '{name}'"


def _describe_schedule(s: dict[str, Any] | None) -> str | None:
    if s is None:
        return None
    t = s.get("trigger_type")
    when = (
        f"once at {s.get('run_at')}"
        if t == "date"
        else f"every {s.get('interval_seconds')} s"
        if t == "interval"
        else f"cron {s.get('cron')}"
    )
    return f"{when}{'' if s.get('enabled', True) else ' (paused)'}"


# ------------------------------------------------------------- workflows


def _wf_store(platform: Any) -> Any:
    from ..workflows.store import WorkflowStore

    return WorkflowStore(platform.engine)


def snap_workflow(platform: Any, name: str) -> dict[str, Any] | None:
    store = _wf_store(platform)
    rec = store.get(name)
    if rec is None:
        return None
    try:
        steps = json.loads(rec.steps_json or "[]")
    except ValueError:
        steps = []
    return {
        "name": rec.name,
        "description": rec.description or "",
        "steps": steps,
        "project_id": store.get_project_id(rec.name) or "",
    }


def restore_workflow(platform: Any, name: str, prior: dict[str, Any] | None, now: str = "") -> str:
    store = _wf_store(platform)
    now = now or name
    if prior is None:
        store.remove(now)
        return f"undo: removed the workflow '{now}'"
    if now != name and store.get(now) is not None and store.get(name) is None:
        store.patch(now, new_name=name)
    store.save(name, prior.get("steps") or [], prior.get("description") or "", project_id=prior.get("project_id") or "")
    return f"undo: restored the workflow '{name}'"


def _describe_workflow(w: dict[str, Any] | None) -> str | None:
    if w is None:
        return None
    n = len(w.get("steps") or [])
    return f"{n} step{'s' if n != 1 else ''}" + (f" — {w['description']}" if w.get("description") else "")


# -------------------------------------------------------------- channels


def _d(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    return getattr(w, "d", None)


def snap_channel(platform: Any, name: str) -> dict[str, Any] | None:
    cfg = ((platform.config.comm or {}).get("channels") or {}).get(name)
    return copy.deepcopy(cfg) if isinstance(cfg, dict) else None


def apply_channel(platform: Any, name: str, cfg: dict[str, Any] | None) -> None:
    """Write one channel's stored config (``None`` removes it) to config.toml
    and to the live notifier. Vault secrets are NOT touched: an undo that
    brings a channel back finds its token where it left it."""
    from ..core.config import persist_config_values

    comm = dict(platform.config.comm or {})
    channels = dict(comm.get("channels") or {})
    old = channels.get(name)
    if cfg is None:
        channels.pop(name, None)
    else:
        channels[name] = cfg
    comm["channels"] = channels
    platform.config.comm = comm
    persist_config_values(platform.config.home, {"comm": comm})
    notifier = platform.notifier
    notifier.remove_channel(name)
    if cfg is not None:
        from ..comm import CHANNEL_TYPES, httpx_get, httpx_post

        ctype = cfg.get("type")
        if ctype in CHANNEL_TYPES:
            notifier.add_channel(
                name,
                CHANNEL_TYPES[ctype](cfg, http_post=httpx_post, http_get=httpx_get, secret_resolver=platform.secrets.get),
            )
    if "slack" in ((old or {}).get("type"), (cfg or {}).get("type")):
        d = _d(platform)
        rearm = getattr(d, "_live_rearm", None) or {}
        loop, fn = rearm.get("loop"), rearm.get("slack")
        if loop is not None and fn is not None:
            try:
                loop.call_soon_threadsafe(fn)
            except Exception:  # noqa: BLE001 — a re-arm hiccup never fails the write
                pass


def restore_channel(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    apply_channel(platform, name, copy.deepcopy(prior) if prior is not None else None)
    return f"undo: {'removed' if prior is None else 'restored'} the channel '{name}'"


def _describe_channel(c: dict[str, Any] | None) -> str | None:
    if c is None:
        return None
    bits = [str(c.get("type") or "channel")]
    if c.get("chat_enabled"):
        bits.append("two-way chat on")
    elif c.get("inbound_enabled"):
        bits.append("two-way on")
    else:
        bits.append("send only")
    return ", ".join(bits)


# ------------------------------------------------------------------ apps


def snap_app(platform: Any, connector_id: str) -> dict[str, Any] | None:
    for s in list(getattr(platform.config, "mcp_servers", None) or []):
        if isinstance(s, dict) and s.get("name") == connector_id:
            return copy.deepcopy(s)
    return None


def load_app(platform: Any, cfg: dict[str, Any]) -> int:
    """(Re)load one MCP pack's tools live. Returns how many registered."""
    from ..mcp.tools import mcp_tools

    name = cfg.get("name") or ""
    for t in platform.registry.mcp_names(name):
        platform.registry.unregister(t)
    loaded = 0
    try:
        for tool in mcp_tools([cfg], secret_resolver=platform.secrets.get, home=platform.config.home):
            platform.registry.register(tool, mcp=True)
            loaded += 1
    except Exception:  # noqa: BLE001 — the config is saved; a restart retries
        log.warning("app %s: tools did not load", name, exc_info=True)
    return loaded


def apply_app(platform: Any, connector_id: str, cfg: dict[str, Any] | None, *, load: bool = True) -> int:
    from ..core.config import persist_config_values

    servers = [
        s for s in (getattr(platform.config, "mcp_servers", None) or []) if not (isinstance(s, dict) and s.get("name") == connector_id)
    ]
    if cfg is not None:
        servers.append(cfg)
    platform.config.mcp_servers = servers
    persist_config_values(platform.config.home, {"mcp_servers": servers})
    if cfg is None:
        for t in platform.registry.mcp_names(connector_id):
            platform.registry.unregister(t)
        return 0
    return load_app(platform, cfg) if load else 0


def restore_app(platform: Any, connector_id: str, prior: dict[str, Any] | None) -> str:
    apply_app(platform, connector_id, copy.deepcopy(prior) if prior is not None else None)
    return f"undo: {'disconnected' if prior is None else 'restored'} the app '{connector_id}'"


def reload_app_after_secret(platform: Any, connector_id: str) -> None:
    """A credential card saved an app's token: load the pack now if its config
    is waiting for it (best-effort; never raises)."""
    try:
        cfg = snap_app(platform, connector_id)
        if cfg is not None:
            load_app(platform, cfg)
    except Exception:  # noqa: BLE001
        log.warning("app %s: reload after its credential failed", connector_id, exc_info=True)


def _dt(v: Any) -> Any:
    """An ISO string from a snapshot back to a datetime (None stays None)."""
    from datetime import datetime

    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


# -------------------------------------------------------------- webhooks


def _wh_row(db: Any, slug: str) -> Any:
    from sqlmodel import select

    from ..webhooks.models import WebhookRecord

    return db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first()


def snap_webhook(platform: Any, slug: str) -> dict[str, Any] | None:
    from ..core.db import session_scope

    with session_scope(platform.engine) as db:
        rec = _wh_row(db, slug)
        if rec is None:
            return None
        try:
            types = json.loads(rec.event_types_json or "[]")
        except ValueError:
            types = []
        return {
            "id": rec.id,
            "slug": rec.slug,
            "direction": rec.direction or "inbound",
            "target_url": rec.target_url or "",
            "secret_name": rec.secret_name or "",
            "event_types": types if isinstance(types, list) else [],
            "enabled": bool(rec.enabled),
            "created_at": _iso(rec.created_at),
        }


def _inbound_handler(platform: Any, slug: str) -> Any:
    """The live handler an inbound webhook answers with — the same body the
    ``webhook_add`` tool and ``POST /webhooks`` install: publish
    ``webhook.received`` and fire any bound reflex rule."""

    async def handler(body: Any, _slug: str = slug) -> dict[str, Any]:
        await platform.event_bus.publish("webhook.received", {"slug": _slug, "body": body})
        fired: list[Any] = []
        router = getattr(platform, "reflex_router", None)
        if router is not None:
            try:
                fired = await router.on_webhook(_slug, body)
            except Exception:  # noqa: BLE001 — never break the ack
                pass
        from ..reflex.router import summarize_fires

        return {"ok": True, **summarize_fires(fired)}

    return handler


def _sync_inbound(platform: Any, slug: str, enabled: bool, secret_name: str) -> None:
    """Make the live inbound handler match the row. The store has no "pause"
    verb (``unregister`` deletes the row), and dispatch answers from the live
    handler — so a paused inbound webhook is a row with ``enabled=False`` and
    no handler, exactly what a restart's ``rehydrate`` leaves for it."""
    inb = platform.inbound_webhooks
    if enabled:
        secret = platform.secrets.get(secret_name) if secret_name else None
        inb.register(slug, _inbound_handler(platform, slug), secret=secret, secret_name=secret_name or None)
    else:
        getattr(inb, "_handlers", {}).pop(slug, None)


def _write_webhook(platform: Any, s: dict[str, Any]) -> None:
    """Write one webhook row exactly as ``s`` describes it (id and creation
    time kept when the row is recreated)."""
    from ..core.db import session_scope
    from ..webhooks.models import WebhookRecord

    with session_scope(platform.engine) as db:
        row = _wh_row(db, s["slug"])
        if row is None:
            row = WebhookRecord(id=s.get("id") or new_id("whk"), slug=s["slug"])
            created = _dt(s.get("created_at"))
            if created is not None:
                row.created_at = created
        row.direction = s.get("direction") or "inbound"
        row.target_url = s.get("target_url") or ""
        row.secret_name = s.get("secret_name") or ""
        row.event_types_json = json.dumps(list(s.get("event_types") or []))
        row.enabled = bool(s.get("enabled", True))
        db.add(row)
        db.commit()


def remove_webhook(platform: Any, slug: str, direction: str) -> None:
    """``DELETE /webhooks/{slug}``'s removal: the store of its direction drops
    the row and the live handler / secret cache; the vault is left alone."""
    if direction == "outbound":
        platform.outbound_webhooks.unregister(slug)
    else:
        platform.inbound_webhooks.unregister(slug)


def apply_webhook_update(
    platform: Any, before: dict[str, Any], *, url: str | None, types: list[str] | None, enabled: bool | None
) -> None:
    """Change an outbound target / event types through the store's own
    ``register`` (the SSRF check and the empty-types refusal ``POST
    /webhooks`` runs), then the enabled flag on the row + the live handler."""
    slug = before["slug"]
    if url is not None or types is not None:
        sn = before.get("secret_name") or ""
        secret = platform.secrets.get(sn) if sn else None
        platform.outbound_webhooks.register(
            slug,
            url if url is not None else before.get("target_url") or "",
            list(types) if types is not None else list(before.get("event_types") or []),
            secret=secret,
            secret_name=sn or None,
        )
    if enabled is not None:
        now = snap_webhook(platform, slug) or before
        _write_webhook(platform, {**now, "enabled": enabled})
        if now.get("direction") != "outbound":
            _sync_inbound(platform, slug, enabled, now.get("secret_name") or "")


def restore_webhook(platform: Any, slug: str, prior: dict[str, Any] | None) -> str:
    cur = snap_webhook(platform, slug)
    if prior is None:
        if cur is not None:
            remove_webhook(platform, slug, cur["direction"])
        else:
            getattr(platform.inbound_webhooks, "_handlers", {}).pop(slug, None)
        return f"undo: removed the webhook '{slug}'"
    _write_webhook(platform, prior)
    if prior.get("direction") == "outbound":
        # A direction flipped by a re-register leaves no inbound handler behind.
        getattr(platform.inbound_webhooks, "_handlers", {}).pop(slug, None)
    else:
        _sync_inbound(platform, slug, bool(prior.get("enabled", True)), prior.get("secret_name") or "")
    return f"undo: {'recreated' if cur is None else 'restored'} the webhook '{slug}'"


def _describe_webhook(w: dict[str, Any] | None) -> str | None:
    if w is None:
        return None
    if w.get("direction") == "outbound":
        types = ", ".join(str(t) for t in w.get("event_types") or []) or "no events"
        text = f"sends {types} to {w.get('target_url')}"
    else:
        text = "receives calls"
    return text + ("" if w.get("enabled", True) else " (paused)")


# ------------------------------------------------------------- sentinels


def snap_sentinel(platform: Any, name: str) -> dict[str, Any] | None:
    rec = platform.sentinels.get(name)
    if rec is None:
        return None
    return {
        "id": rec.id,
        "name": rec.name,
        "kind": rec.kind,
        "config": rec.decoded_config(),
        "task": rec.task or "",
        "agent_type": rec.agent_type or "builder",
        "risk": rec.risk or "low",
        "enabled": bool(rec.enabled),
        "created_at": _iso(rec.created_at),
        "last_checked_at": _iso(rec.last_checked_at),
        "last_state": rec.decoded_state(),
        "last_error": rec.last_error,
    }


def restore_sentinel(platform: Any, name: str, prior: dict[str, Any] | None) -> str:
    """A created sentinel is removed; a changed one gets its settings back
    (its watch memory is left as it is — what it has seen since is real); a
    removed one is recreated WITH its memory, so it does not re-fire for
    changes it had already reported."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..sentinels.models import SentinelRecord

    svc = platform.sentinels
    if prior is None:
        svc.remove(name)
        return f"undo: removed the sentinel '{name}'"
    with session_scope(svc.engine) as db:
        row = db.exec(select(SentinelRecord).where(SentinelRecord.name == name)).first()
        recreated = row is None
        if row is None:
            row = SentinelRecord(id=prior.get("id") or new_id("sentinel"), name=name)
            created = _dt(prior.get("created_at"))
            if created is not None:
                row.created_at = created
            row.last_checked_at = _dt(prior.get("last_checked_at"))
            row.last_state_json = json.dumps(prior.get("last_state") or {}, default=str)
            row.last_error = prior.get("last_error")
        row.kind = prior.get("kind") or "file"
        row.config_json = json.dumps(prior.get("config") or {}, default=str)
        row.task = prior.get("task") or ""
        row.agent_type = prior.get("agent_type") or "builder"
        row.risk = prior.get("risk") or "low"
        row.enabled = bool(prior.get("enabled", True))
        db.add(row)
        db.commit()
    return f"undo: {'recreated' if recreated else 'restored'} the sentinel '{name}'"


def _describe_sentinel(s: dict[str, Any] | None) -> str | None:
    if s is None:
        return None
    where = (s.get("config") or {}).get("path") or s.get("kind")
    return f"watches {where}" + ("" if s.get("enabled", True) else " (paused)")


# ----------------------------------------------------------------- goals

#: The goal fields a change (and its Undo) touches; the budget COUNTERS are
#: the autonomy loop's running totals and are only put back on a recreate.
_GOAL_FIELDS = ("text", "source", "category", "priority", "autonomy_level", "status", "action_budget", "spend_budget")
GOAL_STATUSES = ("active", "paused", "done", "abandoned")


def snap_goal(platform: Any, goal_id: str) -> dict[str, Any] | None:
    rec = platform.intent.get_goal(goal_id)
    if rec is None:
        return None
    out = {f: getattr(rec, f) for f in _GOAL_FIELDS}
    out.update(
        id=rec.id,
        actions_taken=int(rec.actions_taken or 0),
        tokens_spent=int(rec.tokens_spent or 0),
        last_acted_at=_iso(rec.last_acted_at),
        created_at=_iso(rec.created_at),
    )
    return out


def resolve_goal(platform: Any, ref: str) -> str:
    """A goal's id from its id or its exact text (case-insensitive, unique).
    Blocking. "" when nothing (or more than one goal) matches."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if platform.intent.get_goal(ref) is not None:
        return ref
    hits = [g.id for g in platform.intent.list_goals() if (g.text or "").strip().casefold() == ref.casefold()]
    return hits[0] if len(hits) == 1 else ""


def restore_goal(platform: Any, goal_id: str, prior: dict[str, Any] | None) -> str:
    """A created goal is removed — the goal engine has no delete, so the row
    goes directly; if the autonomy loop already proposed work for it, the
    goal is ABANDONED instead (its proposals keep their goal). A changed goal
    gets its fields back."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..motivation.models import GoalRecord, ProposalRecord

    with session_scope(platform.engine) as db:
        row = db.get(GoalRecord, goal_id)
        if prior is None:
            if row is None:
                return "undo: the goal was already gone"
            text = row.text
            if db.exec(select(ProposalRecord).where(ProposalRecord.goal_id == goal_id)).first() is not None:
                row.status = "abandoned"
                db.add(row)
                db.commit()
                return f"undo: abandoned the goal '{text}' (it already had suggestions)"
            db.delete(row)
            db.commit()
            return f"undo: removed the goal '{text}'"
        recreated = row is None
        if row is None:
            row = GoalRecord(id=goal_id)
            row.actions_taken = int(prior.get("actions_taken") or 0)
            row.tokens_spent = int(prior.get("tokens_spent") or 0)
            row.last_acted_at = _dt(prior.get("last_acted_at"))
            created = _dt(prior.get("created_at"))
            if created is not None:
                row.created_at = created
        for f in _GOAL_FIELDS:
            if f in prior:
                setattr(row, f, prior[f])
        db.add(row)
        db.commit()
        return f"undo: {'recreated' if recreated else 'restored'} the goal '{prior.get('text')}'"


def _describe_goal(g: dict[str, Any] | None) -> str | None:
    if g is None:
        return None
    return f"{g.get('status')}, {g.get('autonomy_level')}, priority {g.get('priority')}"


# ----------------------------------------------------------- reflex rules

#: The fields a rule change (and its Undo) touches; the fire bookkeeping is
#: only put back on a recreate.
_REFLEX_FIELDS = ("name", "source", "match", "action", "target", "task_template", "project_id", "enabled")


def validate_reflex(source: str, match: str, action: str, target: str) -> str:
    """``POST /reflex/rules``'s checks, word for word (routes/reflex.py
    ``add_reflex_rule``) — "" when the rule is acceptable."""
    from ..reflex.models import REFLEX_ACTIONS, REFLEX_SOURCES

    if source not in REFLEX_SOURCES:
        return f"source must be one of {REFLEX_SOURCES}"
    if action not in REFLEX_ACTIONS:
        return f"action must be one of {REFLEX_ACTIONS}"
    if source == "webhook" and not match.strip():
        return "a webhook rule needs a webhook slug in `match`"
    if action in ("workflow", "remote_agent") and not target.strip():
        return f"a '{action}' action needs a `target` (the workflow/agent name)"
    return ""


def snap_reflex(platform: Any, rule_id: str) -> dict[str, Any] | None:
    r = platform.reflex.get(rule_id)
    if r is None:
        return None
    out = {f: getattr(r, f) for f in _REFLEX_FIELDS}
    out.update(
        id=r.id,
        created_at=_iso(r.created_at),
        last_fired_at=_iso(r.last_fired_at),
        fire_count=int(r.fire_count or 0),
        last_error=r.last_error,
        last_result=r.last_result,
    )
    return out


def resolve_reflex(platform: Any, ref: str) -> str:
    """A rule's id from its id or its exact name (unique). Blocking."""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if platform.reflex.get(ref) is not None:
        return ref
    hits = [r.id for r in platform.reflex.list() if (r.name or "").strip().casefold() == ref.casefold()]
    return hits[0] if len(hits) == 1 else ""


def restore_reflex(platform: Any, rule_id: str, prior: dict[str, Any] | None) -> str:
    from ..core.db import session_scope
    from ..reflex.models import ReflexRule

    store = platform.reflex
    if prior is None:
        store.remove(rule_id)
        return f"undo: removed the rule '{rule_id}'"
    with session_scope(store.engine) as db:
        row = db.get(ReflexRule, rule_id)
        recreated = row is None
        if row is None:
            row = ReflexRule(id=rule_id)
            created = _dt(prior.get("created_at"))
            if created is not None:
                row.created_at = created
            row.last_fired_at = _dt(prior.get("last_fired_at"))
            row.fire_count = int(prior.get("fire_count") or 0)
            row.last_error = prior.get("last_error")
            row.last_result = prior.get("last_result")
        for f in _REFLEX_FIELDS:
            if f in prior:
                setattr(row, f, prior[f])
        db.add(row)
        db.commit()
    return f"undo: {'recreated' if recreated else 'restored'} the rule '{prior.get('name') or rule_id}'"


def _describe_reflex(r: dict[str, Any] | None) -> str | None:
    if r is None:
        return None
    on = f"{r.get('source')} '{r.get('match')}'" if r.get("match") else f"any {r.get('source')} signal"
    act = f"{r.get('action')} {r.get('target')}".strip()
    return f"on {on} → {act}" + ("" if r.get("enabled", True) else " (off)")


# -------------------------------------------------------- standing grants


def snap_grant(platform: Any, grant_id: str) -> dict[str, Any] | None:
    rec = platform.grants.get(grant_id)
    if rec is None:
        return None
    return {
        "id": rec.id,
        "scope_kind": rec.scope_kind,
        "scope_id": rec.scope_id,
        "tool": rec.tool,
        "args_hash": rec.args_hash,
        "label": rec.label,
        "created_by": rec.created_by,
        "created_at": _iso(rec.created_at),
        "expires_at": _iso(rec.expires_at),
        "revoked_at": _iso(rec.revoked_at),
        "uses": int(rec.uses or 0),
        "last_used_at": _iso(rec.last_used_at),
    }


def resolve_grant(platform: Any, ref: str) -> tuple[str, str]:
    """``(grant_id, problem)`` from a grant id or a tool name. A tool with
    several live grants is a problem that lists them, so the next call can
    name one. Blocking."""
    ref = (ref or "").strip()
    if not ref:
        return "", "Say which always-allow to take back: its id or its tool."
    store = platform.grants
    if store.get(ref) is not None:
        return ref, ""
    live = [g for g in store.list() if g.tool == ref]
    if len(live) == 1:
        return live[0].id, ""
    if not live:
        return "", f"There is no always-allow for {ref!r}."
    rows = "; ".join(f"{g.id} — {g.label} ({g.scope_kind})" for g in live[:10])
    return "", f"{len(live)} always-allows match {ref!r}; name one by id: {rows}"


def restore_grant(platform: Any, grant_id: str, prior: dict[str, Any] | None) -> str:
    """Put a revoked grant back as it was (un-revoke): the row's
    ``revoked_at`` returns to its prior value and the store's in-memory live
    view is re-read, so the next identical call is covered again."""
    from ..core.db import session_scope
    from ..core.grants import StandingGrantRecord

    store = platform.grants
    if prior is None:
        store.revoke(grant_id)
        return f"undo: took back the always-allow '{grant_id}'"
    with session_scope(store.engine) as db:
        row = db.get(StandingGrantRecord, grant_id)
        if row is None:
            row = StandingGrantRecord(id=grant_id)
            for f in ("scope_kind", "scope_id", "tool", "args_hash", "label", "created_by"):
                setattr(row, f, prior.get(f) or "")
            row.created_at = _dt(prior.get("created_at")) or row.created_at
            row.expires_at = _dt(prior.get("expires_at"))
            row.uses = int(prior.get("uses") or 0)
            row.last_used_at = _dt(prior.get("last_used_at"))
        row.revoked_at = _dt(prior.get("revoked_at"))
        db.add(row)
        db.commit()
    store.refresh()
    return f"undo: restored the always-allow '{prior.get('label') or grant_id}'"


# ---------------------------------------------------------------- dispatch

_SNAP = {
    "schedule": snap_schedule,
    "workflow": snap_workflow,
    "channel": snap_channel,
    "app": snap_app,
    "webhook": snap_webhook,
    "sentinel": snap_sentinel,
    "goal": snap_goal,
    "reflex": snap_reflex,
    "grant": snap_grant,
}


def restore(platform: Any, desc: dict[str, Any]) -> str:
    """``POST /undo/{id}`` for a ``record_restore`` row. Blocking."""
    record = str(desc.get("record") or "")
    name = str(desc.get("name") or "")
    prior = desc.get("prior")
    if not name:
        raise RecordError("this change has no record name")
    if record == "schedule":
        return restore_schedule(platform, name, prior)
    if record == "workflow":
        return restore_workflow(platform, name, prior, str(desc.get("now") or ""))
    if record == "channel":
        return restore_channel(platform, name, prior)
    if record == "app":
        return restore_app(platform, name, prior)
    if record == "webhook":
        return restore_webhook(platform, name, prior)
    if record == "sentinel":
        return restore_sentinel(platform, name, prior)
    if record == "goal":
        return restore_goal(platform, name, prior)
    if record == "reflex":
        return restore_reflex(platform, name, prior)
    if record == "grant":
        return restore_grant(platform, name, prior)
    raise RecordError(f"unknown record kind {record!r}")


# ------------------------------------------------------------------- tools


class _RecordTool(Tool):
    """A REVERSIBLE tool over one record: snapshot before, restore on Undo."""

    RECORD = ""
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("name") or "").strip()

    def _now(self, args: dict[str, Any]) -> str:
        return ""

    def _resolve(self, args: dict[str, Any]) -> str:
        """The record's KEY for the snapshot. BLOCKING — a tool whose
        argument may name the record by its words (a goal's text, a rule's
        name) reads the store here; run through ``asyncio.to_thread``."""
        return self._target(args)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _CHANGE_ID.set("")  # never a previous call's id on this card
        _PENDING.set(None)
        try:
            name = await asyncio.to_thread(self._resolve, args)
        except Exception:  # noqa: BLE001 — no key, no undo row
            return None
        if not name:
            return None
        try:
            prior = await asyncio.to_thread(_SNAP[self.RECORD], self.platform, name)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row
            return None
        change_id = new_id("rec")
        _CHANGE_ID.set(change_id)
        desc = {"record": self.RECORD, "name": name, "prior": prior, "change_id": change_id}
        now = self._now(args)
        if now:
            desc["now"] = now
        return {"kind": RECORD_RESTORE, "pre_inline": json.dumps(desc, default=str)}

    def _card(self, title: str, label: str, old: Any, new: Any, key: str) -> dict[str, Any]:
        return {"title": title, "key": key, "label": label, "old": old, "new": new, "change_id": _CHANGE_ID.get()}


# --- schedules


class ScheduleUpdateTool(_RecordTool):
    name = "schedule_update"
    RECORD = "schedule"
    description = (
        "Change an existing scheduled task: its timing (exactly one of `cron`, `run_at`, "
        "`interval_seconds`), its `payload`, or pause/resume it with `enabled`. The user "
        "approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "cron": {"type": "string"},
            "run_at": {"type": "string"},
            "interval_seconds": {"type": "integer", "minimum": 1},
            "payload": {"type": "object"},
            "enabled": {"type": "boolean"},
        },
        "required": ["name"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..scheduling import knobs as _knobs

        name = self._target(args)
        before = await asyncio.to_thread(snap_schedule, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no schedule called {name!r}.")
        payload = args.get("payload")
        if isinstance(payload, dict) and payload.get("script") not in (None, {}):
            return ToolResult(ok=False, error=_knobs.SCRIPT_FROM_AGENT_REFUSAL)
        try:
            if isinstance(payload, dict):
                await asyncio.to_thread(
                    _knobs.validate_knobs,
                    payload,
                    name=name,
                    kind=before["kind"],
                    scheduler=self.platform.scheduler,
                    skills=getattr(self.platform, "skills", None),
                    from_user=False,
                )
            await asyncio.to_thread(
                self.platform.scheduler.update_task,
                name,
                cron=args.get("cron"),
                run_at=args.get("run_at"),
                interval_seconds=args.get("interval_seconds"),
                payload=payload if isinstance(payload, dict) else None,
                enabled=args.get("enabled") if isinstance(args.get("enabled"), bool) else None,
            )
        except _knobs.KnobError as exc:
            return ToolResult(ok=False, error=exc.detail)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_schedule, self.platform, name)
        old, new = _describe_schedule(before), _describe_schedule(after)
        return ToolResult(
            ok=True,
            output=f"Changed the schedule '{name}': {old} → {new}.",
            data={"record_change": self._card("Schedule changed", f"Schedule “{name}”", old, new, f"schedule.{name}")},
        )


class ScheduleDeleteTool(_RecordTool):
    name = "schedule_delete"
    RECORD = "schedule"
    description = "Remove a scheduled task. The user approves on a card; Undo brings it back as it was."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_schedule, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no schedule called {name!r}.")
        await asyncio.to_thread(self.platform.scheduler.remove, name)
        return ToolResult(
            ok=True,
            output=f"Removed the schedule '{name}'.",
            data={"record_change": self._card("Schedule removed", f"Schedule “{name}”", _describe_schedule(before), None, f"schedule.{name}")},
        )


# --- workflows


class WorkflowUpdateTool(_RecordTool):
    name = "workflow_update"
    RECORD = "workflow"
    description = (
        "Change a saved workflow: rename it (`new_name`), re-describe it, or replace its "
        "`steps` ({name, agent, task} objects). The user approves on a card; the change has "
        "an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "new_name": {"type": "string"},
            "description": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["name"],
    }

    def _now(self, args: dict[str, Any]) -> str:
        return str(args.get("new_name") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        new_name = self._now(args)
        before = await asyncio.to_thread(snap_workflow, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no workflow called {name!r}.")
        if new_name and not _NAME.match(new_name):
            return ToolResult(ok=False, error="A workflow name starts with a letter and is at most 80 characters.")
        store = _wf_store(self.platform)
        try:
            if new_name and new_name != name:
                await asyncio.to_thread(store.patch, name, new_name=new_name)
            target = new_name or name
            if isinstance(args.get("steps"), list) or isinstance(args.get("description"), str):
                cur = await asyncio.to_thread(snap_workflow, self.platform, target)
                steps = args["steps"] if isinstance(args.get("steps"), list) else (cur or {}).get("steps") or []
                desc = args["description"] if isinstance(args.get("description"), str) else (cur or {}).get("description") or ""
                await asyncio.to_thread(store.save, target, steps, desc)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_workflow, self.platform, new_name or name)
        old, new = _describe_workflow(before), _describe_workflow(after)
        label = f"Workflow “{name}”" + (f" → “{new_name}”" if new_name and new_name != name else "")
        return ToolResult(
            ok=True,
            output=f"Changed the workflow '{name}'" + (f" (now '{new_name}')" if new_name and new_name != name else "") + ".",
            data={"record_change": self._card("Workflow changed", label, old, new, f"workflow.{new_name or name}")},
        )


class WorkflowDeleteTool(_RecordTool):
    name = "workflow_delete"
    RECORD = "workflow"
    description = "Remove a saved workflow. The user approves on a card; Undo brings it back with its steps."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_workflow, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no workflow called {name!r}.")
        await asyncio.to_thread(_wf_store(self.platform).remove, name)
        return ToolResult(
            ok=True,
            output=f"Removed the workflow '{name}'.",
            data={"record_change": self._card("Workflow removed", f"Workflow “{name}”", _describe_workflow(before), None, f"workflow.{name}")},
        )


class WorkflowScheduleTool(_RecordTool):
    name = "workflow_schedule"
    RECORD = "schedule"
    description = (
        "Run a saved workflow on a schedule: give the workflow's name and exactly one of "
        "`cron` (5-field crontab), `run_at` (ISO date-time, once) or `interval_seconds`. "
        "The user approves on a card; Undo removes the schedule."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "workflow": {"type": "string"},
            "schedule_name": {"type": "string", "description": "Optional; defaults to the workflow's name."},
            "cron": {"type": "string"},
            "run_at": {"type": "string"},
            "interval_seconds": {"type": "integer", "minimum": 1},
        },
        "required": ["workflow"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("schedule_name") or args.get("workflow") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        wf = str(args.get("workflow") or "").strip()
        name = self._target(args)
        if await asyncio.to_thread(snap_workflow, self.platform, wf) is None:
            return ToolResult(ok=False, error=f"There is no workflow called {wf!r}.")
        try:
            await asyncio.to_thread(
                self.platform.scheduler.add_task,
                name,
                args.get("cron"),
                run_at=args.get("run_at"),
                interval_seconds=args.get("interval_seconds"),
                kind="workflow",
                payload={"workflow": wf},
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_schedule, self.platform, name)
        return ToolResult(
            ok=True,
            output=f"Scheduled the workflow '{wf}' ({_describe_schedule(after)}).",
            data={"record_change": self._card("Workflow scheduled", f"Schedule “{name}”", None, _describe_schedule(after), f"schedule.{name}")},
        )


# --- channels


class ChannelToggleTool(_RecordTool):
    name = "channel_toggle"
    RECORD = "channel"
    description = (
        "Turn a notification channel's two-way listening on or off: `mode` 'two_way' (people "
        "on the allowlist can message Iron Jarvis), 'chat' (they can chat with it), or 'off' "
        "(send only). The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "mode": {"type": "string", "enum": ["two_way", "chat", "off"]}},
        "required": ["name", "mode"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        mode = str(args.get("mode") or "")
        before = await asyncio.to_thread(snap_channel, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no channel called {name!r}.")
        if mode not in ("two_way", "chat", "off"):
            return ToolResult(ok=False, error="mode must be two_way, chat or off")
        cfg = copy.deepcopy(before)
        cfg["inbound_enabled"] = mode in ("two_way", "chat")
        cfg["chat_enabled"] = mode == "chat"
        if cfg["inbound_enabled"] and not cfg.get("allowed_senders"):
            return ToolResult(
                ok=False,
                error=f"The channel {name!r} has no allowlist yet — add who may message it on the Notifications page first.",
            )
        await asyncio.to_thread(apply_channel, self.platform, name, cfg)
        return ToolResult(
            ok=True,
            output=f"Channel '{name}' is now {_describe_channel(cfg)}.",
            data={"record_change": self._card("Channel changed", f"Channel “{name}”", _describe_channel(before), _describe_channel(cfg), f"channel.{name}")},
        )


#: The non-secret fields each channel type takes from chat; the token comes
#: through the secure card (vault name ``channel_<name>_token``).
_CHANNEL_FIELDS = {
    "telegram": ("chat_id",),
    "slack": ("channel",),
    "discord": ("webhook_url",),
}


class ChannelConnectTool(_RecordTool):
    name = "channel_connect"
    RECORD = "channel"
    description = (
        "Add a notification channel so Iron Jarvis can message the user: `type` telegram "
        "(needs `chat_id`), slack (needs `channel`, e.g. #general) or discord (needs "
        "`webhook_url`). Never ask for a token in chat — the user pastes it into the secure "
        "card this shows. The user approves on a card; Undo removes the channel."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string", "enum": sorted(_CHANNEL_FIELDS)},
            "chat_id": {"type": "string"},
            "channel": {"type": "string"},
            "webhook_url": {"type": "string"},
        },
        "required": ["name", "type"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        ctype = str(args.get("type") or "").strip().lower()
        if not _CHANNEL_NAME.match(name):
            return ToolResult(ok=False, error="A channel name starts with a letter: letters, digits, - and _ only.")
        if ctype not in _CHANNEL_FIELDS:
            return ToolResult(ok=False, error=f"type must be one of {', '.join(sorted(_CHANNEL_FIELDS))}")
        if await asyncio.to_thread(snap_channel, self.platform, name) is not None:
            return ToolResult(ok=False, error=f"A channel called {name!r} already exists.")
        cfg: dict[str, Any] = {"type": ctype}
        for f in _CHANNEL_FIELDS[ctype]:
            v = str(args.get(f) or "").strip()
            if not v:
                return ToolResult(ok=False, error=f"{ctype} needs `{f}`.")
            cfg[f] = v
        requests: list[dict[str, Any]] = []
        if ctype in ("telegram", "slack"):
            cfg["token_secret"] = f"channel_{name}_token"
            requests.append(
                {
                    "name": f"channel.{name}",
                    "label": f"{ctype.title()} token for “{name}”",
                    "help": "Telegram: from @BotFather. Slack: the bot token (xoxb-…).",
                    "why": f"to finish connecting {ctype.title()}",
                    "request_id": new_id("seq"),
                }
            )
        await asyncio.to_thread(apply_channel, self.platform, name, cfg)
        waiting = " — paste its token into the secure card to finish" if requests else ""
        return ToolResult(
            ok=True,
            output=f"Added the {ctype} channel '{name}'{waiting}. Never ask for the token in chat.",
            data={
                "record_change": self._card("Channel added", f"Channel “{name}”", None, _describe_channel(cfg), f"channel.{name}"),
                "secret_requests": requests,
            },
        )


# --- apps


class AppConnectTool(_RecordTool):
    name = "app_connect"
    RECORD = "app"
    description = (
        "Connect an app from the Directory (\"connect my Notion\") so its tools are available: "
        "`app` is the connector id (notion, github, sentry, slack, stripe, brave_search, "
        "google_maps, postgres, filesystem, fetch, memory, …). Non-secret settings go in "
        "`values`; a token is pasted into the secure card this shows — never ask for it in "
        "chat. The user approves on a card; Undo disconnects it."
    )
    input_schema = {
        "type": "object",
        "properties": {"app": {"type": "string"}, "values": {"type": "object"}},
        "required": ["app"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("app") or "").strip().lower()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..connectors.catalog import CATALOG, get_connector
        from ..connectors.service import _secret_name

        cid = self._target(args)
        conn = get_connector(cid)
        if conn is None:
            known = ", ".join(c.id for c in CATALOG)
            return ToolResult(ok=False, error=f"There is no app called {cid!r}. Known: {known}.")
        if conn.connect_via == "oauth":
            return ToolResult(ok=False, error=f"{conn.name} signs in through the browser — connect it from the Connections page.")
        if conn.connect_via == "api_key":
            req = {
                "name": f"connection.{conn.provider}",
                "label": f"{conn.name} API key",
                "help": "",
                "why": f"to connect {conn.name}",
                "request_id": new_id("seq"),
            }
            return ToolResult(
                ok=True,
                output=f"A secure card for the {conn.name} key is shown. Never ask for it in chat.",
                data={"secret_requests": [req]},
            )
        values = args.get("values") if isinstance(args.get("values"), dict) else {}
        cfg_args = list(conn.args)
        env: dict[str, str] = {}
        env_secrets: dict[str, str] = {}
        requests: list[dict[str, Any]] = []
        for f in conn.fields:
            if f.kind == "secret":
                env_secrets[f.name] = _secret_name(conn.id, f.name)
                if not f.optional:
                    multi = sum(1 for g in conn.fields if g.kind == "secret") > 1
                    requests.append(
                        {
                            "name": f"app.{conn.id}" + (f"__{f.name}" if multi else ""),
                            "label": f.label,
                            "help": getattr(f, "help", "") or "",
                            "why": f"to connect {conn.name}",
                            "request_id": new_id("seq"),
                        }
                    )
                continue
            val = str(values.get(f.name, "")).strip()
            if not val:
                if not f.optional:
                    return ToolResult(ok=False, error=f"{conn.name} needs `{f.name}` ({f.label}) in values.")
                continue
            if f.kind == "arg":
                cfg_args = [a.replace(f"<{f.name}>", val) for a in cfg_args]
            else:
                env[f.name] = val
        cfg: dict[str, Any] = {"name": conn.id, "command": conn.command, "args": cfg_args}
        if env:
            cfg["env"] = env
        if env_secrets:
            cfg["env_secrets"] = env_secrets
        before = await asyncio.to_thread(snap_app, self.platform, conn.id)
        # A pack waiting for its token is saved now and loaded when the card
        # stores it (credentials.CredentialStore); one with no token loads now.
        loaded = await asyncio.to_thread(apply_app, self.platform, conn.id, cfg, load=not requests)
        state = "waiting for its token" if requests else (f"{loaded} tools" if loaded else "saved — loads at the next start")
        return ToolResult(
            ok=True,
            output=f"Connected {conn.name} ({state}). Never ask for a token in chat.",
            data={
                "record_change": self._card(
                    "App connected", conn.name, "connected" if before else None, state, f"app.{conn.id}"
                ),
                "secret_requests": requests,
            },
        )


# --- webhooks


class WebhookUpdateTool(_RecordTool):
    name = "webhook_update"
    RECORD = "webhook"
    description = (
        "Change a webhook (by its `slug`): pause or resume it with `enabled`; for an outbound "
        "webhook also where it sends (`target_url`) or which `event_types` it sends. The user "
        "approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "slug": {"type": "string"},
            "enabled": {"type": "boolean"},
            "target_url": {"type": "string"},
            "event_types": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slug"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("slug") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        slug = self._target(args)
        before = await asyncio.to_thread(snap_webhook, self.platform, slug)
        if before is None:
            return ToolResult(ok=False, error=f"There is no webhook called {slug!r}.")
        url = args.get("target_url")
        url = url.strip() if isinstance(url, str) and url.strip() else None
        types = args.get("event_types") if isinstance(args.get("event_types"), list) else None
        enabled = args.get("enabled") if isinstance(args.get("enabled"), bool) else None
        if url is None and types is None and enabled is None:
            return ToolResult(ok=False, error="Say what to change: enabled, target_url or event_types.")
        if before["direction"] != "outbound" and (url is not None or types is not None):
            return ToolResult(
                ok=False, error=f"{slug!r} receives calls; it has no target or event types — only `enabled` can change."
            )
        try:
            await asyncio.to_thread(
                apply_webhook_update, self.platform, before, url=url, types=types, enabled=enabled
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        after = await asyncio.to_thread(snap_webhook, self.platform, slug)
        old, new = _describe_webhook(before), _describe_webhook(after)
        return ToolResult(
            ok=True,
            output=f"Changed the webhook '{slug}': {old} → {new}.",
            data={"record_change": self._card("Webhook changed", f"Webhook “{slug}”", old, new, f"webhook.{slug}")},
        )


class WebhookDeleteTool(_RecordTool):
    name = "webhook_delete"
    RECORD = "webhook"
    description = "Remove a webhook (by its `slug`). The user approves on a card; Undo brings it back as it was."
    input_schema = {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("slug") or "").strip()

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        slug = self._target(args)
        before = await asyncio.to_thread(snap_webhook, self.platform, slug)
        if before is None:
            return ToolResult(ok=False, error=f"There is no webhook called {slug!r}.")
        await asyncio.to_thread(remove_webhook, self.platform, slug, before["direction"])
        return ToolResult(
            ok=True,
            output=f"Removed the webhook '{slug}'.",
            data={"record_change": self._card("Webhook removed", f"Webhook “{slug}”", _describe_webhook(before), None, f"webhook.{slug}")},
        )


# --- sentinels


class SentinelUpdateTool(_RecordTool):
    name = "sentinel_update"
    RECORD = "sentinel"
    description = (
        "Pause or resume a sentinel (an always-on watcher, by its `name`) with `enabled`. "
        "The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "enabled": {"type": "boolean"}},
        "required": ["name", "enabled"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_sentinel, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no sentinel called {name!r}.")
        if not isinstance(args.get("enabled"), bool):
            return ToolResult(ok=False, error="Say `enabled`: true to resume it, false to pause it.")
        await asyncio.to_thread(self.platform.sentinels.set_enabled, name, bool(args["enabled"]))
        after = await asyncio.to_thread(snap_sentinel, self.platform, name)
        old, new = _describe_sentinel(before), _describe_sentinel(after)
        return ToolResult(
            ok=True,
            output=f"Sentinel '{name}': {old} → {new}.",
            data={"record_change": self._card("Sentinel changed", f"Sentinel “{name}”", old, new, f"sentinel.{name}")},
        )


class SentinelDeleteTool(_RecordTool):
    name = "sentinel_delete"
    RECORD = "sentinel"
    description = "Remove a sentinel (by its `name`). The user approves on a card; Undo brings it back with what it had already seen."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = self._target(args)
        before = await asyncio.to_thread(snap_sentinel, self.platform, name)
        if before is None:
            return ToolResult(ok=False, error=f"There is no sentinel called {name!r}.")
        await asyncio.to_thread(self.platform.sentinels.remove, name)
        return ToolResult(
            ok=True,
            output=f"Removed the sentinel '{name}'.",
            data={"record_change": self._card("Sentinel removed", f"Sentinel “{name}”", _describe_sentinel(before), None, f"sentinel.{name}")},
        )


# --- goals


class _GoalTool(_RecordTool):
    RECORD = "goal"

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("goal") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_goal(self.platform, self._target(args))

    def _missing(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=False, error=f"There is no goal {self._target(args)!r} — goal_list shows each goal's id."
        )


class GoalUpdateTool(_GoalTool):
    name = "goal_update"
    description = (
        "Change a standing goal (`goal` = its id or its exact words): `status` (active, paused, "
        "done, abandoned), `text`, `category`, `priority` 1-5, its autonomy dial "
        "`autonomy_level` (suggest, act_low, act_all) or its budgets (`action_budget`, "
        "`spend_budget` tokens). The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "status": {"type": "string", "enum": list(GOAL_STATUSES)},
            "text": {"type": "string"},
            "category": {"type": "string"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
            "autonomy_level": {"type": "string", "enum": ["suggest", "act_low", "act_all"]},
            "action_budget": {"type": "integer", "minimum": 0},
            "spend_budget": {"type": "integer", "minimum": 0},
        },
        "required": ["goal"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..motivation.models import AUTONOMY_LEVELS

        gid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_goal, self.platform, gid) if gid else None
        if before is None:
            return self._missing(args)
        keys = ("status", "text", "category", "priority", "autonomy_level", "action_budget", "spend_budget")
        fields = {k: args[k] for k in keys if args.get(k) is not None}
        if not fields:
            return ToolResult(ok=False, error=f"Say what to change: {', '.join(keys)}.")
        if "status" in fields and fields["status"] not in GOAL_STATUSES:
            return ToolResult(ok=False, error=f"status must be one of {', '.join(GOAL_STATUSES)}")
        if "autonomy_level" in fields and fields["autonomy_level"] not in AUTONOMY_LEVELS:
            return ToolResult(ok=False, error=f"autonomy_level must be one of {', '.join(AUTONOMY_LEVELS)}")
        if "text" in fields and not str(fields["text"]).strip():
            return ToolResult(ok=False, error="A goal needs its words.")
        try:
            for k in ("priority", "action_budget", "spend_budget"):
                if k in fields:
                    fields[k] = int(fields[k])
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="priority and the budgets are whole numbers.")
        await asyncio.to_thread(self.platform.intent.update_goal, gid, **fields)
        after = await asyncio.to_thread(snap_goal, self.platform, gid)
        if after and after["text"] != before["text"]:
            old, new = f"“{before['text']}”", f"“{after['text']}”"
        else:
            old, new = _describe_goal(before), _describe_goal(after)
        return ToolResult(
            ok=True,
            output=f"Changed the goal '{before['text']}': {old} → {new}.",
            data={"record_change": self._card("Goal changed", f"Goal “{before['text']}”", old, new, f"goal.{gid}")},
        )


class GoalDeleteTool(_GoalTool):
    name = "goal_delete"
    description = (
        "Drop a standing goal (`goal` = its id or its exact words). The goal store keeps no "
        "delete, so the goal is marked ABANDONED: it stays listed and the autonomy loop never "
        "works on it again. The user approves on a card; Undo gives it back its status."
    )
    input_schema = {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        gid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_goal, self.platform, gid) if gid else None
        if before is None:
            return self._missing(args)
        if before["status"] == "abandoned":
            return ToolResult(ok=False, error=f"The goal '{before['text']}' is already abandoned.")
        await asyncio.to_thread(self.platform.intent.update_goal, gid, status="abandoned")
        after = await asyncio.to_thread(snap_goal, self.platform, gid)
        return ToolResult(
            ok=True,
            output=f"Dropped the goal '{before['text']}' (marked abandoned).",
            data={
                "record_change": self._card(
                    "Goal dropped", f"Goal “{before['text']}”", _describe_goal(before), _describe_goal(after), f"goal.{gid}"
                )
            },
        )


# --- reflex rules


class _ReflexTool(_RecordTool):
    RECORD = "reflex"

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("rule") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_reflex(self.platform, self._target(args))

    def _missing(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=False, error=f"There is no rule {self._target(args)!r} (give its id or its exact name).")


class ReflexCreateTool(_ReflexTool):
    name = "reflex_create"
    description = (
        "Make a rule that starts work when something happens: a signal from `source` "
        "(webhook, comm, email, calendar, slack) whose `match` is the webhook's slug or a "
        "keyword in the message (empty = every message of that source) starts `action` "
        "(workflow, remote_agent or session) on `target` (the workflow or agent name), with an "
        "optional `task_template` ({body}, {text}, {slug} are filled in). The user approves on "
        "a card; Undo removes the rule."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "source": {"type": "string", "enum": ["webhook", "comm", "email", "calendar", "slack"]},
            "match": {"type": "string"},
            "action": {"type": "string", "enum": ["workflow", "remote_agent", "session"]},
            "target": {"type": "string"},
            "task_template": {"type": "string"},
            "enabled": {"type": "boolean"},
            "project_id": {"type": "string"},
        },
        "required": ["source", "action"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        # The store mints the rule's id, so the capture is named after the write.
        return await capture_create_undo(self.platform, "reflex", "", pending=True)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Defaults and checks are POST /reflex/rules' (ReflexRuleBody + add_reflex_rule).
        source = str(args.get("source") or "webhook")
        action = str(args.get("action") or "workflow")
        match = str(args.get("match") or "")
        target = str(args.get("target") or "")
        problem = validate_reflex(source, match, action, target)
        if problem:
            return ToolResult(ok=False, error=problem)
        enabled = args.get("enabled") if isinstance(args.get("enabled"), bool) else True
        rule = await asyncio.to_thread(
            lambda: self.platform.reflex.add(
                name=str(args.get("name") or ""),
                source=source,
                match=match,
                action=action,
                target=target,
                task_template=str(args.get("task_template") or ""),
                enabled=enabled,
                project_id=args.get("project_id") or None,
            )
        )
        bind_created(rule.id)
        after = await asyncio.to_thread(snap_reflex, self.platform, rule.id)
        data: dict[str, Any] = {"id": rule.id, "name": rule.name}
        card = create_card("reflex", rule.id, _describe_reflex(after), label=f"Rule “{rule.name}”")
        if card:
            data["record_change"] = card
        return ToolResult(ok=True, output=f"Made the rule '{rule.name}' ({_describe_reflex(after)}).", data=data)


class ReflexUpdateTool(_ReflexTool):
    name = "reflex_update"
    description = (
        "Turn a rule (`rule` = its id or exact name) on or off with `enabled`, or ground it in "
        "a project with `project_id` (\"\" clears it). The user approves on a card; the change "
        "has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"rule": {"type": "string"}, "enabled": {"type": "boolean"}, "project_id": {"type": "string"}},
        "required": ["rule"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_reflex, self.platform, rid) if rid else None
        if before is None:
            return self._missing(args)
        enabled = args.get("enabled") if isinstance(args.get("enabled"), bool) else None
        project = args.get("project_id") if isinstance(args.get("project_id"), str) else None
        if enabled is None and project is None:
            return ToolResult(ok=False, error="Say what to change: enabled or project_id.")
        # PATCH /reflex/rules/{id}'s three intents: omitted = unchanged,
        # "" = clear the grounding, a value = re-ground.
        if enabled is not None:
            await asyncio.to_thread(self.platform.reflex.set_enabled, rid, enabled)
        if project is not None:
            await asyncio.to_thread(self.platform.reflex.set_project, rid, project)
        after = await asyncio.to_thread(snap_reflex, self.platform, rid)
        label = f"Rule “{before['name']}”"
        old, new = _describe_reflex(before), _describe_reflex(after)
        if old == new:
            old, new = before.get("project_id") or "no project", (after or {}).get("project_id") or "no project"
        return ToolResult(
            ok=True,
            output=f"Changed the rule '{before['name']}': {old} → {new}.",
            data={"record_change": self._card("Rule changed", label, old, new, f"reflex.{rid}")},
        )


class ReflexDeleteTool(_ReflexTool):
    name = "reflex_delete"
    description = "Remove a rule (`rule` = its id or exact name). The user approves on a card; Undo brings it back as it was."
    input_schema = {"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rid = await asyncio.to_thread(self._resolve, args)
        before = await asyncio.to_thread(snap_reflex, self.platform, rid) if rid else None
        if before is None:
            return self._missing(args)
        await asyncio.to_thread(self.platform.reflex.remove, rid)
        return ToolResult(
            ok=True,
            output=f"Removed the rule '{before['name']}'.",
            data={"record_change": self._card("Rule removed", f"Rule “{before['name']}”", _describe_reflex(before), None, f"reflex.{rid}")},
        )


# --- standing grants


class GrantRevokeTool(_RecordTool):
    name = "grant_revoke"
    RECORD = "grant"
    description = (
        "Take back an \"always allow\" (a standing grant): `grant` is its id, or the tool's "
        "name when only one always-allow covers that tool. From then on that call asks again. "
        "Undo puts the always-allow back."
    )
    input_schema = {"type": "object", "properties": {"grant": {"type": "string"}}, "required": ["grant"]}

    def _target(self, args: dict[str, Any]) -> str:
        return str(args.get("grant") or "").strip()

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_grant(self.platform, self._target(args))[0]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        gid, problem = await asyncio.to_thread(resolve_grant, self.platform, self._target(args))
        if problem:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_grant, self.platform, gid)
        if before is None:
            return ToolResult(ok=False, error=f"There is no always-allow {gid!r}.")
        if before.get("revoked_at"):
            return ToolResult(ok=False, error="That always-allow was already taken back.")
        await asyncio.to_thread(self.platform.grants.revoke, gid)
        label = before.get("label") or before.get("tool") or gid
        where = f"{before.get('scope_kind')} {before.get('scope_id')}".strip()
        return ToolResult(
            ok=True,
            output=f"Took back the always-allow: {label}. It asks again from now on.",
            data={
                "record_change": self._card(
                    "Always-allow taken back", label, f"runs without asking ({where})", "asks first", f"grant.{gid}"
                )
            },
        )


# --- "undo that"


def ledger_tools() -> frozenset[str]:
    """The tools the configuration ledger lists — the SAME set as ``GET
    /config/ledger`` (routes/settings.py ``config_ledger``)."""
    return frozenset(
        {"update_settings", "set_secret", "config_set", "config_change", "config_change_protected", *RECORD_CARD_TOOLS}
    )


def find_undo_target(engine: Any, action_id: str = "") -> dict[str, Any] | None:
    """The configuration change ``config_undo`` reverses. Blocking.

    With an id: that ledger row (whatever state it is in — ``perform_undo``
    gives the exact refusal for one already undone or without an inverse),
    or None when it is not a configuration change. Without: the NEWEST row
    the ledger would offer an Undo for — ok, not itself an undo, not undone,
    with a reversible inverse, from a tool that did not declare itself
    irreversible."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal

    tools = ledger_tools()
    with session_scope(engine) as db:
        if action_id:
            inv = db.get(ToolInvocation, action_id)
            if inv is None or inv.tool not in tools or inv.undo_of is not None or not inv.ok:
                return None
            return {"action_id": inv.id, "tool": inv.tool, "summary": (inv.output or "")[:200]}
        rows = db.exec(
            select(ToolInvocation, UndoJournal)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(ToolInvocation.tool.in_(tools))  # type: ignore[attr-defined]
            .where(ToolInvocation.ok == True)  # noqa: E712
            .where(ToolInvocation.undo_of == None)  # noqa: E711
            .where(ToolInvocation.undone_at == None)  # noqa: E711
            .where(UndoJournal.reversible == True)  # noqa: E712
            .order_by(ToolInvocation.created_at.desc())  # type: ignore[attr-defined]
            .limit(50)
        ).all()
        for inv, _j in rows:
            if (inv.reversibility or "").lower() == Reversibility.IRREVERSIBLE.value:
                continue
            return {"action_id": inv.id, "tool": inv.tool, "summary": (inv.output or "")[:200]}
    return None


class ConfigUndoTool(Tool):
    """"undo that" from chat: reverse a configuration change through THE code
    ``POST /undo/{id}`` runs (``routes.undo.perform_undo``), so chat and the
    Undo button cannot disagree about what an undo does. Not itself undoable
    (the undo row is IRREVERSIBLE, exactly as the route writes it), so it
    carries no card — the reply says what was undone."""

    name = "config_undo"
    description = (
        "Undo a configuration change — \"undo that\". With no `action_id` it undoes the "
        "newest change that can still be undone (a setting, a credential, a schedule, "
        "workflow, channel, app, webhook, sentinel, goal, rule or always-allow — changed in "
        "chat or on a page). The user approves on a card. Tell the user what was undone."
    )
    input_schema = {"type": "object", "properties": {"action_id": {"type": "string"}}}

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.undo import perform_undo

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error="Undo from chat needs the running app.")
        aid = str(args.get("action_id") or "").strip()
        target = await asyncio.to_thread(find_undo_target, self.platform.engine, aid)
        if target is None:
            return ToolResult(
                ok=False,
                error=(
                    f"There is no configuration change {aid!r} to undo."
                    if aid
                    else "There is no recent change that can still be undone."
                ),
            )
        try:
            out = await perform_undo(d, target["action_id"])
        except HTTPException as exc:
            return ToolResult(ok=False, error=str(exc.detail))
        summary = target["summary"] or target["tool"]
        return ToolResult(
            ok=True,
            output=f"Undone: {summary}",
            data={
                "undone": target["action_id"],
                "tool": target["tool"],
                "summary": summary,
                "undo_invocation_id": out.get("undo_invocation_id"),
            },
        )


def record_tools(platform: Any) -> list[Tool]:
    return [
        ScheduleUpdateTool(platform),
        ScheduleDeleteTool(platform),
        WorkflowUpdateTool(platform),
        WorkflowDeleteTool(platform),
        WorkflowScheduleTool(platform),
        ChannelToggleTool(platform),
        ChannelConnectTool(platform),
        AppConnectTool(platform),
        WebhookUpdateTool(platform),
        WebhookDeleteTool(platform),
        SentinelUpdateTool(platform),
        SentinelDeleteTool(platform),
        GoalUpdateTool(platform),
        GoalDeleteTool(platform),
        ReflexCreateTool(platform),
        ReflexUpdateTool(platform),
        ReflexDeleteTool(platform),
        GrantRevokeTool(platform),
        ConfigUndoTool(platform),
    ]


# ----------------------------------------------- undo for the create tools


async def capture_create_undo(
    platform: Any, record: str, name: str, *, pending: bool = False
) -> dict[str, Any] | None:
    """A create tool gains an Undo: the same snapshot (None for a brand-new
    record, so Undo removes it; the prior version for one re-saved in place —
    a workflow saved again, a webhook slug registered again).

    ``pending=True`` is for a record whose id the STORE mints (a goal, a
    reflex rule): nothing exists to snapshot yet, so the descriptor is
    captured with no name and the tool names it after the write with
    :func:`bind_created`. The registry journals the descriptor only after a
    SUCCESSFUL execute (the same post-execute fill as ``finalize_post_hash``),
    so a failed create leaves no row."""
    _CHANGE_ID.set("")
    _PENDING.set(None)
    if pending:
        change_id = new_id("rec")
        _CHANGE_ID.set(change_id)
        desc = {
            "kind": RECORD_RESTORE,
            "pre_inline": json.dumps({"record": record, "name": "", "prior": None, "change_id": change_id}),
        }
        _PENDING.set(desc)
        return desc
    if not name:
        return None
    try:
        prior = await asyncio.to_thread(_SNAP[record], platform, name)
    except Exception:  # noqa: BLE001
        return None
    change_id = new_id("rec")
    _CHANGE_ID.set(change_id)
    return {
        "kind": RECORD_RESTORE,
        "pre_inline": json.dumps({"record": record, "name": name, "prior": prior, "change_id": change_id}, default=str),
    }


def bind_created(name: str) -> None:
    """Name the record a ``pending`` capture is about, once the store minted
    it. A no-op when this call captured nothing (a direct ``execute``)."""
    desc = _PENDING.get()
    if not desc or not name:
        return
    try:
        meta = json.loads(desc.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        return
    meta["name"] = str(name)
    desc["pre_inline"] = json.dumps(meta, default=str)
    _PENDING.set(None)


_CREATE_WORDS = {
    "schedule": "Schedule",
    "workflow": "Workflow",
    "webhook": "Webhook",
    "sentinel": "Sentinel",
    "goal": "Goal",
    "reflex": "Rule",
}


def create_card(
    record: str, name: str, new: str | None, *, existed: bool = False, label: str = ""
) -> dict[str, Any] | None:
    """The reply's card for a create — only when this call captured an Undo
    (no ledger row, no card: a direct ``execute`` keeps its old result)."""
    if not _CHANGE_ID.get():
        return None
    word = _CREATE_WORDS.get(record, record.title())
    return {
        "title": f"{word} {'updated' if existed else 'created'}",
        "key": f"{record}.{name}",
        "label": label or f"{word} “{name}”",
        "old": None,
        "new": new,
        "change_id": _CHANGE_ID.get(),
    }
