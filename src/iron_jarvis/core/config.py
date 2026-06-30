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
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        "web_search": "ask",
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
    comm: dict[str, Any] = Field(default_factory=dict)  # communication channels
    search_roots: list[str] = Field(default_factory=list)  # extra file_search roots
    obsidian_vault: str | None = None  # long-term memory vault path
    notion_database_id: str | None = None  # long-term memory Notion DB
    computer_use: dict[str, Any] = Field(default_factory=default_computer_use)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)  # external MCP servers (mcp_call)
    event_retention_days: int = 0  # 0 = keep forever; >0 prunes old events on boot
    ollama_base_url: str | None = None  # local OpenAI-compatible (Ollama) endpoint URL
    ollama_model: str = "llama3.1"  # default model for the local "ollama" provider
    # Self-tuning router (§6 phase-1) — OFF by default. When enabled AND the local
    # Ollama model is configured AND eval/observability shows it has met the
    # quality bar for a task class, the router prefers it for that class. With the
    # flag off (default) routing is byte-for-byte unchanged and fully offline-safe.
    prefer_local_when_capable: bool = False
    local_quality_bar: float = 0.75  # avg completion a local model must clear
    local_quality_min_samples: int = 3  # evaluated sessions needed before trusting
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


def global_config_path() -> Path:
    return Path.home() / ".ironjarvis" / "config.toml"


def load_config(project_root: str | Path) -> Config:
    """Load merged config for a project root."""
    root = Path(project_root).resolve()
    home = root / ".ironjarvis"

    layered: dict[str, Any] = {}
    layered = _deep_merge(layered, _read_toml(global_config_path()))
    layered = _deep_merge(layered, _read_toml(home / "config.toml"))

    # Merge nested dicts onto code defaults so a partial config file does not
    # wipe out unspecified permission/sandbox keys.
    permissions = _deep_merge(default_permissions(), layered.pop("permissions", {}))
    sandbox = _deep_merge(default_sandbox_policy(), layered.pop("sandbox", {}))

    return Config(
        project_root=root,
        home=home,
        permissions=permissions,
        sandbox=sandbox,
        **{k: v for k, v in layered.items() if k in Config.model_fields},
    )


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
