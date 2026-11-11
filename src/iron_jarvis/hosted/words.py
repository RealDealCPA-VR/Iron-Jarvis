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

from ..providers import subscription_clis as _SUBS
from .events import clip, make

#: harness id -> how a person names it.
LABELS: dict[str, str] = {"claude-code": "Claude Code", "codex": "Codex", "pi": "Pi", "opencode": "OpenCode",
                          "grok": "Grok", "antigravity": "Antigravity"}
#: harness id -> the Iron Jarvis provider whose CLI it is.
#: v1.426.0 (E5): read from THE table (``providers/subscription_clis``).
PROVIDERS: dict[str, str] = dict(_SUBS.HARNESS_TO_PROVIDER)
#: harness id -> the Iron-Proxy provider of its accounts. Pi has none: it
#: signs in by itself, per model provider (its own `/login`); OpenCode too
#: (`opencode auth login`). v1.416.0: a Grok account is its home
#: (``GROK_HOME``, Iron-Proxy's ``xai`` provider), as for a Build terminal.
PROXY: dict[str, str] = dict(_SUBS.HARNESS_TO_PROXY)
#: harness id -> the Build terminal Launch id (``terminals/ai_clis``).
#: v1.439.0: Antigravity's program is ``agy`` (its Launch id too).
CLI: dict[str, str] = {"claude-code": "claude", "codex": "codex", "pi": "pi", "opencode": "opencode",
                       "grok": "grok", "antigravity": "agy"}

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
    "grok": "Grok takes it at once and reads it at its next step.",
    "antigravity": "Antigravity reads it at its next step.",
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
#: v1.425.0 (Wave 5, B7): said once while a message waits for a place.
POOL_WAITING = (
    "Six coding sessions are working right now — this message starts as soon as one "
    "of them finishes."
)
POOL_FULL = (
    "Six Code-view sessions are already working right now — wait for one to finish, "
    "then send this again."
)
NO_SESSION_YET = "This session has no conversation yet — send a message first."
RUNNING = "This session is working on a message — stop it or let it finish first."
#: v1.453.0: Archive is refused while the tool still works in the background
#: (archiving stops the tool, and what it runs in the background with it).
ARCHIVE_BACKGROUND = (
    "{label} is still working in the background in this session (helpers, commands, or a reply of "
    "its own). Archiving stops it, so wait for that to finish, or ask it to stop, then archive."
)
#: v1.453.0: the summary could not be built (the archive still happened).
ARCHIVE_SUMMARY_FAILED = "The session was archived, but its summary could not be written."
NO_ARCHIVE_SUMMARY = "This session has no archive summary yet — one is written when it is archived."
#: v1.417.0 review: what runs in the background dies with the process — a
#: restart (effort, model, tools) waits for it, and "Open in terminal" is refused.
BACKGROUND_OPEN_TERMINAL = (
    "{label} still runs {tasks} in this session (helpers or commands), and opening it in a "
    "terminal would stop them — wait for them to finish, or ask it to stop them, then try again."
)
RESTART_WAITS = (
    "{label} still runs {tasks} in this session, so your change of model, effort or tools "
    "applies once they finish — restarting it now would stop them."
)


