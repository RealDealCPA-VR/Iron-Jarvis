"""v1.386.0 REVIEW: what the bash dialect's first cut got wrong, each pinned.

The doer's change runs the IJ harness's Claude dialect ``Bash`` in Git for
Windows' bash. The review found, each red on the doer's code first:

1. THE FINISH CHECK (v1.386.0, merged from master) re-runs the test part of
   the model's own command — and ran a BASH command's test part in cmd.exe
   (``export PYTHONPATH=. && python -m unittest 2>/dev/null`` failed there,
   so the note told the model its passing tests FAILED). A re-run of the
   model's command now runs in the shell that command ran in; a folder's own
   ``[[checks]]`` (the user's words) stay in cmd.exe for every dialect.

Driven through the REAL lanes (registry ``invoke``, permission engine, shell
tool, real Git bash on this PC); only the model is scripted.
"""

from __future__ import annotations

import os

import pytest

from iron_jarvis.harness import finish as F
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_bash_dialect_v1386 import BASH, _of, _record_runs, needs_bash
from tests.test_finish_lanes_v1386 import NOTE_HEAD, _finish_rows, _lane, env  # noqa: F401 — fixture

WIN = os.name == "nt"

#: A test command only bash runs: `export` and `2>/dev/null` are bash's.
BASH_TEST_CMD = "export PYTHONPATH=. && python -m unittest -q test_mini 2>/dev/null"


def _claude_body(ws, text: str = "set X to 2 in app.py and run the tests"):
    from tests.test_finish_lanes_v1386 import _body

    return _body(ws, text)


