"""Subscription CLI providers — run inference through a LOGGED-IN local CLI.

``claude`` (Claude Code, Max plan) and ``codex exec`` (Codex, ChatGPT plan) both
support headless print-mode: prompt in, answer out, billed to the FLAT-RATE
subscription the CLI is already logged into. This is the SANCTIONED way to use a
subscription programmatically — the CLI owns auth, model churn, and token
refresh; Iron Jarvis never sees or stores the credential. There is no in-app
account login: the app simply inherits the login you already performed in the
provider's own CLI.

The ``claude`` adapter (v1.300.0) speaks the CLI's NATIVE stream-json
protocol (``claude_native/``): the conversation is replayed turn by turn, tools
are real ``tool_use`` blocks offered through an inert MCP inventory (the app
still executes every tool — Iron Jarvis's own perceive→act loop, tool registry
and permission engine stay in charge), text streams as it is written, images
ride the user turn, and usage carries the cache buckets and the CLI's own
list-price cost. One fresh process per step, cancellable as a tree. The
``codex`` adapter stays text-only.
"""

from __future__ import annotations

import asyncio
import os
import shutil


def _which_cli(binary: str) -> str | None:
    """Resolve a CLI binary like the AVAILABILITY probe does (v1.124.0).

    ``available()`` uses ``terminals.ai_clis._find`` (PATH + the per-user bin
    dirs a GUI-launched daemon's PATH misses: npm shims, pipx, cargo, ...). The
    adapters used bare ``shutil.which`` — so in the PACKAGED app a provider
    could report available and then error "not installed/on PATH" on the very
    first request, forcing a pointless failover. One resolver, one truth.
    """
    try:
        from ...terminals.ai_clis import _find

        return _find(binary)
    except Exception:  # noqa: BLE001 — degraded envs keep the plain probe
        return shutil.which(binary)
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ProviderError
from ..budget import run_budget
from ..cli_auth import cli_failure_message, note_cli_failure

#: Hard wall-clock cap per CLI call — a wedged CLI must never hang a turn. A
#: tool-using step can legitimately take 10–20s, so this is generous.
_TIMEOUT_S = 240

#: Structured-output schema for one agent step: the model returns EITHER a
#: final `reply` (text) OR one `tool_call` — never both. This is what turns the
#: loop-owning CLI into a single-step primitive the app's own loop can drive.
_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {
            "type": ["string", "null"],
            "description": "Your final answer text. null if you are calling a tool.",
        },
        "tool_call": {
            "type": ["object", "null"],
            "description": "The single tool to call, or null if you are answering.",
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["name", "arguments"],
            "additionalProperties": True,
        },
    },
    "required": ["reply", "tool_call"],
    "additionalProperties": False,
}


def _spawn(argv: list[str]) -> "subprocess.Popen[str]":
    """Start the CLI with its own pipes — the caller owns the handle.

    POSIX: its own session, so ``_kill_tree``'s ``killpg`` reaches the CLI's
    helpers and nothing else. Windows: ``taskkill /T`` walks the PID tree.
    """
    popen_kw: dict[str, Any] = {}
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    return subprocess.Popen(  # noqa: S603 — argv list, no shell
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", **popen_kw,
    )


def _kill(proc: "subprocess.Popen[str]") -> None:
    """Kill the CLI AND its descendants — the ONE tree-kill (sandbox/native)."""
    from ...sandbox.native import _kill_tree

    _kill_tree(proc)


def _drain(
    proc: "subprocess.Popen[str]", stdin: str | None, timeout: float
) -> tuple[int, str, str]:
    """Blocking: feed stdin, wait at most ``timeout``, return the result.

    v1.287.0 (chat-01): ``subprocess.run(timeout=)`` was not a bound. On a
    timeout it killed only the direct child, then drained the pipes with NO
    timeout — an npm ``.cmd`` shim's node, or any helper the CLI started,
    held stdout open and the "240 s cap" waited for it (a 1 s cap measured
    8 s). Here the whole tree dies and the drain itself is bounded, then the
    TimeoutExpired propagates exactly as before (→ transient ProviderError).
    """
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001 — a wedged drain must still return
            pass
        raise
    return proc.returncode, out or "", err or ""


