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


S = SettingDef

SETTINGS: tuple[SettingDef, ...] = (
    # ---------------------------------------------------------------- models
    S("default_provider", "Default provider", "models", "string",
      "The AI service used when a chat doesn't pick one. Manage accounts and keys under Connections.",
      tier="ask", aliases=("ai provider", "which ai", "default ai"), placeholder="anthropic"),
    S("default_model", "Default model", "models", "string",
      "The model that answers by default.", tier="allow",
      aliases=("model", "default model", "which model", "use model"), placeholder="claude-opus-4-8"),
    S("default_persona", "Default persona", "models", "string",
      "Used whenever a chat doesn't pick one — including from your phone.", tier="allow",
      aliases=("persona", "assistant style")),
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
    S("voice_transcribe_base_url", "Voice: speech-to-text endpoint", "models", "string",
      "Optional dedicated whisper server for dictation. Its key is set under Connections.",
      tier="ask", section="Voice", advanced=True),
    S("voice_transcribe_model", "Voice: speech-to-text model", "models", "string",
      "The transcription model that server serves. Empty = auto-discover.",
      tier="allow", section="Voice", advanced=True),
    S("voice_vosk_model_path", "Voice: offline model folder", "models", "string",
      "Folder of an offline Vosk speech model.", tier="ask", section="Voice", advanced=True),
    # ------------------------------------------------------------ connections
    S("iron_proxy_enabled", "Iron-Proxy (shared accounts)", "connections", "bool",
      "Pick which signed-in account each Claude/Codex/Grok call uses.",
      tier="ask", put=False, aliases=("iron proxy", "accounts switcher")),
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
    S("mcp_auto_approve", "Run app tools without asking", "permissions", "bool",
      "Let every app (MCP) tool run without an approval card. Not recommended.",
      tier="ask-floor", restart=True, put=False, aliases=("mcp auto approve",)),
    # -------------------------------------------------------------- automation
    S("max_agent_steps", "Max steps per run", "automation", "number",
      "Safety ceiling on how many steps one agent run may take.", tier="ask", check=_range(1, 1000)),
    S("tool_call_timeout_s", "Tool call deadline (seconds)", "automation", "number",
      "How long one tool call may take before it is stopped and recorded as failed. 0 = no deadline.",
      tier="ask", check=_range(0, 86400)),
    S("max_concurrent_sessions", "Runs at once", "automation", "number",
      "How many agent runs may work at the same time. 0 = no cap.", tier="ask", check=_range(0, 64)),
    S("autonomy_enabled", "Autonomy (the pulse)", "automation", "bool",
      "Let Iron Jarvis deliberate on your goals and propose (or, within budget, act).",
      tier="ask-floor", section="Autonomy", aliases=("autonomy",)),
    S("autonomy_level", "Autonomy ceiling", "automation", "enum",
      "How far it may go on its own. High-risk steps are never done automatically.",
      options=(("suggest", "Suggest only"), ("act_low", "Act on low-risk steps"),
               ("act_all", "Act on low- and medium-risk steps")),
      tier="ask-floor", section="Autonomy"),
    S("autonomy_dry_run", "Dry-run mode", "automation", "bool",
      "Propose what it WOULD do without executing anything.",
      tier="allow", floor_when=(False,), section="Autonomy"),
    S("autonomy_kill_switch", "Emergency stop", "automation", "bool",
      "Immediately blocks every self-initiated action.",
      tier="allow", floor_when=(False,), section="Autonomy", aliases=("kill switch", "stop everything")),
    S("autonomy_tick_seconds", "Think every (seconds)", "automation", "number",
      "How often the background loop wakes up.", tier="ask", section="Autonomy", check=_range(10, 86400)),
    S("autonomy_max_actions_per_day", "Max actions / day", "automation", "number",
      "Rolling cap on self-initiated actions.", tier="ask", section="Autonomy", check=_range(0)),
    S("autonomy_max_tokens_per_day", "Max tokens / day", "automation", "number",
      "Rolling token budget for self-initiated work.", tier="ask", section="Autonomy", check=_range(0)),
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
    S("pi_sessions_dir", "Pi sessions folder", "system", "string", "Where Pi coding sessions are read from.",
      tier="ask", advanced=True),
    S("opencode_data_dir", "OpenCode data folder", "system", "string", "Where OpenCode keeps its data.",
      tier="ask", advanced=True),
    # ----------------------------------------------------- device preferences
    S("device.theme", "Theme", "appearance", "string",
      "The theme on this device (a built-in Mark or one of your own).", tier="allow", store="device",
      aliases=("theme", "colors", "dark mode", "light mode")),
    S("device.approval_mode", "Approvals in chat", "permissions", "enum",
      "How chat asks before acting, on this device.",
      options=(("always_ask", "Ask for approval"), ("approve_for_me", "Approve for me"),
               ("yolo", "Auto-approve")),
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
