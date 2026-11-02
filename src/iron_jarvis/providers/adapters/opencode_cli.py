"""OpenCode CLI as a provider — LOCAL MODELS ONLY.

Drives ``opencode run --format json`` headlessly and parses its JSONL event
stream back into an :class:`LLMResponse`.

The hard rule this adapter exists to enforce: **it refuses any model that is
not on the allowed-local list**, before spawning anything. OpenCode can reach
paid remote models (its hosted tier, or a passthrough alias on your own proxy),
and the user asked for local only — so an unrecognised model is an honest error,
never a "well, it probably works" call that quietly bills them. See
``providers/opencode.py`` for how locality is decided.

OPENCODE'S OWN TOOLS ARE OFF (v1.424.0, audit D5). ``opencode run`` keeps
every tool of OpenCode's on, and OpenCode's own default runs every one of them
without asking (``"*": "allow"``; an ``ask`` in ``opencode run`` is auto-
rejected, an ``allow`` just runs). As the model behind a CHAT turn or an agent
run that meant OpenCode could edit files and run commands Iron Jarvis never
saw: no card, no ledger row, no Undo. Here Iron Jarvis carries out every tool
itself (the router's prompted-tools wrap) or the turn is text-only, so the
call runs with EVERY OpenCode tool denied — and a tool OpenCode denies with
the pattern ``*`` is REMOVED from what the model is offered (measured, below).

MEASURED on this PC with the installed opencode 1.18.18, in scratch config /
data folders (``XDG_CONFIG_HOME`` and friends), with ``opencode debug agent
<name>`` (the agent's resolved rules and the tools it offers — no model call)
and ``opencode run --agent <name> -m nonexistent/none`` (whether ``run`` takes
the agent — no model call). Recorded in
``tests/fixtures/opencode_text_only_v14240.json``:

* ``OPENCODE_PERMISSION`` (JSON) is merged OVER the config's ``permission``
  (``hosted/opencode``'s merge model: a key the user already has keeps its
  PLACE, a new key is appended, the last matching rule wins). ``{"*":
  "deny"}`` alone LOST to a user's ``"*": "allow"`` followed by ``"bash":
  "allow"`` (bash stayed offered). So every key of the user's own global
  rules is named ``deny`` too (:func:`text_only_setup`, through
  ``hosted.opencode.user_rules``, the Code view's reader — the user's files
  are only ever read).
* An AGENT's own ``permission`` in the user's config is placed AFTER the
  config's (and the environment's) rules: ``agent.build.permission.bash =
  "allow"`` beat every deny above. So the call runs as an agent of Iron
  Jarvis's OWN, defined for that process only in ``OPENCODE_CONFIG_CONTENT``
  (merged into an inline config of the user's, never replacing it) and named
  with ``--agent``: its own all-``deny`` permission is the LAST rule for every
  tool. The name is FRESH per call (``iron-jarvis-text-<hex>``): a user agent
  of the same name (a ``.md`` agent file is merged BEFORE the inline config,
  in place) would put its own rules around ours.
* ``opencode run --agent <name>`` with an agent it cannot find prints
  ``agent "<name>" not found. Falling back to default agent`` on stderr and
  runs the user's default agent (tools on). That warning, and any
  ``tool_use`` event in the reply, makes the reply an honest ERROR here
  (:func:`fell_back`, :func:`tools_run`) — never an answer passed on as if
  nothing happened.
* ``opencode run`` adds session rules after the agent's (``question``,
  ``plan_enter``, ``plan_exit``: all deny) and nothing that allows.

Build's Code view (``hosted/opencode``) is the OTHER way round on purpose —
the user picked the real tool there — and none of this reaches it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
from typing import Any, Callable

from .base import LLMAdapter, LLMMessage, LLMResponse, ProviderError
from .subprocess_cli import _run as _cli_run, run_cli

log = logging.getLogger(__name__)

#: OpenCode boots a server and may run a tool loop; give it room but never hang.
_TIMEOUT_S = 300.0

#: v1.424.0 (D5): OpenCode's own tools, every one named ``deny`` beside
#: ``"*"`` (the Code view's two lists, plus the tools ``opencode debug agent
#: build`` listed on 1.18.18 — ``write``, ``question`` — and the edit family's
#: other names and the plan switches). ``"*"`` already covers them; naming
#: each keeps a tool denied even where a rule of the user's sits after ``"*"``.
_EXTRA_TOOLS: tuple[str, ...] = (
    "write", "patch", "apply_patch", "multiedit", "question", "plan_enter", "plan_exit",
)

#: The agent each call runs as: Iron Jarvis's own, defined only in that
#: process's ``OPENCODE_CONFIG_CONTENT`` (``iron-jarvis-text-<8 hex>``).
AGENT_PREFIX = "iron-jarvis-text-"


def _agent_name() -> str:
    """A fresh agent name for one call (a seam: the fixture's regeneration
    fixes it)."""
    return AGENT_PREFIX + secrets.token_hex(4)


class TextOnlyRefused(RuntimeError):
    """Iron Jarvis could not make sure OpenCode's own tools are off for this
    call, so OpenCode was not started (fail closed)."""


def builtin_tools() -> tuple[str, ...]:
    """Every OpenCode tool Iron Jarvis knows by name (the Code view's lists +
    :data:`_EXTRA_TOOLS`), each once, in a stable order."""
    from ...hosted.opencode import ACTING_TOOLS, READ_ONLY_TOOLS

    return tuple(dict.fromkeys(READ_ONLY_TOOLS + ACTING_TOOLS + _EXTRA_TOOLS))


def deny_all(rules: dict[str, Any] | str) -> dict[str, str]:
    """``{"*": "deny"}`` + every built-in tool + every key of the user's own
    rules (``rules``: ``hosted.opencode.user_rules``' answer — a whole-config
    string rule names no key), each ``deny``. Pure."""
    out: dict[str, str] = {"*": "deny"}
    for name in builtin_tools():
        out[name] = "deny"
    if isinstance(rules, dict):
        for key in rules:
            out[str(key)] = "deny"
    return out


def text_only_setup(environ: dict[str, str], folder: str) -> tuple[dict[str, str], str]:
    """BLOCKING (reads the user's OpenCode config files, read-only): ``(the
    child's environment, the agent to run as)`` for an ``opencode run`` whose
    own tools are ALL off (see the module docstring). ``folder`` is the
    folder OpenCode will run in (its project config files count too).

    Raises :class:`TextOnlyRefused` when the user's own
    ``OPENCODE_CONFIG_CONTENT`` is not a JSON object with an ``agent`` object
    (Iron Jarvis would have to drop it to add its agent): nothing starts."""
    from ...hosted.opencode import user_rules

    env = dict(environ)
    deny = deny_all(user_rules(folder, env))
    name = _agent_name()
    raw = str(env.get("OPENCODE_CONFIG_CONTENT") or "").strip()
    content: Any = {}
    if raw:
        try:
            content = json.loads(raw)
        except ValueError:
            content = None
    agents = content.get("agent", {}) if isinstance(content, dict) else None
    if not isinstance(content, dict) or not isinstance(agents, dict):
        raise TextOnlyRefused(
            "opencode-cli: your OPENCODE_CONFIG_CONTENT setting is not a config "
            "Iron Jarvis can add to, so it cannot make sure OpenCode's own tools "
            "stay off — OpenCode was not started. Fix or remove that setting."
        )
    agents = dict(agents)
    agents[name] = {
        "mode": "primary",
        "description": "Iron Jarvis: answer in text; Iron Jarvis runs every tool itself.",
        "permission": dict(deny),
    }
    content = dict(content)
    content["agent"] = agents
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(content, separators=(",", ":"))
    # One OPENCODE_PERMISSION: an object of the user's own already there is
    # read by `user_rules` (its keys are denied above) and replaced — every
    # rule here says deny, so nothing of theirs is loosened.
    env["OPENCODE_PERMISSION"] = json.dumps(deny, separators=(",", ":"))
    return env, name


def fell_back(out: str, err: str, agent: str) -> bool:
    """Did ``opencode run`` refuse Iron Jarvis's agent and fall back to the
    user's default one (its own tools on)? Its own warning: ``agent "<name>"
    not found. Falling back to default agent`` (or "is a subagent")."""
    needle = f'agent "{agent}"'
    return needle in (err or "") or needle in (out or "")


def tools_run(stdout: str) -> list[str]:
    """The OpenCode tools a reply's JSON event stream says RAN (``tool_use``
    events), each once. ``invalid`` — OpenCode's answer to a call of a tool
    it does not offer — ran nothing and is left out."""
    names: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if not isinstance(evt, dict) or evt.get("type") != "tool_use":
            continue
        part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
        name = str(part.get("tool") or "?").strip() or "?"
        if name != "invalid" and name not in names:
            names.append(name)
    return names


def _run(
    argv: list[str], stdin: str | None = None, timeout: float = _TIMEOUT_S
) -> tuple[int, str, str]:
    """Blocking run with a REAL bound — the shared CLI runner (chat-01)."""
    return _cli_run(argv, stdin, timeout)


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
        #: An injected ``runner(argv, stdin)`` (test doubles) keeps the plain
        #: to_thread path; None = the real, cancel-killable `run_cli` (chat-01).
        self._runner = runner
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
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        # Guided-decoding knobs (v1.203.0): accepted so callers can pass them
        # uniformly across adapters; IGNORED — the CLI owns its own harness,
        # not part of the openai-compat guided-decoding family this wave.
        response_format: dict | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
        reasoning: str = "",
    ) -> LLMResponse:
        from ..opencode import binary

        model = self._resolve_model()  # refuses BEFORE anything is spawned
        exe = binary(self._which)
        if not exe:
            raise RuntimeError("opencode-cli: the 'opencode' CLI is not installed/on PATH")
        prompt = _flatten(system, messages)
        if not prompt.strip():
            raise RuntimeError("opencode-cli: nothing to send")
        # The prompt rides STDIN, never argv: Windows caps a command line at
        # 32,767 chars (8,191 when the CLI resolves to an npm .cmd shim, which
        # spawns through cmd.exe), and one extracted PDF plus the identity/skills
        # system prompt clears that easily — the request then dies before the CLI
        # starts (raw OSError WinError 206, or "The command line is too long").
        # Same live-hit that moved the codex/claude adapters to stdin on
        # 2026-07-20; `opencode run` with no positional message reads its prompt
        # from piped stdin.
        # v1.424.0 (D5): OpenCode's own tools OFF — its permission rules and an
        # agent of Iron Jarvis's own, built from the user's config (read-only,
        # off the loop) for the folder the CLI runs in (it inherits ours).
        env, agent = await asyncio.to_thread(text_only_setup, dict(os.environ), os.getcwd())
        argv = [exe, "run", "--format", "json", "--agent", agent, "-m", model]
        try:
            if self._runner is not None:
                code, out, err = await asyncio.to_thread(
                    lambda: self._runner(argv, prompt, env=env)
                )
            else:
                # Stop / a closed tab kills the CLI tree, not just the await.
                code, out, err = await run_cli(argv, prompt, timeout=_TIMEOUT_S, env=env)
        except subprocess.TimeoutExpired as exc:
            timed_out = ProviderError(
                f"opencode-cli: CLI timed out after {_TIMEOUT_S:.0f}s", transient=True
            )
            # v1.358.0 (integrate): typed like the Claude transport's timeout,
            # so chat says "OpenCode didn't answer in time".
            timed_out.kind = "timeout"  # type: ignore[attr-defined]
            timed_out.failed_provider = "opencode-cli"  # type: ignore[attr-defined]
            raise timed_out from exc
        # v1.424.0 (D5): checked BEFORE the exit code — a run that fell back to
        # the user's own agent, or that ran a tool of OpenCode's, is said as
        # that whatever else went wrong. Its answer is never passed on.
        if fell_back(out, err, agent):
            raise RuntimeError(
                "opencode-cli: OpenCode did not take the tool-free setup Iron Jarvis "
                "gives it and ran your default agent instead, so its answer was not "
                "used (OpenCode's own tools may have run)."
            )
        ran = tools_run(out)
        if ran:
            raise RuntimeError(
                "opencode-cli: OpenCode ran its own tools ("
                + ", ".join(ran[:8])
                + ") outside Iron Jarvis, so its answer was not used."
            )
        if code != 0:
            detail = (err or out).strip()[:400]
            raise RuntimeError(f"opencode-cli: CLI exited {code}: {detail}")
        text, usage = parse_events(out)
        if not text:
            raise RuntimeError("opencode-cli: CLI returned no output")
        return LLMResponse(text=text, tool_calls=[], usage=usage)


def make_opencode_cli(**kw: Any) -> OpencodeCliAdapter:
    return OpencodeCliAdapter(**kw)
