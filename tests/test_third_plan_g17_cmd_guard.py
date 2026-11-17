"""Third north-star fix plan, group 17: the command guard (PG6, PG9, PG8).

* **PG6.** The ``;`` guard for Windows cmd.exe (``sandbox/cmd_guard.py``) cut
  a line at the ``&`` inside ``2>&1``. ``del /q *.tmp >nul 2>&1; python
  run.py`` was not refused, and cmd hands ``del`` the later words (measured
  with ``echo`` on this PC: ``echo A >nul 2>&1; echo B`` prints nothing, so
  ``; echo B`` went to ``echo`` and on to ``nul``). An ``&`` RIGHT AFTER a
  ``>`` or ``<`` duplicates a handle (``2>&1``, ``1>&2``, ``>&2``, ``<&0``,
  and ``2>& 1``, which cmd also accepts); it is part of the command, never a
  separator. ``2> &1`` is a syntax error in cmd (nothing runs), so that
  ``&`` still separates.
* **PG9.** A destructive program reached through ``if [/i] [not] exist|
  defined|errorlevel ... <command>``, ``if a==b <command>``, ``if a equ b
  <command>``, ``for ... do <command>`` (with or without a ``( ... )`` block,
  and the ``else`` of an ``if`` block) is judged as the program of that
  command.
* **PG8 (decision K6).** A line an agent types into a Build terminal
  (``pane_send``) meets the shell tool's checks when it will run as a
  command (no coding CLI in the pane, or the pane is back at its shell's
  prompt): the git guard (refusal in the tool, the card through the tool's
  ``spend_check``) and, in a Command Prompt pane, the ``;`` guard.
  ``pane_spawn`` has no command of its own (it only opens a shell); its
  folder is confined to the session's folder by the same rule as the shell
  tool's ``cwd``.

No real ``del`` runs anywhere here: the guard is a pure function over text,
and every pane is a stand-in backend (no process).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import branches as B
from iron_jarvis.harness import git_guard as GG
from iron_jarvis.platform import build_platform
from iron_jarvis.sandbox import cmd_guard as G
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.tools.base import ToolContext, spend_checks_for

# --------------------------------------------------------------------------- #
# PG6: a handle duplicated (`2>&1`) is part of the command
# --------------------------------------------------------------------------- #

PG6_MUST_REFUSE: list[tuple[str, str]] = [
    # the finding's own command (RED before the fix)
    ("del /q *.tmp >nul 2>&1; python run.py", "del"),
    # the redirect before the ';', every spelling cmd accepts
    ("rm -f build.log >nul 2>&1; python run.py", "rm"),
    ("del /q *.tmp 2>&1 >nul; python run.py", "del"),
    ("del /q *.tmp 1>&2; python run.py", "del"),
    ("del /q *.tmp >&2; python run.py", "del"),
    ("del /q *.tmp 2>& 1; python run.py", "del"),
    ("del /q *.tmp <&0; python run.py", "del"),
    ("del /q *.tmp 2>>&1; python run.py", "del"),
    ("rm -f a 2>&1; python b", "rm"),
    ("git checkout -- app.py 2>&1; python app.py", "git"),
    # the redirect after the ';' (refused before and after the fix)
    ("del /q *.tmp; python run.py >nul 2>&1", "del"),
    # a ^-escaped '&' is a plain character in cmd (refused before and after)
    ("del a.txt 2^>^&1; python b", "del"),
]

PG6_MUST_RUN: list[str] = [
    # '&&' and a real '&' still separate commands
    "del /q *.tmp 2>&1 && echo a; echo b",
    "del /q *.tmp >nul 2>&1 & echo a; echo b",
    "del /q *.tmp 2>&1 & echo a; echo b",
    # inside double quotes it is no redirect and no separator
    'del "a 2>&1;b.txt"',
    # an escaped '>' is a plain character, so the '&' after it separates
    "del a.txt 2^>&1; python b",
    # `2> &1` is a syntax error in cmd (measured: nothing runs); '&' separates
    "del /q *.tmp 2> &1; python run.py",
    # ordinary lines
    "python server.py --port 0 > out.txt 2>&1",
    "python run.py >nul 2>&1 & type out.txt",
    "echo a 2>&1; echo b",
]


@pytest.mark.parametrize("command,program", PG6_MUST_REFUSE)
def test_pg6_a_duplicated_handle_does_not_hide_the_semicolon(command, program):
    """Pins ``cmd_guard._segments``'s ``elif not quoted and ch == "&" and
    redirect`` branch (``2>&1`` is part of the command)."""
    assert G.semicolon_refusal(command) == G.refusal_sentence(program), command


@pytest.mark.parametrize("command", PG6_MUST_RUN)
def test_pg6_real_separators_still_separate(command):
    assert G.semicolon_refusal(command) == "", command


def test_pg6_the_segments_keep_2_and_1_together():
    """The cut itself: ``2>&1`` stays in its command; ``&&`` / ``&`` still cut."""
    assert G._segments("del /q *.tmp >nul 2>&1; python run.py") == ["del /q *.tmp >nul 2>&1; python run.py"]
    assert G._segments("a 2>&1 & b") == ["a 2>&1 ", " b"]
    assert G._segments("a 2>&1 && b") == ["a 2>&1 ", " b"]
    assert G._segments('a "x&y" 2>&1') == ['a "x&y" 2>&1']


# --------------------------------------------------------------------------- #
# PG9: `if` / `for` / `@` / `call` reach a program
# --------------------------------------------------------------------------- #

PG9_MUST_REFUSE: list[tuple[str, str]] = [
    # the finding's own command (RED before the fix)
    ("if exist out.txt del out.txt; python gen.py", "del"),
    ("IF EXIST out.txt DEL out.txt; python gen.py", "del"),
    ("if not exist keep.txt del /q build\\*; python gen.py", "del"),
    ('if /i "%MODE%"=="clean" del out.txt; python gen.py', "del"),
    ("if %MODE% == clean rm -f out.txt; python gen.py", "rm"),
    ("if %MODE%== clean rm -f out.txt; python gen.py", "rm"),
    ("if %MODE% ==clean rm -f out.txt; python gen.py", "rm"),
    ("if 1 equ 1 del a.txt; python b", "del"),
    ("if /i not a neq b del a.txt; python b", "del"),
    ("if defined CLEAN del out.txt; python gen.py", "del"),
    ("if errorlevel 1 del out.txt; python gen.py", "del"),
    ("if not errorlevel 1 rm -f out.txt; python gen.py", "rm"),
    # a ( ... ) block, and the else of one
    ("if exist out.txt (del out.txt; python gen.py)", "del"),
    ("if exist a.txt (echo x) else del b.txt; python gen.py", "del"),
    ("if exist a.txt (echo x) else (del b.txt; python gen.py)", "del"),
    # for ... do
    ("for %f in (*.tmp) do del %f; python gen.py", "del"),
    ("for /l %i in (1,1,3) do (rm -f out%i.txt; python gen.py)", "rm"),
    ("for /f %a in (list.txt) do @rm -f %a; echo done", "rm"),
    ('for /f "tokens=1 delims=;" %a in (list.txt) do del %a; echo done', "del"),
    # '@' and 'call' in front of the program, and nested
    ("@if exist out.txt del out.txt; python gen.py", "del"),
    ("if exist out.txt @del out.txt; python gen.py", "del"),
    ("if exist out.txt call del out.txt; python gen.py", "del"),
    ("if exist a.txt if exist b.txt del a.txt; python gen.py", "del"),
    ("for %f in (*.tmp) do if exist %f del %f; python gen.py", "del"),
    ('cmd /c "if exist out.txt del out.txt; python gen.py"', "del"),
    ("echo start & if exist out.txt del out.txt; python gen.py", "del"),
]

PG9_MUST_RUN: list[str] = [
    "if exist a.txt (echo x)",
    "if exist a.txt (echo x; echo y)",
    "if exist a.txt echo found; done",
    "if exist out.txt del out.txt & python gen.py",
    "if exist out.txt del out.txt && python gen.py",
    'if "%a%"=="x;y" echo ok',
    'if exist "a;b.txt" echo found',
    "for %f in (a;b) do echo %f",
    'for /f "tokens=1,2 delims=;" %a in (x.txt) do @echo %a',
    # the block ends at its ')': del gets only a.txt, the else is harmless
    "if exist a.txt (del a.txt) else echo b; c",
    "if exist a.txt (del a.txt) & echo a; b",
    "if errorlevel 1 echo failed; see log",
]


@pytest.mark.parametrize("command,program", PG9_MUST_REFUSE)
def test_pg9_a_program_behind_if_or_for_is_judged(command, program):
    """Pins ``cmd_guard._judge``'s ``if prog == "if"`` / ``if prog == "for"``
    branches (and ``_judge_body`` for a block and its ``else``)."""
    assert G.semicolon_refusal(command) == G.refusal_sentence(program), command


@pytest.mark.parametrize("command", PG9_MUST_RUN)
def test_pg9_an_ordinary_if_or_for_still_runs(command):
    assert G.semicolon_refusal(command) == "", command


def test_pg9_deep_nesting_stays_cheap_and_never_raises():
    line = "if a==a " * 5_000 + "del x; y"
    assert isinstance(G.semicolon_refusal(line), str)
    line = "if exist a (" * 5_000 + "del x; y"
    assert isinstance(G.semicolon_refusal(line), str)
    for odd in ("if", "if ;", "if exist", "if exist a", "for", "for ; do", "if a==b (", "if a==b ()else;x"):
        assert isinstance(G.semicolon_refusal(odd), str)


# --------------------------------------------------------------------------- #
# PG8: a line typed into a Build terminal; the folder a terminal opens in
# --------------------------------------------------------------------------- #


class _RecBackend(FakeBackend):
    """No process: records what was typed."""

    def __init__(self) -> None:
        super().__init__()
        self.typed: list[str] = []

    def write(self, data):  # noqa: ANN001
        self.typed.append(data if isinstance(data, str) else data.decode("utf-8"))
        super().write(data)


@pytest.fixture()
def plat(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    yield p
    try:
        p.terminals.kill_all()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def ws(tmp_path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "sub").mkdir()
    (w / "keep.txt").write_text("keep me\n", encoding="utf-8")
    return w


def _ctx(platform, ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s_pane", agent_run_id="r", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )


def _pane(platform, ws: Path, shell: str, *, cli: str | None = None):
    backend = _RecBackend()
    session = platform.terminals.create(cwd=str(ws), shell=shell, backend=backend, agent_cli=cli)
    return session, backend


async def _send(platform, ws: Path, pane: str, text: str):
    return await platform.registry.invoke(
        "pane_send", {"pane": pane, "text": text}, _ctx(platform, ws),
        platform.permissions, session_allow={"pane_send"}, allowed_names={"pane_send"},
    )


async def test_pg8_a_cmd_terminal_refuses_a_destructive_semicolon(plat, ws):
    """RED before: the line was typed into cmd (and del would get the rest).
    Pins ``PaneSendTool.execute``'s ``semicolon_refusal`` call."""
    session, backend = _pane(plat, ws, "cmd")
    res = await _send(plat, ws, session.id, "del keep.txt; python server.py")
    assert res.ok is False and res.error == G.refusal_sentence("del"), res.error
    assert backend.typed == [], "nothing may reach the terminal"
    # every line of a multi-line text is judged (each is a cmd line)
    res = await _send(plat, ws, session.id, "echo hi\nrm -f a; python b")
    assert res.ok is False and res.error == G.refusal_sentence("rm")
    assert backend.typed == []
    # an ordinary cmd line still goes through
    ok = await _send(plat, ws, session.id, "echo a; echo b & dir")
    assert ok.ok, ok.error
    assert backend.typed == ["echo a; echo b & dir\r"]


