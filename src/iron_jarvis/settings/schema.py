"""THE SETTINGS SCHEMA — every setting, declared once (calm UI redesign S1).

Before this, a setting lived in three hand-kept places that drifted: the
dashboard's ``FIELDS`` list (labels, hints, sections), the daemon's
``_SETTINGS_KEYS`` whitelist (what ``PUT /settings`` accepts), and nothing at
all for chat. The drift was real: ``comm_trust`` was rendered and PUT by the
page and silently dropped by the whitelist (fixed in v1.320.2).

Now this module is the one declaration, and everything else is generated
from it:

* ``GET /settings/schema`` → the dashboard Settings page (groups, sections,
  labels, help, options, restart notes);
* :func:`daemon_keys` → the ``PUT /settings`` whitelist;
* the chat config tools (``config_list`` / ``config_get`` / ``config_set``)
  and their per-key permission tiers (``config_set:<key>``);
* secrets (:data:`SECRETS`) → the credential card, never a value in chat.

STORES. ``config`` = a field on ``core.config.Config`` (config.toml, validated
by the Config model on a trial copy); ``profile`` = a field of the one
``UserProfileRecord`` (``profile.store``); ``device`` = a per-device
preference the daemon keeps per device id (``settings.device``) and the
dashboard mirrors locally (theme, approval default…).

TIERS (AUDIT §6, approved): ``allow`` applies at once (then a "changed" card
with Undo); ``ask`` asks first with one press (a standing "always" grant may
lift it later); ``ask-floor`` asks every time and is never lifted by a grant.
``floor_when`` names VALUES that raise a key to ``ask-floor`` (turning a
safety off is not the same act as turning it on).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

GROUPS: tuple[tuple[str, str, str], ...] = (
    ("models", "Models", "Which AI answers, and how."),
    ("connections", "Connections", "Accounts, keys, apps and where alerts go."),
    ("automation", "Agents & automation", "Limits for agent runs and the work that runs by itself."),
    ("memory", "Memory & you", "Who you are, how you like answers, and what is shared."),
    ("permissions", "Permissions & ledger", "What may run without asking, and the record of changes."),
    ("appearance", "Appearance", "How Iron Jarvis looks on this device."),
    ("system", "System", "Updates, backups, storage and power-user options."),
)
GROUP_IDS = tuple(g[0] for g in GROUPS)

TIERS = ("allow", "ask", "ask-floor")
TYPES = ("bool", "enum", "string", "number", "list", "dict")
STORES = ("config", "profile", "device")


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    group: str
    type: str
    help: str = ""
    section: str = ""
    options: tuple[tuple[str, str], ...] = ()
    tier: str = "ask"
    #: Values that raise this key's tier to ``ask-floor``.
    floor_when: tuple[Any, ...] = ()
    restart: bool = False
    #: Folded under "▸ <section>" in its group.
    advanced: bool = False
    aliases: tuple[str, ...] = ()
    store: str = "config"
    placeholder: str = ""
    #: The attribute / field the value lives in (defaults to ``key``).
    attr: str = ""
    #: Extra validation beyond the store's own (raises ValueError).
    check: Callable[[Any], None] | None = field(default=None, compare=False, repr=False)
    #: A family of keys: ``key`` ends in ``.{name}`` (e.g. ``permissions.{tool}``).
    pattern: bool = False
    #: False = not accepted by the generic ``PUT /settings``: the key has its
    #: own route with its own side effect (Iron-Proxy start/stop, writing the
    #: CLI's instruction file, clearing per-pack flags), as it always had.
    #: Chat's ``config_set`` still changes it — through the writer, which runs
    #: that same side effect.
    put: bool = True

    @property
    def field_name(self) -> str:
        return self.attr or self.key

    def tier_for(self, value: Any) -> str:
        """The tier for setting THIS value (a floor value raises it)."""
        if self.floor_when and value in self.floor_when:
            return "ask-floor"
        return self.tier

    def public(self) -> dict[str, Any]:
        """The shape ``GET /settings/schema`` serves (no callables)."""
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "section": self.section,
            "type": self.type,
            "help": self.help,
            "options": [{"value": v, "label": lbl} for v, lbl in self.options],
            "tier": self.tier,
            "floor_when": list(self.floor_when),
            "restart": self.restart,
            "advanced": self.advanced,
            "aliases": list(self.aliases),
            "store": self.store,
            "placeholder": self.placeholder,
            "secret": False,
            "pattern": self.pattern,
        }


@dataclass(frozen=True)
class SecretDef:
    """A credential. Set only through the credential card (never config_set),
    stored in the encrypted vault, never echoed back."""

    name: str
    label: str
    group: str
    help: str = ""
    #: Vault key; ``{x}`` is filled from the request (a provider, a channel).
    vault_key: str = ""
    pattern: bool = False
    aliases: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "key": self.name,
            "label": self.label,
            "group": self.group,
            "help": self.help,
            "secret": True,
            "pattern": self.pattern,
            "tier": "ask-floor",
            "aliases": list(self.aliases),
        }


def _range(lo: float, hi: float | None = None) -> Callable[[Any], None]:
    def check(v: Any) -> None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("must be a number")
        if v < lo or (hi is not None and v > hi):
            raise ValueError(f"must be between {lo} and {hi}" if hi is not None else f"must be at least {lo}")

    return check


def _night_time(v: Any) -> None:
    """v1.398.0: a night-window time ("23:00")."""
    from ..harness.night_window import check_time

    check_time(v)


#: v1.393.0: the Group choice's own words (the Iron-Proxy card says them too —
#: dashboard/lib/ironProxy.ts ``GROUP_HELP``, pinned equal).
ACCOUNT_GROUPS_HELP = (
    "Accounts in the same group can take over from each other when one reaches its limit. "
    "Work and personal accounts should be in different groups."
)
#: The most groups, and accounts in one group, a save may hold.
ACCOUNT_GROUPS_MAX = 20
ACCOUNT_GROUP_SIZE_MAX = 20


def _account_groups_shape(v: Any) -> None:
    """THE shape of ``account_groups`` (v1.393.0), refused in words before
    anything is saved: ``{name: [account ids]}``, a name of 1–40 characters,
    each account in ONE group. Whether each id is an Iron-Proxy account is
    the writer's check (it reads the cached snapshot)."""
    from ..core.config import ACCOUNT_GROUP_NAME_MAX

    if not isinstance(v, dict):
        raise ValueError("must be a set of group names, each with its accounts")
    if len(v) > ACCOUNT_GROUPS_MAX:
        raise ValueError(f"must hold at most {ACCOUNT_GROUPS_MAX} groups")
    seen: dict[str, str] = {}
    names: set[str] = set()
    for raw_name, ids in v.items():
        name = " ".join(str(raw_name).split()) if isinstance(raw_name, str) else ""
        if not name:
            raise ValueError("must give every group a name")
        if len(name) > ACCOUNT_GROUP_NAME_MAX:
            raise ValueError(f"must have names of at most {ACCOUNT_GROUP_NAME_MAX} characters")
        if name.casefold() in names:
            raise ValueError(f"must have different names (two are called “{name}”)")
        names.add(name.casefold())
        if not isinstance(ids, list) or not all(isinstance(i, str) and i.strip() for i in ids):
            raise ValueError(f"must list the accounts of “{name}”")
        if len(ids) > ACCOUNT_GROUP_SIZE_MAX:
            raise ValueError(f"must hold at most {ACCOUNT_GROUP_SIZE_MAX} accounts in one group")
        for raw in ids:
            pid = raw.strip()
            if pid in seen and seen[pid] != name:
                raise ValueError(
                    f"must put each account in one group only (one is in “{seen[pid]}” and “{name}”)"
                )
            seen[pid] = name


