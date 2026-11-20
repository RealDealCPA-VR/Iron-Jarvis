"""Build-out plan R0.1: the task door PLANS a paired computer's chat history.

The door used to keep the newest 20 rows of the sender's conversation and
then 8,000 characters of them (``task_door.py`` ``[-HISTORY_ROWS_IN_TASK:]``):
a count, not a size, and whatever fell off was gone without a word. "History
is budgeted, never sliced" (``docs/rules/context.md``): the conversation now
goes through ``context.budget.plan_history``, like every chat turn.

  P1 a chat that fits arrives whole (30 short rows, where 20 used to).
  P2 the room is a share of the window the RUN will be planned against (the
     run's own resolver, so a pinned 8,192 gives 2,048), never more than
     ``TASK_TOKENS_MAX``; what does not fit keeps the newest rows whole and
     says how many earlier ones were left out, and how they began.
  P3 nothing that rode along is dropped silently, past 80 rows included.
  P4 the shape of a task that fits is unchanged; a newest row too big for the
     room alone is cut and marked; no history is byte-identical to before.
  P5 the planning runs off the event loop (a body may be 2 MB).

Driven through the real door, pairing store, orchestrator and runtime; the
model is the one stand-in (a scripted stream).
"""

from __future__ import annotations

import asyncio
import re
import threading

import httpx
import pytest
from sqlmodel import select

from iron_jarvis.agents import task_door as td
from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.peers import store as S
from iron_jarvis.providers.adapters.base import LLMResponse

DOOR_HOST = "b-pc:8787"
LAPTOP_IP = "10.0.0.1"
#: What the laptop shows the desk's door: a plain phrase, never shaped like a
#: real credential (the repository is public).
PAIRING_KEY = "the-laptops-pairing-key-for-this-test"

LEFT_OUT = re.compile(r"(\d+) earlier messages? (?:was|were) left out to fit")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _desk(tmp_path, monkeypatch):
    """The desk, with the laptop PAIRED to it (the store's own ``add``)."""
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app = create_app(str(tmp_path / "desk"))
    platform = app.state.platform
    if getattr(platform, "orchestrator", None) is None:
        platform.orchestrator = app.state.orchestrator
    S.store_for(platform).add(
        name="laptop",
        their_name="desk",
        address=f"http://{LAPTOP_IP}:8787",
        in_token=PAIRING_KEY,
        out_token="the-desks-key-for-the-laptop",
        my_address=f"http://{DOOR_HOST}",
    )
    return app, platform


class _Model:
    """The scripted model: records what each call was sent, then answers."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def stream(self, *, system=None, messages=None, tools=None, **kw):
        self.sent.append(
            "\n".join(
                str(getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else ""))
                for m in (messages or [])
            )
        )
        yield {"type": "final", "response": LLMResponse(text="Done here."),
               "provider": "openai", "model": "gpt-5"}


def _remote_rows(engine) -> list[Session]:
    with session_scope(engine) as db:
        rows = [s for s in db.exec(select(Session)) if td.is_remote_task_origin(s.origin)]
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, timeout_s: float = 30.0) -> Session:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout_s
    running = app.state.orchestrator._running
    while True:
        rows = await asyncio.to_thread(_remote_rows, app.state.platform.engine)
        done = [
            r for r in rows
            if r.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
            and (running.get(r.id) is None or running[r.id].done())
        ]
        if done or loop.time() > end:
            break
        await asyncio.sleep(0.05)
    await td.drain_replies()
    (row,) = await asyncio.to_thread(_remote_rows, app.state.platform.engine)
    return row


async def _send(app, history: list[dict], task: str = "pick up where we left off") -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(LAPTOP_IP, 50000))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOOR_HOST}") as c:
        return await c.post(
            TASK_DOOR_PATH,
            json={"task": task, "conversation_id": "athr_1", "history": history},
            headers={"Authorization": f"Bearer {PAIRING_KEY}"},
        )


def _chat(n: int, words: int = 3) -> list[dict]:
    """``n`` rows, the person first, each opening with its own marker."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"m{i:02d} " + " ".join(["word"] * words)}
        for i in range(n)
    ]


def _line(row: dict) -> str:
    who = "The person" if row["role"] == "user" else "Assistant"
    return f"{who}: {row['content']}"


def _kept_markers(text: str, n: int) -> list[int]:
    """Which rows arrived as a whole line of the conversation (a left-out
    row's opening is a "- " line, never one of these)."""
    return [i for i in range(n) if re.search(rf"^(?:The person|Assistant): m{i:02d} ", text, re.M)]


# --------------------------------------------------------------------------- #
# P1 — a chat that fits arrives whole
# --------------------------------------------------------------------------- #