def background_count(n: int) -> str:
    return f"{n} background task" + ("" if n == 1 else "s")
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
#: v1.429.0: Pi listed no model at all (nothing signed in, none configured).
PI_NO_MODELS = (
    "Pi has no model it can use yet. Open Pi in a Build terminal and type /login, "
    "or add a model to Pi's models.json."
)
#: v1.429.0: a /model typed with a name Pi does not list.
PI_NO_SUCH_MODEL = (
    "Pi does not list a model called {name}. Type it as provider/model, the way the model menu shows it."
)
#: v1.429.0: Pi's own commands are a message of their own (never a note).
PI_COMMAND_NOT_A_NOTE = "Pi's own commands go as a message of their own — send it once Pi has finished."
#: v1.431.0 (``hosted/pi_trust``): a folder's OWN Pi resources, which Pi
#: skips here because it cannot ask (its terminal would).
PI_FOLDER_THINGS = "extensions and settings"
PI_FOLDER_SKIPPED = (
    "This folder has its own Pi {things}. Pi skips them here, because it can only ask about them "
    "in its terminal."
)
PI_FOLDER_LOADED = "This folder's own Pi {things} load in this session."
PI_FOLDER_TITLE = (
    "A folder's Pi extensions run as programs on this PC. Load them only if you trust this folder. "
    "This applies to this session only and saves nothing in Pi's trust list. If one of your own Pi "
    "extensions decides trust itself, Pi may already load them; Iron Jarvis cannot see that."
)
PI_FOLDER_LOAD_NOW = "Pi will load this folder's own {things} from your next message."
PI_FOLDER_SKIP_NOW = "Pi will skip this folder's own {things} again from your next message."
PI_FOLDER_NOT_PI = "Only a Pi session loads a folder's own Pi extensions."
PI_FOLDER_NOTHING = "This folder has no Pi extensions or settings of its own to load."
PI_FOLDER_ALREADY = "Pi already loads this folder's own extensions: your Pi settings trust this folder."
PI_FOLDER_DECLINED = (
    "You told Pi not to trust this folder. To change that, open Pi in a Build terminal and type /trust."
)
PI_FOLDER_CANNOT_TELL = (
    "Iron Jarvis cannot read this folder's Pi settings safely, so it will not load them from here."
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
#: v1.416.0: Grok — a Deny ENDS its reply (measured: ``reject-once`` answers
#: the prompt "cancelled", category PermissionRejected).
GROK_DECLINED_ENDS = (
    "You declined, so Grok stopped this reply — tell it what to do differently in your next message."
)
#: v1.416.0: Grok's cancel leaves a running command running (measured), so a
#: Stop then closes Grok, whose process tree takes the command with it.
GROK_STOP_CLOSED = (
    "Grok was running a command when you pressed Stop, so Grok was closed to end it — the "
    "conversation is kept and reopens with your next message."
)
GROK_NO_SUCH_EFFORT = 'This Grok model does not take the effort "{effort}", so it runs on its own default.'
#: v1.416.0: Grok keeps a reopened conversation's last model (measured).
GROK_KEEPS_LAST = (
    "This Grok conversation keeps the model it last used — choose a model from the list instead."
)
GROK_CONTINUE = (
    "Continue on another account works for Claude Code and Codex sessions — "
    "open a new Grok session on the other account instead."
)
#: v1.413.0: how each tool that signs in BY ITSELF does it (the account
#: chip's title on the page says THIS tool's way, never Pi's for OpenCode).
SIGN_IN_HINTS: dict[str, str] = {
    "pi": ("Pi signs in by itself, per model provider: to change it, open Pi in a Build terminal and "
           "type /login there. Iron Jarvis's accounts do not apply."),
    "opencode": ("OpenCode signs in by itself, per provider: to change it, run opencode auth login in a "
                 "Build terminal. Iron Jarvis's accounts do not apply."),
    "antigravity": ("Antigravity signs in by itself, with its own Google sign-in: to change it, run agy in a "
                    "Build terminal and sign in there. Iron Jarvis's accounts do not apply."),
}
#: v1.439.0: Antigravity signs in by itself (its own Google sign-in); it is no
#: Iron-Proxy vendor, so a session has no account to choose or move to.
ANTIGRAVITY_OWN_SIGN_IN = (
    "Antigravity signs in by itself, with its own Google sign-in, so an Antigravity session has no "
    "account to choose — to change its sign-in, run agy in a Build terminal and sign in there."
)
#: v1.443.0: Antigravity's own sign-in has not finished (its onboarding
#: file), so its model list is not read yet.
ANTIGRAVITY_NOT_SIGNED_IN = (
    "Antigravity isn't signed in yet. Open a Build terminal, run agy and sign in there; "
    "its models then show here."
)


def antigravity_models_unread(said: str) -> str:
    """v1.443.0: Antigravity was asked for its models and listed none."""
    return f"Antigravity did not list its models: {said}"


ANTIGRAVITY_CONTINUE = (
    "Antigravity signs in by itself, so it has no other account to continue on — "
    "choose another model instead."
)
#: v1.439.0: ``session/new`` answered "Authentication required" (measured).
#: v1.441.0 (review A4): no "send your message again" here — the error line
#: it becomes already ends "Fix that, then send your message again."
ANTIGRAVITY_SIGN_IN = (
    "Antigravity isn't signed in — open a Build terminal, run agy and sign in there"
)
#: v1.441.0 (review A1): Antigravity takes no second prompt while its running
#: one waits on the user (measured on agy 1.5.2: "the running prompt is
#: waiting for a permission or question answer"), so a note is refused BEFORE
#: anything is sent — the page puts the words back in the box.
ANTIGRAVITY_NOTE_WHILE_ASKING = (
    "Antigravity is waiting for your answer on the card above — answer it first, then send your note."
)
#: v1.441.0 (review A1): the backstop — a note Antigravity refused anyway
#: (a card came in while the note went out). The reply goes on.
ANTIGRAVITY_NOTE_REFUSED = (
    "Antigravity did not take your note while it waited for your answer — "
    "it goes back in the box when this reply ends."
)
#: v1.441.0 (review A3): a question form with no question Iron Jarvis can
#: show (no ``q1``…): declined, and said — never silent.
ANTIGRAVITY_QUESTION_UNSHOWN = (
    "Antigravity asked something this view cannot show, so the question was skipped — "
    "to answer it, open this session with Open in terminal and ask Antigravity again there."
)
#: v1.441.0 (review X4): the Build terminals the Code view can take over.
NOT_A_CODING_TERMINAL = (
    "This terminal is not running Claude Code, Codex, Pi, OpenCode, Grok or Antigravity."
)
#: v1.439.0: a Deny ENDS Antigravity's reply (measured: it makes no model
#: call after a declined command).
ANTIGRAVITY_DECLINED_ENDS = (
    "You declined, so Antigravity stopped this reply — tell it what to do differently in your next message."
)
ANTIGRAVITY_NO_SUCH_EFFORT = (
    'This Antigravity model does not take the thinking level "{effort}", so it runs on its own default.'
)
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
#: v1.441.0: the user's own OpenCode agents as modes.
OPENCODE_NO_SUCH_AGENT = (
    "OpenCode has no agent \u201c{agent}\u201d in this folder, so this session uses its build agent."
)
OPENCODE_UNKNOWN_AGENT = (
    "OpenCode has not listed an agent \u201c{agent}\u201d — choose one from the mode menu."
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
#: v1.422.0: a Codex conversation moves too (``hosted/codex_move``); this is
#: said only for a tool the service does not know.
CODEX_CONTINUE = (
    "Continue on another account works for Claude Code and Codex sessions — "
    "open a new session on the other account instead."
)
THIS_PC = (
    "This session runs on this PC's login. Start a session on one of your accounts "
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
#: v1.426.0 (Wave 6, E3): Open in terminal pressed while the conversation is
#: already open in a Build terminal (pressed twice, or another terminal runs
#: it) — a second terminal would be a second writer on one conversation.
ALREADY_IN_TERMINAL = (
    "This conversation is already open in a Build terminal — use that terminal, or exit "
    "{label} there first (type {exit}) before you open it again."
)
#: v1.426.0: what a session is doing while Open in terminal hands it over
#: (said by :data:`CLAIMED` to a second press meanwhile).
CLAIM_TERMINAL = "being opened in a terminal"
STOPPED_BECAUSE = "Stopped because the session is {why}."
STOPPED_PLAIN = "Stopped: the coding tool was closed."
#: v1.427.2: steps a coding tool was running ON ITS OWN (after its reply ended:
#: helpers, a reply it started itself) when it or Iron Jarvis stopped. Each
#: step's row says "did not finish" (status ``stopped``); this says why, once.
STEPS_CUT_RESTART = (
    "Iron Jarvis closed while {label} was working on its own, so {steps} did not finish. "
    "Send a message to carry on."
)
STEPS_CUT_STOPPED = (
    "{label} stopped while it was working on its own, so {steps} did not finish. "
    "Send a message to carry on."
)


def steps_words(n: int) -> str:
    """'1 step' / '3 steps'."""
    return "1 step" if n == 1 else f"{n} steps"
CODEX_MODE_NEXT = (
    "Codex keeps its current mode for the message it is working on; {mode} applies from your "
    "next message."
)
MODE_FROM_SESSION_ALLOW = "{label} now runs in {mode} for the rest of this session (you chose Allow for this session)."
#: v1.441.0: Codex's Plan.
CODEX_PLAN_KEPT = "Codex stays in Plan. Say what to change, and it plans again."
#: v1.441.0 (integration I5): Plan names a model to Codex; one Codex would
#: not tell (an older session the app kept running through an update, whose
#: model was never picked, and ``thread/read`` gave none).
CODEX_PLAN_NO_MODEL = (
    "Codex did not say which model this conversation runs on, so it was not put in its own Plan for this "
    "message (file changes are still refused). Pick a model in the model chip, then send it again."
)
CODEX_LEAVE_PLAN_NO_MODEL = (
    "Codex did not say which model this conversation runs on, so it could not be taken out of Plan for this "
    "message: it may still only plan. Pick a model in the model chip, then send it again."
)
CODEX_PLAN_NO_EDIT = "In Plan, Codex wanted to change {file}; nothing was changed. Approve its plan to let it start."
#: v1.439.0 (review): an approved plan leaves Plan for the mode it was in
#: before (Claude Code's own rule); the row and the chip follow.
MODE_AFTER_PLAN = "{label} left Plan for your approved plan and now runs in {mode}."
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
    # v1.474.0 (Hand off): ``{q}`` is the piece of work (``task``).
    "code_handoff_start": ('Hand work to another coding tool: "{q}"', "Hand work to another coding tool"),
    "code_handoff_check": ("Check on a helper coding tool", "Check on a helper coding tool"),
    "code_handoff_stop": ("Stop a helper coding tool", "Stop a helper coding tool"),
    "code_handoff_list": ("List the coding tools that can help", "List the coding tools that can help"),
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
        a = args if isinstance(args, dict) else {}
        q = one_line(a.get("query") or a.get("task") or "", 80)
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
            HostedToolError(label(harness), str(technical or "")), provider=PROVIDERS.get(harness, ""),
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


# --------------------------------------------------------------------------- #
# v1.417.0 (fix plan wave 1: C1.1, C4.1, C4.7, C4.9)
# --------------------------------------------------------------------------- #
class HostedToolError(RuntimeError):
    """A coding tool's failure, carrying the tool's NAME (``tool_label``) so
    ``providers.failure_words`` heads it "<Claude Code> couldn't finish this
    answer" — never "Iron Jarvis", whose fault it is not (C4.9)."""

    def __init__(self, tool_label: str, text: str = "") -> None:
        super().__init__(text)
        self.tool_label = str(tool_label or "")


#: C1.1: Claude Code's own (per-prefix) session-wide allow, beside "Allow all
#: commands this session" — the card's button and its explanation say this.
ALLOW_SIMILAR = "Allow similar commands"
#: C1.1: the answer asked of a card that does not offer it.
ALL_COMMANDS_NOT_HERE = (
    "{label} offers no \"Allow all commands this session\" for this one — allow it once, or for this session."
)
#: C1.1: the answer asked of a tool that has no such answer (Pi, OpenCode, and
#: Codex when it offers no session-wide allow for this command).
ALL_COMMANDS_NOT_OFFERED = (
    "{label} has no way to allow every command for the session from here — allow this one once."
)
#: C4.1: Claude Code's ExitPlanMode card (the plan itself is shown under it).
PLAN_CARD_TITLE = "Start working on this plan"


def start_notice(harness: str, *, why: str = "", resumed: bool = False, idle_minutes: int = 15) -> str:
    """C4.7: what the view says when a process (re)starts for a message — a
    cold start used to show the same spinner with no word. ``why``: why the
    last process went ("idle" | "evicted" | "restart" | "")."""
    name = label(harness)
    if why == "restart":
        return f"Restarting {name} with your new settings…"
    if why == "idle":
        return f"Resuming {name} after {idle_minutes} minutes idle…"
    if why == "evicted":
        return f"Starting {name} again (it was stopped to make room for another session)…"
    if resumed:
        return f"Resuming {name}…"
    return f"Starting {name}…"


# --------------------------------------------------------------------------- #
# v1.418.0 (C4.6, C4.8): history that stays, and messages that wait
# --------------------------------------------------------------------------- #
#: C4.8: a message queued when no reply runs any more (the page sends it as a
#: message instead).
QUEUE_NOT_WORKING = "{label} is not working on anything right now — send it as a message."
#: C4.8: the queue is full.
QUEUE_FULL = "{n} messages are already waiting — send this one once {label} has taken them."
#: C4.8: a queued message the daemon could not send when the reply ended.
QUEUE_NOT_SENT = "Your waiting message was not sent: {why} It is back in your message box."
#: C4.6: a "Load earlier" cursor this daemon did not write.
BAD_CURSOR = "That place in the conversation is not one Iron Jarvis knows — load the session again."
#: C4.6: the item asked for is not in this session's record.
NO_SUCH_ITEM = "That step is not in this session's record any more."


# --------------------------------------------------------------------------- #
# v1.418.0 (C4.2, C4.3): what Claude Code says about its own work
# --------------------------------------------------------------------------- #
#: C4.2: a helper step's title (Claude Code's Task / Agent tool), with the
#: tool's own short description of the job.
HELPER_TITLE = "Helper: {what}"
HELPER_TITLE_PLAIN = "Hand a task to a helper"
#: C4.3: Claude Code's ``status: compacting`` (sent every 30 s while it lasts:
#: said once) and a compaction that failed (its own reason).
SYS_COMPACTING = "Claude Code is compacting the conversation to make room…"
SYS_COMPACT_FAILED = "Claude Code could not compact the conversation: {why}"
#: C4.3: a hook Claude Code ran (its own log line's words: "Hook <name>
#: (<event>) <outcome>").
SYS_HOOK = "Hook {name} ({event}) {outcome}{said}"
HOOK_OUTCOMES = {"success": "ran", "error": "failed", "cancelled": "was cancelled", "blocking": "blocked the step"}
#: C4.3: a hook that stopped Claude Code from carrying on (its stop hook summary).
SYS_STOP_HOOK = "A hook stopped Claude Code from carrying on{why}"
#: C4.3: a task Claude Code runs in the background, and how one ended (its
#: own summary).
SYS_TASK_STARTED = "Claude Code started a background task: {what}"
SYS_TASK_DONE = {
    "completed": "A helper finished: {summary}",
    "failed": "A helper failed: {summary}",
    "killed": "A helper was stopped: {summary}",
    "stopped": "A helper was stopped: {summary}",
}
#: C4.3: an action Claude Code refused on its own (Auto's safety check, a rule).
SYS_REFUSED = "Claude Code refused {tool}: {why}"
#: C4.3: a model-service error Claude Code is retrying.
SYS_API_ERROR = "Claude Code got an error from the model service ({error}) and is trying again{attempt}."
#: C4.3: a request Claude Code sends that Iron Jarvis does not answer — it is
#: refused, and the screen says what was asked.
CONTROL_REFUSED = "Claude Code asked Iron Jarvis to {what}; Iron Jarvis does not answer that here, so it was refused."
CONTROL_ASKS = {
    "hook_callback": "answer one of its hooks",
    "mcp_message": "pass a message to one of its added tools",
    "elicitation": "fill in a form for one of its added tools",
    "request_user_dialog": "show you one of its own dialogs",
}
CONTROL_ASK_OTHER = "answer a request it calls {subtype}"
#: v1.420.0 (C1.10): an "Allow for this session" answer given before a mode
#: change started a new OpenCode, applied again by Iron Jarvis (said once).
OPENCODE_ALWAYS_CARRIED = (
    "Your earlier \"Allow for this session\" answers still apply in this conversation, so OpenCode "
    "was not asked again for those."
)
#: v1.420.0 (C1.10): the user's own rule for every tool decides, and Plan
#: still changes no file.
OPENCODE_RULES_DECIDE_PLAN = (
    "Your OpenCode config sets a permission rule for every tool, so that rule decides in this "
    "session; Plan still changes no file."
)


def _model_words(harness: str, rows: Any, model: str) -> str:
    """A model's name the way the picker shows it (its row's label; the
    tool's default by name; else the id)."""
    if not model:
        return f"{label(harness)}'s default"
    want = model
    if harness == "claude-code" and model.lower() in ("opus", "sonnet", "haiku", "fable"):
        # Claude Code's own short name: the ONE listed model of that family
        # (the picker's rule, dashboard ModelPicker.aliasTarget).
        fam = f"claude-{model.lower()}-"
        hits = [r for r in rows or [] if isinstance(r, dict) and str(r.get("id", "")).lower().startswith(fam)]
        if len(hits) == 1:
            want = hits[0].get("id")
    for r in rows or []:
        if isinstance(r, dict) and r.get("id") == want and r.get("label"):
            return str(r["label"])
    return model


def _effort_words(effort: str) -> str:
    return effort[:1].upper() + effort[1:] if effort else "Default"


def model_change_words(harness: str, rows: Any, old_model: str, new_model: str,
                       old_effort: str, new_effort: str, when: str = "",
                       effort_when: str | None = None) -> str:
    """v1.475.0: the conversation's one line for a model or effort change.
    ``when`` is the model change's ``applied`` word ("now" | "next" | ""),
    ``effort_when`` the effort's (default: the same as ``when``)."""
    effort_when = when if effort_when is None else effort_when
    model = old_model != new_model
    effort = old_effort != new_effort
    if not (model or effort):
        return ""
    m = (f"Model changed: {_model_words(harness, rows, old_model)} → "
         f"{_model_words(harness, rows, new_model)}")
    e = f"{_effort_words(old_effort)} → {_effort_words(new_effort)}"
    nxt = ", from your next message"
    if model and effort and (when == "next") != (effort_when == "next"):
        # They apply at different times: each says its own.
        return (f"{m}{nxt if when == 'next' else ''}. "
                f"Effort changed: {e}{nxt if effort_when == 'next' else ''}.")
    parts = ([m] if model else []) + ([f"{'effort' if model else 'Effort changed'}: {e}"] if effort else [])
    return "; ".join(parts) + (nxt if (when if model else effort_when) == "next" else "") + "."
