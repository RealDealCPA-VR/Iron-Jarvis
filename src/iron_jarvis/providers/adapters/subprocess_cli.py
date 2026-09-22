"""Subscription CLI providers — run inference through a LOGGED-IN local CLI.

``claude`` (Claude Code, Max plan) and ``codex exec`` (Codex, ChatGPT plan) both
support headless print-mode: prompt in, answer out, billed to the FLAT-RATE
subscription the CLI is already logged into. This is the SANCTIONED way to use a
subscription programmatically — the CLI owns auth, model churn, and token
refresh; Iron Jarvis never sees or stores the credential. There is no in-app
account login: the app simply inherits the login you already performed in the
provider's own CLI.

The ``claude`` adapter is a full **single-step structured completer**: with the
built-in tools disabled (``--tools ""``) and a JSON schema forcing either a text
reply or ONE tool call, ``claude -p`` behaves exactly like the raw Messages-API
adapter's ``complete()`` — it returns either final text or a ``tool_use``, and
Iron Jarvis's own perceive→act loop, tool registry, and permission engine stay
in charge (the app still executes every tool). So Claude-backed agent sessions,
workflows, and armed chat work the same on the inherited login as on the API key
— only slower (a fresh process per step, typically 3–15s) and without inline
vision (needs an API key). The ``codex`` adapter stays text-only.
"""

from __future__ import annotations

import asyncio
import json
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
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ProviderError, ToolCall
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


# --- Claude Code (`claude -p`) — FULL single-step structured completer -------

#: Map an Iron Jarvis model id to a `claude --model` argument. Full ids
#: (`claude-opus-4-8`) and bare aliases (`opus`/`sonnet`/`haiku`/`fable`) both
#: work; unknown/placeholder values (the adapter's default "subscription") pass
#: nothing so the CLI uses its own default. Family prefixes map to the alias so
#: an id the CLI doesn't recognize verbatim still resolves.
def _claude_model_arg(model: str | None) -> str | None:
    m = (model or "").strip().lower()
    if not m or m in ("subscription", "default", "auto"):
        return None
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if m == fam or m.startswith(f"claude-{fam}"):
            return fam
    if m.startswith("claude-"):
        return model  # a full id we don't have an alias for — pass through
    return None


def _tool_catalog(tools: list[dict[str, Any]]) -> str:
    """Render the available tools so the model can pick one to call."""
    lines = ["[Available tools — call ONE by returning tool_call, or answer with reply]"]
    for t in tools:
        name = t.get("name", "")
        desc = (t.get("description") or "").strip()
        schema = t.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        args = ", ".join(
            f"{k}{'*' if k in required else ''}" for k in props
        ) or "(no args)"
        lines.append(f"- {name}({args}): {desc}")
    return "\n".join(lines)


def _flatten_for_claude(
    system: str, messages: list[LLMMessage], tools: list[dict[str, Any]]
) -> str:
    """Build the single-step prompt: system + tool catalog + transcript +
    the structured-output instruction. Assistant tool calls and tool results
    are rendered inline so a multi-step loop (re-flattened each step) sees the
    outcome of prior tool calls and continues correctly."""
    parts: list[str] = []
    if system.strip():
        parts.append(f"[System instructions]\n{system.strip()}")
    if tools:
        parts.append(_tool_catalog(tools))
    for m in messages:
        if m.role == "tool":
            parts.append(
                f"[Tool result — {m.name or 'tool'}]\n{(m.content or '').strip()}"
            )
        elif m.role == "assistant" and m.tool_calls:
            calls = "; ".join(
                f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
                for tc in m.tool_calls
            )
            if (m.content or "").strip():
                parts.append(f"Assistant: {m.content.strip()}")
            parts.append(f"[Assistant called tool(s): {calls}]")
        else:
            who = "User" if m.role == "user" else "Assistant"
            if (m.content or "").strip():
                parts.append(f"{who}: {m.content.strip()}")
    if tools:
        parts.append(
            "Respond with JSON matching the required schema. To answer, set "
            '"reply" to your text and "tool_call" to null. To use a tool, set '
            '"reply" to null and "tool_call" to {"name": <tool name>, '
            '"arguments": <object>} for exactly ONE tool.'
        )
    return "\n\n".join(parts)


