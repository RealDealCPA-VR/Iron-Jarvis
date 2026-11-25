"""12-a (build-out plan R2.5, D-12.1): coding work the phone started runs NOW.

THE PROOF ("done when"): a scripted phone conversation — ``/code projA: …``,
the card, "1" — starts a task in a test repository OUTSIDE the night window,
through the night shift's own pass, on a NEW branch of its own; it ends
committed on that branch with the user's main checkout untouched; the phone
hears "Started on branch …" and ONE ending, and both match the task's record.

Inside the night shift's own limits: one per folder, how many at once, and a
task queued for the window still waits for it; Emergency stop starts nothing
and stops a running phone task (its first turn is listed as Iron Jarvis's
own); a pay-per-use model with no room and a Local only chat with a cloud
model are refused in words before anything is kept.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from iron_jarvis.agents.orchestrator import EMERGENCY_STOP_REFUSAL
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import phone_code as PC
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests._fakes.phone_back import FakeTelegramBack, telegram_back
from tests._fakes.phone_code import report_json_of, text
from tests._fakes.phone_files import ME, poller
from tests.test_build_git_v1375 import git, make_repo, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import Script, wire

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(600)]


def _local(h: int, m: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 10, day, h, m).astimezone()


class PhoneScript(Script):
    """One scripted model: each task's own moves, picked by the token in its
    question."""

    def __init__(self, plans: dict[str, list]) -> None:
        super().__init__([])
        self.plans = {k: list(v) for k, v in plans.items()}

    async def _next(self, system, messages, tools):
        first = next((str(getattr(m, "content", "")) for m in messages if getattr(m, "role", "") == "user"), "")
        key = next((k for k in self.plans if k in first), "")
        self.calls.append({"tools": [t["name"] for t in tools or []], "task": key})
        moves = self.plans.get(key) or []
        return moves.pop(0) if (moves and tools) else "Done."


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


def _branch_files(repo: Path, branch: str) -> list[str]:
    base = git("merge-base", "main", branch, cwd=repo)
    out = git("diff", "--name-only", f"{base}..{branch}", cwd=repo)
    return [ln for ln in out.splitlines() if ln.strip()]


async def _ended(app, task_id: str) -> dict:
    d = app.state.d
    end = asyncio.get_running_loop().time() + 240
    while asyncio.get_running_loop().time() < end:
        await night_routes.night_pass(d)
        task = N.get_task(app.state.platform.engine, task_id)
        if task["state"] in N.ENDED:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError(f"the phone task did not end: {N.get_task(app.state.platform.engine, task_id)}")


async def test_the_phone_starts_work_outside_the_window_it_ends_committed_and_the_words_match_the_record(
        tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "projA")
    before = snapshot(repo)
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    S.create(platform.engine, str(repo), "Earlier work", {})  # a folder the user has used
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))  # the night window (23:00-07:00) is closed
    wire(app, PhoneScript({"PHONE-TASK": [
        [ToolCall(id="p1", name="Write", arguments={"file_path": "hello.py", "content": "print('hi')\n"})],
        "Done PHONE-TASK: hello.py is there.",
    ]}))
    summaries: list = []
    platform.event_bus.add_handler(
        lambda ev: summaries.append(ev.type) if ev.type == EventType.BUILD_NIGHT_SUMMARY else None)
    fake = FakeTelegramBack([])
    ch = telegram_back(fake, chat_id=ME)
    platform.notifier.add_channel("tg", ch)  # where the daemon's own handler tells the phone
    poll = poller(platform, ch)
    poll.thread_store.resolve("tg", str(ME), "Val")

    fake.updates = [text(1, "/code projA: PHONE-TASK add hello.py")]
    res = await poll.poll_once()
    assert res[0]["status"] == "code_card", res
    assert fake.texts[-1].startswith("Iron Jarvis: Start coding work in projA (")
    fake.updates = [text(2, "1")]
    res = await poll.poll_once()
    assert res[0]["status"] == "code_started", res
    [task] = N.list_tasks(platform.engine)
    assert task["start_now"] is True and task["state"] == N.QUEUED

    # Outside the window, the night shift's own pass starts it.
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]]
    started = N.get_task(platform.engine, task["id"])
    assert started["branch"].startswith("ij/")
    ended = await _ended(app, task["id"])
    assert ended["state"] == N.DONE, ended["notes"]
    # Committed on its OWN branch; the user's main checkout untouched.
    assert ended["commit_sha"]
    assert _branch_files(repo, ended["branch"]) == ["hello.py"]
    assert snapshot(repo) == before
    # The phone heard the start and ONE ending, each read from the record.
    report = PC.report_of({"report_json": report_json_of(platform, task["id"])})
    starts = [t for t in fake.texts if t.startswith("Iron Jarvis: Started")]
    assert starts == [f"Iron Jarvis: {PC.started_words(ended, report, platform.config)}"], fake.texts
    assert ended["branch"] in starts[0]
    endings = [t for t in fake.texts if t.startswith(("Iron Jarvis: Done", "Iron Jarvis: Needs you",
                                                       "Iron Jarvis: Failed", "Iron Jarvis: Checks failed",
                                                       "Iron Jarvis: Stopped"))]
    assert endings == [f"Iron Jarvis: {PC.ending_words(ended, report)}"], fake.texts
    assert endings[0].startswith("Iron Jarvis: Done in projA: 1 file changed")
    assert "Done PHONE-TASK" not in endings[0]  # never the model's own words
    # One notice per outcome: no night summary for phone work; and the whole
    # conversation is the card, the answer, the start and the ending.
    assert summaries == []
    assert len(fake.texts) == 4, fake.texts


async def test_only_tasks_the_phone_started_run_outside_the_window_one_per_folder(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))
    began: list[str] = []

    async def fake_start(d, task):
        began.append(task["id"])
        return await asyncio.to_thread(N.mark_running, engine, task["id"], "")

    monkeypatch.setattr(night_routes, "start_task", fake_start)
    a1 = N.add_task(engine, folder="C:\\work\\a", words="one", start_now=True, asked_from="comm:tg")
    a2 = N.add_task(engine, folder="C:\\work\\a", words="two", start_now=True, asked_from="comm:tg")
    b1 = N.add_task(engine, folder="C:\\work\\b", words="three", start_now=True, asked_from="comm:tg")
    tonight = N.add_task(engine, folder="C:\\work\\c", words="for tonight")
    out = await night_routes.night_pass(app.state.d)
    # Two at once (the night shift's limit), never two in one folder, and the
    # task queued for the window still waits for it.
    assert out["started"] == [a1["id"], b1["id"]] == began
    assert N.get_task(engine, a2["id"])["state"] == N.QUEUED
    assert N.get_task(engine, tonight["id"])["state"] == N.QUEUED
    # A pass while both run starts nothing more.
    assert (await night_routes.night_pass(app.state.d))["started"] == []


async def test_emergency_stop_starts_nothing_and_stops_a_running_phone_task(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    engine = platform.engine
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))
    N.add_task(engine, folder="C:\\work\\a", words="one", start_now=True, asked_from="comm:tg")
    platform.config.autonomy_kill_switch = True
    assert (await night_routes.night_pass(app.state.d))["started"] == []
    # A running phone task's first turn is Iron Jarvis's own: Emergency stop
    # reaches it and its record says so.
    running = N.add_task(engine, folder="C:\\work\\b", words="two", start_now=True, asked_from="comm:tg")
    N.mark_running(engine, running["id"])
    N.set_session(engine, running["id"], thread_id="t-phone")
    from iron_jarvis.core import turns as T

    monkeypatch.setattr(T.TURNS, "stop", lambda turn_id, by=None: True)
    N.note_unattended_turn("turn-phone", "t-phone", N.BY_PHONE)
    try:
        assert N.stop_unattended_turns(engine) == ["turn:turn-phone"]
    finally:
        N.forget_unattended_turn("turn-phone")
    assert N.has_note(N.get_task(engine, running["id"]), "emergency")


def test_a_phone_task_may_run_now_and_a_night_task_only_in_its_window(tmp_path):
    app = create_app(str(tmp_path / "home"))
    cfg = app.state.platform.config
    afternoon, night = _local(15, 0), _local(23, 30)
    assert N.may_run_now({"start_now": True}, cfg, afternoon) is True
    assert N.may_run_now({"start_now": False}, cfg, afternoon) is False
    assert N.may_run_now({"start_now": False}, cfg, night) is True
    # It keeps the PC awake while it waits for room, by day too.
    N.add_task(app.state.platform.engine, folder="C:\\work\\a", words="one", start_now=True, asked_from="comm:tg")
    assert N.awake_needed(app.state.platform.engine, cfg, afternoon) is True


@pytest.mark.parametrize("case", ["emergency", "pay", "local_only"])
async def test_emergency_stop_the_pay_limit_and_a_local_only_chat_are_refused_in_words(tmp_path, case):
    repo = make_repo(tmp_path / "projA")
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    cfg = platform.config
    local = False
    if case == "emergency":
        cfg.autonomy_kill_switch = True
        expect = EMERGENCY_STOP_REFUSAL
    elif case == "pay":
        cfg.default_provider, cfg.default_model, cfg.team_paid_cap_usd = "openai", "gpt-5", 0.0
        expect = "is billed per use"
    else:
        cfg.default_provider, cfg.default_model = "claude-cli", ""
        local = True
        expect = "This chat is Local only"
    why = await PC.problem(platform, str(repo), "fix the broken contact form", local_only=local)
    assert expect in why, why
    assert N.list_tasks(platform.engine) == []


async def test_a_phone_task_at_its_usage_limit_by_day_waits_and_a_night_task_is_stopped(tmp_path, monkeypatch):
    """The card says "at its usage limit now; it waits for the reset": a
    phone task whose account reached its limit keeps waiting by day (it runs
    now, inside its own time limit). A night task in the same state at 15:00
    is stopped, its record saying the window closed."""
    from iron_jarvis.daemon.routes import build as build_routes

    app = create_app(str(tmp_path / "home"))
    d, engine = app.state.d, app.state.platform.engine
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))  # the night window (23:00-07:00) is closed

    def waiting_task(folder: Path, *, phone: bool) -> tuple[str, str]:
        folder.mkdir(parents=True)
        row = S.create(engine, str(folder), "work", {})
        task = N.add_task(engine, folder=str(folder), words="carry on", start_now=phone,
                          asked_from="comm:tg" if phone else "")
        N.mark_running(engine, task["id"])
        N.set_session(engine, task["id"], thread_id=row.id)
        S.update(engine, row.id, setup={"account": "acct-1", "account_wait": {
            "account": "acct-1", "account_name": "Work", "resets_at": "", "armed_at": "x", "next_check_at": "",
        }})
        return task["id"], row.id

    phone_id, phone_thread = waiting_task(tmp_path / "phone", phone=True)
    night_id, night_thread = waiting_task(tmp_path / "night", phone=False)
    settled = await night_routes.settle_running(d)
    assert phone_id not in settled and night_id in settled
    held = N.get_task(engine, phone_id)
    assert held["state"] == N.RUNNING
    assert build_routes._setup_of(S.get_row(engine, phone_thread)).get("account_wait"), "its wait was cleared"
    stopped = N.get_task(engine, night_id)
    assert stopped["state"] == N.STOPPED, stopped
    assert stopped["notes"][0]["text"] == N.WAIT_WINDOW_CLOSED
    assert "account_wait" not in build_routes._setup_of(S.get_row(engine, night_thread))


async def test_a_phone_tasks_carry_on_may_run_by_day_and_a_night_tasks_may_not(tmp_path, monkeypatch):
    """Resume when ready, a restart's carry-on: a night task's carry-on starts
    only inside the window (``WINDOW_CLOSED``); one the phone started runs
    now, so its carry-on may too (still inside its own time limit)."""
    from fastapi import HTTPException

    from iron_jarvis.daemon.routes import build as build_routes

    app = create_app(str(tmp_path / "home"))
    d, platform = app.state.d, app.state.platform
    engine = platform.engine
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))
    wire(app, PhoneScript({}))

    def running_task(folder: Path, *, phone: bool) -> str:
        folder.mkdir(parents=True)
        row = S.create(engine, str(folder), "work", {})
        task = N.add_task(engine, folder=str(folder), words="carry on", start_now=phone,
                          asked_from="comm:tg" if phone else "")
        N.mark_running(engine, task["id"])
        N.set_session(engine, task["id"], thread_id=row.id)
        return row.id

    night_thread = running_task(tmp_path / "night", phone=False)
    with pytest.raises(HTTPException) as refused:
        await build_routes.start_build_turn(d, night_thread, "go on", "bt_night", started_by=N.BY_WAIT)
    assert refused.value.status_code == 409 and refused.value.detail == N.WINDOW_CLOSED
    phone_thread = running_task(tmp_path / "phone", phone=True)
    lg = await build_routes.start_build_turn(d, phone_thread, "go on", "bt_phone", started_by=N.BY_WAIT)
    assert lg is not None
    await asyncio.wait_for(lg.task, 120)
