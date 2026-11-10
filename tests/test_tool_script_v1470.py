"""v1.470.0: `tool_script` — one script that calls the turn's tools itself,
in an ISOLATED worker.

What is pinned here, against the REAL registry and a REAL worker process:

* a script calls a read-only tool of the turn and only what it prints comes
  back; each nested call is a ledger row of its own;
* THE GATE: a script cannot call a tool that changes something, a tool
  outside the turn's roster, a tool set to ask or to never run, a tool whose
  own spend check asks, a tool that reaches outside in a Local only chat, or
  itself — and each refusal is a sentence, not a run;
* THE ISOLATION: no network, no other program, no file written outside the
  private scratch folder, no file read outside the working folder, no secret
  in the environment, a namespace separate from the `repl` tool's;
* on Windows the Job holds "no other program" and the memory ceiling;
* a Stop (cancel) kills the worker and cancels the tool call in flight;
* the time a script waits on a tool is not counted against the script;
* chat offers the tool only for the same read done many times, and only
  beside a tool that reads.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import egress
from iron_jarvis.repl import session as S
from iron_jarvis.repl import worker as W
from iron_jarvis.tools.autoselect import wants_tool_script
from iron_jarvis.tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult
from iron_jarvis.tools import tool_script as TS
from iron_jarvis.tools.tool_script import NAME, ToolScriptTool, prewarm

pytestmark = pytest.mark.timeout(180)


class _Echo(Tool):
    """A read-only stand-in: answers with what it was asked."""

    name = "echo_read"
    description = "read-only test tool"
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    reversibility = Reversibility.READONLY

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.delay = 0.0

    async def execute(self, args, ctx):
        self.calls.append(str(args.get("q")))
        if self.delay:
            await asyncio.sleep(self.delay)
        return ToolResult(ok=True, output=f"read:{args.get('q')}", data={"q": args.get("q")})


class _Priced(_Echo):
    name = "priced_read"

    async def spend_check(self, args, ctx):
        return SpendCheck(ask=True, words="costs money")


class _Writer(Tool):
    name = "writer_tool"
    description = "changes something"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.IRREVERSIBLE

    def __init__(self) -> None:
        self.ran = 0

    async def execute(self, args, ctx):
        self.ran += 1
        return ToolResult(ok=True, output="wrote")


@pytest.fixture()
def world(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "note.txt").write_text("in the folder", encoding="utf-8")
    echo, priced, writer = _Echo(), _Priced(), _Writer()
    for t in (echo, priced, writer):
        p.registry.register(t)
    for name in ("echo_read", "priced_read", "writer_tool"):
        p.permissions._base[name] = "allow"
    ctx = ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", thread_id="chat_one",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    yield SimpleNamespace(p=p, ws=ws, ctx=ctx, echo=echo, priced=priced, writer=writer)
    asyncio.run(p.repl.dispose_all())


def _run(w, code, *, allowed=("tool_script", "echo_read"), overrides=None, **kw):
    async def go():
        return await w.p.registry.invoke(
            NAME, {"code": code, **kw}, w.ctx, w.p.permissions,
            agent_overrides=overrides, allowed_names=set(allowed),
        )

    return asyncio.run(go())


def test_it_is_registered_read_only_at_the_allow_tier(world):
    tool = world.p.registry.get(NAME)
    assert isinstance(tool, ToolScriptTool)
    assert tool.reversibility is Reversibility.READONLY
    assert tool.returns_untrusted_content is True
    assert world.p.config.permissions.get(NAME) == "allow"
    assert isinstance(tool.spec()["input_schema"], dict)
    # "Ask before every action" shows the script first.
    assert NAME in CT.STRICT_ASK_TOOLS


def test_a_script_calls_a_read_tool_and_only_what_it_prints_comes_back(world):
    r = _run(world, (
        "seen = [tools.call('echo_read', q=str(i)).output for i in range(5)]\n"
        "r = tools.echo_read(q='x')\n"
        "print(len(seen), r.data['q'], tools.names)"
    ))
    assert r.ok, r.error
    assert "5 x ('echo_read',)" in r.output
    assert "read:0" not in r.output  # the raw results stayed in the script
    assert world.echo.calls == ["0", "1", "2", "3", "4", "x"]
    assert r.data["tool_calls"] == {"echo_read": 6}
    assert "made 6 tool calls: echo_read x6" in r.output
    # Every nested call is on the ledger beside the script's own row.
    from sqlmodel import select

    with session_scope(world.p.engine) as db:
        rows = [(i.tool, i.ok) for i in db.exec(select(ToolInvocation)).all()]
    assert rows.count(("echo_read", True)) == 6
    assert (NAME, True) in rows


def test_variables_persist_between_scripts_and_the_repl_never_sees_them(world):
    assert _run(world, "kept = 41").ok
    r = _run(world, "print(kept + 1)")
    assert r.ok and "42" in r.output
    keys = list(world.p.repl._sessions)
    assert len(keys) == 1 and keys[0].endswith("#tools")

    assert keys[0].startswith("chat:chat_one@")

    async def repl_cell():
        return await world.p.repl.execute("chat", "print('kept' in globals())", workspace=world.ws)

    out = asyncio.run(repl_cell())
    assert out["ok"] and "False" in out["stdout"]
    assert len(world.p.repl._sessions) == 2
    # The registry still finds, and ends, a chat's worker by its own key.
    assert asyncio.run(world.p.repl.dispose("chat:chat_one")) is True


@pytest.mark.parametrize(
    "call, allowed, overrides, words",
    [
        ("tools.call('writer_tool')", ("tool_script", "writer_tool"), None, "changes something"),
        ("tools.call('echo_read', q='a')", ("tool_script",), None, "not one of this turn's tools"),
        ("tools.call('no_such_tool')", ("tool_script", "no_such_tool"), None, "no tool named"),
        ("tools.call('tool_script', code='1')", ("tool_script",), None, "another script"),
        ("tools.call('echo_read', q='a')", ("tool_script", "echo_read"), {"echo_read": "ask"}, "asks the user first"),
        ("tools.call('echo_read', q='a')", ("tool_script", "echo_read"), {"echo_read": "deny"}, "never run"),
        ("tools.call('priced_read', q='a')", ("tool_script", "priced_read"), None, "asks the user before this call"),
    ],
)
def test_the_gate_refuses_in_words_and_nothing_runs(world, call, allowed, overrides, words):
    r = _run(
        world,
        f"try:\n    {call}\n    print('RAN')\nexcept ToolError as e:\n    print('refused:', e)",
        allowed=allowed, overrides=overrides,
    )
    assert r.ok, r.error
    assert "refused:" in r.output and words in r.output, r.output
    assert "RAN" not in r.output
    assert world.echo.calls == [] and world.priced.calls == [] and world.writer.ran == 0
    assert r.data["tool_calls"] == {}


def test_one_chats_variables_are_never_another_chats(world):
    """Every chat turn runs under the session id "chat": keyed on that, a
    Local only chat's rows would be readable from a script in another chat."""
    import dataclasses

    assert _run(world, "client_rows = ['secret row']").ok
    other = dataclasses.replace(world.ctx, thread_id="chat_two")

    async def go(ctx, code):
        return await world.p.registry.invoke(
            NAME, {"code": code}, ctx, world.p.permissions, allowed_names={NAME},
        )

    r = asyncio.run(go(other, "print('client_rows' in globals())"))
    assert r.ok and "False" in r.output
    # A new chat's first message has no thread yet: its turn is the key.
    first = dataclasses.replace(world.ctx, thread_id="", turn_id="turn_9")
    assert TS.worker_key(first) == ("chat:turn_9", False)
    # A caller with neither gets a worker used once and ended.
    bare = dataclasses.replace(world.ctx, thread_id="", turn_id="")
    key, once = TS.worker_key(bare)
    assert once and key.startswith("chat:once-")

    async def once_run():
        r = await go(bare, "print('client_rows' in globals())")
        for _ in range(200):
            if not any(k.startswith("chat:once-") for k in world.p.repl._sessions):
                break
            await asyncio.sleep(0.05)
        return r

    r = asyncio.run(once_run())
    assert r.ok and "False" in r.output
    assert not any(k.startswith("chat:once-") for k in world.p.repl._sessions)
    # An agent run keeps its own id.
    run = dataclasses.replace(world.ctx, session_id="sess_1", thread_id="chat_one")
    assert TS.worker_key(run) == ("sess_1", False)


