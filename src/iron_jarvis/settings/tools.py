"""Chat's settings tools, generated from the ONE schema (calm UI redesign S3).

Every setting the Settings page shows can be changed in chat, through the
EXISTING permission gate, with a ledger row and an Undo — no parallel path:

* ``config_list``  — read: find settings by words, see their current values
  and which tool changes each (allow; nothing changes).
* ``config_set``   — change a setting whose tier is ``allow`` (theme, default
  model, persona, profile fields…). Applied at once; the reply carries a
  "Setting changed: old → new  [Undo]" card.
* ``config_change`` — change an ``ask`` setting. The tool is ask-tier, so the
  chat's ordinary ApprovalCard asks first; a standing "always" grant may lift
  it later, like any ask tool.
* ``config_change_protected`` — change an ``ask-floor`` setting (autonomy,
  sandbox, self-development, per-tool permissions, widening trust or the
  browser, turning a safety OFF). Ask-tier AND on the deny floor: never
  raised to allow by an agent definition, never offered "Always", carded
  even in Auto-approve, refused under low trust.

The tier is per setting AND per value (``SettingDef.tier_for``): the tool a
call came through must match, or the call is refused with the right tool's
name — so a protected change can never ride the allow tool.

Every change goes through ``settings.writer.ConfigWriter`` (validation, side
effects, persistence). The tools are REVERSIBLE: ``capture_undo`` snapshots
the prior value into a ``setting_restore`` journal row the registry writes for
this very call, and ``POST /undo/{id}`` restores it through the same writer.
A ``change_id`` minted at capture rides the result so the card can find its
Undo (``GET /config/changes/{change_id}``).

Secrets are never settings: ``config_secret`` asks for a credential CARD
(settings.credentials) — the value never passes through the model.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextvars import ContextVar
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import schema

TOOL_FOR_TIER = {"allow": "config_set", "ask": "config_change", "ask-floor": "config_change_protected"}
CONFIG_TOOLS = ("config_list", "config_set", "config_change", "config_change_protected", "config_secret")
#: Ask-tier settings tools: visible to the model, never granted up front.
ASK_CONFIG_TOOLS = ("config_change", "config_change_protected")
#: Carded even when the chat's approval posture is Auto-approve, and never
#: offered "Always" (AUDIT §6 ask-floor).
ALWAYS_CARD_TOOLS = frozenset({"config_change_protected"})

_CHANGE_ID: ContextVar[str] = ContextVar("ij_config_change_id", default="")

_SETTINGS_WORDS = re.compile(
    r"\b(?:settings?|preferences?|turn (?:on|off)|switch (?:on|off|to)|enable|disable|"
    r"set (?:my|the|it)|change (?:my|the) (?:default|theme|model|persona)|default model|"
    r"use (?:the )?(?:local|cloud)|theme|dark mode|light mode|kill switch|emergency stop|"
    r"autonomy|sentinels?|watchers?|approvals?|notifications?|telegram|slack|iron.?proxy|"
    r"connect (?:my|to)|api key|token|answer length|shorter answers|undo (?:that|the) setting)\b",
    re.IGNORECASE,
)


def wants_settings(text: str) -> bool:
    """Does this message plausibly ask to see or change a setting?"""
    return bool(_SETTINGS_WORDS.search(text or ""))


# ------------------------------------------------------------------ helpers


def _writer(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    if w is None:
        raise RuntimeError("settings can only be changed while the Iron Jarvis app is running")
    return w


#: v1.352.0 (G24): the approval levels' names before they became the chat
#: chip's three ("Ask first" / "Ask when risky" / "Don't ask"). A person (or a
#: model reading the Handbook of an older version) may still type them; they
#: keep meaning the same level. Per key, lower-case words -> option value.
_RETIRED_OPTION_WORDS: dict[str, dict[str, str]] = {
    "device.approval_mode": {
        "ask for approval": "always_ask",
        "always ask": "always_ask",
        "approve for me": "approve_for_me",
        "auto-approve": "yolo",
        "auto approve": "yolo",
        "auto-approve (yolo)": "yolo",
    },
}


def _coerce(d: schema.SettingDef, value: Any) -> Any:
    """Models send strings; turn them into the setting's type."""
    if d.type == "bool" and isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "on", "yes", "1", "enabled", "enable"):
            return True
        if v in ("false", "off", "no", "0", "disabled", "disable"):
            return False
    if d.type == "number" and isinstance(value, str):
        try:
            f = float(value.strip())
            return int(f) if f.is_integer() else f
        except ValueError:
            return value
    if d.type == "list" and isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if d.type == "dict" and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return parsed
    if d.type == "enum" and isinstance(value, str):
        for v, label in d.options:
            if value.strip().lower() in (str(v).lower(), label.lower()):
                return v
        retired = _RETIRED_OPTION_WORDS.get(d.key, {}).get(value.strip().lower())
        if retired is not None:
            return retired
    return value