async def test_a_paired_computers_chat_that_fits_reaches_the_run_whole(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    model = _Model()
    platform.router.stream = model.stream
    history = _chat(30)

    r = await _send(app, history)
    assert r.status_code == 202, r.text
    row = await _settled(app)

    assert _kept_markers(row.task, 30) == list(range(30)), (
        "the oldest rows of a chat that fits were cut off (the old 20-row tail)"
    )
    for h in history:
        assert _line(h) in row.task
    assert not LEFT_OUT.search(row.task), "nothing was left out, so nothing may say so"
    # what the run really sent its model carries them too
    assert model.sent and "m00 word word word" in model.sent[0]


# --------------------------------------------------------------------------- #
# P2 — the room follows the run's window; the newest kept whole; said
# --------------------------------------------------------------------------- #


async def test_a_chat_too_big_keeps_the_newest_whole_and_says_what_was_left_out(
    tmp_path, monkeypatch
):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    provider = str(platform.config.default_provider or "")
    assert provider, "the test app has no default provider to pin a window on"
    # The window the RUN will be planned against: a quarter of it is the room.
    platform.config.model_context_windows = {provider: 8_192}
    room = 8_192 // 4
    history = _chat(40, words=400)  # ~2,000 characters a row, ~80,000 in all

    r = await _send(app, history)
    assert r.status_code == 202, r.text
    row = await _settled(app)

    assert estimate_tokens(row.task) <= room, (
        f"the task is {estimate_tokens(row.task)} tokens; a quarter of the run's "
        f"8,192 window is {room}"
    )
    kept = _kept_markers(row.task, 40)
    assert kept and kept == list(range(40 - len(kept), 40)), (
        f"the kept rows must be the newest, in order: {kept}"
    )
    for i in kept:
        assert _line(history[i]) in row.task, f"row {i} was kept but not whole"
    said = LEFT_OUT.search(row.task)
    assert said, "rows were left out and the task does not say so"
    assert int(said.group(1)) == 40 - len(kept)
    # how they began: the oldest left-out row's opening words, as data
    assert "- The person: m00 word" in row.task


# --------------------------------------------------------------------------- #
# P3 — nothing dropped silently, past the wire's 80 rows too
# --------------------------------------------------------------------------- #


async def test_every_row_that_rode_along_is_kept_or_counted(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    platform.config.model_context_windows = {}
    history = _chat(100)

    r = await _send(app, history)
    assert r.status_code == 202, r.text
    row = await _settled(app)

    kept = _kept_markers(row.task, 100)
    assert kept == list(range(100 - len(kept), 100)) and len(kept) > 20
    said = LEFT_OUT.search(row.task)
    assert said and int(said.group(1)) + len(kept) == 100, (
        "a row that rode along was neither kept nor counted as left out"
    )


# --------------------------------------------------------------------------- #
# P4 — the text itself
# --------------------------------------------------------------------------- #


def test_no_history_is_the_task_alone_and_a_fitting_one_keeps_its_shape():
    bare = td.build_task_text("summarise the notes", None, "laptop")
    assert bare == td.TASK_INTRO.format(caller="laptop") + "\n\nsummarise the notes"
    assert td.build_task_text("summarise the notes", [], "laptop") == bare
    assert td.build_task_text("summarise the notes", [{"role": "user", "content": "  "}], "laptop") == bare

    text = td.build_task_text(
        "summarise the notes",
        [{"role": "user", "content": "we need the Q3 notes"},
         {"role": "assistant", "content": "on it"}],
        "laptop",
        window=200_000,
    )
    assert text == (
        bare
        + "\n\nThe conversation so far on that computer:\n"
        + "The person: we need the Q3 notes\nAssistant: on it"
    )


def test_the_room_is_a_quarter_of_the_runs_window_and_never_more_than_the_cap():
    assert td.task_room(8_192) == 2_048
    assert td.task_room(4_096) == 1_024
    assert td.task_room(None) == td.task_room(0) == td.task_room(-5) == td.TASK_TOKENS_MAX
    assert td.task_room(1_000_000) == td.TASK_TOKENS_MAX


def test_a_newest_row_too_big_for_the_room_alone_is_cut_and_marked():
    row = {"role": "user", "content": "start " + "z" * 11_000}
    text = td.build_task_text("look at this", [row], "laptop", window=4_000)
    assert estimate_tokens(text) <= td.task_room(4_000) + 1
    assert "The person: start zzz" in text
    assert text.endswith(" …"), "a cut row must say it was cut"
    assert not LEFT_OUT.search(text)


@pytest.mark.parametrize("task", ["go on", "read every line of this: " + "detail " * 1_400])
def test_the_task_and_the_left_out_lines_stay_inside_the_room(task):
    # A long task (~10,000 characters) is priced FIRST: the conversation gets
    # what it leaves, so the whole task still fits the room.
    history = _chat(60, words=200)  # ~1,000 characters a row: past every room
    for window in (32_000, 128_000):
        text = td.build_task_text(task, history, "laptop", window=window)
        assert estimate_tokens(text) <= td.task_room(window) + 1, window
        assert LEFT_OUT.search(text), window
        assert "The person: m58 " in text or "Assistant: m59 " in text, "the newest went"


# --------------------------------------------------------------------------- #
# P5 — planned off the event loop
# --------------------------------------------------------------------------- #


async def test_the_door_plans_the_task_off_the_event_loop(tmp_path, monkeypatch):
    app, platform = _desk(tmp_path, monkeypatch)
    platform.router.stream = _Model().stream
    where: list[bool] = []
    real = td.planned_task_text

    def recording(*a, **kw):
        where.append(threading.current_thread() is threading.main_thread())
        return real(*a, **kw)

    monkeypatch.setattr(td, "planned_task_text", recording)
    r = await _send(app, _chat(4))
    assert r.status_code == 202, r.text
    await _settled(app)
    assert where == [False], "the conversation was planned on the event loop"


@pytest.mark.parametrize("bad", ["not a list", {"role": "user"}, [1, None, "x", {"content": 5}]])
def test_a_history_of_the_wrong_shape_never_raises(bad):
    text = td.build_task_text("t", bad, "laptop")
    assert text.startswith(td.TASK_INTRO.format(caller="laptop"))
