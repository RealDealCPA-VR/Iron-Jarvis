"""Tool interface (§19).

Every tool exposes name/description/input schema and a permission key, and runs
inside a ``ToolContext`` scoped to a session's isolated workspace (§15).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
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


class Reversibility(str, Enum):
    """Whether a tool's effect can be UNDONE (TX-01 time-travel).

    ``READONLY``     — no side effect, so "undo" is a trivial no-op (reads).
    ``REVERSIBLE``   — mutates state we can capture an inverse for (file write,
                       memory append, settings change) → the registry snapshots
                       the pre-image and ``revert`` restores it.
    ``IRREVERSIBLE`` — the effect leaves the machine (send email/comm, external
                       API POST, generative spend) and CANNOT be taken back.

    Default is IRREVERSIBLE — FAIL-SAFE: a tool that hasn't declared itself is
    treated as non-undoable so we never offer a fake "undone" for something that
    actually left a trace. Str-valued so it serializes straight into the audit
    ledger + the tool.executed event.
    """

    READONLY = "readonly"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class Tool(ABC):
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    #: key looked up in Config.permissions; defaults to ``name``.
    permission_key: str = ""
    #: True when this tool's output is EXTERNALLY-sourced (a file/PDF/note/web
    #: page/memory a third party could have planted). The agent runtime fences
    #: such output as untrusted DATA and scans it for prompt-injection before the
    #: model sees it, so imperatives inside it can't be followed as instructions.
    #: (web_search/browse already self-fence, so they leave this False.)
    returns_untrusted_content: bool = False
    #: TX-01 undo contract. Fail-safe default = IRREVERSIBLE (see enum). A tool
    #: that sets this to REVERSIBLE MUST also implement ``capture_undo`` (return a
    #: non-None inverse descriptor) and ``revert`` — the registry snapshots the
    #: inverse BEFORE the mutation and the /undo endpoint replays it.
    reversibility: Reversibility = Reversibility.IRREVERSIBLE

    def perm_key(self) -> str:
        return self.permission_key or self.name

    async def capture_undo(
        self, args: dict[str, Any], ctx: "ToolContext"
    ) -> "dict[str, Any] | None":
        """Snapshot the INVERSE of this call, taken BEFORE ``execute`` mutates
        anything. Return a small, redaction-safe descriptor the registry stores
        in the undo journal (e.g. ``{"kind": "file_restore", "pre_ref": ...,
        "pre_sha256": ...}``), or ``None`` when there is nothing to undo (a
        no-op) or the capture failed. Only called for ``REVERSIBLE`` tools; the
        default no-op keeps every other tool unaffected."""
        return None

    async def revert(
        self, undo: dict[str, Any], ctx: "ToolContext"
    ) -> ToolResult:
        """Apply the inverse captured by :meth:`capture_undo` — restore the prior
        bytes, delete the created path, drop the appended memory, etc. Must go
        through the same fs-policy/safety checks as the forward mutation. Default:
        honestly report that this tool cannot be undone."""
        return ToolResult(ok=False, error=f"{self.name}: this action cannot be undone")

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