def _shown_value(platform: Any, key: str, value: Any) -> Any:
    """The value as the approval card (and the ledger row) shows it.
    v1.393.0 review: ``account_groups`` is one line naming each account
    ("Work: Work Max (Claude Code), Spare (Claude Code); Home: …") instead of
    Iron-Proxy's opaque ids in a nested value the card folds away — which
    accounts end up in one group is the user's whole privacy decision on
    that card. Read from the CACHED snapshot (never a call); an id Iron-Proxy
    does not list stays as it is. Every other setting is shown exactly as
    sent. Never raises."""
    if key != "account_groups":
        return value
    try:
        from ..harness import build_accounts as BA

        groups = _coerce(schema.get(key), value)
        if not isinstance(groups, dict):
            return value
        if not groups:
            return "no groups"
        svc = getattr(platform, "iron_proxy", None)
        parts: list[str] = []
        for name, ids in groups.items():
            if not isinstance(ids, list):
                return value
            shown: list[str] = []
            for raw in ids:
                row = BA.find(svc, str(raw)) if isinstance(raw, str) else None
                shown.append(f"{row['name']} ({row['vendor_label']})" if row else str(raw))
            parts.append(f"{name}: {', '.join(shown)}")
        return "; ".join(parts)
    except Exception:  # noqa: BLE001 — the words never break a card
        return value


def _show(value: Any) -> str:
    if value is None or value == "":
        return "(not set)"
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


# --------------------------------------------------------------------- list


class ConfigListTool(Tool):
    name = "config_list"
    description = (
        "Find Iron Jarvis settings by words and see their current values, and which tool "
        "changes each one. Call this first when the user asks to change a setting and you "
        "are not sure of its key. Read-only."
    )
    # v1.341.0: this was `parameters =` since v1.321.0 — a name `Tool.spec()`
    # never reads — so every model was shown `input_schema: {}` and had to
    # guess the arguments. The registry-wide pin in
    # tests/test_tool_schemas_v1341.py catches the next one.
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to look for (label, key or alias)."},
            "group": {"type": "string", "description": "Optional group id: " + ", ".join(schema.GROUP_IDS)},
        },
    }
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        q = str(args.get("query") or "").strip().lower()
        group = str(args.get("group") or "").strip()
        words = [w for w in re.split(r"\W+", q) if len(w) > 2]
        rows: list[dict[str, Any]] = []
        w = getattr(self.platform, "config_writer", None)

        def _hay(d: schema.SettingDef) -> str:
            return " ".join([d.key, d.label, *d.aliases]).lower()

        pool = [d for d in schema.SETTINGS if not group or d.group == group]
        if words:
            # Every word in the label/key/aliases first; else any word there;
            # else any word in the help text.
            ranked = [d for d in pool if all(word in _hay(d) for word in words)]
            if not ranked:
                ranked = [d for d in pool if any(word in _hay(d) for word in words)]
            if not ranked:
                ranked = [d for d in pool if any(word in d.help.lower() for word in words)]
            pool = ranked
        for d in pool:
            current: Any = None
            if w is not None and not d.pattern:
                try:
                    current = await asyncio.to_thread(w.current, d.key, device_id=ctx.device_id)
                except Exception:  # noqa: BLE001 — a value we cannot read is shown as unknown
                    current = None
            rows.append(
                {
                    "key": d.key,
                    "label": d.label,
                    "group": d.group,
                    "type": d.type,
                    "options": [v for v, _ in d.options],
                    "value": current,
                    "tool": TOOL_FOR_TIER[d.tier],
                    "protected_values": list(d.floor_when),
                }
            )
            if len(rows) >= 25:
                break
        secrets = [
            {"key": s.name, "label": s.label, "tool": "config_secret"}
            for s in schema.SECRETS
            if not words or any(word in " ".join([s.name, s.label, *s.aliases]).lower() for word in words)
        ]
        lines = [f"{r['label']} ({r['key']}) = {_show(r['value'])} → {r['tool']}" for r in rows]
        lines += [f"{s['label']} ({s['key']}) → config_secret (a secure card; never ask for the value)" for s in secrets]
        return ToolResult(
            ok=True,
            output="\n".join(lines) if lines else "No setting matches those words.",
            data={"settings": rows, "secrets": secrets},
        )


