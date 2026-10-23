"""v1.377.0 review (IJ harness part 2, Phase 6): THE BUILD ENVIRONMENT'S
SECRET RULE, read by its VALUE as well as its NAME, and the scope of the
switch proven byte for byte.

The doer's rule (``sandbox/native.secret_name``) is by NAME only. A secret
whose name carries no secret word reached a Build session's commands:
``DATABASE_URL=postgres://user:password@host/db``, ``PIP_INDEX_URL`` with a
token in its address, ``HTTPS_PROXY`` with a proxy password, a Slack webhook,
``DB_PASS`` / ``MYSQL_PWD``, an Azure storage connection string, a JWT. The
review's decisions, pinned here as tables:

* a URL's ``user:password@`` is REMOVED and the variable KEPT, so a package
  index, a database or a proxy still points where the user set it (a
  dropped ``PIP_INDEX_URL`` would silently install from the public index
  instead — dependency confusion); only the sign-in is gone;
* any other value ``detections.redact.mask`` would change, or a credential
  shape it does not know (an Azure ``AccountKey=``, a SAS ``sig=``, a
  webhook address, a PEM private key, a JWT, an npm / GitLab / Stripe /
  Google key) DROPS the variable;
* ``PASS`` / ``PWD`` as a whole ``_``-part (never ``PWD`` / ``OLDPWD``, the
  current folder), ``WEBHOOK`` and ``JWT`` are secret words;
* ``SSH_AUTH_SOCK`` and Git Credential Manager's ``GCM_*`` settings are
  KEPT: a socket path and a sign-in helper's settings are not secrets (no
  key or token passes through them), and git push over ssh / https needs
  them exactly as in the user's own terminal (the push itself is carded);
* a variable whose value points into THIS APP's own bundle (a frozen
  daemon's ``_MEIPASS``, a dev daemon's virtual environment) is dropped —
  the frozen build's runtime hooks may set some (``OPENSSL_MODULES``), and
  they are the app's, not the user's.

Every value here is OBVIOUSLY FAKE. No real environment value of this PC is
read, compared or printed; a failure lists NAMES only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.harness import helpers as H
from iron_jarvis.sandbox import native as N
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
from iron_jarvis.tools.base import ToolContext

WIN = os.name == "nt"
FAKE = "FAKE-not-a-real-secret-7f3a"

# --------------------------------------------------------------------------- #
# the tables
# --------------------------------------------------------------------------- #

#: name -> value: DROPPED (a secret by its value or by a word the doer's rule
#: missed).
DROP_BY_VALUE_OR_WORD = {
    "DB_PASS": FAKE,
    "SMTP_PASS": FAKE,
    "MYSQL_PWD": FAKE,
    "DB_PWD": FAKE,
    "SLACK_WEBHOOK_URL": f"https://hooks.slack.com/services/T000/B000/{FAKE}",
    "DISCORD_WEBHOOK": f"https://discord.com/api/webhooks/1/{FAKE}",
    "CI_JOB_JWT": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlLXVzZXIifQ.ZmFrZS1zaWduYXR1cmUtdmFsdWU",
    "MY_JSON_WEB": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlLXVzZXIifQ.ZmFrZS1zaWduYXR1cmUtdmFsdWU",
    "NODE_OPTIONS": f"--max-old-space-size=4096 --api-key={FAKE}",
    "EXTRA_HEADER": f"Authorization: Bearer {FAKE}",
    "SERVICE_URL": f"https://api.example.test/v1?token={FAKE}",
    "AzureWebJobsStorage": f"DefaultEndpointsProtocol=https;AccountName=fake;AccountKey={FAKE}==;",
    "BLOB_SAS_URL": f"https://fake.blob.core.windows.net/c?sv=2022-11-02&sig={FAKE}",
    "CONN": f"Server=db.example.test;Database=app;User Id=app;Password={FAKE};",
    "DEPLOY_SSH": "-----BEGIN OPENSSH PRIVATE KEY-----\\nFAKE\\n-----END OPENSSH PRIVATE KEY-----",
    "SOME_FORGE": "ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE",
    "REGISTRY_NPM": "npm_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE",
    "GITLAB_X": "glpat-FAKEFAKEFAKEFAKEFAKE",
    "PAYMENTS": "sk_live_FAKEFAKEFAKEFAKEFAKE",
    "MAPS": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK",
    "SLACK_BOT": "xoxb-FAKEFAKE-FAKEFAKE",
    "AWS_LIKE": "AKIAFAKEFAKEFAKEFAKE",
    "GIT_CONFIG_PARAMETERS": f"'http.extraheader=Authorization: Bearer {FAKE}'",
    # each caught by ONE rule only (so a mutation of that rule goes red):
    "MY_WEBHOOK": f"https://example.test/hook/{FAKE}",  # the WEBHOOK word
    "SERVICE_JWT": f"opaque-{FAKE}",  # the JWT word
    "NOTIFY_URL": f"https://hooks.slack.com/services/T0/B0/{FAKE}",  # a webhook address
    "TEAMS_URL": f"https://fake.webhook.office.com/webhookb2/{FAKE}",  # a webhook address
    "MODEL_HUB": "hf_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE",  # a Hugging Face key
}

#: name -> (value given, value the child must get): the address KEPT, the
#: sign-in removed.
URL_CREDENTIALS_REMOVED = {
    "DATABASE_URL": (f"postgres://app:{FAKE}@db.example.test:5432/app",
                     "postgres://db.example.test:5432/app"),
    "REDIS_URL": (f"redis://:{FAKE}@cache.example.test:6379/0", "redis://cache.example.test:6379/0"),
    "MONGODB_URI": (f"mongodb+srv://u:{FAKE}@cluster.example.test/app?retryWrites=true",
                    "mongodb+srv://cluster.example.test/app?retryWrites=true"),
    "PIP_INDEX_URL": (f"https://user:{FAKE}@pkgs.example.test/simple",
                      "https://pkgs.example.test/simple"),
    "HTTPS_PROXY": (f"http://me:{FAKE}@proxy.example.test:8080", "http://proxy.example.test:8080"),
    # GitHub's token-as-username form: the WHOLE userinfo goes.
    "REPO_URL": (f"https://{FAKE}:x-oauth-basic@github.com/o/r.git", "https://github.com/o/r.git"),
}

#: name -> value: KEPT EXACTLY (the user's terminal has them; not secrets).
KEEP_EXACT = {
    "PATH": os.pathsep.join([r"C:\Users\someone\bin" if WIN else "/home/someone/bin",
                             r"C:\tools" if WIN else "/opt/tools"]),
    "PWD": "/c/Users/someone/project",
    "OLDPWD": "/c/Users/someone",
    "PGPASSFILE": r"C:\Users\someone\pgpass.conf",
    "KUBECONFIG": r"C:\Users\someone\.kube\config",
    "SSH_AUTH_SOCK": r"\\.\pipe\openssh-ssh-agent",
    "GCM_CREDENTIAL_STORE": "dpapi",
    "GCM_AUTHORITY": "organizations",
    "GCM_INTERACTIVE": "auto",
    "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/",
    "HTTP_PROXY": "http://proxy.example.test:8080",
    "DATABASE_URL_LOCAL": "postgres://localhost:5432/app",
    "PG_USER_URL": "postgres://alice@localhost/app",
    "NODE_OPTIONS_PLAIN": "--max-old-space-size=4096",
    "PSMODULEPATH": r"C:\Users\someone\Documents\WindowsPowerShell\Modules",
    "BYPASS_LIST": "localhost;127.0.0.1",
    "GIT_AUTHOR_NAME": "Someone",
}


def _only_names(names) -> list[str]:
    return sorted(str(n) for n in names)


def test_a_secret_whose_name_has_no_secret_word_is_dropped_by_its_value_or_word():
    env = N.build_env({**DROP_BY_VALUE_OR_WORD, "PATH": KEEP_EXACT["PATH"]})
    leaked = _only_names(n for n in DROP_BY_VALUE_OR_WORD if n in env)
    assert leaked == [], f"secrets reached a Build session's command (names only): {leaked}"
    fake_reached = FAKE in "".join(env.values())
    assert not fake_reached, "a fake secret's value reached the child"


def test_a_urls_sign_in_is_removed_and_the_address_kept():
    env = N.build_env({k: v[0] for k, v in URL_CREDENTIALS_REMOVED.items()})
    wrong = _only_names(n for n, (_, want) in URL_CREDENTIALS_REMOVED.items() if env.get(n) != want)
    assert wrong == [], f"these addresses were not kept without their sign-in (names only): {wrong}"
    fake_reached = FAKE in "".join(env.values())
    assert not fake_reached


def test_what_the_users_terminal_has_is_kept_exactly():
    env = N.build_env(dict(KEEP_EXACT))
    lost = _only_names(n for n, v in KEEP_EXACT.items() if env.get(n) != v)
    assert lost == [], f"kept variables were dropped or changed (names only): {lost}"


def test_the_value_rule_never_reads_a_value_into_an_error_or_a_log(caplog):
    """A value is judged and either kept, cut or dropped — never logged."""
    caplog.set_level("DEBUG")
    N.build_env({**DROP_BY_VALUE_OR_WORD, **{k: v[0] for k, v in URL_CREDENTIALS_REMOVED.items()}})
    logged = FAKE in caplog.text
    assert not logged


def test_a_variable_pointing_into_the_frozen_bundle_is_dropped(monkeypatch, tmp_path):
    """A frozen daemon's runtime hooks may point a variable into its own
    bundle (``OPENSSL_MODULES``, ``TCL_LIBRARY``…): the app's, not the
    user's — a user's own ``openssl`` / ``python`` would load the app's
    modules. PATH loses only the bundle's entries."""
    bundle = tmp_path / "Iron Jarvis" / "resources" / "daemon" / "_internal"
    bundle.mkdir(parents=True)
    user_bin = tmp_path / "user-bin"
    user_bin.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    env = N.build_env({
        "OPENSSL_MODULES": str(bundle / "ossl-modules"),
        "TCL_LIBRARY": str(bundle / "tcl"),
        "PATH": os.pathsep.join([str(bundle), str(user_bin)]),
        "KEEP_ME": str(user_bin),
    })
    assert "OPENSSL_MODULES" not in env and "TCL_LIBRARY" not in env
    assert env["PATH"].split(os.pathsep) == [str(user_bin)]
    assert env["KEEP_ME"] == str(user_bin)


