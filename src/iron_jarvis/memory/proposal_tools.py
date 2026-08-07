"""``memory_propose`` — the agent-facing way to FILE a housekeeping suggestion.

Without this tool the whole v1.143.0 steward is INERT. The steward composes a
prompt that asks a real agent session to notice duplicates, stale notes,
contradictions and merges; :mod:`iron_jarvis.memory.proposals` can store,
review, approve and undo them; the Memory page can show them. But nothing in
production ever called :meth:`MemoryProposalStore.create`, so the queue could
only ever be filled by a test. This is the missing seam.

Why a TOOL and not a parsed report
----------------------------------
The alternative was to parse the session's final prose for housekeeping
candidates. That was rejected: a parser over model prose is a second, silently
diverging notion of what a proposal IS, and the thing it feeds is the only
DESTRUCTIVE path in this release (an approved proposal deletes files). A tool
call carries a typed payload the store validates, and an ordinary
``ToolInvocation`` row so every suggestion is auditable back to the session that
filed it.

What it can and cannot do
-------------------------
* It WRITES NOTHING to memory. It appends one ``pending`` row to a review queue.
  Nothing on disk changes until a human clicks Approve — the invariant this
  whole feature exists to protect.
* Its ``run_id`` is the calling session's id (``ctx.session_id``), which is what
  makes ``proposals_raised`` in the steward's run accounting a READ number
  rather than an estimate (``routes/memory_review.py::_count_proposals``
  counts rows whose ``run_id`` equals the session id).
* Suppression is REPORTED, never hidden: a signature the user already dismissed
  (or one already waiting) comes back as an honest "not filed, and why" instead
  of a silent success — otherwise a model would re-file the same rejected
  suggestion every week and read its own success message as proof it worked.

Permission tier
---------------
``allow``. Queuing a suggestion is strictly less powerful than ``ltm_append``
(which this same session already holds and which really does write a file), and
the v1.142 lesson is explicit: a tool with NO entry in
``core/config.py::default_permissions`` resolves to ``ask``, and an ``ask`` with
no interactive resolver is a DENY in the headless daemon — so a scheduled
steward could never file anything and the queue would stay empty forever with
no error anyone would see. The entry is declared next to ``history_search`` for
exactly that reason.

Undo tier
---------
The framework default (``IRREVERSIBLE``) stands, deliberately: there is nothing
to time-travel. A suggestion's inverse is *Dismiss*, which is one click in the
review card and which additionally suppresses the signature — a better outcome
than a silent undo, because dismissing also teaches the steward not to ask
again.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from ..tools.base import Tool, ToolContext, ToolResult
from .proposals import KINDS, MemoryProposalStore, _norm_ref, signature_for

log = get_logger("memory.propose")

#: Kind -> the sentence the tool echoes back, so a model reading its own tool
#: output re-learns what it just committed to.
_KIND_ECHO = {
    "duplicate": "duplicate notes",
    "stale": "an out-of-date note",
    "contradiction": "two notes that disagree",
    "merge": "notes that should become one",
}


def _clean_refs(value: Any) -> list[str]:
    """A ``refs``/``remove_refs`` argument as a list of non-empty strings.

    Models send a list, a comma-joined string, or (rarely) one bare string. All
    three mean the same thing, and rejecting the last two would turn a valid
    suggestion into a retry loop, so all three are accepted.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    else:
        parts = str(value).split(",")
    return [p.strip() for p in parts if str(p).strip()]


