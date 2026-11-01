"""v1.422.0 (audit gap B6): a NEW terminal no longer defaults to the Microsoft
Store copy of PowerShell 7 — the program whose process creation never returned
in the v1.407.0, v1.409.1 and v1.419.1 freezes. A real PowerShell 7 install is
the default when there is one, else Windows PowerShell; the Store copy stays in
the menu, said for what it is; a restored pane keeps the shell it had.

The Store test is the app's OWN (``terminals.app_alias.is_packaged``): a
``pwsh.exe`` under a folder named ``WindowsApps`` reads as packaged, exactly as
the real alias does — no detection of the test's own.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from iron_jarvis.terminals import shells as SH
from iron_jarvis.terminals.app_alias import is_packaged
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Store copy of PowerShell is a Windows thing")


def _exe(folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "pwsh.exe"
    exe.write_bytes(b"MZ")
    return str(exe)


@pytest.fixture()
def pc(tmp_path, monkeypatch):
    """A PC with only what each test puts on it: PATH and the install folders
    point into ``tmp_path``."""
    pf = tmp_path / "ProgramFiles"
    pf.mkdir()
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramW6432", str(pf))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    return tmp_path


def _path(monkeypatch, *folders: Path) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(str(f) for f in folders))


def test_only_the_store_copy_a_new_terminal_gets_windows_powershell(pc, monkeypatch):
    store = _exe(pc / "AppData" / "Local" / "Microsoft" / "WindowsApps")
    assert is_packaged(store), "the test's Store copy does not read as packaged"
    _path(monkeypatch, Path(store).parent)
    shells = SH.available_shells()
    assert [s["name"] for s in shells] == ["powershell", "pwsh", "cmd"]
    assert SH.default_shell()["name"] == "powershell"
    assert SH.resolve_shell(None) == ("powershell", ["powershell", "-NoLogo"])
    pwsh = shells[1]
    assert pwsh["argv"] == [store, "-NoLogo"] and pwsh["store"] is True
    assert pwsh["label"] == "PowerShell 7 (Microsoft Store copy)"
    assert shells[0]["label"] == "Windows PowerShell" and shells[2]["label"] == "Command Prompt"
    # Still pickable by its name.
    assert SH.resolve_shell("pwsh") == ("pwsh", [store, "-NoLogo"])


def test_a_real_install_is_the_default_and_the_store_copy_is_not_listed_twice(pc, monkeypatch):
    store = _exe(pc / "WindowsApps")
    real = _exe(pc / "ProgramFiles" / "PowerShell" / "7")
    _path(monkeypatch, Path(store).parent)  # the alias first on PATH, as on the owner's PC
    shells = SH.available_shells()
    assert [s["name"] for s in shells] == ["pwsh", "powershell", "cmd"]
    assert shells[0] == {"name": "pwsh", "argv": [real, "-NoLogo"], "label": "PowerShell 7"}
    assert SH.default_shell()["argv"][0] == real
    assert not any(s.get("store") for s in shells)


def test_a_real_install_later_on_path_beats_the_alias_first_on_path(pc, monkeypatch):
    store = _exe(pc / "WindowsApps")
    real = _exe(pc / "tools" / "pwsh7")
    _path(monkeypatch, Path(store).parent, Path(real).parent)
    assert SH.default_shell()["argv"] == [real, "-NoLogo"]


def test_no_powershell_7_at_all(pc):
    names = [s["name"] for s in SH.available_shells()]
    assert names == ["powershell", "cmd"]


def test_a_path_folder_on_a_network_share_is_never_looked_in(pc, monkeypatch):
    # A stat of a share path is an SMB connection (the v1.379.0 lesson).
    seen: list[str] = []
    real_lexists = os.path.lexists

    def spy(p):
        seen.append(str(p))
        return real_lexists(p)

    monkeypatch.setattr(SH.os.path, "lexists", spy)
    _path(monkeypatch, Path("\\\\nohost.invalid\\share\\bin"), pc / "x")
    SH.available_shells()
    assert not [p for p in seen if p.startswith(("\\\\", "//"))], seen


def test_a_new_terminal_uses_the_default_and_a_restored_one_keeps_its_shell(pc, monkeypatch):
    store = _exe(pc / "WindowsApps")
    _path(monkeypatch, Path(store).parent)
    m = TerminalManager()
    try:
        fresh = m.create(cwd=str(pc), backend=FakeBackend())
        assert fresh.shell == "powershell" and fresh.argv == ["powershell", "-NoLogo"]
        # A pane saved on the Store copy is restored on the Store copy: its
        # saved argv, untouched by the new default.
        kept = m.restore({"id": "term_old", "cwd": str(pc), "shell": "pwsh", "argv": [store, "-NoLogo"]},
                         backend=FakeBackend())
        assert kept is not None and kept.shell == "pwsh" and kept.argv == [store, "-NoLogo"]
        # An older snapshot with no argv: its NAME still means the same shell.
        older = m.restore({"id": "term_older", "cwd": str(pc), "shell": "pwsh"}, backend=FakeBackend())
        assert older is not None and older.shell == "pwsh" and older.argv == [store, "-NoLogo"]
    finally:
        m.kill_all()


def test_the_route_names_the_default_and_the_labels(pc, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    store = _exe(pc / "WindowsApps")
    _path(monkeypatch, Path(store).parent)
    with TestClient(create_app(str(tmp_path / "home"))) as c:
        body = c.get("/terminals/shells").json()
    assert body["default"] == "powershell"
    assert [s["label"] for s in body["shells"]] == [
        "Windows PowerShell", "PowerShell 7 (Microsoft Store copy)", "Command Prompt",
    ]
