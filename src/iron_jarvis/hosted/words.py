"""The Code view's words (v1.409.0): names, failure sentences, refusals.

Failures are ``failure_words`` shapes ``{title, fix, technical}`` (the chat's
and Build's own), with ``fix_kind`` when a press fixes it: ``sign_in`` (the
CLI's own sign-in, ``cli_auth.SIGN_IN_FIX``) or ``account_limit`` (Continue on
another account). The technical text is masked before it is cut. A sign-in
failure is read from the harness's WORDS (``cli_auth.is_sign_in_refusal``),
never from Claude Code's ``authentication_failed`` code alone — the CLI stamps
that code on every upstream 403, including "your organisation has no access
to this model" (CLAUDE.md v1.300.0).
"""

from __future__ import annotations

from typing import Any

from .events import clip, make

#: harness id -> how a person names it.
LABELS: dict[str, str] = {"claude-code": "Claude Code", "codex": "Codex"}
#: harness id -> the Iron Jarvis provider whose CLI it is.
PROVIDERS: dict[str, str] = {"claude-code": "claude-cli", "codex": "codex-cli"}
#: harness id -> the Iron-Proxy provider of its accounts.
PROXY: dict[str, str] = {"claude-code": "anthropic", "codex": "openai"}
#: harness id -> the Build terminal Launch id (``terminals/ai_clis``).
CLI: dict[str, str] = {"claude-code": "claude", "codex": "codex"}

BUSY = (
    "This session is still working on your last message — wait for it to finish, "
    "or press Stop."
)
ARCHIVED = "This session is archived — unarchive it to send a message."
NOT_INSTALLED = "{label} is not installed on this PC, so this session cannot start it."
FULL_NEEDS_CONFIRM = (
    "Full access lets {label} run anything without asking — press it twice to confirm."
)
POOL_FULL = (
    "Six Code-view sessions are already working right now — wait for one to finish, "
    "then send this again."
)
NO_SESSION_YET = "This session has no conversation yet — send a message first."
RUNNING = "This session is working on a message — stop it or let it finish first."
ACCOUNT_LIMIT_FIX = "Press Continue on another account, or wait until it resets."
CODEX_CONTINUE = (
    "Continue on another account works for Claude Code sessions only for now — "
    "open a new Codex session on the other account instead."
)
THIS_PC = (
    "This session runs on this PC's login — start a session on an Iron-Proxy account "
    "to move between accounts."
)
GONE_DURING_TURN = "{label} stopped while it was working."


def label(harness: str) -> str:
    return LABELS.get(harness, harness or "The harness")


def error(title: str, fix: str, technical: Any = "", **extra: Any) -> dict[str, Any]:
    """An ``error`` event (technical masked, then cut)."""
    out = make("error", title=title, fix=fix, technical=clip(technical, 2000))
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def sign_in_error(harness: str, technical: Any) -> dict[str, Any]:
    from ..providers.cli_auth import SIGN_IN_FIX

    name = label(harness)
    title = f"{name} isn't signed in"
    fix = SIGN_IN_FIX.get(CLI.get(harness, ""), f"Sign in to {name} in Settings › Connections.")
    # SIGN_IN_FIX opens with the title's own sentence; the card says it once.
    if fix.startswith(title + ". "):
        fix = fix[len(title) + 2:]
    return error(title, fix, technical, fix_kind="sign_in")


def limit_error(harness: str, technical: Any, *, reset_words: str = "") -> dict[str, Any]:
    name = label(harness)
    fix = ACCOUNT_LIMIT_FIX
    if reset_words:
        fix = f"Press Continue on another account, or wait until it resets ({clip(reset_words, 80)})."
    return error(f"{name} hit this account's limit", fix, technical, fix_kind="account_limit")


def generic_error(harness: str, technical: Any) -> dict[str, Any]:
    """The failure words' own sentence for anything not recognised."""
    from ..providers import failure_words as fw

    try:
        words = fw.friendly_error(
            RuntimeError(str(technical or "")), provider=PROVIDERS.get(harness, ""),
            surface=fw.SURFACE_BUILD,
        )
        title, fix = words["title"], words["fix"]
    except Exception:  # noqa: BLE001 — words are never a new failure
        title, fix = f"{label(harness)} couldn't finish this answer", fw.GENERIC_FIX_BUILD
    return error(title, fix, technical)


def failure_for(harness: str, text: Any) -> dict[str, Any]:
    """The error event for a failure the harness SAID in ``text``."""
    from ..providers.cli_auth import is_sign_in_refusal
    from ..terminals.limit_state import detect_limit

    said = str(text or "")
    if is_sign_in_refusal(said):
        return sign_in_error(harness, said)
    found = detect_limit(said)
    if found is not None:
        return limit_error(harness, said, reset_words=found.reset_words)
    return generic_error(harness, said)
