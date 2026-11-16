"""v1.510.0 (second review, group 10 part F): a chat turn that fails or is
cancelled keeps what it already did.

L4. A reply whose round 1 wrote files and whose round 2's model call failed
said only that it failed. Both lanes' failure now carries what ran
(``tools_used``), the files made or changed (``documents``, absolute paths)
and ONE sentence the daemon builds from the undo journal's record of this
turn (``kept_note``), never from the model's words; the phone's "I hit a
problem" message carries the sentence too. A failure before any step ran is
answered exactly as before.

L6. A plain-lane turn that was CANCELLED after billed rounds wrote no usage
row (and so started no safety scan). It now writes ONE cancelled row for
the completed rounds, starts the scan, and re-raises the cancellation; a
cancel that lands after the normal row writes nothing more.

Only the model is scripted; the app, the registry, the journal and the lanes
are the real ones.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.comm.inbound import _failure_words_for_phone
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun, AgentState
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

pytestmark = pytest.mark.timeout(300)

NEW_KEYS = {"tools_used", "documents", "kept_note"}
USAGE = {"input_tokens": 120, "output_tokens": 30}


class _Boom(RuntimeError):
    """The second round's model call: the model server went away."""


def _write(i: int, path: str, content: str) -> list[ToolCall]:
    return [ToolCall(id=f"c{i}", name="write_document", arguments={"path": path, "content": content})]


class Model:
    """A scripted model for either lane. Each step is a list of ToolCalls, a
    reply, or an exception to raise."""

    def __init__(self, steps) -> None:
        self.steps = list(steps)

    def _next(self):
        move = self.steps.pop(0) if self.steps else "Done."
        if isinstance(move, BaseException):
            raise move
        return move

    async def stream(self, **kw):
        move = self._next()
        if isinstance(move, list):
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=move, usage=dict(USAGE)),
                   "provider": "mock", "model": "mock"}
            return
        yield {"type": "text", "text": move}
        yield {"type": "final", "response": LLMResponse(text=move, usage=dict(USAGE)),
               "provider": "mock", "model": "mock"}

    async def complete(self, **kw):
        move = self._next()
        if isinstance(move, list):
            return RouteResult(LLMResponse(text="", tool_calls=move, usage=dict(USAGE)), "mock", "mock")
        return RouteResult(LLMResponse(text=move, usage=dict(USAGE)), "mock", "mock")


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "plan.md").write_text("old plan\n", encoding="utf-8")
    return app, ws


_TURN = iter(range(1, 10_000))


def _body(ws: Path, text: str = "write the notes and update the plan") -> dict:
    return {"messages": [{"role": "user", "content": text}], "tools": ["write_document"],
            "auto_tools": False, "approval_mode": "approve_for_me", "workspace_dir": str(ws),
            "turn_id": f"turn-failed-{next(_TURN)}"}


def _wrote_then_failed() -> Model:
    return Model([
        _write(1, "notes.md", "new notes\n") + [
            ToolCall(id="c1b", name="write_document", arguments={"path": "plan.md", "content": "new plan\n"}),
        ],
        _Boom("the model server went away"),
    ])


async def _post_failure(app, body, model: Model) -> dict:
    app.state.platform.router.complete = model.complete
    status, raw = await _asgi_post(app, "/chat", body)
    assert status == 502, raw
    return json.loads(raw)


async def _stream_failure(app, body, model: Model) -> dict:
    app.state.platform.router.stream = model.stream

    async def answer(data):
        status, raw = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200, raw

    frames = await _drive_stream(app, body, answer)
    errors = [d for ev, d in frames if ev == "error"]
    assert errors, [ev for ev, _ in frames]
    return errors[-1]


async def _failure(lane: str, app, body, model: Model) -> dict:
    return await (_post_failure if lane == "post" else _stream_failure)(app, body, model)


# --------------------------------------------------------------------------- #
# L4: the failure says what the turn already did
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_a_failure_after_its_tools_ran_names_the_files_it_kept(desk, lane):
    app, ws = desk
    out = await _failure(lane, app, _body(ws), _wrote_then_failed())
    notes = str((ws / "notes.md").resolve())
    plan = str((ws / "plan.md").resolve())
    assert out.get("tools_used") == ["write_document", "write_document"], out
    assert sorted(out.get("documents") or []) == sorted([notes, plan]), out
    assert all(Path(p).is_absolute() for p in out["documents"])
    assert out.get("kept_note") == (
        "Before it failed, this reply made notes.md and changed plan.md. "
        "They are kept, and each can be undone on the Activity page."
    ), out
    # The old keys are unchanged beside them.
    assert out.get("detail") and out.get("title")
    assert (ws / "notes.md").read_text(encoding="utf-8") == "new notes\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_a_failure_before_any_step_ran_is_answered_exactly_as_before(desk, lane):
    app, ws = desk
    out = await _failure(lane, app, _body(ws), Model([_Boom("the model server went away")]))
    assert not NEW_KEYS & set(out), out
    assert out.get("detail") and out.get("title")