class ClaudeCliAdapter(LLMAdapter):
    """Claude via the inherited `claude` CLI, as a single-step completer that
    supports tool calls, per-call model selection, and token accounting."""

    provider = "claude-cli"

    def capabilities(self) -> dict[str, Any]:
        # Claude via the inherited CLI IS a single-step structured completer that
        # emits tool_use, so it can drive the agent loop (tool_use True). Inline
        # vision needs the raw Messages API, so vision stays off — the router
        # prefers an API adapter when images are present.
        return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": False}

    def __init__(
        self,
        *,
        model: str = "subscription",
        runner: Callable[..., tuple[int, str, str]] | None = None,
        which: Callable[[str], str | None] = _which_cli,
    ) -> None:
        self.model = model
        self._runner = runner  # None = the real, cancel-killable `run_cli`
        self._which = which

    def _argv(
        self, exe: str, tools: list[dict[str, Any]], reasoning: str = ""
    ) -> list[str]:
        # NO positional prompt: `claude -p` reads it from STDIN. As a command-
        # line arg, a big office prompt (extracted PDFs, project knowledge)
        # blew Windows' 32,767-char CreateProcess limit — live-hit 2026-07-20:
        # "claude-cli: CLI exited 1: The command line is too long."
        argv = [
            exe, "-p", "--output-format", "json",
            "--no-session-persistence",
            "--setting-sources", "",   # ignore user/project/local settings
            "--strict-mcp-config",     # no ambient MCP servers
            "--tools", "",             # disable the CLI's own tool set — WE run tools
        ]
        marg = _claude_model_arg(self.model)
        if marg:
            argv += ["--model", marg]
        if reasoning:
            # v1.263.0: the CLI's own effort flag ("Effort level for the current
            # session") — the same low/medium/high vocabulary the composer offers.
            argv += ["--effort", reasoning]
        if tools:
            argv += ["--json-schema", json.dumps(_STEP_SCHEMA)]
        return argv

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted-ignored (see
        # SubprocessCliAdapter.complete — subscription CLI backend).
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        exe = self._which("claude")
        if not exe:
            raise RuntimeError(
                "claude-cli: the 'claude' CLI is not installed/on PATH"
            )
        # Inline vision needs the raw Messages API (base64 image blocks); the
        # headless CLI path can't carry them. Fail honestly rather than silently
        # drop the image and answer about nothing.
        if any(getattr(m, "images", None) for m in messages):
            raise RuntimeError(
                "claude-cli: image input isn't supported over the inherited CLI — "
                "connect an Anthropic API key for vision."
            )
        prompt = _flatten_for_claude(system, messages, tools)
        argv = self._argv(exe, tools, reasoning)
        try:
            code, out, err = await _call(self._runner, argv, prompt)
        except subprocess.TimeoutExpired as exc:
            # Transient (typed): a wedged CLI should fail over, not hard-error.
            raise ProviderError(
                f"claude-cli: CLI timed out after {_TIMEOUT_S}s", transient=True
            ) from exc
        if code != 0:
            # v1.234.0 (live report): newer Claude Code builds exit 1 when
            # signed out AND print the result JSON to stdout. This branch used
            # to fire before _parse ever saw the JSON, so the user got 400
            # characters of it instead of "Not logged in". The message helper
            # reads the JSON FIRST and maps a sign-in refusal to the remedy;
            # the shared auth probe is told so availability turns honest now.
            msg = cli_failure_message(self.provider, "claude", code, out, err)
            note_cli_failure("claude", msg)
            raise RuntimeError(msg)
        return self._parse(out, bool(tools))

    @staticmethod
    def _parse(stdout: str, had_tools: bool) -> LLMResponse:
        try:
            data = json.loads(stdout)
        except Exception:  # noqa: BLE001 — non-JSON: treat the raw text as the answer
            text = stdout.strip()
            if not text:
                raise RuntimeError("claude-cli: CLI returned no output")
            return LLMResponse(text=text, tool_calls=[], usage={})
        if not isinstance(data, dict):
            return LLMResponse(text=str(data), tool_calls=[], usage={})
        # A failed run (not logged in, api error, refusal) must RAISE so the
        # router treats it as a provider failure and fails over — never return
        # the error string as if it were the model's answer.
        if data.get("is_error"):
            msg = cli_failure_message("claude-cli", "claude", 0, stdout, "")
            note_cli_failure("claude", msg)
            raise RuntimeError(msg)
        usage_src = data.get("usage") or {}
        usage = {
            "input_tokens": int(usage_src.get("input_tokens", 0) or 0),
            "output_tokens": int(usage_src.get("output_tokens", 0) or 0),
        }
        struct = data.get("structured_output")
        if had_tools and isinstance(struct, dict):
            tc = struct.get("tool_call")
            if isinstance(tc, dict) and tc.get("name"):
                call = ToolCall(
                    id="cli_0",
                    name=str(tc.get("name")),
                    arguments=dict(tc.get("arguments") or {}),
                )
                return LLMResponse(
                    text="", tool_calls=[call], finish_reason="tool_use", usage=usage
                )
            reply = struct.get("reply")
            return LLMResponse(text=str(reply or ""), tool_calls=[], usage=usage)
        # No schema (tool-less step) or malformed structured output: the plain
        # `result` string is the answer.
        text = str(data.get("result") or "").strip()
        if not text and had_tools and isinstance(struct, dict):
            text = str(struct.get("reply") or "")
        if not text:
            raise RuntimeError("claude-cli: CLI returned no usable output")
        return LLMResponse(text=text, tool_calls=[], usage=usage)


def make_claude_cli(**kw: Any) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(**kw)
