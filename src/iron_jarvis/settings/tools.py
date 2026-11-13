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
from .device import valid_device_id
#: v1.442.0 (Wave 7 group C): how chat writes ``device.theme_colors`` (the
#: value has a shape; config_list tells the model what it is), and its words.
from .theme_colors import HOW as THEME_COLORS_HOW
from .theme_colors import describe as describe_theme_colors

TOOL_FOR_TIER = {"allow": "config_set", "ask": "config_change", "ask-floor": "config_change_protected"}
CONFIG_TOOLS = ("config_list", "config_set", "config_change", "config_change_protected", "config_secret")
#: Ask-tier settings tools: visible to the model, never granted up front.
ASK_CONFIG_TOOLS = ("config_change", "config_change_protected")
#: Carded even when the chat's approval posture is Auto-approve, and never
#: offered "Always" (AUDIT §6 ask-floor).
ALWAYS_CARD_TOOLS = frozenset({"config_change_protected"})
#: v1.438.0 (Wave 4): how chat changes ``account_groups`` — the value is
#: MERGED into the user's groups (``records_connections.merged_account_groups``),
#: so the model is told how to remove one; leaving a group out keeps it.
ACCOUNT_GROUPS_HOW = (
    " (send only the groups to change, by account name: a group you name is set to that list, "
    "an empty list [] removes that group, groups you leave out are kept)"
)

_CHANGE_ID: ContextVar[str] = ContextVar("ij_config_change_id", default="")


def wants_settings(text: str) -> bool:
    """Does this message plausibly ask to see or change a setting?

    v1.434.0 (chat-first plan, Wave 2 group E, B1.1): read off the schema's
    OWN words — every row's label, aliases and option names — by
    ``settings.trigger``, which replaced the one hand-kept regex that left 29
    of 57 measured requests unarmed. True means the reader ``config_list`` is
    armed; whether a WRITER is (a change was asked for, not a question) is
    ``settings.trigger.settings_ask(text).writers``."""
    from .trigger import settings_ask

    return settings_ask(text).reader


# ------------------------------------------------------------------ helpers


def _profile_on(platform: Any) -> bool:
    """Is "Use my profile" on? Blocking; an unreadable profile reads on (the
    note is only ever extra words)."""
    try:
        from ..profile.store import ProfileStore

        return bool(getattr(ProfileStore(platform.engine).get(), "enabled", True))
    except Exception:  # noqa: BLE001
        return True


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
        # v1.406.0 part 2: the levels were renamed for the five-level switch
        # ("Ask first" now names the default, which "Ask when risky" did).
        "ask when risky": "approve_for_me",
        "ask before everything": "always_ask",
        "accept edits": "accept_edits",
        "auto mode": "auto",
        # v1.420.0 (C1.8, D3): named by what they do; the v1.406.0 words keep
        # their level ("Ask first" is the default, "Auto" is Auto).
        "ask first": "approve_for_me",
        "auto": "auto",
        # v1.430.0: the v1.420.0 name, before the menu showed names only.
        "auto (its own safety check)": "auto",
        # v1.420.0 review: the chip's TOOLBAR words (lib/permissionLevels
        # `chip`) are what the user sees and types; they are not option
        # labels, so they live here (pinned:
        # dashboard/__tests__/one-vocabulary-review-v1420.test.tsx).
        "ask every time": "always_ask",
        "ask for commands": "approve_for_me",
    },
}


BAD_DEVICE_REFUSAL = (
    "{label} is set per screen, and this chat named a screen Iron Jarvis does not know, "
    "so nothing was changed. Ask again from a chat in the app."
)


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
        # v1.438.0 review: the card shows what will be SAVED — the user's
        # other groups kept, the named accounts moved (records_connections.
        # chat_account_groups, the same function execute uses).
        from .records_connections import chat_account_groups

        merged, _problem = chat_account_groups(platform, groups)
        if isinstance(merged, dict):
            groups = merged
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
    if isinstance(value, dict) and "accent" in value:
        # v1.442.0 (Wave 7 group C): a theme of your own colours, in words.
        return describe_theme_colors(value)
    return str(value)


#: v1.455.0 (Wave E): the ``default_model`` value that means "whatever the
#: plan's own CLI uses" (``routes/settings.promote_model`` saves
#: "subscription"), and the words a card shows for it.
_PLAN_DEFAULT_IDS = frozenset({"subscription"})
PLAN_DEFAULT_MODEL_WORDS = "Your plan's own default model"
#: Review: "default" is ALSO what the custom server and a fleet node are sent
#: when no model is named (``ProviderManager``, ``fleet/adapter``), so it is
#: never called the plan's: the card says the service's own default.
OWN_DEFAULT_MODEL_WORDS = "Its own default model"