@pytest.mark.asyncio
async def test_an_undone_or_unrecorded_file_is_never_promised_an_undo(desk):
    """The sentence reads the journal: a file the journal holds no undoable
    write for is kept, and no Undo is promised for it."""
    app, ws = desk
    plat = app.state.platform
    other = ws / "elsewhere.txt"
    other.write_text("x", encoding="utf-8")
    keys = await CT.failed_work_keys(
        plat, tools_used=["write_document"], made_docs=[str(other.resolve())],
        since=utcnow(), session="chat",
    )
    assert keys["documents"] == [str(other.resolve())]
    assert keys["kept_note"] == "Before it failed, this reply wrote elsewhere.txt. It is kept."
    # A step that changed nothing it could name: the steps are counted.
    keys = await CT.failed_work_keys(
        plat, tools_used=["web_search"], made_docs=[], since=utcnow(), session="chat",
    )
    assert keys == {"tools_used": ["web_search"], "documents": [],
                    "kept_note": "Before it failed, this reply ran 1 step."}
    assert await CT.failed_work_keys(plat, tools_used=[], made_docs=[], since=utcnow()) == {}


@pytest.mark.asyncio
async def test_the_phone_hears_what_the_failed_reply_kept(desk):
    """Both phone doors word a failed turn through `_failure_words_for_phone`."""
    app, ws = desk
    plat = app.state.platform
    plat.router.complete = _wrote_then_failed().complete
    with pytest.raises(CT.ChatTurnFailed) as caught:
        await CT.run_chat_turn(plat, {}, ChatBody(**_body(ws)))
    words = _failure_words_for_phone(caught.value)
    assert words.endswith(
        "Before it failed, this reply made notes.md and changed plan.md. "
        "They are kept, and each can be undone on the Activity page."
    ), words
    # A failure with nothing kept is worded exactly as before.
    plain = CT.ChatTurnFailed("boom", {"title": "The model stopped answering", "fix": "Try again."})
    assert _failure_words_for_phone(plain) == "The model stopped answering. Try again."


# --------------------------------------------------------------------------- #
# L6: a cancelled plain-lane turn leaves its usage row and its scan
# --------------------------------------------------------------------------- #


def _chat_rows(app) -> list[AgentRun]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(AgentRun).where(AgentRun.session_id == "chat")))
        for r in rows:
            db.expunge(r)
        return rows


@pytest.fixture()
def scans(monkeypatch):
    seen: list[str] = []
    from iron_jarvis.detections import bell

    monkeypatch.setattr(bell, "schedule_chat_turn_scan", lambda platform, run_id: seen.append(run_id))
    return seen


@pytest.mark.asyncio
async def test_a_plain_turn_cancelled_after_a_tool_round_leaves_one_cancelled_row(desk, scans):
    app, ws = desk
    plat = app.state.platform
    reached = asyncio.Event()
    calls: list[int] = []

    async def complete(**kw):
        calls.append(1)
        if len(calls) == 1:
            return RouteResult(
                LLMResponse(text="", tool_calls=_write(1, "notes.md", "new notes\n"), usage=dict(USAGE)),
                "mock", "mock",
            )
        reached.set()
        await asyncio.Event().wait()  # round 2 never answers

    plat.router.complete = complete
    task = asyncio.create_task(CT.run_chat_turn(plat, {}, ChatBody(**_body(ws))))
    await asyncio.wait_for(reached.wait(), 60)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = _chat_rows(app)
    assert len(rows) == 1, rows
    assert rows[0].state == AgentState.CANCELLED
    assert (rows[0].input_tokens, rows[0].output_tokens) == (120, 30)
    assert scans == [rows[0].id]


@pytest.mark.asyncio
async def test_a_cancel_while_asking_for_the_final_answer_leaves_one_cancelled_row(desk, scans):
    """The awaits between the last round and the normal row (the final
    answer, the language check) end a cancelled turn the same way."""
    app, ws = desk
    plat = app.state.platform
    reached = asyncio.Event()
    calls: list[int] = []

    async def complete(**kw):
        calls.append(1)
        if len(calls) == 1:
            return RouteResult(
                LLMResponse(text="", tool_calls=_write(1, "notes.md", "new notes\n"), usage=dict(USAGE)),
                "mock", "mock",
            )
        if len(calls) == 2:  # no words after the step: the final answer is asked for
            return RouteResult(LLMResponse(text="", usage=dict(USAGE)), "mock", "mock")
        reached.set()
        await asyncio.Event().wait()

    plat.router.complete = complete
    task = asyncio.create_task(CT.run_chat_turn(plat, {}, ChatBody(**_body(ws))))
    await asyncio.wait_for(reached.wait(), 60)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = _chat_rows(app)
    assert [r.state for r in rows] == [AgentState.CANCELLED], rows
    assert (rows[0].input_tokens, rows[0].output_tokens) == (240, 60)
    assert scans == [rows[0].id]


@pytest.mark.asyncio
async def test_a_cancel_after_the_normal_row_writes_nothing_more(desk, scans, monkeypatch):
    app, ws = desk
    plat = app.state.platform
    plat.router.complete = Model([_write(1, "notes.md", "new notes\n"), "Saved."]).complete
    reached = asyncio.Event()

    async def hangs(*a, **kw):
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(CT, "client_question_for", hangs)
    task = asyncio.create_task(CT.run_chat_turn(plat, {}, ChatBody(**_body(ws))))
    await asyncio.wait_for(reached.wait(), 60)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = _chat_rows(app)
    assert [r.state for r in rows] == [AgentState.COMPLETED], rows
    assert scans == [rows[0].id]


@pytest.mark.asyncio
async def test_a_cancel_before_any_billed_round_writes_no_row(desk, scans):
    app, ws = desk
    plat = app.state.platform
    reached = asyncio.Event()

    async def complete(**kw):
        reached.set()
        await asyncio.Event().wait()

    plat.router.complete = complete
    task = asyncio.create_task(CT.run_chat_turn(plat, {}, ChatBody(**_body(ws))))
    await asyncio.wait_for(reached.wait(), 60)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _chat_rows(app) == []
    assert scans == []