S = SettingDef

SETTINGS: tuple[SettingDef, ...] = (
    # ---------------------------------------------------------------- models
    S("default_provider", "Default provider", "models", "string",
      "The AI service used when a chat doesn't pick one. Manage accounts and keys under Connections.",
      tier="ask", aliases=("ai provider", "which ai", "default ai"), placeholder="anthropic"),
    S("default_model", "Default model", "models", "string",
      "The model that answers by default.", tier="allow",
      aliases=("model", "default model", "which model", "use model"), placeholder="claude-opus-4-8"),
    # v1.358.0 (lane 8E, readiness M): the label in plain words; the help
    # names the word the chat's own menu uses ("Persona"). Key unchanged.
    S("default_persona", "How the assistant talks by default", "models", "string",
      "The persona (the assistant's voice and manner) used whenever a chat doesn't pick one, "
      "including from your phone. A chat can change it from its ⋯ menu.", tier="allow",
      aliases=("persona", "default persona", "assistant style")),
    S("chat_followups", "Suggest follow-up questions", "models", "bool",
      "Show up to three short questions you might ask next under a reply. "
      "This makes one extra short call to the same model after each reply.",
      tier="allow", aliases=("follow-up questions", "suggested questions", "follow ups")),
    S("strict_model_pin", "Strict model pin", "models", "bool",
      "When on, a chat that explicitly picks a model must be answered by THAT model — never silently substituted. Applies only to explicit picks.",
      tier="ask", advanced=True, section="Routing"),
    S("local_primary_policy", "If my local model answers with an error", "models", "enum",
      "When the model on your own machine replies with an error: stop (the chat stays on this PC), or let another connected model answer.",
      options=(("refuse", "Stop and tell me (default)"), ("failover", "Let another model answer")),
      tier="ask", floor_when=("failover",), section="Routing", advanced=True,
      aliases=("local failover", "fall back")),
    S("routing_model", "Auto-routing classifier model", "models", "string",
      "The model Auto uses to decide which model answers. Empty = the built-in rules.",
      tier="ask", section="Routing", advanced=True),
    S("routing_tiers_json", "Auto-routing tiers", "models", "string",
      "JSON overrides for Auto's quality tiers. Leave empty for the defaults.",
      tier="ask", section="Routing", advanced=True),
    S("model_roles", "Model roles", "models", "dict",
      "Which model plays each role (e.g. a cheap model for summaries).",
      tier="ask", section="Routing", advanced=True),
    S("routing_local_ladder", "Local model ladder", "models", "list",
      "Local models to try in order before a cloud model.",
      tier="ask", section="Routing", advanced=True),
    S("model_context_windows", "Context windows", "models", "dict",
      "Your own context-window size per model (tokens), when the detected one is wrong.",
      tier="ask", section="Context windows", advanced=True),
    S("context_compaction", "Conversation compaction", "models", "dict",
      "When a long conversation is summarized to fit (thresholds).",
      tier="ask", section="Context windows", advanced=True),
    S("ollama_base_url", "Ollama server URL", "models", "string",
      "Point at a local Ollama server. Leave blank to turn local models off.",
      tier="ask", section="Local and custom models", advanced=True,
      aliases=("ollama", "local model server"), placeholder="http://127.0.0.1:11434"),
    S("ollama_model", "Ollama model", "models", "string",
      "Default model on that Ollama server.", tier="allow",
      section="Local and custom models", advanced=True, placeholder="llama3.1"),
    S("custom_base_url", "Custom endpoint URL", "models", "string",
      "Any OpenAI-compatible endpoint — LM Studio, vLLM, a private gateway. Add its key under Connections.",
      tier="ask", section="Local and custom models", advanced=True, placeholder="https://…/v1"),
    S("custom_model", "Custom endpoint model", "models", "string",
      "Default model id for that custom endpoint.", tier="allow",
      section="Local and custom models", advanced=True),
    S("prefer_local_when_capable", "Prefer my own hardware", "models", "bool",
      "Route work to your own machines first, and use a cloud model only when a local one can't do it.",
      tier="allow", section="Local and custom models", advanced=True, aliases=("prefer local",)),
    S("local_quality_bar", "Quality bar for local models", "models", "number",
      "0–1. Average score a local model must reach on a kind of work before it is preferred for it.",
      tier="allow", section="Local and custom models", advanced=True, check=_range(0, 1)),
    S("local_quality_min_samples", "Evidence needed first", "models", "number",
      "How many scored sessions a local model needs before its average is trusted.",
      tier="allow", section="Local and custom models", advanced=True, check=_range(0)),
    S("decompose_local_tasks", "Break big tasks into steps for local models", "models", "bool",
      "Split a multi-step task into plan → do → check for a smaller local model.",
      tier="allow", section="Local and custom models", advanced=True),
    S("decompose_all_tasks", "Break big tasks into steps for every model", "models", "bool",
      "Like the above, for cloud models too.", tier="ask", section="Local and custom models", advanced=True),
    S("opencode_local_models", "OpenCode local models", "models", "string",
      "Comma-separated OpenCode models to offer as local.", tier="ask",
      section="Local and custom models", advanced=True),
    S("fleet_code_route_enabled", "Send coding work to the fleet", "models", "bool",
      "Route coding tasks to a fleet node.", tier="ask", section="Fleet", advanced=True),
    S("fleet_code_target", "Fleet coding target", "models", "string",
      "provider:model that takes coding work.", tier="ask", section="Fleet", advanced=True),
    S("fleet_code_task_classes", "Fleet coding task classes", "models", "string",
      "Comma-separated task classes; empty = the built-in set.", tier="ask", section="Fleet", advanced=True),
    S("fleet_sampling_enabled", "Fleet telemetry", "models", "bool",
      "Sample local endpoints in the background for the Fleet page.", tier="allow",
      section="Fleet", advanced=True),
    S("fleet_sampling_seconds", "Fleet telemetry every (seconds)", "models", "number",
      "Idle sampling cadence.", tier="allow", section="Fleet", advanced=True, check=_range(5, 3600)),
    S("fleet_savings_baseline", "Savings baseline model", "models", "string",
      "The cloud model local runs are compared against on Usage.", tier="allow",
      section="Fleet", advanced=True),
    # v1.395.0 (the long-jobs plan's Phase 6, decision 8): the model team's
    # pay-per-use members only under a monthly cap the user sets — 0 (the
    # default) = never. Raising it spends money, so from chat it asks every
    # time (no grant lifts it). Read live by the team router.
    S("team_paid_cap_usd", "Pay-per-use limit for your model team ($ a month)", "models", "number",
      "The most your model team's pay-per-use members may spend in a calendar month, in "
      "dollars. Iron Jarvis and its agents hand work to a pay-per-use member, or ask it a "
      "question, only while this month's spend is under this limit. 0 means never. Work you "
      "start yourself (on the Agents page, a schedule, a task you queue) is not stopped by "
      "it, but what it spends counts. Members on your own hardware and on your Claude Code "
      "or Codex plan are not counted.",
      tier="ask-floor", section="Model team", check=_range(0, 100000),
      aliases=("team pay cap", "pay per use limit", "pay per use cap", "model team budget")),
    # v1.400.0 (TEAM-FIRST): what a coding session's planner is TOLD about
    # the team (agents/model_team.team_section). Turning it ON moves work and
    # usage onto other models and accounts, so from chat it asks every time
    # (no grant lifts it); turning it off applies at once. Read live.
    # v1.401.0: it also makes Iron Jarvis ITSELF hand each coding reply that
    # changed files to the team's reviewer at the finish (harness/finish).
    S("team_first", "Hand work to my team first", "models", "bool",
      "When this is on, a coding session is asked to hand reading, test runs, first drafts and "
      "reviews to your model team by role, and to keep the planning and the final check on its own "
      "model. Iron Jarvis also hands each coding reply that changed files to the team member with "
      "the Review role before the reply ends: the reviewer reads the change on the model you set "
      "for it, cannot change files, and the session answers what it found. A review adds up to "
      "ten minutes to the reply. "
      "Members on your Claude Code or Codex plans use those plans. Off, a coding session is "
      "shown your team and decides for itself whether to use it.",
      tier="allow", floor_when=(True,), section="Model team",
      aliases=("team first", "team-first", "hand work to my team", "use my team first",
               "model team first")),
    S("voice_transcribe_base_url", "Voice: speech-to-text endpoint", "models", "string",
      "Optional dedicated whisper server for dictation. Its key is set under Connections.",
      tier="ask", section="Voice", advanced=True),
    S("voice_transcribe_model", "Voice: speech-to-text model", "models", "string",
      "The transcription model that server serves. Empty = auto-discover.",
      tier="allow", section="Voice", advanced=True),
    # v1.358.0 (lane 8E, G76 scan): a SPEECH model that works offline, said
    # so — "offline model" is the demo model's retired name.
    S("voice_vosk_model_path", "Voice: offline speech model folder", "models", "string",
      "Folder of an offline Vosk speech model.", tier="ask", section="Voice", advanced=True),
    # ------------------------------------------------------------ connections
    S("iron_proxy_enabled", "Use several accounts", "connections", "bool",
      "Pick which of your signed-in Claude, Codex or Grok accounts each request uses. "
      "Add the accounts under Connections, Several accounts (Iron-Proxy).",
      tier="ask", put=False, aliases=("iron proxy", "iron-proxy", "shared accounts", "accounts switcher")),
    # v1.393.0 (the long-jobs plan's Phase 4, decision 4): account groups.
    # Made on the Iron-Proxy card's Group choice (the same writer); which
    # account ids exist is the writer's check at save time. "ask-floor" in
    # chat (v1.393.0 review; the doer had "ask", which a yolo chat runs with
    # no card and a standing grant may lift): which accounts may take over
    # each other's conversations is the user's privacy decision (work and
    # personal never mix), so every change from chat is carded, every time.
    S("account_groups", "Account groups", "connections", "dict",
      ACCOUNT_GROUPS_HELP,
      tier="ask-floor", section="Several accounts", advanced=True, check=_account_groups_shape,
      aliases=("account group", "account groups", "work accounts", "take over at a limit")),
    S("browser_access", "Your browser", "connections", "enum",
      "What Jarvis may do in your own browser through the add-on.",
      options=(("off", "Off"), ("read_only", "Read pages only"), ("interactive", "Read and act (each action asks)")),
      tier="ask", floor_when=("interactive",), aliases=("browser", "chrome", "edge")),
    S("calendar_trigger_enabled", "Calendar reminders", "connections", "bool",
      "Start work when a calendar event is about to begin. The calendar link is set as a secret.",
      tier="ask", section="Calendar", aliases=("calendar",)),
    S("calendar_lead_minutes", "Calendar lead time (minutes)", "connections", "number",
      "How long before an event to fire.", tier="allow", section="Calendar", check=_range(0, 1440)),
    S("calendar_tick_seconds", "Calendar check every (seconds)", "connections", "number",
      "How often the calendar is polled.", tier="ask", section="Calendar", advanced=True,
      check=_range(30, 86400)),
    S("agent_browser_enabled", "Agent browser", "connections", "bool",
      "Let agents drive their own separate browser (never your logged-in one).",
      tier="ask", floor_when=(True,), attr="computer_use.enabled", section="Browser",
      aliases=("computer use", "agent browser")),
    # v1.358.0 (G61): ANOTHER COMPUTER can reach this one only when the user
    # turns this on. Off by default; asks every time from chat (a model must
    # never widen who can reach this PC); takes effect when Iron Jarvis
    # restarts, because the DESKTOP SHELL reads it from config.toml at every
    # daemon spawn (desktop/main.js readDaemonNetwork) and only then listens
    # on the network with the host allowlist set to this PC's own private
    # addresses. Every request still needs a token. The doors' reachability
    # sentence (routes/agents.reachability) names THIS row.
    S("lan_access", "Let other computers on my network reach this Iron Jarvis", "connections", "bool",
      "Off unless you turn it on. When on, Iron Jarvis also listens on this PC's address on your "
      "network after it restarts, so another computer running Iron Jarvis can hand it tasks "
      "(Agents, Your team, Another computer running Jarvis). Every request still needs that "
      "door's token. Windows may ask once whether to allow Iron Jarvis on your network.",
      tier="ask-floor", restart=True, section="Another computer",
      aliases=("lan access", "network access", "another computer", "other computers",
               "reach from another computer", "local network")),
    # Media in chat (v1.346.0): which service makes images/video/audio, and
    # when a generation asks before spending. The rows are config fields;
    # the Settings page draws them from here.
    S("media_provider", "Media service", "connections", "enum",
      "Which service makes images, video and audio from chat. Auto = the only one connected; "
      "when two are connected, pick one here.",
      options=(("auto", "Auto (the only connected one)"), ("pixio", "Pixio"),
               ("openai_images", "OpenAI Images")),
      tier="ask", section="Media", aliases=("media provider", "image service", "pixio", "media generation")),
    S("media_ask_when_free", "Ask even when a generation is free", "connections", "bool",
      "A generation the service says is free (a daily allowance, a plan-covered model) runs "
      "without asking. Turn this on to be asked every time. A paid or unknown price always asks.",
      tier="allow", section="Media", aliases=("ask before free generation",)),
    S("media_inline_wait_s", "Wait in chat up to (seconds)", "connections", "number",
      "A quick generation (an image) is waited for in the chat. Longer work finishes in the "
      "background and the bell tells you when it is ready.",
      tier="allow", section="Media", advanced=True, check=_range(0, 600)),
    S("media_job_max_s", "Give up on a generation after (seconds)", "connections", "number",
      "How long a background generation may take before it is marked failed.",
      tier="allow", section="Media", advanced=True, check=_range(60, 86400)),
    S("media_default_models", "Default media models", "connections", "dict",
      "The model to use per kind (image, video, audio, music). Empty = the service's cheapest model of that kind.",
      tier="ask", section="Media", advanced=True),
    S("mcp_auto_approve", "Run app tools without asking", "permissions", "bool",
      "Let every tool from your connected apps and extensions run without asking you first. "
      "Not recommended.",
      tier="ask-floor", restart=True, put=False, aliases=("mcp auto approve",)),
    # -------------------------------------------------------------- automation
    S("max_agent_steps", "Max steps per run", "automation", "number",
      "Safety ceiling on how many steps one agent run may take. Runs you start and watch "
      "(from chat, the Agents page, a project) use “Steps for runs you start” instead.",
      tier="ask", check=_range(1, 1000)),
    # v1.402.0: a run a person started and is watching gets its own, larger
    # budget, and carries on ONCE by itself when it spends it.
    S("attended_run_steps", "Steps for runs you start", "automation", "number",
      "How many steps an agent run you start and watch (from chat, the Agents page, a project "
      "task) may take when nothing else says. If it uses them all before finishing, it "
      "carries on once by itself with the same number again, then stops and offers Keep going.",
      tier="allow", check=_range(12, 200),
      aliases=("attended steps", "steps for my runs", "agent step budget")),
    S("tool_call_timeout_s", "Time limit for one step (seconds)", "automation", "number",
      "How long one step of a run (reading a file, running a command, using an app) may take "
      "before it is stopped and recorded as failed. 0 = no limit.",
      tier="ask", check=_range(0, 86400), aliases=("tool call deadline", "tool timeout", "step time limit")),
    S("max_concurrent_sessions", "Runs at once", "automation", "number",
      "How many agent runs may work at the same time. 0 = no cap.", tier="ask", check=_range(0, 64)),
    S("busy_runs_notice_at", "Busy notice after", "automation", "number",
      "When there is no cap and more than this many background runs are going at once, "
      "the bell asks whether to queue the extras. 0 = never ask.",
      tier="allow", check=_range(0, 64), aliases=("busy notice", "busy runs")),
    # v1.367.0 (IJ harness Phase 4): one Build session turn's tool rounds.
    # v1.384.0: 0 = no limit, the default (like Claude Code); a number is
    # still honoured. The safety stop is harness.mode.BUILD_SAFETY_ROUNDS.
    S("build_round_budget", "Steps per Build session reply", "automation", "number",
      "How many steps (tool rounds) one reply in a Build session may take. 0 = no limit "
      "(the default): it works until it is done or you press Stop, with a safety stop at "
      "1,000 steps so a reply stuck in a loop cannot run for days. A reply that reaches its "
      "limit says what it got done, and Keep going carries on from there. The work stays "
      "in the session; nothing is handed to an agent.",
      tier="allow", check=_range(0, 1000),
      aliases=("build round budget", "build steps", "build step limit")),
    # v1.393.0 (the long-jobs plan's Phase 4, decision 4): a coding reply cut
    # off by its account's limit waits for the reset and carries on by itself
    # (today's "Resume when ready", with no press). Read live.
    S("build_auto_resume", "Carry on by itself after an account's limit", "automation", "bool",
      "When a coding session's Claude Code or Codex account reaches its limit, the reply waits "
      "for the reset and then carries on by itself, the way Resume when ready does; if the "
      "account is in an account group, another account of that group may carry it on first. "
      "Cancel or Choose account on the limit card still work. Off: nothing carries on by "
      "itself; the card waits for you to press Resume when ready.",
      tier="allow",
      aliases=("resume when ready", "auto resume", "account limit", "carry on after a limit")),
    # v1.405.0: the editor a coding session's "Open in …" presses open
    # (harness/code_editor). Read live by GET /editor/options.
    S("code_editor", "Code editor", "automation", "enum",
      "The editor a coding session opens a file in, at the line you pressed. Automatic: "
      "VS Code when it is installed, else Cursor.",
      options=(("auto", "Automatic"), ("vscode", "VS Code"), ("cursor", "Cursor")),
      tier="allow", aliases=("editor", "vs code", "vscode", "cursor", "open in editor")),
    # v1.398.0 (night shift N1, harness/night): the queue in Coding sessions ›
    # Tonight is the opt-in; these say WHEN and HOW MUCH. Review: WHEN and
    # HOW MUCH work runs with nobody watching is the user's to change — from
    # chat they ask (the "Runs at once" rule, ``max_concurrent_sessions``),
    # so a model cannot widen the window or the limits by itself. The card
    # wait only decides how long a question waits: allow.
    S("night_window_start", "Night tasks start at", "automation", "string",
      "Tasks in Coding sessions › Tonight start from this time (24-hour, like 23:00), each on its own "
      "branch. A task not started before the window ends waits for the next night.",
      tier="ask", section="Night shift", check=_night_time, placeholder="23:00",
      aliases=("night window", "night shift start", "tonight starts")),
    S("night_window_end", "Night tasks stop starting at", "automation", "string",
      "No new night task starts after this time (24-hour, like 07:00). A task already running "
      "finishes under its own time limit; one waiting for its account's limit to reset is stopped.",
      tier="ask", section="Night shift", check=_night_time, placeholder="07:00",
      aliases=("night window end", "night shift end")),
    S("night_max_at_once", "Night tasks at once", "automation", "number",
      "How many night tasks may run at the same time. Never two in the same folder.",
      tier="ask", section="Night shift", check=_range(1, 8),
      aliases=("night tasks at once", "night shift at once")),
    S("night_task_minutes", "Time limit per night task (minutes)", "automation", "number",
      "A night task stops after this many minutes and says what it got done.",
      tier="ask", section="Night shift", check=_range(5, 1440),
      aliases=("night task limit", "night time limit")),
    S("night_card_wait_minutes", "Wait for an answer at night (minutes)", "automation", "number",
      "When a night task needs your OK, it waits this long (your phone gets the question, if a phone "
      "channel is set up). Then that one step is skipped, the task carries on with the rest, and the "
      "step is listed as needing you.",
      tier="allow", section="Night shift", check=_range(1, 240),
      aliases=("night card wait", "night question wait")),
    # v1.350.0: an agent's plan, shown before any step runs. Turning it OFF
    # from chat asks every time (a model must not quietly drop the check).
    S("plan_approval", "Ask me to approve an agent's plan before it starts", "automation", "bool",
      "When an agent splits a task into steps, it shows you the plan and does nothing until "
      "you press Approve. Only runs started from chat or the Agents page wait; schedules, "
      "goals and other background runs never do. Off: the plan shows as a checklist and "
      "the work starts at once.",
      tier="allow", floor_when=(False,), aliases=("plan approval", "approve the plan", "approve plans")),
    S("autonomy_enabled", "Work on my goals by itself", "automation", "bool",
      "Let Iron Jarvis think about your goals in the background and suggest next steps "
      "(or, within the limits below, take them).",
      tier="ask-floor", section="Autonomy", aliases=("autonomy", "the pulse")),
    S("autonomy_level", "Autonomy ceiling", "automation", "enum",
      "How far it may go on its own. High-risk steps are never done automatically.",
      options=(("suggest", "Suggest only"), ("act_low", "Act on low-risk steps"),
               ("act_all", "Act on low- and medium-risk steps")),
      tier="ask-floor", section="Autonomy"),
    S("autonomy_dry_run", "Dry-run mode", "automation", "bool",
      "Propose what it WOULD do without executing anything.",
      tier="allow", floor_when=(False,), section="Autonomy"),
    # v1.351.0 (readiness audit G13): the promise is now enforced at
    # ``Orchestrator.create_session`` (every unattended door) and engaging it
    # stops what those doors have running — so the words name what it stops.
    S("autonomy_kill_switch", "Emergency stop", "automation", "bool",
      "Immediately blocks every self-initiated action: schedules, goals, rules, phone tasks, "
      "queued tasks, tasks from another computer and Autonomy do not start, and what they "
      "have running is stopped. "
      "Chat and work you start keep going.",
      tier="allow", floor_when=(False,), section="Autonomy", aliases=("kill switch", "stop everything")),
    S("autonomy_tick_seconds", "Think every (seconds)", "automation", "number",
      "How often the background loop wakes up.", tier="ask", section="Autonomy", check=_range(10, 86400)),
    S("autonomy_max_actions_per_day", "Most actions it may take in a day", "automation", "number",
      "How many actions work it started by itself may take in any 24 hours.", tier="ask",
      section="Autonomy", check=_range(0), aliases=("max actions per day",)),
    S("autonomy_max_tokens_per_day", "Most model use in a day (tokens)", "automation", "number",
      "How much model text work it started by itself may use in any 24 hours, counted in "
      "tokens (a token is about three quarters of a word).", tier="ask", section="Autonomy",
      check=_range(0), aliases=("max tokens per day", "token budget")),
    S("sentinels_enabled", "Folder watchers (sentinels)", "automation", "bool",
      "Watchers that notice changes and suggest work (they never act on their own).",
      tier="ask", section="Watchers", aliases=("sentinels", "watchers")),
    S("sentinels_tick_seconds", "Watch every (seconds)", "automation", "number",
      "How often the watchers check.", tier="ask", section="Watchers", check=_range(10, 86400)),
    S("skill_learning_enabled", "Learn new skills from my work", "automation", "bool",
      "Propose reusable skills from finished work (you approve each).", tier="ask", section="Skills"),
    S("skill_learning_auto_approve", "Approve learned skills automatically", "automation", "bool",
      "Keep proposed skills without asking.", tier="ask-floor", section="Skills"),
    S("default_skills", "Skills always loaded", "automation", "list",
      "Skills injected into every run.", tier="ask", section="Skills", advanced=True),
    S("extra_skill_paths", "Extra skill folders", "automation", "list",
      "More folders to find skills in.", tier="ask", section="Skills", advanced=True),
    # ------------------------------------------------------------------ memory
    S("profile.about", "About you", "memory", "string",
      "A few lines every model reads about you.", tier="allow", store="profile", attr="about",
      section="Profile", aliases=("about me", "my profile")),
    S("profile.tone", "Tone", "memory", "string", "How answers should sound.", tier="allow",
      store="profile", attr="tone", section="Profile"),
    S("profile.writing_style", "Writing style", "memory", "string", "Your own writing style.",
      tier="allow", store="profile", attr="writing_style", section="Profile"),
    S("profile.formatting", "Formatting", "memory", "string", "Lists, headings, tables…", tier="allow",
      store="profile", attr="formatting", section="Profile"),
    S("profile.reading_level", "Reading level", "memory", "string", "How plain the words should be.",
      tier="allow", store="profile", attr="reading_level", section="Profile"),
    S("profile.response_length", "Answer length", "memory", "string", "Short, medium or detailed.",
      tier="allow", store="profile", attr="response_length", section="Profile",
      aliases=("shorter answers", "longer answers")),
    S("profile.accessibility", "Accessibility", "memory", "string", "Anything that makes answers easier for you.",
      tier="allow", store="profile", attr="accessibility", section="Profile"),
    S("profile.enabled", "Use my profile", "memory", "bool", "Send the profile with every prompt.",
      tier="allow", store="profile", attr="enabled", section="Profile"),
    S("profile_share_claude_code", "Share my profile with Claude Code", "memory", "bool",
      "Write your profile into Claude Code's own instructions file on this PC.",
      tier="ask", put=False, section="Share with Build"),
    S("profile_share_codex", "Share my profile with Codex", "memory", "bool",
      "Write your profile into Codex's own instructions file on this PC.",
      tier="ask", put=False, section="Share with Build"),
    # v1.370.0 (IJ harness Phase 7): the Build experience library.
    S("build_reflect", "Learn from Build sessions", "memory", "bool",
      "When a Build session has been idle for 10 minutes after replies that changed files or ran "
      "commands, Iron Jarvis may suggest one lesson for that folder, and sometimes a small edit to "
      "the folder's coding-session instructions. It sends one request, cut to a fixed size, to the session's own "
      "model on the session's own account, so on a Claude Code or Codex plan it counts toward that "
      "plan's limits. It waits while that account is at or near its limit. Nothing is kept until "
      "you accept it.",
      tier="allow", aliases=("build library", "build lessons", "learn from build")),
    S("chat_folder_rules", "Follow a project folder's own instructions in chat", "memory", "bool",
      "When a chat is in a project that has a folder, it reads the instruction files kept in "
      "that folder (AGENTS.md, CLAUDE.md and their .local versions) and follows them.",
      tier="allow", aliases=("agents.md", "claude.md", "folder rules", "project instructions")),
    S("memory_steward_enabled", "Memory steward", "memory", "bool",
      "Tidy and de-duplicate long-term memory in the background.", tier="ask", advanced=True),
    S("chat_files_root", "Chat files folder", "memory", "string",
      "Where files you hand to chat are kept. Empty = Documents\\Iron Jarvis.", tier="ask", advanced=True),
    S("search_roots", "Extra folders for file search", "memory", "list",
      "More folders File search looks in.", tier="ask", advanced=True),
    S("obsidian_vault", "Obsidian vault", "memory", "string", "A vault used as long-term memory.",
      tier="ask", advanced=True),
    S("notion_database_id", "Notion database", "memory", "string",
      "A Notion database used as long-term memory.", tier="ask", advanced=True),
    S("active_project_id", "Focused project", "memory", "string",
      "The project new chats are grounded in. Empty = none.", tier="allow",
      aliases=("focus project", "active project")),
    # ------------------------------------------------------------- permissions
    S("comm_trust", "Runs started from inbound messages", "permissions", "enum",
      "A message from your phone, Slack or email starts a run. Low trust may read and answer but not change memory, settings, agents or skills.",
      options=(("low", "Low trust (default)"), ("full", "Full trust")),
      tier="ask", floor_when=("full",), aliases=("phone trust", "inbound trust")),
    # v1.356.0 (G81): what chat and agents may read is a setting, not an
    # environment variable. Read LIVE by core/fs_policy; every folder is
    # checked when it is saved (settings/writer -> fs_policy.read_root_problem).
    # Changing it from chat asks every time — a model must never widen (or
    # quietly move) what it may read.
    S("readable_folders", "Folders Jarvis may read", "permissions", "list",
      "Chat and agents read files only inside these folders, plus Jarvis's own working folders. "
      "One folder per line. Leave it empty to let them read any file on this PC, except the "
      "folders that hold Jarvis's keys and records.",
      tier="ask-floor",
      aliases=("readable folders", "read folders", "allowed folders", "limit reading",
               "folders jarvis can read", "which folders"),
      placeholder="One folder per line, for example C:\\Users\\you\\Documents\\Clients"),
    S("permissions.{tool}", "Approval for one tool", "permissions", "enum",
      "Whether a tool may run without asking (allow), asks first (ask), or never runs (deny). Tools that touch your PC can never be set to allow.",
      options=(("allow", "Run without asking"), ("ask", "Ask first"), ("deny", "Never run")),
      tier="ask-floor", pattern=True, aliases=("tool permission", "always allow", "never allow")),
    S("event_retention_days", "Keep activity history (days)", "permissions", "number",
      "How long the activity log and ledger are kept. 0 = forever.", tier="ask", restart=True,
      check=_range(0, 36500)),
    # ---------------------------------------------------------------- system
    S("git_native", "Git-native workspaces", "system", "bool",
      "Run each session on its own git worktree.", tier="ask", advanced=True),
    S("sandbox_runtime", "Sandbox runtime", "system", "enum",
      "How tool execution is isolated.",
      options=(("native", "On this PC (no container)"), ("docker", "Inside Docker")),
      tier="ask-floor", restart=True, advanced=True),
    S("self_dev_enabled", "Self-development", "system", "bool",
      "Let Iron Jarvis edit its own source code (always review-gated).", tier="ask-floor",
      restart=True, advanced=True, section="Self-development"),
    S("self_dev_root", "Self-development repo folder", "system", "string",
      "Path to the Iron Jarvis repo.", tier="ask-floor", restart=True, advanced=True,
      section="Self-development"),
    S("backup_mirror_dir", "Backup copy folder", "system", "string",
      "Also copy every backup to this folder (another drive, a synced folder).", tier="ask",
      section="Backups", aliases=("backup folder",)),
    S("backup_mirror_media", "Copy media with backups", "system", "bool",
      "Include generated media in the backup copy.", tier="allow", section="Backups"),
    # v1.405.0: THE UPDATE CHANNEL. The desktop shell reads it from
    # config.toml at every update check (desktop/main.js updateChannelOf), so
    # a change applies at the next check. Moving to Fresh WIDENS what reaches
    # this PC (every release, the moment it is published), so from chat it
    # asks every time; moving to Stable only narrows it, so it applies at once.
    # The Updates page's own Stable / Fresh choice saves this same row.
    S("update_channel", "Update channel", "system", "enum",
      "Stable gets a release once it has been out for a day with every test passing, unless "
      "you held it back. Fresh gets every release as soon as it is published. A PC on Stable "
      "never goes back to an older version: it stays on its version until Stable passes it.",
      options=(("fresh", "Fresh (every release)"), ("stable", "Stable (out for a day first)")),
      tier="allow", floor_when=("fresh",), section="Updates",
      aliases=("update channel", "stable channel", "fresh channel", "stable updates",
               "release channel", "updates")),
    # v1.391.0 (long jobs, Phase 2, decision 1): the desktop app holds off
    # sleep while work runs. Read LIVE by the desktop through /system/activity
    # (no restart); never keeps the screen on.
    S("keep_awake", "Keep this PC awake while work runs", "system", "bool",
      "While a reply, an agent run, a workflow or a coding tool in a Build terminal is working, "
      "the desktop app keeps Windows from going to sleep, so the work can finish. The screen can "
      "still turn off. Work waiting for your answer does not keep it awake. When nothing is "
      "working, the PC sleeps as usual.",
      tier="allow", section="Power",
      aliases=("keep awake", "prevent sleep", "stay awake", "sleep", "power")),
    S("pi_sessions_dir", "Pi sessions folder", "system", "string", "Where Pi coding sessions are read from.",
      tier="ask", advanced=True),
    S("opencode_data_dir", "OpenCode data folder", "system", "string", "Where OpenCode keeps its data.",
      tier="ask", advanced=True),
    # ----------------------------------------------------- device preferences
    S("device.theme", "Theme", "appearance", "string",
      "The theme on this device (a built-in Mark or one of your own).", tier="allow", store="device",
      aliases=("theme", "colors", "dark mode", "light mode")),
    # v1.352.0 (G24): the chat chip's OWN words for the three levels
    # (dashboard/lib/permissionLevels.ts PERMISSION_LEVELS), so Settings, the
    # composer's chip and an agent's setup name a level the same way — and
    # "Don't ask" typed in chat finds `yolo` (the settings tools match a
    # label as well as a value). Pinned equal: tests/test_approval_words_v13520.py.
    S("device.approval_mode", "Approvals in chat", "permissions", "enum",
      "How chat asks before acting, on this device.",
      options=(("always_ask", "Ask first"), ("approve_for_me", "Ask when risky"),
               ("yolo", "Don't ask")),
      tier="allow", floor_when=("yolo",), store="device", aliases=("approvals", "approval mode")),
    S("device.chat_persona", "Chat persona", "appearance", "string",
      "The persona new chats start with on this device.", tier="allow", store="device"),
    S("device.auto_tools", "Auto tools in chat", "permissions", "bool",
      "Let chat arm the tools a message needs.", tier="allow", store="device"),
    S("device.read_aloud", "Read replies aloud", "appearance", "bool",
      "Speak each reply on this device.", tier="allow", store="device", aliases=("text to speech",)),
)