def _run(
    argv: list[str], stdin: str | None = None, timeout: float | None = None
) -> tuple[int, str, str]:
    """Blocking subprocess run with a REAL wall-clock bound (see `_drain`)."""
    return _drain(_spawn(argv), stdin, _TIMEOUT_S if timeout is None else timeout)


async def run_cli(
    argv: list[str], stdin: str | None, *, timeout: float
) -> tuple[int, str, str]:
    """Run a CLI off the loop — and KILL it when the awaiting turn is cancelled.

    v1.287.0 (chat-01): the adapters awaited ``to_thread(subprocess.run)``.
    Stop / a closed tab / Retry cancelled only the AWAIT; the thread and the
    CLI ran on for up to the full cap, spending the user's plan and a pool
    thread (the v1.228.0 trap: a cancelled await does not cancel the thread).
    The spawn happens in the worker thread (nothing blocks the loop); the
    handle is published under a lock, so a cancel that lands before the spawn
    finishes is honoured by the thread itself. The CancelledError is always
    re-raised — the ledger's CANCELLED row depends on it.
    """
    lock = threading.Lock()
    box: dict[str, Any] = {"proc": None, "cancelled": False}

    def _work() -> tuple[int, str, str]:
        proc = _spawn(argv)
        with lock:
            box["proc"] = proc
            cancelled = box["cancelled"]
        if cancelled:
            _kill(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001 — reaping is best-effort
                pass
            return -1, "", ""
        return _drain(proc, stdin, timeout)

    try:
        return await asyncio.to_thread(_work)
    except asyncio.CancelledError:
        with lock:
            box["cancelled"] = True
            proc = box["proc"]
        if proc is not None:
            # Off the loop (taskkill is a process spawn), and shielded so a
            # second cancel cannot abandon the kill half-way.
            try:
                await asyncio.shield(asyncio.to_thread(_kill, proc))
            except asyncio.CancelledError:
                pass
        raise


async def _call(
    runner: Callable[..., tuple[int, str, str]] | None, argv: list[str], stdin: str
) -> tuple[int, str, str]:
    """The adapters' one door to the CLI: the injected runner, else `run_cli`
    with the cap read at CALL time (tests shrink ``_TIMEOUT_S``)."""
    if runner is not None:
        return await asyncio.to_thread(runner, argv, stdin)
    return await run_cli(argv, stdin, timeout=_TIMEOUT_S)


# --- Codex (`codex exec …`) — TEXT-ONLY -------------------------------------

def _flatten(system: str, messages: list[LLMMessage]) -> str:
    """One prompt string from the transcript (text-only CLIs take one prompt)."""
    parts: list[str] = []
    if system.strip():
        parts.append(f"[System instructions]\n{system.strip()}")
    for m in messages:
        who = "User" if m.role == "user" else ("Assistant" if m.role == "assistant" else m.role)
        if (m.content or "").strip():
            parts.append(f"{who}: {m.content.strip()}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


class SubprocessCliAdapter(LLMAdapter):
    """A TEXT-ONLY provider backed by a local AI CLI's headless print mode."""

    def capabilities(self) -> dict[str, Any]:
        # CRITICAL for routing: this adapter (codex-cli) CANNOT call tools — it
        # returns final text only. The router MUST exclude it from any request
        # that carries tools, otherwise the agent loop silently stalls on an
        # empty tool_calls=[]. No vision either.
        return {"provider": self.provider, "model": self.model, "tool_use": False, "vision": False}

    def __init__(
        self,
        provider: str,
        binary: str,
        argv_builder: Callable[[str, str], list[str]],
        parse: Callable[[str], str],
        *,
        model: str = "subscription",
        runner: Callable[..., tuple[int, str, str]] | None = None,
        which: Callable[[str], str | None] = _which_cli,
        output_last_message_flag: str | None = None,
        reasoning_argv: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._binary = binary
        self._argv_builder = argv_builder
        self._parse = parse
        #: An injected 2-arg ``runner(argv, stdin)`` (test doubles) keeps the
        #: plain to_thread path; None = the real, cancel-killable `run_cli`.
        self._runner = runner
        self._which = which
        #: v1.263.0: how THIS CLI spells a reasoning level on its command line
        #: (codex: `-c model_reasoning_effort=<level>`), or None when it has no
        #: such flag — the level is then accepted and ignored, never guessed.
        self._reasoning_argv = reasoning_argv
        #: When set (e.g. codex's --output-last-message), the CLI writes its
        #: FINAL message to a temp file we read back — the DETERMINISTIC reply
        #: channel. Parsing stdout with heuristics is only the fallback: a CLI
        #: build whose stdout ends with a footer/next-steps block made the old
        #: last-block parse return THAT instead of the answer (live-hit
        #: 2026-07-20: a web question came back as a greeting).
        self._out_flag = output_last_message_flag

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted so callers can pass them
        # uniformly across adapters; IGNORED — subscription CLI backend, not
        # part of the openai-compat guided-decoding family this wave.
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        exe = self._which(self._binary)
        if not exe:
            raise RuntimeError(
                f"{self.provider}: the '{self._binary}' CLI is not installed/on PATH"
            )
        prompt = _flatten(system, messages)
        argv = [exe] + self._argv_builder(prompt, self.model)
        if reasoning and self._reasoning_argv is not None:
            # Flags precede the positional prompt marker (the builder's last arg).
            argv = argv[:-1] + list(self._reasoning_argv(reasoning)) + argv[-1:]
        out_path: str | None = None
        if self._out_flag:
            fd, out_path = tempfile.mkstemp(prefix="ij-cli-reply-", suffix=".txt")
            os.close(fd)
            # Flags must precede the positional prompt (the builder's last arg).
            argv = argv[:-1] + [self._out_flag, out_path, argv[-1]]
        try:
            try:
                # The prompt rides STDIN (never argv): Windows caps a command
                # line at 32,767 chars, and an extracted-PDF prompt exceeds it.
                code, out, err = await _call(self._runner, argv, prompt)
            except subprocess.TimeoutExpired as exc:
                # A wedged CLI is a TRANSIENT failure (typed) — the router should
                # fail over to another provider, not surface it as a hard error.
                raise ProviderError(
                    f"{self.provider}: CLI timed out after {_TIMEOUT_S}s", transient=True
                ) from exc
            if code != 0:
                # v1.234.0: the CLI's own words, mapped to the remedy when it
                # is a sign-in refusal — and the shared probe hears about it.
                msg = cli_failure_message(self.provider, self._binary, code, out, err)
                note_cli_failure(self._binary, msg)
                raise RuntimeError(msg)
            text = ""
            if out_path is not None:
                try:
                    text = Path(out_path).read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                except OSError:
                    text = ""
            if not text:
                text = self._parse(out).strip()
        finally:
            if out_path is not None:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
        if not text:
            raise RuntimeError(f"{self.provider}: CLI returned no output")
        return LLMResponse(text=text, tool_calls=[], usage={})


def _codex_argv(_prompt: str, _model: str) -> list[str]:
    # --skip-git-repo-check: we run from no particular directory. "-" = read
    # the prompt from STDIN — a big office prompt (extracted PDFs, knowledge)
    # blew Windows' 32,767-char command line as a positional arg (live-hit
    # 2026-07-20: "The command line is too long"). Stdin has no such limit.
    return ["exec", "--skip-git-repo-check", "-"]


def _codex_parse(stdout: str) -> str:
    """FALLBACK ONLY (--output-last-message is the real channel): strip
    banner/log lines and keep EVERYTHING that remains. This used to keep only
    the LAST blank-line block — a codex build whose stdout ends with a
    footer/next-steps block then returned THAT instead of the answer sitting
    right above it (live-hit 2026-07-20: 'What would you like help with?')."""
    lines = [
        ln for ln in stdout.splitlines()
        if not ln.startswith(("[", "OpenAI Codex", "--------"))
    ]
    return "\n".join(lines).strip()


def _codex_reasoning_argv(level: str) -> list[str]:
    """Codex's spelling of the reasoning level (v1.263.0): a config override
    on the command line — the same key `~/.codex/config.toml` takes."""
    return ["-c", f"model_reasoning_effort={level}"]


def make_codex_cli(**kw: Any) -> SubprocessCliAdapter:
    kw.setdefault("output_last_message_flag", "--output-last-message")
    kw.setdefault("reasoning_argv", _codex_reasoning_argv)
    return SubprocessCliAdapter(
        "codex-cli", "codex", _codex_argv, _codex_parse, **kw
    )


# --- Claude Code (`claude -p`) — the NATIVE stream-json client (v1.300.0) ----
#
# Rebuilt on the design of NousResearch's MIT-licensed claude-subscription-
# directsdk plugin (commit ef73726). The old adapter flattened the whole
# conversation into ONE prompt, emulated tools with a `--json-schema` "reply or
# ONE tool_call" step and a text catalog, never streamed, refused images, sent
# `--model` as a bare alias (dropping `[1m]`) and reported only input/output
# tokens. Now (see `claude_native/`): history is REPLAYED frame by frame,
# tools are NATIVE (an inert MCP inventory + the full schemas through
# CLAUDE_CODE_EXTRA_BODY — parallel calls in one step), text STREAMS, images
# ride the user frame, the model id keeps its `[1m]`, signed thinking is
# carried for verbatim replay, usage carries the cache buckets and the CLI's
# own list-price cost, and an admission relay holds every call to ONE upstream
# request. What stayed: the cancellable tree-kill discipline (v1.287.0), the
# sign-in mapping (v1.234.0), `--max-budget-usd` (v1.295.0) and `--effort`.

#: The smallest ``--max-budget-usd`` the Claude CLI accepts (it refuses
#: lower values); a remaining allowance under this arms no flag.
MIN_BUDGET_USD = 0.05

#: v1.300.0: the OUTER bound on one native claude call. The CLI streams, so
#: the real watchdog is the transport's IDLE timeout (no output for 180 s —
#: reset by every event, as the reference plugin does); this cap exists only so
#: a call that keeps trickling can never run forever. The old 240 s whole-call
#: cap (``_TIMEOUT_S``, still the Codex cap) cut off a long answer that was
#: still streaming. Read at CALL time: tests shrink it.
_CLAUDE_TOTAL_TIMEOUT_S = 3600.0

#: The model ids that mean "the CLI's own default" (used only when the
#: ``claude_models`` catalog is absent; it otherwise decides, and may name the
#: live default row so the relay does not cap it at 200K).
_PLACEHOLDER_MODELS = ("", "subscription", "default", "auto")

#: The effort levels the CLI's ``--effort`` takes (our vocabulary is
#: low/medium/high; anything else is accepted and ignored, never guessed).
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _claude_model_arg(model: str | None) -> str | None:
    """The ``--model`` argument for an Iron Jarvis model id, or None (send no
    ``--model``: the CLI's own default).

    The mapping lives in ``providers/claude_models.native_model`` (the live
    picker + pinned catalog: which ids have a 1M route, which alias means
    what). It is imported lazily; ONLY when the module or the function is
    absent does a full id pass through unchanged (and a placeholder id send
    nothing). NEVER a bare family alias for a full id — collapsing
    ``claude-sonnet-5`` to ``sonnet`` lost its 1M route. A refusal from the
    catalog (``haiku[1m]``: a 200K model) is the user's error to see, so it is
    raised in its own words, never swallowed into a pass-through.
    """
    m = (model or "").strip()
    try:
        from ..claude_models import native_model
    except ImportError:  # the catalog module is not shipped: pass through
        return None if m.lower() in _PLACEHOLDER_MODELS else m
    try:
        mapped = native_model(m)
    except ValueError as exc:
        raise RuntimeError(f"claude-cli: {exc}") from exc
    return mapped.strip() if isinstance(mapped, str) and mapped.strip() else None


class ClaudeCliAdapter(LLMAdapter):
    """Claude via the inherited `claude` CLI, spoken natively over stream-json:
    replayed history, native tool calls, streaming, images, token + cost
    accounting. Iron Jarvis still executes every tool."""

    provider = "claude-cli"

    def capabilities(self) -> dict[str, Any]:
        # Native tool_use (many per step) AND inline images on the user frame.
        return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": True}

    def __init__(
        self,
        *,
        model: str = "subscription",
        runner: Callable[..., tuple[int, str, str]] | None = None,
        which: Callable[[str], str | None] = _which_cli,
        idle_timeout_s: float | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        #: An injected ``runner(argv, stdin) -> (code, stdout, stderr)`` is a
        #: TEST DOUBLE: every frame is handed over at once and its stdout is
        #: replayed through the same acknowledgment/assembly code. None = the
        #: real process (the only production path).
        self._runner = runner
        self._which = which
        self._idle_timeout_s = idle_timeout_s
        #: Tests only: an explicit child environment, which (their rule) skips
        #: the inherited-environment conflict guard. Production passes None.
        self._env = env

    def _native_call(
        self, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]], reasoning: str
    ):
        from .claude_native import frames as native_frames
        from .claude_native.transport import DEFAULT_IDLE_TIMEOUT_S, NativeCall

        exe = self._which("claude")
        if not exe:
            raise RuntimeError("claude-cli: the 'claude' CLI is not installed/on PATH")
        manifest, native_tools, names = native_frames.build_tools(tools or [])
        prompt, frames = native_frames.history_frames(system, messages, names)
        effort = reasoning if reasoning in _EFFORTS else ""
        # v1.295.0 (the job card): what is LEFT of a custom agent's monthly
        # dollar allowance caps ONE invocation — only when armed and at or
        # above the CLI's floor; 0 means "no ceiling", never "spend nothing".
        budget = run_budget()
        return NativeCall(
            exe=exe,
            model_arg=_claude_model_arg(self.model),
            system=prompt,
            frames=frames,
            manifest=manifest,
            body=native_frames.request_body(native_tools, effort=effort),
            names=names,
            effort=effort,
            budget_usd=budget if budget >= MIN_BUDGET_USD else 0.0,
            idle_timeout_s=self._idle_timeout_s or DEFAULT_IDLE_TIMEOUT_S,
            # Read at CALL time: tests shrink the module cap.
            total_timeout_s=_CLAUDE_TOTAL_TIMEOUT_S,
            base_env=self._env,
        )

    async def _frames(
        self, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]], reasoning: str
    ) -> AsyncIterator[dict[str, Any]]:
        from .claude_native import transport

        # Off the loop: frames copy every image's base64, and the model
        # catalog may read its disk cache on first use.
        call = await asyncio.to_thread(self._native_call, system, messages, tools, reasoning)
        agen = transport.run(call, runner=self._runner)
        try:
            async for frame in agen:
                yield frame
        finally:
            # Walking away from the stream (Stop, a closed tab, an error in the
            # consumer) must kill the process — aclose() runs the teardown.
            await agen.aclose()

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted-ignored (subscription CLI).
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        final: LLMResponse | None = None
        agen = self._frames(system, messages, tools, reasoning)
        try:
            async for frame in agen:
                if frame.get("type") == "final":
                    final = frame["response"]
        finally:
            await agen.aclose()
        if final is None:  # pragma: no cover — the transport always ends in final or raises
            raise RuntimeError("claude-cli: the CLI ended without an answer")
        return final

    async def stream(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """Text deltas LIVE as the CLI streams them, then ``final`` — the same
        LLMResponse :meth:`complete` returns for the same run."""
        agen = self._frames(system, messages, tools, reasoning)
        try:
            async for frame in agen:
                yield frame
        finally:
            await agen.aclose()


def make_claude_cli(**kw: Any) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(**kw)
