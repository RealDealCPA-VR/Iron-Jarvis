"""THE TWO RUNS v1.386.2 LOST, AND THE TWO PROCESS TASKS, replayed through the lane.

The part 3 Phase 2 measurement (Jarvis on Claude, sonnet medium, 3 repeats)
lost ``hard-expression-evaluator`` repeats 1 and 2: each model read calc.py
and test_calc.py, then wrote calc.py with a BASH HEREDOC through the claude
dialect's ``Bash`` (``python - <<'EOF' … EOF`` in the same command as its test
run) and ended. No file tool ran, so the undo journal held no change,
``extra.finish`` was null, the finish check never ran — and the docstring's
"``min`` and ``max`` (one or more arguments)" point it exists to catch went
unchecked: ``min(4)`` raised in the hidden check.

Each run is replayed from its recorded trajectory (copied READ-ONLY into
``fixtures/finish_shell_replays_v1387.json``) through the REAL stream lane as
the bench ran it — a Build session, auto-approve, the bench's own prompt, the
claude dialect, the recorded command run in REAL Git for Windows bash (skipped
where there is none) — and then the scripted model answers the note the way
the real model would have needed to. THE CONTROL is the code before this
change (the folder witness disabled: the journal alone, v1.386.2's
``prepare``): the note never fires and the hidden check fails as it did.

``process-http-server`` and ``hard-file-watcher-process`` are the other side:
their PROGRAMS write files the task's own check reads (server.log; results.json
and done/). Replayed with real programs, the note lists those files in words
that ask to keep them, the scripted model keeps them, and the hidden checks
still pass.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import iron_jarvis
from iron_jarvis.core import procs
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import folder_changes as FC
from iron_jarvis.harness_bench.runners import PROMPT_TEMPLATE
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.sandbox import git_bash
from tests.test_finish_lanes_v1386 import NOTE_HEAD, Model, _frames

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "finish_shell_replays_v1387.json").read_text(encoding="utf-8")
)
TASKS = Path(iron_jarvis.__file__).parent / "harness_bench" / "tasks"
WHO = ("anthropic", "claude-sonnet-4-5")
FILES_HEAD = "## Files this reply made that the request does not name\n"

WIN = os.name == "nt"
BASH = git_bash.find() if WIN else None
needs_bash = pytest.mark.skipif(
    BASH is None, reason="the recorded runs wrote calc.py in Git for Windows' bash, which is not installed here",
)


@pytest.fixture(autouse=True)
def _no_kept_snapshots():
    FC.forget_kept()
    yield
    FC.forget_kept()


def _files(note: str) -> str:
    return note.split(FILES_HEAD, 1)[1].split("\n\n", 1)[0]


def _note(model: Model) -> str:
    notes = model.notes()
    assert notes, "no finish note reached the model"
    assert len(notes) == 1
    return notes[0]


def _point_number(note: str, words: str) -> int:
    for line in note.splitlines():
        head, _, rest = line.partition(". ")
        if head.isdigit() and words in rest:
            return int(head)
    raise AssertionError(f"no point says {words!r}:\n{note}")


def _points(note: str) -> list[int]:
    body = note.split("## The request, point by point\n", 1)[1].split("\n\n", 1)[0]
    return [int(ln.split(".", 1)[0]) for ln in body.splitlines() if ln.split(".", 1)[0].isdigit()]


def _hidden_check(task: str, work: Path, tmp_path: Path, pattern: str) -> subprocess.CompletedProcess:
    """The task's hidden check on a copy of the work (the bench's own command)."""
    grade = tmp_path / f"grade-{work.name}"
    shutil.copytree(work, grade, ignore=shutil.ignore_patterns("__pycache__"))
    for check in (TASKS / task / "check").iterdir():
        shutil.copy(check, grade / check.name)
    return subprocess.run(
        [sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", pattern],
        cwd=grade, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )


async def _old_folder_changes(self):
    """v1.386.2: the journal alone decides what a reply changed."""
    return None


# --------------------------------------------------------------------------- #
# the two lost runs: calc.py written by a bash heredoc
# --------------------------------------------------------------------------- #

_CLAUDE_NAMES = {"read_file": "Read", "edit_file": "Edit", "write_file": "Write", "shell": "Bash"}

#: The one line each recorded calc.py got wrong — `func(*values)` raises for
#: one argument — and the fix the scripted answer makes.
FIXES = {
    "claude-r1": ("        return fn(*vals)",
                  '        return fn(vals) if name != "abs" else fn(vals[0])'),
    "claude-r2": ('        return {"min": min, "max": max, "abs": abs}[name](*vals)',
                  '        return {"min": min, "max": max, "abs": abs}[name](vals) if name != "abs" else abs(vals[0])'),
}


def _subst(value, work: Path):
    if isinstance(value, str):
        return value.replace("{WORK/}", str(work).replace("\\", "/")).replace("{WORK}", str(work))
    if isinstance(value, dict):
        return {k: _subst(v, work) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, work) for v in value]
    return value


def _claude_args(tool: str, args: dict) -> dict:
    if tool == "read_file":
        return {"file_path": args["path"]}
    return {"command": args["command"]}


def _recorded(name: str, work: Path) -> list:
    moves: list = []
    for move in FIXTURE["runs"][name]["moves"]:
        if not move["calls"]:
            moves.append(move["text"])
            continue
        moves.append([ToolCall(id=c["id"], name=_CLAUDE_NAMES[c["tool"]],
                               arguments=_claude_args(c["tool"], _subst(c["args"], work)))
                      for c in move["calls"]])
    return moves


async def _replay_lost_run(tmp_path, name: str, finish_answer: list | None, monkeypatch) -> dict:
    platform = build_platform(str(tmp_path / f"home-{name}"))
    work = tmp_path / f"work-{name}"
    shutil.copytree(TASKS / "hard-expression-evaluator" / "repo", work)
    task = (TASKS / "hard-expression-evaluator" / "task.md").read_text(encoding="utf-8").strip()
    assert task == FIXTURE["runs"][name]["task"].strip(), "the fixture is this task's run"
    moves = _recorded(name, work)
    if finish_answer is None:
        monkeypatch.setattr(F.FinishCheck, "_folder_changes", _old_folder_changes, raising=False)
    else:
        moves += finish_answer
    model = Model(moves)
    monkeypatch.setattr(platform.router, "stream", model.stream)
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content=PROMPT_TEMPLATE.format(task=task, folder=str(work)))],
        provider=WHO[0], model=WHO[1], workspace_dir=str(work), approval_mode="yolo",
    )
    sid = f"build:replay-{name}"
    try:
        gen = await stream_chat_turn(
            platform, BUILTIN_PERSONAS, body, harness="claude", session_id=sid,
            round_budget=60, stay_in_chat=True,
        )
        frames = _frames([c async for c in gen])
    finally:
        await asyncio.to_thread(procs.end_session, sid)
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-3:]
    # Every recorded call ended here as it ended in the recorded run.
    got = {d["id"]: bool(d.get("ok")) for e, d in frames
           if e == "tool_call" and d.get("status") == "finished" and d["id"] in FIXTURE["runs"][name]["outcomes"]}
    assert got == FIXTURE["runs"][name]["outcomes"]
    return {"done": done[0], "model": model, "work": work}


@needs_bash
@pytest.mark.parametrize("name", ["claude-r1", "claude-r2"])
async def test_the_lost_run_is_shown_the_one_argument_point_and_then_passes(tmp_path, name, monkeypatch):
    old, new = FIXES[name]

    def answer(model: Model):
        note = model.calls[-1]["messages"][-1].content
        assert note.startswith(NOTE_HEAD)
        n = _point_number(note, "``min`` and ``max`` (one or more arguments)")
        lines = [f"{i}. met: checked." for i in range(1, n)] + [
            f"{n}. not met: min(4) and max(4) raise TypeError, because the call unpacks a single number."
        ]
        fix = ToolCall(id="fix-min", name="Edit",
                       arguments={"file_path": "calc.py", "old_string": old, "new_string": new})
        return ("\n".join(lines), [fix])

    rerun = [ToolCall(id="rerun", name="Bash", arguments={"command": "python -m unittest -q test_calc"})]
    out = await _replay_lost_run(tmp_path, name, [answer, rerun, "Fixed min and max with one argument."],
                                 monkeypatch)
    note = _note(out["model"])
    # The heredoc wrote calc.py (no journal row): the folder says it changed.
    assert "The request points to the docstring of `calc.py`" in note
    assert "``min`` and ``max`` (one or more arguments)" in note
    assert _files(note) == "None."  # calc.py was CHANGED, nothing was made
    # (OPEN, not this change: the recorded command is longer than the test-part
    # reader's MAX_COMMAND_CHARS, so its own `python -m unittest` is not found.)
    assert "No tests found" in note
    finish = out["done"]["finish"]
    assert finish["tests"] == "none" and finish["unmet"] == 1 and finish["after"] == "passed"
    hidden = _hidden_check("hard-expression-evaluator", out["work"], tmp_path, "test_*.py")
    assert hidden.returncode == 0, hidden.stderr[-2000:]


@needs_bash
@pytest.mark.parametrize("name", ["claude-r1", "claude-r2"])
async def test_control_with_the_journal_alone_the_lost_run_still_fails(tmp_path, name, monkeypatch):
    out = await _replay_lost_run(tmp_path, name, None, monkeypatch)
    assert out["model"].notes() == [] and "finish" not in out["done"]
    hidden = _hidden_check("hard-expression-evaluator", out["work"], tmp_path, "test_*.py")
    assert hidden.returncode != 0 and "min(4)" in hidden.stderr, hidden.stderr[-2000:]


def test_the_fixture_is_the_two_lost_runs():
    for name in ("claude-r1", "claude-r2"):
        run = FIXTURE["runs"][name]
        assert run["recorded"]["finish"] is None and run["recorded"]["passed"] is False
        assert run["recorded"]["version"] == "1.386.2"
        assert run["recorded"]["tools_used"] == ["read_file", "read_file", "shell"]
        command = run["moves"][1]["calls"][0]["args"]["command"]
        assert command.startswith("cd {WORK/} && python - <<'EOF'\n")
        assert FIXES[name][0] in command


# --------------------------------------------------------------------------- #
# the process tasks: what a PROGRAM wrote is kept
# --------------------------------------------------------------------------- #


def _py(code: str) -> str:
    return f'python -c "{code}"'


async def _replay_process_task(tmp_path, task: str, moves: list, monkeypatch) -> dict:
    platform = build_platform(str(tmp_path / f"home-{task}"))
    platform.config.sandbox["timeout_s"] = 60
    work = tmp_path / f"work-{task}"
    shutil.copytree(TASKS / task / "repo", work)
    text = (TASKS / task / "task.md").read_text(encoding="utf-8").strip()
    model = Model(moves)
    monkeypatch.setattr(platform.router, "stream", model.stream)
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content=PROMPT_TEMPLATE.format(task=text, folder=str(work)))],
        provider=WHO[0], model=WHO[1], workspace_dir=str(work), approval_mode="yolo",
    )
    sid = f"build:replay-{task}"
    try:
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, harness="generic", session_id=sid,
                                     round_budget=60, stay_in_chat=True)
        frames = _frames([c async for c in gen])
    finally:
        await asyncio.to_thread(procs.end_session, sid)
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-3:]
    failed = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished" and not d.get("ok")]
    assert not failed, failed
    return {"done": done[0], "model": model, "work": work}


def assert_keep_words(files: str) -> None:
    """The words the model reads about files a PROGRAM may have written: a
    program's own output may be what the task needs, so KEEP them — never
    the journal list's "remove the rest", never a delete instruction."""
    assert "a program running here (a server, a watcher)" in files
    assert "may be just what the task needs, so leave these as they are" in files
    assert "remove one only if you wrote it yourself as a scratch file and nothing needs it" in files
    assert "remove the rest" not in files and "Delete File" not in files