# --------------------------------------------------------------------------- #
# the child really got it (names; our own fake values only)
# --------------------------------------------------------------------------- #


def _ctx(ws: Path, session: str) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id=session, agent_run_id="r",
        config=SimpleNamespace(home=ws.parent, sandbox={}), event_bus=None, engine=None,
    )


async def test_a_build_sessions_command_gets_the_address_without_the_password(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"postgres://app:{FAKE}@db.example.test/app")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", f"https://hooks.slack.com/services/T0/B0/{FAKE}")
    monkeypatch.setenv("DB_PASS", FAKE)
    N._TOOLS_CACHE.clear()
    cmd = "set > env.txt" if WIN else "env > env.txt"
    res = await SandboxedShellTool().execute({"command": cmd}, _ctx(ws, "build:review1"))
    assert res.ok, res.error
    dump = (ws / "env.txt").read_bytes().decode("utf-8", "replace")
    names = {line.split("=", 1)[0].strip().upper() for line in dump.splitlines() if "=" in line}
    # A bare bool: pytest must never print the dump (it holds this PC's real values).
    fake_reached = FAKE in dump
    del dump
    assert not fake_reached, "a fake secret's value reached the Build session's command"
    assert "DATABASE_URL" in names, "the database's address was dropped with its password"
    assert "SLACK_WEBHOOK_URL" not in names and "DB_PASS" not in names


