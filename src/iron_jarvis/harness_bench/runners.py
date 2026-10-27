"""The three runners: Iron Jarvis's own coding turn, Claude Code, and Codex.

Every runner works in a FRESH copy of the task's repo, is given the SAME
prompt (:func:`render_prompt`), and returns the same :class:`RunOutcome`.
Every model call it observes is entered in the run's ledger rollout
(:mod:`.ledger`) as it happens.

* ``jarvis`` — the streaming chat lane (``routes/chat.stream_chat_turn``), the
  lane a chat THREAD turn runs on and the one the IJ harness reuses in Phase 4
  (durable turns, rejoin, steering, approvals and the done receipt all live
  there), run in HARNESS MODE (``harness=``, v1.366.0, plan Phase 3): the
  coding tool set the harness arms (``harness/mode.CODING_TOOLS``) in the
  model family's DIALECT (``--dialect auto``, the default) or under Jarvis's
  own names (``--dialect generic``), the component tree in the prompt, no
  hand-off exits. The turn's folder is the copy, approvals are granted
  (``approval_mode="yolo"`` — the copy is disposable — and any card that
  still appears is answered "once"), and the turn gets
  :data:`DEFAULT_ROUNDS` tool rounds and ENDS IN CHAT at the last one
  (``round_budget`` + ``stay_in_chat``, v1.363.0) — the benchmark never
  escalates to an agent run. The dialect and the component tree's
  FINGERPRINT join the configuration fingerprint.
* ``claude`` — ``claude -p`` headless in the copy, in safe mode (no CLAUDE.md,
  skills, plugins, hooks or MCP servers: stock Claude Code, the same on every
  machine), permissions bypassed inside the disposable copy, with
  ``--output-format stream-json`` — its last line is the ``json`` result
  (``num_turns``, ``total_cost_usd``, ``usage``) and the lines before it carry
  the ``tool_use`` blocks the plain ``json`` format leaves out.
* ``codex`` — ``codex exec --json`` in the copy with its sandbox and approvals
  OFF (``--dangerously-bypass-approvals-and-sandbox``: the copy is
  disposable — parity with Claude Code's bypassPermissions; the shared
  GUARDRAIL keeps it inside the folder), the user's config and rules ignored
  (stock Codex), nothing saved to Codex's history. Round 3: with
  ``--sandbox workspace-write`` on Windows Codex's OWN commands were refused
  ("blocked by policy") and the model scored 0/3 for a setting it never saw.
  A sandbox refusal in Codex's ERROR output is now infrastructure
  (:func:`codex_sandbox_refusals`), never the model's failure.

Both CLIs run through ``core/procs`` with a time limit (the tree dies with it)
and without the parent Claude Code session's markers in their environment.

THE EXACT MODEL (v1.383.0, IJ harness part 3 Phase 1). A record used to say
"default" for a CLI's model, and the user's Claude settings changed between
two runs, so nobody could say which model ran. Every runner is now given its
model and its effort EXPLICITLY (Claude Code ``--model`` / ``--effort``;
Codex ``-m`` / ``-c model_reasoning_effort="…"``; Jarvis the provider and
model on the turn, and the reasoning level only where
``providers.reasoning`` offers that level for that pair — otherwise none is
sent and the record says "not offered"), and keeps what the run REPORTS back:
Claude Code's ``system/init`` event names its model, Jarvis's route names
provider, model and reasoning, and Codex's events are read for a model only
if they carry one (never invented). See :meth:`passed_facts` and
``RunOutcome.model_reported``.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

from ..core import procs
from ..core.claude_markers import drop_claude_session_markers
from . import usage as _usage
from .catalog import Task, rounds_for
from .ledger import PLANNER, CallEntry, Rollout

#: The tool rounds one benchmark turn gets — the IJ harness plan's Phase 4
#: decision (60, measured by this benchmark).
DEFAULT_ROUNDS = 60

#: (Before v1.366.0 the runner armed seven tools by hand: six by name — the
#: chat lane's cap — and ``shell`` through the folder rule. Harness mode arms
#: the coding set itself now.)
JARVIS_APPROVAL_MODE = "yolo"
#: ``--dialect``: "auto" — the model family's own row of the dialect table
#: (glm → generic, by measurement: ``harness/dialects.FAMILY_MEASURED``);
#: "generic" — Jarvis's own tool names on any model; or a dialect id to force
#: one ("claude" reproduces the glm row that lost on cost).
DEFAULT_DIALECT = "auto"
#: The session id the chat lanes give their tools (``ToolContext(session_id=
#: "chat")`` in routes/chat.py): what a turn's shell leaves running is kept
#: under it until :func:`procs.end_session` is called for it. (The refine
#: loop's proposer turn still runs under it; a benchmark TASK turn runs as a
#: Build session since v1.378.0 — :data:`BENCH_SESSION_PREFIX`.)
LANE_SESSION_ID = "chat"
#: v1.378.0 (IJ harness part 2, Phase 7): every benchmark task turn runs AS A
#: BUILD SESSION — its tool calls under a fresh ``build:<this prefix><hex>``
#: session id (``harness/sessions.session_id_for``) and its model HELD
#: (``providers.hold``), exactly as ``routes/build.start_build_turn`` runs an
#: IJ harness turn. Before, the turn ran under "chat": the shell had the
#: SCRUBBED environment and was described to the model as one, so v1.377.0's
#: "the agent's terminal finds your tools" (``harness/command_env``) never
#: reached a benchmark turn, and nothing held the model for a helper.
BENCH_SESSION_PREFIX = "bench-"

#: The guardrail every runner is given (Phase 0 review, round 2). A real
#: run's model ended a server task with ``taskkill /f /im python.exe`` — every
#: Python program on the PC, the benchmark included. These sentences ride the
#: ONE prompt all three runners send, so every row's prompt fingerprint
#: carries them.
GUARDRAIL = (
    "Work only inside this folder. Do not stop, kill or change any program you "
    "did not start yourself, and never end processes by name or image (for "
    "example taskkill /im, pkill or killall)."
)

#: What every runner is told: the task's own words, where it works, and the
#: guardrail.
PROMPT_TEMPLATE = (
    "{task}\n\n"
    "---\n"
    "You are working in this project folder: {folder}\n"
    + GUARDRAIL + "\n"
    "Make the changes directly in the files there (or write the file the task "
    "asks for). When you are finished, reply with a short summary of what you did."
)

#: Seconds a runner gets past its task's time limit to wind down before the
#: bench stops waiting for it.
_GRACE_S = 60.0
#: v1.383.0: the efforts ``--effort`` takes (the reasoning vocabulary every
#: runner speaks: ``providers.reasoning.LEVELS``).
EFFORTS: tuple[str, ...] = ("low", "medium", "high")
#: What a Jarvis record says when the pair it ran on offers no reasoning
#: level (nothing was sent).
NOT_OFFERED = "not offered"
#: How much of a final reply a record keeps.
REPLY_CHARS = 2000
#: v1.396.0: the clock a usage-window reading is stamped with when its call's
#: answer arrives (wall clock: readings of several runs are put in time order
#: across a whole benchmark). Tests put a clock of their own here.
READING_CLOCK = time.time
#: The fields of a helper row (the reply line's ``helpers``, the turn's
#: ``helper`` frames) a run record keeps — who did what, never its steps.
HELPER_KEYS: tuple[str, ...] = (
    "id", "agent", "task", "state", "role", "reason", "provider", "model", "usage", "session",
    "result", "since", "ended",
)
#: How much of a helper's task and result a record keeps.
HELPER_TEXT_CHARS = 300


def render_prompt(task: Task, folder: Path) -> str:
    return PROMPT_TEMPLATE.format(task=task.prompt, folder=str(folder))


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass
class RunOutcome:
    """What a runner observed. ``None`` = not reported / not known."""

    ok_run: bool = False
    error: str = ""
    answered_provider: str = ""
    answered_model: str = ""
    tool_calls: int | None = None
    model_rounds: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cached: int | None = None
    cost_metered_usd: float | None = None
    cost_list_price_usd: float | None = None
    final_reply: str = ""
    usage_source: str = ""
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    #: What the runner OBSERVED, as plain facts (``failures.FACT_KEYS``) —
    #: :func:`failures.classify_failure` decides whose failure it was.
    facts: dict[str, Any] = field(default_factory=dict)
    #: v1.383.0: the model the run REPORTED it ran on (Claude Code's init
    #: event, Jarvis's route), ``None`` when it reported none — never guessed.
    model_reported: str | None = None
    #: The reasoning level the run reported it applied (Jarvis's route), or
    #: ``None`` when it reported none.
    effort_reported: str | None = None
    #: v1.396.0 (Jarvis runners): the run's PARTS — ``{who: {provider, model,
    #: vendor, receipts, pieces, model_calls, tokens_in, tokens_out,
    #: tokens_cached, cost_metered_usd, cost_list_price_usd}}`` where ``who`` is
    #: ``planner`` (the turn's own model, and any helper on it) or a model
    #: team member's name. The totals above are their sum. None for a CLI
    #: runner (one opaque rollout).
    parts: dict[str, Any] | None = None
    #: v1.396.0: per ACCOUNT the vendor's usage-window readings seen during
    #: the run (``usage.account_summary``). None for a CLI runner.
    accounts: dict[str, Any] | None = None
    #: v1.396.0: WHO DID WHAT — every helper the turn started, as its row on
    #: the reply line says (member, role, the team router's reason, state,
    #: model). None for a CLI runner.
    helpers: list[dict[str, Any]] | None = None
    #: v1.396.0 (the team runner): the team router's record per member and
    #: role after the run (``team_record.records_for``), and the thresholds it
    #: judged with. None otherwise.
    team_records: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Cost, in the two kinds v1.300.0 keeps apart
# --------------------------------------------------------------------------- #


def call_cost(
    provider: str, model: str, usage: dict[str, Any], *, manager: Any = None
) -> tuple[float | None, float | None]:
    """(metered, list-price equivalent) for ONE model call — the pricing
    table's answer, 0 for a model on the user's own network, ``None`` when
    the price is unknown."""
    from ..eval.pricing import is_list_price_equivalent, is_priced, native_cost, step_cost

    try:
        known = native_cost(usage) is not None or is_priced(provider, model)
    except Exception:  # noqa: BLE001 — an unreadable price is an unknown one
        known = False
    if known:
        cost = float(step_cost(provider, model, usage))
        if is_list_price_equivalent(provider, model):
            return 0.0, cost
        return cost, 0.0
    try:
        from ..providers.egress import stays_on_this_network

        local = stays_on_this_network(provider, manager, None, model)
    except Exception:  # noqa: BLE001
        local = False
    return (0.0, 0.0) if local else (None, None)


def lane_cost(usage: dict[str, Any], route: dict[str, Any]) -> tuple[float | None, float | None]:
    """(metered, list-price equivalent) for a whole Jarvis turn, read off the
    lane's OWN receipt (the done frame's ``usage`` and ``route``) — the other
    side of the ledger identity."""
    if "cost_usd" in usage:
        cost = float(usage.get("cost_usd") or 0.0)
        if usage.get("list_price_equivalent"):
            return 0.0, cost
        return cost, 0.0
    if route.get("on_this_network") is True:
        return 0.0, 0.0
    return None, None


# --------------------------------------------------------------------------- #
# Jarvis
# --------------------------------------------------------------------------- #


def parse_sse(chunk: str) -> list[tuple[str, dict[str, Any]]]:
    """``(event, data)`` pairs from a chunk of the lane's SSE text."""
    out: list[tuple[str, dict[str, Any]]] = []
    for block in chunk.split("\n\n"):
        event, lines = "", []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                lines.append(line[5:].lstrip())
        if not event:
            continue
        try:
            data = json.loads("".join(lines)) if lines else {}
        except json.JSONDecodeError:
            data = {"raw": "".join(lines)}
        out.append((event, data if isinstance(data, dict) else {"value": data}))
    return out


