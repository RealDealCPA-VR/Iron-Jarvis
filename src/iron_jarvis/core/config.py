"""Layered configuration (§8).

Precedence (lowest → highest): built-in defaults → global
``~/.ironjarvis/config.toml`` → project ``<root>/.ironjarvis/config.toml``.
Per-agent overrides are applied later by the agent definition (§20 scope model:
global < project < agent).
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_log = logging.getLogger("iron_jarvis.config")

#: Serializes the read-modify-write of config.toml across the daemon's threads so
#: concurrent persisters (PUT /settings, /autonomy/kill, provider auto-promote)
#: can't lose each other's keys or collide on the temp file (a 500 on Windows).
_CONFIG_WRITE_LOCK = threading.Lock()


def default_permissions() -> dict[str, str]:
    """Default per-tool permission modes (§20 examples + §17 intent)."""
    return {
        "read_file": "allow",
        "write_file": "allow",
        "edit_file": "allow",
        "list_files": "allow",
        "grep": "allow",
        "search_codebase": "deny",  # no tool yet — fail-closed (use grep/file_search)
        "shell": "ask",
        "git_status": "deny",  # no agent git tool yet — fail-closed
        "git_diff": "deny",
        "git_commit": "ask",
        "memory_read": "allow",
        "memory_write": "allow",
        "memory_search": "allow",
        "skill_search": "allow",
        "skill_load": "allow",
        "delegate": "ask",
        # Read-only web retrieval — allow-by-default so headless agent runs and
        # scheduled workflows can research (the engine enforces the same tier;
        # an explicit user "deny" always wins).
        "web_search": "allow",
        "web_fetch": "allow",
        "browser_use": "deny",  # computer-control capability — never default-allow
        "mcp_call": "ask",
        "create_document": "deny",  # superseded by write_document — fail-closed
        "extract_pdf": "allow",
        "image_analysis": "deny",  # no tool yet — fail-closed
        "delete_file": "ask",
        "internet": "ask",
        # Robust feature set: reads are allowed; actions/secret-writes ask.
        "secret_list": "allow",
        "secret_set": "ask",
        "integration_list": "allow",
        "integration_test": "ask",
        "notify": "ask",
        "file_search": "allow",
        "recall": "allow",  # semantic recall across indexed roots + long-term memory
        "ltm_search": "allow",
        "ltm_append": "allow",
        "list_agents": "allow",
        "create_agent": "ask",
        "spawn_agent": "ask",
        # Departments: the shared blackboard. Posting/reading notes and messaging
        # a sibling are low-risk, local, and user-visible — allowed.
        "blackboard_post": "allow",
        "blackboard_read": "allow",
        "message_agent": "allow",
        # Agents authoring their own reusable tools. Listing is read-only; creating
        # or deleting a tool (it runs commands) asks for approval like create_agent.
        # Each created tool runs under "custom:<name>", which defaults to ASK.
        "tool_list": "allow",
        "tool_create": "ask",
        "tool_delete": "ask",
        # Agent self-service (local, user-visible, reversible) — allowed.
        "schedule_create": "allow",
        "webhook_add": "allow",
        "workflow_create": "allow",
        # Documents (read any file type; write within the workspace).
        "read_document": "allow",
        "write_document": "allow",
        "extract_pdf": "allow",
        # Self-correcting learning loop.
        "remember_preference": "allow",
        "recall_lessons": "allow",
        # Motivation Layer: recording a standing goal is local + reversible and
        # never acts on its own (acting is gated by the autonomy dial + budget +
        # autonomy_enabled), so listing/adding goals is allowed.
        "goal_add": "allow",
        "goal_list": "allow",
        # Sentinels: registering an always-on watcher is local + reversible and
        # never acts on its own (a fired Sentinel only mints a suggest-only
        # proposal, and the runner is OFF unless sentinels_enabled), so allowed.
        "sentinel_add": "allow",
        # Computer use (opt-in): reads allowed but still gated by policy.enabled;
        # actions ask. The capability is OFF unless the user enables it.
        "browse": "allow",
        "web_extract": "allow",
        "computer_use_status": "allow",
        "web_action": "ask",
    }


def default_computer_use() -> dict[str, Any]:
    """Computer-use policy (§ best practices) — DISABLED by default."""
    return {
        "enabled": False,
        "domain_allowlist": [],
        "action_allowlist": ["navigate", "read", "extract", "wait"],
        "isolation": "isolated",
        "max_steps": 20,
        "max_retries": 2,
    }


def default_sandbox_policy() -> dict[str, Any]:
    """Default sandbox security policy (§17)."""
    return {
        "filesystem": "workspace_only",
        "internet": "ask",
        "process_spawn": "allow",
        "delete_files": "ask",
        "modify_env": "deny",
        "host_access": "deny",
    }


class Config(BaseModel):
    # Validate on assignment so a bad value via PUT /settings is rejected (400)
    # rather than silently persisted to config.toml and bricking the next boot.
    model_config = ConfigDict(validate_assignment=True)

    project_root: Path
    home: Path
    default_provider: str = "mock"
    default_model: str = "claude-opus-4-8"
    max_agent_steps: int = 12
    permissions: dict[str, str] = Field(default_factory=default_permissions)
    sandbox: dict[str, Any] = Field(default_factory=default_sandbox_policy)
    sandbox_runtime: str = "native"  # "native" | "docker" (§16)
    git_native: bool = False  # run sessions on a git worktree branch (§27)
    # Self-development (opt-in, OFF by default): when enabled, a `self_dev`
    # session runs a Maintainer agent on a git worktree of Iron Jarvis's OWN
    # source so agents can read/edit/fix this project — changes land only via
    # the same review/approve gate (never auto-merge). `self_dev_root` overrides
    # the auto-detected repo path (e.g. when running from an installed package).
    self_dev_enabled: bool = False
    self_dev_root: str | None = None
    default_skills: list[str] = Field(default_factory=list)  # auto-injected (§23)
    # Extra directories to recursively scan for <..>/SKILL.md, on top of the
    # built-in Claude (~/.claude/skills, plugins) + Codex (~/.codex/skills) roots.
    extra_skill_paths: list[str] = Field(default_factory=list)
    # The ACTIVE project (context spine): new sessions/chats default into it,
    # and its brief + recent activity inject into tagged agent calls.
    active_project_id: str | None = None
    comm: dict[str, Any] = Field(default_factory=dict)  # communication channels
    search_roots: list[str] = Field(default_factory=list)  # extra file_search roots
    obsidian_vault: str | None = None  # long-term memory vault path
    notion_database_id: str | None = None  # long-term memory Notion DB
    computer_use: dict[str, Any] = Field(default_factory=default_computer_use)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)  # external MCP servers (mcp_call)
    # Bounded rolling window (was 0 = keep-forever). The persisted event log grows
    # with every session/tool/autonomous tick and is the root of the unbounded
    # EventRecord table that made /metrics, memory-recall, integrity_check and
    # backups scale with uptime. 90 days keeps ample history while capping growth;
    # set 0 to keep forever, or lower to stay leaner. Pruned on boot.
    event_retention_days: int = 90
    ollama_base_url: str | None = None  # local OpenAI-compatible (Ollama) endpoint URL
    ollama_model: str = "llama3.1"  # default model for the local "ollama" provider
    # CUSTOM inference endpoint — any OpenAI-compatible API the user points at:
    # aggregators (OpenRouter has its own built-in provider), Ollama Cloud
    # (https://ollama.com), LM Studio, vLLM, llama.cpp server... The optional key
    # lives in the vault (custom_api_key via Connections); keyless local servers
    # work too.
    custom_base_url: str | None = None
    custom_model: str = ""  # default model id for the "custom" provider
    # SPEECH-TO-TEXT (voice dictation) — an OPTIONAL dedicated transcription
    # backend, so a self-hosted whisper server (faster-whisper-server / Speaches /
    # LocalAI / a Groq endpoint) can be used INDEPENDENTLY of the chat endpoint.
    # This matters because an Ollama LLM server can't transcribe at all, yet it's
    # the `custom` fallback — pointing STT at it only ever 404s. When
    # `voice_transcribe_base_url` is set it wins; when `voice_transcribe_model` is
    # set that exact model is requested (no name guessing). Both empty => fall back
    # to an OpenAI key, then the custom endpoint (with model auto-discovery). The
    # optional key for a dedicated endpoint lives in the vault as
    # `voice_transcribe_key`.
    voice_transcribe_base_url: str = ""  # OpenAI-compatible /v1 base for STT
    voice_transcribe_model: str = ""  # exact model id the STT server serves
    # BUNDLED OFFLINE speech-to-text (Vosk) — a fully local, real-time dictation
    # model that ships with the desktop app (no key, no server, no internet). The
    # desktop app points IRONJARVIS_VOSK_MODEL at its bundled copy; this override
    # lets a dev/user name a model dir explicitly. Empty => also look at
    # <home>/vosk-model. When a model is found, /voice/status prefers it and the
    # client streams over the /voice/stream WebSocket.
    voice_vosk_model_path: str = ""
    # User-added REST integrations (id/name/description); re-registered at boot so
    # they survive a restart. Their per-instance config (base_url, auth secret
    # NAME) lives in the IntegrationRecord table; the token lives in the vault.
    custom_integrations: list[dict[str, Any]] = Field(default_factory=list)
    # LOCAL FLEET — extra inference nodes ON TOP of the two endpoint slots above
    # (ollama_base_url / custom_base_url), which are auto-seeded into the fleet, so
    # this stays empty by default and the feature works with zero setup. Same
    # list-of-dict shape as mcp_servers/custom_integrations; managed by /fleet/nodes
    # (NOT via PUT /settings — a UI round-trip of a complex list loses nodes).
    fleet_nodes: list[dict[str, Any]] = Field(default_factory=list)
    fleet_sampling_enabled: bool = True  # background telemetry loop (30s idle)
    fleet_sampling_seconds: int = 30  # idle cadence; a watched page samples at 2s
    #: "provider:model" the local-vs-cloud savings estimate is priced against —
    #: an estimate is only honest when its BASIS is named. "" = the built-in default.
    fleet_savings_baseline: str = ""
    # Code routing (Wave 2) — send coding work to a chosen local model. OFF by
    # default; with it off, routing is byte-for-byte unchanged.
    fleet_code_route_enabled: bool = False
    fleet_code_target: str = ""  # "provider:model" (providers/routing.parse_pm)
    fleet_code_task_classes: str = ""  # CSV override; "" = the built-in set
    # Self-tuning router (§6 phase-1) — OFF by default. When enabled AND the local
    # Ollama model is configured AND eval/observability shows it has met the
    # quality bar for a task class, the router prefers it for that class. With the
    # flag off (default) routing is byte-for-byte unchanged and fully offline-safe.
    prefer_local_when_capable: bool = False
    local_quality_bar: float = 0.75  # avg completion a local model must clear
    local_quality_min_samples: int = 3  # evaluated sessions needed before trusting
    # Auto model routing (§6 — the routing model). OFF unless the user selects
    # "Auto" (``default_provider == "auto"``). ``routing_model`` is the cheap
    # classifier ("provider:model"); ``routing_tiers_json`` optionally overrides
    # the light/standard/heavy targets (else derived from connected models). With
    # Auto off, routing is byte-for-byte identical to before.
    routing_model: str = ""
    routing_tiers_json: str = ""
    # Embeddings (§22 Total Recall): pick a real local embedder when one is
    # reachable, else the offline MockEmbedder. "auto" probes Ollama once and
    # falls back silently; "ollama" forces the real path (still safe-fallback if
    # unreachable); "mock" pins the deterministic offline embedder.
    embedder_provider: str = "auto"  # "auto" | "ollama" | "mock"
    embedder_model: str = "nomic-embed-text"  # local embedding model (Ollama)
    # Motivation Layer ("the pulse") — OFF by default, exactly like computer_use
    # and self_dev. When disabled NO deliberation tick runs and no goal acts, so
    # the default install + the offline test suite are untouched.
    autonomy_enabled: bool = False
    # Global dial ceiling: caps EVERY goal's own dial (suggest < act_low < act_all).
    # "suggest" => every deliberated action is a proposal, never auto-executed.
    autonomy_level: str = "suggest"  # suggest | act_low | act_all
    autonomy_dry_run: bool = False  # log/propose what it WOULD do, never execute
    autonomy_kill_switch: bool = False  # global emergency stop (POST /autonomy/kill)
    autonomy_tick_seconds: int = 900  # deliberation cadence (background loop)
    autonomy_max_actions_per_day: int = 5  # global rolling self-initiated action cap
    autonomy_max_tokens_per_day: int = 50000  # global rolling self-initiated token cap
    # Sentinels ("always-on watchers") — OFF by default, exactly like autonomy and
    # computer_use. When disabled NO watcher runs and nothing is polled, so the
    # default install + the offline test suite are untouched. A fired Sentinel
    # only mints a SUGGEST-ONLY proposal into the Motivation Layer backlog; it
    # never executes (the autonomy dial + budget + approval still gate any action).
    sentinels_enabled: bool = False
    sentinels_tick_seconds: int = 300  # filesystem poll cadence (background loop)
    # CX-05 "inbound everything" — calendar trigger. OFF by default, exactly like
    # autonomy/sentinels. When enabled AND a secret ICS URL is stored (vault key
    # "calendar_ics_url"), a background loop polls the calendar and fires matching
    # `calendar` reflex rules for events coming due within `lead_minutes`. Email
    # triggers ride the existing per-channel comm inbound toggle (no global flag),
    # and Slack triggers ride the existing Slack channel/socket path.
    calendar_trigger_enabled: bool = False
    calendar_tick_seconds: int = 300  # calendar poll cadence (background loop)
    calendar_lead_minutes: int = 15  # fire when an event starts within this window

    @field_validator("autonomy_level")
    @classmethod
    def _valid_autonomy_level(cls, v: str) -> str:
        # Reject a bad /settings value (422) rather than persist it + skew the
        # global ceiling. validate_assignment=True applies this on PUT /settings too.
        if v not in ("suggest", "act_low", "act_all"):
            raise ValueError("autonomy_level must be suggest | act_low | act_all")
        return v

    @property
    def db_path(self) -> Path:
        return self.home / "ironjarvis.db"

    @property
    def workspaces_dir(self) -> Path:
        return self.home / "workspaces"

    @property
    def browser_dir(self) -> Path:
        return self.home / "browser"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def artifacts_dir(self) -> Path:
        return self.home / "artifacts"

    def ensure_dirs(self) -> None:
        for d in (
            self.home,
            self.workspaces_dir,
            self.browser_dir,
            self.memory_dir,
            self.artifacts_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        # A torn/corrupt config (e.g. a crash mid-write) must NOT abort boot —
        # fall back to defaults loudly so the daemon still starts and can be fixed
        # from within the app, instead of being wedged before it can self-correct.
        _log.error("ignoring unreadable config %s: %s", path, exc)
        return {}


def atomic_write_toml(path: Path, doc: dict[str, Any]) -> None:
    """Write ``doc`` to ``path`` crash-safely: dump to a UNIQUE sibling temp then
    ``os.replace`` (atomic on the same filesystem). A power loss mid-write leaves
    either the old file or the new one — never a truncated config that bricks boot.
    A unique temp name (not a fixed ``.tmp``) means two concurrent writers can't
    clobber each other's temp or fail os.replace. ``None`` values are dropped."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            tomli_w.dump({k: v for k, v in doc.items() if v is not None}, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def persist_config_values(home: str | Path, values: dict[str, Any]) -> None:
    """Merge ``values`` into ``<home>/config.toml`` atomically AND concurrency-safely.

    The whole read-merge-write is held under a process lock so two overlapping
    persisters (e.g. a settings save racing the kill-switch persist) can't read the
    same base and drop one another's keys — without it, a lost kill-switch write
    would silently re-enable autonomy on the next boot."""
    path = Path(home) / "config.toml"
    with _CONFIG_WRITE_LOCK:
        doc = _read_toml(path)
        doc.update(values)
        atomic_write_toml(path, doc)


