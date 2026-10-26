"""v1.397.0 — TRY ONE TASK ON SEVERAL MODELS (``harness/attempts``).

The long-jobs plan's Phase 5 Part B (= IJ harness part 3's Phase 8, decision 8:
opt-in per message; each attempt on the model the user named; under Local only
only local models). Driven through the REAL app factory, the real
``POST /build/sessions/{id}/turn`` route, the REAL stream lane (a harness turn
per attempt, so the finish check runs inside each one), the REAL router (its
hold and pin), REAL git in a scratch repository, the real shell tool and the
real Iron-Proxy client against the fake Iron-Proxy. Only the three MODELS are
scripted doubles (the subscription ones make the same ``iron_proxy.accounts``
calls the real adapters make), and the model menu is a fixed list — nothing
here reaches a real service, a model, the user's gateway or the running app.

THE PROOF (the brief's "Done means" 6): a scratch repository, three attempts
on three scripted models — one passes the tests, one fails them, one hits a
scripted account limit. The card shows all three; Keep merges ONLY the
passing one into the session's branch; the user's main checkout is byte-for-
byte unchanged; Stop mid-run ends every attempt's processes (checked by pid);
Local only refuses a cloud model in the list. Each pin fails on the code
before v1.397.0 (there were no attempts: a "try this with …" message was an
ordinary turn of the session's own model).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build_attempts as BAR
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import branches as B
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError, ToolCall
from iron_jarvis.providers.hold import current_hold
from tests.test_build_accounts_v1368 import enable, isolate, make_fake
from tests.test_build_git_v1375 import git, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import (
    asgi,
    frames_of,
    messages_of,
    open_turn,
    setup_of,
    turn_frames,
    turn_log,
    until,
)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

TEST_CMD = "python -m unittest -q test_calc"
BUGGY = b"def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b  # fixed\n"
WRONG = "def add(a, b):\n    return (a - b) * 1\n"
TESTS = (
    b"import unittest\n\nimport calc\n\n\nclass T(unittest.TestCase):\n"
    b"    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n"
)
TASK = "Try this with Claude, Codex and the local model: fix the add function in calc.py so the tests pass."

CLAUDE = ("claude-cli", "claude-sonnet-4-5")
CODEX = ("codex-cli", "gpt-5.5")
LOCAL = ("ollama", "glm-local")
MENU = [
    {"provider": CLAUDE[0], "model": CLAUDE[1], "available": True, "on_this_network": False},
    {"provider": CODEX[0], "model": CODEX[1], "available": True, "on_this_network": False},
    {"provider": LOCAL[0], "model": LOCAL[1], "available": True, "on_this_network": True},
]


# --------------------------------------------------------------------------- #
# the scripted models
# --------------------------------------------------------------------------- #


class World:
    """What the three scripted models do, per provider: ``moves[provider]``
    (a list of ToolCalls, a reply text, "LIMIT" — the account's next call is
    refused at its limit — or a callable ``(world) -> move``); past the script
    "Done.". Every call records the HOLD and the PIN it ran under."""

    def __init__(self, moves: dict[str, list]) -> None:
        self.moves = {k: list(v) for k, v in moves.items()}
        self.calls: list[dict[str, Any]] = []


class ScriptedModel(LLMAdapter):
    """A model double. A subscription provider makes the SAME
    ``iron_proxy.accounts`` calls the real CLI adapters make: a lease off the
    loop (a PINNED pick under a pin), ``retry_lease`` on a 429 (under a pin it
    parks the account and refuses — never the next account) and
    ``report_success_async``."""

    def __init__(self, provider: str, model: str | None, world: World) -> None:
        self.provider = provider
        self.model = model or "subscription"
        self.world = world
        if provider == "ollama":
            self._endpoint = "http://127.0.0.1:11434/v1"  # never called: a double

    def capabilities(self) -> dict[str, Any]:
        return {"tool_use": True, "vision": False, "streaming": True}

    async def complete(self, *, system, messages, tools, **kw):
        final = None
        async for frame in self.stream(system=system, messages=messages, tools=tools, **kw):
            if frame["type"] == "final":
                final = frame["response"]
        return final

    async def stream(self, *, system, messages, tools, **kw):
        sub = self.provider in ("claude-cli", "codex-cli")
        lease = await asyncio.to_thread(accounts.lease, self.provider) if sub else None
        hold = current_hold()
        self.world.calls.append({
            "provider": self.provider, "model": self.model,
            "hold": (hold.provider, hold.model) if hold is not None else None,
            "pin": getattr(P.current(), "profile_id", None),
            "account": getattr(lease, "profile_id", None),
            "tools": [t["name"] for t in tools or []],
        })
        moves = self.world.moves.setdefault(self.provider, [])
        move = moves.pop(0) if (moves and tools) else "Done."
        if callable(move):
            move = await move(self.world) if asyncio.iscoroutinefunction(move) else move(self.world)
        if move == "LIMIT":
            cause = ProviderError(f"{self.provider}: rate-limited", status_code=429)
            await accounts.retry_lease(lease, cause, {"status": 429, "text": "You've hit your limit"}, [])
            raise AssertionError("retry_lease returned under a pin")
        usage = {"input_tokens": 30, "output_tokens": 7}
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage=usage)
        else:
            resp = LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage)
        if lease is not None:
            await accounts.report_success_async(lease, usage, 5, self.model)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire_models(app, monkeypatch, world: World, menu=MENU) -> World:
    mgr = app.state.platform.providers
    for prov in ("claude-cli", "codex-cli", "ollama"):
        mgr.register(prov, lambda model=None, _p=prov: ScriptedModel(_p, model, world))
    real = mgr.available
    monkeypatch.setattr(mgr, "available", lambda name: True if name in ("claude-cli", "codex-cli", "ollama") else real(name))
    monkeypatch.setattr(BAR, "menu_rows", lambda d: [dict(r) for r in menu])
    return world


def write(cid: str, path: str, text: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="write_file", arguments={"path": path, "content": text})]


def shell(cid: str, command: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="shell", arguments={"command": command})]


def codex_patch(cid: str, old: str, new: str) -> list[ToolCall]:
    """Codex's own shape (the GPT dialect): one V4A patch."""
    patch = "\n".join([
        "*** Begin Patch", "*** Update File: calc.py", "@@", " def add(a, b):",
        f"-{old}", f"+{new}", "*** End Patch",
    ])
    return [ToolCall(id=cid, name="apply_patch", arguments={"input": patch})]


