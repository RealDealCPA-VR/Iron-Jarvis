"""Leap 06-a: the side doors — effects that never pass ``registry.invoke``.

``tools/rehearsal.SIDE_DOORS`` lists every one a rehearsal of a rule or a
schedule can reach: a schedule's pre-run script and its delivery line, the
chat delivery, the post-run lessons, a remote agent's hand-off. Each one, in
a rehearsal, records ONE rehearsed row and does nothing. This file scans the
code each names for its guard, then drives each through the real daemon.

It also scans the automation packages, and ``platform.py`` where a
schedule's own door lives, for EVERY direct send and process start (plan
06-a, Risk: "a side door nobody listed"): each one is in a reviewed table
with why a rehearsal never goes through it, and a new one fails until it is
reviewed. Review 4: a helper's piece in a rehearsal leaves no mark on the
model team's record either. Review 6: the scan also sees a sending callable
passed BY REFERENCE (``asyncio.to_thread(ch.send, text)``,
``functools.partial(hooks.http_post, url)``), the shape async code here uses.
Review 7: and a send whatever its receiver is called (``dest.send(text)``,
``to_thread(lambda: self._slack.send(t))``, ``client.post(url)``), and a
request through the app's own client (``core/http``), whose import is a door.
Review 8: and every USE of a name a door binds, the way a start through the
procs import is one more: a sending function imported by name or defined in
the file (``to_thread(httpx_post, url)``, ``_send_once(…)``), a name from the
sandbox, whose import is now a door too (``NativeSandbox(policy).run(cmd)``),
and a tool executed outside the registry, which skips the rehearsal's hold
(``registry.get("notify").execute(args, ctx)``).
Review 9: and a private sender with a suffix (``self._post_json(url)``, never
``self._post_run_learning``, which is "after the run"), a sending function
called through a module attribute (``cb.httpx_post(url)``), a start through
the loop itself (``loop.subprocess_exec``), and a post into a chat through
any of the app's chat writers (``report_to_chat``, ``say_in_chat``,
``append_to_chat_thread``). Each count is EXACT: a door added fails until it
is reviewed, and a door removed, or a scan narrowed, fails until the count
comes down, so no reviewed count leaves room for a new door. And each
alternative of each pattern is pinned by one line only it sees, each
exclusion by one line it keeps out (:data:`_SHAPES`), so no pattern here is
pinned by nothing.

THE SCAN IS A TRIPWIRE FOR THE COMMON SHAPES, NOT A PROOF. It reads text
with patterns, and a pattern can always be dodged. Shapes it is known NOT to
see (reviews 8 and 9 found none of them reaching a real send or process
start in the scanned code):

- a call through ``getattr`` with the name in a string;
- a second call through a local name bound to a sender (``f = ch.send``
  counts once, at the binding);
- a call of a helper whose name does not say it sends (``_deliver(…)``:
  its body's own send is counted where it is, if that file is scanned);
- a sending function with another name called through a module attribute
  (``cb.deliver(url)``; ``cb.httpx_post`` and ``x.send_*`` ARE seen);
- an HTTP verb other than post on a client this file did not make
  (``self._client.request("POST", url)``, ``self._client.put(url)``): seeing
  them would also count every ``queue.put`` and ``db.delete``;
- anything in a package that is not in :data:`SCANNED`.

The real guarantee is the registry gate: ``registry.invoke`` holds every
call of a tool that is not READONLY in a rehearsal, proven through the layer
that refuses (``tests/test_leap06a_gate.py``), and each listed side door is
driven through the real daemon below. This scan only makes a NEW effect
outside the registry, in a common shape, fail until somebody reviews it.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import re
import time
import tokenize
from pathlib import Path

import pytest
from sqlmodel import select

import iron_jarvis
from iron_jarvis.agents import chat_delivery as _chat_delivery
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools import rehearsal_runs as RR

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]

SRC = Path(iron_jarvis.__file__).parent


def _quiet_stream():
    async def fake_stream(**kw):
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.router.stream = _quiet_stream()
    sent: list = []
    p.notifier.notify = lambda *a, **k: sent.append(a)  # type: ignore[method-assign]
    return app, p, sent


def _rows(p, session_id: str, tool: str) -> list[ToolInvocation]:
    with session_scope(p.engine) as db:
        out = list(db.exec(select(ToolInvocation).where(
            ToolInvocation.session_id == session_id, ToolInvocation.tool == tool,
        )))
        for r in out:
            db.expunge(r)
        return out


async def _settled(p, rid: str) -> dict:
    deadline = time.monotonic() + 60
    view = await asyncio.to_thread(RR.report, p, rid)
    while view["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        view = await asyncio.to_thread(RR.report, p, rid)
    return view


def test_every_side_door_is_guarded_where_the_table_says():
    for door, spec in R.SIDE_DOORS.items():
        text = (SRC / spec.where).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert f'door="{door}"' in text, f"{door}: no rehearsal guard in {spec.where}"


# --------------------------------------------------------------------------- #
# The scan: every direct send and process start in the automation packages
# --------------------------------------------------------------------------- #

#: The packages a rule's or a schedule's run, and what follows it, reach.
#: Review 4: and the one FILE that holds a schedule's own door,
#: ``platform.py`` (``_dispatch_scheduled``, the pre-run script, the knobs and
#: the schedule's real delivery line), plus the packages that fire a rule
#: (``triggers``) or push a notice of their own (``motivation``). An entry
#: ending in ``.py`` is one file; any other is a package.
SCANNED = ("reflex", "scheduling", "workflows", "webhooks", "comm", "agents",
           "triggers", "motivation", "platform.py")

#: The end of a sending callable's name, CALLED or passed BY REFERENCE
#: (review 6): ``ch.send(text)`` and ``asyncio.to_thread(ch.send, text)``,
#: ``functools.partial(hooks.http_post, url)``, a callback in a dict. Only
#: a place that SETS the attribute is not one (``self.http_post = …``,
#: ``self._http_post: HttpPost = …``).
_USE = r"\b(?!\s*(?::(?!=)|=(?!=)))"

#: A direct effect outside ``registry.invoke``: a message or a request that
#: leaves the PC, a post into a chat, a program started. Matched in CODE
#: only (strings and comments are blanked first, so a docstring that names
#: one is not one).
DOOR_KINDS: dict[str, re.Pattern[str]] = {
    "notify": re.compile(r"\.notify" + _USE),
    "channel_send": re.compile(r"\b(?:ch|chan\w*)\.send" + _USE),
    "http_post": re.compile(r"\.\s*_?http_post" + _USE),
    "http_client": re.compile(
        r"\bhttpx\.(?:post|get|put|patch|delete|request|stream|AsyncClient|Client)\b"
        r"|\brequests\.(?:post|get|put|patch|delete|request)\b"
        r"|\baiohttp\.ClientSession\b|\burlopen\b"
        r"|\bwebsockets\.(?:connect|serve)\b|\bsocket\.(?:socket|create_connection)\b"
        # Review 7: the app's own shared client (``core/http``: ``client()``,
        # ``async_client()``, ``get()``) under any name: the import is the
        # door, the way the procs import is. So is a library imported under
        # another name, and the phone channels' transports imported by name.
        r"|\bfrom\s+(?:\.+|iron_jarvis\.)core(?:\.http\s+import\b|\s+import\s+(?:\([^)]*|[^\n(]*)\bhttp\b)"
        r"|\bimport\s+iron_jarvis\.core\.http\b"
        r"|\bimport\s+(?:httpx|requests|aiohttp|websockets|socket)\s+as\b"
        r"|\bfrom\s+(?:httpx|requests|aiohttp|websockets|socket|urllib\.request)\s+import\b"
        r"|\bfrom\s+[\w.]+\s+import\s+(?:\([^)]*|[^\n(]*)\bhttpx_(?:post|get)\b"
        # Review 9: those transports called through a module attribute
        # (``cb.httpx_post(url)``, ``comm.httpx_get(url)``).
        r"|\.\s*httpx_(?:post|get)\b"
    ),
    "smtp": re.compile(r"\bsmtplib\.SMTP(?:_SSL)?\b"),
    "remote_agent": re.compile(r"\bRemoteAgentRegistry\("),
    # A post into a chat. Review 9: through any of the app's chat writers,
    # the report writer (idea 39) and the one writer under them too, each
    # mention in code (its import is one, the way the procs import is).
    "chat_post": re.compile(
        r"(?<!def )\b(?:deliver_session_line|report_to_chat|say_in_chat|append_to_chat_thread)" + _USE
    ),
    "process": re.compile(
        r"\b_?procs\.(?:spawn|run|run_to_files|run_async|adopt)" + _USE
        # Review 6: ``procs`` under any name. The import is the door: a file
        # that imports it is reviewed, and a start through it is one more.
        + r"|\bfrom\s+(?:\.+|iron_jarvis\.)core\s+import\s+(?:\([^)]*|[^\n(]*)\bprocs\b"
        r"|\bfrom\s+(?:\.+|iron_jarvis\.)core\.procs\s+import\b"
        r"|\bimport\s+iron_jarvis\.core\.procs\b"
        r"|\bfrom\s+subprocess\s+import\b"
        # Review 9: and the loop's own start (``loop.subprocess_exec``).
        r"|\bsubprocess\.\w+|\b(?:create_)?subprocess_(?:exec|shell)\b"
        r"|\bos\.(?:system|popen|startfile|spawn\w*|exec\w*)\b"
    ),
    "sandbox": re.compile(
        r"(?i)\b\w*sandbox\w*\.(?:run|run_async|execute|exec_command)" + _USE
        # Review 8: the sandbox under any name. The import is the door, the
        # way the procs import is, and each use of a name it brings is one
        # more (``_name_doors``): ``NativeSandbox(policy).run(cmd)``.
        # (``from iron_jarvis.sandbox… import`` is counted by the last one.)
        + r"|\bfrom\s+\.+sandbox\b[\w.]*\s+import\b"
        r"|\bfrom\s+(?:\.+|iron_jarvis)\s+import\s+(?:\([^)]*|[^\n(]*)\bsandbox\b"
        r"|\biron_jarvis\.sandbox\b"
    ),
    "browser_open": re.compile(r"\bwebbrowser\.open"),
    # Review 6: the shape CLAUDE.md asks async code to use, a blocking send
    # handed to a worker, whatever the receiver is called
    # (``to_thread(self._slack.send, …)``, ``run_in_executor(None,
    # remote.run, …)``, ``Thread(target=x.post)``). A hand-off whose callable
    # a kind above already names counts there, not here (``scan_doors``).
    # (A hand-off of ``x.http_post`` is not named here: ``http_post`` above
    # always counts it first.)
    "handed_off": re.compile(
        r"(?:\b(?:to_thread|run_in_executor|partial|call_soon(?:_threadsafe)?|call_later|submit)"
        r"\(\s*(?:[\w.]+\s*,\s*)?|\btarget\s*=\s*)"
        r"(?:[\w.]|\[[^\]\n]*\])*\.(?:send\w*|post|notify\w*|spawn\w*|request|run\w*|deliver\w*)\b"
    ),
    # Review 7: any send or post, whatever the receiver is called, called
    # directly, in a lambda or passed on (``dest.send(text)``,
    # ``to_thread(lambda: self._slack.send(t))``, ``client.post(url)``,
    # ``smtp.send_message(msg)``, ``self.send_chunked(ch, …)``), and a sending
    # function imported by name. A name that only starts so is not one
    # (``msg.sender_id``), nor is setting the attribute. Review 8: a private
    # one IS one (``self._send_reply(t)``, a channel's ``self._post(url)``),
    # and review 9: with a suffix too (``self._post_json(url)``);
    # ``self._post_run_learning`` is "after the run", not a post.
    "direct_send": re.compile(
        r"\.\s*(?:_?send(?:all|mail|_\w+)?|post(?:_\w+)?|_post(?!_run_)(?:_\w+)?)" + _USE
        + r"|\.\s*notify_\w+\s*\("
        r"|\bfrom\s+[\w.]+\s+import\s+(?:\([^)]*|[^\n(]*)\b_?send(?:_\w+)?\b"
    ),
    # Review 8: a tool executed directly skips ``registry.invoke`` and so the
    # rehearsal's hold (``registry.get("notify").execute(args, ctx)``,
    # ``SandboxedShellTool().execute(…)``), called or passed on. A SQL
    # statement on the app's own database (``db.execute(stmt)``) is not one.
    "tool_execute": re.compile(
        r"(?<!\bdb)(?<!\bconn)(?<!\bconnection)(?<!\bcur)(?<!\bcursor)(?<!\bsession)\.execute" + _USE
    ),
}

#: The kinds that count only what no kind before them names, in this order:
#: every other kind first, then a hand-off, then any other send, then a
#: tool's own execute. A hand-off of ``ch.send`` counts once, under
#: ``channel_send``; ``self.sandbox.execute(…)`` once, under ``sandbox``.
_FALLBACK_KINDS = ("handed_off", "direct_send", "tool_execute")

#: Review 8: a NAME bound to a door, and every use of it. An import the
#: kinds above call a door binds names (``from ..sandbox.native import
#: NativeSandbox``, ``from ..comm import httpx_post``, ``import httpx as
#: _h``), and so does a sending function this file defines (``def
#: _send_once``; not a method, which is reached through an attribute that
#: ``direct_send`` counts). Each use of such a name, called or passed on
#: (``NativeSandbox(policy).run(cmd)``, ``to_thread(httpx_post, url)``,
#: ``_send_once(…)``, ``http_post=httpx_post``), is one more door of that
#: kind, unless a kind above already counts it there (``procs.run(…)``).
#: ``(module it comes from, the name, or None for any name, kind)``, the
#: module absolute; a function this file defines comes from ``""``.
_NAME_DOORS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None, str], ...] = (
    (re.compile(r"iron_jarvis\.core\.procs|subprocess"), None, "process"),
    (re.compile(r"iron_jarvis\.core"), re.compile(r"procs"), "process"),
    (re.compile(r"iron_jarvis\.sandbox(?:\.\w+)*"), None, "sandbox"),
    (re.compile(r"iron_jarvis"), re.compile(r"sandbox"), "sandbox"),
    (re.compile(r"iron_jarvis\.core\.http|httpx|requests|aiohttp(?:\.\w+)*|websockets(?:\.\w+)*"
                r"|socket|urllib\.request"), None, "http_client"),
    (re.compile(r"iron_jarvis\.core"), re.compile(r"http"), "http_client"),
    (re.compile(r".*"), re.compile(r"httpx_(?:post|get)"), "http_client"),
    (re.compile(r".*"), re.compile(r"_?send(?:_\w+)?"), "direct_send"),
)

#: Every direct door the scan finds, REVIEWED: ``(file, kind) -> (how many,
#: why a rehearsal never goes through it)``. A new one, or one more of a
#: kind in a file, fails until it is reviewed here: either guarded like a
#: ``SIDE_DOORS`` entry (one rehearsed row, nothing done), or shown to be out
#: of a rehearsal's reach.
REVIEWED_DOORS: dict[tuple[str, str], tuple[int, str]] = {
    ("agents/chat_delivery.py", "chat_post"): (
        3, "the one chat writer under each line (append_to_chat_thread): deliver_session_line's, "
           "reached only through Orchestrator._deliver_to_chat_sync, held in a rehearsal "
           "(SIDE_DOORS chat_delivery); say_in_chat's, a round table's own line (agents/threads.py); "
           "and _write_report's, under report_to_chat, whose callers are a schedule's _fire, a "
           "workflow, a goal, a watcher and a night task, none of them a rehearsal's path"),
    ("agents/orchestrator.py", "chat_post"): (
        1, "the chat delivery line: held in a rehearsal (SIDE_DOORS chat_delivery); since idea 39 "
           "it is also the report of a schedule set up from a chat, which a rehearsal lists instead "
           "(test_a_chat_made_schedules_rehearsal_reports_nothing_into_the_chat)"),
    ("agents/orchestrator.py", "direct_send"): (
        2, "the answer to a task another computer sent (task_door.send_reply_in_thread and "
           "send_reply_soon, the same reply as task_door.py's): started only when "
           "task_door.reply_spec_of finds the run's reply address, which only a run of origin "
           "remote_task has; no rehearsal's door makes one, Run again and a follow-up drop it"),
    ("agents/orchestrator.py", "process"): (
        2, "imports core.procs only to mark an unattended run as background work "
           "(procs.background_lane, its one use); it starts no program, and a start added "
           "through it is a third hit here"),
    ("agents/runtime.py", "process"): (
        2, "imports core.procs only to end a finished run's own programs (_procs.end_session, "
           "its one use, handed to a worker); it starts none, and a start added through it is "
           "a third hit here"),
    ("agents/runtime.py", "sandbox"): (
        2, "imports host_os_line from the sandbox and calls it once: the one line naming the "
           "PC's system for the model's prompt (platform.system()); it starts no program"),
    ("agents/delegate_tool.py", "remote_agent"): (
        1, "a helper's hand-off to a remote agent: held in a rehearsal (SIDE_DOORS remote_handoff)"),
    ("agents/remote.py", "remote_agent"): (
        1, "the delegate_remote tool's own call: a tool that changes things, so the registry holds it"),
    ("agents/remote.py", "http_client"): (
        3, "the remote agent's request, its reply's files and its answer: reached only through "
           "the remote_agent doors listed here, each held or out of a rehearsal's reach"),
    ("agents/remote.py", "direct_send"): (
        1, "the remote agent's request itself (client.post on the client counted under "
           "http_client): reached only through the remote_agent doors listed here"),
    ("agents/roster.py", "remote_agent"): (
        1, "reads the list of remote agents for the roster; sends nothing"),
    ("agents/task_door.py", "http_client"): (
        1, "the answer to a task another computer sent: only a run of origin remote_task with its "
           "reply address; no rehearsal's door makes one, Run again and a follow-up drop the address"),
    ("agents/task_door.py", "direct_send"): (
        6, "that answer's own post (client.post on the client counted under http_client), and "
           "the five calls and hand-offs in this file that lead to it (send_reply to _send_once, "
           "send_reply_soon to send_reply or send_reply_in_thread, _start_send): the same reply, "
           "only for a run of origin remote_task with its reply address (reply_spec_of)"),
    ("agents/threads.py", "remote_agent"): (
        2, "a round table's remote speaker: reached from a person's post in a room "
           "(the Agents page, the phone), never from a run"),
    ("agents/threads.py", "chat_post"): (
        2, "a round table's line in its chat that a remote agent's answer did not come "
           "(end_overdue_waits: say_in_chat imported, then handed to a worker): reached from a "
           "person's post in a room, never from a run"),
    ("agents/threads.py", "tool_execute"): (
        1, "the Guide seat's lookup of the person's own things (AppSearchTool, read-only: "
           "app_search finds a schedule or a rule by name) for its reply in a round table: "
           "reached from a person's post in a room, never from a run, and it changes nothing"),
    ("comm/__init__.py", "http_client"): (
        1, "re-exports the phone channels' transports (httpx_post, httpx_get) by name; sends "
           "nothing"),
    ("comm/base.py", "http_client"): (
        4, "a phone channel's transport: reached through Notifier.notify (listed) or a reply "
           "to the person's own message; and (10-a) the download of a file the person sent "
           "from the phone (httpx_get_bytes, a GET that stops at the cap), reached only from "
           "the phone poller's _take_files on that person's own message, after the allowlist: "
           "it carries no run's words, and a rehearsal's run is never phone-started; and "
           "(10-b) the upload of a file Jarvis made for the person's own phone message "
           "(httpx_post_file), reached only through send_file from the phone poller's files "
           "back (listed under comm/inbound.py)"),
    ("comm/base.py", "direct_send"): (
        1, "10-b: Channel.send_files' default loop over send_file, reached only from the phone "
           "poller's _send_files_now: after the answer to the person's own phone message or a "
           "phone-started run, or on their Send under the files question. A rehearsal's run is "
           "never phone-started, and its held writes are in no phone turn's files"),
    ("comm/base.py", "http_post"): (
        3, "a phone channel's transport (the same as above); the third is a look at the "
           "transport's signature (_accepts_headers), not a send"),
    ("comm/channels.py", "smtp"): (
        1, "the email channel's transport: reached through Notifier.notify (listed)"),
    ("comm/channels.py", "direct_send"): (
        13, "the email channel's send_message on that same SMTP line and the phone channels' "
            "own transport inside their send (self._post: Slack's two, Discord's, Telegram's), "
            "reached through Notifier.notify (listed) or a reply to the person's own message, "
            "and Telegram's one-line notice that it cannot read a photo, sent only while "
            "polling, to the person who sent the photo in their own private chat. 10-b: a "
            "file's upload (Slack's upload-address and complete steps and the upload, "
            "Telegram's sendPhoto or sendDocument, email's send_file), reached only from the "
            "phone poller's files back (listed under comm/inbound.py); and a pressed button's "
            "answer (Slack's chat.update, Telegram's answerCallbackQuery and "
            "editMessageReplyMarkup), only for the person's own press under their own question"),
    ("comm/compose.py", "smtp"): (
        2, "an email the person writes on the Email page (daemon/routes/comm.py); no run reaches it"),
    ("comm/compose.py", "direct_send"): (
        2, "that email's send_message on the same SMTP line, and run_compose's call of "
           "send_mail that leads to it: only from the Email page"),
    ("comm/inbound.py", "channel_send"): (
        11, "replies to the person's own phone message (_dispatch, send_chunked, a phone-started "
            "job's result) and the notices about one a restart lost or cut off: reached only from "
            "a message the phone sent, a run of origin comm:<channel>, or a remote agent's line "
            "coming home to a phone thread. A rehearsal's run is reflex:<rule> or "
            "schedule:<name>, it hands nothing to a remote agent (both hand-offs are held), and "
            "a rehearsal the phone asks for answers in that reply with its report's words "
            "(rehearsal_runs._report_words). 10-b: the files question with its buttons "
            "(_ask_files_back), asked only after such a reply"),
    ("comm/inbound.py", "direct_send"): (
        21, "the same replies through the poller's own send_chunked (counted once more where it "
            "calls ch.send): each answers a message the phone sent (a command, a new thread, "
            "the rate limit, a refusal, an escalation, a job's start, an answer to a question, a "
            "remote agent's answer to the person's line, the sentence that the files a message "
            "brought were not used: _say_files_not_read, R0.3) or a phone-started run's result; "
            "a rehearsal's run is never phone-started. 10-b: the files back after such a reply "
            "or on the person's Send (_files_back, _send_files_now twice, the line for what "
            "did not go twice, the files question without buttons twice) and a reply held "
            "under a press (_handle_press): the files a phone chat turn made are read off that "
            "turn's own documents, never a rehearsal's held run"),
    ("comm/inbound.py", "handed_off"): (
        1, "10-b: _send_files_now hands ch.send_files to a worker: the files back listed above, "
           "only after the answer to the person's own phone message or on their Send"),
    ("comm/integrations.py", "http_client"): (
        3, "build_notifier's default transports (httpx_post, httpx_get): imported, then handed "
           "to the phone channels it builds when the caller passed none; sends nothing itself"),
    ("comm/notifier.py", "channel_send"): (
        2, "Notifier.notify's own fan-out to each destination (its callers are listed), on one "
           "line: with an approval's Approve and Deny buttons (10-b) or without"),
    ("comm/notifier.py", "notify"): (
        2, "the bus alerts: say nothing for a rehearsal's run (comm/notifier.is_rehearsal_run); "
           "the second (10-b) is the same approval alert with Approve and Deny under it, and a "
           "rehearsal never asks: registry.invoke holds a call that would ask as \"would ask "
           "first\", never parked, so no approval is requested"),
    ("comm/prompts.py", "channel_send"): (
        1, "a question or an approval the phone can answer: a held call never asks "
           "(it records would ask), and a workflow cannot be rehearsed yet"),
    ("comm/prompts.py", "direct_send"): (
        1, "a parked workflow's question to the phone (handle_workflow_waiting, on the "
           "workflow.waiting event, through _send_chunked_sync): a workflow cannot be "
           "rehearsed yet (06-b), so no rehearsal parks one"),
    ("comm/slack_socket.py", "channel_send"): (
        1, "the note to a person who mentioned Jarvis in a shared Slack channel that the answer "
           "comes in the DM: only from that person's own Slack message"),
    ("comm/slack_socket.py", "http_client"): (
        3, "opens Slack's socket for the person's incoming messages (the two socket-URL "
           "requests and websockets.connect); sends no run's words"),
    ("comm/slack_socket.py", "direct_send"): (
        1, "the socket's receipt for each incoming envelope (ws.send of its envelope_id), which "
           "Slack asks for within 3 seconds; it carries no run's words"),
    ("comm/tools.py", "notify"): (
        1, "the notify tool's own send: a tool that changes things, so registry.invoke holds it "
           "in a rehearsal (test_leap06a_gate)"),
    ("comm/voice_note.py", "process"): (
        2, "10-a: imports core.procs and starts the offline speech worker (audio_worker, "
           "procs.run) for a voice note the person sent from the phone; reached only from the "
           "phone poller (comm/inbound.py: _take_files, and the yes that hears the waiting "
           "notes), as is its fallback to the speech service the person chose: a rehearsal's "
           "run is never phone-started and has no voice note"),
    ("motivation/engine.py", "notify"): (
        1, "the morning briefing's push: only the person's own press on the autonomy route "
           "(POST briefing with notify); the engine's bus handler only files a suggestion"),
    ("platform.py", "chat_post"): (
        1, "a schedule's report into the chat it was set up from when no run of its own wrote it "
           "(_schedule_report, report_to_chat, idea 39): called only from _fire; a rehearsal goes "
           "through _rehearse_scheduled to _dispatch_scheduled and never _fire, and its run's own "
           "report is the held chat delivery (SIDE_DOORS chat_delivery)"),
    ("platform.py", "notify"): (
        1, "a schedule's REAL delivery line (_deliver_outcome), reached only through _fire; a "
           "rehearsal goes through _rehearse_scheduled to _dispatch_scheduled and never _fire "
           "(SIDE_DOORS schedule_delivery lists the line instead)"),
    ("platform.py", "handed_off"): (
        1, "the pre-run script handed to a worker (_knobs.run_pre_run_script): "
           "_dispatch_scheduled reaches it only outside a rehearsal (SIDE_DOORS pre_run_script, "
           "the branch just above it)"),
    ("platform.py", "http_client"): (
        7, "client wiring at start-up, called only by its owner: the phone channels' transports "
           "imported for build_notifier and handed to it (reached through Notifier.notify, "
           "listed), the "
           "Connections sign-in's token exchange and refresh (no message, no change), the "
           "outgoing webhook's transport (skips every event of a rehearsal's run), and the "
           "Notion and custom memory bases' clients (read through the memory tools; a write is "
           "held)"),
    ("platform.py", "sandbox"): (
        2, "imports the sandboxed shell tool and registers it (registry.register("
           "SandboxedShellTool())): each call of it then goes through registry.invoke, which "
           "holds a rehearsal's (test_leap06a_gate); nothing here runs a command"),
    ("reflex/commands.py", "remote_agent"): (
        2, "the phone's /agents and /ask commands, typed by the person; no run reaches them"),
    ("reflex/router.py", "remote_agent"): (
        1, "a rule's REAL fire to a remote agent; a rehearsal goes through ReflexRouter.rehearse "
           "(SIDE_DOORS remote_agent) and never here"),
    ("reflex/router.py", "tool_execute"): (
        3, "the router's own execute, not a tool's: a rule's REAL fire from a webhook, a signal "
           "or the phone's /run (on_webhook, on_signal, start); a rehearsal goes through "
           "ReflexRouter.rehearse to _run_session and never calls it"),
    ("triggers/calendar.py", "http_client"): (
        1, "the calendar feed's own poll, which reads the person's calendar to fire its rules; "
           "a rehearsal starts from a made-up sample event (tools/rehearsal_runs) and never polls"),
    ("scheduling/knobs.py", "sandbox"): (
        9, "the pre-run script (run_pre_run_script: the sandbox's four imports, the policy, the "
           "isolation check, the manager, the native check and the run): _dispatch_scheduled "
           "runs it only outside a rehearsal (SIDE_DOORS pre_run_script)"),
    ("webhooks/outbound.py", "http_post"): (
        1, "an outgoing webhook: skips every event of a rehearsal's run (SIDE_DOORS outbound_webhook)"),
    ("workflows/engine.py", "chat_post"): (
        1, "a workflow run's report into the chat it was run from (report_to_chat, idea 39): a rule "
           "or a schedule that starts a workflow cannot be rehearsed yet (06-b), so no rehearsal "
           "reaches it"),
    ("workflows/engine.py", "notify"): (
        1, "a workflow's notify step: a rule or a schedule that starts a workflow cannot be "
           "rehearsed yet (06-b), so no rehearsal reaches it"),
}


def _code_only(text: str) -> str:
    """``text`` with every string and comment blanked (line numbers kept)."""
    rows = [list(line) for line in text.splitlines(keepends=True)]
    blank = {tokenize.COMMENT, tokenize.STRING}
    if hasattr(tokenize, "FSTRING_MIDDLE"):
        blank.add(tokenize.FSTRING_MIDDLE)
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type not in blank:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        for r in range(sr, er + 1):
            row = rows[r - 1]
            for i in range(sc if r == sr else 0, min(ec if r == er else len(row), len(row))):
                if row[i] != "\n":
                    row[i] = " "
    return "".join("".join(r) for r in rows)


def _absolute(rel: str, node: ast.ImportFrom) -> str:
    """The module ``node`` imports from, absolute: ``..sandbox.native`` in
    ``scheduling/knobs.py`` is ``iron_jarvis.sandbox.native``."""
    if not node.level:
        return node.module or ""
    package = ["iron_jarvis", *rel.split("/")[:-1]]
    return ".".join([*package[: len(package) - node.level + 1], *([node.module] if node.module else [])])


def _name_kind(module: str, name: str) -> str | None:
    for mod, member, kind in _NAME_DOORS:
        if mod.fullmatch(module) and (member is None or member.fullmatch(name)):
            return kind
    return None


def _name_doors(rel: str, text: str) -> list[tuple[str, int, int]]:
    """``(kind, start, end)`` in ``text`` of every use of a name bound to a
    door (:data:`_NAME_DOORS`): read or called, never where it is bound."""
    tree = ast.parse(text)
    methods = {id(n) for c in ast.walk(tree) if isinstance(c, ast.ClassDef) for n in c.body}
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _absolute(rel, node)
            for a in node.names:
                if kind := _name_kind(module, a.name):
                    bound[a.asname or a.name] = kind
        elif isinstance(node, ast.Import):
            # ``import httpx as _h``; a plain ``import httpx`` binds the
            # library's own name, whose sends the kinds above name.
            for a in node.names:
                if a.asname and (kind := _name_kind(a.name, "")):
                    bound[a.asname] = kind
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in methods:
            if kind := _name_kind("", node.name):
                bound[node.name] = kind
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in bound:
            row = lines[node.lineno - 1]
            col = len(row.encode("utf-8")[: node.col_offset].decode("utf-8"))
            start = starts[node.lineno - 1] + col
            out.append((bound[node.id], start, start + len(node.id)))
    return sorted(out, key=lambda hit: hit[1])


def scan_doors(root: Path = SRC) -> dict[tuple[str, str], list[int]]:
    """``(file, kind) -> [line, …]`` of every direct door in :data:`SCANNED`."""
    found: dict[tuple[str, str], list[int]] = {}
    for entry in SCANNED:
        paths = [root / entry] if entry.endswith(".py") else sorted((root / entry).rglob("*.py"))
        assert all(p.is_file() for p in paths), f"{entry}: the scan lost a file it must read"
        for path in paths:
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            code = _code_only(text)
            taken: list[tuple[int, int]] = []
            hits: dict[str, list[int]] = {}
            order = [k for k in DOOR_KINDS if k not in _FALLBACK_KINDS] + list(_FALLBACK_KINDS)
            for kind in order:
                spans = [
                    m.span() for m in DOOR_KINDS[kind].finditer(code)
                    # A door a kind before this one names is counted there.
                    if kind not in _FALLBACK_KINDS
                    or not any(s < m.end() and m.start() < e for s, e in taken)
                ]
                taken += spans
                hits.setdefault(kind, []).extend(s for s, _e in spans)
            # Review 8: each use of a name a door binds, unless counted above.
            for kind, s, e in _name_doors(rel, text):
                if not any(a < e and s < b for a, b in taken):
                    taken.append((s, e))
                    hits.setdefault(kind, []).append(s)
            for kind, offsets in hits.items():
                if offsets:
                    found[(rel, kind)] = sorted(code.count("\n", 0, s) + 1 for s in offsets)
    return found


def test_every_direct_send_and_process_start_was_reviewed():
    """Plan 06-a, Risk: "a side door nobody listed". A new direct send or
    process start in the automation packages fails here until it is either
    guarded (a ``SIDE_DOORS`` entry) or shown to be out of a rehearsal's
    reach, and written into :data:`REVIEWED_DOORS`."""
    found = scan_doors()
    unreviewed, gone = [], []
    for key in sorted(set(found) | set(REVIEWED_DOORS)):
        lines = found.get(key, [])
        known, _why = REVIEWED_DOORS.get(key, (0, ""))
        if len(lines) > known:
            unreviewed.append(f"{key[0]} ({key[1]}): {len(lines)} found, {known} reviewed, lines {lines}")
        elif len(lines) < known:
            gone.append(f"{key[0]} ({key[1]}): {len(lines)} found, {known} reviewed, lines {lines}")
    assert not unreviewed, (
        "A direct send or process start nobody reviewed for rehearsals (leap 06-a). Guard it "
        "(tools/rehearsal.SIDE_DOORS: one rehearsed row, nothing done) or show a rehearsal "
        "cannot reach it, then add it to REVIEWED_DOORS:\n" + "\n".join(unreviewed)
    )
    # Review 9: the count is EXACT. Fewer found than reviewed means a door was
    # removed or the scan was narrowed; the extra count would let a NEW door
    # of that kind in that file pass unread, and its reason no longer holds.
    assert not gone, (
        "Fewer direct sends or process starts found than REVIEWED_DOORS says (leap 06-a): a "
        "door was removed or the scan stopped seeing one. If the code changed, lower the count "
        "and make its reason true again; if the scan lost a shape, put it back:\n" + "\n".join(gone)
    )
    for key, (count, why) in REVIEWED_DOORS.items():
        assert count >= 1 and len(why.split()) >= 5, f"{key}: a reviewed door needs its reason"


def test_the_scan_sees_a_new_door_and_not_a_mention(tmp_path):
    """The scan's own check: a new ``notify(`` in a rule's code is found; the
    same words in a comment or a docstring are not. Review 4: so is a new
    door in the one top-level FILE it reads (``platform.py``). Reviews 6, 7
    and 8: each shape a send or a start can take (below)."""
    pkg = tmp_path / "reflex"
    pkg.mkdir()
    for other in SCANNED[1:]:
        if other.endswith(".py"):
            (tmp_path / other).write_text("", encoding="utf-8")
        else:
            (tmp_path / other).mkdir()
    (pkg / "new_door.py").write_text(
        '"""Calls self.notifier.notify(text) and httpx.post(url)."""\n'
        "# platform.notifier.notify(message)\n"
        "def f(p, text):\n"
        "    p.notifier.notify(text)\n"
        "    return 'subprocess.run(x)'\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path) == {("reflex/new_door.py", "notify"): [4]}
    # Review 6: a sending callable handed to a worker BY REFERENCE, the shape
    # CLAUDE.md asks async code to use, is a door too; a hand-off of one a
    # kind names counts once, under that kind.
    (pkg / "new_door.py").unlink()
    (pkg / "handed_off.py").write_text(
        "import asyncio, functools\n"
        "from ..core import procs as P\n"
        "async def g(self, p, ch, hooks, text, loop):\n"
        "    await asyncio.to_thread(ch.send, text, chat_id=1)\n"
        "    await asyncio.to_thread(p.notifier.notify, text)\n"
        "    await asyncio.to_thread(functools.partial(hooks.http_post, text))\n"
        "    await asyncio.to_thread(procs.run, ['x'])\n"
        "    await asyncio.to_thread(\n"
        "        self._slack.send, text)\n"
        "    await loop.run_in_executor(None, self.remote.run, text)\n"
        "    self.http_post = hooks.http_post_fn\n"
        "    return 'asyncio.to_thread(ch.send, text)'\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path) == {
        ("reflex/handed_off.py", "process"): [2, 7],
        ("reflex/handed_off.py", "channel_send"): [4],
        ("reflex/handed_off.py", "notify"): [5],
        ("reflex/handed_off.py", "http_post"): [6],
        ("reflex/handed_off.py", "handed_off"): [8, 10],
    }
    # Review 7: a send whatever its receiver is called, called directly or in
    # a lambda, and a request through the app's own client (core/http) under
    # any name. A name that only starts so, setting the attribute, and the
    # words in a string are not doors.
    (pkg / "handed_off.py").unlink()
    (pkg / "any_receiver.py").write_text(
        "import asyncio\n"
        "from ..core import http as _http\n"
        "from ..core.http import client\n"
        "async def h(self, p, url, text, msg):\n"
        "    dest = p.notifier.get('slack')\n"
        "    dest.send(text)\n"
        "    await asyncio.to_thread(lambda: self._slack.send(text))\n"
        "    async with _http.async_client() as c:\n"
        "        await c.post(url, json={})\n"
        "    with client() as s:\n"
        "        s.send_message(text)\n"
        "    self.send = dest.sender_id\n"
        "    return 'dest.send(text)', msg.sender_id\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path) == {
        # Review 8: and each use of a name the import binds (lines 8 and 10).
        ("reflex/any_receiver.py", "http_client"): [2, 3, 8, 10],
        ("reflex/any_receiver.py", "direct_send"): [6, 7, 9, 11],
    }
    # Review 8: a sending function imported by name or defined in the file,
    # called or handed to a worker; a name from the sandbox, whose import is
    # a door too; a tool executed outside the registry. The import and the
    # def are not uses, nor a SQL statement, setting the attribute, a name
    # that only starts so, or the words in a string.
    (pkg / "any_receiver.py").unlink()
    (pkg / "by_name.py").write_text(
        "import asyncio\n"
        "from ..comm import httpx_post\n"
        "from ..comm.base import httpx_get as fetch\n"
        "from ..sandbox.native import NativeSandbox\n"
        "from ..agents.task_door import send_reply_soon as reply\n"
        "def _send_note(ch, text):\n"
        "    return None\n"
        "async def k(self, p, url, ctx, cmd, s, tool, args, db, stmt):\n"
        "    await asyncio.to_thread(httpx_post, url, {}, {})\n"
        "    fetch(url)\n"
        "    NativeSandbox(p).run(cmd)\n"
        "    reply(p, s)\n"
        "    _send_note(p, url)\n"
        "    await p.registry.get('notify').execute({'message': url}, ctx)\n"
        "    await asyncio.to_thread(tool.execute, args, ctx)\n"
        "    db.execute(stmt)\n"
        "    self.execute = tool\n"
        "    httpx_posted = s\n"
        "    return 'httpx_post(url)', httpx_posted\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path) == {
        ("reflex/by_name.py", "http_client"): [2, 3, 9, 10],
        ("reflex/by_name.py", "sandbox"): [4, 11],
        ("reflex/by_name.py", "direct_send"): [5, 12, 13],
        ("reflex/by_name.py", "tool_execute"): [14, 15],
    }
    # Review 9: a private send or post, with a suffix too, but not "after the
    # run"; a transport called through a module attribute; the loop's own
    # program start; a post into a chat through each of the chat writers,
    # its import included. Setting a private attribute is not one.
    (pkg / "by_name.py").unlink()
    (pkg / "private_send.py").write_text(
        "import asyncio\n"
        "from ..comm import base as cb\n"
        "from ..agents.chat_delivery import say_in_chat\n"
        "async def q(self, p, loop, url, text, s, chat, report, _cd, _ca):\n"
        "    self._send_reply(text)\n"
        "    self._post(url, {})\n"
        "    self._post_json(url)\n"
        "    self._post_run_learning(s)\n"
        "    await asyncio.to_thread(self._post_run_allowance, s)\n"
        "    cb.httpx_post(url, {}, {})\n"
        "    await loop.subprocess_exec('x')\n"
        "    await asyncio.to_thread(_cd.report_to_chat, p, chat, report)\n"
        "    _cd.deliver_session_line(p.engine, s)\n"
        "    _ca.append_to_chat_thread(p.engine, chat, [])\n"
        "    say_in_chat(p.engine, chat, text)\n"
        "    self._post_hook = url\n"
        "    return 'self._post(url)', s._sender\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path) == {
        ("reflex/private_send.py", "direct_send"): [5, 6, 7],
        ("reflex/private_send.py", "http_client"): [10],
        ("reflex/private_send.py", "process"): [11],
        ("reflex/private_send.py", "chat_post"): [3, 12, 13, 14, 15],
    }
    # Review 9: every other shape a pattern above names, so no pattern of the
    # scan is pinned by nothing (the real tree pins the rest through its exact
    # counts): the other libraries' calls and imports, the procs and the
    # sandbox imported whole, a name from subprocess and from the sandbox
    # package used, a program opened by the system, a page opened in the
    # browser, a notify_ helper. An unused import binds a name, not a door.
    (pkg / "private_send.py").unlink()
    (pkg / "every_shape.py").write_text(
        "import os, webbrowser\n"
        "import requests, aiohttp, socket\n"
        "import iron_jarvis.core.http\n"
        "import httpx as hx\n"
        "from websockets import serve as ws_serve\n"
        "from ..core.procs import adopt\n"
        "import iron_jarvis.core.procs\n"
        "from subprocess import Popen\n"
        "from .. import sandbox as sb\n"
        "import iron_jarvis.sandbox.native\n"
        "def r(p, url, host, path, text):\n"
        "    requests.post(url)\n"
        "    aiohttp.ClientSession()\n"
        "    socket.create_connection((host, 80))\n"
        "    Popen(['x'])\n"
        "    os.startfile(path)\n"
        "    sb.native.NativeSandbox(p)\n"
        "    webbrowser.open(url)\n"
        "    p.notifier.notify_owner(text)\n"
        "    return None\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path) == {
        ("reflex/every_shape.py", "http_client"): [3, 4, 5, 12, 13, 14],
        ("reflex/every_shape.py", "process"): [6, 7, 8, 15, 16],
        ("reflex/every_shape.py", "sandbox"): [9, 10, 17],
        ("reflex/every_shape.py", "browser_open"): [18],
        ("reflex/every_shape.py", "direct_send"): [19],
    }
    # A process start added to a schedule's own door, in platform.py.
    (tmp_path / "platform.py").write_text(
        "async def _dispatch_scheduled(task):\n"
        "    # subprocess.run(x) in a comment is not one\n"
        "    subprocess.run(['x'])\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path)[("platform.py", "process")] == [3]


#: Review 9 (c): "a pattern the scan has is pinned by nothing" is a finding,
#: so EACH alternative of each pattern above has one line here that only it
#: sees (taking it out makes the line uncounted, or counted as another kind),
#: and each exclusion one line it keeps out. ``(code, kind or None, line)``.
_SHAPES: tuple[tuple[str, str | None, int], ...] = (
    # a channel's own send, by the receiver's name
    ("chan.send(t)", "channel_send", 1),
    ("channel_slack.send(t)", "channel_send", 1),
    # the network libraries, called
    *((f"httpx.{verb}(u)", "http_client", 1)
      for verb in ("post", "get", "put", "patch", "delete", "request", "stream", "AsyncClient", "Client")),
    *((f"requests.{verb}(u)", "http_client", 1) for verb in ("post", "get", "put", "patch", "delete", "request")),
    ("aiohttp.ClientSession()", "http_client", 1),
    ("urlopen(u)", "http_client", 1),
    ("websockets.connect(u)", "http_client", 1),
    ("websockets.serve(h)", "http_client", 1),
    ("socket.socket()", "http_client", 1),
    ("socket.create_connection(a)", "http_client", 1),
    ("cb.httpx_post(u, {}, {})", "http_client", 1),
    ("cb.httpx_get(u)", "http_client", 1),
    # ... and imported (a whole import of the app's client, or under a name)
    ("from iron_jarvis.core.http import client", "http_client", 1),
    ("from ..core.http import client", "http_client", 1),
    ("from iron_jarvis.core import http", "http_client", 1),
    ("from ..core import (\n    http,\n)", "http_client", 1),
    ("import iron_jarvis.core.http", "http_client", 1),
    *((f"import {lib} as x", "http_client", 1) for lib in ("httpx", "requests", "aiohttp", "websockets", "socket")),
    *((f"from {lib} import x", "http_client", 1)
      for lib in ("httpx", "requests", "aiohttp", "websockets", "socket", "urllib.request")),
    ("from ..comm import httpx_post", "http_client", 1),
    ("from ..comm.base import httpx_get", "http_client", 1),
    ("from ..comm import (\n    httpx_post,\n)", "http_client", 1),
    # a program started, through the app's procs or the system
    *((f"procs.{fn}(x)", "process", 1) for fn in ("spawn", "run", "run_to_files", "run_async", "adopt")),
    ("_procs.run(x)", "process", 1),
    ("from iron_jarvis.core import procs", "process", 1),
    ("from ..core import (\n    procs,\n)", "process", 1),
    ("from iron_jarvis.core.procs import run", "process", 1),
    ("from ..core.procs import run", "process", 1),
    ("import iron_jarvis.core.procs", "process", 1),
    ("from subprocess import run", "process", 1),
    ("subprocess.run(x)", "process", 1),
    ("asyncio.create_subprocess_exec(x)", "process", 1),
    ("asyncio.create_subprocess_shell(x)", "process", 1),
    ("loop.subprocess_exec(x)", "process", 1),
    ("loop.subprocess_shell(x)", "process", 1),
    *((f"os.{fn}(x)", "process", 1) for fn in ("system", "popen", "startfile", "spawnl", "execv")),
    # the sandbox, called or imported
    *((f"self.sandbox.{fn}(c)", "sandbox", 1) for fn in ("run", "run_async", "execute", "exec_command")),
    ("NativeSandbox.run(c)", "sandbox", 1),
    ("from iron_jarvis.sandbox.native import x", "sandbox", 1),
    ("from ..sandbox import x", "sandbox", 1),
    ("from iron_jarvis import sandbox", "sandbox", 1),
    ("from .. import (\n    sandbox,\n)", "sandbox", 1),
    ("import iron_jarvis.sandbox", "sandbox", 1),
    # a callable handed to a worker, by every hand-off and every verb
    *((f"{hand}(self.remote.run, t)", "handed_off", 1)
      for hand in ("asyncio.to_thread", "functools.partial", "loop.call_soon", "loop.call_soon_threadsafe",
                   "pool.submit")),
    ("loop.call_later(1, self.remote.run)", "handed_off", 1),
    ("loop.run_in_executor(None, self.remote.run, t)", "handed_off", 1),
    ("threading.Thread(target=self.remote.run)", "handed_off", 1),
    ("asyncio.to_thread(self.pool['a'].run, t)", "handed_off", 1),
    *((f"asyncio.to_thread(x.{verb}, t)", "handed_off", 1)
      for verb in ("send_all", "post", "notify_owner", "spawn_child", "request", "deliver_line")),
    # any other send or post, whatever the receiver
    ("sock.sendall(b)", "direct_send", 1),
    ("smtp.sendmail(a, b, m)", "direct_send", 1),
    ("self.slack.post_message(t)", "direct_send", 1),
    ("self._post_json(u)", "direct_send", 1),
    ("p.notifier.notify_owner(t)", "direct_send", 1),
    ("from ..agents.task_door import (\n    send_reply,\n)", "direct_send", 1),
    ("same = dest.send == other", "direct_send", 1),
    ("self.send = dest.send", "direct_send", 1),
    # a post into a chat, through each writer, and a walrus is a use too
    *((f"{w}(e, c, x)", "chat_post", 1)
      for w in ("deliver_session_line", "report_to_chat", "say_in_chat", "append_to_chat_thread")),
    ("if (report_to_chat := pick()):\n    pass", "chat_post", 1),
    ("p.notifier.notify(t)", "notify", 1),
    ("hooks.http_post(u)", "http_post", 1),
    ("self._http_post(u)", "http_post", 1),
    ("smtplib.SMTP_SSL(h)", "smtp", 1),
    ("RemoteAgentRegistry(p)", "remote_agent", 1),
    ("webbrowser.open(u)", "browser_open", 1),
    ("registry.get(n).execute(a, ctx)", "tool_execute", 1),
    # NOT doors: setting or annotating the attribute, a SQL statement, a name
    # that only starts so, "after the run"
    ("p.notifier.notify = fake", None, 0),
    ("self.notify: Notifier = n", None, 0),
    ("self.send = s", None, 0),
    ("self._send: Sender = s", None, 0),
    ("self._post_hook = u", None, 0),
    ("self._post_run_learning(s)", None, 0),
    ("ok = msg.sender_id", None, 0),
    ("self.sandbox.run = f", None, 0),
    ("procs.run = f", None, 0),
    ("self._http_post: Post = f", None, 0),
    *((f"{who}.execute(stmt)", None, 0) for who in ("db", "conn", "connection", "cur", "cursor", "session")),
    ("self.execute = tool", None, 0),
    # ... for each kind that reads a use (``_USE``): setting or annotating is
    # not one, and comparing is one
    *((f"{name} = f", None, 0) for name in ("ch.send", "report_to_chat")),
    *((f"{name}: T = f", None, 0)
      for name in ("ch.send", "report_to_chat", "procs.run", "self.sandbox.run", "self.execute")),
    *((f"same = {name} == f", kind, 1)
      for name, kind in (("p.notifier.notify", "notify"), ("ch.send", "channel_send"),
                         ("hooks.http_post", "http_post"), ("report_to_chat", "chat_post"),
                         ("procs.run", "process"), ("self.sandbox.run", "sandbox"),
                         ("tool.execute", "tool_execute"))),
)


def test_each_shape_a_pattern_names_is_seen(tmp_path):
    """Review 9: every alternative of every pattern is pinned by a line only
    it sees, and every exclusion by a line it keeps out (``_SHAPES``)."""
    pkg = tmp_path / "reflex"
    pkg.mkdir()
    for other in SCANNED[1:]:
        if other.endswith(".py"):
            (tmp_path / other).write_text("", encoding="utf-8")
        else:
            (tmp_path / other).mkdir()
    wrong = []
    for code, kind, line in _SHAPES:
        (pkg / "shape.py").write_text(code + "\n", encoding="utf-8")
        want = {("reflex/shape.py", kind): [line]} if kind else {}
        got = scan_doors(tmp_path)
        if got != want:
            wrong.append(f"{code!r}: want {want}, got {got}")
    assert not wrong, "The scan sees a shape wrongly:\n" + "\n".join(wrong)


async def test_the_pre_run_script_and_the_delivery_line_do_nothing(daemon, tmp_path):
    app, p, sent = daemon
    folder = tmp_path / "work"
    folder.mkdir()
    p.scheduler.add_task(
        "Weekly export", "0 6 * * 1", kind="task",
        payload={"task": "Export the week.", "workspace_root": str(folder),
                 "script": {"command": "echo ran > marker.txt", "timeout_s": 30, "cwd": "workspace"},
                 "notify_channels": ["slack"]},
    )
    started = await RR.start(p, "schedule", "Weekly export")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert not (folder / "marker.txt").exists(), "the pre-run script ran in a rehearsal"
    words = [w["words"] for w in view["would"]]
    assert "run the schedule's pre-run script `echo ran > marker.txt`" in words
    assert "send the result to Slack" in words
    assert view["sentence"] == "This would send 1 message and run 1 command. Nothing was changed."
    await asyncio.sleep(0)
    assert sent == [], "a rehearsal delivered the schedule's line"


async def test_a_chat_made_schedules_rehearsal_reports_nothing_into_the_chat(daemon, tmp_path):
    """Idea 39 (master v1.555.0): a schedule set up from a chat reports each
    result into that chat, through the run's own ending (its options carry
    the chat and ``report_from``). A REHEARSAL of it goes through the same
    door, so its run carries them too; the chat gets nothing, and the report
    lists the line instead."""
    from iron_jarvis.core.models import ChatThreadRecord

    app, p, sent = daemon
    first = [{"role": "user", "content": "Check this supplier's prices every Friday"}]
    with session_scope(p.engine) as db:
        chat = ChatThreadRecord(title="Supplier prices", messages_json=json.dumps(first))
        db.add(chat)
        db.commit()
        db.refresh(chat)
        chat_id = chat.id
    p.scheduler.add_task("Friday prices", "0 9 * * 5", kind="task",
                         payload={"task": "Check the supplier's prices."}, chat_thread_id=chat_id)
    started = await RR.start(p, "schedule", "Friday prices")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    with session_scope(p.engine) as db:
        messages = json.loads(db.get(ChatThreadRecord, chat_id).messages_json or "[]")
    assert messages == first, "a rehearsal wrote into the chat its schedule reports to"
    assert "post the result in the chat that asked" in [w["words"] for w in view["would"]]
    await asyncio.sleep(0)
    assert sent == []


async def test_a_rehearsals_worktree_is_discarded_and_offers_no_review(daemon, monkeypatch):
    """A run on a git worktree ends with a review to merge (``build_review``).
    A rehearsal's run changed nothing, so its worktree is discarded and no
    review is offered. (The rebase review: this guard was pinned by nothing.)"""
    from iron_jarvis.agents import orchestrator as _orch

    app, p, sent = daemon
    orch = app.state.orchestrator
    built: list = []

    def no_review(*a, **k):
        built.append(a)
        raise RuntimeError("no review in this test")

    monkeypatch.setattr(_orch, "build_review", no_review)

    class _Worktree:
        branch = "ij/test"

        def __init__(self) -> None:
            self.workspace = str(p.config.home)
            self.discarded = 0

        def discard(self) -> None:
            self.discarded += 1

    for rehearsal, reviews in (("reh_worktree", 0), ("", 1)):
        session = await orch.create_session(
            "write the report", AgentType.BUILDER,
            options={"rehearsal_id": rehearsal} if rehearsal else {},
        )
        tree = _Worktree()
        orch._git_sessions[session.id] = tree
        before = len(built)
        await orch.run_session(session.id)
        assert len(built) - before == reviews, f"rehearsal={rehearsal!r}: reviews built"
        if rehearsal:
            assert tree.discarded == 1 and session.id not in orch._git_sessions


async def test_the_chat_delivery_and_the_lessons_do_nothing(daemon, monkeypatch):
    app, p, sent = daemon
    orch = app.state.orchestrator
    delivered: list = []
    monkeypatch.setattr(_chat_delivery, "deliver_session_line", lambda *a, **k: delivered.append(a) or {"ok": True})
    reflected: list = []
    monkeypatch.setattr(p.learning, "reflect", lambda *a, **k: reflected.append(a))
    session = await orch.create_session(
        "write the report", AgentType.BUILDER, chat_thread_id="thread_06a",
        options={"rehearsal_id": "reh_side"},
    )
    out = orch._deliver_to_chat_sync(session)
    assert out["why"] == "rehearsal" and delivered == []
    assert [r.would for r in _rows(p, session.id, "chat_delivery")] == ["run"]
    orch._post_run_learning(session)
    assert reflected == [], "a rehearsal kept a lesson"
    assert [r.rehearsed for r in _rows(p, session.id, "post_run_learning")] == [True]
    # Control: a real run's chat line is delivered.
    real = await orch.create_session("write the report", AgentType.BUILDER, chat_thread_id="thread_06a")
    orch._deliver_to_chat_sync(real)
    assert len(delivered) == 1


async def test_a_remote_agents_hand_off_sends_nothing(daemon, monkeypatch):
    app, p, sent = daemon
    from iron_jarvis.agents import remote as _remote

    calls: list = []
    monkeypatch.setattr(_remote.RemoteAgentRegistry, "run", lambda *a, **k: calls.append(a))
    p.reflex.add(name="To hermes", source="webhook", match="hermes", action="remote_agent",
                 target="hermes", task_template="Handle {text}", enabled=True)
    view = await RR.start(p, "rule", "To hermes")
    assert view["status"] == "ready"
    assert calls == []
    assert [w["words"] for w in view["would"]] == ["hand the task to the remote agent hermes"]
    rows = _rows(p, view["id"], "remote_agent")
    assert [(r.rehearsed, json.loads(r.args_json)["target"]) for r in rows] == [(True, "hermes")]


# --------------------------------------------------------------------------- #
# Review 4: the model team's record
# --------------------------------------------------------------------------- #


async def test_a_rehearsals_helper_leaves_no_mark_on_the_model_teams_record(tmp_path, monkeypatch):
    """A helper's piece writes a row on the model-team member's record for its
    role, and the team router picks members by that record. In a rehearsal
    the piece's writes were held, so "finished" says nothing of the member's
    work: the piece writes no row. Control: the same hand-off outside a
    rehearsal writes its row."""
    from iron_jarvis.agents import team_record as TR
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import Session
    from iron_jarvis.providers import router as router_mod
    from iron_jarvis.tools.base import ToolContext
    from tests._fakes.team_world_v1395 import LOCAL, make_world, member

    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)
    w = make_world(tmp_path, monkeypatch)
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("read",))

    def team_rows() -> list[tuple[str, str, str]]:
        with session_scope(w.p.engine) as db:
            return [(r.member, r.role, r.session_id) for r in db.exec(select(TR.TeamRoleRun))]

    async def hand_off(rehearsal_id: str) -> str:
        parent = await Orchestrator(w.p).create_session(
            "the parent's task", AgentType.SUPERVISOR, provider="google", model="gemini-3.8-flash",
            options={R.REHEARSAL_OPTION: rehearsal_id} if rehearsal_id else None,
        )
        ctx = ToolContext(workspace=tmp_path, session_id=parent.id, agent_run_id="parent-run",
                          config=w.p.config, event_bus=w.p.event_bus, engine=w.p.engine,
                          rehearsal_id=rehearsal_id)
        with R.scope(rehearsal_id):  # what registry.invoke sets around the call
            res = await w.p.registry.get("delegate").execute(
                {"role": "read", "task": "Read app.py and summarize it."}, ctx)
        assert res.ok, res.error
        assert res.data["target"] == "custom:glm"
        return res.data["child_session_id"]

    rehearsed = await hand_off("reh_team")
    with session_scope(w.p.engine) as db:
        assert R.rehearsal_of(db.get(Session, rehearsed)) == "reh_team", "the helper did not inherit"
    assert team_rows() == [], "a rehearsal's piece was written on the member's record"
    real = await hand_off("")
    assert team_rows() == [("glm", "read", real)]  # control
