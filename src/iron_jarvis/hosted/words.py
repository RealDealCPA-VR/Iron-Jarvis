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

import re
from typing import Any

from .events import clip, make

#: harness id -> how a person names it.
LABELS: dict[str, str] = {"claude-code": "Claude Code", "codex": "Codex", "pi": "Pi", "opencode": "OpenCode"}
#: harness id -> the Iron Jarvis provider whose CLI it is.
PROVIDERS: dict[str, str] = {"claude-code": "claude-cli", "codex": "codex-cli"}
#: harness id -> the Iron-Proxy provider of its accounts. Pi has none: it
#: signs in by itself, per model provider (its own `/login`); OpenCode too
#: (`opencode auth login`).
PROXY: dict[str, str] = {"claude-code": "anthropic", "codex": "openai"}
#: harness id -> the Build terminal Launch id (``terminals/ai_clis``).
CLI: dict[str, str] = {"claude-code": "claude", "codex": "codex", "pi": "pi", "opencode": "opencode"}

BUSY = (
    "This session is still working on your last message — wait for it to finish, "
    "or press Stop."
)
#: v1.412.0: a note typed while the tool works (a STEER).
STEER_NOT_WORKING = "{label} is not working on anything right now — send it as a message."
STEER_TOO_LATE = "{label} had already finished, so your note did not reach it — send it as a message."
STEER_UNSUPPORTED = (
    "This version of {label} does not say when it reads a note, so notes cannot be sent while it works — "
    "wait for it, or press Stop."
)
#: How each tool takes a note (said under the note).
STEER_HOW: dict[str, str] = {
    "claude-code": "Claude Code reads it at its next step, or right after this reply.",
    "codex": "Codex reads it at its next step.",
    "pi": "Pi reads it after its current step.",
    "opencode": "OpenCode reads it at its next step.",
}
STEER_READ = "Read by {label}."
STEER_RETURNED = "{label} did not read this note before it stopped — it is back in your message box."
#: v1.412.0 review: an attachment spelled as a network share (refused before
#: anything touches it, unless the session's own folder is on a share).
ATTACHED_ON_SHARE = (
    "Attach files from this PC's own drives — a file on a network share is not sent."
)
#: v1.412.0: a picture that could not go AS a picture (the tool got its path).
PICTURE_AS_PATH = "{name} went to {label} as a file path, not as a picture: {why}."
#: v1.412.0: the mode changed while a card was waiting.
MODE_CARD_STILL_OPEN = (
    "{label} now runs in {mode}. It had already asked about the card above — answer that one yourself."
)
ARCHIVED = "This session is archived — unarchive it to send a message."
#: v1.412.0: a session started with no folder (its own folder is made with
#: its first message, under the chat files folder).
NO_FOLDER_RESUME = "A saved conversation belongs to its folder — choose that folder to open it."
NO_FOLDER_MADE = "This session's own folder could not be made in {root}: {why}"
NO_FOLDER_UNUSABLE = "This session's own folder cannot be used: {why}"
#: v1.412.0 review: the session's own folder is gone and cannot be made again
#: at its path (the folder above it is gone too — an unplugged drive, a moved
#: chat files folder). Its conversation belongs to that path.
NO_FOLDER_GONE = (
    "This session's folder {folder} is missing, and the folder above it is gone too, so it "
    "cannot be made again. Put that folder back (or plug its drive in), then send your message again."
)
#: v1.412.0 review: the folder a session works in is missing (Windows only
#: says "The directory name is invalid", which read as the tool's fault).
FOLDER_MISSING = (
    "This session's folder {folder} is missing. Put it back (or plug its drive in), "
    "or start a new session in another folder."
)
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
#: v1.413.0: OpenCode signs in by itself, per provider (`opencode auth login`).
OPENCODE_OWN_SIGN_IN = (
    "OpenCode signs in by itself, per provider, so an OpenCode session has no account to choose — "
    "to change its sign-in, run opencode auth login in a Build terminal."
)
OPENCODE_CONTINUE = (
    "OpenCode signs in by itself, per provider, so it has no other account to continue on — "
    "choose another model instead."
)
#: v1.413.0: how each tool that signs in BY ITSELF does it (the account
#: chip's title on the page says THIS tool's way, never Pi's for OpenCode).
SIGN_IN_HINTS: dict[str, str] = {
    "pi": ("Pi signs in by itself, per model provider: to change it, open Pi in a Build terminal and "
           "type /login there. Iron Jarvis's accounts do not apply."),
    "opencode": ("OpenCode signs in by itself, per provider: to change it, run opencode auth login in a "
                 "Build terminal. Iron Jarvis's accounts do not apply."),
}
#: v1.413.0: the user's own OpenCode permission rules, said once per start.
OPENCODE_RULES_KEPT = (
    "Your own OpenCode permission rules for {tools} apply in this session as you wrote them. "
    "So that they still apply, this mode names only OpenCode's built-in tools: tools from the MCP "
    "servers and plugins in your OpenCode config run without asking unless a rule of yours says otherwise."
)
#: v1.413.0 review: the same, when no rule of the user's is kept word for word
#: (Plan replaced their edit rule with a refusal) but "*" still could not be used.
OPENCODE_UNNAMED_UNASKED = (
    "In this session, tools from the MCP servers and plugins in your OpenCode config run without "
    "asking unless a rule of yours says otherwise."
)
OPENCODE_RULES_DECIDE = (
    "Your OpenCode config sets one permission rule for every tool, so that rule decides in this "
    "session, whatever mode is chosen here."
)
#: v1.413.0: a note OpenCode took but never reached before Stop: it stays in
#: OpenCode's conversation (never handed back to be sent twice).
OPENCODE_NOTE_KEPT = (
    "OpenCode stopped before it reached your note; the note stays in its conversation and it will "
    "see it with your next message."
)
OPENCODE_NO_SUCH_EFFORT = (
    'This OpenCode model does not take the effort "{effort}", so it runs on its own default.'
)
#: v1.413.0: OpenCode keeps a reopened conversation's last model and effort.
OPENCODE_KEEPS_LAST = (
    "This OpenCode conversation keeps the model and effort it last used — "
    "choose one from the list instead."
)
#: v1.413.0: a mode whose permission rules apply from the next message (they
#: are the OpenCode process's own environment).
MODE_NEXT = (
    "{label} keeps its current mode for the message it is working on; {mode} applies from your "
    "next message."
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


#: v1.414.0 (fix): Iron Jarvis's OWN server's tools (``jarvis_tools.SERVER_NAME``)
#: in plain words on a card — ``{q}`` is the call's ``query``. Any other
#: server's tool reads "Use <server> › <tool>" (the tool row's own words).
JARVIS_TOOL_TITLES: dict[str, tuple[str, str]] = {
    # tool -> (with a query, without one)
    "memory_search": ('Search Iron Jarvis memory for "{q}"', "Search Iron Jarvis memory"),
    "memory_read": ("Read a note from Iron Jarvis memory", "Read a note from Iron Jarvis memory"),
    "ltm_search": ('Search your memory bases for "{q}"', "Search your memory bases"),
    "brain_search": ('Search your memory base for "{q}"', "Search your memory base"),
    "brain_read": ("Read a note from your memory base", "Read a note from your memory base"),
}
#: How many of a call's arguments a card lists (the rest are counted).
MCP_ARG_LINES = 12
#: v1.414.0 (fix): an MCP server's OWN form (a question with fields to fill,
#: or a web page to open) — not a "may I run this tool" question.
MCP_FORM_DECLINED = (
    "{server} asked Codex to fill in a form or open a page; the Code view cannot answer an app's "
    "form yet, so it was declined."
)


def mcp_call_title(server: str, tool: str, args: Any) -> str:
    """A card's one line for an MCP tool call: Iron Jarvis's own tools in
    words, any other server's as "Use <server> › <tool>"."""
    from .events import one_line
    from .jarvis_tools import SERVER_NAME

    tool = str(tool or "")
    if server == SERVER_NAME and tool in JARVIS_TOOL_TITLES:
        with_q, plain = JARVIS_TOOL_TITLES[tool]
        q = one_line((args or {}).get("query") if isinstance(args, dict) else "", 80)
        return with_q.format(q=q) if q else plain
    if server == SERVER_NAME:
        return f"Use Iron Jarvis › {tool}" if tool else "Use an Iron Jarvis tool"
    if not tool:
        return f"Use a {server} tool" if server else "Use an app's tool"
    return f"Use {server} › {tool}" if server else f"Use {tool}"


def mcp_args_lines(args: Any) -> str:
    """A call's arguments as "name: value" lines (sorted; long values on one
    cut line; past :data:`MCP_ARG_LINES` the rest counted) — masked by the
    caller's ``clip``."""
    import json

    from .events import one_line

    if not isinstance(args, dict) or not args:
        return ""
    lines = []
    for k in sorted(args)[:MCP_ARG_LINES]:
        v = args[k]
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)
        lines.append(f"{one_line(k, 60)}: {one_line(text, 200)}")
    more = len(args) - MCP_ARG_LINES
    if more > 0:
        lines.append(f"…and {more} more")
    return "\n".join(lines)