def value_label(d: Any, value: Any, manager: Any = None) -> str | None:
    """v1.448.0 (Wave A, A3): the DISPLAY NAME of *value* for the schema row
    *d* — what the Settings page shows for it — or ``None`` when the row has
    none to give (free text, a number, a switch: the card's own words serve).

    * a row with ``options``: the option's label (``"approve_for_me"`` →
      "Ask before commands", ``"mark1"`` → "Daylight");
    * ``default_provider``: the AI service's plain name
      (``providers.failure_words.plain_name``, the twin of the dashboard's
      ``providerDisplay`` — "claude-cli" → "Claude Code"; a fleet node reads
      as the label the user gave it when *manager* — the platform's
      ``providers`` — is handed over, else as its id);
    * a theme of the user's own colours: its words (``describe_theme_colors``);
    * any other dict: its keys, joined — never a JSON dump on a card.

    Never raises."""
    try:
        if value is None or value == "" or isinstance(value, bool):
            return None
        if isinstance(value, dict):
            if "accent" in value:
                return describe_theme_colors(value)
            keys = [str(k) for k in value]
            return ", ".join(keys) if keys else None
        key = str(getattr(d, "key", "") or "")
        options = tuple(getattr(d, "options", ()) or ())
        wanted = str(value).strip().lower()
        for v, label in options:
            if label and wanted == str(v).strip().lower():
                return str(label)
        if key == "default_provider":
            from ..providers.failure_words import plain_name

            name = plain_name(str(value), manager)
            return name or None
        if key == "default_model" and wanted in _PLAN_DEFAULT_IDS:
            # v1.455.0 (Wave E): "subscription" is the wire's word for the
            # plan's own default model; a card says it in words.
            return PLAN_DEFAULT_MODEL_WORDS
        if key == "default_model" and wanted == "default":
            return OWN_DEFAULT_MODEL_WORDS
        return None
    except Exception:  # noqa: BLE001 — a label never breaks a change
        return None


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
                # v1.442.0 (Wave 7 live run): ranked by how many of the words
                # are in the name (worth more) and in the help, so "test runs"
                # finds the approvals level whose help names test runs, not
                # only rows whose name says "runs".
                def _score(d: schema.SettingDef) -> int:
                    hay, help_text = _hay(d), d.help.lower()
                    return sum(3 * (word in hay) + (word in help_text) for word in words)

                scored = sorted(((_score(d), i, d) for i, d in enumerate(pool)), key=lambda t: (-t[0], t[1]))
                ranked = [d for s, _i, d in scored if s > 0]
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
        lines = [
            f"{r['label']} ({r['key']}) = {_show(r['value'])} → {r['tool']}"
            + _what_it_does(r["key"])
            + (ACCOUNT_GROUPS_HOW if r["key"] == "account_groups" else "")
            + (THEME_COLORS_HOW if r["key"] == "device.theme_colors" else "")
            + (_model_choices(self.platform) if r["key"] == "default_model" else "")
            for r in rows
        ]
        lines += [f"{s['label']} ({s['key']}) → config_secret (a secure card; never ask for the value)" for s in secrets]
        return ToolResult(
            ok=True,
            output="\n".join(lines) if lines else "No setting matches those words.",
            data={"settings": rows, "secrets": secrets},
        )


#: How many models the default-model line names (each "provider model (name)").
MODEL_CHOICES_MAX = 30
#: v1.489.0: a default model picked on the card names its AI service too:
#: "provider::model" (the dashboard's own row id shape).
PICKED_SEP = "::"


def _model_rows(platform: Any) -> list[tuple[str, str, str]]:
    """``(provider, model, label)`` for every model that can be chosen now,
    from the model catalog's KEPT answer only (never a build: no endpoint is
    asked and nothing starts here). The demo model and rows that are not
    available are left out. Never raises; not kept yet = []."""
    try:
        cat = getattr(platform, "_model_catalog", None)
        entry = getattr(cat, "_entry", None) if cat is not None else None
        rows = list(getattr(entry, "rows", None) or [])
    except Exception:  # noqa: BLE001 — not readable: nothing to offer
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("available"):
            continue
        prov, mid = str(row.get("provider") or ""), str(row.get("model") or "")
        if not prov or not mid or prov == "mock" or (prov, mid) in seen:
            continue
        seen.add((prov, mid))
        out.append((prov, mid, str(row.get("label") or "")))
    return out


