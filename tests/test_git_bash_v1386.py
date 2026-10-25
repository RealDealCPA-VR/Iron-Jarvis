"""v1.386.0: FINDING Git for Windows' bash, and TELLING THE MODEL the truth
about the shell its ``Bash`` really runs in.

``sandbox/git_bash.find``: ``CLAUDE_CODE_GIT_BASH_PATH`` when set and valid,
else the Git for Windows folder ``git.exe`` lives in, else the standard install
folders — never WSL's ``System32`` / ``WindowsApps`` ``bash.exe`` (refused by
path, even through the setting) and never a bare ``bash`` looked up on PATH.
A file is verified once (``--version`` says GNU bash, within a bound) and
remembered; a miss is looked for again later, a changed setting at once, and
Re-detect forgets.

The description a Build turn's ``Bash`` carries follows the REAL interpreter:
Git's bash when found, cmd.exe and what differs from bash when not — never one
claimed while the other runs. Every other dialect's words are unchanged. The
benchmark's identity names the interpreter, so a result under one is never
reused for the other.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from iron_jarvis.harness import command_env as CE
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.platform import build_platform
from iron_jarvis.sandbox import git_bash as GB
from iron_jarvis.sandbox import native as N
from iron_jarvis.sandbox import shell_tool as ST

WIN = os.name == "nt"
REAL = GB.find() if WIN else None
needs_bash = pytest.mark.skipif(REAL is None, reason="Git for Windows' bash is not installed here")
needs_win = pytest.mark.skipif(not WIN, reason="Windows only")

FAKE = b"FAKE GNU BASH\n"
FAKE_VERSION = "GNU bash, version 9.9.9(1)-release (x86_64-pc-msys)"


@pytest.fixture(autouse=True)
def _fresh():
    GB.forget()
    yield
    GB.forget()


def _fake_bash(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE)
    return path


def _git_tree(root: Path, *, launcher: bool = True, git_at: str = "cmd") -> Path:
    """A Git for Windows folder made of stand-ins: ``cmd\\git.exe`` (or
    ``mingw64\\bin\\git.exe``), ``usr\\bin\\bash.exe`` and, with ``launcher``,
    ``bin\\bash.exe``."""
    _fake_bash(root / "usr" / "bin" / "bash.exe")
    (root / "mingw64" / "bin").mkdir(parents=True, exist_ok=True)
    if launcher:
        _fake_bash(root / "bin" / "bash.exe")
    git = root / git_at / "git.exe" if git_at == "cmd" else root / "mingw64" / "bin" / "git.exe"
    git.parent.mkdir(parents=True, exist_ok=True)
    git.write_bytes(b"MZ fake git")
    return git


@pytest.fixture()
def fake_verify(monkeypatch):
    """``--version`` answered for the stand-ins only. Nothing real is EVER
    run from these tests (a WSL ``bash.exe`` would start a whole Linux): any
    other file answers "not bash", and every path asked is recorded."""
    ran: list[str] = []

    def verify(path):
        ran.append(path)
        try:
            with open(path, "rb") as fh:
                head = fh.read(len(FAKE) + 1)
        except OSError:
            return ""
        return FAKE_VERSION if head == FAKE else ""

    monkeypatch.setattr(GB, "_verify", verify)
    return ran


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """No setting, an empty PATH, no standard folders: only what a test adds."""
    monkeypatch.delenv(GB.ENV_OVERRIDE, raising=False)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(GB, "_standard_roots", lambda: [])
    yield


# --------------------------------------------------------------------------- #
# finding it
# --------------------------------------------------------------------------- #


@needs_win
def test_the_setting_wins_when_it_names_a_real_bash(monkeypatch, tmp_path, fake_verify, isolated):
    bash = _fake_bash(tmp_path / "MyBash" / "bin" / "bash.exe")
    _git_tree(tmp_path / "Git")
    monkeypatch.setenv("PATH", str(tmp_path / "Git" / "cmd"))
    monkeypatch.setenv(GB.ENV_OVERRIDE, str(bash))
    found = GB.find()
    assert found is not None and found.path == str(bash) and found.source == "setting"
    assert found.version == FAKE_VERSION


@needs_win
@pytest.mark.parametrize("wsl", [
    r"C:\Windows\System32\bash.exe",
    r"C:\Windows\SysWOW64\bash.exe",
    r"C:\Windows\Sysnative\bash.exe",
    r"C:\Users\someone\AppData\Local\Microsoft\WindowsApps\bash.exe",
    r"C:\Program Files\WindowsApps\CanonicalGroupLimited.Ubuntu_2204\ubuntu.exe\..\bash.exe",
])
def test_wsl_bash_is_refused_by_path(wsl):
    assert GB.is_wsl_launcher(wsl)


@needs_win
def test_a_setting_naming_wsl_falls_back_to_git(monkeypatch, tmp_path, fake_verify, isolated):
    git = _git_tree(tmp_path / "Git")
    monkeypatch.setenv("PATH", str(git.parent))
    system_bash = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "bash.exe")
    monkeypatch.setenv(GB.ENV_OVERRIDE, system_bash)
    found = GB.find()
    assert found is not None and found.source == "git"
    assert found.path == str(tmp_path / "Git" / "bin" / "bash.exe")
    assert not any("system32" in p.lower() for p in fake_verify), "WSL's bash was run"


@needs_win
def test_a_setting_that_is_not_bash_or_missing_falls_back(monkeypatch, tmp_path, fake_verify, isolated):
    git = _git_tree(tmp_path / "Git")
    monkeypatch.setenv("PATH", str(git.parent))
    other = tmp_path / "x" / "python.exe"
    other.parent.mkdir()
    other.write_bytes(FAKE)
    for setting in (str(other), str(tmp_path / "missing" / "bash.exe")):
        GB.forget()
        monkeypatch.setenv(GB.ENV_OVERRIDE, setting)
        found = GB.find()
        assert found is not None and found.source == "git", setting


@needs_win
@pytest.mark.parametrize("git_at", ["cmd", "mingw64"])
def test_derived_from_where_git_is_installed(monkeypatch, tmp_path, fake_verify, isolated, git_at):
    git = _git_tree(tmp_path / "Git", git_at=git_at)
    monkeypatch.setenv("PATH", str(git.parent))
    found = GB.find()
    assert found is not None and found.source == "git"
    assert found.path == str(tmp_path / "Git" / "bin" / "bash.exe")
    assert found.sets_path is True
    assert found.path_dirs == (str(tmp_path / "Git" / "mingw64" / "bin"), str(tmp_path / "Git" / "usr" / "bin"))


@needs_win
def test_a_git_without_the_launcher_uses_usr_bin_bash_and_puts_gits_tools_first(monkeypatch, tmp_path,
                                                                                  fake_verify, isolated):
    git = _git_tree(tmp_path / "Git", launcher=False)
    monkeypatch.setenv("PATH", str(git.parent))
    found = GB.find()
    assert found is not None and found.path == str(tmp_path / "Git" / "usr" / "bin" / "bash.exe")
    assert found.sets_path is False
    argv, env = GB.invocation(found, "ls", {"PATH": r"C:\Windows\System32"})
    assert env["PATH"].split(os.pathsep) == [
        str(tmp_path / "Git" / "mingw64" / "bin"), str(tmp_path / "Git" / "usr" / "bin"), r"C:\Windows\System32",
    ]


@needs_win
def test_the_standard_folders_are_looked_in_last(monkeypatch, tmp_path, fake_verify, isolated):
    _git_tree(tmp_path / "PF" / "Git")
    monkeypatch.setattr(GB, "_standard_roots", lambda: [str(tmp_path / "PF" / "Git")])
    found = GB.find()
    assert found is not None and found.source == "standard"
    assert found.path == str(tmp_path / "PF" / "Git" / "bin" / "bash.exe")


@needs_win
def test_a_bash_on_path_is_never_used_by_name(monkeypatch, tmp_path, fake_verify, isolated):
    """On the user's normal PATH ``bash`` can be WSL's: a bash.exe found only
    by being on PATH (no Git folder around it) is never picked, and PATH is
    only ever asked for ``git``."""
    loose = _fake_bash(tmp_path / "tools" / "bash.exe")
    monkeypatch.setenv("PATH", str(loose.parent))
    asked: list[str] = []
    real_which = shutil.which

    def which(name, *a, **kw):
        asked.append(name)
        return real_which(name, *a, **kw)

    monkeypatch.setattr(GB.shutil, "which", which)
    assert GB.find() is None
    assert asked == ["git"]


@needs_win
def test_it_is_remembered_and_looked_for_again_when_it_should_be(monkeypatch, tmp_path, fake_verify, isolated):
    searches: list[int] = []
    real_search = GB._search

    def search():
        searches.append(1)
        return real_search()

    monkeypatch.setattr(GB, "_search", search)
    monkeypatch.setenv("PATH", str(tmp_path / "Git" / "cmd"))  # git is not installed YET
    assert GB.find() is None and GB.find() is None
    assert len(searches) == 1, "a miss was not remembered"
    _git_tree(tmp_path / "Git")  # installed now, same PATH
    assert GB.find() is None and len(searches) == 1, "a miss was looked for again too soon"
    monkeypatch.setattr(GB, "MISS_RECHECK_S", 0.0)  # ... until the recheck time passes
    found = GB.find()
    assert found is not None and len(searches) == 2
    assert GB.find() == found and len(searches) == 2, "a found bash was not remembered"
    monkeypatch.setenv(GB.ENV_OVERRIDE, str(tmp_path / "nowhere" / "bash.exe"))  # a changed setting
    assert GB.find() == found and len(searches) == 3, "a changed setting did not look again"
    GB.forget()
    assert GB.find() == found and len(searches) == 4, "Re-detect did not look again"
    (tmp_path / "Git" / "bin" / "bash.exe").unlink()
    again = GB.find()
    assert again is not None and again.path.endswith(os.path.join("usr", "bin", "bash.exe"))


@needs_bash
def test_the_real_bash_is_verified_once_with_a_bound(monkeypatch):
    from iron_jarvis.core import procs

    runs: list = []
    real_run = procs.run

    def run(argv, **kw):
        runs.append((list(argv), kw.get("timeout")))
        return real_run(argv, **kw)

    monkeypatch.setattr(procs, "run", run)
    path = REAL.path
    v1 = GB._verify(path)
    v2 = GB._verify(path)
    assert v1 == v2 and v1.startswith("GNU bash, version ")
    assert runs == [([path, "--version"], GB.VERSION_TIMEOUT_S)]


@needs_win
def test_a_program_that_is_not_gnu_bash_is_never_verified():
    """A real program that answers ``--version`` (here this Python) is not
    bash: only "GNU bash" passes."""
    import sys

    assert GB._verify(sys.executable) == ""


def test_off_windows_nothing_is_looked_for(monkeypatch):
    class _Os:
        name = "posix"

        def __getattr__(self, item):
            return getattr(os, item)

    monkeypatch.setattr(GB, "os", _Os())
    monkeypatch.setattr(GB, "_search", lambda: pytest.fail("looked for Git bash on another OS"))
    assert GB.find() is None


def test_rescan_forgets_it(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import connections as C

    forgot: list[int] = []
    monkeypatch.setattr(GB, "forget", lambda: forgot.append(1))
    monkeypatch.setattr("iron_jarvis.providers.cli_detect.detect_cli_providers", lambda **kw: [])
    platform = build_platform(str(tmp_path / "home"))
    try:
        C.rescan_providers(SimpleNamespace(platform=platform))
    except Exception:  # noqa: BLE001 — the rest of the rescan is not this test's
        pass
    assert forgot == [1]


def test_a_command_an_environment_variable_cannot_carry_is_refused_in_words():
    fake = GB.GitBash(path="bash.exe", version="x", source="git", path_dirs=(), sets_path=True)
    with pytest.raises(ValueError, match="NUL"):
        GB.invocation(fake, "a\x00b", {})
    with pytest.raises(ValueError, match="script file"):
        GB.invocation(fake, "x" * (GB.MAX_COMMAND_CHARS + 1), {})
    argv, env = GB.invocation(fake, "echo hi", {"PATH": "p"})
    assert argv == ["bash.exe", "-c", GB.RUNNER] and env == {"PATH": "p", GB.COMMAND_VAR: "echo hi"}


# --------------------------------------------------------------------------- #
# the words follow the real interpreter
# --------------------------------------------------------------------------- #


def _bash_spec(platform, turn) -> str:
    return next(s for s in turn.specs(platform.registry) if s["name"] == "Bash")["description"]


@needs_bash
def test_bash_words_when_bash_runs_it(tmp_path):
    """RED on v1.385.1: Claude's Bash was told "Windows cmd.exe with the
    user's own PATH" while bash-written commands ran in cmd."""
    platform = build_platform(str(tmp_path / "home"))
    turn = M.build(registry=platform.registry, permissions=platform.permissions, requested="claude",
                   provider="anthropic", model="claude-sonnet-4-5", stream=True, today="",
                   bash_description=CE.bash_shell_description(True))
    words = _bash_spec(platform, turn)
    assert "Git for Windows' bash (GNU bash" in words and "not cmd.exe" in words
    assert "Write bash" in words and "heredocs" in words and "user's own PATH" in words
    assert "Windows cmd.exe with" not in words and "This is Windows cmd.exe" not in words
    assert "Prefer the built-in tools" not in words  # the dialect's names, not Jarvis's


