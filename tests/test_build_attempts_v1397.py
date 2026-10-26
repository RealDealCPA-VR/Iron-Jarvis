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
import shutil
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build_attempts as BAR
from iron_jarvis.harness import attempts as A
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError, ToolCall
from iron_jarvis.providers.hold import current_hold
from tests.test_build_accounts_v1368 import enable, isolate, make_fake
from tests.test_build_git_v1375 import git, git_ok, no_real_gh, snapshot
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


@pytest.mark.parametrize("how", ["stop", "archive"])
async def test_stop_ends_every_attempt_and_every_process_it_started(app, fake, repo, monkeypatch, how):
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
    if how == "stop":
        # THE STOP: the attempts turn's own Stop (the screen's Stop button).
        status, _ = await asgi(app, "POST", "/chat/turns/att-stop/stop", {})
        assert status == 200
    else:
        # Archiving the session ("stop and archive") stops them too.
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {"stop": True})
        assert status == 200, out
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


@pytest.mark.parametrize("text, names, count, task", [
    ("Try this with Claude, Codex and the local model: fix the failing test.",
     ["Claude", "Codex", "the local model"], 0, "fix the failing test."),
    ("Fix the failing test. Try it on two models.", [], 2, "Fix the failing test."),
    ("Please try this using Codex and Claude - make add() add", ["Codex", "Claude"], 0, "make add() add"),
    ("Can you try it across three different models? The parser drops a field.",
     [], 3, "The parser drops a field."),
    ("try it with `glm-local`", ["glm-local"], 0, ""),
])
def test_the_words_that_ask_for_attempts(text, names, count, task):
    req = A.parse_request(text)
    assert req is not None
    assert req.names == names and req.count == count and req.task == task


@pytest.mark.parametrize("text", [
    "", "Fix the failing test.", "I'll try this with the new API later.", "We should try harder.",
    "Try to make it faster.", "Did you try it?",
])
def test_ordinary_sentences_ask_for_nothing(text):
    assert A.parse_request(text) is None


# --------------------------------------------------------------------------- #
# Keep: refused while running; a conflict moves nothing
# --------------------------------------------------------------------------- #


async def test_keep_is_refused_while_any_attempt_still_runs(app, fake, repo, monkeypatch):
    await enable(app)
    release = asyncio.Event()

    async def held(_world):
        await release.wait()
        return "Done waiting."

    wire_models(app, monkeypatch, World({
        "codex-cli": passing_script(),
        "ollama": [held],
    }))
    view = await new_session(app, repo)
    sid = view["id"]
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "Try this with Codex and the local model: fix add in calc.py.", "turn_id": "kr-1",
    }, timeout=120.0))

    def codex_done():
        return next((d for e, d in turn_frames(app, "kr-1")
                     if e == A.EVENT and d.get("provider") == "codex-cli" and d.get("state") == "done"), None)

    row = await until(codex_done, 60.0)
    assert row is not None
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "kr-1", "attempt": row["id"]})
    assert status == 409 and body["detail"] == A.KEEP_RUNNING
    # Nothing steers the attempts while they work (each is its model's own
    # try at the same words): a note is refused, and the page keeps it.
    status, _ = await asgi(app, "POST", "/chat/turns/kr-1/steer", {"text": "also add a docstring"})
    assert status == 404
    release.set()
    status, raw = await task
    assert status == 200 and frames_of(raw)[-1][0] == "done"
    # A session BRANCHED from this reply carries the card; its Keep refuses
    # in words (the attempts started from another branch).
    status, branched = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "kr-1", "at": "reply"})
    assert status == 201, branched
    status, other = await asgi(app, "POST", f"/build/sessions/{branched['id']}/attempts/keep",
                               {"turn_id": "kr-1", "attempt": row["id"]})
    assert status == 409 and "cannot be kept here" in other["detail"], other
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "kr-1", "attempt": row["id"]})
    assert status == 200, kept


