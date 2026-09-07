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
    #: The grounded project, when the caller resolved one (v1.200.0). Chat runs
    #: as session_id="chat" — not a Session row — so artifact sinks could never
    #: inherit a project from the session table; this field is how a
    #: project-grounded chat turn's generations reach the project's Media view
    #: instead of stranding in the global gallery with project_id NULL.
    project_id: str | None = None


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None
    #: Files this call CREATED whose names it could not predict (v1.157.0).
    #:
    #: ``capture_undo`` runs BEFORE ``execute``, so a tool that learns its
    #: filenames from the work itself — a remote agent's reply, a batch job —
    #: has no way to journal them, and anything derived from the journal
    #: (``agents/outcome``, the run's result card, the preview rail) never
    #: hears about them. Reporting them here lets the registry journal the
    #: creations after the fact, through the same path every other file
    #: mutation takes. Absolute paths.
    created_paths: list[str] | None = None


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


class RiskClass(str, Enum):
    """How far beyond reading a tool call can reach (v1.235.0, D12).

    ``READ``            — observes only.
    ``LOCAL_UI``        — moves the user's own view; no page/document state changes.
    ``PAGE_ACTION``     — changes a page's (or a document's) state.
    ``EXTERNAL_COMMIT`` — money, identity, deletion, sending.

    Fail-safe default is EXTERNAL_COMMIT for the same reason
    :class:`Reversibility` defaults to IRREVERSIBLE: a tool that forgets to
    declare must not be treated as harmless. The silent failure that default
    prevents is a new tool inheriting "this is only a read" from the base class
    and then appearing on a ledger row, an approval card, or a future
    discovery filter as safe — with nothing in the diff to notice.

    Orthogonal to :class:`Reversibility` (undoability) and to
    :class:`~iron_jarvis.core.models.PermissionMode` (the resolved per-install
    verdict). ``str``-valued so it serialises straight into the audit ledger and
    the ``tool.executed`` event; a bare :class:`Enum` renders as
    ``"RiskClass.READ"`` inside an f-string and would ship that to the model.
    """

    READ = "read"
    LOCAL_UI = "local_ui"
    PAGE_ACTION = "page_action"
    EXTERNAL_COMMIT = "external_commit"


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
    #: RISK CLASS (v1.235.0, D12). How far beyond reading this call can reach.
    #: Fail-safe default = EXTERNAL_COMMIT (see the enum). Read for LOGGING and
    #: for a tool's own escalation decision only: it never LOWERS a permission
    #: verdict, and ``DENY_FLOOR_TOOLS`` stays authoritative, so a tool cannot
    #: declare its way down off the floor.
    #:
    #: IN v1.235.0 NOTHING READS IT YET. The ledger/event read site and the
    #: escalation that consults it land with the acting browser tools (plan §8.1
    #: and §8.2, v1.237.0). It is declared now so the fourteen browser tools can
    #: carry it from the start, and it is documented as inert on purpose: an
    #: attribute that LOOKS like a live gate is worse than an absent one, because
    #: a later reader will trust it to be enforcing something.
    risk_class: RiskClass = RiskClass.EXTERNAL_COMMIT

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


def unwritable_workspace_error(exc: PermissionError, workspace: Path) -> str:
    """The model-facing text for a ``PermissionError`` raised by a WRITE tool
    (v1.228.0, audit T3).

    Two different things arrive as ``PermissionError``: the confinement
    refusal :func:`safe_path` raises (no ``errno``; its message names the
    escaping path and is kept verbatim) and the OS refusing the write itself
    (``errno`` set — EACCES/EPERM). The OS message names whatever file the
    writer was creating, which for an atomic write is a hidden sibling
    ``.<name>.tmp-<pid>`` the user can neither see nor fix; say instead what
    is true and actionable: the folder the session is bound to is not
    writable.
    """
    if getattr(exc, "errno", None) is None:
        return f"{type(exc).__name__}: {exc}"
    return (
        f"cannot write in {workspace}: the folder this session is bound to is "
        "not writable — pick a folder you can save files in"
    )