def _keep_everything(model: Model):
    note = model.calls[-1]["messages"][-1].content
    assert note.startswith(NOTE_HEAD)
    lines = [f"{n}. met: checked." for n in _points(note)]
    return "\n".join(lines) + "\nThe files listed are the program's own output: kept. Nothing needed changing."


async def test_the_http_server_task_keeps_the_servers_log_and_its_check_passes(tmp_path, monkeypatch):
    """The model starts the server, saves /status to status.json with a
    command and stops the server — every file is a command's or the server's.
    server.port and status.json are named in the request (never listed);
    server.log — which the hidden check READS — is listed with words that ask
    to keep it."""
    fetch = _py(
        "import pathlib, time, urllib.request as u; p = pathlib.Path('server.port'); "
        "[time.sleep(0.1) for _ in range(300) if not (p.exists() and p.read_text().strip())]; "
        "open('status.json', 'wb').write(u.urlopen('http://127.0.0.1:' + p.read_text().strip() + '/status', "
        "timeout=20).read())"
    )
    stop = _py(
        "import pathlib, time, urllib.request as u; port = pathlib.Path('server.port').read_text().strip(); "
        "u.urlopen('http://127.0.0.1:' + port + '/shutdown', timeout=20).read(); log = pathlib.Path('server.log'); "
        "[time.sleep(0.1) for _ in range(300) if 'stopped' not in log.read_text()]"
    )
    moves = [
        [ToolCall(id="start", name="process_start", arguments={"command": "python server.py", "wait_ms": 1000})],
        [ToolCall(id="fetch", name="shell", arguments={"command": fetch})],
        [ToolCall(id="stop", name="shell", arguments={"command": stop})],
        "Started the server, saved its /status body to status.json and stopped it with /shutdown.",
        _keep_everything,
    ]
    out = await _replay_process_task(tmp_path, "process-http-server", moves, monkeypatch)
    note = _note(out["model"])
    assert_keep_words(_files(note))
    assert _files(note) == (
        "None made with your file tools. " + F.APPEARED_HEAD + "\n- `server.log`\n" + F.APPEARED_KEEP
    )
    assert F.GONE_HEAD not in note
    assert out["done"]["finish"]["tests"] == "none" and out["done"]["finish"]["kept"] == 0
    for name in ("server.log", "server.port", "status.json"):
        assert (out["work"] / name).exists(), name
    hidden = _hidden_check("process-http-server", out["work"], tmp_path, "test_*.py")
    assert hidden.returncode == 0, hidden.stderr[-2000:]