# ---------------------------------------------------------------------- set


class _ConfigChangeBase(Tool):
    TIER = "allow"
    input_schema = {  # v1.341.0: was `parameters` (never read) — see ConfigListTool
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "The setting key from config_list (e.g. default_model)."},
            "value": {"description": "The new value (true/false, a number, text, or one of the options)."},
        },
        "required": ["key", "value"],
    }
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def _resolve(self, args: dict[str, Any]) -> tuple[schema.SettingDef, str, Any]:
        key = str(args.get("key") or "").strip()
        d = schema.get(key)
        return d, key, _coerce(d, args.get("value"))

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("key") or "")
        return {"key": key, "value": _shown_value(self.platform, key, args.get("value"))}

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        try:
            d, key, _value = self._resolve(args)
            prior = await asyncio.to_thread(_writer(self.platform).current, key, device_id=ctx.device_id)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row; execute says why
            return None
        change_id = new_id("cfg")
        _CHANGE_ID.set(change_id)
        return {
            "kind": "setting_restore",
            "pre_inline": json.dumps(
                {"prior": {key: prior}, "device_id": ctx.device_id, "change_id": change_id}, default=str
            ),
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .writer import SettingError

        try:
            d, key, value = self._resolve(args)
        except KeyError:
            return ToolResult(ok=False, output="", error=f"There is no setting {args.get('key')!r}. Call config_list.")
        tier = d.tier_for(value)
        if tier != self.TIER:
            right = TOOL_FOR_TIER[tier]
            return ToolResult(
                ok=False,
                output="",
                error=f"{d.label} needs {right} (it {'always asks first' if tier == 'ask-floor' else 'asks first'}). Call {right} with the same key and value.",
            )
        if d.store == "device" and not ctx.device_id:
            return ToolResult(ok=False, output="", error=f"{d.label} is set per device; open this chat in the app to change it.")
        try:
            writer = _writer(self.platform)
            change = await asyncio.to_thread(
                writer.apply, {key: value}, actor="chat", device_id=ctx.device_id, journal=False
            )
        except SettingError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        except RuntimeError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        change_id = _CHANGE_ID.get()
        if not change.changed:
            return ToolResult(ok=True, output=f"{d.label} was already {_show(value)}.", data={"config_change": None})
        c = change.changed[0]
        restart = " It takes full effect after Iron Jarvis restarts." if d.restart else ""
        return ToolResult(
            ok=True,
            output=f"Changed {c['label']}: {_show(c['old'])} → {_show(c['new'])}.{restart}",
            data={
                "config_change": {
                    "key": key,
                    "label": c["label"],
                    "old": c["old"],
                    "new": c["new"],
                    "restart": d.restart,
                    "change_id": change_id,
                }
            },
        )


class ConfigSetTool(_ConfigChangeBase):
    name = "config_set"
    TIER = "allow"
    description = (
        "Change an Iron Jarvis setting that is safe to apply at once (config_list says which "
        "tool each setting needs). The reply shows the change with an Undo."
    )


class ConfigChangeTool(_ConfigChangeBase):
    name = "config_change"
    TIER = "ask"
    description = (
        "Change an Iron Jarvis setting that asks the user first (config_list says which tool "
        "each setting needs). The user approves on a card; the change has an Undo."
    )


class ConfigChangeProtectedTool(_ConfigChangeBase):
    name = "config_change_protected"
    TIER = "ask-floor"
    description = (
        "Change a PROTECTED Iron Jarvis setting (autonomy, sandbox, self-development, tool "
        "permissions, or turning a safety off). The user approves every time on a card."
    )


def config_tools(platform: Any) -> list[Tool]:
    from .credentials import ConfigSecretTool

    return [
        ConfigListTool(platform),
        ConfigSetTool(platform),
        ConfigChangeTool(platform),
        ConfigChangeProtectedTool(platform),
        ConfigSecretTool(platform),
    ]
