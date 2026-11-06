"""v1.441.0 lane g4 — the independent review's pins.

Each one was red on the builder's commit (952ca189):

1. A Pi ``!`` command running when Iron Jarvis restarted (the code host keeps
   Pi alive, ``hosted/adopt.py``) could never end: the waiter for Pi's ``bash``
   answer has no JSON form and was not re-armed, so neither the answer nor
   Stop ended the turn — the session refused every later message as busy.
2. A ``!`` command that ran past :data:`P.BASH_TIMEOUT_S` said only
   "Stopped." — as if the user had pressed Stop.
3. A restart that cut off a ``!`` turn was carried on BY ITSELF with the
   "carry on with the task" message, so Pi's MODEL started working on a
   message the user meant as a command (for ``!!`` one it never even saw).
4. Antigravity's transcript pairs each ``GENERIC`` step (it names no call)
   with the oldest open call; a call left with no result shifted every later
   result onto the wrong call.
5. Secrets: every field of Grok's MCP notifications holding a made-up key
   (URL, headers, env, args, command, detail, tool descriptions, unknown
   status and reason words) — none reaches a driver event, a log line or the
   view; a secret-shaped server NAME is masked.

Re-measured by the reviewer (scratch homes only):
* pi 1.3.0 ``--mode rpc --no-session --offline``: ``abort_bash`` answered at
  once and the ``bash`` answer followed with ``cancelled: true``; a command
  that floods its output answered ``truncated: true`` + ``fullOutputPath``
  (50 KB kept); ``!important: …`` ran through Git bash and exited 127.
* agy 1.5.2 with the stand-in model answering TWO function calls in one
  reply: the transcript holds ONE ``PLANNER_RESPONSE`` with both calls, then
  the two ``GENERIC`` results in call order.
* grok 1.0.70 ``grok inspect --json``: a Grok home's ``AGENTS.md`` AND
  ``~/.claude/CLAUDE.md`` (read from the user folder, not CLAUDE_CONFIG_DIR)
  are both global instructions; ``GROK_CLAUDE_AGENTS_ENABLED=false`` or
  ``[compat.claude] agents = false`` turns the second off.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import history as H
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import tool_setup as TS
from iron_jarvis.hosted.grok import GrokDriver


def _pi(emitted: list, written: list | None = None) -> P.PiDriver:
    async def write(obj):
        if written is not None:
            written.append(obj)

    return P.PiDriver(write=write, emit=emitted.append, ask=lambda a: None,
                      on_session_id=lambda s: None, mode="own")


def _running_bang(d: P.PiDriver) -> None:
    d.turn_open = True
    d._turn_n = 4
    d._builtin = "!"
    d._bash = {"rid": "ij-bash-9", "item": "x-bash4", "command": "ping -n 30 127.0.0.1", "out": "Reply\n"}


# --------------------------------------------------------------------------- #
# 1. a ``!`` command across a restart
# --------------------------------------------------------------------------- #
async def test_a_bang_command_kept_across_a_restart_ends_on_pis_answer():
    d = _pi([])
    _running_bang(d)
    kept = json.loads(json.dumps(d.state()))
    after: list[dict] = []
    e = _pi(after)
    e.restore(kept)
    e.on_message({"id": "ij-bash-9", "type": "response", "command": "bash", "success": True,
                  "data": {"output": "Reply\nReply\n", "exitCode": 0, "cancelled": False}})
    await asyncio.sleep(0)
    assert after and after[-1]["type"] == "done" and after[-1]["stop"] == "completed"
    step = [x for x in after if x.get("type") == "tool"][-1]
    assert step["status"] == "done" and step["item"] == "x-bash4"
    assert e._builtin == "" and e._bash is None


async def test_stop_on_a_bang_command_kept_across_a_restart_ends_the_turn():
    written: list[dict] = []
    after: list[dict] = []
    d = _pi([])
    _running_bang(d)
    kept = json.loads(json.dumps(d.state()))
    e = _pi(after, written)
    e.restore(kept)
    P.ABORT_ANSWER_WAIT_S, old = 0.05, P.ABORT_ANSWER_WAIT_S
    try:
        task = asyncio.ensure_future(e.interrupt())
        await asyncio.sleep(0.01)
        # Pi answers the abort; its ``bash`` answer was lost while Iron Jarvis
        # was down — the turn still ends.
        abort = next(x for x in written if x.get("type") == "abort_bash")
        e.on_message({"id": abort["id"], "type": "response", "command": "abort_bash", "success": True})
        await asyncio.wait_for(task, 5)
    finally:
        P.ABORT_ANSWER_WAIT_S = old
    assert after and after[-1]["type"] == "done" and after[-1]["stop"] == "interrupted"
    # A late answer after that never ends ANOTHER turn.
    e.turn_open = True
    e._turn_n = 5
    e.on_message({"id": "ij-bash-9", "type": "response", "command": "bash", "success": True,
                  "data": {"output": "", "exitCode": None, "cancelled": True}})
    await asyncio.sleep(0)
    assert e.turn_open is True


# --------------------------------------------------------------------------- #
# 2. the time limit says so
# --------------------------------------------------------------------------- #
async def test_a_command_past_the_time_limit_says_it_was_the_time_limit():
    written: list[dict] = []
    said: list[dict] = []
    d = _pi(said, written)
    old = P.BASH_TIMEOUT_S
    P.BASH_TIMEOUT_S = 0.05
    try:
        async def answer_abort():
            while not any(x.get("type") == "abort_bash" for x in written):
                await asyncio.sleep(0.01)
            ab = next(x for x in written if x.get("type") == "abort_bash")
            d.on_message({"id": ab["id"], "type": "response", "command": "abort_bash", "success": True})

        helper = asyncio.ensure_future(answer_abort())
        await asyncio.wait_for(d.run_builtin("!", "ping -n 99 127.0.0.1"), 5)
        await helper
    finally:
        P.BASH_TIMEOUT_S = old
    step = [x for x in said if x.get("type") == "tool"][-1]
    assert step["status"] == "failed"
    assert "longer than" in step["output"] and not step["output"].rstrip().endswith("Stopped.")
    assert said[-1]["type"] == "done"


# --------------------------------------------------------------------------- #
# 3. a cut ``!`` turn is offered, never carried on by itself
# --------------------------------------------------------------------------- #
def test_a_cut_bang_turn_is_never_carried_on_by_itself():
    events = [
        {"type": "user", "turn": "t1", "text": "!npm run build", "command": True, "at": "2026-11-06T10:00:00Z"},
        {"type": "tool", "turn": "t1", "item": "x-bash1", "status": "running", "at": "2026-11-06T10:00:01Z"},
    ]
    rec = CO.analyse_tail(events, stopped_title="Stopped when Iron Jarvis stopped")
    assert rec is not None and rec["command"] is True
    # An ordinary message is still carried on.
    events[0].pop("command")
    assert CO.analyse_tail(events, stopped_title="x")["command"] is False


async def test_the_service_marks_a_bang_turns_message_as_a_command(tmp_path, monkeypatch):
    from ._build_app_helpers_v1409 import client, frames, new_session, stop_all  # noqa: F401
    import sys

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import service as S

    from ._build_app_helpers_v1409 import FAKE

    fix = Path(__file__).parent / "fixtures"
    steps = json.loads((fix / "build_app_v1411" / "pi_read.json").read_text(encoding="utf-8"))["steps"]
    rec = {(f["type"] if f["type"] != "response" else f["id"]): f for f in
           json.loads((fix / "build_app_v14410" / "pi_user_bash.json").read_text(encoding="utf-8"))["frames"]}
    steps += [{"await": "command", "command": "bash"}, {"out": rec["b1"], "reply_to_command": "bash"}]
    scenario = tmp_path / "s.json"
    scenario.write_text(json.dumps({"harness": "pi", "steps": steps}), encoding="utf-8")
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setattr(P, "LAST_MODELS", [])
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(tmp_path / "fake.log"))
    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            s = await new_session(c, work, harness="pi")
            await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                          json={"text": "hi", "turn_id": "t0"}), 30)
            r = await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                              json={"text": "!echo hello-bash", "turn_id": "t1"}), 30)
            evs = frames(r.text)
    finally:
        await stop_all(app)
    users = [e for e in evs if e["type"] == "user"]
    assert users and users[-1].get("command") is True
    logged = [json.loads(line) for p in (tmp_path / "home").rglob("events.jsonl")
              for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    plain = [e for e in logged if e.get("type") == "user" and e.get("text") == "hi"]
    assert plain and "command" not in plain[0], "an ordinary message carries no mark"


# --------------------------------------------------------------------------- #
# 4. Antigravity: a result never lands on the wrong call
# --------------------------------------------------------------------------- #
def _agy(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _call(name: str, path: str) -> dict:
    return {"name": name, "args": {"Path": json.dumps(path), "Content": json.dumps("x")}}


def test_two_calls_in_one_reply_take_their_results_in_order(tmp_path):
    # Measured on agy 1.5.2: one PLANNER_RESPONSE with both calls, then both
    # GENERIC results in call order.
    rows = [
        {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>\nwrite two\n</USER_REQUEST>",
         "created_at": "2026-11-06T15:58:40Z"},
        {"type": "PLANNER_RESPONSE", "content": "", "created_at": "2026-11-06T15:58:45Z",
         "tool_calls": [_call("write_file", "C:/w/a.txt"), _call("write_file", "C:/w/b.txt")]},
        {"type": "GENERIC", "status": "DONE", "content": "Created At: x\nCompleted At: y\nA-RESULT"},
        {"type": "GENERIC", "status": "ERROR", "content": "Created At: x\nCompleted At: y\nB-RESULT"},
        {"type": "PLANNER_RESPONSE", "content": "both written", "created_at": "2026-11-06T15:58:47Z"},
    ]
    tools = [e for e in H._antigravity_events(_agy(tmp_path, rows)) if e["type"] == "tool"]
    assert [(t["path"], t["output"], t["status"]) for t in tools] == [
        ("C:/w/a.txt", "A-RESULT", "done"), ("C:/w/b.txt", "B-RESULT", "failed")]


def test_a_call_left_without_a_result_does_not_take_the_next_calls(tmp_path):
    rows = [
        {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>go</USER_REQUEST>"},
        {"type": "PLANNER_RESPONSE", "content": "", "tool_calls": [_call("write_file", "C:/w/first.txt")]},
        # no GENERIC for the first call
        {"type": "PLANNER_RESPONSE", "content": "", "tool_calls": [_call("write_file", "C:/w/second.txt")]},
        {"type": "GENERIC", "status": "DONE", "content": "Created At: x\nSECOND-RESULT"},
    ]
    tools = [e for e in H._antigravity_events(_agy(tmp_path, rows)) if e["type"] == "tool"]
    by_path = {t["path"]: t for t in tools}
    assert by_path["C:/w/second.txt"].get("output") == "SECOND-RESULT"
    assert "output" not in by_path["C:/w/first.txt"]


# --------------------------------------------------------------------------- #
# 5. secrets in every field of Grok's MCP notifications
# --------------------------------------------------------------------------- #
KEY = "FAKEKEYzz9q8w7e6r5t4"
SECRET_FRAMES = [
    {"method": "_x.ai/mcp/servers_updated", "params": {"mcpServers": [
        {"name": "web", "source": f"local-{KEY}", "type": f"http-{KEY}", "url": f"https://h/{KEY}/mcp?api_key={KEY}",
         "headers": {"Authorization": f"Bearer {KEY}", "X-Key": KEY}, "env": {"TOKEN": KEY},
         "args": ["--token", KEY], "command": f"C:/x/{KEY}.exe", "transport": KEY, "config": {"deep": [KEY]}},
        {"name": "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF", "url": f"https://h/?k={KEY}"},
    ]}},
    {"method": "_x.ai/mcp/server_status", "params": {
        "sessionId": "SID", "name": "web", "source": KEY, "status": f"weird-{KEY}", "reason": f"odd-{KEY}",
        "detail": f"failed https://h/{KEY}/mcp?api_key={KEY}", "tools": [{"name": KEY, "description": KEY}],
        "url": f"https://h/{KEY}", "env": {"K": KEY}}},
    {"method": "_x.ai/mcp/list", "params": {"servers": [{"name": "web", "url": f"https://h/{KEY}"}]}},
    {"method": "_x.ai/mcp/init_progress", "params": {"sessionId": "SID", "detail": KEY}},
]


def test_no_field_of_groks_mcp_notifications_reaches_an_event_a_log_or_the_view(caplog):
    said: list[dict] = []

    async def _write(obj):
        return None

    d = GrokDriver(write=_write, emit=said.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.session_id = "SID"
    with caplog.at_level(logging.DEBUG):
        for f in SECRET_FRAMES:
            d.on_extension(f["method"], f["params"])
    setups = [e for e in said if e.get("type") == "setup"]
    assert setups, "the list is said"
    view = TS.view("grok", setups, running=True)
    blob = json.dumps(said) + json.dumps(view) + json.dumps(d.reported_mcp) + caplog.text
    assert KEY.lower() not in blob.lower(), "not even lower-cased"
    assert "AAAABBBBCCCCDDDD" not in blob, "a secret-shaped name is masked"
    assert [s["name"] for s in view["servers"]][0] == "web"


def test_grok_records_that_carry_mcp_config_never_reach_the_imported_conversation(tmp_path):
    folder = tmp_path / "sessions" / "C%3A%5Cwork" / "019a-sess"
    folder.mkdir(parents=True)
    rows = [
        {"type": "system", "content": f"MCP: https://h/{KEY}/mcp?api_key={KEY}"},
        {"type": "user", "content": [{"type": "text", "text": f"<user_info>\nmcp https://h/{KEY}\n</user_info>"}]},
        {"type": "user", "content": [{"type": "text", "text": f"<system-reminder>connecting {KEY}</system-reminder>"}],
         "synthetic_reason": "system_reminder"},
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nlist my tables\n</user_query>\n"
                                                              f"<system-reminder>{KEY}</system-reminder>"}],
         "prompt_index": 0},
        {"type": "reasoning", "encrypted_content": KEY},
        {"type": "assistant", "content": "ok", "tool_calls": [
            {"id": "c1", "name": "accounting__query", "arguments": json.dumps({"sql": "select 1", "api_key": KEY})}]},
        {"type": "tool_result", "tool_call_id": "c1", "content": f"Error: GET https://h/mcp?api_key={KEY} failed"},
    ]
    (folder / "chat_history.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (folder / "events.jsonl").write_text(json.dumps({
        "ts": "2026-11-06T10:00:00Z", "type": "mcp_server_starting", "target": f"https://h/{KEY}"}) + "\n" + json.dumps({
        "ts": "2026-11-06T10:00:01Z", "type": "turn_started", "turn_number": 0}) + "\n", encoding="utf-8")
    H.forget_transcripts()
    got = H.transcript_events("grok", str(tmp_path), r"C:\work", "019a-sess")
    assert [e["type"] for e in got] == ["user", "text_done", "tool"]
    assert got[0]["text"] == "list my tables"
    assert KEY not in json.dumps(got)


def test_the_restart_pass_offers_a_cut_command_and_never_carries_it_on(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.hosted import store

    row = SimpleNamespace(archived_at=None, terminal_id="", harness_session_id="019a")
    monkeypatch.setattr(store, "get", lambda engine, sid: row)
    svc = SimpleNamespace(engine=None, home=str(tmp_path), live={})
    CO.note_cut(tmp_path, "s1", {"turn": "t1", "command": True, "cut_at": CO._iso(CO.CLOCK())})
    rec = CO.read_cut(tmp_path, "s1")
    assert rec["command"] is True
    assert CO._restart_refusal(svc, "s1", rec, since=CO.CLOCK() - __import__("datetime").timedelta(days=1)) \
        == CO.WHY_COMMAND