@needs_win
def test_cmd_words_and_what_differs_when_no_bash(tmp_path, monkeypatch):
    monkeypatch.setattr(GB, "find", lambda: None)
    platform = build_platform(str(tmp_path / "home"))
    turn = M.build(registry=platform.registry, permissions=platform.permissions, requested="claude",
                   provider="anthropic", model="claude-sonnet-4-5", stream=True, today="",
                   bash_description=CE.bash_shell_description(True))
    words = _bash_spec(platform, turn)
    assert "Windows cmd.exe with the user's own PATH" in words
    assert ST.NOT_BASH_NOTE in words and "';' does not separate commands (use '&&')" in words
    assert "Git for Windows' bash (" not in words


@needs_win
async def test_a_build_turns_words_and_its_interpreter_agree(tmp_path, monkeypatch):
    """Through the REAL lane: what the Bash spec says is what runs it — bash
    words with bash's argv, cmd words with cmd's line."""
    from iron_jarvis.core import procs
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.providers.adapters.base import ToolCall
    from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

    seen: list = []
    real_run = procs.run

    def run(argv, **kw):
        seen.append(argv)
        return real_run(argv, **kw)

    monkeypatch.setattr(procs, "run", run)
    for found in ((REAL,) if REAL is not None else ()) + (None,):
        monkeypatch.setattr(GB, "find", lambda f=found: f)
        app = create_app(str(tmp_path / f"home-{found is not None}"))
        work = tmp_path / f"w-{found is not None}"
        work.mkdir()
        sid = new_session(app, work, approval_mode="yolo")["id"]
        script = wire(app, Script([[ToolCall(id="b1", name="Bash", arguments={"command": "echo agree"})], "Done."]))
        seen.clear()
        status, raw = await open_turn(app, sid, {"text": "go", "turn_id": f"agree-{found is not None}"}, timeout=60)
        assert status == 200 and dict(frames_of(raw)).get("done")
        words = next(s for s in script.calls[0]["specs"] if s["name"] == "Bash")["description"]
        ran = [a for a in seen if a == "echo agree" or (isinstance(a, list) and a[:2] == [getattr(found, "path", ""), "-c"])]
        assert ran, seen
        if found is not None:
            assert "Git for Windows' bash" in words and isinstance(ran[0], list)
        else:
            assert "This is Windows cmd.exe, not bash" in words and ran[0] == "echo agree"


