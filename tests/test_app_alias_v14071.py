"""v1.407.1 (a USER BUG): a terminal pane starts a Microsoft Store app's REAL
program, never its App Execution Alias.

On the user's PC, Build stuck at "Attaching to sessions…" and then "Iron
Jarvis isn't responding": both restored panes were parked in
``CreateProcessW`` on ``WindowsApps\\pwsh.EXE`` (the Store alias, whose
package activation sometimes never returns), and the daemon stopped answering
until the desktop watchdog killed it. ``terminals/app_alias`` reads the
alias's target and every backend starts that instead; the pane's saved argv
keeps the alias so a package update (a new folder name) is read again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from iron_jarvis.terminals import app_alias
from iron_jarvis.terminals import backend as B

WINDOWS = sys.platform == "win32"
USER_ALIAS = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe")


def _buffer(*strings: str, tag: int = app_alias.APPEXECLINK_TAG, version: int = 3) -> bytes:
    body = version.to_bytes(4, "little") + "".join(s + "\x00" for s in strings).encode("utf-16-le")
    return tag.to_bytes(4, "little") + len(body).to_bytes(2, "little") + b"\x00\x00" + body


# --------------------------------------------------------------------------- #
# reading the alias
# --------------------------------------------------------------------------- #


def test_the_target_is_the_third_string_of_the_reparse_data():
    data = _buffer(
        "Microsoft.PowerShell_8wekyb3d8bbwe",
        "Microsoft.PowerShell_8wekyb3d8bbwe!App",
        r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe",
        "0",
    )
    assert app_alias.parse_reparse_data(data) == (
        r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe"
    )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00" * 11,
        _buffer("a", "b", "c", tag=0xA000000C),  # a symlink's tag
        _buffer("a", "b"),  # no target string
        _buffer("a", "b", ""),  # an empty target
        app_alias.APPEXECLINK_TAG.to_bytes(4, "little") + b"\x40\x00\x00\x00" + b"\xff" * 9,
    ],
)
def test_anything_but_a_readable_alias_buffer_names_no_target(data):
    assert app_alias.parse_reparse_data(data) is None


def _fake_alias(monkeypatch, target: str | None) -> None:
    monkeypatch.setattr(app_alias, "is_app_alias", lambda p: p == "ALIAS")
    monkeypatch.setattr(
        app_alias, "_read_reparse", lambda p: _buffer("pkg", "pkg!App", target) if target else None
    )


def test_an_alias_whose_target_exists_resolves_to_it(monkeypatch):
    _fake_alias(monkeypatch, sys.executable)
    assert app_alias.resolve("ALIAS") == sys.executable


@pytest.mark.parametrize(
    "target",
    [None, "relative\\pwsh.exe", os.path.join(os.path.dirname(sys.executable), "missing-x.exe")],
)
def test_the_alias_itself_is_kept_when_its_target_cannot_be_used(monkeypatch, target):
    _fake_alias(monkeypatch, target)
    assert app_alias.resolve("ALIAS") == "ALIAS"


def test_a_target_that_is_not_a_program_keeps_the_alias(monkeypatch, tmp_path):
    not_exe = tmp_path / "pwsh.txt"
    not_exe.write_text("x", encoding="utf-8")
    _fake_alias(monkeypatch, str(not_exe))
    assert app_alias.resolve("ALIAS") == "ALIAS"


def test_an_ordinary_program_is_never_touched(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(app_alias, "_read_reparse", lambda p: seen.append(p))
    assert app_alias.resolve(sys.executable) == sys.executable
    assert seen == []  # not an alias: its reparse data is never read


def test_direct_argv_keeps_the_arguments_and_never_changes_the_callers_list(monkeypatch):
    _fake_alias(monkeypatch, sys.executable)
    saved = ["ALIAS", "-NoLogo", "-NoProfile"]
    out = app_alias.direct_argv(saved)
    assert out == [sys.executable, "-NoLogo", "-NoProfile"]
    assert saved == ["ALIAS", "-NoLogo", "-NoProfile"]  # the pane's saved argv keeps the alias


def test_resolution_never_raises(monkeypatch):
    def boom(p):
        raise RuntimeError("nope")

    monkeypatch.setattr(app_alias, "is_app_alias", boom)
    assert app_alias.resolve("ALIAS") == "ALIAS"
    assert app_alias.direct_argv([]) == []


@pytest.mark.skipif(not (WINDOWS and os.path.lexists(USER_ALIAS)), reason="no Store pwsh alias here")
def test_this_pcs_real_pwsh_alias_resolves_to_a_program_under_windowsapps():
    assert app_alias.is_app_alias(USER_ALIAS)
    real = app_alias.resolve(USER_ALIAS)
    assert real != USER_ALIAS
    assert real.lower().endswith("pwsh.exe") and "\\windowsapps\\microsoft.powershell_" in real.lower()
    assert os.path.isfile(real)


# --------------------------------------------------------------------------- #
# every backend starts the resolved program
# --------------------------------------------------------------------------- #


def test_the_pipe_backend_starts_the_real_program(monkeypatch):
    monkeypatch.setattr(app_alias, "resolve", lambda p: "REAL" if p == "ALIAS" else p)
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

    monkeypatch.setattr(B.subprocess, "Popen", FakePopen)
    with pytest.raises(OSError):
        B.PipeBackend().start(["ALIAS", "-NoLogo"], os.getcwd(), None, 80, 24)
    assert seen["argv"] == ["REAL", "-NoLogo"]


def test_the_pywinpty_backend_starts_the_real_program(monkeypatch):
    monkeypatch.setattr(app_alias, "resolve", lambda p: "REAL" if p == "ALIAS" else p)
    seen: dict = {}

    class FakePty:
        @staticmethod
        def spawn(argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

    fake = type(sys)("winpty")
    fake.PtyProcess = FakePty
    monkeypatch.setitem(sys.modules, "winpty", fake)
    with pytest.raises(OSError):
        B.WinPtyBackend().start(["ALIAS", "-NoLogo"], os.getcwd(), None, 80, 24)
    assert seen["argv"] == ["REAL", "-NoLogo"]


@pytest.mark.skipif(not WINDOWS, reason="ConPTY is Windows only")
def test_the_conpty_backend_starts_the_real_program(monkeypatch):
    """A REAL pseudoconsole: the argv names a program that does not exist,
    and the spawn succeeds only because the alias was resolved to cmd.exe."""
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    monkeypatch.setattr(app_alias, "resolve", lambda p: cmd if p == "NOT-A-PROGRAM-ALIAS" else p)
    b = B.ConPtyBackend()
    eof = threading.Event()
    out = bytearray()

    def on_output(data):
        out.extend(data)
        if b"\x1b[c" in data:
            b.write("\x1b[?1;2c")

    b.set_output_handler(on_output, eof.set)
    b.start(["NOT-A-PROGRAM-ALIAS", "/c", "echo", "alias-resolved"], os.getcwd(), None, 120, 30)
    try:
        assert eof.wait(60), "cmd.exe never exited"
        assert b"alias-resolved" in bytes(out)
    finally:
        b.kill()


def test_an_unresolved_program_still_reaches_the_backend_unchanged(monkeypatch):
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

    monkeypatch.setattr(B.subprocess, "Popen", FakePopen)
    with pytest.raises(OSError):
        B.PipeBackend().start([sys.executable, "-c", "pass"], os.getcwd(), None, 80, 24)
    assert seen["argv"] == [sys.executable, "-c", "pass"]
    assert subprocess.list2cmdline is not None
