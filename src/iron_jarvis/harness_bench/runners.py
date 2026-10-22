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
"""

from __future__ import annotations

import asyncio
import contextlib
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
from .catalog import Task
from .ledger import CallEntry, Rollout

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
#: under it until :func:`procs.end_session` is called for it.
LANE_SESSION_ID = "chat"

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
#: How much of a final reply a record keeps.
REPLY_CHARS = 2000


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
                manager: Any) -> CallEntry:
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
    )


@contextlib.contextmanager
def counting_router(platform: Any, rollout: Rollout, observed: dict[str, Any]):
    """Enter every model call the platform's router makes into ``rollout``
    while the block runs — ``stream()`` and ``complete()``, whatever the
    router object currently has there (a test's scripted model included).
    A call that never reached its final answer is entered as incomplete with
    nothing reported. Restored however the block ends."""
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

    async def _counted(frames: AsyncIterator[dict[str, Any]], kwargs: dict[str, Any]):
        _observe(kwargs)
        entry: CallEntry | None = None
        try:
            async with contextlib.aclosing(frames) as inner:
                async for frame in inner:
                    if isinstance(frame, dict) and frame.get("type") == "final":
                        resp = frame.get("response")
                        entry = _entry_from(
                            str(frame.get("provider") or ""), str(frame.get("model") or ""),
                            getattr(resp, "usage", None),
                            with_tools=bool(kwargs.get("tools")), source="router.stream",
                            manager=manager,
                        )
                    yield frame
        finally:
            rollout.add(entry or CallEntry(
                complete=False, with_tools=bool(kwargs.get("tools")), source="router.stream",
            ))

    def stream(*args: Any, **kwargs: Any):
        return _counted(real_stream(*args, **kwargs), kwargs)

    async def complete(*args: Any, **kwargs: Any):
        _observe(kwargs)
        entry: CallEntry | None = None
        try:
            result = await real_complete(*args, **kwargs)
            entry = _entry_from(
                str(getattr(result, "provider", "") or ""), str(getattr(result, "model", "") or ""),
                getattr(getattr(result, "response", None), "usage", None),
                with_tools=bool(kwargs.get("tools")), source="router.complete", manager=manager,
            )
            return result
        finally:
            rollout.add(entry or CallEntry(
                complete=False, with_tools=bool(kwargs.get("tools")), source="router.complete",
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


class JarvisRunner:
    name = "jarvis"

    def __init__(self, platform: Any, *, provider: str = "", model: str = "",
                 round_budget: int = DEFAULT_ROUNDS, dialect: str = DEFAULT_DIALECT) -> None:
        from ..daemon.chat_turn import turn_harness, turn_round_budget

        self.platform = platform
        self.provider = provider or ""
        self.model = model or ""
        self.round_budget = turn_round_budget(int(round_budget))
        # "auto" or a dialect id; anything else is refused before any run.
        self.dialect = turn_harness(dialect or DEFAULT_DIALECT) or DEFAULT_DIALECT

    def harness(self) -> Any:
        """The harness turn this runner's turns will build (no folder: a
        task's copy carries no harness layer), for the identity."""
        from ..daemon.chat_turn import _harness_model
        from ..harness.mode import build

        provider, model = _harness_model(self.platform, self.provider, self.model)
        return build(
            registry=self.platform.registry, permissions=self.platform.permissions,
            requested=self.dialect, provider=provider, model=model, stream=True,
            approval_mode=JARVIS_APPROVAL_MODE, today="",
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
        from ..harness import dialects as harness_dialects
        from ..harness import mode as harness_mode
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
        }

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
        )
        deadline = time.monotonic() + float(timeout_s)
        approvals = _approvals(SimpleNamespace(platform=self.platform))
        observed: dict[str, Any] = {}
        rounds = tools = 0
        done: dict[str, Any] | None = None
        error = ""
        error_fix_kind = ""
        timed_out = False
        with counting_router(self.platform, rollout, observed):
            try:
                gen = await stream_chat_turn(
                    self.platform, BUILTIN_PERSONAS, body,
                    should_stop=lambda: time.monotonic() > deadline,
                    round_budget=self.round_budget, stay_in_chat=True,
                    harness=self.dialect,
                )
                async with asyncio.timeout(float(timeout_s) + _GRACE_S):
                    async with contextlib.aclosing(gen) as frames:
                        async for chunk in frames:
                            for event, data in parse_sse(chunk):
                                if event == "round":
                                    rounds += 1
                                elif event == "tool_call" and data.get("status") == "finished":
                                    tools += 1
                                elif event == "approval":
                                    # Belt and braces: yolo grants every card
                                    # these tools raise; one that still shows
                                    # is answered for this call only.
                                    if approvals.resolve(str(data.get("id") or ""), "once"):
                                        out.notes.append(f"answered an approval for {data.get('tool')}")
                                elif event == "error":
                                    error = str(data.get("detail") or data.get("title") or "the turn failed")
                                    error_fix_kind = str(data.get("fix_kind") or "")
                                elif event == "done":
                                    done = data
            except TimeoutError:
                timed_out = True
            except Exception as exc:  # noqa: BLE001 — recorded, never raised past the run
                error = f"{type(exc).__name__}: {exc}"
        # What the turn's shell / run_code left running (a server the agent
        # started and never stopped) belongs to the lane's session until that
        # session ends (`procs.owned_by` / `procs.end_session`) — and in this
        # process nothing else ever ends it, so it would outlive the benchmark
        # and keep its folder open. Stop it here, off the loop.
        stopped = await asyncio.to_thread(procs.end_session, LANE_SESSION_ID)
        if stopped:
            out.notes.append(f"stopped {len(stopped)} program(s) the turn left running")
        if time.monotonic() > deadline and done is None:
            timed_out = True
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
            return out
        usage = done.get("usage") or {}
        route = done.get("route") or {}
        out.answered_provider = str(done.get("provider") or "")
        out.answered_model = str(done.get("model") or "")
        out.final_reply = str(done.get("reply") or "")[:REPLY_CHARS]
        out.tokens_in = int(usage.get("input_tokens", 0) or 0)
        out.tokens_out = int(usage.get("output_tokens", 0) or 0)
        cached = usage.get("cache_read_input_tokens")
        out.tokens_cached = int(cached) if isinstance(cached, (int, float)) else None
        out.cost_metered_usd, out.cost_list_price_usd = lane_cost(usage, route)
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
        })
        out.ok_run = True
        out.facts["finished"] = True
        # The agent used every round it was given (the turn then ended in
        # chat with one tool-less answer).
        out.facts["cut_off"] = rounds >= self.round_budget
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
    for obj in _json_lines(stdout):
        kind = obj.get("type")
        if kind == "assistant":
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
    parsed: dict[str, Any] = {"tool_calls": len(tool_ids), "answered": answered, "result": result}
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
    for obj in _json_lines(stdout):
        kind = obj.get("type")
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
    return {"tool_calls": tools, "items": items, "turns": entries, "error": error, "reply": reply}


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

    def __init__(self, *, model: str = "", binary: str | None = None) -> None:
        self.provider = self.name
        self.model = model or ""
        self.binary = binary or find_cli(self.binary_name)
        self._version: str | None = None

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
        env = self.child_env()
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

    def __init__(self, *, model: str = "", binary: str | None = None) -> None:
        super().__init__(model=model, binary=binary)
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
