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
}
#: Tools whose successful result carries a ``record_change`` (the S5 tools
#: plus the two create tools that gained an Undo).
RECORD_CARD_TOOLS = frozenset({*RECORD_TOOL_TIERS, "schedule_create", "workflow_create"})

_CHANGE_ID: ContextVar[str] = ContextVar("ij_record_change_id", default="")
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


def record_tools_for(text: str) -> list[str]:
    """The record tools a chat message plausibly asks for, by the record it
    names (chat-only arming, like ``settings.tools.wants_settings``). Every
    one is ask tier, so the stream lane arms them VISIBLE, never granted."""
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


# ---------------------------------------------------------------- dispatch

_SNAP = {
    "schedule": snap_schedule,
    "workflow": snap_workflow,
    "channel": snap_channel,
    "app": snap_app,
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

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _CHANGE_ID.set("")  # never a previous call's id on this card
        name = self._target(args)
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
    ]


# ----------------------------------------------- undo for the create tools


async def capture_create_undo(platform: Any, record: str, name: str) -> dict[str, Any] | None:
    """``schedule_create`` / ``workflow_create`` gain an Undo: the same
    snapshot (None for a brand-new record, so Undo removes it; the prior
    version for a workflow re-saved in place)."""
    _CHANGE_ID.set("")
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


def create_card(record: str, name: str, new: str | None, *, existed: bool = False) -> dict[str, Any] | None:
    """The reply's card for a create — only when this call captured an Undo
    (no ledger row, no card: a direct ``execute`` keeps its old result)."""
    if not _CHANGE_ID.get():
        return None
    word = "Schedule" if record == "schedule" else "Workflow"
    return {
        "title": f"{word} {'updated' if existed else 'created'}",
        "key": f"{record}.{name}",
        "label": f"{word} “{name}”",
        "old": None,
        "new": new,
        "change_id": _CHANGE_ID.get(),
    }
