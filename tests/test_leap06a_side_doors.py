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
"""

from __future__ import annotations

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
    "channel_send": re.compile(r"\b(?:ch|chan\w*|channel\w*)\.send" + _USE),
    "http_post": re.compile(r"\.\s*_?http_post" + _USE),
    "http_client": re.compile(
        r"\bhttpx\.(?:post|get|put|patch|delete|request|stream|AsyncClient|Client)\b"
        r"|\brequests\.(?:post|get|put|patch|delete|request)\b"
        r"|\baiohttp\.ClientSession\b|\burlopen\b"
    ),
    "smtp": re.compile(r"\bsmtplib\.SMTP(?:_SSL)?\b"),
    "remote_agent": re.compile(r"\bRemoteAgentRegistry\("),
    "chat_post": re.compile(r"(?<!def )\bdeliver_session_line" + _USE),
    "process": re.compile(
        r"\b_?procs\.(?:spawn|run|run_to_files|run_async|adopt)" + _USE
        # Review 6: ``procs`` under any name. The import is the door: a file
        # that imports it is reviewed, and a start through it is one more.
        + r"|\bfrom\s+(?:\.+|iron_jarvis\.)core\s+import\s+(?:\([^)]*|[^\n(]*)\bprocs\b"
        r"|\bfrom\s+(?:\.+|iron_jarvis\.)core\.procs\s+import\b"
        r"|\bimport\s+iron_jarvis\.core\.procs\b"
        r"|\bfrom\s+subprocess\s+import\b"
        r"|\bsubprocess\.\w+|\bcreate_subprocess_(?:exec|shell)\b"
        r"|\bos\.(?:system|popen|startfile|spawn\w*|exec\w*)\b"
    ),
    "sandbox": re.compile(r"(?i)\b\w*sandbox\w*\.(?:run|run_async|execute|exec_command)" + _USE),
    "browser_open": re.compile(r"\bwebbrowser\.open"),
    # Review 6: the shape CLAUDE.md asks async code to use, a blocking send
    # handed to a worker, whatever the receiver is called
    # (``to_thread(self._slack.send, …)``, ``run_in_executor(None,
    # remote.run, …)``, ``Thread(target=x.post)``). A hand-off whose callable
    # a kind above already names counts there, not here (``scan_doors``).
    "handed_off": re.compile(
        r"(?:\b(?:to_thread|run_in_executor|partial|call_soon(?:_threadsafe)?|call_later|submit)"
        r"\(\s*(?:[\w.]+\s*,\s*)?|\btarget\s*=\s*)"
        r"(?:[\w.]|\[[^\]\n]*\])*\.(?:send\w*|post|notify\w*|_?http_post|spawn\w*|request|run\w*|deliver\w*)\b"
    ),
}

#: The kind that only counts what no other kind names.
_FALLBACK_KIND = "handed_off"

#: Every direct door the scan finds, REVIEWED: ``(file, kind) -> (how many,
#: why a rehearsal never goes through it)``. A new one, or one more of a
#: kind in a file, fails until it is reviewed here: either guarded like a
#: ``SIDE_DOORS`` entry (one rehearsed row, nothing done), or shown to be out
#: of a rehearsal's reach.
REVIEWED_DOORS: dict[tuple[str, str], tuple[int, str]] = {
    ("agents/orchestrator.py", "chat_post"): (
        1, "the chat delivery line: held in a rehearsal (SIDE_DOORS chat_delivery)"),
    ("agents/orchestrator.py", "process"): (
        1, "imports core.procs only to mark an unattended run as background work "
           "(procs.background_lane); it starts no program, and a start added through it is a "
           "second hit here"),
    ("agents/runtime.py", "process"): (
        1, "imports core.procs only to end a finished run's own programs (_procs.end_session); "
           "it starts none, and a start added through it is a second hit here"),
    ("agents/delegate_tool.py", "remote_agent"): (
        1, "a helper's hand-off to a remote agent: held in a rehearsal (SIDE_DOORS remote_handoff)"),
    ("agents/remote.py", "remote_agent"): (
        1, "the delegate_remote tool's own call: a tool that changes things, so the registry holds it"),
    ("agents/remote.py", "http_client"): (
        3, "the remote agent's request, its reply's files and its answer: reached only through "
           "the remote_agent doors listed here, each held or out of a rehearsal's reach"),
    ("agents/roster.py", "remote_agent"): (
        1, "reads the list of remote agents for the roster; sends nothing"),
    ("agents/task_door.py", "http_client"): (
        1, "the answer to a task another computer sent: only a run of origin remote_task with its "
           "reply address; no rehearsal's door makes one, Run again and a follow-up drop the address"),
    ("agents/threads.py", "remote_agent"): (
        2, "a round table's remote speaker: reached from a person's post in a room "
           "(the Agents page, the phone), never from a run"),
    ("comm/base.py", "http_client"): (
        2, "a phone channel's transport: reached through Notifier.notify (listed) or a reply "
           "to the person's own message"),
    ("comm/base.py", "http_post"): (
        3, "a phone channel's transport (the same as above); the third is a look at the "
           "transport's signature (_accepts_headers), not a send"),
    ("comm/channels.py", "smtp"): (
        1, "the email channel's transport: reached through Notifier.notify (listed)"),
    ("comm/compose.py", "smtp"): (
        2, "an email the person writes on the Email page (daemon/routes/comm.py); no run reaches it"),
    ("comm/inbound.py", "channel_send"): (
        10, "replies to the person's own phone message (_dispatch, send_chunked, a phone-started "
            "job's result) and the notices about one a restart lost or cut off: reached only from "
            "a message the phone sent, a run of origin comm:<channel>, or a remote agent's line "
            "coming home to a phone thread. A rehearsal's run is reflex:<rule> or "
            "schedule:<name>, it hands nothing to a remote agent (both hand-offs are held), and "
            "a rehearsal the phone asks for answers in that reply with its report's words "
            "(rehearsal_runs._report_words)"),
    ("comm/notifier.py", "channel_send"): (
        1, "Notifier.notify's own fan-out to each destination (its callers are listed)"),
    ("comm/notifier.py", "notify"): (
        1, "the bus alerts: say nothing for a rehearsal's run (comm/notifier.is_rehearsal_run)"),
    ("comm/prompts.py", "channel_send"): (
        1, "a question or an approval the phone can answer: a held call never asks "
           "(it records would ask), and a workflow cannot be rehearsed yet"),
    ("comm/slack_socket.py", "channel_send"): (
        1, "the note to a person who mentioned Jarvis in a shared Slack channel that the answer "
           "comes in the DM: only from that person's own Slack message"),
    ("comm/slack_socket.py", "http_client"): (
        2, "opens Slack's socket for the person's incoming messages; sends no run's words"),
    ("comm/tools.py", "notify"): (
        1, "the notify tool's own send: a tool that changes things, so registry.invoke holds it "
           "in a rehearsal (test_leap06a_gate)"),
    ("motivation/engine.py", "notify"): (
        1, "the morning briefing's push: only the person's own press on the autonomy route "
           "(POST briefing with notify); the engine's bus handler only files a suggestion"),
    ("platform.py", "notify"): (
        1, "a schedule's REAL delivery line (_deliver_outcome), reached only through _fire; a "
           "rehearsal goes through _rehearse_scheduled to _dispatch_scheduled and never _fire "
           "(SIDE_DOORS schedule_delivery lists the line instead)"),
    ("platform.py", "handed_off"): (
        1, "the pre-run script handed to a worker (_knobs.run_pre_run_script): "
           "_dispatch_scheduled reaches it only outside a rehearsal (SIDE_DOORS pre_run_script, "
           "the branch just above it)"),
    ("platform.py", "http_client"): (
        4, "client wiring at start-up, called only by its owner: the Connections sign-in's token "
           "exchange and refresh (no message, no change), the outgoing webhook's transport (skips "
           "every event of a rehearsal's run), and the Notion and custom memory bases' clients "
           "(read through the memory tools; a write is held)"),
    ("reflex/commands.py", "remote_agent"): (
        2, "the phone's /agents and /ask commands, typed by the person; no run reaches them"),
    ("reflex/router.py", "remote_agent"): (
        1, "a rule's REAL fire to a remote agent; a rehearsal goes through ReflexRouter.rehearse "
           "(SIDE_DOORS remote_agent) and never here"),
    ("triggers/calendar.py", "http_client"): (
        1, "the calendar feed's own poll, which reads the person's calendar to fire its rules; "
           "a rehearsal starts from a made-up sample event (tools/rehearsal_runs) and never polls"),
    ("scheduling/knobs.py", "sandbox"): (
        1, "the pre-run script: _dispatch_scheduled runs it only outside a rehearsal "
           "(SIDE_DOORS pre_run_script)"),
    ("webhooks/outbound.py", "http_post"): (
        1, "an outgoing webhook: skips every event of a rehearsal's run (SIDE_DOORS outbound_webhook)"),
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


def scan_doors(root: Path = SRC) -> dict[tuple[str, str], list[int]]:
    """``(file, kind) -> [line, …]`` of every direct door in :data:`SCANNED`."""
    found: dict[tuple[str, str], list[int]] = {}
    for entry in SCANNED:
        paths = [root / entry] if entry.endswith(".py") else sorted((root / entry).rglob("*.py"))
        assert all(p.is_file() for p in paths), f"{entry}: the scan lost a file it must read"
        for path in paths:
            rel = path.relative_to(root).as_posix()
            code = _code_only(path.read_text(encoding="utf-8").replace("\r\n", "\n"))
            named = sorted(
                m.start() for kind, rx in DOOR_KINDS.items() if kind != _FALLBACK_KIND
                for m in rx.finditer(code)
            )
            for kind, rx in DOOR_KINDS.items():
                lines = [
                    code.count("\n", 0, m.start()) + 1 for m in rx.finditer(code)
                    # A hand-off of a callable another kind names is counted there.
                    if kind != _FALLBACK_KIND or not any(m.start() <= s < m.end() for s in named)
                ]
                if lines:
                    found[(rel, kind)] = lines
    return found


def test_every_direct_send_and_process_start_was_reviewed():
    """Plan 06-a, Risk: "a side door nobody listed". A new direct send or
    process start in the automation packages fails here until it is either
    guarded (a ``SIDE_DOORS`` entry) or shown to be out of a rehearsal's
    reach, and written into :data:`REVIEWED_DOORS`."""
    found = scan_doors()
    unreviewed = []
    for key, lines in sorted(found.items()):
        known, _why = REVIEWED_DOORS.get(key, (0, ""))
        if len(lines) > known:
            unreviewed.append(f"{key[0]} ({key[1]}): {len(lines)} found, {known} reviewed, lines {lines}")
    assert not unreviewed, (
        "A direct send or process start nobody reviewed for rehearsals (leap 06-a). Guard it "
        "(tools/rehearsal.SIDE_DOORS: one rehearsed row, nothing done) or show a rehearsal "
        "cannot reach it, then add it to REVIEWED_DOORS:\n" + "\n".join(unreviewed)
    )
    for key, (count, why) in REVIEWED_DOORS.items():
        assert count >= 1 and len(why.split()) >= 5, f"{key}: a reviewed door needs its reason"


def test_the_scan_sees_a_new_door_and_not_a_mention(tmp_path):
    """The scan's own check: a new ``notify(`` in a rule's code is found; the
    same words in a comment or a docstring are not. Review 4: so is a new
    door in the one top-level FILE it reads (``platform.py``)."""
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
    # A process start added to a schedule's own door, in platform.py.
    (tmp_path / "platform.py").write_text(
        "async def _dispatch_scheduled(task):\n"
        "    # subprocess.run(x) in a comment is not one\n"
        "    subprocess.run(['x'])\n",
        encoding="utf-8",
    )
    assert scan_doors(tmp_path)[("platform.py", "process")] == [3]


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
