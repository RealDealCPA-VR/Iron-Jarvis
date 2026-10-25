"""IJ harness part 3, Phase 2 — THE TWO LOST RUNS, replayed through the lane.

The v1.378.0 benchmark lost ``hard-expression-evaluator`` twice:

* Jarvis on Claude wrote ``func(*values)`` for ``min`` / ``max``, which
  raises for one argument, so the hidden check's ``min(4)`` failed — while the
  calc.py docstring the request points to says "``min`` and ``max`` (one or
  more arguments)".
* Jarvis on glm left its own scratch ``test_spec.py`` (its last line raises
  SystemExit), and the hidden check's test discovery imported it.

Each run is replayed from its recorded trajectory (copied READ-ONLY into
``fixtures/finish_replays_v1386.json``: every round's words and calls, the
work folder as {WORK}) through the REAL stream lane exactly as the bench ran
it (a Build session, auto-approve, the bench's own prompt, the run's dialect)
with real tools in a copy of the task's own folder. The scripted model makes
the recorded calls, in the recorded rounds, and then answers the finish
note the way the real run's model would have needed to. Every replayed call
must end as it ended in the recorded run (the replay followed the run).

Shown: the finish note puts the one-argument point in front of the model in
the first run and lists ``test_spec.py`` as a file the reply made in the
second — and with that answer, the hidden check passes. The CONTROL (the
finish check absent, i.e. the code before Phase 2) replays the same run and
the hidden check fails exactly as it did in the benchmark.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import iron_jarvis
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import finish as F
from iron_jarvis.harness_bench.runners import PROMPT_TEMPLATE
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_finish_lanes_v1386 import NOTE_HEAD, Model, _frames

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "finish_replays_v1386.json").read_text(encoding="utf-8"))
TASK = Path(iron_jarvis.__file__).parent / "harness_bench" / "tasks" / "hard-expression-evaluator"

#: The recorded runs' DIALECTS (claude; glm's generic), forced on the turn —
#: the model is scripted, so the provider named is only the route's label.
RUNS = {
    "claude": {"who": ("anthropic", "claude-sonnet-4-5"), "harness": "claude"},
    "glm": {"who": ("anthropic", "claude-sonnet-4-5"), "harness": "generic"},
}
#: A recorded call that HUNG until its time limit (glm's own test case
#: `2 ** 99999 ** 99999`) is cut after this many seconds here — it times out
#: the same way, without the test waiting 90 s.
REPLAY_TIMEOUT_S = 6


# --------------------------------------------------------------------------- #
# the recorded run's machine: tail, rm and grep on PATH (Git's, on Windows)
# --------------------------------------------------------------------------- #

_SHIMS = {
    "tail": (
        "import sys\nargs = sys.argv[1:]\nn = 10\nfor i, a in enumerate(args):\n"
        "    if a.startswith('-') and a[1:].isdigit(): n = int(a[1:])\n"
        "    elif a == '-n' and i + 1 < len(args): n = int(args[i + 1])\n"
        "lines = sys.stdin.read().splitlines()\nprint('\\n'.join(lines[-n:]))\n"
    ),
    "grep": (
        "import re, sys\nargs = [a for a in sys.argv[1:]]\ninvert = '-v' in args\n"
        "pat = [a for a in args if not a.startswith('-')][0]\n"
        "for line in sys.stdin.read().splitlines():\n"
        "    if bool(re.search(pat, line)) != invert: print(line)\n"
    ),
    "rm": (
        "import os, sys\nfor a in sys.argv[1:]:\n    if not a.startswith('-') and os.path.exists(a): os.remove(a)\n"
    ),
}


@pytest.fixture()
def posix_tools(tmp_path, monkeypatch):
    """The recorded runs' commands pipe through ``tail`` / ``grep`` and remove
    a file with ``rm`` (Git's own tools on the user's PATH). The replay gives
    every machine the same three, so it ends the same everywhere."""
    if os.name != "nt":
        return
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    for name, code in _SHIMS.items():
        (bin_dir / f"{name}_shim.py").write_text(code, encoding="utf-8")
        (bin_dir / f"{name}.cmd").write_text(f'@"{sys.executable}" "{bin_dir / (name + "_shim.py")}" %*\r\n',
                                             encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))


# --------------------------------------------------------------------------- #
# the replay
# --------------------------------------------------------------------------- #

_CLAUDE_NAMES = {"read_file": "Read", "edit_file": "Edit", "write_file": "Write", "shell": "Bash"}


def _claude_args(tool: str, args: dict) -> dict:
    """A recorded REAL call in the claude dialect's own shape (the run was
    a claude-dialect turn; the lane translates it back)."""
    if tool == "read_file":
        return {"file_path": args["path"]}
    if tool == "write_file":
        return {"file_path": args["path"], "content": args["content"]}
    if tool == "edit_file":
        return {"file_path": args["path"], "old_string": args["old"], "new_string": args["new"]}
    out = {"command": args["command"]}
    return out


def _subst(value, work: str):
    if isinstance(value, str):
        return value.replace("{WORK}", work)
    if isinstance(value, dict):
        return {k: _subst(v, work) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, work) for v in value]
    return value


def _recorded_moves(name: str, work: Path) -> list:
    run = FIXTURE["runs"][name]
    moves: list = []
    for move in run["moves"]:
        if not move["calls"]:
            moves.append(move["text"])
            continue
        calls = []
        for c in move["calls"]:
            args = _subst(c["args"], str(work))
            if c["tool"] == "shell" and isinstance(args.get("timeout_s"), (int, float)):
                args = {**args, "timeout_s": min(args["timeout_s"], REPLAY_TIMEOUT_S)}
            if name == "claude":
                calls.append(ToolCall(id=c["id"], name=_CLAUDE_NAMES[c["tool"]], arguments=_claude_args(c["tool"], args)))
            else:
                calls.append(ToolCall(id=c["id"], name=c["tool"], arguments=args))
        moves.append((move["text"], calls) if move["text"] else calls)
    return moves


async def _replay(tmp_path, name: str, finish_answer: list | None, monkeypatch):
    platform = build_platform(str(tmp_path / f"home-{name}"))
    platform.config.sandbox["timeout_s"] = REPLAY_TIMEOUT_S
    work = tmp_path / f"work-{name}"
    shutil.copytree(TASK / "repo", work)
    moves = _recorded_moves(name, work)
    if finish_answer is None:
        monkeypatch.setattr(F, "start", _no_finish)
    else:
        moves += finish_answer
    model = Model(moves)
    monkeypatch.setattr(platform.router, "stream", model.stream)
    task = (TASK / "task.md").read_text(encoding="utf-8").strip()
    assert task == FIXTURE["runs"][name]["task"].strip(), "the fixture is this task's run"
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content=PROMPT_TEMPLATE.format(task=task, folder=str(work)))],
        provider=RUNS[name]["who"][0], model=RUNS[name]["who"][1], workspace_dir=str(work),
        approval_mode="yolo",
    )
    gen = await stream_chat_turn(
        platform, BUILTIN_PERSONAS, body, harness=RUNS[name]["harness"],
        session_id=f"build:replay-{name}", round_budget=60, stay_in_chat=True,
    )
    frames = _frames([c async for c in gen])
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-3:]
    return {"done": done[0], "frames": frames, "model": model, "work": work}


async def _no_finish(**_kw):
    return None


def _assert_followed_the_run(name: str, frames) -> None:
    """Every recorded call ended here as it ended in the recorded run."""
    got = {d["id"]: bool(d.get("ok")) for e, d in frames
           if e == "tool_call" and d.get("status") == "finished" and d["id"] in FIXTURE["runs"][name]["outcomes"]}
    want = FIXTURE["runs"][name]["outcomes"]
    assert set(got) == set(want), sorted(set(want) - set(got))
    assert got == want, {k: (got[k], want[k]) for k in want if got[k] != want[k]}


def _hidden_check(work: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    """The task's hidden check on a copy of the work (the bench's own command)."""
    grade = tmp_path / f"grade-{work.name}"
    shutil.copytree(work, grade, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(TASK / "check" / "test_hidden_calc.py", grade / "test_hidden_calc.py")
    return subprocess.run(
        [sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
        cwd=grade, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def _note(model: Model) -> str:
    notes = [m.content for c in model.calls for m in c["messages"]
             if m.role == "user" and str(m.content).startswith(NOTE_HEAD)]
    assert notes, "no finish note reached the model"
    return notes[0]


def _point_number(note: str, words: str) -> int:
    for line in note.splitlines():
        head, _, rest = line.partition(". ")
        if head.isdigit() and words in rest:
            return int(head)
    raise AssertionError(f"no point says {words!r}:\n{note}")


# --------------------------------------------------------------------------- #
# Jarvis on Claude: min(*values) with one argument
# --------------------------------------------------------------------------- #


async def test_the_claude_run_is_shown_the_one_argument_point_and_then_passes(tmp_path, posix_tools, monkeypatch):
    work_holder: dict = {}

    def answer(model: Model):
        note = model.calls[-1]["messages"][-1].content
        assert note.startswith(NOTE_HEAD)
        # (a) the tests the model itself ran: the test part of its last
        # command (its `rm` and `| tail` are not run again)
        work = str(work_holder["work"])
        assert f'`cd "{work}" && python -m unittest -q test_calc 2>&1`' in note and "passed (exit code 0)" in note
        # (c) the request, point by point — and the docstring it points to,
        # with the one-argument rule in front of the model
        n = _point_number(note, "``min`` and ``max`` (one or more arguments)")
        work_holder["point"] = n
        lines = [f"{i}. met: checked." for i in range(1, n)] + [
            f"{n}. not met: min(4) and max(4) raise TypeError — func(*values) unpacks a single number."
        ]
        fix = ToolCall(id="fix-min", name="Edit", arguments={
            "file_path": "calc.py",
            "old_string": 'return func(*values) if name != "abs" else func(values[0])',
            "new_string": 'return func(values) if name != "abs" else func(values[0])',
        })
        return ("\n".join(lines), [fix])

    rerun = [ToolCall(id="rerun", name="Bash", arguments={"command": "python -m unittest -q test_calc"})]
    finish_answer = [answer, rerun, "Fixed min and max with one argument; the tests pass."]
    # The recorded run, then the scripted answer to the note.
    platform_work = tmp_path / "work-claude"
    work_holder["work"] = platform_work
    out = await _replay(tmp_path, "claude", finish_answer, monkeypatch)
    _assert_followed_the_run("claude", out["frames"])
    note = _note(out["model"])
    assert "(one or more arguments)" in note
    assert "The request points to the docstring of `calc.py`" in note
    # the scratch script the run removed itself is not listed (it is gone)
    assert "_check.py" not in note.split("## Files this reply made", 1)[1].split("##", 1)[0]
    finish = out["done"]["finish"]
    assert finish["tests"] == "passed" and finish["unmet"] == 1 and finish["after"] == "passed"
    assert finish["points"] >= work_holder["point"]
    hidden = _hidden_check(out["work"], tmp_path)
    assert hidden.returncode == 0, hidden.stderr[-2000:]


async def test_control_without_the_finish_check_the_claude_run_still_fails(tmp_path, posix_tools, monkeypatch):
    out = await _replay(tmp_path, "claude", None, monkeypatch)
    _assert_followed_the_run("claude", out["frames"])
    assert "finish" not in out["done"]
    hidden = _hidden_check(out["work"], tmp_path)
    assert hidden.returncode != 0 and "min(4)" in hidden.stderr, hidden.stderr[-2000:]


# --------------------------------------------------------------------------- #
# Jarvis on glm: the scratch test_spec.py the hidden check imported
# --------------------------------------------------------------------------- #


async def test_the_glm_run_is_shown_its_scratch_test_file_and_then_passes(tmp_path, posix_tools, monkeypatch):
    def answer(model: Model):
        note = model.calls[-1]["messages"][-1].content
        files = note.split("## Files this reply made that the request does not name\n", 1)[1].split("##", 1)[0]
        # (b) the files the reply MADE (its own undo journal), still there,
        # not named in the request — its scratch test file first of all
        assert "- `test_spec.py`" in files and "- `_fix.py`" in files and "- `_trace.py`" in files
        assert "calc.py" not in files.replace("test_calc.py", "")  # changed, never made
        delete = "*** Begin Patch\n" + "".join(
            f"*** Delete File: {p}\n" for p in ("test_spec.py", "_fix.py", "_trace.py")
        ) + "*** End Patch"
        return ("All points met. test_spec.py, _fix.py and _trace.py were my own scratch files: removing them.",
                [ToolCall(id="rm-scratch", name="apply_patch", arguments={"patch": delete})])

    finish_answer = [answer, "Removed my three scratch files; calc.py and test_calc.py are as they were."]
    out = await _replay(tmp_path, "glm", finish_answer, monkeypatch)
    _assert_followed_the_run("glm", out["frames"])
    note = _note(out["model"])
    assert "- `test_spec.py`" in note
    finish = out["done"]["finish"]
    assert finish["removed"] == 3 and finish["kept"] == 0
    # its last test command's test part ran, and passed (test_calc's 4 tests)
    assert finish["tests"] == "passed" and finish["command"] == "python -m unittest test_calc 2>&1"
    assert not (out["work"] / "test_spec.py").exists()
    hidden = _hidden_check(out["work"], tmp_path)
    assert hidden.returncode == 0, hidden.stderr[-2000:]


async def test_control_without_the_finish_check_the_glm_run_still_fails(tmp_path, posix_tools, monkeypatch):
    out = await _replay(tmp_path, "glm", None, monkeypatch)
    _assert_followed_the_run("glm", out["frames"])
    assert "finish" not in out["done"] and (out["work"] / "test_spec.py").exists()
    hidden = _hidden_check(out["work"], tmp_path)
    assert hidden.returncode != 0 and "test_spec" in hidden.stderr, hidden.stderr[-2000:]