def _write(path: str, content: str, cid: str = "w1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="Write", arguments={"file_path": path, "content": content})]


def _bash(command: str, cid: str = "b1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="Bash", arguments={"command": command})]


# --------------------------------------------------------------------------- #
# 1. the finish check re-runs a bash command in bash
# --------------------------------------------------------------------------- #


@needs_bash
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_finish_check_reruns_a_bash_test_command_in_bash(env, lane, monkeypatch):  # noqa: F811
    platform, ws, wire = env
    seen = _record_runs(monkeypatch)

    def answer(model):
        note = model.calls[-1]["messages"][-1].content
        assert note.startswith(NOTE_HEAD)
        assert "passed (exit code 0)" in note, note
        return "1. met: X is 2 and the tests pass."

    wire([_write("app.py", "X = 2\n"), _bash(BASH_TEST_CMD), "Set X to 2; the tests pass.", answer])
    done, _frames = await _lane(lane, platform, _claude_body(ws), monkeypatch, harness="claude")
    assert done["finish"]["tests"] == "passed", done["finish"]
    assert done["finish"]["command"] == BASH_TEST_CMD
    (row,) = _finish_rows(platform)
    assert row.tool == "shell" and row.ok
    # Both runs — the model's and the check's — went to Git's bash.
    runs = _of(seen, BASH_TEST_CMD)
    assert len(runs) == 2, runs
    for run in runs:
        assert run["argv"][0] == BASH.path and run["shell"] is False


@needs_bash
async def test_a_folder_check_stays_in_cmd_while_the_models_bash_reruns_in_bash(env, monkeypatch):  # noqa: F811
    """The folder's ``[[checks]]`` are the folder's words: cmd.exe for every
    dialect (the same check must not change interpreter with the model)."""
    from tests.test_build_checks_v1367 import _plugins

    platform, ws, wire = env
    _plugins(ws, "echo folder-check")
    seen = _record_runs(monkeypatch)
    wire([_write("app.py", "X = 2\n"), _bash(BASH_TEST_CMD), "Done.", "1. met: done."])
    # The folder's own check asks a person (the v1.366.0 rule): answered once.
    await _lane("stream", platform, _claude_body(ws), monkeypatch, harness="claude",
                answer=lambda frame: "once")
    checks = _of(seen, "echo folder-check")
    assert checks, "the folder's check ran"
    for check in checks:
        assert check["shell"] is True and check["argv"] == "echo folder-check"


def test_the_test_part_of_a_bash_command_is_read_with_bash_quoting():
    """A backslash escapes in bash: the test part keeps the command's own
    words (a ``\\"`` inside a double-quoted pytest -k stays one word)."""
    cmd = 'pytest -k "a\\"b" ; echo done'
    assert F.test_part(cmd, posix=True) == 'pytest -k "a\\"b"'


def test_a_bash_commands_test_part_is_read_with_bash_rules():
    """In bash ``\\"`` is an escaped quote, so ``echo "a\\" ; python -m
    unittest"`` is ONE echo: it has no test part. Read with cmd's rules (no
    escapes) the quote closes early and the check would re-run, as a command
    of its own, words the model's shell never ran as a command."""
    from types import SimpleNamespace

    class _Harness:
        def shell_syntax_of(self, call):
            return "bash" if call.name == "shell" else ""

    fc = F.FinishCheck(engine=None, home=None, event_bus=None, session_id="build:x", folder=os.getcwd(),
                       mark=0, request="fix it", harness=_Harness())
    cmd = 'echo "a\\" ; python -m unittest -q test_mini"'
    fc.observe(SimpleNamespace(name="shell", id="b1", arguments={"command": cmd}),
               SimpleNamespace(ok=True, data={"returncode": 0}, error=None, output=""), refused=False)
    assert fc._last_test_command() is None
    # the same command from a dialect that runs cmd.exe keeps cmd's reading
    plain = F.FinishCheck(engine=None, home=None, event_bus=None, session_id="build:x", folder=os.getcwd(),
                          mark=0, request="fix it")
    plain.observe(SimpleNamespace(name="shell", id="s1", arguments={"command": cmd}),
                  SimpleNamespace(ok=True, data={"returncode": 0}, error=None, output=""), refused=False)
    if WIN:
        assert plain._last_test_command() is not None


@needs_bash
async def test_a_carry_on_reruns_the_cut_parts_bash_command_in_bash(env, monkeypatch):  # noqa: F811
    """Resume when ready / Continue on another account: the cut part's test
    command comes back from the LEDGER (its ``called_as`` says ``Bash``), and
    re-runs in bash."""
    from iron_jarvis.daemon.routes.build import CONTINUE_TEXT
    from iron_jarvis.daemon.schemas import ChatMessageBody
    from iron_jarvis.harness import sessions as S
    from iron_jarvis.providers.adapters.base import ProviderError
    from tests.test_finish_lanes_v1386 import _body, _stream

    platform, ws, wire = env
    row = S.create(platform.engine, str(ws), "t")
    S.append_lines(platform.engine, row.id, [{"role": "user", "content": "set X to 2 in app.py", "turnId": "t1"}])
    sid = S.session_id_for(row.id)
    wire([_bash(BASH_TEST_CMD, "c2"), ProviderError("limit")])
    await _stream(platform, _body(ws, text="set X to 2 in app.py", thread_id=row.id), session_id=sid,
                  harness="claude")
    S.append_lines(platform.engine, row.id, [
        {"role": "assistant", "content": "", "turnId": "t1", "error": "limit"},
        {"role": "user", "content": CONTINUE_TEXT, "turnId": "t2", "continuation": True},
    ])
    seen = _record_runs(monkeypatch)
    model = wire([_write("app.py", "X = 2\n", "k1"), "Done.", "1. met."])
    body = _body(ws, thread_id=row.id)
    body.messages = [ChatMessageBody(role="user", content="set X to 2 in app.py"),
                     ChatMessageBody(role="user", content=CONTINUE_TEXT)]
    out = await _stream(platform, body, session_id=sid, harness="claude")
    note = model.notes()[0]
    assert f"`{BASH_TEST_CMD}`" in note and "passed (exit code 0)" in note, note
    assert out["done"]["finish"]["tests"] == "passed"
    (rerun,) = _of(seen, BASH_TEST_CMD)
    assert rerun["argv"][0] == BASH.path and rerun["shell"] is False


# --------------------------------------------------------------------------- #
# 2. the git guard reads a bash line continuation as ONE command
# --------------------------------------------------------------------------- #
#
# cmd.exe ran only a command's first line, so `git \` + a line break + `push
# -f` was two broken lines. Bash joins a backslash-newline into one command:
# the guard read `git push \` (a push: a card) and `git \` (nothing at all)
# while bash ran a FORCE push.

_BS, _LF, _CR = "\\", "\n", "\r"


@pytest.mark.parametrize("command,kinds", [
    ("git push " + _BS + _LF + "  --force origin HEAD:main", ["force"]),
    ("git push " + _BS + _CR + _LF + "  --force origin HEAD:main", ["force"]),
    ("git " + _BS + _LF + "  push -f origin HEAD:main", ["force"]),
    ("git commit -am wip && " + _BS + _LF + "  git push --force", ["commit", "force"]),
    ("git reset " + _BS + _LF + " --hard HEAD~2", ["rewrite"]),
    # a continuation that changes nothing still reads as it did
    ("git status " + _BS + _LF + "  --short", []),
    ("git push " + _BS + _LF + "  origin HEAD", ["push"]),
])
def test_the_git_guard_joins_a_line_continuation(command, kinds):
    from iron_jarvis.harness import git_guard as G

    assert [a.kind for a in G.classify(command)] == kinds, repr(command)


@needs_bash
async def test_a_force_push_split_over_lines_is_refused_through_the_real_turn(tmp_path, monkeypatch):
    """On a session's own branch, through the real Build turn: the split force
    push is refused with NO card and nothing reaches the remote."""
    import shutil

    from iron_jarvis.core import procs
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.harness import branches as B
    from tests.test_bash_dialect_v1386 import _rows
    from tests.test_build_git_v1375 import git, make_remote, make_repo, new_branch_session, no_real_gh
    from tests.test_build_plan_ask_v1367 import _finish, _start
    from tests.test_build_sessions_v1367 import Script, turn_frames, wire
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    app = create_app(str(tmp_path / "home"))
    try:
        no_real_gh(monkeypatch)
        repo = make_repo(tmp_path / "proj")
        bare = make_remote(tmp_path, repo)
        view = await new_branch_session(app, repo, approval_mode="yolo")
        sid = view["id"]
        wire(app, Script([
            [ToolCall(id="f1", name="Bash", arguments={
                "command": "git push " + _BS + _LF + "  --force origin HEAD:refs/heads/forced-one"})],
            [ToolCall(id="f2", name="Bash", arguments={
                "command": "git " + _BS + _LF + "  push -f origin HEAD:refs/heads/forced-two"})],
            "Done.",
        ]))
        drop, task = await _start(app, sid, "go", "bash-cont")
        answered: set[str] = set()
        while not task.done():
            for e, d in turn_frames(app, "bash-cont"):
                if e == "approval" and d["id"] not in answered:
                    answered.add(d["id"])
                    await _asgi_post(app, f"/chat/approvals/{d['id']}", {"decision": "deny"})
            lg = getattr(app.state.platform, "turn_logs", None)
            lg = lg.get("bash-cont") if lg is not None else None
            if lg is not None and lg.status != "running":
                break
            await __import__("asyncio").sleep(0.05)
        await _finish(app, "bash-cont", drop, task)
        assert not answered, "a card was shown for a force push the guard must refuse"
        rows = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
        assert len(rows) == 2 and all(r.ok is False for r in rows), [(r.ok, r.output) for r in rows]
        assert all("force push" in (r.output or "").lower() for r in rows), [r.output for r in rows]
        refs = git("for-each-ref", "--format=%(refname)", cwd=bare)
        assert "forced-one" not in refs and "forced-two" not in refs, refs
        B.forget_gh_state()
    finally:
        procs.end_all()


# --------------------------------------------------------------------------- #
# 3. a job left running with `&` (output not redirected) returns at once
# --------------------------------------------------------------------------- #
#
# Claude Code writes its Bash output to a FILE and waits for bash itself, so
# `python server.py &` returns at once there and the server keeps running —
# what a model trained on it writes. Through a PIPE the job held the pipe:
# the call waited until its timeout and the tree kill then ended the server.


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pid_alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).is_running() and psutil.Process(pid).status() != "zombie"
    except psutil.Error:
        return False


@needs_bash
async def test_a_background_job_without_a_redirect_returns_at_once_and_keeps_serving(tmp_path):
    import time
    import urllib.request

    from iron_jarvis.core import procs
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.harness import sessions as S
    from tests.test_bash_dialect_v1386 import _rows
    from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "w"
    work.mkdir()
    port = _free_port()
    (work / "srv.py").write_text(
        "import http.server, os, sys\n"
        "open('srv.pid', 'w').write(str(os.getpid()))\n"
        "print('serving', flush=True)\n"
        f"http.server.ThreadingHTTPServer(('127.0.0.1', {port}), "
        "http.server.SimpleHTTPRequestHandler).serve_forever()\n",
        encoding="utf-8",
    )
    (work / "hello.txt").write_bytes(b"hello from the server\n")
    pid = 0
    try:
        sid = new_session(app, work, approval_mode="yolo")["id"]
        # The model's line, as Claude writes it: the server in the background,
        # its output NOT redirected, a short wait, the rest of the work.
        wire(app, Script([[ToolCall(id="b1", name="Bash", arguments={
            "command": "python srv.py & sleep 1; echo started", "timeout": 30_000})], "Done."]))
        began = time.monotonic()
        status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "bg-1"}, timeout=90)
        took = time.monotonic() - began
        assert status == 200 and dict(frames_of(raw)).get("done")
        (row,) = [r for r in _rows(app, "shell") if r.session_id == f"build:{sid}"]
        assert row.ok, row.output
        assert "started" in (row.output or "")
        assert took < 15, f"the call waited {took:.1f} s for the background job (its timeout was 30 s)"
        pid = int((work / "srv.pid").read_text(encoding="utf-8"))
        assert _pid_alive(pid), "the server the model started in the background was stopped"
        # It keeps answering — a later call of the same session reaches it.
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/hello.txt", timeout=10).read()
        assert body == b"hello from the server\n"
        assert procs.kept_jobs(f"build:{sid}") >= 1, "the job is not the session's"
        # The session's end (archive / delete call this) ends it.
        S.end_session_programs(sid)
        deadline = time.monotonic() + 10
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _pid_alive(pid), "the session ended and its background job kept running"
    finally:
        procs.end_all()
        if pid and _pid_alive(pid):
            import psutil

            psutil.Process(pid).kill()
