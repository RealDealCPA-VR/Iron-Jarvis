"""v1.441.0 (lane g4): Pi's own ``!command`` and ``!!command`` in the Code view.

Measured on pi 1.3.0 (``--mode rpc --no-session --offline``, a scratch
PI_CODING_AGENT_DIR, no model call; ``tests/fixtures/build_app_v14410/
pi_user_bash.json``): the RPC command ``bash {command, excludeFromContext}``
runs it in Pi's own shell, ``bash_execution_update {id, delta}`` streams its
output, and the answer is {output, exitCode, cancelled, truncated}. Pi records
a ``bashExecution`` message; with ``excludeFromContext`` (``!!``) the model
never sees it. Typed in the box, a ``!`` message used to reach the MODEL as
plain words; now it is the step Pi's own terminal would run.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import service as S

from ._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until

FIX = Path(__file__).parent / "fixtures"
PI_READ = FIX / "build_app_v1411" / "pi_read.json"
REC = {(f["type"] if f["type"] != "response" else f["id"]): f for f in
       json.loads((FIX / "build_app_v14410" / "pi_user_bash.json").read_text(encoding="utf-8"))["frames"]}


def _scenario(tmp_path: Path, extra: list[dict]) -> Path:
    steps = json.loads(PI_READ.read_text(encoding="utf-8"))["steps"] + extra
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"harness": "pi", "steps": steps}), encoding="utf-8")
    return path


def _bash_steps(answer: str, update: bool = True) -> list[dict]:
    out: list[dict] = [{"await": "command", "command": "bash"}]
    if update:
        out.append({"out": REC["bash_execution_update"], "id_of_command": "bash"})
    out.append({"out": REC[answer], "reply_to_command": "bash"})
    return out


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    from iron_jarvis.hosted import claude as CL

    monkeypatch.setattr(P, "LAST_MODELS", [])
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    return agent


def test_typed_reading():
    assert P.typed_builtin("!ls -la", []) == ("!", "ls -la")
    assert P.typed_builtin("  !! git status  ", []) == ("!!", "git status")
    assert P.typed_builtin("!", []) == ("!", "")
    assert P.typed_builtin("say !hello", []) is None
    assert P.typed_builtin("/compact", []) == ("compact", "")


async def _run(tmp_path, monkeypatch, work, extra, texts):
    log = tmp_path / "fake.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(_scenario(tmp_path, extra)))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    app = create_app(str(tmp_path / "home"))
    out = []
    try:
        async with client(app) as c:
            s = await new_session(c, work, harness="pi")
            await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                          json={"text": "hi", "turn_id": "t0"}), 30)
            for n, text in enumerate(texts, start=1):
                r = await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                  json={"text": text, "turn_id": f"t{n}"}), 30)
                out.append((r.status_code, frames(r.text) if r.status_code == 200 else r.json()))
    finally:
        await stop_all(app)
    return out, log


async def test_a_bang_command_runs_through_pis_own_bash_and_is_one_command_step(tmp_path, monkeypatch, work, fresh):
    out, log = await _run(tmp_path, monkeypatch, work, _bash_steps("b1"), ["!echo hello-bash; echo two"])
    status, evs = out[0]
    assert status == 200
    steps = [e for e in evs if e["type"] == "tool"]
    assert steps[0]["status"] == "running" and steps[0]["title"] == "Run echo hello-bash; echo two"
    last = steps[-1]
    assert last["status"] == "done" and last["kind"] == "command" and "hello-bash" in last["output"]
    assert len({s["item"] for s in steps}) == 1, "one step, updated in place"
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
    bash = [x for x in sent(log) if x.get("type") == "bash"]
    assert bash == [{"id": bash[0]["id"], "type": "bash", "command": "echo hello-bash; echo two",
                     "excludeFromContext": False}]
    prompts = [x.get("message") for x in sent(log) if x.get("type") == "prompt"]
    assert prompts == ["hi"], "the command never reached the model as words"


async def test_a_double_bang_keeps_it_out_of_the_conversation_and_a_failing_exit_is_a_failed_step(
        tmp_path, monkeypatch, work, fresh):
    extra = _bash_steps("b3", update=False)
    out, log = await _run(tmp_path, monkeypatch, work, extra, ["!! exit 3"])
    status, evs = out[0]
    assert status == 200
    assert [e["text"] for e in evs if e["type"] == "notice"][-1] == P.SHELL_KEPT_OUT
    last = [e for e in evs if e["type"] == "tool"][-1]
    assert last["status"] == "failed" and last["output"].endswith("exit code 3")
    assert [x["excludeFromContext"] for x in sent(log) if x.get("type") == "bash"] == [True]


async def test_a_bare_bang_is_refused_in_words_and_pi_is_never_asked(tmp_path, monkeypatch, work, fresh):
    out, log = await _run(tmp_path, monkeypatch, work, [], ["!"])
    status, body = out[0]
    assert status == 400 and body["detail"] == P.SHELL_NEEDS_COMMAND
    assert not any(x.get("type") == "bash" for x in sent(log))


async def test_stop_during_a_command_asks_pi_to_abort_it(tmp_path, monkeypatch, work, fresh):
    cancelled = {"id": "x", "type": "response", "command": "bash", "success": True,
                 "data": {"output": "n1\n", "exitCode": None, "cancelled": True, "truncated": False}}
    extra = [{"await": "command", "command": "bash"},
             {"await": "command", "command": "abort_bash"},
             {"out": {"id": "y", "type": "response", "command": "abort_bash", "success": True},
              "reply_to_command": "abort_bash"},
             {"out": cancelled, "reply_to_command": "bash"}]
    log = tmp_path / "fake.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(_scenario(tmp_path, extra)))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            s = await new_session(c, work, harness="pi")
            await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                          json={"text": "hi", "turn_id": "t0"}), 30)
            task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                json={"text": "!ping -n 30 127.0.0.1", "turn_id": "t1"}))
            await until(lambda: any(x.get("type") == "bash" for x in sent(log)))
            r = await c.post(f"/build/app/sessions/{s['id']}/interrupt")
            assert r.status_code == 200, r.text
            evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await stop_all(app)
    assert any(x.get("type") == "abort_bash" for x in sent(log))
    last = [e for e in evs if e["type"] == "tool"][-1]
    assert last["status"] == "failed" and last["output"].endswith("Stopped.")
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "interrupted"
    assert len(starts(log)) == 1
