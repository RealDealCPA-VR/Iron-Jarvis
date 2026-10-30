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
LABELS: dict[str, str] = {"claude-code": "Claude Code", "codex": "Codex", "pi": "Pi"}
#: harness id -> the Iron Jarvis provider whose CLI it is.
PROVIDERS: dict[str, str] = {"claude-code": "claude-cli", "codex": "codex-cli"}
#: harness id -> the Iron-Proxy provider of its accounts. Pi has none: it
#: signs in by itself, per model provider (its own `/login`).
PROXY: dict[str, str] = {"claude-code": "anthropic", "codex": "openai"}
#: harness id -> the Build terminal Launch id (``terminals/ai_clis``).
CLI: dict[str, str] = {"claude-code": "claude", "codex": "codex", "pi": "pi"}

BUSY = (
    "This session is still working on your last message — wait for it to finish, "
    "or press Stop."
)
ARCHIVED = "This session is archived — unarchive it to send a message."
#: v1.412.0: a session started with no folder (its own folder is made with
#: its first message, under the chat files folder).
NO_FOLDER_RESUME = "A saved conversation belongs to its folder — choose that folder to open it."
NO_FOLDER_MADE = "This session's own folder could not be made in {root}: {why}"
NO_FOLDER_UNUSABLE = "This session's own folder cannot be used: {why}"
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
PI_OWN_SIGN_IN = (
    "Pi signs in by itself, per model provider, so a Pi session has no account to choose — "
    "to change its sign-in, open Pi in a Build terminal and type /login there."
)
#: v1.411.0: /login typed in the Code view's box would reach the model as
#: text (Pi's sign-in screen exists in its terminal only).
PI_LOGIN_HERE = (
    "Pi signs in on its own screen, which needs a terminal — open Pi in a Build terminal "
    "(Open in terminal) and type /login there."
)
#: v1.411.0: Pi is running something an extension started, outside any message.
PI_STILL_WORKING_TITLE = "Pi is still working on something an extension started"
PI_STILL_WORKING_FIX = "Wait for it to finish, then send your message again."
PI_STILL_WORKING = (
    "Pi is still working on something an extension started — wait for it to finish, "
    "then send your message again."
)
#: v1.411.0: Pi restores a conversation's last model and thinking level when it
#: reopens it, so "Pi's default" cannot apply to one that already has messages.
PI_KEEPS_LAST = (
    "This Pi conversation keeps the model and thinking level it last used — "
    "choose one from the list instead."
)
PI_CONTINUE = (
    "Pi signs in by itself, per model provider, so it has no other account to continue on — "
    "choose another model instead."
)
CODEX_CONTINUE = (
    "Continue on another account works for Claude Code sessions only for now — "
    "open a new Codex session on the other account instead."
)
THIS_PC = (
    "This session runs on this PC's login — start a session on an Iron-Proxy account "
    "to move between accounts."
)
GONE_DURING_TURN = "{label} stopped while it was working."
CLAIMED = "This session is {what} — try again in a moment."
HELD_BY_ROW = (
    "This conversation is already open in another Code-view session — use that one, "
    "or archive it first."
)
HELD_BY_TERMINAL = (
    "This conversation is open in a Build terminal — exit {label} there first (type {exit}), "
    "then send your message here."
)
OPEN_IN_TERMINAL = (
    "This conversation is open in a Build terminal — exit {label} there first (type {exit}), "
    "then send your message here."
)
CANNOT_TELL = (
    "Iron Jarvis cannot tell which {label} conversation this terminal ran — open it from the "
    "folder's saved sessions instead."
)
STOPPED_BECAUSE = "Stopped because the session is {why}."
STOPPED_PLAIN = "Stopped: the coding tool was closed."
CODEX_MODE_NEXT = (
    "Codex keeps its current mode for the message it is working on; {mode} applies from your "
    "next message."
)
MODE_FROM_SESSION_ALLOW = "{label} now runs in {mode} for the rest of this session (you chose Allow for this session)."
NO_CONVERSATION_FILE = (
    "The conversation's file is not in this account's folder, so it cannot move to another "
    "account — nothing was changed."
)
CANNOT_COPY = "The conversation could not be copied to the other account — nothing was changed."
DIVERGED = (
    '"{title}" already holds a different copy of this conversation, so it was not moved — '
    "nothing was changed."
)
CONTINUED = (
    'Continued on "{title}" — the conversation was copied there; your next message resumes it.'
)


def label(harness: str) -> str:
    return LABELS.get(harness, harness or "The harness")


#: harness id -> the command that quits it in a terminal (Pi's is /quit).
EXIT_COMMANDS: dict[str, str] = {"pi": "/quit"}


def exit_command(harness: str) -> str:
    return EXIT_COMMANDS.get(harness, "/exit")


def could_not_start(harness: str, program: str, exc: BaseException) -> str:
    """The sentence for a program Windows could not start at all."""
    import os

    name = os.path.basename(str(program or "")) or label(harness)
    code = getattr(exc, "winerror", None)
    if code == 193:
        why = "that file is not a program Windows can run"
    elif code == 2 or isinstance(exc, FileNotFoundError):
        why = "that file is not there any more"
    elif code == 5 or isinstance(exc, PermissionError):
        why = "Windows did not allow it to run"
    else:
        why = clip(getattr(exc, "strerror", None) or type(exc).__name__, 120)
    return (f"Iron Jarvis could not start {label(harness)} from {clip(name, 80)}: {why}. "
            f"Check that {label(harness)} runs in a Build terminal.")


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
