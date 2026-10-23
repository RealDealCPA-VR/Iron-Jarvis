"""v1.377.0 (IJ harness part 2, Phase 6): THE AGENT'S TERMINAL FINDS THE
USER'S TOOLS — the user's decision of 2026-10-23.

A Build session's ``shell`` and program tools run with the user's normal PATH
and home folder (``sandbox/native.build_env``) — ``git``, ``node``, ``npm`` and
``python`` resolve by name and ``git commit`` uses the user's own identity —
with SECRET VARIABLES STILL STRIPPED; chat, agents and every unattended run
keep ``scrubbed_env`` exactly as before. The switch is keyed on the SESSION
(``harness/command_env``): a ``build:<id>`` turn and a helper registered for
one, never the tool, the posture or a model argument.

Every child-environment pin reads the environment the CHILD RECEIVED (``set``
/ ``env`` written by the real command), never a dict. Names only: no value is
ever compared or printed (a failure lists names).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import process as P
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.sandbox import native as N
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool, runtime_description
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import Script, new_session, wire
from tests.test_build_git_v1375 import turn

WIN = os.name == "nt"
#: A secret planted in the daemon's own environment for these tests.
PLANTED = "IJ_TEST_SECRET_TOKEN"
#: A plain variable the user's terminal would have — it must reach the child.
PLAIN = "IJ_PHASE6_PLAIN_SETTING"


# --------------------------------------------------------------------------- #
# the keep / drop table (names only)
# --------------------------------------------------------------------------- #

#: What a coding session's child KEEPS (the user's terminal has them).
KEEP = [
    "PATH", "PATHEXT", "SystemRoot", "COMSPEC", "WINDIR", "HOME", "USERPROFILE", "HOMEDRIVE",
    "HOMEPATH", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USERNAME", "USERDOMAIN",
    "COMPUTERNAME", "PROGRAMFILES", "PROGRAMDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_IDENTIFIER",
    "OS", "PSMODULEPATH", "PUBLIC", "ALLUSERSPROFILE", "ONEDRIVE",
    # git's own identity and config — the reason this exists (AUTHOR, never AUTH)
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
    "GIT_CONFIG_GLOBAL", "GIT_SSH_COMMAND",
    # node / npm / pnpm / python / uv
    "NVM_HOME", "NVM_SYMLINK", "PNPM_HOME", "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS",
    "NPM_CONFIG_PREFIX", "PYTHONPATH", "PIP_INDEX_URL", "UV_CACHE_DIR", "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "CHOCOLATEYINSTALL",
    # the user's own CLI settings (not secrets, not a parent's markers)
    "CLAUDE_CONFIG_DIR", "CODEX_HOME", PLAIN,
    # moved on purpose (the v1.377.0 review): the ssh agent's socket path and
    # Git Credential Manager's settings are not secrets, and git push needs
    # them (tests/test_build_env_review_v1377.py holds the decision).
    "SSH_AUTH_SOCK", "GCM_CREDENTIAL_STORE",
]

#: What is DROPPED: secrets by name, vendors', this app's own, a parent's.
DROP = [
    PLANTED, "PIXIO_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
    "OPENAI_BASE_URL", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AZURE_OPENAI_ENDPOINT",
    "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "GH_TOKEN", "GITHUB_TOKEN", "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN", "NPM_TOKEN", "NPM_CONFIG__AUTH", "NPM_CONFIG_//REGISTRY.NPMJS.ORG/:_AUTHTOKEN",
    "DATABASE_PASSWORD", "MY_SECRET", "SESSION_COOKIE", "PRIVATE_KEY_PATH", "SENTRY_DSN",
    "SENTRY-TRACE", "BAGGAGE", "AZURE_DEVOPS_EXT_PAT", "STRIPE_SECRET", "OAUTH_CLIENT",
    "SOME_AUTHENTICATION", "BASIC_AUTH", "GIT_AUTHORIZATION", "GIT_ASKPASS",
    "SSH_ASKPASS", "DB_PASSWD", "MY_CREDENTIALS_FILE", "SESSIONNAME",
    # this app's own (the desktop's spawn env names — see the next test)
    "IRONJARVIS_TOKEN", "IRONJARVIS_HOME", "IRONJARVIS_ROOT", "IRONJARVIS_HOST_ALLOWLIST",
    "IRON_PROXY_DATA_DIR", "ELECTRON_RUN_AS_NODE", "CHROME_CRASHPAD_PIPE_NAME",
    "PLAYWRIGHT_BROWSERS_PATH", "_PYI_ARCHIVE_FILE", "_MEIPASS2", "VIRTUAL_ENV",
    "UV_RUN_RECURSION_DEPTH",
    # the git redirects git_env() drops
    "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    # a parent Claude Code session's markers and settings
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_USER_EMAIL", "CLAUDE_AGENT_SDK_VERSION",
]


def _base() -> dict[str, str]:
    return {name: "x" for name in KEEP + DROP} | {"PATH": os.environ.get("PATH", "")}


def test_the_build_environment_keeps_the_users_tools_and_drops_every_secret_by_name():
    env = N.build_env(_base())
    upper = {k.upper() for k in env}
    kept_wrong = [n for n in DROP if n.upper() in upper]
    lost = [n for n in KEEP if n.upper() not in upper]
    assert kept_wrong == [], f"secret or internal names reached the child: {kept_wrong}"
    assert lost == [], f"names the user's terminal has were dropped: {lost}"
    # git never waits on a terminal prompt nobody sees (git_env's rule).
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_no_value_of_a_kept_variable_is_changed_except_path():
    base = _base() | {"USERPROFILE": "C:\\Users\\someone", "GIT_AUTHOR_NAME": "Someone"}
    env = N.build_env(base)
    assert env["USERPROFILE"] == base["USERPROFILE"] and env["GIT_AUTHOR_NAME"] == "Someone"


def test_every_name_the_desktop_gives_the_daemon_of_its_own_is_dropped():
    """The packaged desktop spawns the daemon with ``process.env`` (the user's)
    plus its OWN names (desktop/main.js): the install token, its folders, the
    Iron-Proxy / Playwright node paths, the network setting. Every one is this
    app's, never the user's terminal's — read off main.js, so a new one is
    caught."""
    main = (Path(__file__).resolve().parents[1] / "desktop" / "main.js").read_text(encoding="utf-8")
    names = set(re.findall(r"\b(IRONJARVIS_[A-Z_]+)\s*:", main)) | {"ELECTRON_RUN_AS_NODE"}
    assert "IRONJARVIS_TOKEN" in names and len(names) >= 5
    leaked = sorted(n for n in names if not N.secret_name(n))
    assert leaked == [], f"names the desktop adds reach a Build child: {leaked}"


def test_the_dev_daemons_own_virtual_environment_is_not_on_the_childs_path(monkeypatch, tmp_path):
    """``uv run`` puts the daemon's venv Scripts first: ``python`` would be Iron
    Jarvis's (with its packages), not the user's."""
    venv = tmp_path / "ij-venv"
    scripts = venv / ("Scripts" if WIN else "bin")
    scripts.mkdir(parents=True)
    other = tmp_path / "user-bin"
    other.mkdir()
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base-python"))
    env = N.build_env({"PATH": os.pathsep.join([str(scripts), str(other)])})
    assert env["PATH"].split(os.pathsep) == [str(other)]


def test_the_description_names_only_tools_really_found_and_never_a_store_stub(tmp_path):
    """Never overpromise: a tool the user does not have is not named, and the
    Microsoft Store's ``python.exe`` alias (it opens the Store) is not Python."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub = tmp_path / "Microsoft" / "WindowsApps"
    stub.mkdir(parents=True)
    ext = ".exe" if WIN else ""
    for name in ("git", "node"):
        p = bin_ / f"{name}{ext}"
        p.write_bytes(b"")
        p.chmod(0o755)
    py_stub = stub / f"python{ext}"
    py_stub.write_bytes(b"")
    py_stub.chmod(0o755)
    N._TOOLS_CACHE.clear()
    found = N.build_env_tools({"PATH": os.pathsep.join([str(bin_), str(stub)]), "PATHEXT": ".EXE"})
    assert "git" in found and "node" in found
    assert "python" not in found and "npm" not in found
    words = N.build_env_description(found, "nt")
    assert "Found on it: git, node" in words and "python" not in words.split("Found on it:")[1].split(".")[0]
    assert "user's own PATH" in words and "Secret variables" in words
    assert "no python" not in words
    assert "None of git" in N.build_env_description((), "nt")


# --------------------------------------------------------------------------- #
# the switch: keyed on the session
# --------------------------------------------------------------------------- #


def test_the_switch_is_keyed_on_the_session_and_a_build_sessions_helper():
    from iron_jarvis.harness import command_env as CE

    assert CE.build_session_of("build:abc") == "abc"
    assert CE.build_session_of("chat") == "" and CE.build_session_of("sess_1") == ""
    call = SimpleNamespace(ask_id="")
    with H._LIVE_LOCK:
        H._LIVE.setdefault("build:abc", {})["sess_helper"] = call
        H._LIVE.setdefault("chat", {})["sess_bench_helper"] = call
    try:
        assert CE.build_session_of("sess_helper") == "abc"
        assert CE.build_session_of("sess_bench_helper") == "", "the benchmark's helper is not a Build session"
    finally:
        with H._LIVE_LOCK:
            H._LIVE.get("build:abc", {}).pop("sess_helper", None)
            H._LIVE.get("chat", {}).pop("sess_bench_helper", None)
    assert CE.build_session_of("sess_helper") == "", "a helper that ended is no longer one"
    assert CE.command_env("allow", "build:abc") is None, "a policy that inherits is unchanged"


# --------------------------------------------------------------------------- #
# what the CHILD received
# --------------------------------------------------------------------------- #


def _dump_cmd(out: str) -> str:
    return f"set > {out}" if WIN else f"env > {out}"


def _names(path: Path) -> set[str]:
    text = path.read_bytes().decode("utf-8", "replace")
    return {line.split("=", 1)[0].strip().upper() for line in text.splitlines() if "=" in line}


def _ctx(ws: Path, session: str, config=None) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id=session, agent_run_id="r",
        config=config or SimpleNamespace(home=ws.parent, sandbox={}),
        event_bus=None, engine=None,
    )