async def test_pg8_a_powershell_terminal_keeps_its_own_semicolon(plat, ws):
    """';' separates commands in PowerShell: no ';' refusal there."""
    session, backend = _pane(plat, ws, "powershell")
    res = await _send(plat, ws, session.id, "del keep.txt; python server.py")
    assert res.ok, res.error
    assert backend.typed == ["del keep.txt; python server.py\r"]


async def test_pg8_text_for_a_coding_cli_is_prose(plat, ws):
    """A line typed into a coding CLI's input box is a request to that CLI,
    not a command line: neither guard reads it."""
    session, backend = _pane(plat, ws, "cmd", cli="claude")
    token = B.set_turn_branch({"repo": str(ws), "worktree": str(ws), "branch": "ij/x", "base": "main"},
                              str(ws))
    try:
        res = await _send(plat, ws, session.id, "please del old.log; then git push --force")
    finally:
        B.reset_turn_branch(token)
    assert res.ok, res.error
    assert backend.typed == ["please del old.log; then git push --force\r"]


async def test_pg8_a_cli_pane_back_at_its_cmd_prompt_is_a_shell_again(plat, ws):
    """The CLI hint stays after the CLI exits; the shell's own prompt on the
    last line says the next line runs as a command."""
    session, backend = _pane(plat, ws, "cmd", cli="claude")
    session._tail[:] = b"Goodbye!\r\nC:\\work>"
    session.output_seq += 1
    res = await _send(plat, ws, session.id, "del keep.txt; python server.py")
    assert res.ok is False and res.error == G.refusal_sentence("del")
    assert backend.typed == []


