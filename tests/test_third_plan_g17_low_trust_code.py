"""Third north-star fix plan, group 17, PG3 (decision K3): a low run does not
run model-written code on this computer with the network open.

THE DEFECT. Once a run has READ flagged text (a taint), or was started low by
its door (a phone message), it loses the web tools and the record-changing
tools, and its ``shell`` must run in the isolated sandbox (the lanes add
``_isolate``). ``process_start`` is kept away for exactly that reason. But
``run_code`` (``tools/runcode.py``) and ``code_run`` (``codelab/tools.py``)
were on no list and get no isolation: both start a native interpreter through
``core/procs`` with the network open. ``repl`` (``tools/repl_tool.py``) was
suspected; it is proven here: the tool runs its code in the session's
NON-isolated worker (``ReplRegistry.execute`` with ``isolated=False``), which
can open a socket and write in the working folder.

THE RULE. ``core/trust.CODE_DENY`` holds the three; ``deny_names`` adds them
for every low run (door or taint), and ``low_trust_overrides`` denies them by
default. They are refused BY NAME, with a sentence that says the shell tool
is not kept away. ``LOW_TRUST_DENY`` itself is unchanged, so Auto's "always
ask" list and the dashboard's low-trust words do not move, and an ordinary
run runs all three exactly as before.

Proved through the layer that refuses: the real runtime and both real chat
lanes, the real registry's invoke, the real permission engine; only the model
is a stand-in. The code tools' ``execute`` is REAL: the marker file is the
evidence that code ran.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.core import auto_approve as AA
from iron_jarvis.core import trust as T
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes.chat import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_trust_posture_v1298 import (
    INJECTION,
    _app,
    _complete_scripted,
    _definition,
    _drain,
    _events,
    _invocations,
    _row,
    _run,
    _scripted,
    _stub_execute,
)

CODE = ("run_code", "code_run", "repl")


def _allow(platform, *names: str) -> None:
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, **{n: "allow" for n in names}}
    )


def _writer(marker) -> str:
    """Python that writes ``marker`` and opens (and closes) a socket: the
    file says code ran, the socket says the network was open to it."""
    return (
        "import os, socket\n"
        "s = socket.socket()\n"
        "s.close()\n"
        f"open({str(marker)!r}, 'w').write('ran, socket opened')\n"
        f"print('PG3 RAN at ' + os.path.abspath({str(marker)!r}))\n"
    )


#: The REPL writes only inside its working folder, so its marker is relative.
REPL_MARKER = "pg3_repl_marker.txt"


def _saved_script(platform, marker) -> str:
    """A saved script (what ``code_run`` re-runs) that writes ``marker``."""
    store = platform.registry.get("code_run")._store
    rec = store.save("pg3_marker", "python", _writer(marker), description="writes a marker")
    return rec.id


def _repl_spy(platform) -> list[str]:
    """Record every piece of code that reaches the REPL workers (the spy
    calls the real registry: nothing is stubbed)."""
    seen: list[str] = []
    real = platform.repl.execute

    # The tool reads this signature to decide what it passes (``workspace``,
    # ``timeout``), so the spy spells them out.
    async def spy(session_id, code, *, workspace, timeout=30.0, **kw):
        seen.append(code)
        return await real(session_id, code, workspace=workspace, timeout=timeout, **kw)

    platform.repl.execute = spy  # type: ignore[method-assign]
    return seen


def _call(name: str, marker, platform, cid: str = "x1") -> ToolCall:
    if name == "run_code":
        args = {"language": "python", "code": _writer(marker), "purpose": "write a marker"}
    elif name == "code_run":
        args = {"id": _saved_script(platform, marker)}
    else:
        args = {"code": _writer(REPL_MARKER)}
    return ToolCall(id=cid, name=name, arguments=args)


def _refusal(name: str, tainted: bool) -> str:
    head = (
        f"this run is in low trust after reading flagged content — {name} is kept away"
        if tainted
        else f"this run is in low trust — {name} is kept away"
    )
    return (
        head
        + ", because it runs code on this computer with the network open. The shell "
        "tool is not kept away: it runs commands in the isolated sandbox."
    )


# --------------------------------------------------------------------------- #
# the lists
# --------------------------------------------------------------------------- #


def test_the_code_tools_are_registered_and_kept_apart_from_the_door_list(tmp_path):
    _app_, platform, _ = _app(tmp_path)
    assert set(T.CODE_DENY) == set(CODE)
    for name in CODE:
        tool = platform.registry.get(name)
        assert tool is not None, name
        assert tool.perm_key() == name
    # Every low run loses them, door or taint ...
    assert set(CODE) <= T.deny_names(False) and set(CODE) <= T.deny_names(True)
    merged = T.low_trust_overrides({n: "allow" for n in CODE})
    assert all(merged[n] == "deny" for n in CODE)
    # ... and LOW_TRUST_DENY itself does not move (Auto's always-ask list and
    # the dashboard's low-trust words read it).
    assert not (set(CODE) & set(T.LOW_TRUST_DENY))
    assert not (set(CODE) & AA._standing_tools())


def test_an_ordinary_run_is_judged_by_auto_exactly_as_before():
    """Auto's verdict on a code call in a FULL run is unchanged: the code
    rule, then the judge — never the low-trust "standing" ask."""
    for name in CODE:
        args = {"code": "print(1)", "language": "python", "id": "x"}
        v = AA.classify(name, args, mode=AA.AUTO)
        assert v.rule != "standing", (name, v)
    assert AA.classify("run_code", {"code": "print(1)", "language": "python"}, mode=AA.AUTO).rule == AA.JUDGE_RULE


# --------------------------------------------------------------------------- #
# the agent runtime
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", CODE)
async def test_an_ordinary_run_still_runs_the_code(tmp_path, name):
    """CONTROL: a full run that read nothing flagged runs it, natively, with
    the network open (the marker says so)."""
    _app_, platform, orch = _app(tmp_path)
    _allow(platform, name)
    seen = _repl_spy(platform)
    marker = tmp_path / f"{name}_full.txt"
    sid, _ = await _run(orch, platform, trust=None, definition=_definition(name),
                        rounds=[[_call(name, marker, platform)]])
    assert _row(platform.engine, sid).trust == "full"
    row = [r for r in _invocations(platform.engine, sid) if r.tool == name][-1]
    assert row.ok is True, row.output
    out = str(row.output or "")
    assert "PG3 RAN at " in out, out
    written = out.split("PG3 RAN at ", 1)[1].splitlines()[0].strip()
    # The REPL's marker is in the run's working folder, which outlives the
    # run; the others are at the absolute path the code was given.
    assert Path(written).read_text() == "ran, socket opened"
    assert len(seen) == (1 if name == "repl" else 0)


@pytest.mark.parametrize("name", CODE)
async def test_a_tainted_run_never_runs_the_code(tmp_path, name):
    _app_, platform, orch = _app(tmp_path)
    _allow(platform, "recall", "shell", name)
    _stub_execute(platform, "recall", output=INJECTION)
    shelled = _stub_execute(platform, "shell", output="hi")
    seen = _repl_spy(platform)
    marker = tmp_path / f"{name}_tainted.txt"
    rounds = [
        [ToolCall(id="r1", name="recall", arguments={"query": "notes"})],
        [_call(name, marker, platform, "c1"), ToolCall(id="s1", name="shell", arguments={"command": "echo hi"})],
    ]
    sid, _ = await _run(orch, platform, trust=None, definition=_definition("recall", "shell", name),
                        rounds=rounds)
    assert _row(platform.engine, sid).tainted_at is not None
    assert not marker.exists(), f"a tainted run ran {name}"
    assert seen == [], f"a tainted run's code reached a REPL worker: {seen}"
    row = [r for r in _invocations(platform.engine, sid) if r.tool == name][-1]
    assert row.ok is False
    assert row.output == _refusal(name, True)
    kinds = [e.get("kind") for e in _events(platform.engine, EventType.TOOL_DENIED, sid)]
    assert kinds.count("low trust") == 1, kinds
    # What the sentence says still works, works: the shell ran, isolated.
    assert shelled == [{"command": "echo hi", "_isolate": True}]


@pytest.mark.parametrize("name", CODE)
async def test_a_run_low_from_its_door_never_runs_the_code(tmp_path, name):
    _app_, platform, orch = _app(tmp_path)
    _allow(platform, name)
    seen = _repl_spy(platform)
    marker = tmp_path / f"{name}_door.txt"
    sid, _ = await _run(orch, platform, trust="low", definition=_definition(name),
                        rounds=[[_call(name, marker, platform)]])
    assert _row(platform.engine, sid).trust == "low"
    assert not marker.exists(), f"a low run ran {name}"
    assert seen == []
    row = [r for r in _invocations(platform.engine, sid) if r.tool == name][-1]
    assert row.ok is False and row.output == _refusal(name, False)


# --------------------------------------------------------------------------- #
# both chat lanes
# --------------------------------------------------------------------------- #


async def _chat(platform, monkeypatch, lane: str, body: ChatBody, rounds, **kw):
    if lane == "post":
        _complete_scripted(platform, monkeypatch, rounds)
        await run_chat_turn(platform, {}, body, **kw)
    else:
        platform.router.stream = _scripted(rounds)
        await _drain(await stream_chat_turn(platform, {}, body, **kw))


@pytest.mark.parametrize("lane", ["post", "stream"])
@pytest.mark.parametrize("name", ["run_code", "code_run"])
async def test_both_chat_lanes_run_the_code_in_a_full_turn(tmp_path, monkeypatch, lane, name):
    """CONTROL for the lanes below: the same turn at full trust runs it."""
    _app_, platform, _ = _app(tmp_path)
    marker = tmp_path / f"{name}_{lane}_full.txt"
    body = ChatBody(messages=[{"role": "user", "content": "run the script"}], tools=[name])
    await _chat(platform, monkeypatch, lane, body, [[_call(name, marker, platform)]])
    assert marker.read_text() == "ran, socket opened"


@pytest.mark.parametrize("lane", ["post", "stream"])
@pytest.mark.parametrize("name", CODE)
async def test_both_chat_lanes_refuse_the_code_after_a_taint(tmp_path, monkeypatch, lane, name):
    _app_, platform, _ = _app(tmp_path)
    _stub_execute(platform, "recall", output=INJECTION)
    seen = _repl_spy(platform)
    marker = tmp_path / f"{name}_{lane}_tainted.txt"
    body = ChatBody(messages=[{"role": "user", "content": "recall then run the script"}],
                    tools=["recall", name])
    rounds = [
        [ToolCall(id="c1", name="recall", arguments={"query": "notes"})],
        [_call(name, marker, platform, "c2")],
    ]
    await _chat(platform, monkeypatch, lane, body, rounds)
    assert not marker.exists(), f"a tainted chat turn ran {name}"
    assert seen == []
    row = [r for r in _invocations(platform.engine, "chat") if r.tool == name][-1]
    assert row.ok is False and row.output == _refusal(name, True)


@pytest.mark.parametrize("lane", ["post", "stream"])
@pytest.mark.parametrize("name", CODE)
async def test_both_chat_lanes_refuse_the_code_low_from_the_door(tmp_path, monkeypatch, lane, name):
    _app_, platform, _ = _app(tmp_path)
    seen = _repl_spy(platform)
    marker = tmp_path / f"{name}_{lane}_door.txt"
    # A second tool stays armed: a low turn is no longer SHOWN the code tool
    # (it is taken off the list), and a turn with nothing armed ends before a
    # call is judged. The model's call of the unshown tool is still refused.
    body = ChatBody(messages=[{"role": "user", "content": "run the script"}], tools=["read_file", name])
    await _chat(platform, monkeypatch, lane, body, [[_call(name, marker, platform)]],
                trust="low", trust_reason="started from an inbound tg message")
    assert not marker.exists(), f"a low chat turn ran {name}"
    assert seen == []
    row = [r for r in _invocations(platform.engine, "chat") if r.tool == name][-1]
    assert row.ok is False and row.output == _refusal(name, False)