def test_an_uncaught_refusal_fails_the_script_with_the_sentence(world):
    r = _run(world, "tools.call('writer_tool')", allowed=("tool_script", "writer_tool"))
    assert not r.ok
    assert "ToolError" in r.error and "Call it directly" in r.error
    assert world.writer.ran == 0


def test_a_caller_that_named_no_roster_offers_no_tool(world):
    async def go():
        return await world.p.registry.invoke(
            NAME, {"code": "print(tools.names)\ntools.call('echo_read', q='a')"},
            world.ctx, world.p.permissions,
        )

    r = asyncio.run(go())
    assert not r.ok and "()" in r.error and "not one of this turn's tools" in r.error
    assert world.echo.calls == []


def test_local_only_keeps_a_tool_that_reaches_outside_out_of_a_script(world):
    assert "web_fetch" in egress.INTERNET_TOOLS

    async def go():
        with egress.local_only_scope(True):
            return await world.p.registry.invoke(
                NAME,
                {"code": "try:\n    tools.call('web_fetch', url='http://example.com')\nexcept ToolError as e:\n    print('refused:', e)"},
                world.ctx, world.p.permissions,
                allowed_names={NAME, "web_fetch"},
            )

    r = asyncio.run(go())
    assert r.ok and "Local only" in r.output, r.output or r.error


