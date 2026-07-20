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
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ProviderError, ToolCall

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


def _run(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """Blocking subprocess run (called via to_thread)."""
    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        argv, capture_output=True, text=True, timeout=_TIMEOUT_S,
        input=stdin, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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
        which: Callable[[str], str | None] = shutil.which,
        output_last_message_flag: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._binary = binary
        self._argv_builder = argv_builder
        self._parse = parse
        self._runner = runner or _run
        self._which = which
        #: When set (e.g. codex's --output-last-message), the CLI writes its
        #: FINAL message to a temp file we read back — the DETERMINISTIC reply
        #: channel. Parsing stdout with heuristics is only the fallback: a CLI
        #: build whose stdout ends with a footer/next-steps block made the old
        #: last-block parse return THAT instead of the answer (live-hit
        #: 2026-07-20: a web question came back as a greeting).
        self._out_flag = output_last_message_flag

    async def complete(
        self, *, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        exe = self._which(self._binary)
        if not exe:
            raise RuntimeError(
                f"{self.provider}: the '{self._binary}' CLI is not installed/on PATH"
            )
        prompt = _flatten(system, messages)
        argv = [exe] + self._argv_builder(prompt, self.model)
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
                code, out, err = await asyncio.to_thread(self._runner, argv, prompt)
            except subprocess.TimeoutExpired as exc:
                # A wedged CLI is a TRANSIENT failure (typed) — the router should
                # fail over to another provider, not surface it as a hard error.
                raise ProviderError(
                    f"{self.provider}: CLI timed out after {_TIMEOUT_S}s", transient=True
                ) from exc
            if code != 0:
                detail = (err or out).strip()[:400]
                raise RuntimeError(f"{self.provider}: CLI exited {code}: {detail}")
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


def make_codex_cli(**kw: Any) -> SubprocessCliAdapter:
    kw.setdefault("output_last_message_flag", "--output-last-message")
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
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.model = model
        self._runner = runner or _run
        self._which = which

    def _argv(self, exe: str, tools: list[dict[str, Any]]) -> list[str]:
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
        if tools:
            argv += ["--json-schema", json.dumps(_STEP_SCHEMA)]
        return argv

    async def complete(
        self, *, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]]
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
        argv = self._argv(exe, tools)
        try:
            code, out, err = await asyncio.to_thread(self._runner, argv, prompt)
        except subprocess.TimeoutExpired as exc:
            # Transient (typed): a wedged CLI should fail over, not hard-error.
            raise ProviderError(
                f"claude-cli: CLI timed out after {_TIMEOUT_S}s", transient=True
            ) from exc
        if code != 0:
            detail = (err or out).strip()[:400]
            raise RuntimeError(f"claude-cli: CLI exited {code}: {detail}")
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
            raise RuntimeError(
                f"claude-cli: {str(data.get('result') or 'CLI error').strip()[:300]}"
            )
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
