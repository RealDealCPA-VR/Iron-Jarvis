"""v1.398.0 (night shift N1): THE PROOF — a scripted night, end to end.

Three tasks in two folders (two scratch git repositories), the night loop's
own pass (``routes/night.night_pass``) driven by a clock the test controls,
the real app factory, real git worktrees, the real stream lane; only the
model is a scripted double and the night's card wait is shortened:

* at most 2 run at once and never two in one folder (the second task of
  folder A waits for the first);
* a safety card (the agent's own ``git commit``) waits, is DECLINED after the
  wait, and its task finishes the rest — the model is told, the record says
  "needs you";
* the model's own question waits, is left unanswered, and its task finishes;
* each finished task is on its OWN branch, its finish check ran with the
  MAIN folder's .venv (a real venv; the test file in the repository passes
  only there) and says so;
* a person's turn starts while the night runs (the night limit is not theirs);
* Emergency stop halfway stops the running task, and nothing new starts;
* the user's main checkouts are byte-identical at the end.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import git, make_repo, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    asgi_get,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    until,
    wire,
)

TEST_ENV = '''import os, sys, unittest


class MainEnv(unittest.TestCase):
    def test_runs_with_the_main_folders_venv(self):
        venv = os.environ.get("VIRTUAL_ENV", "")
        self.assertIn("projA", venv)
        self.assertTrue(venv.endswith(".venv"))
        self.assertEqual(os.path.normcase(os.path.realpath(sys.prefix)),
                         os.path.normcase(os.path.realpath(venv)))
'''


def _repo_with_venv(path: Path) -> Path:
    repo = make_repo(path)
    (repo / ".gitignore").write_bytes(b".venv/\nnode_modules/\n__pycache__/\n")
    (repo / "test_env.py").write_bytes(TEST_ENV.encode("utf-8"))
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "tests", cwd=repo)
    # A REAL virtual environment in the main checkout only (nothing to
    # install: the test file needs the standard library alone).
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(repo / ".venv")],
                   check=True, capture_output=True, timeout=240)
    return repo


def _local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 26, h, m).astimezone()


class Gate:
    """A model call held until ``gate`` is set; ``then`` is its move."""

    def __init__(self, then) -> None:
        self.gate = asyncio.Event()
        self.reached = asyncio.Event()
        self.then = then

    async def __call__(self, script):
        self.reached.set()
        await self.gate.wait()
        return self.then


class NightScript(Script):
    """One scripted model for the whole night: each task's own moves, picked
    by the token in its question (turns run at the same time)."""

    def __init__(self, plans: dict[str, list]) -> None:
        super().__init__([])
        self.plans = {k: list(v) for k, v in plans.items()}
        self.seen: dict[str, list] = {k: [] for k in plans}

    async def _next(self, system, messages, tools):
        first = next((str(getattr(m, "content", "")) for m in messages if getattr(m, "role", "") == "user"), "")
        key = next((k for k in self.plans if k in first), "")
        self.calls.append({"system": system, "tools": [t["name"] for t in tools or []],
                           "messages": list(messages), "task": key})
        if key:
            self.seen[key].append(list(messages))
        moves = self.plans.get(key) or []
        move = moves.pop(0) if (moves and tools) else "Done."
        if callable(move):
            move = await move(self)
        return move


QUESTION = {"questions": [{"question": "Which name?", "header": "Name",
                           "options": [{"label": "alpha"}, {"label": "beta"}], "multiSelect": False}]}


def _task(app, task_id: str) -> dict:
    return N.get_task(app.state.platform.engine, task_id)


def _all_text(messages_lists) -> str:
    out = []
    for msgs in messages_lists:
        for m in msgs:
            out.append(str(getattr(m, "content", "") or ""))
    return "\n".join(out)


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


async def test_a_scripted_night_three_tasks_two_folders_a_declined_card_and_emergency_stop(tmp_path, monkeypatch):
    repo_a = _repo_with_venv(tmp_path / "projA")
    repo_b = make_repo(tmp_path / "projB")
    before_a, before_b = snapshot(repo_a), snapshot(repo_b)

    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    platform = app.state.platform
    # The night's clock and a short card wait (the rule, not its 15 minutes).
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 0.6)
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)

    hold_one = Gate([ToolCall(id="a1", name="Write", arguments={"file_path": "hello.py", "content": "print('hi')\n"})])
    hold_two = Gate("Never said.")
    script = wire(app, NightScript({
        "TASK-ONE": [
            hold_one,
            [ToolCall(id="a2", name="Bash", arguments={"command": "python -m unittest -q test_env"})],
            [ToolCall(id="a3", name="Bash", arguments={"command": "git commit -am wip"})],
            "Done TASK-ONE: hello.py is there; the commit is left for you.",
        ],
        "TASK-TWO": [hold_two],
        "TASK-THREE": [
            [ToolCall(id="b1", name="AskUserQuestion", arguments=QUESTION)],
            [ToolCall(id="b2", name="Write", arguments={"file_path": "b.txt", "content": "beta\n"})],
            "Done TASK-THREE: I picked beta.",
        ],
        "PERSON": ["Hello from the day."],
    }))

    async def add(folder: Path, words: str) -> dict:
        status, task = await asgi(app, "POST", "/build/night", {"folder": str(folder), "words": words})
        assert status == 201, task
        return task

    t1 = await add(repo_a, "TASK-ONE: add hello.py and run the tests")
    t2 = await add(repo_a, "TASK-TWO: tidy the readme")
    t3 = await add(repo_b, "TASK-THREE: write b.txt")

    # Outside the window: nothing starts.
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))
    out = await night_routes.night_pass(d)
    assert out["started"] == [] and _task(app, t1["id"])["state"] == N.QUEUED
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))

    # Pass 1 (inside the window): at most 2, one per folder — TASK-TWO waits
    # for folder A.
    out = await night_routes.night_pass(d)
    assert sorted(out["started"]) == sorted([t1["id"], t3["id"]])
    assert _task(app, t2["id"])["state"] == N.QUEUED
    await asyncio.wait_for(hold_one.reached.wait(), 60)
    # A pass while both run starts nothing more.
    out = await night_routes.night_pass(d)
    assert out["started"] == [] and _task(app, t2["id"])["state"] == N.QUEUED

    # A person's turn starts while the night runs.
    plain = tmp_path / "day"
    plain.mkdir()
    sid_person = new_session(app, plain)["id"]
    status, raw = await asyncio.wait_for(open_turn(app, sid_person, {"text": "PERSON hi", "turn_id": "bt-day"}), 60)
    assert status == 200 and "event: done" in raw

    hold_one.gate.set()
    one = _task(app, t1["id"])
    three = _task(app, t3["id"])

    async def ended(task_id: str) -> dict:
        async def _settled():
            await night_routes.settle_running(d)
            return _task(app, task_id)["state"] in N.ENDED

        end = asyncio.get_running_loop().time() + 120
        while asyncio.get_running_loop().time() < end:
            if await _settled():
                return _task(app, task_id)
            await asyncio.sleep(0.1)
        raise AssertionError(f"night task {task_id} did not end: {_task(app, task_id)}")

    one = await ended(t1["id"])
    three = await ended(t3["id"])

    # TASK-ONE: the agent's own commit (a safety card) waited, was declined,
    # and the task finished the rest — the model was told, the record keeps it.
    assert one["state"] == N.NEEDS_YOU, one
    declined = [n for n in one["notes"] if n["kind"] == "declined"]
    assert declined and declined[0]["text"].startswith("Needs your OK: run a command")
    told = _all_text(script.seen["TASK-ONE"])
    assert "left for the user: run a command" in told
    lg_one = platform.turn_logs.get(one["turn_id"])
    cards = [json.loads(data) for event, data in lg_one.frames if event == "approval"]
    assert cards and cards[0]["once_only"] is True and cards[0]["timeout_s"] == 1, cards
    resolved = [json.loads(data) for event, data in lg_one.frames if event == "approval_resolved"]
    assert resolved and resolved[0]["decision"] == "timeout"
    # Its finish check ran the tests again WITH THE MAIN FOLDER'S .venv (the
    # test file passes only there) and said so; the record says so too.
    assert "These ran in this branch folder with your main folder's environment" in told
    assert str(repo_a / ".venv") in told
    kinds = {n["kind"]: n["text"] for n in one["notes"]}
    assert kinds["tests"] == "Its tests passed. They ran with your main folder's environment."
    assert "used your main folder's environment" in kinds["env"] and str(repo_a / ".venv") in kinds["env"]
    line = next(m for m in reversed(messages_of(app, one["thread_id"])) if m["role"] == "assistant")
    assert (line.get("finish") or {}).get("tests") == "passed", line.get("finish")

    # TASK-THREE: its question waited, nobody answered, it decided itself.
    assert three["state"] == N.NEEDS_YOU, three
    assert any(n["kind"] == "unanswered" for n in three["notes"])
    assert "Nobody answered: this task runs at night" in _all_text(script.seen["TASK-THREE"])

    # Each on its OWN branch, its work in its own worktree.
    git_one, git_three = setup_of(app, one["thread_id"])["git"], setup_of(app, three["thread_id"])["git"]
    assert git_one["branch"].startswith("ij/") and git_three["branch"].startswith("ij/")
    assert one["branch"] == git_one["branch"] and three["branch"] == git_three["branch"]
    assert git_one["branch"] != git_three["branch"]
    assert (Path(git_one["worktree"]) / "hello.py").read_text() == "print('hi')\n"
    assert (Path(git_three["worktree"]) / "b.txt").read_text() == "beta\n"
    # The night posture was the TURN's: the session keeps the user's own.
    assert setup_of(app, one["thread_id"]).get("approval_mode") in (None, "approve_for_me")
    assert "cap" not in setup_of(app, one["thread_id"])

    # Folder A is free: the next pass starts TASK-TWO.
    out = await night_routes.night_pass(d)
    assert out["started"] == [t2["id"]]
    await asyncio.wait_for(hold_two.reached.wait(), 60)

    # EMERGENCY STOP halfway: the running task stops, nothing new starts.
    t4 = await add(repo_b, "TASK-FOUR: never starts")
    platform.config.autonomy_kill_switch = True
    stopped = await asyncio.to_thread(platform.orchestrator.stop_unattended)
    two = _task(app, t2["id"])
    assert any(s.startswith("turn:") for s in stopped)
    two = await ended(t2["id"])
    assert two["state"] == N.STOPPED and any(n["kind"] == "emergency" for n in two["notes"])
    assert "Never said." not in json.dumps(messages_of(app, two["thread_id"]))
    out = await night_routes.night_pass(d)
    assert out["started"] == [] and _task(app, t4["id"])["state"] == N.QUEUED
    hold_two.gate.set()

    # The listing says it all.
    status, listed = await asgi_get(app, "/build/night")
    assert status == 200 and listed["emergency_stop"] is True
    states = {t["id"]: t["state"] for t in listed["tasks"]}
    assert states == {t1["id"]: N.NEEDS_YOU, t2["id"]: N.STOPPED, t3["id"]: N.NEEDS_YOU, t4["id"]: N.QUEUED}

    # The user's main checkouts are byte-identical.
    assert snapshot(repo_a) == before_a
    assert snapshot(repo_b) == before_b
    # No turn Iron Jarvis started is left listed as running.
    await until(lambda: not N.unattended_turns(), 30)
    assert not N.unattended_turns()
