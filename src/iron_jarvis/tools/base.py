"""Tool interface (§19).

Every tool exposes name/description/input schema and a permission key, and runs
inside a ``ToolContext`` scoped to a session's isolated workspace (§15).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    #: The chat turn this call belongs to (v1.276.0) — the panel's `turn_id`
    #: for a sidebar turn, "" elsewhere. A browser tool's risk door hands it
    #: to the approval resolver, which answers only for ITS OWN turn: a card
    #: for a chat-page click must never land in the sidebar.
    turn_id: str = ""
    #: The device the chat turn came from (calm UI redesign S3): per-device
    #: settings (theme, approval default…) changed from chat land on THIS
    #: device. "" when the caller sent none (headless, an older client).
    device_id: str = ""
    #: The SAVED chat this call was asked in (v1.346.0, media in chat): both
    #: chat lanes set it from ``body.thread_id``, the agent runtime from the
    #: run's ``options["chat_thread_id"]`` (v1.345.0). A generation that
    #: finishes in the background comes home to THIS chat; "" = no chat is
    #: waiting (an unsaved chat, a schedule), so nothing is appended anywhere.
    thread_id: str = ""
    #: The name the MODEL used for this call when an IJ harness dialect
    #: translated it (v1.366.0, ``harness/dialects.py``) — ``Read`` for a call
    #: that ran ``read_file``. The registry records it on the ledger row
    #: (``ToolInvocation.called_as``); "" when the model used the real name.
    called_as: str = ""


@dataclass
class SpendCheck:
    """A tool's own answer to "will this call cost money, and should a human
    be asked first?" (v1.346.0, media in chat).

    ``ask`` True = show the approval card whatever the posture (a paid or
    unknown price asks before EVERY spend — the user's rule); False = this
    call is free by the provider's own verdict (Pixio's Maker allowance, a
    plan-covered model) and runs without a card. ``words`` is the sentence the
    card shows (``Generate 1 image on Pixio for 12 credits (your price)``);
    ``cost_note`` the short form the tool output, the ledger row and the bell
    carry (``free (Maker: 4 left today)``). Never carries a key.
    """

    ask: bool
    words: str = ""
    cost_note: str = ""


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
    #: READ SINCE v1.237.0, and it was inert before that — this note used to say
    #: so, and updating it is part of shipping the read site rather than an
    #: afterthought. Two things consult it now: ``ToolRegistry.invoke`` puts it on
    #: the ``tool.executed`` / ``tool.denied`` payloads (D24), and the browser's
    #: acting tools pass their declared class to ``browser.risk`` to decide whether
    #: a call needs approval. Neither LOWERS a verdict: an attribute can raise the
    #: bar, never drop it, and ``DENY_FLOOR_TOOLS`` stays authoritative — otherwise
    #: a dynamically created tool could declare its way down off the floor.
    risk_class: RiskClass = RiskClass.EXTERNAL_COMMIT
    #: OUTSIDE THE PER-CALL DEADLINE (v1.288.0). True only for a tool whose
    #: ``execute`` awaits a WHOLE sub-agent run (``delegate``, ``spawn_agent``).
    #: The v1.228.0 deadline (``config.tool_call_timeout_s``) is a guard for a
    #: wedged subprocess/MCP/HTTP call; applied to a sub-agent it killed a
    #: worker still doing real work at minute 10 and recorded it "cancelled by
    #: the user". Such a run is bounded by its OWN step budget, its own
    #: per-tool deadlines, the provider timeouts and the user's Cancel (which
    #: reaches it through the parent). ``ToolRegistry.invoke`` reads this — the
    #: one place — so every lane (agent runtime, both chat lanes) obeys it.
    deadline_exempt: bool = False
    #: PACK-TOOL QUARANTINE (v1.299.0). True for an external (MCP) tool that
    #: can write or destroy and APPEARED LATER than the pack it belongs to —
    #: or changed shape — so the user never knowingly installed it. A
    #: quarantined tool asks regardless of its pack's auto-approve until the
    #: user trusts it (``POST /mcp/servers/{s}/tools/{t}/trust``). Set only by
    #: ``mcp/tools.apply_quarantine`` from the persisted pack manifest; every
    #: built-in keeps the default. Read by the platform's MCP auto-approve
    #: resolver wrapper (never LOWERS a verdict — it can only turn an automatic
    #: yes back into a question) and advertised in ``spec()`` only when set.
    quarantined: bool = False
    #: SAFE TO RUN AGAIN (v1.335.0). Whether a call that a crash or restart cut
    #: off may simply be run again. ``None`` (the default) derives it: only a
    #: READONLY tool is safe — anything else may already have changed the world
    #: before the process died, so it is reported as "may have landed" and is
    #: never rerun by itself. Read through :func:`replay_safe`, never directly.
    replay: bool | None = None

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

    async def spend_check(
        self, args: dict[str, Any], ctx: "ToolContext"
    ) -> "SpendCheck | None":
        """What this call would SPEND, decided by the tool itself (v1.346.0).

        Consulted by the stream chat lane and the agent runtime BEFORE their
        approval gate, once per call: ``None`` = no opinion (the ordinary
        permission verdict decides, as for every tool today); a
        :class:`SpendCheck` with ``ask`` False lets an ASK-tier call run
        without a card for THIS call only (the lane carries the grant into
        the registry — the v1.270.1 rule); ``ask`` True shows the card with
        ``words`` on it. The default is no opinion, so every existing tool is
        untouched. A check must never raise for a plain refusal — return
        ``None`` and let ``execute`` say the words."""
        return None

    def spec(self) -> dict[str, Any]:
        """Schema advertised to the model (§19 inputSchema)."""
        spec = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        # Additive and ONLY when set (v1.299.0): every provider adapter picks
        # name/description/input_schema by key, so an extra key never reaches
        # a strict API, and a spec without the key reads byte-identically to
        # before for the tools that never quarantine (all built-ins).
        if getattr(self, "quarantined", False):
            spec["quarantined"] = True
        return spec

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


async def spend_checks_for(
    registry: Any, calls: Any, ctx: "ToolContext", names: "set[str] | None" = None
) -> "dict[int, SpendCheck]":
    """ONE spend check per call of a round (v1.346.0), keyed by ``id(call)``
    — the call OBJECT, because an adapter may send "" for every call id.

    Only tools that carry an opinion land in the map; a tool whose check
    raises is "no opinion" (the ordinary verdict decides, never a crashed
    turn). ``names`` narrows to the armed set — an unarmed call is refused by
    the registry and must not be quoted. The ONE helper both chat lanes and
    the agent runtime call, so the three gates cannot drift."""
    out: dict[int, SpendCheck] = {}
    for tc in calls or []:
        name = str(getattr(tc, "name", "") or "")
        if names is not None and name not in names:
            continue
        try:
            tool = registry.get(name)
        except Exception:  # noqa: BLE001 — a double without get
            tool = None
        fn = getattr(tool, "spend_check", None) if tool is not None else None
        if fn is None:
            continue
        args = getattr(tc, "arguments", None)
        try:
            verdict = await fn(args if isinstance(args, dict) else {}, ctx)
        except Exception:  # noqa: BLE001 — no opinion, never a stopped turn
            verdict = None
        if verdict is not None:
            out[id(tc)] = verdict
    return out


def spend_words_for(checks: Any) -> str:
    """The card sentence for ONE card that covers SEVERAL calls (v1.346.0,
    X-review): a round's same-permission asks share one card (v1.247.0), so
    a batch of two generations — an image and a video — must show BOTH
    quotes, not the first call's alone. Each distinct sentence once, in call
    order, "; "-joined; a single-call batch reads exactly its own words. The
    ONE helper the stream lane's card and the runtime's ask both call."""
    out: list[str] = []
    for check in checks:
        words = str(getattr(check, "words", "") or "").strip() if check is not None else ""
        if words and words not in out:
            out.append(words)
    return "; ".join(out)


def replay_safe(tool: Any) -> bool:
    """Whether a cut-off call of ``tool`` may be run again (v1.335.0).

    An explicit ``Tool.replay`` bool wins; otherwise only a READONLY tool is
    safe. ``None`` or an unknown tool is NOT safe — the fail-safe direction,
    for the same reason ``reversibility`` defaults to IRREVERSIBLE."""
    if tool is None:
        return False
    explicit = getattr(tool, "replay", None)
    if isinstance(explicit, bool):
        return explicit
    rev = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
    return rev == Reversibility.READONLY or rev == Reversibility.READONLY.value


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