def codex_exec(cid: str, command: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="exec_command", arguments={"cmd": command})]


def passing_script() -> list:
    """Codex: fixes add() in its own dialect and runs the tests."""
    return [codex_patch("p1", "    return a - b", "    return a + b  # fixed"), codex_exec("e1", TEST_CMD),
            "Fixed add() and the tests pass.", "1. met: add() adds and the tests pass."]


def failing_script() -> list:
    return [write("w1", "calc.py", WRONG), shell("s1", TEST_CMD), "Changed add().",
            "1. not met: the test still fails."]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    no_real_gh(monkeypatch)
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def make_calc_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("config", "user.email", "dev@example.com", cwd=path)
    git("config", "user.name", "Dev", cwd=path)
    git("config", "core.autocrlf", "false", cwd=path)
    (path / "calc.py").write_bytes(BUGGY)
    (path / "test_calc.py").write_bytes(TESTS)
    (path / ".gitignore").write_bytes(b"__pycache__/\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "first", cwd=path)
    return path


@pytest.fixture
def repo(tmp_path) -> Path:
    return make_calc_repo(tmp_path / "proj")


async def new_session(app, folder: Path, **extra) -> dict:
    body = {"folder": str(folder), "provider": CLAUDE[0], "model": CLAUDE[1], "approval_mode": "yolo", **extra}
    status, view = await asgi(app, "POST", "/build/sessions", body)
    assert status == 201, view
    return view


async def attempts_turn(app, sid: str, text: str, turn_id: str, **body) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id, **body}, timeout=120.0)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


def card_of(frames) -> dict:
    done = dict(frames)["done"]
    return done[A.LINE_KEY]


def by_label(card: dict) -> dict[str, dict]:
    return {i["label"]: i for i in card["items"]}


def item_for(card: dict, provider: str) -> dict:
    return next(i for i in card["items"] if i["provider"] == provider)


# --------------------------------------------------------------------------- #
# THE PROOF: three models — passes, fails, account limit — then Keep
# --------------------------------------------------------------------------- #