def _entry_from(provider: str, model: str, usage: Any, *, with_tools: bool, source: str,
                manager: Any, who: str = "", account: tuple[str, str] = ("", ""),
                extra: Any = None) -> CallEntry:
    usage = usage if isinstance(usage, dict) else {}
    metered, listed = call_cost(provider, model, usage, manager=manager)
    cached = usage.get("cache_read_input_tokens")
    return CallEntry(
        provider=provider, model=model,
        tokens_in=int(usage.get("input_tokens", 0) or 0),
        tokens_out=int(usage.get("output_tokens", 0) or 0),
        tokens_cached=int(cached) if isinstance(cached, (int, float)) else None,
        cost_metered_usd=metered, cost_list_price_usd=listed,
        complete=True, with_tools=with_tools, source=source,
        who=who, account=account[0], account_name=account[1],
        readings=_usage.readings_from(extra, at=float(READING_CLOCK())),
    )


def call_who() -> str:
    """WHO is making the model call now (v1.396.0): a model-team member's
    run is held to its own model under a hold that names it
    (``harness/helpers.pinned`` / ``model_team.member_scope``:
    ``member:<name>``); anything else — the turn's own model, a helper on it
    — is :data:`ledger.PLANNER`. Read in the context of the coroutine that
    makes the call. Never raises."""
    try:
        from ..providers.hold import current_hold

        who = str(getattr(current_hold(), "who", "") or "")
    except Exception:  # noqa: BLE001
        who = ""
    name = who.split(":", 1)[1].strip() if who.startswith("member:") else ""
    return name or PLANNER


def call_account(platform: Any, provider: str) -> tuple[str, str]:
    """``(account, name)`` a subscription call on ``provider`` ran on
    (v1.396.0), read in the call's own context: the Iron-Proxy account its
    pin names; this PC's own sign-in when Iron-Proxy is off (or holds no
    account of that vendor); ``("", "")`` when Iron-Proxy picked one per call
    (not known — never guessed) and for a model with no account at all."""
    vendor = _usage.vendor_of(provider)
    if vendor not in ("claude", "codex"):
        return "", ""
    try:
        from ..iron_proxy.pin import current

        pin = current()
    except Exception:  # noqa: BLE001
        pin = None
    if pin is not None and _usage.vendor_of(str(getattr(pin, "ij_provider", "") or "")) == vendor:
        return str(getattr(pin, "profile_id", "") or ""), str(getattr(pin, "title", "") or "")
    svc = getattr(platform, "iron_proxy", None)
    if not getattr(svc, "enabled", False):
        return _usage.THIS_PC, _usage.THIS_PC_WORDS
    try:
        from ..harness import build_accounts as BA

        snap = BA.snapshot(svc)
        if snap is not None and not BA.vendor_rows(snap, vendor, include_off=True):
            return _usage.THIS_PC, _usage.THIS_PC_WORDS
    except Exception:  # noqa: BLE001
        pass
    return "", ""


@contextlib.contextmanager
def session_ended_on_escape(session_id: str):
    """v1.378.0 review: end the turn's own session (``procs.end_session``)
    when the block is left by ANY exception — a cancel (Stop, Ctrl+C) or a
    runner bug. A run that finishes ends it itself, off the loop, after the
    block. Each turn has a session of its own, so nothing later sweeps what a
    cancelled turn left running; this is the one door it has. Synchronous on
    purpose: a cancelled task must not await again before the programs stop."""
    try:
        yield
    except BaseException:
        with contextlib.suppress(Exception):
            procs.end_session(session_id)
        raise