def test_tools_that_talk_to_the_user_are_never_reachable_from_a_script(world):
    tool = world.p.registry.get(NAME)
    for name in sorted(TS.NOT_FROM_A_SCRIPT):
        other = world.p.registry.get(name)
        if other is not None:
            assert tool._fixed_refusal(name, other), name
    # Every extra reader is a registered tool, and none is on the deny floor
    # or in the list of tools a low-trust run may not use.
    from iron_jarvis.core.trust import LOW_TRUST_DENY
    from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS

    for name in sorted(TS.EXTRA_READERS):
        assert world.p.registry.get(name) is not None, name
        assert name not in DENY_FLOOR_TOOLS and name not in LOW_TRUST_DENY, name
    # A tool that waits on a person or an agent is refused whatever it says.
    for name in world.p.registry.names():
        other = world.p.registry.get(name)
        if getattr(other, "deadline_exempt", False):
            assert tool._fixed_refusal(name, other), name


ISOLATION = [
    ("import socket\nsocket.socket()", "the network"),
    ("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:9/')", "the network"),
    ("import subprocess\nsubprocess.run(['cmd', '/c', 'echo hi'])", "BLOCKED"),
    ("import os\nos.system('echo hi')", "outside this script"),
    ("open(WS + '/made.txt', 'w').write('x')", "writing a file"),
    ("open(OUTSIDE).read()", "only inside the turn's working folder"),
]


@pytest.mark.parametrize("code, words", ISOLATION)
def test_the_worker_is_isolated(world, tmp_path, code, words):
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours", encoding="utf-8")
    pre = f"WS = {str(world.ws)!r}\nOUTSIDE = {str(outside)!r}\n"
    r = _run(world, pre + code)
    assert not r.ok
    assert words in r.error, r.error
    assert not (world.ws / "made.txt").exists()


