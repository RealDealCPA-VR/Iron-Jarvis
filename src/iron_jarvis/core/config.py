"""Layered configuration (§8).

Precedence (lowest → highest): built-in defaults → global
``~/.ironjarvis/config.toml`` → project ``<root>/.ironjarvis/config.toml``.
Per-agent overrides are applied later by the agent definition (§20 scope model:
global < project < agent).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field


def default_permissions() -> dict[str, str]:
    """Default per-tool permission modes (§20 examples + §17 intent)."""
    return {
        "read_file": "allow",
        "write_file": "allow",
        "edit_file": "allow",
        "list_files": "allow",
        "grep": "allow",
        "search_codebase": "allow",
        "shell": "ask",
        "git_status": "allow",
        "git_diff": "allow",
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
        "create_document": "allow",
        "extract_pdf": "allow",
        "image_analysis": "allow",
        "delete_file": "ask",
        "internet": "ask",
        # Robust feature set: reads are allowed; actions/secret-writes ask.
        "secret_list": "allow",
        "secret_set": "ask",
        "integration_list": "allow",
        "integration_test": "ask",
        "notify": "ask",
        "file_search": "allow",
        "ltm_search": "allow",
        "ltm_append": "allow",
        "list_agents": "allow",
        "create_agent": "ask",
        "spawn_agent": "ask",
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
    project_root: Path
    home: Path
    default_provider: str = "mock"
    default_model: str = "claude-opus-4-8"
    max_agent_steps: int = 12
    permissions: dict[str, str] = Field(default_factory=default_permissions)
    sandbox: dict[str, Any] = Field(default_factory=default_sandbox_policy)
    sandbox_runtime: str = "native"  # "native" | "docker" (§16)
    git_native: bool = False  # run sessions on a git worktree branch (§27)
    default_skills: list[str] = Field(default_factory=list)  # auto-injected (§23)
    comm: dict[str, Any] = Field(default_factory=dict)  # communication channels
    search_roots: list[str] = Field(default_factory=list)  # extra file_search roots
    obsidian_vault: str | None = None  # long-term memory vault path
    notion_database_id: str | None = None  # long-term memory Notion DB
    computer_use: dict[str, Any] = Field(default_factory=default_computer_use)

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
    with path.open("rb") as fh:
        return tomllib.load(fh)


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