@pytest.fixture()
def planted(monkeypatch, tmp_path):
    """A secret and a plain variable in the DAEMON's own environment."""
    monkeypatch.setenv(PLANTED, "planted-not-for-the-child")
    monkeypatch.setenv(PLAIN, "kept")
    if not os.environ.get("USERPROFILE") and not os.environ.get("HOME"):
        monkeypatch.setenv("HOME", str(tmp_path))
    getattr(N, "_TOOLS_CACHE", {}).clear()
    yield


def _home_names() -> set[str]:
    return {n for n in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "HOME")
            if os.environ.get(n)}


@pytest.fixture()
def ws(tmp_path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    return w


async def test_a_build_sessions_shell_child_gets_path_and_home_and_no_secret(planted, ws):
    tool = SandboxedShellTool()
    res = await tool.execute({"command": _dump_cmd("build-env.txt")}, _ctx(ws, "build:t1"))
    assert res.ok, res.error
    names = _names(ws / "build-env.txt")
    assert PLANTED not in names, "the planted secret reached a Build session's command"
    assert PLAIN in names, "a plain variable of the user's did not reach it"
    assert _home_names() <= names, f"home folders missing: {sorted(_home_names() - names)}"
    assert "PATH" in names


async def test_chat_and_an_unattended_run_keep_the_scrubbed_environment(planted, ws):
    tool = SandboxedShellTool()
    for session, out in (("chat", "chat-env.txt"), ("sess_schedule_nightly", "sched-env.txt")):
        res = await tool.execute({"command": _dump_cmd(out)}, _ctx(ws, session))
        assert res.ok, res.error
        names = _names(ws / out)
        assert PLANTED not in names and PLAIN not in names, session
        if WIN:
            assert "USERPROFILE" not in names and "APPDATA" not in names, session
        scrub = {k.upper() for k in N.scrubbed_env()}
        # cmd adds a few of its own (PROMPT, COMSPEC…); nothing of the daemon's.
        assert not ({PLANTED, PLAIN, "LOCALAPPDATA"} & names), session
        assert "PATH" in scrub


async def test_a_helper_of_a_build_session_gets_the_build_environment(planted, ws):
    call = SimpleNamespace(ask_id="")
    with H._LIVE_LOCK:
        H._LIVE.setdefault("build:t2", {})["sess_helper_t2"] = call
    try:
        res = await SandboxedShellTool().execute({"command": _dump_cmd("helper-env.txt")},
                                                 _ctx(ws, "sess_helper_t2"))
    finally:
        with H._LIVE_LOCK:
            H._LIVE.get("build:t2", {}).pop("sess_helper_t2", None)
    assert res.ok, res.error
    names = _names(ws / "helper-env.txt")
    assert PLAIN in names and PLANTED not in names
    # the same helper session after its call ended: scrubbed again
    res = await SandboxedShellTool().execute({"command": _dump_cmd("after-env.txt")},
                                             _ctx(ws, "sess_helper_t2"))
    assert PLAIN not in _names(ws / "after-env.txt")


async def test_a_build_sessions_program_gets_the_build_environment_and_others_do_not(planted, ws):
    tool = P.ProcessStartTool()
    try:
        res = await tool.execute({"command": _dump_cmd("proc-build.txt"), "until_exit": True,
                                  "wait_ms": 10_000}, _ctx(ws, "build:t3"))
        assert res.ok, res.error
        res = await tool.execute({"command": _dump_cmd("proc-chat.txt"), "until_exit": True,
                                  "wait_ms": 10_000}, _ctx(ws, "sess_other"))
        assert res.ok, res.error
    finally:
        P.end_session(None)
    build = _names(ws / "proc-build.txt")
    other = _names(ws / "proc-chat.txt")
    assert PLAIN in build and PLANTED not in build and _home_names() <= build
    assert PLAIN not in other and PLANTED not in other


async def test_low_trust_still_refuses_the_native_runtime_in_a_build_session(planted, ws, monkeypatch):
    from iron_jarvis.core.trust import LOW_TRUST_SHELL_REFUSAL
    from iron_jarvis.sandbox import shell_tool as st
    from iron_jarvis.sandbox.native import NativeSandbox

    monkeypatch.setattr(st.SandboxManager, "get", lambda self: NativeSandbox(self.policy))
    res = await SandboxedShellTool().execute(
        {"command": _dump_cmd("lowtrust.txt"), "_isolate": True}, _ctx(ws, "build:t4"),
    )
    assert res.ok is False and res.error == LOW_TRUST_SHELL_REFUSAL
    assert not (ws / "lowtrust.txt").exists(), "a native command ran under low trust"


# --------------------------------------------------------------------------- #
# the model is told the truth
# --------------------------------------------------------------------------- #


def test_the_shell_words_differ_only_for_a_build_session():
    plain = runtime_description("nt")
    build = runtime_description("nt", build_tools=("git", "python"))
    assert "minimal scrubbed PATH" in plain and "no python" in plain
    assert "user's own PATH" in build and "Found on it: git, python" in build
    assert "no python" not in build


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def test_a_build_turns_model_is_told_the_users_tools_and_a_chat_harness_turn_is_not(app, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work, approval_mode="yolo")["id"]
    script = wire(app, Script(["Done."]))
    await turn(app, sid, "hello", "env-words-1")
    bash = next(s for s in script.calls[0]["specs"] if s["name"] == "Bash")
    assert "user's own PATH" in bash["description"]
    assert "minimal scrubbed PATH" not in bash["description"]
    # CONTROL: the same harness for a turn that is not a Build session.
    from iron_jarvis.daemon import chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody

    d = SimpleNamespace(platform=app.state.platform)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="x")], workspace_dir=str(work))
    turn_chat, _ = await chat_turn._build_harness(
        d, body, "auto", provider="anthropic", model="claude-x", resolved_proj=None,
        grounding=chat_turn._Grounding(), stream=True, low_trust=False, session_id="chat",
    )
    spec = next(s for s in turn_chat.specs(app.state.platform.registry) if s["name"] == "Bash")
    assert "minimal scrubbed PATH" in spec["description"] or "scrubbed" in spec["description"]
    assert "user's own PATH" not in spec["description"]