# --------------------------------------------------------------------------- #
# SCOPE: every run that is not a Build session's gets EXACTLY scrubbed_env()
# --------------------------------------------------------------------------- #


class _Spy:
    def __init__(self) -> None:
        self.envs: list[object] = []


@pytest.fixture()
def spy_run(monkeypatch):
    """Record the ``env`` the shell's real spawn was handed (``procs.run``)."""
    from iron_jarvis.core import procs

    spy = _Spy()
    real = procs.run

    def _run(*a, **kw):
        spy.envs.append(kw.get("env"))
        return real(*a, **kw)

    monkeypatch.setattr(procs, "run", _run)
    return spy


NOT_BUILD = ("chat", "sess_schedule_nightly", "session_goal_1", "mcp:pane1", "", "build", "builder:x",
             "Build:abc")


@pytest.mark.parametrize("session", NOT_BUILD)
async def test_every_other_runs_shell_gets_exactly_the_scrubbed_environment(spy_run, tmp_path, session):
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await SandboxedShellTool().execute({"command": "echo ok"}, _ctx(ws, session))
    assert res.ok, res.error
    exact = bool(spy_run.envs) and spy_run.envs[-1] == N.scrubbed_env()  # a bool: never print an env
    assert exact, f"{session!r}: a non-Build run did not get exactly scrubbed_env()"


