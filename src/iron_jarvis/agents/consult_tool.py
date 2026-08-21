"""Consult tool — ask a NAMED teammate one question, get one answer (v1.193.0).

WHY THIS EXISTS. Until now the only agent-to-agent primitive was
``delegate``/``spawn_agent``: a whole child SESSION with its own workspace,
``AgentRun``, step budget and learning loop — and it is FULLY BLOCKING, because
the parent sits inside ``registry.invoke`` for the child's entire lifetime.
That has two costs. The obvious one is price: a builder that only wants the
reviewer's judgement on one paragraph pays for a session. The subtle one is
that the parent executes NO model turns while the child runs, so there is no
instant in which both parties are awake — which is precisely why directed
blackboard messaging between a parent and its running child is structurally
dead. A genuinely asynchronous conversation needs a NON-BLOCKING delegate, a
deep architectural change this release is not making.

``consult`` is the primitive that fits in the meantime: one question, one
model call, one answer, attributed. No session, no workspace, no ``AgentRun``,
no fan-out — a consulted agent cannot itself consult, because it answers with
``tools=[]`` (see :data:`~iron_jarvis.agents.threads.PANEL_NO_TOOLS`), so a
consult LOOP is impossible by construction rather than by policy.

WHAT IT SHARES WITH THE REST OF THE WAVE (deliberately, so nothing drifts):

* **the roster** — the target is resolved through :func:`agents.roster
  .resolve_target`, exactly as ``delegate`` resolves its own. An unresolvable
  name is REFUSED with the addressable names; it is never coerced to
  ``builder``. Fake capability is worse than a refusal.
* **the identity** — a local teammate answers on its REAL definition prompt
  (``types._DEFINITIONS`` for a builtin, the registry's COMPOSED definition —
  identity anchor included — for a dynamic one), reusing the round table's own
  two readers rather than growing a second copy of that lookup.
* **the no-tools framing** — those real prompts instruct the agent to read
  files, run shell and delegate, and here it can do NONE of that. The round
  table solved this in the same wave; :data:`PANEL_NO_TOOLS` is imported
  verbatim instead of re-worded, because two wordings of one rule are two
  rules. It always lands LAST, so it is the final word on the prompt above it.
* **the remote fence** — a ``remote:<name>`` target rides
  ``DelegateTool._delegate_remote``, so the reply (and a non-2xx error body)
  is scanned and wrapped as untrusted DATA by the same code delegate uses.

ONE-SHOT PATH. A local consult goes through ``platform.router.complete(...,
tools=[])`` — the agents-layer one-shot, the same door ``agents/decompose``
uses, carrying the router's transient-retry + cross-provider failover and the
v1.162.0 "an unreachable real provider RAISES, it never returns mock output"
rule. The daemon's ``d._one_shot_complete`` is the equivalent wrapper around a
RAW adapter, and it is a closure inside ``daemon/app.py``'s factory: a
registered tool holds a ``platform``, never the daemon's ``d``, so the router
door is the one a tool can actually reach.

FAN-OUT. This tool calls a model, so it is a spend surface even without
sessions: :data:`_MAX_CONSULTS_PER_RUN` bounds one run's consults,
:data:`_CONSULT_TIMEOUT_S` bounds one call, and consulting YOURSELF is refused.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult

#: How many consults ONE agent run may make. This tool spends real provider
#: budget without opening a session, so it needs its own ceiling — the
#: delegation depth cap cannot see it (a consult creates no ``AgentRun`` to
#: walk a parent chain through). Six is generous for the intended use ("ask the
#: reviewer", "ask the tax reader what this box means") and small enough that a
#: prompt-injected "consult everyone about everything" loop dies quickly. The
#: count is per RUN, so a fresh run starts fresh — a long-lived session is not
#: punished for a previous task's questions.
_MAX_CONSULTS_PER_RUN = 6

#: Wall-clock ceiling on one consult. The caller is BLOCKED for this whole
#: time (same as delegate, minus the session), so an unresponsive teammate must
#: hand the turn back rather than hold it forever.
_CONSULT_TIMEOUT_S = 120.0

#: Rolling window for a caller that has NO agent run id — chat, or any ad-hoc
#: invocation. An ``AgentRun`` is finite, so a per-run counter that never
#: expires is exactly right there; a chat SESSION is not (its id is the literal
#: string "chat" for the app's whole life), so the same never-expiring counter
#: would refuse every consult forever after the sixth one — a permanent
#: lockout, from a guard meant to bound a loop. The window keeps a runaway turn
#: bounded without that, and the surface it applies to is the one with a human
#: at the keyboard.
_ADHOC_WINDOW_S = 300.0

#: Prompt budget on BOTH sides. The caller supplies a question and a SHORT
#: context string — never its transcript. Clipping is REPORTED (the marker is
#: part of the text the answering agent reads) so nobody answers a truncated
#: question believing it was whole.
_MAX_QUESTION_CHARS = 4000
_MAX_CONTEXT_CHARS = 2000

#: Bound on the per-run counter map. The tool instance lives for the daemon's
#: whole life, so an unbounded dict would grow one entry per run forever.
#: Eviction is LEAST-RECENTLY-CHARGED, not oldest-inserted — see :meth:`_prune`.
_MAX_TRACKED_RUNS = 512


def _clip(text: str, limit: int) -> str:
    """Bounded text that SAYS it was cut (the truncation-is-reported rule)."""
    flat = (text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…\n[… clipped by the consult tool …]"


def _fold(name: Any) -> str:
    """Casefolded comparison key for a roster name. Never raises.

    Deliberately local rather than imported from ``roster._norm``: this needs
    one predicate that cannot move under it, and a private helper in another
    module is not a contract.
    """
    try:
        text = " ".join(str(name or "").split())
    except Exception:  # noqa: BLE001 — an unstringable name is simply unknown
        return ""
    if ":" in text:
        head, _, tail = text.partition(":")
        text = f"{head.strip()}:{tail.strip()}"
    return text.casefold()


@dataclass
class _Consultation:
    """Everything the model call needs, resolved off the event loop."""

    entry: Any = None
    base_prompt: str = ""
    provider: str = ""
    model: str = ""
    asker: str = ""
    error: str = ""


class ConsultTool(Tool):
    name = "consult"
    description = (
        "Ask ONE named teammate a single question and get their answer back "
        "right now — advice only, no session, no files, no work handed over "
        "(use `delegate` when you want work DONE). Good for a second opinion, a "
        "judgement call, or a specialist's read on something. Args: agent — a "
        "name from the 'Who can take this work' roster when one is shown: a "
        "builtin specialist ('reviewer', 'researcher', 'planner', ...), a "
        "listed 'custom:<name>' agent, or a listed 'remote:<name>' agent; "
        "question (the single self-contained question); and an optional short "
        "context (a few sentences of background — never your transcript). An "
        "unknown name is REFUSED and the reply lists who you can ask. The "
        "teammate you consult has NO TOOLS, so it advises; it cannot act."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "question": {"type": "string"},
            "context": {"type": "string"},
        },
        "required": ["agent", "question"],
    }
    permission_key = "consult"
    #: Asking a question changes nothing on this machine — there is no inverse
    #: to capture and "undo" is honestly a no-op.
    reversibility = Reversibility.READONLY

    def __init__(self, platform) -> None:
        self.platform = platform
        #: budget key -> (consults made, monotonic start). See
        #: :data:`_MAX_CONSULTS_PER_RUN` and :meth:`_charge`. ORDERED because
        #: :meth:`_prune` evicts the least-recently-CHARGED entry, and a plain
        #: dict cannot express that: re-assigning an existing key leaves it
        #: where it was first inserted.
        self._consults: "OrderedDict[str, tuple[int, float]]" = OrderedDict()

    # -- the cap ------------------------------------------------------------

    @staticmethod
    def _budget_key(ctx: ToolContext) -> tuple[str, bool]:
        """``(key, is_run)`` — who is being charged, and whether it EXPIRES.

        A run id is the honest unit: an ``AgentRun`` is finite, so its counter
        never expires. Everything else (chat, an ad-hoc call) falls back to the
        session id under a rolling window — see :data:`_ADHOC_WINDOW_S`.
        """
        run_id = str(getattr(ctx, "agent_run_id", "") or "").strip()
        if run_id:
            return run_id, True
        return str(getattr(ctx, "session_id", "") or "").strip() or "-", False

    def _charge(self, ctx: ToolContext) -> str:
        """Spend one consult from this caller's budget; ``""`` when allowed.

        Charged on the ATTEMPT, not on success: a consult that times out or
        errors has already spent the provider call the cap exists to bound, so
        counting only the wins would leave the loop unbounded in exactly the
        failing case.
        """
        key, is_run = self._budget_key(ctx)
        now = time.monotonic()
        used, started = self._consults.get(key, (0, now))
        if not is_run and now - started > _ADHOC_WINDOW_S:
            used, started = 0, now
        if used >= _MAX_CONSULTS_PER_RUN:
            return (
                f"consult limit reached ({_MAX_CONSULTS_PER_RUN} questions) — "
                "nothing was asked. Work with the answers you already have, or "
                "`delegate` the piece you cannot settle yourself."
            )
        self._consults[key] = (used + 1, started)
        # LIVENESS, NOT AGE. Re-assigning an EXISTING key does not move it in a
        # dict's insertion order, so before v1.195.0 a run that kept consulting
        # stayed pinned at the FRONT of the map — exactly where :meth:`_prune`
        # evicts — and its counter was dropped while it was still asking. The
        # next consult then started from zero, so the cap this method IS could
        # be walked straight past by a long-lived run under churn. Touching the
        # key on every charge makes the map an LRU keyed on last USE, which is
        # what "still live" actually means here.
        #
        # `started` is deliberately CARRIED, never refreshed: it is the ad-hoc
        # rolling window's origin (:data:`_ADHOC_WINDOW_S`), and re-stamping it
        # on each charge would make the window slide forward forever and never
        # roll over — the permanent lockout that branch exists to prevent.
        self._consults.move_to_end(key)
        self._prune()
        return ""

    def _prune(self) -> None:
        """Keep the counter map bounded, dropping the LEAST-RECENTLY-CHARGED key.

        Same shape as ``tools/registry.py``'s ``_read_cache``
        (``move_to_end`` on use + ``popitem(last=False)`` here), copied rather
        than re-invented so both bounded maps age entries the same way. Evicting
        a counter is always a budget RESET for that key, so the one entry it
        must never pick is the one still being charged.
        """
        while len(self._consults) > _MAX_TRACKED_RUNS:
            try:
                self._consults.popitem(last=False)
            except KeyError:  # pragma: no cover — race-proofing
                break

    # -- resolution (all of it off the event loop) --------------------------

    def _resolve(self, wanted: str, ctx: ToolContext) -> _Consultation:
        """Roster lookup + caller identity + the target's real prompt.

        Synchronous on purpose: every step here touches SQLite (the roster
        joins stats, registries and the delegation ledger; the caller's name
        needs its session row), and the daemon is one event loop —
        :meth:`execute` runs this whole block in a worker thread.
        """
        try:
            from .roster import build_roster, resolve_target
        except Exception:  # noqa: BLE001 — no roster, no addressing; refuse
            return _Consultation(
                error="the agent roster is unavailable, so there is no way to "
                "tell who you would be consulting — nothing was asked."
            )

        entry = None
        try:
            # require_delegable=False: a consult hands over no WORK, so the
            # anti-fork-bomb rule that hides coordinators from `delegate` does
            # not apply — asking the planner for its opinion spawns nothing.
            # This is the same reason v1.166.0 added the flag for conversation
            # surfaces. Offline remotes are still excluded (resolve_target
            # checks `healthy`), so a refusal never points at a dead endpoint.
            entry = resolve_target(self.platform, wanted, require_delegable=False)
        except Exception:  # noqa: BLE001 — roster trouble must refuse, not crash
            entry = None
        if entry is None:
            try:
                names = [e.name for e in build_roster(self.platform) if e.healthy]
            except Exception:  # noqa: BLE001
                names = []
            listed = ", ".join(names) if names else "(nobody is reachable right now)"
            return _Consultation(
                error=f"there is no teammate '{wanted}' to consult — nothing was "
                f"asked. You can consult: {listed}."
            )

        asker = self._caller_name(ctx)
        if asker and _fold(asker) == _fold(entry.name):
            return _Consultation(
                error=f"you ARE {entry.name} — consulting yourself would just "
                "spend budget re-asking your own model. Answer it yourself, or "
                "consult a different teammate."
            )

        if entry.kind == "remote":
            # No prompt to compose: the remote runs its own agent behind its
            # own transport. Provider/model are its business too.
            return _Consultation(entry=entry, asker=asker)

        provider = model = ""
        base_prompt = ""
        if entry.kind == "dynamic":
            slug = entry.name.partition(":")[2] or entry.name
            registry = getattr(self.platform, "agents_registry", None)
            row = registry.get(slug) if registry is not None else None
            if registry is None or row is None:
                return _Consultation(
                    error=f"'{entry.name}' has no runnable definition right now "
                    "— consult a builtin specialist instead."
                )
            # ONE reader for a dynamic agent's prompt, shared with the round
            # table: the COMPOSED definition, so the identity anchor is applied.
            from .threads import AgentThreads

            base_prompt = AgentThreads._dynamic_prompt(registry, slug, row)
            provider = str(getattr(row, "provider", "") or "")
            model = str(getattr(row, "model", "") or "")
        else:
            from .threads import AgentThreads

            base_prompt = AgentThreads._builtin_prompt(entry.name)

        if not provider or not model:
            # Inherit the CALLER's provider/model, exactly as delegate does, so
            # a real multi-agent run stays on the user's chosen model end to end
            # (a dynamic agent's own pin wins — pinning is the point of pinning).
            session = self._caller_session(ctx)
            provider = provider or str(getattr(session, "provider", "") or "")
            model = model or str(getattr(session, "model", "") or "")

        return _Consultation(
            entry=entry,
            base_prompt=base_prompt,
            provider=provider,
            model=model,
            asker=asker,
        )

    def _caller_session(self, ctx: ToolContext):
        """The caller's ``Session`` row, DETACHED, or ``None``. Never raises."""
        try:
            from ..core.db import session_scope
            from ..core.models import Session

            sid = str(getattr(ctx, "session_id", "") or "")
            if not sid:
                return None
            with session_scope(self.platform.engine) as db:
                row = db.get(Session, sid)
                if row is None:
                    return None
                return Session(**row.model_dump())
        except Exception:  # noqa: BLE001 — no row, no inheritance; defaults apply
            return None

    def _caller_name(self, ctx: ToolContext) -> str:
        """The roster name of whoever is calling, or ``""``. Never raises.

        Uses the wave's ONE session→name predicate (``roster
        .resolve_roster_name``: explicit column → delegation ledger → type), so
        the self-consult guard names the caller the same way the roster does.
        ``""`` (chat, an unpersisted session) simply disables the guard — a
        caller with no identity cannot be the target either.
        """
        try:
            from .roster import resolve_roster_name

            session = self._caller_session(ctx)
            if session is None:
                return ""
            return resolve_roster_name(self.platform, session)
        except Exception:  # noqa: BLE001 — an unknown caller is not an error
            return ""

    # -- the prompt ---------------------------------------------------------

    @staticmethod
    def _system_for(entry_name: str, base_prompt: str, asker: str) -> str:
        """identity → seat → NO TOOLS, in that order (the round table's rule).

        The base prompt is the agent's REAL working prompt and it talks about
        tools; the correction must come after it, never before.
        """
        from .threads import PANEL_NO_TOOLS

        who = asker or "another agent"
        seat = (
            f"You are {entry_name}, and {who} is CONSULTING you. They have asked "
            "you ONE question and will read your answer as advice — this is not "
            "a task and you have not been handed any work. Answer the question "
            "directly and concretely in under ~250 words: your actual judgement, "
            "the risk you would worry about, and what you would need to be sure. "
            "If you cannot answer with what you were given, say exactly what is "
            "missing instead of guessing."
        )
        parts = [(base_prompt or "").strip(), seat, PANEL_NO_TOOLS]
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _user_message(question: str, context: str, asker: str) -> str:
        who = asker or "the agent consulting you"
        blocks = []
        if context:
            blocks.append(f"CONTEXT FROM {who}:\n{context}")
        blocks.append(f"QUESTION FROM {who}:\n{question}")
        return "\n\n".join(blocks)

    # -- execution ----------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        wanted = str(args.get("agent") or "").strip()
        question = str(args.get("question") or "").strip()
        context = str(args.get("context") or "").strip()
        if not wanted:
            return ToolResult(
                ok=False,
                error="`agent` is required — name the teammate you want to ask "
                "(e.g. 'reviewer').",
            )
        if not question:
            return ToolResult(ok=False, error="`question` is required")
        question = _clip(question, _MAX_QUESTION_CHARS)
        context = _clip(context, _MAX_CONTEXT_CHARS)

        plan = await asyncio.to_thread(self._resolve, wanted, ctx)
        if plan.error:
            return ToolResult(ok=False, error=plan.error)

        refusal = self._charge(ctx)
        if refusal:
            return ToolResult(ok=False, error=refusal)

        try:
            return await asyncio.wait_for(
                self._ask(plan, question, context, ctx), _CONSULT_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            return ToolResult(
                ok=False,
                output="",
                error=f"{plan.entry.name} did not answer within "
                f"{int(_CONSULT_TIMEOUT_S)}s — nothing ran and no session was "
                "started. Proceed without their input or try a different "
                "teammate.",
                data={"target": plan.entry.name, "timeout_s": _CONSULT_TIMEOUT_S},
            )

    async def _ask(
        self, plan: _Consultation, question: str, context: str, ctx: ToolContext
    ) -> ToolResult:
        if plan.entry.kind == "remote":
            return await self._ask_remote(plan, question, context)
        return await self._ask_local(plan, question, context, ctx)

    async def _ask_local(
        self, plan: _Consultation, question: str, context: str, ctx: ToolContext
    ) -> ToolResult:
        """One completion through the router with ``tools=[]``.

        The router carries transient retry, cross-provider failover, and the
        v1.162.0 rule that an unreachable REAL provider raises rather than
        returning mock prose — so an offline teammate produces an honest
        refusal here, never a fabricated opinion.
        """
        from ..providers.adapters.base import LLMMessage

        system = self._system_for(plan.entry.name, plan.base_prompt, plan.asker)
        # USER PROFILE, "how" ONLY — the same slice the round table takes. The
        # answer may be relayed to the user verbatim, so language and
        # accessibility needs apply; the user's VOICE and preferences must not,
        # or every teammate would answer as the same person.
        try:
            from ..profile import profile_block

            prefs = profile_block(self.platform, include=("how",))
            if prefs:
                system += "\n\n" + prefs
        except Exception:  # noqa: BLE001 — never break a consult
            pass

        route = await self.platform.router.complete(
            provider=plan.provider or None,
            model=plan.model or None,
            system=system,
            messages=[
                LLMMessage(
                    role="user",
                    content=self._user_message(question, context, plan.asker),
                )
            ],
            tools=[],
            session_id=getattr(ctx, "session_id", None),
        )
        text = (getattr(route.response, "text", "") or "").strip()
        if not text:
            return ToolResult(
                ok=False,
                output="",
                error=f"{plan.entry.name} returned an empty answer",
                data={"target": plan.entry.name},
            )
        return ToolResult(
            ok=True,
            # WHO ANSWERED IS IN THE OUTPUT. The runtime hands the model
            # ``result.output`` and nothing else — a name that lives only in
            # ``data`` never reaches the agent that asked, and an unattributed
            # opinion is indistinguishable from the caller's own reasoning.
            output=f"{plan.entry.name} answered:\n{text}",
            data={
                "target": plan.entry.name,
                "kind": plan.entry.kind,
                "provider": route.provider,
                "model": route.model,
                "answer": text,
            },
        )

    async def _ask_remote(
        self, plan: _Consultation, question: str, context: str
    ) -> ToolResult:
        """A ``remote:<name>`` teammate, through delegate's own remote path.

        REUSED, NOT REIMPLEMENTED: ``DelegateTool._delegate_remote`` applies
        ``detect_injection`` + ``wrap_untrusted`` to the reply AND to a failure
        body. A remote answer is attacker-reachable text; a second copy of that
        fence here would be a second thing to forget to update.
        """
        from .delegate_tool import DelegateTool

        who = plan.asker or "another agent"
        task = (
            f"{who} is CONSULTING you: answer this ONE question with your own "
            "judgement, in under ~250 words. This is advice, not a task — no "
            "work has been handed to you, so do not claim to have done any.\n\n"
            + self._user_message(question, context, plan.asker)
        )
        res = await DelegateTool(self.platform)._delegate_remote(plan.entry, task)
        data = dict(res.data or {})
        data.setdefault("target", plan.entry.name)
        if not res.ok:
            return ToolResult(ok=False, output="", error=res.error, data=data)
        # The name rides OUTSIDE the fence — it is ours, not the remote's.
        return ToolResult(
            ok=True,
            output=f"{plan.entry.name} answered:\n{res.output}",
            data=data,
        )