async def test_a_merge_conflict_is_said_in_words_and_nothing_moves(app, fake, repo, monkeypatch):
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    before = snapshot(repo)
    frames = await attempts_turn(app, sid, "Try this with Codex and the local model: fix add in calc.py.", "mc-1")
    card = card_of(frames)
    codex = item_for(card, "codex-cli")
    # Meanwhile the session's own branch changed the same line another way.
    (wt / "calc.py").write_bytes(b"def add(a, b):\n    return b + a  # mine\n")
    git("commit", "-q", "-am", "mine", cwd=wt)
    session_head = git("rev-parse", "HEAD", cwd=wt)
    attempt_wt = Path(codex["git"]["worktree"])
    attempt_head = git("rev-parse", "HEAD", cwd=attempt_wt)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "mc-1", "attempt": codex["id"]})
    assert status == 409, body
    assert "would conflict in calc.py" in body["detail"] and "Nothing was changed" in body["detail"]
    # Nothing moved: the session's branch, every attempt's folder, the
    # attempt's own branch (Iron Jarvis's commit taken back: its work is in
    # its folder, not committed, exactly as before the press), the card.
    assert git("rev-parse", "HEAD", cwd=wt) == session_head
    assert git("rev-parse", "HEAD", cwd=attempt_wt) == attempt_head
    assert "calc.py" in git("status", "--porcelain", cwd=attempt_wt)
    for it in card["items"]:
        assert Path(it["git"]["worktree"]).exists()
    line = next(m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == "mc-1")
    assert "kept" not in line[A.LINE_KEY]
    assert snapshot(repo) == before


async def test_a_session_working_in_its_folder_directly_moves_onto_the_kept_branch(
    app, fake, repo, tmp_path, monkeypatch,
):
    """No branch of its own: the attempts start from the folder's last commit
    (its base), and Keep MOVES the session onto the kept attempt's branch —
    the user's main checkout is never touched."""
    await enable(app)
    wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo, branch=False)
    sid = view["id"]
    assert "git" not in view and view["folder"] == str(repo)
    before = snapshot(repo)
    frames = await attempts_turn(app, sid, "Try this with Codex and the local model: fix add in calc.py.", "nb-1")
    card = card_of(frames)
    assert card["adopt"] is True and card["base"] == "main"
    codex, local = item_for(card, "codex-cli"), item_for(card, "ollama")
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "nb-1", "attempt": codex["id"]})
    assert status == 200, kept
    setup = setup_of(app, sid)
    assert setup["git"]["branch"] == codex["branch"] and setup["git"]["checkout"] == str(repo)
    assert setup["workspace_dir"] == codex["folder"]
    # Its work is committed on the branch the session now works on.
    assert (Path(codex["folder"]) / "calc.py").read_text(encoding="utf-8").replace("\r\n", "\n") == FIXED
    assert git("status", "--porcelain", cwd=Path(codex["folder"])) == ""
    # The kept folder stays (the session works there); the other one is in
    # the trash with its branch kept.
    assert Path(codex["git"]["worktree"]).exists() and not Path(local["git"]["worktree"]).exists()
    assert git("rev-parse", "--verify", f"refs/heads/{local['branch']}", cwd=repo)
    # The main checkout: files, index, HEAD, status, current branch and main.
    assert snapshot(repo) == before