def test_every_other_dialects_words_are_unchanged(tmp_path, monkeypatch):
    """Codex's exec_command, Qwen Code's run_shell_command and Jarvis's own
    shell carry exactly the words they did, and no Git bash is looked for."""
    monkeypatch.setattr(CE, "bash_shell_description", lambda build: pytest.fail("bash words for a non-bash dialect"))
    platform = build_platform(str(tmp_path / "home"))
    shell_words = CE.build_shell_description()
    for dialect, name in (("gpt", "exec_command"), ("qwen", "run_shell_command")):
        turn = M.build(registry=platform.registry, permissions=platform.permissions, requested=dialect,
                       provider="openai", model="gpt-5", stream=True, today="", shell_description=shell_words)
        spec = next(s for s in turn.specs(platform.registry) if s["name"] == name)
        tool = D.DIALECTS[dialect].lookup(name)
        expected = tool.description + "\n\nHow commands run here: " + M.runtime_note(
            platform.registry, "shell", shell_description=shell_words)
        assert spec["description"] == expected
        assert "bash" not in spec["description"].lower().replace("bash -lc", "")
    assert not M.speaks_bash("gpt", "openai", "gpt-5") and not M.speaks_bash("auto", "custom", "glm-5.3-flash")
    assert M.speaks_bash("auto", "anthropic", "claude-sonnet-4-5") and M.speaks_bash("claude", "custom", "glm")