@contextlib.contextmanager
def counting_router(platform: Any, rollout: Rollout, observed: dict[str, Any],
                    reasoning_for: Any = None):
    """Enter every model call the platform's router makes into ``rollout``
    while the block runs — ``stream()`` and ``complete()``, whatever the
    router object currently has there (a test's scripted model included).
    A call that never reached its final answer is entered as incomplete with
    nothing reported. Restored however the block ends.

    v1.396.0 review: ``reasoning_for(who)`` names the level a call by a
    model-team MEMBER is sent with (the run's ``--effort`` or the team file's,
    "" for none): a member's run is an agent run, which sends no level of its
    own, so without this the members answered at their own defaults while the
    planner and every single-model row ran at the asked effort. Only a call
    by a member, and only when the call names no level itself."""
    router = platform.router
    manager = getattr(platform, "providers", None)
    had_stream = "stream" in vars(router)
    had_complete = "complete" in vars(router)
    real_stream = router.stream
    real_complete = router.complete

    def _observe(kwargs: dict[str, Any]) -> None:
        if "system_sha256" not in observed and kwargs.get("tools"):
            observed["system_sha256"] = _sha(str(kwargs.get("system") or ""))
            observed["tools"] = sorted(
                str(t.get("name") if isinstance(t, dict) else getattr(t, "name", ""))
                for t in (kwargs.get("tools") or [])
            )
            # What the model was actually sent as the task (the guardrail
            # included) — compared with the run's prompt.txt by the record.
            messages = kwargs.get("messages") or []
            first = getattr(messages[0], "content", "") if messages else ""
            observed["first_user_sha256"] = _sha(str(first or ""))

    def _with_level(who: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if reasoning_for is None or who == PLANNER or kwargs.get("reasoning"):
            return kwargs
        try:
            level = str(reasoning_for(who) or "")
        except Exception:  # noqa: BLE001 — a level never breaks a call
            level = ""
        return {**kwargs, "reasoning": level} if level else kwargs

    async def _counted(frames: AsyncIterator[dict[str, Any]], kwargs: dict[str, Any], who: str):
        _observe(kwargs)
        entry: CallEntry | None = None
        try:
            async with contextlib.aclosing(frames) as inner:
                async for frame in inner:
                    if isinstance(frame, dict) and frame.get("type") == "final":
                        resp = frame.get("response")
                        provider = str(frame.get("provider") or "")
                        entry = _entry_from(
                            provider, str(frame.get("model") or ""),
                            getattr(resp, "usage", None),
                            with_tools=bool(kwargs.get("tools")), source="router.stream",
                            manager=manager, who=who, account=call_account(platform, provider),
                            extra=getattr(resp, "extra", None),
                        )
                    yield frame
        finally:
            rollout.add(entry or CallEntry(
                complete=False, with_tools=bool(kwargs.get("tools")), source="router.stream", who=who,
            ))

    def stream(*args: Any, **kwargs: Any):
        # v1.396.0: who is calling, read in the caller's context (a member's
        # run makes its own calls under its own hold).
        who = call_who()
        kwargs = _with_level(who, kwargs)
        return _counted(real_stream(*args, **kwargs), kwargs, who)

    async def complete(*args: Any, **kwargs: Any):
        _observe(kwargs)
        who = call_who()
        kwargs = _with_level(who, kwargs)
        entry: CallEntry | None = None
        try:
            result = await real_complete(*args, **kwargs)
            provider = str(getattr(result, "provider", "") or "")
            response = getattr(result, "response", None)
            entry = _entry_from(
                provider, str(getattr(result, "model", "") or ""),
                getattr(response, "usage", None),
                with_tools=bool(kwargs.get("tools")), source="router.complete", manager=manager,
                who=who, account=call_account(platform, provider), extra=getattr(response, "extra", None),
            )
            return result
        finally:
            rollout.add(entry or CallEntry(
                complete=False, with_tools=bool(kwargs.get("tools")), source="router.complete", who=who,
            ))

    router.stream = stream
    router.complete = complete
    try:
        yield
    finally:
        if had_stream:
            router.stream = real_stream
        else:
            with contextlib.suppress(AttributeError):
                del router.stream
        if had_complete:
            router.complete = real_complete
        else:
            with contextlib.suppress(AttributeError):
                del router.complete


def _module_bytes(module: Any) -> bytes:
    """A module's source, line endings folded (a CRLF checkout of the same
    code is the same harness)."""
    try:
        return Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
    except (OSError, TypeError, AttributeError):
        return repr(getattr(module, "__name__", module)).encode("utf-8")


def _add(a: Any, b: Any) -> Any:
    return None if a is None or b is None else a + b


def _add_receipt(part: dict[str, Any], tokens_in: Any, tokens_out: Any,
                 metered: Any, listed: Any) -> None:
    """Add one receipt to a part (an unknown number stays unknown)."""
    part["receipts"] = int(part.get("receipts") or 0) + 1
    part["tokens_in"] = _add(part.get("tokens_in"), tokens_in)
    part["tokens_out"] = _add(part.get("tokens_out"), tokens_out)
    part["cost_metered_usd"] = _add(part.get("cost_metered_usd"), metered)
    part["cost_list_price_usd"] = _add(part.get("cost_list_price_usd"), listed)


def _on_network(provider: str, model: str, manager: Any) -> bool:
    try:
        from ..providers.egress import stays_on_this_network

        return bool(stays_on_this_network(provider, manager, None, model))
    except Exception:  # noqa: BLE001
        return False


def helper_cost(provider: str, model: str, usage: dict[str, Any], *,
                manager: Any = None) -> tuple[float | None, float | None]:
    """(metered, list-price equivalent) of a helper's run, read off its
    session row's usage (``harness/helpers.HelperCall.read_usage``: its cost
    by ``eval.pricing.recorded_cost``, flagged when a subscription served
    it); 0 for a model on the user's own network, unknown otherwise."""
    if "cost_usd" in usage:
        cost = float(usage.get("cost_usd") or 0.0)
        return (0.0, cost) if usage.get("list_price_equivalent") else (cost, 0.0)
    return (0.0, 0.0) if _on_network(provider, model, manager) else (None, None)


def keep_helper(helpers: dict[str, dict[str, Any]], data: dict[str, Any]) -> None:
    """Fold one ``helper`` frame into its row (later frames win, as the reply
    line keeps them). Never raises."""
    try:
        hid = str(data.get("id") or "")
        if not hid:
            return
        row = helpers.setdefault(hid, {})
        for key in HELPER_KEYS:
            if key in data and data[key] is not None:
                row[key] = data[key]
    except Exception:  # noqa: BLE001 — a row never breaks a run
        pass


def helper_rows(helpers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Who did what: one plain row per helper, in the order they started."""
    out: list[dict[str, Any]] = []
    for row in sorted(helpers.values(), key=lambda r: (int(r.get("since") or 0), str(r.get("id") or ""))):
        agent = str(row.get("agent") or "")
        used = row.get("usage") if isinstance(row.get("usage"), dict) else None
        out.append({
            "id": str(row.get("id") or ""),
            "agent": agent,
            "member": agent.split(":", 1)[1] if agent.startswith("custom:") else agent,
            "role": str(row.get("role") or ""),
            "reason": str(row.get("reason") or ""),
            "state": str(row.get("state") or ""),
            "provider": str(row.get("provider") or ""),
            "model": str(row.get("model") or ""),
            "session": str(row.get("session") or ""),
            "task": str(row.get("task") or "")[:HELPER_TEXT_CHARS],
            "result": str(row.get("result") or "")[:HELPER_TEXT_CHARS],
            "usage": dict(used) if used is not None else None,
        })
    return out


def _members(platform: Any) -> list[Any]:
    try:
        from ..agents import model_team as MT

        return list(MT.team_members(platform))
    except Exception:  # noqa: BLE001
        return []


#: v1.396.0: the instructions every member the TEAM benchmark makes is given
#: (``{name}`` is the member's name). A member whose stored instructions are
#: exactly these was made by the benchmark (:func:`_bench_made`) — the only
#: members a run may remove. Part of the team runner's identity.
MEMBER_PROMPT = (
    "You are {name}, a member of this coding team. Do exactly the piece of work you are "
    "handed, in the project folder, and finish with a short report of what you did."
)


def _bench_made(record: Any) -> bool:
    name = str(getattr(record, "name", "") or "")
    return bool(name) and str(getattr(record, "system_prompt", "") or "") == MEMBER_PROMPT.format(name=name)


#: Entries one run's TRAJECTORY keeps (v1.370.0): every tool call's input and
#: output (the lane's own masked ``detail``), each round's words, the reply.
TRAJECTORY_ENTRIES = 600


def layer_tree_hash(layer_dir: Path) -> str:
    """sha256 over a candidate layer's harness files (names + normalised
    bytes) — the identity of the harness a refine probe runs."""
    from ..harness.components import HARNESS_PARTS

    root = Path(layer_dir).joinpath(*HARNESS_PARTS)
    h = hashlib.sha256()
    if root.is_dir():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            h.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            h.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


class JarvisRunner:
    name = "jarvis"

    def __init__(self, platform: Any, *, provider: str = "", model: str = "",
                 round_budget: int = DEFAULT_ROUNDS, dialect: str = DEFAULT_DIALECT,
                 layer_dir: "Path | str | None" = None, effort: str = "") -> None:
        from ..daemon.chat_turn import turn_harness, turn_round_budget

        self.platform = platform
        self.provider = provider or ""
        self.model = model or ""
        #: v1.383.0: the effort the bench was asked for ("" = none asked —
        #: the refine loop and older callers; nothing is sent, as before).
        self.effort = (effort or "").strip().lower()
        if self.effort and self.effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        self.round_budget = turn_round_budget(int(round_budget))
        # "auto" or a dialect id; anything else is refused before any run.
        self.dialect = turn_harness(dialect or DEFAULT_DIALECT) or DEFAULT_DIALECT
        #: v1.370.0 (the refine loop): a CANDIDATE harness layer — a folder
        #: holding ``.ironjarvis/harness/`` — that every turn reads instead of
        #: the task copy's own (``components.LAYER_SOURCE``). None = the
        #: default harness, exactly as before.
        self.layer_dir = Path(layer_dir) if layer_dir else None

    def reasoning(self) -> str:
        """The reasoning level the turn SENDS: the asked effort when
        ``providers.reasoning`` offers it for this (provider, model) pair,
        else ``""`` (nothing sent — a level the pair does not take is never
        put on the wire)."""
        from ..providers.reasoning import supports

        return self.effort if self.effort and supports(self.provider, self.model, self.effort) else ""

    def passed_facts(self) -> dict[str, Any]:
        """What this runner passes: the model as named, and the effort as
        sent (``"not offered"`` when the pair takes no level; ``None`` when no
        effort was asked)."""
        return {
            "model_passed": self.model,
            "effort_passed": (self.reasoning() or NOT_OFFERED) if self.effort else None,
        }

    # -- v1.396.0: who did what (a single model: the planner alone) ---------- #

    def member_specs(self) -> dict[str, tuple[str, str]]:
        """The model-team members this runner's turns hand work to,
        ``{name: (provider, model)}`` — none for a single-model run (the
        team runner names its file's)."""
        return {}

    def call_reasoning(self, who: str) -> str:
        """v1.396.0 review: the level a call by model-team member ``who`` is
        sent with — none for a single-model run (the team runner's)."""
        return ""

    def member_facts(self) -> dict[str, dict[str, Any]]:
        """v1.396.0 review: what each member's part records besides its
        numbers (the level its calls sent, its step limit) — none here."""
        return {}

    def check_ready(self) -> None:
        """Refuse (``bench.BenchRefused``, in words) a single-model run in a
        benchmark home that holds a model team made by someone else: its
        planner would see the team and hand work to it, and the row would not
        be one model. Members the team benchmark made itself (a run that was
        stopped before it could remove them) are removed by :meth:`prepare`.
        BLOCKING (a registry read)."""
        foreign = [r.name for r in _members(self.platform) if not _bench_made(r)]
        if foreign:
            from .bench import BenchRefused

            raise BenchRefused(
                "This benchmark's home has model-team members (" + ", ".join(sorted(foreign))
                + "), so a single-model run could hand its work to them. Run it with a --root that has "
                "no team, or use --runner jarvis-team for a team."
            )

    def prepare(self) -> None:
        """Before the first task: remove the team members the team benchmark
        left behind (its process was stopped before it could). BLOCKING."""
        for record in _members(self.platform):
            if _bench_made(record):
                self.platform.agents_registry.remove(record.name)

    def close(self) -> None:
        """After the last task (nothing to undo for a single model)."""

    async def team_records_after(self) -> dict[str, Any] | None:
        """The team router's record after a run (the team runner's); None
        for a single model."""
        return None

    def _part_of(self, row: dict[str, Any]) -> str:
        """The part a helper row belongs to: a model-team MEMBER on its own
        model (the predicate its hold is armed by,
        ``model_team.runs_elsewhere``) is its own part; any other helper ran
        on the turn's model (``ledger.PLANNER``); a role the router could not
        place started nothing ("")."""
        agent = str(row.get("agent") or "")
        if agent.startswith("role:"):
            return ""
        name = agent.split(":", 1)[1] if agent.startswith("custom:") else agent
        if name in self.member_specs():
            return name
        try:
            from ..agents import model_team as MT

            record = self.platform.agents_registry.get(name)
            if record is not None and MT.runs_elsewhere(record):
                return name
        except Exception:  # noqa: BLE001 — not readable: the turn's own model
            pass
        return PLANNER

    def _new_part(self, provider: str, model: str) -> dict[str, Any]:
        on = _on_network(provider, model, getattr(self.platform, "providers", None))
        return {
            "provider": provider, "model": model, "vendor": _usage.vendor_of(provider, on_network=on),
            "on_this_network": on, "receipts": 0, "pieces": {}, "model_calls": 0,
            "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0,
            "cost_metered_usd": 0.0, "cost_list_price_usd": 0.0,
        }

    def _seed_parts(self, provider: str, model: str) -> dict[str, dict[str, Any]]:
        parts = {PLANNER: self._new_part(provider, model)}
        facts = self.member_facts()
        for name, (m_provider, m_model) in self.member_specs().items():
            parts[name] = {**self._new_part(m_provider, m_model), **facts.get(name, {})}
        return parts

    def _count_pieces(self, parts: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        for row in rows:
            name = self._part_of(row)
            if not name:
                continue
            part = parts.setdefault(name, self._new_part(str(row.get("provider") or ""),
                                                         str(row.get("model") or "")))
            key = str(row.get("role") or "") or ("by name" if name != PLANNER else "helper")
            part["pieces"][key] = int(part["pieces"].get(key, 0)) + 1

    @staticmethod
    def _observed(parts: dict[str, dict[str, Any]], rollout: Rollout) -> None:
        """Each part's model calls and cached tokens, as the ledger saw them."""
        for name, part in parts.items():
            calls = [c for c in rollout.calls if (c.who or PLANNER) == name]
            part["model_calls"] = len(calls)
            cached = [c.tokens_cached for c in calls if c.complete]
            part["tokens_cached"] = None if any(c is None for c in cached) else int(sum(cached))

    def parts_from_receipts(self, usage: dict[str, Any], route: dict[str, Any],
                            rows: list[dict[str, Any]] | None, rollout: Rollout, *,
                            provider: str, model: str) -> dict[str, dict[str, Any]]:
        """The run's parts from their OWN receipts (the other side of the
        ledger identity): the planner = the turn's done receipt plus every
        helper on its model; each member = its helpers' session rows."""
        parts = self._seed_parts(provider, model)
        rows = list(rows or [])
        _add_receipt(parts[PLANNER], int(usage.get("input_tokens", 0) or 0),
                     int(usage.get("output_tokens", 0) or 0), *lane_cost(usage, route))
        self._count_pieces(parts, rows)
        manager = getattr(self.platform, "providers", None)
        for row in rows:
            name = self._part_of(row)
            used = row.get("usage")
            if not name or not isinstance(used, dict):
                continue
            _add_receipt(parts[name], int(used.get("input_tokens", 0) or 0),
                         int(used.get("output_tokens", 0) or 0),
                         *helper_cost(str(row.get("provider") or ""), str(row.get("model") or ""), used,
                                      manager=manager))
        self._observed(parts, rollout)
        return parts

    def parts_from_calls(self, rollout: Rollout, rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
        """The parts of a turn that never reached its receipt: what the
        ledger saw, by who."""
        parts = self._seed_parts(self.provider, self.model)
        self._count_pieces(parts, list(rows or []))
        for c in rollout.calls:
            name = c.who or PLANNER
            part = parts.setdefault(name, self._new_part(c.provider, c.model))
            _add_receipt(part, c.tokens_in, c.tokens_out, c.cost_metered_usd, c.cost_list_price_usd)
        self._observed(parts, rollout)
        return parts

    def _layer(self) -> Any:
        from ..harness import components

        if self.layer_dir is None:
            return components.EMPTY_LAYER
        try:
            known = set(self.platform.registry.names())
        except Exception:  # noqa: BLE001
            known = None
        return components.read_project_layer(self.layer_dir, known_tools=known, session_id=None)

    def harness(self) -> Any:
        """The harness turn this runner's turns will build (no folder: a
        task's copy carries no harness layer; a refine probe's candidate
        layer when one is set), for the identity."""
        from ..daemon.chat_turn import _harness_model
        from ..harness.command_env import bash_shell_description, build_shell_description
        from ..harness.mode import build, speaks_bash

        provider, model = _harness_model(self.platform, self.provider, self.model)
        # v1.386.0: a dialect whose command tool's model writes bash (Claude
        # Code's `Bash`) is shown what really runs it on Windows (Git for
        # Windows' bash, or cmd.exe) — exactly as a Build turn builds it.
        bash_words = bash_shell_description(True) if speaks_bash(self.dialect, provider, model) else ""
        return build(
            registry=self.platform.registry, permissions=self.platform.permissions,
            requested=self.dialect, provider=provider, model=model, stream=True,
            approval_mode=JARVIS_APPROVAL_MODE, today="", layer=self._layer(),
            # v1.378.0: the turn runs as a Build session, so its shell is the
            # one a Build session's model is shown (the user's PATH).
            shell_description="" if bash_words else build_shell_description(),
            bash_description=bash_words,
        )

    def identity(self) -> dict[str, Any]:
        """What makes this harness THIS harness, computable before any run:
        the two chat lanes' source, the harness modules, the dialect and the
        FINGERPRINT of the rendered component tree, the specs the model is
        shown (names, descriptions, argument shapes), the persona, the
        prompt, the round budget and the approval mode."""
        from ..daemon import chat_turn
        from ..daemon.routes import chat as chat_routes
        from ..harness import components as harness_components
        from ..harness import checkers as harness_checkers
        from ..harness import dialects as harness_dialects
        from ..harness import finish as harness_finish
        from ..harness import folder_changes as harness_folder_changes
        from ..harness import mode as harness_mode
        from ..harness import project_map as harness_project_map
        from ..personas.builtins import BUILTIN_PERSONAS

        turn = self.harness()
        specs = turn.specs(self.platform.registry)
        persona = str(getattr(self.platform.config, "default_persona", "") or "")
        # The state home's user profile rides every prompt (v1.144.0); a
        # scratch home has none, but a home that has one is another harness.
        profile = chat_turn._profile_section(self.platform)
        return {
            "lane_sha256": _sha(_module_bytes(chat_turn) + b"\0" + _module_bytes(chat_routes)),
            "harness_sha256": _sha(
                _module_bytes(harness_mode) + b"\0" + _module_bytes(harness_dialects)
                + b"\0" + _module_bytes(harness_components)
                # Part 3, Phase 2: the finish check is part of the harness
                # (a Build turn runs it), so a change to it is a new harness.
                + b"\0" + _module_bytes(harness_finish)
                # ...and its folder comparison (what a reply changed through
                # commands decides whether the check runs, and what it lists).
                + b"\0" + _module_bytes(harness_folder_changes)
                # Part 3, Phase 3: so are the project map every Build turn
                # starts with and the project's own checkers after an edit
                # (the map itself is per folder, never in the fingerprint).
                + b"\0" + _module_bytes(harness_project_map)
                + b"\0" + _module_bytes(harness_checkers)
            ),
            "dialect": turn.plan.dialect.id,
            "dialect_requested": self.dialect,
            "family": turn.plan.family,
            "harness_fingerprint": turn.fingerprint,
            "tools_sha256": _sha(json.dumps(specs, sort_keys=True, default=str)),
            "tools": [s["name"] for s in specs],
            "persona": persona,
            "persona_sha256": _sha(json.dumps(BUILTIN_PERSONAS.get(persona, persona), sort_keys=True, default=str)),
            "prompt_sha256": _sha(PROMPT_TEMPLATE),
            "profile_sha256": _sha(profile),
            "round_budget": self.round_budget,
            "approval_mode": JARVIS_APPROVAL_MODE,
            # v1.378.0: the turn runs as a Build session (its shell's
            # environment, the held model).
            "session": "build",
            # v1.383.0: the reasoning level the turn sends (absent when no
            # effort was asked, so every earlier configuration keeps its key).
            **({"reasoning": self.reasoning() or NOT_OFFERED} if self.effort else {}),
            # v1.370.0: a refine probe's candidate layer (absent for the
            # default harness, so every earlier configuration keeps its key).
            **({"layer_sha256": layer_tree_hash(self.layer_dir), "layer_used": bool(turn.layer.used)}
               if self.layer_dir is not None else {}),
            # v1.386.0: the shell a bash-written command tool (Claude Code's
            # `Bash`) runs in IS part of the harness — Git for Windows' bash
            # (its version) or cmd.exe — so a run under one is never reused
            # for the other (absent for a dialect without one).
            **({"command_shell": shell} if (shell := self._command_shell(turn)) else {}),
        }

    @staticmethod
    def _command_shell(turn: Any) -> str:
        from ..harness import dialects as harness_dialects
        from ..harness.command_env import bash_identity

        return bash_identity() if harness_dialects.speaks_bash(turn.plan.dialect) else ""

    async def run(self, task: Task, workspace: Path, rollout: Rollout, *, timeout_s: float) -> RunOutcome:
        from ..core.fs_policy import usable_workspace_root
        from ..daemon.chat_stream import stream_chat_turn
        from ..daemon.routes.chat import _approvals
        from ..daemon.schemas import ChatBody, ChatMessageBody
        from ..personas.builtins import BUILTIN_PERSONAS

        out = RunOutcome(usage_source="lane")
        if not await asyncio.to_thread(usable_workspace_root, str(workspace)):
            # The lane would quietly run the tools in its scratch folder instead.
            out.error = f"Iron Jarvis would not work in {workspace} (the folder was refused)"
            return out
        # HARNESS MODE (v1.366.0) arms the coding set itself — no "+" picks.
        body = ChatBody(
            messages=[ChatMessageBody(role="user", content=render_prompt(task, workspace))],
            provider=self.provider, model=self.model,
            workspace_dir=str(workspace), approval_mode=JARVIS_APPROVAL_MODE,
            # v1.383.0: the level only where the pair offers it.
            **({"reasoning": self.reasoning()} if self.reasoning() else {}),
        )
        deadline = time.monotonic() + float(timeout_s)
        # v1.400.2: a task may ask for more rounds than the runner's budget
        # (the team tier: the product's own ceiling); never fewer.
        budget = rounds_for(task, self.round_budget)
        approvals = _approvals(SimpleNamespace(platform=self.platform))
        observed: dict[str, Any] = {}
        #: v1.396.0: the turn's helper rows by call id (``keep_helper``).
        helpers: dict[str, dict[str, Any]] = {}
        rounds = tools = 0
        done: dict[str, Any] | None = None
        error = ""
        error_fix_kind = ""
        timed_out = False
        #: v1.370.0: the run's TRAJECTORY — what the agent did, step by step
        #: (the refine loop's evidence; never shown to the agent).
        trajectory: list[dict[str, Any]] = []
        words: list[str] = []

        def _note(entry: dict[str, Any]) -> None:
            if len(trajectory) < TRAJECTORY_ENTRIES:
                trajectory.append(entry)

        def _flush_words() -> None:
            text = "".join(words).strip()
            words.clear()
            if text:
                _note({"kind": "text", "text": text[:4000]})

        from uuid import uuid4

        from ..daemon.chat_turn import _harness_model
        from ..harness.components import layer_source
        from ..harness.sessions import session_id_for
        from ..providers.hold import reset_hold, set_hold

        # v1.378.0: the turn runs AS A BUILD SESSION (BENCH_SESSION_PREFIX):
        # a fresh ``build:`` session for its tool calls, and the model held
        # for the turn and its helpers — the pair a Build turn would run on.
        session = session_id_for(BENCH_SESSION_PREFIX + uuid4().hex[:12])
        # (review: the session is ended however the block is left — a Stop
        # included — and the hold is armed inside it, so nothing can leave
        # it armed.)
        # v1.383.0 review: the turn's Build session reads os.environ for its
        # commands' environment — without the bench root in it.
        with session_ended_on_escape(session), \
                counting_router(self.platform, rollout, observed, reasoning_for=self.call_reasoning), \
                layer_source(self.layer_dir), hidden_environ():
            hold_token = set_hold(*_harness_model(self.platform, self.provider, self.model))
            try:
                gen = await stream_chat_turn(
                    self.platform, BUILTIN_PERSONAS, body,
                    should_stop=lambda: time.monotonic() > deadline,
                    round_budget=budget, stay_in_chat=True,
                    harness=self.dialect, session_id=session,
                )
                async with asyncio.timeout(float(timeout_s) + _GRACE_S):
                    async with contextlib.aclosing(gen) as frames:
                        async for chunk in frames:
                            for event, data in parse_sse(chunk):
                                if event == "round":
                                    rounds += 1
                                    _flush_words()
                                elif event == "token":
                                    words.append(str(data.get("text") or ""))
                                elif event == "reset":
                                    words.clear()
                                elif event == "tool_call" and data.get("status") == "started":
                                    _flush_words()
                                    _note({"kind": "call", "id": data.get("id"), "tool": data.get("name"),
                                           "args": data.get("args")})
                                elif event == "tool_call" and data.get("status") == "finished":
                                    tools += 1
                                    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
                                    _note({"kind": "result", "id": data.get("id"), "tool": data.get("name"),
                                           "ok": data.get("ok"),
                                           "output": str(detail.get("output") or data.get("output") or "")[:2000]})
                                elif event == "approval":
                                    # Belt and braces: yolo grants every card
                                    # these tools raise; one that still shows
                                    # is answered for this call only.
                                    if approvals.resolve(str(data.get("id") or ""), "once"):
                                        out.notes.append(f"answered an approval for {data.get('tool')}")
                                elif event == "helper":
                                    # v1.396.0: WHO DID WHAT — each helper's
                                    # row, as the reply line keeps it (its
                                    # member, role, the router's reason, its
                                    # model and what its run used).
                                    keep_helper(helpers, data)
                                    if isinstance(data.get("ask"), dict):
                                        # v1.372.0: a HELPER's own ask (yolo is
                                        # never handed down — a helper asks) is
                                        # answered the same way: this call only.
                                        _ask = data["ask"]
                                        if approvals.resolve(str(_ask.get("id") or ""), "once"):
                                            out.notes.append(
                                                f"answered a helper's approval for {_ask.get('tool')}"
                                            )
                                elif event == "error":
                                    error = str(data.get("detail") or data.get("title") or "the turn failed")
                                    error_fix_kind = str(data.get("fix_kind") or "")
                                elif event == "done":
                                    done = data
            except TimeoutError:
                timed_out = True
            except Exception as exc:  # noqa: BLE001 — recorded, never raised past the run
                error = f"{type(exc).__name__}: {exc}"
            finally:
                reset_hold(hold_token)
        # What the turn's shell / run_code left running (a server the agent
        # started and never stopped) belongs to the turn's session until that
        # session ends (`procs.owned_by` / `procs.end_session`) — and in this
        # process nothing else ever ends it, so it would outlive the benchmark
        # and keep its folder open. Stop it here, off the loop (this also
        # closes the session's agent browser: the end hook of page_browser).
        stopped = await asyncio.to_thread(procs.end_session, session)
        if stopped:
            out.notes.append(f"stopped {len(stopped)} program(s) the turn left running")
        if time.monotonic() > deadline and done is None:
            timed_out = True
        _flush_words()
        if done is not None:
            _note({"kind": "reply", "text": str(done.get("reply") or "")[:4000]})
        elif error:
            _note({"kind": "error", "text": error[:2000]})
        out.extra["trajectory"] = trajectory
        out.tool_calls = tools
        out.model_rounds = rounds
        out.extra["model_calls"] = len(rollout.calls)
        out.extra["incomplete_calls"] = sum(1 for c in rollout.calls if not c.complete)
        out.extra["observed"] = observed
        out.facts["timed_out"] = bool(timed_out and done is None)
        # The agent had started work: a tool ran, or the model answered once.
        out.facts["acted"] = tools > 0 or any(c.complete for c in rollout.calls)
        if error_fix_kind:
            out.facts["fix_kind"] = error_fix_kind
        # v1.383.0: the model the turn REPORTED — its done receipt, else the
        # last model call that answered (a turn that failed part-way).
        answered = [c for c in rollout.calls if c.complete and c.model]
        out.model_reported = (
            str(done.get("model") or "") or None if done is not None
            else (answered[-1].model if answered else None)
        )
        # v1.396.0: who did what, and what each account's usage windows said
        # during the run (from the calls' own answers — never guessed).
        out.helpers = helper_rows(helpers)
        out.accounts = _usage.account_summary(rollout.calls)
        out.team_records = await self.team_records_after()
        # v1.396.0 review: a helper (a team member) that could not do its
        # piece for an infrastructure reason — its account's limit, a sign-in,
        # an unreachable server — means the run never had its fair go: "did
        # not run", never the team's (or the model's) failure.
        from .failures import helper_infra

        blocked = helper_infra(out.helpers)
        if blocked:
            out.facts["member_infra"] = blocked
        if done is None:
            out.error = error or (
                f"stopped at the {timeout_s:g} s time limit" if timed_out
                else "the turn ended without a reply"
            )
            totals = rollout.totals()
            out.tokens_in, out.tokens_out = totals["tokens_in"], totals["tokens_out"]
            out.cost_metered_usd = totals["cost_metered_usd"]
            out.cost_list_price_usd = totals["cost_list_price_usd"]
            out.usage_source = "ledger"
            # Nothing received a receipt: each part is what the ledger saw.
            out.parts = self.parts_from_calls(rollout, out.helpers)
            return out
        usage = done.get("usage") or {}
        route = done.get("route") or {}
        # The reasoning level the route says was APPLIED ("" = none).
        out.effort_reported = str(route.get("reasoning") or "") or None
        out.answered_provider = str(done.get("provider") or "")
        out.answered_model = str(done.get("model") or "")
        out.final_reply = str(done.get("reply") or "")[:REPLY_CHARS]
        out.tokens_in = int(usage.get("input_tokens", 0) or 0)
        out.tokens_out = int(usage.get("output_tokens", 0) or 0)
        cached = usage.get("cache_read_input_tokens")
        out.tokens_cached = int(cached) if isinstance(cached, (int, float)) else None
        out.cost_metered_usd, out.cost_list_price_usd = lane_cost(usage, route)
        # v1.396.0: the run's PARTS from their own receipts — the turn's done
        # receipt for its own model, each helper's session row for the
        # helper — and the totals are their sum (a turn that started no
        # helper keeps exactly the receipt's numbers above).
        out.parts = self.parts_from_receipts(
            usage, route, out.helpers, rollout,
            provider=out.answered_provider or self.provider, model=out.answered_model or self.model,
        )
        if any(int(p.get("receipts") or 0) for name, p in out.parts.items() if name != PLANNER) or \
                int(out.parts.get(PLANNER, {}).get("receipts") or 0) > 1:
            from .ledger import sum_fields

            totals = sum_fields(out.parts.values())
            out.tokens_in, out.tokens_out = totals["tokens_in"], totals["tokens_out"]
            out.cost_metered_usd = totals["cost_metered_usd"]
            out.cost_list_price_usd = totals["cost_list_price_usd"]
            cached_parts = [p.get("tokens_cached") for p in out.parts.values()]
            out.tokens_cached = None if any(c is None for c in cached_parts) else int(sum(cached_parts))
        out.extra.update({
            "escalated": bool(done.get("escalate")),
            "text_only": bool(route.get("text_only")),
            "route_reason": str(route.get("reason") or ""),
            "on_this_network": route.get("on_this_network"),
            "documents": list(done.get("documents") or []),
            "tools_used": list(done.get("tools_used") or []),
            # v1.366.0: the harness the turn really ran (family, dialect, the
            # component tree's fingerprint, whether it was nudged).
            "harness": route.get("harness"),
            # Part 3, Phase 2: what the reply's finish check did (None when
            # it did not run: the reply changed no file).
            "finish": done.get("finish"),
        })
        out.ok_run = True
        out.facts["finished"] = True
        # The agent used every round it was given (the turn then ended in
        # chat with one tool-less answer).
        out.facts["cut_off"] = rounds >= budget
        if self.provider and out.answered_provider != self.provider:
            out.ok_run = False
            out.facts["answered_by"] = (
                f"{out.answered_provider or 'nothing'} instead of {self.provider}"
            )
            out.error = (
                f"answered by {out.answered_provider or 'nothing'} instead of {self.provider} "
                f"({route.get('reason') or 'no reason given'})"
            )
        elif route.get("reason") == "mock":
            out.ok_run = False
            out.facts["demo_answered"] = True
            out.error = "the demo model answered, not a real model"
        if out.extra["escalated"]:
            out.notes.append("the model asked to hand the work to an agent; the turn ended there")
        if out.extra["text_only"]:
            out.notes.append("the model ran with no tools (it cannot use them)")
        return out


# --------------------------------------------------------------------------- #
# Claude Code and Codex
# --------------------------------------------------------------------------- #


def find_cli(name: str) -> str | None:
    """The CLI's executable, resolved like the app's own availability probe."""
    try:
        from ..terminals.ai_clis import _find

        return _find(name)
    except Exception:  # noqa: BLE001
        import shutil

        return shutil.which(name)


#: v1.383.0 review: the paths no runner may be handed — the bench's state home
#: (its results, every other run's finished work). ``bench.run_task`` sets it
#: around each run; :func:`hide_paths` applies it.
HIDDEN_PATHS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "ij_bench_hidden_paths", default=()
)
#: Variables that describe the folders of the SHELL the bench was started
#: from — meaningless to a child whose folder is set, and a path to wherever
#: that shell stood (the bench root, the source checkout). Never handed on.
_SHELL_FOLDERS = frozenset({"PWD", "OLDPWD"})


def _path_key(text: str) -> str:
    return text.replace("\\", "/").rstrip("/").lower()


def hide_paths(env: dict[str, str], hidden: tuple[str, ...] | None = None) -> dict[str, str]:
    """``env`` without the shell's own folders (``PWD``/``OLDPWD``) and
    without any value that names a :data:`HIDDEN_PATHS` path: a list of
    folders (``PATH``-like) loses just those entries, any other value goes.
    ``hidden`` defaults to the current run's."""
    marks = [_path_key(h) for h in (HIDDEN_PATHS.get() if hidden is None else hidden) if h]
    out: dict[str, str] = {}
    for name, value in env.items():
        if name.upper() in _SHELL_FOLDERS:
            continue
        if not marks or not any(m in _path_key(value) for m in marks):
            out[name] = value
            continue
        parts = value.split(os.pathsep)
        kept = [part for part in parts if not any(m in _path_key(part) for m in marks)]
        if len(parts) > 1 and kept:
            out[name] = os.pathsep.join(kept)
    return out


@contextlib.contextmanager
def hidden_environ(hidden: tuple[str, ...] | None = None):
    """This process's environment through :func:`hide_paths` while the block
    runs (a Jarvis turn's Build session reads ``os.environ`` for its
    commands' environment), put back exactly afterwards."""
    saved = dict(os.environ)
    clean = hide_paths(saved, hidden)
    for name in saved:
        if name not in clean:
            os.environ.pop(name, None)
        elif clean[name] != saved[name]:
            os.environ[name] = clean[name]
    try:
        yield
    finally:
        for name, value in saved.items():
            if os.environ.get(name) != value:
                os.environ[name] = value


def cli_env() -> dict[str, str]:
    """This process's environment for a CLI child, minus a parent Claude Code
    session's markers (the coordinator runs inside one)."""
    return drop_claude_session_markers(dict(os.environ))


def claude_env() -> dict[str, str]:
    """The ``claude`` child's environment: the app's OWN guard
    (``providers.claude_models.child_env`` — every ``ANTHROPIC_*`` variable,
    the Bedrock/Vertex/Foundry switches and ``CLAUDE_CODE_EXTRA_BODY``
    removed, the parent session's markers dropped), so a stray key or base
    URL in the shell can never bill or reroute a benchmark run: Claude Code
    runs on the user's own sign-in, and its cost is always a list-price
    equivalent."""
    from ..providers.claude_models import child_env

    return drop_claude_session_markers(child_env(dict(os.environ)))


def cli_version(binary: str) -> str:
    """``<binary> --version`` (no model call). Raises when it cannot run."""
    result = procs.run([binary, "--version"], timeout=60, background=False)
    text = procs.text(result.stdout or b"", "utf-8").strip() or procs.text(result.stderr or b"", "utf-8").strip()
    if result.timed_out or result.returncode != 0 or not text:
        raise RuntimeError(f"{binary} --version did not answer")
    return text.splitlines()[0].strip()


def _json_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_claude_stream(stdout: str) -> dict[str, Any]:
    """Claude Code's ``stream-json`` lines → the run's numbers. The ``result``
    line is the ``--output-format json`` object (``num_turns``,
    ``total_cost_usd``, ``usage``, ``result``); the ``assistant`` lines carry
    the ``tool_use`` blocks, counted once each by id. ``answered`` counts the
    model's own assistant messages (not the API-error ones Claude Code writes
    as assistant lines). An ``error_during_execution`` result carries its
    words in ``errors`` (a list) and has no ``result`` — read from Claude
    Code 2.1.306's own result builder — so ``errors`` is kept too."""
    tool_ids: list[str] = []
    answered = 0
    result: dict[str, Any] | None = None
    reported: str | None = None
    for obj in _json_lines(stdout):
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init" and isinstance(obj.get("model"), str):
            # v1.383.0: Claude Code's init event names the model it runs on
            # (read from 2.1.309's own init builder: ``model: e.model``).
            reported = obj["model"].strip() or reported
        elif kind == "assistant":
            message = obj.get("message") or {}
            if not obj.get("isApiErrorMessage") and message.get("content"):
                answered += 1
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    bid = str(block.get("id") or f"anon-{len(tool_ids)}")
                    if bid not in tool_ids:
                        tool_ids.append(bid)
        elif kind == "result":
            result = obj
    parsed: dict[str, Any] = {
        "tool_calls": len(tool_ids), "answered": answered, "result": result, "model_reported": reported,
    }
    if result is None:
        return parsed
    usage = result.get("usage") or {}
    cached = _int(usage.get("cache_read_input_tokens"))
    errors = result.get("errors")
    parsed.update({
        "is_error": bool(result.get("is_error")),
        "subtype": str(result.get("subtype") or ""),
        "num_turns": _int(result.get("num_turns")) if result.get("num_turns") is not None else None,
        "tokens_in": _int(usage.get("input_tokens")) + _int(usage.get("cache_creation_input_tokens")) + cached,
        "tokens_out": _int(usage.get("output_tokens")),
        "tokens_cached": cached,
        "reply": str(result.get("result") or ""),
        "errors": [str(e) for e in errors] if isinstance(errors, list) else [],
    })
    cost = result.get("total_cost_usd")
    # Always the subscription's (``claude_env`` strips every API key): a
    # list-price equivalent, never metered money.
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        parsed["cost_metered_usd"] = 0.0
        parsed["cost_list_price_usd"] = float(cost)
    else:
        parsed["cost_metered_usd"] = parsed["cost_list_price_usd"] = None
    return parsed


#: Codex item types that are a tool the model used.
CODEX_TOOL_ITEMS = frozenset({
    "command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call",
})


def parse_codex_events(stdout: str, model: str = "", billing: str = "list_price") -> dict[str, Any]:
    """``codex exec --json`` events → the run's numbers. Usage comes from each
    ``turn.completed``; tools from completed items of a tool kind. Codex
    reports no per-call model rounds. Its cost, when the model has a price
    row, is METERED money on an API-key login (``billing="metered"``) and a
    list-price equivalent on a ChatGPT plan; without a price row, unknown."""
    tools = 0
    items = 0
    turns: list[dict[str, Any]] = []
    error = ""
    reply = ""
    reported: str | None = None
    for obj in _json_lines(stdout):
        kind = obj.get("type")
        # v1.383.0: a model only if an EVENT itself names one (top level, or
        # its thread / turn object) — never read out of an item, whose text
        # is the model's own words, and never invented.
        for holder in (obj, obj.get("thread"), obj.get("turn")):
            if isinstance(holder, dict) and isinstance(holder.get("model"), str) and holder["model"].strip():
                reported = holder["model"].strip()
        if kind == "item.completed":
            items += 1
            item = obj.get("item") or {}
            if item.get("type") in CODEX_TOOL_ITEMS:
                tools += 1
            elif item.get("type") == "agent_message":
                reply = str(item.get("text") or reply)
        elif kind == "turn.completed":
            turns.append(obj.get("usage") or {})
            # A turn that completed AFTER an error event outlived it: that
            # error was not the end of the run.
            error = ""
        elif kind == "turn.failed":
            error = str((obj.get("error") or {}).get("message") or "the turn failed")
        elif kind == "error":
            error = str(obj.get("message") or "Codex reported an error")
    entries = []
    for usage in turns:
        tin, cached, tout = _int(usage.get("input_tokens")), _int(usage.get("cached_input_tokens")), _int(usage.get("output_tokens"))
        listed = None
        if model:
            from ..eval.pricing import is_priced, step_cost

            if is_priced("openai", model):
                listed = float(step_cost("openai", model, {
                    "input_tokens": max(tin - cached, 0), "cache_read_input_tokens": cached,
                    "output_tokens": tout,
                }))
        if listed is None:
            metered_cost, list_cost = None, None
        elif billing == "metered":
            metered_cost, list_cost = listed, 0.0
        else:
            metered_cost, list_cost = 0.0, listed
        entries.append({
            "tokens_in": tin, "tokens_out": tout, "tokens_cached": cached,
            "cost_metered_usd": metered_cost, "cost_list_price_usd": list_cost,
        })
    return {"tool_calls": tools, "items": items, "turns": entries, "error": error, "reply": reply,
            "model_reported": reported}


#: Environment names under which ``codex exec`` bills an API account.
CODEX_API_KEY_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")

#: A Codex log line at ERROR level ("ERROR codex_core::…", with or without a
#: timestamp in front) …
_CODEX_ERROR_LINE = re.compile(r"(?:^|\s)ERROR\b")
#: … that says its sandbox refused a command. Measured on 0.157.0 (Windows,
#: ``--sandbox workspace-write``): ``ERROR codex_core::tools::router:
#: error=exec_command failed: CreateProcess { message: "Rejected(\"`…`
#: rejected: blocked by policy\")" }``.
_CODEX_SANDBOX_REFUSAL = re.compile(
    r"blocked by policy|rejected\(|sandbox (?:denied|refused|rejected)|denied by (?:the )?sandbox",
    re.IGNORECASE,
)


def codex_sandbox_refusals(stderr: str, prompt: str = "") -> list[str]:
    """Codex's own ERROR lines that say its sandbox refused a command. A
    line that also occurs in the prompt is not Codex's (``codex exec`` echoes
    the prompt into stderr), so a task's words can never count."""
    out: list[str] = []
    for raw in (stderr or "").splitlines():
        line = raw.strip()
        if not line or line in (prompt or ""):
            continue
        if _CODEX_ERROR_LINE.search(line) and _CODEX_SANDBOX_REFUSAL.search(line):
            out.append(line)
    return out


def codex_billing(binary: str | None, env: dict[str, str] | None = None) -> tuple[str, str]:
    """``(billing, how_we_know)``: ``"metered"`` when an API key is in the
    environment or ``codex login status`` (no model call) says the login is
    an API key, else ``"list_price"`` (a ChatGPT plan). Only the derived word
    is kept — the status text can carry part of a key and is never stored
    or logged."""
    env = dict(os.environ if env is None else env)
    for name in CODEX_API_KEY_ENV:
        if (env.get(name) or "").strip():
            return "metered", f"{name} is set"
    if not binary:
        return "list_price", "assumed (Codex not found)"
    try:
        result = procs.run([binary, "login", "status"], timeout=60, env=env, background=False)
    except OSError:
        return "list_price", "assumed (login status could not run)"
    said = (procs.text(result.stdout or b"", "utf-8") + procs.text(result.stderr or b"", "utf-8")).lower()
    if "api key" in said or "api-key" in said:
        return "metered", "codex login status: API key"
    if "chatgpt" in said:
        return "list_price", "codex login status: ChatGPT"
    return "list_price", "assumed (login status did not say)"


class _CliRunner:
    name = ""
    binary_name = ""
    display = ""

    def __init__(self, *, model: str = "", binary: str | None = None, effort: str = "") -> None:
        self.provider = self.name
        self.model = model or ""
        #: v1.383.0: passed on the command line when set ("" = not asked).
        self.effort = (effort or "").strip().lower()
        if self.effort and self.effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        self.binary = binary or find_cli(self.binary_name)
        self._version: str | None = None

    def passed_facts(self) -> dict[str, Any]:
        """What this runner passes on the command line: the model and the
        effort (``None`` when none was asked)."""
        return {"model_passed": self.model, "effort_passed": self.effort or None}

    def argv(self, workspace: Path) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def child_env(self) -> dict[str, str]:
        return cli_env()

    def version(self) -> str:
        if self._version is None:
            if not self.binary:
                raise RuntimeError(f"{self.binary_name} was not found on this PC")
            self._version = cli_version(self.binary)
        return self._version

    def identity(self) -> dict[str, Any]:
        # The flags, never the executable's path (it differs per machine).
        return {
            "cli_version": self.version(),
            "argv": self.argv(Path("{folder}"))[1:],
            "prompt_sha256": _sha(PROMPT_TEMPLATE),
        }

    def _not_started(self) -> RunOutcome | None:
        out = RunOutcome(usage_source="cli", answered_provider=self.name, answered_model=self.model)
        if not self.binary:
            out.error = f"{self.display} was not found on this PC"
            out.facts["cli_missing"] = True
            return out
        return None

    async def _execute(self, workspace: Path, prompt: str, timeout_s: float):
        # v1.383.0 review: nothing in the child's environment leads to the
        # bench root (the shell's PWD, a value naming it).
        env = hide_paths(self.child_env())
        result = await procs.run_async(
            self.argv(workspace), timeout=float(timeout_s), input=prompt.encode("utf-8"),
            cwd=workspace, env=env, background=False,
        )
        return result, env


class ClaudeRunner(_CliRunner):
    name = "claude"
    binary_name = "claude"
    display = "Claude Code"

    def argv(self, workspace: Path) -> list[str]:
        out = [
            str(self.binary or "claude"), "-p", "--safe-mode",
            "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
        ]
        if self.model:
            out += ["--model", self.model]
        if self.effort:
            out += ["--effort", self.effort]
        return out

    def child_env(self) -> dict[str, str]:
        return claude_env()

    def identity(self) -> dict[str, Any]:
        # Always the user's own sign-in (``claude_env``): a list-price equivalent.
        return {**super().identity(), "billing": "list_price"}

    async def run(self, task: Task, workspace: Path, rollout: Rollout, *, timeout_s: float) -> RunOutcome:
        missing = self._not_started()
        if missing is not None:
            return missing
        out = RunOutcome(usage_source="cli", answered_provider=self.name, answered_model=self.model)
        try:
            result, _env = await self._execute(workspace, render_prompt(task, workspace), timeout_s)
        except OSError as exc:
            out.error = f"Claude Code could not start ({exc})"
            out.facts["start_failed"] = True
            return out
        stdout = procs.text(result.stdout or b"", "utf-8")
        stderr = procs.text(result.stderr or b"", "utf-8")
        parsed = parse_claude_stream(stdout)
        out.tool_calls = parsed["tool_calls"]
        out.model_reported = parsed.get("model_reported")
        out.extra["exit_code"] = result.returncode
        out.facts["timed_out"] = bool(result.timed_out)
        out.facts["acted"] = parsed["tool_calls"] > 0 or parsed["answered"] > 0
        if parsed["result"] is None:
            rollout.add(CallEntry(
                provider=self.name, model=self.model, tokens_in=None, tokens_out=None,
                tokens_cached=None, cost_metered_usd=None, cost_list_price_usd=None,
                complete=False, source="claude: no result line",
            ))
            out.error = (
                f"stopped at the {timeout_s:g} s time limit" if result.timed_out
                else "Claude Code ended without a result: " + (stderr.strip()[-500:] or f"exit code {result.returncode}")
            )
            return out
        out.model_rounds = parsed["num_turns"]
        out.tokens_in, out.tokens_out = parsed["tokens_in"], parsed["tokens_out"]
        out.tokens_cached = parsed["tokens_cached"]
        out.cost_metered_usd = parsed["cost_metered_usd"]
        out.cost_list_price_usd = parsed["cost_list_price_usd"]
        out.final_reply = parsed["reply"][:REPLY_CHARS]
        rollout.add(CallEntry(
            provider=self.name, model=self.model, tokens_in=out.tokens_in, tokens_out=out.tokens_out,
            tokens_cached=out.tokens_cached, cost_metered_usd=out.cost_metered_usd,
            cost_list_price_usd=out.cost_list_price_usd, complete=True, with_tools=True,
            source="claude: result line",
        ))
        out.ok_run = not parsed["is_error"] and result.returncode == 0 and not result.timed_out
        out.facts["finished"] = out.ok_run
        # Claude Code's own "the agent used every turn it had".
        out.facts["cut_off"] = parsed["subtype"] == "error_max_turns"
        if not out.ok_run and parsed["is_error"]:
            out.error = (
                parsed["reply"][:500] or "; ".join(parsed["errors"])[:500]
                or parsed["subtype"] or f"exit code {result.returncode}"
            )
        elif not out.ok_run:
            # A result that is NOT an error, yet the process failed: the
            # result text is the agent's own answer and must not be read for
            # infrastructure words ("fixed the 503 retry" is not a 503).
            out.error = f"exit code {result.returncode}" + (
                f": {stderr.strip()[-500:]}" if stderr.strip() else ""
            )
        if parsed["subtype"] and parsed["subtype"] != "success":
            out.notes.append(f"Claude Code ended with {parsed['subtype']}")
        return out


class CodexRunner(_CliRunner):
    name = "codex"
    binary_name = "codex"
    display = "Codex"

    def __init__(self, *, model: str = "", binary: str | None = None, effort: str = "") -> None:
        super().__init__(model=model, binary=binary, effort=effort)
        self._billing: tuple[str, str] | None = None

    def billing(self) -> tuple[str, str]:
        """``(billing, how_we_know)`` — see :func:`codex_billing`. Read once."""
        if self._billing is None:
            self._billing = codex_billing(self.binary, self.child_env())
        return self._billing

    def identity(self) -> dict[str, Any]:
        # Which account pays is part of the setup: an API-key login is metered.
        return {**super().identity(), "billing": self.billing()[0]}

    def argv(self, workspace: Path) -> list[str]:
        out = [
            str(self.binary or "codex"), "exec", "--json",
            "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
            "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--color", "never", "-C", str(workspace),
        ]
        if self.model:
            out += ["-m", self.model]
        if self.effort:
            # A TOML string (``codex exec --help``: "The `value` portion is
            # parsed as TOML"); Codex's own config key for the level.
            out += ["-c", f'model_reasoning_effort="{self.effort}"']
        return out + ["-"]

    async def run(self, task: Task, workspace: Path, rollout: Rollout, *, timeout_s: float) -> RunOutcome:
        missing = self._not_started()
        if missing is not None:
            return missing
        out = RunOutcome(usage_source="cli", answered_provider=self.name, answered_model=self.model)
        billing, billing_source = await asyncio.to_thread(self.billing)
        out.extra["billing"] = billing
        out.extra["billing_source"] = billing_source
        prompt = render_prompt(task, workspace)
        try:
            result, _env = await self._execute(workspace, prompt, timeout_s)
        except OSError as exc:
            out.error = f"Codex could not start ({exc})"
            out.facts["start_failed"] = True
            return out
        stdout = procs.text(result.stdout or b"", "utf-8")
        stderr = procs.text(result.stderr or b"", "utf-8")
        parsed = parse_codex_events(stdout, self.model, billing)
        out.tool_calls = parsed["tool_calls"]
        out.model_reported = parsed.get("model_reported")
        out.final_reply = parsed["reply"][:REPLY_CHARS]
        out.extra["exit_code"] = result.returncode
        out.extra["turns"] = len(parsed["turns"])
        turns = parsed["turns"] or [{
            "tokens_in": None, "tokens_out": None, "tokens_cached": None,
            "cost_metered_usd": None, "cost_list_price_usd": None,
        }]
        for t in turns:
            rollout.add(CallEntry(
                provider=self.name, model=self.model, complete=bool(parsed["turns"]), with_tools=True,
                source="codex: turn.completed" if parsed["turns"] else "codex: no usage reported", **t,
            ))
        totals = rollout.totals()
        out.tokens_in, out.tokens_out = totals["tokens_in"], totals["tokens_out"]
        cached = [t["tokens_cached"] for t in turns]
        out.tokens_cached = None if any(c is None for c in cached) else sum(cached)
        out.cost_metered_usd = totals["cost_metered_usd"]
        out.cost_list_price_usd = totals["cost_list_price_usd"]
        out.facts["timed_out"] = bool(result.timed_out)
        # The agent had started work: an item (a reply, a command, an edit)
        # or a turn completed before it stopped.
        out.facts["acted"] = parsed["items"] > 0 or bool(parsed["turns"])
        # Codex's sandbox refused the model's own commands: whatever the
        # model then wrote, it never had a fair go (round 3).
        refusals = codex_sandbox_refusals(stderr, prompt)
        if refusals:
            out.facts["sandbox_refused"] = True
            out.extra["refused_commands"] = len(refusals)
            out.notes.append(f"Codex's sandbox refused {len(refusals)} of its commands")
        if result.timed_out:
            out.error = f"stopped at the {timeout_s:g} s time limit"
        elif parsed["error"]:
            out.error = parsed["error"]
        elif result.returncode != 0 or not parsed["turns"]:
            # ONLY Codex's own ERROR lines (the v1.301.0 rule): ``codex exec``
            # echoes the prompt into stderr, and a task's words ("billing",
            # "log in") must never read as an infrastructure failure.
            from ..providers.adapters.subprocess_cli import cli_error_lines

            own = cli_error_lines(stderr, "", prompt).strip()
            out.error = "Codex ended without finishing: " + (own[-500:] or f"exit code {result.returncode}")
        out.ok_run = not out.error
        out.facts["finished"] = out.ok_run
        return out


RUNNERS: dict[str, type] = {"jarvis": JarvisRunner, "claude": ClaudeRunner, "codex": CodexRunner}