async def test_the_try_on_control_runs_exactly_the_picked_models(app, fake, repo, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": failing_script()}))
    view = await new_session(app, repo)
    frames = await attempts_turn(app, view["id"], "fix add in calc.py", "to-1",
                                 try_on=[{"provider": CODEX[0], "model": CODEX[1]},
                                         {"provider": LOCAL[0], "model": LOCAL[1]}])
    card = card_of(frames)
    assert [(i["provider"], i["model"]) for i in card["items"]] == [CODEX, LOCAL]
    assert {c["provider"] for c in world.calls} == {"codex-cli", "ollama"}
    # More than three is refused in words, not a 422.
    status, raw = await open_turn(app, view["id"], {
        "text": "fix it", "turn_id": "to-2", "try_on": [{"provider": p, "model": m} for p, m in (*MENU_PAIRS, CODEX)],
    })
    assert status == 400 and json.loads(raw)["detail"] == A.TOO_MANY


MENU_PAIRS = [(r["provider"], r["model"]) for r in MENU]


# --------------------------------------------------------------------------- #
# the account rules: the 95% floor, the pay-per-use limit
# --------------------------------------------------------------------------- #


async def test_an_account_past_the_floor_is_refused_before_anything_starts(app, fake, repo, tmp_path, monkeypatch):
    fake.states["x-plus"]["windows"][0]["usedPercent"] = 97
    fake.states["x-team"]["status"] = "unauthenticated"
    await enable(app)
    world = wire_models(app, monkeypatch, World({}))
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {
        "text": "Try this with Codex and the local model: fix add.", "turn_id": "fl-1",
    })
    assert status == 400, raw
    detail = json.loads(raw)["detail"]
    assert "97%" in detail and "Name another model to try it on" in detail
    assert world.calls == []
    _no_attempt_made(app, view["id"], tmp_path)


async def test_a_pay_per_use_model_past_the_limit_is_refused(app, fake, repo, tmp_path, monkeypatch):
    await enable(app)
    menu = [*MENU, {"provider": "google", "model": "gemini-3.8-flash", "available": True, "on_this_network": False}]
    world = wire_models(app, monkeypatch, World({}), menu=menu)
    mgr = app.state.platform.providers
    real = mgr.available
    monkeypatch.setattr(mgr, "available", lambda name: True if name == "google" else real(name))
    assert app.state.platform.config.team_paid_cap_usd == 0
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {
        "text": "Try this with Gemini and the local model: fix add.", "turn_id": "pay-1",
    })
    assert status == 400, raw
    detail = json.loads(raw)["detail"]
    assert "pay per use" in detail and "$0 a month" in detail
    assert world.calls == []
    _no_attempt_made(app, view["id"], tmp_path)


# --------------------------------------------------------------------------- #
# an attempt's own card waits on its row; a restart keeps the card
# --------------------------------------------------------------------------- #


async def test_an_attempts_approval_card_is_drawn_on_its_row_and_answered_there(app, fake, repo, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({
        "ollama": [shell("s1", "echo hello"), "Ran it."],
    }))
    view = await new_session(app, repo, approval_mode="always_ask")
    sid = view["id"]
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "Try this with the local model: say hello.", "turn_id": "ap-1",
    }, timeout=120.0))

    def asking():
        return next((d for e, d in turn_frames(app, "ap-1")
                     if e == A.EVENT and isinstance(d.get("ask"), dict) and d["ask"].get("id")), None)

    row = await until(asking, 60.0)
    assert row is not None and row["state"] == "waiting" and row["ask"]["tool"] == "shell"
    status, _ = await asgi(app, "POST", f"/chat/approvals/{row['ask']['id']}", {"decision": "once"})
    assert status == 200
    status, raw = await task
    frames = frames_of(raw)
    last = [d for e, d in frames if e == A.EVENT][-1]
    assert last["ask"] is None and last["state"] == "done"
    assert [c["provider"] for c in world.calls].count("ollama") >= 2