def test_the_worker_reads_its_folder_writes_scratch_and_holds_no_secret(world, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-000000000000")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "abc")
    monkeypatch.setenv("IRONJARVIS_SOMETHING", "internal")
    r = _run(world, (
        "import os, tempfile, statistics, decimal, csv, json, datetime\n"
        "print(open('note.txt').read())\n"
        "f = tempfile.NamedTemporaryFile('w', delete=False); f.write('x'); f.close()\n"
        "print('scratch ok', statistics.mean([1, 2, 3]))\n"
        "bad = [k for k in os.environ if 'KEY' in k.upper() or 'TOKEN' in k.upper()]\n"
        "own = sorted(k for k in os.environ if k.upper().startswith('IRONJARVIS_'))\n"
        "print('secrets', bad, own)"
    ))
    assert r.ok, r.error
    assert "in the folder" in r.output and "scratch ok 2" in r.output
    assert "secrets [] ['IRONJARVIS_REPL_" in r.output
    assert "IRONJARVIS_SOMETHING" not in r.output


def test_a_file_in_the_folder_is_never_imported_as_a_library(world):
    (world.ws / "statistics.py").write_text("raise SystemExit('planted')\n", encoding="utf-8")
    r = _run(world, "import statistics\nprint(statistics.mean([2, 4]))")
    assert r.ok and "3" in r.output, r.error or r.output


@pytest.mark.skipif(sys.platform != "win32", reason="the Job is a Windows mechanism")
def test_windows_itself_holds_no_other_program_and_the_memory_ceiling(world):
    assert _run(world, "pass").ok
    sess = next(iter(world.p.repl._sessions.values()))
    assert sess.isolated
    assert sess.sealed == {"no_new_processes": True, "memory": True, "no_desktop": True}
    # The audit hook refuses a spawn first; the Job is what stands behind it.
    # Proven on the Job itself: it may hold no more processes than it holds.
    from iron_jarvis.core import procs

    job = procs._job_of(sess._proc)
    k = procs._kernel()
    info = k.EXTENDED()
    assert k.k32.QueryInformationJobObject(
        job.handle, procs._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        k.ctypes.byref(info), k.ctypes.sizeof(info), None,
    )
    flags = int(info.BasicLimitInformation.LimitFlags)
    assert flags & procs._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    assert flags & procs._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert not flags & procs._JOB_OBJECT_LIMIT_BREAKAWAY_OK
    assert int(info.BasicLimitInformation.ActiveProcessLimit) == len(job.member_pids())
    assert int(info.ProcessMemoryLimit) == S.ISOLATED_MEMORY_BYTES
    r = _run(world, "x = bytearray(3 * 1024 * 1024 * 1024)")
    assert not r.ok and "MemoryError" in r.error


def test_time_waiting_on_a_tool_is_not_the_scripts(world):
    world.echo.delay = 1.5
    r = _run(world, "print(tools.call('echo_read', q='slow').output)", timeout_s=1)
    assert r.ok, r.error
    assert "read:slow" in r.output


def test_a_script_that_runs_too_long_is_killed_and_says_so(world):
    r = _run(world, "while True:\n    pass", timeout_s=1)
    assert not r.ok and "Timed out" in r.error
    again = _run(world, "print('fresh')")
    assert again.ok and "fresh" in again.output and "GONE" in again.output


def test_a_stop_kills_the_worker_and_cancels_the_call_in_flight(world):
    world.echo.delay = 30.0
    cancelled = asyncio.Event()
    real = world.echo.execute

    async def watched(args, ctx):
        try:
            return await real(args, ctx)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    world.echo.execute = watched

    async def go():
        task = asyncio.ensure_future(world.p.registry.invoke(
            NAME, {"code": "tools.call('echo_read', q='held')\nprint('AFTER')"},
            world.ctx, world.p.permissions, allowed_names={NAME, "echo_read"},
        ))
        for _ in range(600):
            if world.echo.calls:
                break
            await asyncio.sleep(0.05)
        assert world.echo.calls == ["held"]
        sess = next(iter(world.p.repl._sessions.values()))
        proc = sess._proc
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 10)
        assert sess._proc is None
        for _ in range(200):
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.05)
        assert proc.poll() is not None
        for _ in range(200):
            if not sess._reaps:
                break
            await asyncio.sleep(0.05)

    asyncio.run(go())


def test_a_script_is_bounded_in_tool_calls(world, monkeypatch):
    monkeypatch.setattr(S, "MAX_TOOL_CALLS", 3)
    r = _run(world, (
        "n = 0\n"
        "try:\n"
        "    for i in range(10):\n"
        "        tools.call('echo_read', q=str(i)); n += 1\n"
        "except ToolError as e:\n"
        "    print('stopped after', n, e)"
    ))
    assert r.ok and "stopped after 3" in r.output
    assert len(world.echo.calls) == 3