async def test_pg8_a_force_push_typed_on_a_sessions_branch_is_refused(plat, ws):
    """RED before: the line was typed. Pins ``PaneSendTool.execute``'s
    ``refusal_off_loop`` call (the shell tool's own git refusal)."""
    session, backend = _pane(plat, ws, "powershell")
    token = B.set_turn_branch({"repo": str(ws), "worktree": str(ws), "branch": "ij/x", "base": "main"},
                              str(ws))
    try:
        res = await _send(plat, ws, session.id, "git push --force origin ij/x")
        shell_res = await plat.registry.invoke(
            "shell", {"command": "git push --force origin ij/x"}, _ctx(plat, ws),
            plat.permissions, session_allow={"shell"}, allowed_names={"shell"},
        )
    finally:
        B.reset_turn_branch(token)
    assert res.ok is False and res.error == GG.FORCE_REFUSAL, res.error
    assert shell_res.error == res.error, "the same sentence as the shell tool"
    assert backend.typed == []


async def test_pg8_a_push_typed_on_a_sessions_branch_asks_like_the_shell(plat, ws):
    """RED before: no card. Pins ``PaneSendTool.spend_check`` (the lanes'
    ``spend_checks_for``): the same once-only card as the shell tool's."""
    session, _backend = _pane(plat, ws, "powershell")
    cli_session, _b2 = _pane(plat, ws, "powershell", cli="claude")
    calls = [
        SimpleNamespace(name="pane_send", arguments={"pane": session.id, "text": "git push origin ij/x"}),
        SimpleNamespace(name="shell", arguments={"command": "git push origin ij/x"}),
        SimpleNamespace(name="pane_send", arguments={"pane": cli_session.id, "text": "git push origin ij/x"}),
    ]
    token = B.set_turn_branch({"repo": str(ws), "worktree": str(ws), "branch": "ij/x", "base": "main"},
                              str(ws))
    try:
        verdicts = await spend_checks_for(plat.registry, calls, _ctx(plat, ws))
    finally:
        B.reset_turn_branch(token)
    pane, shell = verdicts.get(id(calls[0])), verdicts.get(id(calls[1]))
    assert shell is not None and shell.ask is True and shell.once_only is True
    assert pane is not None, "a push typed into a terminal must ask like the shell's"
    assert (pane.ask, pane.once_only, pane.words, pane.reason) == (
        shell.ask, shell.once_only, shell.words, shell.reason)
    assert id(calls[2]) not in verdicts, "text for a coding CLI is prose, no git card"