async def test_a_shutdown_mid_run_keeps_the_card_cut_off_and_ends_every_program(app, fake, repo, monkeypatch):
    await enable(app)
    wire_models(app, monkeypatch, World({
        "codex-cli": passing_script(),
        "ollama": [shell("s1", SLEEPER.format(n="local"))],
    }))
    view = await new_session(app, repo)
    sid = view["id"]
    task = asyncio.create_task(open_turn(app, sid, {
        "text": "Try this with Codex and the local model: fix add in calc.py.", "turn_id": "sd-1",
    }, timeout=120.0))

    def local_wt():
        return next((Path(d["git"]["worktree"]) for e, d in turn_frames(app, "sd-1")
                     if e == A.EVENT and d.get("provider") == "ollama" and isinstance(d.get("git"), dict)), None)

    wt = await until(local_wt, 30.0)
    pidfile = wt / "pid-local.txt"
    assert await until(lambda: pidfile.exists() and pidfile.read_text(encoding="utf-8").strip(), 60.0)
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    await until(lambda: any(e == A.EVENT and d.get("provider") == "codex-cli" and d.get("state") == "done"
                            for e, d in turn_frames(app, "sd-1")), 60.0)
    # The app stops (the lifespan cuts every running turn off).
    await app.state.platform.turn_logs.shutdown()
    # The page following it sees the turn end (its log says it was cut off).
    status, raw = await asyncio.wait_for(task, 30.0)
    assert frames_of(raw)[-1][0] == "error"
    assert await until(lambda: not _alive(pid), 20.0)
    line = next(m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == "sd-1")
    card = line[A.LINE_KEY]
    states = {i["provider"]: (i["state"], i["why"]) for i in card["items"]}
    assert states["codex-cli"] == ("done", "finished")
    assert states["ollama"] == ("stopped", "cut_off")
    # The card a restart shows is the line's — and the finished attempt can
    # still be kept.
    status, kept = await asgi(app, "POST", f"/build/sessions/{sid}/attempts/keep",
                              {"turn_id": "sd-1", "attempt": item_for(card, "codex-cli")["id"]})
    assert status == 200, kept


def test_a_crash_rebuild_keeps_the_card_cut_off():
    """A crash leaves only the stored frames: the boot's rebuild
    (``routes/build.replayed_reply_line``, the live writer's own fold) keeps
    the card, every attempt the frames left unfinished read as cut off."""
    from iron_jarvis.daemon.routes.build import replayed_reply_line

    frames = [
        ["attempt", json.dumps({"id": "try1", "label": "Codex", "provider": "codex-cli", "model": "gpt-5.5",
                                "state": "running", "steps": 2, "since": 1, "ask": None})],
        ["attempt", json.dumps({"id": "try2", "label": "glm-local", "provider": "ollama", "model": "glm-local",
                                "state": "done", "why": "finished", "words": "Finished", "since": 1,
                                "finish": {"tests": "passed", "command": TEST_CMD}})],
    ]
    lines = replayed_reply_line("cr-1", frames, started_at=1_700_000_000.0)
    assert lines is not None
    card = lines[-1][A.LINE_KEY]
    by_id = {i["id"]: i for i in card["items"]}
    assert by_id["try1"]["state"] == "stopped" and by_id["try1"]["why"] == "cut_off"
    assert by_id["try2"]["state"] == "done" and by_id["try2"]["tests"] == "passed"


def test_an_attempts_card_parks_the_reply_on_the_user_like_a_helpers():
    """The reply's record and its stall watch read whether the reply waits on
    the user (``routes/build._asks_after``): an attempt's own card does, until
    it is answered or the attempt is over."""
    from iron_jarvis.daemon.routes.build import _asks_after, _parks_or_answers

    asks: set[str] = set()
    frame = [("attempt", json.dumps({"id": "try1", "state": "waiting", "ask": {"id": "apr_1", "tool": "shell"}}))]
    assert _parks_or_answers(frame)
    assert _asks_after(asks, frame) is True
    assert _asks_after(asks, [("attempt", json.dumps({"id": "try1", "state": "running", "ask": None}))]) is False
    _asks_after(asks, frame)
    assert _asks_after(asks, [("attempt", json.dumps({"id": "try1", "state": "stopped", "ask": None}))]) is False