async def test_local_only_still_cards_every_command_in_a_build_session(app, tmp_path, planted):
    """Local only keeps its always-ask for the shell and the program tools in
    a Build session — the user's PATH does not change who is asked: in YOLO a
    local-only turn's ``shell`` and ``process_start`` each card ("Allow once"
    only), and a denied one never runs."""
    from tests.test_build_plan_ask_v1367 import _finish, _start
    from tests.test_build_sessions_v1367 import turn_frames, until
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work, provider="mock", model="mock", approval_mode="yolo", local_only=True)["id"]
    wire(app, Script([
        [ToolCall(id="s1", name="shell", arguments={"command": _dump_cmd("lo-shell.txt")})],
        [ToolCall(id="p1", name="process_start", arguments={"command": _dump_cmd("lo-proc.txt"),
                                                            "until_exit": True})],
        "Done.",
    ], who=("mock", "mock")))
    drop, task = await _start(app, sid, "dump the environment", "env-lo-1")
    cards = []
    for n in (1, 2):
        got = await until(lambda: [d for e, d in turn_frames(app, "env-lo-1") if e == "approval"][n - 1:n])
        assert got, f"card {n} never came"
        card = got[0]
        cards.append(card)
        assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "env-lo-1", drop, task)
    assert [c["tool"] for c in cards] == ["shell", "process_start"]
    assert all(c["once_only"] is True for c in cards), "Local only offers Allow once only"
    assert not (work / "lo-shell.txt").exists() and not (work / "lo-proc.txt").exists()