SECRETS: tuple[SecretDef, ...] = (
    SecretDef("connection.{provider}", "API key for a model provider", "connections",
              "Stored encrypted; used to reach that provider.", vault_key="{provider}_api_key",
              pattern=True, aliases=("api key", "openai key", "anthropic key")),
    SecretDef("channel.{name}", "A notification channel's token", "connections",
              "Telegram bot token, Slack token, email password.", vault_key="channel_{name}",
              pattern=True, aliases=("telegram token", "slack token")),
    SecretDef("voice_transcribe_key", "Voice: speech-to-text key", "models",
              "Key for the speech-to-text endpoint.", vault_key="voice_transcribe_key"),
    SecretDef("calendar_ics_url", "Calendar link", "connections",
              "Your private calendar (.ics) link.", vault_key="calendar_ics_url"),
    SecretDef("app.{name}", "An app's token", "connections",
              "A token for a connected app (Notion, GitHub…).", vault_key="mcp_{name}_token",
              pattern=True, aliases=("connect notion", "connect github")),
    SecretDef("secret.{name}", "A named secret", "connections",
              "Any secret in the vault.", vault_key="{name}", pattern=True),
)

BY_KEY: dict[str, SettingDef] = {d.key: d for d in SETTINGS}
_PATTERNS: tuple[SettingDef, ...] = tuple(d for d in SETTINGS if d.pattern)
_NAME = re.compile(r"^[A-Za-z0-9_:.\-]{1,80}$")