#: harness id -> the command that quits it in a terminal (Pi's is /quit).
EXIT_COMMANDS: dict[str, str] = {"pi": "/quit"}  # OpenCode: /exit


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
    elif code == 267:
        # v1.412.0 review: the folder the tool starts in is missing, not
        # the tool ("The directory name is invalid").
        return f"The folder this session works in is missing, so {label(harness)} could not start there."
    else:
        why = clip(getattr(exc, "strerror", None) or type(exc).__name__, 120)
    return (f"Iron Jarvis could not start {label(harness)} from {clip(name, 80)}: {why}. "
            f"Check that {label(harness)} runs in a Build terminal.")


def error(title: str, fix: str, technical: Any = "", **extra: Any) -> dict[str, Any]:
    """An ``error`` event (technical masked, then cut)."""
    out = make("error", title=title, fix=fix, technical=clip(technical, 2000))
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


#: v1.414.0 (fix): a login that was there and ran out (expired, revoked, or
#: could not be renewed) — the title then says "needs to sign in again".
_EXPIRED_LOGIN = re.compile(r"expired|revoked|could not be refreshed", re.I)


def sign_in_error(harness: str, technical: Any) -> dict[str, Any]:
    from ..providers.cli_auth import SIGN_IN_FIX

    name = label(harness)
    title = f"{name} isn't signed in"
    fix = SIGN_IN_FIX.get(CLI.get(harness, ""), f"Sign in to {name} in Settings › Connections.")
    # SIGN_IN_FIX opens with the title's own sentence; the card says it once.
    if fix.startswith(title + ". "):
        fix = fix[len(title) + 2:]
    if _EXPIRED_LOGIN.search(str(technical or "")):
        title = f"{name} needs to sign in again"
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
