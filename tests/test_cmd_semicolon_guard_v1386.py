"""v1.386.0: THE ``;`` GUARD wherever Windows cmd.exe still runs a model's
command (``sandbox/cmd_guard.py``).

The Phase 1 benchmark (Jarvis on Claude, v1.385.x): the model's first command
in ``process-http-server`` was ``rm -f server.port; python server.py & sleep 2;
...``. cmd.exe has no ``;``, so Git's ``rm.exe`` got ``-f server.port; python
server.py`` and DELETED ``server.py`` (repeats 1 and 3). Where cmd.exe still
runs a model's command — plain chat, agents, every dialect but Claude's, and
Claude's ``Bash`` on a PC with no Git for Windows bash — a POSIX file command
(or cmd's own, or an installer) before an UNQUOTED ``;`` with words after it
is refused before anything runs, in one sentence the model can act on; every
ordinary cmd line still runs. The table below is both ways (must refuse /
must run, real commands: the benchmark's own, and the cmd lines users write);
the refusal is an ordinary failed result through the REAL registry ``invoke``
(ledgered).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.harness import process as P
from iron_jarvis.platform import build_platform
from iron_jarvis.sandbox import cmd_guard as G
from iron_jarvis.sandbox import git_bash
from iron_jarvis.tools.base import ToolContext

WIN = os.name == "nt"

# --------------------------------------------------------------------------- #
# the table, both ways
# --------------------------------------------------------------------------- #

#: (command, the program the refusal names). Each would hand the words after
#: the ``;`` to a program that deletes, moves, overwrites or installs them.
MUST_REFUSE: list[tuple[str, str]] = [
    # the benchmark's own (process-http-server, repeats 1 and 3)
    ("rm -f server.port; python server.py & sleep 2; P=$(cat server.port); echo $P; curl -s "
     "http://127.0.0.1:$P/status > status.json; cat status.json; echo; curl -s "
     "http://127.0.0.1:$P/shutdown; sleep 2; curl -s -m 3 http://127.0.0.1:$P/status || echo down; "
     "tail -3 server.log", "rm"),
    ('cd "C:/Users/VR/AppData/Local/Temp/ij-bench-runs/2ca1cead0d67788b/run/work" && rm -f server.port; '
     "python server.py & sleep 2; P=$(cat server.port); echo port $P", "rm"),
    # its kin: POSIX file commands Git puts on PATH
    ("rm -rf build; python setup.py build", "rm"),
    ("rm -f out.txt ; python make.py", "rm"),
    ("rm x;  echo done", "rm"),
    ("rm -f a.log; rm -f b.log", "rm"),
    ('"C:\\Program Files\\Git\\usr\\bin\\rm.exe" -f x; python y', "rm"),
    ("rm.exe -f x; python y", "rm"),
    ("(rm -f x; python y)", "rm"),
    ("@rm -f a; python b", "rm"),
    ("rm -f a 2>nul; python b", "rm"),
    ("rm -f a ; python b & echo c", "rm"),
    ("mv a.txt b.txt; cat b.txt", "mv"),
    ("cp incoming/*.csv inbox/; ls inbox", "cp"),
    ("cp -r src dst; rm -rf src", "cp"),
    ("touch a.txt; python app.py", "touch"),
    ("ln -sf target link; ls -la", "ln"),
    ("chmod +x run.sh; ./run.sh", "chmod"),
    ("tar -xf archive.tar; python run.py", "tar"),
    ("rsync -a src/ dst/; ls dst", "rsync"),
    ("echo x | tee out.txt; cat out.txt", "tee"),
    ("sed -i 's/a/b/' f.py; python f.py", "sed"),
    ("perl -pi -e 's/a/b/' f.py; python f.py", "perl"),
    # cmd.exe's own file commands (';' separates their arguments, space or not)
    ("mkdir -p out; python x.py", "mkdir"),
    ("mkdir out;python x.py", "mkdir"),
    ("del server.port; python server.py", "del"),
    ("del a.txt;b.txt", "del"),
    ("copy a.txt b.txt; type b.txt", "copy"),
    ("move a.txt b; dir", "move"),
    ("rmdir /s /q build; mkdir build", "rmdir"),
    ("call del x; dir", "del"),
    ("xcopy /e src dst; dir dst", "xcopy"),
    # git commands that delete or overwrite the files named after them
    ("git checkout -- app.py; python app.py", "git"),
    ("git rm -f old.py; git commit -m x", "git"),
    ("git restore .; python run.py", "git"),
    ("git clean -fd; npm test", "git"),
    ("git -C repo reset --hard; ls", "git"),
    ("git stash; git pull", "git"),
    # installers: the words after ';' become packages fetched and installed
    ("npm install express; npm start", "npm"),
    ("pip install requests; python app.py", "pip"),
    ("python -m pip install -r requirements.txt; python app.py", "python"),
    ("py -3 -m pip install x; python y", "py"),
    ("uv add httpx; uv run app.py", "uv"),
    # wrappers that hand the rest to the program
    ("xargs rm < list.txt; echo done", "rm"),
    ("start /b rm -f a; python b", "rm"),
    ('start "" rm -f a; python b', "rm"),
    ("sudo rm -rf /tmp/x; echo ok", "rm"),
    ("env FOO=1 rm -f a; python b", "rm"),
    ('cmd /c "rm -f a; python b"', "rm"),
    ('cmd.exe /s /c "del a; dir"', "del"),
]

#: Ordinary cmd lines (and bash lines cmd runs harmlessly, if wrongly): none
#: is refused.
MUST_RUN: list[str] = [
    "set PATH=%PATH%;C:\\x",
    'findstr "a;b" f.txt',
    "echo a;b",
    "echo a; echo b",
    "echo rm -f a; python b",
    'python -c "import x; print(1)"',
    'rm -f "a; b.txt"',
    "rm -f 'a; b.txt'",
    "rm -f a;b",
    "rm -f server.port & python server.py",
    "rm -f server.port && python server.py",
    "rm -f a.txt",
    "del /q build\\*.obj",
    "copy a.txt b.txt",
    'move "old;name.txt" new.txt',
    'cp a.txt "b;c.txt"',
    "dir /b",
    "path %PATH%;C:\\tools",
    'setx PATH "%PATH%;C:\\bin"',
    "echo %DATE%;%TIME%",
    'for /f "tokens=1,2 delims=;" %a in (x.txt) do @echo %a',
    'type a.txt | findstr ";"',
    "xcopy /e /i src dst",
    "robocopy src dst /e",
    'pip install "requests; python_version>\'3.6\'"',
    "npm install && npm test",
    'git commit -m "fix; tidy"',
    "git status --short; git diff",
    "git log --oneline -5; git status",
    "sleep 2; cat x",
    "sed -n 1,12p file; sed -n 28,50p file",
    "python server.py --port 0 > out.txt 2>&1",
    'cmd /c "echo a; echo b"',
    'bash -c "rm -f a; python b"',
    'powershell -Command "Remove-Item a; python b"',
    "mkdir build && cd build",
    "rmdir /s /q build",
    "git checkout main && git pull",
    # the benchmark's own cmd lines
    "ping -n 3 127.0.0.1 >nul & type watcher.log",
    "copy incoming\\*.csv inbox\\ && ping -n 4 127.0.0.1 >nul & type watcher.log & type results.json "
    "& dir /b inbox done",
    "del status.json & dir /a",
    "type nul > inbox\\STOP & ping -n 3 127.0.0.1 >nul & type watcher.log & dir /b inbox",
    "printf 8 > failures.txt; python -m unittest 2>&1 | grep -c \"^ERROR:\"",
    # a heredoc's later lines never run in cmd (it stops at the first line break)
    "python - <<'E'\nrm -f x; python y\nE",
]


def test_the_table_is_large_enough_both_ways():
    assert len(MUST_REFUSE) >= 25 and len(MUST_RUN) >= 25


@pytest.mark.parametrize("command,program", MUST_REFUSE)
def test_a_destructive_semicolon_is_refused_naming_the_program(command, program):
    words = G.semicolon_refusal(command)
    assert words == G.refusal_sentence(program), (command, words)
    assert "cmd.exe" in words and "'&&'" in words and f"`{program}`" in words


@pytest.mark.parametrize("command", MUST_RUN)
def test_an_ordinary_cmd_line_runs(command):
    assert G.semicolon_refusal(command) == "", command


def test_the_sentence_is_one_plain_instruction():
    assert G.refusal_sentence("rm") == (
        "This shell is Windows cmd.exe: ';' does not separate commands here, so `rm` would also "
        "receive the words after it. Nothing was run; use '&&' between commands."
    )


def test_the_guard_never_raises_on_odd_input():
    for odd in (None, 0, "", ";", ";;;", "rm ;", '"', "'", "^", "rm " + '"' * 7 + ";x", "\x00;rm a; b"):
        assert isinstance(G.semicolon_refusal(odd), str)


def test_interactive_cmd_is_recognised_and_cmd_c_is_not():
    assert G.is_interactive_cmd("cmd")
    assert G.is_interactive_cmd("cmd.exe /k echo hi")
    assert G.is_interactive_cmd('"C:\\Windows\\System32\\cmd.exe"')
    assert not G.is_interactive_cmd("cmd /c dir")
    assert not G.is_interactive_cmd("python server.py")


# --------------------------------------------------------------------------- #
# through the REAL registry (only the folder is temporary)
# --------------------------------------------------------------------------- #


def _rows(platform) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)))
        for r in rows:
            db.expunge(r)
        return rows


def _ctx(platform, ws: Path, session: str = "chat", **kw) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id=session, agent_run_id="r", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine, **kw,
    )


@pytest.fixture()
def plat(tmp_path):
    return build_platform(str(tmp_path / "home"))


@pytest.fixture()
def ws(tmp_path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "keep.txt").write_text("keep me\n", encoding="utf-8")
    (w / "server.py").write_text("print('the server')\n", encoding="utf-8")
    return w


@pytest.mark.skipif(not WIN, reason="cmd.exe is Windows'")
async def test_plain_chats_shell_refuses_it_through_the_registry_and_ledgers_it(plat, ws):
    """RED on v1.385.1: the command ran in cmd and Git's ``rm`` deleted
    ``server.py`` (when Git's tools are on the user's PATH) — here nothing
    runs, the result is a failed row with the sentence."""
    res = await plat.registry.invoke(
        "shell", {"command": "rm -f keep.txt; python server.py"}, _ctx(plat, ws),
        plat.permissions, session_allow={"shell"}, allowed_names={"shell"},
    )
    assert res.ok is False and res.error == G.refusal_sentence("rm")
    assert (ws / "keep.txt").exists() and (ws / "server.py").exists()
    (row,) = [r for r in _rows(plat) if r.tool == "shell"]
    assert row.ok is False and "does not separate commands" in (row.output or "")


@pytest.mark.skipif(not WIN, reason="cmd.exe is Windows'")
async def test_an_ordinary_cmd_line_still_runs_through_the_registry(plat, ws):
    res = await plat.registry.invoke(
        "shell", {"command": "echo a; echo b & set PATH=%PATH%;C:\\x"}, _ctx(plat, ws),
        plat.permissions, session_allow={"shell"}, allowed_names={"shell"},
    )
    assert res.ok is True, res.error
    assert "a; echo b" in res.output


@pytest.mark.skipif(not WIN, reason="cmd.exe is Windows'")
async def test_a_build_session_without_bash_refuses_it_too(plat, ws, monkeypatch):
    """Claude's ``Bash`` on a PC with no Git for Windows bash runs in cmd —
    the guard holds there as well."""
    monkeypatch.setattr(git_bash, "find", lambda: None)
    res = await plat.registry.invoke(
        "shell", {"command": "rm -f keep.txt; python server.py"},
        _ctx(plat, ws, "build:t-guard", called_as="Bash", shell_syntax="bash"),
        plat.permissions, session_allow={"shell"}, allowed_names={"shell"},
    )
    assert res.ok is False and res.error == G.refusal_sentence("rm")
    assert (ws / "server.py").exists()


@pytest.mark.skipif(not WIN, reason="cmd.exe is Windows'")
async def test_process_start_refuses_it_before_taking_a_slot(plat, ws):
    try:
        res = await plat.registry.invoke(
            "process_start", {"command": "del keep.txt; python server.py"}, _ctx(plat, ws, "sess_g"),
            plat.permissions, session_allow={"process_start"}, allowed_names={"process_start"},
        )
        assert res.ok is False and res.error == G.refusal_sentence("del")
        assert P.TABLE.sessions("sess_g") == [], "a refused start took a slot or ran"
        assert (ws / "keep.txt").exists() and (ws / "server.py").exists()
    finally:
        P.end_session("sess_g")


@pytest.mark.skipif(not WIN, reason="cmd.exe is Windows'")
async def test_a_line_typed_into_an_interactive_cmd_is_guarded(plat, ws):
    ctx = _ctx(plat, ws, "sess_w")
    try:
        start = await plat.registry.invoke(
            "process_start", {"command": "cmd /q /k", "wait_ms": 500}, ctx,
            plat.permissions, session_allow={"process_start"}, allowed_names={"process_start"},
        )
        assert start.ok, start.error
        handle = start.data["id"]
        res = await plat.registry.invoke(
            "process_write", {"id": handle, "input": "del keep.txt; type server.py\n"}, ctx,
            plat.permissions, session_allow={"process_write"}, allowed_names={"process_write"},
        )
        assert res.ok is False and res.error == G.refusal_sentence("del")
        ok = await plat.registry.invoke(
            "process_write", {"id": handle, "input": "echo still-here\n", "wait_ms": 1500}, ctx,
            plat.permissions, session_allow={"process_write"}, allowed_names={"process_write"},
        )
        assert ok.ok and "still-here" in ok.output
        assert (ws / "keep.txt").exists()
    finally:
        P.end_session("sess_w")


def _rm_on_build_path() -> bool:
    from iron_jarvis.sandbox.native import build_env

    env = build_env()
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    return shutil.which("rm", path=path) is not None


@pytest.mark.skipif(not WIN or not _rm_on_build_path(), reason="needs Git's rm.exe on the user's PATH (the case that bit)")
def test_control_cmd_really_feeds_rm_the_rest_of_the_line(tmp_path):
    """The reason for the guard, measured: cmd.exe runs ``rm -f a; b`` with
    ``b`` as a FILE NAME (cmd straight through the native runtime, as the
    shell tool did before the guard)."""
    from iron_jarvis.sandbox.native import NativeSandbox

    (tmp_path / "victim.py").write_text("x", encoding="utf-8")
    NativeSandbox().run("rm -f nothing.port; victim.py", cwd=tmp_path, timeout=30, build=True)
    assert not (tmp_path / "victim.py").exists()


def test_a_long_line_is_judged_in_linear_time():
    """The guard runs before a command starts (``process_start`` on the
    loop): a long line with many ';' must stay cheap."""
    import time

    line = "echo " + "a; " * 50_000 + "& rm -f x; y"
    t = time.perf_counter()
    assert G.semicolon_refusal(line) == G.refusal_sentence("rm")
    assert time.perf_counter() - t < 2.0