def _model_choice_name(provider: str, model: str, label: str) -> str:
    """One model on the pick card: its name and the AI service it is on."""
    try:
        from ..providers.failure_words import plain_name

        service = plain_name(provider)
    except Exception:  # noqa: BLE001 — the id still says which
        service = provider
    return f"{label or model} ({service})"


def _model_choices(platform: Any) -> str:
    """v1.484.1: the models that can be chosen, for the default-model line.

    Seen live: "switch my default to the fast model" made the model read
    ``config_list``, which showed the current value and nothing else, so it
    asked the user which model they meant. This names what the pickers offer,
    from the model catalog's KEPT answer only (never a build: no endpoint is
    asked and nothing starts here). Not kept yet, or unreadable: "" (the line
    is as before). Names only, never a key."""
    names = [
        f"{prov} {mid}" + (f" ({label})" if label and label != mid else "")
        for prov, mid, label in _model_rows(platform)
    ]
    if not names:
        return ""
    more = len(names) - MODEL_CHOICES_MAX
    shown = "; ".join(names[:MODEL_CHOICES_MAX]) + (f"; and {more} more" if more > 0 else "")
    return (
        " Models that can be chosen now (set default_provider to the first word and "
        f"default_model to the second): {shown}."
    )


#: v1.442.0 (Wave 7 live run): a row's help and its choices' NAMES, so the
#: model knows what a setting does — with key and value alone, Claude read
#: ``config_list`` twice for "stop asking me before you run tests" and never
#: saw that Auto runs test commands without asking. Bounded per row.
_HELP_CHARS = 400


