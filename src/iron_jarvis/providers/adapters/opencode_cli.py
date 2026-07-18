"""OpenCode CLI as a provider — LOCAL MODELS ONLY.

Drives ``opencode run --format json`` headlessly and parses its JSONL event
stream back into an :class:`LLMResponse`.

The hard rule this adapter exists to enforce: **it refuses any model that is
not on the allowed-local list**, before spawning anything. OpenCode can reach
paid remote models (its hosted tier, or a passthrough alias on your own proxy),
and the user asked for local only — so an unrecognised model is an honest error,
never a "well, it probably works" call that quietly bills them. See
``providers/opencode.py`` for how locality is decided.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ProviderError

log = logging.getLogger(__name__)

#: OpenCode boots a server and may run a tool loop; give it room but never hang.
_TIMEOUT_S = 300.0


def _run(argv: list[str], timeout: float = _TIMEOUT_S) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        argv, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _flatten(system: str, messages: list[LLMMessage]) -> str:
    """One prompt string — ``opencode run`` takes a single message."""
    parts: list[str] = []
    if system.strip():
        parts.append(system.strip())
    for m in messages:
        role = (getattr(m, "role", "") or "user").upper()
        content = (getattr(m, "content", "") or "").strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


def parse_events(stdout: str) -> tuple[str, dict[str, int]]:
    """``(text, usage)`` from OpenCode's JSONL event stream.

    Text arrives as ``{"type":"text", "part":{"type":"text","text":...}}``
    events (several per reply); ``step_finish`` carries the token counts. A
    line that isn't JSON is skipped rather than poisoning the reply.
    """
    chunks: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        part = evt.get("part") or {}
        if evt.get("type") == "text" and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
        elif evt.get("type") == "step_finish":
            tokens = part.get("tokens") or {}
            try:
                usage["input_tokens"] += int(tokens.get("input") or 0)
                usage["output_tokens"] += int(tokens.get("output") or 0)
            except (TypeError, ValueError):
                pass
    return "".join(chunks).strip(), usage


class OpencodeCliAdapter(LLMAdapter):
    """Text-only provider backed by the local ``opencode`` CLI."""

    provider = "opencode-cli"

    def capabilities(self) -> dict[str, Any]:
        # OpenCode runs its OWN tool loop internally and hands back final text;
        # it never returns structured tool_calls to us. Declaring tool_use here
        # would let the router send it agent work that then stalls on an empty
        # tool-call list — the same trap the fleet adapter guards against.
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    def __init__(
        self,
        model: str = "",
        *,
        allowed: Callable[[], list[str]] | None = None,
        runner: Callable[..., tuple[int, str, str]] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.model = model or ""
        self._allowed = allowed or (lambda: [])
        self._runner = runner or _run
        self._which = which

    def _resolve_model(self) -> str:
        """The model to run, or raise an honest refusal.

        With no model requested we fall back to the FIRST allowed local model
        rather than OpenCode's configured default — that default may well be a
        paid one (the user's is ``spark/fleet``, but it could equally be
        ``spark/frontier``), and silently honouring it would break the promise
        this provider is built on.
        """
        allowed = list(self._allowed())
        if not allowed:
            raise RuntimeError(
                "opencode-cli: no LOCAL models are available. Point an OpenCode "
                "provider at a server on your own network (or set "
                "opencode_local_models) — remote/hosted OpenCode models are "
                "deliberately not offered here."
            )
        if not self.model:
            return allowed[0]
        if self.model not in allowed:
            raise RuntimeError(
                f"opencode-cli: refusing {self.model!r} — it is not one of your "
                f"local models ({', '.join(allowed)}). This provider is "
                "restricted to models served by your own hardware."
            )
        return self.model

    async def complete(
        self, *, system: str, messages: list[LLMMessage], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        from ..opencode import binary

        model = self._resolve_model()  # refuses BEFORE anything is spawned
        exe = binary(self._which)
        if not exe:
            raise RuntimeError("opencode-cli: the 'opencode' CLI is not installed/on PATH")
        prompt = _flatten(system, messages)
        if not prompt.strip():
            raise RuntimeError("opencode-cli: nothing to send")
        argv = [exe, "run", "--format", "json", "-m", model, prompt]
        try:
            code, out, err = await asyncio.to_thread(self._runner, argv)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                f"opencode-cli: CLI timed out after {_TIMEOUT_S:.0f}s", transient=True
            ) from exc
        if code != 0:
            detail = (err or out).strip()[:400]
            raise RuntimeError(f"opencode-cli: CLI exited {code}: {detail}")
        text, usage = parse_events(out)
        if not text:
            raise RuntimeError("opencode-cli: CLI returned no output")
        return LLMResponse(text=text, tool_calls=[], usage=usage)


def make_opencode_cli(**kw: Any) -> OpencodeCliAdapter:
    return OpencodeCliAdapter(**kw)