def _spawn_without_a_process(platform, monkeypatch):
    """The REAL manager, with a stand-in backend (no shell process starts)."""
    real = platform.terminals.create

    def create(*a, **kw):
        kw.setdefault("backend", _RecBackend())
        return real(*a, **kw)

    monkeypatch.setattr(platform.terminals, "create", create)


async def _spawn(platform, ws: Path, args: dict):
    return await platform.registry.invoke(
        "pane_spawn", args, _ctx(platform, ws),
        platform.permissions, session_allow={"pane_spawn"}, allowed_names={"pane_spawn"},
    )


async def test_pg8_a_terminal_is_held_to_the_folders_jarvis_may_read(plat, ws, tmp_path, monkeypatch):
    """RED before: the terminal opened in any folder. Pins
    ``PaneSpawnTool.execute``'s ``_pane_folder``: a relative folder stays in
    the session's folder (``safe_path``); a full path outside it passes the
    rule for what Iron Jarvis may read (``fs_policy.fs_read_ok``): never a
    protected folder, never outside a confined run's folder."""
    from iron_jarvis.core import fs_policy

    _spawn_without_a_process(plat, monkeypatch)
    res = await _spawn(plat, ws, {"cwd": "..", "name": "up"})
    assert res.ok is False and "outside the session's folder" in res.error
    kept = tmp_path / "kept-keys"
    kept.mkdir()
    fs_policy.register_protected_root(kept)
    res = await _spawn(plat, ws, {"cwd": str(kept), "name": "keys"})
    assert res.ok is False and "cannot be opened" in res.error and "protected" in res.error
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    token = fs_policy.confine_reads([ws])
    try:
        res = await _spawn(plat, ws, {"cwd": str(elsewhere), "name": "out"})
    finally:
        fs_policy.release_reads(token)
    assert res.ok is False and "cannot be opened" in res.error
    assert plat.terminals.list() == [], "a refused terminal must not open"
    missing = await _spawn(plat, ws, {"cwd": "nope", "name": "missing"})
    assert missing.ok is False and "not a folder" in missing.error
    assert plat.terminals.list() == []


async def test_pg8_a_full_path_the_user_may_read_still_opens(plat, ws, tmp_path, monkeypatch):
    """The lead's review: "open a terminal in my project's folder" is asked about on
    the card and must keep working; only the folder rules refuse."""
    _spawn_without_a_process(plat, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    res = await _spawn(plat, ws, {"cwd": str(elsewhere), "name": "out"})
    assert res.ok, res.error
    assert Path(res.data["pane"]["cwd"]) == elsewhere.resolve()


async def test_pg8_a_terminal_inside_the_sessions_folder_opens(plat, ws, monkeypatch):
    _spawn_without_a_process(plat, monkeypatch)
    sub = await _spawn(plat, ws, {"cwd": "sub", "name": "inner"})
    assert sub.ok, sub.error
    assert Path(sub.data["pane"]["cwd"]) == (ws / "sub").resolve()
    absolute = await _spawn(plat, ws, {"cwd": str(ws / "sub"), "name": "inner2"})
    assert absolute.ok, absolute.error
    default = await _spawn(plat, ws, {"name": "home"})
    assert default.ok, default.error
    assert Path(default.data["pane"]["cwd"]).resolve() == ws.resolve()