def test_prewarm_starts_the_worker_before_the_first_script(world):
    async def go():
        assert prewarm(world.p, ["read_file"], world.ctx) is False
        assert prewarm(world.p, [NAME], world.ctx) is True
        for _ in range(600):
            sess = next(iter(world.p.repl._sessions.values()), None)
            if sess is not None and sess.alive and not sess.busy:
                break
            await asyncio.sleep(0.05)
        assert sess.alive and sess.executions == 0
        pid = sess.pid
        r = await world.p.registry.invoke(
            NAME, {"code": "print('warm')"}, world.ctx, world.p.permissions,
            allowed_names={NAME},
        )
        assert r.ok and "warm" in r.output and "GONE" not in r.output
        assert sess.pid == pid  # the warmed worker ran it

    asyncio.run(go())


def test_the_plain_repl_worker_is_unchanged_by_the_isolated_rules(world):
    async def go():
        return await world.p.repl.execute(
            "chat",
            "import os\nprint(os.environ.get('IRONJARVIS_REPL_ISOLATED'), 'tools' in globals())",
            workspace=world.ws,
        )

    out = asyncio.run(go())
    assert out["ok"] and "None False" in out["stdout"]


def test_the_worker_stays_standard_library_only():
    src = Path(W.__file__).read_text(encoding="utf-8")
    assert "import iron_jarvis" not in src and "from iron_jarvis" not in src
    assert "from ." not in src and "from .." not in src


# --------------------------------------------------------------------------- #
# chat offers it only for the same read done many times
# --------------------------------------------------------------------------- #

MANY = [
    "summarize every workbook in this folder",
    "for each client, pull the total from their statement",
    "check all of the invoices for a missing PO number",
    "compare the fees across the files",
    "read these 12 statements and total the deposits",
]
NOT_MANY = [
    "summarize this workbook",
    "what does the report say about fees?",
    "all good, thanks",
    "read the 2 files",
    "every day I get more email",
]


@pytest.mark.parametrize("text", MANY)
def test_the_matcher_says_yes_for_the_same_read_done_many_times(text):
    assert wants_tool_script(text, ["read_document"])
    assert not wants_tool_script(text, [])  # nothing to read with


@pytest.mark.parametrize("text", NOT_MANY)
def test_the_matcher_says_no_for_an_ordinary_request(text):
    assert not wants_tool_script(text, ["read_document"])


def test_many_attached_files_count_too():
    assert wants_tool_script("total these", ["read_document"], attachments=4)
    assert not wants_tool_script("total these", ["read_document"], attachments=3)


def test_the_matcher_is_linear_on_a_long_message():
    import time

    text = ("every " * 20000) + "file"
    started = time.perf_counter()
    wants_tool_script(text, ["read_document"])
    assert time.perf_counter() - started < 5


def _armed(p, text, **kw):
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True, **kw)
    return list(CT._resolve_armed_tools(SimpleNamespace(platform=p), body)[0])


def test_chat_arms_it_beside_a_reader_for_bulk_work_only(world):
    bulk = _armed(world.p, "read every workbook in this folder and list the totals", workspace_dir=str(world.ws))
    assert NAME in bulk and any(t != NAME for t in bulk)
    one = _armed(world.p, "read this workbook and list the totals", workspace_dir=str(world.ws))
    assert NAME not in one
    assert NAME not in _armed(world.p, "hello there")


def test_chat_never_arms_it_over_the_users_never_run(world):
    world.p.permissions._base[NAME] = "deny"
    armed = _armed(world.p, "read every workbook in this folder and list the totals", workspace_dir=str(world.ws))
    assert NAME not in armed


def test_auto_tools_off_arms_nothing(world):
    body = ChatBody(
        messages=[{"role": "user", "content": "read every workbook in this folder"}],
        auto_tools=False, workspace_dir=str(world.ws),
    )
    assert NAME not in CT._resolve_armed_tools(SimpleNamespace(platform=world.p), body)[0]


def test_the_isolated_environment_is_built_whole(world, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notreal")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = S.isolated_env(world.ws, scratch)
    assert env[W.ENV_ISOLATED] == "1"
    assert env[W.ENV_WRITE_ROOTS] == str(scratch)
    assert str(world.ws.resolve()) in env[W.ENV_READ_ROOTS].split(os.pathsep)
    assert "GITHUB_TOKEN" not in env and "PYTHONPATH" not in env
    assert env["TEMP"] == str(scratch)