def _what_it_does(key: str) -> str:
    try:
        d = schema.get(key)
    except Exception:  # noqa: BLE001 — a row we cannot describe is shown bare
        return ""
    if d is None:
        return ""
    out = ""
    help_text = " ".join(str(d.help or "").split())
    if help_text:
        out += " — " + (help_text if len(help_text) <= _HELP_CHARS else help_text[: _HELP_CHARS - 1] + "…")
    names = [f"{v} = {label}" for v, label in d.options if label and label != v]
    if names:
        out += " Choices: " + "; ".join(names) + "."
    return out


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
    # v1.489.0: a setting with FIXED CHOICES asked for without a clear value
    # ("can the app look different, whatever is easiest on the eyes") is put
    # to the user as a card of its choices, instead of a list in words and a
    # second message. Only such settings ask (`missing_details`); any other
    # setting called without a value gets the registry's plain sentence.
    user_details = {"value": {"title": "Which one?", "what": "which value to use"}}
    user_details_lead = "Pick the one you want."
    user_details_note = (
        "When it is NOT clear which value the user wants for a setting with fixed choices "
        "(config_list names them) or for default_model, call this with the key and WITHOUT "
        "`value` (do not list the choices in words, and never guess): the user picks one "
        "on a card."
    )

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def missing_details(self, args: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, dict[str, Any]]:
        given = args if isinstance(args, dict) else {}
        value = given.get("value")
        if not (value is None or (isinstance(value, str) and not value.strip())):
            return {}
        try:
            d = schema.get(str(given.get("key") or "").strip())
        except Exception:  # noqa: BLE001 — an unknown key: execute says so
            return {}
        if d is None:
            return {}
        if d.key == "default_model":
            # The models this install can use now (the catalog's KEPT answer:
            # nothing is asked and nothing starts here). A pick names the AI
            # service too ("provider::model"); `_writes` reads it.
            choices = [
                (f"{prov}{PICKED_SEP}{mid}", _model_choice_name(prov, mid, label))
                for prov, mid, label in _model_rows(self.platform)[:MODEL_CHOICES_MAX]
            ]
        else:
            choices = [(str(v), str(label or v)) for v, label in (d.options or ()) if isinstance(v, str) and v]
        if len(choices) < 2:
            return {}
        help_text = " ".join(str(d.help or "").split())
        return {
            "value": {
                "title": str(d.label),
                "description": help_text if len(help_text) <= _HELP_CHARS else help_text[: _HELP_CHARS - 1] + "…",
                "enum": [v for v, _ in choices],
                "enumNames": [label for _, label in choices],
                "what": f"which choice to use for {d.label}",
            }
        }

    def _resolve(self, args: dict[str, Any]) -> tuple[schema.SettingDef, str, Any]:
        key = str(args.get("key") or "").strip()
        d = schema.get(key)
        return d, key, _coerce(d, args.get("value"))

    def _writes(self, key: str, value: Any, device_id: str = "") -> dict[str, Any]:
        """The settings ONE call writes. Always ``{key: value}``, except a
        default model given WITH its AI service ("provider::model", what the
        pick card answers): that sets the model, and the service too when it
        is not the one in use — so "the good one" picked on the card really
        answers. BLOCKING (one settings read)."""
        if key == "default_model" and isinstance(value, str) and PICKED_SEP in value:
            provider, _, model = value.partition(PICKED_SEP)
            provider, model = provider.strip(), model.strip()
            if provider and model:
                current = _writer(self.platform).current("default_provider", device_id=device_id)
                if str(current or "") != provider:
                    return {"default_provider": provider, "default_model": model}
                return {"default_model": model}
        return {key: value}

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("key") or "")
        return {"key": key, "value": _shown_value(self.platform, key, args.get("value"))}

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        try:
            d, key, value = self._resolve(args)
            writer = _writer(self.platform)

            def _snapshot() -> dict[str, Any]:
                # v1.442.0 (Wave 7 group C): a row this change also clears
                # (``device.theme`` <-> ``device.theme_colors``) is part of
                # what the Undo puts back.
                writes = self._writes(key, value, ctx.device_id)
                keys = [*writes, *writer.linked_keys(writes, device_id=ctx.device_id)]
                return {k: writer.current(k, device_id=ctx.device_id) for k in dict.fromkeys(keys)}

            prior = await asyncio.to_thread(_snapshot)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row; execute says why
            return None
        change_id = new_id("cfg")
        _CHANGE_ID.set(change_id)
        return {
            "kind": "setting_restore",
            "pre_inline": json.dumps(
                {"prior": prior, "device_id": ctx.device_id, "change_id": change_id}, default=str
            ),
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .writer import SettingError

        try:
            d, key, value = self._resolve(args)
        except KeyError:
            return ToolResult(ok=False, output="", error=f"There is no setting {args.get('key')!r}. Call config_list.")
        if key in ("account_groups", "brain_profile"):
            # v1.438.0 (Wave 4 group B): chat names accounts and memory
            # profiles by their NAMES ("put TSI and VR2 in a group called
            # Work", "use my memory profile Work"); the setting holds ids.
            # Off the loop: the profile table is read.
            from .records_connections import named_setting_value

            value, named_problem = await asyncio.to_thread(named_setting_value, self.platform, key, value)
            if named_problem:
                return ToolResult(ok=False, output="", error=f"{d.label}: {named_problem}.")
        try:
            writes = await asyncio.to_thread(self._writes, key, value, ctx.device_id)
        except Exception:  # noqa: BLE001 — unreadable: the plain one-row write
            writes = {key: value}
        tier = d.tier_for(value)
        if len(writes) > 1 or key not in writes or writes[key] != value:
            # A default model picked with its AI service: the strictest tier
            # of the rows it writes decides (changing the service asks first).
            order = ("allow", "ask", "ask-floor")
            tier = max((schema.get(k).tier_for(v) for k, v in writes.items()), key=order.index)
        if tier != self.TIER:
            right = TOOL_FOR_TIER[tier]
            return ToolResult(
                ok=False,
                output="",
                # (v1.489.0: the key and value are named — the value may have
                # been picked by the user on a card, so the model's own call
                # did not hold it.)
                error=(
                    f"{d.label} needs {right} (it {'always asks first' if tier == 'ask-floor' else 'asks first'}). "
                    f"Call {right} with the same key and value: key {key!r}, value {_show(value)}."
                ),
            )
        if d.store == "device" and not ctx.device_id:
            # v1.434.0 (Wave 2, F): every chat in the app window sends its
            # device id now (the chat page, a coding session, a Build
            # terminal's chat). The browser SIDEBAR is the add-on, not a
            # screen of the app, so it has none — and "open this chat in the
            # app" is not something the sidebar can do: name the place.
            from ..providers.failure_words import SURFACE_SIDEBAR, current_surface

            if current_surface() == SURFACE_SIDEBAR:
                return ToolResult(
                    ok=False, output="",
                    error=(
                        f"{d.label} belongs to one screen of the Iron Jarvis app; change it there "
                        "(Settings, or a chat in the app)."
                    ),
                )
            return ToolResult(ok=False, output="", error=f"{d.label} is set per device; open this chat in the app to change it.")
        if d.store == "device" and not valid_device_id(ctx.device_id):
            # v1.434.0: a device id the app never makes (an API caller's
            # typo) used to reach the store and come back as its raw error.
            return ToolResult(ok=False, output="", error=BAD_DEVICE_REFUSAL.format(label=d.label))
        try:
            writer = _writer(self.platform)
            change = await asyncio.to_thread(
                writer.apply, writes, actor="chat", device_id=ctx.device_id, journal=False
            )
        except SettingError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        except RuntimeError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        change_id = _CHANGE_ID.get()
        # v1.433.0 (Wave 1, A2): turning "Run app tools without asking" off
        # also clears each app's own flag — said, so "was already Off" is
        # never the whole truth when an app still ran its tools unasked.
        cleared = list(getattr(change, "cleared_auto_approve", None) or [])
        apps = ""
        if cleared:
            n = len(cleared)
            apps = (
                f" {n} {'app that' if n == 1 else 'apps that'} still ran {'its' if n == 1 else 'their'} "
                "tools without asking will ask first too."
            )
        if not change.changed:
            restart_now = " This takes full effect after Iron Jarvis restarts." if (cleared and d.restart) else ""
            return ToolResult(
                ok=True,
                output=f"{d.label} was already {_show(writes.get(key, value))}.{apps}{restart_now}",
                data={"config_change": None},
            )
        # The row this call was about leads; a second row it wrote (the AI
        # service of a model picked on the card) is said after it.
        c = next((x for x in change.changed if x.get("key") == key), change.changed[0])
        restart = " It takes full effect after Iron Jarvis restarts." if d.restart else ""
        # v1.438.0: a row the daemon does not act on itself says WHEN it
        # applies ("The desktop app on this PC picks this up…").
        applies = f" {d.applies}" if d.applies else ""
        # v1.438.0 review: a profile row (the answer language, formatting
        # rules…) reaches no prompt while "Use my profile" is off — the
        # change is saved, and the reply must not read as "done".
        if d.store == "profile" and key != "profile.enabled" and not await asyncio.to_thread(
            _profile_on, self.platform
        ):
            applies += (
                " Your profile is switched off (“Use my profile”), so answers do not follow this "
                "until it is turned on."
            )
        # v1.448.0 (Wave A, A3): the option's DISPLAY NAME beside the wire
        # value ("Ask before commands", never "approve_for_me") — the card and
        # the reply both say the name; a row with none keeps the plain value.
        _manager = getattr(self.platform, "providers", None)
        d_c = d if c.get("key") == key else schema.get(c["key"])
        old_label = value_label(d_c, c["old"], _manager)
        new_label = value_label(d_c, c["new"], _manager)
        also = ""
        for other in change.changed:
            if other is c:
                continue
            d_o = schema.get(other["key"])
            also += (
                f" Also changed {other['label']}: "
                f"{value_label(d_o, other['old'], _manager) or _show(other['old'])} → "
                f"{value_label(d_o, other['new'], _manager) or _show(other['new'])}."
            )
        return ToolResult(
            ok=True,
            output=(
                f"Changed {c['label']}: {old_label or _show(c['old'])} → "
                f"{new_label or _show(c['new'])}.{also}{apps}{restart}{applies}"
            ),
            data={
                "config_change": {
                    "key": c.get("key") or key,
                    "label": c["label"],
                    "old": c["old"],
                    "new": c["new"],
                    **({"old_label": old_label} if old_label else {}),
                    **({"new_label": new_label} if new_label else {}),
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
        "tool each setting needs). The reply shows the change with an Undo. When the user "
        "asked for a change and it is clear which value fits, make it: do not ask in words "
        "whether to (they can Undo it)."
    )


class ConfigChangeTool(_ConfigChangeBase):
    name = "config_change"
    TIER = "ask"
    description = (
        "Change an Iron Jarvis setting that asks the user first (config_list says which tool "
        "each setting needs). The user approves on a card; the change has an Undo. When the "
        "user asked for a change and it is clear which value fits, call this: do not ask in "
        "words first, the card is where they decide."
    )


class ConfigChangeProtectedTool(_ConfigChangeBase):
    name = "config_change_protected"
    TIER = "ask-floor"
    description = (
        "Change a PROTECTED Iron Jarvis setting (autonomy, sandbox, self-development, tool "
        "permissions, or turning a safety off). The user approves every time on a card. When "
        "the user asked for a change and it is clear which value fits, call this: do not ask "
        "in words first, the card is where they decide."
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
