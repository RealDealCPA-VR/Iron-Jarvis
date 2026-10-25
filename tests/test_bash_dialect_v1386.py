"""v1.386.0: THE CLAUDE DIALECT'S ``Bash`` RUNS IN BASH.

The IJ harness shows Claude its trained command tool, ``Bash``, so Claude
writes bash; on Windows the shell and program tools ran it in cmd.exe. The
Phase 1 benchmark (Jarvis on Claude, sonnet, v1.385.x) lost 7 of its 11
failures to that — once by DELETING the project's ``server.py``: the model's
first command was ``rm -f server.port; python server.py & ...``, cmd has no
``;``, and Git's ``rm.exe`` got ``server.py`` as a file name.

Now a Build turn's ``Bash`` — the foreground ``shell`` path and
``run_in_background`` → ``process_start`` — runs through Git for Windows'
bash (``sandbox/git_bash``), the way Claude Code runs its own Bash on Windows,
with the same Build environment, folder, timeout, tree kill, decoding,
process table and end hooks; every gate is unchanged. Every OTHER path keeps
cmd.exe byte for byte. Driven through the REAL Build turn (stream lane, real
dialect translation, real registry ``invoke``, real shell tool, real Git bash
on this PC); only the model is scripted. Skipped cleanly where Git for
Windows' bash is not installed.

The replays run the benchmark's RECORDED commands verbatim (copied from
``runs/<task>/trajectory.json`` of the v1.385.x baseline) in a copy of the
task's own repository, and grade the result with the task's own hidden check.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import mode as M
from iron_jarvis.harness import process as P
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.sandbox import cmd_guard
from iron_jarvis.sandbox import git_bash
from iron_jarvis.sandbox import native as N
from iron_jarvis.sandbox import shell_tool as ST
from iron_jarvis.sandbox.base import SandboxResult
from iron_jarvis.sandbox.native import NativeSandbox
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, turn_frames, until, wire

WIN = os.name == "nt"
BASH = git_bash.find() if WIN else None
needs_bash = pytest.mark.skipif(BASH is None, reason="Git for Windows' bash is not installed here")
needs_win = pytest.mark.skipif(not WIN, reason="cmd.exe vs bash is a Windows question")
TASKS = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "harness_bench" / "tasks"

#: process-http-server, repeat 1: the command that deleted server.py.
RECORDED_R1 = (
    "rm -f server.port; python server.py & sleep 2; P=$(cat server.port); echo $P; curl -s "
    "http://127.0.0.1:$P/status > status.json; cat status.json; echo; curl -s "
    "http://127.0.0.1:$P/shutdown; sleep 2; curl -s -m 3 http://127.0.0.1:$P/status || echo down; "
    "tail -3 server.log"
)
#: process-http-server, repeat 3 (its folder replaced by the copy's).
RECORDED_R3 = (
    'cd "{folder}" && rm -f server.port; python server.py & sleep 2; P=$(cat server.port); echo port '
    "$P; curl -s http://127.0.0.1:$P/status -o status.json; cat status.json; echo; curl -s "
    "http://127.0.0.1:$P/shutdown; sleep 1; curl -s -m 2 http://127.0.0.1:$P/status || echo stopped; "
    "tail -3 server.log"
)
#: hard-file-watcher-process, repeat 2: a heredoc ("<< was unexpected at this time.").
RECORDED_HEREDOC = (
    "cd work 2>/dev/null; python - <<'E'\np='watcher.py'\ns=open(p).read()\na=s.index(\"def total_file\"); "
    "b=s.index(\"def _log\")\nnew='''_AMOUNT = re.compile(r\"[+-]?(\\\\d+(\\\\.\\\\d{1,2})?|\\\\.\\\\d{1,2})\")\n\n\n"
    "def total_file(path):\n    \"\"\"The entry for one CSV file.\"\"\"\n    rows = 0\n    bad = 0\n"
    "    total = Decimal(0)\n    with open(path, newline=\"\", encoding=\"utf-8-sig\") as fh:\n"
    "        for row in csv.DictReader(fh):\n            rows += 1\n            raw = row.get(\"amount\")\n"
    "            text = raw.strip() if isinstance(raw, str) else \"\"\n            if _AMOUNT.fullmatch(text):\n"
    "                total += Decimal(text)\n            else:\n                bad += 1\n"
    "    return {\"rows\": rows, \"total\": format(total.quantize(Decimal(\"0.01\")), \"f\"),\n"
    "            \"bad_rows\": bad}\n\n\n'''\ns=s[:a]+new+s[b:]\n"
    "s=s.replace(\"import os\\n\",\"import os\\nimport re\\n\",1).replace(\"import csv\\n\",\"import csv\\n"
    "from decimal import Decimal\\n\",1)\ns=s.replace('''        if os.path.exists(stop):\n"
    "            os.remove(stop)\n''','''        stopping = os.path.exists(stop)\n        _process_inbox(folder, "
    "results)\n        if stopping:\n            os.remove(stop)\n''').replace('''            return 0\n"
    "        _process_inbox(folder, results)\n''','''            return 0\n''')\nopen(p,'w').write(s)\nE\n"
    "sed -n 50,80p watcher.py; head -3 incoming/*.csv"
)
#: shell-count-failures, repeat 2: a ';' chain (cmd wrote "8printf: warning..." into the file).
RECORDED_CHAIN = 'printf 8 > failures.txt; python -m unittest 2>&1 | grep -c "^ERROR:"'
#: frontier-debug-price-server, repeat 1: a background '&' (cmd ran the server in the foreground).
RECORDED_BACKGROUND = "python priceserver.py --port 0 > out.txt 2>&1 &\nsleep 2; cat out.txt; python replay.py --help | head -20"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    yield a
    procs.end_all()


def _copy_task(name: str, tmp_path: Path) -> Path:
    work = tmp_path / "work"
    shutil.copytree(TASKS / name / "repo", work)
    return work


def _hidden_check(name: str, work: Path, only: str = "") -> subprocess.CompletedProcess:
    """The task's own hidden check, run in the copy (the bench's grader does
    the same in its grading copy); ``only`` picks one test by name."""
    for f in (TASKS / name / "check").iterdir():
        if f.is_file():
            shutil.copy2(f, work / f.name)
    pick = ["-k", only] if only else []
    return subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p",
                           "test_hidden_*.py", *pick], cwd=str(work), capture_output=True, timeout=120)


def _rows(app, tool: str | None = None) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at))
                if tool is None or r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


async def _bash_turn(app, work: Path, command: str, turn_id: str, *, timeout_ms: int | None = None,
                     background: bool = False, wait: float = 120.0, **session) -> str:
    args: dict = {"command": command}
    if timeout_ms:
        args["timeout"] = timeout_ms
    if background:
        args["run_in_background"] = True
    sid = new_session(app, work, approval_mode="yolo", **session)["id"]
    wire(app, Script([[ToolCall(id="b1", name="Bash", arguments=args)], "Done."],
                     who=session.get("who", ("anthropic", "claude-sonnet-4-5"))))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": turn_id}, timeout=wait)
    assert status == 200 and dict(frames_of(raw)).get("done"), raw[-600:]
    return sid


def _record_runs(monkeypatch) -> list[dict]:
    """Every ``procs.run`` / ``procs.run_to_files`` / ``procs.spawn`` the code
    under test makes — argv, shell flag and the extra keywords — passed on to
    the real one."""
    seen: list[dict] = []
    real_run, real_spawn = procs.run, procs.spawn

    def run(argv, **kw):
        seen.append({"via": "run", "argv": argv, "shell": kw.get("shell", False),
                     "startupinfo": "startupinfo" in kw, "env": kw.get("env")})
        return real_run(argv, **kw)

    def spawn(argv, **kw):
        seen.append({"via": "spawn", "argv": argv, "shell": kw.get("shell", False),
                     "startupinfo": "startupinfo" in kw, "env": kw.get("env")})
        return real_spawn(argv, **kw)

    # v1.386.0 review: bash's output goes to files (`procs.run_to_files`, the
    # call waits for bash, not for a background job's pipe) — the same "run".
    real_files = procs.run_to_files

    def run_to_files(argv, **kw):
        seen.append({"via": "run", "argv": argv, "shell": False, "files": True,
                     "startupinfo": "startupinfo" in kw, "env": kw.get("env")})
        return real_files(argv, **kw)

    monkeypatch.setattr(procs, "run", run)
    monkeypatch.setattr(procs, "run_to_files", run_to_files)
    monkeypatch.setattr(procs, "spawn", spawn)
    return seen


def _of(seen: list[dict], command: str, via: str = "run") -> list[dict]:
    """The recorded ``via`` calls (``run``: the shell tool; ``spawn``: the
    program tools — ``run`` itself spawns, so each is counted once) that ran
    ``command`` (as cmd's line or bash's environment variable)."""
    out = []
    for s in seen:
        env = s.get("env") or {}
        if s["via"] == via and (s["argv"] == command or env.get(git_bash.COMMAND_VAR) == command):
            out.append(s)
    return out


def _server_processes(work: Path) -> list:
    import psutil

    alive = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if ("server.py" in cmd or "priceserver.py" in cmd) and p.cwd() and Path(p.cwd()) == work:
                alive.append(p.pid)
        except Exception:  # noqa: BLE001 — another user's process, or gone
            continue
    return alive


def _port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# the switch: the dialect tool, never an argument
# --------------------------------------------------------------------------- #


def test_only_claude_codes_bash_writes_bash():
    from iron_jarvis.harness import dialects as D

    marked = {(d.id, t.name) for d in D.DIALECTS.values() for t in d.tools if t.shell_syntax}
    assert marked == {("claude", "Bash")}
    assert D.speaks_bash(D.CLAUDE) and not D.speaks_bash(D.GPT) and not D.speaks_bash(D.QWEN)
    assert not D.speaks_bash(D.GENERIC)


def test_ctx_for_marks_a_bash_call_and_nothing_else(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path / "home"))
    turn = M.build(registry=platform.registry, permissions=platform.permissions, requested="claude",
                   provider="anthropic", model="claude-sonnet-4-5", stream=True, today="")
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r", config=platform.config,
                      event_bus=None, engine=None)
    calls = turn.translate_calls([
        ToolCall(id="1", name="Bash", arguments={"command": "ls; pwd"}),
        ToolCall(id="2", name="Bash", arguments={"command": "npm run dev", "run_in_background": True}),
        ToolCall(id="3", name="Read", arguments={"file_path": "a.py"}),
        # a model writing the REAL name was shown no such tool: its own shell
        ToolCall(id="4", name="shell", arguments={"command": "dir"}),
        # an argument can never ask for bash
        ToolCall(id="5", name="Bash", arguments={"command": "x", "shell_syntax": "cmd"}),
    ])
    got = [(c.name, turn.ctx_for(ctx, c).shell_syntax, turn.ctx_for(ctx, c).called_as) for c in calls]
    assert got == [("shell", "bash", "Bash"), ("process_start", "bash", "Bash"), ("read_file", "", "Read"),
                   ("shell", "", ""), ("shell", "bash", "Bash")]
    assert "shell_syntax" not in calls[4].arguments


def test_the_other_dialects_never_mark_a_call(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path / "home"))
    ctx = ToolContext(workspace=tmp_path, session_id="build:x", agent_run_id="r", config=platform.config,
                      event_bus=None, engine=None)
    for dialect, name, args in (("gpt", "exec_command", {"cmd": "ls; pwd"}),
                                ("qwen", "run_shell_command", {"command": "ls; pwd"}),
                                ("generic", "shell", {"command": "ls; pwd"})):
        turn = M.build(registry=platform.registry, permissions=platform.permissions, requested=dialect,
                       provider="anthropic", model="claude-sonnet-4-5", stream=True, today="")
        (call,) = turn.translate_calls([ToolCall(id="1", name=name, arguments=args)])
        assert turn.ctx_for(ctx, call).shell_syntax == "", dialect


def test_only_the_harness_ever_sets_the_shell_syntax():
    """No other code path (a lane, the runtime, a route) writes it: plain
    chat, agents, schedules and every other door keep the platform's shell.
    The dialect table DECLARES which tool writes bash; only the harness's
    ``ctx_for`` puts it on a call's context."""
    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
    writers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if "shell_syntax=" in p.read_text(encoding="utf-8"))
    assert writers == [os.path.join("harness", "dialects.py"), os.path.join("harness", "mode.py")], writers
    mode = (root / "harness" / "mode.py").read_text(encoding="utf-8")
    assert mode.count("shell_syntax=") == 1 and "replace(out, shell_syntax=syntax)" in mode


