"""A new Windows PowerShell terminal runs scripts when NOBODY set a policy (v1.422.2).

v1.422.0 made Windows PowerShell 5.1 the default terminal when PowerShell 7 is
only the Microsoft Store copy. On a client PC where no script policy is set,
5.1's policy is ``Restricted`` and refuses every ``.ps1`` — npm's
``claude.ps1`` / ``codex.ps1`` / ``opencode.ps1`` / ``pi.ps1`` come first on
PATH, so the coding CLIs would not start in a new terminal (PowerShell 7, the
old default, defaults to ``RemoteSigned``). The rule
(``terminals.shells.script_policy_unset``): only when no policy is set
anywhere — group policy (machine or user), the machine's own, the user's own,
``PSExecutionPolicyPreference`` — does a NEW Windows PowerShell terminal get
``-ExecutionPolicy RemoteSigned`` (process scope: that terminal only). Any
policy someone set is respected; an unreadable registry changes nothing.
"""

from __future__ import annotations

import sys

import pytest

from iron_jarvis.terminals import shells as SH

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell policy")


@pytest.fixture()
def registry(monkeypatch):
    """A fake registry: ``{(hive, path, name): value}``; absent = not set."""
    values: dict[tuple[str, str, str], object] = {}
    monkeypatch.setattr(SH, "_registry_value", lambda h, p, n: values.get((h, p, n)))
    monkeypatch.delenv("PSExecutionPolicyPreference", raising=False)
    return values


@win
def test_no_policy_anywhere_a_new_windows_powershell_terminal_allows_local_scripts(registry):
    assert SH.script_policy_unset() is True
    ps = next(s for s in SH.available_shells() if s["name"] == "powershell")
    assert ps["argv"] == ["powershell", "-NoLogo", "-ExecutionPolicy", "RemoteSigned"]


@win
@pytest.mark.parametrize("key", SH._POLICY_KEYS)
@pytest.mark.parametrize("value", ["Restricted", "AllSigned", "RemoteSigned", 1])
def test_a_policy_set_anywhere_is_respected_and_nothing_is_added(registry, key, value):
    registry[key] = value
    assert SH.script_policy_unset() is False
    ps = next(s for s in SH.available_shells() if s["name"] == "powershell")
    assert ps["argv"] == ["powershell", "-NoLogo"]


@win
def test_the_environments_own_policy_is_respected(registry, monkeypatch):
    monkeypatch.setenv("PSExecutionPolicyPreference", "AllSigned")
    assert SH.script_policy_unset() is False


@win
def test_a_registry_that_cannot_be_read_changes_nothing(monkeypatch):
    def broken(h, p, n):
        raise PermissionError("denied")

    monkeypatch.setattr(SH, "_registry_value", broken)
    monkeypatch.delenv("PSExecutionPolicyPreference", raising=False)
    assert SH.script_policy_unset() is False


def test_off_windows_nothing_is_ever_added(monkeypatch):
    monkeypatch.setattr(SH.sys, "platform", "linux")
    assert SH.script_policy_unset() is False


def test_the_policy_keys_are_exactly_windows_own_paths():
    """The first cut of this change wrote ``PowerShell\1\ShellIds`` through a
    non-raw string, where ``\1`` became the control character U+0001, so the
    machine's own policy was never found and this PC (LocalMachine
    RemoteSigned) read as "no policy". Pinned character for character."""
    expected = {
        ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "ExecutionPolicy"),
        ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "EnableScripts"),
        ("HKCU", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "ExecutionPolicy"),
        ("HKCU", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "EnableScripts"),
        ("HKLM", r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell", "ExecutionPolicy"),
        ("HKCU", r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell", "ExecutionPolicy"),
    }
    assert set(SH._POLICY_KEYS) == expected
    for _h, path, name in SH._POLICY_KEYS:
        assert all(32 <= ord(c) < 127 for c in path + name), repr(path)


#: The two Windows PowerShell argvs a NEW terminal is given.
PLAIN = ["powershell", "-NoLogo"]
FLAGGED = ["powershell", "-NoLogo", "-ExecutionPolicy", "RemoteSigned"]


@pytest.mark.parametrize("unset,saved,expect", [
    # A policy the user set AFTER the pane was opened: the restored terminal
    # (a new process) must not override it with the flag it was saved with —
    # process scope beats the user's own and the machine's own policy.
    (False, FLAGGED, PLAIN),
    # Still no policy: the flag stays.
    (True, FLAGGED, FLAGGED),
    # A pane saved before v1.422.2 (or while a policy was set) on a PC that
    # now has none: it runs scripts like a new terminal does.
    (True, PLAIN, FLAGGED),
    (False, PLAIN, PLAIN),
])
def test_a_restored_windows_powershell_pane_follows_the_policy_rule_of_today(monkeypatch, tmp_path, unset,
                                                                             saved, expect):
    """v1.422.2 REVIEW: a restored pane is a NEW PowerShell process started
    from its saved argv, so a saved ``-ExecutionPolicy RemoteSigned`` would
    beat a CurrentUser / LocalMachine policy the user set since (process scope
    wins over both). The flag a restore starts with is decided by the same
    rule as a new terminal's — for the two argvs Iron Jarvis itself gives
    Windows PowerShell only; any other saved argv is kept as it was."""
    from iron_jarvis.terminals.backend import FakeBackend
    from iron_jarvis.terminals.manager import TerminalManager

    monkeypatch.setattr(SH, "script_policy_unset", lambda: unset)
    m = TerminalManager()
    try:
        got = m.restore({"id": "term_ps", "cwd": str(tmp_path), "shell": "powershell", "argv": list(saved)},
                        backend=FakeBackend())
        assert got is not None and got.argv == expect, (unset, saved, got and got.argv)
    finally:
        m.kill_all()


@pytest.mark.parametrize("unset", [True, False])
@pytest.mark.parametrize("shell,saved", [
    ("powershell", ["powershell", "-NoLogo", "-NoProfile"]),  # not an argv Iron Jarvis gives
    ("powershell", ["powershell", "-NoLogo", "-ExecutionPolicy", "AllSigned"]),
    ("pwsh", ["C:/Program Files/PowerShell/7/pwsh.exe", "-NoLogo"]),
    ("cmd", ["cmd"]),
])
def test_any_other_saved_argv_is_restored_as_it_was(monkeypatch, tmp_path, unset, shell, saved):
    from iron_jarvis.terminals.backend import FakeBackend
    from iron_jarvis.terminals.manager import TerminalManager

    monkeypatch.setattr(SH, "script_policy_unset", lambda: unset)
    m = TerminalManager()
    try:
        got = m.restore({"id": "term_x", "cwd": str(tmp_path), "shell": shell, "argv": list(saved)},
                        backend=FakeBackend())
        assert got is not None and got.argv == saved
    finally:
        m.kill_all()


@win
def test_the_real_registry_reader_reads_a_value_windows_always_has():
    assert SH._registry_value("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuild")
    assert SH._registry_value("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "NoSuchValueIJ") is None
