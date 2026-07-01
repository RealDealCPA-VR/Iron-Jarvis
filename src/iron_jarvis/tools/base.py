"""Tool interface (§19).

Every tool exposes name/description/input schema and a permission key, and runs
inside a ``ToolContext`` scoped to a session's isolated workspace (§15).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid import cycles at runtime
    from sqlalchemy import Engine

    from ..core.config import Config
    from ..core.events import EventBus


@dataclass
class ToolContext:
    workspace: Path
    session_id: str
    agent_run_id: str
    config: "Config"
    event_bus: "EventBus"
    engine: "Engine"


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None


class Tool(ABC):
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    #: key looked up in Config.permissions; defaults to ``name``.
    permission_key: str = ""

    def perm_key(self) -> str:
        return self.permission_key or self.name

    def spec(self) -> dict[str, Any]:
        """Schema advertised to the model (§19 inputSchema)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``args`` safe to PERSIST/return — the tool-invocation
        transcript is written to the DB at rest, returned by session export, and
        baked into backups. Override to drop plaintext secrets so a credential
        never lands unencrypted (which would defeat the Fernet vault). Default:
        unchanged."""
        return args

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ...


def safe_path(workspace: Path, rel: str) -> Path:
    """Resolve ``rel`` under the workspace, enforcing filesystem=workspace_only (§17)."""
    root = workspace.resolve()
    target = (root / rel).resolve()
    if target != root and not target.is_relative_to(root):
        raise PermissionError(f"path '{rel}' escapes the session workspace")
    return target