async def test_a_real_helper_of_a_build_turn_is_told_and_given_the_users_tools(app, tmp_path, planted):
    """A helper started by a Build turn (Claude Code's ``Task`` → ``delegate``)
    runs its OWN ``shell`` with the Build environment — read off the file its
    real command wrote — and its model is told so in that tool's words."""
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.router import RouteResult

    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work, approval_mode="yolo", granted_tools=["shell"])["id"]
    seen: list[dict] = []
    main_moves = [[ToolCall(id="a1", name="Task", arguments={
        "description": "dump env", "prompt": "Write the environment to helper-env.txt.",
        "subagent_type": "general-purpose",
    })], "The helper did it."]

    async def _move(system, tools, messages):
        main = "# IJ harness" in (system or "")
        seen.append({"main": main, "specs": list(tools or [])})
        if main:
            return main_moves.pop(0) if (main_moves and tools) else "Done."
        done = any(getattr(m, "role", "") == "tool" for m in messages or ())
        if done:
            return "dumped"
        return [ToolCall(id="h1", name="shell", arguments={"command": _dump_cmd("helper-env.txt")})]

    def _resp(move):
        if isinstance(move, str):
            return LLMResponse(text=move, usage={"input_tokens": 1, "output_tokens": 1})
        return LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 1, "output_tokens": 1})

    async def stream(*, provider=None, model=None, system="", messages=(), tools=None, **kw):
        resp = _resp(await _move(system, tools, messages))
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": provider or "anthropic", "model": model or "m"}

    async def complete(*, provider=None, model=None, system="", messages=(), tools=None, **kw):
        return RouteResult(_resp(await _move(system, tools, messages)), provider or "anthropic", model or "m")

    app.state.platform.router.stream = stream
    app.state.platform.router.complete = complete
    await turn(app, sid, "dump the environment with a helper", "env-helper-1")
    names = _names(work / "helper-env.txt")
    assert PLAIN in names and PLANTED not in names, "the helper's command did not get the Build environment"
    helper_specs = [s for c in seen if not c["main"] for s in c["specs"] if s.get("name") == "shell"]
    assert helper_specs, "the helper was not offered its shell"
    assert "user's own PATH" in helper_specs[0]["description"]
    assert "minimal scrubbed PATH" not in helper_specs[0]["description"]