#: A watcher that follows WATCHER.md (Decimal totals, bad rows counted, the
#: STOP drains the inbox first) — the fix the model writes with its file tool.
WATCHER_FIX = '''"""Watch inbox/ for CSV files and total them (see WATCHER.md)."""

import csv
import json
import os
import secrets
import sys
import time
from decimal import Decimal, InvalidOperation


def total_file(path):
    """The entry for one CSV file."""
    rows = 0
    bad = 0
    total = Decimal("0")
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            raw = (row.get("amount") or "").strip()
            try:
                value = Decimal(raw)
                if not value.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                bad += 1
                continue
            total += value
    return {"rows": rows, "total": str(total.quantize(Decimal("0.01"))), "bad_rows": bad}


def _log(folder, line):
    with open(os.path.join(folder, "watcher.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\\n")
        fh.flush()


def _save(folder, results):
    tmp = os.path.join(folder, "results.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(folder, "results.json"))


def _process_inbox(folder, results):
    inbox = os.path.join(folder, "inbox")
    done = os.path.join(folder, "done")
    for name in sorted(os.listdir(inbox)):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(inbox, name)
        results["files"][name] = total_file(path)
        os.replace(path, os.path.join(done, name))
        _save(folder, results)
        _log(folder, f"processed {name}")


def main(folder):
    os.makedirs(os.path.join(folder, "inbox"), exist_ok=True)
    os.makedirs(os.path.join(folder, "done"), exist_ok=True)
    run = secrets.token_hex(8)
    results = {"run": run, "files": {}}
    _save(folder, results)
    _log(folder, f"started {run}")
    stop = os.path.join(folder, "inbox", "STOP")
    while True:
        if os.path.exists(stop):
            _process_inbox(folder, results)
            os.remove(stop)
            _log(folder, "stopped")
            return 0
        _process_inbox(folder, results)
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main(os.path.dirname(os.path.abspath(__file__))))
'''