async def test_a_helper_of_a_chat_turn_gets_exactly_the_scrubbed_environment(spy_run, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    call = SimpleNamespace(ask_id="")
    with H._LIVE_LOCK:
        H._LIVE.setdefault("chat", {})["sess_chat_helper"] = call
        H._LIVE.setdefault("sess_parent_agent", {})["sess_agent_child"] = call
    try:
        for session in ("sess_chat_helper", "sess_agent_child"):
            res = await SandboxedShellTool().execute({"command": "echo ok"}, _ctx(ws, session))
            assert res.ok, res.error
            exact = spy_run.envs[-1] == N.scrubbed_env()
            assert exact, session
    finally:
        with H._LIVE_LOCK:
            H._LIVE.get("chat", {}).pop("sess_chat_helper", None)
            H._LIVE.get("sess_parent_agent", {}).pop("sess_agent_child", None)
            for k in ("chat", "sess_parent_agent"):
                if not H._LIVE.get(k):
                    H._LIVE.pop(k, None)


async def test_every_other_runs_program_gets_exactly_the_scrubbed_environment(monkeypatch, tmp_path):
    from iron_jarvis.core import procs
    from iron_jarvis.harness import process as P

    ws = tmp_path / "ws"
    ws.mkdir()
    seen: list[object] = []
    real = procs.spawn

    def _spawn(*a, **kw):
        seen.append(kw.get("env"))
        return real(*a, **kw)

    monkeypatch.setattr(procs, "spawn", _spawn)
    try:
        for session in ("chat", "sess_other"):
            res = await P.ProcessStartTool().execute(
                {"command": "echo ok", "until_exit": True, "wait_ms": 10_000}, _ctx(ws, session),
            )
            assert res.ok, res.error
            exact = seen[-1] == N.scrubbed_env()
            assert exact, session
        res = await P.ProcessStartTool().execute(
            {"command": "echo ok", "until_exit": True, "wait_ms": 10_000}, _ctx(ws, "build:review2"),
        )
        assert res.ok, res.error
        is_build = seen[-1] != N.scrubbed_env() and seen[-1] == N.build_env()
        assert is_build, "a Build session's program did not get the Build environment"
    finally:
        P.end_session(None)


# --------------------------------------------------------------------------- #
# a HELPER of a chat-harness turn (the benchmark's path): told the scrubbed
# words AND given the scrubbed environment (the runtime's words were unpinned)
# --------------------------------------------------------------------------- #


async def test_a_chat_harness_turns_helper_is_told_and_given_the_scrubbed_environment(
    tmp_path, monkeypatch,
):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.providers.adapters.base import ToolCall
    from tests.test_harness_helpers_v1372 import CLAUDE, Model, wire

    monkeypatch.setenv("IJ_REVIEW_PLAIN_SETTING", "kept-only-in-build")
    N._TOOLS_CACHE.clear()
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    cmd = "set > helper-chat-env.txt" if WIN else "env > helper-chat-env.txt"

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="hs1", name="shell", arguments={"command": cmd})]
        return "dumped"

    model = Model(main=[[ToolCall(id="t1", name="Agent", arguments={
        "description": "dump env", "prompt": "Write the environment to a file.",
    })], "ok"], helper=helper)
    specs_seen: list[tuple[bool, list[dict]]] = []
    real_move = model._move

    async def _move(provider, model_id, system, messages, tools):
        specs_seen.append(("# IJ harness" in (system or ""), list(tools or [])))
        return await real_move(provider, model_id, system, messages, tools)

    model._move = _move
    wire(app, model)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="go")], provider=CLAUDE[0],
                    model=CLAUDE[1], workspace_dir=str(work), approval_mode="yolo",
                    tools=["shell"], granted_tools=["shell"])
    import asyncio

    async with asyncio.timeout(90):  # a parked ask fails here, by name
        async for _ in await stream_chat_turn(app.state.platform, BUILTIN_PERSONAS, body, harness="auto"):
            pass
    out = work / "helper-chat-env.txt"
    assert out.exists(), "the chat harness turn's helper never ran its shell"
    text = out.read_bytes().decode("utf-8", "replace")
    names = {ln.split("=", 1)[0].strip().upper() for ln in text.splitlines() if "=" in ln}
    del text
    assert "IJ_REVIEW_PLAIN_SETTING" not in names, "a chat helper's command got the Build environment"
    if WIN:
        assert "USERPROFILE" not in names and "APPDATA" not in names
    helper_shell = [s.get("description", "") for main, specs in specs_seen if not main
                    for s in specs if s.get("name") == "shell"]
    assert helper_shell, "the helper was not offered its shell"
    assert "user's own PATH" not in helper_shell[0], "a chat helper was told it has the user's tools"
    assert "minimal scrubbed PATH" in helper_shell[0]


