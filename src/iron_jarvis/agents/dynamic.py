"""Dynamic Agent Registry — agents that add more agents (§11/§12 extension).

A *runtime* registry of user/agent-defined agents. Each entry is persisted as a
:class:`DynamicAgentRecord` and rebuilt into a standard
:class:`~iron_jarvis.agents.types.AgentDefinition` on demand, so a dynamic agent
plugs straight into the existing :class:`~iron_jarvis.agents.runtime.AgentRuntime`
loop with no special casing.

Because ``AgentType`` is a fixed enum, a dynamic agent reuses a *base* ``AgentType``
(default :attr:`AgentType.BUILDER`) for its lifecycle/persistence while carrying its
own system prompt and tool allowlist through the ``AgentDefinition``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import AgentType
from .dynamic_models import DynamicAgentRecord
from .types import AgentDefinition, get_agent_definition

if TYPE_CHECKING:  # avoid importing the heavy SQLAlchemy symbol at runtime
    from sqlalchemy import Engine

    from .files import AgentFiles

log = logging.getLogger("iron_jarvis.agents.dynamic")


#: A curated catalog of provider/model options a dynamic agent may select.
#: Offline-safe: the ``mock`` provider drives the deterministic test LLM; the
#: rest are the live providers the platform can route to when keys are present.
KNOWN_MODELS: list[dict] = [
    {"provider": "mock", "model": "mock-1"},
    {"provider": "anthropic", "model": "claude-opus-4-8"},
    {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    {"provider": "anthropic", "model": "claude-haiku-4-5"},
    {"provider": "anthropic", "model": "claude-fable-5"},
    {"provider": "openai", "model": "gpt-4o"},
    {"provider": "openai", "model": "gpt-4o-mini"},
    # Served by the ChatGPT (Codex) backend — the only family available to a
    # subscription-only ChatGPT account. OpenAI retires ids there over time
    # (gpt-5-codex now 400s); gpt-5.5 verified live 2026-07, and the adapter
    # self-heals via a fallback ladder if it's retired too.
    {"provider": "openai", "model": "gpt-5.5"},
    # Subscription CLIs: FLAT-RATE inference through a logged-in local CLI
    # (claude -p / codex exec) — zero API keys; light up when the CLI is found.
    {"provider": "claude-cli", "model": "subscription"},
    {"provider": "codex-cli", "model": "subscription"},
    {"provider": "google", "model": "gemini-2.0-flash"},
    {"provider": "google", "model": "gemini-1.5-pro"},
    # xAI (Grok) — current generation: the flagship, the fast agentic model,
    # and the two coding-agent models (grok-build-0.1 powers the Grok Build CLI).
    {"provider": "xai", "model": "grok-4"},
    {"provider": "xai", "model": "grok-4-1-fast"},
    {"provider": "xai", "model": "grok-code-fast-1"},
    {"provider": "xai", "model": "grok-build-0.1"},
    # OpenRouter — namespaced ids; openrouter/auto picks the best model per task.
    {"provider": "openrouter", "model": "openrouter/auto"},
    {"provider": "openrouter", "model": "x-ai/grok-code-fast-1"},
]


def available_models() -> list[dict]:
    """Return the catalog of selectable ``{provider, model}`` options (a copy)."""
    return [dict(m) for m in KNOWN_MODELS]


def _base_agent_type(raw: str) -> AgentType:
    """Resolve a stored ``base_type`` string to an ``AgentType`` (fail-soft)."""
    try:
        return AgentType(raw)
    except ValueError:
        return AgentType.BUILDER


def identity_anchor(name: str) -> str:
    """The identity anchor line a dynamic agent's composed prompt begins with.

    v1.171.0 (contract 4): a NAMED agent should know it is one — a persistent
    colleague on this machine, not an anonymous per-run persona. The anchor is
    applied at COMPOSITION time (:meth:`DynamicAgentRegistry.definition`), never
    written into the stored record, so the edit dialog keeps showing exactly
    what the user typed and re-saving can never stack anchors. Builtin agent
    definitions are untouched.
    """
    return f"You are {name}, a persistent named agent on this machine."


#: "Keep what the row has" for the job-card kwargs of :meth:`register` — an
#: OMITTED kwarg must not clobber a stored value, so the routes can PATCH one
#: field (``allowance_usd``) without re-sending the rest. ``None`` cannot be
#: the sentinel: ``max_steps=None`` is a real value ("the config default").
_KEEP: Any = object()

#: The postures a record may store. ``yolo`` is deliberately absent — it is
#: consent to one watched turn, never a standing posture for a background
#: employee (``runtime.inherited_approval_mode`` lands it as approve_for_me).
_APPROVAL_MODES = ("", "approve_for_me", "always_ask")


def _names(raw: Any) -> list[str]:
    """A clean, de-duplicated list of names from a list-ish value. Never raises."""
    out: list[str] = []
    try:
        for item in list(raw or []):
            text = " ".join(str(item or "").split())
            if text and text not in out:
                out.append(text)
    except Exception:  # noqa: BLE001 — a bad list is an empty list
        return []
    return out


#: The job-card columns and the value an UNSET one reads as. A row written
#: before v1.295.0 gets these columns from ``_reconcile_additive_columns`` as
#: NULL (``ALTER TABLE ADD COLUMN`` carries no default), so every registry
#: door normalises the detached copy — readers see the model's defaults, never
#: ``None`` for a string or a count. The DB row is left as it is.
_JOB_CARD_DEFAULTS: dict[str, Any] = {
    "approval_mode": "",
    "allowance_tokens": 0,
    "allowance_usd": 0.0,
    "allowance_warned_month": "",
    "paused_reason": "",
    "reports_to": "",
    "skills_json": "[]",
    "deny_tools_json": "[]",
}


def _with_defaults(record: DynamicAgentRecord) -> DynamicAgentRecord:
    for column, default in _JOB_CARD_DEFAULTS.items():
        if getattr(record, column, None) is None:
            try:
                setattr(record, column, default)
            except Exception:  # noqa: BLE001 — a frozen double keeps its None
                pass
    return record


def _json_names(raw: str) -> list[str]:
    """Decode a stored JSON list of names; garbage reads as ``[]``."""
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return _names(data) if isinstance(data, list) else []


class DynamicAgentRegistry:
    """Persisted, in-memory registry of dynamically defined agents."""

    def __init__(self, engine: "Engine", *, files: "AgentFiles | None" = None) -> None:
        self.engine = engine
        self._records: dict[str, DynamicAgentRecord] = {}
        #: THE FOLDER (v1.297.0): ``<home>/agents/<slug>/`` per agent, kept in
        #: step with the row — AGENTS.md mirrors ``system_prompt`` on every
        #: prompt change, ``remove`` moves the folder to the trash. ``None``
        #: (a bare registry in tests / the CLI) keeps no folders at all.
        self.files = files

    # --- the folder (v1.297.0) ---------------------------------------------

    def _mirror_instructions(self, name: str, prompt: str, reason: str) -> None:
        """Write AGENTS.md for ``name`` when a folder store is attached.
        Never raises — a disk hiccup must not fail the DB write it mirrors."""
        if self.files is None:
            return
        try:
            self.files.write_instructions(name, prompt or "", reason=reason)
        except Exception:  # noqa: BLE001 — the row is the truth; the file is a copy
            log.warning("agent folder: could not write AGENTS.md for %r", name, exc_info=True)

    # --- persistence ------------------------------------------------------

    def load(self) -> "DynamicAgentRegistry":
        """Read every persisted dynamic agent into memory (called on startup).

        v1.297.0: every record whose folder has NO ``AGENTS.md`` yet gets one
        (a one-time backfill for agents hired before the folder existed; no
        revision, reason ``backfill``). An existing file is left alone — the
        DB is the mirror the runtime reads, and a load must not clobber a
        file the user may have been editing.
        """
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(DynamicAgentRecord)))
        self._records = {r.name: _with_defaults(r) for r in rows}
        if self.files is not None:
            for record in self._records.values():
                try:
                    if self.files.read_instructions(record.name) is None:
                        self._mirror_instructions(
                            record.name, record.system_prompt or "", "backfill"
                        )
                except Exception:  # noqa: BLE001 — never let a folder stop the boot
                    log.warning("agent folder: backfill failed for %r", record.name, exc_info=True)
        return self

    def register(
        self,
        name: str,
        system_prompt: str,
        tools: list[str],
        base_type: str = "builder",
        description: str = "",
        provider: str = "",
        model: str = "",
        *,
        approval_mode: str | Any = _KEEP,
        max_steps: int | None | Any = _KEEP,
        allowance_tokens: int | Any = _KEEP,
        allowance_usd: float | Any = _KEEP,
        reports_to: str | Any = _KEEP,
        skills: list[str] | None | Any = _KEEP,
        deny_tools: list[str] | None | Any = _KEEP,
        prompt_reason: str = "edit",
    ) -> DynamicAgentRecord:
        """Create or update a dynamic agent (upsert by unique ``name``).

        THE FOLDER (v1.297.0): when a folder store is attached, AGENTS.md is
        written to mirror ``system_prompt`` after the row lands — a CHANGED
        prompt keeps the previous text as a revision tagged ``prompt_reason``
        ("edit" unless the caller says why, e.g. "restore <id>").

        ``provider``/``model`` optionally pin the agent to a specific LLM (see
        :func:`available_models`); empty strings mean "use the platform default".

        THE JOB CARD (v1.295.0) rides the keyword-only arguments. Every one of
        them defaults to *keep*: on an UPDATE an omitted kwarg leaves the stored
        value alone (so a route can PATCH ``allowance_usd`` without clobbering
        ``skills``), and on a CREATE the column default applies. ``skills`` /
        ``deny_tools`` take lists (stored as JSON). ``approval_mode`` outside
        ``_APPROVAL_MODES`` is stored as ``""`` — never ``yolo``. Pausing is
        NOT a register concern: see :meth:`set_paused` / :meth:`resume`.
        """
        tools_json = json.dumps(list(tools or []))
        with session_scope(self.engine) as db:
            existing = db.exec(
                select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)
            ).first()
            if existing is not None:
                existing.system_prompt = system_prompt
                existing.tools_json = tools_json
                existing.base_type = base_type
                existing.description = description
                existing.provider = provider
                existing.model = model
                record = existing
            else:
                record = DynamicAgentRecord(
                    name=name,
                    system_prompt=system_prompt,
                    tools_json=tools_json,
                    base_type=base_type,
                    description=description,
                    provider=provider,
                    model=model,
                )
            self._apply_job_card(
                record,
                approval_mode=approval_mode,
                max_steps=max_steps,
                allowance_tokens=allowance_tokens,
                allowance_usd=allowance_usd,
                reports_to=reports_to,
                skills=skills,
                deny_tools=deny_tools,
            )
            db.add(record)
            db.commit()
            db.refresh(record)  # reload all columns so the detached copy is usable
        self._records[name] = _with_defaults(record)
        self._mirror_instructions(name, system_prompt, prompt_reason)
        return record

    @staticmethod
    def _apply_job_card(
        record: DynamicAgentRecord,
        *,
        approval_mode: Any,
        max_steps: Any,
        allowance_tokens: Any,
        allowance_usd: Any,
        reports_to: Any,
        skills: Any,
        deny_tools: Any,
    ) -> None:
        """Write the job-card kwargs that were GIVEN onto ``record``; ``_KEEP``
        leaves a column untouched. Each value is coerced defensively — a bad
        number keeps the stored value rather than raising mid-upsert."""
        if approval_mode is not _KEEP:
            mode = " ".join(str(approval_mode or "").split()).lower()
            record.approval_mode = mode if mode in _APPROVAL_MODES else ""
        if max_steps is not _KEEP:
            try:
                steps = None if max_steps in (None, "", 0) else int(max_steps)
            except (TypeError, ValueError):
                steps = record.max_steps
            record.max_steps = steps if (steps is None or steps > 0) else None
        if allowance_tokens is not _KEEP:
            try:
                record.allowance_tokens = max(0, int(allowance_tokens or 0))
            except (TypeError, ValueError):
                pass
        if allowance_usd is not _KEEP:
            try:
                record.allowance_usd = max(0.0, float(allowance_usd or 0.0))
            except (TypeError, ValueError):
                pass
        if reports_to is not _KEEP:
            record.reports_to = " ".join(str(reports_to or "").split())
        if skills is not _KEEP:
            record.skills_json = json.dumps(_names(skills))
        if deny_tools is not _KEEP:
            record.deny_tools_json = json.dumps(_names(deny_tools))

    # --- the day off (v1.295.0) -------------------------------------------

    def _update(self, name: str, mutate) -> DynamicAgentRecord | None:
        """Apply ``mutate(row)`` to the persisted row and refresh the cache.
        ``None`` when no such agent exists."""
        with session_scope(self.engine) as db:
            row = db.exec(
                select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)
            ).first()
            if row is None:
                self._records.pop(name, None)
                return None
            mutate(row)
            db.add(row)
            db.commit()
            db.refresh(row)
        self._records[name] = _with_defaults(row)
        return row

    def set_paused(self, name: str, reason: str) -> DynamicAgentRecord | None:
        """Pause ``name`` with a plain-words ``reason`` (the auto-pause or the
        user's day off). Every door then refuses with that reason. ``None`` for
        an unknown agent."""
        text = " ".join(str(reason or "").split()) or "paused"

        def mutate(row: DynamicAgentRecord) -> None:
            row.paused_reason = text
            row.paused_at = utcnow()

        return self._update(name, mutate)

    def resume(self, name: str) -> DynamicAgentRecord | None:
        """Clear the pause. The allowance itself is untouched: a resumed agent
        that is still over its allowance is refused by the allowance door, not
        by this flag. ``None`` for an unknown agent."""

        def mutate(row: DynamicAgentRecord) -> None:
            row.paused_reason = ""
            row.paused_at = None

        return self._update(name, mutate)

    def set_warned_month(self, name: str, month: str) -> DynamicAgentRecord | None:
        """Record that the 80% warning for ``month`` ("YYYY-MM") went out, so
        it is published once per month and not once per run."""
        text = " ".join(str(month or "").split())

        def mutate(row: DynamicAgentRecord) -> None:
            row.allowance_warned_month = text

        return self._update(name, mutate)

    # --- lookups ----------------------------------------------------------

    def get(self, name: str) -> DynamicAgentRecord | None:
        record = self._records.get(name)
        if record is not None:
            return record
        # Fall back to the DB for instances that haven't loaded this name yet.
        with session_scope(self.engine) as db:
            record = db.exec(
                select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)
            ).first()
        if record is not None:
            self._records[name] = _with_defaults(record)
        return record

    def list(self) -> list[DynamicAgentRecord]:
        return sorted(self._records.values(), key=lambda r: r.name)

    def remove(self, name: str) -> bool:
        """Delete a dynamic agent by name; True if a row was removed.

        v1.297.0: its folder is MOVED to ``<home>/trash/`` (never deleted —
        the v1.256.0 rule), after the row is gone and only when one exists.
        """
        with session_scope(self.engine) as db:
            row = db.exec(
                select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)
            ).first()
            if row is None:
                self._records.pop(name, None)
                return False
            db.delete(row)
            db.commit()
        self._records.pop(name, None)
        if self.files is not None:
            try:
                self.files.remove(name)
            except Exception:  # noqa: BLE001 — the row is gone; a stuck folder is a warning
                log.warning("agent folder: could not move %r to the trash", name, exc_info=True)
        return True

    def definition(self, name: str) -> AgentDefinition | None:
        """Rebuild a stored agent into an ``AgentDefinition`` (None if unknown)."""
        record = self.get(name)
        if record is None:
            return None
        try:
            tools = json.loads(record.tools_json or "[]")
        except (TypeError, ValueError):
            tools = []
        base_type = _base_agent_type(record.base_type)
        # An EMPTY stored roster means "not specified" -> INHERIT the base type's
        # tools. It never means "no tools". Measured: the dashboard's Agents page
        # has no tool picker and `SetupCard.tsx` posts `tools: []` hardcoded, so
        # every agent the user created there rebuilt into
        # `AgentDefinition(tools=[])` — the runtime advertises exactly
        # `registry.specs(agent_def.tools)`, so those agents could not read a
        # file, write one, or call anything at all, and the failure looked like
        # a dumb model rather than an empty roster. A NON-empty list is still
        # honoured verbatim (an explicit allowlist, unchanged). The copy is
        # load-bearing: `get_agent_definition` hands back the SHARED builtin
        # definition object, and the runtime/permission layers append to
        # `definition.tools` — mutating it here would leak one dynamic agent's
        # roster into every builtin session for the life of the process.
        if not tools:
            tools = list(get_agent_definition(base_type).tools)
        # Identity section (v1.171.0): anchor first, then the stored prompt.
        # The roster/delegation blocks the runtime appends later are unchanged.
        stored = record.system_prompt or ""
        prompt = identity_anchor(record.name)
        if stored.strip():
            prompt = f"{prompt}\n\n{stored}"
        # THE JOB CARD (v1.295.0): the deny list becomes a "deny" override —
        # ``PermissionEngine.mode_for`` lets an override lower a tool, never
        # raise one past the deny floor, so this can only NARROW what the base
        # policy allows. Skills and the manager ride the definition to the
        # runtime (``AgentRuntime.run`` injects them beside default_skills).
        deny = _json_names(getattr(record, "deny_tools_json", "") or "[]")
        return AgentDefinition(
            type=base_type,
            system_prompt=prompt,
            tools=list(tools),
            permission_overrides={t: "deny" for t in deny},
            skills=_json_names(getattr(record, "skills_json", "") or "[]"),
            reports_to=" ".join(str(getattr(record, "reports_to", "") or "").split()),
        )
