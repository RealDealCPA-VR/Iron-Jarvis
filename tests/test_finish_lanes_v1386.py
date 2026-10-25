"""IJ harness part 3, Phase 2 — THE FINISH CHECK on both chat lanes.

Driven through the REAL lanes on a real platform (the registry, the
permission engine, the cards, the ledger, the undo journal and the shell tool
are the real ones — a test command really runs); only the model is scripted.
The pins say where it runs and where it never does, and that it never widens
a gate. MIRROR NOTE (lock-step): every behaviour pinned on the stream lane is
pinned on the POST lane beside it where that lane can show it (the POST lane
cards nothing, so it arms an ask-tier command tool only when the user's
permissions allow it).

Every "it runs" pin fails on the code before Phase 2 (there was no finish
check: no note, no extra round, no ``finish`` record).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes.chat import _approvals
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import finish as F
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ProviderError, ToolCall
from iron_jarvis.providers.router import RouteResult

WHO = ("anthropic", "claude-sonnet-4-5")
SESSION = "build:finish-test"
NOTE_HEAD = "FINISH CHECK."
TEST_CMD = "python -m unittest -q test_mini"
PASSING = "import unittest\nimport app\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(app.X, 2)\n"


class Model:
    """A scripted model on the router's ``stream()`` / ``complete()``. A move
    is a list of ToolCalls, a reply text, ``(text, calls)``, an
    ``LLMResponse``, an exception (raised), or a callable ``(model) -> move``
    (sync or async — it can read the note the call was sent). Past the script,
    and on a tool-less call, it answers "Done."."""

    def __init__(self, moves: list) -> None:
        self.moves = list(moves)
        self.calls: list[dict] = []

    async def _next(self, system, messages, tools) -> LLMResponse:
        self.calls.append({"system": system, "tools": [t["name"] for t in tools or []],
                           "messages": list(messages)})
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if callable(move):
            move = move(self)
            if inspect.isawaitable(move):
                move = await move
        if isinstance(move, BaseException):
            raise move
        if isinstance(move, LLMResponse):
            return move
        usage = {"input_tokens": 3, "output_tokens": 1}
        if isinstance(move, tuple):
            return LLMResponse(text=move[0], tool_calls=list(move[1]), usage=usage)
        if isinstance(move, str):
            return LLMResponse(text=move, usage=usage)
        return LLMResponse(text="", tool_calls=list(move), usage=usage)

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        resp = await self._next(system, messages, tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        return RouteResult(await self._next(system, messages, tools), WHO[0], WHO[1])

    # -- reading what the model was sent -------------------------------------

    def notes(self) -> list[str]:
        """Every finish note any call was sent (each call carries the whole
        conversation, so a note is counted once: by its first appearance)."""
        seen: list[str] = []
        for call in self.calls:
            for m in call["messages"]:
                if m.role == "user" and str(m.content).startswith(NOTE_HEAD) and m.content not in seen:
                    seen.append(m.content)
        return seen


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_bytes(b"X = 1\n")
    (ws / "test_mini.py").write_bytes(PASSING.encode())

    def wire(moves: list) -> Model:
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


def _body(ws: Path, text: str = "set X to 2 in app.py and run the tests", **kw) -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider=WHO[0], model=WHO[1], workspace_dir=str(ws),
        approval_mode=kw.pop("approval_mode", "yolo"), **kw,
    )


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


async def _stream(platform, body, *, answer=None, session_id=SESSION, **kw) -> dict:
    """The stream lane, its approval cards answered by ``answer(frame)``
    (a decision word) as they come."""
    approvals = _approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(
        platform, BUILTIN_PERSONAS, body, harness=kw.pop("harness", "generic"),
        session_id=session_id, round_budget=kw.pop("round_budget", 30), stay_in_chat=True, **kw,
    )
    frames: list[tuple[str, dict]] = []
    async for chunk in gen:
        for event, data in _frames([chunk]):
            frames.append((event, data))
            if event == "approval":
                decision = answer(data) if answer is not None else None
                assert decision, f"a card nobody expected: {data}"
                approvals.resolve(str(data["id"]), decision)
    done = [d for e, d in frames if e == "done"]
    return {"done": done[0] if done else None, "frames": frames}


async def _post(platform, body, *, session_id=SESSION, **kw) -> dict:
    return await run_chat_turn(
        platform, BUILTIN_PERSONAS, body, harness=kw.pop("harness", "generic"),
        session_id=session_id, round_budget=kw.pop("round_budget", 30), stay_in_chat=True, **kw,
    )


def _ledger(platform) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all())
        for r in rows:
            db.expunge(r)
        return rows


def _finish_rows(platform) -> list[ToolInvocation]:
    return [r for r in _ledger(platform) if r.called_as == F.CALLED_AS]


def _write(path: str, content: str, cid: str = "w1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="write_file", arguments={"path": path, "content": content})]


def _shell(command: str, cid: str = "s1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="shell", arguments={"command": command})]


def _allow_shell_for_post(platform, monkeypatch) -> None:
    # The POST lane shows no card, so it arms an ask-tier tool only where the
    # user's permissions ALLOW it — here, the user set shell to allow.
    monkeypatch.setitem(platform.permissions._base, "shell", "allow")


async def _lane(lane: str, platform, body, monkeypatch, **kw):
    if lane == "stream":
        out = await _stream(platform, body, **kw)
        return out["done"], out["frames"]
    _allow_shell_for_post(platform, monkeypatch)
    kw.pop("answer", None)
    return await _post(platform, body, **kw), []


# --------------------------------------------------------------------------- #
# it runs: once, as one note, its tests through the round, the record
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_reply_that_changed_files_gets_one_note_and_one_extra_round(env, lane, monkeypatch):
    platform, ws, wire = env

    def answer(model: Model):
        note = model.calls[-1]["messages"][-1].content
        assert note.startswith(NOTE_HEAD)
        return "1. met: app.py says X = 2 and the tests pass.\nNothing needed changing."

    model = wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Set X to 2; the tests pass.", answer])
    done, frames = await _lane(lane, platform, _body(ws), monkeypatch)
    # ONE extra model round: write, shell, the reply, the check's answer.
    assert len(model.calls) == 4, [c["messages"][-1].content[:60] for c in model.calls]
    notes = model.notes()
    assert len(notes) == 1
    note = notes[0]
    assert f"`{TEST_CMD}` (the test part of a command you ran in this reply) passed (exit code 0)." in note
    assert "## The request, point by point\n1. set X to 2 in app.py and run the tests" in note
    assert "## Files this reply made that the request does not name\nNone." in note
    assert '"<number>. met: <how you checked>"' in note
    # The model's answer follows its reply — its "1. met: ..." line is the
    # check's bookkeeping (the record counts it), so the reply a person reads
    # keeps only the closing words (review); the record rides the answer LAST.
    assert done["reply"] == "Set X to 2; the tests pass.\n\nNothing needed changing."
    assert list(done)[-1] == "finish"
    assert done["finish"] == {"tests": "passed", "command": TEST_CMD, "removed": 0, "kept": 0,
                              "points": 1, "unmet": 0, "after": ""}
    # Its test run is an ordinary command call — ledgered as the finish check's.
    rows = _finish_rows(platform)
    assert [(r.tool, r.ok, json.loads(r.args_json)["command"]) for r in rows] == [("shell", True, TEST_CMD)]
    assert rows[0].session_id == SESSION
    # The model never sees a tool result it did not ask for (no tool message
    # for the check's own call): its answer round reads the note instead.
    tool_ids = [m.tool_call_id for m in model.calls[-1]["messages"] if m.role == "tool"]
    assert not any(str(t).startswith(F.CALL_ID_PREFIX) for t in tool_ids)
    if lane == "stream":
        # The screen sees the check's test run as a step, then the answer.
        ids = [d["id"] for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
        assert ids[-1].startswith(F.CALL_ID_PREFIX)
        # (The live bubble streamed the whole answer; the saved reply is the
        # done frame's.)
        tokens = "".join(d["text"] for e, d in frames if e == "token")
        assert tokens == ("Set X to 2; the tests pass.\n\n1. met: app.py says X = 2 and the tests pass."
                          "\nNothing needed changing.")


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_failed_tests_keep_the_reply_working_and_the_check_never_runs_twice(env, lane, monkeypatch):
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")

    def answer(model: Model):
        note = model.calls[-1]["messages"][-1].content
        assert "FAILED (exit code 1)." in note and "Its output:" in note and "AssertionError" in note
        return ("1. not met: X is 11, the test wants 2.",
                [ToolCall(id="fix", name="edit_file", arguments={"path": "app.py", "old": "X = 11", "new": "X = 2"})])

    # The wrong value has a DIFFERENT LENGTH from the fix (v1.386.1): Python
    # reuses a cached .pyc whose recorded source size and whole-second mtime
    # still match, and a scripted model writes, tests and fixes inside one
    # second. "X = 1" -> "X = 2" kept the size, so on CI (bytecode on) the
    # re-run imported the stale X = 1 and failed; this PC sets
    # PYTHONDONTWRITEBYTECODE, which hid it.
    model = wire([
        _write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done: X is set.",
        answer, _shell(TEST_CMD, "s2"), "Fixed: X is 2 now and the tests pass.",
    ])
    done, _frames_ = await _lane(lane, platform, _body(ws), monkeypatch)
    assert (ws / "app.py").read_bytes().replace(b"\r\n", b"\n") == b"X = 2\n"
    # ONE note, though the reply ended twice.
    assert len(model.notes()) == 1
    assert done["finish"]["tests"] == "failed" and done["finish"]["after"] == "passed"
    assert done["finish"]["unmet"] == 1
    assert done["reply"] == "Done: X is set.\n\nFixed: X is 2 now and the tests pass."
    # The check's own run is the only one ledgered as the check's — and its
    # failure did not count toward the MODEL's repeated-call limit: the
    # model's own re-run of the same command after its fix RAN (and passed).
    assert [(r.tool, r.ok) for r in _finish_rows(platform)] == [("shell", False)]
    mine = [r for r in _ledger(platform) if r.tool == "shell" and r.called_as != F.CALLED_AS]
    assert [r.ok for r in mine] == [False, True], "the model's re-run after its fix was refused"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_scratch_file_is_listed_and_its_removal_is_ledgered_and_undoable(env, lane, monkeypatch):
    platform, ws, wire = env
    (ws / ".gitignore").write_text("*.log\nout/\n", encoding="utf-8")
    (ws / "util.py").write_bytes(b"Y = 1\n")  # was there before the reply: CHANGED, never made

    def answer(model: Model):
        note = model.calls[-1]["messages"][-1].content
        files = note.split("## Files this reply made that the request does not name\n", 1)[1].split("##", 1)[0]
        assert "- `check_me.py`" in files and "- `notes/todo.md`" in files and "- `notes/more.md`" in files
        # named in the request, ignored by the folder's .gitignore, or a file
        # that was already there (changed, not made): never listed
        assert "app.py" not in files and "run.log" not in files and "out/x.txt" not in files
        assert "util.py" not in files
        assert 'remove the rest with apply_patch ("*** Delete File: <path>"), which can be undone' in files
        return ("1. met.", [ToolCall(id="rm", name="apply_patch", arguments={
            "patch": "*** Begin Patch\n*** Delete File: check_me.py\n*** End Patch"})])

    wire([
        _write("app.py", "X = 2\n", "w1"), _write("check_me.py", "print(1)\n", "w2"),
        _write("notes/todo.md", "- x\n", "w3"), _write("notes/more.md", "- y\n", "w3b"),
        _write("run.log", "log\n", "w4"), _write("out/x.txt", "x\n", "w5"), _write("util.py", "Y = 2\n", "w6"),
        "Done.", answer, "Removed my scratch script; kept the notes.",
    ])
    done, _ = await _lane(lane, platform, _body(ws, text="set X to 2 in app.py"), monkeypatch)
    assert not (ws / "check_me.py").exists() and (ws / "notes" / "todo.md").exists()
    assert done["finish"]["removed"] == 1 and done["finish"]["kept"] == 2
    assert done["finish"]["tests"] == "none"
    # The removal went through the model's own tool: ledgered, with an undo.
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == "apply_patch"]
        assert len(rows) == 1 and rows[0].ok
        journal = db.get(UndoJournal, rows[0].id)
        assert journal is not None and journal.kind == "files_patch" and journal.reversible


# --------------------------------------------------------------------------- #
# it never runs where it must not
# --------------------------------------------------------------------------- #


#: A test runner's own timing ("Ran 1 test in 0.003s") differs run to run —
#: the only part of a tool's output two identical turns do not share.
_TIMING_RX = re.compile(r"\bin \d+(?:\.\d+)?s\b")


def _calls_seen(model: Model) -> list:
    return [(c["system"], c["tools"],
             [(m.role, _TIMING_RX.sub("in <t>s", str(m.content)), m.tool_call_id) for m in c["messages"]])
            for c in model.calls]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_reply_that_changed_nothing_makes_exactly_the_calls_it_made_before(tmp_path, lane, monkeypatch):
    """The same reply on two fresh installs (the same folder): one with the
    finish check, one where it does not exist (``start`` answers None — the
    lanes then run exactly the code before Phase 2). The model is sent the
    SAME calls, byte for byte, and the answer carries no ``finish``."""

    async def run(name: str, old: bool):
        platform = build_platform(str(tmp_path / name))
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        (ws / "app.py").write_bytes(b"X = 1\n")
        (ws / "test_mini.py").write_bytes(PASSING.encode())
        model = Model([[ToolCall(id="r1", name="read_file", arguments={"path": "app.py"})],
                       _shell(TEST_CMD), "X is 1."])
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        monkeypatch.setattr(F, "start", _no_start if old else REAL_START)
        try:
            done, _ = await _lane(lane, platform, _body(ws, text="what is X in app.py?"), monkeypatch)
        finally:
            monkeypatch.setattr(F, "start", REAL_START)
        return platform, model, done

    platform, new_model, new_done = await run("home-new", old=False)
    _old_platform, old_model, old_done = await run("home-old", old=True)
    assert _calls_seen(new_model) == _calls_seen(old_model)
    assert len(new_model.calls) == 3 and new_model.notes() == []
    assert "finish" not in new_done and new_done["reply"] == old_done["reply"] == "X is 1."
    assert set(new_done) == set(old_done)
    assert _finish_rows(platform) == []


REAL_START = F.start


async def _no_start(**_kw):
    return None


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_never_in_plain_chat_or_a_chat_session(env, lane, monkeypatch):
    platform, ws, wire = env
    # Plain chat: no harness at all.
    model = wire([_write("app.py", "X = 2\n"), "Done."])
    body = _body(ws, text="set X to 2 in app.py", tools=["write_file"])
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
        done = [d for e, d in _frames([c async for c in gen]) if e == "done"][0]
    else:
        done = await run_chat_turn(platform, BUILTIN_PERSONAS, body)
    assert len(model.calls) == 2 and model.notes() == [] and "finish" not in done
    # A harness turn under the chat's own session id (not a Build session).
    (ws / "app.py").write_bytes(b"X = 1\n")
    model = wire([_write("app.py", "X = 2\n"), "Done."])
    done, _ = await _lane(lane, platform, _body(ws, text="set X to 2 in app.py"), monkeypatch,
                          session_id="chat")
    assert len(model.calls) == 2 and model.notes() == [] and "finish" not in done


@pytest.mark.parametrize("failure", ["error", "refusal"])
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_never_on_an_error_or_a_refusal(env, lane, failure, monkeypatch):
    from iron_jarvis.providers.adapters.base import ProviderRefusal

    platform, ws, wire = env
    exc = ProviderError("the model went away") if failure == "error" else ProviderRefusal(
        "declined", category="cyber",
    )
    model = wire([_write("app.py", "X = 2\n"), exc])
    if lane == "stream":
        out = await _stream(platform, _body(ws))
        assert out["done"] is None and any(e == "error" for e, _ in out["frames"])
    else:
        _allow_shell_for_post(platform, monkeypatch)
        with pytest.raises(Exception):
            await _post(platform, _body(ws))
    assert len(model.calls) == 2 and model.notes() == [] and _finish_rows(platform) == []


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_never_on_a_reply_cut_off_for_length(env, lane, monkeypatch):
    platform, ws, wire = env
    cut = LLMResponse(text="I set X and was about to", finish_reason="max_tokens",
                      usage={"input_tokens": 3, "output_tokens": 1})
    model = wire([_write("app.py", "X = 2\n"), cut])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    assert model.notes() == [] and "finish" not in done and done["truncated"] is True


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_never_at_the_last_round_nor_on_the_step_limit_report(env, lane, monkeypatch):
    platform, ws, wire = env
    # The reply ends at its LAST round: no round is left to answer a note.
    model = wire([_write("app.py", "X = 2\n"), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch, round_budget=2)
    assert model.notes() == [] and "finish" not in done
    # The reply meets its step limit (still calling tools): the step-limit
    # report, never the check.
    (ws / "app.py").write_bytes(b"X = 1\n")
    model = wire([_write("app.py", "X = 2\n", "a"), _write("b.py", "", "b"), _write("c.py", "", "c")])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch, round_budget=2)
    assert model.notes() == [] and "finish" not in done and done.get("step_limit") == {"limit": 2}


async def test_never_on_a_stop(env):
    platform, ws, wire = env
    from iron_jarvis.core.turns import TURNS

    gate = asyncio.Event()

    async def held(_model):
        await gate.wait()
        return "Done."

    model = wire([_write("app.py", "X = 2\n"), held])
    body = _body(ws, turn_id="fin-stop")
    task = asyncio.create_task(_stream(platform, body))
    for _ in range(500):
        if len(model.calls) >= 2:
            break
        await asyncio.sleep(0.01)
    assert TURNS.stop("fin-stop")
    gate.set()
    out = await asyncio.wait_for(task, 30)
    assert out["done"] is None and model.notes() == [] and _finish_rows(platform) == []


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_folder_can_turn_it_off(env, lane, monkeypatch):
    platform, ws, wire = env
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    model = wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.calls) == 3 and model.notes() == [] and "finish" not in done
    assert _finish_rows(platform) == []


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_folder_can_turn_one_part_off(env, lane, monkeypatch):
    platform, ws, wire = env
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "finish.toml").write_text("tests = false\nrequest = false\n", encoding="utf-8")
    model = wire([_write("app.py", "X = 2\n"), _write("scratch.py", "", "w2"), _shell(TEST_CMD), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    note = model.notes()[0]
    assert "## Tests" not in note and "## The request" not in note and "- `scratch.py`" in note
    assert _finish_rows(platform) == [] and done["finish"]["tests"] == "not_run"


# --------------------------------------------------------------------------- #
# it never widens a gate
# --------------------------------------------------------------------------- #


def _finish_card(frame: dict) -> bool:
    return str(frame.get("call_id") or "").startswith(F.CALL_ID_PREFIX)


@pytest.mark.parametrize("posture", ["approve_for_me", "always_ask"])
async def test_under_ask_first_the_test_command_cards_and_a_decline_is_honoured(env, posture):
    platform, ws, wire = env
    seen: list[dict] = []

    def answer(frame):
        seen.append(frame)
        # the model's own command: allowed once; the check's: declined
        return "deny" if _finish_card(frame) else "once"

    model = wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.", "1. met."])
    out = await _stream(platform, _body(ws, approval_mode=posture), answer=answer)
    mine = [f for f in seen if _finish_card(f)]
    assert len(mine) == 1 and mine[0]["tool"] == "shell" and mine[0]["args"]["command"] == TEST_CMD
    done = out["done"]
    assert done["finish"]["tests"] == "not_run" and "declined" in done["finish"]["why"]
    assert "was not run" in model.notes()[0]
    assert [(r.tool, r.ok) for r in _finish_rows(platform)] == [("shell", False)]


async def test_a_command_the_user_declined_is_never_run_by_the_check(env):
    """The model's own test command was declined on its card: it did not run,
    so the check never runs it (nor asks again)."""
    platform, ws, wire = env
    seen: list[dict] = []
    model = wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.", "1. met."])
    out = await _stream(platform, _body(ws, approval_mode="approve_for_me"),
                        answer=lambda f: (seen.append(f), "deny")[1])
    assert len(seen) == 1 and not _finish_card(seen[0]), "the check asked again"
    assert _finish_rows(platform) == [] and out["done"]["finish"]["tests"] == "none"
    assert "No tests found" in model.notes()[0]


async def test_the_post_lane_can_ask_nobody_so_under_ask_first_it_runs_nothing(env):
    """POST /chat shows no card: an ask-tier command tool is not armed there
    (the lanes' documented asymmetry), so the check has no command tool."""
    platform, ws, wire = env
    model = wire([_write("app.py", "X = 2\n"), "Done.", "1. met."])
    done = await _post(platform, _body(ws, approval_mode="approve_for_me"))
    assert _finish_rows(platform) == []
    assert done["finish"]["tests"] == "none"  # it ran no test command: there is none to run
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text(f'[[checks]]\nfiles = ["*.py"]\ncommand = "{TEST_CMD}"\n', encoding="utf-8")
    model = wire([_write("app.py", "X = 3\n"), "Done.", "1. met."])
    done = await _post(platform, _body(ws, approval_mode="approve_for_me"))
    assert _finish_rows(platform) == []
    assert done["finish"]["tests"] == "not_run" and done["finish"]["why"] == "this reply has no command tool"
    assert "was not run: this reply has no command tool" in model.notes()[0]


async def test_under_local_only_the_post_lane_runs_no_command(env, monkeypatch):
    platform, ws, wire = env
    _allow_shell_for_post(platform, monkeypatch)
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text(f'[[checks]]\nfiles = ["*.py"]\ncommand = "{TEST_CMD}"\n', encoding="utf-8")
    wire([_write("app.py", "X = 2\n"), "Done.", "1. met."])
    done = await _post(platform, _body(ws, local_only=True))
    assert _finish_rows(platform) == [] and done["finish"]["tests"] == "not_run"


async def test_under_ask_first_an_allowed_card_runs_it(env):
    platform, ws, wire = env
    wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.", "1. met."])
    out = await _stream(platform, _body(ws, approval_mode="approve_for_me"), answer=lambda f: "once")
    assert out["done"]["finish"]["tests"] == "passed"


async def test_under_local_only_it_cards_once_only(env):
    platform, ws, wire = env
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text(f'[[checks]]\nfiles = ["*.py"]\ncommand = "{TEST_CMD}"\n', encoding="utf-8")
    seen: list[dict] = []

    def answer(frame):
        seen.append(frame)
        return "conversation"  # a wider answer than a local-only card offers

    model = wire([_write("app.py", "X = 2\n"), "Done.", "1. met."])
    out = await _stream(platform, _body(ws, local_only=True), answer=answer)
    mine = [f for f in seen if _finish_card(f)]
    assert len(mine) == 1 and mine[0].get("once_only") is True
    assert "(the folder's own check) passed" in model.notes()[0]
    assert out["done"]["finish"]["tests"] == "passed"
    # read as once: nothing was granted for the conversation
    assert [d["decision"] for e, d in out["frames"] if e == "approval_resolved"] == ["once"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_under_low_trust_it_runs_no_command(env, lane, monkeypatch):
    platform, ws, wire = env
    model = wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch, trust="low")
    assert _finish_rows(platform) == []
    assert done["finish"]["tests"] == "not_run" and "low trust" in done["finish"]["why"]
    assert "low trust" in model.notes()[0]


async def test_a_git_command_the_folder_names_is_still_guarded(env):
    from iron_jarvis.harness import branches

    platform, ws, wire = env
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text(
        '[[checks]]\nfiles = ["*.py"]\ncommand = "git commit -am wip"\n', encoding="utf-8"
    )
    seen: list[dict] = []
    wire([_write("app.py", "X = 2\n"), "Done.", "1. met."])
    token = branches.set_turn_branch(
        {"branch": "ij/finish", "worktree": str(ws), "checkout": str(ws), "repo": str(ws), "base": "main"},
        str(ws),
    )
    try:
        out = await _stream(platform, _body(ws, approval_mode="yolo"),
                            answer=lambda f: (seen.append(f), "deny")[1])
    finally:
        branches.reset_turn_branch(token)
    mine = [f for f in seen if _finish_card(f)]
    # Even under auto-approve: the git guard's once-only card.
    assert len(mine) == 1 and mine[0].get("once_only") is True and mine[0].get("once_reason") == "git"
    assert out["done"]["finish"]["tests"] == "not_run"


async def test_a_projects_command_turn_asks_for_its_test_run_too(env):
    from iron_jarvis.harness import command_shell as K

    platform, ws, wire = env
    seen: list[dict] = []
    wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.", "1. met."])
    entry = K.CommandLines(name="deploy", source="project", plugin="", turn_id="t", lines=("npm run deploy",))
    token = K.set_turn_rule(K.TurnRule(started_by=entry))
    try:
        out = await _stream(platform, _body(ws, approval_mode="yolo"),
                            answer=lambda f: (seen.append(f), "once")[1])
    finally:
        K.reset_turn_rule(token)
    mine = [f for f in seen if _finish_card(f)]
    assert len(mine) == 1 and mine[0].get("once_reason") == "command"
    assert out["done"]["finish"]["tests"] == "passed"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_command_the_model_never_ran_is_never_run(env, lane, monkeypatch):
    platform, ws, wire = env
    # It ran no test command (and the folder names none): no command at all.
    model = wire([_write("app.py", "X = 2\n"), _shell('python -c "print(1)"'), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    assert _finish_rows(platform) == [] and done["finish"]["tests"] == "none"
    assert "No tests found" in model.notes()[0]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_only_the_test_part_of_the_models_command_is_run_again(env, lane, monkeypatch):
    platform, ws, wire = env
    side = 'python -c "open(\'side.txt\', \'a\').write(\'x\')"'
    wire([_write("app.py", "X = 2\n"), _shell(f"{TEST_CMD} && {side} && git status"), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    rows = _finish_rows(platform)
    assert [json.loads(r.args_json)["command"] for r in rows] == [TEST_CMD]
    assert (ws / "side.txt").read_text(encoding="utf-8") == "x", "the side effect ran once: the model's own run"
    assert done["finish"]["command"] == TEST_CMD


async def test_under_the_gpt_dialect_it_runs_through_process_start(env):
    """Codex's dialect arms process_start (exec_command), no shell: the
    check's test run goes through the command tool the turn REALLY arms."""
    platform, ws, wire = env
    patch = "*** Begin Patch\n*** Update File: app.py\n@@\n-X = 1\n+X = 2\n*** End Patch"
    model = wire([
        [ToolCall(id="p", name="apply_patch", arguments={"input": patch})],
        [ToolCall(id="e", name="exec_command", arguments={"cmd": TEST_CMD})],
        "Done.", "1. met.",
    ])
    out = await _stream(platform, _body(ws), harness="gpt")
    rows = _finish_rows(platform)
    assert [(r.tool, r.ok) for r in rows] == [("process_start", True)]
    assert json.loads(rows[0].args_json)["until_exit"] is True
    assert out["done"]["finish"]["tests"] == "passed"
    assert "remove the rest with apply_patch" in model.notes()[0] or "None." in model.notes()[0]


async def test_a_test_run_cut_off_by_its_time_limit_counts_as_failed(env):
    platform, ws, wire = env
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    slow = 'python -c \\"import time; time.sleep(30)\\"'
    (hd / "plugins.toml").write_text(
        f'[[checks]]\nfiles = ["*.md"]\ncommand = "{slow}"\ntimeout_s = 1\n', encoding="utf-8"
    )
    model = wire([_write("notes.md", "x\n"), "Done.", "1. met."])
    # (Review: the folder's own check never runs on auto-approve's per-call
    # grant — under yolo it cards; allowed once here.)
    out = await _stream(platform, _body(ws, text="write notes.md"), answer=lambda f: "once")
    assert out["done"]["finish"]["tests"] == "failed"
    assert "cut off by its time limit, so it counts as failed" in model.notes()[0]
