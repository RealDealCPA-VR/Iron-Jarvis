"""v1.418.0 (fix plan wave 2, C4.2-C4.5) — Build › Code shows what the coding
tool is doing, as it does it.

* C4.2: a step's row appears when the model STARTS writing it (Claude Code's
  ``content_block_start``) and its input updates as it is written
  (``input_json_delta``, ≤ 4 a second, ``partial: true``); every frame of a
  helper (Task / Agent) carries ``parent`` = that helper's step; a running
  Codex command's output arrives as it comes (``item/commandExecution/
  outputDelta``, read from codex 0.157.0's own schema).
* C4.3: Claude Code's own system messages (hooks, compacting, a refused
  action, a background helper's end) are notices in its words; a request
  Iron Jarvis refuses (a hook callback) is said on the screen; every hook's
  output is asked for (``--include-hook-events``).
* C4.4: a step's output goes to the screen cut at 1,500 characters with
  ``more`` — the log keeps up to 20,000 (``EventLog.item_text``); a command's
  title is Claude Code's own description of it, the command its subtitle.
* C4.5: an edit Claude Code reports with no ``tool_use_result`` (every edit
  in Full access in the live log; a helper's edit, MEASURED in the one real
  run recorded here) shows its change, read off its own input, and its line,
  read off the file (off the loop, bounded).

Every scenario replays a RECORDED run through the REAL parser (and, where it
says so, the real routes and service).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import FIX, client, frames, new_session, sent, stop_all, use_fake

GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-events-v1409.json"


def _claude(got: list, **kw) -> C.ClaudeDriver:
    return C.ClaudeDriver(write=None, emit=got.append, ask=lambda a: got.append(a.event),
                          on_session_id=lambda s: None, mode=kw.get("mode", "ask"), cwd=kw.get("cwd", ""))


def _recorded_frames(name: str) -> list[dict]:
    steps = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))["steps"]
    return [s["out"] for s in steps if "out" in s and "reply_to_control" not in s]


def _stream(ev: dict, parent: str | None = None) -> dict:
    return {"type": "stream_event", "event": ev, "parent_tool_use_id": parent, "session_id": "s"}


# --------------------------------------------------------------------------- #
# C4.2: rows as they are written; helpers nested
# --------------------------------------------------------------------------- #
def test_a_step_row_appears_when_the_model_starts_writing_it(monkeypatch):
    monkeypatch.setattr(C, "PARTIAL_EVERY_S", 0.0)
    got: list = []
    d = _claude(got)
    d.on_message(_stream({"type": "message_start", "message": {"id": "m1"}}))
    d.on_message(_stream({"type": "content_block_start", "index": 0,
                          "content_block": {"type": "tool_use", "id": "t1", "name": "Write", "input": {}}}))
    first = [e for e in got if e["type"] == "tool"]
    assert first and first[0]["partial"] is True and first[0]["status"] == "running"
    assert first[0]["title"] == "Write a file" and first[0]["input"] == ""
    for piece in ['{"file_', 'path": "C:\\\\w\\\\notes.md", "content": "', "line one\\n" * 50]:
        d.on_message(_stream({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "input_json_delta", "partial_json": piece}}))
    rows = [e for e in got if e["type"] == "tool"]
    assert rows[-1]["title"] == "Write notes.md", "the path arrived: the title says so"
    assert rows[-1]["partial"] is True and rows[-1]["input_chars"] > 400
    assert rows[-1]["input"].startswith("…") and len(rows[-1]["input"]) <= E.INPUT_CHARS, "the newest part, cut"
    d.on_message({"type": "assistant", "message": {"id": "m1", "content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "C:\\w\\notes.md", "content": "x"}}]}})
    assert "partial" not in [e for e in got if e["type"] == "tool"][-1], "the whole input arrived: no longer partial"
    n = len(got)
    d.on_message(_stream({"type": "content_block_stop", "index": 0}))
    assert len(got) == n, "a step whose whole input came says nothing more at its block's end"


def test_the_updates_are_bounded_by_the_clock(monkeypatch):
    monkeypatch.setattr(C, "PARTIAL_EVERY_S", 3600.0)
    got: list = []
    d = _claude(got)
    d.on_message(_stream({"type": "message_start", "message": {"id": "m1"}}))
    d.on_message(_stream({"type": "content_block_start", "index": 0,
                          "content_block": {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}}))
    for _ in range(40):
        d.on_message(_stream({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "input_json_delta", "partial_json": '"x'}}))
    assert len([e for e in got if e["type"] == "tool"]) == 1, "≤ 4 a second: none of 40 pieces inside the window"
    d.on_message(_stream({"type": "content_block_stop", "index": 0}))
    rows = [e for e in got if e["type"] == "tool"]
    assert len(rows) == 2 and rows[-1]["input_chars"] == 80, "the block's end sends what came since"


def test_a_helpers_steps_and_answer_carry_its_step_as_parent():
    """The ONE real run (claude 2.1.316, Full access): Claude Code ran the
    helper in the background; its messages came whole, with
    parent_tool_use_id."""
    got: list = []
    d = _claude(got, mode="full")
    for f in _recorded_frames("claude_full_mode_edit_v1418"):
        d.on_message(f)
    agent = "toolu_01C2ykXYSx2B2DMzAqfHZEGc"
    helper_row = [e for e in got if e["type"] == "tool" and e["item"] == agent]
    assert helper_row[-1]["title"] == "Helper: Create new.txt containing hi", "the tool's own description of the job"
    child_tools = [e for e in got if e["type"] == "tool" and e.get("parent") == agent]
    assert {e["name"] for e in child_tools} == {"Write"}
    child_text = [e for e in got if e["type"] == "text_done" and e.get("parent") == agent]
    assert [e["text"] for e in child_text] == ["created"]
    main = [e for e in got if e["type"] in ("tool", "text_done") and "parent" not in e]
    assert {e.get("name") for e in main if e["type"] == "tool"} == {"Read", "Edit", "Agent"}
    assert not [e for e in got if e["type"] == "plan"], "no checklist from a helper"


def test_a_helpers_whole_thinking_is_said_once_never_with_the_streamed_one():
    got: list = []
    d = _claude(got)
    d.on_message(_stream({"type": "message_start", "message": {"id": "main"}}))
    d.on_message(_stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}))
    d.on_message(_stream({"type": "content_block_delta", "index": 0,
                          "delta": {"type": "thinking_delta", "thinking": "streamed"}}))
    d.on_message({"type": "assistant", "message": {"id": "main", "content": [{"type": "thinking", "thinking": "streamed"}]}})
    d.on_message({"type": "assistant", "parent_tool_use_id": "T", "message": {
        "id": "child", "content": [{"type": "thinking", "thinking": "the helper thought"}]}})
    thinking = [(e["delta"], e.get("parent")) for e in got if e["type"] == "thinking"]
    assert thinking == [("streamed", None), ("the helper thought", "T")]


def test_a_running_codex_command_shows_its_output_as_it_arrives(monkeypatch):
    """READ from codex 0.157.0's own `app-server generate-json-schema`:
    item/commandExecution/outputDelta {threadId, turnId, itemId, delta}."""
    monkeypatch.setattr(X, "OUTPUT_EVERY_S", 0.0)
    got: list = []
    d = X.CodexDriver(write=None, emit=got.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    item = {"id": "c1", "type": "commandExecution", "command": "npm test", "status": "inProgress"}
    d.on_message({"method": "item/started", "params": {"item": item}})
    for chunk in ("PASS a.test.js\n", "PASS b.test.js\n"):
        d.on_message({"method": "item/commandExecution/outputDelta",
                      "params": {"threadId": "th", "turnId": "tu", "itemId": "c1", "delta": chunk}})
    rows = [e for e in got if e["type"] == "tool"]
    assert rows[-1]["partial"] is True and rows[-1]["status"] == "running"
    assert rows[-1]["output"] == "PASS a.test.js\nPASS b.test.js\n"
    d.on_message({"method": "item/completed", "params": {"item": {
        **item, "status": "completed", "exitCode": 0, "aggregatedOutput": "PASS a.test.js\nPASS b.test.js\nall done\n"}}})
    last = [e for e in got if e["type"] == "tool"][-1]
    assert last["status"] == "done" and "partial" not in last and last["output"].endswith("all done\n")


def test_codex_output_updates_are_bounded_by_the_clock(monkeypatch):
    monkeypatch.setattr(X, "OUTPUT_EVERY_S", 3600.0)
    got: list = []
    d = X.CodexDriver(write=None, emit=got.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.on_message({"method": "item/started", "params": {"item": {"id": "c1", "type": "commandExecution", "command": "x"}}})
    for _ in range(30):
        d.on_message({"method": "item/commandExecution/outputDelta",
                      "params": {"threadId": "th", "turnId": "tu", "itemId": "c1", "delta": "."}})
    partial = [e for e in got if e.get("partial")]
    assert len(partial) == 1, "the first piece at once, then ≤ 4 a second"


# --------------------------------------------------------------------------- #
# C4.3: Claude Code's own system messages and refused requests, said
# --------------------------------------------------------------------------- #
def test_the_system_messages_worth_a_line_are_said_in_claudes_words():
    cases = {
        "hook_response": ({"hook_name": "PostToolUse:Bash", "hook_event": "PostToolUse", "output": "",
                           "stderr": "2 warnings\n", "outcome": "error"},
                          "Hook PostToolUse:Bash (PostToolUse) failed: 2 warnings"),
        "stop_hook_summary": ({"preventedContinuation": True, "stopReason": "Tests first."},
                              "A hook stopped Claude Code from carrying on: Tests first."),
        # v1.477.2 (moved on purpose): told nothing about the task, the line
        # no longer guesses "A helper" (tests/test_build_app_task_ended_words_v1477.py).
        "task_notification": ({"status": "completed", "summary": "created", "tool_use_id": "T"},
                              "A task finished: created"),
        "task_started": ({"is_backgrounded": True, "description": "watch the build"},
                         "Claude Code started a background task: watch the build"),
        "notification": ({"text": "Fast mode is off for this account."}, "Fast mode is off for this account."),
        "local_command": ({"content": "<local-command-stdout>Total cost: $0.12</local-command-stdout>"},
                          "Total cost: $0.12"),
        "permission_denied": ({"tool_name": "WebFetch", "message": "Outside this task."},
                              "Claude Code refused WebFetch: Outside this task."),
        "api_error": ({"error": "overloaded", "retryAttempt": 2, "maxRetries": 10},
                      "Claude Code got an error from the model service (overloaded) and is trying again "
                      "(attempt 2 of 10)."),
        "model_fallback": ({"content": "Switched to Sonnet: Opus is overloaded."},
                           "Switched to Sonnet: Opus is overloaded."),
    }
    for sub, (fields, words) in cases.items():
        assert C.system_notice({"type": "system", "subtype": sub, **fields}) == words, sub
    quiet = [
        {"subtype": "hook_response", "hook_name": "x", "hook_event": "PreToolUse", "output": "", "outcome": "success"},
        {"subtype": "hook_started", "hook_name": "x"}, {"subtype": "hook_progress", "stdout": "..."},
        {"subtype": "task_progress", "description": "Writing"}, {"subtype": "task_started", "tool_use_id": "T",
                                                                "is_backgrounded": True, "description": "x"},
        {"subtype": "turn_duration", "durationMs": 9}, {"subtype": "thinking_tokens"},
        {"subtype": "stop_hook_summary", "preventedContinuation": False},
    ]
    for q in quiet:
        assert C.system_notice({"type": "system", **q}) == "", q


def test_compacting_is_said_once_and_a_failed_compaction_says_why():
    got: list = []
    d = _claude(got)
    for _ in range(3):  # sent every 30 s while it lasts (claude 2.1.316's bundle)
        d.on_message({"type": "system", "subtype": "status", "status": "compacting"})
    d.on_message({"type": "system", "subtype": "status", "status": None, "compact_result": "failed",
                  "compact_error": "Too short."})
    d.on_message({"type": "system", "subtype": "status", "status": "requesting"})
    assert [e["text"] for e in got] == [W.SYS_COMPACTING, W.SYS_COMPACT_FAILED.format(why="Too short.")]


async def test_a_refused_hook_callback_is_said_and_still_refused():
    got: list = []
    wrote: list = []

    async def write(obj):
        wrote.append(obj)

    d = C.ClaudeDriver(write=write, emit=got.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.on_message({"type": "control_request", "request_id": "r9",
                  "request": {"subtype": "hook_callback", "callback_id": "cb"}})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [e["text"] for e in got] == [W.CONTROL_REFUSED.format(what=W.CONTROL_ASKS["hook_callback"])]
    assert wrote and wrote[0]["response"]["subtype"] == "error" and wrote[0]["response"]["request_id"] == "r9"


def test_every_hooks_output_is_asked_for():
    argv = _claude([]).argv(session_id="s", resume=False)
    assert C.INCLUDE_HOOKS_FLAG in argv


# --------------------------------------------------------------------------- #
# C4.4: the first part on screen, the rest kept; the tool's own title
# --------------------------------------------------------------------------- #
def test_a_long_output_is_cut_for_the_screen_and_kept_in_the_log(tmp_path):
    log = E.EventLog(tmp_path, "s1")
    ev = E.make("tool", item="t1", kind="command", name="Bash", title="x", input="", status="done", output="")
    E.with_full(ev, input_text="ls", output_text="y" * 6000)
    assert len(ev["output"]) == E.OUTPUT_CHARS and ev["more"] is True and ev["output_chars"] == 6000
    assert "input_chars" not in ev, "an input that fit is not said to be cut"
    log.append(ev)
    assert E.FULL_KEY not in ev, "the kept text comes OFF the dict the watchers get next"
    log.close()
    rows, _, _ = log.read()
    assert rows and E.LOG_FULL_KEY not in rows[0], "a reader of the log never gets the kept texts"
    assert json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])[E.LOG_FULL_KEY]["output"] == "y" * 6000
    got = E.EventLog(tmp_path, "s1").item_text("t1")
    assert got == {"item": "t1", "input": "ls", "output": "y" * 6000}
    assert E.EventLog(tmp_path, "s1").item_text("nope") is None


def test_the_kept_text_is_masked_before_it_is_cut():
    key = "sk-ant-api03-" + "A" * 90
    ev = E.with_full({}, output_text="x" * 1480 + " " + key + " " + "y" * 30000)
    assert key[:20] not in ev["output"] and key[:20] not in ev[E.FULL_KEY]["output"]
    assert len(ev[E.FULL_KEY]["output"]) == E.OUTPUT_FULL_CHARS


def test_a_command_is_titled_by_claudes_own_description():
    inp = {"command": "git ls-files && git remote -v", "description": "List tracked files and remotes"}
    assert C.tool_title("Bash", inp) == "List tracked files and remotes"
    assert C.tool_subtitle("Bash", inp) == "git ls-files && git remote -v"
    assert C.tool_title("Bash", {"command": "ls"}) == "Run ls" and C.tool_subtitle("Bash", {"command": "ls"}) == ""
    long = C.tool_title("Bash", {"command": "x", "description": "d" * 200})
    assert long.endswith("…") and len(long) == 90, "a cut title says so"


def test_a_command_card_does_not_say_its_description_twice():
    got: list = []
    d = _claude(got)
    d.on_message({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "description": "List files",
        "input": {"command": "ls", "description": "List files"}}})
    card = got[-1]
    assert card["title"] == "List files" and card["detail"] == "" and card["command"] == "ls"


async def test_a_long_output_reaches_the_screen_cut_and_reads_back_whole(tmp_path, monkeypatch):
    """Through the real routes and service: the SSE carries 1,500 characters
    and `more`, never the kept texts; the log reads back all 6,759."""
    use_fake(monkeypatch, "claude_hooks_v1418", tmp_path / "fake.log")
    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            sid = (await new_session(c, work))["id"]
            r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"})
            evs = frames(r.text)
        done = [e for e in evs if e["type"] == "tool" and e["item"] == "toolu_H1" and e["status"] == "done"][0]
        assert done["more"] is True and len(done["output"]) == E.OUTPUT_CHARS
        assert done["title"] == "List tracked files and remotes" and done["subtitle"] == "git ls-files && git remote -v"
        assert not any(E.FULL_KEY in e or E.LOG_FULL_KEY in e for e in evs)
        from iron_jarvis.hosted import service as S

        live_log = S.service_for(app.state.platform).live[sid].log
        kept = await asyncio.to_thread(live_log.item_text, "toolu_H1")
        assert len(kept["output"]) == done["output_chars"] == 6759
        notices = [e["text"] for e in evs if e["type"] == "notice"]
        assert "Hook SessionStart:startup (SessionStart) ran: Loaded the team's notes." in notices
        assert W.CONTROL_REFUSED.format(what=W.CONTROL_ASKS["hook_callback"]) in notices
        refused = [m for m in sent(tmp_path / "fake.log") if m.get("type") == "control_response"
                   and (m.get("response") or {}).get("request_id") == "hook-cb-1"]
        assert refused and refused[0]["response"]["subtype"] == "error", "Claude Code is still answered"
    finally:
        await stop_all(app)


def test_no_golden_event_carries_the_kept_texts():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, evs in doc["scenarios"].items():
        for e in evs:
            assert E.FULL_KEY not in e and E.LOG_FULL_KEY not in e, name


# --------------------------------------------------------------------------- #
# C4.5: a change shown in every mode
# --------------------------------------------------------------------------- #
def test_an_edit_reported_without_tool_use_result_shows_its_change():
    """MEASURED (the recording): the helper's Write came with no
    tool_use_result; the live log: 619 of 619 Full-access edits had no diff."""
    got: list = []
    d = _claude(got, mode="full")
    for f in _recorded_frames("claude_full_mode_edit_v1418"):
        d.on_message(f)
    edits = [e for e in got if e["type"] == "tool" and e["kind"] in ("edit", "write") and e["status"] == "done"]
    assert [e["name"] for e in edits] == ["Edit", "Write"]
    assert all(e.get("diff") for e in edits), "every edit shows its change"
    write = edits[1]
    assert write["diff"].splitlines()[-1] == "+hi" and write["line"] == 1


async def test_an_edits_line_is_read_off_the_file_when_claude_sends_none(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    got: list = []
    d = _claude(got, mode="full", cwd=str(tmp_path))
    inp = {"file_path": "app.py", "old_string": "c = 30", "new_string": "c = 3"}
    d.on_message({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "tool_use", "id": "e1", "name": "Edit", "input": inp}]}})
    d.on_message({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "e1", "content": "The file has been updated."}]}})
    first = [e for e in got if e["type"] == "tool" and e["status"] == "done"]
    assert first[0]["diff"] and "line" not in first[0], "the change at once; the line once the file is read"
    for _ in range(100):
        if len([e for e in got if e["type"] == "tool" and e["status"] == "done"]) > 1:
            break
        await asyncio.sleep(0.02)
    again = [e for e in got if e["type"] == "tool" and e["status"] == "done"][-1]
    assert again["line"] == 3 and again["item"] == "e1" and again["diff"] == first[0]["diff"]


def test_the_line_read_is_bounded(tmp_path, monkeypatch):
    f = tmp_path / "big.txt"
    f.write_text("x\n" * 50 + "needle\n", encoding="utf-8")
    assert C.line_of(str(f), "needle") == 51
    head = tmp_path / "head.txt"
    head.write_bytes(b"needle\n" + b"x" * 100)
    monkeypatch.setattr(C, "LINE_READ_BYTES", 10)
    assert C.line_of(str(head), "needle") is None, "a file past the bound is not read for a line"
    assert C.line_of(str(tmp_path / "gone.txt"), "x") is None


def test_a_helpers_checklist_is_not_the_conversations_plan():
    got: list = []
    d = _claude(got)
    todos = {"todos": [{"content": "a", "status": "pending"}]}
    d.on_message({"type": "assistant", "parent_tool_use_id": "T", "message": {"id": "c", "content": [
        {"type": "tool_use", "id": "w1", "name": "TodoWrite", "input": todos}]}})
    assert not [e for e in got if e["type"] == "plan"]
    d.on_message({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "tool_use", "id": "w2", "name": "TodoWrite", "input": todos}]}})
    assert [e["steps"] for e in got if e["type"] == "plan"] == [[{"text": "a", "status": "pending"}]]



# --------------------------------------------------------------------------- #
# Review (v1.418.0, lane F): defects found by the independent review
# --------------------------------------------------------------------------- #
def test_a_steps_updates_while_it_is_written_are_logged_once(tmp_path):
    """A long Write sends <= 4 updates a second for as long as it is written;
    the record keeps the FIRST (a crash still shows the step) and the final
    row — never every update (they filled the 2,000-event window and pushed
    the user's own messages out of it)."""
    log = E.EventLog(tmp_path, "s1")
    for i in range(50):
        log.append(E.make("tool", item="t1", kind="write", name="Write", title="Write a.py",
                          input="x" * i, status="running", partial=True))
        log.flush()  # every update in its own write, as a slow writer sends them
    log.append(E.make("tool", item="t1", kind="write", name="Write", title="Write a.py",
                      input="{}", status="done", output="ok"))
    log.close()
    rows, _, _ = log.read()
    tool_rows = [r for r in rows if r["type"] == "tool"]
    assert len(tool_rows) == 2, len(tool_rows)
    assert tool_rows[0].get("partial") is True and tool_rows[-1]["status"] == "done"


def test_a_turns_replay_keeps_only_the_newest_update_of_a_step():
    out: list = []
    for i in range(30):
        E.coalesce_into(out, {"type": "tool", "item": "t1", "turn": "u", "status": "running",
                              "partial": True, "input": str(i)})
    assert len(out) == 1 and out[0]["input"] == "29"
    E.coalesce_into(out, {"type": "tool", "item": "t1", "turn": "u", "status": "done", "output": "ok"})
    assert len(out) == 1 and out[0]["status"] == "done", "the finished row takes its place"
    E.coalesce_into(out, {"type": "tool", "item": "t2", "turn": "u", "status": "running", "partial": True})
    E.coalesce_into(out, {"type": "tool", "item": "t2", "turn": "u", "status": "done"})
    E.coalesce_into(out, {"type": "tool", "item": "t2", "turn": "u", "status": "done", "line": 3})
    assert [r["item"] for r in out] == ["t1", "t2", "t2"], "only a PARTIAL row is replaced"


def test_an_edits_line_is_never_read_over_the_network(monkeypatch):
    opened: list = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    def lstat_spy(path, *a, **k):  # an lstat of a share is an SMB connection too
        opened.append(f"lstat:{path}")
        raise OSError("not here")

    monkeypatch.setattr("builtins.open", spy)
    monkeypatch.setattr(C.os, "lstat", lstat_spy)
    monkeypatch.setattr(C.os, "stat", lstat_spy)
    for p in (r"\\host\share\app.py", "//host/share/app.py", r"\\?\UNC\host\share\app.py", r"\\.\pipe\x"):
        assert C.line_of(p, "x") is None
    assert opened == [], "a network or device path is refused by its spelling, before anything touches it"


def test_an_edits_line_never_follows_a_link_to_the_network(tmp_path, monkeypatch):
    from iron_jarvis.harness import code_editor as CE

    target = tmp_path / "link.py"
    target.write_text("needle\n", encoding="utf-8")

    def refuse(_path):
        raise CE.EditorRefusal(CE.NETWORK_PATH, 403)

    monkeypatch.setattr(CE, "_walk_links", refuse)
    assert C.line_of(str(target), "needle") is None, "the links are walked one hop at a time first"


async def test_the_line_read_is_held_until_it_ends(tmp_path):
    """asyncio keeps only a WEAK reference to a task: one nobody holds can be
    collected before it runs."""
    f = tmp_path / "a.py"
    f.write_text("a\nb\n", encoding="utf-8")
    got: list = []
    d = _claude(got, mode="full", cwd=str(tmp_path))
    d.on_message({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "tool_use", "id": "e1", "name": "Edit",
         "input": {"file_path": "a.py", "old_string": "c", "new_string": "b"}}]}})
    d.on_message({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "e1", "content": "ok"}]}})
    assert len(d._line_tasks) == 1
    for _ in range(100):
        if not d._line_tasks:
            break
        await asyncio.sleep(0.02)
    assert not d._line_tasks, "a finished read lets its task go"


def test_a_failed_edit_shows_no_change_built_from_its_input():
    got: list = []
    d = _claude(got, mode="full")
    d.on_message({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "tool_use", "id": "e1", "name": "Edit",
         "input": {"file_path": "a.py", "old_string": "x", "new_string": "y"}}]}})
    d.on_message({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "e1", "is_error": True, "content": "String not found"}]}})
    row = [e for e in got if e["type"] == "tool"][-1]
    assert row["status"] == "failed" and "diff" not in row and "line" not in row


def test_the_hook_flag_rides_with_a_flag_newer_than_it():
    """--include-hook-events is in claude 2.1.310, 2.1.311 and 2.1.316 (read
    from the installed bundles) and hook events streamed headless by 2.1.204
    (its changelog); --permission-prompts came in 2.1.259. A Claude Code too
    old for the hook flag already refuses --permission-prompts, so the flag
    adds no new way to fail to start — while both ride together."""
    argv = _claude([]).argv(session_id="s", resume=False)
    assert C.INCLUDE_HOOKS_FLAG in argv and "--permission-prompts" in argv
