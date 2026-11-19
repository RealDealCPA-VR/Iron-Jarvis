"""Fourth fix plan, group 32, PG2: the ``;`` guard's idea of "one line" is
cmd.exe's (``sandbox/cmd_guard.py``).

* **A carriage return is not a line end in cmd.** Measured on Windows 11 with
  real ``cmd.exe`` (v1.541.0, the probes below): ``cmd /c "echo one<CR> two>
  out.txt"`` wrote ``b"one two\\r\\n"`` into ``out.txt`` -- cmd REMOVES every
  CR from the line and runs the rest; and ``del /q a.tmp<CR>; victim.txt``
  deleted ``victim.txt``. The guard cut the line at the CR, so it judged only
  ``del /q a.tmp`` and let it run. ``cmd_line`` (the text up to the first LF,
  every CR removed) replaces ``_first_line``.
* **Python's ``str.splitlines`` is not cmd's line end either.** It also breaks
  at ``\\v \\f \\x1c \\x1d \\x1e \\x85 U+2028 U+2029``, which cmd keeps as
  characters of the line (measured: ``echo one<FF> two> out.txt`` wrote
  ``one<FF> two``). ``process_write`` into an interactive cmd reading a pipe
  now splits with ``typed_lines`` (LF only, CR removed: measured the same way
  through ``cmd /q /k``), and ``pane_send`` into a Command Prompt pane with
  ``console_lines`` (CR and LF: a CR is Enter in a terminal).

Every case is judged by the three readers (``semicolon_refusal`` for a
command cmd starts, ``process_write``, ``pane_send``). The tool cases go
through the REAL registry. Every ``del`` names files that are in this test's
own temporary folder only.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from iron_jarvis.harness import process as P
from iron_jarvis.platform import build_platform
from iron_jarvis.sandbox import cmd_guard as G
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.tools.base import ToolContext

WIN = os.name == "nt"

#: Every character Python's ``str.splitlines`` breaks at.
SEPARATORS: dict[str, str] = {
    "CR": "\r",
    "LF": "\n",
    "CRLF": "\r\n",
    "VT": "\v",
    "FF": "\f",
    "FS": "\x1c",
    "GS": "\x1d",
    "RS": "\x1e",
    "NEL": "\x85",
    "LS": "\u2028",
    "PS": "\u2029",
}
#: Where each reader ends a line (every other separator is a character of it).
ENDS_A_CMD_LINE = {"LF", "CRLF"}  # cmd /c, and cmd /k reading a pipe
ENDS_A_CONSOLE_LINE = {"CR", "LF", "CRLF"}  # a terminal: CR is Enter

_IDS = list(SEPARATORS)


def _line(sep: str) -> str:
    return f"del /q nothing.tmp{sep}; nothing-else.txt"


# --------------------------------------------------------------------------- #
# the three readers, every separator
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", _IDS)
def test_a_command_cmd_starts_is_judged_up_to_the_first_lf(name):
    """``shell`` / ``process_start``: ``semicolon_refusal`` (``cmd_line``).
    RED before for CR: ``_first_line`` cut at the CR."""
    expected = "" if name in ENDS_A_CMD_LINE else G.refusal_sentence("del")
    assert G.semicolon_refusal(_line(SEPARATORS[name])) == expected, name
    assert G.has_unquoted_semicolon(_line(SEPARATORS[name])) is (name not in ENDS_A_CMD_LINE), name


@pytest.mark.parametrize("name", _IDS)
def test_typed_lines_end_at_lf_only_with_cr_removed(name):
    sep = SEPARATORS[name]
    lines = G.typed_lines(_line(sep))
    if name in ENDS_A_CMD_LINE:
        assert lines == ["del /q nothing.tmp", "; nothing-else.txt"], name
    else:
        assert lines == [_line(sep.replace("\r", ""))], name


@pytest.mark.parametrize("name", _IDS)
def test_console_lines_end_at_cr_and_lf_only(name):
    sep = SEPARATORS[name]
    lines = [ln for ln in G.console_lines(_line(sep)) if ln]
    if name in ENDS_A_CONSOLE_LINE:
        assert lines == ["del /q nothing.tmp", "; nothing-else.txt"], name
    else:
        assert lines == [_line(sep)], name


def test_cmd_line_is_the_text_up_to_the_first_lf_with_every_cr_removed():
    assert G.cmd_line("echo one\r two> out.txt") == "echo one two> out.txt"
    assert G.cmd_line("a\r\nb") == "a"
    assert G.cmd_line("a\nb\rc") == "a"
    assert G.cmd_line("a\fb\x85c") == "a\fb\x85c"
    assert G.cmd_line("") == ""


def test_the_finding_s_own_command_is_refused():
    """The review's example: ``del /q *.tmp<CR>; python run.py`` -- cmd runs
    ``del /q *.tmp; python run.py`` and ``del`` deletes ``run.py``."""
    assert G.semicolon_refusal("del /q *.tmp\r; python run.py") == G.refusal_sentence("del")
    assert G.semicolon_refusal("rm -f a.tmp\r; python run.py") == G.refusal_sentence("rm")
    # cmd /c "..." inside: the inner line is read the same way
    assert G.semicolon_refusal('cmd /c "del /q a.tmp\r; python run.py"') == G.refusal_sentence("del")
    # a CR inside an ordinary line still runs
    assert G.semicolon_refusal("echo a\r; echo b") == ""
    assert G.semicolon_refusal("set PATH=%PATH%;C:\\x\r") == ""


def test_is_interactive_cmd_reads_the_line_cmd_runs():
    """Measured (probe 1): cmd removes the CR, so ``cmd<CR> /c x`` runs
    ``cmd /c x`` -- not an interactive cmd. Text after an LF never runs."""
    assert not G.is_interactive_cmd("cmd\r /c dir")
    assert G.is_interactive_cmd("cmd\r /k")
    assert G.is_interactive_cmd("cmd /q /k\n/c x")
    assert not G.is_interactive_cmd("cmd /c dir\r\n")


# --------------------------------------------------------------------------- #
# through the REAL registry
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
    (w / "keep.txt").write_text("keep me\n", encoding="utf-8")
    return w


def _ctx(platform, ws: Path, session: str) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id=session, agent_run_id="r", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )


@pytest.mark.parametrize("name", _IDS)
async def test_pane_send_into_a_command_prompt_reads_console_lines(plat, ws, name):
    """``PaneSendTool.execute``. RED before for VT FF FS GS RS NEL LS PS:
    ``str.splitlines`` cut there, so ``; nothing-else.txt`` was a line of its
    own and the text was typed."""
    backend = _RecBackend()
    session = plat.terminals.create(cwd=str(ws), shell="cmd", backend=backend)
    text = _line(SEPARATORS[name])
    res = await plat.registry.invoke(
        "pane_send", {"pane": session.id, "text": text}, _ctx(plat, ws, "s_pane"),
        plat.permissions, session_allow={"pane_send"}, allowed_names={"pane_send"},
    )
    if name in ENDS_A_CONSOLE_LINE:
        assert res.ok, res.error
        assert backend.typed == [text + "\r"]
    else:
        assert res.ok is False and res.error == G.refusal_sentence("del"), (name, res.error)
        assert backend.typed == [], "nothing may reach the terminal"


@pytest.mark.skipif(not WIN, reason="process_write's guard is for Windows cmd.exe")
async def test_process_write_into_an_interactive_cmd_reads_typed_lines(plat, ws):
    """``process_write`` (``harness/process.py``). RED before for every
    separator but LF / CRLF: ``str.splitlines`` cut there. Typed text that
    is not refused names files that do not exist."""
    ctx = _ctx(plat, ws, "sess_g32b")
    try:
        start = await plat.registry.invoke(
            "process_start", {"command": "cmd /q /k", "wait_ms": 500}, ctx,
            plat.permissions, session_allow={"process_start"}, allowed_names={"process_start"},
        )
        assert start.ok, start.error
        handle = start.data["id"]
        for name, sep in SEPARATORS.items():
            res = await plat.registry.invoke(
                "process_write", {"id": handle, "input": _line(sep) + "\n"}, ctx,
                plat.permissions, session_allow={"process_write"}, allowed_names={"process_write"},
            )
            if name in ENDS_A_CMD_LINE:
                assert res.ok, (name, res.error)
            else:
                assert res.ok is False and res.error == G.refusal_sentence("del"), (name, res.error)
        assert (ws / "keep.txt").exists()
    finally:
        P.end_session("sess_g32b")


@pytest.mark.skipif(not WIN, reason="cmd.exe is Windows'")
async def test_the_shell_tool_refuses_a_lone_cr_before_anything_runs(plat, ws):
    """``shell``: RED before -- the guard judged ``del /q keep.txt`` only and
    cmd ran ``del /q keep.txt; server.py``."""
    (ws / "server.py").write_text("print('the server')\n", encoding="utf-8")
    res = await plat.registry.invoke(
        "shell", {"command": "del /q keep.txt\r; server.py"}, _ctx(plat, ws, "chat"),
        plat.permissions, session_allow={"shell"}, allowed_names={"shell"},
    )
    assert res.ok is False and res.error == G.refusal_sentence("del"), res.error
    assert (ws / "keep.txt").exists() and (ws / "server.py").exists()


# --------------------------------------------------------------------------- #
# the probes: real cmd.exe, harmless, in this test's own temporary folder
# --------------------------------------------------------------------------- #


def _files(folder: Path) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted(folder.iterdir())}


@pytest.mark.skipif(not WIN, reason="needs real cmd.exe")
@pytest.mark.parametrize("name", ["CR", "LF", "CRLF", "FF", "VT"])
def test_probe1_cmd_c_runs_what_cmd_line_says(tmp_path, name):
    """``cmd /c`` (the shell tool's own launch, ``NativeSandbox.run``) on
    ``echo one<sep> two> out.txt`` writes exactly what running
    ``cmd_line(<that>)`` writes."""
    from iron_jarvis.sandbox.native import NativeSandbox

    probe = f"echo one{SEPARATORS[name]} two> out.txt"
    raw, judged = tmp_path / "raw", tmp_path / "judged"
    raw.mkdir()
    judged.mkdir()
    NativeSandbox().run(probe, cwd=raw, timeout=30)
    NativeSandbox().run(G.cmd_line(probe), cwd=judged, timeout=30)
    assert _files(raw) == _files(judged), (name, _files(raw), _files(judged))
    if name == "CR":
        # the measurement PG2's `is_interactive_cmd` half rests on
        assert _files(raw) == {"out.txt": b"one two\r\n"}


@pytest.mark.skipif(not WIN, reason="needs real cmd.exe")
def test_probe2_a_lone_cr_never_hides_a_harmful_semicolon(tmp_path):
    """One way: if cmd's ``del`` reached the word after the CR and ``;``,
    the guard must refuse that command."""
    from iron_jarvis.sandbox.native import NativeSandbox

    (tmp_path / "a.tmp").write_text("a", encoding="utf-8")
    (tmp_path / "victim.txt").write_text("v", encoding="utf-8")
    command = "del /q a.tmp\r; victim.txt"
    NativeSandbox().run(command, cwd=tmp_path, timeout=30)
    harmed = not (tmp_path / "victim.txt").exists()
    assert not harmed or G.semicolon_refusal(command), "cmd deleted victim.txt and the guard let it run"


@pytest.mark.skipif(not WIN, reason="needs real cmd.exe")
def test_probe3_an_interactive_cmd_on_a_pipe_runs_what_typed_lines_says(tmp_path):
    """``cmd /q /k`` reading a pipe (``process_write``'s case)."""
    from iron_jarvis.core import procs

    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    typed = "echo one\r two> out.txt\r\nexit\r\n"
    procs.run([comspec, "/q", "/k"], input=typed.encode("ascii"), cwd=tmp_path, timeout=30)
    assert _files(tmp_path) == {"out.txt": b"one two\r\n"}
    assert G.typed_lines(typed)[0] == "echo one two> out.txt"