#: A config KEY whose name matches one of these fragments is treated as carrying
#: a plaintext credential and is NEVER snapshotted into the undo journal (that
#: would spill a secret into the DB + backups, defeating the encrypted vault).
#: None of the current settings keys carry secrets — credentials live in the
#: Fernet vault — but this fails safe if one is ever added.
_SECRET_KEY_FRAGMENTS = ("key", "secret", "token", "password", "passwd", "credential")


def is_secret_config_key(key: str) -> bool:
    """True when ``key`` looks like it holds a plaintext secret (see above)."""
    k = key.lower()
    return any(frag in k for frag in _SECRET_KEY_FRAGMENTS)


def capture_config_undo(cfg: "Config", keys: list[str]) -> dict[str, Any]:
    """Snapshot the PRIOR values of ``keys`` for a settings-change undo (TX-01).

    Returns a ``setting_restore`` descriptor: ``prior`` maps each SAFE key to its
    value before the change, and ``skipped`` lists secret-looking keys that were
    deliberately NOT captured (so a credential never lands in the undo journal in
    plaintext). Reverting applies :func:`restore_config_values` to ``prior``."""
    prior: dict[str, Any] = {}
    skipped: list[str] = []
    for key in keys:
        if is_secret_config_key(key):
            skipped.append(key)
            continue
        prior[key] = getattr(cfg, key, None)
    return {"kind": "setting_restore", "prior": prior, "skipped": skipped}