# --------------------------------------------------------------------------- #
# git's configuration in the environment: GIT_CONFIG_COUNT + KEY_n / VALUE_n
# (found by the review's LIVE run: every git command failed)
# --------------------------------------------------------------------------- #


def test_git_config_pairs_in_the_environment_are_kept_together():
    """``GIT_CONFIG_KEY_0`` holds the word KEY, so the doer's rule dropped it
    and kept ``GIT_CONFIG_COUNT`` — and git then refuses EVERY command
    ("missing config key GIT_CONFIG_KEY_0"). The pairs are one unit: all
    kept, or (when any value is a secret) all dropped."""
    pairs = {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "alias.st", "GIT_CONFIG_VALUE_0": "status -sb",
             "GIT_CONFIG_KEY_1": "core.editor", "GIT_CONFIG_VALUE_1": "code --wait"}
    env = N.build_env(dict(pairs))
    wrong = sorted(n for n, v in pairs.items() if env.get(n) != v)
    assert wrong == [], f"git's own config pairs were broken (names only): {wrong}"
    secret = {**pairs, "GIT_CONFIG_KEY_1": "http.extraheader", "GIT_CONFIG_VALUE_1": f"Authorization: Bearer {FAKE}"}
    env = N.build_env(dict(secret))
    left = sorted(n for n in secret if n in env)
    assert left == [], f"a pair set holding a secret must go as a whole (names only): {left}"


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git is not installed")
async def test_git_runs_in_a_build_session_whose_environment_sets_git_config_pairs(monkeypatch, tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "alias.st")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "status --short")
    res = await SandboxedShellTool().execute({"command": "git st"}, _ctx(repo, "build:review-gitcfg"))
    assert res.ok, "git failed in a Build session with git config pairs in the environment"
