"""``capability_propose`` — the agent-facing way to SAY the app is missing a verb.

WHY A TOOL AND NOT PROSE. The agent could already have written "I have no way to
rename a file" in its final summary. It did not — it shelled out and wrote
PyMuPDF scripts to re-read PDFs it had already read successfully (25 ``shell``
calls, ledger ``run_ab82dea4bf8a``), across four attempts at the same job, and
the user learned about the gap by watching the run fail. Prose is optional,
unstructured, lost when a run is trimmed, and invisible to every surface. A tool
call carries a typed payload the store validates, an ordinary ``ToolInvocation``
row so the request is auditable back to the run that hit the wall, and a queue
the user can actually see.

WHAT IT CAN AND CANNOT DO, and the tool says both out loud:

* it FILES one ``pending`` row. No tool is created, no permission is written, no
  server is added, nothing on disk changes;
* it cannot grant itself anything. An approved custom tool still runs under
  ``custom:<name>`` at ``ask``, and the deny-floor tools (``shell``, ``repl``,
  ``browser_use``, ``web_action``, ``mcp_call``) can never be requested at all —
  :func:`~iron_jarvis.capability.store.floor_violation` refuses those HERE, where
  the model can still ask for something narrower, and again at approve time,
  where the click happens.

PERMISSION TIER: ``allow``, for exactly the reason ``memory_propose`` is allowed
(v1.142 lesson). A tool with no entry in ``core/config.py::default_permissions``
resolves to ``ask``, and an ``ask`` with no interactive resolver is a DENY in the
headless daemon — which is the lane every agent run takes. A tool for reporting
"I cannot do this" that is itself silently denied would reproduce the exact
failure it exists to end. Filing is strictly weaker than ``ltm_append``, which
the same session already holds and which really does write a file.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..core.logging import get_logger
from ..tools.base import Tool, ToolContext, ToolResult
from .models import KIND_LABELS, KINDS, signature_for
from .store import CapabilityProposalStore, floor_violation, parameter_violation

log = get_logger("capability.propose")

#: A proposed TOOL name has to satisfy ``tools/dynamic._NAME_RE`` or
#: ``tool_create`` would refuse it at APPROVE time — hours later, with the user
#: watching a button that does nothing. Screened here, where the model can fix
#: it, exactly as ``memory_propose`` screens a ``text`` without a ``survivor_ref``.
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

#: mcp/connection names are shown to a human, never passed to ``tool_create``,
#: so they may carry spaces and dots ("Box (cloud files)" is a real catalog id).
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-/]{0,63}$")

#: The ``{placeholder}`` spelling ``tools/dynamic.CommandTool._render`` fills.
#: Kept in step with that renderer: it substitutes ONLY names it was given as
#: parameters, so a placeholder with no parameter is left as literal text.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _as_list(value: Any) -> list[str]:
    """A ``command`` argument as an argv list.

    Models send a list, or a single string with the whole command in it.
    A STRING IS REFUSED rather than split on whitespace: splitting invents an
    argv the model did not write ("C:/Program Files/x.exe" becomes two arguments)
    and the result would be created as a real tool on approval. Refusing keeps
    the mistake in front of the model, which can retry in one turn.
    """
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return []


class CapabilityProposeTool(Tool):
    """Ask the user for a capability this app does not have. Changes nothing."""

    name = "capability_propose"
    permission_key = "capability_propose"
    description = (
        "Ask the user to ADD a capability this app does not have — use it the "
        "moment you find yourself with no tool for the job, INSTEAD of working "
        "around it with shell or hand-written scripts. It files a request the "
        "user reviews and changes nothing by itself: nothing is installed, no "
        "permission is granted, and you do NOT get the capability in this run. "
        "Set `kind` to 'tool' for a command this computer can already run "
        "(give the exact `command` argv and its `parameters`), 'mcp' for an MCP "
        "server, or 'connection' for an account/service. Say WHY in one or two "
        "plain sentences tied to the task you are doing right now, and say in "
        "`allowed_to` precisely what it would be permitted to do. After filing, "
        "TELL THE USER IN YOUR REPLY that you asked for it and what you could "
        "not finish without it — a request nobody reads is the same as silence. "
        "Then carry on with the tools you do have. You cannot ask for shell, "
        "repl, browser_use, web_action or mcp_call; ask for the one narrow thing "
        "the job needs."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "tool (a command to run) | mcp (an MCP server) | "
                "connection (an account or service to connect).",
            },
            "name": {
                "type": "string",
                "description": "What to call it. For kind=tool this becomes the "
                "tool name, so use an identifier: letters, digits, underscore, "
                "starting with a letter (e.g. \"pdf_page_count\").",
            },
            "why": {
                "type": "string",
                "description": "WHY, in one or two plain sentences the user "
                "reads verbatim, tied to the task in hand — what you are doing, "
                "what you could not do, what you had to do instead.",
            },
            "allowed_to": {
                "type": "string",
                "description": "Exactly what it would be permitted to do, in one "
                "line. This is the sentence the user approves.",
            },
            "task": {
                "type": "string",
                "description": "The job you are on, in one line (e.g. \"rename 26 "
                "files in C:/clients/2025 to match their contents\").",
            },
            "command": {
                "type": "array",
                "items": {"type": "string"},
                "description": "kind=tool ONLY: the argv to run, program first, "
                "using {param} placeholders that get filled from the call "
                "arguments (e.g. [\"qpdf\", \"--pages\", \"{file}\", \"--\", "
                "\"{out}\"]). Not a shell line — no pipes, no &&.",
            },
            "parameters": {
                "type": "array",
                "items": {"type": "object"},
                "description": "kind=tool ONLY: one object per placeholder, "
                "{name,type,required,description}.",
            },
            "timeout_seconds": {"type": "integer"},
            "details": {
                "type": "string",
                "description": "kind=mcp/connection: the concrete thing you "
                "want — the server package or the service and account.",
            },
        },
        "required": ["kind", "name", "why", "allowed_to"],
    }

    def __init__(self, store: CapabilityProposalStore) -> None:
        self.store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        kind = str(args.get("kind") or "").strip().lower()
        name = str(args.get("name") or "").strip()
        why = str(args.get("why") or "").strip()
        allowed_to = str(args.get("allowed_to") or "").strip()
        task = str(args.get("task") or "").strip()
        command = _as_list(args.get("command"))
        params = args.get("parameters")
        details = str(args.get("details") or "").strip()

        # -- argument honesty (a refusal the model can FIX, never a crash) ----
        if kind not in KINDS:
            return ToolResult(
                ok=False,
                error=f"capability_propose: 'kind' must be one of {', '.join(KINDS)}",
            )
        if not name:
            return ToolResult(
                ok=False,
                error="capability_propose: 'name' is required — say what to call it",
            )
        if kind == "tool" and not _TOOL_NAME_RE.match(name):
            return ToolResult(
                ok=False,
                error=(
                    "capability_propose: a tool name must be an identifier "
                    "(a letter, then letters/digits/underscore) — otherwise the "
                    "user would click Approve and nothing could be created"
                ),
            )
        if kind != "tool" and not _LABEL_RE.match(name):
            return ToolResult(
                ok=False,
                error="capability_propose: 'name' must be a short plain name",
            )
        if not why:
            return ToolResult(
                ok=False,
                error="capability_propose: 'why' is required — the user reads it "
                "verbatim before deciding",
            )
        if not allowed_to:
            return ToolResult(
                ok=False,
                error="capability_propose: 'allowed_to' is required — one line "
                "saying exactly what it would be permitted to do",
            )
        if kind == "tool" and not command:
            return ToolResult(
                ok=False,
                error=(
                    "capability_propose: kind=tool needs 'command' as an argv "
                    "ARRAY (program first, then arguments). A tool with no "
                    "command cannot be created, so it would be an un-approvable "
                    "card on the user's screen"
                ),
            )

        spec: dict[str, Any] = {}
        if command:
            spec["command"] = command
        if isinstance(params, list) and params:
            spec["parameters"] = params
        timeout = args.get("timeout_seconds")
        if isinstance(timeout, int) and timeout > 0:
            spec["timeout_seconds"] = timeout
        if details:
            spec["details"] = details

        # THE DENY FLOOR, at file time. The store checks it again at approve
        # time (that is the safety property); this copy exists so the model
        # learns NOW that it must ask for something narrower, instead of waiting
        # for a user to click a button that refuses.
        violation = floor_violation(kind, name, spec)
        if violation:
            return ToolResult(ok=False, error=f"capability_propose: {violation}")

        # A SHAPE THAT WOULD BRICK THE NEXT BOOT (see store.parameter_violation).
        # `parameters: ["file"]` — the shape a model reaches for — is persisted
        # by tool_create BEFORE it is built, so approving it leaves a record
        # that raises AttributeError in `build_platform` at every later start.
        # Refused here, where the model can still send objects, and again at the
        # click. Note this screens the RAW argument, not `spec`: a non-list was
        # silently dropped on the way into the spec, which turned a fixable
        # mistake into a tool whose placeholders never get filled.
        bad_params = parameter_violation(params) if kind == "tool" else ""
        if bad_params:
            return ToolResult(ok=False, error=f"capability_propose: {bad_params}")
        # ...and the mirror image: an argv carrying {placeholders} with no
        # `parameters` to fill them creates a tool that runs the literal text
        # "{file}" forever. `CommandTool._render` substitutes only NAMED
        # parameters, so this is silent — the approval succeeds and the tool is
        # simply always wrong.
        holes = sorted(
            set(_PLACEHOLDER_RE.findall(" ".join(command)))
            - {str(p.get("name") or "").strip() for p in (params or []) if isinstance(p, dict)}
        )
        if kind == "tool" and holes:
            return ToolResult(
                ok=False,
                error=(
                    "capability_propose: the command uses "
                    + ", ".join("{" + h + "}" for h in holes)
                    + " but 'parameters' does not declare "
                    + ("them" if len(holes) > 1 else "it")
                    + " — the created tool would run that text literally. Add one "
                    "{name,type,required,description} object per placeholder."
                ),
            )

        # Already installed? Then the gap is knowledge, not capability — and
        # this is the exact blindness the wave exists for (a run that shelled
        # out to re-read PDFs it had already read with read_document).
        existing = await asyncio.to_thread(self.store.known_tool, name)
        if existing:
            return ToolResult(
                ok=False,
                error=(
                    f"capability_propose: “{existing}” already exists in this app "
                    "— call it instead of asking for it. If it cannot do what you "
                    "need, propose a DIFFERENT name and say in 'why' what the "
                    "existing one does not do."
                ),
            )

        try:
            # Off the loop: this is a SQLite write, and v1.153.1's rule has no
            # exception for "it is only a small one" — the daemon is one loop
            # and a stalled write there reads to the user as "Daemon offline".
            record = await asyncio.to_thread(
                self.store.create,
                kind=kind,
                name=name,
                rationale=why,
                scope=allowed_to,
                task=task,
                spec=spec,
                # RECORDED, NEVER APPLIED. Approval writes no permission entry,
                # so this is the model's wish on a card and nothing more.
                requested_permission="ask",
                # The accounting seam: the session that hit the wall, so a
                # request is auditable back to the run that needed it.
                run_id=str(getattr(ctx, "session_id", "") or ""),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=f"capability_propose: {exc}")

        if record is None:
            # ``create`` returns None for BOTH "suppressed" and "the DB refused
            # it" — opposite outcomes for the caller (stop asking vs something is
            # broken), so they are disambiguated on the read side rather than
            # guessed at.
            signature = signature_for(kind, name)
            if await asyncio.to_thread(self.store.suppressed, signature):
                return ToolResult(
                    ok=True,
                    output=(
                        "Not filed — this exact request is already waiting for the "
                        "user, or they turned it down before. Do not ask again: "
                        "say in your reply what you cannot do, and finish what you "
                        "can with the tools you have."
                    ),
                    data={"filed": False, "reason": "suppressed", "signature": signature},
                )
            return ToolResult(
                ok=False,
                error=(
                    "capability_propose: the request could not be saved (the "
                    "review queue is unavailable). Say so in your reply instead."
                ),
            )

        capability = self.store.describe_kind(kind)
        bits = [
            f"Filed a request for {KIND_LABELS.get(kind, kind)} “{name}” for the "
            "user to approve. NOTHING was installed and you do not have it in "
            "this run."
        ]
        if kind == "tool":
            bits.append(
                f"If they approve it, it will be created as `{name}` and will "
                "still ask for approval every time it runs."
            )
        note = str(capability.get("note") or "")
        if note and not capability.get("can_apply"):
            bits.append(note)
        bits.append(
            "Now tell the user in your reply that you asked for this and what "
            "you could not finish without it, then carry on with the tools you "
            "have. Do not work around it with shell."
        )
        return ToolResult(
            ok=True,
            output=" ".join(b for b in bits if b),
            data={
                "filed": True,
                "id": record.id,
                "kind": record.kind,
                "name": record.name,
                "status": record.status,
                "run_id": record.run_id,
                "signature": record.signature,
                "granted": False,
                "can_apply": bool(capability.get("can_apply")),
            },
        )


def capability_proposal_tools(store: CapabilityProposalStore) -> list[Tool]:
    """Build the capability-proposal tool bound to the shared store."""
    return [CapabilityProposeTool(store)]