# --------------------------------------------------------------------------- #
# the replays: the benchmark's own commands, before and after
# --------------------------------------------------------------------------- #


def _rm_on_build_path() -> bool:
    env = N.build_env()
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    return shutil.which("rm", path=path) is not None


def _warm_python() -> None:
    """The recorded command gives its server the model's own ``sleep 2`` to
    write ``server.port``; start the ``python`` bash will find once first, so
    a cold disk cache on a loaded runner cannot eat that margin (the replay
    tests the shell, not the runner's disk)."""
    env = N.build_env()
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    python = shutil.which("python", path=path)
    if python:
        subprocess.run([python, "-c", "import csv, hashlib, http.server, json, threading"],
                       capture_output=True, timeout=60)


@needs_bash
async def test_replay_the_deletion_server_py_survives_and_the_server_answered_and_stopped(app, tmp_path):
    """RED on v1.385.1 (see the control below): ``server.py`` was deleted."""
    work = _copy_task("process-http-server", tmp_path)
    _warm_python()
    sid = await _bash_turn(app, work, RECORDED_R1, "bash-replay-r1")
    assert (work / "server.py").exists(), "server.py was deleted"
    status = json.loads((work / "status.json").read_text(encoding="utf-8"))
    assert status["ok"] is True and status["items"] >= 1
    port = int((work / "server.port").read_text(encoding="utf-8"))
    assert not _port_answers(port), "the server is still listening"
    assert _server_processes(work) == [], "the server process is still alive"
    log = [line.strip() for line in (work / "server.log").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert log[-1] == "stopped" and "GET /status" in log and "GET /shutdown" in log
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert row.called_as == "Bash" and row.ok is True
    assert '"ok": true' in (row.output or "") and "down" in (row.output or "")
    # The task's own hidden check grades it: every part passes.
    graded = _hidden_check("process-http-server", work)
    assert graded.returncode == 0, graded.stderr.decode("utf-8", "replace")[-1500:]


@needs_bash
async def test_replay_the_deletion_with_its_cd_prefix(app, tmp_path):
    work = _copy_task("process-http-server", tmp_path)
    _warm_python()
    await _bash_turn(app, work, RECORDED_R3.format(folder=str(work).replace("\\", "/")), "bash-replay-r3")
    assert (work / "server.py").exists()
    assert _hidden_check("process-http-server", work).returncode == 0
    assert _server_processes(work) == []


@needs_win
@pytest.mark.skipif(not _rm_on_build_path(), reason="needs Git's rm.exe on the user's PATH (the case that bit)")
async def test_control_the_old_code_deletes_server_py(app, tmp_path, monkeypatch):
    """CONTROL: the same Build turn with no Git bash and no guard — exactly
    v1.385.1's path — and ``server.py`` is gone (the benchmark's failure)."""
    monkeypatch.setattr(git_bash, "find", lambda: None)
    monkeypatch.setattr(ST, "semicolon_refusal", lambda command: "")
    work = _copy_task("process-http-server", tmp_path)
    await _bash_turn(app, work, RECORDED_R1, "bash-control-r1")
    assert not (work / "server.py").exists(), "the control did not reproduce the deletion"
    assert _server_processes(work) == []


@needs_win
async def test_without_bash_the_guard_keeps_server_py(app, tmp_path, monkeypatch):
    """No Git for Windows bash on the PC: cmd.exe runs Claude's Bash, and the
    ``;`` guard refuses the command before anything runs."""
    monkeypatch.setattr(git_bash, "find", lambda: None)
    work = _copy_task("process-http-server", tmp_path)
    sid = await _bash_turn(app, work, RECORDED_R1, "bash-guard-r1")
    assert (work / "server.py").exists()
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert row.ok is False and cmd_guard.refusal_sentence("rm") in (row.output or "")


@needs_bash
async def test_replay_a_heredoc(app, tmp_path):
    """RED on v1.385.1: "<< was unexpected at this time." and nothing changed."""
    work = _copy_task("hard-file-watcher-process", tmp_path)
    before = (work / "watcher.py").read_bytes()
    sid = await _bash_turn(app, work, RECORDED_HEREDOC, "bash-heredoc")
    after = (work / "watcher.py").read_text(encoding="utf-8")
    assert after.encode() != before
    assert "_AMOUNT = re.compile(" in after and "from decimal import Decimal" in after
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    out = row.output or ""
    assert "<< was unexpected" not in out, out
    # ... and the lines AFTER the heredoc ran too (`head -3 incoming/*.csv`).
    assert "==> incoming/a.csv <==" in out and "==> incoming/c.csv <==" in out, out
    # The model's rewrite does what it meant: the hidden check's own total_file test passes.
    graded = _hidden_check("hard-file-watcher-process", work, "test_total_file")
    assert graded.returncode == 0, graded.stderr.decode("utf-8", "replace")[-1500:]
    assert b"Ran 1 test" in graded.stderr


@needs_win
async def test_control_the_heredoc_in_cmd(app, tmp_path, monkeypatch):
    monkeypatch.setattr(git_bash, "find", lambda: None)
    work = _copy_task("hard-file-watcher-process", tmp_path)
    before = (work / "watcher.py").read_bytes()
    sid = await _bash_turn(app, work, RECORDED_HEREDOC, "bash-heredoc-control")
    assert (work / "watcher.py").read_bytes() == before
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert "<< was unexpected" in (row.output or "")


@needs_bash
async def test_replay_a_semicolon_chain(app, tmp_path):
    """RED on v1.385.1: failures.txt held "8printf: warning: ignoring excess
    arguments..." and the hidden check failed."""
    work = _copy_task("shell-count-failures", tmp_path)
    sid = await _bash_turn(app, work, RECORDED_CHAIN, "bash-chain")
    assert (work / "failures.txt").read_text(encoding="utf-8") == "8"
    # MOVED ON PURPOSE (the finish check sees commands): `printf 8 >
    # failures.txt` is a COMMAND's write, so the reply changed files and its
    # finish check now runs — re-running the test part of the model's own
    # command, in bash, as its own ledger row.
    rows = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    (row,) = [r for r in rows if r.called_as != "finish check"]
    assert [json.loads(r.args_json)["command"] for r in rows if r.called_as == "finish check"] == [
        "python -m unittest 2>&1"
    ]
    assert (row.output or "").strip().splitlines()[-1] == "0", row.output  # grep -c: no ERROR lines
    graded = _hidden_check("shell-count-failures", work)
    assert graded.returncode == 0, graded.stderr.decode("utf-8", "replace")[-1500:]


@needs_win
async def test_control_the_semicolon_chain_in_cmd(app, tmp_path, monkeypatch):
    monkeypatch.setattr(git_bash, "find", lambda: None)
    work = _copy_task("shell-count-failures", tmp_path)
    await _bash_turn(app, work, RECORDED_CHAIN, "bash-chain-control")
    assert (work / "failures.txt").read_text(encoding="utf-8", errors="replace").strip() != "8"


@needs_bash
async def test_replay_a_background_job(app, tmp_path):
    """RED on v1.385.1: cmd ran the server in the FOREGROUND, the call timed
    out after 60 s and the kill ended the server without /shutdown ("2
    servers started but 1 stopped")."""
    work = _copy_task("frontier-debug-price-server", tmp_path)
    sid = await _bash_turn(app, work, RECORDED_BACKGROUND, "bash-bg", timeout_ms=60_000)
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert "timed out" not in (row.output or "") and "listening" in (row.output or ""), row.output
    port = int((work / "out.txt").read_text(encoding="utf-8").split()[1])
    try:
        assert _port_answers(port), "the background server did not keep running"
        # ... until the model stops it the way the task says: GET /shutdown.
        urllib.request.urlopen(f"http://127.0.0.1:{port}/shutdown", timeout=10).read()
        assert await until(lambda: not _port_answers(port), 15)
    finally:
        procs.end_session(f"build:{sid}")
    log = (work / "server.log").read_text(encoding="utf-8").split()
    assert log.count("listening") == 1 and log[-1] == "stopped"
    assert _server_processes(work) == []


@needs_win
async def test_control_the_background_job_in_cmd(app, tmp_path, monkeypatch):
    monkeypatch.setattr(git_bash, "find", lambda: None)
    work = _copy_task("frontier-debug-price-server", tmp_path)
    sid = await _bash_turn(app, work, RECORDED_BACKGROUND, "bash-bg-control", timeout_ms=6_000)
    procs.end_session(f"build:{sid}")
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert row.ok is False and "timed out" in (row.output or ""), row.output
    log = (work / "server.log").read_text(encoding="utf-8").split()
    assert "listening" in log and "stopped" not in log, "the control's server was not killed"
    assert _server_processes(work) == []


# --------------------------------------------------------------------------- #
# the same command tool, the same everything else
# --------------------------------------------------------------------------- #


@needs_bash
async def test_bash_gets_the_command_exactly_and_never_through_the_command_line(app, tmp_path, monkeypatch):
    """The command rides an environment variable: MSYS's command-line parser
    turns ``\\\\`` into ``\\`` and globs an unquoted word (measured), so a
    ``-c <command>`` argv would not be the model's text. The command prints
    itself back byte for byte, and no child it starts sees the variable."""
    seen = _record_runs(monkeypatch)
    payload = "back\\\\slash \"dq\" 'sq' %PATH% !x é 漢字 → * ~ $HOME `echo t`"
    command = f"printf '%s' \"${git_bash.COMMAND_VAR}\"\n: <<'__END__'\n{payload}\n__END__"
    work = tmp_path / "w"
    work.mkdir()
    sid = await _bash_turn(app, work, command, "bash-exact")
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert row.ok and (row.output or "").endswith(command.strip()), row.output
    (call,) = _of(seen, command)
    assert call["argv"] == [BASH.path, "-c", git_bash.RUNNER] and call["shell"] is False
    assert call["startupinfo"] is True, "bash must get the hidden console cmd.exe had"
    child = await _bash_turn(app, work, f"env | grep -c '^{git_bash.COMMAND_VAR}=' || true", "bash-noleak")
    (row2,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{child}"]
    assert (row2.output or "").strip().splitlines()[-1] == "0"


@needs_bash
def test_naive_argv_would_not_be_exact(tmp_path):
    """Why the variable: the SAME command as a ``-c`` argument loses a
    backslash in MSYS's command-line parser (the design's control)."""
    r = subprocess.run([BASH.path, "-c", "printf '%s' 'a\\\\b'"], capture_output=True, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert r.stdout == b"a\\b", r.stdout  # one backslash: the parser ate one
    argv, env = git_bash.invocation(BASH, "printf '%s' 'a\\\\b'", None)
    r = subprocess.run(argv, capture_output=True, timeout=30, env=env, stdin=subprocess.DEVNULL)
    assert r.stdout == b"a\\\\b", r.stdout


@pytest.fixture()
def planted(monkeypatch, tmp_path):
    monkeypatch.setenv("IJ_TEST_SECRET_TOKEN", "planted-not-for-the-child")
    monkeypatch.setenv("IJ_PHASE6_PLAIN_SETTING", "kept")
    N._TOOLS_CACHE.clear()
    yield


@needs_bash
async def test_bash_runs_with_the_build_environment_folder_and_decoding(app, tmp_path, planted, monkeypatch):
    decoded: list = []
    real_as_text = N._as_text

    def as_text(value):
        decoded.append(type(value).__name__)
        return real_as_text(value)

    monkeypatch.setattr(N, "_as_text", as_text)
    work = tmp_path / "w"
    work.mkdir()
    sid = await _bash_turn(app, work, "env > env.txt; pwd -W; printf 'caf\\xc3\\xa9\\n'", "bash-env")
    names = {line.split("=", 1)[0] for line in (work / "env.txt").read_text(encoding="utf-8", errors="replace").splitlines() if "=" in line}
    assert "IJ_PHASE6_PLAIN_SETTING" in names, "the Build environment did not reach bash"
    assert "IJ_TEST_SECRET_TOKEN" not in names, "a secret reached bash"
    assert git_bash.COMMAND_VAR not in names, "the command's variable leaked to a child"
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    out = row.output or ""
    assert str(work).replace("\\", "/").lower() in out.lower(), out  # pwd -W: the session's folder
    assert "café" in out  # UTF-8 output, strict decode
    assert decoded and set(decoded) == {"bytes"}, "bash's output did not go through the ONE decode"


@needs_bash
async def test_bash_cwd_is_honoured_inside_the_folder(app, tmp_path):
    work = tmp_path / "w"
    (work / "sub").mkdir(parents=True)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    wire(app, Script([[ToolCall(id="b1", name="Bash", arguments={"command": "pwd -W", "cwd": "sub"})], "Done."]))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "bash-cwd"}, timeout=60)
    assert status == 200
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert (row.output or "").strip().lower().endswith(str(work / "sub").replace("\\", "/").lower())


@needs_bash
async def test_bash_timeout_kills_the_whole_tree(app, tmp_path):
    import psutil

    work = tmp_path / "w"
    work.mkdir()
    py = sys.executable.replace("\\", "/")
    command = (f'"{py}" -c "import os,time; open(\'pid.txt\',\'w\').write(str(os.getpid())); time.sleep(120)" & '
               f'"{py}" -c "import time; time.sleep(120)"')
    sid = await _bash_turn(app, work, command, "bash-timeout", timeout_ms=3000, wait=90)
    (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert row.ok is False and "timed out" in (row.output or ""), row.output
    pid = int((work / "pid.txt").read_text(encoding="utf-8"))
    assert await until(lambda: not psutil.pid_exists(pid), 15), "a background child outlived the timeout"


@needs_bash
async def test_run_in_background_starts_in_bash_in_the_table_and_ends_with_the_session(app, tmp_path):
    import psutil

    work = tmp_path / "w"
    work.mkdir()
    (work / "stale.pid").write_text("1", encoding="utf-8")
    sid = new_session(app, work, approval_mode="yolo")["id"]
    py = sys.executable.replace("\\", "/")
    bg = f'"{py}" -c "import os,time; open(\'pid.txt\',\'w\').write(str(os.getpid())); time.sleep(120)"; echo never'
    wire(app, Script([
        # `rm -f x; ...`: the shape cmd's guard refuses; in bash it is two commands.
        [ToolCall(id="b1", name="Bash", arguments={"command": f"rm -f stale.pid; echo one; {bg}",
                                                   "run_in_background": True})],
        "Done.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "bash-bg-table"}, timeout=60)
    assert status == 200
    session = f"build:{sid}"
    try:
        (mp,) = P.TABLE.sessions(session)
        assert mp.shell == "bash" and mp.command.startswith("rm -f stale.pid; echo one; ")
        (row,) = [r for r in _rows(app, "process_start") if r.session_id == session]
        assert row.called_as == "Bash" and row.ok, row.output
        # ';' separated the commands: the rm ran, then the program started.
        assert await until(lambda: not (work / "stale.pid").exists(), 15), "the first command did not run"
        pid = int(await until(lambda: (work / "pid.txt").exists() and (work / "pid.txt").read_text(encoding="utf-8"), 15))
        assert psutil.pid_exists(pid)
    finally:
        stopped = procs.end_session(session)  # the run's end hook
    assert stopped and await until(lambda: not psutil.pid_exists(pid), 15)
    assert P.TABLE.sessions(session) == []


@needs_bash
async def test_a_failed_bash_command_is_never_told_it_ran_in_cmd(tmp_path):
    """The POSIX-guess hint ("this shell is Windows cmd") is for cmd.exe only;
    a result says which shell ran it only when bash did."""
    import dataclasses

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(workspace=ws, session_id="build:hint", agent_run_id="r",
                      config=SimpleNamespace(home=tmp_path, sandbox={}), event_bus=None, engine=None,
                      called_as="Bash", shell_syntax="bash")
    res = await ST.SandboxedShellTool().execute({"command": "rm no-such-file-here"}, ctx)
    assert res.ok is False and "No such file" in res.output, res.output
    assert "this shell is Windows cmd" not in res.output
    assert res.data["shell"] == "bash"
    # CONTROL: the same command through cmd.exe with the scrubbed PATH (plain
    # chat: no `rm` on it) still gets its hint. (Review: a Build session's
    # cmd line, whose PATH HAS Git's rm, no longer does — the rm ran; see
    # test_bash_dialect_review_v1386.)
    cmd = await ST.SandboxedShellTool().execute({"command": "rm no-such-file-here"},
                                                dataclasses.replace(ctx, shell_syntax="", session_id="chat"))
    assert cmd.ok is False and "this shell is Windows cmd" in cmd.output
    assert "shell" not in cmd.data


# --------------------------------------------------------------------------- #
# every gate is unchanged
# --------------------------------------------------------------------------- #


@needs_bash
async def test_low_trust_still_refuses_the_native_runtime_for_bash(tmp_path, monkeypatch):
    from iron_jarvis.core.trust import LOW_TRUST_SHELL_REFUSAL

    monkeypatch.setattr(ST.SandboxManager, "get", lambda self: NativeSandbox(self.policy))
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(workspace=ws, session_id="build:lt", agent_run_id="r",
                      config=SimpleNamespace(home=tmp_path, sandbox={}), event_bus=None, engine=None,
                      called_as="Bash", shell_syntax="bash")
    res = await ST.SandboxedShellTool().execute({"command": "touch made.txt", "_isolate": True}, ctx)
    assert res.ok is False and res.error == LOW_TRUST_SHELL_REFUSAL
    assert not (ws / "made.txt").exists()


@needs_bash
async def test_local_only_still_cards_a_bash_call_once_only(app, tmp_path):
    from tests.test_build_plan_ask_v1367 import _finish, _start
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    work = tmp_path / "w"
    work.mkdir()
    who = ("mock", "claude-local")  # a local model in Claude's family: Claude Code's dialect
    sid = new_session(app, work, provider=who[0], model=who[1], approval_mode="yolo", local_only=True)["id"]
    wire(app, Script([[ToolCall(id="b1", name="Bash", arguments={"command": "touch made.txt; echo x"})], "Done."],
                     who=who))
    drop, task = await _start(app, sid, "go", "bash-lo")
    card = (await until(lambda: [d for e, d in turn_frames(app, "bash-lo") if e == "approval"]))[0]
    assert card["tool"] == "shell" and card["once_only"] is True
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "bash-lo", drop, task)
    assert not (work / "made.txt").exists()


@needs_bash
async def test_the_git_guard_reads_bash_text_and_bash_runs_what_it_allows(app, tmp_path, monkeypatch):
    """On a session's own branch: a force push after a ';' is refused with no
    card, a commit inside $(...) cards once-only (denied: nothing commits),
    and an ordinary bash line runs — in bash."""
    from iron_jarvis.harness import branches as B
    from tests.test_build_git_v1375 import git, make_remote, make_repo, new_branch_session, no_real_gh
    from tests.test_build_plan_ask_v1367 import _finish, _start
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    no_real_gh(monkeypatch)
    repo = make_repo(tmp_path / "proj")
    make_remote(tmp_path, repo)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["folder"])
    head = git("rev-parse", "HEAD", cwd=wt)
    (wt / "README.md").write_bytes(b"# changed\n")
    wire(app, Script([
        [ToolCall(id="f1", name="Bash", arguments={"command": "echo start; git push --force origin HEAD:main"})],
        [ToolCall(id="c1", name="Bash", arguments={"command": "x=$(git commit -qam wip) && echo $x"})],
        [ToolCall(id="s1", name="Bash", arguments={"command": "git status --short; git rev-parse --abbrev-ref HEAD"})],
        "Done.",
    ]))
    drop, task = await _start(app, sid, "go", "bash-git")
    card = (await until(lambda: [d for e, d in turn_frames(app, "bash-git") if e == "approval"]))[0]
    assert card["once_only"] is True and "commit" in (card.get("cost_note") or "").lower()
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "bash-git", drop, task)
    cards = [d for e, d in turn_frames(app, "bash-git") if e == "approval"]
    assert len(cards) == 1, "the refused force push showed a card, or the status line did"
    assert git("rev-parse", "HEAD", cwd=wt) == head, "something committed"
    rows = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
    assert rows[0].ok is False and "force" in (rows[0].output or "").lower()
    assert rows[-1].ok is True and "README.md" in (rows[-1].output or "")
    assert view["git"]["branch"] in (rows[-1].output or ""), "the ';' chain did not run in bash"
    B.forget_gh_state()


@pytest.mark.parametrize("command,kinds", [
    # bash runs EVERY line and every ';' / '&&' part — the guard reads them all
    ("echo start; git push --force origin HEAD:main", ["force"]),
    ("git add -A && git commit -m wip", ["commit"]),
    ("x=$(git commit -qam wip) && echo $x", ["commit"]),
    ("echo `git push`", ["push"]),
    ("cat <<'EOF' > notes.txt\nhello\nEOF\ngit push -u origin HEAD", ["push"]),
    ("bash -lc 'git commit -am x'", ["commit"]),
    ("(cd sub && git reset --hard HEAD~2)", ["rewrite"]),
    ("git status --short; git log --oneline -1", []),
    ("echo 'git push is not run here' > note.txt", ["push"]),  # a mention reads as a run (documented)
])
def test_the_git_guard_reads_bash_text(command, kinds):
    from iron_jarvis.harness import git_guard as G

    assert [a.kind for a in G.classify(command)] == kinds, command


@needs_bash
async def test_a_plugin_check_still_runs_in_cmd_beside_a_bash_call(app, tmp_path, monkeypatch):
    """The folder's own checks are the folder's cmd lines: unchanged. Only
    the model's Bash call runs in bash."""
    from tests.test_build_checks_v1367 import _plugins
    from iron_jarvis.harness import sessions as S

    work = tmp_path / "w"
    work.mkdir()
    (work / "app.py").write_text("x = 1\n", encoding="utf-8")
    _plugins(work, "echo checked")
    seen = _record_runs(monkeypatch)
    sid = new_session(app, work, approval_mode="yolo")["id"]
    S.update(app.state.platform.engine, sid, setup={"granted_tools": ["shell"]})
    wire(app, Script([
        [ToolCall(id="e1", name="Edit", arguments={"file_path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})],
        [ToolCall(id="b1", name="Bash", arguments={"command": "echo from-bash; echo two"})],
        "Done.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "bash-check"}, timeout=60)
    assert status == 200
    # Merge with v1.386.0: the folder's check runs twice — after the edit, and
    # again as the reply's FINISH CHECK (the folder's checks that apply to the
    # changed files are its tests). Both are the folder's words: cmd.
    checks = _of(seen, "echo checked")
    assert len(checks) == 2, checks
    for check in checks:
        assert check["shell"] is True and check["argv"] == "echo checked"
    (model,) = _of(seen, "echo from-bash; echo two")
    assert model["argv"][0] == BASH.path and model["shell"] is False


# --------------------------------------------------------------------------- #
# cmd stays for everything else — the exact interpreter and argv as before
# --------------------------------------------------------------------------- #


@needs_win
async def test_plain_chat_and_an_unattended_runs_shell_keep_cmd(tmp_path, monkeypatch):
    seen = _record_runs(monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    for session in ("chat", "sess_schedule_nightly", "build:not-a-bash-call"):
        ctx = ToolContext(workspace=ws, session_id=session, agent_run_id="r",
                          config=SimpleNamespace(home=tmp_path, sandbox={}), event_bus=None, engine=None)
        res = await ST.SandboxedShellTool().execute({"command": f"echo {session.replace(':', '-')}"}, ctx)
        assert res.ok, res.error
        (call,) = _of(seen, f"echo {session.replace(':', '-')}")
        assert call["shell"] is True and call["startupinfo"] is False and isinstance(call["argv"], str)
        assert "shell" not in (res.data or {}), "the result names a shell only when bash ran"


@needs_win
async def test_the_generic_dialects_shell_keeps_cmd(app, tmp_path, monkeypatch):
    seen = _record_runs(monkeypatch)
    work = tmp_path / "w"
    work.mkdir()
    who = ("mock", "mock")
    sid = new_session(app, work, provider=who[0], model=who[1], approval_mode="yolo")["id"]
    wire(app, Script([[ToolCall(id="s1", name="shell", arguments={"command": "echo generic-shell"})], "Done."],
                     who=who))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "generic-shell"}, timeout=60)
    assert status == 200
    (call,) = _of(seen, "echo generic-shell")
    assert call["shell"] is True and call["argv"] == "echo generic-shell" and call["startupinfo"] is False


@needs_win
async def test_the_gpt_dialects_exec_command_keeps_cmd(app, tmp_path, monkeypatch):
    from iron_jarvis.harness import sessions as S

    seen = _record_runs(monkeypatch)
    work = tmp_path / "w"
    work.mkdir()
    who = ("openai", "gpt-5.1")
    sid = new_session(app, work, provider=who[0], model=who[1], approval_mode="yolo")["id"]
    S.update(app.state.platform.engine, sid, setup={"granted_tools": ["process_start"]})
    wire(app, Script([[ToolCall(id="x1", name="exec_command", arguments={"cmd": "echo gpt-exec"})], "Done."],
                     who=who))
    try:
        status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "gpt-exec"}, timeout=60)
        assert status == 200
    finally:
        procs.end_session(f"build:{sid}")
    (call,) = _of(seen, "echo gpt-exec", via="spawn")
    assert call["via"] == "spawn" and call["shell"] is True and call["argv"] == "echo gpt-exec"
    assert call["startupinfo"] is False


@needs_win
async def test_a_helper_agent_run_of_a_claude_build_turn_keeps_cmd(app, tmp_path, monkeypatch):
    """An AGENT run (here the helper a Claude Build turn starts with ``Task``)
    is shown Jarvis's own ``shell`` and its commands keep cmd.exe."""
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.router import RouteResult

    seen = _record_runs(monkeypatch)
    work = tmp_path / "w"
    work.mkdir()
    sid = new_session(app, work, approval_mode="yolo", granted_tools=["shell"])["id"]
    main_moves = [[ToolCall(id="a1", name="Task", arguments={
        "description": "echo", "prompt": "Run echo helper-cmd.", "subagent_type": "general-purpose"})],
        "The helper did it."]

    async def _move(system, tools, messages):
        if "# IJ harness" in (system or ""):
            return main_moves.pop(0) if (main_moves and tools) else "Done."
        if any(getattr(m, "role", "") == "tool" for m in messages or ()):
            return "done"
        return [ToolCall(id="h1", name="shell", arguments={"command": "echo helper-cmd"})]

    def _resp(move):
        if isinstance(move, str):
            return LLMResponse(text=move, usage={"input_tokens": 1, "output_tokens": 1})
        return LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 1, "output_tokens": 1})

    async def stream(*, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        move = await _move(system, tools, messages)
        if isinstance(move, str):
            yield {"type": "text", "text": move}
        yield {"type": "final", "response": _resp(move), "provider": "anthropic", "model": "claude-sonnet-4-5"}

    async def complete(*, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        return RouteResult(_resp(await _move(system, tools, messages)), "anthropic", "claude-sonnet-4-5")

    app.state.platform.router.stream = stream
    app.state.platform.router.complete = complete
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "helper-cmd"}, timeout=90)
    assert status == 200
    (call,) = _of(seen, "echo helper-cmd")
    assert call["shell"] is True and call["argv"] == "echo helper-cmd"


class _OsProxy:
    """``os`` with another ``name`` (the rest is the real module)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, item):
        return getattr(os, item)


async def test_on_another_os_a_bash_call_keeps_the_platforms_own_shell(tmp_path, monkeypatch):
    """A Build session on Linux / macOS already runs POSIX sh: ``Bash`` there
    is unchanged — no Git bash looked for, no guard."""
    monkeypatch.setattr(ST, "os", _OsProxy("posix"))
    monkeypatch.setattr(git_bash, "find", lambda: pytest.fail("looked for Git bash on another OS"))
    got: list[dict] = []

    def fake_run(self, command, **kw):
        got.append({"command": command, **kw})
        return SandboxResult(stdout="ok", stderr="", returncode=0, timed_out=False, duration_s=0.0)

    monkeypatch.setattr(NativeSandbox, "run", fake_run)
    monkeypatch.setattr(ST.SandboxManager, "get", lambda self: NativeSandbox(self.policy))
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(workspace=ws, session_id="build:posix", agent_run_id="r",
                      config=SimpleNamespace(home=tmp_path, sandbox={}), event_bus=None, engine=None,
                      called_as="Bash", shell_syntax="bash")
    res = await ST.SandboxedShellTool().execute({"command": "rm -f a; python b"}, ctx)
    assert res.ok and got and got[0]["command"] == "rm -f a; python b" and "bash" not in got[0]


async def test_on_another_os_a_background_bash_call_keeps_sh(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "os", _OsProxy("posix"))
    monkeypatch.setattr(git_bash, "find", lambda: pytest.fail("looked for Git bash on another OS"))
    got: list = []

    class _Proc:
        pid = 1
        stdout = None
        stdin = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_spawn(argv, **kw):
        got.append((argv, kw.get("shell")))
        return _Proc()

    monkeypatch.setattr(procs, "spawn", fake_spawn)
    ctx = ToolContext(workspace=tmp_path, session_id="build:posix-bg", agent_run_id="r",
                      config=SimpleNamespace(home=tmp_path, sandbox={}), event_bus=None, engine=None,
                      called_as="Bash", shell_syntax="bash")
    try:
        res = await P.ProcessStartTool().execute({"command": "rm -f a; python b", "wait_ms": 0}, ctx)
    finally:
        # Forgotten by hand: the stand-in has no real process to stop.
        for mp in P.TABLE.sessions("build:posix-bg"):
            P.TABLE.remove(mp)
    assert res.ok, res.error
    assert got == [("rm -f a; python b", True)]