class MemoryProposeTool(Tool):
    """File ONE suggest-only memory housekeeping proposal for the user to review."""

    name = "memory_propose"
    permission_key = "memory_propose"
    description = (
        "Propose a memory HOUSEKEEPING change for the user to approve — this is "
        "the ONLY way to suggest deleting, replacing or merging an existing "
        "note, and it changes nothing by itself: the suggestion appears on the "
        "Memory page and waits for the user's click. Use it when you notice "
        "that two notes say the same thing (kind=duplicate), a note the facts "
        "have moved past (kind=stale), two notes that disagree (kind=merge is "
        "wrong here — use kind=contradiction), or several notes that should "
        "become one (kind=merge). Name the memory base the notes live in and "
        "the notes themselves, say WHY in one or two plain sentences, and give "
        "the exact effect: `remove_refs` for notes to delete, and "
        "`survivor_ref` + `text` for the note to keep and the full text it "
        "should hold afterwards. To ADD a new fact, do not use this tool — "
        "`ltm_append` writes a note directly and is always undoable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "duplicate (two notes say the same thing) | "
                "stale (the facts moved past this note) | contradiction (two "
                "notes disagree) | merge (several notes should be one).",
            },
            "base": {
                "type": "string",
                "description": "The memory base the affected notes live in — "
                "the name shown on the Memory page (e.g. \"brain\").",
            },
            "refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every note this suggestion touches, by title or "
                "file name. At least one.",
            },
            "rationale": {
                "type": "string",
                "description": "WHY, in one or two plain sentences the user "
                "will read verbatim. Quote the facts that clash.",
            },
            "suggested_action": {
                "type": "string",
                "description": "One line saying what approving would DO, e.g. "
                "\"Keep “2026 filing dates” and remove the older copy.\"",
            },
            "survivor_ref": {
                "type": "string",
                "description": "Optional: the note that SURVIVES and gets "
                "rewritten. Required whenever you supply `text`.",
            },
            "text": {
                "type": "string",
                "description": "Optional: the COMPLETE new content of the "
                "surviving note — it replaces that note's whole body, so "
                "include everything worth keeping from all of them.",
            },
            "remove_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: the notes approving would DELETE. "
                "Never list the survivor here.",
            },
        },
        "required": ["kind", "base", "refs", "rationale", "suggested_action"],
    }

    def __init__(self, store: MemoryProposalStore) -> None:
        self.store = store

    # -- helpers --------------------------------------------------------------

    def _known_bases(self) -> list[str]:
        """Names of the memory bases this daemon actually has (best-effort)."""
        ltm = getattr(self.store, "ltm", None)
        if ltm is None:
            return []
        try:
            # ``sources()`` is the manager's own name list (the wire still says
            # "sources"; the USER-facing word is "memory base" — v1.113 canon).
            names = [str(n or "") for n in (ltm.sources() or [])]
        except Exception:  # noqa: BLE001 — a listing failure must not block filing
            return []
        return [n for n in names if n]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        kind = str(args.get("kind") or "").strip().lower()
        base = str(args.get("base") or "").strip()
        refs = _clean_refs(args.get("refs"))
        rationale = str(args.get("rationale") or "").strip()
        suggested_action = str(args.get("suggested_action") or "").strip()
        survivor_ref = str(args.get("survivor_ref") or "").strip()
        text = str(args.get("text") or "")
        remove_refs = _clean_refs(args.get("remove_refs"))

        # -- argument honesty (a refusal the model can FIX, never a crash) ----
        if kind not in KINDS:
            return ToolResult(
                ok=False,
                error=f"memory_propose: 'kind' must be one of {', '.join(KINDS)}",
            )
        if not base:
            return ToolResult(
                ok=False,
                error="memory_propose: 'base' is required — name the memory base "
                "the notes live in",
            )
        if not refs:
            return ToolResult(
                ok=False,
                error="memory_propose: 'refs' must name at least one note",
            )
        if not rationale:
            return ToolResult(
                ok=False,
                error="memory_propose: 'rationale' is required — the user reads "
                "it verbatim before deciding",
            )
        if not suggested_action:
            return ToolResult(
                ok=False,
                error="memory_propose: 'suggested_action' is required — one line "
                "saying what approving would do",
            )
        if text.strip() and not survivor_ref:
            # The store would refuse this at APPLY time, hours later, with the
            # user watching. Refuse it now, while the model can still fix it.
            return ToolResult(
                ok=False,
                error="memory_propose: 'text' needs 'survivor_ref' — say which "
                "note the new text should be written to",
            )
        if not text.strip() and not remove_refs:
            return ToolResult(
                ok=False,
                error="memory_propose: a suggestion must say what to change — "
                "give 'remove_refs' (notes to delete) and/or 'survivor_ref' + "
                "'text' (the note to rewrite)",
            )

        # An unknown base can never be applied and would sit in the queue as
        # junk the user can only dismiss, so it is refused WITH the real names.
        known = self._known_bases()
        if known and base not in known:
            return ToolResult(
                ok=False,
                error=(
                    f"memory_propose: there is no memory base called “{base}”. "
                    f"The bases on this computer are: {', '.join(known)}."
                ),
            )

        # A survivor listed for removal is a model slip, not a request. The
        # apply path already refuses to delete the note it just wrote, but
        # saying so HERE keeps the user's review card from showing a removal
        # that will not happen.
        dropped = ""
        if survivor_ref and text.strip():
            # Compared the way the SIGNATURE compares refs, so "alpha.md" listed
            # against a survivor written "alpha" is still caught.
            survivor_key = _norm_ref(survivor_ref)
            keep = [r for r in remove_refs if _norm_ref(r) != survivor_key]
            if len(keep) != len(remove_refs):
                dropped = survivor_ref
            remove_refs = keep

        # "These two notes are duplicates — delete both" destroys the fact the
        # duplicate was evidence of. The apply path would carry it out exactly
        # as written (the survivor guard only fires when there is replacement
        # text), so the slip is caught HERE, where the model can still fix it.
        if kind in ("duplicate", "merge") and not text.strip():
            named = {_norm_ref(r) for r in refs}
            doomed = {_norm_ref(r) for r in remove_refs}
            if named and named <= doomed:
                return ToolResult(
                    ok=False,
                    error=(
                        f"memory_propose: a {kind} suggestion that removes EVERY "
                        "note it names would delete the fact itself. Keep one — "
                        "leave the survivor out of 'remove_refs' — or supply "
                        "'survivor_ref' + 'text' with the content to keep."
                    ),
                )

        payload: dict[str, Any] = {}
        if survivor_ref:
            payload["survivor_ref"] = survivor_ref
        if text.strip():
            payload["text"] = text
        if remove_refs:
            payload["remove_refs"] = remove_refs

        try:
            record = self.store.create(
                kind=kind,
                base=base,
                refs=refs,
                rationale=rationale,
                suggested_action=suggested_action,
                payload=payload,
                # THE accounting seam: the steward's ``proposals_raised`` counts
                # rows whose run_id is the session id, so this must be the live
                # session — never blank, never a fresh id.
                run_id=str(getattr(ctx, "session_id", "") or ""),
            )
        except ValueError as exc:  # unknown kind / no refs — already screened
            return ToolResult(ok=False, error=f"memory_propose: {exc}")

        if record is None:
            # ``create`` returns None for BOTH "suppressed" and "the DB refused
            # it", which are opposite outcomes for the caller: one means stop
            # asking, the other means something is broken. Disambiguate on the
            # read side rather than reporting a guess.
            signature = signature_for(kind, base, refs)
            if self.store.suppressed(signature):
                return ToolResult(
                    ok=True,
                    output=(
                        "Not filed — this exact suggestion is already waiting for "
                        "the user, or they dismissed it before. Do not raise it "
                        "again; move on to the next thing you noticed."
                    ),
                    data={"filed": False, "reason": "suppressed", "signature": signature},
                )
            return ToolResult(
                ok=False,
                error=(
                    "memory_propose: the suggestion could not be saved (the "
                    "review queue is unavailable). Report it in your summary "
                    "instead."
                ),
            )

        capability: dict[str, Any] = {}
        try:
            capability = self.store.describe_base(base) or {}
        except Exception:  # noqa: BLE001 — a capability read must not fail a filing
            capability = {}

        bits = [
            f"Filed a housekeeping suggestion ({_KIND_ECHO.get(kind, kind)}) in "
            f"memory base “{base}” for the user to approve. Nothing has changed yet."
        ]
        if remove_refs:
            bits.append(
                f"It would remove {len(remove_refs)} note"
                + ("s" if len(remove_refs) != 1 else "")
                + f": {', '.join(remove_refs)}."
            )
        if payload.get("text"):
            bits.append(f"It would rewrite “{survivor_ref}”.")
        if dropped:
            bits.append(
                f"“{dropped}” was dropped from remove_refs — the note you are "
                "rewriting is never deleted."
            )
        if capability and not capability.get("can_apply"):
            bits.append(str(capability.get("note") or ""))
        return ToolResult(
            ok=True,
            output=" ".join(b for b in bits if b),
            data={
                "filed": True,
                "id": record.id,
                "kind": record.kind,
                "base": record.base,
                "status": record.status,
                "run_id": record.run_id,
                "signature": record.signature,
                "removes": len(remove_refs),
                "rewrites": bool(payload.get("text")),
                "can_apply": bool(capability.get("can_apply", True)),
            },
        )


def memory_proposal_tools(store: MemoryProposalStore) -> list[Tool]:
    """Build the housekeeping-proposal tool bound to the shared store."""
    return [MemoryProposeTool(store)]