# --------------------------------------------------------------------------- #
# REAL git by name in a Build session: the card, the user's identity
# --------------------------------------------------------------------------- #

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


def _git(*args: str, cwd: Path) -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env)
    assert r.returncode == 0, (args, r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8", "replace").strip()


@needs_git
async def test_git_by_name_commits_as_the_user_in_a_build_session(app, tmp_path, monkeypatch):
    """The repository names nobody; the user's own global git config does.
    In a Build session ``git commit`` (by NAME) runs and the commit is the
    user's — the scrubbed environment found no git on Windows and, named by
    its full path, no identity."""
    gcfg = tmp_path / "user-gitconfig"
    gcfg.write_text("[user]\n\tname = Phase Six User\n\temail = phase6@example.test\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "core.autocrlf", "false", cwd=repo)
    (repo / "a.txt").write_bytes(b"one\n")
    _git("add", "-A", cwd=repo)
    _git("-c", "user.name=Seed", "-c", "user.email=seed@example.test", "commit", "-q", "-m", "seed", cwd=repo)
    (repo / "a.txt").write_bytes(b"two\n")
    sid = new_session(app, repo, approval_mode="yolo")["id"]
    wire(app, Script([[ToolCall(id="c1", name="Bash", arguments={"command": "git commit -qam agent-commit"})],
                      "Done."]))
    frames = await turn(app, sid, "commit it", "env-git-1")
    finished = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is True, finished
    assert _git("log", "-1", "--format=%an <%ae>", cwd=repo) == "Phase Six User <phase6@example.test>"
    assert _git("log", "-1", "--format=%s", cwd=repo) == "agent-commit"


@needs_git
async def test_a_bare_git_push_on_a_branch_still_cards_once_only_and_runs_by_name_when_allowed(
    app, tmp_path, monkeypatch,
):
    from tests.test_build_git_v1375 import make_remote, make_repo, new_branch_session, no_real_gh
    from tests.test_build_plan_ask_v1367 import _finish, _start
    from tests.test_build_sessions_v1367 import turn_frames, until
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    no_real_gh(monkeypatch)
    repo = make_repo(tmp_path / "proj")
    bare = make_remote(tmp_path, repo)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, branch = view["id"], view["git"]["branch"]
    wire(app, Script([[ToolCall(id="p1", name="Bash", arguments={"command": f"git push -u origin {branch}"})],
                      "Done."]))
    drop, task = await _start(app, sid, "push it", "env-push-1")
    card = (await until(lambda: [d for e, d in turn_frames(app, "env-push-1") if e == "approval"]))[0]
    assert card["tool"] == "shell" and card["once_only"] is True and card["once_reason"] == "git"
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "once"}))[0] == 200
    await _finish(app, "env-push-1", drop, task)
    heads = _git("branch", "--list", cwd=bare)
    assert branch in heads, "the allowed push (git by name) reached the remote"