async def test_the_lane_works_the_bash_words_out_only_for_a_bash_dialect(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.daemon import chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody

    calls: list = []
    monkeypatch.setattr(CE, "bash_shell_description", lambda build: calls.append(build) or "BASH WORDS")
    platform = build_platform(str(tmp_path / "home"))
    d = SimpleNamespace(platform=platform)
    work = tmp_path / "w"
    work.mkdir()
    body = ChatBody(messages=[ChatMessageBody(role="user", content="x")], workspace_dir=str(work))
    for provider, model, speaks in (("openai", "gpt-5", False), ("anthropic", "claude-x", True),
                                    ("custom", "glm-5.3-flash", False)):
        calls.clear()
        turn, _ = await chat_turn._build_harness(
            d, body, "auto", provider=provider, model=model, resolved_proj=None,
            grounding=chat_turn._Grounding(), stream=True, low_trust=False, session_id="build:t",
        )
        assert (calls == [True]) is speaks, (provider, calls)
        assert (turn.bash_description == "BASH WORDS") is speaks
        if speaks:
            assert turn.shell_description == "", "the cmd words were worked out for nothing"
        else:
            assert turn.shell_description == CE.build_shell_description()


def test_on_another_os_the_bash_words_are_the_shell_words(monkeypatch):
    """Off Windows the platform's own shell is POSIX sh already: no bash
    words (the shell words apply, as before) and no interpreter in the
    benchmark's identity."""

    class _Os:
        name = "posix"

        def __getattr__(self, item):
            return getattr(os, item)

    monkeypatch.setattr(CE, "os", _Os())
    monkeypatch.setattr(GB, "find", lambda: pytest.fail("looked for Git bash on another OS"))
    assert CE.bash_shell_description(True) == ""
    assert CE.bash_identity() == ""


# --------------------------------------------------------------------------- #
# the benchmark's identity names the interpreter
# --------------------------------------------------------------------------- #


@needs_win
def test_the_bench_identity_names_the_interpreter_for_claudes_dialect(tmp_path, monkeypatch):
    """RED on v1.385.1: a run under cmd.exe and one under Git's bash had the
    same identity, so a cmd-era result would be reused for a bash harness."""
    from iron_jarvis.harness_bench.bench import Configuration
    from iron_jarvis.harness_bench.runners import JarvisRunner

    platform = build_platform(str(tmp_path / "home"))
    fake = GB.GitBash(path=r"C:\G\bin\bash.exe", version=FAKE_VERSION, source="git", path_dirs=(), sets_path=True)
    monkeypatch.setattr(GB, "find", lambda: fake)
    with_bash = JarvisRunner(platform, provider="anthropic", model="claude-sonnet-4-5").identity()
    monkeypatch.setattr(GB, "find", lambda: None)
    with_cmd = JarvisRunner(platform, provider="anthropic", model="claude-sonnet-4-5").identity()
    assert with_bash["command_shell"] == f"bash: {FAKE_VERSION}" and with_cmd["command_shell"] == "cmd.exe"
    keys = {Configuration("jarvis", "anthropic", "claude-sonnet-4-5", h).key for h in (with_bash, with_cmd)}
    assert len(keys) == 2
    # ... and a dialect with no bash tool keeps its identity's shape.
    generic = JarvisRunner(platform, provider="custom", model="glm-5.3-flash").identity()
    assert "command_shell" not in generic


@needs_bash
def test_the_bench_shows_claudes_bash_the_bash_words(tmp_path):
    from iron_jarvis.harness_bench.runners import JarvisRunner

    platform = build_platform(str(tmp_path / "home"))
    runner = JarvisRunner(platform, provider="anthropic", model="claude-sonnet-4-5")
    words = _bash_spec(platform, runner.harness())
    assert "Git for Windows' bash" in words
    assert runner.identity()["command_shell"].startswith("bash: GNU bash, version ")


def test_the_words_live_beside_the_truth():
    """The bash words name what ``invocation`` really gives a command."""
    words = N.bash_env_description("GNU bash, version 5.2.37(1)-release (x86_64-pc-msys)", ("git", "posix"), build=True)
    assert words.startswith("Git for Windows' bash (GNU bash 5.2.37), not cmd.exe, with the user's own PATH")
    assert "Found on it: git." in words and "Secret variables" in words
    scrubbed = N.bash_env_description("GNU bash, version 5.2.37", (), build=False)
    assert "minimal scrubbed PATH" in scrubbed and "Found on it" not in scrubbed