def restore_config_values(cfg: "Config", prior: dict[str, Any]) -> list[str]:
    """Re-apply prior config values (the inverse of a settings change) to the live
    ``cfg`` AND persist them, so the restore survives a restart. Returns the keys
    restored. A single invalid value is skipped rather than aborting the whole
    revert (validate_assignment would raise on assignment)."""
    updated: list[str] = []
    for key, value in prior.items():
        try:
            setattr(cfg, key, value)
        except Exception:  # noqa: BLE001 — pydantic validation on assignment
            continue
        updated.append(key)
    if updated:
        persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in updated})
    return updated


def global_config_path() -> Path:
    return Path.home() / ".ironjarvis" / "config.toml"


def resolve_home(project_root: str | Path) -> Path:
    """The state home (DB, secrets, memory, sessions, schedules, workspaces).

    ``IRONJARVIS_HOME`` (when set) DECOUPLES all persistent state from the
    per-invocation project directory, so ONE Iron Jarvis brain — one vault of
    provider logins/keys, one memory, one session history — serves EVERY project
    the owner works in (the "daily driver for all projects" model). Unset (the
    default) keeps the per-project ``<project_root>/.ironjarvis`` home, so existing
    behavior is unchanged and each project stays fully isolated."""
    override = os.environ.get("IRONJARVIS_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(project_root).resolve() / ".ironjarvis"


def load_config(project_root: str | Path) -> Config:
    """Load merged config for a project root."""
    root = Path(project_root).resolve()
    home = resolve_home(root)

    layered: dict[str, Any] = {}
    layered = _deep_merge(layered, _read_toml(global_config_path()))
    layered = _deep_merge(layered, _read_toml(home / "config.toml"))

    # Merge nested dicts onto code defaults so a partial config file does not
    # wipe out unspecified permission/sandbox keys.
    permissions = _deep_merge(default_permissions(), layered.pop("permissions", {}))
    sandbox = _deep_merge(default_sandbox_policy(), layered.pop("sandbox", {}))

    overrides = {k: v for k, v in layered.items() if k in Config.model_fields}
    try:
        return Config(
            project_root=root, home=home, permissions=permissions, sandbox=sandbox, **overrides
        )
    except ValidationError as exc:
        # Self-heal a hand-edited config.toml with a WRONG-TYPED value (e.g. a
        # quoted number) the same way the DB self-heals a corrupt file: drop just
        # the offending keys (fall back to their defaults) and retry, so a single
        # typo in the primary user-edited file never bricks boot. Torn/unreadable
        # TOML is already handled by _read_toml.
        bad = {str(e["loc"][0]) for e in exc.errors() if e.get("loc")}
        for key in bad:
            overrides.pop(key, None)
            _log.error("ignoring invalid config value for %r — using its default", key)
        if "permissions" in bad:
            permissions = default_permissions()
        if "sandbox" in bad:
            sandbox = default_sandbox_policy()
        try:
            return Config(
                project_root=root, home=home, permissions=permissions, sandbox=sandbox, **overrides
            )
        except ValidationError:
            _log.error("config.toml has multiple invalid values — falling back to all defaults")
            return Config(project_root=root, home=home)


def write_default_config(project_root: str | Path) -> Path:
    """Write a starter project config file; returns its path."""
    root = Path(project_root).resolve()
    home = root / ".ironjarvis"
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    if not path.exists():
        doc = {
            "default_provider": "mock",
            "default_model": "claude-opus-4-8",
            "max_agent_steps": 12,
            "permissions": default_permissions(),
            "sandbox": default_sandbox_policy(),
        }
        with path.open("wb") as fh:
            tomli_w.dump(doc, fh)
    return path