def get(key: str) -> SettingDef:
    """The definition for ``key`` — a plain key, or one of a family
    (``permissions.shell`` → the ``permissions.{tool}`` definition)."""
    if key in BY_KEY and not BY_KEY[key].pattern:
        return BY_KEY[key]
    for d in _PATTERNS:
        prefix = d.key.split("{", 1)[0]
        if key.startswith(prefix) and _NAME.match(key[len(prefix):]):
            return d
    raise KeyError(f"unknown setting: {key}")


def pattern_arg(d: SettingDef, key: str) -> str:
    """``permissions.shell`` → ``shell`` for a family definition."""
    return key[len(d.key.split("{", 1)[0]):] if d.pattern else ""


def daemon_keys() -> list[str]:
    """Plain config-field keys — the ``PUT /settings`` whitelist (dotted and
    family keys go through the writer's own hooks)."""
    return [
        d.key
        for d in SETTINGS
        if d.store == "config" and d.put and not d.pattern and "." not in d.field_name
    ]


def public_schema() -> dict[str, Any]:
    return {
        "groups": [{"id": g, "label": lbl, "description": desc} for g, lbl, desc in GROUPS],
        "settings": [d.public() for d in SETTINGS],
        "secrets": [s.public() for s in SECRETS],
    }


def _self_check() -> None:
    for d in SETTINGS:
        assert d.group in GROUP_IDS, d.key
        assert d.type in TYPES, d.key
        assert d.tier in TIERS, d.key
        assert d.store in STORES, d.key
        if d.type == "enum":
            assert d.options, d.key
    assert len(BY_KEY) == len(SETTINGS), "duplicate setting key"


_self_check()