async def test_three_attempts_three_models_side_by_side_and_keep_merges_only_the_passing_one(
    app, fake, repo, tmp_path, monkeypatch,
):
    await enable(app)
    world = wire_models(app, monkeypatch, World({
        "claude-cli": ["LIMIT"],
        "codex-cli": passing_script(),
        "ollama": failing_script(),
    }))
    view = await new_session(app, repo)
    sid = view["id"]
    session_wt = Path(view["git"]["worktree"])
    session_branch = view["git"]["branch"]
    before = snapshot(repo)

    frames = await attempts_turn(app, sid, TASK, "att-1")

    # Live: each attempt is its own row (an `attempt` frame per change).
    rows = [d for e, d in frames if e == A.EVENT]
    assert {d["id"] for d in rows} == {"try1", "try2", "try3"}
    card = card_of(frames)
    assert card["task"].startswith("fix the add function in calc.py")
    assert card["base"] == session_branch and card["adopt"] is False
    claude, codex, local = (item_for(card, p) for p in ("claude-cli", "codex-cli", "ollama"))

    # Each attempt on ITS OWN branch and worktree beside the repository.
    branches = {claude["branch"], codex["branch"], local["branch"]}
    assert len(branches) == 3 and all(b.startswith("ij/try-") for b in branches)
    for it in (claude, codex, local):
        assert Path(it["git"]["worktree"]).parent == tmp_path / "proj.ij-worktrees"
        assert git("rev-parse", "--verify", f"refs/heads/{it['branch']}", cwd=repo)

    # One passes the tests, one fails them, one hit its account's limit.
    assert codex["state"] == "done" and codex["why"] == "finished"
    assert codex["tests"] == "passed" and codex["finish"]["tests"] == "passed"
    assert local["state"] == "done" and local["tests"] == "failed"
    assert claude["state"] == "limit" and claude["why"] == "limit"
    assert "limit" in claude["words"].lower()
    # Files changed, time and usage, side by side.
    assert codex["files"] == ["calc.py"] and local["files"] == ["calc.py"] and claude.get("files", []) == []
    for it in (codex, local):
        assert it["ms"] >= 0 and it["usage"]["input_tokens"] > 0 and it["usage"]["output_tokens"] > 0
    # Money: a plan model's is a list-price equivalent, the local one is free.
    assert codex["cost"] == "plan" and claude["cost"] == "plan" and local["cost"] == "free"
    # Accounts: the session's own for Claude, a Codex account for Codex,
    # none for the local model.
    assert claude["account"] == "c-work" and codex["account"] == "x-plus" and "account" not in local

    # THE HOLD: every call of an attempt ran held to ITS model, pinned to ITS
    # account (never the session's Claude account for Codex), no failover.
    for call in world.calls:
        assert call["hold"] == (call["provider"], {"claude-cli": CLAUDE[1], "codex-cli": CODEX[1],
                                                   "ollama": LOCAL[1]}[call["provider"]]), call
    assert {c["pin"] for c in world.calls if c["provider"] == "codex-cli"} == {"x-plus"}
    assert {c["pin"] for c in world.calls if c["provider"] == "claude-cli"} == {"c-work"}
    assert {c["pin"] for c in world.calls if c["provider"] == "ollama"} == {None}
    # An attempt starts no helpers and no attempts of its own.
    for call in world.calls:
        assert not set(call["tools"]) & {"delegate", "spawn_agent", "Agent", "Task"}, call["tools"]

    # Each attempt changed ITS worktree only; the session's branch and the
    # user's main checkout are untouched until Keep.
    assert (Path(codex["git"]["worktree"]) / "calc.py").read_text(encoding="utf-8").replace("\r\n", "\n") == FIXED
    assert (Path(local["git"]["worktree"]) / "calc.py").read_text(encoding="utf-8").replace("\r\n", "\n") == WRONG
    assert (session_wt / "calc.py").read_bytes() == BUGGY
    assert snapshot(repo) == before

    # The reply line keeps the same card (a rejoin, a reload, a restart).
    line = next(m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == "att-1")
    assert {i["id"]: i["state"] for i in line[A.LINE_KEY]["items"]} == {i["id"]: i["state"] for i in card["items"]}
    assert "ask" not in json.dumps(line[A.LINE_KEY]["items"]).replace('"task"', "")

    # KEEP the passing one: only its work joins the session's branch.
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "att-1", "attempt": codex["id"]})
    assert status == 200, kept
    assert kept["merged_into"] == session_branch
    assert (session_wt / "calc.py").read_text(encoding="utf-8").replace("\r\n", "\n") == FIXED
    log = git("log", "--format=%s", "-3", cwd=session_wt)
    assert f"Merge branch '{codex['branch']}' into {session_branch}" in log
    # Iron Jarvis committed the attempt's work on ITS branch first.
    assert git("log", "-1", "--format=%s", codex["branch"], cwd=repo).startswith("fix the add function")
    # The others' (and the merged one's) folders went to the trash; every
    # branch stays.
    for it in (claude, codex, local):
        assert not Path(it["git"]["worktree"]).exists(), it["label"]
        assert git("rev-parse", "--verify", f"refs/heads/{it['branch']}", cwd=repo)
    trash = Path(app.state.platform.config.home) / "trash"
    assert any(p.name.startswith("worktree-") for p in trash.rglob("worktree-*"))
    # The failing attempt's work did NOT join.
    assert "(a - b) * 1" not in (session_wt / "calc.py").read_text(encoding="utf-8")
    # The user's main checkout is byte-for-byte what it was.
    assert snapshot(repo) == before
    # The card says what was kept; a second Keep is refused.
    line = next(m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == "att-1")
    assert line[A.LINE_KEY]["kept"] == codex["id"]
    assert "Kept the attempt on" in line["content"]
    status, again = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                               {"turn_id": "att-1", "attempt": local["id"]})
    assert status == 409 and "already kept" in again["detail"]