async def test_the_watcher_task_keeps_what_the_watcher_wrote_and_its_check_passes(tmp_path, monkeypatch):
    """The model fixes watcher.py with its file tool, runs the watcher, feeds
    it with commands and stops it. The WATCHER wrote results.json and moved
    the three files to done/ — the hidden check reads both — so they are
    listed with words that ask to keep them, never "remove the rest"; the
    STOP file the watcher removed itself is neither made nor gone."""
    feed = _py(
        "import os, shutil, time; "
        "[(shutil.copy(os.path.join('incoming', n), os.path.join('inbox', n + '.part')), "
        "os.replace(os.path.join('inbox', n + '.part'), os.path.join('inbox', n))) "
        "for n in ('a.csv', 'b.csv', 'c.csv')]; "
        "[time.sleep(0.1) for _ in range(300) if len([x for x in os.listdir('done') if x.endswith('.csv')]) < 3]"
    )
    stop = _py(
        "import pathlib, time; pathlib.Path('inbox/STOP').write_text(''); log = pathlib.Path('watcher.log'); "
        "[time.sleep(0.1) for _ in range(300) if not log.read_text().rstrip().endswith('stopped')]"
    )
    moves = [
        [ToolCall(id="fix", name="write_file", arguments={"path": "watcher.py", "content": WATCHER_FIX})],
        [ToolCall(id="start", name="process_start", arguments={"command": "python watcher.py", "wait_ms": 1000})],
        [ToolCall(id="feed", name="shell", arguments={"command": feed})],
        [ToolCall(id="stop", name="shell", arguments={"command": stop})],
        "Fixed watcher.py, ran it on the three files and stopped it with inbox/STOP.",
        _keep_everything,
    ]
    out = await _replay_process_task(tmp_path, "hard-file-watcher-process", moves, monkeypatch)
    note = _note(out["model"])
    assert "- `results.json`" in _files(note) and "- `done/a.csv`" in _files(note)
    assert_keep_words(_files(note))
    assert _files(note) == (
        "None made with your file tools. " + F.APPEARED_HEAD
        + "\n- `done/a.csv`\n- `done/b.csv`\n- `done/c.csv`\n- `results.json`\n" + F.APPEARED_KEEP
    )
    assert F.GONE_HEAD not in note and "STOP" not in _files(note)
    assert "watcher.log" not in _files(note)  # the request names it
    hidden = _hidden_check("hard-file-watcher-process", out["work"], tmp_path, "test_hidden_*.py")
    assert hidden.returncode == 0, hidden.stderr[-2000:]
