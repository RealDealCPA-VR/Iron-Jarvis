"""Subscription CLI providers — run inference through a LOGGED-IN local CLI.

``claude -p`` (Claude Code, Max plan) and ``codex exec`` (Codex, ChatGPT plan)
both support headless print-mode: prompt in, answer out, billed to the FLAT-
RATE subscription the CLI is already logged into. That makes every installed
AI CLI a routable Iron Jarvis provider with zero API keys and zero reverse-
engineering — the CLI owns auth, model churn, and token refresh.

Honest limits (v1): text-only (no tool-calling through the CLI), slower than
an API call (a fresh process each turn, typically 3–15s). Perfect for chat
turns, one-shot utilities, and rate-limit spillover; agent sessions that need
tools should stay on API adapters.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse

#: Hard wall-clock cap per CLI call — a wedged CLI must never hang a turn.
_TIMEOUT_S = 180


def _flatten(system: str, messages: list[LLMMessage]) -> str:
    """One prompt string from the transcript (CLIs take a single prompt)."""
    parts: list[str] = []
    if system.strip():
        parts.append(f"[System instructions]\n{system.strip()}")
    for m in messages:
        who = "User" if m.role == "user" else ("Assistant" if m.role == "assistant" else m.role)
        if (m.content or "").strip():
            parts.append(f"{who}: {m.content.strip()}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Blocking subprocess run (called via to_thread)."""
    proc = subprocess.run(  # noqa: S603 — argv list, no shell
        argv, capture_output=True, text=True, timeout=_TIMEOUT_S,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class SubprocessCliAdapter(LLMAdapter):
    """A provider backed by a local AI CLI's headless print mode."""

    def __init__(
        self,
        provider: str,
        binary: str,
        argv_builder: Callable[[str, str], list[str]],
        parse: Callable[[str], str],
        *,
        model: str = "subscription",
        runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.provider = provider
        self.model = model
        self._binary = binary
        self._argv_builder = argv_builder
        self._parse = parse
        self._runner = runner or _run
        self._which = which

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
        try:
            code, out, err = await asyncio.to_thread(self._runner, argv)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{self.provider}: CLI timed out after {_TIMEOUT_S}s")
        if code != 0:
            detail = (err or out).strip()[:400]
            raise RuntimeError(f"{self.provider}: CLI exited {code}: {detail}")
        text = self._parse(out).strip()
        if not text:
            raise RuntimeError(f"{self.provider}: CLI returned no output")
        return LLMResponse(text=text, tool_calls=[], usage={})


# --- Claude Code (`claude -p … --output-format json`) -----------------------

def _claude_argv(prompt: str, _model: str) -> list[str]:
    return ["-p", prompt, "--output-format", "json"]


def _claude_parse(stdout: str) -> str:
    """print-mode JSON carries the answer in `result`; tolerate plain text."""
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return str(data.get("result") or data.get("content") or "")
    except Exception:  # noqa: BLE001 — older CLIs print plain text
        pass
    return stdout


def make_claude_cli(**kw: Any) -> SubprocessCliAdapter:
    return SubprocessCliAdapter(
        "claude-cli", "claude", _claude_argv, _claude_parse, **kw
    )


# --- Codex (`codex exec …`) --------------------------------------------------

def _codex_argv(prompt: str, _model: str) -> list[str]:
    # --skip-git-repo-check: we run from no particular directory.
    return ["exec", "--skip-git-repo-check", prompt]


def _codex_parse(stdout: str) -> str:
    """codex exec prints banners/progress then the answer; keep the tail after
    the last blank-line separator, dropping obvious log/banner lines."""
    lines = [
        ln for ln in stdout.splitlines()
        if not ln.startswith(("[", "OpenAI Codex", "--------"))
    ]
    text = "\n".join(lines).strip()
    # The final paragraph is the reply; earlier paragraphs are usually preamble.
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    return blocks[-1] if blocks else text


def make_codex_cli(**kw: Any) -> SubprocessCliAdapter:
    return SubprocessCliAdapter(
        "codex-cli", "codex", _codex_argv, _codex_parse, **kw
    )