# --------------------------------------------------------------------------- #
# STOP ends every attempt: every process of every attempt (by pid)
# --------------------------------------------------------------------------- #


def _alive(pid: int) -> bool:
    import psutil

    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


SLEEPER = (
    'python -c "import os, pathlib, time; pathlib.Path(\'pid-{n}.txt\').write_text(str(os.getpid())); '
    'time.sleep(300)"'
)


async def test_stop_ends_every_attempt_and_every_process_it_started(app, fake, repo, monkeypatch):
    await enable(app)
    # Three attempts, each running a program that would outlive it: Claude's
    # and the local model's through the shell tool (in flight when Stop
    # lands), Codex's through its own exec_command (the GPT dialect's
    # process_start: still running in the background when its call returns).
    wire_models(app, monkeypatch, World({
        "claude-cli": [shell("s1", SLEEPER.format(n="claude"))],
        "codex-cli": [codex_exec("e1", SLEEPER.format(n="codex")), "Started it."],
        "ollama": [shell("s1", SLEEPER.format(n="local"))],
    }))
    view = await new_session(app, repo)
    sid = view["id"]
    before = snapshot(repo)
    task = asyncio.create_task(open_turn(app, sid, {"text": TASK, "turn_id": "att-stop"}, timeout=120.0))

    def worktrees() -> dict[str, Path]:
        rows = {d["provider"]: Path(d["git"]["worktree"]) for e, d in turn_frames(app, "att-stop")
                if e == A.EVENT and isinstance(d.get("git"), dict)}
        return rows if len(rows) == 3 else {}

    wts = await until(worktrees, 30.0)
    assert wts, turn_frames(app, "att-stop")[-5:]
    names = {"claude-cli": "claude", "codex-cli": "codex", "ollama": "local"}

    def pids() -> dict[str, int]:
        out = {}
        for prov, wt in wts.items():
            f = wt / f"pid-{names[prov]}.txt"
            try:
                out[prov] = int(f.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                return {}
        return out

    started = await until(pids, 60.0)
    assert len(started) == 3, "every attempt's program started"
    assert all(_alive(p) for p in started.values())
    # THE STOP: the attempts turn's own Stop (the screen's Stop button).
    status, _ = await asgi(app, "POST", "/chat/turns/att-stop/stop", {})
    assert status == 200
    status, raw = await task
    frames = frames_of(raw)
    assert frames[-1][0] == "done", frames[-3:]
    card = card_of(frames)
    assert {i["state"] for i in card["items"]} == {"stopped"}
    assert {i["why"] for i in card["items"]} == {"stopped"}
    # Every process of every attempt is gone — checked by its pid.
    gone = await until(lambda: not any(_alive(p) for p in started.values()), 20.0)
    assert gone, {k: _alive(p) for k, p in started.items()}
    # The turn is over (nothing runs for the session), and the main checkout
    # never moved.
    await until(lambda: (turn_log(app, "att-stop") is not None and turn_log(app, "att-stop").status != "running"))
    assert A.running_for(sid) == []
    assert snapshot(repo) == before


# --------------------------------------------------------------------------- #
# the rules: Local only, git, opt-in, names, at most three
# --------------------------------------------------------------------------- #


def _no_attempt_made(app, sid: str, tmp_path: Path) -> None:
    wt_dir = tmp_path / "proj.ij-worktrees"
    if wt_dir.exists():
        assert not any(p.name.startswith("try-") for p in wt_dir.iterdir()), "no attempt worktree was made"
    assert not any(m.get("role") == "user" for m in messages_of(app, sid)), "no question line was written"


async def test_local_only_refuses_a_cloud_model_in_the_list_and_nothing_starts(app, fake, repo, tmp_path, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({}))
    view = await new_session(app, repo)
    sid = view["id"]
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": True})
    assert status == 200
    assert setup_of(app, sid).get("local_only") is True
    status, raw = await open_turn(app, sid, {
        "text": "Try this with Codex and the local model: fix add.", "turn_id": "lo-1",
    })
    assert status == 400, raw
    detail = json.loads(raw)["detail"]
    assert "Local only" in detail and "nothing started" in detail
    assert world.calls == [], "no model was asked"
    _no_attempt_made(app, sid, tmp_path)
    # Local models only: allowed (and Local only rides each attempt).
    status, raw = await open_turn(app, sid, {"text": "Try this with the local model: fix add.", "turn_id": "lo-2"},
                                  timeout=120.0)
    assert status == 200, raw
    card = card_of(frames_of(raw))
    assert [i["provider"] for i in card["items"]] == ["ollama"]
    assert {c["provider"] for c in world.calls} == {"ollama"}


async def test_a_folder_that_is_not_a_git_repository_is_refused_in_words(app, fake, tmp_path, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({}))
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "calc.py").write_bytes(BUGGY)
    view = await new_session(app, plain)
    status, raw = await open_turn(app, view["id"], {"text": TASK, "turn_id": "ng-1"})
    assert status == 409, raw
    assert json.loads(raw)["detail"] == A.NEEDS_GIT
    assert "Trying on several models needs a git folder" in A.NEEDS_GIT
    assert world.calls == []
    assert not any(m.get("role") == "user" for m in messages_of(app, view["id"]))