async def test_the_worktrees_are_all_or_nothing(app, fake, repo, tmp_path, monkeypatch):
    """A worktree that cannot be made undoes the ones made before it (they
    hold nothing of the user's yet): nothing is left behind, and nothing
    starts."""
    await enable(app)
    world = wire_models(app, monkeypatch, World({}))
    view = await new_session(app, repo)
    from iron_jarvis.harness import branches as Bm

    made: list[dict] = []
    original = Bm.start

    def flaky(folder, **kw):
        if made:
            raise Bm.GitRefused("Git could not make the session's branch and worktree, so nothing was made.", 409)
        got = original(folder, **kw)
        made.append(got)
        return got

    monkeypatch.setattr(Bm, "start", flaky)
    status, raw = await open_turn(app, view["id"], {
        "text": "Try this with Codex and the local model: fix add.", "turn_id": "aon-1",
    })
    assert status == 409, raw
    assert "Nothing started" in json.loads(raw)["detail"]
    assert made, "the first worktree was made"
    assert not Path(made[0]["worktree"]).exists(), "…and undone"
    assert not git_ok("rev-parse", "--verify", f"refs/heads/{made[0]['branch']}", cwd=repo)
    assert world.calls == []
    assert not any(m.get("role") == "user" for m in messages_of(app, view["id"]))


async def test_the_account_groups_hold_for_every_attempt(app, fake, repo, tmp_path, monkeypatch):
    """Work and personal accounts never mix (decision 4): a session on an
    account of the user's "work" group runs a Codex attempt only on a Codex
    account of THAT group — none there is a refusal in words, nothing
    started."""
    await enable(app)
    world = wire_models(app, monkeypatch, World({"codex-cli": passing_script()}))
    app.state.platform.config.account_groups = {"work": ["c-work"]}
    view = await new_session(app, repo)
    status, raw = await open_turn(app, view["id"], {
        "text": "Try this with Codex and the local model: fix add.", "turn_id": "grp-1",
    })
    assert status == 400, raw
    detail = json.loads(raw)["detail"]
    assert "'work' account group, which has no Codex account" in detail
    assert "Model team" not in detail, "a model from the menu is not a member"
    assert world.calls == []
    _no_attempt_made(app, view["id"], tmp_path)
    # A Codex account in the group: the attempt runs on THAT one (never the
    # first free Codex account outside it).
    app.state.platform.config.account_groups = {"work": ["c-work", "x-team"]}
    frames = await attempts_turn(app, view["id"], "Try this with Codex: fix add in calc.py.", "grp-2")
    assert item_for(card_of(frames), "codex-cli")["account"] == "x-team"
    assert {c["pin"] for c in world.calls if c["provider"] == "codex-cli"} == {"x-team"}


async def test_with_iron_proxy_off_an_attempt_runs_on_this_pcs_own_login(app, repo, monkeypatch):
    """No Iron-Proxy (the default): no account at all — the attempt runs on
    this PC's own login, exactly like the session's own turns."""
    world = wire_models(app, monkeypatch, World({"codex-cli": passing_script()}))
    view = await new_session(app, repo)
    frames = await attempts_turn(app, view["id"], "Try this with Codex: fix add in calc.py.", "off-1")
    codex = item_for(card_of(frames), "codex-cli")
    assert "account" not in codex and codex["tests"] == "passed"
    assert {c["pin"] for c in world.calls} == {None}


def test_an_attempts_limit_words_say_the_account_never_the_sessions_choice():
    """The account's own sentence, without "choose another account for this
    session" (an attempt is not the session's turn)."""
    a = A.Attempt(id="try1", target=A.Target(label="Claude", provider="claude-cli", model=""),
                  pin=P.AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max"),
                  git={}, folder="", sid="build:x~try1", turn_id="t.try1")
    a.pin.limit = {"provider": "claude-cli", "account_name": "Work Max", "kind": "usage", "resets_at": None}
    a.settle()
    assert a.state == "limit" and a.why == "limit"
    assert "Work Max" in a.words and "for this session" not in a.words