async def test_ordinary_words_are_an_ordinary_turn(app, fake, repo, monkeypatch):
    """Opt-in only: a sentence whose names are none of the user's models is
    the session's own ordinary turn — no attempt, no branch."""
    await enable(app)
    world = wire_models(app, monkeypatch, World({"claude-cli": ["Sure."]}))
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {
        "text": "Try it with a smaller input and tell me what happens.", "turn_id": "ord-1",
    }, timeout=60.0)
    assert status == 200, raw
    frames = frames_of(raw)
    assert not any(e == A.EVENT for e, _ in frames)
    assert A.LINE_KEY not in dict(frames)["done"]
    assert {c["provider"] for c in world.calls} == {"claude-cli"}


@pytest.mark.parametrize("text, says", [
    ("Try this with Claude, Codex, the local model and Gemini: fix add.", "At most three"),
    ("Try this with Codex and Mistral Large: fix add.", "no model called “Mistral Large”"),
    ("Try this with Claude and Claude Code: fix add.", "same model"),
])
async def test_a_request_the_names_cannot_answer_is_refused_in_one_sentence(
    app, fake, repo, tmp_path, monkeypatch, text, says,
):
    await enable(app)
    world = wire_models(app, monkeypatch, World({}))
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {"text": text, "turn_id": "nm-1"})
    assert status in (400, 409), raw
    detail = json.loads(raw)["detail"]
    assert says in detail, detail
    assert world.calls == []
    _no_attempt_made(app, view["id"], tmp_path)


async def test_an_ambiguous_local_model_is_refused(app, fake, repo, tmp_path, monkeypatch):
    await enable(app)
    menu = [*MENU, {"provider": "ollama", "model": "qwen-local", "available": True, "on_this_network": True}]
    world = wire_models(app, monkeypatch, World({}), menu=menu)
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {"text": "Try this with Codex and the local model: fix add.",
                                                    "turn_id": "amb-1"})
    assert status == 400, raw
    assert "more than one local model" in json.loads(raw)["detail"]
    assert world.calls == []
    _no_attempt_made(app, view["id"], tmp_path)


def test_a_model_cannot_start_attempts():
    """No tool starts attempts (no module under ``tools/`` reaches this one),
    the ONE door is the build turn route reading the person's own message, and
    an attempt arms none of the doors that start other work."""
    import inspect

    from iron_jarvis.daemon.routes import build as build_routes

    src = inspect.getsource(build_routes)
    assert src.count("_attempts.maybe_start(") == 1
    tools_dir = Path(A.__file__).resolve().parents[1] / "tools"
    for f in tools_dir.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "harness.attempts" not in text and "import attempts" not in text, f
    assert {"delegate", "spawn_agent", "ask_user", "exit_plan_mode", "escalate_to_agent"} <= A.EXCLUDED_TOOLS
    assert A.parse_request("Let me try it with Codex later") is None, "only a sentence that STARTS with try"
